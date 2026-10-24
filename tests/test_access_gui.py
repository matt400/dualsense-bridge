"""Okno: panel trybu dostępności w widoku Test - przełącznik per pad, suwaki z zapisem, pierścień zasięgu na podglądzie gałek,
kreator "Dostosuj do ręki" (propozycje trafiają do suwaków i włączają tryb), "Przywróć zalecane", komunikaty o porażce,
czyszczenie i przełączanie padów."""
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

C.cfg["tray"] = False
import rx_drift as D
import rx_padcal
import rx_profiles as P
import rx_qt
import rx_reach as R
from proto import BODY, CAP_CALIB, CMD_RAW_STICKS, RUMBLE_HIDRAW, T_CMD, T_IDENT
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
PAD = "aa:aa:aa:aa:aa:03"
cmds = lambda: [BODY[T_CMD].unpack_from(pkt, 4) for t, _i, _sq, pkt in sent if t == T_CMD]


def byte(f):
    return max(0, min(255, round(128 + f * (128 if f < 0 else 127))))


def circle(r, n=240):
    return [(128, 128, byte(r * math.cos(2 * math.pi * k / n)), byte(r * math.sin(2 * math.pi * k / n))) for k in range(n)]


def run_wizard(ap, rim, rest=(128, 128, 130, 127)):
    """Przeprowadza kreator na sztucznym czasie: spoczynek, gotowość, okrążenie gałką po promieniu rim."""
    t = ap.wiz._t0
    state(0, sticks=rest); ap.tick(t + D.SETTLE_S); state(0, sticks=rest); ap.tick(t + D.SETTLE_S + D.MEASURE_S)
    t += D.SETTLE_S + D.MEASURE_S
    ap.tick(t + R.GET_READY_S)
    t += R.GET_READY_S
    if ap.wiz is not None and ap.wiz.step == "reach":
        for s in circle(rim):
            state(0, sticks=s)
        ap.tick(t + R.REACH_S)


feed(T_IDENT, 0, PAD.encode()); state(0)
S.sender_info[0] = (9, RUMBLE_HIDRAW | CAP_CALIB, 0)
rx_padcal.reload()
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 900); win.show(); pump(150)
win.seg.buttons["test"].click(); pump()
panel = win.test_panel
ap = panel.access

# ---------- przełącznik i suwaki ----------
assert not ap.on.isChecked() and ap.sl["outer"].value() == 100 and not ap.restore.isEnabled() and not ap.clear_btn.isVisible()
ap.sl["outer"].slider.setValue(30)                                # 40 + 30 = 70 %
assert ap.sl["outer"].value() == 70 and P.access(0) == {}, "zapis dopiero po chwili od ostatniego ruchu"
pump(450)
assert P.access(0)["right"]["outer"] == 0.7 and "on" not in P.access(0), "suwak zapisał, tryb dalej wyłączony"
state(0, sticks=(128, 128, 220, 128))
assert S.states[0]["rx"] == 220, "wyłączony tryb = gra dostaje gałkę bez zmian"
assert panel.scope.reach == {}, "bez włączenia nie ma pierścienia zasięgu"
ap.on.setChecked(True); pump()
assert P.access(0)["on"] is True and S.states[0]["rx"] == 255, "włączony: 70% wychylenia = pełne w grze"
assert panel.scope.reach == {"right": 0.7} and ap.clear_btn.isVisible()
ap.which.setCurrentIndex(1); ap.which.activated.emit(1)
assert ap.stick == "left" and ap.sl["outer"].value() == 100, "suwaki pokazują wybraną gałkę"
ap.sl["dz"].slider.setValue(8); pump(450)
assert P.access(0)["left"]["dz"] == 0.08 and P.access(0)["right"]["outer"] == 0.7, "druga gałka zostaje"
ap.which.setCurrentIndex(0); ap.which.activated.emit(0)
assert ap.stick == "right" and ap.sl["outer"].value() == 70
ok("panel: suwaki zapisują się z opóźnieniem, przełącznik włącza działanie, pierścień zasięgu na podglądzie, gałki niezależne")

# ---------- kreator ----------
sent.clear()
ap.choice.set_data("right")
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
assert ap.wiz is not None and cmds() == [(CMD_RAW_STICKS, D.RAW_S)] * 3 and ap.bar.isVisible() and ap.start.text() == "Przerwij"
assert "mierzę dryf" in ap.msg.text()
run_wizard(ap, 0.62)
assert ap.wiz is None and not ap.bar.isVisible() and ap.start.text() == "Dostosuj do ręki"
cfg = P.access(0)
assert cfg["on"] is True and 0.55 <= cfg["right"]["outer"] <= 0.6 and cfg["right"]["dz"] >= D.MIN_DZ, cfg
assert "✓" in ap.msg.text() and "Prawa gałka" in ap.msg.text() and cmds()[-1] == (CMD_RAW_STICKS, 0)
assert ap.sl["outer"].value() == round(cfg["right"]["outer"] * 100) and ap.restore.isEnabled(), "propozycja w suwakach"
assert cfg["left"]["dz"] == 0.08, "kreator dla prawej gałki nie rusza lewej"
proposal = ap.sl["outer"].value()
ap.sl["outer"].slider.setValue(50); pump(450)                       # ręczna zmiana po kreatorze
assert P.access(0)["right"]["outer"] == 0.9, "dalej można zmienić ręcznie"
QTest.mouseClick(ap.restore, Qt.MouseButton.LeftButton); pump()
assert ap.sl["outer"].value() == proposal and P.access(0)["right"]["outer"] == proposal / 100, "Przywróć zalecane"
ok("kreator: propozycja trafia do suwaków i włącza tryb, ręczna zmiana możliwa, Przywróć zalecane wraca do wyniku")

# porażki i przerwanie
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
run_wizard(ap, 0.62, rest=(128, 128, 200, 128))
assert ap.wiz is None and "ruszała" in ap.msg.text() and P.access(0)["right"]["outer"] == proposal / 100, "porażka nie zmienia ustawień"
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
assert ap.wiz is None and not ap.msg.isVisible() and S.probe[0] is None and cmds()[-1] == (CMD_RAW_STICKS, 0), "Przerwij sprząta"
ap.choice.set_data("both")
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
assert ap.wiz.steps[-1] == ("reach", "right") and len(ap.wiz.steps) == 5
S.alive[0] = False
ap.tick(0.0)
assert ap.wiz is None and "niepodłączony" in ap.msg.text() and S.probe[0] is None, "pad zniknął - kreator przerwany"
S.alive[0] = True
ok("kreator: porażka zostawia ustawienia, Przerwij sprząta, obie gałki = 5 kroków, zniknięcie pada przerywa")

# ---------- czyszczenie i przełączanie padów ----------
win.select_pad(1); pump()
assert ap.slot == 1 and not ap.on.isChecked() and ap.sl["outer"].value() == 100 and P.access(1) == {}, "ustawienia są per pad"
win.select_pad(0); pump()
assert ap.slot == 0 and ap.on.isChecked() and ap.sl["outer"].value() == proposal
ap.clear_btn.click(); ap.clear_btn.click(); pump()
assert P.access(0) == {} and not ap.on.isChecked() and not ap.clear_btn.isVisible() and panel.scope.reach == {}
state(0, sticks=(128, 128, 220, 128))
assert S.states[0]["rx"] == 220, "po wyczyszczeniu gra znów dostaje gałkę bez zmian"
win.seg.buttons["out"].click(); pump()
ok("czyszczenie i przełączanie: ustawienia per pad, Wyczyść przywraca zwykłą pracę gałek")
