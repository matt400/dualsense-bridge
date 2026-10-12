import sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")            # bez ekranu i bez Xvfb
from rxenv import *                                              # noqa
import rx_i18n; rx_i18n.set_lang("en")              # asercje na angielskich tekstach okna
from proto import T_CMD
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QToolButton

C.cfg["tray"] = False
import rx_editor, rx_games, rx_qt
win = rx_qt.create_window(os.path.join(TMP, "ui.ini"))
app = QApplication.instance()
win.resize(1160, 720); win.show()
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=60: QTest.qWait(ms)
base = S.profile_key(0)
pump(150)


def click_part(name):
    """Prawdziwe kliknięcie myszą w środek części rysunku pada."""
    part = win.pad.by_name[name]
    p = win.pad._tf.map(part.path.boundingRect().center())
    QTest.mouseClick(win.pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(p.x()), round(p.y())))
    pump(20)


# ---------- każda część rysunku jest klikalna i trafia we właściwą nazwę ----------
for part in win.pad.parts:
    p = win.pad._tf.map(part.path.boundingRect().center())
    assert win.pad._hit(QPointF(p)) == part.name, part.name
assert win.pad._hit(QPointF(2, 2)) is None
ok("rysunek pada: wszystkie części trafiane kliknięciem, tło nie")

# ---------- karty padów: łącze, bateria, profil gry, wyłączanie ----------
S.alive[0] = True; S.batt_info[0] = (64, 0); S.batt_info[1] = (12, 0)
S.info[0] = (1.2, 540, 500, time.monotonic()); S.link_loss = 0.0; S.pps[0] = 250.0
S.last_pkt = time.monotonic(); S.peer = ("192.168.1.50", 5005)
store.save(base + "@doom.exe", {}); S.set_game("doom.exe")
win.slow_tick(); pump(20)
c0, c1 = win.cards
assert c0.lat.text() == "ping 1.2 ms" and not c0.lat_bar.isVisible(), "bez pomiaru opóźnienia: ping"
tip = c0.lat.toolTip()
assert "loss 0.0%" in tip and "pad 540 Hz" in tip and "250 pkt/s" in tip and "older version" in tip, tip
assert c0.profile.text().startswith("profile: doom.exe"), c0.profile.text()
assert c0.pct.text() == "64%" and c1.pct.text() == "12%" and "connected" in c0.status.text() and "not connected" in c1.status.text()
assert c0.off.isEnabled() and not c1.off.isEnabled() and not c1.body.isVisible() and c1.property("off") is True
assert not c0.status.isVisible() and c1.status.isVisible(), "status tylko dla niepodłączonego / bez sygnału"
# pomiar opóźnienia pad -> gra: mediana w karcie, odcinki na pasku i w podpowiedzi
import rx_latency
from proto import LAT_NONE
t_ns = time.perf_counter_ns()
for k in range(20):
    rx_latency.add(0, (t_ns - 3_000_000, 1500, LAT_NONE if k % 2 else 400, 600), t_ns - 2_000_000 + k)
win.slow_tick(); pump(20)
assert c0.lat.text().endswith(" ms") and "p99" in c0.lat_p99.text() and c0.lat_bar.isVisible(), c0.lat.text()
assert [col for _v, col in c0.lat_bar.parts] == [rx_qt.SEGMENT[k] for k in ("bt", "phone", "net", "pc")], c0.lat_bar.parts
assert "Phone (kernel → send)" in c0.lat.toolTip() and "Network" in c0.lat.toolTip()
rx_latency.clear(0); win.slow_tick()
ok("karta pada: opóźnienie pad -> gra z odcinkami, ping przed pomiarem, szczegóły łącza w podpowiedzi")
assert "online" in win.sender.text() and "192.168.1.50" in win.sender.toolTip()   # adres w podpowiedzi (wąskie okno)
S.batt_info[0] = (10, 0); win.slow_tick()
assert c0.property("low") is True and win.windowTitle().endswith("LOW BATTERY") and win.warned[0]
S.batt_info[0] = (64, 0); win.slow_tick()
assert c0.property("low") is False and win.windowTitle() == "DualSense bridge" and not win.warned[0]
sent.clear()
c0.off.click(); assert c0.off.text() == "Turn off?" and not sent, "pierwsze kliknięcie tylko uzbraja przycisk"
c0.off.click(); assert sum(1 for x in sent if x[0] == T_CMD) == 3 and c0.off.text() == "", "ikona bez napisu po potwierdzeniu"
ok("karty padów: łącze, bateria, niski stan, profil gry, wyłączenie po drugim kliknięciu")

# ---------- klik w część -> edytor; suwaki działają na żywo ----------
S.set_game(None)
click_part("right_stick"); assert win.part == "right_stick" and win.part_page.stack.currentIndex() == 2
click_part("r2"); assert win.part_page.stack.currentIndex() == 1 and win.part_page.title.text() == "R2 (trigger)" and win.part_page.trig.isVisible()
# opór spustu: siła 0 = wyłączony (początek zablokowany), zmiana idzie do profilu i na plakietkę
pp = win.part_page
assert pp.fx_force.val.text() == "off" and not pp.fx_start.isEnabled()
pp.fx_force.slider.setValue(6); pp.fx_start.slider.setValue(3); pump(300)
assert store.get(base)["triggers"] == {"r2": {"start": 3, "force": 6}} and pp.fx_start.isEnabled()
assert any(b[3] == "fx" and b[2] == "resist 6" for b in win.pad.badges)
assert any("Resistance R2: from 30%, strength 6/8" == t for t, _u in win.ed.changes())
assert pp.fx_warn.isVisible() and "older sender" in pp.fx_warn.text(), "pad bez informacji o nadajniku v6"
S.sender_info[0] = (6, 7, 0); win.slow_tick(); assert not pp.fx_warn.isVisible()
S.sender_info[0] = (6, 1, 0); win.slow_tick(); assert pp.fx_warn.isVisible() and "HID" in pp.fx_warn.text()
S.sender_info[0] = None
pp.fx_force.slider.setValue(0); pump(300)
assert "triggers" not in store.get(base) or store.get(base)["triggers"] == {}
assert not pp.trig_adv.is_open(), "reakcja spustu zwinięta, dopóki nic w niej nie zmieniono"
ok("opór spustu: siła / początek, profil, plakietka, ostrzeżenie o nadajniku")
click_part("cross"); assert not win.part_page.trig.isVisible()
win.part_page.dst.changed.emit("circle"); pump(300)
assert store.get(base)["buttons"] == {"cross": "circle"} and S.remap_maps[0]["buttons"] == {"cross": "circle"}
assert "Applied live" in win.status.text() and win.tabs.tabText(2) == "Changes (1)"
click_part("r2")
win.part_page.trig_adv.set_open(True)
win.part_page.tdz.slider.setValue(30); pump(300)
assert store.get(base)["tuning"] == {"r2": {"dz": 30, "max": 255}}
click_part("left_stick")
win.part_page.dz.slider.setValue(12); win.part_page.curve.slider.setValue(round((1.5 - 0.3) / 0.05)); pump(300)
assert store.get(base)["tuning"]["left"] == {"dz": 0.12, "outer": 1.0, "curve": 1.5, "anti": 0.0}
win.part_page.invx.setChecked(True); win.part_page.ssrc.changed.emit("none"); pump(300)
assert store.get(base)["sticks"]["left"] == {"src": "none", "inv_x": True, "inv_y": False}
win.part_page.l3.changed.emit("__none"); pump(300)
assert store.get(base)["buttons"]["l3"] is None
assert any(b[2] == "→ ∅" and b[3] for b in win.pad.badges) and any(b[2] == "∿" for b in win.pad.badges)
ok("klik w pad: wybór części, przycisk, spust, gałka (strefa, krzywa, zamiana, L3), plakietki")

# ---------- gyro ----------
win.tabs.setCurrentIndex(1)
assert not win.gyro_page.body.isVisible() and win.gyro_page.off_hint.isVisible()
win.gyro_page.mode.changed.emit("stick"); win.gyro_page.hold.changed.emit("l2"); pump(20)
assert win.gyro_page.body.isVisible() and win.gyro_page.anti.isVisible()
win.gyro_page.sens.slider.setValue(round((1.75 - 0.1) / 0.05)); pump(300)
assert store.get(base)["gyro"]["mode"] == "stick" and store.get(base)["gyro"]["hold"] == "l2" and store.get(base)["gyro"]["sens"] == 1.75
win.gyro_page.mode.changed.emit("mouse"); pump(20); assert not win.gyro_page.anti.isVisible()
win.gyro_page.mode.changed.emit("stick"); pump(300)
state(0, lt=200, gyro=(0, -1024 * 40, 0)); win.live_tick()
assert "Gyro active" in win.gyro_page.live.text(), win.gyro_page.live.text()
state(0, lt=0, gyro=(0, 0, 0)); win.live_tick()
assert "Gyro idle — hold L2" in win.gyro_page.live.text(), win.gyro_page.live.text()
ok("gyro: tryb, aktywacja, czułość, wiersz stanu na żywo")

# ---------- lista zmian, cofanie, zapis ----------
win.tabs.setCurrentIndex(2)
assert win.tabs.tabText(2) == "Changes (6)", win.tabs.tabText(2)
texts = [lb.text() for lb in win.changes_page.box.findChildren(QLabel)]
assert any("Gyro: right stick, hold L2" in t for t in texts) and any("Left stick response" in t for t in texts), texts
first = win.changes_page.box.findChildren(QFrame, "row")[0]
first.findChild(QToolButton).click(); pump(300)          # × przy pierwszej pozycji (Cross → Circle)
assert "cross" not in store.get(base)["buttons"] and win.tabs.tabText(2) == "Changes (5)"
win.save_btn.click(); pump(100)
saved = json.load(open(C.REMAP_PATH))["profiles"][base]
assert saved["gyro"]["hold"] == "l2" and saved["buttons"] == {"l3": None} and "Saved" in win.status.text() and win.status.property("level") == "ok"
ok("lista zmian: cofnięcie pozycji, zapis do remap.json")

# ---------- profile gier z okna ----------
bar = win.profile_bar
rx_games.recent[:] = ["eldenring.exe", "cyberpunk2077.exe"]
win.slow_tick(); bar._open_new()
assert bar.form.isVisible() and bar.exe.text() == "eldenring.exe", "podpowiedź z ostatnich gier"
bar.exe.setText("not/a game"); bar._create(); pump(20)
assert bar.form.isVisible() and "eldenring.exe" in win.status.text() and win.status.property("level") == "bad"
bar.exe.setText("EldenRing"); bar._create(); pump(20)
key = base + "@eldenring.exe"
assert not bar.form.isVisible() and win.ed.key == key and store.has(key) and not store.in_file(key)
assert store.get(key)["gyro"]["mode"] == "stick", "kopia bieżących ustawień"
assert bar.combo.currentText().startswith("eldenring.exe") and bar.remove.isVisible()
win.part_page.l3.changed.emit("l3"); win.save_btn.click(); pump(50)
assert store.in_file(key) and key in json.load(open(C.REMAP_PATH))["profiles"]
bar.combo.changed.emit(base); pump(20)
assert win.ed.key == base and not bar.remove.isVisible() and win.tabs.tabText(2) == "Changes (5)"
bar.combo.changed.emit(key); pump(20); assert S.active[0] == key
bar.remove.click(); assert bar.remove.text() == "Sure?" and store.has(key)
bar.remove.click(); pump(20)
assert not store.has(key) and win.ed.key == base and "Profile deleted" in win.status.text()
ok("profile gier: podpowiedzi, walidacja nazwy, kopia ustawień, przełączanie, usuwanie po potwierdzeniu")

# ---------- widok na żywo: wynik vs fizyczny pad ----------
S.alive[0] = True
state(0, mask=BIT[304]); win.live_tick()
store.save(base, {"buttons": {"cross": "circle"}}); S.reload_profile(0); win.ed.select(); win.refresh_all()
state(0, mask=BIT[304]); win.live_tick()
assert win.pad.live["on"] == {"circle"}, win.pad.live
win.seg.buttons["in"].click(); pump(20)
assert win.view == "in" and win.pad.live["on"] == {"cross"}, win.pad.live
state(0, sticks=(255, 0, 128, 128), lt=255); win.live_tick()
assert win.pad.live["ls"] == (255, 0) and win.pad.live["l2"] == 255
win.seg.buttons["out"].click(); pump(20); assert win.view == "out"
ok("widok na żywo: wynik po remapowaniu / fizyczny pad, gałki, spusty")

# ---------- wybór pada, ustawienia, ukrywanie ----------
QTest.mouseClick(win.cards[1], Qt.MouseButton.LeftButton); pump(20)
assert win.ed.slot == 1 and win.cards[1].property("selected") is True and win.cards[0].property("selected") is False
win.save_settings()
win.ed.set_button("cross", "circle"); win.on_edit(); pump(20)
rst = win.changes_page.reset
assert rst.isVisibleTo(win.changes_page) and win.revert_btn.isEnabled()
rst.click(); assert win.ed.prof["buttons"] == {"cross": "circle"}, "pierwsze kliknięcie tylko uzbraja"
rst.click(); pump(20)
assert win.ed.prof == rx_editor.empty() and win.tabs.tabText(2) == "Changes" and not rst.isVisibleTo(win.changes_page)
ok("Usuń wszystkie: na liście zmian, po drugim kliknięciu, znika przy pustym profilu")
win.hide(); assert not win.live_timer.isActive() and win.slow_timer.isActive(), "ukryte okno nie odrysowuje pada, ale odświeża zasobnik"
win.show(); pump(50); assert win.live_timer.isActive()
win2 = rx_qt.create_window(os.path.join(TMP, "ui.ini"))
assert win2.ed.slot == 1 and win2.view == "out", "ustawienia okna zapamiętane"
win2.slow_timer.stop(); win2.deleteLater()
ok("wybór pada kartą, zapamiętane ustawienia, ukrywanie wstrzymuje odrysowywanie")

# ---------- pełny ekran = tryb TV: większa skala, sterowanie padem (strzałki po częściach rysunku) ----------
import rx_qt_style
w0 = win.right_col.width()
win.set_fullscreen(True); pump(50)
assert rx_qt_style.scale == rx_qt_style.TV_SCALE and win.right_col.width() > w0
win.pad.set_selected(None); win.pad.hover = None; win.pad.setFocus()
QTest.keyClick(win.pad, Qt.Key.Key_Right); assert win.pad.hover == "l2", win.pad.hover
QTest.keyClick(win.pad, Qt.Key.Key_Return); pump(20); assert win.part == "l2"
win.set_fullscreen(False); pump(50)
assert rx_qt_style.scale == 1.0 and win.right_col.width() == w0
ok("pełny ekran: skala TV i z powrotem, klawiatura na rysunku pada")

shot = os.path.join(os.path.dirname(__file__), "gui.png")
win.select_pad(0); click_part("left_stick"); pump(100)
win.grab().save(shot)
assert os.path.getsize(shot) > 10000
ok("zrzut okna: tests/gui.png")
