"""Receiver, stick accessibility mode: normalization, range / dead zone / curve / tremor damping on the physical stick,
range measurement analysis (wizard), per-pad storage in padcal.json and hooking into profiles (also with stick swap and drift)."""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_access as A
import rx_drift as D
import rx_padcal
import rx_profiles as P
import rx_reach as R
from proto import T_IDENT
from chk import ok
PAD = "aa:aa:aa:aa:aa:02"


def byte(f: float) -> int:
    """-1..1 -> axis byte (the same as rx_shape._byte)."""
    return max(0, min(255, round(128 + f * (128 if f < 0 else 127))))


def circle(r: float, n: int = 240, start: float = 0.0, span: float = 2 * math.pi) -> list[tuple[int, int, int, int]]:
    """Right stick samples along an arc of radius r (left one centred)."""
    return [(128, 128, byte(r * math.cos(start + span * k / n)), byte(r * math.sin(start + span * k / n))) for k in range(n)]


# ---------- normalizacja ----------
assert A.normalize(None) == {} and A.normalize("x") == {} and A.normalize({"on": False}) == {}
assert A.normalize({"on": True}) == {"on": True}, "on without sticks = mode on, default settings"
n = A.normalize({"on": True, "right": {"outer": 0.1, "dz": 9, "curve": "zły", "steady": 0.05, "x": 1}, "left": {}, "junk": 1})
assert n == {"on": True, "right": {"dz": 0.3, "outer": 0.4, "curve": 1.0, "steady": 0.05}}, n
assert A.normalize({"right": {"dz": 0.3, "outer": 0.5}})["right"]["outer"] == 0.5
assert A.normalize({"right": {"dz": 0.3, "outer": 0.4}})["right"]["outer"] >= 0.3 + A.MIN_GAP, "range always beyond the dead zone"
assert A.normalize({"right": {"outer": 1.0}}) == {}, "a stick with default values disappears"
assert A.normalize({"on": 1, "right": {"outer": float("nan")}}) == {}, "NaN = default, 'on' only for True"
assert A.runtime({"right": {"dz": 0.0, "outer": 0.7, "curve": 1.0, "steady": 0.0}}) == {}, "mode off = nothing to do"
rt = A.runtime(A.normalize({"on": True, "right": {"outer": 0.7}}))
assert set(rt) == {"right"} and rt["right"]["anti"] == 0.0 and rt["right"]["outer"] == 0.7
ok("normalization: ranges, NaN, range beyond the dead zone, defaults disappear, mode off = no effect")

# ---------- effect on the physical stick ----------
C0 = (128, 128, 128, 128)
rt = A.runtime({"on": True, "right": {"dz": 0.0, "outer": 0.5, "curve": 1.0, "steady": 0.0}})
assert A.apply(rt, (128, 128, 192, 128), C0) == (128, 128, 255, 128), "50% physical deflection = full in game"
assert A.apply(rt, (200, 50, 192, 128), C0)[:2] == (200, 50), "left stick without settings untouched"
x, y = A.apply(rt, (128, 128, byte(0.5 * math.cos(0.7)), byte(0.5 * math.sin(0.7))), C0)[2:]
assert abs(math.hypot((x - 128) / 127, (y - 128) / 127) - 1.0) < 0.02, "range computed radially, diagonally too"
assert A.apply(rt, (128, 128, byte(0.25), 128), C0)[2] == byte(0.5), "quarter of the way = half in game"
rt = A.runtime({"on": True, "left": {"dz": 0.1, "outer": 1.0, "curve": 1.0, "steady": 0.0}})
assert A.apply(rt, (128 + 10, 128, 128, 128), C0)[:2] == (128, 128), "centre inside the dead zone"
rt = A.runtime({"on": True, "right": {"dz": 0.0, "outer": 1.0, "curve": 2.0, "steady": 0.0}})
assert abs((A.apply(rt, (128, 128, byte(0.5), 128), C0)[2] - 128) / 127 - 0.25) < 0.02, "curve >1: small deflections weaker"
ok("effect: range (diagonal too), dead zone, curve; the other stick untouched")

# ---------- tremor damping ----------
rt = A.runtime({"on": True, "right": {"dz": 0.0, "outer": 1.0, "curve": 1.0, "steady": 0.05}})
last = (128, 128, 128, 128)
assert A.apply(rt, (128, 128, 131, 128), last)[2] == 128, "twitch below the threshold ignored"
last = (128, 128, 140, 128)
assert A.apply(rt, (128, 128, 142, 128), last)[2] == 140, "small move from the last output ignored"
assert A.apply(rt, (128, 128, 150, 128), last)[2] == 150, "move above the threshold passes at once, no delay"
assert A.apply(rt, (128, 128, 128, 128), last)[2:] == (128, 128), "return to centre always at once"
assert A.apply(rt, (128, 128, 255, 128), (128, 128, 250, 128))[2] == 255, "full deflection is not damped"
assert A.apply(rt, (131, 128, 131, 128), (128, 128, 128, 128))[:2] == (131, 128), "damping only on the stick that has it set"
ok("tremor damping: a threshold instead of a filter - no delay, full deflection and centre always pass")

# ---------- range analysis (wizard) ----------
r = A.analyze_reach(circle(0.72), "right")
assert r.status == "ok" and r.sectors == A.SECTORS and 0.66 <= r.outer <= 0.70 and 0.71 <= r.peak <= 0.73, r
assert A.analyze_reach(circle(0.72), "left").status == "small", "the other stick stays centred"
assert A.analyze_reach(circle(1.0), "right").outer == 1.0, "full range = no scaling"
assert A.analyze_reach(circle(0.98), "right").outer == 1.0, "almost full too"
r = A.analyze_reach(circle(0.72, span=math.pi), "right")
assert r.status == "few" and r.sectors < A.MIN_SECTORS, "half a circle - the wizard asks for a full circle"
assert A.analyze_reach(circle(0.2), "right").status == "small"
mixed = [(128, 128, byte(0.9 * math.cos(a)) if math.cos(a) > 0 else byte(0.6 * math.cos(a)), byte((0.9 if math.cos(a) > 0 else 0.6) * math.sin(a)))
         for a in (2 * math.pi * k / 240 for k in range(240))]
assert 0.54 <= A.analyze_reach(mixed, "right").outer <= 0.60, "range = the weakest direction, so every direction reaches full"
jerk = circle(0.72) + [(128, 128, 255, 128)]
assert A.analyze_reach(jerk, "right").outer <= 0.70, "a single jerk does not spoil the result"
assert A.analyze_reach([], "right").status == "small"
ok("range analysis: weakest direction with a margin, full circle required, a jerk and an empty list have no effect")

# ---------- per-pad storage and hooking into profiles ----------
feed(T_IDENT, 0, PAD.encode()); state(0)
rx_padcal.reload()
assert P.access(0) == {}
ok_, msg = P.set_access(0, {"on": True, "right": {"outer": 0.5}})
assert ok_ and json.load(open(rx_padcal.path()))["pads"] == {PAD: {"access": {"on": True, "right": {
    "curve": 1.0, "dz": 0.0, "outer": 0.5, "steady": 0.0}}}}, open(rx_padcal.path()).read()
state(0, sticks=(128, 128, 192, 128))
assert S.states[0]["rx"] == 255, "once on, 50% deflection = full in game"
assert S.states[0]["rawst"] == (128, 128, 192, 128), "raw stick (Test, drift measurement) unchanged"
assert store.get(PAD)["tuning"] == {} and "access" not in store.get(PAD), "profile in remap.json unchanged"
P.set_access(0, {"on": False, "right": {"outer": 0.5}})
assert S.states[0]["rx"] == 192, "mode off = the game gets the stick unchanged, settings stay stored"
assert P.access(0)["right"]["outer"] == 0.5 and "on" not in P.access(0)
P.set_access(0, {"on": True, "right": {"outer": 0.5}})

store.save(PAD, {"sticks": {"left": {"src": "right"}, "right": {"src": "left"}}})
S.reload_profile(0)
state(0, sticks=(128, 128, 192, 128))
assert S.states[0]["lx"] == 255 and S.states[0]["rx"] == 128, "stick swap in the profile: the range follows the physical stick"
store.save(f"{PAD}@game.exe", {"buttons": {"cross": "circle"}})
assert "access" in S.effective_profile(0, f"{PAD}@game.exe"), "game profile too"
assert "access" not in S.effective_profile(0, PAD, drift=False), "no correction (profile change counter) - no accessibility mode"
store.delete(f"{PAD}@game.exe")
store.save(PAD, {})
S.reload_profile(0)

# drift measured on the physical stick goes through the same range, so it does not escape the dead zone
P.set_drift(0, {"right": 0.05})
assert S.remap_maps[0]["tuning"]["right"]["dz"] == 0.1, "5% drift with 50% range = 10% on the game side"
state(0, sticks=(128, 128, 134, 128))
assert (S.states[0]["rx"], S.states[0]["ry"]) == (128, 128), "4.7% drift under the dead zone - the game sees the centre"
state(0, sticks=(128, 128, 192, 128))
assert S.states[0]["rx"] == 255
P.set_drift(0, {"right": 0})

rx_padcal.reload()
assert P.access(0) == {"on": True, "right": {"dz": 0.0, "outer": 0.5, "curve": 1.0, "steady": 0.0}}, "wczytane po restarcie"
P.set_access(0, {})
assert P.access(0) == {} and json.load(open(rx_padcal.path()))["pads"] == {}
open(rx_padcal.path(), "w").write("[zły")
rx_padcal.reload()
assert P.access(0) == {}, "broken file = no accessibility mode, no exception"
os.remove(rx_padcal.path()); rx_padcal.reload()
state(0)
ok("per-pad storage: separate from profiles, mode off keeps the values, stick swap, drift through the range, restart, broken file")

# ---------- range measurement over time ----------
state(0, sticks=(128, 128, 150, 128))
run = R.ReachRun(0, 50.0)
assert S.probe[0] == [(128, 128, 150, 128)], "the state at the start moment counts"
for s in circle(0.7, 12):
    state(0, sticks=s)
assert run.tick(50.0 + R.REACH_S - 0.1) is None and run.progress(50.0 + R.REACH_S / 2) == 0.5
got = run.tick(50.0 + R.REACH_S)
assert got is not None and len(got) >= 10 and S.probe[0] is None and run.tick(99.0) is None
state(0, sticks=(128, 128, 255, 128))
assert S.probe[0] is None, "after the measurement samples are no longer collected"
run = R.ReachRun(0, 10.0)
run.cancel(); run.cancel()
assert S.probe[0] is None and run.tick(99.0) is None, "abort cleans up and is idempotent"
t0 = 10000 / 7
run = R.ReachRun(0, t0)
assert run.tick(t0 + R.REACH_S) is not None, "time limit robust against float rounding"
state(0)
ok("range measurement: samples from every packet for REACH_S, abort cleans up, time limit robust against float")

# ---------- wizard: rest -> ready -> range, for the chosen sticks ----------
calls = []
raw = lambda slot, secs: calls.append((slot, secs))
t = 1000.0
w = R.Wizard(0, ("right",), t, raw)
assert w.step == "rest" and w.stick == "" and calls == [(0, D.RAW_S)]
for lx in (130, 127, 129):
    state(0, sticks=(lx, 128, 131, 127))
w.tick(t + D.SETTLE_S)
for rxv in (132, 129, 130):
    state(0, sticks=(128, 128, rxv, 127))
w.tick(t + D.SETTLE_S + D.MEASURE_S)
assert w.step == "ready" and w.stick == "right" and w.proposal["right"]["dz"] >= D.MIN_DZ and calls[-1] == (0, 0), "rest -> dead zone, the sender goes back to normal"
t2 = t + D.SETTLE_S + D.MEASURE_S
w.tick(t2 + R.GET_READY_S - 0.5)
assert w.step == "ready" and 0.7 < w.progress(t2 + 1.5) < 0.8
w.tick(t2 + R.GET_READY_S)
assert w.step == "reach" and not w.done and S.probe[0] is not None, "after the pause it starts collecting samples"
for s_ in circle(0.7):
    state(0, sticks=s_)
w.tick(t2 + R.GET_READY_S + R.REACH_S)
assert w.done and w.failure is None and 0.64 <= w.proposal["right"]["outer"] <= 0.68 and S.probe[0] is None, w.proposal
assert set(w.proposal) == {"right"} and w.step == "" and w.progress(0) == 1.0
w.tick(t2 + 99), w.cancel()

# both sticks: one rest measurement, then the range of each in turn
w = R.Wizard(0, ("left", "right"), 0.0, None)
assert w.steps == [("rest", ""), ("ready", "left"), ("reach", "left"), ("ready", "right"), ("reach", "right")]
state(0)
w.tick(D.SETTLE_S); w.tick(D.SETTLE_S + D.MEASURE_S)
assert set(w.proposal) == {"left", "right"} and w.step == "ready" and w.stick == "left"
w.cancel()
assert S.probe[0] is None and w.done

# failures: stick moved at rest, incomplete circle, stick barely moved, no samples
w = R.Wizard(0, ("right",), 0.0, None)
state(0, sticks=(128, 128, 200, 128)); w.tick(D.SETTLE_S); state(0, sticks=(128, 128, 210, 128)); w.tick(D.SETTLE_S + D.MEASURE_S)
assert w.done and w.failure == R.Failure("moved") and w.proposal == {}, "stick moved at rest = repeat, no proposal"
state(0)
for reason, arc in (("few", circle(0.7, span=math.pi)), ("small", circle(0.15))):
    w = R.Wizard(0, ("right",), 0.0, None)
    state(0); w.tick(D.SETTLE_S); w.tick(D.SETTLE_S + D.MEASURE_S); w.tick(D.SETTLE_S + D.MEASURE_S + R.GET_READY_S)
    for s_ in arc:
        state(0, sticks=s_)
    w.tick(D.SETTLE_S + D.MEASURE_S + R.GET_READY_S + R.REACH_S)
    assert w.done and w.failure == R.Failure(reason, "right") and "outer" not in w.proposal["right"], (reason, w.failure)
state(0)
ok("wizard: rest -> ready -> range; both sticks in turn; moving at rest, an incomplete circle and a small range end with a message, not a guess")
