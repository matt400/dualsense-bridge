"""Receiver: trigger effects - modes in the profile (resistance, click, vibration), game rumble on the triggers, inheritance
from the pad profile in a game profile, the trig3 packet, the master feature "triggers", the editor."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, store, sent)
import rx_net
import rx_settings
import rx_status as ST
import proto
from proto import (BODY, RFX_ON, T_TRIG3, TFX_CLICK, TFX_OFF, TFX_RES, TFX_VIB, TRIG2_OFF)
from rx_buttons import normalize_profile
from rx_editor import Editor
from rx_settings import MasterConfig, MouseConfig, Settings
from rx_shape import normalize_triggers, trigger_values

from chk import ok

# ---------- normalizacja ----------
t = normalize_triggers({"l2": {"start": 3, "force": 6}, "r2": {"mode": "click", "pos": 9, "force": 0, "start": 4},
                        "rumble": {"on": True, "gain": 999, "freq": 0}, "x": {"mode": "res"}})
assert t == {"l2": {"mode": "res", "start": 3, "force": 6}, "r2": {"mode": "click", "pos": 7, "force": 1},
             "rumble": {"on": True, "gain": 200, "freq": 1}}, t
assert normalize_triggers({"l2": {"mode": "vib"}, "r2": {"mode": "boom"}}) == \
    {"l2": {"mode": "vib", "start": 0, "amp": 4, "freq": 30}}, "default mode parameters, an unknown mode rejected"
assert normalize_triggers({"l2": {"mode": "off", "force": 5}, "r2": {"force": 0}}) == {"l2": {"mode": "off"}}
assert normalize_triggers({"rumble": {"on": "yes"}})["rumble"]["on"] is False, "on only as true"
ok("normalization: old resistance format, modes and their parameters within range, game rumble, explicit off")

v = trigger_values(t)
assert v == (TFX_RES, 3, 6, 0, TFX_CLICK, 7, 1, 0, RFX_ON, 200, 1, 0), v
assert trigger_values({"r2": {"mode": "vib", "start": 2, "amp": 5, "freq": 90}, "rumble": {"on": False, "gain": 50, "freq": 9}}) \
    == (TFX_OFF, 0, 0, 0, TFX_VIB, 2, 5, 90, 0, 0, 0, 0), "game rumble off = zeros (no refresh)"
assert trigger_values({}) == TRIG2_OFF
ok("profile trigger values (the first 12 of trig3)")

# ---------- a game profile inherits the trigger effects from the pad profile ----------
A = "aa:aa:aa:aa:aa:01"
S.pad_id[0] = A
store.save(A, normalize_profile({"buttons": {"cross": "circle"},
                                 "triggers": {"l2": {"mode": "res", "start": 2, "force": 5},
                                              "rumble": {"on": True, "gain": 80, "freq": 40}}}))
store.save(f"{A}@race.exe", normalize_profile({"triggers": {"r2": {"mode": "click", "pos": 4, "force": 7}}}))
store.save(f"{A}@quiet.exe", normalize_profile({"triggers": {"l2": {"mode": "off"}, "rumble": {"on": False}}}))
S.reload_profile(0)
assert S.remap_maps[0]["triggers"]["l2"]["mode"] == "res"
S.set_game("race.exe")
tr = S.remap_maps[0]["triggers"]
assert S.active[0] == f"{A}@race.exe" and tr["l2"]["mode"] == "res" and tr["r2"]["mode"] == "click" and tr["rumble"]["on"]
assert S.remap_maps[0]["buttons"] == {}, "the rest of the game profile still overrides the pad profile"
S.set_game("quiet.exe")
assert trigger_values(S.remap_maps[0]["triggers"]) == TRIG2_OFF, "an explicit off in the game profile wins"
S.set_game(None)
ok("game profile: trigger effects from the pad profile when it does not set them; off in the game turns them off; buttons without inheritance")

# ---------- sending from the feedback loop: always trig3 ----------
def T3(vals):
    """The trig3 values without game effects."""
    return (*vals, bytes(11), bytes(11), 0)


trig = []
send = lambda i, vals, n: trig.append((i, vals, n))
S.alive[0] = True
F.trig_sent[0] = None
S.sender_info[0] = (proto.SENDER_VERSION, 15, 0)
F.triggers_tick(0, 100.0, send)
assert trig == [(0, T3(trigger_values(S.remap_maps[0]["triggers"])), F.TRIG_REPEAT)] and len(trig[0][1]) == 15, trig
ok("feedback: the profile's effects go in trig3")

# ---------- master only ----------
B = "bb:bb:bb:bb:bb:02"
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=B, when_absent="nobody", only_master=("triggers",))))
F.triggers_tick(0, 101.0, send)
assert trig[-1] == (0, T3(TRIG2_OFF), F.TRIG_REPEAT), "non-master: effects removed"
assert "master" in ST.triggers_status(0)[0]
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=A, only_master=("triggers",))))
F.triggers_tick(0, 101.5, send)
assert trig[-1][1][0] == TFX_RES, "master ma efekty"
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=B, only_master=("console",))))
F.triggers_tick(0, 102.0, send)
assert trig[-1][1][0] == TFX_RES, "trigger effects not restricted - any pad"
rx_settings.apply(Settings())
ok("master feature: trigger effects only on the master pad (the others = Off)")

S.sender_info[0] = (proto.SENDER_VERSION - 1, 15, 0)
assert ST.triggers_status(0) == (ST.version_text(), "warn")
S.sender_info[0] = (proto.SENDER_VERSION, 15, 0)
assert ST.triggers_status(0) == ("", "")
ok("status: a sender in another version is reported")

# ---------- rx_net: trig3 ----------
sent.clear()
vals = T3((TFX_CLICK, 5, 7, 0, TFX_VIB, 0, 3, 30, RFX_ON, 120, 40, 0))
rx_net.send_triggers(1, vals, 2)
assert [(x[0], x[1]) for x in sent] == [(T_TRIG3, 1), (T_TRIG3, 1)], sent
assert BODY[T_TRIG3].unpack_from(sent[0][3], 4) == vals
ok("rx_net.send_triggers: trig3")

# ---------- editor: modes, inheritance, slider memory ----------
ed = Editor(0)
ed.select(A)
assert ed.trigger_fx("l2")["mode"] == "res" and ed.trigger_fx("r2")["mode"] == "off" and ed.rumble_fx()["on"] is True
ed.set_trigger_fx("r2", mode="vib")
ed.set_trigger_fx("r2", amp=7, freq=120)
assert ed.prof["triggers"]["r2"] == {"mode": "vib", "start": 0, "amp": 7, "freq": 120}
ed.set_trigger_fx("r2", mode="off")
assert "r2" not in ed.prof["triggers"], "pad profile: off = no entry"
ed.set_trigger_fx("r2", mode="vib")
assert ed.prof["triggers"]["r2"]["amp"] == 7, "a mode change does not lose the slider values"
ed.set_rumble_fx(on=False)
assert "rumble" not in ed.prof["triggers"]
ed.set_rumble_fx(on=True, gain=150)
assert ed.prof["triggers"]["rumble"] == {"on": True, "gain": 150, "freq": 40}
ed.select(f"{A}@race.exe")
assert ed.trigger_fx("l2")["mode"] == "inherit" and ed.rumble_fx()["on"] is None
ed.set_trigger_fx("l2", mode="off"); ed.set_rumble_fx(on=False)
assert ed.prof["triggers"]["l2"] == {"mode": "off"} and ed.prof["triggers"]["rumble"]["on"] is False
ed.set_trigger_fx("l2", mode="inherit"); ed.set_rumble_fx(on=None)
assert "l2" not in ed.prof["triggers"] and "rumble" not in ed.prof["triggers"]
assert normalize_profile(ed.prof) == ed.prof, "edytor tworzy znormalizowane wpisy"
names = [t for t, _u in ed.changes()]
assert any(n.startswith(("Click R2", "Klik R2")) for n in names), names
ok("editor: modes and parameters, off / as in the pad profile in a game profile, slider memory, change list")
print("TRIGFX OK")
