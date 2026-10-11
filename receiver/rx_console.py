"""Receiver: tryb konsoli - jedno podwójne kliknięcie przycisku pada (domyślnie mute) przełącza ekran i dźwięk
między monitorem a TV.

  ON   ekran tylko TV (DisplaySwitch /external), na TV tv_width x tv_height i skalowanie tv_scale % (rx_display),
       dźwięk audio_tv, toast "Tryb konsoli włączony"
  OFF  ekran extend (DisplaySwitch /extend: oba ekrany), dźwięk audio_pc, toast "Tryb konsoli wyłączony"
  auto-OFF  brak pakietów z pada przez timeout_s -> samo OFF (oba ekrany aktywne, nie zostajesz bez obrazu)
  start     startup_mode: extend (oba ekrany) albo internal (tylko monitor) + dźwięk audio_pc

Ustawienia: console.json obok skryptu (brak pliku / błędna wartość -> domyślne, plik jest tworzony). Edycja w oknie:
rx_qt_console.py. Przyciski pada nadal trafiają do gry - wykrywanie podwójnego kliknięcia tylko je obserwuje.
Naciśnięcie razem z innym wejściem (skrót: PS + Share, PS + d-pad, PS + klik touchpada) nie jest kliknięciem.

Wątek sieci woła tylko on_state() (porównanie bitu i czasu). Polecenia ekranu / dźwięku / toast wykonuje jeden wątek
roboczy z kolejki, więc nic nie blokuje odbioru UDP, a kolejne przełączenia nie wyprzedzają poprzednich.
Błąd któregokolwiek kroku jest logowany i nie przerywa pozostałych.
"""
import json
import os
import queue
import subprocess
import time
import traceback
from dataclasses import asdict, dataclass, fields

import rx_audio
import rx_display
import rx_master as master
import rx_state as S
from rx_buttons import BIT, BUTTON_CODES, DIRS, INPUTS, TRIG_ON, pressed_inputs
from rx_config import C

STARTUP_MODES = ("extend", "internal")
CONFIG_VERSION = 2
TEST_S = 10.0              # "Przetestuj": tyle sekund w trybie ON, potem samo OFF (extend, monitor nie zostaje czarny)
APP_NAME = "DualSense bridge"
DISPLAY_ARG = {"extend": "/extend", "internal": "/internal", "tv": "/external"}
_STOP = object()


def _number(lo, hi):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"expected a number {lo}..{hi}")
        return int(v)
    return check


def _choice(options):
    def check(v):
        if v not in options:
            raise ValueError(f"expected one of {tuple(options)}")
        return v
    return check


def _text(v):
    if not isinstance(v, str):
        raise ValueError("expected text")
    return v.strip()


@dataclass(frozen=True, slots=True)
class ConsoleConfig:
    toggle_button: str = "mute"     # przycisk pada (nazwa jak w remapowaniu: "mute", "share", "cross", "dpad_up"...);
                                    # nie "ps": podwójne PS otwiera też Steam Big Picture
    double_click_ms: int = 400      # okno między dwoma naciśnięciami
    audio_tv: str = ""              # nazwa urządzenia audio TV; puste = nie zmieniaj dźwięku
    audio_pc: str = ""              # nazwa urządzenia audio PC; puste = nie zmieniaj dźwięku
    timeout_s: int = 60             # cisza z pada dłuższa niż tyle = auto-OFF
    startup_mode: str = "extend"    # extend | internal
    tv_width: int = 2560            # rozdzielczość TV po włączeniu (0 = bez zmiany)
    tv_height: int = 1440
    tv_scale: int = 200             # skalowanie TV w % (0 = bez zmiany)
    version: int = CONFIG_VERSION   # wersja pliku (migracje)

    @classmethod
    def from_dict(cls, raw):
        """-> (config, poprawiono): zła albo brakująca wartość = domyślna (poprawiono=True, wtedy warto zapisać plik)."""
        raw = dict(raw) if isinstance(raw, dict) else {}
        fixed = not raw
        if raw and raw.get("version") != CONFIG_VERSION:
            fixed = True                    # plik sprzed wersji 2: PS jako przycisk trybu konsoli -> mute (kolizja z Big Picture)
            raw["version"] = CONFIG_VERSION
            if raw.get("toggle_button") == "ps":
                print("Console: toggle button ps -> mute (double PS also opens Steam Big Picture)")
                raw["toggle_button"] = "mute"
        values = {}
        for f in fields(cls):
            if f.name not in raw:
                fixed = True
                continue
            try:
                values[f.name] = _VALIDATORS[f.name](raw[f.name])
            except ValueError as e:
                print(f"Console: bad {f.name} = {raw[f.name]!r} ({e}) - using default {f.default!r}")
                fixed = True
        return cls(**values), fixed


_VALIDATORS = {
    "toggle_button": _choice(INPUTS),
    "double_click_ms": _number(100, 2000),
    "audio_tv": _text,
    "audio_pc": _text,
    "timeout_s": _number(5, 3600),
    "startup_mode": _choice(STARTUP_MODES),
    "tv_width": _number(0, 15360),
    "tv_height": _number(0, 8640),
    "tv_scale": lambda v: int(_choice((0, *rx_display.DPI_VALUES))(v)),
    "version": _number(1, CONFIG_VERSION),
}


def load(path):
    """Wczytuje console.json; brak pliku, uszkodzony plik albo błędne wartości -> domyślne i zapis poprawionego pliku."""
    raw = None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"Console: cannot read {path} ({e!r}) - using defaults (old file kept as .bad)")
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
    cfg, fixed = ConsoleConfig.from_dict(raw)
    if fixed:
        try:
            save(cfg, path)
        except OSError as e:
            print(f"Console: cannot write {path}: {e!r}")
    return cfg


def save(cfg, path):
    """Zapis atomowy (plik tymczasowy + zamiana): przerwany zapis nie zostawi pustego console.json."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def detector(name):
    """Funkcja (maska, hx, hy, lt, rt) -> bool: czy wejście o tej nazwie jest wciśnięte (na surowych wejściach pada)."""
    if name in BUTTON_CODES:
        bit = BIT[BUTTON_CODES[name]]
        return lambda mask, hx, hy, lt, rt: bool(mask & bit)
    if name in DIRS:
        dx, dy = DIRS[name]
        return (lambda mask, hx, hy, lt, rt: hx == dx) if dx else (lambda mask, hx, hy, lt, rt: hy == dy)
    return (lambda mask, hx, hy, lt, rt: lt >= TRIG_ON) if name == "l2" else (lambda mask, hx, hy, lt, rt: rt >= TRIG_ON)


# ---------------- polecenia systemowe (każde osobno, żeby testy mogły je podmienić) ----------------
def _display(mode):
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "DisplaySwitch.exe")
    subprocess.run([exe, DISPLAY_ARG[mode]], timeout=15, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _tv_mode(w, h, scale):
    print(f"Console: TV {rx_display.apply_tv(w, h, scale)}")


def _audio(name):
    rx_audio.set_default(name)


def _toast(title, msg=""):
    from winotify import Notification
    Notification(app_id=APP_NAME, title=title, msg=msg, duration="short").show()


def _step(what, fn, *args):
    """Krok, który może się nie udać (brak DisplaySwitch / pycaw / winotify, nieobecne urządzenie): log i dalej."""
    try:
        fn(*args)
    except Exception as e:
        print(f"Console: {what} failed: {e!r}")


class Console:
    def __init__(self, cfg):
        self.cfg = cfg
        self.on = False
        self._jobs = queue.SimpleQueue()
        self._since = time.monotonic()      # kiedy ostatnio zmienił się tryb (auto-OFF liczy od późniejszego z: pakiet, zmiana)
        self._revert = None                 # (kiedy, cfg) - powrót do OFF po "Przetestuj"
        self._detect = detector(cfg.toggle_button)
        self._down = [False] * C.NUM_PADS
        self._last_click = [0.0] * C.NUM_PADS
        self._capture = None                # callback wywołany z nazwą następnego wciśniętego przycisku
        self._cap_prev = []
        self._thread = None

    # ---------- wątek sieci ----------
    def on_state(self, i, mask, hx, hy, lt, rt):
        """Wołane przy każdej zmianie surowego stanu pada. Tylko porównania - żadnych poleceń systemowych."""
        down = self._detect(mask, hx, hy, lt, rt)
        rising = down and not self._down[i]
        self._down[i] = down
        if (cb := self._capture) is not None:
            self._feed_capture(i, cb, mask, hx, hy, lt, rt)
            return                          # naciśnięcie przy wyborze przycisku nie liczy się jako kliknięcie
        if down and pressed_inputs(mask, hx, hy, lt, rt) - {self.cfg.toggle_button}:
            self.combo(i)                   # skrót (np. PS + Share, PS + d-pad) to nie kliknięcie
            return
        if not rising:
            return
        now = time.monotonic()
        if now - self._last_click[i] <= self.cfg.double_click_ms / 1000:
            self._last_click[i] = 0.0       # trzecie kliknięcie zaczyna od nowa, nie przełącza drugi raz
            if master.check(i, "console"):  # może być zastrzeżone dla pada master
                self._jobs.put(self._toggle)
        else:
            self._last_click[i] = now

    def combo(self, i):
        """Przycisk trybu użyty razem z innym wejściem (także klikiem touchpada, który nie jest w masce): to naciśnięcie
        nie liczy się jako kliknięcie i przerywa serię - dwa skróty z PS pod rząd nie przełączają ekranu."""
        if self._down[i]:
            self._last_click[i] = 0.0

    def _feed_capture(self, i, cb, *inputs):
        cur = pressed_inputs(*inputs)
        new, self._cap_prev[i] = cur - self._cap_prev[i], cur
        if new:
            self._capture = None
            cb(next(n for n in INPUTS if n in new))

    def arm_capture(self, cb):
        """Następny nowo wciśnięty przycisk (nie ten trzymany teraz) trafi do cb(nazwa) - z wątku sieci."""
        self._cap_prev = [pressed_inputs(*s["raw"]) for s in S.states]
        self._capture = cb

    def cancel_capture(self):
        self._capture = None

    # ---------- wątek roboczy ----------
    def start(self):
        self._thread = S.start_thread("console", self._run)
        self._jobs.put(self._startup)

    def _run(self):
        while True:
            try:
                job = self._jobs.get(timeout=1.0)
            except queue.Empty:
                job = self._tick
            if job is _STOP:
                return
            try:
                job()
            except Exception:
                print(f"Console: job error:\n{traceback.format_exc()}")

    def _tick(self):
        now = time.monotonic()
        if self._revert and now >= self._revert[0]:
            self._apply(False, self._revert[1], "koniec testu")
        elif self.on and now - max(self._since, max(S.last_seen, default=0.0)) > self.cfg.timeout_s:
            print(f"Console: no packets from the pad for {self.cfg.timeout_s}s - auto-OFF")
            self._apply(False, why=f"Brak sygnału z pada przez {self.cfg.timeout_s} s")

    def _startup(self):
        _step("display", _display, self.cfg.startup_mode)
        if self.cfg.audio_pc:
            _step("audio", _audio, self.cfg.audio_pc)
        print(f"Console: startup mode {self.cfg.startup_mode}")

    def _toggle(self):
        self._apply(not self.on)

    def _apply(self, on, cfg=None, why="", toast=True):
        cfg = cfg or self.cfg
        self.on, self._since, self._revert = on, time.monotonic(), None
        _step("display", _display, "tv" if on else "extend")
        if on and (cfg.tv_width and cfg.tv_height or cfg.tv_scale):
            _step("TV resolution / scaling", _tv_mode, cfg.tv_width, cfg.tv_height, cfg.tv_scale)
        if audio := cfg.audio_tv if on else cfg.audio_pc:
            _step("audio", _audio, audio)
        print(f"Console: {'ON' if on else 'OFF'}" + (f" ({why})" if why else ""))
        if toast:
            _step("toast", _toast, "Tryb konsoli włączony" if on else "Tryb konsoli wyłączony", why)

    def _test(self, cfg):
        self._apply(True, cfg)
        self._revert = (time.monotonic() + TEST_S, cfg)

    # ---------- dla okna ustawień ----------
    def test(self, cfg):
        """ON z podanymi (jeszcze niezapisanymi) ustawieniami, po TEST_S sekund OFF z tymi samymi."""
        self._jobs.put(lambda: self._test(cfg))

    def close(self, restart=False):
        """Zamykanie receivera. Przy zwykłym wyjściu z włączonym trybem konsoli wracamy do extend (po restarcie
        sam start ustawi ekran wg startup_mode, więc dwa razy nie przełączamy)."""
        if self.on and not restart:
            self._jobs.put(lambda: self._apply(False, why="zamknięcie receivera", toast=False))
        self._jobs.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=10)


ctl = None


def init():
    """Wczytuje console.json i startuje wątek roboczy (ustawia ekran i dźwięk wg startup_mode)."""
    global ctl
    ctl = Console(load(C.CONSOLE_PATH))
    ctl.start()
    print(f"Console mode: double-click {ctl.cfg.toggle_button} within {ctl.cfg.double_click_ms} ms, "
          f"auto-OFF after {ctl.cfg.timeout_s}s ({C.CONSOLE_PATH})")


def on_state(i, mask, hx, hy, lt, rt):
    if ctl is not None:
        ctl.on_state(i, mask, hx, hy, lt, rt)


def on_touch_click(i):
    """Klik touchpada (pakiet T_CLICK) - przy trzymanym przycisku trybu to skrót, a nie kliknięcie."""
    if ctl is not None:
        ctl.combo(i)


def close(restart=False):
    if ctl is not None:
        ctl.close(restart)
