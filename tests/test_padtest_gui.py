"""Okno: widok Test (trzeci przełącznik pod padem) - panel testu zamiast edycji, blokada wyjścia do gry tylko dla
testowanego pada, dotyk i spusty na rysunku, wykresy ruchu, pomiar dryfu z "Zastosuj", kalibracja żyroskopu,
wyjście z testu w trybie TV przytrzymanym ○ i z pada (PS + △, bez myszy). Zrzut: tests/test_view.png."""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

C.cfg["tray"] = False
import rx_drift as D
import rx_padcal
import rx_profiles as P
import rx_qt
from proto import (BODY, CAL_OK, CAL_RUNNING, CALF_STORED, CAP_CALIB, CMD_GYRO_CAL, CMD_RAW_STICKS, RUMBLE_HIDRAW,
                   T_CALRES, T_CLICK, T_CMD, T_IDENT, T_TOUCH)
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
A = "aa:aa:aa:aa:aa:01"
SHOTS = os.environ.get("SHOTS") or os.path.dirname(__file__)
def activate(w):
    """Aktywne okno: platforma offscreen nie aktywuje ponownie pokazanego okna przez activateWindow()."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(w)
    pump()


cmds = lambda: [BODY[T_CMD].unpack_from(pkt, 4) for t, _i, _sq, pkt in sent if t == T_CMD]

feed(T_IDENT, 0, A.encode()); state(0)
S.sender_info[0] = (9, RUMBLE_HIDRAW | CAP_CALIB, 0)
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 860); win.show(); pump(150)
panel = win.test_panel

# ---------- przełącznik i blokada ----------
assert list(win.seg.buttons) == ["out", "in", "test"] and win.right_stack.currentIndex() == 0
assert not S.states[0]["blocked"]
sent.clear()
win.seg.buttons["test"].click(); pump()
assert win.view == "test" and win.right_stack.currentIndex() == 1 and win.pad.test
assert S.states[0]["blocked"] and not S.states[1]["blocked"], "zablokowany tylko testowany pad"
assert cmds() == [(CMD_GYRO_CAL, 0)] * 3, f"wejście do testu pyta nadajnik o stan kalibracji: {cmds()}"
assert "na żywo" in win.pad_hint.text()
panel.block.setChecked(False); pump()
assert not S.states[0]["blocked"]
panel.block.setChecked(True); pump()
assert S.states[0]["blocked"]
win.select_pad(1); pump()
assert S.states[1]["blocked"] and not S.states[0]["blocked"], "blokada idzie za wybranym padem"
win.select_pad(0); pump()
win.on_part("cross")
assert win.part is None, "w teście klik w część pada nie otwiera remapowania"
ok("Test: panel zamiast edycji, blokada tylko wybranego pada (przełącznik), klik w pad nie remapuje")

# ---------- na żywo: dotyk, spusty, ruch, gałki ----------
state(0, mask=BIT[304], lt=120, rt=255, sticks=(131, 126, 128, 128), gyro=(2048, -1024, 0))
feed(T_TOUCH, 0, 1, 1, 960, 470, 1, 2, 300, 100)
feed(T_CLICK, 0, 1)
for _k in range(5):
    win.live_tick()
pump()
assert win.pad.live["l2"] == 120 and win.pad.live["r2"] == 255 and "cross" in win.pad.live["on"], "rysunek fizycznie"
assert win.pad.touch_live == (((960, 470), (300, 100)), 1)
assert panel.gyro_chart.data[-1] == (2.0, -1.0, 0.0) and abs(panel.acc_chart.data[-1][2] - 1.0) < 1e-9
assert panel.scope.pos["left"] == (D.unit(131), D.unit(126))
win.grab().save(os.path.join(SHOTS, "test_view.png"))
feed(T_CLICK, 0, 0)
ok("na żywo: przyciski i spusty (wartości) na rysunku, 2 palce i klik touchpada, gyro °/s i akcelerometr g, gałki")

# ---------- dryf: pomiar -> propozycja -> Zastosuj ----------
sent.clear()
QTest.mouseClick(panel.measure, Qt.MouseButton.LeftButton); pump()
assert panel.run is not None and not panel.measure.isEnabled() and cmds() == [(CMD_RAW_STICKS, D.RAW_S)] * 3
t0 = panel.run.t0
panel._drift_tick(t0 + D.SETTLE_S)
for lx in (132, 126, 133):
    state(0, sticks=(lx, 128, 128, 129))
panel._drift_tick(t0 + D.SETTLE_S + D.MEASURE_S); panel.refresh(); pump()
assert panel.run is None and panel.result is not None and cmds()[-1] == (CMD_RAW_STICKS, 0)
assert "3.9%" in panel.drift_res.text() and "→" in panel.drift_res.text() and panel.apply.isEnabled(), panel.drift_res.text()
assert panel.scope.peak["left"] == panel.result.left.peak
QTest.mouseClick(panel.apply, Qt.MouseButton.LeftButton); pump()
assert P.drift(0) == {"left": 0.05, "right": D.MIN_DZ} and "✓" in panel.drift_res.text() and not panel.apply.isEnabled()
assert "5%" in panel.corr.text() and panel.clear_btn.isVisible() and panel.scope.dz == {"left": 0.05, "right": 0.03}
win.grab().save(os.path.join(SHOTS, "test_view_drift.png"))
panel.clear_btn.click(); panel.clear_btn.click(); pump()
assert P.drift(0) == {} and not panel.clear_btn.isVisible()
panel.start_drift(); panel._drift_tick(panel.run.t0 + D.SETTLE_S)
state(0, sticks=(250, 128, 128, 128))
panel._drift_tick(panel.run.t0 + D.SETTLE_S + D.MEASURE_S); panel.refresh()
assert panel.result.moved and not panel.apply.isEnabled() and "ruszała" in panel.drift_res.text()
state(0)
ok("dryf: gałki bez strefy nadajnika na czas pomiaru, wynik i propozycja, Zastosuj zapisuje korekcję pada, Wyczyść")

# ---------- kalibracja żyroskopu ----------
sent.clear()
feed(T_CALRES, 0, 3, 0, 0, 0, 0, 512, 0, -256, 0)
panel.refresh()
assert "Brak zapisanej" in panel.cal_text.text() and "+0.50" in panel.cal_text.text(), panel.cal_text.text()
QTest.mouseClick(panel.cal_btn, Qt.MouseButton.LeftButton); pump()
assert cmds() == [(CMD_GYRO_CAL, 3)] * 3 and not panel.cal_btn.isEnabled() and "nie ruszaj" in panel.cal_text.text()
feed(T_CALRES, 0, CAL_RUNNING, 0, 0, 0, 0, 512, 0, -256, 0)
panel._cal_tick(time.monotonic()); panel.refresh()
assert panel.cal_t0 is not None, "'trwa' to jeszcze nie wynik"
feed(T_CALRES, 0, CAL_OK, CALF_STORED, 400, 10, -20, 400, 10, -20, int(time.time()))
panel._cal_tick(time.monotonic()); panel.refresh(); pump()
assert panel.cal_t0 is None and panel.cal_btn.isEnabled()
assert "Skalibrowano" in panel.cal_text.text() and "Zapisana kalibracja" in panel.cal_text.text(), panel.cal_text.text()
panel.start_cal()
panel.cal_t0 -= 10                                               # 10 s bez odpowiedzi
panel._cal_tick(time.monotonic()); panel.refresh()
assert "Brak odpowiedzi" in panel.cal_text.text(), "bez odpowiedzi po czasie - komunikat zamiast wiecznego czekania"
S.sender_info[0] = (8, RUMBLE_HIDRAW, 0); panel.refresh()
assert not panel.cal_btn.isEnabled() and "nowszego nadajnika" in panel.cal_text.text()
S.sender_info[0] = (9, RUMBLE_HIDRAW | CAP_CALIB, 0)
ok("kalibracja: polecenie z czasem, postęp, wynik z datą i offsetem, brak odpowiedzi, stary nadajnik = podpowiedź")

# ---------- wyjście z testu ----------
win.set_view("in"); pump()
assert win.right_stack.currentIndex() == 0 and not win.pad.test and not S.states[0]["blocked"]
assert win.seg.value() == "in" and "zremapować" in win.pad_hint.text()
win.set_view("test"); pump()
panel.start_drift()
win.hide(); pump()
assert not S.states[0]["blocked"] and panel.run is None and S.probe[0] is None, "schowane okno: gra dostaje pada"
win.show(); activate(win)
assert S.states[0]["blocked"]
other = QWidget(); other.show(); activate(other)
assert not win.isActiveWindow() and not S.states[0]["blocked"], "inne okno na pierwszym planie (gra) - pad wraca do gry"
other.close(); activate(win)
assert S.states[0]["blocked"]
win.showMinimized(); pump()
assert not S.states[0]["blocked"], "zminimalizowane okno (bez zasobnika) też oddaje pada"
win.showNormal(); activate(win)
assert S.states[0]["blocked"]
win.save_settings()
assert win.settings.value("view") == "in", "widok Test (z blokadą) nie wraca sam po restarcie"
ok("wyjście: inny widok, schowane / zminimalizowane / nieaktywne okno zdejmują blokadę; Test nie jest zapamiętywany")

# ---------- tryb TV: testowany pad nie steruje oknem, ○ przytrzymane wychodzi ----------
win.set_fullscreen(True); pump(100)
assert "○" in win.pad_hint.text()
state(0, mask=BIT[305])
assert "circle" not in win.nav._pressed(), "○ testowanego pada nie zamyka pełnego ekranu"
now = time.monotonic()
win._exit_hold(now, True); win._exit_hold(now + 0.5, True)
assert win.view == "test"
win._exit_hold(now + D.SETTLE_S + 1.0, True)
assert win.view == "in" and not S.states[0]["blocked"]
assert "circle" in win.nav._pressed(), "po teście pad znowu steruje oknem"
state(0)
win.set_fullscreen(False); pump(100)
ok("tryb TV: testowany pad nie steruje oknem, przytrzymane ○ wraca do poprzedniego widoku")

# ---------- PS + △ z pada: wyjście z testu bez myszy, także przy nieaktywnym oknie ----------
import rx_testmode as TM
from rx_buttons import PS_BIT
win.seg.buttons["test"].click(); activate(win)
assert win.view == "test" and TM.active and S.states[0]["blocked"], "otwarty Test wyłącza skróty padów"
assert "PS + △" in win.pad_hint.text()
state(0, mask=PS_BIT | BIT[307])
assert TM.tick(time.monotonic() + TM.ESCAPE_S); pump()
assert win.view == "in" and not TM.active and not S.states[0]["blocked"], "wyjście z pada: widok i blokada"
assert "PS + △" in win.status.text()
win.sync_block()
assert not S.states[0]["blocked"] and not TM.active, "aktywacja okna przed obsługą sygnału nie blokuje znowu"
state(0)
win.seg.buttons["test"].click(); activate(win)
assert TM.active and S.states[0]["blocked"], "ponowne wejście w Test działa jak wcześniej"
other = QWidget(); other.show(); activate(other)
assert not TM.active, "okno w tle (gra) - skróty padów działają"
other.close(); activate(win)
assert TM.active
win.set_view("in"); pump()
assert not TM.active
ok("PS + △ z pada: widok wraca, blokada zdjęta, Test działa ponownie; skróty stoją tylko przy aktywnym oknie testu")

win.quit()
assert not any(s["blocked"] for s in S.states) and not TM.active
rx_padcal.reload()
ok("zamknięcie okna zdejmuje blokadę")
