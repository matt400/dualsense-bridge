"""Receiver: ustawienia zmieniane w oknie bez restartu (settings.json obok skryptu) - mysz touchpada i pad master.

  mouse   czułość kursora i stabilizacja fizycznego kliknięcia touchpada (rx_mouse)
  master  który pad jest masterem, co wolno tylko jemu i akcje systemowe "PS + przycisk" (rx_master)

Brak pliku / błędna wartość -> domyślna (komunikat w logu, plik poprawiany przy zapisie). Zmiana z okna działa od
razu: apply() podmienia obiekt `cur` (jedno przypisanie - wątki sieci czytają zawsze spójną całość).
"""
from rx_i18n import _
import json
import os
from dataclasses import asdict, dataclass, field, fields, replace

from rx_buttons import BUTTON_CODES

# ---------------- funkcje sprawdzające (wartość z JSON -> wartość albo ValueError) ----------------


def _num(lo, hi, kind=int):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"expected a number {lo}..{hi}")
        return kind(v)
    return check


def _choice(options):
    def check(v):
        if v not in options:
            raise ValueError(f"expected one of {tuple(options)}")
        return v
    return check


def _bool(v):
    if not isinstance(v, bool):
        raise ValueError("expected true / false")
    return v


def _text(v):
    if not isinstance(v, str):
        raise ValueError("expected text")
    return v.strip().lower()


# ---------------- mysz ----------------
@dataclass(frozen=True, slots=True)
class MouseConfig:
    sens: float = 0.5               # piksele na jednostkę touchpada (startowo z mouse_sens w receiver.toml)
    click_freeze_ms: int = 150      # po wciśnięciu / puszczeniu kliku kursor stoi tyle ms (palec "dociska" i przesuwa się)
    click_rewind_ms: int = 80       # ruch z ostatnich tylu ms przed klikiem jest cofany (kursor wraca tam, gdzie celowałeś)
    drag_threshold: int = 60        # przy trzymanym kliku kursor rusza (przeciąganie) dopiero po tylu jednostkach touchpada

    V = None                        # funkcje sprawdzające - ustawiane niżej (pole klasy, nie instancji)


MouseConfig.V = {"sens": _num(0.05, 5.0, float), "click_freeze_ms": _num(0, 600), "click_rewind_ms": _num(0, 300),
                 "drag_threshold": _num(0, 600)}

# ---------------- pad master ----------------
# co można zastrzec tylko dla mastera: klucz -> (opis w oknie, ikona z rx_qt_icons); kolejność = kolejność w oknie
MASTER_FEATURES = {
    "console": ("Tryb konsoli (ekran i dźwięk)", "tv"),   # opisy po polsku - w oknie przez rx_i18n._()
    "actions": ("Akcje systemowe (PS + przycisk)", "ps"),
    "ps_hold": ("Przytrzymanie PS: Game Bar / Big Picture", "ps"),
    "ps_game": ("PS w grze i w Steam (menu, nakładka)", "ps"),
    "screenshot": ("Zrzut ekranu (PS + Share)", "share"),
    "profile": ("Zmiana profilu (PS + ← / →)", "dpad_right"),
    "mouse": ("Touchpad jako mysz (PS + klik)", "mouse"),
    "sens": ("Czułość myszy (PS + ↑ / ↓)", "dpad_up"),
}
# akcje mastera: klucz -> (opis, wymaga przytrzymania HOLD_S - ochrona przed przypadkiem)
MASTER_ACTIONS = {
    "pause": ("Pauza gry (Esc)", False),
    "close_game": ("Zamknij grę", True),
    "sleep": ("Uśpij komputer", True),
    "shutdown": ("Wyłącz komputer", True),
}
ABSENT = {"all": "każdy pad może wszystko", "nobody": "zastrzeżone funkcje są wyłączone"}
# do pary z PS: bez przycisków skrótów PS (Share, D-pad) i R3 (PS + R3 = przejęcie mastera)
COMBO_BUTTONS = tuple(n for n in BUTTON_CODES if n not in ("ps", "share", "r3"))


def _features(v):
    if not isinstance(v, (list, tuple)):
        raise ValueError("expected a list")
    return tuple(k for k in MASTER_FEATURES if k in v)          # znane, bez powtórzeń, stała kolejność


def _actions(v):
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
    pad: str = ""                   # id pada (MAC jak w remap.json albo "slotN"); "" = brak mastera
    when_absent: str = "all"        # master nie jest podłączony: all | nobody
    only_master: tuple = ("console", "actions", "ps_hold")
    actions: dict = field(default_factory=lambda: {"pause": "options", "close_game": "triangle",
                                                   "sleep": "", "shutdown": ""})
    claim_hold: bool = True         # PS + R3 przytrzymane CLAIM_S s na padzie = ten pad zostaje masterem

    V = None


MasterConfig.V = {"pad": _text, "when_absent": _choice(ABSENT), "only_master": _features, "actions": _actions,
                  "claim_hold": _bool}


@dataclass(frozen=True, slots=True)
class UiConfig:
    lang: str = "pl"                # język okna: pl | en (zmiana po restarcie)

    V = None


UiConfig.V = {"lang": _choice(("pl", "en"))}


@dataclass(frozen=True, slots=True)
class Settings:
    mouse: MouseConfig = field(default_factory=MouseConfig)
    master: MasterConfig = field(default_factory=MasterConfig)
    ui: UiConfig = field(default_factory=UiConfig)


def _section(cls, raw, base, name):
    """Słownik z JSON -> dataclass; brak klucza = wartość z `base`, zła wartość = wartość z `base` + komunikat."""
    values = {}
    for f in fields(cls):
        if f.name not in (raw or {}):
            continue
        try:
            values[f.name] = cls.V[f.name](raw[f.name])
        except ValueError as e:
            print(f"Settings: bad {name}.{f.name} = {raw[f.name]!r} ({e}) - using {getattr(base, f.name)!r}")
    return replace(base, **values)


cur = Settings()
_listeners = []


def defaults(mouse_sens=0.5):
    return Settings(MouseConfig(sens=mouse_sens), MasterConfig())


def load(path, base=None):
    """settings.json -> Settings (brak pliku / uszkodzony plik = `base`, domyślnie defaults())."""
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
    sec = lambda k: raw.get(k) if isinstance(raw.get(k), dict) else {}
    return Settings(_section(MouseConfig, sec("mouse"), base.mouse, "mouse"),
                    _section(MasterConfig, sec("master"), base.master, "master"),
                    _section(UiConfig, sec("ui"), base.ui, "ui"))


def save(s, path):
    """Zapis atomowy (plik tymczasowy + zamiana)."""
    tmp = path + ".tmp"
    data = {"version": 1, "mouse": asdict(s.mouse), "master": {**asdict(s.master), "only_master": list(s.master.only_master)},
            "ui": asdict(s.ui)}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def on_change(fn):
    """fn(stare, nowe) po każdym apply() - np. rx_mouse ustawia czułość wszystkich padów."""
    _listeners.append(fn)


def apply(s):
    global cur
    old, cur = cur, s
    for fn in _listeners:
        try:
            fn(old, s)
        except Exception as e:
            print(f"Settings listener error: {e!r}")


def update(path=None, **sections):
    """Zmiana części ustawień (np. update(master=...)) z natychmiastowym działaniem i zapisem, gdy podano path.
    -> (ok, komunikat)."""
    s = replace(cur, **sections)
    apply(s)
    if path:
        try:
            save(s, path)
        except OSError as e:
            return False, _("Nie udało się zapisać {path}: {err}").format(path=os.path.basename(path), err=e)
    return True, _("Zapisano")
