"""Shared assertion helper for the plain-script tests: ``check(cond, msg)`` prints OK / FAIL and collects failures;
``wait_for(pred)`` polls a condition another thread makes true.

A test ends with ``sys.exit(1 if fails else 0)``; ``fails`` is the one list this module owns, so import it instead of redefining it.
"""
import time
from collections.abc import Callable

fails: list[str] = []


def check(cond: object, msg: str) -> None:
    """Print one result line (``run_tests.sh`` treats a line starting with FAIL as a failure) and remember the failure."""
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


def wait_for(pred: Callable[[], object], timeout: float = 2.5) -> bool:
    """Poll ``pred`` every 10 ms until it is true or ``timeout`` seconds pass; return its last value as a bool."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())
