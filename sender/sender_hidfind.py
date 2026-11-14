"""Sender: finding the pad's hidraw node and reading its firmware version.

Rumble through an HID report needs /dev/hidrawN of the same HID device as the pad's evdev node. This module walks the sysfs
tree (the input node -> its HID device directory -> the hidraw child, with a HID_UNIQ fallback), tells Bluetooth from USB
by the bus number in the HID device name, creates a missing node on systems without udev / mdev, and reads the "update"
firmware version from feature report 0x20 (it decides between the v1 and v2 vibration flags). Pure sysfs / ioctl helpers,
no event loop and no state; the pad's lifecycle is in sender_rumble (class Rumble).
"""

import array
import fcntl
import glob
import os
import re
import stat
from typing import Final

from evdev import InputDevice

from sender_ioctl import IOC_READ, IOC_WRITE, ioc

_HID_DIR = re.compile(r"^([0-9A-Fa-f]{4}):[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")
BUS_BLUETOOTH: Final = 0x0005
FW_REPORT, FW_SIZE, FW_VERSION_AT = 0x20, 64, 44    # the feature report with the firmware version (as in hid-playstation)
VIBRATION_V2_FROM: Final = (2 << 8) | 21            # DS_FEATURE_VERSION(2, 21): (major << 8) | minor, so 0x0215 (not 0x0221)
HID_IOC_MAGIC, HIDIOCGFEATURE_NR = "H", 0x07     # HIDIOCGFEATURE(len), see linux/hidraw.h
HIDRAW_NODE_MODE: Final = stat.S_IFCHR | 0o600


def _hidiocgfeature(size: int) -> int:
    """Build the HIDIOCGFEATURE ioctl number (read an HID feature report of ``size`` bytes)."""
    return ioc(IOC_READ | IOC_WRITE, HID_IOC_MAGIC, HIDIOCGFEATURE_NR, size)


def firmware_version(fd: int) -> int | None:
    """Read the pad's "update" firmware version from feature report 0x20.

    The version is a little-endian 16-bit value at a fixed offset of the report; the first byte echoes the report id,
    which doubles as a sanity check that the pad really answered with the expected report.

    Args:
        fd: An open hidraw file descriptor.

    Returns:
        The version, or None (no access / another pad).
    """
    buf = array.array("B", [FW_REPORT] + [0] * (FW_SIZE - 1))
    try:
        fcntl.ioctl(fd, _hidiocgfeature(FW_SIZE), buf, True)
    except OSError:
        return None
    return (buf[FW_VERSION_AT] | buf[FW_VERSION_AT + 1] << 8) if buf[0] == FW_REPORT else None


def _uevent(path: str) -> dict[str, str]:
    """Parse a sysfs uevent file (KEY=VALUE lines) into a dict; empty on any read error."""
    try:
        with open(path) as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return {}


def _by_uniq(uniq: str | None, sys_root: str) -> tuple[str | None, bool]:
    """Find the hidraw whose HID device has the same HID_UNIQ (the pad's MAC address).

    This is the fallback for when the sysfs path from eventN to the HID device is unusual (another kernel / Bluetooth stack).

    Returns:
        ``(path, is_bluetooth)``, or ``(None, False)``.
    """
    uniq = (uniq or "").lower()
    if not uniq:
        return None, False
    for raw in sorted(glob.glob(f"{sys_root}/class/hidraw/hidraw*")):
        ev = _uevent(os.path.join(raw, "device", "uevent"))
        if ev.get("HID_UNIQ", "").lower() == uniq:
            bus = int(ev.get("HID_ID", "0:0:0").split(":")[0], 16)
            return f"/dev/{os.path.basename(raw)}", bus == BUS_BLUETOOTH
    return None, False


def _hid_device_dir(node: str) -> tuple[str, int] | None:
    """Walk up from a sysfs node to its HID device directory (named like 0005:054C:0CE6.0001).

    Returns:
        ``(that directory, the bus number from its name)``, or None when no parent looks like a HID device.
    """
    path = node
    while path not in ("/", ""):
        match = _HID_DIR.match(os.path.basename(path))
        if match:
            return path, int(match.group(1), 16)
        path = os.path.dirname(path)
    return None


def find_hidraw(pad_dev: InputDevice, sys_root: str = "/sys") -> tuple[str | None, bool]:
    """Find the hidraw node for the same HID device as the pad's evdev node.

    Starting from the resolved sysfs path of the input node, it walks up the parents until it reaches the HID device
    directory and looks for a hidraw child there; the bus number in that name tells Bluetooth (0005) from USB. If the walk
    finds nothing, it falls back to matching by HID_UNIQ.

    Args:
        pad_dev: The pad's evdev device.
        sys_root: The sysfs mount point (a parameter for tests).

    Returns:
        ``(/dev/hidrawN, is Bluetooth)``, or ``(None, False)``.
    """
    node = os.path.realpath(f"{sys_root}/class/input/{os.path.basename(os.fsdecode(pad_dev.path))}")
    hid = _hid_device_dir(node)
    if hid is not None:
        directory, bus = hid
        found = sorted(glob.glob(os.path.join(directory, "hidraw", "hidraw*")))
        if found:
            return f"/dev/{os.path.basename(found[0])}", bus == BUS_BLUETOOTH
    return _by_uniq(getattr(pad_dev, "uniq", ""), sys_root)


def kernel_has_hidraw(sys_root: str = "/sys") -> bool:
    """Tell whether the kernel has hidraw (CONFIG_HIDRAW): the hidraw device class exists in sysfs."""
    return os.path.isdir(f"{sys_root}/class/hidraw")


def ensure_node(path: str, sys_root: str = "/sys") -> None:
    """Create /dev/hidrawN when it is missing although the device exists in sysfs.

    That happens on a system without udev / mdev. The node is made with ``mknod`` from the numbers in sysfs (root only).
    Failures are ignored on purpose: the error shows up when the node is opened, with a clearer reason.
    """
    if os.path.exists(path):
        return
    try:
        with open(f"{sys_root}/class/hidraw/{os.path.basename(path)}/dev") as f:
            major, minor = map(int, f.read().split(":"))
        os.mknod(path, HIDRAW_NODE_MODE, os.makedev(major, minor))
        print(f"Created missing device node {path}")
    except (OSError, ValueError):
        return
