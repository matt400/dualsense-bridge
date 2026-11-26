"""Receiver: the touchpad as a Windows precision touchpad made by HIDMaestro (Settings -> Mouse -> "HIDMaestro driver").

The alternative to rx_mouse's own mouse (SendInput): the fingers and the click of a pad in the mouse mode go to a virtual HID
touchpad (HIDMaestro 2.4.0: HidDescriptorBuilder.Touchpad), and Windows does the rest - cursor speed, taps, scrolling and its
other gestures are those of Windows Settings -> Touchpad.

  wanted()          the settings ask for the driver and the backend does not rule HIDMaestro out
  active(i)         the touchpad of slot i exists: rx_mouse sends here instead of moving the cursor itself
  touch / button    the network thread: the new fingers / click of slot i, submitted at once
  thread(stop)      creates and removes the devices (it takes seconds - never on the network thread) and repeats a contact
                    that rests on the pad: a precision touchpad reports as long as a finger is down, the sender only changes

Whenever the device cannot be had (no HIDMaestro, an older one, a failed create) active() stays False and the receiver's own
mouse keeps working; `error` says why.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any, Final

import rx_ds4
import rx_events as ev
import rx_hm
import rx_settings
import rx_state as S
from rx_i18n import _

CONTACTS: Final = 2                    # the DualSense touchpad reports two fingers
PAD_X, PAD_Y = 1919, 942               # the touch packet's scale (the sender's TOUCH_W / TOUCH_H)
LOGICAL: Final = 4095                  # X and Y range of the descriptor
# the physical size told to Windows (its cursor speed works in millimetres): the shape of the pad's sensor, a little larger
WIDTH_MM, HEIGHT_MM = 100, 49
INDEX_BASE: Final = 32                 # HIDMaestro controller indices of the touchpads: above the gamepad slots
VID, PID = 0x1209, 0x0001              # pid.codes, the id reserved for testing / private devices
REPEAT_S: Final = 0.008                # how often a resting contact is reported again
IDLE_S: Final = 0.25                   # how often the thread looks for a change when no finger is down

Contact = tuple[int, int, int]         # (id, x, y) in the descriptor's range


class Device:
    """One virtual touchpad and what it was last told."""

    def __init__(self, ctrl: Any, send: Callable[[Any, Sequence[Contact], bool], None]) -> None:
        """Wrap a created controller; `send(ctrl, contacts, clicked)` submits one report."""
        self.ctrl, self._send = ctrl, send
        self.contacts: tuple[Contact, ...] = ()
        self.clicked = False
        self.lock = threading.Lock()
        self.closed = False

    def submit(self, contacts: tuple[Contact, ...] | None = None, clicked: bool | None = None) -> None:
        """Report a change (None = unchanged); a driver error drops this report only."""
        with self.lock:
            if self.closed:
                return
            if contacts is not None:
                self.contacts = contacts
            if clicked is not None:
                self.clicked = clicked
            try:
                self._send(self.ctrl, self.contacts, self.clicked)
            except Exception as e:  # noqa: BLE001 - a .NET error must not stop the network thread
                print(f"Touchpad driver: report failed: {e!r}")

    def repeat(self) -> bool:
        """Report a resting contact again; -> whether there is one."""
        if not (self.contacts or self.clicked):
            return False
        self.submit()
        return True

    def close(self) -> None:
        """Lift everything and remove the device."""
        self.submit((), False)
        with self.lock:
            self.closed = True
            try:
                self.ctrl.Dispose()
            except Exception as e:  # noqa: BLE001 - the device may already be gone
                print(f"Touchpad driver: remove failed: {e!r}")


devices: dict[int, Device] = {}        # slot -> its touchpad (replaced entries only by the thread)
error = ""                             # why the last create failed ("" = no failure); cleared by a settings change
_wake = threading.Event()


def wanted() -> bool:
    """The settings ask for the driver's touchpad and the backend choice allows HIDMaestro."""
    return rx_settings.cur.mouse.engine == "driver" and rx_settings.cur.vpad.backend != "vigem"


def active(i: int) -> bool:
    """Whether the touchpad of slot i goes through the driver now."""
    return i in devices and wanted()


def contacts_of(touch: Sequence[Sequence[int]]) -> tuple[Contact, ...]:
    """The fingers that are down from a touch packet ((on, id, x, y) twice), scaled to the descriptor, ids unique."""
    out: dict[int, Contact] = {}
    for on, fid, x, y in touch[:CONTACTS]:
        if on:
            out[fid] = (fid, min(x, PAD_X) * LOGICAL // PAD_X, min(y, PAD_Y) * LOGICAL // PAD_Y)
    return tuple(out.values())


def touch(i: int, fingers: Sequence[Sequence[int]]) -> None:
    """New finger positions of slot i."""
    d = devices.get(i)
    if d is not None:
        d.submit(contacts=contacts_of(fingers))
        _wake.set()


def button(i: int, down: bool) -> None:
    """The click of slot i (Windows itself tells a two-finger click apart)."""
    d = devices.get(i)
    if d is not None:
        d.submit(clicked=down)
        _wake.set()


def release(i: int) -> None:
    """Lift the fingers and the click of slot i (mode change, capture, pad gone)."""
    d = devices.get(i)
    if d is not None and (d.contacts or d.clicked):
        d.submit((), False)


def create(backend: Any, i: int) -> Device:
    """Create the virtual precision touchpad of slot i with a loaded HIDMaestro backend.

    Raises:
        rx_hm.HMError: This HIDMaestro has no touch support (older than 2.4.0) or the create failed.
    """
    return Device(*rx_hm.create_touchpad(backend, INDEX_BASE + i, VID, PID, CONTACTS, WIDTH_MM, HEIGHT_MM,
                                         f"dualsense-bridge:touchpad:{i}"))


def _desired() -> set[int]:
    """The slots that should have a touchpad now."""
    if not wanted():
        return set()
    return {i for i in range(len(S.mouse_mode)) if S.mouse_mode[i] and S.alive[i]}


def sync(release_own: Callable[[int], None]) -> None:
    """Remove the touchpads nobody needs and create the missing ones (one failure stops further tries until a settings change).

    `release_own(i)` lets go of the receiver's own mouse button before the driver takes slot i over.
    """
    global error
    need = _desired()
    for i in [i for i in devices if i not in need]:
        devices.pop(i).close()
    if error:
        return
    for i in sorted(need - devices.keys()):
        backend = rx_ds4.hm_backend()
        if backend is None:
            return                       # the reason is in rx_ds4.hm_note
        try:
            d = create(backend, i)
        except Exception as e:  # noqa: BLE001 - any HIDMaestro / .NET failure: the receiver's own mouse stays
            error = rx_hm.describe_error(e)
            ev.warn(_("Touchpad ze sterownika niedostępny - działa obsługa receivera: {why}").format(why=error))
            return
        release_own(i)
        devices[i] = d
        print(f"Controller {i}: touchpad -> HIDMaestro precision touchpad")


def close_all() -> None:
    """Remove every touchpad (the receiver stops)."""
    for i in list(devices):
        devices.pop(i).close()


def thread(stop: threading.Event, release_own: Callable[[int], None]) -> None:
    """Keep the devices in step with the settings and the pads, and repeat resting contacts."""
    try:
        while not stop.is_set():
            sync(release_own)
            resting = [d.repeat() for d in list(devices.values())]
            _wake.wait(REPEAT_S if any(resting) else IDLE_S)
            _wake.clear()
    finally:
        close_all()


def _settings_changed(old: rx_settings.Settings, new: rx_settings.Settings) -> None:
    """Another engine or backend: try again after a failure and apply at once."""
    global error
    if old.mouse.engine != new.mouse.engine or old.vpad != new.vpad:
        error = ""
        _wake.set()


rx_settings.on_change(_settings_changed)
