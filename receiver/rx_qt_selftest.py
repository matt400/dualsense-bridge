"""Receiver: the pad self-test section of the Test view (rx_qt_test embeds it) - one button, a progress bar and the report.

The logic is rx_selftest (the run on its own thread) and rx_selftest_report (the text); this is only the window part:
  - the latency window the user picks (5 / 15 / 30 / 60 s: during it they press buttons and move the sticks),
  - "Run self-test" / "Stop" (one button), the progress over the steps and the name of the current one,
  - the result: the report is saved to reports/ and "Copy report" puts the same text on the clipboard.

The output to the game stays blocked for the whole run: the section reports ``busy`` and the window's sync_block treats it as the lock
being on, even when the user switched the Test lock off. Leaving the Test view, another pad or a lost window focus stops the run.
Refresh: tick() from the Test panel (30 Hz, only while the view is visible).
"""
from __future__ import annotations

from typing import Final

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

import rx_selftest as T
import rx_selftest_report as R
import rx_state as S
import rx_testmode as testmode
from rx_i18n import _
from rx_qt_icons import icon
from rx_qt_panels import _vbox
from rx_qt_style import ACCENT, LINE, px, set_prop
from rx_qt_widgets import Segmented, SegmentBar, button, icon_label, label

JOIN_S: Final = 2.0             # how long stopping the run waits for its thread (it reacts within a tick and then cleans up)

STEP_TEXT: Final = {
    "meta": "Metadane: wersje, pad, backend",
    "idle": "Sieć i Bluetooth w spoczynku - nie dotykaj pada",
    "latency": "Opóźnienie - naciskaj przyciski i ruszaj gałkami",
    "vibration": "Wibracje: impulsy, zatrzymanie, brak odświeżeń",
    "audio": "Strumień haptyki i głośnika (ścieżka, nie dźwięk)",
    "cpu": "Procesor i zapisy nadajnika",
    "mic": "Mikrofon (nieobsługiwany)",
}
REFUSAL_TEXT: Final = {
    "pad": "Pad nie jest podłączony.",
    "block": "Wyjście do gry nie jest zablokowane - self-test działa w aktywnym oknie, w widoku Test.",
}


class SelfTestBox(QWidget):
    """The "pad self-test" block of the Test view. busy_changed(bool): a run started / ended (the window re-applies the output lock)."""

    busy_changed = Signal(bool)
    WINDOWS: Final = tuple((str(s), f"{s} s") for s in T.LATENCY_WINDOWS)

    def __init__(self) -> None:
        """Build the heading, the hint, the window choice, the button, the progress bar and the result block."""
        super().__init__()
        self.slot = 0
        self.run: T.SelfTest | None = None
        self.text = ""                  # the last report (for "Copy report")
        self.path = ""                  # where it was saved
        box = _vbox(self, 6)
        head = QHBoxLayout()
        head.setSpacing(px(6))
        head.addWidget(icon_label("stopwatch", px(16)))
        head.addWidget(label(_("SELF-TEST PADA"), "h2"), 1)
        box.addLayout(head)
        box.addWidget(label(_("Jeden przebieg mierzy sieć, opóźnienie, wibracje, haptykę i głośnik oraz procesor nadajnika, "
                              "a potem daje raport do skopiowania (dla Claude'a). Wyjście do gry jest zablokowane na czas testu."),
                            "dim", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(px(8))
        row.addWidget(label(_("Okno pomiaru opóźnienia:")))
        self.window_seg = Segmented(self.WINDOWS)
        self.window_seg.set("15")
        row.addWidget(self.window_seg)
        row.addStretch(1)
        box.addLayout(row)
        self.start_btn = button(_("Uruchom self-test"), primary=True)
        self.start_btn.setIcon(icon("stopwatch"))
        self.start_btn.clicked.connect(self.toggle)
        box.addWidget(self.start_btn)
        self.bar = SegmentBar(px(5))
        box.addWidget(self.bar)
        self.status = label("", wrap=True)
        box.addWidget(self.status)
        self.copy_btn = button(_("Kopiuj raport"))
        self.copy_btn.setIcon(icon("copy"))
        self.copy_btn.clicked.connect(self.copy_report)
        box.addWidget(self.copy_btn)
        self.reset_view()

    # ---------- state ----------
    @property
    def busy(self) -> bool:
        """Whether a run is in progress (the window keeps the output to the game blocked)."""
        return self.run is not None

    def set_slot(self, slot: int) -> None:
        """Another pad was chosen: a running test of the old one ends, the old report goes away."""
        if slot != self.slot:
            self.cancel()
            self.slot = slot
            self.reset_view()

    def reset_view(self) -> None:
        """Nothing running and no report shown."""
        self.text = self.path = ""
        self._bar(None)
        self._show("", "")
        self.refresh()

    def _bar(self, p: float | None) -> None:
        """The progress bar: p = 0..1, None = hidden."""
        self.bar.setVisible(p is not None)
        if p is not None:
            self.bar.set([(p, ACCENT), (1 - p, LINE)])

    def _show(self, text: str, level: str) -> None:
        """The status line (hidden when empty) with its level style."""
        self.status.setText(text)
        self.status.setVisible(bool(text))
        set_prop(self.status, "level", level)

    def refresh(self) -> None:
        """Buttons: start needs a connected pad, copy needs a report."""
        alive = 0 <= self.slot < len(S.alive) and bool(S.alive[self.slot])
        self.start_btn.setEnabled(self.busy or alive)
        self.start_btn.setText(_("Przerwij self-test") if self.busy else _("Uruchom self-test"))
        self.window_seg.setEnabled(not self.busy)
        self.copy_btn.setVisible(bool(self.text))

    def rescale(self) -> None:
        """Apply the new DPI scale to the icon and the bar."""
        self.start_btn.setIconSize(QSize(px(18), px(18)))
        self.copy_btn.setIconSize(QSize(px(18), px(18)))
        self.bar.setFixedHeight(px(5))

    # ---------- run ----------
    def toggle(self) -> None:
        """The button: start a run, or stop the one in progress."""
        if self.busy:
            self.cancel()
        else:
            self.start()

    def start(self) -> None:
        """Start a run for the selected pad (the window locks the output first; a refusal says why)."""
        slot = self.slot
        self.text = self.path = ""
        self.run = T.SelfTest(slot, T.Plan(window_s=int(self.window_seg.value())))
        self.busy_changed.emit(True)            # sync_block runs now: the lock must be on before the first packet
        why = T.SelfTest.refusal(slot)
        if why is not None:
            self.run = None
            self.busy_changed.emit(False)
            self._show(_(REFUSAL_TEXT[why]), "bad")
        else:
            self._show("", "")
            self.run.start()
        self.refresh()

    def cancel(self) -> None:
        """Stop a run (the button, leaving the Test view, another pad): what was measured still goes to a report."""
        run = self.run
        if run is None:
            return
        run.abort()
        run.join(JOIN_S)
        self._finish(run)

    def activate(self, on: bool) -> None:
        """Entering the Test view: nothing to do; leaving it: stop the run (the output lock goes with the view)."""
        if not on:
            self.cancel()

    def tick(self) -> None:
        """From the Test panel clock: the progress of the run; its end builds the report."""
        run = self.run
        if run is None:
            return
        if run.finished:
            self._finish(run)
            return
        if not testmode.active:                 # the window lost the focus / the view was left: the output lock is gone
            self.cancel()
            return
        step, idx, frac, samples = run.snapshot()
        self._bar((idx + frac) / len(T.STEPS))
        text = _(STEP_TEXT.get(step, step))
        if step == "latency":
            text += " - " + _("{n} próbek").format(n=samples)
        self._show(f"{idx + 1}/{len(T.STEPS)}  {text}", "")

    def _finish(self, run: T.SelfTest) -> None:
        """The run is over (or was stopped): free the lock, build and save the report, show where it is."""
        self.run = None
        self.busy_changed.emit(False)
        self._bar(None)
        try:
            data = R.build(run)
            self.text = R.render(data)
            self.path = R.save(self.text, run.started_at)
            aborted = data["result"] != "complete"
            self._show(_("Raport zapisany: {path}").format(path=self.path) if not aborted
                       else _("Test przerwany - raport z tego, co zdążono zmierzyć: {path}").format(path=self.path),
                       "warn" if aborted else "ok")
        except OSError as e:
            self._show(_("Nie udało się zapisać raportu ({err}) - można go skopiować.").format(err=e), "bad")
        self.refresh()

    def copy_report(self) -> None:
        """Put the report on the clipboard."""
        if self.text:
            QApplication.clipboard().setText(self.text)
            self._show(_("Raport skopiowany do schowka (plik: {path}).").format(path=self.path or "-"), "ok")
