"""Window: sender Bluetooth - state dot in the header, slide-out panel (scan with a countdown, device list with Pair /
Connect / Disconnect / Forget, turning the adapter on, operation result) and driving the panel with the pad alone in TV mode (D-pad only
inside the panel, ✕ clicks, ○ closes, focus goes back to the icon). Screenshot: tests/bt_drawer.png."""
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
                   BT_SCAN_STOP, BT_ADAPTER, BT_ADAPTER_USE, BTA_AUTO, BTA_PICK, BTC_ACTIVE, BTC_BLOCKED, BTC_PICKED, BTC_USB, BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTD_TRUSTED, BTF_BUSY, BTF_HAS_BUILTIN, BTF_HAS_USB, BTF_MODE_SHIFT, BTF_ON_USB, BTF_SCANNING, BTR_OK,
                   BTR_RUNNING, BTS_NO_TOOL, BTS_OFF, BTS_OK, T_BTCMD, T_BTCTRL, T_BTDEV, T_BTSTATE, mac_bytes)
from rx_buttons import BIT, BUTTON_CODES
from rx_qt_bt import DeviceRow, status_icon
from chk import ok
pump = lambda ms=40: QTest.qWait(ms)
SHOTS = os.environ.get("SHOTS") or os.path.dirname(__file__)
PAD, PAD2, PHONE = "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "11:22:33:44:55:66"
from proto import BTF_LIST as LIST
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
    """A list round as from the sender: btstate with BTF_LIST, then the devices."""
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

# ---------- state dot in the header ----------
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
ok("header: Bluetooth icon with a dot (grey / green / blue while scanning / yellow / red) and a state description")

# ---------- panel: otwieranie, keepalive, lista ----------
bt_list(DEVS)
rxenv.sent.clear()
win.bt_btn.click(); pump(260)
assert drawer.is_open() and drawer.isVisible() and drawer.scrim.isVisible()
assert drawer.geometry().right() == win.centralWidget().width() - 1 and drawer.height() == win.centralWidget().height()
assert (BT_LIST, 0, bytes(6)) in cmds(), "open panel: the sender should send the list"
rs = rows()
assert [r.mac for r in rs] == [PAD2, PAD, PHONE]
assert [r.btn.text() for r in rs] == ["Rozłącz", "Sparuj", "Połącz"]
assert [r.forget.isVisible() for r in rs] == [False, False, True], "Forget only for a paired, unconnected device"
assert rs[1].bars.n == 3 and rs[0].bars.n == 0 and "połączony" in rs[0].state.text() and "nowy" in rs[1].state.text()
assert drawer.scan_btn.text() == f"Skanuj ({BT_SCAN_MAX_S} s)" and not drawer.power_btn.isVisible()
QTest.qWait(int(RB.KEEPALIVE_S * 1000) + 300)
assert (BT_LIST, 0, bytes(6)) in cmds(), "keepalive co KEEPALIVE_S"
ok("panel: slides in from the right over the window, list keepalive, rows with signal, state and the right action")

# ---------- akcje ----------
rs[1].btn.click()
assert without_list(cmds()) == [(BT_PAIR, 0, mac_bytes(PAD))] * 3
drawer.refresh()
assert rs[1].btn.text() == "Parowanie…" and not rs[1].btn.isEnabled()
assert not rs[0].btn.isEnabled() and not rs[2].btn.isEnabled(), "other operations wait"
bt_list(DEVS, flags=BTF_BUSY, op=BT_PAIR, res=BTR_RUNNING, mac=PAD); drawer.refresh()
assert "Parowanie" in drawer.result.text()
devs2 = [DEVS[0], (PAD, "DualSense Wireless Controller", -55, BTD_PAD | BTD_PAIRED | BTD_CONNECTED | BTD_TRUSTED), DEVS[2]]
bt_list(devs2, op=BT_PAIR, res=BTR_OK, mac=PAD); drawer.refresh()
assert rows()[1] is rs[1], "the row stays (pad focus does not jump)"
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
assert [r.mac for r in rows()] == [PAD, PAD2], "order as from the sender"
ok("actions: Pair / Disconnect / Connect / Forget (3 packets), an operation in progress blocks the rest, result under the list")

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
ok("scan: start / stop with a countdown; Bluetooth off: Turn on button, no list")

drawer.grab().save(os.path.join(SHOTS, "bt_drawer.png"))
QTest.keyClick(drawer.scan_btn, Qt.Key.Key_Escape); pump(260)
assert not drawer.is_open() and not drawer.isVisible() and not drawer.scrim.isVisible()
win.bt_btn.click(); pump(260)
QTest.mouseClick(drawer.scrim, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 200)); pump(260)
assert not drawer.is_open(), "klik obok panelu zamyka"
rxenv.sent.clear(); pump(int(RB.KEEPALIVE_S * 1000) + 300)
assert not cmds(), "a closed panel does not keep the list alive"
ok("closing: Esc, click on the dimmed background; a closed panel sends no keepalive")

# ---------- TV mode: pad only ----------
win.set_fullscreen(True); pump(150); activate(win)
state(0)
press_t = [time.monotonic()]
def press(name):
    """Press and release a button of pad 0 (the physical one) - through PadNav as in the window every 33 ms."""
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
    assert drawer.isAncestorOf(fw), f"D-pad left the panel: {fw}"
    seen.append(fw)
assert {drawer.close_btn, drawer.scan_btn, rows()[0].btn, rows()[1].btn, rows()[2].btn} <= set(seen), seen
assert drawer.power_btn not in seen, "hidden button skipped"
slot = win.ed.slot
press("r1")
assert win.ed.slot == slot, "R1 with the panel open does not switch the pad"
while QApplication.focusWidget() is not rows()[1].btn:
    press("dpad_down")
rxenv.sent.clear()
press("cross")
assert without_list(cmds()) == [(BT_PAIR, 0, mac_bytes(PAD))] * 3, "✕ clicks the action in the row"
bt_list(DEVS, op=BT_PAIR, res=BTR_OK, mac=PAD); drawer.refresh()
press("circle"); pump(260)
assert not drawer.is_open() and win.fullscreen() and QApplication.focusWidget() is win.bt_btn, \
    "○ closes the panel (not full screen), focus goes back to the icon"
press("circle"); pump(100)
assert not win.fullscreen(), "second ○ - as before: leaves full screen"
ok("TV mode: ✕ on the icon opens, D-pad only inside the panel (hidden ones skipped), ✕ clicks, R1 does not switch the pad, ○ closes")

# ---------- sender Bluetooth controller (built-in / USB dongle) ----------
cgen = [0]


def bt_ctrls(rows):
    """A btctrl round as from the sender: (BTC_* flags, N, name)."""
    cgen[0] += 1
    for k, (f, n, name) in enumerate(rows):
        RB.on_packet(T_BTCTRL, N.codec.pack(T_BTCTRL, 255, cgen[0], k, len(rows), f, n, name.encode()))


win.bt_btn.click(); pump(260)
bt_list(DEVS); drawer.refresh()
assert not drawer.adapter_box.isVisible(), "sender without controller choice - switch hidden"
both = BTF_HAS_BUILTIN | BTF_HAS_USB
QC, TP = "Qualcomm WCN3990", "Realtek TP-Link UB5A Adapter"
bt_list(DEVS, flags=both | BTF_ON_USB | BTA_AUTO << BTF_MODE_SHIFT)
bt_ctrls([(BTC_BLOCKED, 0, QC), (BTC_USB | BTC_ACTIVE, 1, TP)]); drawer.refresh(); win.slow_tick(); pump()
assert drawer.adapter_box.isVisible() and drawer.adapter_seg.value() == str(BTA_AUTO)
rows_c = list(drawer.ctrl_rows.values())
assert [r.name.full for r in rows_c] == [QC, TP], "all modules with the device name"
assert "wbudowany" in rows_c[0].detail.text() and "hci0" in rows_c[0].detail.text() and "wyłączony" in rows_c[0].detail.text()
assert "dongle USB" in rows_c[1].detail.text() and "używany" in rows_c[1].detail.text()
assert rows_c[0].btn.isVisible() and not rows_c[1].btn.isVisible(), "Use only on an unused one"
assert TP in win.bt_btn.toolTip(), "state dot: name of the controller in use"
assert "sparuj pady ponownie" in drawer.adapter_info.text()
drawer.grab().save(os.path.join(SHOTS, "bt_drawer_adapter.png"))
rxenv.sent.clear()
rows_c[0].btn.click()
assert without_list(cmds()) == [(BT_ADAPTER_USE, 0, bytes(6))] * 3, "Use = BT_ADAPTER_USE with the hciN number"
bt_list(DEVS, flags=both | BTA_PICK << BTF_MODE_SHIFT, op=BT_ADAPTER_USE, res=BTR_OK)
bt_ctrls([(BTC_ACTIVE | BTC_PICKED, 0, QC), (BTC_USB | BTC_BLOCKED, 1, TP)]); drawer.refresh()
assert drawer.adapter_seg.value() == str(BTA_PICK) and "wybrany" in rows_c[0].detail.text()
assert not rows_c[0].btn.isVisible() and rows_c[1].btn.isVisible() and "Zmiana kontrolera: gotowe" in drawer.result.text()
drawer.adapter_seg.buttons[str(BTA_AUTO)].click()
assert without_list(cmds()) == [(BT_ADAPTER, BTA_AUTO, bytes(6))] * 3
drawer.refresh()
assert drawer.adapter_seg.value() == str(BTA_AUTO), "during the change no flash of the old mode"
bt_list(DEVS, flags=BTF_HAS_BUILTIN | BTA_AUTO << BTF_MODE_SHIFT)
bt_ctrls([(BTC_ACTIVE, 0, QC)]); drawer.refresh(); pump()
assert [r.name.full for r in drawer.ctrl_rows.values()] == [QC], "an unplugged dongle disappears from the list"
assert "Dongle Bluetooth USB zwykle zmniejsza" in drawer.adapter_info.text(), "no dongle: a hint"
drawer.close_drawer(); pump(260)
ok("controllers: list of all with name, kind, hciN and state; Use (BT_ADAPTER_USE), Auto / Picked / Manual")

# ---------- old / silent sender ----------
RB.reset()
win.bt_btn.click(); pump(260)
assert not drawer.scan_btn.isEnabled() and not rows() and "brak danych" in drawer.status.text()
rxenv.sent.clear(); drawer.scan_btn.click()
assert not cmds(), "sender without Bluetooth support - nothing goes out"
drawer.close_drawer(); pump(260)
ok("sender without Bluetooth from the window (older / offline): the panel explains, buttons disabled")

win.quit()
print("BT GUI OK")
