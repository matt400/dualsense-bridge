"""Receiver: settings changed in the window without a restart (settings.json next to the script).

  mouse     cursor sensitivity and stabilisation of the physical touchpad click (rx_mouse)
  master    which pad is the master, what only it may do, and the system actions "PS + button" (rx_master)
  ui        window language
  lightbar  lightbar colour when the game has set nothing (per pad, by MAC), low-battery blinking and its threshold
            (the same threshold drives the warning on the pad card) - rx_feedback, rx_lightbar
  wol       Wake-on-LAN from the pad (PS + button while the receiver is silent) - sent to the sender (the scfg packet)
  log       the event log in the window: level, line limit, events from the sender
  vpad      virtual pads: the backend (HIDMaestro DualSense / ViGEmBus DS4), fixed player order (rx_ds4, rx_hm)

A missing file / a bad value -> the default (a message in the log, the file is repaired on save). A change from the window
takes effect at once: apply() replaces the `cur` object (a single assignment - the network threads always read a consistent whole).
"""
from rx_i18n import _
import json
import os
import string
from dataclasses import asdict, dataclass, field, fields, replace

from proto import mac_bytes, mac_text, write_json
from rx_buttons import BUTTON_CODES

# ---------------- validators (a JSON value -> a checked value or ValueError) ----------------


def _num(lo, hi, kind=int):
    """Build a validator: a number (not a bool) within lo..hi, converted with `kind`."""
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"expected a number {lo}..{hi}")
        return kind(v)
    return check


def _choice(options):
    """Build a validator: the value must be one of `options` (a dict's keys or a tuple)."""
    def check(v):
        try:
            known = v in options
        # a list / object from JSON used as a lookup in the options dict (unhashable) - a bad value, not a crash
        except TypeError:
            known = False
        if not known:
            raise ValueError(f"expected one of {tuple(options)}")
        return v
    return check


def _bool(v):
    """Accept only a real JSON true / false."""
    if not isinstance(v, bool):
        raise ValueError("expected true / false")
    return v


def _text(v):
    """Accept text; normalise to stripped lower case."""
    if not isinstance(v, str):
        raise ValueError("expected text")
    return v.strip().lower()


# ---------------- mouse ----------------
@dataclass(frozen=True, slots=True)
class MouseConfig:
    sens: float = 0.5               # pixels per touchpad unit (initially mouse_sens from receiver.toml)
    # after a click press / release the cursor stays still for this many ms (the finger "presses down" and slides)
    click_freeze_ms: int = 150
    click_rewind_ms: int = 80       # movement from the last this-many ms before the click is undone (the cursor returns to where you aimed)
    drag_threshold: int = 60        # while a click is held the cursor starts moving (a drag) only after this many touchpad units

    V = None                        # validators - assigned below (a class attribute, not an instance field)


# ranges shared by settings.json validation and the sliders in the settings window (a slider narrower than the validation would clip the
# saved value)
MOUSE_LIMITS = {"sens": (0.05, 5.0), "click_freeze_ms": (0, 600), "click_rewind_ms": (0, 300), "drag_threshold": (0, 600)}
MouseConfig.V = {k: _num(lo, hi, float if k == "sens" else int) for k, (lo, hi) in MOUSE_LIMITS.items()}

# ---------------- master pad ----------------
# what can be reserved for the master only: key -> (label in the window, icon from rx_qt_icons); order = order in the window
MASTER_FEATURES = {
    "console": ("Tryb konsoli (ekran i dźwięk)", "tv"),   # labels are Polish i18n keys - shown in the window through rx_i18n._()
    "actions": ("Akcje systemowe (PS + przycisk)", "ps"),
    "ps_hold": ("Przytrzymanie PS: Game Bar / Big Picture", "ps"),
    "ps_game": ("PS w grze i w Steam (menu, nakładka)", "ps"),
    "screenshot": ("Zrzut ekranu (PS + Share)", "share"),
    "profile": ("Zmiana profilu (PS + ← / →)", "dpad_right"),
    "mouse": ("Touchpad jako mysz (PS + klik)", "mouse"),
    "sens": ("Czułość myszy (PS + ↑ / ↓)", "dpad_up"),
    "triggers": ("Efekty spustów (opór, klik, wibracja)", "r2"),
}
# master actions: key -> (label, needs a HOLD_S hold - protection against accidents)
MASTER_ACTIONS = {
    "pause": ("Pauza gry (Esc)", False),
    "close_game": ("Zamknij grę", True),
    "sleep": ("Uśpij komputer", True),
    "shutdown": ("Wyłącz komputer", True),
}
ABSENT = {"all": "każdy pad może wszystko", "nobody": "zastrzeżone funkcje są wyłączone"}
# buttons that can pair with PS: without the PS shortcut buttons (Share, D-pad) and R3 (PS + R3 = claiming the master)
COMBO_BUTTONS = tuple(n for n in BUTTON_CODES if n not in ("ps", "share", "r3"))


def _features(v):
    """Keep the known master features from a list, without repeats and in the window order."""
    if not isinstance(v, (list, tuple)):
        raise ValueError("expected a list")
    return tuple(k for k in MASTER_FEATURES if k in v)          # known keys only, no repeats, fixed order


def _actions(v):
    """Check {action: button}: each button must be a valid combo button used by at most one action (else cleared with a log line)."""
    if not isinstance(v, dict):
        raise ValueError("expected {action: button}")
    out, used = {}, set()
    for k in MASTER_ACTIONS:
        b = v.get(k, "")
        if b and (b not in COMBO_BUTTONS or b in used):
            print(f"Settings: master action {k} = {b!r} ignored (unknown or already used)")
            b = ""
        used.add(b)
        out[k] = b
    return out


@dataclass(frozen=True, slots=True)
class MasterConfig:
    pad: str = ""                   # pad id (a MAC as in remap.json or "slotN"); "" = no master
    when_absent: str = "all"        # the master is not connected: all | nobody
    only_master: tuple = ("console", "actions", "ps_hold")
    actions: dict = field(default_factory=lambda: {"pause": "options", "close_game": "triangle",
                                                   "sleep": "", "shutdown": ""})
    claim_hold: bool = True         # PS + R3 held for CLAIM_S seconds on a pad = that pad becomes the master

    V = None


MasterConfig.V = {"pad": _text, "when_absent": _choice(ABSENT), "only_master": _features, "actions": _actions,
                  "claim_hold": _bool}


@dataclass(frozen=True, slots=True)
class UiConfig:
    lang: str = "pl"                # window language: pl | en (applied after a restart)

    V = None


UiConfig.V = {"lang": _choice(("pl", "en"))}


# ---------------- lightbar ----------------
LIGHT_MODES = {"solid": "Stały kolor", "battery": "Kolor wg stanu baterii"}
DEFAULT_PAD = "*"                 # the lightbar setting for pads without their own entry


@dataclass(frozen=True, slots=True)
class PadLight:
    mode: str = "solid"           # solid | battery - only when the game has not set a colour
    color: str = ""               # "#rrggbb" for the solid mode; "" = idle_lightbar from receiver.toml
    blink: bool = True            # low battery: red blinking (also over the colour set by the game)
    game: bool = True             # a game / PC program may change the colour (False = always the colour from the pad settings)


def _color(v):
    """Accept \"\" or \"#rrggbb\" (lower-cased)."""
    if v == "":
        return ""
    # exactly 6 hex digits: int(..., 16) let through "#0x1234", "# 12345" and "#12_345", and then
    # rx_lightbar.hex_rgb crashed the feedback thread on every refresh
    if not (isinstance(v, str) and len(v) == 7 and v[0] == "#" and all(c in string.hexdigits for c in v[1:])):
        raise ValueError("expected #rrggbb")
    return v.lower()


def _pads(v):
    """{"*" | MAC: {"mode", "color", "blink", "game"}} -> {key: PadLight}; bad entries are skipped."""
    if not isinstance(v, dict):
        raise ValueError("expected {pad: {mode, color, blink}}")
    out = {}
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
    low_pct: int = 15             # low battery threshold [%] - blinking and the warning on the card (initially low_batt from the toml)
    pads: dict = field(default_factory=dict)

    V = None

    def get(self, key):
        """The lightbar setting for a pad key: its own entry, else the \"*\" default, else the built-in default."""
        return self.pads.get(key) or self.pads.get(DEFAULT_PAD) or PadLight()


LOW_PCT = (5, 50)
LightbarConfig.V = {"low_pct": _num(*LOW_PCT), "pads": _pads}

# ---------------- Wake-on-LAN ----------------
WOL_BUTTONS = ("options", "share", "cross", "circle", "triangle", "square", "l1", "r1", "l3", "r3")
WOL_HOLD_MS = (300, 5000)


def _mac(v):
    """Accept \"\" or a MAC address; return it in the canonical text form."""
    if v == "":
        return ""
    if not isinstance(v, str) or mac_bytes(v) is None:
        raise ValueError("expected a MAC address aa:bb:cc:dd:ee:ff")
    return mac_text(mac_bytes(v))


def _ipv4(v):
    """Accept \"\" or a valid IPv4 address."""
    if v == "":
        return ""
    import ipaddress
    try:
        return str(ipaddress.IPv4Address(v))
    except (ValueError, TypeError):
        raise ValueError("expected an IPv4 address") from None


@dataclass(frozen=True, slots=True)
class WolConfig:
    enabled: bool = True
    mac: str = ""                 # MAC of the PC's network card; "" = the sender works it out itself (the phone's ARP table)
    bcast: str = ""               # broadcast address; "" = the sender works it out itself (the network to the PC + 255.255.255.255)
    button: str = "options"       # PS + this button held while the receiver does not answer
    hold_ms: int = 1000

    V = None


WolConfig.V = {"enabled": _bool, "mac": _mac, "bcast": _ipv4, "button": _choice(WOL_BUTTONS), "hold_ms": _num(*WOL_HOLD_MS)}

# ---------------- event log ----------------
LOG_LEVELS = {"info": "Wszystko", "warn": "Ostrzeżenia i błędy", "error": "Tylko błędy"}
LOG_LINES = (50, 5000)


@dataclass(frozen=True, slots=True)
class LogConfig:
    level: str = "info"           # the lowest level shown
    max_lines: int = 500          # this many last lines in the panel
    sender: bool = False          # events from the sender (the event packet) - the sender sends them only when this is on

    V = None


LogConfig.V = {"level": _choice(LOG_LEVELS), "max_lines": _num(*LOG_LINES), "sender": _bool}


# ---------------- virtual pads ----------------
VPAD_BACKENDS = {"auto": "Automatycznie: HIDMaestro, gdy jest, inaczej ViGEmBus",
                 "hidmaestro": "HIDMaestro - DualSense (zapas: ViGEmBus)",
                 "vigem": "ViGEmBus - DualShock 4"}


def _path(v):
    """Accept a path string, stripped."""
    if not isinstance(v, str):
        raise ValueError("expected a path")
    return v.strip()


@dataclass(frozen=True, slots=True)
class VpadConfig:
    backend: str = "auto"         # auto | hidmaestro | vigem
    fixed_slots: bool = True      # a placeholder pad in empty lower slots: pad 2 never becomes player 1
    # path to HIDMaestro.Core.dll (or its folder); "" = downloaded automatically / hidmaestro\ next to the script
    hm_dll: str = ""
    hm_auto: bool = True          # download and update HIDMaestro from GitHub (only without an own hm_dll path)

    V = None


VpadConfig.V = {"backend": _choice(VPAD_BACKENDS), "fixed_slots": _bool, "hm_dll": _path, "hm_auto": _bool}


@dataclass(frozen=True, slots=True)
class Settings:
    mouse: MouseConfig = field(default_factory=MouseConfig)
    master: MasterConfig = field(default_factory=MasterConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    lightbar: LightbarConfig = field(default_factory=LightbarConfig)
    wol: WolConfig = field(default_factory=WolConfig)
    log: LogConfig = field(default_factory=LogConfig)
    vpad: VpadConfig = field(default_factory=VpadConfig)


def _section(cls, raw, base, name):
    """A JSON dict -> a dataclass; a missing key = the value from `base`, a bad value = the value from `base` + a message."""
    values = {}
    for f in fields(cls):
        if f.name not in (raw or {}):
            continue
        try:
            values[f.name] = cls.V[f.name](raw[f.name])
        except (ValueError, TypeError) as e:       # TypeError: an unexpected type from manual editing - not a startup crash
            print(f"Settings: bad {name}.{f.name} = {raw[f.name]!r} ({e}) - using {getattr(base, f.name)!r}")
    return replace(base, **values)


cur = Settings()
_listeners = []


def defaults(mouse_sens=0.5, low_batt=15):
    """Built-in Settings, seeded with the mouse sensitivity and low-battery threshold from receiver.toml (the threshold is clamped to
    LOW_PCT).
    """
    return Settings(MouseConfig(sens=mouse_sens), MasterConfig(),
                    lightbar=LightbarConfig(low_pct=min(LOW_PCT[1], max(LOW_PCT[0], int(low_batt)))))


def load(path, base=None):
    """settings.json -> Settings (a missing / corrupt file = `base`, by default defaults())."""
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

    def sec(k):
        return raw.get(k) if isinstance(raw.get(k), dict) else {}

    return Settings(*(_section(type(getattr(base, f.name)), sec(f.name), getattr(base, f.name), f.name)
                      for f in fields(Settings)))


def save(s, path) -> None:
    """Atomic write (a temporary file + replace)."""
    data = {"version": 1, "mouse": asdict(s.mouse), "master": {**asdict(s.master), "only_master": list(s.master.only_master)},
            "ui": asdict(s.ui), "lightbar": asdict(s.lightbar), "wol": asdict(s.wol), "log": asdict(s.log),
            "vpad": asdict(s.vpad)}
    write_json(path, data, indent=2, ensure_ascii=False)


def on_change(fn) -> None:
    """Register fn(old, new), called after every apply() - e.g. rx_mouse sets the sensitivity of all pads."""
    _listeners.append(fn)


def apply(s) -> None:
    """Make `s` the current settings with one assignment, then notify the listeners (a failing listener is only logged)."""
    global cur
    old, cur = cur, s
    for fn in _listeners:
        try:
            fn(old, s)
        except Exception as e:
            print(f"Settings listener error: {e!r}")


def update(path=None, **sections):
    """Change part of the settings (e.g. update(master=...)), effective at once and saved when path is given.
    -> (ok, message).
    """
    s = replace(cur, **sections)
    apply(s)
    if path:
        try:
            save(s, path)
        except OSError as e:
            return False, _("Nie udało się zapisać {path}: {err}").format(path=os.path.basename(path), err=e)
    return True, _("Zapisano")
