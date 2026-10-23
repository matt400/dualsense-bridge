"""Sender: the battery of the device the sender runs on (a phone, a laptop) - shown in the receiver window.

Only a real battery counts: a power-supply entry of type "Battery" that is not a peripheral (a DualSense, a mouse - their
entries have scope "Device" or the pad's own name). A Raspberry Pi or a desktop has none, so read() returns None and the
receiver shows no battery at all. The percentage is read from sysfs; the entry is looked up once and cached (a battery does
not come and go), and while none was found it is looked for again only every RESCAN_S (a USB power bank / UPS HAT may appear).
"""
import os
import time
from typing import Final

from proto import BATT_CHARGING

POWER_SUPPLY: Final = "/sys/class/power_supply"
PAD_BATTERY_PREFIX: Final = "ps-controller-battery-"     # the DualSense's own entry (sender_streams.BATTERY_PATH)
RESCAN_S: Final = 60.0
PERCENT_MAX: Final = 100

_path: str | None = None
_next_scan = 0.0


def _text(path: str) -> str:
    """The stripped content of a sysfs file, "" when it cannot be read."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def find(root: str = POWER_SUPPLY) -> str | None:
    """Return the sysfs folder of the device's own battery, None when it has none."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for name in names:
        path = os.path.join(root, name)
        if (_text(path + "/type") == "Battery" and not name.startswith(PAD_BATTERY_PREFIX)
                and _text(path + "/scope") != "Device" and _text(path + "/present") != "0"
                and _text(path + "/capacity").isdigit()):
            return path
    return None


def read(root: str = POWER_SUPPLY, now: float | None = None) -> int | None:
    """Return the battery byte of the build3 packet (percent, + BATT_CHARGING while charging), None without a battery.

    "Full" is not charging: the receiver animates the charging only while the battery really takes current.
    """
    global _path, _next_scan
    now = time.monotonic() if now is None else now
    if _path is None and now >= _next_scan:
        _path, _next_scan = find(root), now + RESCAN_S
    if _path is None:
        return None
    capacity = _text(_path + "/capacity")
    if not capacity.isdigit():              # the battery went away (a removed power bank): look again later
        _path = None
        return None
    percent = min(int(capacity), PERCENT_MAX)
    return percent | (BATT_CHARGING if _text(_path + "/status").lower() == "charging" else 0)
