"""Sender: przycisk mute (mikrofon) DualSense z surowych raportów HID (hidraw).

Sterownik hid-playstation nie wysyła mute przez evdev (sam przełącza nim diodę mikrofonu), więc czytamy go
równolegle z węzła /dev/hidrawN tego samego pada. Każdy czytelnik hidraw dostaje własną kopię raportów -
nic nie zabieramy sterownikowi ani evdev. Odczyt jest nieblokujący (loop.add_reader), a pakiet idzie
tylko przy zmianie stanu przycisku. Brak dostępu do hidraw (uruchomienie bez doas) = pad działa bez mute.

Raport wejściowy: USB 0x01 (dane od bajtu 1), Bluetooth 0x31 (dane od bajtu 2); w danych buttons[2] jest
na pozycji 9, mute = bit 2 (jak DS_BUTTONS2_MIC_MUTE w jądrze).

Weryfikacja formatu: zanim mute zacznie działać, pierwsze raporty są porównywane z evdev - bajty 0-5 danych to gałki
i spusty (x, y, rx, ry, L2, R2), które muszą zgadzać się z osiami pada. Tak wybierany jest początek danych (gdyby
jądro / BlueZ podawały raport inaczej), a przy niezgodności mute zostaje wyłączony z komunikatem, zamiast
zgłaszać przypadkowy bit (np. "wiecznie wciśnięty" mute psułby skróty i tryb konsoli).

Stan dostępności (c.mute_caps) trafia do receivera w pakiecie T_CAPS - okno pokazuje, czy mute działa.
"""
import asyncio
import glob
import os
import re

from proto import CAPS_MUTE_FAIL as CAP_FAIL, CAPS_MUTE_OK as CAP_OK, CAPS_MUTE_OPEN as CAP_OPEN

MUTE_MASK = 0x04
_BASE = {0x01: 1, 0x31: 2}               # id raportu -> spodziewany początek danych (x, y, rx, ry, z, rz, seq, buttons)
_SIZE = {0x01: 64, 0x31: 78}
BUTTONS2 = 9                             # buttons[2] względem początku danych
CHECK_REPORTS = 40                       # tyle raportów porównujemy z evdev przed włączeniem mute
UNKNOWN_LIMIT = 500                      # tyle raportów bez ani jednego pełnego = nieznany format, komunikat
TOL = 12                                 # różnica osi hidraw - evdev (evdev ma strefę martwą i bywa o raport w tyle)
_HID_DIR = re.compile(r"^[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")


def full_report(report):
    """Czy to pełny raport wejściowy DualSense (USB 0x01 / BT 0x31), a nie np. krótki 0x01 trybu uproszczonego."""
    return bool(report) and report[0] in _SIZE and len(report) >= _SIZE[report[0]]


def mute_state(report, base=None):
    """-> True / False (mute wciśnięty) albo None, gdy to nie pełny raport wejściowy."""
    if not full_report(report):
        return None
    return bool(report[(base or _BASE[report[0]]) + BUTTONS2] & MUTE_MASK)


def axes_match(report, base, pad):
    """Czy bajty raportu od `base` (x, y, rx, ry, z, rz) zgadzają się z osiami evdev pad = [X, Y, Z, RX, RY, RZ]."""
    want = (pad[0], pad[1], pad[3], pad[4], pad[2], pad[5])
    return all(abs(report[base + k] - v) <= TOL for k, v in enumerate(want))


class FormatCheck:
    """Wybór początku danych na podstawie zgodności osi z evdev. done -> base (int) albo None (nie pasuje nic)."""

    def __init__(self):
        self.n, self.hits, self.done, self.base = 0, {}, False, None
        self.other = 0                   # raporty innego typu (np. krótkie 0x01 trybu uproszczonego)

    def feed(self, report, pad):
        if self.done or not full_report(report):
            return self.done
        self.n += 1
        exp = _BASE[report[0]]
        for b in (exp, *(x for x in (1, 2, 3) if x != exp)):
            if axes_match(report, b, pad):
                self.hits[b] = self.hits.get(b, 0) + 1
        if self.n >= CHECK_REPORTS:
            self.done = True
            best = max(self.hits.values(), default=0)
            if best >= self.n * 0.7:
                # spodziewany początek wygrywa remis (pad leżący: gałki 128 pasują na kilku pozycjach)
                self.base = exp if self.hits.get(exp) == best else max(self.hits, key=self.hits.get)
        return self.done


def find_hidraw(pad_dev, sys_root="/sys"):
    """Węzeł /dev/hidrawN należący do tego samego urządzenia HID co pad evdev (wspólny katalog VVVV:PPPP:...)."""
    try:
        node = os.path.realpath(f"{sys_root}/class/input/{os.path.basename(pad_dev.path)}")
    except (OSError, AttributeError):
        return None
    hid = node
    while hid not in ("/", "") and not _HID_DIR.match(os.path.basename(hid)):
        hid = os.path.dirname(hid)
    if hid in ("/", ""):
        return None
    found = sorted(glob.glob(os.path.join(hid, "hidraw", "hidraw*")))
    return f"/dev/{os.path.basename(found[0])}" if found else None


async def stream_mute(c, path=None, fd=None):
    """Czyta raporty hidraw pada i woła c.set_mute() przy zmianie. Kończy się, gdy węzeł zniknie (pad odszedł).
    fd: gotowy deskryptor (testy: gniazdo SOCK_SEQPACKET zachowuje granice raportów jak hidraw)."""
    path = path or ("fd" if fd is not None else find_hidraw(c.pad_dev))
    if not path:
        print(f"Controller {c.idx}: mute button unavailable (no hidraw node found)")
        c.mute_caps = CAP_FAIL
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK) if fd is None else fd
    except OSError as e:
        print(f"Controller {c.idx}: mute button unavailable ({path}: {e.strerror}) - run the sender with doas")
        c.mute_caps = CAP_FAIL
        return
    loop = asyncio.get_running_loop()
    done = loop.create_future()
    check = FormatCheck()
    c.mute_caps = CAP_OPEN
    first = [None]                   # (id, długość) pierwszego raportu - do komunikatu przy złym formacie

    def readable():
        try:
            while True:
                data = os.read(fd, 128)
                if not data:
                    raise OSError("hidraw closed")
                if data[0] == 0xA1 and len(data) > 1:
                    data = data[1:]          # nagłówek HIDP (DATA | INPUT), gdyby stos BT go nie zdjął
                if first[0] is None:
                    first[0] = (data[0], len(data))
                if not check.done:
                    check.other += not full_report(data)
                    if check.other == UNKNOWN_LIMIT and not check.n:
                        c.mute_caps = CAP_OPEN | CAP_FAIL
                        print(f"Controller {c.idx}: mute button DISABLED - unknown HID reports on {path} "
                              f"(report 0x{data[0]:02x}, {len(data)} B); please report this")
                    if check.feed(data, c.pad):
                        _checked(c, path, check, first[0])
                    continue
                if check.base is None:
                    continue
                m = mute_state(data, check.base)
                if m is not None and m != c.mute:
                    c.set_mute(m)
        except BlockingIOError:
            pass
        except OSError as e:
            if not done.done():
                done.set_result(e)

    loop.add_reader(fd, readable)
    print(f"Controller {c.idx}: reading mute button from {path}")
    try:
        await done
    finally:
        loop.remove_reader(fd)
        os.close(fd)
        c.mute_caps = 0
        if c.mute:
            c.set_mute(False)        # pad zniknął z wciśniętym mute - nie zostawiaj go "trzymanego"


def _checked(c, path, check, first):
    rid, size = first or (0, 0)
    if check.base is None:
        c.mute_caps = CAP_OPEN | CAP_FAIL
        print(f"Controller {c.idx}: mute button DISABLED - {path} reports do not match the pad "
              f"(report 0x{rid:02x}, {size} B, matches {check.hits} of {check.n}); please report this")
        return
    c.mute_caps = CAP_OPEN | CAP_OK
    note = "" if check.base == _BASE.get(rid) else f" (data offset {check.base}, not the usual {_BASE.get(rid)})"
    print(f"Controller {c.idx}: mute button OK (report 0x{rid:02x}, {size} B){note}")
