"""Gyro -> gałka (rx_gstick + rx_gstick_io): geometria osi, snap-back, delta, sprzęgło, uśpienie, kalibracja, zapis per pad,
nakładka na stan slotu (ujście) i twarde zerowanie."""
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import RX, C, S, TMP, feed                                         # noqa  (zaślepki Windows, stan slotów)
from dataclasses import replace
import rx_gstick as G
import rx_gstick_io as IO

fails = []
def check(cond, text):
    print(("OK    " if cond else "FAIL  ") + text)
    if not cond: fails.append(text)

def acc(pitch=0.0, roll=0.0):
    """Wektor grawitacji [g] dla pada przechylonego (przód w dół = pitch +, prawa strona w dół = roll +)."""
    p, r = math.radians(pitch), math.radians(roll)
    return (-math.sin(r), math.cos(p) * math.cos(r), math.sin(p) * math.cos(r))

class T:
    """Zegar i pomocnik: kolejne próbki co 4 ms."""
    def __init__(self, cfg): self.e, self.t, self.sent = G.Engine(cfg), 1.0, []
    def run(self, a, n=1, gdps=5.0, held=True, alive=True):
        for _ in range(n):
            self.t += 0.004
            if self.e.step(self.t, *a, gdps, held, alive): self.sent.append(self.e.out)
        return self.e.out

base = G.Config(enabled=True, smoothing=0.0, idle_s=0.0, deadzone=3.0, outer=30.0, delta=0.8)

# --- osie: przód w dół = Y+, prawo = X+ ---
t = T(base); t.run(acc(), 5)
o = t.run(acc(pitch=20), 3)
check(o is not None and o[1] > 0.5 and abs(o[0]) < 0.01, f"przód w dół -> Y+ ({o})")
o = t.run(acc(pitch=-20), 3)
check(o[1] < -0.5, f"przód w górę -> Y- ({o})")
o = t.run(acc(roll=20), 3); check(o[0] > 0.5 and abs(o[1]) < 0.01, f"prawo -> X+ ({o})")
o = t.run(acc(roll=-20), 3); check(o[0] < -0.5, f"lewo -> X- ({o})")
o = t.run(acc(pitch=20, roll=20), 3); check(o[0] > 0.3 and o[1] > 0.3 and math.hypot(o[0], o[1]) <= 1.0001, f"ukośnie, długość <= 1 ({o})")
o = t.run(acc(pitch=80), 3); check(math.hypot(o[0], o[1]) > 0.999, f"skraj zakresu = 1.0 ({o})")

# --- the sensor frame is detected: gravity on +Z, -Y or -Z at rest works the same way ---
def rot(a_, frame):
    """Gravity vector a_ (Y up) expressed in another sensor frame: z_up (Z = up, Y = back), y_down, z_down."""
    x, y, z = a_
    return {"z_up": (x, -z, y), "y_down": (-x, -y, z), "z_down": (x, z, -y)}[frame]
for frame in ("z_up", "y_down", "z_down"):
    t = T(base); t.run(rot(acc(), frame), 5)
    o = t.run(rot(acc(pitch=20), frame), 3)
    o2 = t.run(rot(acc(roll=20), frame), 3)
    check(o is not None and abs(o[1]) > 0.5 and abs(o2[0]) > 0.5, f"frame {frame}: tilt drives the stick ({o}, {o2})")
t = T(base); t.run((0.7, 0.7, 0.0), 5)
check(t.e.out is None and t.e.state == G.ST_SAFE, "no axis clearly vertical: waits instead of guessing")

# --- inwersje i ratio ---
t = T(replace(base, invert_pitch=True, invert_roll=True)); t.run(acc(), 5)
o = t.run(acc(pitch=20, roll=20), 3); check(o[0] < 0 and o[1] < 0, "odwrócenie pitch / roll")
t = T(replace(base, ratio=2.0)); t.run(acc(), 5)
o1 = t.run(acc(pitch=10), 3)
t2 = T(base); t2.run(acc(), 5); o2 = t2.run(acc(pitch=10), 3)
check(o1[1] > o2[1], "ratio X/Y zwiększa czułość pionu")

# --- snap-back: powrót do martwej strefy zeruje od razu mimo mocnego filtra ---
t = T(replace(base, smoothing=100.0)); t.run(acc(), 5)
t.run(acc(pitch=25), 200)
check(t.e.out[1] > 0.3, "filtr EMA doszedł do wychylenia")
o = t.run(acc(pitch=1), 1)
check(o == (0.0, 0.0, False), f"snap-back: zero w tej samej próbce ({o})")
check(t.sent[-1] == (0.0, 0.0, False), "zero zostało wysłane do ujścia")

# --- wygładzanie: bez filtra skok, z filtrem narasta ---
t = T(replace(base, smoothing=60.0)); t.run(acc(), 5)
o = t.run(acc(pitch=25), 1); full = T(base); full.run(acc(), 5); of = full.run(acc(pitch=25), 1)
check(o[1] < of[1], "EMA opóźnia narastanie")

# --- próg delta: drobna zmiana nie jest wysyłana ---
t = T(replace(base, delta=5.0)); t.run(acc(), 5); t.run(acc(pitch=15), 3)
n = len(t.sent); t.run(acc(pitch=15.1), 3)
check(len(t.sent) == n, "zmiana poniżej progu delta nie idzie do ujścia")
t.run(acc(pitch=20), 3); check(len(t.sent) > n, "większa zmiana idzie")

# --- krzywe ---
check(G.curve(0.5, "cubic") < 0.5 < G.curve(0.5, "aggressive") and G.curve(0.5, "linear") == 0.5, "krzywe: sześcienna < liniowa < agresywna")

# --- sprzęgło: puszczone = gyro nie steruje ---
t = T(base); t.run(acc(), 5)
check(t.run(acc(pitch=20), 3, held=False) is None and t.e.state == G.ST_HOLD, "sprzęgło puszczone: brak wyjścia")
check(t.sent[-1] is None, "oddanie wejścia fizycznemu padowi wysłane do ujścia")
o = t.run(acc(pitch=20), 3, held=True)
check(o is not None and o[1] > 0.5, "sprzęgło wciśnięte: steruje od razu (położenie pada jest bezwzględne)")

# --- auto-sleep ---
t = T(replace(base, idle_s=1.5)); t.run(acc(), 5)
t.run(acc(pitch=20), 10, gdps=30.0); check(t.e.out[1] > 0.5, "ruch: steruje")
t.run(acc(pitch=20), int(1.6 / 0.004), gdps=0.0)
check(t.e.state == G.ST_IDLE and t.e.out is None, "nieruchomy pad > 1,5 s: uśpione, wejście oddane")
t.run(acc(pitch=20), 3, gdps=60.0)
check(t.e.state != G.ST_IDLE, "wyraźny ruch budzi")

# --- utrata pada i wyłączenie ---
t = T(base); t.run(acc(), 5); t.run(acc(pitch=20), 3)
t.run(acc(pitch=20), 1, alive=False); check(t.e.out is None and t.e.state == G.ST_OFF, "pad offline: oddaje wejście")
t = T(base); t.run(acc(), 5); t.run(acc(pitch=20), 3)
t.e.release(); check(t.e.out is None, "release(): twarde zerowanie")
t.t += 0.004; t.e.step(t.t, *acc(pitch=20), 5.0, True)
check(t.e.out is None, "po release przechylony pad czeka na powrót do środka")
t = T(base); t.run(acc(), 5); t.e.cfg = replace(base, enabled=False)
check(t.run(acc(pitch=20), 2) is None, "funkcja wyłączona: brak wyjścia")

# --- sprint na skraju ---
t = T(replace(base, sprint="l3", sprint_at=90.0)); t.run(acc(), 5)
o = t.run(acc(pitch=60), 3); check(o[2] is True, "sprint na skraju zakresu")
o = t.run(acc(pitch=10), 3); check(o[2] is False, "sprint puszczony")

# --- preview while the feature is off: tracks the tilt, sends nothing ---
t = T(replace(base, enabled=False)); t.e.preview = True; t.run(acc(), 5)
t.run(acc(pitch=20), 3)
check(t.e.out is None and all(s is None for s in t.sent) and t.e.raw[1] > 10, f"preview: the tilt is tracked ({t.e.raw}) but there is no output")
t.e.preview = False; t.run(acc(pitch=20), 2); check(t.e.state == G.ST_OFF and t.e.raw == (0.0, 0.0), "preview off: idle again")

# --- kalibracja środka ---
t = T(base); t.run(acc(), 5)
tilt = acc(pitch=15)
t.e.start_center(t.t, 0.4); t.run(tilt, 120, gdps=1.0)
check(t.e.cal_result and t.e.cal_result[0], "kalibracja wyszła")
cfg = replace(base, center=t.e.cal_result[1]); t.e.cfg = cfg
t.run(tilt, 5); o = t.run(tilt, 3)
check(o == (0.0, 0.0, False) and t.e.raw[1] == (t.e.raw[1]) and abs(t.e.raw[1]) < 0.5, f"w nowym środku wyjście 0 ({t.e.raw})")
t.run(acc(pitch=35), 3); check(t.e.out[1] > 0.4, "przechył względem nowego środka")
t = T(base); t.e.start_center(t.t, 0.4); t.run(acc(), 120, gdps=200.0)
check(t.e.cal_result == (False, None), "kalibracja w ruchu odrzucona")

# --- skala 16-bit i 8-bit ---
check(G.to_i16(1) == 32767 and G.to_i16(-1) == -32768 and G.to_i16(0) == 0, "zakres -32768..32767")
check(G.to_u8(0) == 128 and G.to_u8(1) == 255 and G.to_u8(-1) == 1, "bajt gałki DS4")

# --- plik ustawień ---
bad = G.normalize({"deadzone": "x", "outer": 5, "curve": "zig", "enabled": 1, "nope": 1, "center": [9, 9, 9]})
check(bad.deadzone == 3.0 and bad.curve == "linear" and bad.enabled is False and bad.center is None and bad.outer >= bad.deadzone + G.MIN_SPAN,
      "normalize: złe wartości = domyślne, zasięg > martwa strefa")
st = IO.Store(os.path.join(TMP, "gyrostick.json"))
c = replace(base, target="right", center=(0.0, 0.8, 0.6), clutch="l1")
st.set("AA:BB:CC:DD:EE:FF", c)
st2 = IO.Store(os.path.join(TMP, "gyrostick.json"))
check(st2.get("aa:bb:cc:dd:ee:ff") == G.normalize(G.to_raw(c)) and st2.get("11:22:33:44:55:66") == G.Config(),
      "ustawienia zapisane per pad (MAC), inny pad ma domyślne")

# --- ujście i nakładka na stan slotu ---
r = IO.Runner(IO.Store(os.path.join(TMP, "g2.json"))); r.init()
S.alive[0] = True
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc())
r.set_config(0, replace(base, target="left"))
r._step_slot(0, 10.0); r._step_slot(0, 10.004)
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc(pitch=25))
for k in range(3): r._step_slot(0, 10.01 + k * 0.004)
s = S.states[0]
check(s["gs"] is not None and s["ly"] < 128 - 50 and abs(s["lx"] - 128) <= 1, f"przód w dół: lewa gałka w górę (ly={s['ly']})")
S.states[0]["rawst"] = (10, 10, 77, 77)
with S.lock: S.recompute(S.states[0], S.remap_maps[0])
check(s["ly"] < 128 - 50 and s["rx"] == 77, "kolejny pakiet nie nadpisuje gałki gyro, prawa fizyczna działa")
r.force_release_all()
check(s["gs"] is None and s["ly"] == 10, "force release: fizyczna gałka znów działa")
r.set_config(0, replace(base, target="dpad"))
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc())
r._step_slot(0, 10.9); r._step_slot(0, 10.904)
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc(pitch=25))
for k in range(3): r._step_slot(0, 11.0 + k * 0.004)
check(s["hy"] == -1, f"tryb D-pad: przód w dół = góra (hy={s['hy']})")
r.force_release_all()
r.set_config(0, replace(base, target="right", sprint="l3", sprint_at=60.0))
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc())
r._step_slot(0, 12.0); r._step_slot(0, 12.004)
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc(pitch=60))
for k in range(3): r._step_slot(0, 12.01 + k * 0.004)
check(s["mask"] & G.sprint_mask("l3") and s["ry"] < 20, "sprint wciska wirtualne L3, prawa gałka w górę")
r.force_release_all()
check(not s["mask"] & G.sprint_mask("l3"), "force release zwalnia sprint")
S.alive[0] = False

# --- sygnał zwrotny (haptyka / LED) przy przejściu przez progi stref ---
pulses = []
r = IO.Runner(IO.Store(os.path.join(TMP, "g3.json")), pulse=lambda slot, kind, force=False: pulses.append((slot, kind, force)))
r.init(); S.alive[0] = True
def fb_run(mode, seq):
    pulses.clear(); r.engines[0].release(); r.engines[0].t = 0.0; r._fb_t[0] = 0.0
    r.set_config(0, replace(base, feedback=mode)); now = 20.0
    for a_, n in seq:
        S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in a_)
        for _k in range(n):
            now += 0.004; r._step_slot(0, now)
    return [p[1] for p in pulses]
seq = [(acc(), 5), (acc(pitch=60), 45), (acc(pitch=0.5), 5)]
check(fb_run("off", seq) == [], "feedback off: no pulses")
check(fb_run("haptic", seq) == ["tilt_edge_h", "tilt_center_h"], f"haptic: edge then center ({pulses})")
check(fb_run("led", seq) == ["tilt_edge_l", "tilt_center_l"], "led: colour pulses")
check(fb_run("both", seq) == ["tilt_edge_b", "tilt_center_b"] and all(p[2] for p in pulses), "both: rumble + colour, forced")
check(fb_run("haptic", [(acc(), 5), (acc(pitch=10), 5), (acc(pitch=12), 5)]) == [], "leaving the deadzone / moving inside is silent")
import rx_feedback as RF
check(all(k in RF.PULSES for k in ("tilt_edge_h", "tilt_edge_l", "tilt_edge_b", "tilt_center_h", "tilt_center_l", "tilt_center_b")),
      "every feedback kind exists in rx_feedback.PULSES")
S.alive[0] = False

# --- the whole path: state packet -> rx_net -> 250 Hz thread -> slot state (what the game gets) ---
import threading, time
from proto import T_STATE
import rx_gstick_io as IO2
IO2.runner = rr = IO.Runner(IO.Store(os.path.join(TMP, "g4.json")))
rr.init()
def pkt(a_):
    ax, ay, az = (round(v * C.ACC_RES) for v in a_)
    feed(T_STATE, 0, 128, 128, 0, 128, 128, 0, 0, 0, 0, 80, 0, ax, ay, az, 0, 0, 0)
S.stop.clear()
th = threading.Thread(target=rr.loop, daemon=True); th.start()
pkt(acc())
rr.set_config(0, replace(base, target="left", idle_s=0.0))
time.sleep(0.15); pkt(acc(pitch=25)); time.sleep(0.25)
check(S.states[0]["ly"] < 60 and abs(S.states[0]["lx"] - 128) <= 2, f"end to end: front down moves the left stick up (ly={S.states[0]['ly']})")
pkt(acc()); time.sleep(0.25)
check(S.states[0]["ly"] == 128, "end to end: back in the deadzone the stick is centered")
pkt(acc(pitch=25)); time.sleep(0.1)
rr.set_config(0, replace(base, enabled=False)); time.sleep(0.3)
check(S.states[0]["gs"] is None and S.states[0]["ly"] == 128, "end to end: switching it off gives the stick back")
S.stop.set(); th.join(2); S.stop.clear()
S.states[0]["gs"] = None; S.reset_state(0)
rr._driving[0] = True; rr.engines[0].sent = (0.0, 0.5, False)          # the state was reset behind our back
rr.set_config(0, replace(base, target="left")); S.alive[0] = True
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc())
for k in range(3): rr._step_slot(0, 39.0 + k * 0.004)
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc(pitch=25))
rr._driving[0] = True; rr.engines[0].sent = (0.0, 0.5, False); S.reset_state(0)
S.states[0]["acc"] = tuple(round(v * C.ACC_RES) for v in acc(pitch=25))
for k in range(4): rr._step_slot(0, 40.0 + k * 0.004)
check(S.states[0]["gs"] is not None, "a state reset behind our back: the output is put back")
check(not th.is_alive(), "the thread stops with the receiver")

# --- okno ---
try:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
except Exception as e:
    print("SKIP  okno (brak PySide6):", e)
else:
    import rx_gstick_io
    from rx_qt_gstick import TiltSection
    rx_gstick_io.runner = r
    import rx_qt_gstick; rx_qt_gstick.runner = r
    page = TiltSection(); got = []
    page.changed.connect(got.append)
    page.on.setChecked(True); page.sl["deadzone"].set(5); page.sl["deadzone"]._moved(0)
    page.target.set_data("right"); page.target.changed.emit("right")
    check(got and got[-1].target == "right" and got[-1].enabled and got[-1].deadzone == 5.0, "okno: przełączniki i suwaki emitują Config")
    check(not page.preview_cat.is_open() and all(not c.is_open() for c in page.cats), "every tilt category starts collapsed")
    page.tick(); check(rx_gstick_io.runner.engines[0].preview is False, "collapsed preview: the engine is not asked to follow the pad")
    page.preview_cat.set_open(True); page.tick()
    check(rx_gstick_io.runner.engines[0].preview is True, "open preview: the engine follows the pad")
    check("Raw" in page.numbers.text() and page.status.text(), "podgląd diagnostyczny wypełniony")
    page.view.resize(200, 200); page.view.grab()
    page.refresh(replace(base, outer=10.0, deadzone=2.0)); page.view.grab()

print("GSTICK OK" if not fails else f"FAIL  {len(fails)}")
sys.exit(1 if fails else 0)
