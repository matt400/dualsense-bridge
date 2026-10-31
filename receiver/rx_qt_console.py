"""Receiver: the settings window (tray -> Settings, or a button in the window header).

Nine tabs, in a column on the left (horizontal text, one under another):

  Console mode  the pad button is chosen by pressing it on the pad (rx_console.arm_capture), the audio devices are a list of
                active Windows outputs (rx_audio, Refresh); "Test" switches the console mode on for a few seconds
                with the values from the window (not saved yet) and returns to extend by itself
  Mouse         touchpad cursor sensitivity and click stabilisation (rx_settings.MouseConfig)
  Master        the master pad, features only for it, "PS + button" actions (rx_settings.MasterConfig, rx_master)
  Lightbar      the colour when a game set nothing (solid / by battery) and low battery blinking - per pad (MAC)
                or the default for all; the low battery threshold (the same drives the warning on the pad card)
  Wake-on-LAN   PS + button held on the pad when the receiver does not answer -> the sender wakes the PC; the MAC
                and broadcast typed in or automatic (detected by the sender - shown here)
  Pads          which pad cards the window shows (only connected / a fixed number, rx_settings.UiConfig); virtual pads:
                HIDMaestro (DualSense) / ViGEmBus (DS4), the path to HIDMaestro.Core.dll, the fixed order of players
                (rx_settings.VpadConfig, rx_ds4); without administrator rights - a restart as administrator
  Window        the screen the window appears on (receiver_ui.ini, MainWindow.place_on_screen)
  Backups       copies of settings.json / console.json / remap.json from updates (rx_backup): restore (two clicks,
                then a receiver restart) or make a copy now
  Security      the HMAC key and the hardening of the receiver folder (rx_security)

"Save": mouse, master, lightbar, Wake-on-LAN and pads take effect at once (settings.json, no restart). A console mode change
saves console.json and asks the window owner to restart the receiver (the saved signal) - after the restart
startup_mode applies as with autostart.
"""
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any

from rx_i18n import _
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QHideEvent, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QDialog, QGridLayout, QHBoxLayout, QLayout, QLineEdit,
                               QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget)

import rx_audio
import rx_backup
import rx_console
import rx_ds4
import rx_haptics
import rx_speaker
import rx_hm
import rx_hm_update
import rx_lightbar
import rx_security
import rx_update
import rx_settings
import rx_state as S
from rx_backup import Backup
from rx_buttons import LABELS
from rx_config import C
from rx_console import DEFAULT_APPS, STARTUP_MODES, ConsoleConfig
from rx_display import DPI_VALUES
from rx_i18n import LANGS
from rx_qt_icons import icon
from rx_qt_screens import parse_size
from rx_qt_sendercfg import SenderCfgPanel
from rx_qt_style import set_prop
from rx_qt_widgets import (Combo, ConfirmButton, SideTabs, SliderRow, button, field, group, icon_button, icon_label, input_items,
                           label, left, pair, spinbox)
from rx_settings import (ABSENT, COMBO_BUTTONS, DEFAULT_PAD, HAPTICS_GAIN, LIGHT_MODES, LOW_PCT, MASTER_ACTIONS, MASTER_FEATURES,
                         MOUSE_LIMITS, PADS_COUNT, SPEAKER_GAIN, VPAD_BACKENDS, WOL_BUTTONS, WOL_HOLD_MS, HapticsConfig, LightbarConfig,
                         MasterConfig, MouseConfig, PadHaptics, PadLight, PadSpeaker, SpeakerConfig, UiConfig, VpadConfig, WolConfig)

CAPTURE_TIMEOUT_MS = 15000
LUMA_DARK_TEXT = 380      # R + G + B above this (of 765) is a light colour: the button text turns black
NO_AUDIO = "— nie zmieniaj dźwięku —"
TV_RESOLUTIONS = ((0, 0), (1920, 1080), (2560, 1440), (3840, 2160))
WINDOW_RESOLUTIONS = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440))   # presets for the window size
STARTUP_LABELS = {"extend": "Rozszerz: oba ekrany aktywne", "internal": "Tylko monitor"}
PREF_SIZE = QSize(1060, 780)  # the settings window size on a big screen (a smaller screen -> a smaller one, with scrolling)
FRAME_GUESS = QSize(16, 40)   # the Windows frame and title bar, before the window is shown
FIT_MARGIN = 24
RESTORE_RESTART_MS = 1500     # after restoring a backup: the message is visible this long, then the receiver restarts


class ConsoleSettings(QDialog):
    """The settings dialog: tabs built by the _*_tab methods, the Test / Cancel / Save row and the status line."""
    saved = Signal()            # console.json saved - the window owner restarts the receiver
    elevate = Signal()          # Pads: restart the receiver as administrator (HIDMaestro)
    applied = Signal()          # mouse / master saved and in effect (no restart) - the window refreshes the pad cards
    captured = Signal(str)      # the button name from the network thread (Qt queues it to the window thread)
    outputs_listed = Signal(object)     # (generation, tv, pc, names, error) from the audio listing thread

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the dialog: the capture timer (a pad button pick times out), the captured signal hookup and the tabs."""
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle(_("Ustawienia"))
        self.setMinimumWidth(720)
        self.resize(PREF_SIZE)
        self.toggle_button = ConsoleConfig().toggle_button
        self.captured.connect(self._on_captured)
        self._cap_timer = QTimer(self, singleShot=True, interval=CAPTURE_TIMEOUT_MS)
        self._cap_timer.timeout.connect(self._stop_capture)
        self._build()

    # ---------- building ----------
    def _build(self) -> None:
        """Build the tab widget (one page per settings area) and the bottom row: Test (console tab only), the status, Cancel and Save."""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(8)
        self.tabs = SideTabs()
        self.tabs.addTab(_page(self._console_tab()), icon("tv"), _("Tryb konsoli"))
        self.tabs.addTab(_page(self._mouse_tab()), icon("mouse"), _("Mysz"))
        self.tabs.addTab(_page(self._master_tab()), icon("crown"), _("Master"))
        self.tabs.addTab(_page(self._light_tab()), icon("lightbar"), _("Lightbar"))
        self.tabs.addTab(_page(self._haptics_tab()), icon("rumble"), _("Haptyka i głośnik"))
        self.wol_page = _page(self._wol_tab())
        self.tabs.addTab(self.wol_page, icon("wake"), _("Wake-on-LAN"))
        self.tabs.addTab(_page(self._vpad_tab()), icon("ps"), _("Pady"))
        self.sender_cfg = SenderCfgPanel()
        self.tabs.addTab(_page(self.sender_cfg.sections()), icon("phone"), _("Nadajnik"))
        self.tabs.addTab(_page(self._window_tab()), icon("window"), _("Okno"))
        self.tabs.addTab(_page(self._backup_tab()), icon("backup"), _("Kopie"))
        self.tabs.addTab(_page(self._security_tab()), icon("shield"), _("Bezpieczeństwo"))
        lay.addWidget(self.tabs, 1)

        self.status = label("", wrap=True)      # in the button row - a separate line left an empty strip above Save
        self.test_btn = button(_("Przetestuj"))
        self.test_btn.setIcon(icon("tv"))
        self.test_btn.setToolTip(_("Włącza tryb konsoli na kilka sekund z wartościami z tego okna, potem wraca do trybu rozszerzonego"))
        self.test_btn.clicked.connect(self._test)
        cancel = button(_("Anuluj"))
        cancel.clicked.connect(self.close)
        self.save_btn = button(_("Zapisz"), primary=True)
        self.save_btn.clicked.connect(self._save)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.test_btn)
        row.addWidget(self.status, 1)
        row.addWidget(cancel)
        row.addWidget(self.save_btn)
        lay.addLayout(row)
        self.tabs.currentChanged.connect(lambda i: self.test_btn.setVisible(i == 0))

    def _console_tab(self) -> list[QWidget | QLayout]:
        """The Console mode tab.

        The toggle button pick, the double click interval, auto-OFF, auto-ON, the TV / audio, and the program list that
        blocks switching in a game.
        """
        self.pick = button("", small=True)
        self.pick.clicked.connect(self._toggle_capture)
        self.pick_icon = label("")
        self.pick_name = label("", "val")
        pick = QHBoxLayout()
        pick.setSpacing(8)
        pick.addWidget(self.pick_icon)
        pick.addWidget(self.pick_name, 1)
        pick.addWidget(self.pick)
        self.dbl = spinbox(rx_console.MIN_DOUBLE_CLICK_MS, 2000, 50, " ms")
        self.timeout = spinbox(5, 3600, 5, " s")
        self.auto_on = QCheckBox(_("Włącz po połączeniu pada master (bez mastera: pierwszego pada)"))
        self.auto_on.setIcon(icon("crown"))
        switching = group(_("PRZEŁĄCZANIE"),
                          label(_("Podwójne kliknięcie wybranego przycisku pada przełącza ekran i dźwięk między monitorem "
                                  "a telewizorem. Brak sygnału z pada przez ustawiony czas wraca do trybu rozszerzonego (oba ekrany)."),
                                "dim", wrap=True),
                          field(_("Przycisk pada (podwójne kliknięcie)"), _wrap(pick)),
                          pair(field(_("Odstęp między kliknięciami"), self.dbl),
                               field(_("Auto-OFF po braku sygnału z pada"), self.timeout)),
                          self.auto_on,
                          label(_("Wyłączasz go tym samym podwójnym kliknięciem; zostaje wyłączony do następnego połączenia mastera."),
                                "dim", wrap=True))

        self.tv_res = Combo(_res_items(TV_RESOLUTIONS))
        self.tv_scale = Combo([(v, f"{v} %" if v else _("— bez zmiany —")) for v in (0, *DPI_VALUES)])
        self.tv, self.pc = Combo(), Combo()
        refresh = button(_("Odśwież listę urządzeń"), small=True)
        refresh.clicked.connect(lambda: self.refresh_async())     # clicked passes `checked` - not as a device
        self.startup = Combo([(m, _(STARTUP_LABELS[m])) for m in STARTUP_MODES])
        tv = group(_("TELEWIZOR I DŹWIĘK"),
                   pair(field(_("Rozdzielczość TV po włączeniu"), self.tv_res), field(_("Skalowanie TV"), self.tv_scale)),
                   field(_("Dźwięk po włączeniu (TV)"), self.tv),
                   field(_("Dźwięk po wyłączeniu (komputer)"), self.pc),
                   left(refresh),
                   field(_("Ekran przy starcie receivera"), self.startup))

        self.apps = QPlainTextEdit()
        self.apps.setFixedHeight(120)
        apps_reset = button(_("Przywróć domyślną listę"), small=True)
        apps_reset.clicked.connect(lambda: self.apps.setPlainText("\n".join(DEFAULT_APPS)))
        games = group(_("BLOKADA W GRZE"),
                      label(_("Przełączanie działa tylko, gdy na pierwszym planie jest jeden z tych programów (jeden w linii) - "
                              "w grze podwójne kliknięcie nic nie robi, a włączenie po połączeniu mastera czeka na wyjście z gry. "
                              "Pusta lista = bez blokady."),
                            "dim", wrap=True),
                      field(_("Dozwolone programy (pulpit, Steam, launchery)"), self.apps),
                      left(apps_reset))
        note = label(_("Zmiana trybu konsoli lub języka uruchamia receiver od nowa (pady na chwilę znikną). Niezapisane "
                       "zmiany profilu (Zapisz w oknie głównym) zostaną utracone."), "dim", wrap=True)
        return [switching, tv, games, note]

    def _mouse_tab(self) -> list[QWidget | QLayout]:
        """The Mouse tab: cursor sensitivity and the click stabilisation sliders (ranges shared with settings.json)."""
        lim = MOUSE_LIMITS                  # the same ranges as settings.json - saving does not clip values
        self.sens = SliderRow(_("Czułość kursora"), *lim["sens"], 0.05, "x")
        self.freeze = SliderRow(_("Kursor stoi po wciśnięciu i puszczeniu kliku [ms]"), *lim["click_freeze_ms"], 10)
        self.rewind = SliderRow(_("Cofnij ruch sprzed kliknięcia [ms] (docisk palca przesuwa kursor)"), *lim["click_rewind_ms"], 5)
        self.drag = SliderRow(_("Przeciąganie z wciśniętym klikiem zaczyna się po (jednostki touchpada)"), *lim["drag_threshold"], 5)
        dflt = button(_("Domyślne"), small=True)
        dflt.clicked.connect(lambda: self._load_mouse(MouseConfig(sens=C.MOUSE_SENS)))
        cursor = group(_("KURSOR"),
                       label(_("Touchpad jako mysz (PS + klik touchpada przełącza). Zmiany działają od razu po zapisie."),
                             "dim", wrap=True),
                       self.sens)
        click = group(_("KLIKNIĘCIE"), self.freeze, self.rewind, self.drag,
                      label(_("0 ms wyłącza daną poprawkę. Gdy kursor wciąż ucieka przy kliknięciu - zwiększ oba czasy; "
                              "gdy przeciąganie (np. okna) rusza za późno - zmniejsz ostatni suwak."), "dim", wrap=True))
        return [cursor, click, left(dflt)]

    def _window_tab(self) -> list[QWidget | QLayout]:
        """The Window tab: the screen to open on, window / full screen, and the language."""
        self.screen_box = Combo()
        self.win_mode = Combo([("maximized", _("Okno na cały ekran roboczy"), "window"),
                               ("size", _("Okno o wybranej rozdzielczości"), "window"),
                               ("full", _("Pełny ekran (F11)"), "fullscreen")])
        self.win_mode.changed.connect(lambda _v: self._window_state())
        self.win_res = Combo(_res_items(WINDOW_RESOLUTIONS))
        self.lang = Combo(list(LANGS.items()))
        window = group(_("OKNO"),
                       field(_("Ekran, na którym pojawia się okno DualSense bridge"), self.screen_box),
                       label(_("Okno otwiera się (z ikony w zasobniku, przy starcie) na środku wybranego ekranu. Gdy tego "
                               "ekranu nie ma (np. monitor wyłączony w trybie konsoli), okno zostaje na dostępnym ekranie."),
                             "dim", wrap=True),
                       field(_("Wygląd okna"), self.win_mode),
                       field(_("Rozdzielczość okna"), self.win_res))
        lang = group(_("JĘZYK"), field(_("Język"), self.lang),
                     label(_("Zmiana języka działa po restarcie receivera."), "dim", wrap=True))
        return [window, lang]

    def _load_window(self, owner: Any) -> None:
        """Fill the window mode and resolution from the main window (a size dragged by hand stays on the list)."""
        size = owner.window_size() if hasattr(owner, "window_size") else QSize(*WINDOW_RESOLUTIONS[0])
        res = (size.width(), size.height())
        self.win_res.set_items(_res_items((*WINDOW_RESOLUTIONS, *(() if res in WINDOW_RESOLUTIONS else (res,)))))
        self.win_res.set_data("{}x{}".format(*res))
        if getattr(owner, "fullscreen", lambda: False)():
            self.win_mode.set_data("full")
        else:
            self.win_mode.set_data(owner.window_mode() if hasattr(owner, "window_mode") else "maximized")
        self._window_state()

    def _chosen_size(self) -> QSize:
        """The resolution picked in the list."""
        size = parse_size(self.win_res.currentData())
        return size or QSize(*WINDOW_RESOLUTIONS[0])

    def _window_state(self) -> None:
        """The resolution list matters only for the fixed-size window mode."""
        self.win_res.setEnabled(self.win_mode.currentData() == "size")

    def _vpad_tab(self) -> list[QWidget | QLayout]:
        """The Pads tab: the backend, HIDMaestro download / path, the current state and the fixed player order."""
        self.vpad_backend = Combo([(k, _(v)) for k, v in VPAD_BACKENDS.items()])
        self.vpad_backend.changed.connect(lambda _v: self._vpad_state())
        self.vpad_dll = QLineEdit()
        self.vpad_dll.setPlaceholderText(_("automatycznie"))
        self.vpad_now = label("", "dim", wrap=True)
        self.vpad_admin = button(_("Uruchom ponownie jako administrator"), small=True)
        self.vpad_admin.setIcon(icon("shield"))
        self.vpad_admin.clicked.connect(self.elevate.emit)
        self.vpad_fixed = QCheckBox(_("Stała kolejność graczy"))
        self.vpad_auto = QCheckBox(_("Pobieraj i aktualizuj HIDMaestro automatycznie (GitHub, raz na dobę)"))
        self.vpad_auto.toggled.connect(lambda _on: self._vpad_state())
        self.vpad_check = button(_("Sprawdź teraz"), small=True)
        self.vpad_check.setIcon(icon("update"))
        self.vpad_check.clicked.connect(lambda: self._hm_check())
        self.vpad_skip = button(_("Zainstaluj teraz"), small=True)       # shown only while a newer release is waiting
        self.vpad_skip.setIcon(icon("update"))
        self.vpad_skip.clicked.connect(lambda: self._hm_check(skip_wait=True))
        self.vpad_net = button(_("Zainstaluj .NET 10"), small=True)
        self.vpad_net.setIcon(icon("update"))
        self.vpad_net.clicked.connect(self._net_install)
        self._net_busy = False
        self._net_msg = ""
        self._audio_gen = 0         # the newest audio listing; an older one that finishes late is dropped
        self.outputs_listed.connect(self._outputs_ready)
        self._hm_timer = QTimer(self, interval=500)
        self._hm_timer.timeout.connect(self._vpad_state)
        backend = group(_("WIRTUALNE PADY"),
                        label(_("HIDMaestro tworzy natywny DualSense (gry widzą pada PS5) sterownikiem w trybie użytkownika - "
                                "jest rozwijany. Receiver pobiera go sam i uruchamia się jako administrator (tego wymaga "
                                "tworzenie padów). ViGEmBus (DualShock 4) nie jest już rozwijany, zostaje jako zapas."),
                              "dim", wrap=True),
                        field(_("Backend"), self.vpad_backend),
                        self.vpad_auto,
                        field(_("Własna HIDMaestro.Core.dll (plik albo folder; puste = pobrana automatycznie)"), self.vpad_dll),
                        self.vpad_now, row_of(self.vpad_check, self.vpad_skip, self.vpad_net, self.vpad_admin))
        self.pads_mode = Combo([("dynamic", _("Dynamiczna: tylko podłączone pady")), ("fixed", _("Stała: zawsze te same sloty"))])
        self.pads_mode.changed.connect(lambda _v: self._pads_view_state())
        self.pads_count = spinbox(PADS_COUNT[0], min(PADS_COUNT[1], C.NUM_PADS))
        self.pads_count_row = field(_("Liczba padów, które są zawsze widoczne"), self.pads_count)
        shown = group(_("PADY W OKNIE"),
                      label(_("Dynamiczna: kolumna po lewej pokazuje tylko podłączone pady. Stała: zawsze widać pierwsze pady "
                              "z listy (także rozłączone); podłączony pad spoza tej liczby też się pojawia. Działa od razu po "
                              "zapisaniu."), "dim", wrap=True),
                      field(_("Liczba padów"), self.pads_mode),
                      self.pads_count_row)
        if PADS_COUNT[1] > C.NUM_PADS:
            shown.layout().addWidget(label(_("Liczba slotów w receiverze: {n} (opcja pads w receiver.toml albo --pads) - "
                                             "więcej wymaga zmiany tej wartości i restartu.").format(n=C.NUM_PADS), "dim", wrap=True))
        order = group(_("KOLEJNOŚĆ GRACZY"),
                      self.vpad_fixed,
                      label(_("Gry i Steam numerują pady wg kolejności podłączenia. Gdy pad 2 włączy się pierwszy, pad 1 "
                              "dostaje najpierw pad zastępczy, więc pad 2 zawsze zostaje graczem 2. Który fizyczny pad to "
                              "pad 1 / 2, pamięta nadajnik (slots.json, wg MAC). Zastępczy pad znika razem z padem 2."),
                            "dim", wrap=True))
        return [shown, backend, order]

    def _load_vpad(self, v: VpadConfig) -> None:
        """Fill the Pads tab from a VpadConfig."""
        self.vpad_backend.set_data(v.backend)
        self.vpad_dll.setText(v.hm_dll)
        self.vpad_fixed.setChecked(v.fixed_slots)
        self.vpad_auto.setChecked(v.hm_auto)
        self._vpad_state()

    def _load_pads_view(self, ui: UiConfig) -> None:
        """Fill the "pads in the window" group from the UI settings (the count field only matters for the fixed mode)."""
        self.pads_mode.set_data(ui.pads_mode)
        self.pads_count.setValue(ui.pads_count)
        self._pads_view_state()

    def _pads_view_state(self) -> None:
        """The number of pads is asked for only in the fixed mode."""
        self.pads_count_row.setVisible(self.pads_mode.currentData() == "fixed")

    def _hm_check(self, skip_wait: bool = False) -> None:
        """Check and possibly download in the background (up to several minutes - ~56 MB); the state is refreshed every 0.5 s.

        skip_wait ("Install now") installs a release that is still inside the security wait.
        """
        import threading
        threading.Thread(target=rx_hm_update.check, kwargs={"force": True, "skip_wait": skip_wait},
                         name="hidmaestro check", daemon=True).start()
        self._hm_timer.start()
        self._vpad_state(busy=True)

    def _net_install(self) -> None:
        """.NET 10 through winget in the background (several minutes); after the install HIDMaestro works from the next pad connection."""
        import threading

        def run() -> None:
            ok, msg = rx_hm.install_dotnet()
            self._net_msg = _("✓ .NET 10 zainstalowany - HIDMaestro zadziała przy następnym połączeniu pada") if ok \
                else _("Instalacja .NET 10 nie udała się: {err}").format(err=msg)
            self._net_busy = False
        self._net_busy, self._net_msg = True, ""
        threading.Thread(target=run, name="dotnet install", daemon=True).start()
        self._hm_timer.start()
        self._vpad_state()

    def _vpad_state(self, busy: bool = False) -> None:
        """What works now: the kind of plugged pads.

        The HIDMaestro version (downloaded / newest / downloading / error), the reason for working without HIDMaestro, the
        restart-as-administrator button.
        """
        backend = self.vpad_backend.currentData()
        used = sorted({rx_ds4.LABELS[k] for k in rx_ds4.kinds if k})
        lines = [_("Teraz: {kinds}").format(kinds=", ".join(used)) if used else _("Teraz: żaden wirtualny pad nie jest podłączony")]
        st = rx_hm_update.refresh_status() if not rx_hm_update.status.busy else rx_hm_update.status
        busy = busy or st.busy
        waiting = False                           # a newer release is held back by the security wait
        if busy:
            lines.append(_("HIDMaestro: sprawdzanie / pobieranie…"))
        elif st.version:
            newer = st.latest and rx_hm_update.version_tuple(st.latest) > rx_hm_update.version_tuple(st.version)
            lines.append(_("HIDMaestro {v} pobrany").format(v=st.version)
                         + (_(" (na GitHubie jest {v})").format(v=st.latest) if newer else ""))
            if newer and st.held_until > time.time():
                waiting = True
                lines.append(_("Wersja {v} zainstaluje się {when} - nowe wydania czekają kilka dni (bezpieczeństwo)").format(
                    v=st.latest, when=time.strftime("%d.%m %H:%M", time.localtime(st.held_until))))
        self.vpad_skip.setVisible(waiting and backend != "vigem")
        if st.error and not busy:
            lines.append(_("Pobieranie HIDMaestro: {err}").format(err=st.error))
        need_net = backend != "vigem" and rx_hm.need_dotnet()
        if self._net_busy:
            lines.append(_("Instalowanie .NET 10 (winget)…"))
        elif self._net_msg:
            lines.append(self._net_msg)
        elif need_net:
            lines.append(_("Brakuje .NET 10 - HIDMaestro go wymaga."))
        self.vpad_net.setVisible(need_net or self._net_busy)
        self.vpad_net.setEnabled(not self._net_busy)
        if not busy and not self._net_busy:
            self._hm_timer.stop()
        self.sender_cfg.stop()
        self.vpad_check.setEnabled(not busy and backend != "vigem")
        self.vpad_skip.setEnabled(not busy)
        if backend != "vigem" and rx_ds4.hm_note:
            lines.append(_("HIDMaestro nieużywany: {why}").format(why=rx_ds4.hm_note))
        need_admin = backend != "vigem" and not rx_hm.is_admin()
        if need_admin:
            lines.append(_("Receiver działa bez uprawnień administratora - HIDMaestro nie utworzy padów."))
        self.vpad_now.setText("\n".join(lines))
        set_prop(self.vpad_now, "level", "warn" if need_admin or (backend == "hidmaestro" and rx_ds4.hm_note) else "")
        self.vpad_admin.setVisible(need_admin)

    def _collect_vpad(self) -> VpadConfig:
        """-> the VpadConfig described by the Pads tab."""
        return VpadConfig(backend=self.vpad_backend.currentData(), fixed_slots=self.vpad_fixed.isChecked(),
                          hm_dll=self.vpad_dll.text().strip(), hm_auto=self.vpad_auto.isChecked())

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

    def _load_screens(self) -> None:
        """Fill the screen choice with the connected screens (and the stored one if it is not connected now)."""
        from PySide6.QtGui import QGuiApplication
        from rx_qt_screens import screens
        owner = self.parent()
        cur = owner.screen_choice() if hasattr(owner, "screen_choice") else ""
        items: list[tuple] = [("", _("Tam, gdzie okno było ostatnio"))]       # (key, label[, icon])
        for n, (key, sc) in enumerate(screens(), 1):
            g = sc.geometry()
            model = " ".join(x for x in (sc.manufacturer(), sc.model()) if x).strip()
            prim = _("  · główny") if sc is QGuiApplication.primaryScreen() else ""
            items.append((key, _("Ekran {n}: {name}  ({w}×{h}){prim}").format(
                n=n, name=model or sc.name() or key, w=g.width(), h=g.height(), prim=prim), "window"))
        if cur and cur not in [it[0] for it in items]:
            items.append((cur, _("{key} (teraz niepodłączony)").format(key=cur), "window"))
        self.screen_box.set_items(items)
        self.screen_box.set_data(cur)

    def _master_tab(self) -> list[QWidget | QLayout]:
        """The Master tab: the master pad, claiming with PS + R3, the master-only features.

        The behaviour without a master and the PS + button actions.
        """
        self.master_pad = Combo()
        self._master_touched = False
        self.master_pad.changed.connect(lambda _v: setattr(self, "_master_touched", True))
        self.claim = QCheckBox(_("Przejęcie na padzie: PS + R3 przez {s} s").format(s=_claim_s()))
        self.claim.setIcon(icon("r3"))
        who = group(_("PAD MASTER"),
                    label(_("Master to jeden pad z dodatkowymi uprawnieniami (np. gdy grają dzieci). Szybka zmiana: korona na karcie "
                            "pada w oknie głównym albo przytrzymanie PS + R3 na padzie."), "dim", wrap=True),
                    field(_("Pad master"), self.master_pad),
                    self.claim)

        self.features = {}
        boxes = []
        for k, (text, ic) in MASTER_FEATURES.items():
            cb = QCheckBox(_(text))
            cb.setIcon(icon(ic))
            self.features[k] = cb
            boxes.append(cb)
        self.absent = Combo([(k, _(v)) for k, v in ABSENT.items()])
        rights = group(_("TYLKO MASTER MOŻE"), *boxes, field(_("Gdy master nie jest podłączony"), self.absent))

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.actions = {}
        for r, (k, (text, hold)) in enumerate(MASTER_ACTIONS.items()):
            combo = Combo([("", "—"), *input_items(COMBO_BUTTONS)])
            combo.setFixedWidth(96)
            self.actions[k] = combo
            grid.addWidget(icon_label("ps"), r, 0)
            grid.addWidget(label("+"), r, 1)
            grid.addWidget(combo, r, 2)
            grid.addWidget(label(_(text) + (_("  (przytrzymaj)") if hold else ""), wrap=True), r, 3)
        grid.setColumnStretch(3, 1)
        actions = group(_("AKCJE: PS + PRZYCISK"), grid,
                        label(_("Przycisk z kombinacji nie trafia do gry. Akcje z dopiskiem \"przytrzymaj\" wymagają trzymania ok. 1 s "
                                "(krótka wibracja = trzymaj dalej). Wyłączenie PC daje 10 s na \"shutdown /a\"."), "dim", wrap=True))
        return [who, rights, actions]

    def _haptics_tab(self) -> list[QWidget | QLayout]:
        """The Haptics tab: game HD haptics and the pad's speaker per pad (opt-in), the strength / volume and the test buttons."""
        self._haptics, self._haptics_key, self._haptics_loading = {}, DEFAULT_PAD, False
        self._speaker: dict[str, PadSpeaker] = {}
        self.hap_pad = Combo()
        self.hap_pad.changed.connect(self._pick_haptics_pad)
        self.hap_own = QCheckBox(_("Własne ustawienie dla tego pada (inaczej domyślne)"))
        self.hap_own.toggled.connect(self._haptics_edited)
        self.hap_on = QCheckBox(_("Haptyka gry (HD haptics) na tym padzie"))
        self.hap_on.toggled.connect(self._haptics_edited)
        self.hap_gain = spinbox(*HAPTICS_GAIN, 5, " %")
        self.hap_gain.valueChanged.connect(self._haptics_edited)
        self.hap_test = button(_("Test haptyki"))
        self.hap_test.clicked.connect(self._test_haptics)
        self.hap_note = label("", "dim", wrap=True)
        self.spk_on = QCheckBox(_("Głośnik pada (dźwięk gry) na tym padzie"))
        self.spk_on.toggled.connect(self._haptics_edited)
        self.spk_gain = spinbox(*SPEAKER_GAIN, 5, " %")
        self.spk_gain.valueChanged.connect(self._haptics_edited)
        self.spk_test = button(_("Test głośnika"))
        self.spk_test.clicked.connect(self._test_speaker)
        self.spk_note = label("", "dim", wrap=True)
        box = group(_("HAPTYKA GRY"),
                    label(_("Gry z haptyką DualSense (dźwięk z kanałów 3-4) wysyłają ją do pada przez nadajnik. Wymaga "
                            "wirtualnego pada USB (profil composite) i nadajnika w wersji z haptyką. Jest wyłączona domyślnie: "
                            "pierwsze włączenie instaluje sterownik jądra (usbip-win2), a Windows dodaje urządzenie dźwiękowe "
                            "„Wireless Controller” (domyślne wyjście dźwięku zostaje bez zmian)."), "dim", wrap=True),
                    field(_("Pad"), self.hap_pad), self.hap_own, self.hap_on,
                    pair(field(_("Siła"), self.hap_gain), field(_("Próba"), self.hap_test)),
                    self.hap_note,
                    label(_("Zmiana włączenia działa po ponownym połączeniu pada; siła - od razu."), "dim", wrap=True))
        speaker = group(_("GŁOŚNIK PADA"),
                        label(_("Dźwięk gry z wyjścia „Wireless Controller” (kanały 1-2) gra na głośniku pada przez nadajnik "
                                "(Bluetooth, kodek Opus). Potrzebuje tego samego wirtualnego pada USB co haptyka, nadajnika w wersji "
                                "z głośnikiem i biblioteki libopus na nadajniku. Jest wyłączony domyślnie. Ustawienia pada wyżej "
                                "(własne lub domyślne) dotyczą też głośnika."), "dim", wrap=True),
                        self.spk_on,
                        pair(field(_("Głośność"), self.spk_gain), field(_("Próba"), self.spk_test)),
                        self.spk_note,
                        label(_("Zmiana włączenia działa po ponownym połączeniu pada; głośność - od razu."), "dim", wrap=True))
        return [box, speaker]

    # ---------- haptics ----------
    def _load_haptics(self, cfg: HapticsConfig, speaker: SpeakerConfig) -> None:
        """Fill the Haptics tab: the pad list (default, connected, remembered) and the first entry's values."""
        self._haptics = dict(cfg.pads)
        self._speaker = dict(speaker.pads)
        items: list[tuple] = [(DEFAULT_PAD, _("Domyślne - pady bez własnego ustawienia"), "rumble")]
        known = {DEFAULT_PAD}
        for i in range(C.NUM_PADS):
            key = S.profile_key(i)
            if key not in known and S.pad_id[i] and (S.alive[i] or S.want[i].is_set()):
                known.add(key)
                items.append((key, _("Pad {n}  ·  {key}").format(n=i + 1, key=key)))
        items += [(k, _("{key}  (niepodłączony)").format(key=k)) for k in {**self._haptics, **self._speaker} if k not in known]
        self.hap_pad.set_items(items)
        # reopening the window keeps the pad that was picked last (it would look like its setting was lost otherwise)
        keep = self._haptics_key if any(it[0] == self._haptics_key for it in items) else DEFAULT_PAD
        self.hap_pad.set_data(keep)
        self._pick_haptics_pad(keep)

    def _pick_haptics_pad(self, key: str) -> None:
        """Show the haptics settings of the chosen pad key (falling back to the default entry); loading is not an edit."""
        self._haptics_key = key
        cfg = self._haptics.get(key) or self._haptics.get(DEFAULT_PAD) or PadHaptics()
        spk = self._speaker.get(key) or self._speaker.get(DEFAULT_PAD) or PadSpeaker()
        self._haptics_loading = True
        self.hap_own.setVisible(key != DEFAULT_PAD)
        self.hap_own.setChecked(key == DEFAULT_PAD or key in self._haptics or key in self._speaker)
        self.hap_on.setChecked(cfg.enabled)
        self.hap_gain.setValue(cfg.gain)
        self.spk_on.setChecked(spk.enabled)
        self.spk_gain.setValue(spk.gain)
        self._haptics_loading = False
        self._haptics_enable()

    def _haptics_pad_slot(self) -> int | None:
        """The slot of the pad chosen in the combo (None for the default entry or a pad that is not connected)."""
        for i in range(C.NUM_PADS):
            if S.pad_id[i] and S.alive[i] and S.profile_key(i) == self._haptics_key:
                return i
        return None

    def _haptics_enable(self) -> None:
        """Enable the fields only for an own setting; the test button only for a connected pad; say when its sender is too old."""
        own = self.hap_own.isChecked()
        for w in (self.hap_on, self.hap_gain, self.spk_on, self.spk_gain):
            w.setEnabled(own)
        slot = self._haptics_pad_slot()
        self.hap_test.setEnabled(slot is not None)
        self.spk_test.setEnabled(slot is not None)
        old = slot is not None and not rx_haptics.sender_haptics(slot)
        self.hap_note.setText(_("Nadajnik tego pada jest w starszej wersji i nie przekaże haptyki (kliknij Aktualizuj).") if old else "")
        old = slot is not None and not rx_speaker.sender_speaker(slot)
        self.spk_note.setText(_("Nadajnik tego pada jest w starszej wersji i nie przekaże dźwięku na głośnik (kliknij Aktualizuj).")
                              if old else "")

    def _haptics_edited(self, *_args: Any) -> None:
        """Record the edited fields for the current pad key, or drop the entry when it uses the default."""
        if self._haptics_loading:
            return
        key = self._haptics_key
        if key != DEFAULT_PAD and not self.hap_own.isChecked():
            self._haptics.pop(key, None)
            self._speaker.pop(key, None)
        else:
            self._haptics[key] = PadHaptics(enabled=self.hap_on.isChecked(), gain=self.hap_gain.value())
            self._speaker[key] = PadSpeaker(enabled=self.spk_on.isChecked(), gain=self.spk_gain.value())
        self._haptics_enable()

    def _test_haptics(self) -> None:
        """Send a short tone through the haptics path to the chosen pad (with the strength shown, not the saved one)."""
        slot = self._haptics_pad_slot()
        if slot is None:
            return
        why = rx_haptics.test(slot, self.hap_gain.value() / 100)
        self._status(why or _("Ton próbny wysłany do pada."), "warn" if why else "ok")

    def _test_speaker(self) -> None:
        """Send the test jingle through the speaker path to the chosen pad (with the volume shown, not the saved one)."""
        slot = self._haptics_pad_slot()
        if slot is None:
            return
        why = rx_speaker.test(slot, self.spk_gain.value() / 100)
        self._status(why or _("Jingiel wysłany do pada."), "warn" if why else "ok")

    def _light_tab(self) -> list[QWidget | QLayout]:
        """The Lightbar tab: the low battery threshold and the per-pad colour / mode / blinking / game override."""
        self._lights, self._color, self._light_key, self._loading = {}, "", DEFAULT_PAD, False
        self.low_pct = spinbox(*LOW_PCT, 1, " %")
        low = group(_("NISKA BATERIA"),
                    field(_("Próg niskiej baterii"), self.low_pct),
                    label(_("Ten sam próg ma ostrzeżenie na karcie pada i powiadomienie z zasobnika. Ładujący się pad "
                            "nie miga."), "dim", wrap=True))
        self.light_pad = Combo()
        self.light_pad.changed.connect(self._pick_light_pad)
        self.light_own = QCheckBox(_("Własne ustawienie dla tego pada (inaczej domyślne)"))
        self.light_own.toggled.connect(self._light_edited)
        self.light_mode = Combo([(k, _(v), "lightbar") for k, v in LIGHT_MODES.items()])
        self.light_mode.changed.connect(self._light_edited)
        self.light_color = button("", small=True)
        self.light_color.setToolTip(_("Wybierz kolor"))
        self.light_color.clicked.connect(self._pick_color)
        reset_color = icon_button("reset", _("Kolor domyślny (idle_lightbar z receiver.toml)"), 16)
        reset_color.clicked.connect(lambda: self._set_color(""))
        colors = QHBoxLayout()
        colors.setSpacing(8)
        colors.addWidget(self.light_color, 1)
        colors.addWidget(reset_color)
        self.light_blink = QCheckBox(_("Miganie na czerwono przy niskiej baterii (także w grze)"))
        self.light_blink.toggled.connect(self._light_edited)
        self.light_game = QCheckBox(_("Gra może zmienić kolor (tylko w trakcie gry; wyłączone = zawsze ten kolor)"))
        self.light_game.toggled.connect(self._light_edited)
        pad = group(_("KOLOR LIGHTBARA"),
                    label(_("Poza grą (pulpit, Steam, launchery - lista w Trybie konsoli) zawsze świeci ten kolor. W grze "
                            "może go zastąpić kolor gry, chyba że wyłączysz „Gra może zmienić kolor”. Miganie przy niskiej "
                            "baterii działa zawsze, a potwierdzenia (np. złoty błysk mastera) są na wierzchu."), "dim", wrap=True),
                    field(_("Pad"), self.light_pad), self.light_own,
                    pair(field(_("Tryb"), self.light_mode), field(_("Kolor"), _wrap(colors))),
                    self.light_game, self.light_blink)
        return [low, pad]

    def _wol_tab(self) -> list[QWidget | QLayout]:
        """The Wake-on-LAN tab: enable, the pad combination and hold time, and the MAC / broadcast addresses."""
        self.wol_on = QCheckBox(_("Budzenie komputera z pada"))
        self.wol_on.setIcon(icon("wake"))
        self.wol_btn = Combo(input_items(WOL_BUTTONS, text=True))
        self.wol_hold = spinbox(*WOL_HOLD_MS, 100, " ms")
        combo = QHBoxLayout()
        combo.setSpacing(8)
        combo.addWidget(icon_label("ps"))
        combo.addWidget(label("+"))
        combo.addWidget(self.wol_btn, 1)
        self.wol_mac, self.wol_bcast = QLineEdit(), QLineEdit()
        self.wol_mac.setPlaceholderText("auto")
        self.wol_bcast.setPlaceholderText("auto")
        self.wol_seen = label("", "dim", wrap=True)
        how = group(_("WAKE-ON-LAN"),
                    label(_("Gdy komputer nie odpowiada (wyłączony / uśpiony), przytrzymanie tej kombinacji na padzie wysyła "
                            "z telefonu magic packet. Przy działającym komputerze kombinacja idzie do receivera jak zwykle "
                            "(np. pauza mastera), więc nic nie koliduje. Pad potwierdza wysłanie białym błyskiem, błąd - czerwonym."),
                          "dim", wrap=True),
                    self.wol_on,
                    pair(field(_("Kombinacja na padzie"), _wrap(combo)), field(_("Przytrzymanie"), self.wol_hold)))
        addr = group(_("ADRESY"),
                     pair(field(_("MAC karty sieciowej komputera"), self.wol_mac), field(_("Adres broadcast"), self.wol_bcast)),
                     self.wol_seen,
                     label(_("Puste = automatycznie: nadajnik zapamiętuje MAC komputera z tablicy ARP telefonu, póki komputer "
                             "działa, i wysyła na broadcast swojej sieci. Komputer musi mieć włączony Wake-on-LAN w BIOS / UEFI "
                             "i we właściwościach karty sieciowej (najlepiej przewodowej)."), "dim", wrap=True))
        return [how, addr]

    # ---------- lightbar ----------
    def _load_light(self, cfg: LightbarConfig) -> None:
        """Fill the Lightbar tab from a LightbarConfig.

        The pad list (default, connected pads, remembered ones) and the first entry's values.
        """
        self.low_pct.setValue(cfg.low_pct)
        self._lights = dict(cfg.pads)
        items: list[tuple] = [(DEFAULT_PAD, _("Domyślne - pady bez własnego ustawienia"), "lightbar")]    # (key, label[, icon])
        known = {DEFAULT_PAD}
        for i in range(C.NUM_PADS):
            key = S.profile_key(i)
            if key not in known and S.pad_id[i] and (S.alive[i] or S.want[i].is_set()):
                known.add(key)
                items.append((key, _("Pad {n}  ·  {key}").format(n=i + 1, key=key)))
        items += [(k, _("{key}  (niepodłączony)").format(key=k)) for k in self._lights if k not in known]
        self.light_pad.set_items(items)
        # reopening the window keeps the pad that was picked last (it would look like its setting was lost otherwise)
        keep = self._light_key if any(it[0] == self._light_key for it in items) else DEFAULT_PAD
        self.light_pad.set_data(keep)
        self._pick_light_pad(keep)

    def _pick_light_pad(self, key: str) -> None:
        """Show the lightbar settings of the chosen pad key (falling back to the default entry); loading does not count as an edit."""
        self._light_key = key
        light = self._lights.get(key) or self._lights.get(DEFAULT_PAD) or PadLight()
        self._loading = True
        self.light_own.setVisible(key != DEFAULT_PAD)
        self.light_own.setChecked(key == DEFAULT_PAD or key in self._lights)
        self.light_mode.set_data(light.mode)
        self.light_blink.setChecked(light.blink)
        self.light_game.setChecked(light.game)
        self._set_color(light.color, store=False)
        self._loading = False
        self._light_enable()

    def _light_enable(self) -> None:
        """Enable the lightbar fields only for an own setting; the colour only in the solid mode."""
        own = self.light_own.isChecked()
        for w in (self.light_mode, self.light_color, self.light_blink, self.light_game):
            w.setEnabled(own)
        self.light_color.setEnabled(own and self.light_mode.currentData() == "solid")

    def _set_color(self, hex_color: str, store: bool = True) -> None:
        """Set the chosen colour (empty = the default) and paint the button with it; store=False skips recording the edit."""
        self._color = hex_color
        rgb = rx_lightbar.hex_rgb(hex_color) if hex_color else C.IDLE_RGB
        text = rx_lightbar.rgb_hex(rgb) + ("" if hex_color else _("  (domyślny)"))
        fg = "#000000" if sum(rgb) > LUMA_DARK_TEXT else "#ffffff"
        self.light_color.setText(text)
        self.light_color.setStyleSheet(f"background: {rx_lightbar.rgb_hex(rgb)}; color: {fg};")
        if store:
            self._light_edited()

    def _pick_color(self) -> None:
        """Open the colour dialog and apply the picked colour."""
        start = QColor(*(rx_lightbar.hex_rgb(self._color) if self._color else C.IDLE_RGB))
        c = QColorDialog.getColor(start, self, _("Kolor lightbara"))
        if c.isValid():
            self._set_color(c.name())

    def _light_edited(self, *_args: Any) -> None:
        """Record the edited fields for the current pad key (an own setting), or drop the entry when it uses the default."""
        if getattr(self, "_loading", False):
            return
        key = self._light_key
        if key != DEFAULT_PAD and not self.light_own.isChecked():
            self._lights.pop(key, None)
        else:
            self._lights[key] = PadLight(mode=self.light_mode.currentData(), color=self._color,
                                         blink=self.light_blink.isChecked(), game=self.light_game.isChecked())
        self._light_enable()

    # ---------- Wake-on-LAN ----------
    def _load_wol(self, w: WolConfig) -> None:
        """Fill the Wake-on-LAN tab from a WolConfig."""
        self.wol_on.setChecked(w.enabled)
        self.wol_btn.set_data(w.button)
        self.wol_hold.setValue(w.hold_ms)
        self.wol_mac.setText(w.mac)
        self.wol_bcast.setText(w.bcast)
        self._wol_seen()

    def _wol_seen(self) -> None:
        """What the sender detected by itself (the build2 packet) - a hint in the empty fields."""
        seen = S.sender_wol
        mac, bcast = seen if seen else ("", "")
        self.wol_mac.setPlaceholderText(_("auto: {v}").format(v=mac) if mac else "auto")
        self.wol_bcast.setPlaceholderText(_("auto: {v}").format(v=bcast) if bcast else "auto")
        if seen is None:
            self.wol_seen.setText(_("Nadajnik nie podał jeszcze adresów (offline albo starsza wersja - Aktualizuj)."))
        else:
            self.wol_seen.setText(_("Nadajnik użyje: MAC {mac}, broadcast {bcast}.").format(
                mac=mac or _("nieznany - wpisz go albo uruchom receiver, gdy nadajnik działa"), bcast=bcast or "255.255.255.255"))
        set_prop(self.wol_seen, "level", "warn" if seen is not None and not mac else "")

    def _collect_wol(self) -> WolConfig:
        """-> WolConfig, or a ValueError naming the field."""
        try:
            mac = rx_settings.WolConfig.V["mac"](self.wol_mac.text().strip())
        except ValueError:
            raise ValueError(_("Zły adres MAC - wpisz np. aa:bb:cc:dd:ee:ff albo zostaw puste.")) from None
        try:
            bcast = rx_settings.WolConfig.V["bcast"](self.wol_bcast.text().strip())
        except ValueError:
            raise ValueError(_("Zły adres broadcast - wpisz np. 192.168.1.255 albo zostaw puste.")) from None
        return WolConfig(enabled=self.wol_on.isChecked(), mac=mac, bcast=bcast, button=self.wol_btn.currentData(),
                         hold_ms=self.wol_hold.value())

    # ---------- data ----------
    def present(self) -> None:
        """Show the window; a freshly opened one holds the running configuration's values (not a previous, cancelled edit)."""
        if not self.isVisible():
            self.reload()
            self._fit()
        self.show()
        if not hasattr(self, "_frame"):
            self._fit()                     # the first opening: the real window frame is known only now
        self.raise_()
        self.activateWindow()

    def _fit(self) -> None:
        """Centre on the main window's screen and fully inside the work area (without the taskbar).

        A fixed 860x780 on 1080p with 150 % scaling (720 px of height) went under the taskbar together with the Save
        buttons. The window frame (the title bar) is known only after showing - remembered, so later openings place the
        window once, before showing it (without moving a visible window).
        """
        if self.isVisible():
            self._frame = self.frameGeometry().size() - self.geometry().size()
        frame = getattr(self, "_frame", FRAME_GUESS)
        owner = self.parent()
        scr = (owner.screen() if owner is not None else None) or self.screen() or QApplication.primaryScreen()
        area = scr.availableGeometry()
        size = PREF_SIZE.boundedTo(area.size() - frame - QSize(FIT_MARGIN, FIT_MARGIN)).expandedTo(self.minimumSizeHint())
        size = size.boundedTo(area.size() - frame)
        if size != self.size():
            self.resize(size)
        g = QRect(QPoint(0, 0), size + frame)
        g.moveCenter(area.center())
        pos = QPoint(max(g.left(), area.left()), max(g.top(), area.top()))
        if pos != self.pos():
            self.move(pos)

    def reload(self) -> None:
        """Reload every tab from the running configuration (used when the window opens)."""
        self.save_btn.setEnabled(True)
        self._load_screens()
        self._load_backups()
        self._load_mouse(rx_settings.cur.mouse)
        self._load_master(rx_settings.cur.master)
        self._load_light(rx_settings.cur.lightbar)
        self._load_haptics(rx_settings.cur.haptics, rx_settings.cur.speaker)
        self._load_wol(rx_settings.cur.wol)
        self._load_vpad(rx_settings.cur.vpad)
        self.sender_cfg.load(rx_settings.cur.sender)
        cfg = rx_console.ctl.cfg if rx_console.ctl else ConsoleConfig()
        self.set_button(cfg.toggle_button)
        self.dbl.setValue(cfg.double_click_ms)
        self.timeout.setValue(cfg.timeout_s)
        self.startup.set_data(cfg.startup_mode)
        self.auto_on.setChecked(cfg.auto_on_master)
        res = (cfg.tv_width, cfg.tv_height) if cfg.tv_width and cfg.tv_height else (0, 0)
        extra = () if res in TV_RESOLUTIONS else (res,)     # typed by hand in console.json - stays on the list
        self.tv_res.set_items(_res_items((*TV_RESOLUTIONS, *extra)))
        self.tv_res.set_data("{}x{}".format(*res))
        self.tv_scale.set_data(cfg.tv_scale)
        self.apps.setPlainText("\n".join(cfg.allowed_apps))
        owner = self.parent()
        self._load_window(owner)
        self.lang.set_data(rx_settings.cur.ui.lang)
        self._load_pads_view(rx_settings.cur.ui)
        self.pick.setEnabled(rx_console.ctl is not None)
        self.refresh_async(cfg.audio_tv, cfg.audio_pc)
        self._status("")

    def refresh(self, tv: str | None = None, pc: str | None = None) -> None:
        """Read the active audio outputs.

        A device chosen earlier that is absent now (e.g. the TV is off) stays on the list as "unavailable", so that saving
        does not lose it.
        """
        tv = self.tv.currentData() if tv is None else tv
        pc = self.pc.currentData() if pc is None else pc
        self._audio_gen += 1                # a listing still running in the background is now stale
        try:
            names = [name for _, name in rx_audio.list_outputs()]
            err = None
        except Exception as e:      # noqa: BLE001 - any audio backend failure is shown, not fatal
            names, err = [], e
        self._show_outputs(tv, pc, names, err)

    def refresh_async(self, tv: str | None = None, pc: str | None = None) -> None:
        """refresh() without blocking the window: the COM enumeration (can take a while, e.g. a TV that is waking up) runs in a thread.

        The lists keep their current items until the result arrives.
        """
        tv = self.tv.currentData() if tv is None else tv
        pc = self.pc.currentData() if pc is None else pc
        self._audio_gen += 1
        gen = self._audio_gen

        def run() -> None:
            try:
                names, err = [name for _, name in rx_audio.list_outputs()], None
            except Exception as e:      # noqa: BLE001 - reported in the window like in refresh()
                names, err = [], e
            self.outputs_listed.emit((gen, tv, pc, names, err))      # queued to the window thread

        threading.Thread(target=run, name="audio outputs", daemon=True).start()

    def _outputs_ready(self, result: Any) -> None:
        """The audio listing thread finished: show it, unless a newer listing was started meanwhile."""
        gen, tv, pc, names, err = result
        if gen == self._audio_gen:
            self._show_outputs(tv, pc, names, err)

    def _show_outputs(self, tv: str | None, pc: str | None, names: list[str], err: Exception | None) -> None:
        """Fill both audio combos (or show the error); a device chosen earlier but absent stays as "unavailable"."""
        if err is None:
            self._status("")
        else:
            self._status(_("Nie można odczytać urządzeń audio: {err} (pip install pycaw)").format(err=repr(err)), "bad")
        for combo, current in ((self.tv, tv), (self.pc, pc)):
            items = [("", _(NO_AUDIO)), *((n, n) for n in names)]
            if current and current not in names:
                items.append((current, _("{name} (niedostępne)").format(name=current)))
            combo.set_items(items)
            combo.set_data(current or "")

    def _load_mouse(self, m: MouseConfig) -> None:
        """Fill the Mouse tab from a MouseConfig."""
        self.sens.set(m.sens)
        self.freeze.set(m.click_freeze_ms)
        self.rewind.set(m.click_rewind_ms)
        self.drag.set(m.drag_threshold)

    def _load_master(self, m: MasterConfig) -> None:
        """Fill the Master tab from a MasterConfig; the pad list holds the identified pads and the configured one."""
        items: list[tuple] = [("", _("— brak mastera (każdy pad może wszystko) —"))]     # (key, label[, icon])
        known = {""}
        for i in range(C.NUM_PADS):
            key = S.profile_key(i)
            # only a pad with an identifier (MAC) - a "slotN" key would be lost once the identifier arrives
            if key in known or not ((S.pad_id[i] and (S.alive[i] or S.want[i].is_set())) or key == m.pad):
                continue
            known.add(key)
            items.append((key, _("Pad {n}  ·  {key}").format(n=i + 1, key=key), "crown" if key == m.pad else None))
        if m.pad not in known:
            items.append((m.pad, _("{key}  (niepodłączony)").format(key=m.pad), "crown"))
        self.master_pad.set_items(items)
        self.master_pad.set_data(m.pad)
        self._master_touched = False          # the master list touched in this window (the changed signal - from the user only)
        self.claim.setChecked(m.claim_hold)
        for k, cb in self.features.items():
            cb.setChecked(k in m.only_master)
        self.absent.set_data(m.when_absent)
        for k, combo in self.actions.items():
            combo.set_data(m.actions.get(k, ""))

    def collect_settings(self) -> rx_settings.Settings:
        """-> the Settings described by the window (mouse, master, language, pad cards, lightbar, Wake-on-LAN, pads).

        The log settings stay as they are.
        """
        mouse = MouseConfig(sens=round(self.sens.value(), 2), click_freeze_ms=round(self.freeze.value()),
                            click_rewind_ms=round(self.rewind.value()), drag_threshold=round(self.drag.value()))
        # a master left unchanged in this window stays the current one: the crown on the pad card or PS + R3 while Settings is open
        # must not be undone by Save (e.g. when only the mouse is changed)
        pad = (self.master_pad.currentData() or "") if self._master_touched else rx_settings.cur.master.pad
        master = MasterConfig(pad=pad, when_absent=self.absent.currentData(),
                              only_master=tuple(k for k, cb in self.features.items() if cb.isChecked()),
                              actions={k: c.currentData() or "" for k, c in self.actions.items()},
                              claim_hold=self.claim.isChecked())
        ui = replace(rx_settings.cur.ui, lang=self.lang.currentData(), pads_mode=self.pads_mode.currentData(),
                     pads_count=self.pads_count.value())
        return rx_settings.Settings(mouse, master, ui,
                                    LightbarConfig(low_pct=self.low_pct.value(), pads=dict(self._lights)),
                                    self._collect_wol(), rx_settings.cur.log, self._collect_vpad(),
                                    HapticsConfig(pads=dict(self._haptics)), SpeakerConfig(pads=dict(self._speaker)),
                                    self.sender_cfg.collect())

    def collect(self) -> ConsoleConfig:
        """-> the ConsoleConfig described by the Console mode tab."""
        return ConsoleConfig(toggle_button=self.toggle_button, double_click_ms=self.dbl.value(),
                             audio_tv=self.tv.currentData() or "", audio_pc=self.pc.currentData() or "",
                             timeout_s=self.timeout.value(), startup_mode=self.startup.currentData(),
                             **dict(zip(("tv_width", "tv_height"), map(int, self.tv_res.currentData().split("x")), strict=True)),
                             tv_scale=self.tv_scale.currentData(), auto_on_master=self.auto_on.isChecked(),
                             allowed_apps=tuple(dict.fromkeys(x.strip().lower() for x in self.apps.toPlainText().splitlines()
                                                              if x.strip())))

    # ---------- pad button ----------
    def set_button(self, name: str) -> None:
        """Show the chosen toggle button (name and icon) and leave the capture state."""
        self.toggle_button = name
        self.pick_name.setText(_(LABELS.get(name, name)))
        self.pick_icon.setPixmap(_hidpi(name, 26))
        self.pick.setText(_("Wybierz na padzie"))
        set_prop(self.pick, "armed", False)

    def _toggle_capture(self) -> None:
        """Start waiting for a pad button press (with a timeout), or cancel the wait."""
        if self._cap_timer.isActive():
            self._stop_capture()
            return
        rx_console.ctl.arm_capture(self.captured.emit)       # called from the network thread; emit is safe between threads
        self._cap_timer.start()
        self.pick.setText(_("Naciśnij przycisk na padzie… (klik = anuluj)"))
        set_prop(self.pick, "armed", True)

    def _stop_capture(self) -> None:
        """Stop waiting for a pad button and restore the button caption."""
        self._cap_timer.stop()
        if rx_console.ctl:
            rx_console.ctl.cancel_capture()
        self.set_button(self.toggle_button)

    def _on_captured(self, name: str) -> None:
        """A pad button arrived from the network thread: take it as the toggle button (only while still waiting)."""
        if self._cap_timer.isActive():                       # a late signal after cancelling changes nothing
            self._cap_timer.stop()
            self.set_button(name)

    # ---------- actions ----------
    def _test(self) -> None:
        """The Test button: switch the console mode on for a few seconds with the values in the window."""
        if rx_console.ctl is None:
            self._status(_("Tryb konsoli nie działa w tym uruchomieniu."), "bad")
            return
        rx_console.ctl.test(self.collect())
        self._status(_("Test: TV i dźwięk TV na {s} s, potem powrót do trybu rozszerzonego.").format(
            s=f"{rx_console.TEST_S:.0f}"), "ok")
        self.test_btn.setEnabled(False)
        QTimer.singleShot(int((rx_console.TEST_S + 5) * 1000), lambda: self.test_btn.setEnabled(True))

    def _save(self) -> None:
        """Validate and save everything.

        Settings.json (in effect at once) and, when the console mode or the language changed, console.json plus a restart
        request.
        """
        try:
            new = self.collect_settings()
        except ValueError as e:
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.wol_page))
            self._status(str(e), "bad")
            return
        used = [b for b in new.master.actions.values() if b]
        if len(used) != len(set(used)):
            self.tabs.setCurrentIndex(2)
            self._status(_("Ten sam przycisk przypisano do dwóch akcji mastera."), "bad")
            return
        try:
            rx_settings.save(new, C.SETTINGS_PATH)
        except OSError as e:
            self._status(_("Nie udało się zapisać {path}: {err}").format(path=C.SETTINGS_PATH, err=e), "bad")
            return
        lang_changed = new.ui.lang != rx_settings.cur.ui.lang
        rx_settings.apply(new)
        owner = self.parent()
        if hasattr(owner, "set_screen_choice"):
            # both setters re-place the window and raise it, so call them only for what actually changed
            screen = self.screen_box.currentData() or ""
            mode = self.win_mode.currentData()
            full = mode == "full"
            if screen != owner.screen_choice():
                owner.set_screen_choice(screen)
            if not full and (mode != owner.window_mode() or self._chosen_size() != owner.window_size()):
                owner.set_window_mode(mode, self._chosen_size())
            if full != owner.fullscreen():
                owner.set_fullscreen(full)
        self.applied.emit()
        cfg = self.collect()
        running = rx_console.ctl.cfg if rx_console.ctl else None
        if cfg == running and not lang_changed:              # the console mode and language unchanged - no restart
            self._stop_capture()
            self._status(_("✓ Zapisano - działa od razu."), "ok")
            return
        try:
            rx_console.save(cfg, C.CONSOLE_PATH)
        except OSError as e:
            self._status(_("Nie udało się zapisać {path}: {err}").format(path=C.CONSOLE_PATH, err=e), "bad")
            return
        self._stop_capture()
        self.saved.emit()

    def _status(self, text: str, level: str = "") -> None:
        """Show a status message with a level style."""
        self.status.setText(text)
        set_prop(self.status, "level", level)

    def hideEvent(self, e: QHideEvent) -> None:
        """On hiding: stop a pending pad button capture and the HIDMaestro status timer."""
        super().hideEvent(e)
        if self._cap_timer.isActive():
            self._stop_capture()
        self._hm_timer.stop()


def _backup_reason(b: Backup) -> str:
    """Where a backup came from: an update (old -> new version), before restoring another, manual."""
    if b.reason == "update":
        ver = f"{b.commit} → {b.new}" if b.commit and b.new else b.commit or b.new
        return _("przed aktualizacją") + (f" ({ver})" if ver else "")
    return _("przed przywróceniem kopii") if b.reason == "restore" else _("zrobiona ręcznie")


def _res_items(resolutions: Iterable[tuple[int, int]]) -> list[tuple[str, str]]:
    """Combo items ("WxH" value, "W × H" text) for the TV resolutions; (0, 0) means unchanged."""
    return [("{}x{}".format(*r), f"{r[0]} × {r[1]}" if r[0] else _("— bez zmiany —")) for r in resolutions]


def _page(items: Sequence[QWidget | QLayout]) -> QScrollArea:
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
    return _scroll(w)


def _scroll(w: QWidget) -> QScrollArea:
    """Wrap a widget in a frameless scroll area without a horizontal bar."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QScrollArea
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(w)
    return sa


def _claim_s() -> str:
    """The master claim hold time in seconds as text (imported lazily)."""
    import rx_master
    return f"{rx_master.CLAIM_S:g}"


def _hidpi(name: str, size: int) -> QPixmap:
    """A pad icon pixmap rendered for a 2x device pixel ratio."""
    from rx_qt_icons import pixmap
    pm = pixmap(name, size * 2)
    pm.setDevicePixelRatio(2)
    return pm


def row_of(*widgets: Any) -> QHBoxLayout:
    """Buttons in a row, to the left."""
    row = QHBoxLayout()
    row.setSpacing(8)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    return row


def _wrap(layout: QLayout) -> QWidget:
    """Wrap a layout in a plain widget so it can be used as a field."""
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w
