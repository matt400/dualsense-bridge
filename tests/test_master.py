"""Pad master (rx_master), ustawienia (rx_settings) i stabilizacja kliku touchpada (rx_mouse)."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rxenv
import rx_console, rx_master, rx_settings
from dataclasses import replace
from proto import T_CLICK, T_IDENT, T_MOUSE
from rx_console import Console, ConsoleConfig
from rx_settings import MasterConfig, MouseConfig, Settings

ok = lambda msg: print("OK  ", msg)
fails = []
def check(cond, msg):
    if not cond:
        fails.append(msg); print("FAIL", msg)

def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()

PS, SHARE, CROSS, TRI, OPT, R3 = 316, 314, 304, 307, 315, 318
A, B = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02"
pulses = []
F.pulse = lambda i, kind: pulses.append((i, kind))
actions = []
W.close_foreground = lambda: actions.append("close") or True
W.sleep_pc = lambda: actions.append("sleep")
W.shutdown_pc = lambda *a: actions.append("shutdown")

def ident(i, pid):
    feed(T_IDENT, i, pid.encode())

def settings(**master):
    rx_settings.apply(Settings(MouseConfig(), MasterConfig(**master)))

ident(0, A); ident(1, B)
state(0); state(1)

# ---------- ustawienia: domyślne, błędne wartości, zapis ----------
path = os.path.join(TMP, "s.json")
check(rx_settings.load(path) == rx_settings.defaults(), "brak pliku -> domyślne")
json.dump({"mouse": {"sens": 9, "click_freeze_ms": 200}, "master": {"pad": " AA:AA:AA:AA:AA:01 ", "when_absent": "x",
           "only_master": ["console", "nie-ma", "console", "ps_game"], "actions": {"pause": "share", "sleep": "cross",
           "shutdown": "cross"}, "claim_hold": "tak"}}, open(path, "w"))
s = rx_settings.load(path)
check(s.mouse.sens == 0.5 and s.mouse.click_freeze_ms == 200, f"zła czułość -> domyślna, reszta wczytana: {s.mouse}")
check(s.master.pad == A and s.master.when_absent == "all" and s.master.only_master == ("console", "ps_game"), f"master: {s.master}")
check(s.master.actions == {"pause": "", "close_game": "", "sleep": "cross", "shutdown": ""} and s.master.claim_hold is True,
      f"akcje: Share (skrót PS) niedozwolony, przycisk nie dwa razy: {s.master.actions}")
rx_settings.save(s, path)
check(rx_settings.load(path) == s and not os.path.exists(path + ".tmp"), "zapis i odczyt bez zmian")
open(path, "w").write("{zepsuty")
check(rx_settings.load(path) == rx_settings.defaults(), "uszkodzony plik -> domyślne")
ok("settings.json: domyślne, poprawki, zapis atomowy")

# ---------- uprawnienia ----------
settings(pad="")
check(all(rx_master.allowed(i, f) for i in (0, 1) for f in rx_settings.MASTER_FEATURES), "bez mastera każdy może wszystko")
settings(pad=A, only_master=("console", "ps_game"))
check(rx_master.master_slot() == 0 and rx_master.is_master(0) and not rx_master.is_master(1), "master = pad A w slocie 0")
check(rx_master.allowed(0, "console") and not rx_master.allowed(1, "console") and rx_master.allowed(1, "profile"),
      "zastrzeżone tylko dla mastera, reszta dla wszystkich")
ident(0, B); ident(1, A)                                    # pady zamieniły się slotami - master idzie za padem
check(rx_master.master_slot() == 1 and rx_master.allowed(1, "console") and not rx_master.allowed(0, "console"),
      "master wg MAC, nie wg slotu")
ident(0, A); ident(1, B)
settings(pad="cc:cc:cc:cc:cc:03", only_master=("console",), when_absent="all")
check(rx_master.allowed(1, "console"), "master niepodłączony + all -> każdy może")
settings(pad="cc:cc:cc:cc:cc:03", only_master=("console",), when_absent="nobody")
check(not rx_master.allowed(0, "console") and not rx_master.allowed(1, "console"), "master niepodłączony + nobody -> nikt")
ok("uprawnienia: zastrzeżone funkcje, master wg MAC, master nieobecny")

# ---------- egzekwowanie: tryb konsoli, PS w grze, skróty PS ----------
settings(pad=A, only_master=("console", "ps_game", "screenshot", "profile", "mouse"))
calls = []
rx_console._display = lambda mode: calls.append(mode)
rx_console._audio = rx_console._toast = lambda *a: None
c = Console(ConsoleConfig(toggle_button="r2", auto_on_master=False)); c.start(); rx_console.ctl = c
wait_for(lambda: calls); calls.clear(); pulses.clear()
for i in (1, 1):
    state(1, rt=255); time.sleep(0.03); state(1); time.sleep(0.03)
time.sleep(0.3)
check(not c.on and (1, "denied") in pulses, f"pad 1 (nie master): podwójne R2 nic nie przełącza, czerwone mignięcie: {pulses}")
state(0, rt=255); time.sleep(0.03); state(0); time.sleep(0.03); state(0, rt=255); time.sleep(0.03); state(0)
check(wait_for(lambda: c.on), "master: podwójne R2 przełącza tryb konsoli")
c.close(restart=True); rx_console.ctl = None

pulses.clear()
state(1, mask=BIT[PS]); time.sleep(0.03); state(1); time.sleep(0.03)
check(not S.states[1]["mask"] & BIT[PS] and (1, "denied") in pulses, "nie master: PS nie trafia do gry")
state(0, mask=BIT[PS]); time.sleep(0.02); state(0)
check(wait_for(lambda: S.states[0]["mask"] & BIT[PS], 0.5), "master: krótkie PS trafia do gry (impuls)")
time.sleep(0.2)
rxenv.calls.clear()
state(1, mask=BIT[PS]); state(1, mask=BIT[PS] | BIT[SHARE]); time.sleep(0.1); state(1, mask=BIT[PS]); state(1)
time.sleep(0.1)
check(not any(x[0] == "k" for x in rxenv.calls), f"nie master: PS + Share bez zrzutu ekranu: {rxenv.calls}")
check(not S.states[1]["mask"] & BIT[SHARE], "Share zużyty w skrócie nie idzie do gry")
mode = S.mouse_mode[1]
state(1, mask=BIT[PS]); feed(T_CLICK, 1, 1); feed(T_CLICK, 1, 0); state(1); time.sleep(0.05)
check(S.mouse_mode[1] == mode and not S.states[1]["tp"], "nie master: PS + klik touchpada nie zmienia trybu myszy")
settings(pad=A, only_master=("console",))
state(1, mask=BIT[PS]); time.sleep(0.02); state(1)
check(wait_for(lambda: S.states[1]["mask"] & BIT[PS], 0.5), "PS niezastrzeżony -> działa na każdym padzie")
time.sleep(0.2)
ok("egzekwowanie: tryb konsoli, PS do gry, PS + Share, PS + touchpad")

# ---------- akcje PS + przycisk ----------
rx_master.HOLD_S = 0.15
settings(pad=A, only_master=("actions",), actions={"pause": "options", "close_game": "triangle", "sleep": "", "shutdown": "cross"})
rxenv.calls.clear(); actions.clear()
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[OPT])
check(wait_for(lambda: ("k", W.VK_ESCAPE) in rxenv.calls), "PS + Options -> Esc (pauza)")
check(not S.states[0]["mask"] & BIT[OPT], "Options z kombinacji nie trafia do gry")
state(0, mask=BIT[OPT]); time.sleep(0.2)
check(not S.states[0]["mask"] & BIT[OPT], "puszczony PS, trzymany Options dalej zablokowany do puszczenia")
check(not S.states[0]["mask"] & BIT[PS], "PS użyty w kombinacji nie trafia do gry po puszczeniu")
state(0); state(0, mask=BIT[OPT])
check(S.states[0]["mask"] & BIT[OPT], "po puszczeniu Options znów działa normalnie")
state(0)

S.game = "ittakestwo.exe"
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[TRI]); time.sleep(0.05); state(0, mask=BIT[PS]); time.sleep(0.3)
check("close" not in actions, "zamknięcie gry: puszczone przed czasem -> nic")
state(0, mask=BIT[PS] | BIT[TRI])
check(wait_for(lambda: "close" in actions), "zamknięcie gry: przytrzymane -> WM_CLOSE")
state(0)
S.game = "explorer.exe"; actions.clear()
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[TRI]); time.sleep(0.4); state(0)
check("close" not in actions, "pulpit (explorer) nie jest zamykany")
S.game = None
pulses.clear(); actions.clear()
state(1, mask=BIT[PS]); state(1, mask=BIT[PS] | BIT[CROSS]); time.sleep(0.4); state(1)
check("shutdown" not in actions and (1, "denied") in pulses, "nie master: PS + przycisk nic nie robi")
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[CROSS])
check(wait_for(lambda: "shutdown" in actions), "master: PS + krzyżyk przytrzymane -> wyłączenie PC")
state(0)
ok("akcje mastera: pauza, zamknięcie gry z przytrzymaniem, wyłączenie; przyciski kombinacji nie idą do gry")

# ---------- przejęcie mastera: PS + R3 ----------
rx_master.CLAIM_S = 0.2
settings(pad=A, claim_hold=True)
pulses.clear()
state(1, mask=BIT[PS]); state(1, mask=BIT[PS] | BIT[R3]); time.sleep(0.1); state(1)
time.sleep(0.2)
check(rx_settings.cur.master.pad == A, "krótkie PS + R3 nie przejmuje")
state(1, mask=BIT[PS]); state(1, mask=BIT[PS] | BIT[R3])
check(not S.states[1]["mask"] & BIT[R3], "R3 z kombinacji nie trafia do gry")
check(wait_for(lambda: rx_settings.cur.master.pad == B), "PS + R3 trzymane CLAIM_S -> pad B jest masterem")
state(1)
check((1, "master") in pulses and json.load(open(C.SETTINGS_PATH))["master"]["pad"] == B, "złoty błysk i zapis do settings.json")
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[R3]); time.sleep(0.05); state(0, mask=BIT[PS]); time.sleep(0.3); state(0)
check(rx_settings.cur.master.pad == B, "R3 puszczone przed czasem nie przejmuje mastera")
settings(pad=B, claim_hold=False)
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[R3])
check(S.states[0]["mask"] & BIT[R3], "claim_hold wyłączony -> R3 zwykłym przyciskiem")
time.sleep(0.35); state(0)
check(rx_settings.cur.master.pad == B, "claim_hold wyłączony -> przytrzymanie nic nie robi")
ok("przejęcie mastera: PS + R3, zapis, wyłączalne")

# ---------- mysz: stabilizacja kliknięcia ----------
rx_settings.apply(Settings(MouseConfig(sens=1.0, click_freeze_ms=100, click_rewind_ms=80, drag_threshold=30), MasterConfig()))
check(M.sens == [1.0] * C.NUM_PADS, "suwak czułości ustawia wszystkie pady")
S.mouse_mode[0] = True; M.reset(0); rxenv.calls.clear()
feed(T_MOUSE, 0, 50, 0, 0)                       # dawny ruch - poza oknem cofania
time.sleep(0.12)
feed(T_MOUSE, 0, 3, 1, 0); feed(T_MOUSE, 0, 2, -1, 0)     # docisk palca tuż przed klikiem
rxenv.calls.clear()
feed(T_CLICK, 0, 1)
moves = [x for x in rxenv.calls if x[0] == "m" and x[1] == M.MOVE]
check(moves == [("m", M.MOVE, -2, 1, 0), ("m", M.MOVE, -3, -1, 0)], f"cofnięcie ruchu sprzed kliku (odwrotna kolejność): {moves}")
check(rxenv.calls[-1] == ("m", M.LDOWN, 0, 0, 0), "potem wciśnięcie LPM")
rxenv.calls.clear()
feed(T_MOUSE, 0, 4, 4, 0)
check(not rxenv.calls, "w czasie freeze ruch pomijany")
time.sleep(0.11)
feed(T_MOUSE, 0, 10, 5, 0)
check(not rxenv.calls, "klik trzymany: drobny ruch poniżej progu przeciągania nie rusza kursora")
feed(T_MOUSE, 0, 20, 0, 0)
check(rxenv.calls and rxenv.calls[-1][:2] == ("m", M.MOVE), f"po progu przeciąganie rusza kursor: {rxenv.calls}")
rxenv.calls.clear()
feed(T_CLICK, 0, 0)
feed(T_MOUSE, 0, 5, 5, 0)
check(rxenv.calls == [("m", M.LUP, 0, 0, 0)], f"puszczenie: LUP i freeze: {rxenv.calls}")
time.sleep(0.11); rxenv.calls.clear()
feed(T_MOUSE, 0, 5, 5, 0)
check(rxenv.calls == [("m", M.MOVE, 5, 5, 0)], "po freeze zwykły ruch")
rx_settings.apply(Settings(MouseConfig(sens=1.0, click_freeze_ms=0, click_rewind_ms=0, drag_threshold=0), MasterConfig()))
M.reset(0); feed(T_MOUSE, 0, 3, 0, 0); rxenv.calls.clear()
feed(T_CLICK, 0, 1); feed(T_MOUSE, 0, 2, 0, 0); feed(T_CLICK, 0, 0)
check(rxenv.calls == [("m", M.LDOWN, 0, 0, 0), ("m", M.MOVE, 2, 0, 0), ("m", M.LUP, 0, 0, 0)],
      f"0 = poprawki wyłączone (dawne zachowanie): {rxenv.calls}")
ok("mysz: cofnięcie ruchu sprzed kliku, freeze, próg przeciągania, wyłączalne")

# ---------- pierwszy stan po połączeniu: trzymane wejścia to nie naciśnięcia ----------
settings(pad=A, actions={"pause": "options", "close_game": "", "sleep": "", "shutdown": ""})
rx_master.reset(0); rxenv.calls.clear()
state(0, mask=BIT[PS] | BIT[OPT]); time.sleep(0.05)
check(not any(x[:2] == ("k", W.VK_ESCAPE) for x in rxenv.calls), "pad połączony z trzymanym PS + Options nie pauzuje")
state(0)
ok("pierwszy stan po połączeniu nie wyzwala akcji")

# ---------- korona przed identyfikatorem pada ----------
S.pad_id[1] = None
okk, msg = rx_master.set_master(1, save=False)
check(not okk and "identyfikatora" in msg and rx_settings.cur.master.pad == A, "bez MAC pad nie zostaje masterem (klucz slotN by się zgubił)")
ident(1, B)
ok("master tylko dla pada z identyfikatorem")

print("FAIL" if fails else "PASS", "master:", len(fails), "błędów" if fails else "wszystko OK")
sys.exit(1 if fails else 0)
