"""Receiver window: the Backups and Security tabs of the settings window."""
from __future__ import annotations


from rx_i18n import _
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLayout, QVBoxLayout, QWidget)

import rx_backup
import rx_security
import rx_update
from rx_backup import Backup
from rx_qt_icons import icon
from rx_qt_style import set_prop
from rx_qt_widgets import (ConfirmButton, button, group, label, left)
from rx_qt_console_util import row_of, TabBase


RESTORE_RESTART_MS = 1500     # after restoring a backup: the message is visible this long, then the receiver restarts

def _backup_reason(b: Backup) -> str:
    """Where a backup came from: an update (old -> new version), before restoring another, manual."""
    if b.reason == "update":
        ver = f"{b.commit} → {b.new}" if b.commit and b.new else b.commit or b.new
        return _("przed aktualizacją") + (f" ({ver})" if ver else "")
    return _("przed przywróceniem kopii") if b.reason == "restore" else _("zrobiona ręcznie")


class SafetyTabs(TabBase):
    """The Backups and Security tabs of the settings window."""

    def _backup_tab(self) -> list[QWidget | QLayout]:
        """The Backups tab: the list of copies and a make-a-copy-now button."""
        self.backup_rows = QVBoxLayout()
        self.backup_rows.setSpacing(6)
        now = button(_("Zrób kopię teraz"), small=True)
        now.setIcon(icon("backup"))
        now.clicked.connect(self._backup_now)
        box = group(_("KOPIE USTAWIEŃ"),
                    label(_("Przed każdą aktualizacją receiver kopiuje settings.json, console.json, remap.json i padcal.json "
                            "(zostaje {n} ostatnich). Przywrócenie najpierw zapisuje kopię bieżących plików - też da się "
                            "je cofnąć - a potem uruchamia receiver od nowa.").format(n=rx_backup.KEEP), "dim", wrap=True),
                    self.backup_rows, left(now))
        return [box]

    def _load_backups(self) -> None:
        """The list of backups from the newest: the date, where it came from, the files; "Restore" needs a second click."""
        while self.backup_rows.count():
            w = self.backup_rows.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        backups = rx_backup.list_backups()
        if not backups:
            self.backup_rows.addWidget(label(_("Nie ma jeszcze żadnej kopii."), "dim"))
        for b in backups:
            self.backup_rows.addWidget(self._backup_row(b))

    def _backup_row(self, b: Backup) -> QWidget:
        """One backup row: its time, origin and files, and a Restore button that needs a confirming click."""
        from PySide6.QtWidgets import QFrame
        row = QFrame()
        row.setObjectName("row")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(12, 8, 8, 8)
        hl.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(rx_backup.describe_time(b.time), "val"))
        text.addWidget(label(_backup_reason(b) + "  ·  " + ", ".join(b.files), "dim", wrap=True))
        hl.addLayout(text, 1)
        restore = ConfirmButton(_("Przywróć"), _("Przywrócić?"), small=True)
        restore.setToolTip(_("Zastępuje bieżące ustawienia tymi z kopii i uruchamia receiver od nowa"))
        restore.confirmed.connect(lambda name=b.name: self._restore(name))
        hl.addWidget(restore, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _backup_now(self) -> None:
        """Make a manual backup and refresh the list."""
        try:
            b = rx_backup.create("manual", rx_update.version()[0] or "")
        except OSError as e:
            self._status(_("Nie udało się zrobić kopii: {err}").format(err=e), "bad")
            return
        self._load_backups()
        self._status(_("✓ Kopia zapisana.") if b else _("Nie ma jeszcze plików ustawień do skopiowania."),
                     "ok" if b else "warn")

    def _restore(self, name: str) -> None:
        """Restore a backup; on success disable Save and restart the receiver shortly."""
        ok, msg = rx_backup.restore(name, rx_update.version()[0] or "")
        self._status(("✓ " if ok else "") + msg, "ok" if ok else "bad")
        self._load_backups()
        if ok:
            self._stop_capture()
            self.save_btn.setEnabled(False)       # Save before the restart would overwrite the files just restored
            QTimer.singleShot(RESTORE_RESTART_MS, self.saved.emit)   # the settings are read at startup

    def _security_tab(self) -> list[QWidget | QLayout]:
        """The Security tab: the findings of rx_security, the harden-folder button and a new HMAC key to the clipboard."""
        self.sec_now = label("", wrap=True)
        self.sec_harden = button(_("Zabezpiecz folder"), small=True)
        self.sec_harden.setIcon(icon("shield"))
        self.sec_harden.setToolTip(_("Zapis w folderze receivera tylko dla administratorów (receiver dalej zapisuje "
                                     "ustawienia i się aktualizuje)"))
        self.sec_harden.clicked.connect(self._sec_harden)
        key = button(_("Kopiuj nowy klucz"), small=True)
        key.setIcon(icon("copy"))
        key.setToolTip(_("Losowy klucz HMAC do schowka - wpisz go jako key w receiver.toml i w sender.toml"))
        key.clicked.connect(self._sec_key)
        box = group(_("BEZPIECZEŃSTWO"),
                    label(_("Receiver działa jako administrator i przyjmuje polecenia z sieci. Klucz HMAC (key, ten sam "
                            "po obu stronach) podpisuje pakiety i chroni przed powtórzeniem nagranych; bez niego każdy "
                            "w sieci lokalnej może sterować myszą i akcjami pada master. Szczegóły: docs/SECURITY.md."),
                          "dim", wrap=True),
                    self.sec_now, row_of(key, self.sec_harden))
        self._sec_state()
        return [box]

    def _sec_state(self) -> None:
        """Show the current security findings (or that none were found) and the harden button only when the folder issue exists."""
        found = rx_security.issues()
        self.sec_now.setText("\n".join("• " + i.text for i in found) if found else _("✓ Nie wykryto problemów."))
        set_prop(self.sec_now, "level", "warn" if found else "ok")
        self.sec_harden.setVisible(any(i.code == "folder" for i in found))

    def _sec_key(self) -> None:
        """Copy a freshly generated HMAC key to the clipboard."""
        from proto import generate_key
        QApplication.clipboard().setText(generate_key())
        self._status(_("✓ Nowy klucz w schowku - wpisz go jako key w receiver.toml i w sender.toml, potem uruchom "
                       "oba programy ponownie."), "ok")

    def _sec_harden(self) -> None:
        """Harden the receiver folder and show the result."""
        ok, msg = rx_security.harden()
        self._status(("✓ " if ok else "") + msg, "ok" if ok else "bad")
        self._sec_state()
