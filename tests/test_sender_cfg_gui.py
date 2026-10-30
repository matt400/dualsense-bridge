"""Okno: zakładka Nadajnik - wiersze dla wszystkich ustawień, opisy, wybór wartości, powrót do pliku, komunikaty o kluczu / v3.
    python3 tests/test_sender_cfg_gui.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtWidgets import QApplication, QCheckBox, QDoubleSpinBox, QSpinBox
import cfgspec
import rx_qt_sendercfg as P
import rx_sender_cfg as SC
import rx_settings
from proto import Codec, CFGR_NO_KEY
from rx_qt_widgets import Combo
from chk import check, fails                                  # noqa: E402

app = QApplication.instance() or QApplication([])
wire = []
N.send_peer = lambda pkt: wire.append(pkt)
N.send_repeat = lambda pkt, n: wire.append(pkt)

infos, groups = P.infos(), P.group_titles()
names = [n for _t, g in groups for n in g]
check(set(infos) == set(cfgspec.BY_KEY) and len(names) == len(set(names)) == len(cfgspec.SPECS),
      "każde ustawienie ma opis i leży w dokładnie jednej grupie")
check(all(i.title and len(i.text) > 30 for i in infos.values()), "każde ustawienie ma tytuł i opis")
check(all(cfgspec.check(cfgspec.SPECS[cfgspec.BY_KEY[n]], i.default) is not None for n, i in infos.items()),
      "wartości domyślne w oknie mieszczą się w dozwolonych granicach")

panel = P.SenderCfgPanel()
sections = panel.sections()
check(len(sections) == 1 + len(groups), "strona: nagłówek i po sekcji na grupę")
for name, row in panel.rows.items():
    kind = row.spec.kind
    want = {cfgspec.KIND_BOOL: QCheckBox, cfgspec.KIND_CHOICE: Combo, cfgspec.KIND_INT: QSpinBox, cfgspec.KIND_FLOAT: QDoubleSpinBox}[kind]
    if not isinstance(row.control, want):
        check(False, f"{name}: zły rodzaj kontrolki")
check(True, "kontrolki pasują do rodzaju ustawienia")

panel.load(rx_settings.SenderConfig(values={"max_pads": 4}))
check(panel.rows["max_pads"].control.value() == 4, "zapisany wybór pokazany w polu")
check(panel.rows["grab_pad"].control.isChecked() is True, "bez nadajnika pole pokazuje wartość domyślną")

# nadajnik zgłosił wartości: pole bez wyboru pokazuje to, co działa
S.sender_cfg.clear()
for spec in cfgspec.SPECS:
    base = spec.choices[0] if spec.kind == cfgspec.KIND_CHOICE else spec.lo if spec.kind != cfgspec.KIND_BOOL else True
    S.sender_cfg[spec.key] = SC.Row(base, base, False, 0, time.monotonic())
S.sender_cfg["tap_max_move"] = SC.Row(77, 40, True, 0, time.monotonic())
S.sender_cfg_gen += 1
panel.refresh()
check(panel.rows["tap_max_move"].control.value() == 77, "zmiana z innego odbiornika widoczna w polu")
check("40" in panel.rows["tap_max_move"].badge.text(), "pod wierszem: wartość z pliku nadajnika")
check(not panel.rows["tap_max_move"].reset.isHidden(), "przycisk powrotu widoczny, gdy zmiana jest w mocy")

# edycja
panel.rows["gestures"].control.setChecked(False)
check(panel.collect().values.get("gestures") is False, "zmiana pola trafia do wyboru")
panel.rows["gestures"].control.setChecked(True)
check("gestures" not in panel.collect().values, "wartość równa plikowi nadajnika zdejmuje wpis")
panel.rows["speaker_volume"].control.setValue(200)
check(panel.collect().values["speaker_volume"] == 200 and "następnego strumienia" in panel.rows["speaker_volume"].badge.text(),
      "ustawienie ze strumienia: napis, że zadziała od następnego")
panel.rows["rumble"].control.set_data("off"); panel.rows["rumble"]._edited()
check(panel.collect().values["rumble"] == "off" and "ponownym połączeniu" in panel.rows["rumble"].badge.text(),
      "wybór z listy zapisany; napis o ponownym połączeniu pada")
panel.rows["pad_min_dt"].control.setValue(0.0025)
check(panel.collect().values["pad_min_dt"] == 0.0025, "pole dziesiętne trzyma 4 miejsca")
check(all(cfgspec.check(cfgspec.SPECS[cfgspec.BY_KEY[k]], v) == v for k, v in panel.collect().values.items()),
      "wszystko, co zbiera okno, przechodzi sprawdzenie cfgspec")

panel.reset("speaker_volume")
check("speaker_volume" not in panel.collect().values and panel.rows["speaker_volume"].control.value() == cfgspec.SPECS[cfgspec.BY_KEY["speaker_volume"]].lo,
      "powrót do pliku: wpis znika, pole pokazuje wartość nadajnika")
panel.reset()
check(panel.collect().values == {}, "powrót wszystkiego czyści wybór")

# komunikaty
S.last_pkt = time.monotonic()
N.codec = Codec()
panel.refresh()
check("HMAC" in panel.status.text() and panel.status.property("level") == "warn", "bez klucza: okno mówi, czego brakuje")
N.codec = Codec(b"k" * 32); N.codec.v3 = True
S.sender_cfg_result = CFGR_NO_KEY
panel.refresh()
check("nie ma klucza" in panel.status.text() or "HMAC" in panel.status.text(), "odmowa nadajnika pokazana z powodu")
S.sender_cfg_result = 0
S.sender_cfg.clear(); S.sender_cfg_gen += 1
panel.refresh()
check("Brak odpowiedzi" in panel.status.text(), "brak odpowiedzi (stary nadajnik) opisany")
S.peer = ("127.0.0.1", 5005)
wire.clear()
panel._reread()
check(len(wire) == 1, "przycisk odczytu pyta nadajnik")
panel.stop()

sys.exit(1 if fails else 0)
