"""Okno ustawień trybu konsoli: pola, lista urządzeń audio (odśwież, niedostępne), wybór przycisku na padzie, test, zapis + restart."""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_qt
from rx_console import Console, ConsoleConfig
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
BTN = lambda n: BIT[n]

devices = [("1", "Speakers (Realtek)"), ("2", "LG TV (NVIDIA High Definition Audio)")]
rx_audio.list_outputs = lambda: list(devices)
import rx_settings as _R
from dataclasses import replace as _replace
_R.apply(_replace(_R.cur, master=_replace(_R.cur.master, pad="00:00:00:00:00:ff")))   # bez auto-ON pierwszego pada
rx_console.ctl = ctl = Console(ConsoleConfig("share", 300, "LG TV (NVIDIA High Definition Audio)", "Dawne słuchawki", 45, "internal"))
tests = []
ctl.test = lambda cfg: tests.append(cfg)                       # bez wątku roboczego i bez poleceń systemowych

win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.show(); pump(100)
assert win.settings_dlg is None
win.open_settings(); pump(100)
dlg = win.settings_dlg
assert dlg is not None and dlg.isVisible()

# ---------- wartości z działającej konfiguracji ----------
assert dlg.toggle_button == "share" and "Share" in dlg.pick_name.text()
assert dlg.dbl.value() == 300 and dlg.timeout.value() == 45 and dlg.startup.currentData() == "internal"
tv_items = [dlg.tv.itemData(i) for i in range(dlg.tv.count())]
pc_items = [(dlg.pc.itemData(i), dlg.pc.itemText(i)) for i in range(dlg.pc.count())]
assert tv_items == ["", "Speakers (Realtek)", "LG TV (NVIDIA High Definition Audio)"], tv_items
assert dlg.tv.currentData() == "LG TV (NVIDIA High Definition Audio)"
assert ("Dawne słuchawki", "Dawne słuchawki (niedostępne)") in pc_items and dlg.pc.currentData() == "Dawne słuchawki"
ok("okno: wartości z konfiguracji, lista aktywnych urządzeń, zapisane nieobecne urządzenie zostaje jako niedostępne")

# ---------- odśwież: nowe urządzenie, wybór użytkownika zachowany ----------
dlg.pc.set_data("Speakers (Realtek)")
devices.append(("3", "Headphones (USB)")); dlg.refresh()
assert "Headphones (USB)" in [dlg.tv.itemData(i) for i in range(dlg.tv.count())]
assert dlg.pc.currentData() == "Speakers (Realtek)" and dlg.tv.currentData() == "LG TV (NVIDIA High Definition Audio)"
ok("odśwież: nowe urządzenie na listach, bieżący wybór zostaje")

# ---------- brak pycaw / błąd COM: komunikat, nie wyjątek ----------
def broken(): raise ImportError("No module named 'pycaw'")
rx_audio.list_outputs = broken; dlg.refresh()
assert "pycaw" in dlg.status.text() and dlg.status.property("level") == "bad"
assert dlg.tv.currentData() == "LG TV (NVIDIA High Definition Audio)", "wybrane urządzenia zostają mimo błędu"
rx_audio.list_outputs = lambda: list(devices); dlg.refresh(); assert dlg.status.text() == ""
ok("błąd odczytu urządzeń: komunikat w oknie, wybór nie ginie")

# ---------- wybór przycisku na padzie ----------
dlg.pick.click(); pump()
assert ctl._capture is not None and "Naciśnij" in dlg.pick.text() and dlg.pick.property("armed") is True
state(0, mask=BTN(316)); pump(80)                           # PS (przechwycone, a nie liczone jako kliknięcie)
assert dlg.toggle_button == "ps" and "PS" in dlg.pick_name.text() and ctl._capture is None and dlg.pick.property("armed") is False
assert not ctl._jobs.qsize(), "przechwycenie nie przełącza trybu"
state(0, mask=0)
dlg.pick.click(); dlg.pick.click(); pump()                  # drugi klik anuluje
assert ctl._capture is None and dlg.toggle_button == "ps" and dlg.pick.text() == "Wybierz na padzie"
dlg.pick.click(); pump(); dlg.hide(); pump()
assert ctl._capture is None, "zamknięcie okna anuluje przechwytywanie"
ok("przycisk pada: przechwycenie następnego, anulowanie klikiem i zamknięciem okna")

# ---------- Przetestuj: wartości z okna, jeszcze niezapisane ----------
dlg.present(); dlg.dbl.setValue(500); dlg.timeout.setValue(120); dlg.startup.set_data("extend")
dlg.test_btn.click(); pump()
assert tests and tests[-1].double_click_ms == 500 and tests[-1].timeout_s == 120 and tests[-1].toggle_button == "share"
assert tests[-1].audio_tv == "LG TV (NVIDIA High Definition Audio)" and not dlg.test_btn.isEnabled()
assert not os.path.exists(C.CONSOLE_PATH), "test nic nie zapisuje"
ok("Przetestuj: używa wartości z okna, nic nie zapisuje")

# ---------- Zapisz: console.json + restart ----------
S.restart = False; closed = []
QApplication.instance().quit = lambda: closed.append(True)  # bez pętli zdarzeń w teście
dlg.set_button("cross"); dlg.tv.set_data("Speakers (Realtek)"); dlg.pc.set_data("Headphones (USB)")
assert dlg.tv_res.currentData() == "2560x1440" and dlg.tv_scale.currentData() == 200, "domyślnie TV 1440p / 200 %"
dlg.tv_res.set_data("3840x2160"); dlg.tv_scale.set_data(150)
dlg.save_btn.click(); pump()
saved = json.load(open(C.CONSOLE_PATH))
assert saved == {"toggle_button": "cross", "double_click_ms": 500, "audio_tv": "Speakers (Realtek)",
                 "audio_pc": "Headphones (USB)", "timeout_s": 120, "startup_mode": "extend", "tv_width": 3840,
                 "tv_height": 2160, "tv_scale": 150, "auto_on_master": True,
                 "allowed_apps": list(rx_console.DEFAULT_APPS), "version": 3}, saved
assert S.restart is True and closed, "zapis prosi o restart (zamknięcie okna, S.restart)"
assert rx_console.load(C.CONSOLE_PATH) == ConsoleConfig("cross", 500, "Speakers (Realtek)", "Headphones (USB)", 120, "extend",
                                                        3840, 2160, 150, True)
S.restart = False
ok("Zapisz: console.json zapisany, restart zlecony")

# ---------- nie da się zapisać: komunikat, bez restartu ----------
dlg.present(); closed.clear()
orig = rx_console.save
def deny(cfg, path): raise PermissionError("brak dostępu")
rx_console.save = deny
dlg.save_btn.click(); pump()
rx_console.save = orig
assert "Nie udało się zapisać" in dlg.status.text() and S.restart is False and not closed
ok("błąd zapisu: komunikat w oknie, receiver się nie restartuje")
print("GUI USTAWIEŃ OK")
