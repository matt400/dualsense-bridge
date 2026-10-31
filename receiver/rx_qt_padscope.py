"""Receiver: the pad picker bar at the top of the per-pad settings tabs (Lightbar, Haptics and speaker).

One row: the list of pads, the "Default settings" checkbox and a button that drops the pad's own setting; a hint line under
it says what the fields below edit right now. The bar only chooses the entry ("*" = the defaults, or a pad key); the tab
keeps the data and decides when an entry comes into being (a copy of the defaults, made on the first change).
"""
from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from rx_i18n import _
from rx_qt_widgets import Combo, icon_button, label
from rx_settings import DEFAULT_PAD


class PadScope(QWidget):
    """Which settings entry the tab edits: the defaults (checkbox on) or one pad (checkbox off, pad from the list)."""

    changed = Signal(str)      # DEFAULT_PAD or a pad key: the entry the fields should show now (user action only)
    reset = Signal()           # the user wants the chosen pad to use the defaults again (its own entry is dropped)

    def __init__(self) -> None:
        """Build the row: pad list, "Default settings" checkbox, reset button, and the hint line."""
        super().__init__()
        self.pads = Combo()
        self.pads.changed.connect(self._picked)
        self.use_default = QCheckBox(_("Ustawienia domyślne"))
        self.use_default.setToolTip(_("Włączone: edytujesz ustawienia, które dostają pady bez własnego. Wyłączone: edytujesz wybrany pad."))
        self.use_default.toggled.connect(self._toggled)
        self.drop = icon_button("reset", _("Przywróć ustawienia domyślne dla tego pada"), 16)
        self.drop.clicked.connect(self.reset)
        self.hint = label("", "dim", wrap=True)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.pads, 1)
        row.addWidget(self.use_default)
        row.addWidget(self.drop)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(row)
        lay.addWidget(self.hint)
        self.show_state(False)

    def key(self) -> str:
        """The entry being edited: DEFAULT_PAD while the checkbox is on (or there is no pad to choose), else the chosen pad key."""
        if self.use_default.isChecked() or self.pads.count() == 0:
            return DEFAULT_PAD
        return str(self.pads.currentData())

    def set_pads(self, items: Sequence[tuple[str, str]], key: str) -> None:
        """Replace the pad list ((key, text) pairs, no default entry) and select `key`; emits no signals.

        A key that is not in the list (or DEFAULT_PAD) selects the defaults. With no pads at all the checkbox stays on and
        locked, since there is nothing else to edit.
        """
        self.pads.set_items(items)
        chosen = key if any(k == key for k, _t in items) else DEFAULT_PAD
        self.pads.set_data(chosen)
        self.use_default.blockSignals(True)
        self.use_default.setChecked(chosen == DEFAULT_PAD)
        self.use_default.blockSignals(False)
        self.use_default.setEnabled(bool(items))
        self.pads.setEnabled(bool(items) and chosen != DEFAULT_PAD)

    def show_state(self, own: bool) -> None:
        """Update the hint and the reset button for the current entry; `own` = the chosen pad has its own entry."""
        if self.key() == DEFAULT_PAD:
            text = _("Edytujesz ustawienia domyślne: dostają je pady bez własnego ustawienia.")
        elif own:
            text = _("Ten pad ma własne ustawienie.")
        else:
            text = _("Ten pad używa ustawień domyślnych. Zmiana poniżej da mu własne ustawienie.")
        self.hint.setText(text)
        self.drop.setVisible(self.key() != DEFAULT_PAD and own)

    def _picked(self, _key: object) -> None:
        """The user picked another pad in the list."""
        self.changed.emit(self.key())

    def _toggled(self, _on: bool) -> None:
        """The user flipped "Default settings": the list is live only while the pad is edited."""
        self.pads.setEnabled(not self.use_default.isChecked() and self.pads.count() > 0)
        self.changed.emit(self.key())
