"""Receiver: the accessibility panel (the "Dostępność" tab) - range, deadzone, curve and tremor damping of the sticks, per pad.

Works on the pad the editor shows. A switch turns the mode on for that pad (rx_padcal / rx_access: it applies in every
profile and the stored values stay when it is off), the sliders edit the chosen stick, and "Dostosuj do ręki" runs the wizard
(rx_reach.Wizard): rest -> drift dead zone, then the player circles each chosen stick -> range. A finished wizard writes its
proposal into the sliders and switches the mode on; the sliders stay editable and "Przywróć zalecane" goes back to the proposal.
All controls are ordinary focusable widgets, so another pad (the master) can drive the wizard through the window's TV mode.
The wizard needs the pad's raw sticks without the game reacting to the circling, so the window locks the output to the game while
`wiz` is set (`busy_changed`).
"""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

import rx_access as A
import rx_profiles as P
import rx_reach as R
import rx_state as S
from rx_i18n import _
from rx_qt_style import ACCENT, LINE, px, set_prop
from rx_qt_widgets import Combo, ConfirmButton, SegmentBar, SliderRow, button, field, label

SAVE_MS = 300                      # a slider edit is saved this long after the last move (one write per drag)
STICK_NAME = {"left": "Lewa gałka", "right": "Prawa gałka"}
CHOICES = (("right", "Prawa gałka"), ("left", "Lewa gałka"), ("both", "Obie gałki"))


def _pct(v: float) -> str:
    """A slider value in percent."""
    return f"{round(v)}%"


class AccessPanel(QWidget):
    """The panel body (the tab is added by the window).

    reach_changed({stick: outer}) - the ring for the stick scope in the Test view; busy_changed(bool) - the wizard started / ended.
    """

    reach_changed = Signal(dict)
    busy_changed = Signal(bool)

    def __init__(self) -> None:
        """Build the switch, the stick choice with the four sliders, and the wizard controls."""
        super().__init__()
        self.slot = 0
        self.cfg: dict[str, Any] = {}                       # the stored settings of this pad (rx_access.normalize format)
        self.stick = "right"                                # the stick the sliders edit
        self.wiz: R.Wizard | None = None
        self.proposal: dict[str, dict[str, float]] = {}     # the last finished wizard: what "Przywróć zalecane" returns to
        self._pending: tuple[int, dict[str, Any]] | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_MS)
        self._save_timer.timeout.connect(self.flush)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.on = QCheckBox(_("Tryb dostępności tego pada"))
        self.on.setToolTip(_("Zasięg, strefa martwa, krzywa i tłumienie drżenia gałek tego pada - działa od razu we wszystkich "
                             "jego profilach (także profilach gier). Po wyłączeniu ustawienia zostają zapisane."))
        self.on.toggled.connect(self._toggled)
        lay.addWidget(self.on)

        self.which = Combo([(k, _(STICK_NAME[k])) for k in ("right", "left")])
        self.which.changed.connect(self._pick_stick)
        lay.addWidget(field(_("Gałka"), self.which))
        self.sl = {
            "outer": SliderRow(_("Zasięg - przy tylu % wychylenia gra dostaje pełne"), 40, 100, 1, fmt=_pct),
            "dz": SliderRow(_("Strefa martwa"), 0, 30, 1, fmt=_pct),
            "curve": SliderRow(_("Krzywa odpowiedzi (poniżej 1× małe ruchy mocniejsze)"), 0.5, 2.0, 0.05, fmt=lambda v: f"{v:.2f}×"),
            "steady": SliderRow(_("Tłumienie drżenia"), 0, 10, 0.5, fmt=lambda v: f"{v:.1f}%"),
        }
        for name, row in self.sl.items():
            row.changed.connect(lambda v, n=name: self._edit(n, v))
            lay.addWidget(row)
        self.sl["steady"].setToolTip(_("Ruch gałki mniejszy niż ten próg (od ostatniej wartości) jest ignorowany - bez opóźnienia, "
                                       "ale też bez wygładzania dużych ruchów."))

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.restore = button(_("Przywróć zalecane"), small=True)
        self.restore.setToolTip(_("Wraca do wartości z ostatniego kreatora (dla tej gałki)."))
        self.restore.clicked.connect(self._restore)
        self.clear_btn = ConfirmButton(_("Wyczyść"), _("Wyczyścić?"), iconic="trash",
                                       tip=_("Wyczyść") + " - " + _("Usuwa ustawienia trybu dostępności tego pada"))
        self.clear_btn.confirmed.connect(self._clear)
        row2.addWidget(self.restore, 1)
        row2.addWidget(self.clear_btn)
        lay.addLayout(row2)

        lay.addWidget(label(_("Kreator mierzy dryf w spoczynku, a potem zasięg: obracaj gałką dookoła, tak daleko, jak jest "
                              "wygodnie. Wyniki trafiają do suwaków - możesz je dalej zmieniać."), "dim", wrap=True))
        self.choice = Combo([(k, _(v)) for k, v in CHOICES])
        lay.addWidget(field(_("Gałki do kalibracji"), self.choice))
        self.bar = SegmentBar(px(6))
        lay.addWidget(self.bar)
        self.msg = label("", wrap=True)
        lay.addWidget(self.msg)
        self.start = button(_("Dostosuj do ręki"), primary=True)
        self.start.clicked.connect(self._toggle_wizard)
        lay.addWidget(self.start)
        self._load()

    # ---------- pad / config ----------
    def set_slot(self, slot: int) -> None:
        """Switch to another pad: the wizard and the proposal belong to the pad they were made on."""
        if slot != self.slot:
            self.flush()                         # an edit of the old pad must not be lost or land on the new one
            self.cancel()
            self.slot = slot
            self.proposal = {}
            self._note("", "")
            self._load()

    def refresh(self) -> None:
        """Entering the tab: show what is stored now (unless an edit is still waiting to be saved)."""
        if self._pending is None:
            self._load()

    def rescale(self) -> None:
        """Apply the new DPI scale to the progress bar."""
        self.bar.setFixedHeight(px(6))

    def _load(self) -> None:
        """Read the pad's stored settings into the widgets (without emitting changes)."""
        self.cfg = P.access(self.slot)
        self.on.blockSignals(True)
        self.on.setChecked(bool(self.cfg.get("on")))
        self.on.blockSignals(False)
        self._show_stick()

    def _show_stick(self) -> None:
        """Show the chosen stick's values in the sliders and tell the stick scope where the range ring is."""
        p = A.describe(self.cfg, self.stick)
        self.sl["outer"].set(p["outer"] * 100)
        self.sl["dz"].set(p["dz"] * 100)
        self.sl["curve"].set(p["curve"])
        self.sl["steady"].set(p["steady"] * 100)
        self._after_edit()

    def _after_edit(self) -> None:
        """Refresh what depends on the settings but is not a slider: the buttons and the range rings of the stick scope."""
        self.restore.setEnabled(self.stick in self.proposal)
        self.clear_btn.setVisible(bool(self.cfg))
        rings = {n: A.describe(self.cfg, n)["outer"] for n in A.STICKS if self.cfg.get("on") and n in self.cfg}
        self.reach_changed.emit({n: v for n, v in rings.items() if v < 1.0})

    def _store(self, cfg: dict[str, Any], now: bool = False) -> None:
        """Keep the new settings and save them (at once, or after the last slider move)."""
        self.cfg = cfg
        self._pending = (self.slot, cfg)
        if now:
            self.flush()
        else:
            self._save_timer.start()

    def flush(self) -> None:
        """Save a pending edit now (the timer, a pad switch, the view closed)."""
        self._save_timer.stop()
        pending, self._pending = self._pending, None
        if pending is not None:
            P.set_access(*pending)

    # ---------- controls ----------
    def _toggled(self, on: bool) -> None:
        """The mode switch of this pad."""
        self._store(A.normalize({**self.cfg, "on": on}), now=True)
        self._show_stick()

    def _pick_stick(self, stick: Any) -> None:
        """The sliders now edit another stick."""
        self.stick = str(stick)
        self._show_stick()

    def _edit(self, name: str, value: float) -> None:
        """A slider moved: percent sliders are fractions in the settings."""
        v = value if name == "curve" else value / 100
        self._store(A.merge_proposal(self.cfg, self.stick, **{name: v}))
        self._after_edit()          # not _show_stick: it would reset the slider that is being dragged

    def _restore(self) -> None:
        """Go back to the wizard's proposal for the shown stick."""
        if p := self.proposal.get(self.stick):
            self._store(A.merge_proposal(self.cfg, self.stick, **p), now=True)
            self._show_stick()

    def _clear(self) -> None:
        """Remove all accessibility settings of this pad."""
        self.proposal = {}
        self._store({}, now=True)
        self._note("✓ " + _("Ustawienia trybu dostępności usunięte"), "ok")
        self._load()

    # ---------- wizard ----------
    def _note(self, text: str, level: str) -> None:
        """Show a message (hidden when empty) with its level style."""
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        set_prop(self.msg, "level", level)

    def _toggle_wizard(self) -> None:
        """Start the wizard for the chosen sticks, or stop a running one."""
        if self.wiz is not None:
            self.cancel()
            self._note("", "")
            return
        if not S.alive[self.slot]:
            self._note(_("Pad niepodłączony."), "bad")
            return
        self.flush()
        data = self.choice.currentData()
        sticks = A.STICKS if data == "both" else (str(data),)
        self.wiz = R.Wizard(self.slot, sticks, time.monotonic(), P.raw_sticks if P.sender_calib(self.slot) else None)
        self._note("", "")
        self._show_progress(time.monotonic())
        self.busy_changed.emit(True)

    def cancel(self) -> None:
        """Abort a running wizard and hide its bar (it must not reappear on the next visit)."""
        was = self.wiz is not None
        if self.wiz is not None:
            self.wiz.cancel()
            self.wiz = None
        self._show_progress(None)
        if was:
            self.busy_changed.emit(False)

    def _show_progress(self, now: float | None) -> None:
        """The progress bar, the step text and the button caption for the running wizard (all hidden / idle without one)."""
        w = self.wiz
        self.start.setText(_("Przerwij") if w is not None else _("Dostosuj do ręki"))
        self.bar.setVisible(w is not None)
        if w is None or now is None:
            return
        p = w.progress(now)
        self.bar.set([(p, ACCENT), (1 - p, LINE)])
        who = _(STICK_NAME.get(w.stick, ""))
        text = {"rest": _("Puść gałki i odłóż pad - mierzę dryf…"),
                "ready": _("Przygotuj się: za chwilę obracaj gałką dookoła ({s}).").format(s=who),
                "reach": _("Obracaj gałką dookoła, tak daleko jak wygodnie ({s})…").format(s=who)}.get(w.step, "")
        self._note(text, "")

    def tick(self, now: float) -> None:
        """From the window clock: advance the wizard; a pad that left aborts it; the end applies the proposal."""
        w = self.wiz
        if w is None:
            return
        if not S.alive[self.slot]:
            self.cancel()
            self._note(_("Pad niepodłączony."), "bad")
            return
        w.tick(now)
        if not w.done:
            self._show_progress(now)
            return
        self.wiz = None
        self._show_progress(None)
        self.busy_changed.emit(False)
        if w.failure is not None:
            self._note(self._failure_text(w.failure), "bad")
            return
        self._finish(w.proposal)

    def _failure_text(self, f: R.Failure) -> str:
        """Describe why the wizard stopped."""
        who = _(STICK_NAME.get(f.stick or "", ""))
        return {"nodata": _("Brak próbek - pad niepodłączony?"),
                "moved": _("Gałka się ruszała - puść obie gałki i zacznij jeszcze raz."),
                "few": _("{s}: nie widzę pełnego koła - obróć gałką dookoła, w każdą stronę.").format(s=who),
                "small": _("{s}: gałka prawie się nie ruszała - spróbuj jeszcze raz.").format(s=who)}.get(f.reason, f.reason)

    def _finish(self, proposal: dict[str, dict[str, float]]) -> None:
        """Make the proposal the settings (the mode on) and describe it; the sliders stay editable."""
        self.proposal = proposal
        cfg = {**self.cfg, "on": True}
        for stick, values in proposal.items():
            cfg = A.merge_proposal(cfg, stick, **values)
        self._store(A.normalize(cfg), now=True)
        self._load()
        lines = [_("{s}: zasięg {o}, strefa martwa {d}").format(
                     s=_(STICK_NAME[n]), o=_pct(A.describe(self.cfg, n)["outer"] * 100), d=_pct(A.describe(self.cfg, n)["dz"] * 100))
                 for n in proposal]
        self._note("✓ " + _("Gotowe - tryb dostępności włączony.") + "\n" + "\n".join(lines), "ok")
