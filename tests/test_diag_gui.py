"""Okno: pionowe menu boczne, zakładka Diagnostyka (kafelki, karty padów), czas połączenia na karcie pada."""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_diag, rx_qt
from proto import T_IDENT
from rx_console import Console, ConsoleConfig
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)

rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None
rx_diag.init(C.NUM_PADS)
feed(T_IDENT, 0, b"aa:aa:aa:aa:aa:01"); state(0)
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 760); win.show(); pump(120)
win.slow_tick(); pump()

# ---------- układ: menu z lewej, nadajnik w nagłówku z prawej ----------
x = lambda w: w.mapTo(win, w.rect().topLeft()).x()
y = lambda w: w.mapTo(win, w.rect().topLeft()).y()
assert win.side.isVisible() and x(win.side) < x(win.cards[0]), "menu boczne stoi na lewo od kolumny padów"
assert x(win.sender) > win.width() // 2 and y(win.sender) < y(win.side), "pigułka nadajnika jest po prawej, w nagłówku"
btns = [win.diag_btn, win.bt_btn, win.keys_btn, win.log_btn, win.fs_btn, win.settings_btn, win.update_btn, win.restart_btn, win.quit_btn]
ys = [y(b) for b in btns]
assert ys == sorted(ys) and len(set(ys)) == len(ys), f"ikony w jednej kolumnie, w logicznej kolejności: {ys}"
assert all(abs(x(b) - x(btns[0])) < 3 for b in btns), "wszystkie ikony w jednej pionowej kolumnie"
ok("menu boczne: kolejność i położenie ikon, nadajnik po prawej")

# ---------- przełączenie na diagnostykę ----------
assert win.main_stack.currentIndex() == 0
win.diag_btn.click(); pump()
assert win.main_stack.currentIndex() == 1 and win.diag_page.isVisible()
win.slow_tick(); pump()
assert win.diag_page.pad_cards[0].isVisible() and not win.diag_page.pad_cards[1].isVisible(), "karta tylko dla podłączonego pada"
assert not win.diag_page.empty.isVisible()
ok("ikona Diagnostyka przełącza widok, karty tylko dla podłączonych padów")

# ---------- kafelki ----------
now = time.monotonic()
S.last_pkt = now
S.info[0] = (6.0, 250.0, 0.0, now)
win.slow_tick(); win.diag_page.refresh(now); pump()
assert rx_diag.net.rtt.last() == 6.0 and "6" in win.diag_page.rtt.value.text(), win.diag_page.rtt.value.text()
S.alive[0] = False
win.slow_tick(); win.diag_page.refresh(now); pump()
assert win.diag_page.empty.isVisible() and not win.diag_page.pad_cards[0].isVisible(), "brak padów: komunikat zamiast kart"
S.alive[0] = True
ok("kafelki pokazują wartości z rx_diag, brak padów = komunikat")

# ---------- czas połączenia na karcie pada ----------
S.connected_at[0] = time.monotonic() - 125
win.slow_tick(); pump()
assert win.cards[0].since.text(), "karta pada pokazuje, od jak dawna pad jest połączony"
S.connected_at[0] = None
win.slow_tick(); pump()
assert not win.cards[0].since.text(), "brak czasu = pusta etykieta"
ok("karta pada: czas połączenia")

# ---------- klik w kartę wychodzi z diagnostyki ----------
win._card_clicked(0); pump()
assert win.main_stack.currentIndex() == 0 and not win.diag_btn.isChecked()
ok("klik w kartę pada wraca do edytora")

# ---------- przyciski potwierdzające w wąskim menu ----------
b = win.restart_btn
b.click(); pump()
assert b.property("armed"), "pierwszy klik uzbraja przycisk (czerwony)"
fired = []
b.confirmed.connect(lambda: fired.append(1))
b.click(); pump()
assert fired == [1], "drugi klik potwierdza"
assert not b.text(), "kompaktowy przycisk nie ma napisu - nie rozpycha menu"
ok("kompaktowy przycisk: uzbrojenie i potwierdzenie")

# ---------- tryb TV (skalowanie) ----------
w0 = win.side.width()
win.apply_scale(1.5); pump()
assert win.side.width() > w0 and win.side.isVisible(), "menu rośnie w trybie TV"
win.apply_scale(1.0); pump()
ok("skalowanie menu i diagnostyki")

print("DIAG GUI OK")
