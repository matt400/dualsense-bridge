"""Receiver: the event log panel at the bottom of the window (the header icon slides it out).

It shows the entries of rx_events: level (everything / warnings and errors / errors only), the line limit, events from the
sender (enabled here - the sender sends them only then), copying the visible lines, clearing (two clicks).
The panel settings live in settings.json (log). A hidden panel appends nothing - when shown it rebuilds the view from memory.
"""
import html
from dataclasses import replace

from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QPlainTextEdit, QVBoxLayout

import rx_events as ev
import rx_settings
from rx_config import C
from rx_i18n import _
from rx_qt_style import BAD, FG, WARN, px
from rx_qt_widgets import Combo, ConfirmButton, icon_button, icon_label, label, spinbox
from rx_settings import LOG_LEVELS, LOG_LINES

COLOR = {"info": FG, "warn": WARN, "error": BAD}


def _order(e):
    """Panel order: the entry time (the sender supplies its own), the order of adding on equal times."""
    return e.t, e.seq


class LogPanel(QFrame):
    """The log panel widget: a toolbar (level, sender events, line limit, copy, clear) above a read-only text view."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("logpanel")
        self._seq, self._last = 0, (0.0, 0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(16), px(8), px(16), px(10))
        lay.setSpacing(px(6))
        top = QHBoxLayout()
        top.setSpacing(px(6))
        self.level = Combo([(k, _(v)) for k, v in LOG_LEVELS.items()])
        self.level.setToolTip(_("Które wpisy pokazywać"))
        self.level.changed.connect(lambda v: self._save(level=v))
        self.sender_btn = icon_button("phone", "", 18)
        self.sender_btn.setCheckable(True)
        self.sender_btn.toggled.connect(lambda on: self._save(sender=on))
        self.limit = spinbox(*LOG_LINES, step=100, suffix=_(" linii"))
        self.limit.setToolTip(_("Limit linii w panelu (starsze znikają)"))
        self.limit.editingFinished.connect(lambda: self._save(max_lines=self.limit.value()))
        copy = icon_button("copy", _("Kopiuj widoczne wpisy do schowka"), 18)
        copy.clicked.connect(self.copy)
        self.clear_btn = ConfirmButton(_("Wyczyść"), _("Wyczyścić?"), iconic="trash", tip=_("Wyczyść dziennik"))
        self.clear_btn.confirmed.connect(self.clear)
        top.addWidget(icon_label("log", px(18)))
        top.addWidget(label(_("DZIENNIK ZDARZEŃ"), "h2"))
        top.addStretch(1)
        for w in (self.level, self.sender_btn, self.limit, copy, self.clear_btn):
            top.addWidget(w)
        lay.addLayout(top)
        self.view = QPlainTextEdit()
        self.view.setObjectName("log")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(self.view, 1)
        self.load(rx_settings.cur.log)

    # ---------- settings ----------
    def load(self, cfg) -> None:
        """Show the settings from `cfg` in the toolbar controls (signals blocked where needed so loading does not save)."""
        self.level.set_data(cfg.level)
        self.sender_btn.blockSignals(True)
        self.sender_btn.setChecked(cfg.sender)
        self.sender_btn.blockSignals(False)
        self._sender_tip(cfg.sender)
        self.limit.setValue(cfg.max_lines)

    def _sender_tip(self, on) -> None:
        """Set the tooltip of the sender-events toggle according to its state."""
        self.sender_btn.setToolTip((_("Zdarzenia z nadajnika: włączone") if on else _("Zdarzenia z nadajnika: wyłączone"))
                                   + " - " + _("połączenia padów z telefonem, Wake-on-LAN, błędy nadajnika"))

    def _save(self, **changes) -> None:
        """Persist changed log settings (no-op when nothing differs), then rebuild the view so the new filter applies."""
        cfg = replace(rx_settings.cur.log, **changes)
        if cfg == rx_settings.cur.log:
            return
        ok, msg = rx_settings.update(C.SETTINGS_PATH, log=cfg)
        if not ok:
            ev.error(msg)
        self._sender_tip(cfg.sender)
        self.rebuild()

    # ---------- entries ----------
    def _shown(self, e, cfg):
        """Whether an entry passes the level filter and the sender-events switch."""
        return ev.visible(e, cfg.level) and (cfg.sender or not e.sender)

    def tick(self) -> None:
        """Append new entries (called from the window refresh); a hidden panel waits until it is shown.

        Entries are sorted by time. If the oldest new one is earlier than what is already displayed, appending would
        break the time order, so the whole view is rebuilt instead. Autoscroll happens only when the user was already at the end.
        """
        if not self.isVisible():
            return
        new = ev.since(self._seq)
        if not new:
            return
        new.sort(key=_order)
        if self._seq and _order(new[0]) < self._last:
            # a sender event with its own timestamp (collected while the receiver was not running, e.g. "pad connected
            # to the phone" from before the receiver started) is older than the displayed entries - appending it at the end
            # would break the time order
            self.rebuild()
            return
        self._seq = max(e.seq for e in new)
        self._last = max(self._last, _order(new[-1]))
        cfg = rx_settings.cur.log
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        for e in new:
            if self._shown(e, cfg):
                self.view.appendHtml(f"<span style='color:{COLOR[e.level]}'>{html.escape(ev.fmt(e))}</span>")
        if at_end:                     # scrolled up (reading older entries) - do not jump to the end
            bar.setValue(bar.maximum())

    def rebuild(self) -> None:
        """Clear the view, apply the current line limit and re-add all matching entries from memory."""
        self.view.clear()
        self.view.setMaximumBlockCount(rx_settings.cur.log.max_lines)
        self._seq, self._last = 0, (0.0, 0)
        self.tick()

    def copy(self) -> None:
        """Copy the visible lines to the clipboard."""
        QApplication.clipboard().setText(self.view.toPlainText())

    def clear(self) -> None:
        """Clear the stored log and the view."""
        ev.clear()
        self.rebuild()

    def showEvent(self, e) -> None:
        """Entries are not appended while hidden, so rebuild the view when the panel is shown."""
        super().showEvent(e)
        self.rebuild()
