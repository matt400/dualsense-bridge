"""Receiver: sterowanie oknem padem w trybie TV (pełny ekran) - z kanapy bez myszy.

Działa tylko, gdy okno receivera (albo jego okno Ustawień) jest aktywne i jest w pełnym ekranie; dowolny podłączony
pad, fizyczne przyciski (przed remapowaniem). PS trzymane = skróty PS, nawigacja nic nie robi.

  D-pad ↑ / ↓        poprzedni / następny element (fokus); na liście rozwiniętej - pozycja
  D-pad ← / →        suwak: wartość, lista: poprzednia / następna pozycja, zakładki: zakładka, rysunek pada: część;
                     gdzie indziej jak ↑ / ↓
  ✕                  kliknij (przycisk, pole wyboru), rozwiń listę, wybierz część pada
  ○                  wstecz: zwiń listę, zamknij panel (Bluetooth, skróty), zamknij Ustawienia, wyjdź z pełnego ekranu
  L1 / R1            poprzedni / następny pad (karta po lewej)
Otwarty panel Bluetooth (ikona w nagłówku: D-pad do niej, ✕) zatrzymuje fokus w sobie - D-pad chodzi tylko po
jego przyciskach (skan, wiersze urządzeń: Sparuj / Połącz / Rozłącz, Zapomnij), ○ go zamyka. W panelu skrótów
D-pad ↑ / ↓ przewija listę.
W widoku Test testowany pad nie steruje oknem (jego przyciski są testowane); przytrzymane ○ wychodzi z testu.
Przytrzymany kierunek powtarza krok (po REPEAT_DELAY_S co REPEAT_EVERY_S).
"""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSlider, QApplication, QComboBox, QScrollArea,
                               QTabBar)

import rx_buttons as B
import rx_state as S
from rx_config import C

REPEAT_DELAY_S, REPEAT_EVERY_S = 0.40, 0.12
DIRS = ("dpad_up", "dpad_down", "dpad_left", "dpad_right")
ARROW = {"dpad_up": Qt.Key.Key_Up, "dpad_down": Qt.Key.Key_Down, "dpad_left": Qt.Key.Key_Left, "dpad_right": Qt.Key.Key_Right}


def _key(widget, key):
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(widget, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))


def _focusable(w, root, scope=None):
    return (w.window() is root and w.isVisible() and w.isEnabled()
            and bool(w.focusPolicy() & Qt.FocusPolicy.TabFocus) and (scope is None or scope.isAncestorOf(w)))


def move_focus(root, forward, scope=None):
    """Następny / poprzedni element w łańcuchu fokusu okna `root` (jak Tab), przewijając panel, żeby był widoczny.
    scope: tylko elementy wewnątrz tego widżetu (otwarty panel Bluetooth)."""
    start = QApplication.focusWidget()
    if start is None or start.window() is not root:
        start = root
    w = start
    for _ in range(1000):
        w = w.nextInFocusChain() if forward else w.previousInFocusChain()
        if w is start:
            return None
        if _focusable(w, root, scope):
            w.setFocus(Qt.FocusReason.TabFocusReason)
            sa, p = None, w.parentWidget()
            while p is not None and sa is None:
                sa = p if isinstance(p, QScrollArea) else None
                p = p.parentWidget()
            if sa is not None:
                sa.ensureWidgetVisible(w, 20, 40)
            return w
    return None


class PadNav:
    """Wołane z MainWindow.live_tick (30 Hz): zbocza naciśnięć -> akcje w oknie."""

    def __init__(self, win):
        self.win = win
        self.prev = frozenset()
        self.held = {}                 # kierunek -> czas następnego powtórzenia

    def _pressed(self):
        out = set()
        testing = getattr(self.win, "view", None) == "test"
        for i in range(C.NUM_PADS):
            if testing and i == self.win.ed.slot:
                continue               # widok Test: testowany pad nie steruje oknem (wyjście: przytrzymane ○)
            if S.alive[i]:
                raw = S.states[i]["raw"]
                out |= B.pressed_inputs(*raw)
        return frozenset(out)

    def target(self):
        """Aktywne okno, którym sterujemy (główne albo Ustawienia) albo None."""
        win = self.win
        if not (win.fullscreen() and win.isVisible()):
            return None
        active = QApplication.activeWindow()
        return active if active is not None and active in (win, win.settings_dlg) else None

    def tick(self, now):
        root = self.target()
        pressed = self._pressed() if root is not None else frozenset()
        prev, self.prev = self.prev, pressed
        if "ps" in pressed:            # PS + przycisk to skróty (profil, czułość...) - nie nawigacja
            self.held.clear()
            return
        # prev zawiera też przyciski trzymane razem z PS: puszczenie PS przed D-padem / ✕ nie jest nowym naciśnięciem
        # (wcześniej prev był pusty przy PS, więc PS + → zmieniało profil i od razu przesuwało fokus w oknie)
        new = pressed - prev
        for d in list(self.held):
            if d not in pressed:
                del self.held[d]
        for name in new:
            if name in DIRS:
                self.held[name] = now + REPEAT_DELAY_S
            self.act(root, name)
        for d, t in list(self.held.items()):
            if now >= t:
                self.held[d] = now + REPEAT_EVERY_S
                self.act(root, d)

    def act(self, root, name):
        popup = QApplication.activePopupWidget()
        if popup is not None:                                   # rozwinięta lista: strzałki, ✕ wybór, ○ zwiń
            w = popup.focusWidget() or popup
            key = ARROW.get(name) or {"cross": Qt.Key.Key_Return, "circle": Qt.Key.Key_Escape}.get(name)
            if key is not None:
                _key(w, key)
            return
        fw = QApplication.focusWidget()
        fw = fw if fw is not None and fw.window() is root else None
        scope = self.scope(root)
        if scope is not None and fw is not None and not scope.isAncestorOf(fw):
            fw = None                                           # fokus poza panelem - ✕ nie klika pod nim
        if name in DIRS:
            self._direction(root, fw, name, scope)
        elif name == "cross":
            self._activate(fw)
        elif name == "circle":
            self._back(root, scope)
        elif name in ("l1", "r1") and root is self.win and scope is None:
            self._pad(-1 if name == "l1" else 1)

    def scope(self, root):
        """Otwarty panel nad oknem (Bluetooth, skróty), w którym zostaje fokus, albo None."""
        if root is not self.win:
            return None
        return next((s for d in getattr(self.win, "drawers", ()) if (s := d.focus_scope()) is not None), None)

    def _direction(self, root, fw, name, scope=None):
        side = name in ("dpad_left", "dpad_right")
        from rx_qt_pad import PadView
        if side and isinstance(fw, (QAbstractSlider, QTabBar, PadView)):
            _key(fw, ARROW[name])
            return
        if side and isinstance(fw, QComboBox) and fw.count():
            i = max(0, min(fw.count() - 1, fw.currentIndex() + (1 if name == "dpad_right" else -1)))
            if i != fw.currentIndex():
                fw.setCurrentIndex(i)
                fw.activated.emit(i)
            return
        if isinstance(fw, QAbstractItemView) and not side:
            _key(fw, ARROW[name])
            return
        if isinstance(fw, QScrollArea) and not side:        # lista skrótów: przewijanie, na brzegu - dalej fokus
            bar = fw.verticalScrollBar()
            if bar.value() != (bar.minimum() if name == "dpad_up" else bar.maximum()):
                bar.setValue(bar.value() + (bar.singleStep() * 4 if name == "dpad_down" else -bar.singleStep() * 4))
                return
        move_focus(root, name in ("dpad_down", "dpad_right"), scope)

    @staticmethod
    def _activate(fw):
        from rx_qt_pad import PadView
        if isinstance(fw, QAbstractButton):
            fw.click()
        elif isinstance(fw, QComboBox):
            fw.showPopup()
        elif isinstance(fw, PadView):
            _key(fw, Qt.Key.Key_Return)

    def _back(self, root, scope=None):
        if scope is not None:
            scope.close_drawer()                                # panel Bluetooth / skrótów
        elif root is not self.win:
            root.close()                                        # Ustawienia
        else:
            self.win.set_fullscreen(False)

    def _pad(self, step):
        win = self.win
        n = len(win.cards)
        live = [i for i in range(n) if S.alive[i] or S.want[i].is_set()] or list(range(n))
        cur = win.ed.slot
        later = [i for i in live if (i - cur) * step > 0]
        nxt = (min(later, key=lambda i: abs(i - cur)) if later else (live[0] if step > 0 else live[-1]))
        win.select_pad(nxt)
