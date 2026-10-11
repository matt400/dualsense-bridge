"""Receiver: okno ustawień (tray -> Ustawienia, albo przycisk w nagłówku okna) - cztery zakładki:

  Tryb konsoli  przycisk pada wybierasz naciśnięciem go na padzie (rx_console.arm_capture), urządzenia audio to lista
                aktywnych wyjść Windows (rx_audio, Odśwież); "Przetestuj" włącza tryb konsoli na kilka sekund
                z wartościami z okna (jeszcze niezapisanymi) i sam wraca do extend
  Mysz          czułość kursora touchpada i stabilizacja kliknięcia (rx_settings.MouseConfig)
  Master        pad master, funkcje tylko dla niego, akcje "PS + przycisk" (rx_settings.MasterConfig, rx_master)
  Okno          ekran, na którym pojawia się okno (receiver_ui.ini, MainWindow.place_on_screen)

"Zapisz": mysz i master działają od razu (settings.json, bez restartu). Zmiana trybu konsoli zapisuje console.json
i prosi właściciela okna o restart receivera (sygnał saved) - po restarcie obowiązuje startup_mode jak przy autostarcie.
"""
from rx_i18n import _
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QGridLayout, QHBoxLayout, QPlainTextEdit, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

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
from rx_qt_style import set_prop
from rx_qt_widgets import Combo, SliderRow, button, field, group, icon_label, input_items, label, left, pair
from rx_settings import ABSENT, COMBO_BUTTONS, MASTER_ACTIONS, MASTER_FEATURES, MasterConfig, MouseConfig

CAPTURE_TIMEOUT_MS = 15000
NO_AUDIO = "— nie zmieniaj dźwięku —"
TV_RESOLUTIONS = ((0, 0), (1920, 1080), (2560, 1440), (3840, 2160))
STARTUP_LABELS = {"extend": "Rozszerz: oba ekrany aktywne", "internal": "Tylko monitor"}


class ConsoleSettings(QDialog):
    saved = Signal()            # console.json zapisany - właściciel okna restartuje receiver
    applied = Signal()          # mysz / master zapisane i działają (bez restartu) - okno odświeża karty padów
    captured = Signal(str)      # nazwa przycisku z wątku sieci (Qt kolejkuje do wątku okna)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle(_("Ustawienia"))
        self.setMinimumWidth(560)
        self.resize(600, 760)
        self.toggle_button = ConsoleConfig().toggle_button
        self.captured.connect(self._on_captured)
        self._cap_timer = QTimer(self, singleShot=True, interval=CAPTURE_TIMEOUT_MS)
        self._cap_timer.timeout.connect(self._stop_capture)
        self._build()

    # ---------- budowa ----------
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.addTab(_page(self._console_tab()), icon("tv"), _("Tryb konsoli"))
        self.tabs.addTab(_page(self._mouse_tab()), icon("mouse"), _("Mysz"))
        self.tabs.addTab(_page(self._master_tab()), icon("crown"), _("Master"))
        self.tabs.addTab(_page(self._window_tab()), icon("window"), _("Okno"))
        lay.addWidget(self.tabs, 1)

        self.status = label("", wrap=True)
        lay.addWidget(self.status)
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
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.save_btn)
        lay.addLayout(row)
        self.tabs.currentChanged.connect(lambda i: self.test_btn.setVisible(i == 0))

    def _console_tab(self):
        self.pick = button("", small=True)
        self.pick.clicked.connect(self._toggle_capture)
        self.pick_icon = label("")
        self.pick_name = label("", "val")
        pick = QHBoxLayout()
        pick.setSpacing(8)
        pick.addWidget(self.pick_icon)
        pick.addWidget(self.pick_name, 1)
        pick.addWidget(self.pick)
        self.dbl = _spin(100, 2000, 50, " ms")
        self.timeout = _spin(5, 3600, 5, " s")
        self.auto_on = QCheckBox(_("Włącz po połączeniu pada master"))
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
        refresh.clicked.connect(lambda: self.refresh())     # clicked podaje 'checked' - nie jako urządzenie
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
                              "w grze podwójne kliknięcie nic nie robi, a włączenie po połączeniu mastera czeka na wyjście z gry."),
                            "dim", wrap=True),
                      field(_("Dozwolone programy (pulpit, Steam, launchery)"), self.apps),
                      left(apps_reset))
        note = label(_("Zmiana trybu konsoli lub języka uruchamia receiver od nowa (pady na chwilę znikną). Niezapisane "
                       "zmiany profilu (Zapisz w oknie głównym) zostaną utracone."), "dim", wrap=True)
        return [switching, tv, games, note]

    def _mouse_tab(self):
        self.sens = SliderRow(_("Czułość kursora"), 0.05, 3.0, 0.05, "x")
        self.freeze = SliderRow(_("Kursor stoi po wciśnięciu i puszczeniu kliku [ms]"), 0, 400, 10)
        self.rewind = SliderRow(_("Cofnij ruch sprzed kliknięcia [ms] (docisk palca przesuwa kursor)"), 0, 200, 10)
        self.drag = SliderRow(_("Przeciąganie z wciśniętym klikiem zaczyna się po (jednostki touchpada)"), 0, 300, 5)
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

    def _window_tab(self):
        self.screen_box = Combo()
        self.win_mode = Combo([("window", _("Okno (F11 przełącza)"), "window"), ("full", _("Pełny ekran"), "fullscreen")])
        self.lang = Combo(list(LANGS.items()))
        window = group(_("OKNO"),
                       field(_("Ekran, na którym pojawia się okno DualSense bridge"), self.screen_box),
                       label(_("Okno otwiera się (z ikony w zasobniku, przy starcie) na środku wybranego ekranu. Gdy tego "
                               "ekranu nie ma (np. monitor wyłączony w trybie konsoli), okno zostaje na dostępnym ekranie."),
                             "dim", wrap=True),
                       field(_("Wygląd okna"), self.win_mode))
        lang = group(_("JĘZYK"), field(_("Język"), self.lang),
                     label(_("Zmiana języka działa po restarcie receivera."), "dim", wrap=True))
        return [window, lang]

    def _load_screens(self):
        from PySide6.QtGui import QGuiApplication
        from rx_qt import screens
        owner = self.parent()
        cur = owner.screen_choice() if hasattr(owner, "screen_choice") else ""
        items = [("", _("Tam, gdzie okno było ostatnio"))]
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

    def _master_tab(self):
        self.master_pad = Combo()
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

    # ---------- dane ----------
    def present(self):
        """Pokazuje okno; świeżo otwarte ma wartości z działającej konfiguracji (nie z poprzedniej, anulowanej edycji)."""
        if not self.isVisible():
            self.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def reload(self):
        self._load_screens()
        self._load_mouse(rx_settings.cur.mouse)
        self._load_master(rx_settings.cur.master)
        cfg = rx_console.ctl.cfg if rx_console.ctl else ConsoleConfig()
        self.set_button(cfg.toggle_button)
        self.dbl.setValue(cfg.double_click_ms)
        self.timeout.setValue(cfg.timeout_s)
        self.startup.set_data(cfg.startup_mode)
        self.auto_on.setChecked(cfg.auto_on_master)
        res = (cfg.tv_width, cfg.tv_height) if cfg.tv_width and cfg.tv_height else (0, 0)
        extra = () if res in TV_RESOLUTIONS else (res,)     # wpisana ręcznie w console.json - zostaje na liście
        self.tv_res.set_items(_res_items((*TV_RESOLUTIONS, *extra)))
        self.tv_res.set_data("{}x{}".format(*res))
        self.tv_scale.set_data(cfg.tv_scale)
        self.apps.setPlainText("\n".join(cfg.allowed_apps))
        owner = self.parent()
        self.win_mode.set_data("full" if getattr(owner, "fullscreen", lambda: False)() else "window")
        self.lang.set_data(rx_settings.cur.ui.lang)
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
            self._status(_("Nie można odczytać urządzeń audio: {err} (pip install pycaw)").format(err=repr(e)), "bad")
        for combo, current in ((self.tv, tv), (self.pc, pc)):
            items = [("", _(NO_AUDIO)), *((n, n) for n in names)]
            if current and current not in names:
                items.append((current, _("{name} (niedostępne)").format(name=current)))
            combo.set_items(items)
            combo.set_data(current or "")

    def _load_mouse(self, m):
        self.sens.set(m.sens)
        self.freeze.set(m.click_freeze_ms)
        self.rewind.set(m.click_rewind_ms)
        self.drag.set(m.drag_threshold)

    def _load_master(self, m):
        items, known = [("", _("— brak mastera (każdy pad może wszystko) —"))], {""}
        for i in range(C.NUM_PADS):
            key = S.profile_key(i)
            # tylko pad z identyfikatorem (MAC) - klucz "slotN" zgubiłby się po nadejściu identyfikatora
            if key in known or not ((S.pad_id[i] and (S.alive[i] or S.want[i].is_set())) or key == m.pad):
                continue
            known.add(key)
            items.append((key, _("Pad {n}  ·  {key}").format(n=i + 1, key=key), "crown" if key == m.pad else None))
        if m.pad not in known:
            items.append((m.pad, _("{key}  (niepodłączony)").format(key=m.pad), "crown"))
        self.master_pad.set_items(items)
        self.master_pad.set_data(m.pad)
        self.claim.setChecked(m.claim_hold)
        for k, cb in self.features.items():
            cb.setChecked(k in m.only_master)
        self.absent.set_data(m.when_absent)
        for k, combo in self.actions.items():
            combo.set_data(m.actions.get(k, ""))

    def collect_settings(self):
        mouse = MouseConfig(sens=round(self.sens.value(), 2), click_freeze_ms=round(self.freeze.value()),
                            click_rewind_ms=round(self.rewind.value()), drag_threshold=round(self.drag.value()))
        master = MasterConfig(pad=self.master_pad.currentData() or "", when_absent=self.absent.currentData(),
                              only_master=tuple(k for k, cb in self.features.items() if cb.isChecked()),
                              actions={k: c.currentData() or "" for k, c in self.actions.items()},
                              claim_hold=self.claim.isChecked())
        return rx_settings.Settings(mouse, master, rx_settings.UiConfig(lang=self.lang.currentData()))

    def collect(self):
        return ConsoleConfig(toggle_button=self.toggle_button, double_click_ms=self.dbl.value(),
                             audio_tv=self.tv.currentData() or "", audio_pc=self.pc.currentData() or "",
                             timeout_s=self.timeout.value(), startup_mode=self.startup.currentData(),
                             **dict(zip(("tv_width", "tv_height"), map(int, self.tv_res.currentData().split("x")), strict=True)),
                             tv_scale=self.tv_scale.currentData(), auto_on_master=self.auto_on.isChecked(),
                             allowed_apps=tuple(dict.fromkeys(x.strip().lower() for x in self.apps.toPlainText().splitlines()
                                                              if x.strip())))

    # ---------- przycisk pada ----------
    def set_button(self, name):
        self.toggle_button = name
        self.pick_name.setText(_(LABELS.get(name, name)))
        self.pick_icon.setPixmap(_hidpi(name, 26))
        self.pick.setText(_("Wybierz na padzie"))
        set_prop(self.pick, "armed", False)

    def _toggle_capture(self):
        if self._cap_timer.isActive():
            self._stop_capture()
            return
        rx_console.ctl.arm_capture(self.captured.emit)       # wołane z wątku sieci; emit jest bezpieczny między wątkami
        self._cap_timer.start()
        self.pick.setText(_("Naciśnij przycisk na padzie… (klik = anuluj)"))
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
            self._status(_("Tryb konsoli nie działa w tym uruchomieniu."), "bad")
            return
        rx_console.ctl.test(self.collect())
        self._status(_("Test: TV i dźwięk TV na {s} s, potem powrót do trybu rozszerzonego.").format(
            s=f"{rx_console.TEST_S:.0f}"), "ok")
        self.test_btn.setEnabled(False)
        QTimer.singleShot(int((rx_console.TEST_S + 5) * 1000), lambda: self.test_btn.setEnabled(True))

    def _save(self):
        new = self.collect_settings()
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
            owner.set_screen_choice(self.screen_box.currentData() or "")
            owner.set_fullscreen(self.win_mode.currentData() == "full")
        self.applied.emit()
        cfg = self.collect()
        running = rx_console.ctl.cfg if rx_console.ctl else None
        if cfg == running and not lang_changed:              # tryb konsoli i język bez zmian - bez restartu
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

    def _status(self, text, level=""):
        self.status.setText(text)
        set_prop(self.status, "level", level)

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._cap_timer.isActive():
            self._stop_capture()


def _res_items(resolutions):
    return [("{}x{}".format(*r), f"{r[0]} × {r[1]}" if r[0] else _("— bez zmiany —")) for r in resolutions]


def _page(items):
    """Zakładka: sekcje jedna pod drugą, w przewijaniu tylko w pionie (szerokość zawsze = szerokość okna)."""
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


def _scroll(w):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QScrollArea
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(w)
    return sa


def _claim_s():
    import rx_master
    return f"{rx_master.CLAIM_S:g}"


def _hidpi(name, size):
    from rx_qt_icons import pixmap
    pm = pixmap(name, size * 2)
    pm.setDevicePixelRatio(2)
    return pm


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
