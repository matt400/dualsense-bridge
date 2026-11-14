"""Receiver: trigger effects set by the game on the virtual DualSense (HIDMaestro) - trig3 only to a v11+ sender,
the profile wins (the sender decides), the game drives the triggers = no game rumble on the triggers, the master feature "triggers",
they stay on a program change, released when the program that set them closes, and when the pad detaches.
    python3 tests/test_gametrig.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, store, sent)
import rx_events as ev
import rx_games
import rx_settings
from dataclasses import replace
from proto import (BODY, TRIG_RAW_LEN, CAP_TRIG_FX, CAP_TRIG_RAW, RFX_ON, T_TRIG2, T_TRIG3, TFX_OFF, TFX_RES, TRG3_L2, TRG3_R2,
                   TRIG2_OFF, Codec)
from rx_buttons import normalize_profile

from chk import ok
G_L2 = bytes([0x25, 0x30, 0x00, 0x05] + [0] * 7)
G_R2 = bytes([0x21, 0xFF, 0x03] + [0] * 8)
OFF = bytes([0x05] + [0] * 10)
Z = bytes(11)

A = "aa:aa:aa:aa:aa:07"
S.pad_id[0] = A
S.alive[0] = True
store.save(A, normalize_profile({"triggers": {"r2": {"mode": "res", "start": 2, "force": 5},
                                              "rumble": {"on": True, "gain": 80, "freq": 40}}}))
S.reload_profile(0)
prof = (TFX_OFF, 0, 0, 0, TFX_RES, 2, 5, 0, RFX_ON, 80, 40, 0)
S.sender_info[0] = (11, 3 | CAP_TRIG_FX | CAP_TRIG_RAW, 0)
assert F.trigger_target(0) == prof, "the game set nothing - a plain trig2"

S.game = "forza.exe"
F.game(0, trig=(G_L2, None))
assert F.fb[0]["trig"] == (G_L2, None) and F.fb[0]["trig_owner"] == "forza.exe"
v = F.trigger_target(0)
assert v == (*prof[:8], 0, 0, 0, 0, G_L2, Z, TRG3_L2), ("game effect, game rumble on the triggers off", v)
F.game(0, trig=(None, G_R2))
assert F.fb[0]["trig"] == (G_L2, G_R2), "a trigger without a flag in the report stays"
v = F.trigger_target(0)
assert v[12:] == (G_L2, G_R2, TRG3_L2 | TRG3_R2) and v[4:8] == prof[4:8], "the profile's R2 goes along - the sender gives it priority"
F.game(0, trig=(OFF, OFF))
assert F.trigger_target(0)[8:12] == prof[8:12], "the game released the triggers (Off) - game rumble on the triggers comes back"
ok("trig3: profile + raw game effects, flags; a game driving the triggers turns off game rumble on the triggers")

c = Codec()
F.game(0, trig=(G_L2, G_R2))
v = F.trigger_target(0)
pkt = c.pack(T_TRIG3, 0, *v)
assert c.parse(pkt)[0] == T_TRIG3 and BODY[T_TRIG3].unpack_from(pkt, 4) == v
sent.clear()
N.send_triggers(0, v, 1)
assert sent[-1][0] == T_TRIG3, sent[-1]
ok("rx_net.send_triggers: 15 values -> trig3")

S.sender_info[0] = (10, 3 | CAP_TRIG_FX, 0)
assert F.trigger_target(0) == prof, "v10 sender - profile only (trig2, an older one would drop the length)"
S.sender_info[0] = (11, 3 | CAP_TRIG_FX | CAP_TRIG_RAW, 0)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="bb:bb:bb:bb:bb:bb",
                                                          only_master=("triggers",), when_absent="nobody")))
assert F.trigger_target(0) == TRIG2_OFF, "effects only for the master - the game does not set the triggers of other pads either"
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="", only_master=())))
ok("sender without CAP_TRIG_RAW - trig2; the master feature covers game effects")

calls = []
F.triggers_tick(0, 100.0, lambda i, v, n: calls.append((v, n)))
assert calls and len(calls[-1][0]) == 15 and calls[-1][1] == F.TRIG_REPEAT
F.triggers_tick(0, 100.0 + F.TRIG_REFRESH_S, lambda i, v, n: calls.append((v, n)))
assert len(calls) == 2 and calls[-1][1] == 1, "game effects refreshed like the profile (sender watchdog)"
got = []
F.release_triggers(lambda i, v, n: got.append(v))
assert got == [TRIG2_OFF], "closing the receiver: trig2 without effects releases the game effects too"
ok("refresh and release on close")

seq = ev.since(0)[-1].seq if ev.since(0) else 0
F.fb[0]["trig_note"] = True
F.fb[0]["trig_owner"] = "forza.exe"
F._note_game_triggers(0)
assert any("forza.exe" in e.text and "efekty spustów" in e.text for e in ev.since(seq))
ok("the first game effects in the log")

# ---------- they stay on a program change, go away when the program closes ----------
FG[0] = "explorer.exe"
rx_games.poll_once()
assert S.game == "explorer.exe" and F.fb[0]["trig"] == (G_L2, G_R2), "alt-tab: effects stay"
W.running_exes = lambda: {"forza.exe", "explorer.exe"}
rx_games._procs[:] = [0.0, None]
rx_games.release_closed(now=1000.0)
assert F.fb[0]["trig"] == (G_L2, G_R2), "the game runs in the background - effects stay"
W.running_exes = lambda: {"explorer.exe"}
rx_games.release_closed(now=1001.0)
assert F.fb[0]["trig"] == (G_L2, G_R2), "process snapshot at most every PROCS_S"
rx_games.release_closed(now=1000.0 + rx_games.PROCS_S)
assert F.fb[0]["trig"] == F.NO_GAME_TRIG and F.trigger_target(0) == prof, "game closed - triggers released"
S.game = "steam.exe"
F.game(0, trig=(G_L2, None))
def boom():
    raise OSError("no snapshot")
W.running_exes = boom
rx_games._procs[:] = [0.0, None]
S.game = "explorer.exe"
rx_games.release_closed(now=2000.0)
assert F.fb[0]["trig"] == (G_L2, None), "unknown what is running - effects stay"
F.reset(0)
assert F.fb[0]["trig"] == F.NO_GAME_TRIG and F.fb[0]["trig_owner"] is None, "a new virtual pad - no game effects"
ok("a program change does not release the effects; closing the program and detaching the pad do")
# ---------- an all-zero effect (raw bytes too) is no effect ----------
assert not F._has_effect((0, 0, bytes(TRIG_RAW_LEN), bytes(TRIG_RAW_LEN), 0)), "all zeros (raw bytes too) - no effect"
assert F._has_effect((0, 3, 0)) and F._has_effect((0, bytes(3) + b"\1")), "a non-zero number or byte - an effect"
ok("empty trigger bytes are no effect")
print("GAME TRIGGERS OK")
