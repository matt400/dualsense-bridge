"""Sender: pad discovery (evdev), slot assignment, the life cycle of a controller."""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import os
import re
import time
from collections.abc import Coroutine, Sequence
from typing import Any, Final, Literal

import evdev
from evdev import InputDevice

import sender_events as events
import sender_state as state
import sender_streams as streams
from proto import E_ERROR, E_NO_SLOT, E_PAD_OFF, E_PAD_ON, LAT_NONE, T_BYE, TRIG2_OFF
from sender_config import C, IDLE_TP, SETTLE_S
from sender_ctrl import Ctrl
from sender_net import emit_repeat
from sender_rumble import Rumble

Kind = Literal["pad", "motion", "touch"]
Group = dict[str, InputDevice]          # {"pad" | "motion" | "touch": device} of one physical pad

_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.IGNORECASE)
_not_pads: dict[str, tuple[int, int] | None] = {}   # path -> node identifier (inode, ctime) of a device that is not a pad
_first_seen: dict[str, float] = {}     # key -> when we first saw an incomplete group of devices
_no_slot: set[str] = set()             # pads rejected for lack of a free slot (max_pads) - the message once per connection

INPUT_DIR: Final = b"/dev/input"
PAD_NAME_MARK: Final = "Wireless Controller"   # all three evdev devices of a DualSense contain this in their name
TOUCH_NAME_MARK: Final = "Touchpad"
MOTION_NAME_MARK: Final = "Motion"
UNKNOWN_KEY: Final = "unknown"
LATE_KINDS: Final[tuple[Kind, ...]] = ("motion", "touch")   # devices that may appear later than the pad itself
KIND_ATTR: Final = {"motion": "motion_dev", "touch": "touch_dev"}
NO_PAD_ORDER: Final = 1 << 30           # sort key of a group without a pad node: after every real one
MIN_SCAN_WAIT_S: Final = 0.05           # shortest sleep between scans while a group settles
SCAN_SLACK_S: Final = 0.02              # added to the settle deadline so the scan lands just after it
INOTIFY_READ_SIZE: Final = 4096
GYRO_AXES: Final = 6                    # motion = 3 gyro + 3 accelerometer values


def _node_id(path: str) -> tuple[int, int] | None:
    """Identify one incarnation of a /dev/input/eventN node as (inode, ctime), or None if it is gone.

    A node is created anew at every connection - this lets discover() remember "this node is not a pad" without
    re-opening it every scan, yet look at it again when the node is replaced by a new device with the same name.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_ino, st.st_ctime_ns


def event_num(dev: InputDevice) -> int:
    """Return the trailing number of the device path (/dev/input/event7 -> 7), or 0 if there is none; used for ordering."""
    m = re.search(r"(\d+)$", os.fsdecode(dev.path))
    return int(m.group(1)) if m else 0


def _kind_of(name: str) -> Kind:
    """Classify a DualSense evdev device by its name: touchpad, motion sensors, or the pad itself."""
    if TOUCH_NAME_MARK in name:
        return "touch"
    return "motion" if MOTION_NAME_MARK in name else "pad"


def discover() -> dict[str, Group]:
    """Scan /dev/input and return the not-yet-used pad devices grouped by physical pad.

    A DualSense shows up as three evdev devices ("Wireless Controller", "... Motion Sensors", "... Touchpad") that
    share one key (uniq = Bluetooth MAC, or phys). Devices already used by an active pad are skipped, and devices that
    turned out not to be a DualSense are remembered per node so they are not re-opened on every scan. A duplicate
    device of the same kind inside one group is closed immediately.

    Returns:
        ``{key: {"pad"|"motion"|"touch": device}}``.
    """
    groups: dict[str, Group] = {}
    paths = evdev.list_devices()
    for gone in [p for p in _not_pads if p not in paths]:
        del _not_pads[gone]
    busy = {d.path for c in state.active.values() for d in c.devices()}
    for path in paths:
        if path in busy:
            continue
        nid = _node_id(path)
        if nid is not None and _not_pads.get(path) == nid:
            continue
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if PAD_NAME_MARK not in dev.name:
            dev.close()
            _not_pads[path] = nid
            continue
        group = groups.setdefault(dev.uniq or dev.phys or UNKNOWN_KEY, {})
        kind = _kind_of(dev.name)
        if kind in group:
            dev.close()
        else:
            group[kind] = dev
    return groups


def close_group(group: Group) -> None:
    """Close every device of a discovered group; a device that is already gone is not an error."""
    for dev in group.values():
        with contextlib.suppress(OSError):
            dev.close()


def _load_slots() -> dict[str, int]:
    """Return the remembered pad (MAC) -> slot assignment from earlier runs (slots_file), loaded once and cached in state."""
    if state.saved_slots is None:
        state.saved_slots = {}
        if C.SLOTS_FILE:
            try:
                with open(C.SLOTS_FILE, encoding="utf-8") as f:
                    raw = json.load(f)
                state.saved_slots = {str(k): int(v) for k, v in raw.items() if 0 <= int(v) < C.MAX_PADS}
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, AttributeError) as e:
                print(f"Slots file {C.SLOTS_FILE} ignored: {e!r}")
    return state.saved_slots


def _save_slots() -> None:
    """Write the remembered slot table to slots_file (atomically, fsync in a worker thread); errors are logged only."""
    if not C.SLOTS_FILE:
        return
    try:
        state.write_json_later(C.SLOTS_FILE, state.saved_slots, indent=1, sort_keys=True)
    except OSError as e:
        print(f"Cannot write {C.SLOTS_FILE}: {e!r}")


def pick_slot(key: str) -> int | None:
    """Choose a fixed slot for a pad: the same pad (MAC) gets the same number even after a sender restart.

    In a game (Steam, XInput) it therefore stays the same player regardless of the order in which the pads are
    switched on. Preference order: the slot the pad already holds in this run, then its remembered slot, then a free
    slot that no other known pad has reserved, finally any free slot. Only pads with a MAC are written to the file
    (a USB pad without uniq has a changing key).

    Args:
        key: The pad's key (MAC, or phys path).

    Returns:
        The slot number, or None when all slots are in use.
    """
    if key in state.slots:
        return state.slots[key]
    saved = _load_slots()
    taken = {i for k, i in state.slots.items() if k in state.active}
    free = [i for i in range(C.MAX_PADS) if i not in taken]
    if not free:
        return None
    reserved = {v for k, v in saved.items() if k != key}
    preferred = saved.get(key)
    slot = preferred if preferred in free else next((n for n in free if n not in reserved), free[0])
    for other in [k for k, v in state.slots.items() if v == slot]:
        del state.slots[other]
    state.slots[key] = slot
    if saved.get(key) != slot and _MAC.match(key):
        for other in [k for k, v in saved.items() if v == slot]:
            del saved[other]
        saved[key] = slot
        _save_slots()
    return slot


def dispatch_feedback(idx: int, vals: tuple[int, int, int, int, int]) -> None:
    """Route rumble / lightbar feedback from the PC to the pad in slot idx (ignored if no pad is there)."""
    if (c := state.ctrl_by_idx(idx)) is not None:
        c.apply_feedback(*vals)


def dispatch_triggers(idx: int, vals: Sequence[Any]) -> None:
    """Route adaptive-trigger settings from the PC to the pad in slot idx (ignored if no pad is there)."""
    if (c := state.ctrl_by_idx(idx)) is not None:
        c.set_triggers(vals)


def grab(dev: InputDevice, label: str) -> None:
    """Take exclusive access to an evdev device, so the phone's own system stops reacting to the pad's input."""
    try:
        dev.grab()
        print(f"{label}: grabbed")
    except OSError as e:
        print(f"{label}: grab failed: {e}")


def attach(c: Ctrl, kind: Kind, dev: InputDevice) -> None:
    """Attach a motion / touchpad device to a running pad (also when it appears later than the pad).

    The device is stored on the Ctrl and its reading loop runs as a separate task, kept in c.extra, so losing that
    device never disconnects the pad itself. The touchpad is grabbed if configured.
    """
    setattr(c, KIND_ATTR[kind], dev)
    if kind == "touch":
        if C.GRAB_TOUCH:
            grab(dev, f"Touchpad {c.idx}")
        coro = streams.stream_touch(c)
    else:
        coro = streams.stream_motion(c)
    task = asyncio.create_task(_run_extra(c, kind, dev, coro))
    c.extra.add(task)
    task.add_done_callback(c.extra.discard)


def _reset_lost_stream(c: Ctrl, kind: Kind) -> None:
    """Zero the values of a lost motion / touch stream and tell the receiver.

    The last values must not stay in the receiver: a gyro value from the moment of loss (e.g. 50 °/s) would keep
    turning gyro aiming forever, and a finger placed on the touchpad would stay "placed" in the game.
    """
    if kind == "touch":
        c.touch = IDLE_TP
        c.tp = 0
    else:
        c.motion = [0] * GYRO_AXES
        c.bt_us = LAT_NONE                # without a motion device there are no pad timestamps
    if c.closing:                         # when the pad is closing, send_neutral sends the same thing
        return
    if kind == "touch":
        c.send_touch(force=True)
        c.send_click()
    else:
        c.send_state(force=True)


async def _run_extra(c: Ctrl, kind: Kind, dev: InputDevice, coro: Coroutine[Any, Any, None]) -> None:
    """Run a motion / touchpad stream; its end (the device disappeared) detaches only that device.

    The pad keeps working, and the supervisor attaches the device again when it returns.
    """
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except OSError:
        pass                              # the device disappeared: the normal way for the stream to end
    except Exception as e:  # noqa: BLE001 - a task boundary: any bug must end this stream, not the pad
        print(f"Controller {c.idx} {kind} error: {e!r}")
    finally:
        if getattr(c, KIND_ATTR[kind]) is dev:
            setattr(c, KIND_ATTR[kind], None)
        _reset_lost_stream(c, kind)
        for release in (dev.ungrab, dev.close):
            with contextlib.suppress(OSError):
                release()
        if state.active.get(c.key) is c and not c.closing:
            print(f"Controller {c.idx}: {kind} device lost - waiting for it to come back")


def _reject(key: str) -> None:
    """Report a pad with no free slot once, in the log and in the window's event log."""
    if key in _no_slot:
        return
    _no_slot.add(key)
    print(f"Controller {key}: ignored - all {C.MAX_PADS} slot(s) taken (max_pads in sender.toml)")
    events.warn(E_NO_SLOT, 255, key)


def incomplete(c: Ctrl) -> bool:
    """Return True while a pad still misses its motion or touchpad device (they can appear a moment after the pad)."""
    return c.motion_dev is None or c.touch_dev is None


def _announce(c: Ctrl) -> None:
    """Print the devices of a freshly connected pad."""
    print(f"== Controller {c.idx} ({c.key})")
    print(f"   Pad:    {c.pad_dev.name} ({os.fsdecode(c.pad_dev.path)})")
    print(f"   Motion: {c.motion_dev.name if c.motion_dev else 'NOT FOUND'}")
    print(f"   Touch:  {c.touch_dev.name if c.touch_dev else 'NOT FOUND'}")
    print(f"   Lightbar: {c.led or 'not found'}")


def _start_pad(c: Ctrl) -> list[asyncio.Task[None]]:
    """Set the pad up: initial state, rumble backend, grab, announcement; start its main streams and attach the extras.

    Returns:
        The main stream tasks (pad, battery); motion and touchpad run as separate tasks in c.extra, so losing them
        does not disconnect the pad.
    """
    c.init_state()
    c.rumble = Rumble(c.idx, c.pad_dev, C.RUMBLE)
    if c.triggers != TRIG2_OFF:           # trigger effects came from the PC before the pad was ready
        c.rumble.set_triggers(c.triggers)
    _announce(c)
    if C.GRAB_PAD:
        grab(c.pad_dev, f"Pad {c.idx}")
    c.send_ident()                        # the receiver knows right away which pad this is (remap profile)
    events.info(E_PAD_ON, c.idx, c.key or "")
    tasks = [asyncio.create_task(streams.stream_pad(c)), asyncio.create_task(streams.stream_battery(c))]
    for kind in LATE_KINDS:
        dev = getattr(c, KIND_ATTR[kind])
        if dev is not None:
            setattr(c, KIND_ATTR[kind], None)
            attach(c, kind, dev)
    return tasks


async def _wait_first(c: Ctrl, tasks: list[asyncio.Task[None]]) -> None:
    """Wait for the first main stream to finish, cancel the rest and report an error that ended it (OSError = pad gone)."""
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for t in done:
        exc = t.exception()
        if exc and not isinstance(exc, OSError):
            print(f"Controller {c.idx} error: {exc!r}")
            events.error(E_ERROR, c.idx, repr(exc))


async def _teardown(c: Ctrl, tasks: list[asyncio.Task[None]]) -> None:
    """Stop every task and timer of a pad, close its devices, and tell the receiver to remove it.

    A neutral state and a repeated "bye" go out so the receiver releases every button and drops the pad; the
    slot's active entry is freed last. Once "bye" is out the pad is sealed: the delayed repeats that are still
    scheduled (burst of a button change, click, tap) send nothing for the released slot.
    """
    c.closing = True
    running = [*tasks, *c.extra]
    for t in running:
        t.cancel()
    if running:
        # wait for the read loops: each unregisters its descriptor from the event loop, which has to happen before the
        # device is closed below (a stale registration would break the next device that gets the same descriptor number)
        await asyncio.gather(*running, return_exceptions=True)
    c.cancel_timers()
    c.stop_feedback()
    for dev in c.devices():
        with contextlib.suppress(OSError):
            dev.ungrab()
        with contextlib.suppress(OSError):
            dev.close()
    c.send_neutral()
    emit_repeat(T_BYE, c.idx)
    c.sealed = True
    state.active.pop(c.key, None)
    print(f"Controller {c.idx}: disconnected (waiting for reconnect)")
    events.info(E_PAD_OFF, c.idx)


async def run_ctrl(c: Ctrl) -> None:
    """Life cycle of one pad: set it up, stream it until it goes away, then clean up.

    The function waits for the first main stream to finish (the pad disconnected or an error) and cancels the rest.
    The cleanup always runs, whatever ended the pad.
    """
    tasks: list[asyncio.Task[None]] = []
    try:
        tasks = _start_pad(c)
        await _wait_first(c, tasks)
    except OSError as e:
        print(f"Controller {c.idx}: device error: {e}")
        events.error(E_ERROR, c.idx, str(e))
    finally:
        await _teardown(c, tasks)


class DevWatch:
    """inotify on /dev/input: a new eventN node (pad switched on / connected) wakes the supervisor at once.

    Without it a pad waited for the next scan, up to rescan_s (on average +1 s when connecting). Without inotify
    (another platform, no directory) - plain scanning every rescan_s.

    The watch is created through libc with ctypes (the standard library has no inotify). Raw inotify events are never
    parsed: any activity just sets an asyncio.Event, because the supervisor re-scans the whole directory anyway.
    """

    IN_ATTRIB, IN_CREATE = 0x004, 0x100
    IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000
    DEBOUNCE_S = 0.05             # the pad, motion and touchpad nodes appear together - one scan for the whole group

    def __init__(self) -> None:
        """Create a watcher that is not started yet."""
        self.fd: int | None = None
        self.event = asyncio.Event()

    def start(self, path: bytes = INPUT_DIR) -> bool:
        """Start watching the directory for created / changed nodes.

        Returns:
            False if inotify is unavailable.
        """
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            fd = libc.inotify_init1(self.IN_NONBLOCK | self.IN_CLOEXEC)
            if fd < 0:
                return False
            if libc.inotify_add_watch(fd, path, self.IN_CREATE | self.IN_ATTRIB) < 0:
                os.close(fd)
                return False
            asyncio.get_running_loop().add_reader(fd, self._readable)
        except (OSError, AttributeError, NotImplementedError):
            return False
        self.fd = fd
        return True

    def _readable(self) -> None:
        """Event-loop callback: throw away the pending inotify data and raise the "something changed" flag."""
        if self.fd is None:
            return
        with contextlib.suppress(OSError):        # EAGAIN once the queue is drained
            while os.read(self.fd, INOTIFY_READ_SIZE):
                pass
        self.event.set()

    async def wait(self, timeout: float) -> bool:
        """Wait for a change in /dev/input or ``timeout`` seconds.

        After a change a short debounce delay lets the whole group of nodes (pad, motion, touchpad) appear before the
        caller scans.

        Returns:
            True if there was a change.
        """
        try:
            await asyncio.wait_for(self.event.wait(), timeout)
        except TimeoutError:
            return False
        await asyncio.sleep(self.DEBOUNCE_S)
        self.event.clear()
        return True

    def close(self) -> None:
        """Stop watching and close the inotify descriptor."""
        if self.fd is not None:
            with contextlib.suppress(RuntimeError):     # no running loop: the reader died with it
                asyncio.get_running_loop().remove_reader(self.fd)
            os.close(self.fd)
            self.fd = None


def next_scan_s(now: float) -> float:
    """Return how long until the next scan: rescan_s, or sooner when an incomplete pad waits for its other devices (SETTLE_S)."""
    wait = C.RESCAN_S
    if _first_seen:
        wait = min(wait, max(MIN_SCAN_WAIT_S, SETTLE_S - (now - min(_first_seen.values())) + SCAN_SLACK_S))
    return wait


async def supervisor() -> None:
    """Entry point of the pad supervisor: start the /dev/input watcher, run the scan loop, close the watcher on exit."""
    watch = DevWatch()
    watch.start()
    try:
        await _supervise(watch)
    finally:
        watch.close()


def _attach_late_devices(c: Ctrl, group: Group) -> None:
    """Give a running pad the motion / touchpad devices that appeared after it, and close the rest of the group."""
    for kind in LATE_KINDS:
        if kind in group and getattr(c, KIND_ATTR[kind]) is None and not c.closing:
            dev = group.pop(kind)
            print(f"Controller {c.idx}: {kind} device attached ({os.fsdecode(dev.path)})")
            attach(c, kind, dev)
    close_group(group)


def _start_new_pad(key: str, group: Group, now: float) -> None:
    """Start a newly appeared pad, or hold it back for up to SETTLE_S while its motion / touchpad devices are missing.

    The devices of the group are closed when the pad is not started (it will be discovered again on the next scan);
    a pad with no free slot gets a rejection message.
    """
    first_seen = _first_seen.setdefault(key, now)
    if not ("motion" in group and "touch" in group) and now - first_seen < SETTLE_S:
        close_group(group)
        return
    idx = pick_slot(key)
    # without this pop a pad with no slot kept next_scan_s at MIN_SCAN_WAIT_S - ~17 wake-ups/s endlessly
    # (with full slots discover() no longer removed it)
    _first_seen.pop(key, None)
    if idx is None:
        close_group(group)
        _reject(key)
        return
    c = Ctrl(idx, key, group["pad"], group.get("motion"), group.get("touch"))
    state.active[key] = c
    state.spawn(run_ctrl(c))


def _scan_once() -> None:
    """Discover pads once: attach late devices to running pads, start new pads in slot order of their event node."""
    groups = discover()
    now = time.monotonic()
    for key in [k for k in _first_seen if k not in groups]:
        del _first_seen[key]
    _no_slot.intersection_update(groups)          # the pad went away - the message appears again on the next connection
    order = sorted(groups.items(), key=lambda kv: event_num(kv[1]["pad"]) if "pad" in kv[1] else NO_PAD_ORDER)
    for key, group in order:
        running = state.active.get(key)
        if running is not None:
            _attach_late_devices(running, group)
        elif "pad" not in group:
            close_group(group)
        else:
            _start_new_pad(key, group, now)


async def _supervise(watch: DevWatch) -> None:
    """The scan loop: find pads, give each a slot, start its run_ctrl task, and attach late devices to running pads.

    A scan runs when /dev/input changed, when a slot is still free, or when a running pad still misses a device.
    A newly appeared pad is held back for up to SETTLE_S until its motion and touchpad devices show up (they can
    appear a fraction of a second later), then gets a slot (or a rejection message if all are taken).
    """
    first = True
    changed = False
    while True:
        # all slots full: scan only after a change in /dev/input - a new pad over max_pads gets a message (_reject)
        if changed or len(state.active) < C.MAX_PADS or any(incomplete(c) for c in state.active.values()):
            _scan_once()
            if first and not state.active:
                print("No DualSense found yet - connect controller (bluetoothctl), waiting...")
            first = False
        changed = await watch.wait(next_scan_s(time.monotonic()))
