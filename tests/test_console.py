"""Console mode (rx_console): config, double click, ON/OFF, auto-OFF, start, command errors, button choice."""
import json, os, sys, threading, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_audio, rx_console
from rx_console import Console, ConsoleConfig

PATH = os.path.join(TMP, "console.json"); C.CONSOLE_PATH = PATH
from chk import check, fails, wait_for, ok, raises

# ---------- system commands replaced by a call log ----------
calls = []
rx_console._display = lambda mode: calls.append(("display", mode))
rx_console._audio = lambda name: calls.append(("audio", name))
rx_console._toast = lambda title, msg="": calls.append(("toast", title, msg))
rx_console._tv_mode = lambda w, h, sc: calls.append(("tv", w, h, sc))


class Clock:
    """The time module seen by rx_console and rx_net: monotonic() moves only when the test calls advance()."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def advance(self, s):
        self.t += s

    def __getattr__(self, name):
        return getattr(time, name)


clock = Clock()
rx_console.time = N.time = clock          # double-click window, auto-OFF, test end, pending auto-ON, the 1 s after a bye
rx_console.TICK_S = 3600.0                # no periodic _tick from the worker: the test runs it with tick(c)


def idle(c, timeout=5.0):
    """Wait until the worker thread has run every queued job (the jobs run in order)."""
    done = threading.Event()
    c._jobs.put(done.set)
    return done.wait(timeout)

def tick(c):
    """Run the periodic _tick in the worker thread (where it normally runs) and wait for it."""
    c._jobs.put(c._tick)
    return idle(c)

def turned(c, on=True):
    """Mode switched AND the whole switch carried out: _apply sets c.on first, and the screen / sound / toast
    after it - checking the call log right after c.on changed caught the switch halfway."""
    return wait_for(lambda: c.on == on) and idle(c)

import rx_settings as _R
from dataclasses import replace as _replace
# click tests: master set to an absent pad - without a master the first connected pad turns the mode on (auto_pad)
_R.apply(_replace(_R.cur, master=_replace(_R.cur.master, pad="00:00:00:00:00:ff")))


def new_console(**kw):
    """Console with a worker thread; the startup (extend) already processed and cleared from the call log.
    Click tests use PS (the harder case: PS is also the shortcut modifier)."""
    kw.setdefault("toggle_button", "ps")
    c = Console(ConsoleConfig(**kw)); calls.clear(); c.start()
    idle(c); calls.clear()                 # the start (screen AND sound) has already been carried out in full
    rx_console.ctl = c
    return c

def click(i=0, button=316, gap=0.05):
    state(i, mask=BIT[button]); clock.advance(gap); state(i, mask=0)

PS, SHARE = 316, 314

# ---------- config: no file, bad values, damaged file ----------
cfg = rx_console.load(PATH)
check(cfg == ConsoleConfig() and os.path.exists(PATH), "no file -> defaults and console.json created")
check(json.load(open(PATH)) == {"toggle_button": "r2", "double_click_ms": 400, "audio_tv": "", "audio_pc": "",
                                "timeout_s": 60, "startup_mode": "extend", "tv_width": 2560, "tv_height": 1440,
                                "tv_scale": 200, "auto_on_master": True, "allowed_apps": list(rx_console.DEFAULT_APPS),
                                "version": 3}, "default values in the file (R2, TV 1440p / 200 %)")
json.dump({"toggle_button": "ps", "double_click_ms": 300, "audio_tv": "", "audio_pc": "", "timeout_s": 60,
           "startup_mode": "extend"}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg.toggle_button == "r2" and cfg.double_click_ms == 300 and json.load(open(PATH))["version"] == 3,
      f"old file (no version) with PS -> R2, the rest stays: {cfg}")
json.dump({"toggle_button": "mute", "version": 2}, open(PATH, "w"))
check(rx_console.load(PATH).toggle_button == "r2", "version 2 with mute (removed) -> R2")
json.dump({"toggle_button": "ps", "version": 2}, open(PATH, "w"))
check(rx_console.load(PATH).toggle_button == "ps", "PS chosen on purpose in version 2 stays")
json.dump({**json.load(open(PATH)), "toggle_button": "ps"}, open(PATH, "w"))
check(rx_console.load(PATH).toggle_button == "ps", "PS chosen on purpose in a new file stays")
json.dump({"toggle_button": "share", "double_click_ms": 250, "audio_tv": " LG TV ", "audio_pc": "Głośniki",
           "timeout_s": 90, "startup_mode": "internal"}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg == ConsoleConfig("share", 250, "LG TV", "Głośniki", 90, "internal"), f"a valid file loaded: {cfg}")
json.dump({"toggle_button": "nie-ma", "double_click_ms": "szybko", "timeout_s": True, "startup_mode": "tv", "audio_tv": 5}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg == ConsoleConfig(), "only bad values -> defaults")
json.dump({"toggle_button": "cross", "double_click_ms": 99999, "timeout_s": 120}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg.toggle_button == "cross" and cfg.timeout_s == 120 and cfg.double_click_ms == 400, "a bad value replaced, good ones stay")
check(json.load(open(PATH))["double_click_ms"] == 400 and "startup_mode" in json.load(open(PATH)), "file fixed and filled with the missing keys")
json.dump({"toggle_button": "r2", "double_click_ms": 100}, open(PATH, "w"))
cfg = rx_console.load(PATH)
check(cfg.double_click_ms == 400 and json.load(open(PATH))["double_click_ms"] == 400,
      "100 ms gap (too short for two presses, e.g. after scrolling the wheel over the field) goes back to 400")
open(PATH, "w").write("{ to nie jest json")
cfg = rx_console.load(PATH)
check(cfg == ConsoleConfig() and os.path.exists(PATH + ".bad") and json.load(open(PATH)), "damaged file -> defaults, the old one as .bad")
open(PATH, "w").write("[1, 2]")
check(rx_console.load(PATH) == ConsoleConfig(), "file with a list instead of an object -> defaults")
rx_console.save(ConsoleConfig("l2", 300, "A", "B", 45, "extend"), PATH)
check(rx_console.load(PATH) == ConsoleConfig("l2", 300, "A", "B", 45, "extend") and not os.path.exists(PATH + ".tmp"), "write and read, no temporary files")
ok("config: defaults, fixes, damaged file, atomic write")

# ---------- button detection ----------
det = rx_console.detector
check(det("ps")(BIT[PS], 0, 0, 0, 0) and not det("ps")(BIT[SHARE], 0, 0, 0, 0), "przycisk")
check(det("dpad_up")(0, 0, -1, 0, 0) and not det("dpad_up")(0, 0, 1, 0, 0) and det("dpad_left")(0, -1, 0, 0, 0), "d-pad")
check(det("r2")(0, 0, 0, 0, 200) and not det("r2")(0, 0, 0, 0, 10) and det("l2")(0, 0, 0, 255, 0), "triggers")
ok("detector: buttons, d-pad, triggers")

# ---------- double click toggles ON/OFF (through the real rx_net.handle) ----------
c = new_console(audio_tv="LG TV", audio_pc="Głośniki", double_click_ms=400)
click(); clock.advance(0.05); click()
check(turned(c), "PS double click -> ON")
check(calls == [("display", "tv"), ("tv", 2560, 1440, 200), ("audio", "LG TV"), ("toast", "Tryb konsoli włączony", "")],
      f"ON: screen, TV resolution and scale, TV sound, toast: {calls}")
check(S.states[0]["raw"][0] == 0, "pad state still tracked (buttons reach the game)")
calls.clear()
click(); clock.advance(0.05); click()
check(turned(c, False), "second double click -> OFF")
check(calls == [("display", "extend"), ("audio", "Głośniki"), ("toast", "Tryb konsoli wyłączony", "")], f"OFF: screen, PC sound, toast: {calls}")
ok("double click: ON (TV + audio_tv + toast), OFF (extend + audio_pc + toast)")

calls.clear()
click(); clock.advance(0.5); click(); clock.advance(0.5); click()
idle(c)
check(not c.on and not calls, "clicks further apart than double_click_ms switch nothing")
calls.clear(); clock.advance(0.5)     # the previous click has to be outside the window
click(); clock.advance(0.05); click(); clock.advance(0.05); click()
check(turned(c), "a series of clicks switches")
idle(c)
check(len([x for x in calls if x[0] == "toast"]) == 1, f"three quick clicks = one switch: {calls}")
ok("sparse clicks ignored, a series does not switch twice")

# another pad: two clicks on different pads are not a double click
c.on = False; calls.clear(); clock.advance(0.5)        # the last click of the series is outside the window
click(0); click(1)
idle(c)
check(not calls, "a click on two different pads does not count as a double click")
# a button other than the configured one
click(0, SHARE); click(0, SHARE); idle(c)
check(not calls, "a double click of another button does nothing")
ok("clicks on different pads / another button ignored")

# ---------- the network does not wait for screen commands ----------
c.on = False; calls.clear()
rx_console._display = lambda mode: (time.sleep(0.6), calls.append(("display", mode)))
t0 = time.monotonic(); click(gap=0.0); click(gap=0.0)
check(time.monotonic() - t0 < 0.3, f"packet handling does not wait for DisplaySwitch ({time.monotonic() - t0:.3f} s)")
check(wait_for(lambda: ("display", "tv") in calls, 3), "the command still runs in the background")
rx_console._display = lambda mode: calls.append(("display", mode))
ok("the receive loop is not blocked (commands in a separate thread)")
c.close(restart=True)

# ---------- auto-OFF after silence ----------
c = new_console(audio_tv="LG TV", audio_pc="Głośniki", timeout_s=5)
c._apply(True); calls.clear()
now = clock.monotonic()
for i in range(C.NUM_PADS):
    S.last_seen[i] = now
c._since = now - 6                       # turned on long ago: only the fresh packets keep it on
c._tick()
check(c.on and not calls, "fresh packets -> stays ON")
for i in range(C.NUM_PADS):
    S.last_seen[i] = now - 6
c._since = now - 6
c._tick()
check(not c.on and calls[:2] == [("display", "extend"), ("audio", "Głośniki")] and calls[2][0] == "toast"
      and calls[2][1] == "Tryb konsoli wyłączony" and "5 s" in calls[2][2], f"silence > timeout_s -> OFF + toast: {calls}")
calls.clear(); c._tick()
check(not calls, "in OFF mode silence does nothing")
c._apply(True); c._since = clock.monotonic(); calls.clear()
for i in range(C.NUM_PADS):
    S.last_seen[i] = 0.0
c._tick()
check(c.on and not calls, "a mode turned on by hand does not turn off at once (counted from when it was turned on)")
c.close(restart=True)
ok("auto-OFF: after timeout_s of silence, not earlier")

# ---------- start: extend / internal, PC sound ----------
for mode in ("extend", "internal"):
    calls.clear(); cc = Console(ConsoleConfig(audio_pc="Głośniki", startup_mode=mode)); cc.start()
    check(wait_for(lambda: ("audio", "Głośniki") in calls) and calls == [("display", mode), ("audio", "Głośniki")] and not cc.on,
          f"start {mode}: {calls}")
    cc.close(restart=True)
calls.clear(); cc = Console(ConsoleConfig(startup_mode="extend")); cc.start(); wait_for(lambda: calls)
idle(cc); check(calls == [("display", "extend")], f"empty audio_pc -> sound unchanged: {calls}"); cc.close(restart=True)
ok("start: DisplaySwitch /extend lub /internal + audio_pc")

# ---------- command errors do not crash the script ----------
def boom(*a, **k): raise OSError("no such command")
rx_console._display, rx_console._audio, rx_console._toast, rx_console._tv_mode = boom, boom, boom, boom
c = new_console(audio_tv="X", audio_pc="Y"); calls.clear()
c._jobs.put(c._toggle)
check(turned(c), "every command raises, and the mode switches anyway")
c._jobs.put(c._toggle)
check(turned(c, False) and c._thread.is_alive(), "the worker thread survives the errors")
rx_console._display = lambda mode: calls.append(("display", mode))
rx_console._audio = lambda name: calls.append(("audio", name))
rx_console._toast = lambda title, msg="": calls.append(("toast", title, msg))
c._jobs.put(c._toggle)
check(wait_for(lambda: ("display", "tv") in calls and ("audio", "X") in calls), "after the fix the commands keep working")
rx_console._tv_mode = lambda w, h, sc: calls.append(("tv", w, h, sc))
c.close(restart=True)
ok("screen / sound / toast errors logged, nothing crashes")

# ---------- Windows restores "extend" after power-on -> switch to the TV again ----------
screens = [2, 1]
rx_console._screens = lambda: screens.pop(0) if screens else 1
c = new_console(); calls.clear()
def run(fn):                                           # in the worker thread (like _tick), no race with it
    c._jobs.put(fn); idle(c)
run(lambda: c._apply(True))
check(calls.count(("display", "tv")) == 1 and len(c._verify) == len(rx_console.VERIFY_AFTER_S), "turn on + check plan")
run(lambda: (setattr(c, "_verify", [0.0, 0.0, 0.0]), c._tick()))
check(calls.count(("display", "tv")) == 2, f"two screens after turning on -> TV once more: {calls}")
run(c._tick)
check(calls.count(("display", "tv")) == 2 and not c._verify, "TV only now - stop watching")
run(lambda: c._apply(False))
check(not c._verify, "off - no checking")
rx_console._screens = lambda: None
run(lambda: (c._apply(True), setattr(c, "_verify", [0.0]), c._tick()))
check(calls.count(("display", "tv")) == 3 and not c._verify, "cannot check the screens - no retry")
c.close(restart=True)
ok("console mode: the picture returns to both screens -> switch to the TV again")

# ---------- choosing a button on the pad ----------
c = new_console(); got = []
state(0, mask=BIT[SHARE])                                  # held at the moment of arming - does not count
c.arm_capture(got.append)
state(0, mask=BIT[SHARE] | BIT[PS])
check(got == ["ps"], f"captured a newly pressed button (not a held one): {got}")
state(0, mask=0); calls.clear(); c.on = False
click(0, PS); click(0, PS)
check(turned(c) and got == ["ps"], "after capture the callback is one-shot, and the double click works again")
c.on = False; calls.clear()
c.arm_capture(got.append); click(0, PS); click(0, PS); idle(c)
check(len(got) == 2 and not calls and not c.on, "while choosing a button a double click does not switch the mode")
c.arm_capture(got.append); c.cancel_capture(); state(0, mask=BIT[SHARE]); state(0, mask=0)
check(len(got) == 2, "a cancelled choice captures nothing")
c.arm_capture(got.append); state(0, hat=(0, -1)); state(0)
check(got[-1] == "dpad_up", f"the d-pad can be chosen too: {got}")
c.arm_capture(got.append); state(0, rt=200); state(0)
check(got[-1] == "r2", f"a trigger too: {got}")
c.close(restart=True)
ok("button choice: the first newly pressed, one-shot, without switching the mode")

# ---------- Test: ON with the given config, then OFF by itself ----------
rx_console.TEST_S = 0.4
c = new_console(audio_tv="stare TV", audio_pc="stare PC"); calls.clear()
c.test(ConsoleConfig(audio_tv="nowe TV", audio_pc="nowe PC"))
check(turned(c) and ("audio", "nowe TV") in calls, "test: ON with the values from the window")
clock.advance(0.3); tick(c)
check(c.on and ("audio", "nowe PC") not in calls, "test: still ON before TEST_S")
clock.advance(0.2); tick(c)
check(("audio", "nowe PC") in calls, "test: OFF after TEST_S with the same values")
check(not c.on and ("audio", "stare TV") not in calls, "the test does not use the stored configuration")
c.close(restart=True)
ok("Test: ON and automatic return, with the settings from the window")

# ---------- closing: ON -> back to the monitor (no toast); a restart does not switch ----------
c = new_console(audio_pc="Głośniki"); c._apply(True); calls.clear()
c.close(restart=False)
check(("display", "extend") in calls and ("audio", "Głośniki") in calls and not any(x[0] == "toast" for x in calls) and not c.on,
      f"exit with the mode on -> extend, no toast: {calls}")
c = new_console(audio_pc="Głośniki"); c._apply(True); calls.clear()
c.close(restart=True)
check(not calls, "on a restart the screen is left to the new process start")
ok("closing: ON goes back to extend, a restart does not repeat switches")

# ---------- PS shortcuts are not clicks ----------
from proto import T_CLICK
c = new_console(); calls.clear()
def combo(second, gap=0.03):
    state(0, mask=BIT[PS]); clock.advance(gap); second(); clock.advance(gap); state(0, mask=0); clock.advance(gap)
for _ in range(2):                                         # PS + Share (screenshot) twice in a row
    combo(lambda: state(0, mask=BIT[PS] | BIT[SHARE]))
idle(c)
check(not c.on and not any(x[0] == "display" for x in calls), f"PS + Share twice does not switch the mode: {calls}")
for _ in range(2):                                         # PS + d-pad (profile change) twice
    combo(lambda: state(0, mask=BIT[PS], hat=(1, 0)))
idle(c)
check(not c.on, "PS + d-pad twice does not switch the mode")
for _ in range(2):                                         # PS + touchpad click (mouse / touch) twice
    combo(lambda: (feed(T_CLICK, 0, 1), feed(T_CLICK, 0, 0)))
idle(c)
check(not c.on, "PS + touchpad click twice does not switch the mode")
for m in (BIT[SHARE], BIT[SHARE] | BIT[PS], BIT[SHARE], BIT[SHARE] | BIT[PS], BIT[SHARE], 0):
    state(0, mask=m); clock.advance(0.03)
idle(c)
check(not c.on, "PS pressed while Share is held is a shortcut too")
clock.advance(0.5)
click(); clock.advance(0.05); click()
check(turned(c), "a plain double click after shortcuts still works")
c.close(restart=True)
ok("PS shortcuts (Share, d-pad, touchpad) do not count as clicks")

# ---------- TV resolution / scale ----------
c = new_console(tv_width=0, tv_height=0, tv_scale=0); calls.clear()
c._apply(True); c._apply(False)
check(not any(x[0] == "tv" for x in calls), f"0 = no resolution or scale change: {calls}")
c.close(restart=True)
c = new_console(tv_width=3840, tv_height=2160, tv_scale=0); calls.clear()
c._apply(True); c._apply(False)
check([x for x in calls if x[0] == "tv"] == [("tv", 3840, 2160, 0)], f"only on ON, with the values from the config: {calls}")
c.close(restart=True)
json.dump({"tv_width": 2560, "tv_height": 1440, "tv_scale": 130, "version": 2}, open(PATH, "w"))
check(rx_console.load(PATH).tv_scale == 200, "scale outside the Windows steps -> default")
import rx_display as D
check(D.pick_mode([(2560, 1440, 32, 60), (2560, 1440, 32, 120), (1920, 1080, 32, 144), (2560, 1440, 16, 144)], 2560, 1440)
      == (2560, 1440, 32, 120) and D.pick_mode([(1920, 1080, 32, 60)], 2560, 1440) is None, "mode choice: 32 bpp, highest Hz")
check(D.scale_relative(200, -2, 0, 7) == (2, False) and D.scale_relative(150, -2, 0, 7) == (0, True),
      "scale relative to the recommended one (recommended 150 %: 200 % = +2)")
try:
    D.scale_relative(500, -1, 0, 3); check(False, "scale outside the screen range -> error")
except ValueError as e:
    check("not supported" in str(e), str(e))
steps = iter([[1, 2], [1, 2], [("luid", 3, "DISPLAY2")]])      # Windows switches the screen layout with a delay
D.active_sources = lambda: next(steps)
D.set_resolution = lambda dev, w, h: f"{w}x{h}"
D.set_scale = lambda a, sid, p: f"scale {p}%"
sleep = D.time.sleep
D.time.sleep = lambda s: None
check(D.apply_tv(2560, 1440, 200, settle_s=5) == "DISPLAY2: 2560x1440, scale 200%", "apply_tv: waits for one screen, then the mode and the scale")
D.active_sources = lambda: [1, 2]
raises(RuntimeError, lambda: D.apply_tv(2560, 1440, 200, settle_s=0), "two screens -> no change")
D.time.sleep = sleep                                             # the time module is shared - restore it
ok("TV: resolution and scale only on ON, mode choice, relative scale, waiting for the screen")

# ---------- R2 as the mode button (default): hysteresis, combinations ----------
c = new_console(toggle_button="r2"); calls.clear()
def r2(v, mask=0):
    state(0, rt=v, mask=mask); clock.advance(0.03)
r2(255); r2(0); r2(255); r2(0)
check(turned(c), "double full R2 -> ON")
c.on = False; calls.clear(); clock.advance(0.5)
for v in (150, 200, 170, 199, 160, 0):                     # jitter around the threshold is one press (hysteresis)
    r2(v)
idle(c)
check(not c.on, "trigger jitter at the threshold is not a double click")
clock.advance(0.5)
for v in (60, 150, 0, 70, 0):                               # light presses (in game) do not count
    r2(v)
idle(c)
check(not c.on, "light R2 presses do not switch")
r2(255, BIT[304]); r2(0); r2(255, BIT[304]); r2(0); idle(c)
check(not c.on, "R2 with another button (in game) is not a click")
c.on = False; calls.clear(); clock.advance(0.5)
for v in (255, 120, 255, 120):                              # quick taps: the trigger comes back only halfway (not to zero)
    r2(v)
check(turned(c), "R2 double tap without a full release -> ON")
c.on = False; calls.clear(); clock.advance(0.5)
for v in (200, 128, 200, 128, 0):                           # the release threshold is a 1/8 step (rx_net calls only on a step change)
    r2(v)
check(not c.on, "the trigger did not drop below half the peak - one press")
c.on = False; calls.clear(); clock.advance(0.5)
for v in (200, 120, 200, 120):
    r2(v)
check(turned(c), "peak 200: a drop below 128 (rx_net step) counts as a release")
c.close(restart=True)
ok("R2: a double deep press switches, jitter / light / with another button does not")

# ---------- auto-ON when the master pad connects, manual off, restart ----------
import rx_settings
from dataclasses import replace
from proto import T_IDENT, T_BYE
MAC = "aa:bb:cc:00:00:01"
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad=MAC)))
c = new_console(toggle_button="r2"); calls.clear()
feed(T_IDENT, 1, MAC.encode())
check(turned(c) and any(x[0] == "toast" and "master" in x[2] for x in calls), f"master connected -> ON: {calls}")
clock.advance(0.5)
for v in (255, 0, 255, 0):
    state(1, rt=v); clock.advance(0.03)
check(turned(c, False), "double R2 on the master -> manual OFF")
clock.advance(0.3); feed(T_IDENT, 1, MAC.encode()); idle(c)
check(not c.on, "the same pad still connected (ident unchanged) - the mode stays off")
feed(T_BYE, 1); clock.advance(1.1); feed(T_IDENT, 1, MAC.encode())
check(turned(c), "master reconnects -> ON again")
c.close(restart=True)
c = new_console(toggle_button="r2", auto_on_master=False); feed(T_BYE, 1); clock.advance(1.1)
feed(T_IDENT, 1, MAC.encode()); idle(c)
check(not c.on, "auto_on_master = false -> no automation")
c.close(restart=True)
feed(T_IDENT, 0, b"cc:cc:cc:00:00:09"); clock.advance(0.2)
c = new_console(toggle_button="r2"); feed(T_BYE, 0); clock.advance(1.1); feed(T_IDENT, 0, b"cc:cc:cc:00:00:09"); idle(c)
check(not c.on, "a plain pad (not the master) does not turn the mode on")
c.close(restart=True)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="")))
c = new_console(toggle_button="r2"); feed(T_BYE, 0); feed(T_BYE, 1); clock.advance(1.1); calls.clear()
feed(T_IDENT, 0, b"cc:cc:cc:00:00:09")
check(turned(c), "no master: the first connected pad turns the mode on (after a PC start the mode stayed off)")
c._jobs.put(c._toggle); check(turned(c, False), "manual OFF")
feed(T_IDENT, 1, MAC.encode()); idle(c)
check(not c.on, "no master: a second pad joining later does not turn on a mode switched off by hand")
c.close(restart=True)
feed(T_BYE, 0); feed(T_BYE, 1); clock.advance(1.1)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad=MAC)))
os.environ[rx_console.RESTART_ENV] = "0"
c = new_console(toggle_button="r2"); feed(T_BYE, 1); clock.advance(1.1); feed(T_IDENT, 1, MAC.encode()); idle(c)
check(not c.on and rx_console.RESTART_ENV not in os.environ, "restart with the mode off: the master coming back does not turn it on")
c.close(restart=True)
feed(T_BYE, 1); clock.advance(1.1)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="")))
os.environ[rx_console.RESTART_ENV] = "0"
c = new_console(toggle_button="r2")
state(0); feed(T_IDENT, 0, b"cc:cc:cc:00:00:09")   # the first packet, right after it the identifier
check(idle(c) and not c.on, "no master: restart with the mode off - the ident after the first packet does not turn it on")
c.close(restart=True)
feed(T_BYE, 0); clock.advance(1.1)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad=MAC)))
os.environ[rx_console.RESTART_ENV] = "1"
calls.clear(); c = Console(ConsoleConfig()); c.start()
check(turned(c) and ("display", "tv") in calls, f"restart with the mode on: ON at once: {calls}")
c.close(restart=True)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="")))
ok("auto-ON: master connects, manual OFF, reconnect, can be disabled, a restart keeps the state")

# ---------- game in the foreground: switching blocked, auto-ON waits ----------
import rxenv
pulses = []
F.pulse = lambda i, kind, force=False: pulses.append((i, kind))
c = new_console(toggle_button="r2"); calls.clear()
rxenv.FG[0] = "ittakestwo.exe"
for v in (255, 0, 255, 0):
    state(0, rt=v); clock.advance(0.03)
idle(c)
check(not c.on and (0, "denied") not in pulses, f"game in the foreground: double R2 does nothing and does not flash red: {pulses}")
rxenv.FG[0] = "steamwebhelper.exe"; clock.advance(0.5)
for v in (255, 0, 255, 0):
    state(0, rt=v); clock.advance(0.03)
check(turned(c), "Steam / Big Picture in the foreground: switches")
c.close(restart=True)
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad=MAC)))
c = new_console(toggle_button="r2"); calls.clear()
rxenv.FG[0] = "ittakestwo.exe"
feed(T_BYE, 1); clock.advance(1.1); feed(T_IDENT, 1, MAC.encode())
for _ in range(15):
    state(1); clock.advance(0.1)                            # pad alive, the game still in the foreground
tick(c)
check(not c.on and c._pending == 1, "auto-ON during a game waits")
import rx_events
check(any("ittakestwo.exe" in e.text and "czeka" in e.text for e in rx_events.since(0)),
      "auto-ON waiting shows in the log (with the program name)")
rxenv.FG[0] = "explorer.exe"
for _ in range(15):
    state(1); clock.advance(0.1)
tick(c)
check(c.on, "game closed (desktop in the foreground) -> auto-ON")
check(any("czekania" in e.text for e in rx_events.since(0)), "in the log: turned on after waiting (how many seconds)")
c.close(restart=True)
c = new_console(toggle_button="r2", allowed_apps=("explorer.exe",)); rxenv.FG[0] = "steam.exe"
for v in (255, 0, 255, 0):
    state(0, rt=v); clock.advance(0.03)
idle(c)
check(not c.on, "the allow list from the settings (steam.exe removed) applies")
c.close(restart=True)
rxenv.FG[0] = None
rx_settings.apply(replace(rx_settings.cur, master=replace(rx_settings.cur.master, pad="")))
ok("game in the foreground: R2 blocked, auto-ON waits until the game exits, list from the settings")

# ---------- PC shutdown / sleep with console mode on ----------
c = new_console(audio_pc="Speakers"); calls.clear()
check(rx_console.power_off("x") is False and not calls, "mode off: nothing switches")
c._jobs.put(lambda: c._apply(True, toast=False)); turned(c); calls.clear()
check(rx_console.power_off("wyłączanie komputera") is True, "mode on -> True")
check(not c.on and ("display", "extend") in calls and ("audio", "Speakers") in calls and not any(x[0] == "toast" for x in calls),
      f"before shutdown: both screens (Windows remembers the layout), PC sound, no toast: {calls}")
calls.clear()
c._jobs.put(lambda: c._apply(True, toast=False)); turned(c)
c._jobs.put(lambda: time.sleep(1.0)); calls.clear()           # worker thread busy
rx_console.POWER_WAIT_S, wait = 0.2, rx_console.POWER_WAIT_S
t0 = time.monotonic(); rx_console.power_off("x")
check(time.monotonic() - t0 < 0.8 and not c.on and calls == [("display", "extend")], f"busy thread: switches at once: {calls}")
rx_console.POWER_WAIT_S = wait
idle(c)                                                      # the busy job and the queued switch both ran
check(calls.count(("display", "extend")) == 1, f"a pending job does not switch a second time: {calls}")
c.close(restart=True)
import rx_display
topo = []
rx_display._user32 = lambda: types.SimpleNamespace(SetDisplayConfig=lambda *a: topo.append(a) or 0)
rx_display.set_topology("extend"); rx_display.set_topology("tv")
check([a[-1] for a in topo] == [0x84, 0x88] and topo[0][:4] == (0, None, 0, None), f"SetDisplayConfig: APPLY | TOPOLOGY: {topo}")
rx_display._user32 = lambda: types.SimpleNamespace(SetDisplayConfig=lambda *a: 87)
raises(OSError, lambda: rx_display.set_topology("extend"), "SetDisplayConfig error -> exception")
ok("PC shutdown / sleep: OFF first (extend), also with a busy thread; SetDisplayConfig")

# ---------- rx_audio.find ----------
devs = [("1", "Speakers (Realtek)"), ("2", "LG TV (NVIDIA High Definition Audio)"), ("3", "Headphones (USB)")]
check(rx_audio.find("speakers (realtek)", devs) == "1", "exact name, case-insensitive")
check(rx_audio.find("LG TV", devs) == "2", "the only device containing the text")
check(rx_audio.find("(", devs) is None and rx_audio.find("nie ma", devs) is None, "ambiguous / missing -> None")
ok("rx_audio.find")

print("FAIL" if fails else "PASS", "console:", len(fails), "failures" if fails else "all OK")
sys.exit(1 if fails else 0)
