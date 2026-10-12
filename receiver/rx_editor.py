"""Receiver: model edycji profilu dla okna (bez Qt): wybór profilu, zmiany przycisków / gałek / gyro, zapis.

Zmiany działają na żywo (apply(save=False) wywołuje okno po krótkim debounce), zapis do remap.json jest osobno.
Profil to {"buttons", "sticks", "tuning", "gyro", "triggers"} - jak w rx_buttons.normalize_profile.
"""
from rx_i18n import _
import copy

import rx_buttons as B
import rx_profiles as P
import rx_state as S
from rx_shape import GYRO_DEF, STICK_DEF, TRIG_DEF, TRIG_FX_DEF

SHORT = {"cross": "✕", "circle": "○", "triangle": "△", "square": "□", "l1": "L1", "r1": "R1", "share": "SHARE",
         "options": "OPT", "ps": "PS", "l3": "L3", "r3": "R3", "dpad_up": "↑", "dpad_down": "↓", "dpad_left": "←",
         "dpad_right": "→", "l2": "L2", "r2": "R2"}
STICK_NAME = {"left": "Lewa gałka", "right": "Prawa gałka", "none": "Wyłączona (środek)"}   # przez _() w oknie
STICK_OF = {"left_stick": "left", "right_stick": "right"}     # część rysunku -> wyjście gałki
L3_OF = {"left_stick": "l3", "right_stick": "r3"}             # klik gałki
GYRO_MODES = {"off": "Wyłączone", "stick": "Prawa gałka (działa w każdej grze)", "mouse": "Mysz (strzelanki na PC, menu)"}
NEW_GAME_HINT = "Wpisz nazwę pliku .exe gry, np. eldenring.exe"


def empty():
    return {"buttons": {}, "sticks": {}, "tuning": {}, "gyro": {}, "triggers": {}}


def profile_name(key):
    return S.profile_label(key) if B.is_game_key(key) else _("Domyślny")


def _tuning_defaults(name):
    return TRIG_DEF if name in B.TRIGGERS else STICK_DEF


class Editor:
    """Edytowany profil jednego pada (slot). Okno woła metody i odświeża widżety."""

    def __init__(self, slot=0):
        self.slot = slot
        self.base = None            # klucz profilu pada, dla którego załadowano stan (zmienia się, gdy nadejdzie id pada)
        self.key = None
        self.prof, self.saved, self.in_file = empty(), empty(), False
        self.select()

    # ---------- ładowanie ----------
    def profiles(self):
        """[(klucz, nazwa, czy w użyciu)] - profil pada i profile gier tego pada."""
        active = S.active[self.slot]
        return [(k, profile_name(k), k == active) for k in S.store.keys_for(S.profile_key(self.slot))]

    def select(self, key=None):
        """Wczytuje profil `key` (domyślnie: w użyciu). Edytowany profil działa od razu na padzie (podgląd i test w grze)."""
        s = self.slot
        self.base = S.profile_key(s)
        keys = S.store.keys_for(self.base)
        key = key if key in keys else S.active[s] if S.active[s] in keys else self.base
        self.key = key
        self.prof, self.saved, self.in_file = S.store.get(key), S.store.get_saved(key), S.store.in_file(key)
        if key != S.active[s]:
            P.use(s, key)

    def set_slot(self, slot):
        self.slot = slot
        self.select()

    def sync(self):
        """Odświeżenie co chwilę. -> True, gdy zmienił się wybrany profil (np. nadeszło id pada)."""
        if S.profile_key(self.slot) != self.base or self.key not in S.store.keys_for(self.base):
            self.select()
            return True
        self.saved, self.in_file = S.store.get_saved(self.key), S.store.in_file(self.key)
        return False

    # ---------- zmiany ----------
    def target_of(self, src):
        """Na co zamienione jest wejście `src` (nazwa wejścia albo None = wyłączone)."""
        return self.prof["buttons"].get(src, src)

    def set_button(self, src, dst):
        if dst == src:
            self.prof["buttons"].pop(src, None)
        else:
            self.prof["buttons"][src] = dst

    def clear_button(self, src):
        self.prof["buttons"].pop(src, None)

    def stick(self, out):
        return {"src": out, "inv_x": False, "inv_y": False, **self.prof["sticks"].get(out, {})}

    def set_stick(self, out, **patch):
        e = {**self.stick(out), **patch}
        if e == {"src": out, "inv_x": False, "inv_y": False}:
            self.prof["sticks"].pop(out, None)
        else:
            self.prof["sticks"][out] = e

    def tuning(self, name):
        return {**_tuning_defaults(name), **self.prof["tuning"].get(name, {})}

    def set_tuning(self, name, **patch):
        e = {**self.tuning(name), **patch}
        if e == _tuning_defaults(name):
            self.prof["tuning"].pop(name, None)
        else:
            self.prof["tuning"][name] = e

    def clear_tuning(self, name):
        self.prof["tuning"].pop(name, None)

    def trigger_fx(self, name):
        """Opór spustu L2 / R2: {"start": 0-9, "force": 0-8} (siła 0 = bez oporu)."""
        return {**TRIG_FX_DEF, **self.prof["triggers"].get(name, {})}

    def set_trigger_fx(self, name, **patch):
        e = {**self.trigger_fx(name), **patch}
        if e["force"]:
            self.prof["triggers"][name] = e
        else:
            self.prof["triggers"].pop(name, None)

    def clear_trigger_fx(self, name):
        self.prof["triggers"].pop(name, None)

    def gyro(self):
        return {**GYRO_DEF, **self.prof["gyro"]}

    def set_gyro(self, **patch):
        e = {**self.gyro(), **patch}
        self.prof["gyro"] = {} if e == GYRO_DEF else e

    def clear_gyro(self):
        self.prof["gyro"] = {}

    def reset_all(self):
        self.prof = empty()

    def revert(self):
        self.prof = copy.deepcopy(self.saved)

    def clear_stick(self, out):
        self.prof["sticks"].pop(out, None)

    # ---------- stan ----------
    def modified(self):
        return B.normalize_profile(self.prof) != B.normalize_profile(self.saved)

    def status(self):
        """-> ("ok" | "warn", tekst) do paska stanu."""
        if self.modified():
            return "warn", _("● Zastosowano na żywo - jeszcze niezapisane")
        if B.is_game_key(self.key) and not self.in_file:
            return "warn", _("● Profil gry jeszcze niezapisany")
        return "ok", _("✓ Zgodny z zapisanym profilem")

    def info(self):
        """Opis wybranego profilu pod listą."""
        if self.key == S.active[self.slot]:
            text = _("Używany teraz.")
        elif B.is_game_key(self.key):
            text = _("Używany automatycznie, gdy aktywnym oknem jest {name}.").format(name=profile_name(self.key))
        else:
            text = _("Używany, gdy żaden profil gry nie pasuje do aktywnego okna.")
        return _("{text}  Aktywne okno: {game}.").format(text=text, game=S.game or "—")

    def changes(self):
        """[(opis, cofnięcie)] - lista zmian względem domyślnych; cofnięcie usuwa jedną pozycję."""
        L, out = {k: _(v) for k, v in B.LABELS.items()}, []
        for src, dst in self.prof["buttons"].items():
            out.append((f"{L[src]} → {_('wyłączony') if dst is None else L[dst]}", lambda s=src: self.clear_button(s)))
        for name, e in self.prof["sticks"].items():
            inv = ", ".join(t for t, on in ((_("odwróć X"), e["inv_x"]), (_("odwróć Y"), e["inv_y"])) if on)
            src = _("środek") if e["src"] == "none" else _(STICK_NAME[e["src"]])
            out.append((f"{_(STICK_NAME[name])} ← {src}" + (f" ({inv})" if inv else ""), lambda n=name: self.clear_stick(n)))
        out += [(self._describe_tuning(name), lambda n=name: self.clear_tuning(n)) for name in self.prof["tuning"]]
        out += [(_("Opór {b}: od {start}%, siła {force}/8").format(b=SHORT[name], start=e["start"] * 10, force=e["force"]),
                 lambda n=name: self.clear_trigger_fx(n)) for name, e in self.prof["triggers"].items()]
        g = self.gyro()
        if g["mode"] != "off":
            hold = _("zawsze") if g["hold"] == "always" else _("przytrzymaj {b}").format(b=L[g["hold"]])
            mode = _("prawa gałka") if g["mode"] == "stick" else _("mysz")
            out.append((_("Gyro: {mode}, {hold}, czułość {sens}×").format(mode=mode, hold=hold, sens=f"{g['sens']:g}"),
                        self.clear_gyro))
        return out

    def _describe_tuning(self, name):
        t = self.tuning(name)
        if name in B.TRIGGERS:
            return _("Reakcja {b}: strefa martwa {dz}, pełne od {mx}").format(b=SHORT[name], dz=t["dz"], mx=t["max"])
        parts = [f"{label} {fmt}" for label, fmt, on in (
            (_("strefa martwa"), f"{round(t['dz'] * 100)}%", t["dz"]),
            (_("pełne od"), f"{round(t['outer'] * 100)}%", t["outer"] != 1),
            (_("krzywa"), f"{t['curve']:.2f}", t["curve"] != 1),
            (_("anty-strefa martwa"), f"{round(t['anti'] * 100)}%", t["anti"])) if on]
        return _("{stick} - reakcja: {parts}").format(stick=_(STICK_NAME[name]), parts=", ".join(parts))

    # ---------- zapis i profile gier ----------
    def apply(self, save=False):
        """Wysyła profil na pad (save=True: także do remap.json). -> (ok, komunikat)."""
        ok, msg = P.apply(self.slot, self.key, self.prof, save)
        if ok and save:
            self.prof = S.store.get(self.key)
        self.saved, self.in_file = S.store.get_saved(self.key), S.store.in_file(self.key)
        return ok, msg

    def new_game(self, text, copy_current=True):
        """Tworzy profil gry (tylko w pamięci, do zapisania) i wybiera go. -> (ok, komunikat)."""
        key = P.game_key(self.slot, text)
        if key is None:
            return False, _(NEW_GAME_HINT)
        if key not in S.store.keys_for(self.base):
            P.apply(self.slot, key, copy.deepcopy(self.prof) if copy_current else empty(), save=False)
        self.select(key)
        return True, _("Utworzono profil dla {exe} - zapisz go, żeby go zachować").format(exe=key.split("@", 1)[1])

    def delete_current(self):
        """Usuwa wybrany profil gry. -> (ok, komunikat)."""
        ok, msg = P.delete(self.slot, self.key)
        if ok:
            self.select()
        return ok, _("Profil usunięty") if ok else msg
