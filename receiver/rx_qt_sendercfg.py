"""Receiver window: the Sender tab - the sender's own settings (the ones from sender.toml) changed from here.

The sender applies a change at once (no restart) and keeps it in its remote_cfg.json; the receiver keeps the choice in
settings.json (``sender.values``) and pushes it again when the sender comes online (rx_sender_cfg). Only what differs from the
sender's sender.toml is stored, so "Back to the config file" simply drops the entry.

The tab edits a local copy; Save in the settings window sends the differences (rx_net._settings_changed -> rx_sender_cfg.sync).
Every row shows what the setting does, the value the sender reports from its sender.toml, and when a new value starts to count
("now" / "from the next sound stream" / "when a pad connects"). Without an HMAC key and protocol v3 the sender refuses changes,
so the tab says so instead of pretending.
"""
from __future__ import annotations

from typing import Any, NamedTuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QHBoxLayout, QSpinBox, QVBoxLayout, QWidget

import cfgspec
import rx_sender_cfg
import rx_settings
import rx_state as S
from proto import CFGR_NO_KEY, CFGR_NO_V3, CFGR_OFF
from rx_i18n import _
from rx_qt_style import set_prop
from rx_qt_widgets import Combo, button, group, label, spinbox

REFRESH_MS = 700


class Info(NamedTuple):
    """How a setting looks in the tab."""

    title: str
    unit: str              # suffix of the number field (" ms", " s", ...)
    default: cfgspec.Value  # shown while the sender has not reported its own value
    decimals: int          # fractional digits of a float field
    text: str              # what it does, in full


def _choices() -> dict[str, dict[str, str]]:
    """The texts of the choice settings."""
    return {
        "rumble": {"auto": _("automatycznie (evdev i hidraw naraz)"), "evdev": _("tylko sterownik evdev"),
                   "hidraw": _("tylko raport HID (hidraw)"), "off": _("wyłączone")},
        "haptics_motors": {"haptics": _("haptyka ma pierwszeństwo"), "rumble": _("wibracja ma pierwszeństwo")},
        "speaker_opus_mode": {"hybrid": _("hybrid (czysty głos)"), "celt": _("celt (stary tryb)")},
    }


def group_titles() -> list[tuple[str, tuple[str, ...]]]:
    """The sections of the tab: (title, setting names in order)."""
    return [
        (_("PADY I OSZCZĘDZANIE ENERGII"), ("max_pads", "grab_pad", "grab_touch", "idle_off_min", "battery_poll_s", "rescan_s")),
        (_("GAŁKI I ŻYROSKOP"), ("stick_deadzone", "motion_deadband_gyro", "motion_deadband_acc", "calibrate_s",
                                 "calibrate_max_range", "autobias", "autobias_s", "autobias_range")),
        (_("CZĘSTOŚĆ PAKIETÓW"), ("heartbeat_s", "pad_min_dt", "motion_min_dt")),
        (_("PANEL DOTYKOWY"), ("tap_click", "tap_max_s", "tap_max_move", "touch_coalesce_ms", "touch_deadzone",
                               "touch_start_move", "gestures")),
        (_("WIBRACJE, HAPTYKA I GŁOŚNIK"), ("rumble", "haptics_motors", "haptics_buffer_ms", "speaker_buffer_ms", "speaker_volume",
                                           "speaker_opus_bytes", "speaker_opus_mode", "speaker_opus_complexity")),
        (_("MAŁE OPÓŹNIENIA SYSTEMU (wymaga roota na nadajniku)"), ("rt_priority", "low_latency", "cpu_latency_us")),
    ]


def infos() -> dict[str, Info]:
    """Title, unit, default and the full description of every setting (built per call: the texts follow the window language)."""
    i = Info
    return {
        "max_pads": i(_("Największa liczba padów"), "", 2, 0,
                      _("Ile padów nadajnik obsługuje naraz (sloty 1-8). Kolejny pad ponad limit jest ignorowany. Zmniejszenie "
                        "nie rozłącza padów, które już grają.")),
        "grab_pad": i(_("Przejmij pada na wyłączność"), "", True, 0,
                      _("Nadajnik przejmuje urządzenie wejściowe pada na wyłączność, więc system nadajnika (np. telefonu) nie "
                        "reaguje na jego przyciski. Wyłącz, jeśli pad ma też sterować samym nadajnikiem.")),
        "grab_touch": i(_("Przejmij panel dotykowy na wyłączność"), "", True, 0,
                        _("To samo dla panelu dotykowego pada: bez tego system nadajnika porusza własnym kursorem.")),
        "idle_off_min": i(_("Wyłącz bezczynnego pada po"), " min", 15.0, 1,
                          _("Po tylu minutach bez dotknięcia gałek i przycisków nadajnik rozłącza pada (Bluetooth), żeby nie "
                            "wyczerpał baterii. 0 = nigdy.")),
        "battery_poll_s": i(_("Odczyt baterii pada co"), " s", 5.0, 1,
                            _("Jak często nadajnik pyta o poziom baterii pada. Rzadziej = mniej ruchu, wolniejsza reakcja ikony "
                              "baterii.")),
        "rescan_s": i(_("Zapasowe skanowanie padów co"), " s", 2.0, 1,
                      _("Nowy pad jest wykrywany od razu (inotify na /dev/input); to skanowanie łapie tylko to, co powiadomienie "
                        "przegapiło.")),
        "stick_deadzone": i(_("Martwa strefa gałek"), "", 3, 0,
                            _("Wychylenie gałki poniżej tej wartości (w jednostkach 0-255 wokół środka) nadajnik wysyła jako "
                              "środek. Duża strefa ukrywa dryf, ale też drobne ruchy. Dotyczy od razu padów, które już grają.")),
        "motion_deadband_gyro": i(_("Próg szumu żyroskopu"), "", 128, 0,
                                  _("Zmiany odczytu żyroskopu mniejsze niż ta wartość nie wywołują nowego pakietu ruchu (nieruchomy "
                                    "pad nie zalewa sieci szumem). 0 = wysyłaj każdą zmianę.")),
        "motion_deadband_acc": i(_("Próg szumu akcelerometru"), "", 16, 0,
                                 _("To samo dla akcelerometru. 0 = wysyłaj każdą zmianę.")),
        "calibrate_s": i(_("Kalibracja żyroskopu przy starcie"), " s", 1.0, 1,
                         _("Jak długo nadajnik mierzy spoczynkowy odczyt żyroskopu, gdy pad się połączy (pad musi leżeć nieruchomo). "
                           "Pad skalibrowany z okna odbiornika pomija ten krok. 0 = bez kalibracji. Dotyczy kolejnego połączenia "
                           "pada.")),
        "calibrate_max_range": i(_("Największy odczyt uznany za spoczynek"), "", 4096, 0,
                                 _("Jeśli podczas kalibracji odczyt żyroskopu jest większy, pad uznany jest za ruszany i kalibracja "
                                   "jest powtarzana. Dotyczy kolejnego połączenia pada.")),
        "autobias": i(_("Automatyczna korekta dryfu żyroskopu"), "", True, 0,
                      _("Gdy pad leży nieruchomo, nadajnik na bieżąco poprawia zerowy odczyt żyroskopu, żeby kamera w grze nie "
                        "uciekała.")),
        "autobias_s": i(_("Czas bezruchu przed korektą"), " s", 1.0, 1,
                        _("Tak długo pad musi leżeć nieruchomo, zanim zacznie się korekta dryfu.")),
        "autobias_range": i(_("Zakres uznawany za bezruch"), "", 1024, 0,
                            _("Odczyty żyroskopu w tym zakresie liczą się jako bezruch dla korekty dryfu.")),
        "heartbeat_s": i(_("Pakiet podtrzymujący co"), " s", 0.05, 3,
                         _("Gdy nic się nie zmienia, nadajnik i tak wysyła stan pada co tyle sekund: odbiornik wie, że pad żyje, "
                           "a zgubiony pakiet jest szybko nadrabiany. Mniej = pewniej, więcej ruchu sieciowego.")),
        "pad_min_dt": i(_("Najmniejszy odstęp pakietów stanu"), " s", 0.001, 4,
                        _("Najczęściej co tyle sekund idzie pakiet stanu (0,001 s = 1000 Hz). Pierwsza zmiana po ciszy wychodzi od "
                          "razu, zmiany w oknie łączą się w jeden pakiet. Większy odstęp oszczędza sieć i procesor kosztem opóźnienia "
                          "gałek. 0 = bez limitu.")),
        "motion_min_dt": i(_("Najmniejszy odstęp pakietów ruchu"), " s", 0.001, 4,
                           _("To samo dla żyroskopu i akcelerometru; próbki z okna są uśredniane. 0 = bez limitu.")),
        "tap_click": i(_("Stuknięcie w panel = klik"), "", True, 0,
                       _("Krótkie stuknięcie palcem w panel dotykowy jest klikiem myszy (gdy panel steruje myszą).")),
        "tap_max_s": i(_("Najdłuższe stuknięcie"), " s", 0.2, 2,
                       _("Dłuższy dotyk nie jest stuknięciem, tylko przytrzymaniem.")),
        "tap_max_move": i(_("Największy ruch podczas stuknięcia"), "", 40, 0,
                          _("Dotyk, w którym palec przesunął się dalej (w jednostkach panelu 1920x943), nie jest stuknięciem.")),
        "touch_coalesce_ms": i(_("Łączenie dotyku (DS4 / DSU)"), " ms", 2, 0,
                               _("Dotyk dla pada DS4 i serwera DSU wychodzi najwyżej raz na tyle milisekund. 0 = każda zmiana.")),
        "touch_deadzone": i(_("Próg szumu dotyku"), "", 2, 0,
                            _("Zmiany położenia palca mniejsze niż ta wartość są ignorowane (szum czujnika).")),
        "touch_start_move": i(_("Ruch przed startem kursora"), "", 5, 0,
                              _("Kursor myszy rusza dopiero po tak dużym przesunięciu palca, więc stuknięcie nie drży.")),
        "gestures": i(_("Gesty dwoma palcami"), "", True, 0,
                      _("Dwa palce = przewijanie, stuknięcie dwoma palcami = prawy przycisk myszy.")),
        "rumble": i(_("Wibracje"), "", "auto", 0,
                    _("Którędy nadajnik wysyła wibracje do pada: auto = sterownik evdev i raport HID naraz, evdev albo hidraw "
                      "osobno, off = wyłączone. Dotyczy kolejnego połączenia pada.")),
        "haptics_motors": i(_("Wibracje podczas haptyki"), "", "haptics", 0,
                            _("Gdy gra gra haptykę i jednocześnie wibruje silnikami: haptyka ma pierwszeństwo (silniki czekają) "
                              "albo wibracja (haptyka pauzuje). Dotyczy kolejnego połączenia pada.")),
        "haptics_buffer_ms": i(_("Bufor haptyki"), " ms", 20, 0,
                               _("Bufor przed padem (Bluetooth), 10-200 ms. Większy = mniej przerw w haptyce, większe opóźnienie. "
                                 "Zmiana działa od następnego strumienia haptyki.")),
        "speaker_buffer_ms": i(_("Bufor głośnika pada"), " ms", 30, 0,
                               _("Bufor przed głośnikiem pada, 20-200 ms. Większy = mniej przerw, większe opóźnienie. Zmiana "
                                 "działa od następnego strumienia dźwięku.")),
        "speaker_volume": i(_("Głośność głośnika pada"), "", 100, 0,
                            _("Głośność sprzętowa głośnika pada, 0-255 (osobno od siły dźwięku z odbiornika). Zmiana działa od "
                              "następnego strumienia dźwięku.")),
        "speaker_opus_bytes": i(_("Bajtów Opus na ramkę 10 ms"), "", 80, 0,
                                _("Jakość dźwięku wysyłanego na głośnik: 80 = 64 kbit/s. Więcej = lepszy dźwięk i więcej ruchu "
                                  "Bluetooth. Zmiana działa od następnego strumienia dźwięku.")),
        "speaker_opus_mode": i(_("Tryb Opus"), "", "hybrid", 0,
                               _("hybrid daje czysty głos na padzie; celt to stary tryb (spadek poziomu na brzegu każdej ramki, "
                                 "robotyczny głos). Zmiana działa od następnego strumienia dźwięku.")),
        "speaker_opus_complexity": i(_("Wysiłek kodera Opus"), "", -1, 0,
                                     _("0-10: niżej = mniej procesora w nadajniku (np. w telefonie), -1 = automatycznie. Zmiana "
                                       "działa od następnego strumienia dźwięku.")),
        "rt_priority": i(_("Priorytet czasu rzeczywistego"), "", 10, 0,
                         _("Nadajnik działa w SCHED_FIFO z tym priorytetem (1-99); 0 = zwykły planista. Wymaga uprawnień roota "
                           "na nadajniku; bez nich zmiana jest zapisana, ale nie działa.")),
        "low_latency": i(_("Tryb małego opóźnienia"), "", True, 0,
                         _("Bez głębokiego uśpienia procesora i na najszybszych rdzeniach (wymaga roota na nadajniku).")),
        "cpu_latency_us": i(_("Najdłuższe wybudzenie procesora"), " µs", 50, 0,
                            _("Dla trybu małego opóźnienia: ile najwyżej µs może trwać wybudzenie procesora (głębokie stany "
                              "uśpienia potrafią do ok. 6,5 ms).")),
    }


class _Row:
    """One setting: a title, the control, the badge ("changed", "from the next stream"), a reset button and the description."""

    def __init__(self, name: str, info: Info, panel: SenderCfgPanel) -> None:
        """Build the row's widgets for the setting ``name``."""
        self.name, self.spec, self.info = name, cfgspec.SPECS[cfgspec.BY_KEY[name]], info
        self.panel = panel
        self.control = self._make_control()
        self.badge = label("", "dim", wrap=True)
        self.reset = button(_("Z pliku konfiguracji"), small=True)
        self.reset.clicked.connect(lambda: panel.reset(name))
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(label(info.title, wrap=True), 3)
        top.addWidget(self.control, 2)
        top.addWidget(self.reset)
        self.widget = QWidget()
        lay = QVBoxLayout(self.widget)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(2)
        lay.addLayout(top)
        lay.addWidget(label(info.text, "dim", wrap=True))
        lay.addWidget(self.badge)

    def _make_control(self) -> QWidget:
        """The control for the setting's kind: a switch, a whole-number field, a decimal field or a list."""
        spec, info = self.spec, self.info
        control: QWidget
        if spec.kind == cfgspec.KIND_BOOL:
            control = QCheckBox()
            control.toggled.connect(self._edited)
        elif spec.kind == cfgspec.KIND_CHOICE:
            names = _choices()[self.name]
            control = Combo([(c, names[c]) for c in spec.choices])
            control.changed.connect(self._edited)
        elif spec.kind == cfgspec.KIND_INT:
            control = spinbox(int(spec.lo), int(spec.hi), 1, info.unit)
            control.valueChanged.connect(self._edited)
        else:
            control = QDoubleSpinBox()
            control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            control.setDecimals(info.decimals)
            control.setRange(spec.lo, spec.hi)
            control.setSingleStep(10.0 ** -max(info.decimals - 1, 0))
            control.setSuffix(info.unit)
            control.valueChanged.connect(self._edited)
        return control

    def value(self) -> cfgspec.Value:
        """The value shown in the control."""
        c = self.control
        if isinstance(c, QCheckBox):
            return c.isChecked()
        if isinstance(c, Combo):
            return str(c.currentData())
        if isinstance(c, QSpinBox):
            return c.value()
        assert isinstance(c, QDoubleSpinBox)
        return round(c.value(), self.info.decimals)

    def show(self, value: cfgspec.Value) -> None:
        """Put ``value`` into the control without raising an edit."""
        c = self.control
        c.blockSignals(True)
        if isinstance(c, QCheckBox):
            c.setChecked(bool(value))
        elif isinstance(c, Combo):
            c.set_data(value)
        elif isinstance(c, QSpinBox):
            c.setValue(int(value))
        elif isinstance(c, QDoubleSpinBox):
            c.setValue(float(value))
        c.blockSignals(False)

    def _edited(self, *_args: Any) -> None:
        """The user changed the control."""
        self.panel.edited(self.name, self.value())


class SenderCfgPanel:
    """The Sender tab: ``sections()`` for the page, ``load`` / ``collect`` for the settings window, ``refresh`` on a timer."""

    def __init__(self) -> None:
        """Build the rows; nothing is loaded yet."""
        self.pending: dict[str, cfgspec.Value] = {}
        self.rows = {name: _Row(name, info, self) for name, info in infos().items()}
        self.status = label("", wrap=True)
        self.refresh_btn = button(_("Odczytaj z nadajnika"), small=True)
        self.refresh_btn.clicked.connect(self._reread)
        self.reset_all_btn = button(_("Wszystko z pliku konfiguracji"), small=True)
        self.reset_all_btn.clicked.connect(self.reset)
        self._gen = -1
        self.timer = QTimer()
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)

    def sections(self) -> list[QWidget]:
        """The page: the status box and one section per group of settings."""
        bar = QHBoxLayout()
        bar.addWidget(self.refresh_btn)
        bar.addWidget(self.reset_all_btn)
        bar.addStretch(1)
        head = group(_("USTAWIENIA NADAJNIKA"),
                     label(_("Zmiany stosują się na nadajniku od razu, bez restartu, i zostają po jego restarcie (plik "
                             "remote_cfg.json na nadajniku; sender.toml nie jest ruszany). Wymagają klucza HMAC po obu stronach "
                             "i protokołu v3. Tu są tylko ustawienia, których nie ma w innych zakładkach; adres, port, klucz i "
                             "ścieżki plików ustawia się wyłącznie na nadajniku."), "dim", wrap=True),
                     self.status, bar)
        out: list[QWidget] = [head]
        for title, names in group_titles():
            out.append(group(title, *(self.rows[n].widget for n in names)))
        return out

    def load(self, cfg: rx_settings.SenderConfig) -> None:
        """Fill the tab from the stored choices."""
        self.pending = dict(cfg.values)
        self._gen = -1
        self.refresh()
        self.timer.start()

    def collect(self) -> rx_settings.SenderConfig:
        """The choices made in the tab."""
        return rx_settings.SenderConfig(values=dict(self.pending))

    def stop(self) -> None:
        """Stop refreshing (the window closes)."""
        self.timer.stop()

    def edited(self, name: str, value: cfgspec.Value) -> None:
        """The user set a control: keep it, or drop the entry when it equals the sender.toml value."""
        row = S.sender_cfg.get(name)
        if row is not None and row.base == value:
            self.pending.pop(name, None)
        else:
            self.pending[name] = value
        self._badge(name)

    def reset(self, name: str | None = None) -> None:
        """Drop the choice for one setting (``None``: all) - the control shows the sender.toml value again."""
        for n in ([name] if name else list(self.pending)):
            self.pending.pop(n, None)
            self._show_current(n)
            self._badge(n)

    def _reread(self) -> None:
        """Ask the sender for its values again."""
        S.sender_cfg.clear()
        S.sender_cfg_result = None
        rx_sender_cfg.request_all()
        self._gen = -1
        self.refresh()

    def _show_current(self, name: str) -> None:
        """Show the value in force: the user's choice, else what the sender reports, else the built-in default."""
        row = S.sender_cfg.get(name)
        if name in self.pending:
            value = self.pending[name]
        else:
            value = row.value if row is not None else self.rows[name].info.default
        self.rows[name].show(value)

    def _badge(self, name: str) -> None:
        """The line under a row: the sender.toml value, whether a change is waiting, when it will count."""
        r, row = self.rows[name], S.sender_cfg.get(name)
        parts: list[str] = []
        if row is not None:
            parts.append(_("w pliku nadajnika: {v}").format(v=self._text(name, row.base)))
        if name in self.pending:
            parts.append(_("zmienione tutaj - zapisz, żeby wysłać") if row is None or row.value != self.pending[name]
                         else _("zmienione tutaj"))
            when = r.spec.when
            if when == cfgspec.WHEN_STREAM:
                parts.append(_("zadziała od następnego strumienia dźwięku"))
            elif when == cfgspec.WHEN_PAD:
                parts.append(_("zadziała po ponownym połączeniu pada"))
            else:
                parts.append(_("zadziała od razu"))
        r.badge.setText(" · ".join(parts))
        r.reset.setVisible(name in self.pending or (row is not None and row.remote))

    def _text(self, name: str, value: cfgspec.Value) -> str:
        """A value for people: the name of a choice, yes / no for a switch, the number with its unit."""
        spec, info = self.rows[name].spec, self.rows[name].info
        if spec.kind == cfgspec.KIND_BOOL:
            return _("tak") if value else _("nie")
        if spec.kind == cfgspec.KIND_CHOICE:
            return _choices()[name].get(str(value), str(value))
        return f"{value:g}{info.unit}"

    def _state_text(self) -> tuple[str, str]:
        """The status line and its colour role: why changes are impossible, or how many settings the sender reported."""
        result = S.sender_cfg_result
        if not S.sender_online():
            return _("Nadajnik offline - ustawienia zostaną wysłane, gdy wróci."), ""
        if not rx_sender_cfg.can_send():
            return _("Zmiana ustawień nadajnika wymaga klucza HMAC w odbiorniku i protokołu v3 (klucz w obu miejscach, "
                     "ten sam)."), "warn"
        refusals = {CFGR_NO_KEY: _("Nadajnik nie ma klucza HMAC, więc odmawia zmian."),
                    CFGR_NO_V3: _("Nadajnik nie przeszedł jeszcze na protokół v3 i odmawia zmian."),
                    CFGR_OFF: _("Nadajnik ma wyłączone zmiany z odbiornika (remote_cfg_file = \"\")."),
                    }
        if result in refusals:
            return refusals[result], "warn"
        if not S.sender_cfg:
            return _("Brak odpowiedzi nadajnika - starsza wersja nie zna tych pakietów, albo odpowiedź jeszcze nie dotarła."), ""
        total = len(cfgspec.SPECS)
        return _("Nadajnik zgłosił {n} z {total} ustawień.").format(n=len(S.sender_cfg), total=total), ""

    def refresh(self) -> None:
        """Bring the rows in line with what the sender reported (only when something arrived) and update the status line."""
        text, role = self._state_text()
        self.status.setText(text)
        set_prop(self.status, "level", role)
        gen = S.sender_cfg_gen
        if gen == self._gen:
            return
        self._gen = gen
        for name in self.rows:
            self._show_current(name)
            self._badge(name)
        self.refresh_btn.setEnabled(rx_sender_cfg.can_send())

