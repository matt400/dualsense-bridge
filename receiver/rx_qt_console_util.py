"""Receiver window: Helpers shared by the tabs of the settings window."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from rx_i18n import _
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLayout, QScrollArea, QVBoxLayout, QWidget

import rx_state as S
from rx_config import C
from rx_qt_sendercfg import SenderCfgPanel
from rx_settings import DEFAULT_PAD


def page(items: Sequence[QWidget | QLayout]) -> QScrollArea:
    """A tab: sections one under another, scrolling vertically only (the width is always the window's width)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 12, 6, 4)
    lay.setSpacing(12)
    for it in items:
        if isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    lay.addStretch(1)
    return scroll(w)


def scroll(w: QWidget) -> QScrollArea:
    """Wrap a widget in a frameless scroll area without a horizontal bar."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(w)
    return sa


def row_of(*widgets: Any) -> QHBoxLayout:
    """Buttons in a row, to the left."""
    row = QHBoxLayout()
    row.setSpacing(8)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    return row


def wrap(layout: QLayout) -> QWidget:
    """Wrap a layout in a plain widget so it can be used as a field."""
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w


class TabBase:
    """What the tab mixins of ConsoleSettings share: the per-pad scope memory and the host's members they call.

    The annotations declare the members of ConsoleSettings (the window class) that the tab code uses; the stubs exist for the
    type checker only (they must not shadow the real methods at run time: the mixins come first in the MRO).
    """

    _ui: QSettings | None                   # the main window's receiver_ui.ini (None without an owner window)
    save_btn: Any                           # the Save button, the signals below and the Sender tab: created / declared by the window
    saved: Any
    elevate: Any
    outputs_listed: Any
    sender_cfg: SenderCfgPanel

    if TYPE_CHECKING:
        def _status(self, text: str, level: str = "") -> None:
            """Show a message in the window's status line."""

        def _stop_capture(self) -> None:
            """Stop waiting for a pad button pick."""

        def _outputs_ready(self, result: Any) -> None:
            """Take the audio output listing from the worker thread."""

    def _saved_scope(self, name: str) -> str:
        """The entry (DEFAULT_PAD or a pad key) a per-pad tab showed when it was last used; DEFAULT_PAD without a saved one."""
        if self._ui is None:
            return DEFAULT_PAD
        return str(self._ui.value(name, DEFAULT_PAD)) or DEFAULT_PAD

    def _remember_scope(self, name: str, key: str) -> None:
        """Keep the entry the user picked in receiver_ui.ini, so the tab opens on it after a restart as well."""
        if self._ui is not None:
            self._ui.setValue(name, key)
            self._ui.sync()

    def _scope_items(self, remembered: Iterable[str]) -> list[tuple[str, str]]:
        """The pads for a PadScope list: connected ones first, then the saved ones that are not connected (no default entry)."""
        items: list[tuple[str, str]] = []
        known = {DEFAULT_PAD}
        for i in range(C.NUM_PADS):
            key = S.profile_key(i)
            if key not in known and S.pad_id[i] and (S.alive[i] or S.want[i].is_set()):
                known.add(key)
                items.append((key, _("Pad {n}  ·  {key}").format(n=i + 1, key=key)))
        items += [(k, _("{key}  (niepodłączony)").format(key=k)) for k in remembered if k not in known]
        return items
