"""Receiver: receiver_controller._shutdown stops things in an order that leaves no stuck state behind.
Every step is replaced by a recorder, so the test checks the real call order, not the source text.
    python3 tests/test_shutdown_order.py receiver"""
import os, sys, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import receiver_controller as RC
import rx_console, rx_dsu, rx_haptics, rx_winapi
import rx_feedback as F
import rx_net as N
import rx_state as S

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

log: list[str] = []
rec = lambda name: (lambda *a, **k: log.append(name))


class Thread:
    """A worker thread that only records its join."""
    def __init__(self, name):
        self.name = name

    def join(self, timeout=None):
        log.append(f"{self.name}.join")


class Timer:
    """The restart guard: records start / cancel instead of running."""
    def __init__(self, interval, fn, args):
        self.daemon = False

    def start(self):
        log.append("guard.start")

    def cancel(self):
        log.append("guard.cancel")


F.release_triggers = rec("release_triggers")
rx_haptics.stop_all = rec("haptics.stop_all")
N.clear_slot = rec("clear_slot")
N.close = rec("net.close")
rx_console.is_on = lambda: False
rx_console.close = rec("console.close")
rx_dsu.close = rec("dsu.close")
rx_winapi.release_single_instance = rec("release_single_instance")
rx_winapi.end_timer = rec("end_timer")
RC._restart_now = rec("restart_now")
RC.threading = types.SimpleNamespace(Timer=Timer, Thread=Thread)
C.log_handle = None


def shutdown(restart):
    log.clear()
    S.restart = restart
    S.stop.clear()
    RC._shutdown([Thread("slot0"), Thread("slot1")], Thread("fb"))
    return log[:]


def before(order, *names):
    """Each name is in the log, in this order."""
    return all(n in order for n in names) and [order.index(n) for n in names] == sorted(order.index(n) for n in names)


order = shutdown(None)
check(S.stop.is_set(), "the stop flag is set")
check(before(order, "fb.join", "release_triggers", "haptics.stop_all", "net.close"),
      f"triggers released after the feedback thread stops, haptics ended, both before the socket closes: {order}")
check(before(order, "clear_slot", "slot1.join", "console.close") and order.count("clear_slot") == C.NUM_PADS,
      "every slot cleared and its thread joined before the screens are restored")
check(before(order, "net.close", "dsu.close", "release_single_instance", "end_timer"), "sockets, instance lock, timer last")
check("guard.start" not in order and "restart_now" not in order, "a plain exit has no restart guard and no new process")

order = shutdown("normal")
check(order[0] == "guard.start", f"restart: the guard starts before any cleanup step: {order[:2]}")
check(before(order, "end_timer", "guard.cancel", "restart_now"),
      "restart: the guard is cancelled after the cleanup, before the new process starts")
S.restart = None

sys.exit(1 if fails else 0)
