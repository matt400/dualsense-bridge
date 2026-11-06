"""Master pad (rx_master), settings (rx_settings) and touchpad click stabilization (rx_mouse)."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rxenv
import rx_console, rx_master, rx_settings
from proto import T_CLICK, T_IDENT, T_MOUSE
from rx_console import Console, ConsoleConfig
from rx_settings import MasterConfig, MouseConfig, Settings

from chk import check, fails, wait_for, ok


PS, SHARE, CROSS, TRI, OPT, R3 = 316, 314, 304, 307, 315, 318
A, B = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02"
pulses = []
F.pulse = lambda i, kind: pulses.append((i, kind))
actions = []
W.close_window = lambda hwnd: actions.append("close") or True
W.foreground_window = lambda: (1, S.game)          # active window = S.game set in the tests
W.sleep_pc = lambda: actions.append("sleep")
W.shutdown_pc = lambda *a: actions.append("shutdown")

def ident(i, pid):
    feed(T_IDENT, i, pid.encode())

def settings(**master):
    rx_settings.apply(Settings(MouseConfig(), MasterConfig(**master)))

ident(0, A); ident(1, B)
state(0); state(1)

# ---------- settings: defaults, bad values, saving ----------
path = os.path.join(TMP, "s.json")
check(rx_settings.load(path) == rx_settings.defaults(), "no file -> defaults")
json.dump({"mouse": {"sens": 9, "click_freeze_ms": 200}, "master": {"pad": " AA:AA:AA:AA:AA:01 ", "when_absent": "x",
           "only_master": ["console", "nie-ma", "console", "ps_game"], "actions": {"pause": "share", "sleep": "cross",
           "shutdown": "cross"}, "claim_hold": "tak"}}, open(path, "w"))
s = rx_settings.load(path)
check(s.mouse.sens == 0.5 and s.mouse.click_freeze_ms == 200, f"bad sensitivity -> default, the rest loaded: {s.mouse}")
check(s.master.pad == A and s.master.when_absent == "all" and s.master.only_master == ("console", "ps_game"), f"master: {s.master}")
check(s.master.actions == {"pause": "", "close_game": "", "sleep": "cross", "shutdown": "", "swap": "square"} and s.master.claim_hold is True,
      f"actions: Share (PS shortcut) not allowed, a button not twice, a new action gets the default button: {s.master.actions}")
rx_settings.save(s, path)
check(rx_settings.load(path) == s and not os.path.exists(path + ".tmp"), "write and read without changes")
open(path, "w").write("{zepsuty")
check(rx_settings.load(path) == rx_settings.defaults(), "damaged file -> defaults")
ok("settings.json: defaults, fixes, atomic write")

# ---------- uprawnienia ----------
settings(pad="")
check(all(rx_master.allowed(i, f) for i in (0, 1) for f in rx_settings.MASTER_FEATURES), "without a master everyone can do everything")
settings(pad=A, only_master=("console", "ps_game"))
check(rx_master.master_slot() == 0 and rx_master.is_master(0) and not rx_master.is_master(1), "master = pad A w slocie 0")
check(rx_master.allowed(0, "console") and not rx_master.allowed(1, "console") and rx_master.allowed(1, "profile"),
      "restricted features only for the master, the rest for everyone")
ident(0, B); ident(1, A)                                    # the pads swapped slots - the master follows the pad
check(rx_master.master_slot() == 1 and rx_master.allowed(1, "console") and not rx_master.allowed(0, "console"),
      "master by MAC, not by slot")
ident(0, A); ident(1, B)
settings(pad="cc:cc:cc:cc:cc:03", only_master=("console",), when_absent="all")
check(rx_master.allowed(1, "console"), "master not connected + all -> everyone can")
settings(pad="cc:cc:cc:cc:cc:03", only_master=("console",), when_absent="nobody")
check(not rx_master.allowed(0, "console") and not rx_master.allowed(1, "console"), "master not connected + nobody -> nobody")
ok("permissions: restricted features, master by MAC, master absent")

# ---------- enforcement: console mode, PS in game, PS shortcuts ----------
settings(pad=A, only_master=("console", "ps_game", "screenshot", "profile", "mouse"))
calls = []
rx_console._display = lambda mode: calls.append(mode)
rx_console._audio = rx_console._toast = lambda *a: None
c = Console(ConsoleConfig(toggle_button="r2", auto_on_master=False)); c.start(); rx_console.ctl = c
wait_for(lambda: calls); calls.clear(); pulses.clear()
for i in (1, 1):
    state(1, rt=255); time.sleep(0.03); state(1); time.sleep(0.03)
time.sleep(0.3)
check(not c.on and (1, "denied") in pulses, f"pad 1 (not master): double R2 switches nothing, a red flash: {pulses}")
import rx_events
check(sum("tylko pad master" in e.text or "master pad only" in e.text for e in rx_events.since(0)) == 1,
      "not master: the refusal is in the window log (once, despite two clicks close together)")
state(0, rt=255); time.sleep(0.03); state(0); time.sleep(0.03); state(0, rt=255); time.sleep(0.03); state(0)
check(wait_for(lambda: c.on), "master: double R2 switches console mode")
c.close(restart=True); rx_console.ctl = None

pulses.clear()
state(1, mask=BIT[PS]); time.sleep(0.03); state(1); time.sleep(0.03)
check(not S.states[1]["mask"] & BIT[PS] and (1, "denied") in pulses, "not master: PS does not reach the game")
state(0, mask=BIT[PS]); time.sleep(0.02); state(0)
check(wait_for(lambda: S.states[0]["mask"] & BIT[PS], 0.5), "master: a short PS reaches the game (pulse)")
time.sleep(0.2)
rxenv.calls.clear()
state(1, mask=BIT[PS]); state(1, mask=BIT[PS] | BIT[SHARE]); time.sleep(0.1); state(1, mask=BIT[PS]); state(1)
time.sleep(0.1)
check(not any(x[0] == "k" for x in rxenv.calls), f"not master: PS + Share without a screenshot: {rxenv.calls}")
check(not S.states[1]["mask"] & BIT[SHARE], "Share used in a shortcut does not reach the game")
mode = S.mouse_mode[1]
state(1, mask=BIT[PS]); feed(T_CLICK, 1, 1); feed(T_CLICK, 1, 0); state(1); time.sleep(0.05)
check(S.mouse_mode[1] == mode and not S.states[1]["tp"], "not master: PS + touchpad click does not change the mouse mode")
settings(pad=A, only_master=("console",))
state(1, mask=BIT[PS]); time.sleep(0.02); state(1)
check(wait_for(lambda: S.states[1]["mask"] & BIT[PS], 0.5), "PS not restricted -> works on every pad")
time.sleep(0.2)
ok("enforcement: console mode, PS to the game, PS + Share, PS + touchpad")

# ---------- akcje PS + przycisk ----------
rx_master.HOLD_S = 0.15
settings(pad=A, only_master=("actions",), actions={"pause": "options", "close_game": "triangle", "sleep": "", "shutdown": "cross"})
rxenv.calls.clear(); actions.clear()
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[OPT])
check(wait_for(lambda: ("k", W.VK_ESCAPE) in rxenv.calls), "PS + Options -> Esc (pauza)")
check(not S.states[0]["mask"] & BIT[OPT], "Options from the combo does not reach the game")
state(0, mask=BIT[OPT]); time.sleep(0.2)
check(not S.states[0]["mask"] & BIT[OPT], "puszczony PS, trzymany Options dalej zablokowany do puszczenia")
check(not S.states[0]["mask"] & BIT[PS], "PS used in a combo does not reach the game on release")
state(0); state(0, mask=BIT[OPT])
check(S.states[0]["mask"] & BIT[OPT], "after release Options works normally again")
state(0)

S.game = "ittakestwo.exe"
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[TRI]); time.sleep(0.05); state(0, mask=BIT[PS]); time.sleep(0.3)
check("close" not in actions, "closing the game: released too early -> nothing")
state(0, mask=BIT[PS] | BIT[TRI])
check(wait_for(lambda: "close" in actions), "closing the game: held -> WM_CLOSE")
state(0)
S.game = "explorer.exe"; actions.clear()
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[TRI]); time.sleep(0.4); state(0)
check("close" not in actions, "the desktop (explorer) is not closed")
S.game = None
pulses.clear(); actions.clear()
state(1, mask=BIT[PS]); state(1, mask=BIT[PS] | BIT[CROSS]); time.sleep(0.4); state(1)
check("shutdown" not in actions and (1, "denied") in pulses, "not master: PS + button does nothing")
import rx_console
rx_console.power_off = lambda why: actions.append(("console off", why)) or True
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[CROSS])
check(wait_for(lambda: "shutdown" in actions), "master: PS + cross held -> PC shutdown")
check(actions[-2:] == [("console off", "wyłączanie komputera"), "shutdown"], f"console mode OFF first, then the shutdown: {actions}")
state(0)
ok("master actions: pause, closing the game with a hold, shutdown; combo buttons do not reach the game")

# ---------- master takeover: PS + R3 ----------
rx_master.CLAIM_S = 0.2
settings(pad=A, claim_hold=True)
pulses.clear()
state(1, mask=BIT[PS]); state(1, mask=BIT[PS] | BIT[R3]); time.sleep(0.1); state(1)
time.sleep(0.2)
check(rx_settings.cur.master.pad == A, "a short PS + R3 does not take over")
state(1, mask=BIT[PS]); state(1, mask=BIT[PS] | BIT[R3])
check(not S.states[1]["mask"] & BIT[R3], "R3 from the combo does not reach the game")
check(wait_for(lambda: rx_settings.cur.master.pad == B), "PS + R3 held CLAIM_S -> pad B is the master")
state(1)
def _saved_master():
    try:
        return json.load(open(C.SETTINGS_PATH))["master"]["pad"]
    except (OSError, ValueError, KeyError):
        return None                  # file being written (rx_settings.cur changes before the write)
check(wait_for(lambda: (1, "master") in pulses and _saved_master() == B), "gold flash and save to settings.json")
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[R3]); time.sleep(0.05); state(0, mask=BIT[PS]); time.sleep(0.3); state(0)
check(rx_settings.cur.master.pad == B, "R3 released too early does not take over the master")
settings(pad=B, claim_hold=False)
state(0, mask=BIT[PS]); state(0, mask=BIT[PS] | BIT[R3])
check(S.states[0]["mask"] & BIT[R3], "claim_hold off -> R3 is a plain button")
time.sleep(0.35); state(0)
check(rx_settings.cur.master.pad == B, "claim_hold off -> holding does nothing")
ok("master takeover: PS + R3, save, can be disabled")

# ---------- mouse: click stabilization ----------
rx_settings.apply(Settings(MouseConfig(sens=1.0, click_freeze_ms=100, click_rewind_ms=80, drag_threshold=30), MasterConfig()))
check(M.sens == [1.0] * C.NUM_PADS, "the sensitivity slider sets every pad")
S.mouse_mode[0] = True; M.reset(0); rxenv.calls.clear()
feed(T_MOUSE, 0, 50, 0, 0)                       # dawny ruch - poza oknem cofania
time.sleep(0.12)
feed(T_MOUSE, 0, 3, 1, 0); feed(T_MOUSE, 0, 2, -1, 0)     # finger pressing down right before the click
rxenv.calls.clear()
feed(T_CLICK, 0, 1)
moves = [x for x in rxenv.calls if x[0] == "m" and x[1] == M.MOVE]
check(moves == [("m", M.MOVE, -2, 1, 0), ("m", M.MOVE, -3, -1, 0)], f"undoing the movement from before the click (reverse order): {moves}")
check(rxenv.calls[-1] == ("m", M.LDOWN, 0, 0, 0), "then the left button down")
rxenv.calls.clear()
feed(T_MOUSE, 0, 4, 4, 0)
check(not rxenv.calls, "w czasie freeze ruch pomijany")
time.sleep(0.11)
feed(T_MOUSE, 0, 10, 5, 0)
check(not rxenv.calls, "click held: a small move below the drag threshold does not move the cursor")
feed(T_MOUSE, 0, 20, 0, 0)
check(rxenv.calls and rxenv.calls[-1][:2] == ("m", M.MOVE), f"past the threshold dragging moves the cursor: {rxenv.calls}")
rxenv.calls.clear()
feed(T_CLICK, 0, 0)
feed(T_MOUSE, 0, 5, 5, 0)
check(rxenv.calls == [("m", M.LUP, 0, 0, 0)], f"puszczenie: LUP i freeze: {rxenv.calls}")
time.sleep(0.11); rxenv.calls.clear()
feed(T_MOUSE, 0, 5, 5, 0)
check(rxenv.calls == [("m", M.MOVE, 5, 5, 0)], "after the freeze a normal move")
rx_settings.apply(Settings(MouseConfig(sens=1.0, click_freeze_ms=0, click_rewind_ms=0, drag_threshold=0), MasterConfig()))
M.reset(0); feed(T_MOUSE, 0, 3, 0, 0); rxenv.calls.clear()
feed(T_CLICK, 0, 1); feed(T_MOUSE, 0, 2, 0, 0); feed(T_CLICK, 0, 0)
check(rxenv.calls == [("m", M.LDOWN, 0, 0, 0), ("m", M.MOVE, 2, 0, 0), ("m", M.LUP, 0, 0, 0)],
      f"0 = fixes off (the old behaviour): {rxenv.calls}")
ok("mouse: undoing the movement from before the click, freeze, drag threshold, can be disabled")

# ---------- the first state after connecting: held inputs are not presses ----------
settings(pad=A, actions={"pause": "options", "close_game": "", "sleep": "", "shutdown": ""})
rx_master.reset(0); rxenv.calls.clear()
state(0, mask=BIT[PS] | BIT[OPT]); time.sleep(0.05)
check(not any(x[:2] == ("k", W.VK_ESCAPE) for x in rxenv.calls), "a pad connected with PS + Options held does not pause")
state(0)
ok("the first state after connecting triggers no action")

# ---------- crown before the pad identifier ----------
S.pad_id[1] = None
okk, msg = rx_master.set_master(1, save=False)
check(not okk and "identyfikatora" in msg and rx_settings.cur.master.pad == A, "a pad without a MAC does not become the master (the slotN key would get lost)")
ident(1, B)
ok("master only for a pad with an identifier")

# ---------- sender version and rumble on the pad card ----------
import rx_status
from proto import T_VERSION
S.sender_info[0] = None
check(rx_status.rumble_status(0)[1] == "warn", "no version packet = old sender")
from proto import WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OK, WHY_OPEN
feed(T_VERSION, 0, 5, 3, WHY_OK)
check(S.sender_info[0] == (5, 3, WHY_OK) and "HID" in rx_status.rumble_status(0)[0], rx_status.rumble_status(0))
for why, word in ((WHY_ACCESS, "uprawnień"), (WHY_NO_HIDRAW, "jądrze"), (WHY_NOT_FOUND, "nie znaleziono"), (WHY_OPEN, "niedostępna")):
    feed(T_VERSION, 0, 5, 0, why)
    st = rx_status.rumble_status(0)
    check(st[1] == "bad" and word in st[0] and st[2], f"reason for no rumble on the card: {st}")
ok("version packet: sender version and rumble methods")

print("FAIL" if fails else "PASS", "master:", len(fails), "failures" if fails else "all OK")
sys.exit(1 if fails else 0)
