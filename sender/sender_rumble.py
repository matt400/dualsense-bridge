"""Sender: wibracja pada - przez sterownik (evdev FF) albo bezpośrednio raportem HID (hidraw).

Sterownik hid-playstation obsługuje wibrację tylko z opcją jądra CONFIG_PLAYSTATION_FF - jądra telefonów
(np. postmarketOS) często jej nie mają i wtedy pad nie wibruje wcale. Tryb "hidraw" wysyła do pada ten sam raport
wyjściowy, który wysłałby sterownik (tryb zgodności z DS4: dwa silniki), z flagami wibracji i koloru lightbara -
kolor idzie w tym samym raporcie, bo osobny raport sterownika (zmiana koloru przez sysfs) w tej samej chwili
sprawiał, że pad przyjmował tylko jeden z nich. Diody gracza zostają nietknięte.

  rumble = "auto"    oba naraz: evdev FF (jeśli pad go ma) i raport HID (domyślnie)
           "evdev" / "hidraw" / "off"

Raport: USB 0x02 (63 B), Bluetooth 0x31 (78 B: numer sekwencji, znacznik 0x10, na końcu CRC32 z ziarnem 0xA2).
Ten sam raport niesie efekty spustów (adaptive triggers) z profilu na PC: opór, klik (jak spust broni), wibracja
spustu - gry przez wirtualnego DS4 ich nie ustawią, a sterownik jądra nie ma takiej funkcji. Pad trzyma efekt spustu
do następnej zmiany; raporty bez flag spustów (wibracja, kolor, sterownik) go nie ruszają.
Wibracja z gry na spusty (RFX_ON w trig2) liczona jest tutaj, z tych samych pakietów wibracji - bez dodatkowego ruchu
w sieci: L2 drga wg dużego silnika, R2 wg małego; na czas wibracji zastępuje efekt spustu, a RFX_HOLD_S po ustaniu
wibracji spust wraca do swojego efektu.
Pad trzyma ostatnią moc silników - receiver odświeża wibrację co 0.5 s, a strażnik tutaj zatrzymuje ją, gdy
odświeżenia ustaną (zgubione pakiety, zamknięta gra), żeby pad nie wibrował bez końca.
"""
import asyncio
import errno
import glob
import os
import re
import stat
import time
import zlib

from evdev import ecodes as ec
from evdev import ff

from proto import (CAP_TRIG_FX, CAP_TRIGGERS, RFX_GAIN_MAX, RFX_HOLD_S, RFX_ON, TFX_CLICK, TFX_RES, TFX_VIB,
                   TRIG2_OFF, TRIG_AMP_MAX, TRIG_CLICK_MAX, TRIG_CLICK_MIN, TRIG_FORCE_MAX, TRIG_FREQ_MAX,
                   TRIG_START_MAX, WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OFF, WHY_OK, WHY_OPEN,
                   trig2_from_legacy)

FLAG0_COMPAT_VIBRATION, FLAG0_HAPTICS_SELECT = 0x01, 0x02
FLAG0_RIGHT_TRIGGER, FLAG0_LEFT_TRIGGER = 0x04, 0x08    # valid_flag0: efekt prawego / lewego spustu z tego raportu
RIGHT_TRIGGER_AT, LEFT_TRIGGER_AT, TRIGGER_LEN = 10, 21, 11
TRIG_FEEDBACK, TRIG_OFF = 0x21, 0x05      # efekty spustu: "Feedback" (opór od strefy) i wyłączenie
TRIG_WEAPON, TRIG_VIBRATION = 0x25, 0x26  # "Weapon" (opór do punktu i kliknięcie) i "Vibration" (drganie od strefy)
FLAG2_COMPAT_VIBRATION2 = 0x04            # nowsze oprogramowanie pada (jak use_vibration_v2 w jądrze)
FLAG1_LIGHTBAR = 0x04                     # valid_flag1: kolor lightbara z tego raportu
LIGHTBAR_AT = 44                          # lightbar_red / green / blue we wspólnej części raportu
REPEAT_S = 0.02                           # zmiana wibracji / koloru wysyłana drugi raz po tylu sekundach
OUT_TAG = 0x10
CRC_SEED = b"\xa2"
# błąd zapisu: tyle kolejnych błędów, zanim sposób wibracji zostanie wyłączony. Pojedynczy EIO / ETIMEDOUT przy
# zatkanym Bluetooth to nie awaria - wcześniej wyłączał raport HID (wibracja, kolor, spusty) do rozłączenia pada.
# Pad, którego już nie ma (ENODEV...), i błąd, który się nie zmieni (brak obsługi w jądrze, zły raport, uprawnienia),
# wyłączają od razu.
FAIL_MAX = 5
FATAL_ERRNO = frozenset({errno.ENODEV, errno.ENOENT, errno.ENXIO, errno.EBADF, errno.ENOSYS, errno.EOPNOTSUPP,
                         errno.EINVAL, errno.EACCES, errno.EPERM})
WATCHDOG_S = 1.5
# efekty spustów z PC: receiver odświeża je co 2 s (rx_feedback.TRIG_REFRESH_S); po tylu sekundach bez odświeżenia
# (receiver zamknięty bez sprzątania, uśpiony / wyłączony PC) spusty wracają do zwykłych - pad trzyma efekt bez końca
TRIG_WATCHDOG_S = 7.0
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


def trigger_effect(start, force):
    """Stały opór spustu (efekt "Feedback" 0x21, 11 B): od strefy `start` (0-9, strefa = 10 % skoku) do końca, siła
    `force` 1-8 we wszystkich tych strefach; force 0 -> efekt Off (spust bez oporu). Bity: 10 stref aktywnych
    (2 B) i po 3 bity siły na strefę (4 B), little endian."""
    out = bytearray(TRIGGER_LEN)
    if not force or not 0 <= start <= TRIG_START_MAX:
        out[0] = TRIG_OFF
        return bytes(out)
    out[0] = TRIG_FEEDBACK
    out[1:3] = _zones(start).to_bytes(2, "little")
    out[3:7] = _levels(start, min(force, TRIG_FORCE_MAX) - 1)
    return bytes(out)


OFF_EFFECT = trigger_effect(0, 0)


def _zones(start):
    """Maska stref od `start` do końca skoku (10 stref)."""
    return sum(1 << z for z in range(start, 10))


def _levels(start, level):
    """Po 3 bity poziomu (0-7) na każdą strefę od `start` (4 B, little endian)."""
    return sum(level << (3 * z) for z in range(start, 10)).to_bytes(4, "little")


def weapon_effect(pos, force):
    """Klik jak spust broni (efekt "Weapon" 0x25): opór od strefy `pos` (2-7) narasta do strefy pos + 1 i tam puszcza
    (kliknięcie); siła 1-8; force 0 albo zły punkt -> Off."""
    out = bytearray(TRIGGER_LEN)
    if not force or not TRIG_CLICK_MIN <= pos <= TRIG_CLICK_MAX:
        out[0] = TRIG_OFF
        return bytes(out)
    out[0] = TRIG_WEAPON
    out[1:3] = ((1 << pos) | (1 << (pos + 1))).to_bytes(2, "little")
    out[3] = min(force, TRIG_FORCE_MAX) - 1
    return bytes(out)


def vibration_effect(start, amp, freq):
    """Drganie spustu (efekt "Vibration" 0x26) od strefy `start` (0-9) do końca: amplituda 1-8, częstotliwość 1-255 Hz;
    amplituda albo częstotliwość 0 -> Off."""
    out = bytearray(TRIGGER_LEN)
    if not amp or not freq or not 0 <= start <= TRIG_START_MAX:
        out[0] = TRIG_OFF
        return bytes(out)
    out[0] = TRIG_VIBRATION
    out[1:3] = _zones(start).to_bytes(2, "little")
    out[3:7] = _levels(start, min(amp, TRIG_AMP_MAX) - 1)
    out[9] = min(freq, TRIG_FREQ_MAX)
    return bytes(out)


def effect(mode, a, b, c):
    """Efekt spustu z pakietu trig2 (tryb TFX_*, parametry a, b, c - opis w proto) -> 11 B raportu."""
    if mode == TFX_RES:
        return trigger_effect(a, b)
    if mode == TFX_CLICK:
        return weapon_effect(a, b)
    if mode == TFX_VIB:
        return vibration_effect(a, b, c)
    return OFF_EFFECT


def rumble_amp(motor, gain):
    """Moc silnika 0-255 i siła [%] -> amplituda drgania spustu 0-8 (0 = spust bez wibracji z gry)."""
    return min(TRIG_AMP_MAX, (motor * min(gain, RFX_GAIN_MAX) * TRIG_AMP_MAX + 12750) // 25500)


def output_report(bt, seq, small, large, v2=None, rgb=None, triggers=None):
    """Raport wyjściowy DualSense: wibracja i (opcjonalnie) kolor lightbara i opór spustów. small = silnik prawy
    (słaby), large = lewy (mocny), 0..255. v2: True = oprogramowanie pada >= 2.21 (flaga "vibration v2"), False =
    starsze, None = nieznane -> obie flagi (jak sterownik jądra: starsze pady wymagają flagi v1, nowsze v2).
    triggers: (efekt L2, efekt R2) z trigger_effect() albo None = spusty bez zmian."""
    common = bytearray(47)
    common[0] = FLAG0_HAPTICS_SELECT | (0 if v2 else FLAG0_COMPAT_VIBRATION)
    if triggers is not None:
        common[0] |= FLAG0_RIGHT_TRIGGER | FLAG0_LEFT_TRIGGER
        common[LEFT_TRIGGER_AT:LEFT_TRIGGER_AT + TRIGGER_LEN] = triggers[0]
        common[RIGHT_TRIGGER_AT:RIGHT_TRIGGER_AT + TRIGGER_LEN] = triggers[1]
    common[2], common[3] = small & 0xFF, large & 0xFF        # motor_right, motor_left
    common[38] = FLAG2_COMPAT_VIBRATION2 if v2 is not False else 0   # valid_flag2
    if rgb is not None:
        common[1] = FLAG1_LIGHTBAR
        common[LIGHTBAR_AT:LIGHTBAR_AT + 3] = bytes(v & 0xFF for v in rgb)
    if not bt:
        return bytes([0x02]) + bytes(common) + bytes(15)
    rep = bytearray([0x31, (seq & 0x0F) << 4, OUT_TAG]) + common + bytes(24)
    crc = zlib.crc32(CRC_SEED + bytes(rep)) & 0xFFFFFFFF
    return bytes(rep) + crc.to_bytes(4, "little")


def _uevent(path):
    try:
        with open(path) as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return {}


def _by_uniq(uniq, sys_root):
    """Zapas: hidraw, którego urządzenie HID ma ten sam HID_UNIQ (adres MAC pada) - gdy ścieżka w sysfs od eventN
    do urządzenia HID jest nietypowa (inne jądro / stos Bluetooth)."""
    uniq = (uniq or "").lower()
    if not uniq:
        return None, False
    for raw in sorted(glob.glob(f"{sys_root}/class/hidraw/hidraw*")):
        ev = _uevent(os.path.join(raw, "device", "uevent"))
        if ev.get("HID_UNIQ", "").lower() == uniq:
            bus = int(ev.get("HID_ID", "0:0:0").split(":")[0], 16)
            return f"/dev/{os.path.basename(raw)}", bus == BUS_BLUETOOTH
    return None, False


def find_hidraw(pad_dev, sys_root="/sys"):
    """-> (/dev/hidrawN, czy Bluetooth) dla tego samego urządzenia HID co pad evdev, albo (None, False)."""
    try:
        node = os.path.realpath(f"{sys_root}/class/input/{os.path.basename(pad_dev.path)}")
    except (OSError, AttributeError):
        node = "/"
    hid = node
    while hid not in ("/", "") and not _HID_DIR.match(os.path.basename(hid)):
        hid = os.path.dirname(hid)
    if hid not in ("/", ""):
        found = sorted(glob.glob(os.path.join(hid, "hidraw", "hidraw*")))
        if found:
            bus = int(_HID_DIR.match(os.path.basename(hid)).group(1), 16)
            return f"/dev/{os.path.basename(found[0])}", bus == BUS_BLUETOOTH
    return _by_uniq(getattr(pad_dev, "uniq", ""), sys_root)


def kernel_has_hidraw(sys_root="/sys"):
    """Czy jądro ma hidraw (CONFIG_HIDRAW): klasa urządzeń hidraw w sysfs."""
    return os.path.isdir(f"{sys_root}/class/hidraw")


def ensure_node(path, sys_root="/sys"):
    """Brak /dev/hidrawN przy istniejącym urządzeniu w sysfs (system bez udev / mdev) -> mknod z numerów z sysfs
    (tylko root). Bez wyjątków - błąd wyjdzie przy otwieraniu."""
    if os.path.exists(path):
        return
    try:
        with open(f"{sys_root}/class/hidraw/{os.path.basename(path)}/dev") as f:
            major, minor = map(int, f.read().split(":"))
        os.mknod(path, stat.S_IFCHR | 0o600, os.makedev(major, minor))
        print(f"Created missing device node {path}")
    except (OSError, ValueError):
        pass


def has_ff(dev):
    try:
        return ec.FF_RUMBLE in dev.capabilities().get(ec.EV_FF, [])
    except (OSError, AttributeError):
        return False


WHY_TEXT = {WHY_OK: "ok", WHY_OFF: "rumble = off / evdev in config",
            WHY_ACCESS: "no permission for /dev/hidraw - run with doas or install the service (doas ./install-service.sh)",
            WHY_NO_HIDRAW: "kernel without CONFIG_HIDRAW",
            WHY_NOT_FOUND: "hidraw of this pad not found", WHY_OPEN: "cannot open hidraw"}


class Rumble:
    """Wibracja jednego pada. set(small, large) przy każdym pakiecie feedbacku; close() przy rozłączeniu.

    W trybie "auto" działają OBA sposoby naraz, jeśli są dostępne: sterownik (evdev FF) i raport HID (hidraw).
    Ta sama moc silników wysłana dwiema drogami nic nie psuje, a wibracja działa także wtedy, gdy jedna z nich
    po cichu nic nie robi (jądro bez pełnej obsługi wibracji, nietypowy stos Bluetooth). Sposób, który zgłosi błąd,
    jest wyłączany."""

    def __init__(self, idx, pad_dev, mode="auto", hidraw=None):
        self.idx, self.dev = idx, pad_dev
        self.methods = []             # "evdev" i/lub "hidraw" - w tej kolejności
        self.fd, self.bt, self.seq, self.ff_id, self.path = None, False, 0, None, None
        self.v2, self.fw = None, None
        self.why = WHY_OFF if mode in ("off", "evdev") else WHY_OK   # powód braku raportu HID (pakiet do receivera)
        self.logged = 0               # ile pierwszych poleceń wibracji zapisać w logu (diagnostyka)
        self.fails = {}               # sposób -> liczba kolejnych błędów zapisu (FAIL_MAX)
        self.cur = (0, 0)
        self.rgb = None               # kolor lightbara wysyłany razem z wibracją (tylko raport HID)
        self.trig = None              # efekty spustów (L2, R2) wysłane do pada albo None = nigdy nie ruszane
        self.fx = None                # ustawienia spustów z PC (wartości trig2) albo None = nic nie przyszło
        self._rv = [None, None]       # wibracja z gry na L2 / R2: efekt albo None
        self._rv_off = [None, None]   # od kiedy silnik tego spustu stoi (po RFX_HOLD_S spust wraca do efektu)
        self._rv_timer = None
        self._trig_watch = None       # strażnik efektów spustów (TRIG_WATCHDOG_S bez odświeżenia z PC -> wyłączone)
        self.t_set = 0.0
        self._watch = None
        self._repeat = None
        if mode in ("auto", "evdev") and has_ff(pad_dev):
            self.methods.append("evdev")
        if mode in ("auto", "hidraw") and self._open_hidraw(hidraw):
            self.methods.append("hidraw")
        print(f"Controller {idx}: rumble " + (self.describe() if self.methods else
                                              f"unavailable (no evdev FF; {WHY_TEXT[self.why]})"
                                              if mode != "off" else "off"))

    @property
    def mode(self):
        """Główny sposób (do logów i testów): pierwszy działający albo None."""
        return self.methods[0] if self.methods else None

    @property
    def owns_lightbar(self):
        """Raport HID niesie też kolor lightbara. Kolor przez sterownik (sysfs) wysyła WŁASNY raport wyjściowy pada
        w osobnym momencie - pad przyjmował wtedy tylko jeden z nich i wibracja z przycisku testu (zmienia kolor)
        działała co którymś naciśnięciem. Jeden raport z oboma = brak wyścigu."""
        return "hidraw" in self.methods

    @property
    def bits(self):
        """Sposoby jako bity (pakiet wersji do receivera): 1 = sterownik, 2 = raport HID, 4 = opór spustów,
        8 = efekty spustów z trig2 (klik, wibracja, wibracja z gry)."""
        hid = "hidraw" in self.methods
        return ("evdev" in self.methods) | hid << 1 | (CAP_TRIGGERS | CAP_TRIG_FX if hid else 0)

    def describe(self):
        parts = []
        if "evdev" in self.methods:
            parts.append("kernel driver (evdev FF)")
        if "hidraw" in self.methods:
            fw = f"firmware {self.fw:04x}" if self.fw is not None else "firmware unknown"
            mode = "v2" if self.v2 else "v1" if self.v2 is False else "v1+v2"
            parts.append(f"direct HID reports ({self.path}, {fw}, vibration {mode})")
        return "via " + " + ".join(parts)

    def _open_hidraw(self, given=None):
        path, bt = given or find_hidraw(self.dev)
        if not path:
            self.why = WHY_NOT_FOUND if kernel_has_hidraw() else WHY_NO_HIDRAW
            return False
        if given is None:
            ensure_node(path)
        self._close_fd()
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)   # RDWR: także odczyt wersji oprogramowania
        except OSError as e:
            self.why = WHY_ACCESS if e.errno in (errno.EACCES, errno.EPERM) else WHY_OPEN
            print(f"Controller {self.idx}: cannot open {path} for rumble: {e.strerror}")
            return False
        self.why = WHY_OK
        self.path, self.bt = path, bt
        self.fw = firmware_version(self.fd)
        self.v2 = None if self.fw is None else self.fw >= VIBRATION_V2_FROM
        return True

    # ---------- sterowanie ----------
    def set(self, small, large, rgb=None):
        """Wibracja (i kolor lightbara, gdy owns_lightbar). Zmiana idzie od razu i drugi raz po REPEAT_S
        (zgubiony / nadpisany raport nie zostawia pada bez wibracji); bez zmian - odświeżenie najwyżej co 0.4 s."""
        now = time.monotonic()
        if not self.methods:
            return
        rgb = rgb if rgb is not None else self.rgb
        changed = (small, large) != self.cur or rgb != self.rgb
        if not changed and now - self.t_set < 0.4:
            return                                   # bez zmian i świeże - nic nie wysyłaj
        if self.logged < 3 and (small or large):
            self.logged += 1
            print(f"Controller {self.idx}: rumble {small}/{large} from PC {self.describe()}")
        self.rgb = rgb
        self._send(small, large)
        self.cur, self.t_set = (small, large), now
        self._arm_watchdog()
        if changed:
            self._arm_repeat()

    def set_triggers(self, vals):
        """Efekty spustów z PC: wartości trig2 (12) albo starego trig (4: sam opór). -> True, gdy wysłane do pada
        (raport HID). Receiver odświeża je co kilka sekund, więc pad po ponownym połączeniu wraca do efektów z profilu."""
        vals = tuple(vals)
        if len(vals) == 4:
            vals = trig2_from_legacy(vals)
        if vals != self.fx:
            print(f"Controller {self.idx}: triggers L2 {vals[0:4]} R2 {vals[4:8]} game rumble {vals[8:11]}")
        self.fx = vals
        if not vals[8] & RFX_ON:
            self._rv, self._rv_off = [None, None], [None, None]
        self._arm_trig_watch(any(vals))
        if "hidraw" not in self.methods:
            return False
        trig = self._want_triggers(*self.cur)
        try:
            self.seq = (self.seq + 1) & 0x0F
            os.write(self.fd, output_report(self.bt, self.seq, *self.cur, self.v2, self.rgb, trig))
        except (BlockingIOError, InterruptedError):
            return False                    # pełna kolejka - następne odświeżenie z PC dośle efekt
        except OSError as e:
            self._failed("hidraw", e)
            return False
        self.fails["hidraw"] = 0
        self.trig = trig
        return True

    def _arm_trig_watch(self, on):
        if self._trig_watch is not None:
            self._trig_watch.cancel()
            self._trig_watch = None
        if on:
            try:
                self._trig_watch = asyncio.get_running_loop().call_later(TRIG_WATCHDOG_S, self._trig_expired)
            except RuntimeError:
                pass

    def _trig_expired(self):
        self._trig_watch = None
        if self.fx is not None and any(self.fx):
            print(f"Controller {self.idx}: no trigger refresh from PC for {TRIG_WATCHDOG_S:g}s - triggers released")
            self.set_triggers(TRIG2_OFF)

    def _want_triggers(self, small, large, now=None):
        """Efekty, które pad powinien mieć teraz: z ustawień, a przy wibracji z gry (i RFX_HOLD_S po niej) - drganie
        wg silnika (L2 = duży, R2 = mały). None = PC nic nie ustawił (spustów nie ruszamy)."""
        fx = self.fx
        if fx is None:
            return None
        out = [effect(*fx[0:4]), effect(*fx[4:8])]
        flags, gain, freq = fx[8:11]
        if not flags & RFX_ON:
            return tuple(out)
        now = time.monotonic() if now is None else now
        hold = False
        for k, motor in enumerate((large, small)):
            amp = rumble_amp(motor, gain)
            if amp:
                self._rv[k], self._rv_off[k] = vibration_effect(0, amp, freq or 1), None
            elif self._rv[k] is not None:
                if self._rv_off[k] is None:
                    self._rv_off[k] = now
                if now - self._rv_off[k] >= RFX_HOLD_S - 0.005:
                    self._rv[k] = self._rv_off[k] = None
                else:
                    hold = True
            if self._rv[k] is not None:
                out[k] = self._rv[k]
        self._arm_rv(hold)
        return tuple(out)

    def _arm_rv(self, hold):
        """Po RFX_HOLD_S ciszy silników spust ma wrócić do efektu, choćby nic więcej nie przyszło z PC."""
        if self._rv_timer is not None:
            self._rv_timer.cancel()
            self._rv_timer = None
        if hold:
            try:
                self._rv_timer = asyncio.get_running_loop().call_later(RFX_HOLD_S, self._rv_expired)
            except RuntimeError:
                pass

    def _rv_expired(self):
        self._rv_timer = None
        if self.methods:
            self._send(*self.cur, only="hidraw")

    def _arm_repeat(self):
        if self._repeat is not None:
            self._repeat.cancel()
        try:
            self._repeat = asyncio.get_running_loop().call_later(REPEAT_S, self._send_again)
        except RuntimeError:
            self._repeat = None

    def _send_again(self):
        self._repeat = None
        if self.methods:
            self._send(*self.cur, only="hidraw")

    def _send(self, small, large, only=None):
        for m in list(self.methods):
            if only and m != only:
                continue
            try:
                (self._send_hidraw if m == "hidraw" else self._send_evdev)(small, large)
            except (BlockingIOError, InterruptedError):
                pass        # chwilowo pełna kolejka raportów - to nie awaria; powtórka / odświeżenie dośle stan
            except OSError as e:
                if self._failed(m, e) and m == "evdev" and "hidraw" not in self.methods and self._open_hidraw():
                    self.methods.append("hidraw")
                    self._send(small, large, only="hidraw")    # z tą samą obsługą błędów (wyjątek nie wyjdzie do pętli)
            else:
                self.fails[m] = 0

    def _failed(self, m, e):
        """Błąd zapisu sposobem m. -> True, gdy sposób został wyłączony (FAIL_MAX kolejnych albo pada już nie ma)."""
        n = self.fails[m] = self.fails.get(m, 0) + 1
        if n < FAIL_MAX and e.errno not in FATAL_ERRNO:
            if n == 1:
                print(f"Controller {self.idx}: rumble via {m} failed ({e!r}) - retrying")
            return False
        print(f"Controller {self.idx}: rumble via {m} failed ({e!r}) - not using it any more")
        if m in self.methods:
            self.methods.remove(m)
        self.fails[m] = 0
        if m == "hidraw":
            self._close_fd()                          # bez tego deskryptor wyciekał (ponowne otwarcie nadpisywało fd)
        return True

    def _close_fd(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def _send_hidraw(self, small, large):
        """Wibracja (i kolor); efekty spustów tylko, gdy mają się zmienić (wibracja z gry na spustach)."""
        trig = self._want_triggers(small, large)
        if trig == self.trig:
            trig = None
        self.seq = (self.seq + 1) & 0x0F
        os.write(self.fd, output_report(self.bt, self.seq, small, large, self.v2, self.rgb, trig))
        if trig is not None:
            self.trig = trig

    def _send_evdev(self, small, large):
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
        for name in ("_watch", "_repeat", "_rv_timer", "_trig_watch"):
            t = getattr(self, name)
            if t is not None:
                t.cancel()
                setattr(self, name, None)
        if self.cur != (0, 0):
            self._send(0, 0)
            self.cur = (0, 0)       # set_triggers niżej wysyła raport z bieżącą mocą silników - nie może jej włączyć od nowa
        if self.trig is not None and any(t[0] != TRIG_OFF for t in self.trig):
            self.set_triggers(TRIG2_OFF)         # nadajnik się zamyka - spust nie może zostać twardy
        self._arm_rv(False)
        if "evdev" in self.methods and self.ff_id is not None:
            try:
                self.dev.erase_effect(self.ff_id)
            except OSError:
                pass
        self._close_fd()
        self.methods, self.cur = [], (0, 0)


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
              f"evdev FF {'yes' if has_ff(d) else 'NO (kernel without CONFIG_PLAYSTATION_FF)'}"
              + ("" if kernel_has_hidraw() else ", kernel WITHOUT CONFIG_HIDRAW"))
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
