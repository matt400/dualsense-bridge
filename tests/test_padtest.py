"""Receiver, the Test view without the window: drift measurement (rx_drift), per-pad drift correction in every profile
(rx_padcal), game output lock (neutral DS4 / DSU report, no mouse and no shortcuts) and the gyro calibration result."""
import json, math, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_drift as D
import rx_padcal
import rx_profiles as P
import rx_status as ST
from proto import (BODY, CAL_MOVED, CAL_OK, CALF_STORED, CAP_CALIB, CMD_GYRO_CAL, CMD_RAW_STICKS, RUMBLE_EVDEV, T_CALRES,
                   T_CLICK, T_CMD, T_IDENT, T_MOUSE, T_TOUCH)
from rx_buttons import normalize_profile
from chk import ok
A = "aa:aa:aa:aa:aa:01"

# ---------- propozycja strefy martwej ----------
assert D.propose(0.0) == D.MIN_DZ and D.propose(0.04) == 0.05 and D.propose(0.048) == 0.06 and D.propose(0.9) == 0.5
assert D.unit(0) == -1 and D.unit(255) == 1 and D.unit(128) == 0
r = D.analyze([(128, 128, 128, 128), (133, 128, 128, 124), (131, 126, 128, 125)])
assert r.left.peak == round(5 / 127, 4) and r.right.peak == round(4 / 128, 4) and not r.moved and r.samples == 3
assert r.left.dz == D.propose(5 / 127) == 0.05, r
assert D.analyze([(128, 128, 128, 128), (200, 128, 128, 128)]).moved, "deflection over 30% = stick moved"
ok("drift: peak at rest -> dead zone ×1.25 rounded up to 0.01, min 3%, max 50%; movement detected")

# ---------- measurement run: sender commands and samples from the network ----------
feed(T_IDENT, 0, A.encode()); state(0)
calls = []
run = D.DriftRun(0, 10.0, lambda s, secs: calls.append((s, secs)))
assert calls == [(0, D.RAW_S)] and run.tick(10.1) is None and S.probe[0] is None, "the sender first, then the samples"
state(0, sticks=(131, 128, 128, 128))
assert run.tick(10.0 + D.SETTLE_S) is None and S.probe[0] == [(131, 128, 128, 128)], "the state at the start moment counts"
for lx in (133, 130, 128, 134):
    state(0, sticks=(lx, 127, 128, 129))
assert run.tick(10.0 + D.SETTLE_S + 1) is None
res = run.tick(10.0 + D.SETTLE_S + D.MEASURE_S)
assert res.samples == 5 and res.left.peak == round(math.hypot(6 / 127, 1 / 128), 4), res
assert res.left.dz == 0.06 and res.right.dz == D.MIN_DZ and calls[-1] == (0, 0) and S.probe[0] is None
state(0, sticks=(140, 128, 128, 128))
assert S.probe[0] is None, "after the measurement samples are no longer collected"
run = D.DriftRun(0, 20.0, lambda s, secs: calls.append((s, secs)))
run.tick(20.0 + D.SETTLE_S)
run.cancel()
assert calls[-1] == (0, 0) and S.probe[0] is None and run.tick(30.0) is None, "abort gives the dead zone back to the sender"
t0 = 10000 / 7                                    # (t0 + 0.3) - t0 < 0.3 in float arithmetic - the limit has to count
assert (t0 + D.SETTLE_S) - t0 < D.SETTLE_S and (t0 + D.SETTLE_S + D.MEASURE_S) - t0 < D.SETTLE_S + D.MEASURE_S
run = D.DriftRun(0, t0)
assert run.tick(t0 + D.SETTLE_S) is None and run.phase == "measure", "exactly at SETTLE_S - the measurement continues"
assert run.tick(t0 + D.SETTLE_S + D.MEASURE_S) is not None and run.phase == "done", "exactly at the time - the result"
state(0)
ok("measurement: sticks without the sender zone -> samples from every packet for 3 s -> the zone comes back; abort cleans up; "
   "time limits robust against float rounding")

# ---------- drift correction: per pad, every profile, the physical stick ----------
rx_padcal.reload()
ok_, msg = P.set_drift(0, {"left": 0.07, "right": 0.0})
assert ok_ and json.load(open(rx_padcal.path()))["pads"] == {A: {"drift": {"left": 0.07}}}
assert S.remap_maps[0]["tuning"]["left"]["dz"] == 0.07 and "right" not in S.remap_maps[0]["tuning"]
state(0, sticks=(134, 128, 128, 128))
assert (S.states[0]["lx"], S.states[0]["ly"]) == (128, 128), "5% drift under a 7% zone - the game sees the centre"
state(0, sticks=(255, 128, 128, 128))
assert S.states[0]["lx"] == 255, "full deflection still full"
assert store.get(A)["tuning"] == {}, "the pad profile in remap.json unchanged - the correction is separate"
store.save(f"{A}@game.exe", {"tuning": {"left": {"dz": 0.12}}, "sticks": {"right": {"src": "left"}}})
prof = S.effective_profile(0, f"{A}@game.exe")
assert prof["tuning"]["left"]["dz"] == 0.12, "a bigger zone from the game profile stays"
assert prof["tuning"]["right"]["dz"] == 0.07 and prof["tuning"]["right"]["outer"] == 1.0, "right output from the left stick"
store.save(f"{A}@other.exe", {"buttons": {"cross": "circle"}})
assert S.effective_profile(0, f"{A}@other.exe")["tuning"]["left"]["dz"] == 0.07, "a game profile without tuning too"
p = rx_padcal.apply_drift(normalize_profile({"tuning": {"left": {"dz": 0.0, "outer": 0.5}}}), {"left": 0.48})
assert p["tuning"]["left"]["outer"] == 0.53, "range always beyond the dead zone"
assert rx_padcal.apply_drift(normalize_profile({"sticks": {"left": {"src": "none"}}}), {"left": 0.1})["tuning"] == {}
rx_padcal.reload()
assert P.drift(0) == {"left": 0.07}, "korekcja wczytana z pliku po restarcie"
P.set_drift(0, {"left": 0})
assert P.drift(0) == {} and json.load(open(rx_padcal.path()))["pads"] == {} and S.remap_maps[0]["tuning"] == {}
open(rx_padcal.path(), "w").write("[zły")
rx_padcal.reload()
assert P.drift(0) == {}, "broken file = no correction, no exception"
os.remove(rx_padcal.path()); rx_padcal.reload()
store.delete(f"{A}@game.exe"); store.delete(f"{A}@other.exe")
ok("drift correction: max(profile, drift) in every profile of the pad, after the stick swap, saved at once, clearing")

# ---------- game output lock ----------
H.init(C.NUM_PADS)
state(0, mask=BIT[304], lt=200, sticks=(10, 20, 30, 40), gyro=(500, 600, 700))
feed(T_TOUCH, 0, 1, 3, 900, 400, 0, 0, 0, 0)
assert P.block(0, True) and not P.block(0, True)
with S.lock:
    out = S.output(0)
assert out["mask"] == 0 and out["lt"] == 0 and (out["lx"], out["ly"], out["rx"], out["ry"]) == (128,) * 4
assert out["gyro"] == (0, 0, 0) and out["touch"] == ((0, 0, 0, 0), (0, 0, 0, 0)) and out["batt_ds4"] == S.states[0]["batt_ds4"]
assert S.states[0]["rawst"] == (10, 20, 30, 40) and S.states[0]["touch"][0][2] == 900, "the window sees the physical pad"
store.set_live(A, {"gyro": {"mode": "mouse"}}); S.reload_profile(0)
state(0, gyro=(0, 9000, 0))
assert S.states[0]["gyro_vel"] is None and not S.states[0]["gyro_on"], "the gyro mouse does not move the cursor"
S.mouse_mode[0] = True
import rxenv; mcalls = rxenv.calls                                  # `calls` testu to inna lista (linia 27)
mcalls.clear()
mv = lambda: [c for c in mcalls if c[0] == "m"]                     # (m, flagi SendInput, dx, dy, data): 1 = ruch, 2 = LDOWN, 4 = LUP
feed(T_MOUSE, 0, 30, 5, 0); feed(T_CLICK, 0, 1)
assert any(c[1] == 1 and c[2] > 0 for c in mv()) and any(c[1] == 2 for c in mv()), "test: mysz touchpada dalej steruje oknem"
assert S.states[0]["rtp"] == 1, "the click is visible in mouse mode too"
feed(T_CLICK, 0, 0)
assert any(c[1] == 4 for c in mv()), "klik puszczony"
ok("lock: touchpad as mouse still drives the window (move, click), the click shows on the drawing")

# ---------- "Testuj touchpad": capture freezes the mouse, PS / idle / leaving Test ends it ----------
import rx_testmode as TM
assert not TM.start_capture(0) and TM.capture_slot is None, "capture only in the Test view"
TM.set_active(True)
assert S.alive[0] and TM.start_capture(0) and TM.capturing(0) and not TM.capturing(1)
S.mouse_mode[0] = False
TM.stop_capture()
assert not TM.start_capture(0), "DS4 touch (no mouse) needs no capture"
S.mouse_mode[0] = True
feed(T_CLICK, 0, 1)                                                  # klik trzymany w chwili startu
mcalls.clear()
assert TM.start_capture(0) and any(c[1] == 4 for c in mv()), "trzymany klik puszczony na starcie przechwytu"
mcalls.clear()
feed(T_MOUSE, 0, 30, 5, 0); feed(T_CLICK, 0, 0); feed(T_CLICK, 0, 1)
assert not mv() and S.states[0]["rtp"] == 1, "captured touchpad: cursor and click stand still, the click is visible"
feed(T_CLICK, 0, 0)
assert P.block(0, False)                                             # lock switch off: the same without the lock
feed(T_MOUSE, 0, 30, 5, 0); feed(T_CLICK, 0, 1); feed(T_CLICK, 0, 0)
assert not mv(), "capture works without the game output lock too"
assert P.block(0, True)
TM._capture_t -= 5
feed(T_TOUCH, 0, 1, 3, 901, 400, 0, 0, 0, 0)
assert TM.capture_left(0) > TM.CAPTURE_IDLE_S - 1, "a touch renews the idle time"
TM._capture_t -= 5
feed(T_MOUSE, 0, 1, 1, 0)
assert TM.capture_left(0) > TM.CAPTURE_IDLE_S - 1, "finger movement too"
TM._capture_t -= TM.CAPTURE_IDLE_S + 1
assert not TM.capturing(0) and TM.capture_slot is None and TM.capture_left(0) == 0.0, "without a touch the capture ends by itself"
mcalls.clear(); feed(T_MOUSE, 0, 30, 5, 0)
assert any(c[1] == 1 for c in mv()), "po przechwycie kursor znowu rusza"
assert TM.start_capture(0)
state(0, mask=BIT[316]); state(0)
assert TM.capture_slot is None, "PS on the pad ends the capture"
assert TM.start_capture(0)
TM.set_active(False)
assert TM.capture_slot is None and not TM.start_capture(0), "leaving the Test view ends the capture"
ok("touchpad capture: mouse and click stand still (with and without the lock), touch / move renew the time, end: idle, PS, leaving Test")
def ps_right():                                                     # PS + d-pad → = next profile (shortcut)
    state(0, mask=BIT[316]); state(0, mask=BIT[316], hat=(1, 0)); state(0, mask=BIT[316]); state(0)
    time.sleep(0.1)                                                  # shortcuts run in a separate thread
ps_right()
assert S.override[0] is None, "pad shortcuts do not work"
store.set_live(A, {}); S.reload_profile(0)
S.mouse_mode[0] = False
assert P.block(0, False)
state(0, mask=BIT[304], sticks=(10, 20, 30, 40))
with S.lock:
    out = S.output(0)
assert out is S.states[0] and out["mask"] and out["lx"] == 10, "after the test the game gets the pad again"
state(0)
ps_right()
assert S.override[0] == A, "without the lock the same shortcut works (the test is not empty)"
S.override[0] = None; S.reload_profile(0)
ok("lock: DS4 / DSU neutral (real battery), gyro mouse and shortcuts paused, the window sees the physical state")

# ---------- gyro calibration: commands and result ----------
sent.clear()
S.sender_info[0] = (8, 3, 0)
assert not P.calibrate_gyro(0, 3) and not sent, "old sender - no command"
assert ST.rumble_status(0)[0].startswith("wibracja: ") and "stary" not in ST.rumble_status(0)[0]
S.sender_info[0] = (9, CAP_CALIB, 0)
assert ST.rumble_status(0)[1] == "bad", "the CAP_CALIB bit is not a rumble method"
S.sender_info[0] = (9, RUMBLE_EVDEV | CAP_CALIB, 0)
assert ST.rumble_status(0)[1] == "" and P.sender_calib(0)
assert P.calibrate_gyro(0, 30)
cmds = [BODY[T_CMD].unpack_from(pkt, 4) for t, i, _sq, pkt in sent if t == T_CMD]
assert cmds == [(CMD_GYRO_CAL, 10)] * 3, cmds
sent.clear(); P.raw_sticks(0, 6); P.query_gyro_cal(0)
assert [BODY[T_CMD].unpack_from(p, 4) for *_x, p in sent] == [(CMD_RAW_STICKS, 6)] * 3 + [(CMD_GYRO_CAL, 0)] * 3
import rx_events as ev
ev.clear()
for _n in range(3):
    feed(T_CALRES, 0, CAL_OK, CALF_STORED, 10, 20, 30, 10, 20, 30, 1_700_000_000)
g = S.gyro_cal[0]
assert g[:5] == (CAL_OK, CALF_STORED, (10, 20, 30), (10, 20, 30), 1_700_000_000)
assert sum("skalibrowany" in e.text for e in ev.since(0)) == 1, "reply repeats - one log entry"
feed(T_CALRES, 0, CAL_MOVED, 0, 0, 0, 0, 0, 0, 0, 0)
assert any("ruszał" in e.text for e in ev.since(0))
N.clear_slot(0, forget_id=True)
assert S.gyro_cal[0] is None, "another pad in the slot - calibration state from scratch"
ok("gyro calibration: command only to a v9+ sender, 10 s limit, result in the slot state and the log (no repeats)")

# ---------- drift correction is not a profile change ----------
feed(T_IDENT, 0, A.encode()); state(0)
P.set_drift(0, {"left": 0.08})
assert S.profile_changes(0) == 0 and S.remap_maps[0]["tuning"]["left"]["dz"] == 0.08
P.set_drift(0, {"left": 0})
ok("pad card: drift correction does not count as a profile change")

# ---------- Test view: pad shortcuts paused, the master's Options + Create leaves ----------
import rx_master, rx_settings, rx_testmode as TM
from rx_settings import MasterConfig, MouseConfig, Settings
B_ = "bb:bb:bb:bb:bb:02"
PS, TRI, RIGHT = BIT[316], BIT[307], (1, 0)
OPT, SHARE = BIT[315], BIT[314]
ESC = OPT | SHARE
settings = lambda **m: rx_settings.apply(Settings(MouseConfig(), MasterConfig(**m)))
def out(i):
    with S.lock:
        return S.output(i)["mask"]
escapes = []
TM.on_escape = lambda: escapes.append(1)
feed(T_IDENT, 0, A.encode()); feed(T_IDENT, 1, B_.encode()); state(0); state(1)
settings(pad=A)                                      # slot 0 = master, slot 1 = testowany pad
store.save(f"{A}@game.exe", {"buttons": {"cross": "circle"}})
TM.set_active(True); P.block(1, True)
state(0, mask=PS); state(0, mask=PS, hat=RIGHT); state(0, mask=PS); state(0); time.sleep(0.1)
assert S.override[0] is None, "Test view: the master's PS + → does not change the profile"
S.mouse_mode[0] = False
state(0, mask=PS); feed(T_CLICK, 0, 1); feed(T_CLICK, 0, 0); state(0)
assert not S.mouse_mode[0], "Test view: PS + touchpad click does not switch the mouse"
state(1, mask=ESC)
assert TM.active and S.states[1]["blocked"] and not escapes, "a non-master's Options + Create does nothing"
state(1)
state(0, mask=PS | TRI); time.sleep(0.1); state(0, mask=PS | TRI, lt=10)
assert TM.active and not escapes, "PS + △ no longer leaves the test (a long PS + △ powered the pad off)"
state(0)
state(0, mask=OPT)
assert TM.active and not escapes, "sam Options - dalej test"
state(0, mask=ESC)                                   # Create pressed after Options - a plain press, no waiting
assert not TM.active and TM.escaped and escapes == [1], "the master's Options + Create - leaves at once"
assert not S.states[1]["blocked"], "the tested pad's lock removed"
assert out(0) == 0, "neutral report: Options + Create held on exit do not reach the game"
state(0, mask=ESC, sticks=(130, 128, 128, 128))
assert out(0) == 0, "still held - still swallowed"
state(0, mask=OPT); state(0); state(0, mask=OPT)
assert out(0) == OPT, "after release Options reaches the game again"
state(0); time.sleep(0.15)
assert not [c for c in calls if c[0] == "k"], "leaving gives no PS pulse and no Game Bar"
state(0, mask=PS); state(0, mask=PS, hat=RIGHT); state(0, mask=PS); state(0); time.sleep(0.1)
assert S.override[0] == f"{A}@game.exe", "after the test the shortcuts work again"
S.override[0] = None; S.reload_profile(0)
ok("Test: no pad's shortcuts work; the master's Options + Create leaves at once (PS + △ no longer), "
   "removes the lock and swallows held inputs; another pad's combo does nothing")

# the tested pad is the master: leaving from it, through the network thread
escapes.clear(); TM.rearm(); TM.set_active(True); P.block(0, True)
state(0, mask=ESC, lt=200)
assert escapes == [1] and not S.states[0]["blocked"] and not TM.active
assert out(0) == 0, "buttons held on the tested pad swallowed (sticks and triggers analog, live)"
state(0)
ok("Test: the master tests itself - leaving from the locked pad, inputs neutral until released")

# no master - any pad; a combo held when entering Test has to be released
settings(pad="")
escapes.clear(); TM.rearm()
state(1, mask=ESC)                                   # held since before the view was opened
TM.set_active(True)
state(1, mask=ESC, lt=50)
assert TM.active and not escapes, "a combo held across the test boundary does not leave"
state(1); state(1, mask=ESC)
assert escapes == [1], "without a master any pad leaves (a new press)"
state(1)
settings(pad="cc:cc:cc:cc:cc:03", when_absent="nobody")
TM.rearm(); TM.set_active(True); state(0, mask=ESC)
assert TM.active, "master set, but absent + nobody - nobody"
TM.set_active(False); state(0); settings(pad="")
store.delete(f"{A}@game.exe")
ok("Test: without a master any pad can leave, an absent master by when_absent, held on entry does not leave")
