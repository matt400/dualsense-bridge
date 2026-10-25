"""Receiver: the vertical icon menu on the left edge of the window (next to the pad column).

It holds what used to sit in the header, in three groups: the panels and views (Diagnostics, Bluetooth, pad shortcuts, event
log), the window (full screen, settings) and the system actions at the bottom (update, restart, quit). The buttons are created by
the window (they keep their names: bt_btn, keys_btn, ...); this widget only lays them out in a narrow column and keeps their
sizes right when the TV mode scales the interface. The order of creation is the keyboard / pad focus order, so the window
creates the buttons top to bottom.
"""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QAbstractButton, QFrame, QVBoxLayout

from rx_qt_style import px

BUTTON, ICON, WIDTH = 40, 20, 56           # button size, icon size, column width [px at scale 1]


def _separator() -> QFrame:
    """A thin horizontal line between two groups of buttons."""
    line = QFrame()
    line.setObjectName("vsep")
    return line


class SideMenu(QFrame):
    """A narrow column of icon buttons in groups: `group()` adds buttons, `bottom()` starts the group pinned to the bottom."""

    def __init__(self) -> None:
        """An empty menu; the window fills it."""
        super().__init__()
        self.setObjectName("sidebar")
        self.buttons: list[QAbstractButton] = []
        self.lay = QVBoxLayout(self)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.rescale()

    def group(self, *buttons: QAbstractButton) -> None:
        """Add a group of buttons under the previous ones (a separator line first, except for the very first group)."""
        if self.buttons:
            self.lay.addSpacing(px(2))
            self.lay.addWidget(_separator())
            self.lay.addSpacing(px(2))
        for b in buttons:
            self.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
            self.buttons.append(b)
        self._size(buttons)

    def bottom(self, *buttons: QAbstractButton) -> None:
        """The last group, pinned to the bottom edge: what is rarely used and changes the state of the whole program."""
        self.lay.addStretch(1)
        self.group(*buttons)

    def _size(self, buttons: tuple[QAbstractButton, ...]) -> None:
        """Square buttons of the current scale."""
        for b in buttons:
            b.setFixedSize(px(BUTTON), px(BUTTON))
            b.setIconSize(QSize(px(ICON), px(ICON)))

    def rescale(self) -> None:
        """The width, margins and button sizes for the current scale (TV mode changes it)."""
        self.setFixedWidth(px(WIDTH))
        self.lay.setContentsMargins(px(7), px(10), px(7), px(10))
        self.lay.setSpacing(px(4))
        self._size(tuple(self.buttons))
