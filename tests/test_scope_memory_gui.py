"""Settings window: the pad picked in the Lightbar / Haptics tabs (and "Default settings" switched off) survives a restart, and
every value of the per-pad tabs makes it through settings.json and back.
    python3 tests/test_scope_memory_gui.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

import rx_qt_console, rx_qt_console_haptics
import rx_settings
from proto import T_IDENT
from rx_settings import DEFAULT_PAD, PadHaptics, PadLight, PadSpeaker
from chk import check, fails
app = QApplication.instance() or QApplication([])
A = "aa:aa:aa:aa:aa:01"
feed(T_IDENT, 0, A.encode()); state(0)

ini = os.path.join(TMP, "scope_ui.ini")
if os.path.exists(ini):
    os.remove(ini)


class Owner(QWidget):
    """The main window's part the settings dialog uses: its receiver_ui.ini."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(ini, QSettings.Format.IniFormat)


def open_dialog() -> tuple[Owner, rx_qt_console.ConsoleSettings]:
    """A settings window the way a freshly started receiver builds it (new objects, the same ini file)."""
    owner = Owner()
    d = rx_qt_console.ConsoleSettings(owner)
    d.reload()
    return owner, d


# ---------- first start: the defaults are shown ----------
owner, d = open_dialog()
check(d.hap_scope.use_default.isChecked() and d.light_scope.use_default.isChecked(), "first start: both tabs edit the defaults")

# ---------- the user unchecks "Default settings" on both tabs ----------
d.hap_scope.use_default.setChecked(False)
d.light_scope.use_default.setChecked(False)
check(d._haptics_key == A and d._light_key == A, f"unchecked: the connected pad is edited ({d._haptics_key!r}, {d._light_key!r})")
d.light_game.setChecked(False)
d.spk_on.setChecked(True)
d.spk_gain.setValue(135)
d._save()
del d, owner

# ---------- restart: new objects, settings.json and receiver_ui.ini from disk ----------
rx_settings.apply(rx_settings.load(C.SETTINGS_PATH))
owner, d = open_dialog()
check(not d.hap_scope.use_default.isChecked() and not d.light_scope.use_default.isChecked(),
      "after a restart \"Default settings\" stays unchecked on both tabs")
check(d.hap_scope.key() == A and d.light_scope.key() == A, "after a restart the same pad is selected")
check(d.spk_on.isChecked() and d.spk_gain.value() == 135 and not d.light_game.isChecked(), "the pad's own values came back too")

# ---------- back to the defaults is remembered as well ----------
d.hap_scope.use_default.setChecked(True)
del d, owner
owner, d = open_dialog()
check(d.hap_scope.use_default.isChecked() and not d.light_scope.use_default.isChecked(),
      "the Haptics tab remembers the defaults, the Lightbar tab still the pad (separate memory)")

# ---------- a remembered pad that is gone: the defaults, not an empty or broken selection ----------
QSettings(ini, QSettings.Format.IniFormat).setValue("scope_lightbar", "cc:cc:cc:cc:cc:09")
owner, d = open_dialog()
check(d.light_scope.use_default.isChecked() and d.light_scope.key() == DEFAULT_PAD, "an unknown remembered pad falls back to the defaults")

# ---------- no main window (tests, embedding): nothing is remembered and nothing breaks ----------
d = rx_qt_console.ConsoleSettings()
d.reload()
d.hap_scope.use_default.setChecked(False)
check(d._ui is None and d._saved_scope(rx_qt_console_haptics.SCOPE_HAPTICS) == DEFAULT_PAD, "without an owner window the entry is not stored")

# ---------- every per-pad value survives settings.json ----------
s = rx_settings.Settings(
    lightbar=rx_settings.LightbarConfig(low_pct=22, pads={"*": PadLight("battery", "", False, False), A: PadLight("solid", "#12ab34", True, False)}),
    haptics=rx_settings.HapticsConfig(pads={"*": PadHaptics(True, 80), A: PadHaptics(False, 130)}),
    speaker=rx_settings.SpeakerConfig(pads={A: PadSpeaker(True, 135, (3, 0, -2, 0, 4))}),
    ui=rx_settings.UiConfig(lang="en", pads_mode="fixed", pads_count=3),
    wol=rx_settings.WolConfig(enabled=False, mac="aa:bb:cc:dd:ee:ff", bcast="192.168.1.255", button="share", hold_ms=2000),
    vpad=rx_settings.VpadConfig(backend="vigem", fixed_slots=False, hm_dll="C:\\x", hm_auto=False))
path = os.path.join(TMP, "roundtrip.json")
rx_settings.save(s, path)
back = rx_settings.load(path)
for name in ("lightbar", "haptics", "speaker", "ui", "wol", "vpad"):
    check(getattr(back, name) == getattr(s, name), f"settings.json round trip: {name}")

sys.exit(1 if fails else 0)
