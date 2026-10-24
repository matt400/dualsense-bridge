"""Receiver: tymczasowa zamiana graczy P1 <-> P2 (rx_swap) - routing stanu i feedbacku, akcja mastera, wyłączanie przy zmianie padów.
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

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()

PS, SQUARE, CROSS, TRIANGLE = 316, 308, 304, 307
A, B = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02"
pulses = []
F.pulse = lambda i, kind, force=False: pulses.append((i, kind))

# wirtualne pady-zaślepki: zapamiętują, czyj stan dostały (Out.send woła build(state, counter, mouse_mode, buf))
reports = {0: [], 1: []}
def fake_out(idx):
    pad = types.SimpleNamespace(buf=bytearray(64), submit=lambda: None,
                                build=lambda st, counter, mouse, ba, i=idx: reports[i].append(st["mask"]))
    return rx_ds4.Out(pad)

C.NUM_PADS = 3                                       # trzeci slot: sprawdzamy, że zamiana działa tylko przy dokładnie dwóch padach
S.init(store); M.init(3); H.init(3); F.init(); rx_master.init(3); rxenv.rx_testmode.init(3)
rx_ds4.init()
for i in (0, 1):
    rx_ds4.outs[i] = fake_out(i)
rx_ds4.interval = 0.0

def ident(i, pid):
    feed(T_IDENT, i, pid.encode())

def settings(**master):
    rx_settings.apply(Settings(MouseConfig(), MasterConfig(**master)))

# ---------- bez zamiany: identyczność, zamiana tylko przy dokładnie dwóch padach ----------
check([rx_swap.route(i) for i in range(4)] == [0, 1, 2, 3] and not rx_swap.active(), "bez zamiany route() = tożsamość")
ok, msg = rx_swap.toggle()
check(not ok and not rx_swap.active(), f"bez połączonych padów zamiana odmówiona: {msg}")
ident(0, A); state(0)
ok, _msg = rx_swap.toggle()
check(not ok and not rx_swap.active(), "jeden pad: zamiana odmówiona")
ident(1, B); state(1)
state(2)
ok, _msg = rx_swap.toggle()
check(not ok and not rx_swap.active(), "trzy pady: zamiana odmówiona")
S.want[2].clear(); S.alive[2] = False

# ---------- zamiana: stan idzie do partnera ----------
state(0, mask=BIT[CROSS]); state(1, mask=0)
ok, msg = rx_swap.toggle()
check(ok and rx_swap.active() and rx_swap.route(0) == 1 and rx_swap.route(1) == 0 and rx_swap.route(2) == 2,
      f"dwa pady: zamiana włączona: {rx_swap.pair}")
for r in reports.values():
    r.clear()
state(0, mask=BIT[CROSS] | BIT[SQUARE])             # zmiana na fizycznym padzie 0 ...
check(reports[1] and reports[1][-1] & BIT[SQUARE] and not reports[0], f"... trafia do wirtualnego pada 1: {reports}")
state(1, mask=BIT[TRIANGLE])                            # (PS nie nadaje się: sam PS idzie do gry dopiero po puszczeniu)
check(reports[0] and reports[0][-1] & BIT[TRIANGLE] and not reports[0][-1] & BIT[SQUARE], "pad 1 steruje wirtualnym padem 0")
check(any(m & BIT[CROSS] for m in reports[1]), "wirtualny pad 1 dostał stan pada 0 od razu po włączeniu / zmianie")

# ---------- feedback gry: rumble wirtualnego pada idzie na fizyczny pad, który go steruje ----------
F.game(0, large=200, small=10)                       # gra rumbluje gracza 0 = (po zamianie) fizyczny pad 1
check((F.fb[1]["large"], F.fb[1]["small"]) == (200, 10) and F.fb[0]["large"] == 0, f"rumble gracza 0 -> pad 1: {F.fb[0]['large']}/{F.fb[1]['large']}")
F.cbs[1](None, None, 77, 5, 0, None)                  # to samo przez callback ViGEm
check(F.fb[0]["large"] == 77, "callback ViGEm gracza 1 -> pad 0")

# ---------- wyłączenie: feedback wraca razem z routingiem ----------
F.fb[0]["large"], F.fb[1]["large"] = 10, 20
ok, msg = rx_swap.toggle()
check(ok and not rx_swap.active() and rx_swap.route(0) == 0, "ponowne wywołanie przywraca graczy")
check((F.fb[0]["large"], F.fb[1]["large"]) == (20, 10), "stan feedbacku wirtualnych padów wymieniony z powrotem")
F.game(0, large=99)
check(F.fb[0]["large"] == 99, "po powrocie rumble gracza 0 idzie na pad 0")

# ---------- zamiana kończy się, gdy pad się rozłącza lub dochodzi ----------
rx_swap.toggle()
check(rx_swap.active(), "znów włączona")
N._on_bye(1)
check(not rx_swap.active() and rx_swap.route(0) == 0, "rozłączenie pada kończy zamianę")
ident(1, B); state(1)
rx_swap.toggle()
check(rx_swap.active(), "pad wrócił - można włączyć ponownie")
S.want[1].clear(); S.alive[1] = False; S.last_seen[1] = 0
N.check_stale(time.monotonic() + 3600)
check(not rx_swap.active(), "zanik sygnału pada (odłączenie wirtualnego) kończy zamianę")
ident(1, B); state(1)
rx_swap.toggle()
S.want[2].clear()
state(2)                                              # nowy, trzeci pad
check(not rx_swap.active(), "nowy pad kończy zamianę")
N._on_bye(2)
ident(1, B); state(1)

# ---------- skrót na padzie: PS + kwadrat (akcja mastera) ----------
settings(pad="", only_master=("actions",))            # domyślne akcje, w tym swap = kwadrat
check(rx_settings.cur.master.actions["swap"] == "square", f"domyślny przycisk zamiany: {rx_settings.cur.master.actions}")
pulses.clear()
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[SQUARE])
check(wait_for(rx_swap.active), "PS + kwadrat włącza zamianę")
check(not S.states[0]["mask"] & BIT[SQUARE], "kwadrat z kombinacji nie trafia do gry")
check(wait_for(lambda: {(0, "action"), (1, "action")} <= set(pulses)), f"oba pady dostają potwierdzenie: {pulses}")
state(0); time.sleep(0.05)
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[SQUARE])
check(wait_for(lambda: not rx_swap.active()), "drugie PS + kwadrat przywraca graczy")
state(0)
S.want[1].clear(); S.alive[1] = False
pulses.clear()
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[SQUARE])
check(wait_for(lambda: (0, "denied") in pulses) and not rx_swap.active(), "jeden pad: czerwone mignięcie, bez zamiany")
state(0)

# ---------- ustawienia: akcja dodana później dostaje domyślny przycisk ----------
path = os.path.join(TMP, "s.json")
json.dump({"master": {"actions": {"pause": "options", "close_game": "", "sleep": "", "shutdown": ""}}}, open(path, "w"))
check(rx_settings.load(path).master.actions["swap"] == "square", "stary settings.json bez swap -> domyślny przycisk")
json.dump({"master": {"actions": {"pause": "square", "swap": ""}}}, open(path, "w"))
a = rx_settings.load(path).master.actions
check(a["pause"] == "square" and a["swap"] == "", f"jawnie puste = wyłączone: {a}")
json.dump({"master": {"actions": {"sleep": "square"}}}, open(path, "w"))
a = rx_settings.load(path).master.actions
check(a["sleep"] == "square" and a["swap"] == "", f"przycisk zajęty przez inną akcję -> swap wyłączony: {a}")

print("BŁĘDY:" if fails else "WSZYSTKO OK", *fails, sep="\n" if fails else " ")
sys.exit(1 if fails else 0)
