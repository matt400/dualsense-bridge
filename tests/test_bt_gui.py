"""Okno: Bluetooth nadajnika - kropka stanu w nagłówku, wysuwany panel (skan z odliczaniem, lista urządzeń z Sparuj /
Połącz / Rozłącz / Zapomnij, włączanie adaptera, wynik operacji) i obsługa panelu samym padem w trybie TV (D-pad tylko
po panelu, ✕ klika, ○ zamyka, fokus wraca na ikonę). Zrzut: tests/bt_drawer.png."""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
import rxenv
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

C.cfg["tray"] = False
import rx_bt as RB
import rx_qt
from proto import (BODY, BT_CONNECT, BT_DISCONNECT, BT_LIST, BT_PAIR, BT_POWER, BT_REMOVE, BT_SCAN_MAX_S, BT_SCAN_START,
                   BT_SCAN_STOP, BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTD_TRUSTED, BTF_BUSY, BTF_SCANNING, BTR_OK,
                   BTR_RUNNING, BTS_NO_TOOL, BTS_OFF, BTS_OK, T_BTCMD, T_BTDEV, T_BTSTATE, mac_bytes)
from rx_buttons import BIT, BUTTON_CODES
from rx_qt_bt import DeviceRow, status_icon
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
SHOTS = os.environ.get("SHOTS") or os.path.dirname(__file__)
PAD, PAD2, PHONE = "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "11:22:33:44:55:66"
LIST = 4                                                          # sender_bt.BTF_LIST
gen = [0]


def activate(w):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(w)
    pump()


def bt_state(st=BTS_OK, flags=0, left=0, conn=1, op=0, res=0, mac=""):
    S.last_pkt = time.monotonic()
    RB.on_packet(T_BTSTATE, N.codec.pack(T_BTSTATE, 255, st, flags, left, conn, 0, gen[0], op, res,
                                         mac_bytes(mac) or bytes(6)))


def bt_list(devs, st=BTS_OK, flags=0, left=0, op=0, res=0, mac=""):
    """Runda listy jak z nadajnika: btstate z BTF_LIST, potem urządzenia."""
    gen[0] += 1
    conn = sum(1 for _m, _n, _r, f in devs if f & BTD_CONNECTED)
    S.last_pkt = time.monotonic()
    RB.on_packet(T_BTSTATE, N.codec.pack(T_BTSTATE, 255, st, flags | LIST, left, conn, len(devs), gen[0], op, res,
                                         mac_bytes(mac) or bytes(6)))
    for k, (m, name, rssi, f) in enumerate(devs):
        RB.on_packet(T_BTDEV, N.codec.pack(T_BTDEV, 255, gen[0], k, len(devs), mac_bytes(m), rssi, f, name.encode()))


def cmds():
    out = [BODY[T_BTCMD].unpack_from(p, 4) for t, _i, _sq, p in rxenv.sent if t == T_BTCMD]
    rxenv.sent.clear()
    return out


def without_list(c):
    return [x for x in c if x[0] != BT_LIST]


def rows():
    d = win.bt_drawer
    return [d.list_lay.itemAt(k).widget() for k in range(d.list_lay.count())
            if isinstance(d.list_lay.itemAt(k).widget(), DeviceRow)]


DEVS = [(PAD2, "DualSense Wireless Controller", -128, BTD_PAIRED | BTD_CONNECTED | BTD_TRUSTED | BTD_PAD),
        (PAD, "DualSense Wireless Controller", -58, BTD_PAD),
        (PHONE, "Telefon", -91, BTD_PAIRED)]

S.peer = ("127.0.0.1", 5005)
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 820); win.show(); activate(win)
drawer = win.bt_drawer

# ---------- kropka stanu w nagłówku ----------
assert not RB.supported() and not drawer.is_open() and not drawer.isVisible()
win.slow_tick()
assert "brak danych" in win.bt_btn.toolTip()
bt_state(conn=2); win.slow_tick()
assert "włączony" in win.bt_btn.toolTip() and "połączone: 2" in win.bt_btn.toolTip()
assert win.bt_btn.icon().cacheKey() == status_icon("ok").cacheKey()
bt_state(flags=BTF_SCANNING, left=17); win.slow_tick()
assert "skanowanie 17 s" in win.bt_btn.toolTip() and win.bt_btn.icon().cacheKey() == status_icon("scan").cacheKey()
bt_state(st=BTS_OFF); win.slow_tick()
assert win.bt_btn.icon().cacheKey() == status_icon("warn").cacheKey()
bt_state(st=BTS_NO_TOOL); win.slow_tick()
assert win.bt_btn.icon().cacheKey() == status_icon("bad").cacheKey() and "busctl" in win.bt_btn.toolTip()
ok("nagłówek: ikona Bluetooth z kropką (szara / zielona / niebieska przy skanie / żółta / czerwona) i opis stanu")

# ---------- panel: otwieranie, keepalive, lista ----------
bt_list(DEVS)
rxenv.sent.clear()
win.bt_btn.click(); pump(260)
assert drawer.is_open() and drawer.isVisible() and drawer.scrim.isVisible()
assert drawer.geometry().right() == win.centralWidget().width() - 1 and drawer.height() == win.centralWidget().height()
assert (BT_LIST, 0, bytes(6)) in cmds(), "otwarty panel: nadajnik ma wysyłać listę"
rs = rows()
assert [r.mac for r in rs] == [PAD2, PAD, PHONE]
assert [r.btn.text() for r in rs] == ["Rozłącz", "Sparuj", "Połącz"]
assert [r.forget.isVisible() for r in rs] == [False, False, True], "Zapomnij tylko przy sparowanym, niepołączonym"
assert rs[1].bars.n == 3 and rs[0].bars.n == 0 and "połączony" in rs[0].state.text() and "nowy" in rs[1].state.text()
assert drawer.scan_btn.text() == f"Skanuj ({BT_SCAN_MAX_S} s)" and not drawer.power_btn.isVisible()
QTest.qWait(int(RB.KEEPALIVE_S * 1000) + 300)
assert (BT_LIST, 0, bytes(6)) in cmds(), "keepalive co KEEPALIVE_S"
ok("panel: wysuwa się z prawej nad oknem, keepalive listy, wiersze z sygnałem, stanem i właściwą akcją")

# ---------- akcje ----------
rs[1].btn.click()
assert without_list(cmds()) == [(BT_PAIR, 0, mac_bytes(PAD))] * 3
drawer.refresh()
assert rs[1].btn.text() == "Parowanie…" and not rs[1].btn.isEnabled()
assert not rs[0].btn.isEnabled() and not rs[2].btn.isEnabled(), "inne operacje czekają"
bt_list(DEVS, flags=BTF_BUSY, op=BT_PAIR, res=BTR_RUNNING, mac=PAD); drawer.refresh()
assert "Parowanie" in drawer.result.text()
devs2 = [DEVS[0], (PAD, "DualSense Wireless Controller", -55, BTD_PAD | BTD_PAIRED | BTD_CONNECTED | BTD_TRUSTED), DEVS[2]]
bt_list(devs2, op=BT_PAIR, res=BTR_OK, mac=PAD); drawer.refresh()
assert rows()[1] is rs[1], "wiersz zostaje (fokus z pada nie skacze)"
assert rs[1].btn.text() == "Rozłącz" and rs[1].btn.isEnabled() and "sparowano" in drawer.result.text()
rs[0].btn.click()
assert without_list(cmds()) == [(BT_DISCONNECT, 0, mac_bytes(PAD2))] * 3
bt_list(devs2, op=BT_DISCONNECT, res=BTR_OK, mac=PAD2); drawer.refresh()
rs[2].btn.click()
assert without_list(cmds()) == [(BT_CONNECT, 0, mac_bytes(PHONE))] * 3
bt_list(devs2, op=BT_CONNECT, res=BTR_OK, mac=PHONE); drawer.refresh()
rs[2].forget.click(); rs[2].forget.click()
assert without_list(cmds()) == [(BT_REMOVE, 0, mac_bytes(PHONE))] * 3, "Zapomnij z potwierdzeniem"
bt_list(devs2[:2], op=BT_REMOVE, res=BTR_OK, mac=PHONE); drawer.refresh(); pump()
assert [r.mac for r in rows()] == [PAD2, PAD]
bt_list([devs2[1], devs2[0]]); drawer.refresh()
assert [r.mac for r in rows()] == [PAD, PAD2], "kolejność jak z nadajnika"
ok("akcje: Sparuj / Rozłącz / Połącz / Zapomnij (3 pakiety), operacja w toku blokuje resztę, wynik pod listą")

drawer.scan_btn.click()
assert without_list(cmds()) == [(BT_SCAN_START, BT_SCAN_MAX_S, bytes(6))] * 3
bt_list(devs2[:2], flags=BTF_SCANNING, left=21, op=BT_SCAN_START, res=BTR_OK); drawer.refresh()
assert drawer.scan_btn.text() == "Zatrzymaj skanowanie (21 s)"
drawer.scan_btn.click()
assert without_list(cmds()) == [(BT_SCAN_STOP, 0, bytes(6))] * 3
bt_list(devs2[:2], st=BTS_OFF); drawer.refresh()
assert drawer.power_btn.isVisible() and not rows() and "niedostępna" in drawer.empty.text()
drawer.power_btn.click()
assert without_list(cmds()) == [(BT_POWER, 1, bytes(6))] * 3
bt_list(DEVS); drawer.refresh()
ok("skan: start / stop z odliczaniem; wyłączony Bluetooth: przycisk Włącz, bez listy")

drawer.grab().save(os.path.join(SHOTS, "bt_drawer.png"))
QTest.keyClick(drawer.scan_btn, Qt.Key.Key_Escape); pump(260)
assert not drawer.is_open() and not drawer.isVisible() and not drawer.scrim.isVisible()
win.bt_btn.click(); pump(260)
QTest.mouseClick(drawer.scrim, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 200)); pump(260)
assert not drawer.is_open(), "klik obok panelu zamyka"
rxenv.sent.clear(); pump(int(RB.KEEPALIVE_S * 1000) + 300)
assert not cmds(), "zamknięty panel nie podtrzymuje listy"
ok("zamykanie: Esc, klik w przyciemnione tło; zamknięty panel nie wysyła keepalive")

# ---------- tryb TV: tylko pad ----------
win.set_fullscreen(True); pump(150); activate(win)
state(0)
press_t = [time.monotonic()]
def press(name):
    """Naciśnięcie przycisku pada 0 (fizycznego) i puszczenie - przez PadNav jak w oknie co 33 ms."""
    t = press_t[0] = press_t[0] + 1.0
    if name.startswith("dpad_"):
        hat = {"dpad_up": (0, -1), "dpad_down": (0, 1), "dpad_left": (-1, 0), "dpad_right": (1, 0)}[name]
        state(0, hat=hat)
    else:
        state(0, mask=BIT[BUTTON_CODES[name]])
    win.nav.tick(t); state(0); win.nav.tick(t + 0.05); pump(10)

win.bt_btn.setFocus(Qt.FocusReason.TabFocusReason)
press("cross"); pump(260)
assert drawer.is_open() and drawer.isAncestorOf(QApplication.focusWidget()), "✕ na ikonie otwiera panel z fokusem w nim"
seen = []
for _k in range(12):
    press("dpad_down")
    fw = QApplication.focusWidget()
    assert drawer.isAncestorOf(fw), f"D-pad wyszedł z panelu: {fw}"
    seen.append(fw)
assert {drawer.close_btn, drawer.scan_btn, rows()[0].btn, rows()[1].btn, rows()[2].btn} <= set(seen), seen
assert drawer.power_btn not in seen, "ukryty przycisk pominięty"
slot = win.ed.slot
press("r1")
assert win.ed.slot == slot, "R1 przy otwartym panelu nie zmienia pada"
while QApplication.focusWidget() is not rows()[1].btn:
    press("dpad_down")
rxenv.sent.clear()
press("cross")
assert without_list(cmds()) == [(BT_PAIR, 0, mac_bytes(PAD))] * 3, "✕ klika akcję w wierszu"
bt_list(DEVS, op=BT_PAIR, res=BTR_OK, mac=PAD); drawer.refresh()
press("circle"); pump(260)
assert not drawer.is_open() and win.fullscreen() and QApplication.focusWidget() is win.bt_btn, \
    "○ zamyka panel (nie pełny ekran), fokus wraca na ikonę"
press("circle"); pump(100)
assert not win.fullscreen(), "drugie ○ - jak dotąd: wyjście z pełnego ekranu"
ok("tryb TV: ✕ na ikonie otwiera, D-pad tylko po panelu (ukryte pominięte), ✕ klika, R1 nie zmienia pada, ○ zamyka")

# ---------- stary / milczący nadajnik ----------
RB.reset()
win.bt_btn.click(); pump(260)
assert not drawer.scan_btn.isEnabled() and not rows() and "brak danych" in drawer.status.text()
rxenv.sent.clear(); drawer.scan_btn.click()
assert not cmds(), "nadajnik bez obsługi Bluetooth - nic nie idzie"
drawer.close_drawer(); pump(260)
ok("nadajnik bez Bluetooth z okna (starszy / offline): panel wyjaśnia, przyciski nieaktywne")

win.quit()
print("BT GUI OK")
