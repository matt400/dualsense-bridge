"""Sender: wybór kontrolera Bluetooth, gdy jest ich kilka (wbudowane radio + dongle USB).

Wbudowane radio telefonu / płytki (UART lub SDIO, np. WCN3990 w OnePlus 6) potrafi kolejkować raporty pada - kilka
do kilkunastu ms w odcinku „Bluetooth (ponad minimum)” na karcie pada; dongle USB zwykle ~1 ms. Tryb (sender.toml
`bt_adapter`, zmiana z okna receivera zapisana w bt.json):

  auto     najlepszy kontroler: dongle USB > wewnętrzny USB (laptop: port oznaczony przez firmware jako stały) >
           wbudowany na UART / SDIO; przy remisie niższy numer hciN. Pozostałe blokuje rfkill.
  pick     konkretny kontroler wybrany w oknie (zapamiętany po identyfikatorze urządzenia: USB vid:pid[:serial],
           wbudowany - compatible / ścieżka; nie po hciN, który zmienia się z kolejnością wykrywania). Gdy go nie ma
           (dongle wyjęty) - jak auto, a po powrocie znów on. W sender.toml: "builtin" = wbudowany przed USB.
  manual   nic nie zmienia (Bluetooth ustawiasz sam: rfkill, bluetoothctl)

Wybrany dostaje link policy bez SNIFF (pad nie usypia łącza: pierwsze naciśnięcie po bezruchu bez kilku ms opóźnienia);
jądro przywraca SNIFF przy każdym włączeniu kontrolera, więc jest sprawdzana i poprawiana przy każdym sprawdzeniu.
Zestaw kontrolerów sprawdzamy co kilka sekund (sender_bt, także bez receivera - PC wyłączony, Wake-on-LAN), więc
wpięcie / wyjęcie dongla w trakcie pracy przełącza bez restartu; wyjęty dongle odblokowuje wbudowany. Pady trzeba raz
sparować z każdym kontrolerem (własny adres).
"""
import contextlib
import fcntl
import json
import shutil
import socket
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from proto import BTA_AUTO, BTA_MANUAL, BTA_PICK, write_json

SYSFS = Path("/sys/class/bluetooth")
MODES = {"auto": BTA_AUTO, "pick": BTA_PICK, "manual": BTA_MANUAL}
MODE_NAMES = {v: k for k, v in MODES.items()}
RULE_BUILTIN = "builtin"            # klucz-reguła z sender.toml: wbudowany przed USB
AF_BLUETOOTH = getattr(socket, "AF_BLUETOOTH", 31)
BTPROTO_HCI = 1
HCISETLINKPOL = 0x400448CD          # _IOW('H', 205, int)
HCIGETDEVINFO = 0x800448D3          # _IOR('H', 211, int) -> struct hci_dev_info
DEV_INFO_SIZE, DEV_FLAGS_AT, DEV_POLICY_AT = 92, 16, 36   # rozmiar hci_dev_info, pola flags i link_policy
HCI_UP = 0x0001                     # bit flags: kontroler włączony
HCI_LP_RSWITCH, HCI_LP_SNIFF = 0x0001, 0x0004   # link policy: zmiana roli (bez HOLD / SNIFF / PARK); SNIFF
BUILTIN, USB_FIXED, USB = 0, 1, 2   # ranga kontrolera (auto bierze najwyższą)
KIND_TEXT = {BUILTIN: "built-in", USB_FIXED: "internal USB", USB: "USB"}
# prefiks compatible z device tree -> producent (wbudowane radia telefonów i płytek)
VENDORS = {"qcom": "Qualcomm", "brcm": "Broadcom", "infineon": "Infineon", "cypress": "Cypress", "realtek": "Realtek",
           "mediatek": "MediaTek", "ti": "Texas Instruments", "marvell": "Marvell", "nxp": "NXP", "intel": "Intel",
           "amlogic": "Amlogic", "rockchip": "Rockchip", "allwinner": "Allwinner", "sprd": "Unisoc"}


@dataclass(frozen=True, slots=True)
class Ctrl:
    name: str                                       # hciN
    rank: int                                       # BUILTIN / USB_FIXED / USB
    label: str = field(default="", compare=False)   # nazwa urządzenia do okna
    key: str = ""                                   # trwały identyfikator (wybór z okna)

    @property
    def index(self) -> int:
        return int(self.name[3:])

    @property
    def usb(self) -> bool:
        return self.rank != BUILTIN


def _read(path: Path) -> str:
    with contextlib.suppress(OSError):
        return path.read_text(errors="replace").strip("\0\n ")
    return ""


def _usb_info(dev: Path) -> tuple[int, str, str]:
    """-> (ranga, nazwa, klucz) kontrolera na USB: nazwa z deskryptora (product / manufacturer), klucz vid:pid[:serial]."""
    usb = next((p for p in dev.parents if (p / "idVendor").is_file()), None)
    if usb is None:
        return USB, "USB Bluetooth", "usb:?"
    vid, pid, serial = _read(usb / "idVendor"), _read(usb / "idProduct"), _read(usb / "serial")
    product, maker = _read(usb / "product"), _read(usb / "manufacturer")
    if product and maker and maker.lower() not in product.lower() and maker.lower() not in ("generic", "unknown"):
        label = f"{maker} {product}"
    else:
        label = product or maker or f"USB {vid}:{pid}"
    rank = USB_FIXED if _read(usb / "removable") == "fixed" else USB
    return rank, label, ":".join(filter(None, ("usb", vid, pid, serial)))


def _builtin_info(dev: Path) -> tuple[str, str]:
    """-> (nazwa, klucz) wbudowanego kontrolera: compatible z device tree (qcom,wcn3990-bt -> Qualcomm WCN3990),
    inaczej sterownik, inaczej ogólna nazwa; klucz - compatible albo nazwa urządzenia nadrzędnego."""
    for p in dev.parents:
        if p.name in ("devices", "/", ""):
            break
        compat = _read(p / "of_node" / "compatible").split("\0")[0]
        if compat:
            vendor, _sep, chip = compat.partition(",")
            chip = chip.removesuffix("-bt").upper() if chip else ""
            return f"{VENDORS.get(vendor, vendor.capitalize())} {chip}".strip(), f"builtin:{compat}"
        if (p / "driver").exists():
            return f"Bluetooth ({(p / 'driver').resolve().name})", f"builtin:{p.name}"
    return "Bluetooth", f"builtin:{dev.parent.parent.name}"


def _ctrl(link: Path) -> Ctrl:
    dev = link.resolve()
    if "/usb" in str(dev):
        rank, label, key = _usb_info(dev)
    else:
        rank, (label, key) = BUILTIN, _builtin_info(dev)
    return Ctrl(link.name, rank, label, key)


def scan(sysfs: Path = SYSFS) -> tuple[Ctrl, ...]:
    """Kontrolery z /sys/class/bluetooth (bez wpisów połączeń hciN:uchwyt), po numerze."""
    found = [_ctrl(p) for p in sysfs.glob("hci*") if p.name[3:].isdigit()]
    return tuple(sorted(found, key=lambda c: c.index))


def choose(ctrls: tuple[Ctrl, ...], mode: int, key: str = "") -> Ctrl | None:
    """Kontroler do użycia albo None (tryb ręczny / brak kontrolerów). pick bez wybranego kontrolera = auto."""
    if mode == BTA_MANUAL or not ctrls:
        return None
    if mode == BTA_PICK:
        if key == RULE_BUILTIN:
            return max(ctrls, key=lambda c: (-c.rank, -c.index))
        if pick := next((c for c in ctrls if c.key == key), None):
            return pick
    return max(ctrls, key=lambda c: (c.rank, -c.index))


def is_blocked(name: str, sysfs: Path = SYSFS) -> bool:
    return any(_read(s) == "1" for s in (sysfs / name).glob("rfkill*/soft"))


def set_blocked(name: str, block: bool, sysfs: Path = SYSFS) -> None:
    for soft in (sysfs / name).glob("rfkill*/soft"):
        if _read(soft) != ("1" if block else "0"):
            soft.write_text("1" if block else "0")


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


def dev_info(name: str) -> tuple[bool, int] | None:
    """(kontroler włączony, link policy) z jądra (ioctl HCIGETDEVINFO) albo None (brak AF_BLUETOOTH / uprawnień)."""
    buf = bytearray(DEV_INFO_SIZE)
    struct.pack_into("@H", buf, 0, int(name[3:]))
    try:
        with socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI) as s:
            fcntl.ioctl(s.fileno(), HCIGETDEVINFO, buf, True)
    except (OSError, ValueError):
        return None
    (flags,), (policy,) = struct.unpack_from("@I", buf, DEV_FLAGS_AT), struct.unpack_from("@I", buf, DEV_POLICY_AT)
    return bool(flags & HCI_UP), policy


def ensure_no_sniff(name: str, sniff=None, info=None) -> bool | None:
    """Link policy kontrolera bez SNIFF. Jądro ustawia ją od nowa przy KAŻDYM włączeniu kontrolera (Write Default
    Link Policy z jego możliwości - SNIFF wraca), więc jednorazowe ustawienie przy wyborze kontrolera znikało, gdy
    BlueZ włączał go chwilę później (odblokowanie rfkill, Powered, restart bluetoothd, ponowne wpięcie dongla).
    -> None: nic do zrobienia (już bez SNIFF albo kontroler wyłączony - zrobi się przy następnym sprawdzeniu po
    włączeniu), True: ustawiono teraz, False: nie udało się. Bez HCIGETDEVINFO - ustawia zawsze (jak dawniej)."""
    st = (info or dev_info)(name)
    if st is not None and (not st[0] or not st[1] & HCI_LP_SNIFF):
        return None
    return (sniff or no_sniff)(name)


def apply(ctrls: tuple[Ctrl, ...], mode: int, key: str = "", sysfs: Path = SYSFS, sniff=None, info=None) -> Ctrl | None:
    """Wybrany odblokowany i bez SNIFF, reszta zablokowana. Tryb ręczny: nic. -> wybrany.
    Najpierw odblokowanie wybranego, potem blokowanie reszty: błąd zapisu w połowie (np. EIO z rfkill) zostawia
    najwyżej dwa włączone radia - przy odwrotnej kolejności telefon bez ekranu zostawał bez żadnego Bluetooth."""
    pick = choose(ctrls, mode, key)
    if pick is None:
        return None
    set_blocked(pick.name, False, sysfs)
    for c in ctrls:
        if c != pick:
            set_blocked(c.name, True, sysfs)
    if ensure_no_sniff(pick.name, sniff, info) is False:
        print(f"Bluetooth adapter: cannot disable sniff mode on {pick.name} (needs root)")
    return pick


def unblock_all(ctrls: tuple[Ctrl, ...], sysfs: Path = SYSFS) -> None:
    """Przejście na tryb ręczny z okna: nic nie zostaje zablokowane przez nas."""
    for c in ctrls:
        set_blocked(c.name, False, sysfs)


def describe(ctrls: tuple[Ctrl, ...], pick: Ctrl | None, mode: int) -> str:
    def text(c):
        return f"{c.name} {c.label} ({KIND_TEXT[c.rank]})"
    rest = ", ".join(text(c) for c in ctrls if c != pick)
    if pick is None:
        return f"Bluetooth adapter ({MODE_NAMES[mode]}): " + (rest or "none")
    return f"Bluetooth adapter ({MODE_NAMES[mode]}): {text(pick)}" + (f", blocked: {rest}" if rest else "")


# ---------- tryb: sender.toml (domyślny) + bt.json (wybór z okna) ----------
def mode_from(text: str) -> tuple[int, str]:
    """bt_adapter z sender.toml -> (tryb, klucz): auto | builtin | manual."""
    t = str(text).strip().lower()
    if t == RULE_BUILTIN:
        return BTA_PICK, RULE_BUILTIN
    return MODES.get(t, BTA_AUTO), ""


def load_mode(path: str, default: tuple[int, str]) -> tuple[int, str]:
    """(tryb, klucz) zapisany z okna albo domyślny z konfiguracji."""
    if not path:
        return default
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        print(f"Bluetooth adapter: {path} ignored ({e!r})")
        return default
    if not isinstance(raw, dict) or raw.get("mode") not in MODES:
        return default
    mode, key = MODES[raw["mode"]], raw.get("device", "")
    return (mode, key) if mode != BTA_PICK or (isinstance(key, str) and key) else default


def save_mode(path: str, mode: int, key: str = "") -> None:
    if path:
        try:
            write_json(path, {"mode": MODE_NAMES[mode], **({"device": key} if mode == BTA_PICK else {})}, indent=2)
        except OSError as e:
            print(f"Bluetooth adapter: cannot write {path}: {e!r}")
