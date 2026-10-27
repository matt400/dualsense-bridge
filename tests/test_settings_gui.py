"""Okno ustawień: zakładki Mysz i Master (zapis bez restartu), korona mastera na karcie pada, ikony przycisków."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_master, rx_qt, rx_qt_screens, rx_settings
from proto import T_IDENT
from rx_console import Console, ConsoleConfig
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
A, B = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02"

rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None
feed(T_IDENT, 0, A.encode()); feed(T_IDENT, 1, B.encode()); state(0); state(1)

win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 760); win.show(); pump(150)
os.makedirs(os.path.join(os.path.dirname(__file__)), exist_ok=True)
win.grab().save(os.path.join(os.path.dirname(__file__), "gui_master.png"))

# ---------- korona na karcie pada ----------
card0, card1 = win.cards
assert not rx_master.is_master(0) and card0.crown.isEnabled()
card1.crown.click(); pump()
assert rx_settings.cur.master.pad == B and rx_master.is_master(1)
assert json.load(open(C.SETTINGS_PATH))["master"]["pad"] == B, "korona zapisuje settings.json"
assert "zdjąć" in card1.crown.toolTip() and "zostaje" in card0.crown.toolTip()
card0.crown.click(); pump()
assert rx_settings.cur.master.pad == A
card0.crown.click(); pump()
assert rx_settings.cur.master.pad == "", "drugi klik na koronie mastera - bez mastera"
ok("korona: klik = master, klik na masterze = brak mastera, zapis do pliku")

# ---------- informacja o aktualizacji: pasek z opisem pod nagłówkiem, nie sam kolor ----------
import rx_update
import time as _t
assert [rx_qt.n_changes(n) for n in (1, 2, 5, 12, 22, 25)] == ["1 zmiana", "2 zmiany", "5 zmian", "12 zmian", "22 zmiany",
                                                               "25 zmian"]
win.slow_tick(); pump()
assert win.banner.isHidden(), "nic do zgłoszenia - bez paska"
rx_update.available = rx_update.Available(3, "Nowy tryb konsoli", "abc1234")
win.slow_tick(); pump()
assert win.banner.isVisible() and "Dostępna aktualizacja: 3 zmiany  -  Nowy tryb konsoli" in win.banner_text.text(), \
    win.banner_text.text()
assert not hasattr(win, "banner_btn"), "pasek tylko informuje - przycisk Aktualizuj jest w nagłówku"
S.sender_build = ("0000000", 1_700_000_000, 0, _t.monotonic()); S.last_pkt = _t.monotonic()
rx_update._version = ("1111111", 1_700_000_100, "x")
win.online = None; win.slow_tick(); pump()
assert "Nadajnik ma inną wersję (0000000) niż receiver (1111111)" in win.banner_text.text(), win.banner_text.text()
win.banner.grab().save(os.path.join(os.path.dirname(__file__), "gui_update_banner.png"))
rx_update.available = rx_update.Available(0)
S.sender_build = ("1111111", 1_700_000_100, 0, _t.monotonic())
win.online = None; win.slow_tick(); pump()
assert win.banner.isHidden(), "aktualna wersja na GitHubie i ta sama w nadajniku - pasek znika"
S.sender_build, S.last_pkt, rx_update._version = None, 0.0, None         # dalsze testy: nadajnik offline
win.online = None; win.slow_tick()
ok("aktualizacja: pasek 'Dostępna aktualizacja: N zmian - tytuł' i 'Nadajnik ma inną wersję', przycisk Aktualizuj")

# ---------- zakładki ustawień ----------
win.open_settings(); pump(100)
dlg = win.settings_dlg
assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["Tryb konsoli", "Mysz", "Master", "Lightbar", "Haptyka", "Wake-on-LAN", "Pady", "Okno", "Kopie", "Bezpieczeństwo"]
assert dlg.toggle_button == "r2" and "R2" in dlg.pick_name.text() and not dlg.pick_icon.pixmap().isNull()
assert dlg.auto_on.isChecked(), "domyślnie: tryb konsoli po połączeniu mastera"
assert not dlg.tabs.tabIcon(2).isNull(), "zakładki z ikonami"
dlg.tabs.setCurrentIndex(1); pump()
assert not dlg.test_btn.isVisible(), "Przetestuj tylko na zakładce trybu konsoli"
dlg.sens.slider.setValue(dlg.sens.slider.maximum()); dlg.freeze.set(250); dlg.rewind.set(0); dlg.drag.set(100)
dlg.tabs.setCurrentIndex(2); pump()
dlg.grab().save(os.path.join(os.path.dirname(__file__), "gui_settings_master.png"))
pads = [dlg.master_pad.itemData(i) for i in range(dlg.master_pad.count())]
assert pads == ["", A, B], pads
dlg.master_pad.set_data(B); dlg.master_pad.activated.emit(dlg.master_pad.currentIndex())   # wybór użytkownika
dlg.features["ps_game"].setChecked(True); dlg.features["console"].setChecked(False)
dlg.absent.set_data("nobody"); dlg.claim.setChecked(False)
dlg.actions["swap"].set_data("")                          # domyślnie też kwadrat - ten sam przycisk dla dwóch akcji okno odrzuca
dlg.actions["sleep"].set_data("square")
icons = [dlg.actions["sleep"].itemIcon(i).isNull() for i in range(1, dlg.actions["sleep"].count())]
assert not any(icons), "lista przycisków akcji to ikony"
assert dlg.actions["sleep"].toolTip() == "Kwadrat", dlg.actions["sleep"].toolTip()

S.restart = False; closed = []
QApplication.instance().quit = lambda: closed.append(True)
dlg.save_btn.click(); pump()
m, mo = rx_settings.cur.master, rx_settings.cur.mouse
assert m.pad == B and m.when_absent == "nobody" and not m.claim_hold and "ps_game" in m.only_master and "console" not in m.only_master
assert m.actions["sleep"] == "square" and mo.sens == 5.0 and mo.click_freeze_ms == 250 and mo.click_rewind_ms == 0 and mo.drag_threshold == 100
assert M.sens == [5.0] * C.NUM_PADS, "czułość działa od razu"
assert S.restart is False and not closed, "mysz / master bez restartu (tryb konsoli bez zmian)"
assert "Zapisano" in dlg.status.text()
saved = json.load(open(C.SETTINGS_PATH))
assert saved["mouse"]["click_freeze_ms"] == 250 and saved["master"]["only_master"] == [k for k in rx_settings.MASTER_FEATURES if k in m.only_master]
ok("zapis myszy i mastera: działa od razu, bez restartu, settings.json")

# ---------- Ustawienia -> Pady: backend, ścieżka DLL, stała kolejność; restart jako administrator ----------
import rx_ds4, rx_hm
dlg.tabs.setCurrentIndex(6); pump()
assert dlg.vpad_backend.currentData() == "auto" and dlg.vpad_fixed.isChecked() and dlg.vpad_dll.text() == ""
assert dlg.vpad_dll.placeholderText() == "automatycznie" and dlg.vpad_auto.isChecked()
real_admin = rx_hm.is_admin
rx_hm.is_admin = lambda: False
dlg.vpad_backend.set_data("hidmaestro"); dlg._vpad_state(); pump()
assert dlg.vpad_admin.isVisible() and "administratora" in dlg.vpad_now.text(), dlg.vpad_now.text()
dlg.grab().save(os.path.join(os.path.dirname(__file__), "gui_settings_pads.png"))
dlg.vpad_backend.set_data("vigem"); dlg._vpad_state(); pump()
assert not dlg.vpad_admin.isVisible(), "ViGEm nie potrzebuje administratora"
asked = []
win.restart = lambda admin=False: asked.append(admin)
dlg.elevate.disconnect(); dlg.elevate.connect(lambda: win.restart(admin=True))
dlg.vpad_backend.set_data("hidmaestro"); dlg._vpad_state(); dlg.vpad_admin.click(); pump()
assert asked == [True], asked
del win.restart
rx_hm.is_admin = real_admin
dlg.vpad_fixed.setChecked(False); dlg.vpad_dll.setText("  D:\\HM  "); dlg.vpad_auto.setChecked(False)
dlg.save_btn.click(); pump()
v = rx_settings.cur.vpad
assert (v.backend, v.fixed_slots, v.hm_dll) == ("hidmaestro", False, "D:\\HM"), v
assert json.load(open(C.SETTINGS_PATH))["vpad"] == {"backend": "hidmaestro", "fixed_slots": False, "hm_dll": "D:\\HM",
                                                     "hm_auto": False}
assert S.restart is False, "pady działają bez restartu"
rx_settings.apply(rx_settings.defaults())
ok("Ustawienia -> Pady: backend / DLL / kolejność zapisane od razu, restart jako administrator, gdy go brakuje")

# ---------- ten sam przycisk w dwóch akcjach ----------
dlg.actions["pause"].set_data("square"); dlg.tabs.setCurrentIndex(0)
dlg.save_btn.click(); pump()
assert dlg.tabs.currentIndex() == 2 and dlg.status.property("level") == "bad"
assert rx_settings.cur.master.actions["pause"] != "square", "błędne ustawienia nie zostały zastosowane"
dlg.actions["pause"].set_data("options")
ok("dwie akcje na jednym przycisku: komunikat, nic nie zapisane")

# ---------- zmiana trybu konsoli dalej restartuje ----------
dlg.set_button("cross"); dlg.save_btn.click(); pump()
assert S.restart is True and closed and json.load(open(C.CONSOLE_PATH))["toggle_button"] == "cross"
S.restart = False
ok("zmiana trybu konsoli: zapis + restart jak wcześniej")

# ---------- ikony w remapowaniu ----------
win.on_part("r2"); pump()
assert win.part_page.title.text() == "R2 (spust)" and not win.part_page.title_icon.pixmap().isNull()
targets = [win.part_page.dst.itemData(i) for i in range(win.part_page.dst.count())]
assert "mute" not in targets and "ps" in targets and len(targets) == 18, targets
assert win.part_page.dst.itemText(0) == "Krzyżyk (X)" and not win.part_page.dst.itemIcon(0).isNull(), "cele: ikona + nazwa"
win.grab().save(os.path.join(os.path.dirname(__file__), "gui_master2.png"))
ok("remap: tytuł z ikoną, cele z ikoną i nazwą")
# ---------- ekran okna ----------
dlg.present(); pump()
area = dlg.screen().availableGeometry()
assert area.contains(dlg.frameGeometry()) or dlg.size() == dlg.size().expandedTo(dlg.minimumSizeHint()), \
    ("okno ustawień w obszarze roboczym (nie pod paskiem zadań)", area, dlg.frameGeometry())
assert area.contains(dlg.save_btn.mapToGlobal(dlg.save_btn.rect().bottomRight())), "przycisk Zapisz widoczny"
ok("okno ustawień mieści się na ekranie, Zapisz widoczny")
scr = [dlg.screen_box.itemData(i) for i in range(dlg.screen_box.count())]
names = [k for k, _ in rx_qt_screens.screens()]
assert scr == ["", *names], (scr, names)
dlg.screen_box.set_data(names[0]); dlg.tabs.setCurrentIndex(7)
dlg.save_btn.click(); pump()
assert win.screen_choice() == names[0] and win.settings.value("screen") == names[0], "wybrany ekran zapisany"
win.move(-5000, -5000); win.place_on_screen()
assert QApplication.screens()[0].availableGeometry().contains(win.frameGeometry().center()), "okno przeniesione na wybrany ekran"
win.set_screen_choice("NIE-MA-TAKIEGO"); dlg.hide(); dlg.present(); pump()
assert dlg.screen_box.currentData() == "NIE-MA-TAKIEGO" and "niepodłączony" in dlg.screen_box.currentText()
win.move(-5000, -5000)
assert win.place_on_screen() is win.screen(), "brak wybranego ekranu -> ekran, na którym okno jest teraz"
area = win.screen().availableGeometry()
g = win.frameGeometry()
assert (abs(g.center().x() - area.center().x()) <= 1 if g.width() <= area.width() else g.left() == area.left()), \
    ("okno na środku ekranu (większe niż ekran: od lewej krawędzi)", g, area)
win.set_screen_choice("")
# tryb okna: cały ekran roboczy (bez natywnej maksymalizacji) albo wybrana rozdzielczość
from PySide6.QtCore import QSize
area = win.screen().availableGeometry()
assert win.window_mode() == "maximized"
win.place_on_screen()
g = win.frameGeometry()
assert not win.isMaximized() and g.topLeft() == area.topLeft() and g.width() >= area.width() and g.height() >= area.height(), \
    ("maximized = cały obszar roboczy (nie mniej niż minimum układu)", g, area)
want = QSize(max(rx_qt_screens.MIN_WINDOW.width(), area.width() - 200), max(rx_qt_screens.MIN_WINDOW.height(), area.height() - 200))
win.set_window_mode("size", want); pump(50)
assert win.window_mode() == "size" and win.settings.value("window_size") == f"{want.width()}x{want.height()}"
if want.width() <= area.width() - rx_qt_screens.FIT_MARGIN and want.height() <= area.height() - rx_qt_screens.FIT_MARGIN:
    assert win.size() == want.expandedTo(win.minimumSize()), ("wybrana rozdzielczość", win.size(), want)
assert rx_qt_screens.parse_size("1280x720") == QSize(1280, 720) and rx_qt_screens.parse_size("śmieci") is None
assert rx_qt_screens.parse_size("100x100") is None, "za mała rozdzielczość odrzucona"
dlg.hide(); dlg.present(); pump()
assert dlg.win_mode.currentData() == "size" and dlg.win_res.isEnabled() and dlg.win_res.currentData() == f"{want.width()}x{want.height()}"
dlg.win_mode.set_data("maximized"); dlg._window_state()
assert not dlg.win_res.isEnabled(), "lista rozdzielczości tylko dla trybu z wybraną rozdzielczością"
dlg.save_btn.click(); pump(100)
assert win.window_mode() == "maximized" and win.frameGeometry().topLeft() == area.topLeft(), "Ustawienia -> Okno -> cały ekran roboczy"
win.set_window_mode("size", want)
ok("tryb okna: cały ekran roboczy / wybrana rozdzielczość, zapis w ustawieniach")
# ekran mniejszy niż okno (TV 1280x720 przy 200 %): okno zmniejszone i na środku, potem wraca do swojego rozmiaru
pref = win._pref
def fits():
    g, m = win.frameGeometry(), win.minimumSizeHint().expandedTo(win.minimumSize())
    lim = area.size() - QSize(rx_qt_screens.FIT_MARGIN, rx_qt_screens.FIT_MARGIN)
    assert g.left() >= area.left() and g.top() >= area.top(), (area, g)          # przyciski zawsze widoczne
    assert win.width() <= max(lim.width(), m.width()) and win.height() <= max(lim.height(), m.height()), (area, g, m)
win._pref = area.size() + QSize(400, 400)
win.place_on_screen(); fits()
small = win.minimumSizeHint().expandedTo(QSize(300, 300))
win._pref = small; win.place_on_screen()
assert win.size() == small.boundedTo(area.size()) or win.size() == small, (win.size(), small)
win._pref = area.size() + QSize(400, 400)
win._screens_settled(); fits()
# zmiana ekranów w trakcie gry: bez przestawiania okna, ale po wyjściu z gry okno wraca na środek (następne odświeżenie)
import rx_games, rx_state as _S
_S.game = "forzahorizon5.exe"; win.move(-5000, -5000)
win._screens_settled()
assert win._place_pending and win.frameGeometry().left() == -5000, "w grze okno zostaje (gra zmieniła rozdzielczość)"
win.slow_tick()
assert win._place_pending, "gra dalej na pierwszym planie - czeka"
_S.game = "explorer.exe"; win.slow_tick()
assert not win._place_pending, "po wyjściu z gry okno ułożone od nowa"
fits()
_S.game = None
win._pref = pref
ok("ekran okna: lista ekranów, zapis, przeniesienie, brak ekranu, zawsze środek i mieści się")

# ---------- pełny ekran, język, dozwolone programy, test wibracji ----------
import os
is_ci = os.getenv("CI") == "true"

assert not win.fullscreen()
win.toggle_fullscreen(); pump(100)

# W trybie CI (GitHub Actions) pomijamy fizyczne isFullScreen(), bo Xvfb często tego nie obsługuje poprawnie
if is_ci:
    assert win.fullscreen() and win.settings.value("fullscreen") in (True, "true")
else:
    assert win.fullscreen() and win.isFullScreen() and win.settings.value("fullscreen") in (True, "true")

win.fs_btn.click(); pump(100)

if is_ci:
    assert not win.fullscreen(), "F11 / przycisk: z powrotem okno"
else:
    assert not win.fullscreen() and not win.isFullScreen(), "F11 / przycisk: z powrotem okno"

dlg.hide(); dlg.present(); pump()
assert dlg.win_mode.currentData() == "size" and dlg.lang.currentData() == "pl"
dlg.win_mode.set_data("full"); dlg.save_btn.click(); pump(100)
assert win.fullscreen() and (is_ci or win.isFullScreen()), "Ustawienia -> Okno -> Pełny ekran"   # CI: jak wyżej
win.set_fullscreen(False); pump(100)
assert "steam.exe" in dlg.apps.toPlainText().split()
dlg.hide(); dlg.present(); pump()
dlg.apps.setPlainText("Steam.exe\n  Moja Gra.exe \n\nsteam.exe")
S.restart = False; closed.clear()
dlg.save_btn.click(); pump()
assert S.restart is True and json.load(open(C.CONSOLE_PATH))["allowed_apps"] == ["steam.exe", "moja gra.exe"], \
    "lista programów: małe litery, bez pustych i powtórek, nazwy ze spacją; zmiana = restart"
S.restart = False; closed.clear()
rx_console.ctl.cfg = rx_console.load(C.CONSOLE_PATH)       # jak po restarcie
dlg.hide(); dlg.present(); pump()
dlg.lang.set_data("en"); dlg.save_btn.click(); pump()
assert S.restart is True and json.load(open(C.SETTINGS_PATH))["ui"]["lang"] == "en", "zmiana języka = zapis + restart"
S.restart = False; closed.clear()
import rx_i18n
rx_i18n.set_lang("en"); w2 = rx_qt.create_window(os.path.join(TMP, "ui2.ini")); w2.show(); pump(100)
from PySide6.QtWidgets import QPushButton as _QPB
texts = {b.text() for b in w2.findChildren(_QPB)}
tips = {b.toolTip().split(" - ")[0] for b in w2.findChildren(_QPB) if b.property("iconic")}
assert {"Save", "Revert"} <= texts and {"Settings", "Quit", "Update", "Restart"} <= tips, (texts, tips)
assert w2.tabs.tabText(0) == "Selected" and w2.cards[0].off.toolTip().startswith("Power off")
w2.hide(); rx_i18n.set_lang("pl")
pulses = []
F.pulse = lambda i, kind, force=False: pulses.append((i, kind, force))
card0.rumble.click(); pump()
assert pulses == [(0, "test", True)] and card0.rumble.toolTip().startswith("Test wibracji"), pulses
ok("pełny ekran (F11, przycisk, ustawienia), język (restart, okno po angielsku), lista programów, test wibracji")

# ---------- przycisk Restart w bocznym menu i ikona okna ----------
from PySide6.QtWidgets import QPushButton
btn = next(b for b in win.findChildren(QPushButton) if b.toolTip().startswith("Restart"))
S.restart = False; closed.clear()
btn.click(); pump()
assert S.restart is False and btn.compact and btn.text() == "" and btn.property("armed") is True, "pierwszy klik tylko uzbraja (czerwony przycisk, pytanie w dymku)"
btn.click(); pump()
assert S.restart is True and closed, "drugi klik: restart receivera"
S.restart = False
assert not QApplication.instance().windowIcon().isNull() and len(win.windowIcon().availableSizes()) >= 4, "ikona pada w kilku rozmiarach"
ok("Restart: dwa kliknięcia, restart receivera; ikona aplikacji ustawiona")

# ---------- Aktualizuj: git pull w tle, komunikat, restart po pobraniu ----------
import rx_update
rx_qt.UPDATE_RESTART_MS = 50
results = iter([rx_update.Result(True, False, "Masz najnowszą wersję"),
                rx_update.Result(True, True, "Zaktualizowano (2 zmian): aaaaaaa → bbbbbbb", "a", "b")])
rx_update.update = lambda **_: (time.sleep(0.1), next(results))[1]
import time
btn = win.update_btn
assert btn.text() == "" and btn.toolTip().startswith("Aktualizuj") and not btn.icon().isNull(), "sama ikona"
S.restart = False; closed.clear()
btn.click(); btn.click(); pump(20)
assert not btn.isEnabled() and btn.text() == "" and btn.label_text.startswith("Aktualizuję…") and btn.label_text.endswith("%"), "w trakcie: zablokowany, procenty w dymku (wąskie menu)"
assert win.update_ring.running() and not btn.icon().isNull(), "obok napisu kręci się kółko"
win.update_progress.emit(60); pump(); assert btn.label_text == "Aktualizuję… 60%" == btn.toolTip(), btn.label_text
win.update_progress.emit(30); pump(); assert btn.label_text == "Aktualizuję… 60%", "procenty nie cofają się"
pump(300)
assert btn.isEnabled() and "najnowszą" in win.status.text() and S.restart is False, "brak zmian - bez restartu"
assert btn.label_text == "" and not win.update_ring.running(), "po aktualizacji znów sama ikona"
win.update_progress.emit(50); pump(); assert btn.label_text == "", "spóźniony sygnał po zakończeniu jest ignorowany"
btn.click(); btn.click(); pump(500)
assert S.restart is True and closed and "Zaktualizowano" in btn.toolTip(), "nowa wersja -> restart receivera"
S.restart = False
ok("Aktualizuj: potwierdzenie, git pull w tle, komunikat, restart tylko po pobraniu nowej wersji")

# ---------- wersje i aktualizacja nadajnika ----------
import rx_net
from proto import BODY, CMD_UPDATE, T_BUILD, T_CMD, UPD_DONE
assert win.version_lbl.text().startswith("wersja ") and win.version_lbl.toolTip()
rx_update._version = ("1111111", 1791700000, "Receiver")
def build(h, st):
    rx_net.on_build(rx_net.codec.pack(T_BUILD, 255, h, 1791600000, st), time.monotonic())
S.last_pkt = time.monotonic(); build(b"2222222", 0); win.slow_tick(); pump()
assert "2222222" in win.sender.text() and win.sender.property("level") == "warn" and "Aktualizuj" in win.sender.toolTip(), \
    "inna wersja nadajnika niż receivera -> żółty, podpowiedź"
build(b"1111111", 0); win.slow_tick(); pump()
assert win.sender.property("level") == "ok", "ta sama wersja - zielony"
rx_update.update = lambda **_: rx_update.Result(True, True, "Zaktualizowano (1 zmian): 1111111 → 3333333")
rx_qt.UPDATE_RESTART_MS = 50
sent.clear(); S.restart = False; closed.clear(); btn.setEnabled(True)   # po poprzedniej aktualizacji czeka na restart
btn.click(); btn.click(); pump(150)
cmds = [p for p in sent if p[0] == T_CMD]
assert cmds and cmds[0][1] == 255 and BODY[T_CMD].unpack_from(cmds[0][3], 4)[0] == CMD_UPDATE, "polecenie aktualizacji do nadajnika"
assert S.restart is False, "receiver czeka na wynik nadajnika"
S.last_pkt = time.monotonic(); build(b"1111111", UPD_DONE); pump(500)
assert "nadajnik: zaktualizowany" in btn.toolTip() and S.restart is True, btn.toolTip()
S.restart = False
ok("wersja w nagłówku, wersja nadajnika (inna = żółty), Aktualizuj: polecenie do nadajnika, czekanie na jego wynik")

# nadajnik nie kończy: odliczanie w oknie, potem restart bez niego (jego wersję widać potem przy "Nadajnik online")
rx_qt.UPDATE_WAIT_S = 1.0
S.last_pkt = time.monotonic(); build(b"1111111", 0)
sent.clear(); S.restart = False; closed.clear(); btn.setEnabled(True)
btn.click(); btn.click(); pump(300)
assert S.restart is False and "Czekam na nadajnik" in win.status.text(), win.status.text()
pump(1500)
assert S.restart is True and "jeszcze pobiera" in btn.toolTip(), btn.toolTip()
S.restart = False; rx_qt.UPDATE_WAIT_S = 20.0

# "brak odpowiedzi": etap pip dostaje własny limit, etap git - nie
def slow_update(stage):
    def run(**_):
        win.update_progress.emit(stage); time.sleep(0.7)
        return rx_update.Result(True, False, "Masz najnowszą wersję")
    return run
rx_qt.UPDATE_STUCK_S = 0.2
S.sender_build = None; S.last_pkt = 0.0      # bez nadajnika: wynik receivera od razu kończy aktualizację
S.restart = False; btn.setEnabled(True)
rx_update.update = slow_update(rx_update.PCT_BACKED_UP)
btn.click(); btn.click(); pump(450)
assert not btn.isEnabled() and "nie odpowiada" not in win.status.text(), "pip trwa - to nie zawieszony git"
pump(700)
rx_update.update = slow_update(rx_update.PCT_PULL)
btn.click(); btn.click(); pump(450)
assert btn.isEnabled() and "nie odpowiada (git)" in win.status.text(), win.status.text()
pump(700)
rx_qt.UPDATE_STUCK_S = 150.0
ok("Aktualizuj: odliczanie czekania na nadajnik; zawieszenie liczone od ostatniego etapu, pip ma własny limit")
offs = []
rx_console.power_off = lambda why: offs.append(why) or True
rx_qt.session_end()
assert offs == ["wyłączanie Windows"], offs
import ctypes.wintypes
msg = ctypes.wintypes.MSG(message=rx_qt.WM_QUERYENDSESSION)
assert rx_qt.SessionEndFilter().nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)
assert offs == ["wyłączanie Windows"] * 2, "WM_QUERYENDSESSION -> tryb konsoli OFF przed wyłączeniem Windows"
ok("koniec sesji Windows wyłącza tryb konsoli")
# ---------- Ustawienia -> Kopie: lista, kopia teraz, przywrócenie (2 kliknięcia) -> restart ----------
import rx_backup, rx_qt_console
rx_qt_console.RESTORE_RESTART_MS = 50
with open(C.SETTINGS_PATH, "w") as f: f.write('{"mouse": {"sens": 2.0}}')
dlg = win.settings_dlg; dlg.hide(); dlg.present(); pump()
dlg.tabs.setCurrentIndex(8); pump()
n0 = len(rx_backup.list_backups())
dlg._backup_now(); pump()
rows = [dlg.backup_rows.itemAt(k).widget() for k in range(dlg.backup_rows.count())]
assert len(rx_backup.list_backups()) == n0 + 1 and len(rows) == min(n0 + 1, rx_backup.KEEP) and "✓" in dlg.status.text()
with open(C.SETTINGS_PATH, "w") as f: f.write('{"mouse": {"sens": 3.0}}')
restore = next(b for b in rows[0].findChildren(QPushButton))
S.restart = False; closed.clear()
restore.click(); pump(); assert S.restart is False and json.load(open(C.SETTINGS_PATH))["mouse"]["sens"] == 3.0
restore.click(); pump(300)
assert json.load(open(C.SETTINGS_PATH))["mouse"]["sens"] == 2.0 and S.restart is True, "przywrócenie -> restart"
assert rx_backup.list_backups()[0].reason == "restore" and not dlg.save_btn.isEnabled(), "Zapisz zablokowany do restartu"
S.restart = False
ok("Ustawienia -> Kopie: kopia teraz, przywrócenie po drugim kliknięciu, kopia bieżących, restart")
# ---------- połączenie pada -> okno na wierzch, ale nie w grze ----------
import rx_winapi
fronts, real_front, real_fg = [], win.bring_to_front, rx_winapi.foreground_window
win.bring_to_front = lambda: fronts.append(1)
def set_want(states):
    for w, on in zip(S.want, states):
        w.set() if on else w.clear()
for fg, alive, want in (((1, "eldenring.exe"), [False, False], 0), ((1, "eldenring.exe"), [True, False], 0),
                        ((1, "explorer.exe"), [True, False], 0), ((1, "steam.exe"), [True, True], 1),
                        ((1, None), [False, False], 1), ((1, None), [True, False], 1),
                        ((0, None), [False, False], 1), ((0, None), [True, False], 2)):
    rx_winapi.foreground_window = lambda fg=fg: fg
    set_want(alive); win._pads_connected()
    assert len(fronts) == want, (fg, alive, fronts)
# chwilowa cisza (stale_s): alive gaśnie i wraca, ale pad dalej połączony (want) - okno zostaje, gdzie było
rx_winapi.foreground_window = lambda: (1, "steam.exe")
set_want([True, False]); win._pads_connected(); n = len(fronts)
S.alive[:] = [False, False]; win._pads_connected(); S.alive[:] = [True, False]; win._pads_connected()
assert len(fronts) == n, "przerwa w sygnale to nie nowe połączenie pada"
win.bring_to_front, rx_winapi.foreground_window = real_front, real_fg
set_want([False] * len(S.want)); S.alive[:] = [False] * len(S.alive); win._pads_connected()
ok("połączenie pada: okno na wierzch na pulpicie / w Steam, w grze i przy nieznanym programie - nie; nie po przerwie")
print("GUI USTAWIEŃ (MYSZ / MASTER) OK")
