#!/usr/bin/env python3
"""DualSense UDP sender (Linux, evdev) - protocol v2, see proto.py.

  doas python3 sender_controller.py 172.16.42.2 --key SECRET --stats
  doas python3 sender_controller.py --config sender.toml

Entry point of the sender. It reads DualSense pads through evdev, packs their state and streams it over UDP to the
receiver on the PC; feedback (rumble, lightbar, adaptive triggers) travels back the same way.

Modules: proto.py (shared with the receiver), sender_config.py, sender_state.py, sender_net.py,
         sender_ctrl.py, sender_streams.py, sender_supervisor.py, sender_control.py, sender_rumble.py (rumble),
         sender_wol.py (Wake-on-LAN: PS + button while the receiver is silent), sender_events.py (events for the PC window),
         sender_bt.py (phone Bluetooth driven from the receiver window: scan, pair, connect - busctl / bluetoothctl).
Configuration: --config file.toml|json (keys as in DEFAULTS); CLI flags override the file.
--stats also prints the real pad report rate and traffic rate (Hz, p99/max gaps).
Switching a pad off: the "Off" button in the receiver window, or automatically after idle_off_min minutes without
input (bluetoothctl disconnect - needs bluez). Every second the sender pings the receiver and reports the real pad Hz.
Touchpad gestures (gestures, touch_start_move) and touch latency (touch_coalesce_ms) - see sender_config.DEFAULTS.
"""
import asyncio
import gc
import os
import signal
import socket
import sys
import time
import traceback

import sender_config
import sender_bt
import sender_control as control
import sender_events as events
import sender_net as net
import sender_rt
import sender_streams as streams
import sender_state as state
import sender_supervisor as sup
import sender_update
import sender_wol as wol
from proto import E_ERROR, E_START
from sender_config import C


STABLE_S = 60.0       # a loop that ran this long without an error resets its error counter (full traceback, short pause)


async def forever(name, fn):
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
        except Exception as e:
            if time.monotonic() - t0 >= STABLE_S:
                errors = 0
            errors += 1
            if errors == 1:
                print(f"{name} error - restarting:\n{traceback.format_exc()}")
                events.error(E_ERROR, 255, f"{name}: {e!r}")
            else:
                print(f"{name} error #{errors} - restarting: {e!r}")
            await asyncio.sleep(min(5.0, 0.2 * errors))


async def main():
    """Run the sender until it is cancelled (SIGTERM, Ctrl+C or an update restart).

    Starts the independent service loops (socket keeper, heartbeat, pad supervisor, pinger, info/idle loop and the
    Bluetooth manager), each wrapped in forever(). On shutdown the pad tasks are cancelled first, because their
    cleanup sends a neutral state and a "bye" packet, which still needs a working socket; only then is the socket closed.
    """
    print(f"Sending to {C.HOST}:{C.PORT}" + (" (HMAC on)" if C.KEY else " (NO authentication - consider --key)"))
    state.main_task = asyncio.current_task()
    try:
        # SIGTERM (systemd stop) cancels main(), which runs the orderly cleanup in the finally block below
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    except (NotImplementedError, RuntimeError):
        pass
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


LOCK_NAME = b"\0dualsense-bridge-sender"
_lock = None


def single_instance(name=LOCK_NAME):
    """Allow only one sender per machine; returns True if this process got the lock.

    Two senders (e.g. the root service plus an old user autostart) would fight over the same pads. The lock is an
    abstract-namespace Unix socket: it is shared by all users, needs no file, and disappears by itself when the
    process ends. Binding fails if another process already holds the name.
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
    net.on_scfg = wol.apply_scfg
    net.on_bt = sender_bt.bt.on_command        # phone Bluetooth driven from the receiver window (scan, pair, connect)
    wol.load()                         # Wake-on-LAN and events: settings from the receiver window saved at the last scfg
    sender_rt.setup(C.LOW_LATENCY, C.CPU_LATENCY_US, C.RT_PRIORITY)
    gc.collect()
    gc.freeze()   # objects created at startup are moved out of the GC's reach - shorter GC pauses
    sender_update.init()
    events.info(E_START, 255, sender_update.version[0] or "?")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped.")
    if state.restart:                  # after an update: new code in the same process (the service PID stays the same)
        print("Restarting with the updated code")
        sys.stdout.flush()
        # orig_argv keeps the interpreter options (service: python3 -u) - without -u the log would lag after an update (buffering)
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
