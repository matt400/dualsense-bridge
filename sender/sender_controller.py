#!/usr/bin/env python3
"""DualSense UDP sender (Linux, evdev) - protocol v2, see proto.py.

  doas python3 sender_controller.py 172.16.42.2 --key SECRET --stats
  doas python3 sender_controller.py --config sender.toml

Entry point of the sender. It reads DualSense pads through evdev, packs their state and streams it over UDP to the
receiver on the PC; feedback (rumble, lightbar, adaptive triggers) travels back the same way.

Modules: proto.py (shared with the receiver), sender_config.py, sender_state.py, sender_net.py,
         sender_ctrl.py, sender_streams.py, sender_supervisor.py, sender_control.py, sender_rumble.py (rumble),
         sender_wol.py (Wake-on-LAN: PS + button while the receiver is silent), sender_events.py (events for the PC window),
         sender_bt.py (phone Bluetooth driven from the receiver window: scan, pair, connect - busctl / bluetoothctl),
         sender_batt.py (the battery of the device the sender runs on, shown in the window).
Configuration: --config file.toml|json (keys as in DEFAULTS); CLI flags override the file.
--stats also prints the real pad report rate and traffic rate (Hz, p99/max gaps).
Switching a pad off: the "Off" button in the receiver window, or automatically after idle_off_min minutes without
input (bluetoothctl disconnect - needs bluez). Every second the sender pings the receiver and reports the real pad Hz.
Touchpad gestures (gestures, touch_start_move) and touch latency (touch_coalesce_ms) - see sender_config.DEFAULTS.
"""
from __future__ import annotations

import asyncio
import gc
import os
import signal
import socket
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Final

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))   # proto.py is shared, it lives in common/

import sender_bt
import sender_config
import sender_control as control
import sender_events as events
import sender_net as net
import sender_rt
import sender_state as state
import sender_streams as streams
import sender_supervisor as sup
import sender_update
import sender_wol as wol
from proto import E_ERROR, E_START
from sender_config import C

BROADCAST_IDX: Final = 255         # the idx of events that belong to no pad
STABLE_S: Final = 60.0             # a loop that ran this long without an error resets its error counter (full traceback, short pause)
RESTART_PAUSE_STEP_S: Final = 0.2  # pause before a restart grows by this much per consecutive error ...
RESTART_PAUSE_MAX_S: Final = 5.0   # ... up to this
LOCK_NAME: Final = b"\0dualsense-bridge-sender"    # abstract-namespace socket name (leading NUL)


async def forever(name: str, fn: Callable[[], Awaitable[None]]) -> None:
    """Supervise a long-running coroutine so that one unexpected exception cannot take the whole sender down.

    The coroutine is awaited in a loop: if it raises, the error is logged (the first one with a full traceback and
    also reported to the receiver as an event) and the coroutine is started again after a short, growing pause
    (0.2 s per consecutive error, capped at 5 s). The error counter resets once the coroutine has run for
    STABLE_S seconds without failing - otherwise rare errors (say once an hour) would, after a dozen of them, add a
    5 s pause to every restart. CancelledError is re-raised so shutdown still works; a normal return ends the loop.
    """
    errors = 0
    while True:
        t0 = time.monotonic()
        try:
            await fn()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the whole point: nothing may take a service loop down for good
            if time.monotonic() - t0 >= STABLE_S:
                errors = 0
            errors += 1
            if errors == 1:
                print(f"{name} error - restarting:\n{traceback.format_exc()}")
                events.error(E_ERROR, BROADCAST_IDX, f"{name}: {e!r}")
            else:
                print(f"{name} error #{errors} - restarting: {e!r}")
            await asyncio.sleep(min(RESTART_PAUSE_MAX_S, RESTART_PAUSE_STEP_S * errors))


async def main() -> None:
    """Run the sender until it is cancelled (SIGTERM, Ctrl+C or an update restart).

    Starts the independent service loops (socket keeper, heartbeat, pad supervisor, pinger, info/idle loop and the
    Bluetooth manager), each wrapped in forever(). On shutdown the pad tasks are cancelled first, because their
    cleanup sends a neutral state and a "bye" packet, which still needs a working socket; only then is the socket closed.
    """
    print(f"Sending to {C.HOST}:{C.PORT}" + (" (HMAC on)" if C.KEY else " (NO authentication - consider --key)"))
    task = asyncio.current_task()
    state.main_task = task
    if task is not None:
        try:
            # SIGTERM (systemd stop) cancels main(), which runs the orderly cleanup in the finally block below
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        except (NotImplementedError, RuntimeError):
            pass                   # no signal handlers on this platform / loop: Ctrl+C still works
    try:
        await asyncio.gather(*(forever(name, fn) for name, fn in (
            ("socket", net.sock_keeper), ("heartbeat", streams.heartbeat), ("supervisor", sup.supervisor),
            ("pinger", net.pinger), ("info", control.info_loop), ("bluetooth", sender_bt.bt.loop))))
    finally:
        # pads first: their cleanup sends a neutral state and "bye" - the socket must still be alive
        try:
            for t in list(state.bg_tasks):
                t.cancel()
            await asyncio.gather(*state.bg_tasks, return_exceptions=True)
            # the repeated "bye" packets (emit_repeat) are call_later timers: without a short wait they died with the
            # loop, and after the socket is closed they could not be sent anyway - on an update restart / SIGTERM that
            # left a single "bye" packet, which could be lost
            await asyncio.sleep(max(net.REPEAT_DELAYS) + 0.01)
        finally:
            net.close_sock()


_lock: socket.socket | None = None


def single_instance(name: bytes = LOCK_NAME) -> bool:
    """Allow only one sender per machine.

    Two senders (e.g. the root service plus an old user autostart) would fight over the same pads. The lock is an
    abstract-namespace Unix socket: it is shared by all users, needs no file, and disappears by itself when the
    process ends. Binding fails if another process already holds the name.

    Returns:
        True if this process got the lock.
    """
    global _lock
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.bind(name)
    except OSError:
        s.close()
        return False
    _lock = s                          # keep a reference: closing the socket would release the lock
    return True


if __name__ == "__main__":
    sender_config.init()
    if not single_instance():
        raise SystemExit("Another sender is already running (service or autostart) - stop it first.")
    net.init()
    # callbacks the network layer calls for packets arriving from the receiver
    net.on_feedback = sup.dispatch_feedback
    net.on_command = control.on_command
    net.on_triggers = sup.dispatch_triggers
    net.on_leds = sup.dispatch_leds            # the mute LED and player LEDs the game set
    net.on_haptics = sup.dispatch_haptics      # HD haptics for a Bluetooth pad (sender_haptics)
    net.on_speaker = sup.dispatch_speaker      # the game's sound on the pad's speaker (sender_speaker)
    net.on_scfg = wol.apply_scfg
    net.on_bt = sender_bt.bt.on_command        # phone Bluetooth driven from the receiver window (scan, pair, connect)
    wol.load()                         # Wake-on-LAN and events: settings from the receiver window saved at the last scfg
    sender_rt.setup(C.LOW_LATENCY, C.CPU_LATENCY_US, C.RT_PRIORITY)
    gc.collect()
    gc.freeze()   # objects created at startup are moved out of the GC's reach - shorter GC pauses
    # gen0 runs after 50k net allocations instead of 700: the short-lived state tuples do not trigger a collection every few
    # packets. The GC stays on - asyncio creates reference cycles (futures, exceptions with tracebacks)
    gc.set_threshold(50_000, 20, 100)
    sender_update.init()
    events.info(E_START, BROADCAST_IDX, sender_update.version[0] or "?")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped.")
    state.flush_writes()               # files still queued for the writer thread must reach the disk before exec / exit
    if state.restart:                  # after an update: new code in the same process (the service PID stays the same)
        print("Restarting with the updated code")
        sys.stdout.flush()
        # orig_argv keeps the interpreter options (service: python3 -u) - without -u the log would lag after an update (buffering)
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
