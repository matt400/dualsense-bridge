"""Receiver: settings changed in the window without a restart (settings.json next to the script).

  mouse     cursor sensitivity and stabilisation of the physical touchpad click (rx_mouse)
  master    which pad is the master, what only it may do, and the system actions "PS + button" (rx_master)
  ui        window language, which pad cards the left column shows (only connected / a fixed number)
  lightbar lightbar colour when the game has set nothing (per pad, by MAC), low-battery blinking and its threshold
            (the same threshold drives the warning on the pad card) - rx_feedback, rx_lightbar
  wol       Wake-on-LAN from the pad (PS + button while the receiver is silent) - sent to the sender (the scfg packet)
  log       the event log in the window: level, line limit, events from the sender
  vpad      virtual pads: the backend (HIDMaestro DualSense / ViGEmBus DS4), fixed player order (rx_ds4, rx_hm)
  haptics   game HD haptics per pad (by MAC): on / off and the strength - rx_haptics

A missing file / a bad value -> the default (a message in the log, the file is repaired on save). A change from the window
takes effect at once: apply() replaces the `cur` object (a single assignment - the network threads always read a consistent whole).
"""
from __future__ import annotations

import ipaddress
import json
import os
import string
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, ClassVar, Final, TypeAlias

from proto import mac_bytes, mac_text, write_json
from rx_buttons import BUTTON_CODES
from rx_i18n import _

Validator: TypeAlias = Callable[[Any], Any]     # a JSON value -> the checked value, or ValueError
Validators: TypeAlias = dict[str, Validator]
HEX_COLOR_LEN: Final = 7        # "#rrggbb"

# ---------------- validators (a JSON value -> a checked value or ValueError) ----------------


def _num(lo: float, hi: float, kind: Callable[[float], float] = int) -> Validator:
    """Build a validator: a number (not a bool) within lo..hi, converted with `kind`."""
    def check(v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"expected a number {lo}..{hi}")
        return kind(v)
    return check


def _choice(options: Iterable[Any]) -> Validator:
    """Build a validator: the value must be one of `options` (a dict's keys or a tuple)."""
    def check(v: Any) -> Any:
        try:
            known = v in options
        # a list / object from JSON used as a lookup in the options dict (unhashable) - a bad value, not a crash
        except TypeError:
            known = False
        if not known:
            raise ValueError(f"expected one of {tuple(options)}")
        return v
    return check


def _bool(v: Any) -> bool:
    """Accept only a real JSON true / false."""
    if not isinstance(v, bool):
        raise ValueError("expected true / false")
    return v


def _text(v: Any) -> str:
    """Accept text; normalise to stripped lower case."""
    if not isinstance(v, str):
        raise ValueError("expected text")
    return v.strip().lower()


# ---------------- mouse ----------------
@dataclass(frozen=True, slots=True)
class MouseConfig:
    """Touchpad click and cursor tuning."""

    sens: float = 0.5               # pixels per touchpad unit (initially mouse_sens from receiver.toml)
    # after a click press / release the cursor stays still for this many ms (the finger "presses down" and slides)
    click_freeze_ms: int = 150
    click_rewind_ms: int = 80       # movement from the last this-many ms before the click is undone (the cursor returns to where you aimed)
    drag_threshold: int = 60        # while a click is held the cursor starts moving (a drag) only after this many touchpad units

    V: ClassVar[Validators] = None   # validators - assigned below (a class attribute, not an instance field)


# ranges shared by settings.json validation and the sliders in the settings window (a slider narrower than the validation would clip the
# saved value)
MOUSE_LIMITS: Final = {"sens": (0.05, 5.0), "click_freeze_ms": (0, 600), "click_rewind_ms": (0, 300), "drag_threshold": (0, 600)}
MouseConfig.V = {k: _num(lo, hi, float if k == "sens" else int) for k, (lo, hi) in MOUSE_LIMITS.items()}

# ---------------- master pad ----------------
# what can be reserved for the master only: key -> (label in the window, icon from rx_qt_icons); order = order in the window
MASTER_FEATURES: Final = {
    "console": ("Tryb konsoli (ekran i dźwięk)", "tv"),   # labels are Polish i18n keys - shown in the window through rx_i18n._()
    "actions": ("Akcje systemowe (PS + przycisk)", "ps"),
    "ps_hold": ("Przytrzymanie PS: Game Bar / Big Picture", "ps"),
    "ps_game": ("PS w grze i w Steam (menu, nakładka)", "ps"),
    "screenshot": ("Zrzut ekranu (PS + Share)", "share"),
    "profile": ("Zmiana profilu (PS + ← / →)", "dpad_right"),
    "mouse": ("Touchpad jako mysz (PS + klik)", "mouse"),
    "sens": ("Czułość myszy (PS + ↑ / ↓)", "dpad_up"),
    "triggers": ("Efekty spustów (opór, klik, wibracja)", "r2"),
    "haptics": ("Haptyka gry (HD haptics)", "rumble"),
}
# master actions: key -> (label, needs a HOLD_S hold - protection against accidents)
MASTER_ACTIONS: Final = {
    "pause": ("Pauza gry (Esc)", False),
    "close_game": ("Zamknij grę", True),
    "sleep": ("Uśpij komputer", True),
    "shutdown": ("Wyłącz komputer", True),
    "swap": ("Zamień graczy P1 ↔ P2 (przy dwóch padach)", False),
}
ABSENT: Final = {"all": "każdy pad może wszystko", "nobody": "zastrzeżone funkcje są wyłączone"}
DEFAULT_ACTIONS: Final = {"pause": "options", "close_game": "triangle", "sleep": "", "shutdown": "", "swap": "square"}
ADDED_LATER: Final = frozenset({"swap"})   # actions added after the first release: absent in an old settings.json = default, not "off"
# buttons that can pair with PS: without the PS shortcut buttons (Share, D-pad) and R3 (PS + R3 = claiming the master)
COMBO_BUTTONS: Final = tuple(n for n in BUTTON_CODES if n not in ("ps", "share", "r3"))


def _features(v: Any) -> tuple[str, ...]:
    """Keep the known master features from a list, without repeats and in the window order."""
    if not isinstance(v, (list, tuple)):
        raise ValueError("expected a list")
    return tuple(k for k in MASTER_FEATURES if k in v)          # known keys only, no repeats, fixed order


def _actions(v: Any) -> dict[str, str]:
    """Check {action: button}: each button must be a valid combo button used by at most one action (else cleared with a log line)."""
    if not isinstance(v, dict):
        raise ValueError("expected {action: button}")
    out: dict[str, str] = {}
    used: set[str] = set()
    for k in MASTER_ACTIONS:
        b = v.get(k, DEFAULT_ACTIONS[k] if k in ADDED_LATER else "")      # a file from before the action: its default button
        if b and (b not in COMBO_BUTTONS or b in used):
            print(f"Settings: master action {k} = {b!r} ignored (unknown or already used)")
            b = ""
        used.add(b)
        out[k] = b
    return out


@dataclass(frozen=True, slots=True)
class MasterConfig:
    """Which pad is the master and what only it may do."""

    pad: str = ""                   # pad id (a MAC as in remap.json or "slotN"); "" = no master
    when_absent: str = "all"        # the master is not connected: all | nobody
    only_master: tuple[str, ...] = ("console", "actions", "ps_hold")
    actions: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ACTIONS))
    claim_hold: bool = True         # PS + R3 held for CLAIM_S seconds on a pad = that pad becomes the master

    V: ClassVar[Validators] = None


MasterConfig.V = {"pad": _text, "when_absent": _choice(ABSENT), "only_master": _features, "actions": _actions,
                  "claim_hold": _bool}


PADS_MODES: Final = ("dynamic", "fixed")
PADS_COUNT: Final = (1, 4)


@dataclass(frozen=True, slots=True)
class UiConfig:
    """Window preferences."""

    lang: str = "pl"                # window language: pl | en (applied after a restart)
    # which pad cards the left column shows: dynamic = only connected pads, fixed = the first pads_count slots always
    # (a connected pad beyond them is shown too - it never disappears from the window)
    pads_mode: str = "dynamic"
    pads_count: int = 2

    V: ClassVar[Validators] = None

    def slots(self, connected: Sequence[bool]) -> list[int]:
        """The slots whose cards are shown, given which pads are connected now (index = slot, in slot order)."""
        if self.pads_mode == "dynamic":
            return [i for i, on in enumerate(connected) if on]
        return [i for i, on in enumerate(connected) if on or i < self.pads_count]


UiConfig.V = {"lang": _choice(("pl", "en")), "pads_mode": _choice(PADS_MODES), "pads_count": _num(*PADS_COUNT)}


# ---------------- lightbar ----------------
LIGHT_MODES: Final = {"solid": "Stały kolor", "battery": "Kolor wg stanu baterii"}
DEFAULT_PAD: Final = "*"                 # the lightbar setting for pads without their own entry


@dataclass(frozen=True, slots=True)
class PadLight:
    """Lightbar behaviour of one pad."""

    mode: str = "solid"           # solid | battery - only when the game has not set a colour
    color: str = ""               # "#rrggbb" for the solid mode; "" = idle_lightbar from receiver.toml
    blink: bool = True            # low battery: red blinking (also over the colour set by the game)
    game: bool = True             # a game / PC program may change the colour (False = always the colour from the pad settings)


def _color(v: Any) -> str:
    """Accept "" or "#rrggbb" (lower-cased)."""
    if v == "":
        return ""
    # exactly 6 hex digits: int(..., 16) let through "#0x1234", "# 12345" and "#12_345", and then
    # rx_lightbar.hex_rgb crashed the feedback thread on every refresh
    if not (isinstance(v, str) and len(v) == HEX_COLOR_LEN and v[0] == "#" and all(c in string.hexdigits for c in v[1:])):
        raise ValueError("expected #rrggbb")
    return v.lower()


def _pads(v: Any) -> dict[str, PadLight]:
    """Turn {"*" | MAC: {"mode", "color", "blink", "game"}} into {key: PadLight}; bad entries are skipped."""
    if not isinstance(v, dict):
        raise ValueError("expected {pad: {mode, color, blink}}")
    out: dict[str, PadLight] = {}
    for k, raw in v.items():
        try:
            if not isinstance(raw, dict):
                raise ValueError("expected an object")
            out[_text(k)] = PadLight(mode=_choice(LIGHT_MODES)(raw.get("mode", "solid")), color=_color(raw.get("color", "")),
                                     blink=_bool(raw.get("blink", True)), game=_bool(raw.get("game", True)))
        except (ValueError, TypeError) as e:
            print(f"Settings: lightbar for {k!r} ignored ({e})")
    return out


@dataclass(frozen=True, slots=True)
class LightbarConfig:
    """Lightbar defaults, per-pad overrides and the low-battery threshold."""

    low_pct: int = 15             # low battery threshold [%] - blinking and the warning on the card (initially low_batt from the toml)
    pads: dict[str, PadLight] = field(default_factory=dict)

    V: ClassVar[Validators] = None

    def get(self, key: str) -> PadLight:
        """Return the lightbar setting for a pad key: its own entry, else the "*" default, else the built-in default."""
        return self.pads.get(key) or self.pads.get(DEFAULT_PAD) or PadLight()


LOW_PCT: Final = (5, 50)
LightbarConfig.V = {"low_pct": _num(*LOW_PCT), "pads": _pads}

# ---------------- Wake-on-LAN ----------------
WOL_BUTTONS: Final = ("options", "share", "cross", "circle", "triangle", "square", "l1", "r1", "l3", "r3")
WOL_HOLD_MS: Final = (300, 5000)


def _mac(v: Any) -> str:
    """Accept "" or a MAC address; return it in the canonical text form."""
    if v == "":
        return ""
    if not isinstance(v, str) or mac_bytes(v) is None:
        raise ValueError("expected a MAC address aa:bb:cc:dd:ee:ff")
    raw = mac_bytes(v)
    return mac_text(raw)


def _ipv4(v: Any) -> str:
    """Accept "" or a valid IPv4 address."""
    if v == "":
        return ""
    try:
        return str(ipaddress.IPv4Address(v))
    except (ValueError, TypeError):
        raise ValueError("expected an IPv4 address") from None


@dataclass(frozen=True, slots=True)
class WolConfig:
    """Wake-on-LAN from the pad."""

    enabled: bool = True
    mac: str = ""                 # MAC of the PC's network card; "" = the sender works it out itself (the phone's ARP table)
    bcast: str = ""               # broadcast address; "" = the sender works it out itself (the network to the PC + 255.255.255.255)
    button: str = "options"       # PS + this button held while the receiver does not answer
    hold_ms: int = 1000

    V: ClassVar[Validators] = None


WolConfig.V = {"enabled": _bool, "mac": _mac, "bcast": _ipv4, "button": _choice(WOL_BUTTONS), "hold_ms": _num(*WOL_HOLD_MS)}

# ---------------- event log ----------------
LOG_LEVELS: Final = {"info": "Wszystko", "warn": "Ostrzeżenia i błędy", "error": "Tylko błędy"}
LOG_LINES: Final = (50, 5000)


@dataclass(frozen=True, slots=True)
class LogConfig:
    """The event log panel."""

    level: str = "info"           # the lowest level shown
    max_lines: int = 500          # this many last lines in the panel
    sender: bool = False          # events from the sender (the event packet) - the sender sends them only when this is on

    V: ClassVar[Validators] = None


LogConfig.V = {"level": _choice(LOG_LEVELS), "max_lines": _num(*LOG_LINES), "sender": _bool}


# ---------------- virtual pads ----------------
VPAD_BACKENDS: Final = {"auto": "Automatycznie: HIDMaestro, gdy jest, inaczej ViGEmBus",
                 "hidmaestro": "HIDMaestro - DualSense (zapas: ViGEmBus)",
                 "vigem": "ViGEmBus - DualShock 4"}


def _path(v: Any) -> str:
    """Accept a path string, stripped."""
    if not isinstance(v, str):
        raise ValueError("expected a path")
    return v.strip()


@dataclass(frozen=True, slots=True)
class VpadConfig:
    """Virtual pad backend."""

    backend: str = "auto"         # auto | hidmaestro | vigem
    fixed_slots: bool = True      # a placeholder pad in empty lower slots: pad 2 never becomes player 1
    # path to HIDMaestro.Core.dll (or its folder); "" = downloaded automatically / hidmaestro\ next to the script
    hm_dll: str = ""
    hm_auto: bool = True          # download and update HIDMaestro from GitHub (only without an own hm_dll path)

    V: ClassVar[Validators] = None


VpadConfig.V = {"backend": _choice(VPAD_BACKENDS), "fixed_slots": _bool, "hm_dll": _path, "hm_auto": _bool}


# ---------------- game haptics ----------------
HAPTICS_GAIN: Final = (0, 200)      # strength [%]: the game's haptics waveform times this (a soft clip catches the peaks)


@dataclass(frozen=True, slots=True)
class PadHaptics:
    """Game HD haptics of one pad (rx_haptics)."""

    enabled: bool = False         # opt-in: the virtual pad becomes a USB composite (a kernel driver comes with it) on its next connect
    gain: int = 100               # strength [%]


def _haptic_pads(v: Any) -> dict[str, PadHaptics]:
    """Turn {"*" | MAC: {"enabled", "gain"}} into {key: PadHaptics}; bad entries are skipped."""
    if not isinstance(v, dict):
        raise ValueError("expected {pad: {enabled, gain}}")
    out: dict[str, PadHaptics] = {}
    for k, raw in v.items():
        try:
            if not isinstance(raw, dict):
                raise ValueError("expected an object")
            out[_text(k)] = PadHaptics(enabled=_bool(raw.get("enabled", False)), gain=_num(*HAPTICS_GAIN)(raw.get("gain", 100)))
        except (ValueError, TypeError) as e:
            print(f"Settings: haptics for {k!r} ignored ({e})")
    return out


@dataclass(frozen=True, slots=True)
class HapticsConfig:
    """Per-pad game haptics (keys as in the lightbar: "*" is the default, a MAC is one pad)."""

    pads: dict[str, PadHaptics] = field(default_factory=dict)

    V: ClassVar[Validators] = None

    def get(self, key: str) -> PadHaptics:
        """Return the setting for a pad key: its own entry, else the "*" default, else the built-in default (off)."""
        return self.pads.get(key) or self.pads.get(DEFAULT_PAD) or PadHaptics()


HapticsConfig.V = {"pads": _haptic_pads}


@dataclass(frozen=True, slots=True)
class Settings:
    """All settings sections; replaced as a whole by apply()."""

    mouse: MouseConfig = field(default_factory=MouseConfig)
    master: MasterConfig = field(default_factory=MasterConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    lightbar: LightbarConfig = field(default_factory=LightbarConfig)
    wol: WolConfig = field(default_factory=WolConfig)
    log: LogConfig = field(default_factory=LogConfig)
    vpad: VpadConfig = field(default_factory=VpadConfig)
    haptics: HapticsConfig = field(default_factory=HapticsConfig)


def _section(cls: Any, raw: dict[str, Any] | None, base: Any, name: str) -> Any:
    """Build a section dataclass from a JSON dict; a missing key or a bad value (plus a message) keeps the value from `base`."""
    values: dict[str, Any] = {}
    raw = raw or {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        try:
            values[f.name] = cls.V[f.name](raw[f.name])
        except (ValueError, TypeError) as e:       # TypeError: an unexpected type from manual editing - not a startup crash
            print(f"Settings: bad {name}.{f.name} = {raw[f.name]!r} ({e}) - using {getattr(base, f.name)!r}")
    return replace(base, **values)


cur = Settings()
_listeners: list[Callable[[Settings, Settings], None]] = []
# apply() / update() from the window thread and from timer threads (rx_master claim) must not interleave: update() reads `cur`,
# builds the new object and saves it - two of them at once would lose one change. Re-entrant: a listener may call update().
_lock = threading.RLock()


def defaults(mouse_sens: float = 0.5, low_batt: int = 15) -> Settings:
    """Return the built-in Settings.

    Seeded with the mouse sensitivity and low-battery threshold from receiver.toml (the threshold is clamped to
    LOW_PCT).
    """
    return Settings(MouseConfig(sens=mouse_sens), MasterConfig(),
                    lightbar=LightbarConfig(low_pct=min(LOW_PCT[1], max(LOW_PCT[0], int(low_batt)))))


def load(path: str, base: Settings | None = None) -> Settings:
    """Read settings.json into Settings; a missing or corrupt file gives `base` (by default defaults())."""
    base = base or defaults()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return base
    except (OSError, ValueError) as e:
        print(f"Settings: cannot read {path} ({e!r}) - using defaults")
        return base
    raw = raw if isinstance(raw, dict) else {}

    def sec(k: str) -> dict[str, Any]:
        section = raw.get(k)
        return section if isinstance(section, dict) else {}

    return Settings(*(_section(type(getattr(base, f.name)), sec(f.name), getattr(base, f.name), f.name)
                      for f in fields(Settings)))


def save(s: Settings, path: str) -> None:
    """Write the settings atomically (a temporary file + replace)."""
    data = {"version": 1, "mouse": asdict(s.mouse), "master": {**asdict(s.master), "only_master": list(s.master.only_master)},
            "ui": asdict(s.ui), "lightbar": asdict(s.lightbar), "wol": asdict(s.wol), "log": asdict(s.log),
            "vpad": asdict(s.vpad), "haptics": asdict(s.haptics)}
    write_json(path, data, indent=2, ensure_ascii=False)


def on_change(fn: Callable[[Settings, Settings], None]) -> None:
    """Register fn(old, new), called after every apply() - e.g. rx_mouse sets the sensitivity of all pads."""
    _listeners.append(fn)


def apply(s: Settings) -> None:
    """Make `s` the current settings with one assignment, then notify the listeners (a failing listener is only logged)."""
    global cur
    with _lock:
        old, cur = cur, s
        for fn in _listeners:
            try:
                fn(old, s)
            except Exception as e:  # noqa: BLE001 - one broken listener must not stop the others or the change
                print(f"Settings listener error: {e!r}")


def update(path: str | None = None, **sections: Any) -> tuple[bool, str]:
    """Change part of the settings at once (``update(master=...)``) and save them when path is given.

    When the save fails nothing is applied: the running settings never differ from the file silently.

    Returns:
        (ok, message).
    """
    with _lock:                      # read-modify-write and the file write in one piece: the file always holds the last change
        s = replace(cur, **sections)
        if path:
            try:
                save(s, path)        # before apply(): a failed save must not leave a setting active that the file does not hold
            except OSError as e:
                return False, _("Nie udało się zapisać {path}: {err}").format(path=os.path.basename(path), err=e)
        apply(s)
    return True, _("Zapisano")
