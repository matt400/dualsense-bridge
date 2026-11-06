"""Receiver: the master pad - close game targets the checked window, a silent slot forgets its pad id, Settings do not undo a crown change, the touchpad mouse follows the master.
    python3 tests/test_master_follow.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_console, rx_master, rx_settings
from proto import T_BYE, T_IDENT, T_MOUSE
from rx_settings import MasterConfig, MouseConfig, Settings

from chk import check, fails, wait_for                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- master: "close game" closes the window it checked ----------
A = "aa:aa:aa:aa:aa:01"
feed(T_IDENT, 0, A.encode()); state(0)
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=A, actions={"pause": "", "close_game": "triangle",
                                                                        "sleep": "", "shutdown": ""})))
closed = []
W.close_window = lambda hwnd: closed.append(hwnd) or True
fg = [(0x111, "explorer.exe")]
W.foreground_window = lambda: fg[0]
S.game = "game.exe"                                 # rx_games saw the game half a second ago ...
rx_master._do(0, "close_game")                      # ... but now the desktop is active
check(closed == [], "stale S.game: the desktop in the foreground is not closed")
fg[0] = (0x222, "game.exe")
def switch_after_check():
    r = fg[0]
    fg[0] = (0x333, "steam.exe")                    # alt-tab right after the check
    return r
W.foreground_window = switch_after_check
rx_master._do(0, "close_game")
check(closed == [0x222], f"WM_CLOSE goes to the checked game window, not to the window active a moment later: {closed}")
S.game = None
rx_settings.apply(rx_settings.defaults())

# ---------- detach after silence: another pad in this slot is not the "master" ----------
connected = []
real_pc = rx_console.pad_connected
rx_console.pad_connected = lambda i, ident=False: connected.append((i, rx_master.is_master(i)))
M_ID = "aa:aa:aa:aa:aa:09"
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=M_ID)))
feed(T_IDENT, 1, M_ID.encode()); state(1)
check(rx_master.is_master(1), "master w slocie 1")
S.last_seen[1] = time.monotonic() - C.UNPLUG_S - 1
N.check_stale(time.monotonic())
check(S.pad_id[1] is None and not rx_master.is_master(1), "after unplug_s of silence the slot forgets the pad identifier")
connected.clear()
feed(T_IDENT, 1, b"bb:bb:bb:bb:bb:09"); state(1)
check(connected and not any(m for _i, m in connected), f"another pad in the master's slot does not turn console mode on: {connected}")
rx_console.pad_connected = real_pc
c = rx_console.Console(rx_console.ConsoleConfig())
applied = []
c._apply = lambda on, *a, **k: applied.append(on)
c._auto_on(1)                                       # a queued job for a slot where the master no longer is
check(applied == [], "auto-ON z kolejki sprawdza mastera w chwili wykonania")
rx_settings.apply(rx_settings.defaults())
feed(T_BYE, 1)

# ---------- Settings open, master changed with the crown / PS + R3: Save does not revert it ----------
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import rx_qt_console
except Exception as e:
    print(f"SKIP master w otwartych Ustawieniach ({e!r})")
else:
    MA, MB = "aa:aa:aa:aa:aa:31", "bb:bb:bb:bb:bb:32"
    C.SETTINGS_PATH = os.path.join(TMP, "settings_master.json")
    feed(T_IDENT, 0, MA.encode()); state(0); feed(T_IDENT, 1, MB.encode()); state(1)
    rx_master.set_master(0, save=False)
    d = rx_qt_console.ConsoleSettings(); d.present()
    rx_master.set_master(1, save=False)                       # crown on the card of pad 2
    d.save_btn.click()
    check(rx_settings.cur.master.pad == MB, f"Save does not revert a master changed with the crown: {rx_settings.cur.master.pad}")
    d.present(); d.master_pad.set_data(MA); d.master_pad.activated.emit(d.master_pad.currentIndex())
    d.save_btn.click()
    check(rx_settings.cur.master.pad == MA, "choosing the master in the window (also back to the original) works")
    d.close()
    rx_settings.apply(rx_settings.defaults())

# ---------- touchpad as mouse follows the master (the crown moved only the permissions) ----------
rx_settings.apply(rx_settings.defaults())
MA, MB = "aa:aa:aa:aa:aa:41", "bb:bb:bb:bb:bb:42"
feed(T_IDENT, 0, MA.encode()); state(0); feed(T_IDENT, 1, MB.encode()); state(1)
S.mouse_mode[:] = [i in C.MOUSE_PADS for i in range(C.NUM_PADS)]
check(S.mouse_mode[:2] == [True, False], "no master: mouse by mouse_pads (slot 1)")
rx_master.set_master(1, save=False)                       # crown on the card of pad 2
check(S.mouse_mode[:2] == [False, True], f"master = pad 2 -> its touchpad is the mouse, pad 1 is DS4 touch: {S.mouse_mode[:2]}")
calls.clear()
feed(T_MOUSE, 1, 30, 0, 0)
check(any(c[0] == "m" for c in calls), "ruch na touchpadzie mastera rusza kursorem")
rx_master.set_master(0, save=False)
check(S.mouse_mode[:2] == [True, False], "master z powrotem na pad 1 -> mysz wraca")
rx_master.set_master(None, save=False)
check(S.mouse_mode[:2] == [True, False], "no master -> mouse_pads")
rx_master.set_master(1, save=False)
feed(T_BYE, 1)                                            # the master disconnected
check(S.mouse_mode[0] is True, "master not connected -> mouse by mouse_pads (pad 1 is not left without a mouse)")
time.sleep(1.1); feed(T_IDENT, 1, MB.encode()); state(1)  # the master comes back
check(S.mouse_mode[:2] == [False, True], "the master came back -> its touchpad is the mouse again")
rx_settings.apply(rx_settings.defaults())

sys.exit(1 if fails else 0)
