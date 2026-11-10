"""Receiver window: the "Update" button flow - both sides are updated, the window waits for the sender and restarts.

A mixin of ``rx_qt.MainWindow``: it uses the window's ``update_btn`` / ``update_ring`` buttons, ``note()`` and ``restart()`` and the
signals ``update_progress`` / ``update_done``, which stay on the window class (a Qt signal must belong to a QObject subclass).
"""
from __future__ import annotations

import math
import threading
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer

import rx_events as ev
import rx_net
import rx_state as S
import rx_update
from proto import CMD_UPDATE, UPD_RUNNING
from rx_i18n import _

if TYPE_CHECKING:
    from rx_qt_widgets import ConfirmButton, ProgressRing

UPDATE_WAIT_S = 20.0       # at most this long after the receiver is done we wait for the sender's result (a countdown is shown)
UPDATE_RESTART_MS = 2500   # after a successful update: the message is visible this long, then the receiver restarts
UPDATE_STUCK_S = 150.0     # no progress for this long (git hung despite its limits) -> the button returns, the result still works
UPDATE_PIP_GRACE_S = 30.0  # added to pip's own time limit before the libraries stage counts as stuck


class UpdateFlow:
    """The update steps of the main window (start, progress, stuck guard, waiting for the sender, the result)."""

    update_btn: ConfirmButton              # the window's button and the ring turning next to its label
    update_ring: ProgressRing
    update_done: Any                       # the window's signals: Result from the update thread, the stage percent
    update_progress: Any
    _upd_result: rx_update.Result | None   # the receiver's own result, while waiting for the sender's
    _upd_run: int                          # numbers the attempts: the stuck guard of an older attempt does nothing
    _upd_pct: int                          # the percent shown (never goes back)
    _upd_seen: float                       # the time of the last stage change (the stuck guard counts from it)
    _upd_sender: bool                      # the sender answers with a result (a new enough version, online at the click)
    _upd_wait_t0: float                    # the start of the wait for the sender
    _upd_left: int | None                  # seconds left on the countdown shown
    _upd_commit: str                       # the sender's commit at the click: a new one after its restart = updated

    if TYPE_CHECKING:                      # implemented by MainWindow; not defined at run time (the mixin comes first in the MRO)
        def note(self, text: str, level: str) -> None:
            """Show a status note in the window (MainWindow.note)."""
        def restart(self, admin: bool = False) -> None:
            """Restart the receiver (MainWindow.restart)."""

    def start_update(self) -> None:
        """Update both sides.

        A command to the sender (git pull on the phone, it restarts itself) and the receiver's git pull in a separate
        thread; the result returns by a signal to the window thread, then we wait for the sender's result.
        """
        if not self.update_btn.isEnabled():
            return
        self.update_btn.setEnabled(False)
        self.update_ring.start()
        self._upd_pct = 0
        self._set_progress(0)
        self.note(_("Pobieranie aktualizacji…"), "")
        self._upd_sender = S.sender_online() and S.sender_build is not None   # a new sender answers with a result
        self._upd_seen = time.monotonic()
        self._upd_wait_t0 = 0.0
        self._upd_left = None
        self._upd_result = None
        self._upd_run = run = self._upd_run + 1
        QTimer.singleShot(int(UPDATE_STUCK_S * 1000), lambda: self._update_stuck(run))
        self._upd_commit = S.sender_build[0] if S.sender_build else ""   # a new commit after the restart = updated
        S.sender_update_msg = ""                 # the reason from the previous attempt must not describe this one
        if S.sender_build is not None:
            S.sender_build = (*S.sender_build[:2], UPD_RUNNING, time.monotonic())
        if S.sender_online():
            rx_net.send_cmd(255, CMD_UPDATE)
        threading.Thread(target=self._update_thread, name="update", daemon=True).start()

    def _update_thread(self) -> None:
        """An exception (git hung, no access to the folder) must not leave the button stuck forever on "Updating…"."""
        try:
            r = rx_update.update(progress=self.update_progress.emit)
        except Exception as e:      # noqa: BLE001 - any failure (git, disk) becomes the result text
            r = rx_update.Result(False, False, _("Aktualizacja nie powiodła się: {err}").format(err=repr(e)))
        self.update_done.emit(r)

    def _set_progress(self, pct: int) -> None:
        """Show the update stage as "Updating… 40%" next to the turning ring (a late signal after the update ended is ignored).

        The percent never goes back: the stages of the receiver and the wait for the sender report independently.
        """
        if not self.update_ring.running():
            return
        self._upd_pct = max(self._upd_pct, pct)
        self._upd_seen = time.monotonic()
        self.update_btn.set_label(f"{_('Aktualizuję…')} {self._upd_pct}%")
        self.update_ring.set_value(self._upd_pct)

    def _update_stuck(self, run: int) -> None:
        """The update thread made no progress for too long - the button must not hang on "Updating…" until the receiver restarts.

        The time is counted from the last stage change, not from the click. The libraries stage (75%, pip) may legitimately run
        for minutes, so it gets pip's own limit; declaring it stuck earlier blamed git and let a second update start over
        a running pip. A late result still works (a restart after the code is fetched).
        """
        if run != self._upd_run or self._upd_result is not None:
            return
        pip = self._upd_pct == rx_update.PCT_BACKED_UP
        limit = rx_update.PIP_TIMEOUT_S + UPDATE_PIP_GRACE_S if pip else UPDATE_STUCK_S
        idle = time.monotonic() - self._upd_seen
        if idle < limit:                                   # a stage ended meanwhile: check again when the new one has had its time
            QTimer.singleShot(int((limit - idle) * 1000) + 100, lambda: self._update_stuck(run))
            return
        print(f"Update: no progress for {idle:.0f}s at {self._upd_pct}%")
        text = (_("Instalacja bibliotek (pip) trwa zbyt długo - uruchom update.bat") if pip else
                _("Aktualizacja nie odpowiada (git) - spróbuj ponownie albo uruchom update.bat"))
        ev.error(text)
        self.update_ring.stop()
        self.update_btn.disarm()
        self.update_btn.setEnabled(True)
        self.note(text, "bad")

    def _update_finished(self, r: rx_update.Result) -> None:
        """The receiver is ready - we wait (UPDATE_WAIT_S at most) for the sender's result, then a message and a restart."""
        print(f"Update: {r.text}")
        self._upd_result = r
        self._upd_wait_t0 = time.monotonic()               # the sender's time starts now, not at the click (our pull took part of it)
        self._update_poll()

    def _update_poll(self) -> None:
        """Wait (polling every 250 ms) for the sender to finish updating, then show the combined result.

        Notify the log and restart when the code changed.
        """
        r = self._upd_result
        if r is None:
            return
        waiting = (self._upd_sender and rx_update.sender_state(S.sender_build, self._upd_commit) == UPD_RUNNING
                   and time.monotonic() - self._upd_wait_t0 < UPDATE_WAIT_S)
        if waiting:
            self._set_progress(rx_update.PCT_INSTALLED + 2)       # the receiver is done, the sender is still pulling
            left = math.ceil(UPDATE_WAIT_S - (time.monotonic() - self._upd_wait_t0))
            if left != self._upd_left:                            # a countdown, redrawn once a second (not on every 250 ms poll)
                self._upd_left = left
                self.note(_("Czekam na nadajnik… {n} s").format(n=left), "")
            QTimer.singleShot(250, self._update_poll)
            return
        parts = [r.text]
        if S.sender_online() or S.sender_build is not None:
            parts.append(rx_update.sender_text(S.sender_build, S.sender_update_msg, self._upd_commit))
        else:
            parts.append(_("nadajnik niepołączony - zaktualizuje się następnym razem"))
        text = " · ".join(parts)
        print(f"Update: {text}")
        (ev.info if r.ok else ev.error)(_("Aktualizacja: {text}").format(text=text))
        if r.changed:
            self._set_progress(100)                # the full ring stays until the restart replaces the window
        else:
            self.update_ring.stop()
            self.update_btn.disarm()               # the icon only (the "Updating…" label existed only during the update)
        self.update_btn.setEnabled(not r.changed)
        self.update_btn.setToolTip(text)
        self.note(text, "ok" if r.ok else "bad")
        if r.ok and not r.changed:
            rx_update.available = rx_update.Available(0)      # checked a moment ago: nothing new
        if r.changed:
            QTimer.singleShot(UPDATE_RESTART_MS, self.restart)
