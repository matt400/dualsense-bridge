"""Shared assertion helper for the plain-script tests: ``check(cond, msg)`` prints OK / FAIL and collects failures.

A test ends with ``sys.exit(1 if fails else 0)``; ``fails`` is the one list this module owns, so import it instead of redefining it.
"""
fails: list[str] = []


def check(cond: object, msg: str) -> None:
    """Print one result line (``run_tests.sh`` treats a line starting with FAIL as a failure) and remember the failure."""
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)
