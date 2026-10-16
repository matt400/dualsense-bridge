"""Receiver: wysuwany z prawej panel nad oknem - wspólna podstawa paneli Bluetooth (rx_qt_bt) i skrótów (rx_qt_keys).

Panel leży nad oknem (nie zmienia układu): przyciemnione tło, klik w nie albo Esc zamyka. Tryb TV: otwarty panel
zatrzymuje fokus w sobie (rx_qt_tv.PadNav pyta focus_scope()), ○ go zamyka. Podklasa buduje treść w _build(lay)
i może dopisać _on_open() / _on_close() oraz first_focus().
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
    """Panel wysuwany z prawej nad widżetem `host` (centralny widżet okna)."""

    opened = Signal(bool)
    width_px = WIDTH

    def __init__(self, host: QWidget, icon: str, title: str):
        super().__init__(host)
        self.host = host
        self.setObjectName("drawer")
        self._open = False
        self.scrim = QWidget(host)                 # przyciemnione tło - klik zamyka
        self.scrim.setObjectName("scrim")
        self.scrim.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scrim.hide()
        self.scrim.installEventFilter(self)
        host.installEventFilter(self)              # zmiana rozmiaru okna -> panel przy prawej krawędzi
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
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close_drawer,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.hide()

    # ---------- dla podklas ----------
    def _build(self, lay: QVBoxLayout) -> None:
        raise NotImplementedError

    def _on_open(self) -> None:
        pass

    def _on_close(self) -> None:
        pass

    def first_focus(self) -> QWidget:
        return self.close_btn

    # ---------- otwieranie ----------
    def rescale(self) -> None:
        self.setFixedWidth(min(px(self.width_px), max(px(280), self.host.width() - px(40))))
        self.place()

    def is_open(self) -> bool:
        return self._open

    def toggle(self) -> None:
        self.close_drawer() if self._open else self.open_drawer()

    def open_drawer(self) -> None:
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
        if not self._open:
            return
        self._open = False
        self._on_close()
        self.scrim.hide()
        self._slide(False)
        self.opened.emit(False)

    def _slide(self, show: bool) -> None:
        y = self.host.rect().top()
        inside = QPoint(self.host.width() - self.width(), y)
        outside = QPoint(self.host.width(), y)
        self.anim.stop()
        self.anim.setStartValue(self.pos() if self.isVisible() and not show else outside)
        self.anim.setEndValue(inside if show else outside)
        self.anim.start()

    def _anim_done(self) -> None:
        if not self._open:
            self.hide()

    def place(self) -> None:
        """Panel przy prawej krawędzi, na całą wysokość okna (pod nagłówkiem też - jest nad wszystkim)."""
        h = self.host.rect()
        self.resize(self.width(), h.height())
        if self._open and self.anim.state() != QPropertyAnimation.State.Running:
            self.move(h.width() - self.width(), h.top())
        self.scrim.setGeometry(h)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.host and e.type() == QEvent.Type.Resize:
            self.rescale()
        elif obj is self.scrim and e.type() == QEvent.Type.MouseButtonPress:
            self.close_drawer()
            return True
        return super().eventFilter(obj, e)

    def focus_scope(self) -> QWidget | None:
        """Dla nawigacji padem: otwarty panel zatrzymuje fokus w sobie."""
        return self if self._open else None
