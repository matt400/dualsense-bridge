"""Receiver: the "Pad shortcuts" panel - a drawer sliding in from the right with every pad shortcut (icon in the window header).

The content (rx_shortcuts.groups) is rebuilt on every open, so it always matches the current settings:
the console-mode button, the master actions, the long-PS action. A crown next to a shortcut = master pad only.
TV mode: D-pad up / down scrolls the list (rx_qt_tv: a focused QScrollArea), up at the top of the list moves to "Close",
circle closes the panel.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

import rx_settings
import rx_shortcuts as SC
from rx_i18n import _
from rx_qt_drawer import SideDrawer
from rx_qt_icons import has_icon, tip
from rx_qt_style import px
from rx_qt_widgets import icon_label, label


def _keys_widget(keys: tuple[str, ...]) -> QWidget:
    """Row of pad-button icons; "+" and timings ("3 s", "×2") are rendered as text."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(px(3))
    for k in keys:
        lay.addWidget(icon_label(k, px(20)) if has_icon(k) else label(k, "small" if k == "+" else "val"))
    lay.addStretch(1)
    return w


class ShortcutRow(QFrame):
    """One list row: the button combination on the left, the description in the middle, an optional master crown on the right."""

    def __init__(self, sc: SC.Shortcut) -> None:
        super().__init__()
        self.setObjectName("row")
        self.shortcut = sc
        lay = QHBoxLayout(self)
        lay.setContentsMargins(px(10), px(7), px(8), px(7))
        lay.setSpacing(px(10))
        keys = _keys_widget(sc.keys)
        keys.setFixedWidth(px(132))
        keys.setToolTip(" + ".join(tip(k) if has_icon(k) else k for k in sc.keys if k != "+"))
        lay.addWidget(keys)
        lay.addWidget(label(sc.text, wrap=True), 1)
        self.crown: QLabel | None = None
        if SC.master_only(sc.feature):
            self.crown = icon_label("crown", px(16))
            self.crown.setToolTip(_("Tylko pad master"))
            lay.addWidget(self.crown, 0, Qt.AlignmentFlag.AlignTop)


class KeysDrawer(SideDrawer):
    """Right-hand drawer listing all pad shortcuts."""

    width_px = 460

    def __init__(self, host: QWidget) -> None:
        super().__init__(host, "keys", _("SKRÓTY PADA"))

    def _build(self, lay: QVBoxLayout) -> None:
        """Create the static parts: the legend line and a scroll area that will hold the shortcut rows."""
        self.legend = label("", "dim", wrap=True)
        lay.addWidget(self.legend)
        self.body = QWidget()
        self.list_lay = QVBoxLayout(self.body)
        self.list_lay.setContentsMargins(0, 0, px(4), 0)
        self.list_lay.setSpacing(px(6))
        self.scroll = QScrollArea()
        self.scroll.setObjectName("keys")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)     # TV mode: the D-pad scrolls the list
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)
        QWidget.setTabOrder(self.close_btn, self.scroll)

    def _on_open(self) -> None:
        """Refill on every open so the list reflects settings changed since last time."""
        self.fill()

    def first_focus(self) -> QWidget:
        """The scroll area gets the initial focus, so the D-pad can scroll right away."""
        return self.scroll

    def fill(self) -> None:
        """Rebuild the list from the current settings: drop the old rows, add a heading per group, then the rows and the group note."""
        while (item := self.list_lay.takeAt(0)) is not None:
            if (w := item.widget()) is not None:
                w.hide()
                w.deleteLater()
        self.rows: list[ShortcutRow] = []
        for g in SC.groups():
            self.list_lay.addWidget(label(g.title.upper(), "h2"))
            for sc in g.items:
                row = ShortcutRow(sc)
                self.rows.append(row)
                self.list_lay.addWidget(row)
            if g.note:
                self.list_lay.addWidget(label(g.note, "dim", wrap=True))
            self.list_lay.addSpacing(px(6))
        self.list_lay.addStretch(1)
        self.legend.setText(_("Korona = tylko pad master (Ustawienia → Master).") if rx_settings.cur.master.pad else
                            _("Wszystkie skróty działają na każdym podłączonym padzie."))
        self.scroll.verticalScrollBar().setValue(0)
