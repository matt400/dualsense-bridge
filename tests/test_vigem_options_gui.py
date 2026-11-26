"""Settings window: without HIDMaestro the game haptics / speaker settings and the HIDMaestro fields are greyed out with the reason.
    python3 tests/test_vigem_options_gui.py receiver"""
import io, os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtWidgets import QApplication

import rx_ds4, rx_hm, rx_hm_update, rx_ptp, rx_qt_console
from chk import check, fails, ok
app = QApplication.instance() or QApplication([])
d = rx_qt_console.ConsoleSettings(None)
d.reload()


def game_widgets() -> list:
    return [d.hap_on, d.hap_gain, d.spk_on, d.spk_gain, d.eq_preset, *d.eq_rows]


def choose(backend: str, note: str | None = None) -> None:
    rx_ds4.hm_note = note
    d.vpad_backend.set_data(backend)
    d._vpad_state()


choose("vigem")
check(not any(w.isEnabled() for w in game_widgets()), "vigem: haptics / speaker settings are grey")
check(not d.vpad_auto.isEnabled() and not d.vpad_dll.isEnabled(), "vigem: the HIDMaestro fields are grey")
check(not d.hap_backend_note.isHidden() and "ViGEmBus" in d.hap_backend_note.text(), "vigem: the Haptics tab says why")
check(not d.vpad_limits.isHidden() and "ViGEmBus" in d.vpad_limits.text(), "vigem: the Pads tab says why")

choose("auto")
check(all(w.isEnabled() for w in game_widgets()), "auto with HIDMaestro: settings usable")
check(d.vpad_auto.isEnabled() and d.vpad_dll.isEnabled(), "auto: the HIDMaestro fields are usable")
check(d.hap_backend_note.isHidden() and d.vpad_limits.isHidden(), "auto with HIDMaestro: no notes")

choose("auto", "no DLL")
check(not any(w.isEnabled() for w in game_widgets()), "fallback to ViGEm: haptics / speaker settings are grey")
check("no DLL" in d.hap_backend_note.text(), "fallback: the reason is shown")
check(d.vpad_auto.isEnabled() and d.vpad_dll.isEnabled(), "fallback: the HIDMaestro fields stay usable (they can fix it)")

# ---------- who handles the touchpad ----------
rx_hm.find_dll = lambda _p="": "HIDMaestro.Core.dll"
choose("vigem")
check(not d.tp_engine.isEnabled() and not d.tp_note.isHidden() and d.sens.isEnabled(),
      "vigem: the driver cannot be picked, a warning, the receiver's sliders work")
d.tp_engine.set_data("driver")
d._touchpad_state()
check(d.tp_engine.isEnabled() and d.sens.isEnabled(), "vigem with a stored driver choice: changeable, the receiver still handles it")
choose("auto")
check(d.tp_note.isHidden() and not d.tp_info.isHidden() and not d.sens.isEnabled(), "driver available: Windows handles it, sliders grey")
check(d.collect_settings().mouse.engine == "driver", "the choice is saved")
rx_ptp.error = "boom"
d._touchpad_state()
check("boom" in d.tp_note.text() and d.sens.isEnabled(), "a failed create: the reason, the receiver's sliders work")
rx_ptp.error = ""

rx_hm_update.PINNED_TAG, rx_hm_update.upstream = "v2.4.0", "v2.5.0"
check(rx_hm_update.newer_release() == "v2.5.0", "a newer release is reported")
rx_hm_update.upstream = "v2.4.0"
check(rx_hm_update.newer_release() == "", "the pinned release is not news")

rx_hm_update.upstream = ""
rx_hm_update.check_newer(lambda _url: io.BytesIO(b'{"tag_name": "v3.0.0"}'))
check(rx_hm_update.upstream == "v3.0.0", "the tag is read from GitHub's answer")
rx_hm_update.check_newer(lambda _url: io.BytesIO(b"<html>"))
check(rx_hm_update.upstream == "v3.0.0", "a bad answer keeps what was known")

rx_ds4.hm_note = None
ok("settings greyed without HIDMaestro")
d.close()
sys.exit(1 if fails else 0)
