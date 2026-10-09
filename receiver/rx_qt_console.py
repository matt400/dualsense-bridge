"""Receiver: okno ustawień trybu konsoli (tray -> Ustawienia, albo przycisk w nagłówku okna).

Przycisk pada wybierasz naciśnięciem go na padzie (rx_console.arm_capture), urządzenia audio to lista aktywnych wyjść
Windows (rx_audio, przycisk Odśwież). "Przetestuj" włącza tryb konsoli na kilka sekund z wartościami z okna (jeszcze
niezapisanymi) i sam wraca do extend. "Zapisz" zapisuje console.json i prosi właściciela okna o restart receivera
(sygnał saved) - po restarcie obowiązuje startup_mode jak przy autostarcie.
"""
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QSpinBox, QVBoxLayout, QWidget

import rx_audio
import rx_console
from rx_buttons import LABELS
from rx_config import C
from rx_console import STARTUP_MODES, ConsoleConfig
from rx_qt_style import set_prop
from rx_qt_widgets import Combo, button, field, label

CAPTURE_TIMEOUT_MS = 15000
NO_AUDIO = ("", "— nie zmieniaj dźwięku —")
STARTUP_LABELS = {"extend": "Rozszerz: oba ekrany aktywne", "internal": "Tylko monitor"}


class ConsoleSettings(QDialog):
    saved = Signal()            # console.json zapisany - właściciel okna restartuje receiver
    captured = Signal(str)      # nazwa przycisku z wątku sieci (Qt kolejkuje do wątku okna)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("Ustawienia - tryb konsoli")
        self.setMinimumWidth(480)
        self.toggle_button = ConsoleConfig().toggle_button
        self.captured.connect(self._on_captured)
        self._cap_timer = QTimer(self, singleShot=True, interval=CAPTURE_TIMEOUT_MS)
        self._cap_timer.timeout.connect(self._stop_capture)
        self._build()

    # ---------- budowa ----------
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        lay.addWidget(label("Tryb konsoli", "h3"))
        lay.addWidget(label("Podwójne kliknięcie wybranego przycisku pada przełącza ekran i dźwięk między monitorem "
                            "a telewizorem. Brak sygnału z pada przez ustawiony czas wraca do trybu rozszerzonego (oba ekrany).", "dim", wrap=True))

        self.pick = button("")
        self.pick.clicked.connect(self._toggle_capture)
        self.pick_name = label("", "val")
        row = QHBoxLayout()
        row.addWidget(self.pick_name, 1)
        row.addWidget(self.pick)
        lay.addWidget(field("Przycisk pada (podwójne kliknięcie)", _wrap(row)))

        self.dbl = _spin(100, 2000, 50, " ms")
        self.timeout = _spin(5, 3600, 5, " s")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(field("Odstęp między kliknięciami", self.dbl))
        row.addWidget(field("Auto-OFF po braku sygnału z pada", self.timeout))
        lay.addLayout(row)

        self.tv, self.pc = Combo(), Combo()
        lay.addWidget(field("Dźwięk po włączeniu (TV)", self.tv))
        lay.addWidget(field("Dźwięk po wyłączeniu (komputer)", self.pc))
        refresh = button("Odśwież listę urządzeń", small=True)
        refresh.clicked.connect(lambda: self.refresh())     # clicked podaje 'checked' - nie jako urządzenie
        lay.addWidget(refresh)

        self.startup = Combo([(m, STARTUP_LABELS[m]) for m in STARTUP_MODES])
        lay.addWidget(field("Ekran przy starcie receivera", self.startup))

        self.status = label("", wrap=True)
        lay.addWidget(self.status)
        lay.addWidget(label("Zapis uruchamia receiver od nowa (pady na chwilę znikną). Niezapisane zmiany profilu "
                            "(Save w oknie głównym) zostaną utracone.", "dim", wrap=True))

        self.test_btn = button("Przetestuj")
        self.test_btn.setToolTip("Włącza tryb konsoli na kilka sekund z wartościami z tego okna, potem wraca do trybu rozszerzonego")
        self.test_btn.clicked.connect(self._test)
        cancel = button("Anuluj")
        cancel.clicked.connect(self.close)
        self.save_btn = button("Zapisz i uruchom ponownie", primary=True)
        self.save_btn.clicked.connect(self._save)
        row = QHBoxLayout()
        row.addWidget(self.test_btn)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.save_btn)
        lay.addLayout(row)

    # ---------- dane ----------
    def present(self):
        """Pokazuje okno; świeżo otwarte ma wartości z działającej konfiguracji (nie z poprzedniej, anulowanej edycji)."""
        if not self.isVisible():
            self.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def reload(self):
        cfg = rx_console.ctl.cfg if rx_console.ctl else ConsoleConfig()
        self.set_button(cfg.toggle_button)
        self.dbl.setValue(cfg.double_click_ms)
        self.timeout.setValue(cfg.timeout_s)
        self.startup.set_data(cfg.startup_mode)
        self.pick.setEnabled(rx_console.ctl is not None)
        self.refresh(cfg.audio_tv, cfg.audio_pc)
        self._status("")

    def refresh(self, tv=None, pc=None):
        """Odczytuje aktywne wyjścia audio; wybrane wcześniej urządzenie, którego teraz nie ma (np. TV wyłączony),
        zostaje na liście jako "niedostępne", żeby zapis go nie zgubił."""
        tv = self.tv.currentData() if tv is None else tv
        pc = self.pc.currentData() if pc is None else pc
        try:
            names = [name for _, name in rx_audio.list_outputs()]
            self._status("")
        except Exception as e:
            names = []
            self._status(f"Nie można odczytać urządzeń audio: {e!r} (pip install pycaw)", "bad")
        for combo, current in ((self.tv, tv), (self.pc, pc)):
            items = [NO_AUDIO, *((n, n) for n in names)]
            if current and current not in names:
                items.append((current, f"{current} (niedostępne)"))
            combo.set_items(items)
            combo.set_data(current or "")

    def collect(self):
        return ConsoleConfig(toggle_button=self.toggle_button, double_click_ms=self.dbl.value(),
                             audio_tv=self.tv.currentData() or "", audio_pc=self.pc.currentData() or "",
                             timeout_s=self.timeout.value(), startup_mode=self.startup.currentData())

    # ---------- przycisk pada ----------
    def set_button(self, name):
        self.toggle_button = name
        self.pick_name.setText(LABELS.get(name, name))
        self.pick.setText("Wybierz na padzie")
        set_prop(self.pick, "armed", False)

    def _toggle_capture(self):
        if self._cap_timer.isActive():
            self._stop_capture()
            return
        rx_console.ctl.arm_capture(self.captured.emit)       # wołane z wątku sieci; emit jest bezpieczny między wątkami
        self._cap_timer.start()
        self.pick.setText("Naciśnij przycisk na padzie… (klik = anuluj)")
        set_prop(self.pick, "armed", True)

    def _stop_capture(self):
        self._cap_timer.stop()
        if rx_console.ctl:
            rx_console.ctl.cancel_capture()
        self.set_button(self.toggle_button)

    def _on_captured(self, name):
        if self._cap_timer.isActive():                       # spóźniony sygnał po anulowaniu nic nie zmienia
            self._cap_timer.stop()
            self.set_button(name)

    # ---------- akcje ----------
    def _test(self):
        if rx_console.ctl is None:
            self._status("Tryb konsoli nie działa w tym uruchomieniu.", "bad")
            return
        rx_console.ctl.test(self.collect())
        self._status(f"Test: TV i dźwięk TV na {rx_console.TEST_S:.0f} s, potem powrót do trybu rozszerzonego.", "ok")
        self.test_btn.setEnabled(False)
        QTimer.singleShot(int((rx_console.TEST_S + 5) * 1000), lambda: self.test_btn.setEnabled(True))

    def _save(self):
        try:
            rx_console.save(self.collect(), C.CONSOLE_PATH)
        except OSError as e:
            self._status(f"Nie udało się zapisać {C.CONSOLE_PATH}: {e}", "bad")
            return
        self._stop_capture()
        self.saved.emit()

    def _status(self, text, level=""):
        self.status.setText(text)
        set_prop(self.status, "level", level)

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._cap_timer.isActive():
            self._stop_capture()


def _spin(lo, hi, step, suffix):
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    return box


def _wrap(layout):
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w
