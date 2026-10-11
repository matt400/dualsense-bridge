"""Receiver: tabele przycisków, remapowanie wejść i zapis profili (remap.json).

Remapować można 17 "wejść": 11 przycisków, 4 kierunki D-pada i spusty L2/R2. Mapa to słownik
{wejście_źródłowe: wejście_docelowe | None}; brak wpisu = bez zmian, None = wyłączone.
Spust -> przycisk działa progowo (TRIG_ON), przycisk -> spust daje pełne 255.
PS + klik touchpada (przełączanie myszy) używa SUROWYCH wejść, więc nie da się go "zremapować away".

Gałki: profil["sticks"] = {wyjście: {"src": "left"|"right"|"none", "inv_x": bool, "inv_y": bool}}, gdzie wyjście to
"left" / "right" (gałka widziana przez grę), a src - z której fizycznej gałki bierze dane ("none" = środek).
Profil = {"buttons", "sticks", "tuning", "gyro"} (tuning i gyro: rx_shape.py); stary format (sama mapa przycisków)
jest wczytywany jak "buttons".

Profile gier: klucz "<pad>@<gra.exe>" (np. "aa:bb:cc:dd:ee:ff@eldenring.exe") - używany automatycznie, gdy ta gra
jest na pierwszym planie. Profil gry istnieje nawet pusty (= "w tej grze bez zmian"), w odróżnieniu od profilu pada.
"""
from rx_i18n import _
import json
import os
import threading

from proto import PAD_KEYS
from rx_shape import normalize_gyro, normalize_tuning

BIT = {c: 1 << k for k, c in enumerate(PAD_KEYS)}
MASK_ALL = (1 << len(PAD_KEYS)) - 1
PS_BIT, L1_BIT, R1_BIT = BIT[316], BIT[310], BIT[311]

BUTTON_CODES = {"cross": 304, "circle": 305, "triangle": 307, "square": 308,
                "l1": 310, "r1": 311, "share": 314, "options": 315, "ps": 316, "l3": 317, "r3": 318}
DIRS = {"dpad_up": (0, -1), "dpad_down": (0, 1), "dpad_left": (-1, 0), "dpad_right": (1, 0)}   # (hx, hy)
TRIGGERS = ("l2", "r2")
INPUTS = tuple(BUTTON_CODES) + tuple(DIRS) + TRIGGERS
# nazwy wejść po polsku - w oknie zawsze przez rx_i18n._() (wersja angielska w rx_i18n.EN)
LABELS = {"cross": "Krzyżyk (X)", "circle": "Kółko (O)", "triangle": "Trójkąt", "square": "Kwadrat",
          "l1": "L1", "r1": "R1", "share": "Share / Create", "options": "Options", "ps": "Przycisk PS",
          "l3": "L3 (gałka)", "r3": "R3 (gałka)",
          "dpad_up": "D-pad góra", "dpad_down": "D-pad dół", "dpad_left": "D-pad lewo", "dpad_right": "D-pad prawo",
          "l2": "L2 (spust)", "r2": "R2 (spust)"}
TRIG_ON = 40   # od tej wartości analogowej spust liczy się jako wciśnięty przycisk


def bits_from(mask, *tables):
    """Dla maski przycisków składa bajty raportu z tabel {kod_evdev: bit}."""
    out = [0] * len(tables)
    for code, bit in BIT.items():
        if mask & bit:
            for n, t in enumerate(tables):
                out[n] |= t.get(code, 0)
    return tuple(out)


def normalize(m):
    """Czyści mapę: tylko znane nazwy, bez wpisów tożsamościowych."""
    out = {}
    for src, dst in (m or {}).items():
        if src not in INPUTS:
            continue
        if dst in (None, "", "none"):
            out[src] = None
        elif dst in INPUTS and dst != src:
            out[src] = dst
    return out


def pressed_inputs(mask, hx, hy, lt, rt):
    """Zbiór wejść aktualnie aktywnych (dla podświetlenia w oknie remapowania)."""
    out = {n for n, code in BUTTON_CODES.items() if mask & BIT[code]}
    if hy < 0:
        out.add("dpad_up")
    if hy > 0:
        out.add("dpad_down")
    if hx < 0:
        out.add("dpad_left")
    if hx > 0:
        out.add("dpad_right")
    if lt >= TRIG_ON:
        out.add("l2")
    if rt >= TRIG_ON:
        out.add("r2")
    return out


def apply_remap(m, mask, hx, hy, lt, rt):
    """(maska, hatx, haty, l2, r2) surowe -> po remapowaniu. m = znormalizowana mapa."""
    if not m:
        return mask, hx, hy, lt, rt
    out_mask = 0
    d_up = d_down = d_left = d_right = 0
    olt = ort = 0
    for src in INPUTS:
        if src in BUTTON_CODES:
            v = 255 if mask & BIT[BUTTON_CODES[src]] else 0
        elif src in DIRS:
            dx, dy = DIRS[src]
            v = 255 if ((hx == dx) if dx else (hy == dy)) else 0
        else:
            v = lt if src == "l2" else rt
        if not v:
            continue
        dst = m.get(src, src)
        if dst is None:
            continue
        if dst in BUTTON_CODES:
            if v >= TRIG_ON:
                out_mask |= BIT[BUTTON_CODES[dst]]
        elif dst in DIRS:
            if v >= TRIG_ON:
                if dst == "dpad_up":
                    d_up = 1
                elif dst == "dpad_down":
                    d_down = 1
                elif dst == "dpad_left":
                    d_left = 1
                else:
                    d_right = 1
        elif dst == "l2":
            olt = max(olt, v)
        else:
            ort = max(ort, v)
    return out_mask, d_right - d_left, d_down - d_up, olt, ort


STICKS = ("left", "right")


def normalize_sticks(sm):
    """Czyści ustawienia gałek: zostają tylko odstępstwa od domyślnych (src = to samo wyjście, bez inwersji)."""
    out = {}
    for name in STICKS:
        e = (sm or {}).get(name)
        if not isinstance(e, dict):
            continue
        src = e.get("src", name)
        if src not in STICKS and src != "none":
            src = name
        ne = {"src": src, "inv_x": bool(e.get("inv_x")), "inv_y": bool(e.get("inv_y"))}
        if ne != {"src": name, "inv_x": False, "inv_y": False}:
            out[name] = ne
    return out


PROFILE_PARTS = ("buttons", "sticks", "tuning", "gyro")
HOLD_INPUTS = ("always",) + INPUTS      # czym można aktywować gyro


def normalize_profile(p):
    """-> {"buttons", "sticks", "tuning", "gyro"}; akceptuje też stary, płaski format (same przyciski)."""
    if not isinstance(p, dict):
        p = {}
    elif not any(k in p for k in PROFILE_PARTS):
        p = {"buttons": p}
    return {"buttons": normalize(p.get("buttons")), "sticks": normalize_sticks(p.get("sticks")),
            "tuning": normalize_tuning(p.get("tuning")), "gyro": normalize_gyro(p.get("gyro"), HOLD_INPUTS)}


def profile_size(p):
    """Liczba zmian w profilu (0 = wszystko domyślne)."""
    return len(p["buttons"]) + len(p["sticks"]) + len(p["tuning"]) + (1 if p["gyro"] else 0)


def is_game_key(key):
    return "@" in key


def _inv(v):
    return min(255, 256 - v)   # 0<->255, środek 128 zostaje 128


def apply_sticks(sm, lx, ly, rx, ry):
    """Surowe wychylenia (0..255, środek 128) -> (lx, ly, rx, ry) po remapowaniu gałek."""
    if not sm:
        return lx, ly, rx, ry
    phys = {"left": (lx, ly), "right": (rx, ry), "none": (128, 128)}
    out = []
    for name in STICKS:
        e = sm.get(name)
        if e is None:
            out.extend(phys[name])
            continue
        x, y = phys[e["src"]]
        out.append(_inv(x) if e["inv_x"] else x)
        out.append(_inv(y) if e["inv_y"] else y)
    return tuple(out)


class RemapStore:
    """Profile remapowania: `saved` (z pliku) i `live` (aktualnie używane, mogą być niezapisane).

    Klucz profilu = identyfikator pada (MAC z nadajnika, małe litery) albo "slotN", gdy nadajnik
    go nie przysłał (stara wersja)."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self.saved = {}
        self.live = {}
        self.load()

    @staticmethod
    def key_for(pad_id, slot):
        return pad_id or f"slot{slot}"

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            print(f"Remap file {self.path}: cannot read ({e}) - starting with no remaps")
            return
        profiles = data.get("profiles", {}) if isinstance(data, dict) else {}
        with self._lock:
            self.saved = {}
            for k, v in profiles.items():
                k = str(k) if str(k).startswith("slot") else str(k).lower()
                p = normalize_profile(v)
                if profile_size(p) or is_game_key(k):
                    self.saved[k] = p
            self.live = {}
        print(f"Remap profiles loaded from {self.path}: {sorted(self.saved) or 'none'}")

    def get(self, key):
        """Profil w użyciu (live, a jeśli go nie ma - zapisany). Zwraca kopię."""
        with self._lock:
            p = self.live.get(key)
            if p is None:
                p = self.saved.get(key)
            return normalize_profile(p)

    def has(self, key):
        with self._lock:
            return key in self.live or key in self.saved

    def keys_for(self, base):
        """[profil pada, profile gier tego pada (alfabetycznie)]."""
        prefix = base + "@"
        with self._lock:
            games = {k for k in self.live.keys() | self.saved.keys() if k.startswith(prefix)}
        return [base, *sorted(games)]

    def in_file(self, key):
        with self._lock:
            return key in self.saved

    def get_saved(self, key):
        with self._lock:
            return normalize_profile(self.saved.get(key))

    def set_live(self, key, p):
        with self._lock:
            self.live[key] = normalize_profile(p)

    def save(self, key, p):
        """Zapisuje profil do pliku (atomowo). -> (ok, komunikat)."""
        p = normalize_profile(p)
        with self._lock:
            self.live[key] = p
            if profile_size(p) or is_game_key(key):
                self.saved[key] = p
            else:
                self.saved.pop(key, None)
            return self._write()

    def delete(self, key):
        """Usuwa profil (z pamięci i z pliku). -> (ok, komunikat)."""
        with self._lock:
            self.live.pop(key, None)
            if self.saved.pop(key, None) is None:
                return True, _("Usunięto")
            return self._write("Usunięto - {file}")

    def _write(self, done="Zapisano - {file}"):
        """Zapis atomowy (plik tymczasowy + zamiana) - przerwanie w trakcie nie psuje remap.json. Wołać pod _lock."""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 3, "profiles": self.saved}, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as e:
            return False, _("Zapis nieudany: {err}").format(err=e)
        return True, _(done).format(file=os.path.basename(self.path))
