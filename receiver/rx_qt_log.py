"""Receiver: panel dziennika zdarzeń na dole okna (ikona w nagłówku go wysuwa).

Pokazuje wpisy z rx_events: poziom (wszystko / ostrzeżenia i błędy / tylko błędy), limit linii, zdarzenia z nadajnika
(włączane tutaj - nadajnik wysyła je tylko wtedy), kopiowanie widocznych linii, czyszczenie (dwa kliknięcia).
Ustawienia panelu w settings.json (log). Ukryty panel nic nie dopisuje - po pokazaniu odbudowuje widok z pamięci.
"""
import html
from dataclasses import replace

from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QPlainTextEdit, QSpinBox, QVBoxLayout

import rx_events as ev
import rx_settings
from rx_config import C
from rx_i18n import _
from rx_qt_style import BAD, FG, WARN, px
from rx_qt_widgets import Combo, ConfirmButton, icon_button, icon_label, label
from rx_settings import LOG_LEVELS, LOG_LINES

COLOR = {"info": FG, "warn": WARN, "error": BAD}


class LogPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("logpanel")
        self._seq = 0
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
        self.limit = QSpinBox()
        self.limit.setRange(*LOG_LINES)
        self.limit.setSingleStep(100)
        self.limit.setSuffix(_(" linii"))
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

    # ---------- ustawienia ----------
    def load(self, cfg):
        self.level.set_data(cfg.level)
        self.sender_btn.blockSignals(True)
        self.sender_btn.setChecked(cfg.sender)
        self.sender_btn.blockSignals(False)
        self._sender_tip(cfg.sender)
        self.limit.setValue(cfg.max_lines)

    def _sender_tip(self, on):
        self.sender_btn.setToolTip((_("Zdarzenia z nadajnika: włączone") if on else _("Zdarzenia z nadajnika: wyłączone"))
                                   + " - " + _("połączenia padów z telefonem, Wake-on-LAN, błędy nadajnika"))

    def _save(self, **changes):
        cfg = replace(rx_settings.cur.log, **changes)
        if cfg == rx_settings.cur.log:
            return
        ok, msg = rx_settings.update(C.SETTINGS_PATH, log=cfg)
        if not ok:
            ev.error(msg)
        self._sender_tip(cfg.sender)
        self.rebuild()

    # ---------- wpisy ----------
    def _shown(self, e, cfg):
        return ev.visible(e, cfg.level) and (cfg.sender or not e.sender)

    def tick(self):
        """Dopisuje nowe wpisy (wołane z odświeżania okna); ukryty panel czeka na pokazanie."""
        if not self.isVisible():
            return
        new = ev.since(self._seq)
        if not new:
            return
        self._seq = new[-1].seq
        cfg = rx_settings.cur.log
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        for e in new:
            if self._shown(e, cfg):
                self.view.appendHtml(f"<span style='color:{COLOR[e.level]}'>{html.escape(ev.fmt(e))}</span>")
        if at_end:                     # przewinięty w górę (czytanie starszych) - nie skacz na koniec
            bar.setValue(bar.maximum())

    def rebuild(self):
        self.view.clear()
        self.view.setMaximumBlockCount(rx_settings.cur.log.max_lines)
        self._seq = 0
        self.tick()

    def copy(self):
        QApplication.clipboard().setText(self.view.toPlainText())

    def clear(self):
        ev.clear()
        self.rebuild()

    def showEvent(self, e):
        super().showEvent(e)
        self.rebuild()
