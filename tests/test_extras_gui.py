"""Okno: panel dziennika zdarzeń (ikona w nagłówku, filtr, limit, zdarzenia nadajnika, kopiowanie, czyszczenie) oraz
zakładki Lightbar i Wake-on-LAN w Ustawieniach (zapis bez restartu, walidacja adresów)."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_events as ev, rx_qt, rx_settings as R
from proto import T_IDENT
from rx_console import Console, ConsoleConfig
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
A = "aa:aa:aa:aa:aa:01"
SHOTS = os.environ.get("SHOTS") or os.path.dirname(__file__)

rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = Console(ConsoleConfig())
feed(T_IDENT, 0, A.encode()); state(0)
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 820); win.show(); pump(150)

# ---------- dziennik zdarzeń ----------
assert not win.log_panel.isVisible() and not win.log_btn.isChecked(), "domyślnie schowany"
ev.clear()
ev.info("Pad 1 połączony"); ev.warn("Nadajnik offline"); ev.error("błąd ViGEm")
ev.add("info", "Wysłano Wake-on-LAN", sender=True)
win.log_btn.click(); pump()
assert win.log_panel.isVisible() and win.log_btn.isChecked()
text = win.log_panel.view.toPlainText()
assert "Pad 1 połączony" in text and "błąd ViGEm" in text and "Wake-on-LAN" not in text, "zdarzenia nadajnika domyślnie wyłączone"
win.log_panel.level.set_data("warn"); win.log_panel.level.changed.emit("warn"); pump()
text = win.log_panel.view.toPlainText()
assert "Pad 1" not in text and "Nadajnik offline" in text and R.cur.log.level == "warn"
assert json.load(open(C.SETTINGS_PATH))["log"]["level"] == "warn", "filtr zapisany w settings.json"
win.log_panel.sender_btn.click(); pump()
assert R.cur.log.sender and "Wake-on-LAN" not in win.log_panel.view.toPlainText(), "info nadajnika pod filtrem warn"
win.log_panel.level.set_data("info"); win.log_panel.level.changed.emit("info"); pump()
assert "📱 Wysłano Wake-on-LAN" in win.log_panel.view.toPlainText()
ev.info("nowy wpis"); win.slow_tick(); pump()
assert win.log_panel.view.toPlainText().splitlines()[-1].endswith("nowy wpis"), "odświeżanie dopisuje nowe"
import time as _t
ev.add("info", "pad połączony z telefonem", sender=True, t=_t.time() - 120); win.slow_tick(); pump()
lines = win.log_panel.view.toPlainText().splitlines()
old_i = next(k for k, l in enumerate(lines) if l.endswith("pad połączony z telefonem"))
assert old_i < next(k for k, l in enumerate(lines) if l.endswith("nowy wpis")) and lines[-1].endswith("nowy wpis"), \
    ("starsze zdarzenie nadajnika wstawione wg czasu, nie na koniec", lines)
win.log_panel.limit.setValue(50); win.log_panel.limit.editingFinished.emit(); pump()
for k in range(80):
    ev.info(f"wpis {k}")
win.slow_tick(); pump()
lines = win.log_panel.view.toPlainText().splitlines()
assert len(lines) == 50 and lines[-1].endswith("wpis 79") and R.cur.log.max_lines == 50, len(lines)
win.log_panel.copy()
assert QApplication.clipboard().text().splitlines()[-1].endswith("wpis 79")
win.log_panel.grab().save(os.path.join(SHOTS, "gui_log.png"))
win.log_panel.clear_btn.click(); assert win.log_panel.view.toPlainText(), "pierwsze kliknięcie tylko uzbraja"
win.log_panel.clear_btn.click(); pump()
assert win.log_panel.view.toPlainText() == "" and ev.since(0) == []
win.save_settings()
assert win.settings.value("log_open") in (True, "true")
win.log_btn.click(); pump(); assert not win.log_panel.isVisible()
ok("dziennik: ikona w nagłówku, poziom, zdarzenia nadajnika, limit linii, kopiowanie, czyszczenie (2 kliknięcia), zapis")

# ---------- Ustawienia: Lightbar ----------
win.open_settings(); pump(100)
dlg = win.settings_dlg
dlg.tabs.setCurrentIndex(3); pump()
assert dlg.tabs.tabText(3) == "Lightbar" and dlg.tabs.tabText(4) == "Haptyka i głośnik" and dlg.tabs.tabText(5) == "Wake-on-LAN" and not dlg.tabs.tabIcon(4).isNull()
assert [dlg.light_pad.itemData(i) for i in range(dlg.light_pad.count())] == ["*", A]
assert not dlg.light_own.isVisible(), "domyślne ustawienie zawsze własne"
dlg.light_mode.set_data("battery"); dlg.light_mode.changed.emit("battery")
assert not dlg.light_color.isEnabled(), "kolor stały nieaktywny w trybie wg baterii"
dlg.light_pad.set_data(A); dlg.light_pad.changed.emit(A); pump()
assert dlg.light_own.isVisible() and not dlg.light_own.isChecked() and not dlg.light_mode.isEnabled()
assert dlg.light_mode.currentData() == "battery", "pad bez wpisu pokazuje domyślne"
dlg.light_own.setChecked(True)
dlg.light_mode.set_data("solid"); dlg.light_mode.changed.emit("solid")
dlg._set_color("#ff8800")
dlg.light_blink.setChecked(False)
dlg.low_pct.setValue(25)
dlg.grab().save(os.path.join(SHOTS, "gui_lightbar.png"))
dlg.save_btn.click(); pump()
lb = R.cur.lightbar
assert lb.low_pct == 25 and lb.pads == {"*": R.PadLight("battery", "", True), A: R.PadLight("solid", "#ff8800", False)}, lb
assert json.load(open(C.SETTINGS_PATH))["lightbar"]["pads"][A]["color"] == "#ff8800"
assert rx_qt.ST.low_pct() == 25, "ten sam próg dla karty pada"
dlg.close(); win.open_settings(); pump()
dlg.tabs.setCurrentIndex(3)
dlg.light_pad.set_data(A); dlg.light_pad.changed.emit(A)
dlg.light_own.setChecked(False)
dlg.save_btn.click(); pump()
assert A not in R.cur.lightbar.pads, "odznaczenie - pad wraca do domyślnego"
ok("Ustawienia -> Lightbar: domyślne + per pad (MAC), tryb, kolor, miganie, próg; zapis bez restartu")

# ---------- Ustawienia: Haptyka ----------
import rx_haptics
from proto import CAP_HAPTICS
dlg.close(); win.open_settings(); pump()
dlg.tabs.setCurrentIndex(4); pump()
assert [dlg.hap_pad.itemData(i) for i in range(dlg.hap_pad.count())] == ["*", A]
assert not dlg.hap_on.isChecked() and dlg.hap_gain.value() == 100 and not dlg.hap_own.isVisible(), "domyślnie wyłączona"
dlg.hap_on.setChecked(True); dlg.hap_gain.setValue(60)
dlg.hap_pad.set_data(A); dlg.hap_pad.activated.emit(dlg.hap_pad.currentIndex()); pump()
assert dlg.hap_own.isVisible() and not dlg.hap_own.isChecked() and not dlg.hap_on.isEnabled(), "pad bez własnego ustawienia: pola nieaktywne"
dlg.hap_own.setChecked(True); dlg.hap_on.setChecked(False); dlg.hap_gain.setValue(120)
S.alive[0], S.pad_id[0], S.sender_info[0] = True, A, (12, 3, 0)
dlg._haptics_enable()
assert dlg.hap_test.isEnabled() and "starszej" in dlg.hap_note.text(), "stary nadajnik: notka"
S.sender_info[0] = (13, 3 | CAP_HAPTICS, 0)
dlg._haptics_enable()
assert not dlg.hap_note.text()
dlg.grab().save(os.path.join(SHOTS, "gui_haptics.png"))
dlg.save_btn.click(); pump()
h = R.cur.haptics
assert h.get("*") == R.PadHaptics(True, 60) and h.get(A) == R.PadHaptics(False, 120), h
dlg._test_haptics(); pump()
assert rx_haptics.link(0)._inject, "przycisk testu zakolejkował ton"
rx_haptics.link(0).detach()
ok("Ustawienia -> Haptyka: domyślne + per pad, siła, notka o starym nadajniku, test, zapis bez restartu")

# ---------- Ustawienia: Wake-on-LAN ----------
S.sender_wol = ("11:22:33:44:55:66", "192.168.1.255")
dlg.close(); win.open_settings(); pump()
dlg.tabs.setCurrentIndex(5); pump()
assert "11:22:33:44:55:66" in dlg.wol_mac.placeholderText() and "11:22:33:44:55:66" in dlg.wol_seen.text()
assert dlg.wol_on.isChecked() and dlg.wol_btn.currentData() == "options" and dlg.wol_hold.value() == 1000
dlg.wol_mac.setText("zly-mac"); dlg.tabs.setCurrentIndex(0)
dlg.save_btn.click(); pump()
assert dlg.tabs.currentIndex() == 5 and dlg.status.property("level") == "bad" and R.cur.wol.mac == ""
dlg.wol_mac.setText("AA-BB-CC-DD-EE-FF"); dlg.wol_bcast.setText("192.168.1.255")
dlg.wol_btn.set_data("triangle"); dlg.wol_hold.setValue(1500)
dlg.grab().save(os.path.join(SHOTS, "gui_wol.png"))
sent.clear(); S.peer = ("192.168.1.50", 5005)
dlg.save_btn.click(); pump()
assert R.cur.wol == R.WolConfig(True, "aa:bb:cc:dd:ee:ff", "192.168.1.255", "triangle", 1500), R.cur.wol
from proto import T_SCFG
assert sum(1 for x in sent if x[0] == T_SCFG) == 3, "zapis wysyła ustawienia do nadajnika"
S.sender_wol = None
dlg.close(); win.open_settings(); pump()
assert "starsza" in dlg.wol_seen.text() and dlg.wol_mac.text() == "aa:bb:cc:dd:ee:ff"
dlg.close()
ok("Ustawienia -> Wake-on-LAN: wykryty MAC w podpowiedzi, walidacja adresów, zapis i scfg do nadajnika")
win.grab().save(os.path.join(SHOTS, "gui_main_extras.png"))
print("GUI EXTRAS OK")
