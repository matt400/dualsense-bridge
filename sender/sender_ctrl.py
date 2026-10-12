"""Sender: stan jednego pada (klasa Ctrl) - budowanie i wysyłanie pakietów, rumble, lightbar."""
import asyncio
import glob
import os
import time
import zlib

import sender_net as net
from proto import (KEY_BIT, SENDER_VERSION, T_CLICK, T_IDENT, T_MOUSE, T_STATE, T_TOUCH, T_VERSION, WHY_OFF, clamp)
from sender_config import BURST_DELAYS, C, IDLE_TP, NEUTRAL_PAD, PAD_ABS, STICK_AXES


def find_led(pad_dev):
    try:
        ev = os.path.realpath(f"/sys/class/input/{os.path.basename(pad_dev.path)}")
        for led in glob.glob("/sys/class/leds/*:rgb:indicator"):
            hid = os.path.dirname(os.path.dirname(os.path.realpath(led)))
            if ev.startswith(hid + "/"):
                return led
    except OSError:
        pass
    return None


def pad_ident(key):
    """Stały identyfikator pada (max 17 B): MAC z uniq; dla długich kluczy skrót."""
    if len(key) <= 17:
        return key
    return "h" + format(zlib.crc32(key.encode()) & 0xFFFFFFFF, "08x")


class Ctrl:
    def __init__(self, idx, key, pad, motion, touch):
        self.idx, self.key = idx, key
        self.pad_dev, self.motion_dev, self.touch_dev = pad, motion, touch
        self.pad = list(NEUTRAL_PAD)
        self.hat = [0, 0]
        self.mask = 0
        self.motion = [0] * 6
        self.batt, self.flags = 255, 0
        self.touch = IDLE_TP
        self.tp = 0                   # 0 / 1 / 2 (klik z dwoma palcami)
        self.tap_id = 0
        self.t_state = 0.0
        self.last_key = None
        self.pending = False
        self.rumble = None            # sender_rumble.Rumble (ustawiane w run_ctrl)
        self.led = None
        self.led_color = None
        self.reports = {"pad": 0, "motion": 0}   # liczniki raportów urządzeń (Hz w pakiecie info)
        self.last_input = time.monotonic()        # ostatnie świadome użycie pada (do wyłączania po bezczynności)
        self.powering_off = 0.0
        self.closing = False          # run_ctrl sprząta - nie dołączaj już nowych urządzeń
        self.extra = set()            # zadania strumieni ruchu / touchpada (dołączane i odłączane niezależnie od pada)
        self.meters = {}              # RateMeter per urządzenie (tylko przy --stats)
        self._state_timer = None
        self.last_touch_send = 0.0
        self._touch_timer = None

    def devices(self):
        return [d for d in (self.pad_dev, self.motion_dev, self.touch_dev) if d]

    def set_abs(self, code, value):
        v = 0 if value < 0 else 255 if value > 255 else value
        if C.STICK_DZ and code in STICK_AXES and abs(v - 128) <= C.STICK_DZ:
            v = 128
        self.pad[code] = v

    def init_state(self):
        self.resync()
        self.led = find_led(self.pad_dev)

    def note_input(self, now):
        """Zapamiętuje czas użycia, jeśli stan to świadome użycie, a nie szum gałek ani leżący pad."""
        p = self.pad
        if (self.mask or self.hat[0] or self.hat[1] or p[2] > 30 or p[5] > 30
                or any(abs(p[k] - 128) > 40 for k in STICK_AXES)):
            self.last_input = now

    def resync(self):
        """Odczytuje pełny stan pada z urządzenia (start oraz po SYN_DROPPED, gdy bufor evdev się przepełnił)."""
        for c in PAD_ABS:
            try:
                self.set_abs(c, self.pad_dev.absinfo(c).value)
            except Exception:
                pass
        for i, c in enumerate((16, 17)):
            try:
                self.hat[i] = clamp(self.pad_dev.absinfo(c).value, -1, 1)
            except Exception:
                pass
        self.mask = 0
        try:
            for k in self.pad_dev.active_keys():
                self.mask |= KEY_BIT.get(k, 0)
        except OSError:
            pass

    # ---- wysyłanie ----
    def send_ident(self):
        net.emit(T_IDENT, self.idx, pad_ident(self.key).encode("ascii", "replace")[:17])
        r = self.rumble
        net.emit(T_VERSION, self.idx, SENDER_VERSION, r.bits if r is not None else 0, r.why if r is not None else WHY_OFF)

    def send_state(self, force=False):
        key = (*self.pad, *self.hat, self.mask, self.batt, self.flags, *self.motion)
        self.pending = False
        if not force and key == self.last_key:
            return
        self.last_key = key
        self.t_state = time.monotonic()
        net.emit(T_STATE, self.idx, *key)

    def request_send(self):
        """Wyślij zmieniony stan z limitem pad_min_dt: pierwsza zmiana po ciszy idzie jeszcze w tej iteracji pętli
        (call_soon - dziesiątki µs), kolejne w oknie zlewają się w jeden pakiet na jego końcu - nic nie ginie.
        call_soon zamiast wysłania od razu: jeden raport pada trafia do trzech urządzeń evdev (przyciski / gałki,
        ruch, touchpad), ich zdarzenia przychodzą w tej samej iteracji pętli - zmiany gałek i ruchu z jednego raportu
        idą jednym pakietem, a nie dwoma (drugi dopiero po pad_min_dt)."""
        self.pending = True
        if self._state_timer is not None:
            return                     # wysłanie zaplanowane - zabierze też tę zmianę
        wait = C.PAD_MIN_DT - (time.monotonic() - self.t_state)
        loop = asyncio.get_running_loop()
        self._state_timer = loop.call_soon(self._state_flush) if wait <= 0 else loop.call_later(wait, self._state_flush)

    def _state_flush(self):
        self._state_timer = None
        if self.pending:
            self.send_state()

    def send_touch(self, force=False):
        """Dotyk jest zlewany co touch_coalesce_ms; zmiana odrzucona przez okno nie ginie -
        zostaje wysłana z opóźnieniem (trailing flush), więc np. puszczenie palca nie czeka na heartbeat."""
        now = time.monotonic()
        wait = C.TOUCH_COALESCE_S - (now - self.last_touch_send)
        if not force and wait > 0:
            if self._touch_timer is None:
                self._touch_timer = asyncio.get_running_loop().call_later(wait, self._touch_flush)
            return
        if self._touch_timer is not None:
            self._touch_timer.cancel()
            self._touch_timer = None
        self.last_touch_send = now
        net.emit(T_TOUCH, self.idx, *self.touch[0], *self.touch[1])

    def _touch_flush(self):
        self._touch_timer = None
        self.send_touch(force=True)

    def send_click(self):
        net.emit(T_CLICK, self.idx, int(self.tp))

    def send_tap(self, flags):
        """Tap (bit0 LPM / bit2 PPM) z numerem w bitach 4-7, wysyłany 3x: zgubiony pakiet nie gubi kliknięcia,
        a odbiornik po numerze wykonuje klik tylko raz."""
        self.tap_id = self.tap_id % 15 + 1
        f = flags | (self.tap_id << 4)
        self.send_mouse(0, 0, f)
        loop = asyncio.get_running_loop()
        for d in BURST_DELAYS:
            loop.call_later(d, self.send_mouse, 0, 0, f)

    def send_mouse(self, dx, dy, flags):
        net.emit(T_MOUSE, self.idx, clamp(dx, -32768, 32767), clamp(dy, -32768, 32767), flags)

    def send_neutral(self):
        self.pad = list(NEUTRAL_PAD)
        self.hat = [0, 0]
        self.mask = 0
        self.motion = [0] * 6
        self.touch = IDLE_TP
        self.tp = 0
        self.send_state(True)
        self.send_touch(force=True)
        self.send_click()

    # ---- feedback z PC ----
    def apply_feedback(self, small, large, r, g, b):
        if self.rumble is not None and self.rumble.owns_lightbar:
            self.rumble.set(small, large, (r, g, b))     # wibracja i kolor w jednym raporcie HID
            return
        if self.rumble is not None:
            self.rumble.set(small, large)
        if self.led and (r, g, b) != self.led_color:
            try:
                with open(self.led + "/multi_intensity", "w") as f:
                    f.write(f"{r} {g} {b}")
                try:
                    with open(self.led + "/max_brightness") as f:
                        mb = f.read().strip()
                except OSError:
                    mb = "255"
                with open(self.led + "/brightness", "w") as f:
                    f.write(mb)
                self.led_color = (r, g, b)
            except OSError as e:
                print(f"Controller {self.idx}: lightbar unavailable: {e!r}")
                self.led = None

    def cancel_timers(self):
        for name in ("_state_timer", "_touch_timer"):
            t = getattr(self, name)
            if t is not None:
                t.cancel()
                setattr(self, name, None)

    def stop_feedback(self):
        if self.rumble is not None:
            self.rumble.close()
            self.rumble = None
