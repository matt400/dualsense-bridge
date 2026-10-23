"""Receiver: the profile editing model for the window (no Qt): choosing a profile, changes of buttons / sticks / gyro, saving.

Changes take effect live (the window calls apply(save=False) after a short debounce), saving to remap.json is separate.
A profile is {"buttons", "sticks", "tuning", "gyro", "triggers"} - as in rx_buttons.normalize_profile.
"""
from rx_i18n import _
import copy

import rx_buttons as B
import rx_profiles as P
import rx_state as S
from rx_shape import GYRO_DEF, RUMBLE_DEF, STICK_DEF, TRIG_DEF, TRIG_FX_DEF, TRIG_PARAMS

SHORT = {"cross": "✕", "circle": "○", "triangle": "△", "square": "□", "l1": "L1", "r1": "R1", "share": "SHARE",
         "options": "OPT", "ps": "PS", "l3": "L3", "r3": "R3", "dpad_up": "↑", "dpad_down": "↓", "dpad_left": "←",
         "dpad_right": "→", "l2": "L2", "r2": "R2"}
STICK_NAME = {"left": "Lewa gałka", "right": "Prawa gałka", "none": "Wyłączona (środek)"}   # translated through _() in the window
STICK_OF = {"left_stick": "left", "right_stick": "right"}     # drawing part -> stick output
L3_OF = {"left_stick": "l3", "right_stick": "r3"}             # stick click
GYRO_MODES = {"off": "Wyłączone", "stick": "Prawa gałka (działa w każdej grze)", "mouse": "Mysz (strzelanki na PC, menu)"}
NEW_GAME_HINT = "Wpisz nazwę pliku .exe gry, np. eldenring.exe"


def empty():
    """An empty profile."""
    return {"buttons": {}, "sticks": {}, "tuning": {}, "gyro": {}, "triggers": {}}


def profile_name(key):
    """Display name of a profile: the game label for a game profile, "Default" otherwise."""
    return S.profile_label(key) if B.is_game_key(key) else _("Domyślny")


def describe_trigger(name, e):
    """A profile trigger effect entry ("l2" / "r2" / "rumble") -> its description in the change list."""
    if name == "rumble":
        if not e["on"]:
            return _("Wibracja z gry na spustach: wyłączona")
        return _("Wibracja z gry na spustach: siła {gain}%, {freq} Hz").format(gain=e["gain"], freq=e["freq"])
    b = SHORT[name]
    match e["mode"]:
        case "res":
            return _("Opór {b}: od {start}%, siła {force}/8").format(b=b, start=e["start"] * 10, force=e["force"])
        case "click":
            return _("Klik {b}: w {pos}%, siła {force}/8").format(b=b, pos=e["pos"] * 10, force=e["force"])
        case "vib":
            return _("Wibracja {b}: od {start}%, siła {amp}/8, {freq} Hz").format(
                b=b, start=e["start"] * 10, amp=e["amp"], freq=e["freq"])
    return _("Efekt {b}: wyłączony").format(b=b)


def _tuning_defaults(name):
    """The default values a tuning entry is compared with: triggers and sticks differ."""
    return TRIG_DEF if name in B.TRIGGERS else STICK_DEF


class Editor:
    """The edited profile of one pad (slot). The window calls the methods and refreshes its widgets."""

    def __init__(self, slot: int = 0) -> None:
        self.slot = slot
        self.base = None            # key of the pad profile the state was loaded for (changes when the pad id arrives)
        self.key = None
        self.prof, self.saved, self.in_file = empty(), empty(), False
        self._fx_mem = {}           # last values of the trigger effect sliders (a mode change does not lose them)
        self.select()

    # ---------- loading ----------
    def profiles(self):
        """[(key, name, in use)] - the pad profile and the game profiles of this pad."""
        active = S.active[self.slot]
        return [(k, profile_name(k), k == active) for k in S.store.keys_for(S.profile_key(self.slot))]

    def select(self, key=None) -> None:
        """Load profile `key` (default: the one in use). The edited profile takes effect on the pad at once (preview and testing in a
        game).
        """
        s = self.slot
        self.base = S.profile_key(s)
        keys = S.store.keys_for(self.base)
        key = key if key in keys else S.active[s] if S.active[s] in keys else self.base
        self.key = key
        self.prof, self.saved, self.in_file = S.store.get(key), S.store.get_saved(key), S.store.in_file(key)
        if key != S.active[s]:
            P.use(s, key)

    def set_slot(self, slot) -> None:
        """Switch to another pad slot and load its profile."""
        self.slot = slot
        self.select()

    def sync(self) -> bool:
        """Periodic refresh. -> True when the selected profile changed (e.g. the pad id arrived)."""
        if S.profile_key(self.slot) != self.base or self.key not in S.store.keys_for(self.base):
            self.select()
            return True
        self.saved, self.in_file = S.store.get_saved(self.key), S.store.in_file(self.key)
        return False

    # ---------- changes ----------
    def target_of(self, src):
        """What input `src` is replaced with (an input name, or None = disabled)."""
        return self.prof["buttons"].get(src, src)

    def set_button(self, src, dst) -> None:
        """Remap input `src` to `dst`; mapping an input to itself removes the entry."""
        if dst == src:
            self.prof["buttons"].pop(src, None)
        else:
            self.prof["buttons"][src] = dst

    def clear_button(self, src) -> None:
        """Remove the remap of button `src`."""
        self.prof["buttons"].pop(src, None)

    def stick(self, out):
        """The stick output `out` with defaults filled in: {"src", "inv_x", "inv_y"}."""
        return {"src": out, "inv_x": False, "inv_y": False, **self.prof["sticks"].get(out, {})}

    def set_stick(self, out, **patch) -> None:
        """Change fields of a stick output; an entry equal to the default is removed."""
        e = {**self.stick(out), **patch}
        if e == {"src": out, "inv_x": False, "inv_y": False}:
            self.prof["sticks"].pop(out, None)
        else:
            self.prof["sticks"][out] = e

    def tuning(self, name):
        """The tuning of a stick or trigger with defaults filled in."""
        return {**_tuning_defaults(name), **self.prof["tuning"].get(name, {})}

    def set_tuning(self, name, **patch) -> None:
        """Change fields of a tuning entry; an entry equal to the defaults is removed."""
        e = {**self.tuning(name), **patch}
        if e == _tuning_defaults(name):
            self.prof["tuning"].pop(name, None)
        else:
            self.prof["tuning"][name] = e

    def clear_tuning(self, name) -> None:
        """Remove the tuning entry."""
        self.prof["tuning"].pop(name, None)

    def is_game(self):
        """Whether the edited profile is a game profile."""
        return B.is_game_key(self.key)

    def trigger_fx(self, name):
        """A trigger effect of L2 / R2: {"mode", parameters of all modes} - the sliders remember the values of other modes.
        mode "inherit" = a game profile without an entry (as in the pad profile).
        """
        e = self.prof["triggers"].get(name)
        mode = e["mode"] if e else "inherit" if self.is_game() else "off"
        return {**self._fx_mem.get(name, TRIG_FX_DEF), **(e or {}), "mode": mode}

    def set_trigger_fx(self, name, **patch) -> None:
        """Change a trigger effect; the values of the sliders are remembered, and an "inherit" / default-off entry is removed from the
        profile.
        """
        e = {**self.trigger_fx(name), **patch}
        self._fx_mem[name] = {k: v for k, v in e.items() if k != "mode"}
        mode = e["mode"]
        if mode == "inherit" or (mode == "off" and not self.is_game()):
            self.prof["triggers"].pop(name, None)     # pad profile: no entry = disabled
        else:
            self.prof["triggers"][name] = {"mode": mode, **{k: e[k] for k in TRIG_PARAMS[mode]}}

    def clear_trigger_fx(self, name) -> None:
        """Remove the trigger effect entry."""
        self.prof["triggers"].pop(name, None)

    def rumble_fx(self):
        """Game rumble on the triggers: {"on": True / False / None (game profile: as in the pad profile), "gain", "freq"}."""
        e = self.prof["triggers"].get("rumble")
        on = e["on"] if e else None if self.is_game() else False
        return {**self._fx_mem.get("rumble", RUMBLE_DEF), **(e or {}), "on": on}

    def set_rumble_fx(self, **patch) -> None:
        """Change game rumble on the triggers; the same removal rules as for trigger effects."""
        e = {**self.rumble_fx(), **patch}
        self._fx_mem["rumble"] = {k: v for k, v in e.items() if k != "on"}
        if e["on"] is None or (not e["on"] and not self.is_game()):
            self.prof["triggers"].pop("rumble", None)
        else:
            self.prof["triggers"]["rumble"] = {"on": bool(e["on"]), "gain": e["gain"], "freq": e["freq"]}

    def gyro(self):
        """The gyro settings with defaults filled in."""
        return {**GYRO_DEF, **self.prof["gyro"]}

    def set_gyro(self, **patch) -> None:
        """Change gyro settings; settings equal to the defaults are stored as an empty dict."""
        e = {**self.gyro(), **patch}
        self.prof["gyro"] = {} if e == GYRO_DEF else e

    def clear_gyro(self) -> None:
        """Disable gyro."""
        self.prof["gyro"] = {}

    def reset_all(self) -> None:
        """Reset the whole edited profile to empty."""
        self.prof = empty()

    def revert(self) -> None:
        """Throw away the unsaved changes (go back to the saved profile)."""
        self.prof = copy.deepcopy(self.saved)

    def clear_stick(self, out) -> None:
        """Remove the stick output entry."""
        self.prof["sticks"].pop(out, None)

    # ---------- state ----------
    def modified(self):
        """Whether the edited profile differs from the saved one."""
        return B.normalize_profile(self.prof) != B.normalize_profile(self.saved)

    def status(self):
        """-> ("ok" | "warn", text) for the status bar."""
        if self.modified():
            return "warn", _("● Zastosowano na żywo - jeszcze niezapisane")
        if B.is_game_key(self.key) and not self.in_file:
            return "warn", _("● Profil gry jeszcze niezapisany")
        return "ok", _("✓ Zgodny z zapisanym profilem")

    def info(self):
        """Description of the selected profile under the list."""
        if self.key == S.active[self.slot]:
            text = _("Używany teraz.")
        elif B.is_game_key(self.key):
            text = _("Używany automatycznie, gdy aktywnym oknem jest {name}.").format(name=profile_name(self.key))
        else:
            text = _("Używany, gdy żaden profil gry nie pasuje do aktywnego okna.")
        return _("{text}  Aktywne okno: {game}.").format(text=text, game=S.game or "—")

    def changes(self):
        """[(description, undo)] - the list of changes against the defaults; undo removes one entry."""
        L, out = {k: _(v) for k, v in B.LABELS.items()}, []
        for src, dst in self.prof["buttons"].items():
            out.append((f"{L[src]} → {_('wyłączony') if dst is None else L[dst]}", lambda s=src: self.clear_button(s)))
        for name, e in self.prof["sticks"].items():
            inv = ", ".join(t for t, on in ((_("odwróć X"), e["inv_x"]), (_("odwróć Y"), e["inv_y"])) if on)
            src = _("środek") if e["src"] == "none" else _(STICK_NAME[e["src"]])
            out.append((f"{_(STICK_NAME[name])} ← {src}" + (f" ({inv})" if inv else ""), lambda n=name: self.clear_stick(n)))
        out += [(self._describe_tuning(name), lambda n=name: self.clear_tuning(n)) for name in self.prof["tuning"]]
        out += [(describe_trigger(name, e), lambda n=name: self.clear_trigger_fx(n))
                for name, e in self.prof["triggers"].items()]
        g = self.gyro()
        if g["mode"] != "off":
            hold = _("zawsze") if g["hold"] == "always" else _("przytrzymaj {b}").format(b=L[g["hold"]])
            mode = _("prawa gałka") if g["mode"] == "stick" else _("mysz")
            out.append((_("Gyro: {mode}, {hold}, czułość {sens}×").format(mode=mode, hold=hold, sens=f"{g['sens']:g}"),
                        self.clear_gyro))
        return out

    def _describe_tuning(self, name):
        """Describe a tuning entry for the change list (only the non-default parts of a stick)."""
        t = self.tuning(name)
        if name in B.TRIGGERS:
            return _("Reakcja {b}: strefa martwa {dz}, pełne od {mx}").format(b=SHORT[name], dz=t["dz"], mx=t["max"])
        parts = [f"{label} {fmt}" for label, fmt, on in (
            (_("strefa martwa"), f"{round(t['dz'] * 100)}%", t["dz"]),
            (_("pełne od"), f"{round(t['outer'] * 100)}%", t["outer"] != 1),
            (_("krzywa"), f"{t['curve']:.2f}", t["curve"] != 1),
            (_("anty-strefa martwa"), f"{round(t['anti'] * 100)}%", t["anti"])) if on]
        return _("{stick} - reakcja: {parts}").format(stick=_(STICK_NAME[name]), parts=", ".join(parts))

    # ---------- saving and game profiles ----------
    def apply(self, save: bool = False):
        """Send the profile to the pad (save=True: also to remap.json). -> (ok, message)."""
        ok, msg = P.apply(self.slot, self.key, self.prof, save)
        if ok and save:
            self.prof = S.store.get(self.key)
        self.saved, self.in_file = S.store.get_saved(self.key), S.store.in_file(self.key)
        return ok, msg

    def new_game(self, text, copy_current: bool = True):
        """Create a game profile (in memory only, to be saved) and select it. -> (ok, message)."""
        key = P.game_key(self.slot, text)
        if key is None:
            return False, _(NEW_GAME_HINT)
        if key not in S.store.keys_for(self.base):
            P.apply(self.slot, key, copy.deepcopy(self.prof) if copy_current else empty(), save=False)
        self.select(key)
        return True, _("Utworzono profil dla {exe} - zapisz go, żeby go zachować").format(exe=key.split("@", 1)[1])

    def delete_current(self):
        """Delete the selected game profile. -> (ok, message)."""
        ok, msg = P.delete(self.slot, self.key)
        if ok:
            self.select()
        return ok, _("Profil usunięty") if ok else msg
