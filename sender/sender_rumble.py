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
FW_REPORT, FW_SIZE, FW_VERSION_AT = 0x20, 64, 44   # raport cech z wersją oprogramowania (jak w hid-playstation)
VIBRATION_V2_FROM = 0x0221                         # DS_FEATURE_VERSION(2, 21)


def _hidiocgfeature(size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | 0x07


def firmware_version(fd):
    """Wersja "update" oprogramowania pada z raportu cech 0x20 albo None (brak dostępu / inny pad)."""
    import array
    import fcntl
    buf = array.array("B", [FW_REPORT] + [0] * (FW_SIZE - 1))
    try:
        fcntl.ioctl(fd, _hidiocgfeature(FW_SIZE), buf, True)
    except OSError:
        return None
    return buf[FW_VERSION_AT] | buf[FW_VERSION_AT + 1] << 8 if buf[0] == FW_REPORT else None


def output_report(bt, seq, small, large, v2=None):
    """Raport wyjściowy DualSense z samą wibracją. small = silnik prawy (słaby), large = lewy (mocny), 0..255.
    v2: True = oprogramowanie pada >= 2.21 (flaga "vibration v2"), False = starsze, None = nieznane -> obie flagi
    (jak sterownik jądra: starsze pady wymagają flagi v1, nowsze v2)."""
    common = bytearray(47)
    common[0] = FLAG0_HAPTICS_SELECT | (0 if v2 else FLAG0_COMPAT_VIBRATION)
    common[2], common[3] = small & 0xFF, large & 0xFF        # motor_right, motor_left
    common[38] = FLAG2_COMPAT_VIBRATION2 if v2 is not False else 0   # valid_flag2
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
        self.v2, self.fw = None, None
        self.logged = 0               # ile pierwszych poleceń wibracji zapisać w logu (diagnostyka)
        self.cur = (0, 0)
        self.t_set = 0.0
        self._watch = None
        if mode in ("auto", "evdev") and has_ff(pad_dev):
            self.mode = "evdev"
        elif mode in ("auto", "hidraw"):
            self._open_hidraw(hidraw)
        fw = (f", firmware {self.fw:04x}" if self.fw is not None else ", firmware unknown") \
            + f", vibration {'v2' if self.v2 else 'v1' if self.v2 is False else 'v1+v2'}" if self.mode == "hidraw" else ""
        what = {"evdev": "kernel driver (evdev FF)", "hidraw": f"direct HID reports ({self.path}{fw})", None: ""}
        print(f"Controller {idx}: rumble " + (f"via {what[self.mode]}" if self.mode else
                                              "unavailable (no evdev FF and no hidraw access - run with doas)"
                                              if mode != "off" else "off"))

    def _open_hidraw(self, given=None):
        path, bt = given or find_hidraw(self.dev)
        if not path:
            return False
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)   # RDWR: także odczyt wersji oprogramowania
        except OSError as e:
            print(f"Controller {self.idx}: cannot open {path} for rumble: {e.strerror}")
            return False
        self.path, self.bt, self.mode = path, bt, "hidraw"
        self.fw = firmware_version(self.fd)
        self.v2 = None if self.fw is None else self.fw >= VIBRATION_V2_FROM
        return True

    # ---------- sterowanie ----------
    def set(self, small, large):
        now = time.monotonic()
        if self.mode is None:
            return
        if (small, large) == self.cur and now - self.t_set < 0.4:
            return                                   # bez zmian i świeże - nic nie wysyłaj
        if self.logged < 3 and (small or large):
            self.logged += 1
            print(f"Controller {self.idx}: rumble {small}/{large} from PC via {self.mode}")
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
            os.write(self.fd, output_report(self.bt, self.seq, small, large, self.v2))
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


# ---------------- test z linii poleceń: doas python3 sender_rumble.py --test ----------------
def _test():
    """Wibruje każdym podłączonym DualSense po kolei każdym sposobem - bez sieci i receivera. Pokazuje, czy problem
    jest w padzie / jądrze (nic nie wibruje), czy w drodze PC -> nadajnik (tu wibruje, z gry nie)."""
    import evdev
    pads = [evdev.InputDevice(p) for p in evdev.list_devices()]
    pads = [d for d in pads if "Wireless Controller" in d.name and "Motion" not in d.name and "Touchpad" not in d.name]
    if not pads:
        print("No DualSense found (is it connected? run with doas)")
        return
    for d in pads:
        path, bt = find_hidraw(d)
        print(f"== {d.name} {d.uniq or d.phys} ({d.path}): {'Bluetooth' if bt else 'USB'}, hidraw {path}, "
              f"evdev FF {'yes' if has_ff(d) else 'NO (kernel without CONFIG_PLAYSTATION_FF)'}")
        steps = [("evdev FF", "evdev", None)] if has_ff(d) else []
        steps += [("HID report v1", "hidraw", False), ("HID report v2", "hidraw", True), ("HID report v1+v2", "hidraw", None)]
        for label, mode, v2 in steps:
            r = Rumble(0, d, mode)
            if r.mode != mode:
                print(f"   {label}: not available")
                continue
            if mode == "hidraw":
                r.v2 = v2
            print(f"   {label}: rumbling 1.5 s ...", flush=True)
            try:
                r._send(255, 255)
                time.sleep(1.5)
                r._send(0, 0)
            except OSError as e:
                print(f"   {label}: ERROR {e!r}")
            r.close()
            time.sleep(0.8)
    print("Which step did you feel? Set rumble = evdev / hidraw in sender.toml if auto picks the wrong one.")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        _test()
    else:
        print("usage: doas python3 sender_rumble.py --test")
