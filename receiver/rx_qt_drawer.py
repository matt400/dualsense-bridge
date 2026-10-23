"""Receiver: a drawer sliding in from the right over the window.

The common base of the Bluetooth (rx_qt_bt) and shortcuts (rx_qt_keys) panels.

The panel lies over the window (it does not change the layout): a dimmed background, a click on it or Esc closes it. TV mode: an
open panel keeps the focus inside itself (rx_qt_tv.PadNav asks focus_scope()), circle closes it. A subclass builds its content
in _build(lay) and may add _on_open() / _on_close() and first_focus().
"""
from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from rx_i18n import _
from rx_qt_style import px
from rx_qt_widgets import button, icon_label, label

ANIM_MS = 170
WIDTH = 380


class SideDrawer(QFrame):
    """A panel sliding in from the right over the `host` widget (the window's central widget), with a scrim behind it."""

    opened = Signal(bool)
    width_px = WIDTH

    def __init__(self, host: QWidget, icon: str, title: str) -> None:
        super().__init__(host)
        self.host = host
        self.setObjectName("drawer")
        self._open = False
        self.scrim = QWidget(host)                 # dimmed background - a click closes the drawer
        self.scrim.setObjectName("scrim")
        self.scrim.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scrim.hide()
        self.scrim.installEventFilter(self)
        host.installEventFilter(self)              # window resize -> keep the panel at the right edge
        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(ANIM_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self._anim_done)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(18), px(16), px(18), px(16))
        lay.setSpacing(px(10))
        head = QHBoxLayout()
        head.setSpacing(px(8))
        head.addWidget(icon_label(icon, px(22)))
        head.addWidget(label(title, "h2"), 1)
        self.close_btn: QPushButton = button(_("Zamknij"), small=True)
        self.close_btn.setToolTip(_("Zamknij panel (Esc, ○ na padzie)"))
        self.close_btn.clicked.connect(self.close_drawer)
        head.addWidget(self.close_btn)
        lay.addLayout(head)
        self._build(lay)
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close_drawer)
        self.hide()

    # ---------- for subclasses ----------
    def _build(self, lay: QVBoxLayout) -> None:
        """Fill the drawer body below the header (the layout `lay` already holds the title row)."""
        raise NotImplementedError

    def _on_open(self) -> None:
        """Hook: called when the drawer starts opening (refresh the content here)."""

    def _on_close(self) -> None:
        """Hook: called when the drawer starts closing."""

    def first_focus(self) -> QWidget:
        """The widget that gets the focus when the drawer opens (the Close button by default)."""
        return self.close_btn

    # ---------- opening ----------
    def rescale(self) -> None:
        """Recompute the width (the preferred width, but at most the host width minus a margin) and reposition."""
        self.setFixedWidth(min(px(self.width_px), max(px(280), self.host.width() - px(40))))
        self.place()

    def is_open(self) -> bool:
        """Whether the drawer is open (or opening)."""
        return self._open

    def toggle(self) -> None:
        """Open the drawer when closed, close it when open."""
        self.close_drawer() if self._open else self.open_drawer()

    def open_drawer(self) -> None:
        """Show the scrim and the drawer, run _on_open, slide the drawer in and focus first_focus(); emits opened(True)."""
        if self._open:
            return
        self._open = True
        self.rescale()
        self.scrim.setGeometry(self.host.rect())
        self.scrim.show()
        self.scrim.raise_()
        self.show()
        self.raise_()
        self._on_open()
        self._slide(True)
        self.first_focus().setFocus(Qt.FocusReason.TabFocusReason)
        self.opened.emit(True)

    def close_drawer(self) -> None:
        """Run _on_close, hide the scrim and slide the drawer out; emits opened(False). No-op when already closed."""
        if not self._open:
            return
        self._open = False
        self._on_close()
        self.scrim.hide()
        self._slide(False)
        self.opened.emit(False)

    def _slide(self, show: bool) -> None:
        """Animate the drawer's position between "outside" (just past the right edge) and "inside" (flush with it)."""
        y = self.host.rect().top()
        inside = QPoint(self.host.width() - self.width(), y)
        outside = QPoint(self.host.width(), y)
        self.anim.stop()
        self.anim.setStartValue(self.pos() if self.isVisible() and not show else outside)
        self.anim.setEndValue(inside if show else outside)
        self.anim.start()

    def _anim_done(self) -> None:
        """After the slide-out animation ends, hide the widget so it no longer takes part in layout or focus."""
        if not self._open:
            self.hide()

    def place(self) -> None:
        """Put the panel at the right edge, over the whole window height (the header too - it lies above everything)."""
        h = self.host.rect()
        self.resize(self.width(), h.height())
        if self._open and self.anim.state() != QPropertyAnimation.State.Running:
            self.move(h.width() - self.width(), h.top())
        self.scrim.setGeometry(h)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        """Re-layout when the host is resized; a mouse press on the scrim closes the drawer."""
        if obj is self.host and e.type() == QEvent.Type.Resize:
            self.rescale()
        elif obj is self.scrim and e.type() == QEvent.Type.MouseButtonPress:
            self.close_drawer()
            return True
        return super().eventFilter(obj, e)

    def focus_scope(self) -> QWidget | None:
        """For pad navigation: an open drawer keeps the focus inside itself."""
        return self if self._open else None
