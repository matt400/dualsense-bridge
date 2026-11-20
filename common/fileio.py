"""Shared file helpers of the sender and the receiver: atomic settings writes, JSON state files, the config file.

  write_file / write_json   atomic (temporary file, fsync, replace), keeping the permission bits of the file they replace
  read_json_object          a JSON file whose top level must be an object (settings and state files)
  load_config               a .toml / .json config file, unknown keys reported

There is one copy, in common/ (like proto.py); the entry scripts and the tests put common/ on sys.path.
"""
import contextlib
import json
import math
import os
import sys
import threading
import tomllib
from collections.abc import Collection, Mapping
from typing import Any, Final

KEY_SETTING: Final = "key"          # the shared HMAC key: the one text setting that may be written as a number in the file
PERMISSION_BITS: Final = 0o7777    # st_mode & this = the chmod value (rwx for user / group / other + setuid etc.)


def _fits_default(key: str, value: Any, default: Any) -> bool:
    """Tell whether a config value has a type the code can use, judging by the default value it replaces.

    A number must be finite (``nan`` / ``inf`` break rates and timeouts), a text is a text (but the HMAC key may be written as a
    number: ``key = 1234``), a flag must be
    true / false (or 0 / 1: ``bool("false")`` would be true) and a list must be a list (of numbers when the default is one). A
    default of another type is not checked.
    """
    if isinstance(default, bool):
        return isinstance(value, bool) or (isinstance(value, int) and value in (0, 1))
    if isinstance(default, int | float):
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            return False
        try:
            number = (int(value) if isinstance(default, int) else float(value)) if isinstance(value, str) else float(value)
        except (ValueError, OverflowError):
            return False
        return math.isfinite(number)
    if isinstance(default, str):
        return isinstance(value, str) or (key == KEY_SETTING and isinstance(value, int | float) and not isinstance(value, bool))
    if isinstance(default, list):
        numbers = bool(default) and all(isinstance(item, int | float) for item in default)    # [1, 1, 1], [0, 0, 128]
        return isinstance(value, list) and (not numbers or all(_fits_default(key, item, 0.0) for item in value))
    return True


def load_config(path: str | None, defaults: Collection[str] | None = None) -> dict[str, Any]:
    """Load a ``.toml`` or ``.json`` configuration file and warn about unknown keys (typos) and values of the wrong type.

    A value that cannot be used (text where a number is expected, ``nan``, a number where a list is expected) is reported with
    its key and left out of the result, so the default is used: a typo in the file must not end the program with a bare
    ``ValueError`` (an autostarted receiver has no console to show it in) or silently turn into something else.

    Args:
        path: The file to load; None or empty means "no file" and yields an empty dict.
        defaults: The known keys; any other key in the file is reported (and kept). When it is a mapping, its values are the
            defaults the type of a file's value is checked against.

    Returns:
        The parsed top-level mapping.
    """
    if not path:
        return {}
    with open(path, "rb") as f:
        cfg = json.load(f) if path.lower().endswith(".json") else tomllib.load(f)
    if defaults:
        for key in list(cfg):
            if key not in defaults:
                print(f"WARNING: unknown config key {key!r} in {path} (ignored)")
            elif isinstance(defaults, Mapping) and not _fits_default(key, cfg[key], defaults[key]):
                print(f"WARNING: {key} = {cfg[key]!r} in {path} is not usable here (expected {type(defaults[key]).__name__}) "
                      f"- using the default {defaults[key]!r}")
                del cfg[key]
    return cfg


def read_json_object(path: str) -> dict[str, Any]:
    """Read a JSON file whose top level must be an object (the settings and state files of both sides).

    Raises:
        FileNotFoundError: The file does not exist (callers usually treat that as "nothing saved yet").
        OSError: The file cannot be read.
        ValueError: The file is not valid JSON, or its top level is not an object (``json.JSONDecodeError`` is a ValueError).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    return data


def write_file(path: str, content: str | bytes) -> None:
    """Write a file atomically: a temporary file, fsync, then replace.

    An interrupted write, a power loss, or a PC sleep / shutdown right after the write (master actions) will not leave an
    empty or truncated file. Without the fsync, ``os.replace`` could swap in a file whose data had not reached the disk yet.
    A failed write removes the temporary file. The new file keeps the permission bits of the one it replaces (POSIX): a
    config file that holds the HMAC key and was made 0600 must not come back from a rewrite as 0644. The temporary name
    carries the process and thread id, so two writers of the same file never share one temporary file.

    Args:
        path: The destination file.
        content: The text (written as UTF-8) or bytes to store.
    """
    temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        mode: int | None = os.stat(path).st_mode & PERMISSION_BITS
    except OSError:
        mode = None             # a new file: the default permissions
    try:
        with open(temp_path, "wb") as f:
            if mode is not None and sys.platform != "win32":
                os.fchmod(f.fileno(), mode)         # before the content is written: the file is never readable with data in it
            f.write(content.encode("utf-8") if isinstance(content, str) else content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except BaseException:       # also KeyboardInterrupt / SystemExit: no stray temp file whatever stops the write
        with contextlib.suppress(OSError):
            os.remove(temp_path)
        raise


def write_json(path: str, obj: object, *, indent: int | None = None, sort_keys: bool = False, ensure_ascii: bool = True) -> None:
    """Write ``obj`` as JSON with :func:`write_file` (atomic), with a newline at the end.

    Args:
        path: The destination file.
        obj: A JSON-serialisable object.
        indent: As in ``json.dumps``.
        sort_keys: As in ``json.dumps``.
        ensure_ascii: As in ``json.dumps``.
    """
    write_file(path, json.dumps(obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii) + "\n")
