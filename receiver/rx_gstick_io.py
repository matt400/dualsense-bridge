"""Receiver: gyro -> gałka analogowa, część wykonawcza - wątek 250 Hz, ujście do wirtualnego pada, zapis per pad (MAC).

  Store        gyrostick.json obok remap.json: {"version": 1, "pads": {"<mac albo slotN>": {...Config...}}}
  StateSink    ujście: wpisuje wyjście silnika do stanu slotu (s["gs"]), rx_state.recompute nakłada je po remapie
  Runner       jeden Engine na slot; wątek czyta acc z rx_state, krokuje silnik i woła ujście tylko przy zmianie
  runner       instancja używana przez okno (rx_qt_gstick) i receiver_controller (start wątku)

Wątek nie zna Qt: okno odpytuje snapshot() timerem, a zmiany ustawień przekazuje przez set_config() (sygnał -> slot).
Twarde zerowanie (force release): wyłączenie funkcji, zmiana profilu / gry na pierwszym planie, utrata pada, zatrzymanie."""
import json
import os
import threading
import time
from dataclasses import replace

import rx_state as S
from proto import write_json
from rx_buttons import pressed_inputs
from rx_config import C
from rx_gstick import ST_OFF, Config, Engine, Sink, normalize, sprint_mask, to_raw

STEP_S = 0.004                 # 250 Hz
IDLE_WAIT_S = 0.25             # nic nie włączone: wątek śpi i budzi się przy zmianie ustawień
FILE_VERSION = 1


def path():
    """Obok remap.json (remap_file da się zmienić w konfiguracji - gyrostick.json idzie za nim)."""
    return os.path.join(os.path.dirname(C.REMAP_PATH), "gyrostick.json")


class Store:
    """Ustawienia gyro-gałki każdego pada. Klucz: identyfikator pada (MAC) albo "slotN"."""

    def __init__(self, file=None):
        self.file = file
        self._lock = threading.Lock()
        self._cfgs = None

    def _path(self):
        return self.file or path()

    def _load(self):
        """Wołać pod _lock."""
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

    def set(self, key, cfg):
        """Zapamiętuje i zapisuje atomowo (proto.write_json)."""
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
    """Ujście dla wirtualnego pada receivera (ViGEm DS4 / HIDMaestro DualSense czytają stan slotu). Inne ujścia
    (vJoy, uinput) = kolejne podklasy Sink; Runner zna tylko ten interfejs."""

    def drive(self, slot, target, x, y, button):
        self._set(slot, (target, x, y, button))

    def release(self, slot):
        self._set(slot, None)

    @staticmethod
    def _set(slot, gs):
        with S.lock:
            s = S.states[slot]
            if s["gs"] == gs:
                return
            s["gs"] = gs
            S.recompute(s, S.remap_maps[slot])
            s["ts"] = time.monotonic_ns() // 1000
            s["ver"] += 1
        S.notify(slot)


class Runner:
    def __init__(self, store=None, sink=None):
        self.store = store or Store()
        self.sink = sink or StateSink()
        self.engines = []
        self.wake = threading.Event()
        self.cal_msg = []                   # ostatni wynik kalibracji slotu: None / True / False (okno)
        self._driving = []                  # ujście ma teraz od nas wyjście
        self._keys = []                     # klucz pada, dla którego silnik ma ustawienia
        self._game = None

    def init(self):
        n = C.NUM_PADS
        self.engines = [Engine() for _ in range(n)]
        self.cal_msg = [None] * n
        self._driving = [False] * n
        self._keys = [None] * n

    # ---------- dla okna ----------
    def key(self, slot):
        return S.store.key_for(S.pad_id[slot], slot)

    def config(self, slot):
        return self.store.get(self.key(slot))

    def set_config(self, slot, cfg, save=True):
        """Nowe ustawienia pada (okno). Silnik przejmuje je w następnej próbce (Config jest niezmienny)."""
        if save:
            self.store.set(self.key(slot), cfg)
        self.engines[slot].cfg = cfg
        self.wake.set()

    def calibrate(self, slot):
        """Ustaw bieżące położenie pada jako środek (0, 0): uśrednia ~0,4 s, wynik w cal_msg i w ustawieniach."""
        self.cal_msg[slot] = None
        self.engines[slot].start_center(time.monotonic())
        self.wake.set()

    def reset_center(self, slot):
        self.set_config(slot, replace(self.config(slot), center=None))

    def snapshot(self, slot):
        return self.engines[slot].snapshot()

    def force_release_all(self):
        """Wszystkie sloty: gałki z powrotem w rękach fizycznego pada, silniki wyzerowane."""
        for i, e in enumerate(self.engines):
            e.release()
            self._push(i, e)

    # ---------- wątek ----------
    def _push(self, i, e):
        """Przekazuje wyjście silnika do ujścia (wołać, gdy e.send)."""
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
        if key != self._keys[i]:                     # pad dostał identyfikator (MAC) albo zmienił się: jego ustawienia
            self._keys[i] = key
            e.cfg = self.store.get(key)
            e.release()
            self._push(i, e)
        cfg = e.cfg
        with S.lock:
            s = S.states[i]
            acc, gyro, raw = s["acc"], s["gyro"], s["raw"]
        k0, k1, k2 = C.ACC_SIGN
        res = C.ACC_RES
        gdps = (gyro[0] ** 2 + gyro[1] ** 2 + gyro[2] ** 2) ** 0.5 / C.GYRO_RES
        held = cfg.clutch == "always" or cfg.clutch in pressed_inputs(*raw)
        alive = bool(S.alive[i])
        send = e.step(now, acc[0] / res * k0, acc[1] / res * k1, acc[2] / res * k2, gdps, held, alive)
        if send:
            self._push(i, e)
        return e.state != ST_OFF

    def _finish_calibration(self, i, e):
        res = e.cal_result
        if res is None or e.calibrating:
            return
        e.cal_result = None
        ok, center = res
        self.cal_msg[i] = ok
        if ok:
            self.set_config(i, replace(self.config(i), center=center))

    def loop(self):
        """Pętla 250 Hz; bez włączonej funkcji (i bez kalibracji) wątek tylko czeka."""
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


def thread():
    runner.loop()
