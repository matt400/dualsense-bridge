"""Receiver: regressions for the GUI hot-path cleanup - profile_changes() is cached and follows the store, the event log
appends a burst in one block (order and colours kept), the settings window lists audio outputs in a thread (a stale
listing is dropped), the .NET runtime probe is reused for a while, the Test panel redraws only on a state change.
    python3 tests/test_gui_hot_path.py receiver"""
import os
import sys
import types
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_events as ev, rx_hm, rx_qt
from proto import T_IDENT
from rx_console import Console, ConsoleConfig
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
A = "aa:aa:aa:aa:aa:01"

rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = Console(ConsoleConfig())
feed(T_IDENT, 0, A.encode()); state(0)
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 820); win.show(); pump(150)

# ---------- profile_changes: cached, invalidated by the store version ----------
calls_eff = []
real_eff = S.effective_profile
S.effective_profile = lambda *a, **k: (calls_eff.append(a), real_eff(*a, **k))[1]
S._changes.clear()
n0 = S.profile_changes(0)
S.profile_changes(0); S.profile_changes(0)
assert len(calls_eff) == 1, f"the profile was rebuilt on every call: {len(calls_eff)}"
prof = S.store.get(S.active[0])
prof["buttons"]["cross"] = "circle"
S.store.set_live(S.active[0], prof)
assert S.profile_changes(0) == n0 + 1 and len(calls_eff) == 2, "a store change must invalidate the cached count"
S.profile_changes(0)
assert len(calls_eff) == 2
S.effective_profile = real_eff
ok("profile_changes: cache + invalidation")

# ---------- the log: a burst goes in as separate, ordered paragraphs ----------
ev.clear()
win.log_btn.click(); pump()
win.log_panel.rebuild()
for text in ("first", "second", "third"):
    ev.info(text)
ev.error("boom")
win.log_panel.tick()
lines = win.log_panel.view.toPlainText().splitlines()
assert [x for x in lines if x][-4:] and "first" in lines[0] and "boom" in lines[-1], lines
assert win.log_panel.view.document().blockCount() == len(lines), "each entry must be its own paragraph"
ev.info("later"); win.log_panel.tick()
assert win.log_panel.view.toPlainText().splitlines()[-1].endswith("later")
ok("log: one-block append keeps order and paragraphs")

# ---------- settings window: audio listing in a thread, stale results dropped ----------
win.open_settings() if hasattr(win, "open_settings") else None
dlg = win.settings_dlg
if dlg is not None:
    dlg.reload(); pump(200)
    assert "Speakers" in [dlg.tv.itemText(i) for i in range(dlg.tv.count())], "the async listing should fill the combo"
    rx_audio.list_outputs = lambda: [("2", "Soundbar")]
    dlg.refresh_async(); pump(200)
    names = [dlg.tv.itemText(i) for i in range(dlg.tv.count())]
    assert "Soundbar" in names and "Speakers" not in names, names
    dlg.outputs_listed.emit((dlg._audio_gen - 1, "", "", ["Old"], None)); pump()
    assert "Old" not in [dlg.tv.itemText(i) for i in range(dlg.tv.count())], "a stale listing must be dropped"
    ok("settings: async audio listing + stale drop")

# ---------- .NET runtimes: one probe per TTL, reset() forces a new one ----------
probes = []
fake = types.ModuleType("clr_loader")
fake.find_runtimes = lambda: (probes.append(1), [types.SimpleNamespace(name="Microsoft.NETCore.App", version="10.0.1")])[1]
sys.modules["clr_loader"] = fake
rx_hm.reset()
for _ in range(5):
    assert len(rx_hm.net_runtimes()) == 1
assert len(probes) == 1, f"the dotnet probe ran {len(probes)} times"
rx_hm.reset(); rx_hm.net_runtimes()
assert len(probes) == 2, "reset() must drop the cached runtimes"
del sys.modules["clr_loader"]; rx_hm.reset()
ok("rx_hm.net_runtimes: cached for a while, reset() drops it")

# ---------- Test panel: redraws only when something it shows changed ----------
panel = win.test_panel
shown = []
real_refresh = panel.refresh
panel.refresh = lambda: (shown.append(1), real_refresh())[1]
live = {"ls": (128, 128), "rs": (128, 128)}
panel.tick(1.0, live)
shown.clear()
panel.tick(1.03, live); panel.tick(1.06, live)
assert not shown, f"refresh() ran {len(shown)} times without a state change"
S.alive[panel.slot] = not S.alive[panel.slot]
panel.tick(1.1, live)
assert shown, "a state change must refresh the panel"
S.alive[panel.slot] = not S.alive[panel.slot]
panel.refresh = real_refresh
ok("Test panel: refresh on change only")

print("GUI HOT PATH OK")
