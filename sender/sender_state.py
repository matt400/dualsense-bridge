"""Sender: shared state (active pads, slot assignment, background tasks)."""
import asyncio

active = {}      # device key (uniq/phys) -> Ctrl
slots = {}       # device key -> slot number (stable across reconnects)
bg_tasks = set()
main_task = None     # the main() task - cancelled on restart after an update
restart = False      # after main() ends: os.execv (new code after an update)
saved_slots = None   # MAC -> slot remembered in slots_file (loaded on the first assignment)


def spawn(coro):
    """Start a background task and keep a strong reference to it in bg_tasks.

    The asyncio loop only holds weak references to tasks, so a fire-and-forget task could be garbage-collected
    mid-run. The done-callback removes the task from the set once it finishes, so the set never grows.
    """
    t = asyncio.get_running_loop().create_task(coro)
    bg_tasks.add(t)
    t.add_done_callback(bg_tasks.discard)
    return t


def ctrl_by_idx(idx):
    """Find the active Ctrl that sits in slot idx (linear scan over a handful of pads); None if there is none."""
    return next((c for c in active.values() if c.idx == idx), None)
