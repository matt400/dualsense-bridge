"""Sender: wybór kontrolera Bluetooth, gdy jest ich kilka (wbudowane radio + dongle USB).

Wbudowane radio telefonu / płytki (UART lub SDIO, np. WCN3990 w OnePlus 6) potrafi kolejkować raporty pada - kilka
do kilkunastu ms w odcinku „Bluetooth (ponad minimum)” na karcie pada; dongle USB zwykle ~1 ms. Tryb (sender.toml
`bt_adapter`, zmiana z okna receivera zapisana w bt.json):

  auto     najlepszy kontroler: dongle USB > wewnętrzny USB (laptop: port oznaczony przez firmware jako stały) >
           wbudowany na UART / SDIO; przy remisie niższy numer hciN. Pozostałe blokuje rfkill.
  builtin  odwrotnie - wbudowany przed USB (dongle zostaje wpięty, ale nieużywany)
  manual   nic nie zmienia (Bluetooth ustawiasz sam: rfkill, bluetoothctl)

Kontroler poznajemy po ścieżce urządzenia w /sys (szyna USB, atrybut `removable`), nie po MAC ani numerze hciN -
numery zmieniają się z kolejnością wykrywania. Wybrany dostaje link policy bez SNIFF (pad nie usypia łącza: pierwsze
naciśnięcie po bezruchu bez kilku ms opóźnienia). Zestaw kontrolerów sprawdzamy co odświeżenie stanu Bluetooth, więc
wpięcie / wyjęcie dongla w trakcie pracy przełącza bez restartu; wyjęty dongle odblokowuje wbudowany.
Pady trzeba raz sparować z każdym kontrolerem (każdy ma własny adres).
"""
import contextlib
import fcntl
import json
import shutil
import socket
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from proto import BTA_AUTO, BTA_BUILTIN, BTA_MANUAL, write_json

SYSFS = Path("/sys/class/bluetooth")
MODES = {"auto": BTA_AUTO, "builtin": BTA_BUILTIN, "manual": BTA_MANUAL}
MODE_NAMES = {v: k for k, v in MODES.items()}
AF_BLUETOOTH = getattr(socket, "AF_BLUETOOTH", 31)
BTPROTO_HCI = 1
HCISETLINKPOL = 0x400448CD          # _IOW('H', 205, int)
HCI_LP_RSWITCH = 0x0001             # link policy: tylko zmiana roli (bez HOLD / SNIFF / PARK)
BUILTIN, USB_FIXED, USB = 0, 1, 2   # ranga kontrolera (auto bierze najwyższą)
KIND_TEXT = {BUILTIN: "built-in", USB_FIXED: "internal USB", USB: "USB"}


@dataclass(frozen=True, slots=True)
class Ctrl:
    name: str                       # hciN
    rank: int                       # BUILTIN / USB_FIXED / USB

    @property
    def index(self) -> int:
        return int(self.name[3:])

    @property
    def usb(self) -> bool:
        return self.rank != BUILTIN


def _rank(dev: Path) -> int:
    """Szyna kontrolera z jego ścieżki w /sys: USB (wymienny / nieznany) albo USB stały, inaczej wbudowany."""
    if "/usb" not in str(dev):
        return BUILTIN
    for parent in dev.parents:      # interfejs USB -> urządzenie USB z atrybutem removable
        with contextlib.suppress(OSError):
            return USB_FIXED if (parent / "removable").read_text().strip() == "fixed" else USB
    return USB


def scan(sysfs: Path = SYSFS) -> tuple[Ctrl, ...]:
    """Kontrolery z /sys/class/bluetooth (bez wpisów połączeń hciN:uchwyt), po numerze."""
    found = [Ctrl(p.name, _rank(p.resolve())) for p in sysfs.glob("hci*") if p.name[3:].isdigit()]
    return tuple(sorted(found, key=lambda c: c.index))


def choose(ctrls: tuple[Ctrl, ...], mode: int) -> Ctrl | None:
    """Kontroler do użycia albo None (tryb ręczny / brak kontrolerów)."""
    if mode == BTA_MANUAL or not ctrls:
        return None
    sign = 1 if mode == BTA_AUTO else -1
    return max(ctrls, key=lambda c: (sign * c.rank, -c.index))


def set_blocked(name: str, blocked: bool, sysfs: Path = SYSFS) -> None:
    for soft in (sysfs / name).glob("rfkill*/soft"):
        if soft.read_text().strip() != ("1" if blocked else "0"):
            soft.write_text("1" if blocked else "0")


def no_sniff(name: str) -> bool:
    """Domyślna link policy kontrolera bez SNIFF - ioctl jak `hciconfig hciN lp RSWITCH` (jądro zapamiętuje ją
    dla nowych połączeń), bez zależności od przestarzałego hciconfig; ten tylko jako zapas."""
    try:
        with socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI) as s:
            fcntl.ioctl(s.fileno(), HCISETLINKPOL, struct.pack("@HI", int(name[3:]), HCI_LP_RSWITCH))
        return True
    except OSError:
        pass
    if tool := shutil.which("hciconfig"):
        return subprocess.run([tool, name, "lp", "RSWITCH"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False).returncode == 0
    return False


def apply(ctrls: tuple[Ctrl, ...], mode: int, sysfs: Path = SYSFS, sniff=None) -> Ctrl | None:
    """Wybrany odblokowany i bez SNIFF, reszta zablokowana. Tryb ręczny: nic. -> wybrany."""
    pick = choose(ctrls, mode)
    if pick is None:
        return None
    for c in ctrls:
        set_blocked(c.name, c != pick, sysfs)
    if not (sniff or no_sniff)(pick.name):
        print(f"Bluetooth adapter: cannot disable sniff mode on {pick.name} (needs root)")
    return pick


def unblock_all(ctrls: tuple[Ctrl, ...], sysfs: Path = SYSFS) -> None:
    """Przejście na tryb ręczny z okna: nic nie zostaje zablokowane przez nas."""
    for c in ctrls:
        set_blocked(c.name, False, sysfs)


def describe(ctrls: tuple[Ctrl, ...], pick: Ctrl | None, mode: int) -> str:
    rest = ", ".join(f"{c.name} ({KIND_TEXT[c.rank]})" for c in ctrls if c != pick)
    if pick is None:
        return f"Bluetooth adapter ({MODE_NAMES[mode]}): " + (rest or "none")
    return (f"Bluetooth adapter ({MODE_NAMES[mode]}): {pick.name} ({KIND_TEXT[pick.rank]})"
            + (f", blocked: {rest}" if rest else ""))


# ---------- tryb: sender.toml (domyślny) + bt.json (wybór z okna) ----------
def mode_from(text: str) -> int:
    return MODES.get(str(text).strip().lower(), BTA_AUTO)


def load_mode(path: str, default: int) -> int:
    """Tryb zapisany z okna albo domyślny z konfiguracji."""
    if not path:
        return default
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return MODES[raw["mode"]] if isinstance(raw, dict) and raw.get("mode") in MODES else default
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        print(f"Bluetooth adapter: {path} ignored ({e!r})")
        return default


def save_mode(path: str, mode: int) -> None:
    if path:
        try:
            write_json(path, {"mode": MODE_NAMES[mode]}, indent=2)
        except OSError as e:
            print(f"Bluetooth adapter: cannot write {path}: {e!r}")
