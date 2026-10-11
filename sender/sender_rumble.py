"""Sender: wibracja pada - przez sterownik (evdev FF) albo bezpośrednio raportem HID (hidraw).

Sterownik hid-playstation obsługuje wibrację tylko z opcją jądra CONFIG_PLAYSTATION_FF - jądra telefonów
(np. postmarketOS) często jej nie mają i wtedy pad nie wibruje wcale. Tryb "hidraw" wysyła do pada ten sam raport
wyjściowy, który wysłałby sterownik (tryb zgodności z DS4: dwa silniki), ustawiając w nim TYLKO flagi wibracji -
lightbar i diody, którymi steruje sterownik, zostają nietknięte.

  rumble = "auto"    evdev FF, jeśli pad go ma; inaczej hidraw (domyślnie)
           "evdev" / "hidraw" / "off"

Raport: USB 0x02 (63 B), Bluetooth 0x31 (78 B: numer sekwencji, znacznik 0x10, na końcu CRC32 z ziarnem 0xA2).
Pad trzyma ostatnią moc silników - receiver odświeża wibrację co 0.5 s, a strażnik tutaj zatrzymuje ją, gdy
odświeżenia ustaną (zgubione pakiety, zamknięta gra), żeby pad nie wibrował bez końca.
"""
import asyncio
import glob
import os
import re
import time
import zlib

from evdev import ecodes as ec
from evdev import ff

FLAG0_COMPAT_VIBRATION, FLAG0_HAPTICS_SELECT = 0x01, 0x02
FLAG2_COMPAT_VIBRATION2 = 0x04            # nowsze oprogramowanie pada (jak use_vibration_v2 w jądrze)
OUT_TAG = 0x10
CRC_SEED = b"\xa2"
WATCHDOG_S = 1.5
EFFECT_MS = 1000
_HID_DIR = re.compile(r"^([0-9A-Fa-f]{4}):[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")
BUS_BLUETOOTH = 0x0005


def output_report(bt, seq, small, large):
    """Raport wyjściowy DualSense z samą wibracją. small = silnik prawy (słaby), large = lewy (mocny), 0..255."""
    common = bytearray(47)
    common[0] = FLAG0_COMPAT_VIBRATION | FLAG0_HAPTICS_SELECT
    common[2], common[3] = small & 0xFF, large & 0xFF        # motor_right, motor_left
    common[38] = FLAG2_COMPAT_VIBRATION2                      # valid_flag2
    if not bt:
        return bytes([0x02]) + bytes(common) + bytes(15)
    rep = bytearray([0x31, (seq & 0x0F) << 4, OUT_TAG]) + common + bytes(24)
    crc = zlib.crc32(CRC_SEED + bytes(rep)) & 0xFFFFFFFF
    return bytes(rep) + crc.to_bytes(4, "little")


def find_hidraw(pad_dev, sys_root="/sys"):
    """-> (/dev/hidrawN, czy Bluetooth) dla tego samego urządzenia HID co pad evdev, albo (None, False)."""
    try:
        node = os.path.realpath(f"{sys_root}/class/input/{os.path.basename(pad_dev.path)}")
    except (OSError, AttributeError):
        return None, False
    hid = node
    while hid not in ("/", "") and not _HID_DIR.match(os.path.basename(hid)):
        hid = os.path.dirname(hid)
    if hid in ("/", ""):
        return None, False
    found = sorted(glob.glob(os.path.join(hid, "hidraw", "hidraw*")))
    bus = int(_HID_DIR.match(os.path.basename(hid)).group(1), 16)
    return (f"/dev/{os.path.basename(found[0])}" if found else None), bus == BUS_BLUETOOTH


def has_ff(dev):
    try:
        return ec.FF_RUMBLE in dev.capabilities().get(ec.EV_FF, [])
    except (OSError, AttributeError):
        return False


class Rumble:
    """Wibracja jednego pada. set(small, large) przy każdym pakiecie feedbacku; close() przy rozłączeniu."""

    def __init__(self, idx, pad_dev, mode="auto", hidraw=None):
        self.idx, self.dev, self.mode = idx, pad_dev, None
        self.fd, self.bt, self.seq, self.ff_id, self.path = None, False, 0, None, None
        self.cur = (0, 0)
        self.t_set = 0.0
        self._watch = None
        if mode in ("auto", "evdev") and has_ff(pad_dev):
            self.mode = "evdev"
        elif mode in ("auto", "hidraw"):
            self._open_hidraw(hidraw)
        what = {"evdev": "kernel driver (evdev FF)", "hidraw": f"direct HID reports ({self.path})", None: ""}
        print(f"Controller {idx}: rumble " + (f"via {what[self.mode]}" if self.mode else
                                              "unavailable (no evdev FF and no hidraw access - run with doas)"
                                              if mode != "off" else "off"))

    def _open_hidraw(self, given=None):
        path, bt = given or find_hidraw(self.dev)
        if not path:
            return False
        try:
            self.fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            print(f"Controller {self.idx}: cannot open {path} for rumble: {e.strerror}")
            return False
        self.path, self.bt, self.mode = path, bt, "hidraw"
        return True

    # ---------- sterowanie ----------
    def set(self, small, large):
        now = time.monotonic()
        if self.mode is None:
            return
        if (small, large) == self.cur and now - self.t_set < 0.4:
            return                                   # bez zmian i świeże - nic nie wysyłaj
        try:
            self._send(small, large)
        except OSError as e:
            if self.mode == "evdev":
                print(f"Controller {self.idx}: evdev rumble failed ({e!r}) - switching to hidraw")
                self.mode = None
                if self._open_hidraw():
                    self._send(small, large)
            else:
                print(f"Controller {self.idx}: rumble failed: {e!r}")
                self.close()
                return
        self.cur, self.t_set = (small, large), now
        self._arm_watchdog()

    def _send(self, small, large):
        if self.mode == "hidraw":
            self.seq = (self.seq + 1) & 0x0F
            os.write(self.fd, output_report(self.bt, self.seq, small, large))
            return
        if small or large:
            eff = ff.Effect(ec.FF_RUMBLE, -1 if self.ff_id is None else self.ff_id, 0, ff.Trigger(0, 0),
                            ff.Replay(EFFECT_MS, 0),
                            ff.EffectType(ff_rumble_effect=ff.Rumble(strong_magnitude=large * 257,
                                                                     weak_magnitude=small * 257)))
            self.ff_id = self.dev.upload_effect(eff)
            self.dev.write(ec.EV_FF, self.ff_id, 1)
        elif self.ff_id is not None:
            self.dev.write(ec.EV_FF, self.ff_id, 0)

    def _arm_watchdog(self):
        if self._watch is not None:
            self._watch.cancel()
            self._watch = None
        if self.cur != (0, 0):
            try:
                self._watch = asyncio.get_running_loop().call_later(WATCHDOG_S, self._expired)
            except RuntimeError:
                pass

    def _expired(self):
        self._watch = None
        if self.cur != (0, 0) and time.monotonic() - self.t_set >= WATCHDOG_S - 0.05:
            self.set(0, 0)                           # receiver przestał odświeżać - zatrzymaj silniki

    def close(self):
        if self._watch is not None:
            self._watch.cancel()
            self._watch = None
        try:
            if self.cur != (0, 0):
                self._send(0, 0)
            if self.mode == "evdev" and self.ff_id is not None:
                self.dev.erase_effect(self.ff_id)
        except OSError:
            pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        self.mode, self.cur = None, (0, 0)
