"""Sender: shared state (active pads, slot assignment, background tasks)."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sender_ctrl import Ctrl

active: dict[str, Ctrl] = {}        # device key (uniq/phys) -> Ctrl
slots: dict[str, int] = {}          # device key -> slot number (stable across reconnects)
bg_tasks: set[asyncio.Task[Any]] = set()
main_task: asyncio.Task[Any] | None = None      # the main() task - cancelled on restart after an update
restart = False                     # after main() ends: os.execv (new code after an update)
saved_slots: dict[str, int] | None = None       # MAC -> slot remembered in slots_file (loaded on the first assignment)


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
