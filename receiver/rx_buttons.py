"""Receiver: button tables, input remapping and saving profiles (remap.json).

17 "inputs" can be remapped: 11 buttons, 4 D-pad directions and the L2 / R2 triggers. A map is a dict
{source_input: target_input | None}; no entry = no change, None = disabled.
Trigger -> button works with a threshold (TRIG_ON), button -> trigger gives the full 255.
PS + touchpad click (mouse switching) uses RAW inputs, so it cannot be "remapped away".

Sticks: profile["sticks"] = {output: {"src": "left"|"right"|"none", "inv_x": bool, "inv_y": bool}}, where the output is
"left" / "right" (the stick as the game sees it), and src - which physical stick supplies the data ("none" = centre).
A profile = {"buttons", "sticks", "tuning", "gyro", "triggers"} (tuning, gyro and trigger resistance: rx_shape.py); the old format
(a bare button map) is loaded as "buttons".

Game profiles: the key "<pad>@<game.exe>" (e.g. "aa:bb:cc:dd:ee:ff@eldenring.exe") - used automatically when that game
is in the foreground. A game profile exists even when empty (= "no changes in this game"), unlike a pad profile.
"""
from rx_i18n import _
import json
import os
import threading

from proto import PAD_KEYS, write_json
from rx_shape import _byte, _unit, normalize_gyro, normalize_triggers, normalize_tuning

BIT = {c: 1 << k for k, c in enumerate(PAD_KEYS)}
MASK_ALL = (1 << len(PAD_KEYS)) - 1
PS_BIT = BIT[316]

BUTTON_CODES = {"cross": 304, "circle": 305, "triangle": 307, "square": 308,
                "l1": 310, "r1": 311, "share": 314, "options": 315, "ps": 316, "l3": 317, "r3": 318}
DIRS = {"dpad_up": (0, -1), "dpad_down": (0, 1), "dpad_left": (-1, 0), "dpad_right": (1, 0)}   # (hx, hy)
TRIGGERS = ("l2", "r2")
INPUTS = tuple(BUTTON_CODES) + tuple(DIRS) + TRIGGERS
# input names in Polish - in the window always through rx_i18n._() (the English version in rx_i18n.EN)
LABELS = {"cross": "Krzyżyk (X)", "circle": "Kółko (O)", "triangle": "Trójkąt", "square": "Kwadrat",
          "l1": "L1", "r1": "R1", "share": "Share / Create", "options": "Options", "ps": "Przycisk PS",
          "l3": "L3 (gałka)", "r3": "R3 (gałka)",
          "dpad_up": "D-pad góra", "dpad_down": "D-pad dół", "dpad_left": "D-pad lewo", "dpad_right": "D-pad prawo",
          "l2": "L2 (spust)", "r2": "R2 (spust)"}
TRIG_ON = 40   # from this analog value a trigger counts as a pressed button


def bits_from(mask, *tables):
    """For a button mask, assemble report bytes from tables {evdev_code: bit}."""
    out = [0] * len(tables)
    for code, bit in BIT.items():
        if mask & bit:
            for n, t in enumerate(tables):
                out[n] |= t.get(code, 0)
    return tuple(out)


def normalize(m):
    """Clean a map: known names only, no identity entries."""
    out = {}
    for src, dst in (m.items() if isinstance(m, dict) else ()):    # a wrong type (hand edit of remap.json, API) = an empty map
        if src not in INPUTS:
            continue
        if dst in (None, "", "none"):
            out[src] = None
        elif dst in INPUTS and dst != src:
            out[src] = dst
    return out


def pressed_inputs(mask, hx, hy, lt, rt):
    """The set of currently active inputs (for highlighting in the remap window)."""
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
    """(mask, hatx, haty, l2, r2) raw -> after remapping. m = a normalised map."""
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
    """Clean the stick settings: only deviations from the defaults remain (src = the same output, no inversion)."""
    out = {}
    sm = sm if isinstance(sm, dict) else {}
    for name in STICKS:
        e = sm.get(name)
        if not isinstance(e, dict):
            continue
        src = e.get("src", name)
        if src not in STICKS and src != "none":
            src = name
        ne = {"src": src, "inv_x": bool(e.get("inv_x")), "inv_y": bool(e.get("inv_y"))}
        if ne != {"src": name, "inv_x": False, "inv_y": False}:
            out[name] = ne
    return out


PROFILE_PARTS = ("buttons", "sticks", "tuning", "gyro", "triggers")
HOLD_INPUTS = ("always", *INPUTS)      # what can activate the gyro


def normalize_profile(p):
    """-> {"buttons", "sticks", "tuning", "gyro", "triggers"}; also accepts the old flat format (buttons only)."""
    if not isinstance(p, dict):
        p = {}
    elif not any(k in p for k in PROFILE_PARTS):
        p = {"buttons": p}
    return {"buttons": normalize(p.get("buttons")), "sticks": normalize_sticks(p.get("sticks")),
            "tuning": normalize_tuning(p.get("tuning")), "gyro": normalize_gyro(p.get("gyro"), HOLD_INPUTS),
            "triggers": normalize_triggers(p.get("triggers"))}


def profile_size(p):
    """The number of changes in a profile (0 = everything default)."""
    return len(p["buttons"]) + len(p["sticks"]) + len(p["tuning"]) + (1 if p["gyro"] else 0) + len(p["triggers"])


def is_game_key(key):
    """Whether a profile key belongs to a game ("<pad>@<game.exe>") rather than to a pad."""
    return "@" in key


def _inv(v):
    """Mirror an axis around the centre 128: 0 <-> 255, 128 stays 128 (like _unit/_byte in rx_shape - a full deflection
    in both directions gives a full deflection, not 1).
    """
    return _byte(-_unit(v))


def apply_sticks(sm, lx, ly, rx, ry):
    """Raw deflections (0..255, centre 128) -> (lx, ly, rx, ry) after stick remapping."""
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
    """Remap profiles: `saved` (from the file) and `live` (currently used, may be unsaved).

    A profile key = the pad identifier (the MAC from the sender, lower case) or "slotN" when the sender
    did not send it (an old version).
    """

    def __init__(self, path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.saved = {}
        self.live = {}
        self.load()

    @staticmethod
    def key_for(pad_id, slot):
        """The profile key of a pad: its id, or "slotN" when the sender has not sent one."""
        return pad_id or f"slot{slot}"

    def load(self) -> None:
        """Load remap.json (a missing or broken file = no remaps), normalising every profile; resets the live profiles."""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            print(f"Remap file {self.path}: cannot read ({e}) - starting with no remaps")
            return
        profiles = data.get("profiles", {}) if isinstance(data, dict) else {}
        if not isinstance(profiles, dict):
            print(f"Remap file {self.path}: 'profiles' is not an object - starting with no remaps")
            profiles = {}
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
        """The profile in use (live, and when there is none - the saved one). Returns a copy."""
        with self._lock:
            p = self.live.get(key)
            if p is None:
                p = self.saved.get(key)
            return normalize_profile(p)

    def has(self, key):
        """Whether a profile exists, live or saved."""
        with self._lock:
            return key in self.live or key in self.saved

    def keys_for(self, base):
        """[the pad profile, the game profiles of this pad (alphabetically)]."""
        prefix = base + "@"
        with self._lock:
            games = {k for k in self.live.keys() | self.saved.keys() if k.startswith(prefix)}
        return [base, *sorted(games)]

    def in_file(self, key):
        """Whether a profile is saved in the file."""
        with self._lock:
            return key in self.saved

    def get_saved(self, key):
        """A copy of the saved version of a profile (an empty profile when it is not saved)."""
        with self._lock:
            return normalize_profile(self.saved.get(key))

    def set_live(self, key, p) -> None:
        """Set the live (unsaved) version of a profile."""
        with self._lock:
            self.live[key] = normalize_profile(p)

    def save(self, key, p):
        """Save the profile to the file (atomically). -> (ok, message)."""
        p = normalize_profile(p)
        with self._lock:
            self.live[key] = p
            if profile_size(p) or is_game_key(key):
                self.saved[key] = p
            else:
                self.saved.pop(key, None)
            return self._write()

    def delete(self, key):
        """Delete a profile (from memory and from the file). -> (ok, message)."""
        with self._lock:
            self.live.pop(key, None)
            if self.saved.pop(key, None) is None:
                return True, _("Usunięto")
            return self._write("Usunięto - {file}")

    def _write(self, done="Zapisano - {file}"):
        """Atomic write (temporary file + replace) - an interruption does not corrupt remap.json. Call with _lock held."""
        try:
            write_json(self.path, {"version": 3, "profiles": self.saved}, indent=2, sort_keys=True)
        except OSError as e:
            return False, _("Zapis nieudany: {err}").format(err=e)
        return True, _(done).format(file=os.path.basename(self.path))
