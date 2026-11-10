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

ConsoleSettings itself holds the dialog, the Console / Mouse / Master / Window tabs and Save; the other tabs are mixins in their own
modules (rx_qt_console_vpad, _haptics, _light, _safety; rx_qt_console_util has the shared base and helpers).
"""
import threading
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from rx_i18n import _
from PySide6.QtCore import QPoint, QRect, QSettings, QSize, QTimer, Signal
from PySide6.QtGui import QHideEvent, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLayout, QPlainTextEdit, QVBoxLayout, QWidget)

import rx_audio
import rx_console
import rx_settings
import rx_state as S
from rx_buttons import LABELS
from rx_config import C
from rx_console import DEFAULT_APPS, STARTUP_MODES, ConsoleConfig
from rx_display import DPI_VALUES
from rx_i18n import LANGS
from rx_qt_icons import icon
from rx_qt_screens import FIT_MARGIN, parse_size
from rx_qt_sendercfg import SenderCfgPanel
from rx_qt_style import set_prop
from rx_qt_widgets import (Combo, SideTabs, SliderRow, button, field, group, icon_label, input_items,
                           label, left, pair, spinbox)
from rx_settings import (ABSENT, COMBO_BUTTONS, MASTER_ACTIONS, MASTER_FEATURES,
                         MOUSE_LIMITS, HapticsConfig, LightbarConfig,
                         MasterConfig, MouseConfig, SpeakerConfig)
from rx_qt_console_haptics import HapticsTab
from rx_qt_console_light import LightWolTabs
from rx_qt_console_safety import SafetyTabs
from rx_qt_console_util import page, wrap
from rx_qt_console_vpad import VpadTab


CAPTURE_TIMEOUT_MS = 15000
NO_AUDIO = "— nie zmieniaj dźwięku —"
TV_RESOLUTIONS = ((0, 0), (1920, 1080), (2560, 1440), (3840, 2160))
WINDOW_RESOLUTIONS = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440))   # presets for the window size
STARTUP_LABELS = {"extend": "Rozszerz: oba ekrany aktywne", "internal": "Tylko monitor"}
PREF_SIZE = QSize(1060, 780)  # the settings window size on a big screen (a smaller screen -> a smaller one, with scrolling)
FRAME_GUESS = QSize(16, 40)   # the Windows frame and title bar, before the window is shown


class ConsoleSettings(SafetyTabs, VpadTab, HapticsTab, LightWolTabs, QDialog):
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
        self._frame: QSize | None = None    # the window frame (title bar), known after the first showing
        # the main window's receiver_ui.ini: remembers which pad / "Default settings" entry the per-pad tabs showed last
        ui = getattr(parent, "settings", None)
        self._ui: QSettings | None = ui if isinstance(ui, QSettings) else None
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
        self.tabs.addTab(page(self._console_tab()), icon("tv"), _("Tryb konsoli"))
        self.tabs.addTab(page(self._mouse_tab()), icon("mouse"), _("Mysz"))
        self.tabs.addTab(page(self._master_tab()), icon("crown"), _("Master"))
        self.tabs.addTab(page(self._light_tab()), icon("lightbar"), _("Lightbar"))
        self.tabs.addTab(page(self._haptics_tab()), icon("rumble"), _("Haptyka i głośnik"))
        self.wol_page = page(self._wol_tab())
        self.tabs.addTab(self.wol_page, icon("wake"), _("Wake-on-LAN"))
        self.tabs.addTab(page(self._vpad_tab()), icon("ps"), _("Pady"))
        self.sender_cfg = SenderCfgPanel()
        self.tabs.addTab(page(self.sender_cfg.sections()), icon("phone"), _("Nadajnik"))
        self.tabs.addTab(page(self._window_tab()), icon("window"), _("Okno"))
        self.tabs.addTab(page(self._backup_tab()), icon("backup"), _("Kopie"))
        self.tabs.addTab(page(self._security_tab()), icon("shield"), _("Bezpieczeństwo"))
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
                          field(_("Przycisk pada (podwójne kliknięcie)"), wrap(pick)),
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

    # ---------- haptics ----------
    # ---------- lightbar ----------
    # ---------- Wake-on-LAN ----------
    # ---------- data ----------
    def present(self) -> None:
        """Show the window; a freshly opened one holds the running configuration's values (not a previous, cancelled edit)."""
        if not self.isVisible():
            self.reload()
            self._fit()
        self.show()
        if self._frame is None:
            self._fit()                     # the first opening: the real window frame is known only now
        self.raise_()
        self.activateWindow()

    def _fit(self) -> None:
        """Centre on the main window's screen and fully inside the work area (without the taskbar).

        A fixed size cannot work: 860x780 on 1080p with 150 % scaling (720 px of height) would put the Save buttons under
        the taskbar. The window frame (the title bar) is known only after showing - remembered, so later openings place the
        window once, before showing it (without moving a visible window).
        """
        if self.isVisible():
            self._frame = self.frameGeometry().size() - self.geometry().size()
        frame = self._frame if self._frame is not None else FRAME_GUESS
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
        if rx_console.ctl is None:                           # console mode not started (no settings loaded yet)
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

def _res_items(resolutions: Iterable[tuple[int, int]]) -> list[tuple[str, str]]:
    """Combo items ("WxH" value, "W × H" text) for the TV resolutions; (0, 0) means unchanged."""
    return [("{}x{}".format(*r), f"{r[0]} × {r[1]}" if r[0] else _("— bez zmiany —")) for r in resolutions]

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
