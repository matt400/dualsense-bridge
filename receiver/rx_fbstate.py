"""Receiver: the shared state and constants of the feedback to the pad (see rx_feedback).

The per-slot lists (what the game set, what was sent last, active confirmation pulses), the timing constants, the pulse
table and the type aliases. Lists are filled in place by rx_feedback.init(), so every module holding a reference sees the
same objects. No logic and no imports from the rest of the receiver: rx_feedback and rx_fbtrig both build on it.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any, Final, TypeAlias

FeedbackState: TypeAlias = dict[str, Any]           # what the game set for one slot (see init())
Trig: TypeAlias = tuple[bytes | None, bytes | None]   # the game's trigger effects (L2, R2); None = not set
Pulse: TypeAlias = tuple[float, int, int, tuple[int, int, int] | None]   # (end, small, large, colour | None)
SendFeedback: TypeAlias = Callable[[int, tuple[int, int, int, int, int]], None]
SendTriggers: TypeAlias = Callable[[int, Sequence[Any], int], None]
SendLeds: TypeAlias = Callable[[int, tuple[int, int, int], int], None]
Leds: TypeAlias = tuple[int | None, int | None]       # the game's (mute button LED, player LEDs); None = not set


FX_SENDER: Final = 8          # from this version the sender knows trig2 (with the CAP_TRIG_FX bit in the version packet)
RAW_SENDER: Final = 11        # from this version the sender knows trig3 - trigger effects from the game (the CAP_TRIG_RAW bit)
NO_GAME_TRIG: Final[Trig] = (None, None)    # the game has set no trigger effects (L2, R2)
NO_LEDS: Final[Leds] = (None, None)         # the game has set neither the mute LED nor the player LEDs
TRIG_OFF_MODES: Final = (0x00, 0x05)  # first byte of a trigger effect: none / Off - the game released the trigger

TRIG_REFRESH_S: Final = 2.0   # trigger effects are refreshed this often (a pad after reconnecting / a lost packet)
TRIG_REPEAT: Final = 3        # a change of effects is sent this many times (UDP)
GAME_RUMBLE_STALE_S: Final = 1.0   # game rumble with no new motor report for longer than this -> 0. Games send reports every frame while
                            # rumbling; the last "motors 0" (entering a menu, the end of a drive) can get lost
                            # (HIDMaestro overwrites old packets) and the pad would vibrate forever. Not longer:
                            # with 3 s the pad keeps twitching in the Forza Horizon menu after leaving a drive
GAME_RUMBLE_GAP_S: Final = 0.25    # the game still sends reports (colour, triggers) but no motors for longer than this since the last
                            # motor report -> it stopped driving the rumble (a menu) -> 0, without waiting for STALE
RUMBLE_REFRESH_S: Final = 0.5         # while rumbling: refreshes the timeout on the sender
IDLE_REFRESH_S: Final = 5.0           # at rest: a lost packet will not leave a wrong lightbar colour
PCT_MAX: Final = 100                  # a battery level above this means "unknown"

# confirmations on the pad: (time s, small rumble, large rumble, lightbar colour | None = colour unchanged)
PULSES: Final[dict[str, Pulse]] = {
    "mouse": (0.18, 0, 150, (0, 90, 255)),        # touchpad -> mouse
    "ds4": (0.18, 0, 150, (255, 255, 255)),       # touchpad -> DS4 touch
    "profile": (0.25, 0, 190, (0, 220, 90)),      # profile change (game / manual)
    "default": (0.25, 0, 190, (255, 170, 0)),     # back to the default profile
    "up": (0.07, 160, 0, None),                   # sensitivity up
    "down": (0.07, 80, 0, None),                  # sensitivity down
    "action": (0.10, 130, 0, None),               # screenshot, Game Bar etc.
    "auto_profile": (0.30, 0, 0, (0, 220, 90)),   # a game profile switched on by itself (alt-tab) - colour only, no rumble
    "auto_default": (0.30, 0, 0, (255, 170, 0)),
    "master": (0.60, 0, 200, (255, 200, 0)),      # this pad became the master - a golden flash
    "hold": (0.06, 90, 0, None),                  # a master action: keep holding
    "denied": (0.25, 0, 0, (255, 0, 0)),          # only the master may - a red blink
    "test": (0.6, 200, 255, (255, 255, 255)),     # the "Test rumble" button in the window
    # tilt-to-stick zone crossings (rx_gstick_io, per-pad "feedback": haptic / led / both)
    "tilt_edge_h": (0.06, 0, 170, None),          # full tilt reached - a firm tick
    "tilt_edge_l": (0.25, 0, 0, (255, 120, 0)),   # ... an orange flash
    "tilt_edge_b": (0.25, 0, 170, (255, 120, 0)),
    "tilt_center_h": (0.04, 90, 0, None),         # back in the deadzone - a light tick
    "tilt_center_l": (0.15, 0, 0, (0, 200, 255)), # ... a cyan flash
    "tilt_center_b": (0.15, 90, 0, (0, 200, 255)),
}
fb: list[FeedbackState] = []
fb_event = threading.Event()
pulses: list[Pulse | None] = []       # the active confirmation per slot: (end, small, large, colour | None) or None
fb_sent: list[tuple[tuple[int, ...], float] | None] = []      # (value, time) of the feedback sent last; None = send at the next opportunity
# (trigger effects, time) sent last per slot; None = nothing (the pad has default triggers)
trig_sent: list[tuple[Sequence[Any], float] | None] = []
leds_sent: list[tuple[tuple[int, int, int], float] | None] = []   # (leds packet values, time) sent last; None = send at once
cbs: list[Callable[..., None]] = []
