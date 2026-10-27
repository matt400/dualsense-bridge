"""Receiver: Windows audio devices (pycaw): list the active outputs and set the default one.

pycaw / comtypes are imported lazily, only when used - without them the receiver still works, just without audio
switching. Every function initialises COM in the calling thread itself, so they can be used from any thread.

A virtual DualSense created with HIDMaestro's composite profile (game haptics, rx_haptics) brings a Windows audio endpoint
of its own ("Speakers (Wireless Controller)"). It is not an output anyone should pick: list_outputs() leaves such endpoints
out (so the console mode and the settings window never see them, and a partial name match cannot become ambiguous), and
default_output_ids() / set_default_id() let rx_haptics put the previous default back when Windows switches to it.
"""
from __future__ import annotations

import contextlib
import time
from collections.abc import Iterable, Iterator
from typing import Final

POLL_S: Final = 0.5           # how often to look for a device that has not appeared yet
ROLES: Final = ("eConsole", "eMultimedia", "eCommunications")   # same as "Set as default" in the Windows sound settings
CONTROLLER_ENDPOINT: Final = "wireless controller"     # the USB product name of a DualSense: "Speakers (Wireless Controller)"


@contextlib.contextmanager
def _com() -> Iterator[None]:
    """Context manager that initialises COM for the current thread and uninitialises it afterwards (only if we did the init)."""
    import comtypes
    try:
        comtypes.CoInitialize()
        owned = True
    except OSError:          # the thread already has COM in another mode (e.g. a Qt thread) - use the existing one
        owned = False
    try:
        yield
    finally:
        if owned:
            comtypes.CoUninitialize()


def is_controller_endpoint(name: str) -> bool:
    """Tell whether an output's name is the audio endpoint of a (virtual) DualSense, e.g. "Speakers (Wireless Controller)"."""
    return CONTROLLER_ENDPOINT in name.casefold()


def list_outputs(controllers: bool = False) -> list[tuple[str, str]]:
    """Return the active output devices as [(id, name)], in system order.

    Args:
        controllers: Also list the audio endpoints of gamepads (left out by default - nobody should pick them as the sound
            output of the PC, and rx_haptics asks for them only to recognise a default that Windows switched by itself).
    """
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.utils import AudioUtilities
    with _com():
        devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        out = [(d.id, d.FriendlyName) for d in devices if d.FriendlyName and (controllers or not is_controller_endpoint(d.FriendlyName))]
        # release the COM objects BEFORE CoUninitialize: otherwise the list would live until the function returns, i.e. past
        # the exit from _com(), and a Release on an uninitialised COM (console-mode thread, a thread with no other COM
        # users) could crash the whole receiver
        del devices
    return out


def find(name: str, devices: Iterable[tuple[str, str]]) -> str | None:
    """Find a device id by name: an exact case-insensitive match first, otherwise the only device whose name contains the text."""
    want = name.casefold()
    exact = [dev_id for dev_id, n in devices if n.casefold() == want]
    if exact:
        return exact[0]
    partial = [dev_id for dev_id, n in devices if want in n.casefold()]
    return partial[0] if len(partial) == 1 else None


def set_default(name: str, wait_s: float = 6.0) -> None:
    """Set the default audio output (all roles) by device name.

    Polls the device list for up to wait_s until the device appears - a TV's HDMI audio only becomes active after its
    screen is switched on. Device not found -> LookupError.
    """
    deadline = time.monotonic() + wait_s
    while (dev_id := find(name, list_outputs())) is None:
        if time.monotonic() >= deadline:
            raise LookupError(f"audio output {name!r} not found")
        time.sleep(POLL_S)
    set_default_id(dev_id)


def default_output_ids() -> dict[str, str]:
    """Return the default output device id of each role in ROLES ({role: id}); a role with no default is left out."""
    from pycaw.constants import EDataFlow, ERole
    from pycaw.utils import AudioUtilities
    out: dict[str, str] = {}
    with _com():
        enumerator = AudioUtilities.GetDeviceEnumerator()
        for role in ROLES:
            try:
                dev = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole[role].value)
                out[role] = str(dev.GetId())
            except Exception:  # noqa: BLE001, S112 - comtypes.COMError (no default output for this role): the role is simply absent
                continue
            del dev
        del enumerator      # COM objects go before CoUninitialize (see list_outputs)
    return out


def set_default_id(dev_id: str, roles: Iterable[str] = ROLES) -> None:
    """Set the default audio output by device id for the given roles (all of them by default)."""
    from pycaw.constants import ERole
    from pycaw.utils import AudioUtilities
    with _com():
        AudioUtilities.SetDefaultDevice(dev_id, [ERole[r] for r in roles])
