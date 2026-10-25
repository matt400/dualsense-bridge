"""Receiver: the console mode.

A single double click of a pad button (R2 by default) switches the screen and sound between the monitor and the TV.

  auto-ON   after the master pad connects (auto_on_master) the mode switches on by itself; you switch it off by hand with a double R2
            and it stays off until the master connects next time
  in a game switching (manual and auto-ON) works only when a program from allowed_apps is in the foreground
            (the desktop, Steam / Big Picture, launchers); in a game a double R2 does nothing (no blink), and auto-ON
            waits until the game is no longer in the foreground

  ON   the screen is the TV only (DisplaySwitch /external), the TV at tv_width x tv_height and tv_scale % scaling (rx_display),
       the sound audio_tv, the toast "Console mode on"
  OFF  the screen extend (DisplaySwitch /extend: both screens), the sound audio_pc, the toast "Console mode off"
  auto-OFF  no packets from the pad for timeout_s -> OFF by itself (both screens active, you are not left without a picture)
  start     startup_mode: extend (both screens) or internal (the monitor only) + the sound audio_pc
  PC shutdown / sleep  (a master action or Windows: Start, Big Picture, shutdown) with the mode on -> first
            OFF (extend): Windows remembers the screen layout, so without this the picture would be on the TV only after startup

Settings: console.json next to the script (a missing file / a bad value -> the defaults, the file is created). Edited in the window:
rx_qt_console.py. The pad buttons still reach the game - the double click detection only observes them.
A press together with another input (a shortcut: PS + Share, PS + d-pad, PS + touchpad click) is not a click.

The network thread calls on_state() only (a bit and time comparison). The screen / sound commands and the toast are run by one
worker thread from a queue, so nothing blocks UDP reception, and later switches do not overtake earlier ones.
An error in any step is logged and does not stop the others.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from typing import Any, Final, TypeAlias

import rx_audio
import rx_display
import rx_events as ev
import rx_master as master
import rx_settings
import rx_state as S
import rx_winapi as W
from rx_buttons import BIT, BUTTON_CODES, DIRS, INPUTS, TRIG_ON, pressed_inputs
from proto import write_json
from rx_config import C
from rx_i18n import _
from rx_settings import _bool, _choice, _num

Detector: TypeAlias = Callable[[int, int, int, int, int], bool]   # (mask, hx, hy, lt, rt) -> is the input pressed

STARTUP_MODES: Final = ("extend", "internal")
# programs where switching the console mode is allowed (the desktop, Steam / Big Picture, launchers, this receiver)
DEFAULT_APPS: Final = ("explorer.exe", "steam.exe", "steamwebhelper.exe", "python.exe", "pythonw.exe",
                "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
                "applicationframehost.exe", "textinputhost.exe", "lockapp.exe", "systemsettings.exe",
                "playnite.fullscreenapp.exe", "playnite.desktopapp.exe", "epicgameslauncher.exe", "galaxyclient.exe",
                "eadesktop.exe", "upc.exe")
CONFIG_VERSION: Final = 3
# below this two presses are not humanly possible (a value this low left over from an accidental wheel scroll over the
# Settings field made the double click never register); an older, lower value in console.json is reset to the default
MIN_DOUBLE_CLICK_MS: Final = 200
# the "Test" button: this many seconds in the ON mode, then OFF by itself (extend, the monitor does not stay black)
TEST_S: Final = 10.0
APP_NAME: Final = "DualSense bridge"
# a trigger as the mode button: pressed from half travel, released when it falls below half of the deepest point of that
# press (hysteresis - jitter does not give a "double click", but a quick double tap that only lets the trigger go part
# of the way back still counts as two presses; a fixed low release threshold made such taps merge into one)
TRIG_HI: Final = 128
RESTART_ENV: Final = "DSB_CONSOLE_ON" # a restart from the window: "1" / "0" = the console mode before the restart (stays the same)
DISPLAY_ARG: Final = {"extend": "/extend", "internal": "/internal", "tv": "/external"}
VERIFY_AFTER_S: Final = (3.0, 10.0, 25.0)   # after switching on: check whether the picture is on the TV only (and retry). After startup
                               # Windows (logon, waking the TV over HDMI) restores the remembered "extend" layout -
                               # the mode was "on" while the picture stayed on both screens
POWER_WAIT_S: Final = 8.0             # this long at most the PC shutdown / sleep waits for the screens to return to extend
TICK_S: Final = 1.0                # the worker wakes up this often when idle
JOIN_TIMEOUT_S: Final = 10
DISPLAYSWITCH_TIMEOUT_S: Final = 15
_STOP: Final = object()


def _apps(v: Any) -> tuple[str, ...]:
    """Validate the allowed program list: lower-cased, stripped, without empties and repeats."""
    if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) for x in v):
        raise ValueError("expected a list of program names")
    return tuple(dict.fromkeys(x.strip().lower() for x in v if x.strip()))


def _text(v: Any) -> str:
    """Accept text, stripped."""
    if not isinstance(v, str):
        raise ValueError("expected text")
    return v.strip()


@dataclass(frozen=True, slots=True)
class ConsoleConfig:
    """The console mode settings (console.json)."""

    toggle_button: str = "r2"       # a pad button (a name as in remapping: "r2", "share", "cross", "dpad_up"...);
                                    # not "ps": a double PS also opens Steam Big Picture
    double_click_ms: int = 400      # the window between two presses
    audio_tv: str = ""              # the TV audio device name; empty = leave the sound alone
    audio_pc: str = ""              # the PC audio device name; empty = leave the sound alone
    timeout_s: int = 60             # pad silence longer than this = auto-OFF
    startup_mode: str = "extend"    # extend | internal
    tv_width: int = 2560            # the TV resolution after switching on (0 = unchanged)
    tv_height: int = 1440
    tv_scale: int = 200             # the TV scaling in % (0 = unchanged)
    auto_on_master: bool = True     # the master pad connecting = console mode ON
    allowed_apps: tuple[str, ...] = DEFAULT_APPS   # switching only when one of these programs is in the foreground
    version: int = CONFIG_VERSION   # the file version (migrations)

    @classmethod
    def from_dict(cls, raw: Any) -> tuple[ConsoleConfig, bool]:
        """Build (config, fixed) from a JSON dict; a bad or missing value = the default (fixed=True: the file is worth saving)."""
        raw = dict(raw) if isinstance(raw, dict) else {}
        fixed = not raw
        if raw and raw.get("version") != CONFIG_VERSION:
            fixed = True
            old, raw["version"] = raw.get("version"), CONFIG_VERSION
            # v1: the default PS (a clash with Big Picture); v2: the default mute (removed - the pad does not pass the microphone)
            if raw.get("toggle_button") == "mute" or (old is None and raw.get("toggle_button") == "ps"):
                print(f"Console: toggle button {raw['toggle_button']} -> r2")
                raw["toggle_button"] = "r2"
        values: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in raw:
                fixed = True
                continue
            try:
                values[f.name] = _VALIDATORS[f.name](raw[f.name])
            except (ValueError, TypeError) as e:       # TypeError: an unexpected type from manual editing
                print(f"Console: bad {f.name} = {raw[f.name]!r} ({e}) - using default {f.default!r}")
                fixed = True
        return cls(**values), fixed


_VALIDATORS: Final = {
    "toggle_button": _choice(INPUTS),
    "double_click_ms": _num(MIN_DOUBLE_CLICK_MS, 2000),
    "audio_tv": _text,
    "audio_pc": _text,
    "timeout_s": _num(5, 3600),
    "startup_mode": _choice(STARTUP_MODES),
    "tv_width": _num(0, 15360),
    "tv_height": _num(0, 8640),
    "tv_scale": lambda v: int(_choice((0, *rx_display.DPI_VALUES))(v)),
    "auto_on_master": _bool,
    "allowed_apps": _apps,
    "version": _num(1, CONFIG_VERSION),
}


def load(path: str) -> ConsoleConfig:
    """Read console.json; a missing file, a corrupt file or bad values give the defaults and the corrected file is saved."""
    raw = None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"Console: cannot read {path} ({e!r}) - using defaults (old file kept as .bad)")
        with contextlib.suppress(OSError):
            os.replace(path, path + ".bad")
    cfg, fixed = ConsoleConfig.from_dict(raw)
    if fixed:
        try:
            save(cfg, path)
        except OSError as e:
            print(f"Console: cannot write {path}: {e!r}")
    return cfg


def save(cfg: ConsoleConfig, path: str) -> None:
    """An atomic write (a temporary file + replace): an interrupted write will not leave an empty console.json."""
    write_json(path, asdict(cfg), indent=2, ensure_ascii=False)


def detector(name: str) -> Detector:
    """Return a function (mask, hx, hy, lt, rt) -> bool telling whether the input with this name is pressed (raw inputs)."""
    if name in BUTTON_CODES:
        bit = BIT[BUTTON_CODES[name]]
        return lambda mask, hx, hy, lt, rt: bool(mask & bit)
    if name in DIRS:
        dx, dy = DIRS[name]
        return (lambda mask, hx, hy, lt, rt: hx == dx) if dx else (lambda mask, hx, hy, lt, rt: hy == dy)
    return (lambda mask, hx, hy, lt, rt: lt >= TRIG_ON) if name == "l2" else (lambda mask, hx, hy, lt, rt: rt >= TRIG_ON)


# ---------------- system commands (each separate so that tests can replace them) ----------------
def _display(mode: str) -> None:
    """Set the screen topology (extend / internal / tv) through SetDisplayConfig, falling back to DisplaySwitch.exe."""
    try:
        rx_display.set_topology(mode)
        return
    except (OSError, AttributeError) as e:          # an old Windows / no API -> DisplaySwitch
        print(f"Console: SetDisplayConfig failed ({e!r}) - using DisplaySwitch")
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "DisplaySwitch.exe")
    subprocess.run([exe, DISPLAY_ARG[mode]], timeout=DISPLAYSWITCH_TIMEOUT_S, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _screens() -> int | None:
    """Return the number of active screens, or None (no API / an error - then no checking)."""
    try:
        return len(rx_display.active_sources())
    except (OSError, AttributeError):
        return None


def _tv_mode(w: int, h: int, scale: int) -> None:
    """Set the TV resolution and scaling and log the result."""
    print(f"Console: TV {rx_display.apply_tv(w, h, scale)}")


def _foreground() -> str | None:
    """Return the foreground program (lower case), or None (no window / no access)."""
    try:
        return W.foreground_exe()
    except OSError:
        return None


def _audio(name: str) -> None:
    """Make the named device the default audio output."""
    rx_audio.set_default(name)


def _toast(title: str, msg: str = "") -> None:
    """Show a Windows notification."""
    from winotify import Notification
    Notification(app_id=APP_NAME, title=title, msg=msg, duration="short").show()


def _step(what: str, fn: Callable[..., Any], *args: Any) -> None:
    """A step that may fail (no DisplaySwitch / pycaw / winotify, an absent device): log it and carry on."""
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - a step may fail in many ways (COM, WinAPI, notifications) and must not stop the others
        print(f"Console: {what} failed: {e!r}")
        ev.error(_("Tryb konsoli: {what} - błąd: {err}").format(what=what, err=repr(e)))


def auto_pad(i: int) -> bool:
    """Tell whether a pad connected in slot i switches the console mode on.

    The master pad, and without a set master (any pad may do everything) - the first connected pad. Before, without a
    master auto-ON never worked (after the PC started the mode stayed off); a second pad arriving later does not switch
    on a mode that was switched off by hand.
    """
    if rx_settings.cur.master.pad:
        return master.is_master(i)
    return not any(S.alive[k] for k in range(len(S.alive)) if k != i)


class Console:
    """The console mode controller: double-click detection on the network thread, a worker thread for the system commands."""

    def __init__(self, cfg: ConsoleConfig) -> None:
        """Set up the state.

        Job queue, double-click tracking per pad, the pending auto-ON and the restart flags read from the environment.
        """
        self.cfg = cfg
        self.on = False
        self._jobs: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._since = time.monotonic()      # when the mode last changed (auto-OFF counts from the later of: a packet, a change)
        self._revert: tuple[float, ConsoleConfig] | None = None                 # (when, cfg) - back to OFF after "Test"
        self._verify: list[float] = []                   # when to check that the mode, once on, really shows the picture on the TV only
        self._detect = detector(cfg.toggle_button)
        self._trigger = cfg.toggle_button in ("l2", "r2")
        self._down = [False] * C.NUM_PADS
        self._peak = [0] * C.NUM_PADS       # the deepest trigger value of the current press (the release threshold is half of it)
        self._last_click = [0.0] * C.NUM_PADS
        self._capture: Callable[[str], None] | None = None                # a callback called with the name of the next pressed button
        self._cap_prev: list[set[str]] = []
        self._thread: threading.Thread | None = None
        restored = os.environ.pop(RESTART_ENV, None)
        self._restore_on = restored == "1"  # a restart with the mode on -> stays on
        self._pending: int | None = None   # auto-ON waiting for a game to leave the foreground (the master's slot)
        self._pending_since = 0.0
        self._skip_auto = restored == "0"   # a restart with the mode off -> the master reconnecting does not switch it on

    # ---------- network thread ----------
    def on_state(self, i: int, mask: int, hx: int, hy: int, lt: int, rt: int) -> None:
        """Called on every change of the pad's raw state. Comparisons only - no system commands."""
        if self._trigger:
            down = self._trigger_down(i, lt if self.cfg.toggle_button == "l2" else rt)
        else:
            down = self._detect(mask, hx, hy, lt, rt)
        rising = down and not self._down[i]
        self._down[i] = down
        if (cb := self._capture) is not None:
            self._feed_capture(i, cb, mask, hx, hy, lt, rt)
            return                          # a press while choosing the button does not count as a click
        if down and pressed_inputs(mask, hx, hy, lt, rt) - {self.cfg.toggle_button}:
            self.combo(i)                   # a shortcut (e.g. PS + Share, PS + d-pad) is not a click
            return
        if not rising:
            return
        now = time.monotonic()
        if now - self._last_click[i] <= self.cfg.double_click_ms / 1000:
            self._last_click[i] = 0.0       # a third click starts over, it does not switch a second time
            if (exe := self.blocked_by()) is not None:
                # in a game a double R2 is ordinary play (throttle, shooting) - silently, without a red blink
                print(f"Console: double {self.cfg.toggle_button} on controller {i} ignored - {exe} in the foreground")
                ev.info(_("Tryb konsoli: przełączenie pominięte - na pierwszym planie {exe}").format(exe=exe))
                return
            if not master.check(i, "console"):   # may be reserved for the master pad
                return
            print(f"Console: double {self.cfg.toggle_button} on controller {i}")
            self._jobs.put(self._toggle)
        else:
            self._last_click[i] = now

    def _trigger_down(self, i: int, v: int) -> bool:
        """Hysteresis of the trigger as a button: down from TRIG_HI, up when v drops below half of this press's peak.

        rx_net calls on_state only when a trigger moves to another 1/8 step (32 units), so the release threshold is rounded
        up to such a step: a fall below it is then always a step change and cannot go unnoticed.
        """
        if not self._down[i]:
            if v < TRIG_HI:
                return False
            self._peak[i] = v
            return True
        if v > self._peak[i]:
            self._peak[i] = v
        return v >= (((self._peak[i] >> 1) + 31) >> 5) << 5

    def pad_connected(self, i: int, ident: bool = False) -> None:
        """The pad in slot i has just connected (the first packet after silence) or sent a new identifier (ident).

        Without a master in the settings the connection alone decides: the identifier arrives right after the first packet
        and set auto-ON a second time - after a receiver restart with the mode off the first one used up the skip
        (_skip_auto) and the second switched the mode on anyway; with a game in the foreground the warning went out twice.
        """
        if not self.cfg.auto_on_master:
            return
        if ident and not rx_settings.cur.master.pad:
            return
        if not auto_pad(i):
            if S.pad_id[i]:                  # without an id (the first packet) the master is unknown - this is not an error
                print(f"Console: controller {i} ({S.profile_key(i)}) connected - "
                      + (f"not the master ({rx_settings.cur.master.pad})" if rx_settings.cur.master.pad
                         else "another controller is already connected") + ", no auto-ON")
            return
        if self._skip_auto:
            self._skip_auto = False
            print("Console: master connected - auto-ON skipped (receiver restarted with console mode off)")
            ev.info(_("Tryb konsoli: auto-włączenie pominięte - restart receivera przy wyłączonym trybie"))
            return
        print(f"Console: master connected on controller {i} - auto-ON queued")
        self._jobs.put(lambda: self._auto_on(i))

    def blocked_by(self) -> str | None:
        """Return the foreground program that blocks switching (a game), or None.

        An empty allowed_apps = no blocking (otherwise a cleared field in Settings blocked switching everywhere, the desktop
        too).
        """
        if not self.cfg.allowed_apps:
            return None
        exe = _foreground()
        return exe if exe and exe not in self.cfg.allowed_apps else None

    def _auto_on(self, i: int) -> None:
        """Queued auto-ON: wait (pending) when a game is in the foreground, otherwise switch on."""
        if self.on or not auto_pad(i):             # the pad in the slot or the master may have changed while queued
            return
        if (exe := self.blocked_by()) is not None:
            print(f"Console: master connected, {exe} in the foreground - console mode waits until it is not")
            ev.warn(_("Tryb konsoli czeka - na pierwszym planie {exe} (dodaj go do listy programów w Ustawieniach, "
                      "jeśli to nie gra)").format(exe=exe))
            self._pending, self._pending_since = i, time.monotonic()
            return
        self._apply(True, why=_("połączono pad master (pad {n})").format(n=i + 1))

    def resync(self, i: int) -> None:
        """A boundary of the Test view (the network thread, before on_state).

        During the test on_state is not called, so the mode button's state was from before the test - R2 held on entering
        and released during the test "stayed held" and the first click after the test was lost. The state is taken from the
        pad's current inputs, the click series starts over.
        """
        mask, hx, hy, lt, rt = S.states[i]["raw"]
        if self._trigger:
            self._down[i] = False           # judge the current value as a fresh press, not against the old press's peak
            self._peak[i] = 0
            self._down[i] = self._trigger_down(i, lt if self.cfg.toggle_button == "l2" else rt)
        else:
            self._down[i] = self._detect(mask, hx, hy, lt, rt)
        self._last_click[i] = 0.0

    def combo(self, i: int) -> None:
        """The mode button used together with another input (also the touchpad click, which is not in the mask).

        This press does not count as a click and breaks the series - two shortcuts from PS in a row do not switch the
        screen.
        """
        if self._down[i]:
            self._last_click[i] = 0.0

    def _feed_capture(self, i: int, cb: Callable[[str], None], *inputs: int) -> None:
        """While capturing: the first newly pressed input is passed to the callback."""
        cur = pressed_inputs(*inputs)
        new, self._cap_prev[i] = cur - self._cap_prev[i], cur
        if new:
            self._capture = None
            cb(next(n for n in INPUTS if n in new))

    def arm_capture(self, cb: Callable[[str], None]) -> None:
        """The next newly pressed button (not the one held now) goes to cb(name) - from the network thread."""
        self._cap_prev = [pressed_inputs(*s["raw"]) for s in S.states]
        self._capture = cb

    def cancel_capture(self) -> None:
        """Stop waiting for a button press."""
        self._capture = None

    # ---------- worker thread ----------
    def start(self) -> None:
        """Start the worker thread and queue the startup job."""
        self._thread = S.start_thread("console", self._run)
        self._jobs.put(self._startup)

    def _run(self) -> None:
        """The worker loop: run queued jobs, or a periodic _tick after 1 s of quiet, until _STOP; a failing job is logged."""
        while True:
            try:
                job = self._jobs.get(timeout=TICK_S)
            except queue.Empty:
                job = self._tick
            if job is _STOP:
                return
            try:
                job()
            except Exception as e:  # noqa: BLE001 - the worker loop must survive any failing job
                print(f"Console: job error:\n{traceback.format_exc()}")
                ev.error(_("Tryb konsoli: błąd - {err}").format(err=repr(e)))   # otherwise auto-ON vanished without a trace in the window

    def _tick(self) -> None:
        """Periodic work: release a waiting auto-ON, verify the TV layout, end a test, auto-OFF after silence."""
        now = time.monotonic()
        if self._pending is not None:
            i = self._pending
            if self.on or not S.alive[i] or not auto_pad(i):
                self._pending = None                 # already on / the master left - stop waiting
            elif self.blocked_by() is None:
                self._pending = None
                self._apply(True, why=_("połączono pad master (pad {n}), po {s} s czekania").format(
                    n=i + 1, s=round(now - self._pending_since)))
                return
        if self.on and self._verify and now >= self._verify[0]:
            self._verify.pop(0)
            self._check_tv()
        if self._revert and now >= self._revert[0]:
            self._apply(False, self._revert[1], _("koniec testu"))
        elif self.on and now - max(self._since, max(S.last_seen, default=0.0)) > self.cfg.timeout_s:
            print(f"Console: no packets from the pad for {self.cfg.timeout_s}s - auto-OFF")
            self._apply(False, why=_("Brak sygnału z pada przez {s} s").format(s=self.cfg.timeout_s))

    def _startup(self) -> None:
        """The first job: restore the mode after a restart, else set the startup screen mode and the PC sound."""
        if self._restore_on:
            self._apply(True, why=_("po restarcie receivera"), toast=False)
            return
        _step("display", _display, self.cfg.startup_mode)
        if self.cfg.audio_pc:
            _step("audio", _audio, self.cfg.audio_pc)
        print(f"Console: startup mode {self.cfg.startup_mode}")

    def _toggle(self) -> None:
        """A double click: switch the mode over (cancelling a pending auto-ON)."""
        self._pending = None
        self._apply(not self.on)

    def _apply(self, on: bool, cfg: ConsoleConfig | None = None, why: str = "", toast: bool = True) -> None:
        """Switch the mode on / off.

        The screen topology, the TV resolution, the audio device, the log / event and the toast (each step isolated by
        _step).
        """
        cfg = cfg or self.cfg
        self.on, self._since, self._revert = on, time.monotonic(), None
        self._verify = [self._since + d for d in VERIFY_AFTER_S] if on else []
        _step("display", _display, "tv" if on else "extend")
        if on and ((cfg.tv_width and cfg.tv_height) or cfg.tv_scale):
            _step("TV resolution / scaling", _tv_mode, cfg.tv_width, cfg.tv_height, cfg.tv_scale)
        if audio := cfg.audio_tv if on else cfg.audio_pc:
            _step("audio", _audio, audio)
        print(f"Console: {'ON' if on else 'OFF'}" + (f" ({why})" if why else ""))
        ev.info((_("Tryb konsoli włączony") if on else _("Tryb konsoli wyłączony")) + (f" ({why})" if why else ""))
        if toast:
            _step("toast", _toast, _("Tryb konsoli włączony") if on else _("Tryb konsoli wyłączony"), why)

    def _check_tv(self) -> None:
        """The mode is on but more than one screen is active (Windows restored "extend") -> switch again."""
        n = _screens()
        if n is None or n <= 1:
            self._verify.clear()             # the TV only (or it cannot be checked) - no more watching
            return
        cfg = self._revert[1] if self._revert else self.cfg
        print(f"Console: ON, but {n} displays are active (Windows restored its layout) - switching to the TV again")
        ev.warn(_("Tryb konsoli: Windows przywrócił oba ekrany - ponowne przełączenie na TV"))
        _step("display", _display, "tv")
        if (cfg.tv_width and cfg.tv_height) or cfg.tv_scale:
            _step("TV resolution / scaling", _tv_mode, cfg.tv_width, cfg.tv_height, cfg.tv_scale)

    def _test(self, cfg: ConsoleConfig) -> None:
        """Switch ON with cfg and schedule the return to OFF after TEST_S."""
        self._apply(True, cfg)
        self._revert = (time.monotonic() + TEST_S, cfg)

    def power_off(self, why: str) -> bool:
        """Switch the mode off before a PC shutdown / sleep (a Windows session end too); True when the mode was on.

        We wait for it to finish (POWER_WAIT_S at most). Windows remembers the screen layout, so without this the picture
        would be on the TV only after the computer starts. Callable from any thread.
        """
        if not self.on:
            return False
        done = threading.Event()

        def job() -> None:
            try:
                if self.on:
                    self._apply(False, why=why, toast=False)
            finally:
                done.set()
        self._jobs.put(job)
        if not done.wait(POWER_WAIT_S) and self.on:  # the worker thread is busy / stopped - switch at once
            self.on = False
            _step("display", _display, "extend")
            print(f"Console: OFF ({why}, direct)")
        return True

    # ---------- for the settings window ----------
    def test(self, cfg: ConsoleConfig) -> None:
        """ON with the given (not yet saved) settings, after TEST_S seconds OFF with the same ones."""
        self._jobs.put(lambda: self._test(cfg))

    def close(self, restart: bool = False) -> None:
        """Closing the receiver.

        On a plain exit with the console mode on we return to extend (after a restart the start itself sets the screen by
        startup_mode, so we do not switch twice).
        """
        if self.on and not restart:
            self._jobs.put(lambda: self._apply(False, why=_("zamknięcie receivera"), toast=False))
        self._jobs.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=JOIN_TIMEOUT_S)


ctl: Console | None = None


def init() -> None:
    """Load console.json and start the worker thread (sets the screen and sound by startup_mode)."""
    global ctl
    ctl = Console(load(C.CONSOLE_PATH))
    ctl.start()
    print(f"Console mode: double-click {ctl.cfg.toggle_button} within {ctl.cfg.double_click_ms} ms, "
          f"auto-OFF after {ctl.cfg.timeout_s}s ({C.CONSOLE_PATH})")


def on_state(i: int, mask: int, hx: int, hy: int, lt: int, rt: int) -> None:
    """Forward a pad state to the console mode (if initialised)."""
    if ctl is not None:
        ctl.on_state(i, mask, hx, hy, lt, rt)


def pad_connected(i: int, ident: bool = False) -> None:
    """Forward a pad connection to the console mode (if initialised)."""
    if ctl is not None:
        ctl.pad_connected(i, ident)


def resync(i: int) -> None:
    """A boundary of the Test view - see Console.resync."""
    if ctl is not None:
        ctl.resync(i)


def is_on() -> bool:
    """Tell whether the console mode is on permanently.

    Without a running "Test" (the test returns to OFF by itself, so a restart during it, e.g. Save in Settings, must not
    leave the mode on forever).
    """
    return ctl is not None and ctl.on and ctl._revert is None


def power_off(why: str) -> bool:
    """Before a PC shutdown / sleep - see Console.power_off."""
    return ctl is not None and ctl.power_off(why)


def on_touch_click(i: int) -> None:
    """A touchpad click (the T_CLICK packet) - with the mode button held it is a shortcut, not a click."""
    if ctl is not None:
        ctl.combo(i)


def close(restart: bool = False) -> None:
    """Close the console mode (if initialised)."""
    if ctl is not None:
        ctl.close(restart)
