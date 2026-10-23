"""Sender: choosing the Bluetooth controller when there are several (built-in radio + USB dongle).

The built-in radio of a phone / board (UART or SDIO, e.g. the WCN3990 in the OnePlus 6) may queue pad reports - a few
to a dozen ms in the "Bluetooth (above the minimum)" segment on the pad card; a USB dongle is usually ~1 ms. Mode
(sender.toml `bt_adapter`; a change made in the receiver window is saved in bt.json):

  auto     the best controller: USB dongle > internal USB (laptop: a port marked as fixed by the firmware) >
           built-in on UART / SDIO; on a tie the lower hciN number wins. The others are blocked with rfkill.
  pick     a specific controller chosen in the window (remembered by device identifier: USB vid:pid[:serial],
           built-in - compatible / path; not by hciN, which changes with the detection order). When it is missing
           (dongle unplugged) - behaves like auto, and when it returns it is used again. In sender.toml: "builtin" =
           the built-in one before USB.
  manual   changes nothing (you manage Bluetooth yourself: rfkill, bluetoothctl)

The chosen one gets a link policy without SNIFF (the pad does not put the link to sleep: the first press after a
pause has no extra few ms of delay); the kernel restores SNIFF every time a controller is switched on, so it is
checked and corrected on every check. The set of controllers is checked every few seconds (sender_bt, also without
a receiver - PC off, Wake-on-LAN), so plugging in / unplugging a dongle while running switches without a restart; an
unplugged dongle unblocks the built-in one. Pads must be paired once with each controller (its own address).
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
RULE_BUILTIN = "builtin"            # rule key from sender.toml: built-in before USB
AF_BLUETOOTH = getattr(socket, "AF_BLUETOOTH", 31)
BTPROTO_HCI = 1
HCISETLINKPOL = 0x400448CD          # _IOW('H', 205, int)
HCIGETDEVINFO = 0x800448D3          # _IOR('H', 211, int) -> struct hci_dev_info
DEV_INFO_SIZE, DEV_FLAGS_AT, DEV_POLICY_AT = 92, 16, 36   # size of hci_dev_info, offsets of the flags and link_policy fields
HCI_UP = 0x0001                     # flags bit: the controller is switched on
HCI_LP_RSWITCH, HCI_LP_SNIFF = 0x0001, 0x0004   # link policy: role switch (without HOLD / SNIFF / PARK); SNIFF
BUILTIN, USB_FIXED, USB = 0, 1, 2   # controller rank (auto takes the highest)
KIND_TEXT = {BUILTIN: "built-in", USB_FIXED: "internal USB", USB: "USB"}
# device-tree compatible prefix -> manufacturer (built-in radios of phones and boards)
VENDORS = {"qcom": "Qualcomm", "brcm": "Broadcom", "infineon": "Infineon", "cypress": "Cypress", "realtek": "Realtek",
           "mediatek": "MediaTek", "ti": "Texas Instruments", "marvell": "Marvell", "nxp": "NXP", "intel": "Intel",
           "amlogic": "Amlogic", "rockchip": "Rockchip", "allwinner": "Allwinner", "sprd": "Unisoc"}


@dataclass(frozen=True, slots=True)
class Ctrl:
    """One Bluetooth controller found in sysfs."""
    name: str                                       # hciN
    rank: int                                       # BUILTIN / USB_FIXED / USB
    label: str = field(default="", compare=False)   # device name for the window
    key: str = ""                                   # persistent identifier (the choice made in the window)

    @property
    def index(self) -> int:
        """The numeric part of the hciN name."""
        return int(self.name[3:])

    @property
    def usb(self) -> bool:
        """True for any USB-attached controller (a dongle or an internal USB one)."""
        return self.rank != BUILTIN


def _read(path: Path) -> str:
    """Read a small sysfs text file, stripped of NULs, newlines and spaces; "" if it cannot be read."""
    with contextlib.suppress(OSError):
        return path.read_text(errors="replace").strip("\0\n ")
    return ""


def _usb_info(dev: Path) -> tuple[int, str, str]:
    """Return (rank, name, key) of a USB controller: the name comes from the descriptor (product / manufacturer), the key is
    vid:pid[:serial].

    Walks up from the Bluetooth device to the USB device node (the one with idVendor). The manufacturer is prepended
    to the product name unless it is already part of it or is a placeholder like "Generic". A port the firmware marks
    as "fixed" (not removable) ranks as an internal USB controller.
    """
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
    """Return (name, key) of a built-in controller.

    The name comes from the device-tree `compatible` string (qcom,wcn3990-bt -> Qualcomm WCN3990), otherwise from the
    driver name, otherwise it is generic; the key is the compatible string or the parent device name. The search walks
    up the sysfs parents and stops at the first one that carries either piece of information.
    """
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
    """Build a Ctrl from a /sys/class/bluetooth/hciN symlink: a path through a USB bus means USB, anything else is built-in."""
    dev = link.resolve()
    if "/usb" in str(dev):
        rank, label, key = _usb_info(dev)
    else:
        rank, (label, key) = BUILTIN, _builtin_info(dev)
    return Ctrl(link.name, rank, label, key)


def scan(sysfs: Path = SYSFS) -> tuple[Ctrl, ...]:
    """Controllers from /sys/class/bluetooth (without the hciN:handle connection entries), ordered by number."""
    found = [_ctrl(p) for p in sysfs.glob("hci*") if p.name[3:].isdigit()]
    return tuple(sorted(found, key=lambda c: c.index))


def choose(ctrls: tuple[Ctrl, ...], mode: int, key: str = "") -> Ctrl | None:
    """The controller to use, or None (manual mode / no controllers). `pick` without a matching controller falls back to auto.

    Auto ranks by (rank, -index): the highest rank wins and, on a tie, the lowest hciN. The "builtin" rule inverts
    the rank so that the built-in controller is preferred over USB.
    """
    if mode == BTA_MANUAL or not ctrls:
        return None
    if mode == BTA_PICK:
        if key == RULE_BUILTIN:
            return max(ctrls, key=lambda c: (-c.rank, -c.index))
        if pick := next((c for c in ctrls if c.key == key), None):
            return pick
    return max(ctrls, key=lambda c: (c.rank, -c.index))


def is_blocked(name: str, sysfs: Path = SYSFS) -> bool:
    """True if any rfkill soft switch of the controller is set (the radio is blocked)."""
    return any(_read(s) == "1" for s in (sysfs / name).glob("rfkill*/soft"))


def set_blocked(name: str, block: bool, sysfs: Path = SYSFS) -> None:
    """Block or unblock the controller's radio through its rfkill soft switch (written only if the value differs)."""
    for soft in (sysfs / name).glob("rfkill*/soft"):
        if _read(soft) != ("1" if block else "0"):
            soft.write_text("1" if block else "0")


def no_sniff(name: str) -> bool:
    """Set the controller's default link policy without SNIFF - the same ioctl as `hciconfig hciN lp RSWITCH` (the
    kernel remembers it for new connections), so the deprecated hciconfig is not required; it is only a fallback."""
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
    """(controller is up, link policy) read from the kernel (HCIGETDEVINFO ioctl), or None (no AF_BLUETOOTH / permissions).

    The ioctl fills a struct hci_dev_info; only two 32-bit fields are decoded from it, at fixed offsets.
    """
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
    """Keep the controller's link policy free of SNIFF. The kernel resets it EVERY time the controller is switched on
    (Write Default Link Policy from its capabilities - SNIFF comes back), so a one-time setting made when the
    controller was chosen vanished when BlueZ powered it on a moment later (rfkill unblock, Powered, a bluetoothd
    restart, plugging the dongle in again).
    Returns None: nothing to do (already without SNIFF, or the controller is off - it will be done at the next check
    after it is switched on), True: set now, False: failed. Without HCIGETDEVINFO it always sets (as it used to)."""
    st = (info or dev_info)(name)
    if st is not None and (not st[0] or not st[1] & HCI_LP_SNIFF):
        return None
    return (sniff or no_sniff)(name)


def apply(ctrls: tuple[Ctrl, ...], mode: int, key: str = "", sysfs: Path = SYSFS, sniff=None, info=None) -> Ctrl | None:
    """Make the chosen controller unblocked and SNIFF-free and block the rest. Manual mode: nothing. Returns the chosen one.
    The chosen one is unblocked first and the rest are blocked afterwards: a write error in the middle (e.g. EIO from
    rfkill) leaves at most two radios on - in the reverse order a phone with no screen was left without any Bluetooth."""
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
    """Switch to manual mode from the window: nothing stays blocked by us."""
    for c in ctrls:
        set_blocked(c.name, False, sysfs)


def describe(ctrls: tuple[Ctrl, ...], pick: Ctrl | None, mode: int) -> str:
    """One-line log description: the mode, the chosen controller and the blocked ones."""
    def text(c) -> str:
        return f"{c.name} {c.label} ({KIND_TEXT[c.rank]})"
    rest = ", ".join(text(c) for c in ctrls if c != pick)
    if pick is None:
        return f"Bluetooth adapter ({MODE_NAMES[mode]}): " + (rest or "none")
    return f"Bluetooth adapter ({MODE_NAMES[mode]}): {text(pick)}" + (f", blocked: {rest}" if rest else "")


# ---------- mode: sender.toml (default) + bt.json (choice from the window) ----------
def mode_from(text: str) -> tuple[int, str]:
    """Translate the bt_adapter value from sender.toml into (mode, key): auto | builtin | manual."""
    t = str(text).strip().lower()
    if t == RULE_BUILTIN:
        return BTA_PICK, RULE_BUILTIN
    return MODES.get(t, BTA_AUTO), ""


def load_mode(path: str, default: tuple[int, str]) -> tuple[int, str]:
    """(mode, key) saved from the window, or the default from the configuration.

    A missing, unreadable or invalid file falls back to the default; a `pick` entry without a device key is invalid too.
    """
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
    """Persist the controller choice made in the window to bt.json (the device key only for `pick`); errors are logged only."""
    if path:
        try:
            write_json(path, {"mode": MODE_NAMES[mode], **({"device": key} if mode == BTA_PICK else {})}, indent=2)
        except OSError as e:
            print(f"Bluetooth adapter: cannot write {path}: {e!r}")
