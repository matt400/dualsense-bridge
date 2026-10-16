"""Okno: panel „Skróty pada” - lista z bieżących ustawień (przycisk trybu konsoli, akcje mastera, długie PS, korona
przy funkcjach mastera), otwieranie z nagłówka, naraz jeden panel (Bluetooth / skróty) i obsługa padem w trybie TV
(D-pad przewija, ○ zamyka, fokus wraca na ikonę). Zrzut: tests/keys_drawer.png."""
import sys, os, time
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

C.cfg["tray"] = False
import rx_console
import rx_qt
import rx_settings
import rx_shortcuts as SC
from rx_buttons import BIT, BUTTON_CODES
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
SHOTS = os.environ.get("SHOTS") or os.path.dirname(__file__)


def activate(w):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(w)
    pump()


def flat():
    return {(g.title, sc.keys): sc for g in SC.groups() for sc in g.items}


# ---------- dane: z bieżących ustawień ----------
rx_console.ctl = rx_console.Console(rx_console.ConsoleConfig(toggle_button="options", double_click_ms=500))
cur = rx_settings.cur
rx_settings.cur = replace(cur, master=replace(cur.master, pad="aa:bb:cc:dd:ee:01", claim_hold=False,
                                              actions={"pause": "options", "close_game": "", "sleep": "square",
                                                       "shutdown": ""}))
C.PS_HOLD_ACTION, C.PS_SHARE_ACTION = "bigpicture", "none"
f = flat()
keys = {k for _t, k in f}
assert ("options", "×2") in keys and "500 ms" in f["Tryb konsoli", ("options", "×2")].text, "przycisk trybu konsoli z ustawień"
assert ("ps", "+", "options") in keys and ("ps", "+", "square", "1,2 s") in keys, "akcje mastera (groźne z czasem)"
assert not any("r3" in k for k in keys), "przejęcie mastera wyłączone - nie pokazane"
assert ("ps", "+", "share") not in keys, "PS + Share = none - nie pokazane"
assert ("options", "+", "share") in keys, "wyjście z widoku Test: Options + Create"
assert any(sc.text == "Steam Big Picture" for sc in f.values()), "akcja długiego PS"
assert SC.master_only("console") and SC.master_only("actions") and not SC.master_only("profile")
C.PS_MODIFIER = False
assert [sc.keys for sc in SC.groups()[0].items] == [("ps",)] and SC.groups()[0].note, "skróty PS wyłączone"
C.PS_MODIFIER = True
ok("lista z ustawień: przycisk trybu konsoli, akcje mastera, długie PS; wyłączone skróty pominięte")

# ---------- okno ----------
S.peer = ("127.0.0.1", 5005)
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 820); win.show(); activate(win)
d = win.keys_drawer
assert not d.is_open() and not d.isVisible()
win.keys_btn.click(); pump(260)
assert d.is_open() and d.isVisible() and d.rows
crowned = {r.shortcut.feature for r in d.rows if r.crown is not None}
assert crowned == {"console", "actions", "ps_hold"}, crowned
assert "Korona" in d.legend.text()
d.grab().save(os.path.join(SHOTS, "keys_drawer.png"))
win.bt_btn.click(); pump(260)
assert win.bt_drawer.is_open() and not d.is_open(), "naraz jeden panel"
win.bt_drawer.close_drawer(); pump(260)
rx_console.ctl = rx_console.Console(rx_console.ConsoleConfig(toggle_button="r2"))
win.keys_btn.click(); pump(260)
assert any(r.shortcut.keys == ("r2", "×2") for r in d.rows), "lista budowana od nowa przy otwarciu"
QTest.keyClick(d.scroll, Qt.Key.Key_Escape); pump(260)
assert not d.is_open()
ok("panel: ikona otwiera, korona przy funkcjach mastera, naraz jeden panel, treść odświeżana, Esc zamyka")

# ---------- tryb TV ----------
win.set_fullscreen(True); pump(150); activate(win)
state(0)
press_t = [time.monotonic()]
def press(name):
    t = press_t[0] = press_t[0] + 1.0
    if name.startswith("dpad_"):
        state(0, hat={"dpad_up": (0, -1), "dpad_down": (0, 1)}[name])
    else:
        state(0, mask=BIT[BUTTON_CODES[name]])
    win.nav.tick(t); state(0); win.nav.tick(t + 0.05); pump(10)

win.keys_btn.setFocus(Qt.FocusReason.TabFocusReason)
press("cross"); pump(260)
assert d.is_open() and QApplication.focusWidget() is d.scroll, "✕ otwiera, fokus na liście"
bar = d.scroll.verticalScrollBar()
assert bar.maximum() > 0, "lista dłuższa niż ekran (inaczej nie ma czego przewijać)"
for _k in range(200):
    press("dpad_down")
    if bar.value() == bar.maximum():
        break
assert bar.value() == bar.maximum() and QApplication.focusWidget() is d.scroll, "D-pad ↓ przewija do końca"
while bar.value() > bar.minimum():
    press("dpad_up")
press("dpad_up")
assert QApplication.focusWidget() is d.close_btn, "na górze listy ↑ przechodzi do Zamknij"
press("circle"); pump(260)
assert not d.is_open() and win.fullscreen() and QApplication.focusWidget() is win.keys_btn, "○ zamyka, fokus na ikonie"
ok("tryb TV: ✕ otwiera, D-pad przewija listę, na górze do Zamknij, ○ zamyka, fokus wraca na ikonę")
rx_settings.cur = cur
print("KEYS GUI OK")
