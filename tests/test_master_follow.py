"""Receiver: the master pad - close game targets the checked window, a silent slot forgets its pad id, Settings do not undo a crown change, the touchpad mouse follows the master.
    python3 tests/test_master_follow.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_console, rx_master, rx_settings
from proto import T_BYE, T_IDENT, T_MOUSE
from rx_settings import MasterConfig, MouseConfig, Settings

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.01)
    return pred()


# ---------- master: "zamknij grę" zamyka okno, które sprawdził ----------
A = "aa:aa:aa:aa:aa:01"
feed(T_IDENT, 0, A.encode()); state(0)
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=A, actions={"pause": "", "close_game": "triangle",
                                                                        "sleep": "", "shutdown": ""})))
closed = []
W.close_window = lambda hwnd: closed.append(hwnd) or True
fg = [(0x111, "explorer.exe")]
W.foreground_window = lambda: fg[0]
S.game = "game.exe"                                 # rx_games widział grę pół sekundy temu ...
rx_master._do(0, "close_game")                      # ... ale teraz aktywny jest pulpit
check(closed == [], "nieaktualne S.game: pulpit na pierwszym planie nie jest zamykany")
fg[0] = (0x222, "game.exe")
def switch_after_check():
    r = fg[0]
    fg[0] = (0x333, "steam.exe")                    # alt-tab tuż po sprawdzeniu
    return r
W.foreground_window = switch_after_check
rx_master._do(0, "close_game")
check(closed == [0x222], f"WM_CLOSE idzie do sprawdzonego okna gry, a nie do okna aktywnego chwilę później: {closed}")
S.game = None
rx_settings.apply(rx_settings.defaults())

# ---------- odłączenie po ciszy: inny pad w tym slocie nie jest "masterem" ----------
connected = []
real_pc = rx_console.pad_connected
rx_console.pad_connected = lambda i, ident=False: connected.append((i, rx_master.is_master(i)))
M_ID = "aa:aa:aa:aa:aa:09"
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=M_ID)))
feed(T_IDENT, 1, M_ID.encode()); state(1)
check(rx_master.is_master(1), "master w slocie 1")
S.last_seen[1] = time.monotonic() - C.UNPLUG_S - 1
N.check_stale(time.monotonic())
check(S.pad_id[1] is None and not rx_master.is_master(1), "po unplug_s ciszy slot zapomina identyfikator pada")
connected.clear()
feed(T_IDENT, 1, b"bb:bb:bb:bb:bb:09"); state(1)
check(connected and not any(m for _i, m in connected), f"inny pad w slocie mastera nie włącza trybu konsoli: {connected}")
rx_console.pad_connected = real_pc
c = rx_console.Console(rx_console.ConsoleConfig())
applied = []
c._apply = lambda on, *a, **k: applied.append(on)
c._auto_on(1)                                       # zadanie z kolejki dla slotu, w którym mastera już nie ma
check(applied == [], "auto-ON z kolejki sprawdza mastera w chwili wykonania")
rx_settings.apply(rx_settings.defaults())
feed(T_BYE, 1)

# ---------- Ustawienia otwarte, master zmieniony koroną / PS + R3: Zapisz go nie cofa ----------
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
    rx_master.set_master(1, save=False)                       # korona na karcie pada 2
    d.save_btn.click()
    check(rx_settings.cur.master.pad == MB, f"Zapisz nie cofa mastera zmienionego koroną: {rx_settings.cur.master.pad}")
    d.present(); d.master_pad.set_data(MA); d.master_pad.activated.emit(d.master_pad.currentIndex())
    d.save_btn.click()
    check(rx_settings.cur.master.pad == MA, "wybór mastera w oknie (także powrót do pierwotnego) działa")
    d.close()
    rx_settings.apply(rx_settings.defaults())

# ---------- touchpad jako mysz idzie za masterem (korona przenosiła tylko uprawnienia) ----------
rx_settings.apply(rx_settings.defaults())
MA, MB = "aa:aa:aa:aa:aa:41", "bb:bb:bb:bb:bb:42"
feed(T_IDENT, 0, MA.encode()); state(0); feed(T_IDENT, 1, MB.encode()); state(1)
S.mouse_mode[:] = [i in C.MOUSE_PADS for i in range(C.NUM_PADS)]
check(S.mouse_mode[:2] == [True, False], "bez mastera: mysz wg mouse_pads (slot 1)")
rx_master.set_master(1, save=False)                       # korona na karcie pada 2
check(S.mouse_mode[:2] == [False, True], f"master = pad 2 -> jego touchpad to mysz, pad 1 dotyk DS4: {S.mouse_mode[:2]}")
calls.clear()
feed(T_MOUSE, 1, 30, 0, 0)
check(any(c[0] == "m" for c in calls), "ruch na touchpadzie mastera rusza kursorem")
rx_master.set_master(0, save=False)
check(S.mouse_mode[:2] == [True, False], "master z powrotem na pad 1 -> mysz wraca")
rx_master.set_master(None, save=False)
check(S.mouse_mode[:2] == [True, False], "bez mastera -> mouse_pads")
rx_master.set_master(1, save=False)
feed(T_BYE, 1)                                            # master się rozłączył
check(S.mouse_mode[0] is True, "master niepodłączony -> mysz wg mouse_pads (pad 1 nie zostaje bez myszy)")
time.sleep(1.1); feed(T_IDENT, 1, MB.encode()); state(1)  # master wraca
check(S.mouse_mode[:2] == [False, True], "master wrócił -> znów jego touchpad to mysz")
rx_settings.apply(rx_settings.defaults())

sys.exit(1 if fails else 0)
