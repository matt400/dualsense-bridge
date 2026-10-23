"""Sender: pad discovery (evdev), slot assignment, the life cycle of a controller."""
import asyncio
import json
import os
import re
import time

import evdev

import sender_events as events
import sender_state as state
import sender_streams as streams
from proto import E_ERROR, E_NO_SLOT, E_PAD_OFF, E_PAD_ON, LAT_NONE, T_BYE, TRIG2_OFF, write_json
from sender_config import C, IDLE_TP, SETTLE_S
from sender_ctrl import Ctrl
from sender_rumble import Rumble
from sender_net import emit_repeat

_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.IGNORECASE)
_not_pads = {}       # path -> node identifier (inode, ctime) of a device that is not a pad


def _node_id(path):
    """A /dev/input/eventN node is created anew at every connection - (inode, ctime) tells the incarnations apart.

    It lets discover() remember "this node is not a pad" without re-opening it every scan, yet look at it again when
    the node is replaced by a new device with the same name.
    """
    try:
        st = os.stat(path)
        return st.st_ino, st.st_ctime_ns
    except OSError:
        return None
_first_seen = {}     # key -> when we first saw an incomplete group of devices
_no_slot = set()     # pads rejected for lack of a free slot (max_pads) - the message once per connection


def event_num(dev):
    """The trailing number of the device path (/dev/input/event7 -> 7); 0 if there is none. Used for ordering."""
    m = re.search(r"(\d+)$", dev.path)
    return int(m.group(1)) if m else 0


def discover():
    """Scan /dev/input and return the not-yet-used pad devices grouped by physical pad: {key: {"pad"|"motion"|"touch": device}}.

    A DualSense shows up as three evdev devices ("Wireless Controller", "... Motion Sensors", "... Touchpad") that
    share one key (uniq = Bluetooth MAC, or phys). Devices already used by an active pad are skipped, and devices that
    turned out not to be a DualSense are remembered per node so they are not re-opened on every scan. A duplicate
    device of the same kind inside one group is closed immediately.
    """
    groups = {}
    paths = evdev.list_devices()
    for p in [p for p in _not_pads if p not in paths]:
        del _not_pads[p]
    busy = {d.path for c in state.active.values() for d in c.devices()}
    for path in paths:
        if path in busy:
            continue
        nid = _node_id(path)
        if nid is not None and _not_pads.get(path) == nid:
            continue
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        name = d.name
        if "Wireless Controller" not in name:
            d.close()
            _not_pads[path] = nid
            continue
        key = d.uniq or d.phys or "unknown"
        kind = "touch" if "Touchpad" in name else "motion" if "Motion" in name else "pad"
        g = groups.setdefault(key, {})
        if kind in g:
            d.close()
        else:
            g[kind] = d
    return groups


def close_group(g) -> None:
    """Close every device of a discovered group, ignoring errors."""
    for d in g.values():
        try:
            d.close()
        except Exception:  # best effort: the device may already be gone
            pass


def _load_slots():
    """The remembered pad (MAC) -> slot assignment from earlier runs (slots_file), loaded once and cached in state."""
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
    """Write the remembered slot table to slots_file (atomically); errors are logged only."""
    if not C.SLOTS_FILE:
        return
    try:
        write_json(C.SLOTS_FILE, state.saved_slots, indent=1, sort_keys=True)
    except OSError as e:
        print(f"Cannot write {C.SLOTS_FILE}: {e!r}")


def pick_slot(key):
    """A fixed slot for a pad: the same pad (MAC) gets the same number even after a sender restart, so in a game
    (Steam, XInput) it stays the same player regardless of the order in which the pads are switched on.

    Preference order: the slot the pad already holds in this run, then its remembered slot, then a free slot that no
    other known pad has reserved, finally any free slot. Returns None when all slots are in use. Only pads with a MAC
    are written to the file (a USB pad without uniq has a changing key).
    """
    if key in state.slots:
        return state.slots[key]
    saved = _load_slots()
    taken = {i for k, i in state.slots.items() if k in state.active}
    pref = saved.get(key)
    free = [i for i in range(C.MAX_PADS) if i not in taken]
    if not free:
        return None
    # first the remembered slot; then a free one that no other known pad "reserves"; finally any free one
    reserved = {v for k, v in saved.items() if k != key}
    i = pref if pref in free else next((n for n in free if n not in reserved), free[0])
    for k in [k for k, v in state.slots.items() if v == i]:
        del state.slots[k]
    state.slots[key] = i
    if saved.get(key) != i and _MAC.match(key):   # only pads with a MAC are remembered (a USB pad without uniq has a changing key)
        for k in [k for k, v in saved.items() if v == i]:
            del saved[k]
        saved[key] = i
        _save_slots()
    return i


def dispatch_feedback(idx, vals) -> None:
    """Route rumble / lightbar feedback from the PC to the pad in slot idx (ignored if no pad is there)."""
    if (c := state.ctrl_by_idx(idx)) is not None:
        c.apply_feedback(*vals)


def dispatch_triggers(idx, vals) -> None:
    """Route adaptive-trigger settings from the PC to the pad in slot idx (ignored if no pad is there)."""
    if (c := state.ctrl_by_idx(idx)) is not None:
        c.set_triggers(vals)


def grab(dev, label) -> None:
    """Take exclusive access to an evdev device, so the phone's own system stops reacting to the pad's input."""
    try:
        dev.grab()
        print(f"{label}: grabbed")
    except OSError as e:
        print(f"{label}: grab failed: {e}")


KIND_ATTR = {"motion": "motion_dev", "touch": "touch_dev"}


def attach(c, kind, dev) -> None:
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
    t = asyncio.create_task(_run_extra(c, kind, dev, coro))
    c.extra.add(t)
    t.add_done_callback(c.extra.discard)


async def _run_extra(c, kind, dev, coro) -> None:
    """The end of a stream (the device disappeared) detaches only that device - the pad keeps working,
    and the supervisor attaches it again when it returns."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except OSError:
        pass
    except Exception as e:
        print(f"Controller {c.idx} {kind} error: {e!r}")
    finally:
        if getattr(c, KIND_ATTR[kind]) is dev:
            setattr(c, KIND_ATTR[kind], None)
        # the last values must not stay in the receiver: a gyro value from the moment of loss (e.g. 50 °/s) would keep turning
        # gyro aiming forever, and a finger placed on the touchpad would stay "placed" in the game
        if kind == "touch":
            c.touch = IDLE_TP
            c.tp = 0
        else:
            c.motion = [0] * 6
            c.bt_us = LAT_NONE                # without a motion device there are no pad timestamps
        if not c.closing:                     # when the pad is closing, send_neutral sends the same thing
            if kind == "touch":
                c.send_touch(force=True)
                c.send_click()
            else:
                c.send_state(force=True)
        for fn in (dev.ungrab, dev.close):
            try:
                fn()
            except Exception:  # best effort: the device may already be gone
                pass
        if state.active.get(c.key) is c and not getattr(c, "closing", False):
            print(f"Controller {c.idx}: {kind} device lost - waiting for it to come back")


def _reject(key) -> None:
    """A pad with no free slot: reported once in the log and in the window's event log (earlier it vanished without a trace)."""
    if key in _no_slot:
        return
    _no_slot.add(key)
    print(f"Controller {key}: ignored - all {C.MAX_PADS} slot(s) taken (max_pads in sender.toml)")
    events.warn(E_NO_SLOT, 255, key)


def incomplete(c) -> bool:
    """True while a pad still misses its motion or touchpad device (they can appear a moment after the pad)."""
    return c.motion_dev is None or c.touch_dev is None


async def run_ctrl(c) -> None:
    """Life cycle of one pad: set it up, stream it until it goes away, then clean up.

    Setup reads the initial state, starts the rumble backend, grabs the pad, announces it to the receiver and starts
    the pad and battery streams; motion and touchpad run as separate tasks. The function then waits for the first
    main stream to finish (the pad disconnected or an error) and cancels the rest. The finally block always runs:
    it stops every task and timer, closes the devices, sends a neutral state and a repeated "bye" so the receiver
    removes the pad, and frees the slot's active entry.
    """
    tasks = []
    try:
        c.init_state()
        c.rumble = Rumble(c.idx, c.pad_dev, C.RUMBLE)
        if c.triggers != TRIG2_OFF:           # trigger effects came from the PC before the pad was ready
            c.rumble.set_triggers(c.triggers)
        print(f"== Controller {c.idx} ({c.key})")
        print(f"   Pad:    {c.pad_dev.name} ({c.pad_dev.path})")
        print(f"   Motion: {c.motion_dev.name if c.motion_dev else 'NOT FOUND'}")
        print(f"   Touch:  {c.touch_dev.name if c.touch_dev else 'NOT FOUND'}")
        print(f"   Lightbar: {c.led or 'not found'}")
        if C.GRAB_PAD:
            grab(c.pad_dev, f"Pad {c.idx}")
        c.send_ident()        # the receiver knows right away which pad this is (remap profile)
        events.info(E_PAD_ON, c.idx, c.key or "")
        tasks.append(asyncio.create_task(streams.stream_pad(c)))
        tasks.append(asyncio.create_task(streams.stream_battery(c)))
        for kind in ("motion", "touch"):          # separate tasks: losing them does not disconnect the pad
            dev = getattr(c, KIND_ATTR[kind])
            if dev is not None:
                setattr(c, KIND_ATTR[kind], None)
                attach(c, kind, dev)

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, OSError):
                print(f"Controller {c.idx} error: {exc!r}")
                events.error(E_ERROR, c.idx, repr(exc))
    except OSError as e:
        print(f"Controller {c.idx}: device error: {e}")
        events.error(E_ERROR, c.idx, str(e))
    finally:
        c.closing = True
        for t in tasks + list(c.extra):
            t.cancel()
        if c.extra:
            await asyncio.gather(*c.extra, return_exceptions=True)
        c.cancel_timers()
        c.stop_feedback()
        for d in c.devices():
            try:
                d.ungrab()
            except OSError:
                pass
            try:
                d.close()
            except Exception:  # best effort: the device may already be gone
                pass
        c.send_neutral()
        emit_repeat(T_BYE, c.idx)
        state.active.pop(c.key, None)
        print(f"Controller {c.idx}: disconnected (waiting for reconnect)")
        events.info(E_PAD_OFF, c.idx)


class DevWatch:
    """inotify on /dev/input: a new eventN node (pad switched on / connected) wakes the supervisor at once - without it a
    pad waited for the next scan, up to rescan_s (on average +1 s when connecting). Without inotify (another platform,
    no directory) - plain scanning every rescan_s.

    The watch is created through libc with ctypes (the standard library has no inotify). Raw inotify events are never
    parsed: any activity just sets an asyncio.Event, because the supervisor re-scans the whole directory anyway."""

    IN_ATTRIB, IN_CREATE = 0x004, 0x100
    IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000
    DEBOUNCE_S = 0.05             # the pad, motion and touchpad nodes appear together - one scan for the whole group

    def __init__(self) -> None:
        self.fd = None
        self.event = asyncio.Event()

    def start(self, path: bytes=b"/dev/input") -> bool:
        """Start watching the directory for created / changed nodes; returns False if inotify is unavailable."""
        try:
            import ctypes
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
        try:
            while os.read(self.fd, 4096):
                pass
        except OSError:
            pass
        self.event.set()

    async def wait(self, timeout) -> bool:
        """Wait for a change in /dev/input or `timeout` seconds. Returns True if there was a change.

        After a change a short debounce delay lets the whole group of nodes (pad, motion, touchpad) appear before the
        caller scans.
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
            try:
                asyncio.get_running_loop().remove_reader(self.fd)
            except RuntimeError:
                pass
            os.close(self.fd)
            self.fd = None


def next_scan_s(now):
    """How long until the next scan: rescan_s, or sooner when an incomplete pad is waiting for its other devices (SETTLE_S)."""
    wait = C.RESCAN_S
    if _first_seen:
        wait = min(wait, max(0.05, SETTLE_S - (now - min(_first_seen.values())) + 0.02))
    return wait


async def supervisor() -> None:
    """Entry point of the pad supervisor: start the /dev/input watcher, run the scan loop, close the watcher on exit."""
    watch = DevWatch()
    watch.start()
    try:
        await _supervise(watch)
    finally:
        watch.close()


async def _supervise(watch) -> None:
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
            groups = discover()
            now = time.monotonic()
            for k in [k for k in _first_seen if k not in groups]:
                del _first_seen[k]
            _no_slot.intersection_update(groups)          # the pad went away - the message appears again on the next connection
            order = sorted(groups.items(),
                           key=lambda kv: event_num(kv[1]["pad"]) if "pad" in kv[1] else 1 << 30)
            for key, g in order:
                c = state.active.get(key)
                if c is not None:             # the pad already works: attach the missing devices that appeared later
                    for kind in ("motion", "touch"):
                        if kind in g and getattr(c, KIND_ATTR[kind]) is None and not getattr(c, "closing", False):
                            dev = g.pop(kind)
                            print(f"Controller {c.idx}: {kind} device attached ({dev.path})")
                            attach(c, kind, dev)
                    close_group(g)
                    continue
                if "pad" not in g:
                    close_group(g)
                    continue
                # a newly appeared pad: the motion / touchpad devices can show up a fraction of a second later
                t0 = _first_seen.setdefault(key, now)
                if not ("motion" in g and "touch" in g) and now - t0 < SETTLE_S:
                    close_group(g)
                    continue
                idx = pick_slot(key)
                _first_seen.pop(key, None)    # without this a pad with no slot kept next_scan_s at 0.05 s - ~17 wake-ups/s
                if idx is None:                # endlessly (with full slots discover() no longer removed it)
                    close_group(g)
                    _reject(key)
                    continue
                c = Ctrl(idx, key, g["pad"], g.get("motion"), g.get("touch"))
                state.active[key] = c
                state.spawn(run_ctrl(c))
            if first and not state.active:
                print("No DualSense found yet - connect controller (bluetoothctl), waiting...")
            first = False
        changed = bool(await watch.wait(next_scan_s(time.monotonic())))
