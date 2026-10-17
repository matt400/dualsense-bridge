"""Receiver: ustawienia zmieniane w oknie bez restartu (settings.json obok skryptu).

  mouse     czułość kursora i stabilizacja fizycznego kliknięcia touchpada (rx_mouse)
  master    który pad jest masterem, co wolno tylko jemu i akcje systemowe "PS + przycisk" (rx_master)
  ui        język okna
  lightbar  kolor lightbara, gdy gra nic nie ustawiła (per pad, wg MAC), miganie przy niskiej baterii i jej próg
            (ten sam próg ma ostrzeżenie na karcie pada) - rx_feedback, rx_lightbar
  wol       Wake-on-LAN z pada (PS + przycisk przy milczącym receiverze) - wysyłane do nadajnika (pakiet scfg)
  log       dziennik zdarzeń w oknie: poziom, limit linii, zdarzenia z nadajnika
  vpad      wirtualne pady: backend (HIDMaestro DualSense / ViGEmBus DS4), stała kolejność graczy (rx_ds4, rx_hm)

Brak pliku / błędna wartość -> domyślna (komunikat w logu, plik poprawiany przy zapisie). Zmiana z okna działa od
razu: apply() podmienia obiekt `cur` (jedno przypisanie - wątki sieci czytają zawsze spójną całość).
"""
from rx_i18n import _
import json
import os
import string
from dataclasses import asdict, dataclass, field, fields, replace

from proto import mac_bytes, mac_text, write_json
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
        try:
            known = v in options
        except TypeError:             # lista / obiekt z JSON w słowniku opcji (unhashable) - zła wartość, nie awaria
            known = False
        if not known:
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


# zakresy wspólne dla walidacji settings.json i suwaków w oknie ustawień (suwak węższy niż walidacja obcinał zapis)
MOUSE_LIMITS = {"sens": (0.05, 5.0), "click_freeze_ms": (0, 600), "click_rewind_ms": (0, 300), "drag_threshold": (0, 600)}
MouseConfig.V = {k: _num(lo, hi, float if k == "sens" else int) for k, (lo, hi) in MOUSE_LIMITS.items()}

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
    "triggers": ("Efekty spustów (opór, klik, wibracja)", "r2"),
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


# ---------------- lightbar ----------------
LIGHT_MODES = {"solid": "Stały kolor", "battery": "Kolor wg stanu baterii"}
DEFAULT_PAD = "*"                 # ustawienie lightbara dla padów bez własnego wpisu


@dataclass(frozen=True, slots=True)
class PadLight:
    mode: str = "solid"           # solid | battery - tylko gdy gra nie ustawiła koloru
    color: str = ""               # "#rrggbb" dla trybu solid; "" = idle_lightbar z receiver.toml
    blink: bool = True            # niska bateria: czerwone miganie (także nad kolorem z gry)


def _color(v):
    if v == "":
        return ""
    # dokładnie 6 cyfr szesnastkowych: int(..., 16) przepuszczał "#0x1234", "# 12345" i "#12_345", a potem
    # rx_lightbar.hex_rgb wywracał wątek feedbacku przy każdym odświeżeniu
    if not (isinstance(v, str) and len(v) == 7 and v[0] == "#" and all(c in string.hexdigits for c in v[1:])):
        raise ValueError("expected #rrggbb")
    return v.lower()


def _pads(v):
    """{"*" | MAC: {"mode", "color", "blink"}} -> {klucz: PadLight}; złe wpisy pomijane."""
    if not isinstance(v, dict):
        raise ValueError("expected {pad: {mode, color, blink}}")
    out = {}
    for k, raw in v.items():
        try:
            if not isinstance(raw, dict):
                raise ValueError("expected an object")
            out[_text(k)] = PadLight(mode=_choice(LIGHT_MODES)(raw.get("mode", "solid")), color=_color(raw.get("color", "")),
                                     blink=_bool(raw.get("blink", True)))
        except (ValueError, TypeError) as e:
            print(f"Settings: lightbar for {k!r} ignored ({e})")
    return out


@dataclass(frozen=True, slots=True)
class LightbarConfig:
    low_pct: int = 15             # próg niskiej baterii [%] - miganie i ostrzeżenie na karcie (startowo low_batt z toml)
    pads: dict = field(default_factory=dict)

    V = None

    def get(self, key):
        return self.pads.get(key) or self.pads.get(DEFAULT_PAD) or PadLight()


LOW_PCT = (5, 50)
LightbarConfig.V = {"low_pct": _num(*LOW_PCT), "pads": _pads}

# ---------------- Wake-on-LAN ----------------
WOL_BUTTONS = ("options", "share", "cross", "circle", "triangle", "square", "l1", "r1", "l3", "r3")
WOL_HOLD_MS = (300, 5000)


def _mac(v):
    if v == "":
        return ""
    if not isinstance(v, str) or mac_bytes(v) is None:
        raise ValueError("expected a MAC address aa:bb:cc:dd:ee:ff")
    return mac_text(mac_bytes(v))


def _ipv4(v):
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
    mac: str = ""                 # MAC karty sieciowej komputera; "" = nadajnik sam (tablica ARP telefonu)
    bcast: str = ""               # adres broadcast; "" = nadajnik sam (sieć do PC + 255.255.255.255)
    button: str = "options"       # PS + ten przycisk przytrzymane, gdy receiver nie odpowiada
    hold_ms: int = 1000

    V = None


WolConfig.V = {"enabled": _bool, "mac": _mac, "bcast": _ipv4, "button": _choice(WOL_BUTTONS), "hold_ms": _num(*WOL_HOLD_MS)}

# ---------------- dziennik zdarzeń ----------------
LOG_LEVELS = {"info": "Wszystko", "warn": "Ostrzeżenia i błędy", "error": "Tylko błędy"}
LOG_LINES = (50, 5000)


@dataclass(frozen=True, slots=True)
class LogConfig:
    level: str = "info"           # najniższy pokazywany poziom
    max_lines: int = 500          # tyle ostatnich linii w panelu
    sender: bool = False          # zdarzenia z nadajnika (pakiet event) - nadajnik wysyła je tylko, gdy to włączone

    V = None


LogConfig.V = {"level": _choice(LOG_LEVELS), "max_lines": _num(*LOG_LINES), "sender": _bool}


# ---------------- wirtualne pady ----------------
VPAD_BACKENDS = {"auto": "Automatycznie: HIDMaestro, gdy jest, inaczej ViGEmBus",
                 "hidmaestro": "HIDMaestro - DualSense (zapas: ViGEmBus)",
                 "vigem": "ViGEmBus - DualShock 4"}


def _path(v):
    if not isinstance(v, str):
        raise ValueError("expected a path")
    return v.strip()


@dataclass(frozen=True, slots=True)
class VpadConfig:
    backend: str = "auto"         # auto | hidmaestro | vigem
    fixed_slots: bool = True      # pad zastępczy w pustych niższych slotach: pad 2 nigdy nie zostaje graczem 1
    hm_dll: str = ""              # ścieżka do HIDMaestro.Core.dll (albo folderu); "" = pobrany sam / hidmaestro\ obok
    hm_auto: bool = True          # pobieraj i aktualizuj HIDMaestro z GitHuba (tylko bez własnej ścieżki hm_dll)

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
    """Słownik z JSON -> dataclass; brak klucza = wartość z `base`, zła wartość = wartość z `base` + komunikat."""
    values = {}
    for f in fields(cls):
        if f.name not in (raw or {}):
            continue
        try:
            values[f.name] = cls.V[f.name](raw[f.name])
        except (ValueError, TypeError) as e:       # TypeError: nieoczekiwany typ z ręcznej edycji - nie awaria startu
            print(f"Settings: bad {name}.{f.name} = {raw[f.name]!r} ({e}) - using {getattr(base, f.name)!r}")
    return replace(base, **values)


cur = Settings()
_listeners = []


def defaults(mouse_sens=0.5, low_batt=15):
    return Settings(MouseConfig(sens=mouse_sens), MasterConfig(),
                    lightbar=LightbarConfig(low_pct=min(LOW_PCT[1], max(LOW_PCT[0], int(low_batt)))))


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

    def sec(k):
        return raw.get(k) if isinstance(raw.get(k), dict) else {}

    return Settings(*(_section(type(getattr(base, f.name)), sec(f.name), getattr(base, f.name), f.name)
                      for f in fields(Settings)))


def save(s, path):
    """Zapis atomowy (plik tymczasowy + zamiana)."""
    data = {"version": 1, "mouse": asdict(s.mouse), "master": {**asdict(s.master), "only_master": list(s.master.only_master)},
            "ui": asdict(s.ui), "lightbar": asdict(s.lightbar), "wol": asdict(s.wol), "log": asdict(s.log),
            "vpad": asdict(s.vpad)}
    write_json(path, data, indent=2, ensure_ascii=False)


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
