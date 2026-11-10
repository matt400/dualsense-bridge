"""Import every receiver module (and report all failures at once): ``python tools/import_smoke.py``.

The Linux tests run the receiver against stubs for ctypes / pythonnet / COM (tests/rxenv.py), so a wrong DLL name, a missing
attribute of a real package or a broken import order only shows on Windows. This script has no stubs: run on Windows
(the ``windows-imports`` CI job) it fails on whatever really does not import. The entry point is only imported (its main() sits behind ``__name__``), never run.
"""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """Import every module under receiver/ and common/; return the number of failures."""
    sys.path[:0] = [str(ROOT / "common"), str(ROOT / "receiver")]
    names = sorted(p.stem for d in ("receiver", "common") for p in (ROOT / d).glob("*.py"))
    failed = 0
    for name in names:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - every failure is reported, the loop goes on
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        else:
            print(f"OK    {name}")
    print(f"{len(names) - failed}/{len(names)} modules import")
    return failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
