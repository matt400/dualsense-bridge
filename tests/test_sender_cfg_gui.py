"""Window: the Sender tab - rows for every setting, descriptions, choosing values, back to the file, key / v3 messages.
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
      "every setting has a description and sits in exactly one group")
check(all(i.title and len(i.text) > 30 for i in infos.values()), "every setting has a title and a description")
check(all(cfgspec.check(cfgspec.SPECS[cfgspec.BY_KEY[n]], i.default) is not None for n, i in infos.items()),
      "default values in the window are within the allowed limits")

panel = P.SenderCfgPanel()
sections = panel.sections()
check(len(sections) == 1 + len(groups), "page: a header and one section per group")
WIDGET = {cfgspec.KIND_BOOL: QCheckBox, cfgspec.KIND_CHOICE: Combo, cfgspec.KIND_INT: QSpinBox, cfgspec.KIND_FLOAT: QDoubleSpinBox}
wrong = [name for name, row in panel.rows.items() if not isinstance(row.control, WIDGET[row.spec.kind])]
check(not wrong, f"controls match the setting kind (wrong kind: {wrong})")

panel.load(rx_settings.SenderConfig(values={"max_pads": 4}))
check(panel.rows["max_pads"].control.value() == 4, "the stored choice shown in the field")
check(panel.rows["grab_pad"].control.isChecked() is True, "without a sender the field shows the default value")

# the sender reported values: a field without a choice shows what is in effect
S.sender_cfg.clear()
for spec in cfgspec.SPECS:
    base = spec.choices[0] if spec.kind == cfgspec.KIND_CHOICE else spec.lo if spec.kind != cfgspec.KIND_BOOL else True
    S.sender_cfg[spec.key] = SC.Row(base, base, False, 0, time.monotonic())
S.sender_cfg["tap_max_move"] = SC.Row(77, 40, True, 0, time.monotonic())
S.sender_cfg_gen += 1
panel.refresh()
check(panel.rows["tap_max_move"].control.value() == 77, "a change from another receiver visible in the field")
check("40" in panel.rows["tap_max_move"].badge.text(), "under the row: the value from the sender file")
check(not panel.rows["tap_max_move"].reset.isHidden(), "the back button visible while a change is in effect")

# edycja
panel.rows["gestures"].control.setChecked(False)
check(panel.collect().values.get("gestures") is False, "a field change goes into the choice")
panel.rows["gestures"].control.setChecked(True)
check("gestures" not in panel.collect().values, "a value equal to the sender file removes the entry")
panel.rows["speaker_volume"].control.setValue(200)
check(panel.collect().values["speaker_volume"] == 200 and "następnego strumienia" in panel.rows["speaker_volume"].badge.text(),
      "a stream setting: a note that it takes effect from the next one")
panel.rows["rumble"].control.set_data("off"); panel.rows["rumble"]._edited()
check(panel.collect().values["rumble"] == "off" and "ponownym połączeniu" in panel.rows["rumble"].badge.text(),
      "a choice from the list stored; a note about reconnecting the pad")
panel.rows["pad_min_dt"].control.setValue(0.0025)
check(panel.collect().values["pad_min_dt"] == 0.0025, "a decimal field keeps 4 places")
check(all(cfgspec.check(cfgspec.SPECS[cfgspec.BY_KEY[k]], v) == v for k, v in panel.collect().values.items()),
      "everything the window collects passes the cfgspec check")

panel.reset("speaker_volume")
check("speaker_volume" not in panel.collect().values and panel.rows["speaker_volume"].control.value() == cfgspec.SPECS[cfgspec.BY_KEY["speaker_volume"]].lo,
      "back to the file: the entry is gone, the field shows the sender value")
panel.reset()
check(panel.collect().values == {}, "everything back clears the choice")

# komunikaty
S.last_pkt = time.monotonic()
N.codec = Codec()
panel.refresh()
check("HMAC" in panel.status.text() and panel.status.property("level") == "warn", "no key: the window says what is missing")
N.codec = Codec(b"k" * 32); N.codec.v3 = True
S.sender_cfg_result = CFGR_NO_KEY
panel.refresh()
check("nie ma klucza" in panel.status.text() or "HMAC" in panel.status.text(), "a sender refusal shown with the reason")
S.sender_cfg_result = 0
S.sender_cfg.clear(); S.sender_cfg_gen += 1
panel.refresh()
check("Brak odpowiedzi" in panel.status.text(), "no reply (old sender) described")
S.peer = ("127.0.0.1", 5005)
wire.clear()
panel._reread()
check(len(wire) == 1, "the read button asks the sender")
panel.stop()

sys.exit(1 if fails else 0)
