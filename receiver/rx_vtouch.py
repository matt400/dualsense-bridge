"""Receiver: the touchpad mouse through the driver - a virtual Windows precision touchpad (HIDMaestro 2.4+).

While HIDMaestro is available, a slot in mouse mode hands its two fingers and the physical click to a virtual precision
touchpad, and Windows does the cursor, taps, scrolling and zoom itself. Without it (ViGEmBus fallback, an older
HIDMaestro, a failed create) rx_mouse keeps doing all of that with SendInput.

  Link (one per slot)
    touch() / click()  network thread: remember the state, decide which path drives Windows, queue a frame
    release()          any thread: lift everything without a tap (mode change, pad gone, test capture)
  worker thread        submits the queued frames and repeats the last one while a finger is down

The device is created on the first touch of a slot in mouse mode, in its own thread (PnP takes seconds); the old path
drives the cursor until it is ready. The switch happens only when a new contact starts, so one gesture never uses both
paths. A precision touchpad has to keep reporting while a finger rests on it, while the sender only sends changes - hence
the repeat (REPEAT_S).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any, Final, NamedTuple, TypeAlias

import rx_state as S

REPEAT_S: Final = 0.008          # a precision touchpad reports at >= 125 Hz while a finger is down
IDLE_WAIT_S: Final = 0.5         # the worker's longest sleep with nothing down (a new frame wakes it)
RETRY_S: Final = 600.0           # after a failed create or submit: the old mouse for this long (or until the settings change)
MAX_QUEUE: Final = 32            # frames waiting for the worker; only a stalled driver gets there


class Contact(NamedTuple):
    """One finger on the virtual touchpad, in the pad's own touch scale."""

    id: int
    x: int
    y: int
    palm: bool = False           # lifted as "not a finger": Windows makes no tap of it


Frame: TypeAlias = tuple[tuple[Contact, ...], bool]      # (contacts down, the click)
Factory: TypeAlias = Callable[[int], Any]                # slot -> a device with submit(contacts, click) and close()
NOTHING: Final[Frame] = ((), False)


class Link:
    """The virtual touchpad of one slot: its device, the live frame and the queue to the worker.

    Every field is read and written under `lock`; the driver is only called outside it.
    """

    def __init__(self) -> None:
        """An idle link: no device, nothing down."""
        self.lock = threading.Lock()
        self.dev: Any = None
        self.creating = False
        self.live = False             # the virtual touchpad (not rx_mouse) drives Windows for this slot
        self.muted = False            # after release(): ignore the touchpad until every finger is up
        self.retry_at = 0.0
        self.contacts: tuple[Contact, ...] = ()
        self.click = False
        self.queue: deque[Frame] = deque(maxlen=MAX_QUEUE)
        self.sent: Frame = NOTHING    # what the device got last


links: list[Link] = []
wake = threading.Event()
factory: Factory | None = None


def _no() -> bool:
    return False


allowed: Callable[[], bool] = _no     # the settings let the driver be used
loaded: Callable[[], bool] = _no      # the driver is loaded in this process


def init(n: int, make: Factory | None = None, is_allowed: Callable[[], bool] | None = None,
         is_loaded: Callable[[], bool] | None = None) -> None:
    """Reset the links of `n` slots.

    Args:
        n: The number of slots.
        make: Creates the device of a slot, None when the driver is not usable; without it every slot stays on the old mouse.
        is_allowed: Whether the settings allow the driver (default: whenever there is a factory).
        is_loaded: Whether the driver is already loaded (default: as is_allowed).
    """
    global factory, allowed, loaded
    factory = make
    allowed = is_allowed or (lambda: factory is not None)
    loaded = is_loaded or allowed
    links[:] = [Link() for _ in range(n)]


def live(i: int) -> bool:
    """Whether the virtual touchpad drives Windows for slot i (then rx_mouse does nothing for it)."""
    return i < len(links) and links[i].live


def in_use() -> bool:
    """Whether the window should treat the touchpad mouse as the driver's (hide the old mouse settings and shortcuts)."""
    if any(link.live for link in links):
        return True
    now = time.monotonic()
    return factory is not None and allowed() and loaded() and not any(link.retry_at > now for link in links)


def _push(link: Link) -> None:
    """Queue the live frame (call under link.lock)."""
    link.queue.append((link.contacts, link.click))
    wake.set()


def _start_create(i: int, link: Link) -> None:
    """Create the device of slot i in the background, once (call under link.lock)."""
    if link.creating or link.dev is not None or time.monotonic() < link.retry_at or factory is None or not allowed():
        return
    link.creating = True
    threading.Thread(target=_create, args=(i, link), name=f"touchpad{i}", daemon=True).start()


def _create(i: int, link: Link) -> None:
    """The creation thread: any driver / .NET failure leaves the slot on the old mouse."""
    dev, error = None, None
    try:
        if factory is not None:
            dev = factory(i)
    except Exception as e:  # noqa: BLE001 - whatever HIDMaestro throws: the old mouse keeps working
        error = e
    with link.lock:
        link.creating = False
        stale = factory is None or i >= len(links) or links[i] is not link      # shut down or reset meanwhile
        if dev is not None and not stale:
            link.dev = dev
        if dev is None:
            link.retry_at = time.monotonic() + RETRY_S
    if stale and dev is not None:
        _close(dev)
    elif error is not None:
        _failed(i, error)
    elif dev is not None:
        print(f"Controller {i}: virtual touchpad ready")


def _failed(i: int, error: Exception) -> None:
    """Report a failure to the console and the window log."""
    import rx_events as ev
    from rx_i18n import _
    print(f"Controller {i}: virtual touchpad failed: {error!r}")
    ev.warn(_("{pad}: wirtualny touchpad nie działa, mysz po staremu ({err})").format(pad=ev.pad_name(i), err=error))


def touch(i: int, fingers: Sequence[tuple[int, int, int, int]], mouse: bool = True) -> None:
    """Network thread: the fingers (active, id, x, y) of a slot.

    Args:
        i: The slot.
        fingers: Both fingers of the touch packet.
        mouse: Whether they drive the mouse now. False (DS4 touch, the Test view's capture) only remembers that fingers
            are down, so a contact that is already there never turns into a new one halfway through.
    """
    if i >= len(links):
        return
    link = links[i]
    contacts: dict[int, Contact] = {}
    for on, finger_id, x, y in fingers:
        if on:
            contacts[finger_id] = Contact(finger_id, x, y)
    with link.lock:
        began = bool(contacts) and not link.contacts
        link.contacts = tuple(contacts.values())
        if not mouse:
            link.muted = bool(contacts)
            return
        if link.muted:
            link.muted = bool(contacts)
            return
        if began:
            _start_create(i, link)
            if link.dev is not None and not link.live and allowed():
                link.live = True
        if link.live:
            _push(link)


def click(i: int, down: bool) -> bool:
    """Network thread: the physical click of a slot in mouse mode.

    Returns:
        True when the virtual touchpad took it (rx_mouse must not click).
    """
    if not live(i):
        return False
    link = links[i]
    with link.lock:
        if not link.live:
            return False
        if not link.muted:
            link.click = down
            _push(link)
    return True


def release(i: int) -> None:
    """Lift everything of slot i without a tap or a click, and wait for all fingers to leave the touchpad."""
    if i >= len(links):
        return
    link = links[i]
    with link.lock:
        link.muted = bool(link.contacts)
        link.click = False
        if link.live:
            down, held = link.queue[-1] if link.queue else link.sent
            if down:
                link.queue.append((tuple(c._replace(palm=True) for c in down), False))
            if down or held:
                link.queue.append(NOTHING)
                wake.set()


def forget(i: int) -> None:
    """The pad left the slot: lift everything and forget its fingers (the device stays for the next pad)."""
    release(i)
    if i < len(links):
        with links[i].lock:
            links[i].contacts, links[i].muted = (), False


def step(i: int) -> bool:
    """Worker: send what slot i has waiting, or repeat the resting frame.

    Returns:
        True while something is down (the worker keeps the repeat rate).
    """
    link = links[i]
    with link.lock:
        dev = link.dev
        if dev is None or not link.live:
            return False
        frames = list(link.queue) or ([link.sent] if link.sent[0] or link.sent[1] else [])
        link.queue.clear()
    last = NOTHING
    try:
        for frame in frames:
            dev.submit(*frame)
            last = frame
    except Exception as e:  # noqa: BLE001 - the device is gone or refused the report: back to the old mouse
        if link.dev is dev:      # not a device another thread has just removed
            _drop(link, RETRY_S)
            _failed(i, e)
        return False
    with link.lock:
        if link.dev is dev and frames:
            link.sent = last
    return bool(last[0] or last[1])


def _close(dev: Any) -> None:
    """Remove a device (Dispose lifts whatever is still down)."""
    try:
        dev.close()
    except Exception as e:  # noqa: BLE001 - best effort: the driver removes it with the process anyway
        print(f"Virtual touchpad close failed: {e!r}")


def _drop(link: Link, retry_s: float) -> None:
    """Detach and remove the device of a link."""
    with link.lock:
        dev, link.dev = link.dev, None
        link.live, link.click, link.sent = False, False, NOTHING
        link.queue.clear()
        link.retry_at = time.monotonic() + retry_s
    if dev is not None:
        _close(dev)


def reset_devices() -> None:
    """The virtual pad settings changed (e.g. ViGEmBus chosen): remove the devices; the next touch decides again."""
    for link in links:
        _drop(link, 0.0)


def shutdown() -> None:
    """Receiver exit: remove every device and create no more."""
    global factory
    factory = None
    reset_devices()


def thread() -> None:
    """The worker: submit the queued frames, repeat resting contacts every REPEAT_S."""
    while not S.stop.is_set():
        wake.clear()
        busy = [step(i) for i in range(len(links))]
        wake.wait(REPEAT_S if any(busy) else IDLE_WAIT_S)
