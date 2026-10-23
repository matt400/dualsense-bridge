"""Receiver: controlling the window with a pad in TV mode (full screen) - from the couch, no mouse.

Works only when the receiver window (or its Settings window) is active and in full screen; any connected
pad, physical buttons (before remapping). While PS is held these are PS shortcuts and navigation does nothing.

  D-pad up / down    previous / next element (focus); in an expanded list - the item
  D-pad left / right slider: the value, list: the previous / next item, tabs: the tab, pad drawing: the part;
                     elsewhere the same as up / down
  cross              click (button, check box), expand a list, choose a pad part
  circle             back: collapse a list, close a drawer (Bluetooth, shortcuts), close Settings, leave full screen
  L1 / R1            previous / next pad (the card on the left)
An open Bluetooth drawer (the header icon: D-pad to it, cross) keeps the focus inside itself - the D-pad walks only over
its buttons (scan, device rows: Pair / Connect / Disconnect, Forget), circle closes it. In the shortcuts drawer
D-pad up / down scrolls the list.
In the Test view the tested pad does not control the window (its buttons are being tested); holding circle leaves the test.
A held direction repeats the step (after REPEAT_DELAY_S every REPEAT_EVERY_S).
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
    """Send a key press and release of `key` to the widget."""
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(widget, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))


def _focusable(w, root, scope=None):
    """Whether widget w can take focus by Tab in window `root` (and, optionally, lies inside `scope`)."""
    return (w.window() is root and w.isVisible() and w.isEnabled()
            and bool(w.focusPolicy() & Qt.FocusPolicy.TabFocus) and (scope is None or scope.isAncestorOf(w)))


def move_focus(root, forward, scope=None):
    """Next / previous element in the focus chain of window `root` (like Tab), scrolling the panel so that it is visible.
    scope: only elements inside this widget (an open Bluetooth drawer)."""
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
    """Called from MainWindow.live_tick (30 Hz): press edges -> actions in the window."""

    def __init__(self, win):
        self.win = win
        self.prev = frozenset()
        self.held = {}                 # direction -> time of the next repeat

    def _pressed(self):
        """The set of pad inputs pressed now on all connected pads (raw, before remapping); the tested pad is skipped in the Test view."""
        out = set()
        testing = getattr(self.win, "view", None) == "test"
        for i in range(C.NUM_PADS):
            if testing and i == self.win.ed.slot:
                continue               # Test view: the tested pad does not control the window (exit: hold circle)
            if S.alive[i]:
                raw = S.states[i]["raw"]
                out |= B.pressed_inputs(*raw)
        return frozenset(out)

    def target(self):
        """The active window being controlled (the main one or Settings), or None."""
        win = self.win
        if not (win.fullscreen() and win.isVisible()):
            return None
        active = QApplication.activeWindow()
        return active if active is not None and active in (win, win.settings_dlg) else None

    def tick(self, now):
        """One navigation step: compute the new press edges against the previous state, drop released directions from the
        repeat table, act on every new press and repeat the held directions when their time comes."""
        root = self.target()
        pressed = self._pressed() if root is not None else frozenset()
        prev, self.prev = self.prev, pressed
        if "ps" in pressed:            # PS + button is a shortcut (profile, sensitivity...) - not navigation
            self.held.clear()
            return
        # prev also contains buttons held together with PS: releasing PS before the D-pad / cross is not a new press
        # (previously prev was empty while PS was held, so PS + right changed the profile and at once moved the focus in the window)
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
        """Perform one pad input as a UI action: with an open popup it is translated to keys; otherwise D-pad moves the focus
        (or changes a slider / combo / tab), cross activates, circle goes back and L1 / R1 switch the pad."""
        popup = QApplication.activePopupWidget()
        if popup is not None:                                   # an expanded list: arrows, cross selects, circle collapses
            w = popup.focusWidget() or popup
            key = ARROW.get(name) or {"cross": Qt.Key.Key_Return, "circle": Qt.Key.Key_Escape}.get(name)
            if key is not None:
                _key(w, key)
            return
        fw = QApplication.focusWidget()
        fw = fw if fw is not None and fw.window() is root else None
        scope = self.scope(root)
        if scope is not None and fw is not None and not scope.isAncestorOf(fw):
            fw = None                                           # focus is outside the drawer - cross does not click underneath it
        if name in DIRS:
            self._direction(root, fw, name, scope)
        elif name == "cross":
            self._activate(fw)
        elif name == "circle":
            self._back(root, scope)
        elif name in ("l1", "r1") and root is self.win and scope is None:
            self._pad(-1 if name == "l1" else 1)

    def scope(self, root):
        """The open drawer over the window (Bluetooth, shortcuts) that keeps the focus, or None."""
        if root is not self.win:
            return None
        return next((s for d in getattr(self.win, "drawers", ()) if (s := d.focus_scope()) is not None), None)

    def _direction(self, root, fw, name, scope=None):
        """A D-pad direction: left / right adjust sliders, tabs, the pad drawing and combo boxes; up / down scroll item views and
        shortcut lists; everything else (and the edges) moves the focus in the focus chain."""
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
        if isinstance(fw, QScrollArea) and not side:        # shortcut list: scrolling, at the edge the focus moves on
            bar = fw.verticalScrollBar()
            if bar.value() != (bar.minimum() if name == "dpad_up" else bar.maximum()):
                bar.setValue(bar.value() + (bar.singleStep() * 4 if name == "dpad_down" else -bar.singleStep() * 4))
                return
        move_focus(root, name in ("dpad_down", "dpad_right"), scope)

    @staticmethod
    def _activate(fw):
        """Cross: click a button, open a combo box or activate the pad drawing."""
        from rx_qt_pad import PadView
        if isinstance(fw, QAbstractButton):
            fw.click()
        elif isinstance(fw, QComboBox):
            fw.showPopup()
        elif isinstance(fw, PadView):
            _key(fw, Qt.Key.Key_Return)

    def _back(self, root, scope=None):
        """Circle: close the open drawer, else close Settings, else leave full screen."""
        if scope is not None:
            scope.close_drawer()                                # Bluetooth / shortcuts drawer
        elif root is not self.win:
            root.close()                                        # Settings
        else:
            self.win.set_fullscreen(False)

    def _pad(self, step):
        """Select the previous / next pad that is connected or wanted (wraps to the first / last when there is none further)."""
        win = self.win
        n = len(win.cards)
        live = [i for i in range(n) if S.alive[i] or S.want[i].is_set()] or list(range(n))
        cur = win.ed.slot
        later = [i for i in live if (i - cur) * step > 0]
        nxt = (min(later, key=lambda i: abs(i - cur)) if later else (live[0] if step > 0 else live[-1]))
        win.select_pad(nxt)
