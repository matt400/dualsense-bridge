"""Sender: the phone's Bluetooth driven from the receiver window (scan, pair, connect and disconnect pads).

It works without a screen on the phone (proto: btcmd -> btstate + btdev).

BlueZ over D-Bus: `busctl --json=short` (systemd, no extra packages) - adapter and device state
(ObjectManager.GetManagedObjects), Pair / Connect / Disconnect / RemoveDevice, Powered / Trusted. BlueZ ends a
discovery when the D-Bus client that started it disconnects - a one-off `busctl call StartDiscovery` would do nothing.
So a scan is a `bluetoothctl --timeout N scan on` process: it holds the session for N s and ends it by itself
(auto-stop), and stopping from the window means ending the process. A scan takes the radio away from connected pads
(delay spikes), so it always has the BT_SCAN_MAX_S limit, and pairing and connecting interrupt it (BlueZ connects more
reliably without discovery).

With no panel open in the window: only the adapter state every IDLE_S (the dot in the header). For BT_ACTIVE_S after every
btcmd (the window repeats BT_LIST while the panel is open) and during a scan / operation: the state and the device list
every second. Operations run one at a time (BlueZ rejects parallel ones); a second one in progress -> the result BTR_BUSY.
A missing busctl, bluetoothd, adapter or permissions is a BTS_* state in the window, not an exception - the loop keeps going.
With several controllers (built-in + USB dongle) the choice is made by sender_btadapter - on every state refresh, and without
a receiver every IDLE_S, so a dongle plugged in while running takes over the pads without a restart (also with the PC off); the mode
(auto / built-in / manual) can be changed from the window.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from subprocess import DEVNULL, PIPE
from typing import Any, Final

import sender_btadapter as btadapter
import sender_net as net
import sender_state as state
from proto import (BODY, BTA_MANUAL, BTA_PICK, BTC_ACTIVE, BTC_BLOCKED, BTC_FIXED, BTC_PICKED, BTC_USB,
                   BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTD_TRUSTED, BTF_BUSY, BTF_HAS_BUILTIN, BTF_HAS_USB,
                   BTF_MODE_SHIFT, BTF_ON_USB, BTF_SCANNING, BTR_AUTH, BTR_BUSY, BTR_DENIED, BTR_FAILED, BTR_NONE,
                   BTR_NOT_FOUND, BTR_OFF, BTR_OK, BTR_RUNNING, BTR_TIMEOUT, BTS_BLOCKED, BTS_DENIED, BTS_ERROR,
                   BTS_NO_ADAPTER, BTS_NO_SERVICE, BTS_NO_TOOL, BTS_OFF, BTS_OK, BT_ACTIVE_S, BT_ADAPTER,
                   BT_ADAPTER_USE, BT_CONNECT, BT_DEV_MAX, BT_DISCONNECT, BT_LIST, BT_PAIR, BT_POWER, BT_REMOVE,
                   BT_RSSI_NONE, BT_SCAN_MAX_S, BT_SCAN_START, BT_SCAN_STOP, T_BTCTRL, T_BTDEV, T_BTSTATE, clamp,
                   mac_bytes, mac_text, utf8_field)
from sender_config import C

BTF_LIST: Final = 4         # btstate flag (outside proto.BTF_*, since it only means "a list round follows me")
BUSCTL, BLUETOOTHCTL = "busctl", "bluetoothctl"
BLUEZ, ADAPTER, DEVICE = "org.bluez", "org.bluez.Adapter1", "org.bluez.Device1"
COD_MAJOR_SHIFT, COD_MAJOR_MASK, COD_MAJOR_PERIPHERAL = 8, 0x1F, 0x05      # Bluetooth class of device: major class (5 = peripheral)
COD_MINOR_SHIFT, COD_MINOR_MASK, COD_MINOR_JOYSTICK, COD_MINOR_GAMEPAD = 2, 0x0F, 1, 2
CALL_S, CONNECT_S, PAIR_S = 5.0, 25.0, 40.0   # limits of D-Bus calls [s] (pairing waits for the pad)
IDLE_S = 5.0                 # the adapter state with no panel open
TICK_S = 1.0                 # the state and the list with the panel open / during a scan
DUP_S = 0.5                  # btcmd arrives 3x (UDP) - repeats of the same operation within this time are skipped
NAME_MAX = BODY[T_BTDEV].size - 12
CTRL_NAME_MAX = BODY[T_BTCTRL].size - 6
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


def _val(props: dict[str, Any], key: str, default: Any = None) -> Any:
    """The value of a property from busctl JSON ({"type": "b", "data": true}) or `default`."""
    v = props.get(key)
    return v.get("data", default) if isinstance(v, dict) else default


def is_pad(props: dict[str, Any]) -> bool:
    """A game controller: the BlueZ icon, or a peripheral / joystick-gamepad device class (DualSense: 0x002508).

    In the Bluetooth class-of-device value, bits 8-12 hold the major class (5 = peripheral) and bits 2-5 the minor
    class (1 = joystick, 2 = gamepad).
    """
    cls = _val(props, "Class", 0) or 0
    major, minor = (cls >> COD_MAJOR_SHIFT) & COD_MAJOR_MASK, (cls >> COD_MINOR_SHIFT) & COD_MINOR_MASK
    return _val(props, "Icon") == "input-gaming" or (major == COD_MAJOR_PERIPHERAL and minor in (COD_MINOR_JOYSTICK, COD_MINOR_GAMEPAD))


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
    path = prefer if prefer in adapters else min(adapters, key=lambda p: (not _val(adapters[p], "Powered", False), p))
    a = adapters[path]
    if info is not None:
        info["address"] = str(_val(a, "Address", "") or "").upper()
    if _val(a, "Powered", False):
        st = BTS_OK
    else:
        st = BTS_BLOCKED if _val(a, "PowerState") == "off-blocked" else BTS_OFF
    devices = []
    for p, ifaces in objects.items():
        d = ifaces.get(DEVICE)
        if d is None or not p.startswith(path + "/") or not (mac := mac_bytes(_val(d, "Address", ""))):
            continue
        paired, name = bool(_val(d, "Paired", False)), _val(d, "Name") or ""
        if not (name or paired):
            continue
        flags = ((BTD_PAIRED if paired else 0) | (BTD_CONNECTED if _val(d, "Connected", False) else 0)
                 | (BTD_TRUSTED if _val(d, "Trusted", False) else 0) | (BTD_PAD if is_pad(d) else 0))
        devices.append(Device(mac_text(mac), _val(d, "Alias") or name or mac_text(mac), _val(d, "RSSI"), flags))
    devices.sort(key=lambda d: d.sort_key)
    return st, path, devices[:BT_DEV_MAX]


async def _scan_feed(proc: asyncio.subprocess.Process, address: str, wait_s: float = 3.0) -> None:
    """Drive a scan on the chosen controller through bluetoothctl's stdin.

    The commands go to bluetoothctl only once it prints that controller (BlueZ lists the controllers asynchronously
    after start - an earlier "select" ended with "Controller ... not available"), waiting at most wait_s. Afterwards
    it reads the output until the process ends: a full pipe would stall bluetoothctl in the middle of the scan.
    """
    loop = asyncio.get_running_loop()
    end, seen, sent = loop.time() + wait_s, "", False
    try:
        while True:
            try:
                chunk = await (proc.stdout.read(4096) if sent else
                               asyncio.wait_for(proc.stdout.read(4096), max(0.0, end - loop.time())))
            except TimeoutError:
                chunk = None
            if not sent:
                seen = (seen + (chunk or b"").decode(errors="replace").upper())[-256:]
                if chunk is None or address in seen:
                    proc.stdin.write(f"select {address}\nscan on\n".encode())
                    await proc.stdin.drain()
                    sent = True
                    continue
            if not chunk:
                return
    except (OSError, ValueError):            # the process ended (the scan was stopped) - the pipe is closed
        return


class Bluetooth:
    """The Bluetooth state and operations of the sender. run: the command executor (tests substitute their own).

    One instance (`bt`) lives for the whole process. It keeps a cached picture of the adapter and its devices, runs
    the pair / connect / disconnect / remove / power operations one at a time, manages the scan subprocess, chooses
    the controller through sender_btadapter, and periodically reports everything to the receiver window (loop()).
    """

    def __init__(self, run: Runner = run_cmd, emit: Callable[..., None] | None = None, sysfs: Path | None = None) -> None:
        """Create the manager; ``run`` and ``emit`` are replaceable for tests, ``sysfs=None`` disables the controller choice."""
        self.run = run
        self.emit = emit or (lambda *a: net.emit(*a))
        self.state = BTS_ERROR
        self.adapter = ""
        self.adapter_addr = ""                       # the address (MAC) of the adapter self.adapter
        self.adapter_count = 0                       # how many adapters BlueZ sees
        self.devices: list[Device] = []
        self.last = (BTR_NONE, BTR_NONE, b"")       # the last operation: (BT_*, BTR_*, MAC 6 B)
        self.busy: asyncio.Task | None = None       # the operation's task (tests wait for it)
        self.running = False                         # an operation is in progress (until its end, before the result is sent)
        self.scan: asyncio.subprocess.Process | None = None
        # scan start / stop one after another: a start is several calls (refresh, power, starting bluetoothctl) - a second
        # start or stop in the meantime still saw self.scan = None, so the first process was left orphaned
        # (the scan took the radio from the pads for up to BT_SCAN_MAX_S and "Stop" in the window did not stop it)
        self.scan_lock = asyncio.Lock()
        self.scan_end = 0.0
        self.active_until = 0.0
        self.gen = 0
        self.dups: dict[tuple[int, int, bytes], float] = {}
        self.wake: asyncio.Event | None = None
        # controller choice (sender_btadapter): the BTA_* mode, the controllers seen last, the chosen one
        self.sysfs = sysfs                           # /sys/class/bluetooth; None = no controller choice (tests)
        self.mode: int | None = None                 # loaded on first use (the configuration is ready by then)
        self.key = ""                                # BTA_PICK: the identifier of the chosen controller
        self.cgen = 0                                # the round of the controller list
        self.ctrls: tuple[btadapter.Ctrl, ...] | None = None
        self.pick: btadapter.Ctrl | None = None
        self.adapter_err = ""
        self.untrusted: set[str] = set()             # pads for which Trusted could not be set (trust_pads)

    # ---------- commands from the window (the asyncio loop thread, from on_readable) ----------
    def on_command(self, op: int, arg: int, mac: bytes, now: float | None = None) -> None:
        """Handle a btcmd packet from the receiver window.

        Every command (also a plain BT_LIST poll) keeps the "active" period alive, which makes loop() report the
        full device list every second. Other commands are de-duplicated (UDP repeats within DUP_S are ignored), then
        dispatched: scan start / stop run as background tasks; pair / connect / ... go through start_op.
        """
        now = time.monotonic() if now is None else now
        self.active_until = now + BT_ACTIVE_S
        if op != BT_LIST:
            key = (op, arg, mac)
            if now - self.dups.get(key, -DUP_S) < DUP_S:
                return
            self.dups = {k: t for k, t in self.dups.items() if now - t < DUP_S}
            self.dups[key] = now
            if op == BT_SCAN_START:
                state.spawn(self.start_scan(clamp(arg or BT_SCAN_MAX_S, 1, BT_SCAN_MAX_S)))
            elif op == BT_SCAN_STOP:
                state.spawn(self.stop_scan())
            elif op in OPS:
                self.start_op(op, arg, mac)
        self.poke()

    def poke(self) -> None:
        """Wake the reporting loop so that a state change reaches the window without waiting for the next tick."""
        if self.wake is not None:
            self.wake.set()

    def start_op(self, op: int, arg: int, mac: bytes) -> None:
        """Start an operation as a background task.

        If another one is still running, BTR_BUSY is the answer instead (BlueZ rejects parallel ones).
        """
        if self.running:
            self.last = (op, BTR_BUSY, mac)
            return
        self.running, self.last = True, (op, BTR_RUNNING, mac)
        self.busy = state.spawn(self._op(op, arg, mac))

    async def _op(self, op: int, arg: int, mac: bytes) -> None:
        """Run one operation: refresh the state, call the operation from OPS, refresh again, and send the result to the window.

        BtError carries a result code for the window; any other exception is only logged, so a bad operation can never
        stop the sender. The `finally` always clears `running` - an exception after the operation (e.g. in the refresh)
        used to leave it set, and every following operation from the window answered "another operation is running"
        until the sender was restarted.
        """
        what = " ".join(filter(None, (OP_NAMES[op], mac_text(mac), "on" if op == BT_POWER and arg else "")))
        res = BTR_FAILED
        try:
            try:
                await self.refresh()
                await OPS[op](self, arg, mac_text(mac))
                res = BTR_OK
            except BtError as e:
                res = e.result
                print(f"Bluetooth {what} failed: {e}")
            except Exception as e:  # noqa: BLE001 - an operation must not stop the sender
                print(f"Bluetooth {what} error: {e!r}")
            else:
                print(f"Bluetooth {what}: OK")
            await self.refresh()
        finally:
            # always: an exception after the operation (e.g. in the refresh) left running = True - every following operation
            # from the window got "another operation is running" until the sender was restarted
            self.running, self.last = False, (op, res, mac)
        self.send(full=True)
        self.poke()

    # ---------- D-Bus ----------
    async def busctl(self, *args: str, timeout: float = CALL_S) -> str:
        """Run busctl with the given arguments and return its JSON output; failures become BtError with a mapped result code.

        A missing busctl or an OS-level error (no execute permission, fork out of memory) and a non-zero exit code are
        all translated, so callers only ever have to catch BtError.
        """
        try:
            rc, out, err = await self.run((BUSCTL, "--json=short", f"--timeout={timeout:g}", *args), timeout + 2)
        except FileNotFoundError:
            raise BtError("busctl not found (systemd)", BTR_FAILED) from None
        except OSError as e:                         # no execute permission, fork out of memory (EAGAIN / ENOMEM)
            raise BtError(f"cannot run busctl: {e}", BTR_DENIED if isinstance(e, PermissionError) else BTR_FAILED) from None
        if rc:
            msg = (err or out).strip() or f"exit {rc}"
            raise BtError(msg, classify(msg, RESULTS, BTR_FAILED))
        return out

    async def call(self, path: str, iface: str, method: str, *args: str, timeout: float = CALL_S) -> str:
        """Call a method of a BlueZ D-Bus object (a thin wrapper over busctl call)."""
        return await self.busctl("call", BLUEZ, path, iface, method, *args, timeout=timeout)

    async def set_prop(self, path: str, iface: str, name: str, value: bool) -> None:
        """Set a boolean property of a BlueZ D-Bus object (e.g. Powered, Trusted, Pairable)."""
        await self.busctl("set-property", BLUEZ, path, iface, name, "b", "true" if value else "false")

    async def refresh(self) -> None:
        """Read the adapter state and the device list from BlueZ.

        An error becomes a BTS_* state (the window shows the reason) and the list stays unchanged.

        First the controller choice is checked, then one GetManagedObjects call returns everything BlueZ knows; the
        JSON is parsed into the adapter path, its state and the devices. Finally paired pads that are not trusted yet
        are trusted.
        """
        await self.check_adapters()
        try:
            rc, out, err = await self.run((BUSCTL, "--json=short", f"--timeout={CALL_S:g}", "call", BLUEZ, "/",
                                           "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"), CALL_S + 2)
        except FileNotFoundError:
            self.state, self.adapter, self.devices = BTS_NO_TOOL, "", []
            return
        except OSError as e:                         # another busctl start failure - a state in the window, not an exception in the loop
            self.state = BTS_DENIED if isinstance(e, PermissionError) else BTS_ERROR
            print(f"Bluetooth: cannot run busctl: {e}")
            return
        if rc:
            self.state = classify(err or out, STATES, BTS_ERROR)
            return
        try:
            info: dict[str, Any] = {}
            self.state, self.adapter, self.devices = parse_objects(out, self.prefer(), info)
            self.adapter_addr, self.adapter_count = info["address"], info["count"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            print(f"Bluetooth: unexpected busctl output: {e!r}")
            self.state = BTS_ERROR
            return
        await self.trust_pads()

    async def trust_pads(self) -> None:
        """Trust paired pads that are not trusted yet (paired outside the window or by an older version).

        Without it BlueZ rejects a connection that the pad starts (switching on with the PS button), and the pad goes
        dark after a few seconds of blinking.
        """
        want = BTD_PAD | BTD_PAIRED
        for d in [d for d in self.devices if d.flags & (want | BTD_TRUSTED) == want and d.mac not in self.untrusted]:
            try:
                await self.set_prop(dev_path(self.adapter, d.mac), DEVICE, "Trusted", True)
            except BtError as e:
                self.untrusted.add(d.mac)            # once in the log, not on every refresh (every IDLE_S)
                print(f"Bluetooth {d.mac}: cannot set trusted: {e}")
                continue
            print(f"Bluetooth {d.mac}: paired pad was not trusted - trusted now")
            self.devices[self.devices.index(d)] = Device(d.mac, d.name, d.rssi, d.flags | BTD_TRUSTED)

    # ---------- controller choice ----------
    def get_mode(self) -> int:
        """Return the BTA_* mode (and set self.key for BTA_PICK): bt.json from the window, otherwise bt_adapter from sender.toml."""
        if self.mode is None:
            default = btadapter.mode_from(getattr(C, "BT_ADAPTER", "auto"))
            self.mode, self.key = btadapter.load_mode(getattr(C, "BT_FILE", ""), default)
        return self.mode

    def prefer(self) -> str:
        """The D-Bus path of the chosen controller (e.g. /org/bluez/hci1), or "" when no choice has been made."""
        return f"/org/bluez/{self.pick.name}" if self.pick is not None else ""

    async def check_adapters(self, force: bool = False) -> None:
        """Scan the controllers in /sys and choose anew when the set changes (a dongle plugged in / unplugged) or the mode does.

        A write error (a sender without root) goes once to the log, not as an exception.

        The actual blocking / unblocking runs in a worker thread (it writes sysfs files). When nothing changed, the only
        upkeep is making sure SNIFF is still off. After a controller was switched to, it is powered on (BlueZ usually
        does it itself, but we do not wait for it) and its link policy is fixed.
        """
        if self.sysfs is None:
            return
        try:
            ctrls = btadapter.scan(self.sysfs)
        except OSError:
            ctrls = ()
        if not force and ctrls == self.ctrls and not self.adapter_err:
            await self.keep_no_sniff()               # the kernel restores SNIFF every time a controller is switched on
            return                                   # after an error: a retry on every refresh until it succeeds
        self.ctrls, old = ctrls, self.pick
        mode = self.get_mode()
        try:
            self.pick = await asyncio.to_thread(btadapter.apply, ctrls, mode, self.key, self.sysfs)
        except OSError as e:
            self.pick = btadapter.choose(ctrls, mode, self.key)
            if str(e) != self.adapter_err:
                print(f"Bluetooth adapter: cannot switch ({e}) - run the sender as root")
            self.adapter_err = str(e) or repr(e)
            return
        recovered = bool(self.adapter_err)           # an earlier failure: the chosen one has only now been unblocked
        if recovered:
            print("Bluetooth adapter: switched after an earlier error")
            self.adapter_err = ""
        if len(ctrls) > 1 or force:
            print(btadapter.describe(ctrls, self.pick, mode))
        if self.pick is not None and (self.pick != old or recovered):
            # BlueZ usually powers on an unblocked controller itself (AutoEnable); when it does not - here, without waiting for the window
            with contextlib.suppress(BtError):
                await self.set_prop(self.prefer(), ADAPTER, "Powered", True)
            await self.keep_no_sniff()               # powering on a moment ago restored SNIFF

    async def keep_no_sniff(self) -> None:
        """Keep the chosen controller without SNIFF - again when the kernel restored it (a controller power-on).

        Without this a pad connected after the power-on (phone start, a dongle unblocked) had SNIFF for the whole
        connection: delay spikes of the first press after a pause.
        """
        if self.pick is None or self.sysfs is None:
            return
        name = self.pick.name
        st = await asyncio.to_thread(btadapter.dev_info, name)
        if st is None:                               # the state cannot be read - the setting from the controller choice stays
            return
        if await asyncio.to_thread(btadapter.ensure_no_sniff, name, None, lambda _name: st):
            print(f"Bluetooth adapter: sniff mode disabled on {name} (controller was powered on)")

    async def set_mode(self, mode: int, key: str = "") -> None:
        """From the window: BTA_AUTO / BTA_MANUAL (BT_ADAPTER) or a specific controller (BT_ADAPTER_USE -> BTA_PICK).

        The choice is validated, remembered in bt.json, and applied at once. Entering manual mode first unblocks every
        controller, so that nothing stays blocked by us.
        """
        if self.sysfs is None:
            raise BtError("adapter choice disabled")
        if mode not in btadapter.MODE_NAMES or (mode == BTA_PICK) != bool(key):
            raise BtError(f"unknown adapter mode {mode}")
        was = self.get_mode()
        self.mode, self.key = mode, key
        btadapter.save_mode(getattr(C, "BT_FILE", ""), mode, key)
        if mode == BTA_MANUAL and was != BTA_MANUAL:
            await asyncio.to_thread(btadapter.unblock_all, self.ctrls or btadapter.scan(self.sysfs), self.sysfs)
        await self.check_adapters(force=True)

    async def use_adapter(self, index: int) -> None:
        """Use the controller hciN from the list in the window - remembered by the device identifier, not by the number."""
        await self.check_adapters()
        c = next((c for c in self.ctrls or () if c.index == index), None)
        if c is None:
            raise BtError(f"no controller hci{index}", BTR_NOT_FOUND)
        await self.set_mode(BTA_PICK, c.key)

    def adapter_flags(self) -> int:
        """Return the btstate bits: which controllers there are, whether the one in use is USB, and the mode.

        Zero means there is no choice - the window hides the switch.
        """
        if self.sysfs is None:
            return 0
        ctrls = self.ctrls or ()
        return ((BTF_HAS_BUILTIN if any(not c.usb for c in ctrls) else 0) | (BTF_HAS_USB if any(c.usb for c in ctrls) else 0)
                | (BTF_ON_USB if self.pick is not None and self.pick.usb else 0) | self.get_mode() << BTF_MODE_SHIFT)

    def ctrl_rows(self) -> list[tuple[int, int, str]]:
        """Return the controllers for the window: (BTC_*, N of hciN, device name)."""
        picked = self.get_mode() == BTA_PICK
        rows = []
        for c in self.ctrls or ():
            flags = ((BTC_USB if c.usb else 0) | (BTC_FIXED if c.rank == btadapter.USB_FIXED else 0)
                     | (BTC_ACTIVE if c == self.pick else 0)
                     | (BTC_BLOCKED if self.sysfs is not None and btadapter.is_blocked(c.name, self.sysfs) else 0)
                     | (BTC_PICKED if picked and c.key == self.key else 0))
            rows.append((flags, c.index, c.label))
        return rows

    def no_adapter(self) -> BtError:
        """Build the error for a missing adapter.

        With missing permissions (the sender not running as root) the window shows "no permission", not the generic
        "failed".
        """
        return BtError(f"no adapter (state {self.state})", BTR_DENIED if self.state == BTS_DENIED else BTR_FAILED)

    def need(self, mac: str, *, known: bool = True) -> str:
        """Return the D-Bus path of a device; with ``known`` it must be in BlueZ's list (found by a scan or paired)."""
        if self.state in (BTS_OFF, BTS_BLOCKED):
            raise BtError("adapter powered off", BTR_OFF)
        if not self.adapter:
            raise self.no_adapter()
        if known and mac not in {d.mac for d in self.devices}:
            raise BtError("unknown device - scan first", BTR_NOT_FOUND)
        return dev_path(self.adapter, mac)

    async def power(self, on: bool) -> None:
        """Switch the adapter's radio on or off (Powered); an rfkill-blocked adapter cannot be powered on from here."""
        if self.state == BTS_BLOCKED and on:
            raise BtError("adapter blocked (rfkill)", BTR_OFF)
        if not self.adapter:
            raise self.no_adapter()
        await self.set_prop(self.adapter, ADAPTER, "Powered", on)
        self.state = BTS_OK if on else BTS_OFF

    async def ensure_on(self) -> None:
        """Power the adapter on if it is currently off or blocked."""
        if self.state in (BTS_OFF, BTS_BLOCKED):
            await self.power(True)

    async def get_bool(self, path: str, iface: str, name: str) -> bool | None:
        """Read a BlueZ boolean property; None = absent (an older BlueZ without "Bonded") or unreadable."""
        try:
            return bool(json.loads(await self.busctl("get-property", BLUEZ, path, iface, name))["data"])
        except (BtError, ValueError, KeyError, TypeError):
            return None

    async def bonded(self, path: str) -> bool:
        """Tell whether the link key is stored.

        Paired without bonding (an adapter with Pairable=false, e.g. a freshly plugged-in dongle) works only until
        disconnection: BlueZ (input, ClassicBondedOnly) rejects HID - no /dev/input, and a pad switched on with PS
        blinks for a few seconds and goes dark. An older BlueZ without the "Bonded" property - Paired means the same.
        """
        return await self.get_bool(path, DEVICE, "Bonded") is not False

    async def _pair_once(self, path: str) -> None:
        """Call Pair on the device; "already exists" is not an error (it was paired in the meantime)."""
        try:
            await self.call(path, DEVICE, "Pair", timeout=PAIR_S)
        except BtError as e:
            if "already exists" not in str(e).lower():
                raise

    async def pair(self, mac: str) -> None:
        """Pair with bonding, trust (the pad then connects by itself) and connect.

        BlueZ without an agent = NoInputNoOutput, like the DualSense. Paired without bonding -> forgotten and paired
        anew (the pad must be in pairing mode); paired with bonding -> only trusting and connecting.

        Only game controllers (BTD_PAD): pairing without an agent (Just Works) with trust, ordered over the network,
        would let someone attach e.g. a foreign Bluetooth keyboard to the sender (root). Pairable goes back to false
        after the attempt - the adapter is not left open for pairing.
        """
        await self.stop_scan()
        path = self.need(mac)
        dev = next(d for d in self.devices if d.mac == mac)
        if not dev.flags & BTD_PAD:
            raise BtError("not a game controller - only pads can be paired from the window", BTR_DENIED)
        try:
            with contextlib.suppress(BtError):           # without Pairable BlueZ pairs without storing the key
                await self.set_prop(self.adapter, ADAPTER, "Pairable", True)
            if dev.flags & BTD_PAIRED and not await self.bonded(path):
                print(f"Bluetooth {mac}: paired without bonding - re-pairing")
                await self.call(self.adapter, ADAPTER, "RemoveDevice", "o", path)
                raise BtError("paired without a stored key - removed, put the pad in pairing mode and pair again",
                              BTR_AUTH)
            if not dev.flags & BTD_PAIRED:
                await self._pair_once(path)
                if not await self.bonded(path):
                    raise BtError("paired without a stored key (adapter not bondable) - pair again", BTR_AUTH)
        finally:
            with contextlib.suppress(BtError):
                await self.set_prop(self.adapter, ADAPTER, "Pairable", False)
        await self.set_prop(path, DEVICE, "Trusted", True)
        await self.call(path, DEVICE, "Connect", timeout=CONNECT_S)

    async def connect(self, mac: str) -> None:
        """Connect a device; trust it here too if it is a pad.

        A pad paired earlier outside the window (bluetoothctl) without trust would connect only from the phone, and when
        switched on with the PS button it went dark after a few seconds. Trust only for pads: a trusted device can later
        connect by itself (a foreign device - not without consent from the window).
        """
        await self.stop_scan()
        path = self.need(mac)
        if next((d.flags for d in self.devices if d.mac == mac), 0) & BTD_PAD:
            with contextlib.suppress(BtError):
                await self.set_prop(path, DEVICE, "Trusted", True)
        await self.call(path, DEVICE, "Connect", timeout=CONNECT_S)

    async def disconnect(self, mac: str) -> None:
        """Disconnect a device (D-Bus Disconnect)."""
        await self.call(self.need(mac), DEVICE, "Disconnect", timeout=CONNECT_S)

    async def remove(self, mac: str) -> None:
        """Forget a device (e.g. a pad later paired with a console - the old keys do not work anyway)."""
        path = self.need(mac)
        await self.call(self.adapter, ADAPTER, "RemoveDevice", "o", path)

    async def disconnect_pad(self, mac: str) -> bool:
        """Switch a pad off (window / idle) with a D-Bus Disconnect on the controller the pad is connected to.

        `bluetoothctl disconnect` looks for the device only on bluetoothctl's default controller (hci0) - for a pad on a
        dongle (hci1) it ended with "Device ... not available" and the pad did not switch off.

        Returns:
            False when D-Bus did not help (no busctl / BlueZ, an unknown pad) - then bluetoothctl remains as the fallback.
        """
        try:
            out = await self.busctl("call", BLUEZ, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
            objects: dict[str, dict[str, Any]] = json.loads(out)["data"][0]
        except (BtError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            return False
        want = mac.upper()
        found = [(p, d) for p, ifaces in objects.items() if isinstance(d := ifaces.get(DEVICE), dict)
                 and str(_val(d, "Address", "")).upper() == want]
        path = next((p for p, d in found if _val(d, "Connected", False)), None)
        if path is None:
            return False
        try:
            await self.call(path, DEVICE, "Disconnect", timeout=CONNECT_S)
        except BtError as e:
            print(f"Bluetooth disconnect {mac} failed: {e}")
            return False
        return True

    # ---------- scan ----------
    def scanning(self) -> bool:
        """True while the scan subprocess is alive."""
        return self.scan is not None and self.scan.returncode is None

    async def start_scan(self, secs: int) -> None:
        """Scan for ``secs`` seconds (the bluetoothctl process ends the discovery session by itself); a running scan gets the new limit."""
        async with self.scan_lock:
            await self._start_scan(secs)

    async def _start_scan(self, secs: int) -> None:
        """Start the scan subprocess (the caller holds scan_lock).

        With one controller, `bluetoothctl --timeout N scan on` is enough. With several, bluetoothctl would scan on its
        own default controller, so it is started in interactive mode and told over stdin which controller to select; a
        separate watcher then enforces the time limit. Errors become a result for the window instead of exceptions.
        """
        await self._stop_scan()
        await self.refresh()
        try:
            await self.ensure_on()
            if not self.adapter:
                raise self.no_adapter()
            if self.adapter_count > 1 and self.adapter_addr:
                # several controllers (built-in blocked + dongle): `bluetoothctl scan on` scans with bluetoothctl's default
                # controller - the first one BlueZ knows (hci0, usually the blocked built-in one), not the chosen one.
                # The scan ended at once with a "NotReady" error and a new pad could not be paired from the window.
                # Interactive mode: "select <address>" and "scan on" through stdin (--timeout works only without
                # interactive mode - the limit is watched by _scan_watch).
                self.scan = await asyncio.create_subprocess_exec(BLUETOOTHCTL, stdin=PIPE, stdout=PIPE, stderr=DEVNULL)
                state.spawn(_scan_feed(self.scan, self.adapter_addr))
                limit = secs
            else:
                self.scan = await asyncio.create_subprocess_exec(BLUETOOTHCTL, "--timeout", str(secs), "scan", "on",
                                                                 stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
                limit = secs + 1                     # a margin: bluetoothctl ends by itself
        except FileNotFoundError:
            self.last = (BT_SCAN_START, BTR_FAILED, b"")
            print("Bluetooth scan failed: bluetoothctl not found (install bluez)")
        except OSError as e:                         # without this the exception ended the scan task with no result for the window
            self.last = (BT_SCAN_START, BTR_DENIED if isinstance(e, PermissionError) else BTR_FAILED, b"")
            print(f"Bluetooth scan failed: cannot run bluetoothctl: {e}")
        except BtError as e:
            self.last = (BT_SCAN_START, e.result, b"")
            print(f"Bluetooth scan failed: {e}")
        else:
            self.scan_end = time.monotonic() + secs
            self.last = (BT_SCAN_START, BTR_OK, b"")
            print(f"Bluetooth scan for {secs}s")
            state.spawn(self._scan_watch(self.scan, limit))
        self.poke()

    async def _scan_watch(self, proc: asyncio.subprocess.Process, limit: float) -> None:
        """Wait for the end of a scan.

        The process ends by itself (--timeout); without that (interactive mode, bluetoothctl without --timeout) it is
        stopped after ``limit`` seconds - a scan takes the radio from the pads and must not last longer than promised.
        """
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), limit)
        except TimeoutError:
            if self.scan is proc:
                async with self.scan_lock:
                    if self.scan is proc:
                        await self._stop_scan()
            await proc.wait()
        if self.scan is proc:
            self.scan = None
            print("Bluetooth scan finished")
            self.poke()

    async def stop_scan(self) -> None:
        """Stop the scan - also one that is just starting (it waits for the end of the start)."""
        async with self.scan_lock:
            await self._stop_scan()

    async def _stop_scan(self) -> None:
        """Terminate the scan subprocess (SIGTERM, then SIGKILL after 3 s); the caller holds scan_lock."""
        proc, self.scan = self.scan, None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        print("Bluetooth scan stopped")

    def kill_scan(self) -> None:
        """When the sender shuts down (without a loop): the scan must not outlive it."""
        if self.scan is not None and self.scan.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.scan.kill()

    # ---------- to the window ----------
    def active(self, now: float) -> bool:
        """True while the window is (recently) interested: a command arrived lately, a scan runs, or an operation is in progress."""
        return now < self.active_until or self.scanning() or self.running

    def send(self, full: bool, now: float | None = None) -> None:
        """Send the btstate packet (+ a btdev round when full): the state, the scan, the last operation, the device list.

        The btstate packet always goes; with full=True it is followed by one btdev packet per device (index / total, so
        the window can assemble the list and detect a missing one) and by the controller rows. `gen` numbers the rounds.
        """
        now = time.monotonic() if now is None else now
        scanning = self.scanning()
        flags = ((BTF_SCANNING if scanning else 0) | (BTF_BUSY if self.running else 0) | (BTF_LIST if full else 0)
                 | self.adapter_flags())
        left = clamp(round(self.scan_end - now), 0, 255) if scanning else 0
        devs = self.devices if full else []
        if full:
            self.gen = (self.gen + 1) & 0xFFFF
        conn = sum(1 for d in self.devices if d.flags & BTD_CONNECTED)
        op, res, mac = self.last
        self.emit(T_BTSTATE, 255, self.state, flags, left, conn, len(devs), self.gen, op, res, mac or bytes(6))
        for k, d in enumerate(devs):
            rssi = BT_RSSI_NONE if d.rssi is None else clamp(d.rssi, BT_RSSI_NONE + 1, 127)
            self.emit(T_BTDEV, 255, self.gen, k, len(devs), mac_bytes(d.mac), rssi, d.flags, utf8_field(d.name, NAME_MAX, "…"))
        if full and self.sysfs is not None:          # the sender's controllers (panel: a list with names, "Use")
            rows = self.ctrl_rows()
            self.cgen = (self.cgen + 1) & 0xFFFF
            for k, (cflags, index, label) in enumerate(rows):
                self.emit(T_BTCTRL, 255, self.cgen, k, len(rows), cflags, index, utf8_field(label, CTRL_NAME_MAX, "…"))

    async def loop(self) -> None:
        """Report the state to the receiver.

        Every IDLE_S just the state; with the panel open, during a scan or an operation every TICK_S, with the list.

        Only a receiver that knows the new packets (it sent scfg) gets them - an older one rejects unknown lengths.
        When no receiver is present, the loop still runs the controller choice every IDLE_S. It sleeps on an Event, so
        a command (poke) wakes it immediately instead of waiting for the next tick.
        """
        self.wake = asyncio.Event()
        t_sent = t_adapter = float("-inf")
        try:
            while True:
                now = time.monotonic()
                active = self.active(now)
                if net.new_receiver(now) and (active or now - t_sent >= IDLE_S):
                    t_sent = t_adapter = now
                    if not self.running:
                        await self.refresh()          # during an operation it refreshes itself (BlueZ is busy)
                    self.send(full=active)
                elif now - t_adapter >= IDLE_S and not self.running:
                    # controller choice and link policy without a receiver: the phone starts while the PC is off (a pad wakes it
                    # through Wake-on-LAN), the receiver is not running - earlier the dongle and the lack of SNIFF waited for the window on
                    # the PC
                    t_adapter = now
                    await self.check_adapters()
                self.wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), TICK_S if active else IDLE_S / 5)
        finally:
            self.kill_scan()


OPS: dict[int, Callable[[Bluetooth, int, str], Awaitable[None]]] = {
    BT_PAIR: lambda b, _arg, mac: b.pair(mac),
    BT_CONNECT: lambda b, _arg, mac: b.connect(mac),
    BT_DISCONNECT: lambda b, _arg, mac: b.disconnect(mac),
    BT_REMOVE: lambda b, _arg, mac: b.remove(mac),
    BT_POWER: lambda b, arg, _mac: b.power(bool(arg)),
    BT_ADAPTER: lambda b, arg, _mac: b.set_mode(arg),
    BT_ADAPTER_USE: lambda b, arg, _mac: b.use_adapter(arg),
}
OP_NAMES = {BT_LIST: "list", BT_SCAN_START: "scan", BT_SCAN_STOP: "scan stop", BT_PAIR: "pair", BT_CONNECT: "connect",
            BT_DISCONNECT: "disconnect", BT_REMOVE: "remove", BT_POWER: "power", BT_ADAPTER: "adapter",
            BT_ADAPTER_USE: "use adapter"}


bt = Bluetooth(sysfs=btadapter.SYSFS)
