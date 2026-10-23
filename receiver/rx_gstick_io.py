"""Receiver: gyro -> analog stick, the runtime part - a 250 Hz thread, the sink to the virtual pad, per-pad (MAC) storage.

  Store        gyrostick.json obok remap.json: {"version": 1, "pads": {"<mac albo slotN>": {...Config...}}}
  StateSink    sink: writes the engine output into the slot state (s["gs"]); rx_state.recompute applies it after the remap
  Runner       one Engine per slot; the thread reads acc from rx_state, steps the engine and calls the sink only on a change
  runner       the instance used by the window (rx_qt_gstick) and receiver_controller (starts the thread)

The thread knows nothing about Qt: the window polls snapshot() with a timer and passes setting changes via set_config() (signal -> slot).
Force release: disabling the feature, a profile / foreground game change, losing the pad, stopping.
"""
import json
import os
import threading
import time
from dataclasses import replace

import rx_feedback as F
import rx_state as S
from proto import write_json
from rx_buttons import pressed_inputs
from rx_config import C
from rx_gstick import EV_DEAD_IN, EV_NONE, EV_OUTER_IN, FORCE, ST_OFF, Config, Engine, Sink, normalize, sprint_mask, to_raw

FEEDBACK_GAP_S = 0.15           # minimum gap between two feedback pulses of a pad (no chatter on a zone edge)
STEP_S = 0.004                 # 250 Hz
IDLE_WAIT_S = 0.25             # nothing enabled: the thread sleeps and wakes on a settings change
FILE_VERSION = 1


def path():
    """Next to remap.json (remap_file can be changed in the config - gyrostick.json follows it)."""
    return os.path.join(os.path.dirname(C.REMAP_PATH), "gyrostick.json")


class Store:
    """Gyro-stick settings of each pad. Key: the pad identifier (MAC) or "slotN"."""

    def __init__(self, file=None) -> None:
        self.file = file
        self._lock = threading.Lock()
        self._cfgs = None

    def _path(self):
        return self.file or path()

    def _load(self):
        """Call under _lock."""
        if self._cfgs is None:
            self._cfgs = {}
            try:
                with open(self._path(), encoding="utf-8") as f:
                    pads = json.load(f).get("pads", {})
                for k, v in pads.items():
                    self._cfgs[str(k).lower()] = normalize(v)
            except FileNotFoundError:
                pass
            except (OSError, ValueError, AttributeError) as e:
                print(f"Gyro stick file {self._path()}: cannot read ({e}) - default settings")
        return self._cfgs

    def get(self, key):
        with self._lock:
            return self._load().get(key.lower()) or Config()

    def set(self, key, cfg) -> bool:
        """Remembers and saves atomically (proto.write_json)."""
        with self._lock:
            cfgs = self._load()
            cfgs[key.lower()] = cfg
            try:
                write_json(self._path(), {"version": FILE_VERSION, "pads": {k: to_raw(c) for k, c in cfgs.items()}},
                           indent=2, sort_keys=True)
            except OSError as e:
                print(f"Gyro stick file {self._path()}: cannot save ({e})")
                return False
            return True


class StateSink(Sink):
    """Sink for the receiver's virtual pad (ViGEm DS4 / HIDMaestro DualSense read the slot state). Other sinks
    (vJoy, uinput) = further Sink subclasses; Runner knows only this interface.
    """

    def drive(self, slot, target, x, y, button) -> None:
        self._set(slot, (target, x, y, button))

    def release(self, slot) -> None:
        self._set(slot, None)

    @staticmethod
    def _set(slot, gs) -> None:
        with S.lock:
            s = S.states[slot]
            if s["gs"] == gs:
                return
            s["gs"] = gs
            S.recompute(s, S.remap_maps[slot])
            s["ts"] = time.monotonic_ns() // 1000
            s["ver"] += 1
        S.notify(slot)


FEEDBACK_SUFFIX = {"haptic": "h", "led": "l", "both": "b"}


class Runner:
    def __init__(self, store=None, sink=None, pulse=None) -> None:
        self.store = store or Store()
        self.sink = sink or StateSink()
        self.pulse = pulse or F.pulse           # (slot, kind, force) -> a short rumble / colour pulse on the pad
        self.engines = []
        self.wake = threading.Event()
        self.cal_msg = []                   # ostatni wynik kalibracji slotu: None / True / False (okno)
        self._driving = []                  # the sink currently holds our output
        self._keys = []                     # the pad key the engine's settings belong to
        self._fb_t = []                     # when the last feedback pulse of the slot was sent
        self._game = None

    def init(self) -> None:
        """One engine per slot (receiver_controller at startup; the window entry points call it too if nobody did)."""
        n = C.NUM_PADS
        self.engines = [Engine() for _ in range(n)]
        self.cal_msg = [None] * n
        self._driving = [False] * n
        self._keys = [None] * n
        self._fb_t = [0.0] * n

    # ---------- dla okna ----------
    def key(self, slot):
        return S.store.key_for(S.pad_id[slot], slot)

    def config(self, slot):
        return self.store.get(self.key(slot))

    def _ready(self) -> None:
        if not self.engines:
            self.init()

    def set_config(self, slot, cfg, save=True) -> None:
        """New pad settings (from the window). The engine picks them up on the next sample (Config is immutable)."""
        self._ready()
        if save:
            self.store.set(self.key(slot), cfg)
        self.engines[slot].cfg = cfg
        self.wake.set()

    def calibrate(self, slot) -> None:
        """Sets the current pad position as the center (0, 0): averages ~0.4 s, the result goes to cal_msg and the settings."""
        self._ready()
        self.cal_msg[slot] = None
        self.engines[slot].start_center(time.monotonic())
        self.wake.set()

    def reset_center(self, slot) -> None:
        self.set_config(slot, replace(self.config(slot), center=None))

    def cal_result(self, slot, clear=False):
        """The last calibration outcome of the slot (None / True / False); clear=True forgets it."""
        self._ready()
        msg = self.cal_msg[slot]
        if clear:
            self.cal_msg[slot] = None
        return msg

    def set_preview(self, slot, on) -> None:
        """The tilt window is open for the slot: the preview follows the pad even while the feature is switched off."""
        self._ready()
        self.engines[slot].preview = on
        self.wake.set()

    def snapshot(self, slot):
        self._ready()
        return self.engines[slot].snapshot()

    def force_release_all(self) -> None:
        """All slots: the sticks go back to the physical pad, the engines are zeroed."""
        for i, e in enumerate(self.engines):
            e.release()
            self._push(i, e)

    # ---------- thread ----------
    def _push(self, i, e) -> None:
        """Passes the engine output to the sink (call when e.send)."""
        out = e.out
        if out is None:
            if self._driving[i]:
                self.sink.release(i)
                self._driving[i] = False
            return
        cfg = e.cfg
        self.sink.drive(i, cfg.target, out[0], out[1], sprint_mask(cfg.sprint) if out[2] else 0)
        self._driving[i] = True

    def _step_slot(self, i, now):
        e = self.engines[i]
        key = self.key(i)
        if key != self._keys[i]:                     # the pad got an identifier (MAC) or it changed: load its settings
            self._keys[i] = key
            e.cfg = self.store.get(key)
            e.release()
            self._push(i, e)
        cfg = e.cfg
        with S.lock:
            s = S.states[i]
            acc, gyro, raw, gs = s["acc"], s["gyro"], s["raw"], s["gs"]
        if gs is None and self._driving[i]:         # the slot state was reset (a gap in the data): put our output back
            e.sent = FORCE
            self._driving[i] = False
        k0, k1, k2 = C.ACC_SIGN
        res = C.ACC_RES
        gdps = (gyro[0] ** 2 + gyro[1] ** 2 + gyro[2] ** 2) ** 0.5 / C.GYRO_RES
        held = cfg.clutch == "always" or cfg.clutch in pressed_inputs(*raw)
        alive = bool(S.alive[i])
        send = e.step(now, acc[0] / res * k0, acc[1] / res * k1, acc[2] / res * k2, gdps, held, alive)
        if send:
            self._push(i, e)
        if e.event != EV_NONE and cfg.feedback != "off" and cfg.enabled:
            self._feedback(i, e.event, cfg.feedback, now)
        return e.state != ST_OFF

    def _feedback(self, slot, event, mode, now) -> None:
        """Rumble / lightbar pulse on a zone crossing: full tilt reached (edge) or back in the deadzone (center). Only these two
        crossings are signalled - leaving the deadzone and leaving the outer zone would tick on every small move.
        """
        kind = "edge" if event == EV_OUTER_IN else "center" if event == EV_DEAD_IN else None
        if kind is None or now - self._fb_t[slot] < FEEDBACK_GAP_S:
            return
        self._fb_t[slot] = now
        self.pulse(slot, f"tilt_{kind}_{FEEDBACK_SUFFIX[mode]}", force=True)    # force: independent of the "confirmations" setting

    def _finish_calibration(self, i, e) -> None:
        res = e.cal_result
        if res is None or e.calibrating:
            return
        e.cal_result = None
        ok, center = res
        self.cal_msg[i] = ok
        if ok:
            self.set_config(i, replace(self.config(i), center=center))

    def loop(self) -> None:
        """The 250 Hz loop; with nothing enabled (and no calibration) the thread just waits."""
        from rx_winapi import boost_thread
        boost_thread()
        nxt = time.perf_counter()
        while not S.stop.is_set():
            busy = False
            now = time.monotonic()
            if S.game != self._game:                  # zmiana okna na pierwszym planie = bezpieczne zerowanie
                self._game = S.game
                for i, e in enumerate(self.engines):
                    if e.cfg.focus_release:
                        e.release()
                        self._push(i, e)
            for i, e in enumerate(self.engines):
                if self._step_slot(i, now):
                    busy = True
                if e.cal_result is not None:
                    self._finish_calibration(i, e)
                busy = busy or e.calibrating
            if not busy:
                self.wake.wait(IDLE_WAIT_S)
                self.wake.clear()
                nxt = time.perf_counter()
                continue
            nxt += STEP_S
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.perf_counter()
        self.force_release_all()


runner = Runner()


def thread() -> None:
    runner.loop()
