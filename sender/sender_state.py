"""Sender: shared state (active pads, slot assignment, background tasks) and the helper for writing state files."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from proto import write_file

if TYPE_CHECKING:
    from sender_ctrl import Ctrl

active: dict[str, Ctrl] = {}        # device key (uniq/phys) -> Ctrl
slots: dict[str, int] = {}          # device key -> slot number (stable across reconnects)
bg_tasks: set[asyncio.Task[Any]] = set()
main_task: asyncio.Task[Any] | None = None      # the main() task - cancelled on restart after an update
restart = False                     # after main() ends: os.execv (new code after an update)
saved_slots: dict[str, int] | None = None       # MAC -> slot remembered in slots_file (loaded on the first assignment)
# One worker for all state files: writes run in the order they were requested, so an older version never lands after a newer one.
_file_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="state-files")


def write_json_later(path: str, obj: object, *, indent: int | None = None, sort_keys: bool = False) -> None:
    """Write ``obj`` as JSON like :func:`proto.write_json` (atomic, fsync), without stopping the event loop for the fsync.

    The fsync of a phone's flash can take tens of milliseconds, and the event loop is also what reads every pad, so inside
    the loop the file is written by a worker thread. The JSON text is made here, at call time: a later change of ``obj``
    does not reach this write. A failed write is only logged (the callers did the same). Without a running loop (tests,
    tools) the write is done at once and an ``OSError`` reaches the caller, as with ``write_json``.

    Args:
        path: The file to write.
        obj: The JSON-serialisable content.
        indent: JSON indentation (None = compact).
        sort_keys: Sort the keys of objects.
    """
    text = json.dumps(obj, indent=indent, sort_keys=sort_keys) + "\n"
    loop = running_loop()
    if loop is None:
        write_file(path, text)
        return

    def done(fut: asyncio.Future[None]) -> None:
        try:
            fut.result()
        except OSError as e:
            print(f"Cannot write {path}: {e!r}")

    loop.run_in_executor(_file_writer, write_file, path, text).add_done_callback(done)


def flush_writes() -> None:
    """Wait for the writes still queued and stop the writer; call it once, when the sender is about to end or restart.

    ``os.execv`` replaces the process at once and the worker thread with it, so a write that is still queued would be lost.
    """
    _file_writer.shutdown(wait=True)


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Start a background task and keep a strong reference to it in ``bg_tasks``.

    The asyncio loop only holds weak references to tasks, so a fire-and-forget task could be garbage-collected
    mid-run. The done-callback removes the task from the set once it finishes, so the set never grows.

    Args:
        coro: The coroutine to run.

    Returns:
        The created task.
    """
    task = asyncio.get_running_loop().create_task(coro)
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)
    return task


def running_loop() -> asyncio.AbstractEventLoop | None:
    """Return the running event loop, or None when called outside one (unit tests, the --test command)."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def call_later_if_running(delay: float, callback: Callable[..., object], *args: object) -> asyncio.TimerHandle | None:
    """Schedule callback(*args) after delay seconds on the running loop; None when there is no running loop."""
    loop = running_loop()
    return loop.call_later(delay, callback, *args) if loop is not None else None


def ctrl_by_idx(idx: int) -> Ctrl | None:
    """Find the active pad that sits in slot ``idx`` (linear scan over a handful of pads); None if there is none."""
    return next((c for c in active.values() if c.idx == idx), None)
