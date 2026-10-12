"""Sender: stan jednego pada (klasa Ctrl) - budowanie i wysyłanie pakietów, rumble, lightbar."""
import asyncio
import glob
import os
import time
import zlib

import sender_net as net
from proto import (CAP_CALIB, KEY_BIT, LAT_NONE, RAW_STICKS_MAX_S, SENDER_VERSION, T_CLICK, T_IDENT, T_MOUSE, T_STATE,
                   T_STATE2, T_TOUCH, T_VERSION, TRIG2_OFF, WHY_OFF, clamp, trig2_from_legacy)
from sender_config import BURST_DELAYS, C, IDLE_TP, NEUTRAL_PAD, PAD_ABS, STICK_AXES


# lokalne potwierdzenia nadajnika: (mała wibracja, duża, kolor, czas s)
LOCAL_PULSES = {"wol": (0, 200, (255, 255, 255), 0.5),     # wysłano Wake-on-LAN - biały błysk
                "fail": (0, 120, (255, 0, 0), 0.6)}        # nie wysłano (brak MAC / sieci) - czerwony


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
        # pomiar opóźnienia (pakiet state2): czas w jądrze [µs, zegar zdarzeń evdev] najstarszej niewysłanej zmiany,
        # opóźnienie Bluetooth ponad minimum w tamtej chwili i bieżące (z sender_streams.BtDelay)
        self.t_event = None
        self.ev_bt = self.bt_us = LAT_NONE
        self.triggers = TRIG2_OFF     # efekty spustów z PC (wartości trig2: L2, R2, wibracja z gry)
        self.fb = None                # ostatni feedback z PC (mała, duża, r, g, b) - powrót po lokalnym błysku
        self.wol_timer = None         # odliczanie przytrzymania kombinacji Wake-on-LAN (sender_wol)
        self._pulse_timer = None
        self._pulse_back = None       # (0, 0, r, g, b) - stan po trwającym błysku
        self.stick_dz = C.STICK_DZ    # strefa martwa gałek; 0 na czas pomiaru dryfu z okna (CMD_RAW_STICKS)
        self._raw_timer = None
        self.gyro_bias = [0.0, 0.0, 0.0]   # bias żyroskopu odejmowany przed wysłaniem (kalibracja / autobias)
        self.cal_request = 0          # kalibracja z okna: tyle sekund (stream_motion ją zaczyna), 0 = brak
        self.cal_cmd_t = -1.0         # kiedy przyszło ostatnie polecenie kalibracji (powtórki UDP)

    def devices(self):
        return [d for d in (self.pad_dev, self.motion_dev, self.touch_dev) if d]

    def set_abs(self, code, value):
        v = 0 if value < 0 else 255 if value > 255 else value
        if self.stick_dz and code in STICK_AXES and abs(v - 128) <= self.stick_dz:
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
        net.emit(T_VERSION, self.idx, SENDER_VERSION, (r.bits if r is not None else 0) | CAP_CALIB,
                 r.why if r is not None else WHY_OFF)

    def mark_event(self, e):
        """Zdarzenie z jądra zmieniło stan (przycisk, gałka, ruch): zapamiętaj czas najstarszej niewysłanej zmiany."""
        if self.t_event is None:
            sec = getattr(e, "sec", None)            # zdarzenia w testach nie mają czasu
            if sec is not None:
                self.t_event = sec * 1_000_000 + e.usec
                self.ev_bt = self.bt_us

    def send_state(self, force=False):
        key = (*self.pad, *self.hat, self.mask, self.batt, self.flags, *self.motion)
        self.pending = False
        if not force and key == self.last_key:
            return
        fresh = key != self.last_key
        self.last_key = key
        self.t_state = time.monotonic()
        ev, self.t_event = self.t_event, None
        pc = net.pc_clock.now_us()
        if pc is None:                                   # receiver bez pong2 (starszy) albo brak synchronizacji
            net.emit(T_STATE, self.idx, *key)
            return
        phone = bt = LAT_NONE                            # powtórka (heartbeat, seria) - bez pomiaru
        if fresh and ev is not None:
            age = time.time_ns() // 1000 - ev            # zegar zdarzeń evdev = CLOCK_REALTIME
            if 0 <= age < LAT_NONE:
                phone, bt = age, self.ev_bt
        net.emit(T_STATE2, self.idx, *key, pc, phone, bt)

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
    def apply_feedback(self, small, large, r, g, b, remember=True):
        if remember:
            self.fb = (small, large, r, g, b)
            if self._pulse_timer is not None:      # PC ustawił coś nowego - lokalny błysk nie przywróci starego
                self._pulse_timer.cancel()
                self._pulse_timer = None
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

    def led_rgb(self):
        """Bieżący kolor lightbara (raport HID albo sysfs) albo None."""
        if self.rumble is not None and self.rumble.rgb is not None:
            return self.rumble.rgb
        if self.led:
            try:
                with open(self.led + "/multi_intensity") as f:
                    return tuple(int(x) for x in f.read().split()[:3])
            except (OSError, ValueError):
                pass
        return None

    def local_pulse(self, kind):
        """Potwierdzenie na padzie bez receivera (Wake-on-LAN): wibracja i kolor na chwilę, potem poprzedni kolor.
        Powrót bez wibracji: receiver milczy, a ostatni feedback z PC (np. wibracja gry sprzed wyłączenia komputera,
        już zatrzymana przez strażnika) nie może jej włączyć od nowa. Błysk w trakcie błysku wraca do koloru sprzed
        pierwszego, a nie do koloru błysku."""
        small, large, rgb, dur = LOCAL_PULSES[kind]
        if self._pulse_timer is not None:
            self._pulse_timer.cancel()
            back = self._pulse_back
        else:
            back = (0, 0, *(self.fb[2:] if self.fb else self.led_rgb() or (0, 0, 64)))
        self._pulse_back = back
        self.apply_feedback(small, large, *rgb, remember=False)
        try:
            self._pulse_timer = asyncio.get_running_loop().call_later(dur, self._pulse_end, back)
        except RuntimeError:
            self._pulse_timer = None

    def _pulse_end(self, back):
        self._pulse_timer = None
        self.apply_feedback(*back, remember=False)

    def set_triggers(self, vals):
        """Efekty spustów z profilu (pakiet trig2 albo stary trig = sam opór). Tylko raport HID - sterownik jądra nie
        ma takiej funkcji."""
        vals = tuple(vals)
        self.triggers = trig2_from_legacy(vals) if len(vals) == 4 else vals
        if self.rumble is not None:
            self.rumble.set_triggers(self.triggers)

    def set_raw_sticks(self, secs):
        """Pomiar dryfu z okna: gałki bez strefy martwej nadajnika przez secs sekund (0 = od razu z powrotem).
        Wraca sama po czasie - receiver, który zniknie w trakcie pomiaru, nie zostawi pada bez strefy martwej.
        Stan jest odczytany od nowa: gałka przycięta do 128 musi pokazać prawdziwe położenie (i odwrotnie)."""
        if self._raw_timer is not None:
            self._raw_timer.cancel()
            self._raw_timer = None
        secs = clamp(secs, 0, RAW_STICKS_MAX_S)
        self.stick_dz = 0 if secs else C.STICK_DZ
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if secs and loop is not None:
            self._raw_timer = loop.call_later(secs, self.set_raw_sticks, 0)
        if self.pad_dev is not None:
            self.resync()
            if loop is not None:
                self.request_send()

    def cancel_timers(self):
        for name in ("_state_timer", "_touch_timer", "wol_timer", "_pulse_timer", "_raw_timer"):
            t = getattr(self, name)
            if t is not None:
                t.cancel()
                setattr(self, name, None)

    def stop_feedback(self):
        if self.rumble is not None:
            self.rumble.close()
            self.rumble = None
