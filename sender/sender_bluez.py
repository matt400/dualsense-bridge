"""Sender: BlueZ over busctl - running the tools, parsing GetManagedObjects into pads and adapters, error texts -> results.

No state: ``sender_bt.Bluetooth`` drives the operations and uses these helpers (``run_cmd`` is its default runner).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from subprocess import DEVNULL, PIPE
from typing import Any

from proto import (BT_DEV_MAX, BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTD_TRUSTED, BTR_AUTH, BTR_BUSY, BTR_DENIED, BTR_FAILED,
                   BTR_NOT_FOUND, BTR_OFF, BTR_TIMEOUT, BTS_BLOCKED, BTS_DENIED, BTS_NO_ADAPTER, BTS_NO_SERVICE, BTS_OFF, BTS_OK,
                   mac_bytes, mac_text)

BLUEZ, ADAPTER, DEVICE = "org.bluez", "org.bluez.Adapter1", "org.bluez.Device1"
COD_MAJOR_SHIFT, COD_MAJOR_MASK, COD_MAJOR_PERIPHERAL = 8, 0x1F, 0x05      # Bluetooth class of device: major class (5 = peripheral)
COD_MINOR_SHIFT, COD_MINOR_MASK, COD_MINOR_JOYSTICK, COD_MINOR_GAMEPAD = 2, 0x0F, 1, 2
# an error from busctl (a BlueZ / D-Bus message, lower case) -> an operation result / an adapter state
RESULTS = (("authentication", BTR_AUTH), ("access denied", BTR_DENIED), ("permission", BTR_DENIED),
           ("not authorized", BTR_DENIED), ("does not exist", BTR_NOT_FOUND), ("unknown object", BTR_NOT_FOUND),
           ("not ready", BTR_OFF), ("rfkill", BTR_OFF), ("in progress", BTR_BUSY), ("busy", BTR_BUSY),
           ("timed out", BTR_TIMEOUT), ("timeout", BTR_TIMEOUT))
STATES = (("access denied", BTS_DENIED), ("permission", BTS_DENIED), ("not authorized", BTS_DENIED),
          ("org.bluez was not provided", BTS_NO_SERVICE), ("serviceunknown", BTS_NO_SERVICE),
          ("not activatable", BTS_NO_SERVICE), ("name has no owner", BTS_NO_SERVICE), ("not found", BTS_NO_SERVICE))

Runner = Callable[[Iterable[str], float], Awaitable[tuple[int, str, str]]]


@dataclass(frozen=True, slots=True)
class Device:
    """A Bluetooth device as shown in the window: address, display name, signal strength and BTD_* flags."""

    mac: str
    name: str
    rssi: int | None
    flags: int

    @property
    def sort_key(self) -> tuple:
        """Connected, paired, pads, then name - without RSSI, which jumps around and would reorder the list in the window."""
        f = self.flags
        return not f & BTD_CONNECTED, not f & BTD_PAIRED, not f & BTD_PAD, self.name.lower(), self.mac


class BtError(Exception):
    """A failed busctl call: a message and a BTR_* result for the window."""

    def __init__(self, msg: str, result: int = BTR_FAILED) -> None:
        """Create the error with its message and the BTR_* result reported to the window."""
        super().__init__(msg)
        self.result = result


async def run_cmd(argv: Iterable[str], timeout: float) -> tuple[int, str, str]:
    """Run a program without a shell -> (code, stdout, stderr); exceeding the time = the process is killed and the code is -1."""
    proc = await asyncio.create_subprocess_exec(*argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()              # without this a zombie process is left behind
        return -1, "", "timed out"
    code = proc.returncode           # set once communicate() has returned
    return -1 if code is None else code, out.decode(errors="replace"), err.decode(errors="replace")


def classify(msg: str, table: tuple[tuple[str, int], ...], default: int) -> int:
    """Map an error message to a result code: the first table entry whose key occurs in the lower-cased message, else `default`."""
    low = (msg or "").lower()
    return next((code for key, code in table if key in low), default)


def dev_path(adapter: str, mac: str) -> str:
    """The D-Bus object path of a device: <adapter path>/dev_AA_BB_CC_DD_EE_FF."""
    return f"{adapter}/dev_{mac.upper().replace(':', '_')}"


def prop_value(props: dict[str, Any], key: str, default: Any = None) -> Any:
    """The value of a property from busctl JSON ({"type": "b", "data": true}) or `default`."""
    v = props.get(key)
    return v.get("data", default) if isinstance(v, dict) else default


def is_pad(props: dict[str, Any]) -> bool:
    """A game controller: the BlueZ icon, or a peripheral / joystick-gamepad device class (DualSense: 0x002508).

    In the Bluetooth class-of-device value, bits 8-12 hold the major class (5 = peripheral) and bits 2-5 the minor
    class (1 = joystick, 2 = gamepad).
    """
    cls = prop_value(props, "Class", 0) or 0
    major, minor = (cls >> COD_MAJOR_SHIFT) & COD_MAJOR_MASK, (cls >> COD_MINOR_SHIFT) & COD_MINOR_MASK
    gamepad = major == COD_MAJOR_PERIPHERAL and minor in (COD_MINOR_JOYSTICK, COD_MINOR_GAMEPAD)
    return prop_value(props, "Icon") == "input-gaming" or gamepad


def parse_objects(text: str, prefer: str = "", info: dict[str, Any] | None = None) -> tuple[int, str, list[Device]]:
    """Parse the result of `busctl --json=short call ... GetManagedObjects`.

    The adapter is the one chosen by sender_btadapter (``prefer``, e.g. /org/bluez/hci1), otherwise the first powered
    one, and when none is powered - the first (hci0). Devices without a name and without pairing (beacons, phones
    nearby) are skipped.

    Args:
        text: The busctl JSON output.
        prefer: The D-Bus path of the preferred adapter.
        info: If given, receives the "address" of the chosen adapter and "count" - how many adapters BlueZ sees
            (needed by the scan: select).

    Returns:
        ``(BTS_* state, adapter path, devices)``.
    """
    objects: dict[str, dict[str, Any]] = json.loads(text)["data"][0]
    adapters = {path: ifaces[ADAPTER] for path, ifaces in objects.items() if ADAPTER in ifaces}
    if info is not None:
        info.update(address="", count=len(adapters))
    if not adapters:
        return BTS_NO_ADAPTER, "", []
    path = prefer if prefer in adapters else min(adapters, key=lambda p: (not prop_value(adapters[p], "Powered", False), p))
    a = adapters[path]
    if info is not None:
        info["address"] = str(prop_value(a, "Address", "") or "").upper()
    devices = []
    for p, ifaces in objects.items():
        d = ifaces.get(DEVICE)
        if d is not None and p.startswith(path + "/") and (dev := _parse_device(d)) is not None:
            devices.append(dev)
    devices.sort(key=lambda d: d.sort_key)
    return _adapter_state(a), path, devices[:BT_DEV_MAX]


def _adapter_state(adapter: dict[str, Any]) -> int:
    """The BTS_* state of an adapter: powered (OK), blocked by rfkill or simply off."""
    if prop_value(adapter, "Powered", False):
        return BTS_OK
    return BTS_BLOCKED if prop_value(adapter, "PowerState") == "off-blocked" else BTS_OFF


def _parse_device(d: dict[str, Any]) -> Device | None:
    """One BlueZ device; None without a usable address or when it has neither a name nor a pairing (beacons, phones nearby)."""
    if not (mac := mac_bytes(prop_value(d, "Address", ""))):
        return None
    paired, name = bool(prop_value(d, "Paired", False)), prop_value(d, "Name") or ""
    if not (name or paired):
        return None
    flags = ((BTD_PAIRED if paired else 0) | (BTD_CONNECTED if prop_value(d, "Connected", False) else 0)
             | (BTD_TRUSTED if prop_value(d, "Trusted", False) else 0) | (BTD_PAD if is_pad(d) else 0))
    return Device(mac_text(mac), prop_value(d, "Alias") or name or mac_text(mac), prop_value(d, "RSSI"), flags)


async def scan_feed(proc: asyncio.subprocess.Process, address: str, wait_s: float = 3.0) -> None:
    """Drive a scan on the chosen controller through bluetoothctl's stdin.

    The commands go to bluetoothctl only once it prints that controller (BlueZ lists the controllers asynchronously
    after start - a "select" sent sooner ends with "Controller ... not available"), waiting at most wait_s. Afterwards
    it reads the output until the process ends: a full pipe would stall bluetoothctl in the middle of the scan.
    """
    out, inp = proc.stdout, proc.stdin
    if out is None or inp is None:
        raise ValueError("bluetoothctl must be started with stdin and stdout pipes")
    loop = asyncio.get_running_loop()
    end, seen, sent = loop.time() + wait_s, "", False
    try:
        while True:
            try:
                chunk = await (out.read(4096) if sent else asyncio.wait_for(out.read(4096), max(0.0, end - loop.time())))
            except TimeoutError:
                chunk = None
            if not sent:
                seen = (seen + (chunk or b"").decode(errors="replace").upper())[-256:]
                if chunk is None or address in seen:
                    inp.write(f"select {address}\nscan on\n".encode())
                    await inp.drain()
                    sent = True
                    continue
            if not chunk:
                return
    except (OSError, ValueError):            # the process ended (the scan was stopped) - the pipe is closed
        return
