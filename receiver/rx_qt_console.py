"""Receiver: okno ustawień (tray -> Ustawienia, albo przycisk w nagłówku okna) - siedem zakładek:

  Tryb konsoli  przycisk pada wybierasz naciśnięciem go na padzie (rx_console.arm_capture), urządzenia audio to lista
                aktywnych wyjść Windows (rx_audio, Odśwież); "Przetestuj" włącza tryb konsoli na kilka sekund
                z wartościami z okna (jeszcze niezapisanymi) i sam wraca do extend
  Mysz          czułość kursora touchpada i stabilizacja kliknięcia (rx_settings.MouseConfig)
  Master        pad master, funkcje tylko dla niego, akcje "PS + przycisk" (rx_settings.MasterConfig, rx_master)
  Lightbar      kolor, gdy gra nic nie ustawiła (stały / wg baterii) i miganie przy niskiej baterii - per pad (MAC)
                albo domyślnie dla wszystkich; próg niskiej baterii (ten sam ma ostrzeżenie na karcie pada)
  Wake-on-LAN   PS + przycisk przytrzymane na padzie, gdy receiver nie odpowiada -> nadajnik budzi komputer; MAC
                i broadcast wpisane albo automatyczne (wykryte przez nadajnik - widać je tutaj)
  Okno          ekran, na którym pojawia się okno (receiver_ui.ini, MainWindow.place_on_screen)
  Kopie         kopie settings.json / console.json / remap.json z aktualizacji (rx_backup): przywróć (dwa kliknięcia,
                potem restart receivera) albo zrób kopię teraz

"Zapisz": mysz, master, lightbar i Wake-on-LAN działają od razu (settings.json, bez restartu). Zmiana trybu konsoli zapisuje console.json
i prosi właściciela okna o restart receivera (sygnał saved) - po restarcie obowiązuje startup_mode jak przy autostarcie.
"""
from rx_i18n import _
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QDialog, QGridLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

import rx_audio
import rx_backup
import rx_console
import rx_lightbar
import rx_update
import rx_settings
import rx_state as S
from rx_buttons import LABELS
from rx_config import C
from rx_console import DEFAULT_APPS, STARTUP_MODES, ConsoleConfig
from rx_display import DPI_VALUES
from rx_i18n import LANGS
from rx_qt_icons import icon
from rx_qt_style import set_prop
from rx_qt_widgets import (Combo, ConfirmButton, SliderRow, button, field, group, icon_button, icon_label, input_items, label,
                           left, pair)
from rx_settings import (ABSENT, COMBO_BUTTONS, DEFAULT_PAD, LIGHT_MODES, LOW_PCT, MASTER_ACTIONS, MASTER_FEATURES, MOUSE_LIMITS,
                         WOL_BUTTONS, WOL_HOLD_MS, LightbarConfig, MasterConfig, MouseConfig, PadLight, WolConfig)

CAPTURE_TIMEOUT_MS = 15000
NO_AUDIO = "— nie zmieniaj dźwięku —"
TV_RESOLUTIONS = ((0, 0), (1920, 1080), (2560, 1440), (3840, 2160))
STARTUP_LABELS = {"extend": "Rozszerz: oba ekrany aktywne", "internal": "Tylko monitor"}
RESTORE_RESTART_MS = 1500     # po przywróceniu kopii: tyle widać komunikat, potem restart receivera


class ConsoleSettings(QDialog):
    saved = Signal()            # console.json zapisany - właściciel okna restartuje receiver
    applied = Signal()          # mysz / master zapisane i działają (bez restartu) - okno odświeża karty padów
    captured = Signal(str)      # nazwa przycisku z wątku sieci (Qt kolejkuje do wątku okna)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle(_("Ustawienia"))
        self.setMinimumWidth(640)
        self.resize(860, 780)
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
        self.tabs.addTab(_page(self._light_tab()), icon("lightbar"), _("Lightbar"))
        self.wol_page = _page(self._wol_tab())
        self.tabs.addTab(self.wol_page, icon("wake"), _("Wake-on-LAN"))
        self.tabs.addTab(_page(self._window_tab()), icon("window"), _("Okno"))
        self.tabs.addTab(_page(self._backup_tab()), icon("backup"), _("Kopie"))
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
                              "w grze podwójne kliknięcie nic nie robi, a włączenie po połączeniu mastera czeka na wyjście z gry. "
                              "Pusta lista = bez blokady."),
                            "dim", wrap=True),
                      field(_("Dozwolone programy (pulpit, Steam, launchery)"), self.apps),
                      left(apps_reset))
        note = label(_("Zmiana trybu konsoli lub języka uruchamia receiver od nowa (pady na chwilę znikną). Niezapisane "
                       "zmiany profilu (Zapisz w oknie głównym) zostaną utracone."), "dim", wrap=True)
        return [switching, tv, games, note]

    def _mouse_tab(self):
        lim = MOUSE_LIMITS                  # te same zakresy co settings.json - zapis nie obcina wartości
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

    def _backup_tab(self):
        self.backup_rows = QVBoxLayout()
        self.backup_rows.setSpacing(6)
        now = button(_("Zrób kopię teraz"), small=True)
        now.setIcon(icon("backup"))
        now.clicked.connect(self._backup_now)
        box = group(_("KOPIE USTAWIEŃ"),
                    label(_("Przed każdą aktualizacją receiver kopiuje settings.json, console.json i remap.json "
                            "(zostaje {n} ostatnich). Przywrócenie najpierw zapisuje kopię bieżących plików - też da się "
                            "je cofnąć - a potem uruchamia receiver od nowa.").format(n=rx_backup.KEEP), "dim", wrap=True),
                    self.backup_rows, left(now))
        return [box]

    def _load_backups(self):
        """Lista kopii od najnowszej: data, skąd się wzięła, pliki; "Przywróć" wymaga drugiego kliknięcia."""
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

    def _backup_row(self, b):
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

    def _backup_now(self):
        try:
            b = rx_backup.create("manual", rx_update.version()[0] or "")
        except OSError as e:
            self._status(_("Nie udało się zrobić kopii: {err}").format(err=e), "bad")
            return
        self._load_backups()
        self._status(_("✓ Kopia zapisana.") if b else _("Nie ma jeszcze plików ustawień do skopiowania."),
                     "ok" if b else "warn")

    def _restore(self, name):
        ok, msg = rx_backup.restore(name, rx_update.version()[0] or "")
        self._status(("✓ " if ok else "") + msg, "ok" if ok else "bad")
        self._load_backups()
        if ok:
            self._stop_capture()
            self.save_btn.setEnabled(False)       # Zapisz przed restartem nadpisałby właśnie przywrócone pliki
            QTimer.singleShot(RESTORE_RESTART_MS, self.saved.emit)   # ustawienia są czytane przy starcie

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

    def _light_tab(self):
        self._lights, self._color, self._light_key, self._loading = {}, "", DEFAULT_PAD, False
        self.low_pct = _spin(*LOW_PCT, 1, " %")
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
        pad = group(_("KOLOR LIGHTBARA"),
                    label(_("Gra ma pierwszeństwo: ten kolor świeci, dopóki gra sama nie ustawi lightbara. Miganie przy niskiej "
                            "baterii działa zawsze, a potwierdzenia (np. złoty błysk mastera) są na wierzchu."), "dim", wrap=True),
                    field(_("Pad"), self.light_pad), self.light_own,
                    pair(field(_("Tryb"), self.light_mode), field(_("Kolor"), _wrap(colors))),
                    self.light_blink)
        return [low, pad]

    def _wol_tab(self):
        self.wol_on = QCheckBox(_("Budzenie komputera z pada"))
        self.wol_on.setIcon(icon("wake"))
        self.wol_btn = Combo(input_items(WOL_BUTTONS, text=True))
        self.wol_hold = _spin(*WOL_HOLD_MS, 100, " ms")
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
    def _load_light(self, cfg):
        self.low_pct.setValue(cfg.low_pct)
        self._lights = dict(cfg.pads)
        items, known = [(DEFAULT_PAD, _("Domyślne - pady bez własnego ustawienia"), "lightbar")], {DEFAULT_PAD}
        for i in range(C.NUM_PADS):
            key = S.profile_key(i)
            if key not in known and S.pad_id[i] and (S.alive[i] or S.want[i].is_set()):
                known.add(key)
                items.append((key, _("Pad {n}  ·  {key}").format(n=i + 1, key=key)))
        items += [(k, _("{key}  (niepodłączony)").format(key=k)) for k in self._lights if k not in known]
        self.light_pad.set_items(items)
        self.light_pad.set_data(DEFAULT_PAD)
        self._pick_light_pad(DEFAULT_PAD)

    def _pick_light_pad(self, key):
        self._light_key = key
        light = self._lights.get(key) or self._lights.get(DEFAULT_PAD) or PadLight()
        self._loading = True
        self.light_own.setVisible(key != DEFAULT_PAD)
        self.light_own.setChecked(key == DEFAULT_PAD or key in self._lights)
        self.light_mode.set_data(light.mode)
        self.light_blink.setChecked(light.blink)
        self._set_color(light.color, store=False)
        self._loading = False
        self._light_enable()

    def _light_enable(self):
        own = self.light_own.isChecked()
        for w in (self.light_mode, self.light_color, self.light_blink):
            w.setEnabled(own)
        self.light_color.setEnabled(own and self.light_mode.currentData() == "solid")

    def _set_color(self, hex_color, store=True):
        self._color = hex_color
        rgb = rx_lightbar.hex_rgb(hex_color) if hex_color else C.IDLE_RGB
        text = rx_lightbar.rgb_hex(rgb) + ("" if hex_color else _("  (domyślny)"))
        fg = "#000000" if sum(rgb) > 380 else "#ffffff"
        self.light_color.setText(text)
        self.light_color.setStyleSheet(f"background: {rx_lightbar.rgb_hex(rgb)}; color: {fg};")
        if store:
            self._light_edited()

    def _pick_color(self):
        start = QColor(*(rx_lightbar.hex_rgb(self._color) if self._color else C.IDLE_RGB))
        c = QColorDialog.getColor(start, self, _("Kolor lightbara"))
        if c.isValid():
            self._set_color(c.name())

    def _light_edited(self, *_args):
        if getattr(self, "_loading", False):
            return
        key = self._light_key
        if key != DEFAULT_PAD and not self.light_own.isChecked():
            self._lights.pop(key, None)
        else:
            self._lights[key] = PadLight(mode=self.light_mode.currentData(), color=self._color,
                                         blink=self.light_blink.isChecked())
        self._light_enable()

    # ---------- Wake-on-LAN ----------
    def _load_wol(self, w):
        self.wol_on.setChecked(w.enabled)
        self.wol_btn.set_data(w.button)
        self.wol_hold.setValue(w.hold_ms)
        self.wol_mac.setText(w.mac)
        self.wol_bcast.setText(w.bcast)
        self._wol_seen()

    def _wol_seen(self):
        """Co nadajnik wykrył sam (pakiet build2) - podpowiedź w pustych polach."""
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

    def _collect_wol(self):
        """-> WolConfig albo ValueError z opisem pola."""
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

    # ---------- dane ----------
    def present(self):
        """Pokazuje okno; świeżo otwarte ma wartości z działającej konfiguracji (nie z poprzedniej, anulowanej edycji)."""
        if not self.isVisible():
            self.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def reload(self):
        self.save_btn.setEnabled(True)
        self._load_screens()
        self._load_backups()
        self._load_mouse(rx_settings.cur.mouse)
        self._load_master(rx_settings.cur.master)
        self._load_light(rx_settings.cur.lightbar)
        self._load_wol(rx_settings.cur.wol)
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
        self._master_touched = False          # lista mastera ruszana w tym oknie (sygnał changed - tylko od użytkownika)
        self.claim.setChecked(m.claim_hold)
        for k, cb in self.features.items():
            cb.setChecked(k in m.only_master)
        self.absent.set_data(m.when_absent)
        for k, combo in self.actions.items():
            combo.set_data(m.actions.get(k, ""))

    def collect_settings(self):
        mouse = MouseConfig(sens=round(self.sens.value(), 2), click_freeze_ms=round(self.freeze.value()),
                            click_rewind_ms=round(self.rewind.value()), drag_threshold=round(self.drag.value()))
        # master niezmieniany w tym oknie zostaje bieżący: korona na karcie pada albo PS + R3 przy otwartych Ustawieniach
        # nie może zostać cofnięta przez Zapisz (np. przy zmianie samej myszy)
        pad = (self.master_pad.currentData() or "") if self._master_touched else rx_settings.cur.master.pad
        master = MasterConfig(pad=pad, when_absent=self.absent.currentData(),
                              only_master=tuple(k for k, cb in self.features.items() if cb.isChecked()),
                              actions={k: c.currentData() or "" for k, c in self.actions.items()},
                              claim_hold=self.claim.isChecked())
        return rx_settings.Settings(mouse, master, rx_settings.UiConfig(lang=self.lang.currentData()),
                                    LightbarConfig(low_pct=self.low_pct.value(), pads=dict(self._lights)),
                                    self._collect_wol(), rx_settings.cur.log)

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


def _backup_reason(b):
    """Skąd kopia: aktualizacja (stara → nowa wersja), przed przywróceniem innej, ręczna."""
    if b.reason == "update":
        ver = f"{b.commit} → {b.new}" if b.commit and b.new else b.commit or b.new
        return _("przed aktualizacją") + (f" ({ver})" if ver else "")
    return _("przed przywróceniem kopii") if b.reason == "restore" else _("zrobiona ręcznie")


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
