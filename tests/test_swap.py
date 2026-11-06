"""Receiver: temporary P1 <-> P2 player swap (rx_swap) - state and feedback routing, the master action, turning off when pads change.
    python3 tests/test_swap.py receiver"""
import json, os, sys, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rxenv
import rx_ds4, rx_feedback as F, rx_master, rx_settings, rx_swap
import rx_state as S
from dataclasses import replace
from proto import T_IDENT
from rx_settings import MasterConfig, MouseConfig, Settings

from chk import check, fails, wait_for                                  # noqa: E402  (shared helper, tests/chk.py)


PS, SQUARE, CROSS, TRIANGLE = 316, 308, 304, 307
A, B = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02"
pulses = []
F.pulse = lambda i, kind, force=False: pulses.append((i, kind))

# stub virtual pads: they remember whose state they got (Out.send calls build(state, counter, mouse_mode, buf))
reports = {0: [], 1: []}
def fake_out(idx):
    pad = types.SimpleNamespace(buf=bytearray(64), submit=lambda: None,
                                build=lambda st, counter, mouse, ba, i=idx: reports[i].append(st["mask"]))
    return rx_ds4.Out(pad)

C.NUM_PADS = 3                                       # a third slot: checks that the swap works only with exactly two pads
S.init(store); M.init(3); H.init(3); F.init(); rx_master.init(3); rxenv.rx_testmode.init(3)
rx_ds4.init()
for i in (0, 1):
    rx_ds4.outs[i] = fake_out(i)
rx_ds4.interval = 0.0

def ident(i, pid):
    feed(T_IDENT, i, pid.encode())

def settings(**master):
    rx_settings.apply(Settings(MouseConfig(), MasterConfig(**master)))

# ---------- no swap: identity, the swap only with exactly two pads ----------
check([rx_swap.route(i) for i in range(4)] == [0, 1, 2, 3] and not rx_swap.active(), "without a swap route() = identity")
ok, msg = rx_swap.toggle()
check(not ok and not rx_swap.active(), f"no connected pads: swap refused: {msg}")
ident(0, A); state(0)
ok, _msg = rx_swap.toggle()
check(not ok and not rx_swap.active(), "one pad: swap refused")
ident(1, B); state(1)
state(2)
ok, _msg = rx_swap.toggle()
check(not ok and not rx_swap.active(), "three pads: swap refused")
S.want[2].clear(); S.alive[2] = False

# ---------- swap: the state goes to the partner ----------
state(0, mask=BIT[CROSS]); state(1, mask=0)
ok, msg = rx_swap.toggle()
check(ok and rx_swap.active() and rx_swap.route(0) == 1 and rx_swap.route(1) == 0 and rx_swap.route(2) == 2,
      f"two pads: swap on: {rx_swap.pair}")
for r in reports.values():
    r.clear()
state(0, mask=BIT[CROSS] | BIT[SQUARE])             # a change on physical pad 0 ...
check(reports[1] and reports[1][-1] & BIT[SQUARE] and not reports[0], f"... reaches virtual pad 1: {reports}")
state(1, mask=BIT[TRIANGLE])                            # (PS does not fit: PS alone reaches the game only after release)
check(reports[0] and reports[0][-1] & BIT[TRIANGLE] and not reports[0][-1] & BIT[SQUARE], "pad 1 drives virtual pad 0")
check(any(m & BIT[CROSS] for m in reports[1]), "virtual pad 1 got the state of pad 0 right after the switch / change")

# ---------- game feedback: the virtual pad's rumble goes to the physical pad that drives it ----------
F.game(0, large=200, small=10)                       # the game rumbles player 0 = (after the swap) physical pad 1
check((F.fb[1]["large"], F.fb[1]["small"]) == (200, 10) and F.fb[0]["large"] == 0, f"rumble gracza 0 -> pad 1: {F.fb[0]['large']}/{F.fb[1]['large']}")
F.cbs[1](None, None, 77, 5, 0, None)                  # the same through the ViGEm callback
check(F.fb[0]["large"] == 77, "callback ViGEm gracza 1 -> pad 0")

# ---------- turning off: feedback returns together with the routing ----------
F.fb[0]["large"], F.fb[1]["large"] = 10, 20
ok, msg = rx_swap.toggle()
check(ok and not rx_swap.active() and rx_swap.route(0) == 0, "calling again restores the players")
check((F.fb[0]["large"], F.fb[1]["large"]) == (20, 10), "the feedback state of the virtual pads swapped back")
F.game(0, large=99)
check(F.fb[0]["large"] == 99, "po powrocie rumble gracza 0 idzie na pad 0")

# ---------- the swap ends when a pad disconnects or joins ----------
rx_swap.toggle()
check(rx_swap.active(), "on again")
N._on_bye(1)
check(not rx_swap.active() and rx_swap.route(0) == 0, "a pad disconnect ends the swap")
ident(1, B); state(1)
rx_swap.toggle()
check(rx_swap.active(), "the pad came back - can be turned on again")
S.want[1].clear(); S.alive[1] = False; S.last_seen[1] = 0
N.check_stale(time.monotonic() + 3600)
check(not rx_swap.active(), "a pad signal loss (virtual detach) ends the swap")
ident(1, B); state(1)
rx_swap.toggle()
S.want[2].clear()
state(2)                                              # nowy, trzeci pad
check(not rx_swap.active(), "a new pad ends the swap")
N._on_bye(2)
ident(1, B); state(1)

# ---------- shortcut on the pad: PS + square (a master action) ----------
settings(pad="", only_master=("actions",))            # default actions, swap = square among them
check(rx_settings.cur.master.actions["swap"] == "square", f"default swap button: {rx_settings.cur.master.actions}")
pulses.clear()
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[SQUARE])
check(wait_for(rx_swap.active), "PS + square turns the swap on")
check(not S.states[0]["mask"] & BIT[SQUARE], "square from the combo does not reach the game")
check(wait_for(lambda: {(0, "action"), (1, "action")} <= set(pulses)), f"both pads get a confirmation: {pulses}")
state(0); time.sleep(0.05)
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[SQUARE])
check(wait_for(lambda: not rx_swap.active()), "drugie PS + kwadrat przywraca graczy")
state(0)
S.want[1].clear(); S.alive[1] = False
pulses.clear()
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[SQUARE])
check(wait_for(lambda: (0, "denied") in pulses) and not rx_swap.active(), "one pad: a red flash, no swap")
state(0)

# ---------- settings: an action added later gets the default button ----------
path = os.path.join(TMP, "s.json")
json.dump({"master": {"actions": {"pause": "options", "close_game": "", "sleep": "", "shutdown": ""}}}, open(path, "w"))
check(rx_settings.load(path).master.actions["swap"] == "square", "old settings.json without swap -> default button")
json.dump({"master": {"actions": {"pause": "square", "swap": ""}}}, open(path, "w"))
a = rx_settings.load(path).master.actions
check(a["pause"] == "square" and a["swap"] == "", f"explicitly empty = off: {a}")
json.dump({"master": {"actions": {"sleep": "square"}}}, open(path, "w"))
a = rx_settings.load(path).master.actions
check(a["sleep"] == "square" and a["swap"] == "", f"button taken by another action -> swap off: {a}")

print("FAILURES:" if fails else "ALL OK", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
