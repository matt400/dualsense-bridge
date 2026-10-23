"""Sender: migrate sender.toml after default values change - only OLD default values are changed, custom ones stay.

Runs by itself at sender startup (sender_config.init, before the file is loaded), so after an update with the button
in the PC window nothing has to be done by hand. Manually:

  python3 sender_migrate.py [sender.toml]      (no argument: sender.toml next to the script)

The file is rewritten line by line (comments and layout are kept), a copy sender.toml.bak is made first; the file
owner is not changed (the service runs as root).
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from typing import Final

from proto import write_file

Change = tuple[str, str, str, str]      # (key, old text, new text, reason)

# key -> (old default values, new default value, reason)
CHANGES: Final[dict[str, tuple[tuple[float, ...], float, str]]] = {
    "pad_min_dt": ((0.004,), 0.001, "limiter 4 ms -> 1 ms (szybsze gałki)"),
    "motion_min_dt": ((0.004,), 0.001, "limiter ruchu 4 ms -> 1 ms (szybsze gyro)"),
    "touch_coalesce_ms": ((5, 50), 2, "dotyk co 2 ms"),
}
FLOAT_EPSILON: Final = 1e-9       # config numbers are compared as floats: 0.004 must match 0.004000
_LINE = re.compile(r"^(?P<lead>\s*)(?P<key>[A-Za-z_]+)(?P<eq>\s*=\s*)(?P<val>[-+0-9.eE]+)(?P<rest>\s*(#.*)?)$")


def _num(text: str) -> float | None:
    """Parse a number from config text; None if it is not numeric."""
    try:
        return float(text)
    except ValueError:
        return None


def _fmt(v: float) -> str:
    """Format a default value for the file: integers without a decimal point, floats via repr."""
    return str(int(v)) if float(v).is_integer() and not isinstance(v, float) else repr(v)


def migrate_text(text: str) -> tuple[str, list[Change]]:
    """Return the migrated text and the list of changes made, ``[(key, old, new, reason)]``.

    Walks the file line by line and matches "key = number # comment" lines with a regex that captures the leading
    whitespace, separators and trailing comment separately, so only the number is replaced and everything else
    (including the original line ending) is preserved. Only active (not commented-out) lines that still hold an
    OLD default value are touched; anything the user changed to another value is left alone.
    """
    out: list[str] = []
    done: list[Change] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = _LINE.match(body)
        if m and m["key"] in CHANGES:
            old_defaults, new, why = CHANGES[m["key"]]
            v = _num(m["val"])
            if v is not None and any(abs(v - d) < FLOAT_EPSILON for d in old_defaults):
                line = f"{m['lead']}{m['key']}{m['eq']}{_fmt(new)}{m['rest']}" + line[len(body):]
                done.append((m["key"], m["val"], _fmt(new), why))
        out.append(line)
    return "".join(out), done


def migrate(path: str, log: Callable[[str], None] = print) -> list[Change]:
    """Migrate the file in place and return the list of changes (empty = nothing to do, no file, or a write error - logged).

    Order of operations: read the file, compute the new text, save the old text as <path>.bak, write the new text
    atomically (proto.write_file), then restore the original owner - the service runs as root and must not take over
    a file that belongs to the user. If writing fails, the old defaults simply stay in use.
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    except OSError as e:
        log(f"Config migration: cannot read {path}: {e}")
        return []
    new, done = migrate_text(text)
    if not done:
        return []
    try:
        st = os.stat(path)
        with open(path + ".bak", "w", encoding="utf-8") as f:
            f.write(text)
        write_file(path, new)
        if hasattr(os, "chown"):
            for p in (path, path + ".bak"):
                try:
                    os.chown(p, st.st_uid, st.st_gid)       # root (the service) does not take over the user's file
                except OSError:
                    continue      # a file system without owners (FAT, some mounts): the owner does not matter there
    except OSError as e:
        log(f"Config migration: cannot write {path}: {e} - old defaults stay in use")
        return []
    for key, old, val, why in done:
        log(f"Config migration: {os.path.basename(path)}: {key} {old} -> {val} ({why}); backup {os.path.basename(path)}.bak")
    return done


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "sender.toml")
    if not os.path.exists(target):
        print(f"{target}: brak pliku - nic do migracji")
    elif not migrate(target):
        print(f"{target}: nic do zmiany (nowe wartości domyślne albo własne ustawienia)")
