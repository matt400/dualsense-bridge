"""Sender: state of a single pad (the Ctrl class) - building and sending packets, rumble, lightbar."""
import asyncio
import glob
import os
import time
import zlib

import sender_net as net
from proto import (CAP_CALIB, KEY_BIT, LAT_NONE, RAW_STICKS_MAX_S, SENDER_VERSION, T_CLICK, T_IDENT, T_MOUSE, T_STATE,
                   T_STATE2, T_TOUCH, T_VERSION, TRIG2_OFF, WHY_OFF, clamp, trig2_from_legacy)
from sender_config import BURST_DELAYS, C, IDLE_TP, NEUTRAL_PAD, PAD_ABS, STICK_AXES


# local confirmations by the sender: (small rumble, large rumble, colour, duration s)
LOCAL_PULSES = {"wol": (0, 200, (255, 255, 255), 0.5),     # Wake-on-LAN was sent - white flash
                "fail": (0, 120, (255, 0, 0), 0.6)}        # not sent (no MAC / network) - red


def find_led(pad_dev):
    """Find the sysfs lightbar (RGB indicator LED) that belongs to the same HID device as the pad's evdev node.

    The pad's /sys/class/input/eventN path is compared against the parent HID device of every
    `*:rgb:indicator` LED: the LED whose HID device is a prefix of the input path is the pad's own lightbar.
    Returns the LED directory or None (no kernel lightbar driver / no permission).
    """
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
    """Stable pad identifier (max 17 B): the MAC from uniq; a hash for long keys.

    The receiver uses it to select the per-pad profile, so it must stay the same across reconnects and restarts.
    """
    if len(key) <= 17:
        return key
    return "h" + format(zlib.crc32(key.encode()) & 0xFFFFFFFF, "08x")


class Ctrl:
    """Everything the sender knows about one pad: its evdev devices, the current input state and the send machinery.

    The input streams (sender_streams) write into the state fields (pad axes, hat, button mask, motion, touch); the
    send_* methods turn that state into packets for slot `idx`. Sending is rate-limited by timers kept on the object,
    and feedback from the PC (rumble, lightbar, adaptive triggers) flows back through apply_feedback / set_triggers.
    """

    def __init__(self, idx, key, pad, motion, touch) -> None:
        self.idx, self.key = idx, key
        self.pad_dev, self.motion_dev, self.touch_dev = pad, motion, touch
        self.pad = list(NEUTRAL_PAD)
        self.hat = [0, 0]
        self.mask = 0
        self.motion = [0] * 6
        self.batt, self.flags = 255, 0
        self.touch = IDLE_TP
        self.tp = 0                   # 0 / 1 / 2 (click with two fingers)
        self.tap_id = 0
        self.t_state = 0.0
        self.last_key = None
        self.pending = False
        self.rumble = None            # sender_rumble.Rumble (set in run_ctrl)
        self.led = None
        self.led_color = None
        self.reports = {"pad": 0, "motion": 0}   # device report counters (Hz in the info packet)
        self.last_input = time.monotonic()        # the last deliberate use of the pad (for idle power-off)
        self.powering_off = 0.0
        self.closing = False          # run_ctrl is cleaning up - do not attach any new devices
        self.extra = set()            # tasks of the motion / touchpad streams (attached and detached independently of the pad)
        self.meters = {}              # RateMeter per device (only with --stats)
        self._state_timer = None
        self.last_touch_send = 0.0
        self._touch_timer = None
        # latency measurement (state2 packet): kernel time [µs, evdev event clock] of the oldest unsent change,
        # the Bluetooth delay above the minimum at that moment, and the current one (from sender_streams.BtDelay)
        self.t_event = None
        self.ev_bt = self.bt_us = LAT_NONE
        self.triggers = TRIG2_OFF     # trigger effects from the PC (trig2 values: L2, R2, rumble from the game)
        self.fb = None                # the last feedback from the PC (small, large, r, g, b) - restored after a local flash
        self.wol_timer = None         # countdown of holding the Wake-on-LAN combination (sender_wol)
        self._pulse_timer = None
        self._pulse_back = None       # (0, 0, r, g, b) - the state after a running flash
        self.stick_dz = C.STICK_DZ    # stick deadzone; 0 while the drift measurement from the window runs (CMD_RAW_STICKS)
        self._raw_timer = None
        self.gyro_bias = [0.0, 0.0, 0.0]   # gyro bias subtracted before sending (calibration / autobias)
        self.cal_request = 0          # calibration from the window: this many seconds (stream_motion starts it), 0 = none
        self.cal_cmd_t = -1.0         # when the last calibration command arrived (UDP repeats)

    def devices(self):
        """The evdev devices currently attached to this pad (pad, motion, touchpad - whichever exist)."""
        return [d for d in (self.pad_dev, self.motion_dev, self.touch_dev) if d]

    def set_abs(self, code, value) -> None:
        """Store an absolute-axis value: clamp it to 0..255 and snap sticks within the deadzone to the centre (128)."""
        v = 0 if value < 0 else 255 if value > 255 else value
        if self.stick_dz and code in STICK_AXES and abs(v - 128) <= self.stick_dz:
            v = 128
        self.pad[code] = v

    def init_state(self) -> None:
        """Read the initial pad state from the device and locate the lightbar."""
        self.resync()
        self.led = find_led(self.pad_dev)

    def note_input(self, now) -> None:
        """Remember the time of use if the state is deliberate use, not stick noise or a pad lying still.

        "Deliberate" means any button / D-pad pressed, a trigger pulled beyond 30, or a stick moved more than 40 units
        from the centre. The timestamp feeds the idle power-off.
        """
        p = self.pad
        if (self.mask or self.hat[0] or self.hat[1] or p[2] > 30 or p[5] > 30
                or any(abs(p[k] - 128) > 40 for k in STICK_AXES)):
            self.last_input = now

    def resync(self) -> None:
        """Read the complete pad state from the device (at start, and after SYN_DROPPED when the evdev buffer overflowed).

        Axes and the hat come from the current absinfo values, the button mask from the list of keys that are down.
        Individual read errors are ignored: whatever cannot be read keeps its previous value.
        """
        for c in PAD_ABS:
            try:
                self.set_abs(c, self.pad_dev.absinfo(c).value)
            except Exception:  # the axis cannot be read (the device is going away): keep the value we have
                pass
        for i, c in enumerate((16, 17)):
            try:
                self.hat[i] = clamp(self.pad_dev.absinfo(c).value, -1, 1)
            except Exception:  # the axis cannot be read (the device is going away): keep the value we have
                pass
        self.mask = 0
        try:
            for k in self.pad_dev.active_keys():
                self.mask |= KEY_BIT.get(k, 0)
        except OSError:
            pass

    # ---- sending ----
    def send_ident(self) -> None:
        """Tell the receiver which pad this is (identifier) and what the sender can do (version, capability bits, rumble mode)."""
        net.emit(T_IDENT, self.idx, pad_ident(self.key).encode("ascii", "replace")[:17])
        r = self.rumble
        net.emit(T_VERSION, self.idx, SENDER_VERSION, (r.bits if r is not None else 0) | CAP_CALIB,
                 r.why if r is not None else WHY_OFF)

    def mark_event(self, e) -> None:
        """A kernel event changed the state (button, stick, motion): remember the time of the oldest unsent change.

        The kernel timestamp of the event (evdev clock) is later subtracted from the send time to measure how long the
        change sat on the phone; the Bluetooth delay seen at that moment is saved with it.
        """
        if self.t_event is None:
            sec = getattr(e, "sec", None)            # events in tests have no time
            if sec is not None:
                self.t_event = sec * 1_000_000 + e.usec
                self.ev_bt = self.bt_us

    def send_state(self, force: bool=False) -> None:
        """Pack and send the full pad state, unless nothing changed since the last packet (force=True sends anyway).

        The state key is a tuple of everything that goes into the packet. A repeat of an identical state (heartbeat,
        burst) carries no latency measurement. When the PC clock is synchronised, a state2 packet is sent that also
        carries the PC time and the measured phone / Bluetooth delay of the change; otherwise a plain state packet.
        """
        key = (*self.pad, *self.hat, self.mask, self.batt, self.flags, *self.motion)
        self.pending = False
        if not force and key == self.last_key:
            # a change with no effect (stick noise inside the deadzone, the same value): its time must not stay as the "oldest
            # unsent change" - the next real press would get a delay measured from that noise (up to ~100 ms)
            self.t_event = None
            return
        fresh = key != self.last_key
        self.last_key = key
        self.t_state = time.monotonic()
        ev, self.t_event = self.t_event, None
        pc = net.pc_clock.now_us()
        if pc is None:                                   # a receiver without pong2 (older) or no synchronisation
            net.emit(T_STATE, self.idx, *key)
            return
        phone = bt = LAT_NONE                            # a repeat (heartbeat, burst) - no measurement
        if fresh and ev is not None:
            age = time.time_ns() // 1000 - ev            # the evdev event clock = CLOCK_REALTIME
            if 0 <= age < LAT_NONE:
                phone, bt = age, self.ev_bt
        net.emit(T_STATE2, self.idx, *key, pc, phone, bt)

    def request_send(self) -> None:
        """Send the changed state, limited by pad_min_dt: the first change after silence goes out in this very loop
        iteration (call_soon - tens of µs), later ones inside the window merge into one packet at its end - nothing is lost.
        call_soon instead of sending at once: one pad report lands in three evdev devices (buttons / sticks, motion,
        touchpad), and their events arrive in the same loop iteration - stick and motion changes from one report go
        out in one packet, not two (the second only after pad_min_dt)."""
        self.pending = True
        if self._state_timer is not None:
            return                     # a send is scheduled - it will take this change as well
        wait = C.PAD_MIN_DT - (time.monotonic() - self.t_state)
        loop = asyncio.get_running_loop()
        self._state_timer = loop.call_soon(self._state_flush) if wait <= 0 else loop.call_later(wait, self._state_flush)

    def _state_flush(self) -> None:
        """Timer callback of request_send: send the pending state, if it is still pending."""
        self._state_timer = None
        if self.pending:
            self.send_state()

    def send_touch(self, force: bool=False) -> None:
        """Touch is merged every touch_coalesce_ms; a change rejected by the window is not lost -
        it is sent late (trailing flush), so e.g. lifting a finger does not wait for the heartbeat."""
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

    def _touch_flush(self) -> None:
        """Timer callback of send_touch: the trailing flush of a touch change that the coalescing window held back."""
        self._touch_timer = None
        self.send_touch(force=True)

    def send_click(self) -> None:
        """Send the touchpad click state (0 = none, 1 = click, 2 = click with two fingers)."""
        net.emit(T_CLICK, self.idx, int(self.tp))

    def send_tap(self, flags) -> None:
        """A tap (bit0 left button / bit2 right button) with a sequence number in bits 4-7, sent 3 times: a lost packet
        does not lose the click, and the receiver performs the click only once, going by the number."""
        self.tap_id = self.tap_id % 15 + 1
        f = flags | (self.tap_id << 4)
        self.send_mouse(0, 0, f)
        loop = asyncio.get_running_loop()
        for d in BURST_DELAYS:
            loop.call_later(d, self.send_mouse, 0, 0, f)

    def send_mouse(self, dx, dy, flags) -> None:
        """Send a mouse movement / button packet; dx and dy are clamped to the signed 16-bit packet fields."""
        net.emit(T_MOUSE, self.idx, clamp(dx, -32768, 32767), clamp(dy, -32768, 32767), flags)

    def send_neutral(self) -> None:
        """Reset all inputs to neutral and send that, so the receiver releases everything (used when the pad goes away)."""
        self.pad = list(NEUTRAL_PAD)
        self.hat = [0, 0]
        self.mask = 0
        self.motion = [0] * 6
        self.touch = IDLE_TP
        self.tp = 0
        self.send_state(True)
        self.send_touch(force=True)
        self.send_click()

    # ---- feedback from the PC ----
    def apply_feedback(self, small, large, r, g, b, remember: bool=True) -> None:
        """Apply rumble (small and large motor) and the lightbar colour sent by the PC.

        With remember=True the values are stored as the "last PC state" (and cancel a running local flash, so the flash
        cannot restore something stale). When the HID rumble backend owns the lightbar, colour and rumble go out in one
        HID report; otherwise rumble goes through the driver and the colour is written to the sysfs LED (multi_intensity
        and brightness). If that write fails, the lightbar is considered unavailable.
        """
        if remember:
            self.fb = (small, large, r, g, b)
            if self._pulse_timer is not None:      # the PC set something new - the local flash will not restore the old state
                self._pulse_timer.cancel()
                self._pulse_timer = None
        if self.rumble is not None and self.rumble.owns_lightbar:
            self.rumble.set(small, large, (r, g, b))     # rumble and colour in one HID report
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
        """The current lightbar colour (HID report or sysfs), or None."""
        if self.rumble is not None and self.rumble.rgb is not None:
            return self.rumble.rgb
        if self.led:
            try:
                with open(self.led + "/multi_intensity") as f:
                    return tuple(int(x) for x in f.read().split()[:3])
            except (OSError, ValueError):
                pass
        return None

    def local_pulse(self, kind) -> None:
        """Confirmation on the pad without the receiver (Wake-on-LAN): rumble and colour for a moment, then the previous colour.
        The return is without rumble: the receiver is silent, and the last feedback from the PC (e.g. game rumble from before
        the PC was switched off, already stopped by the watchdog) must not switch it on again. A flash during a flash returns
        to the colour from before the first one, not to the flash colour."""
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

    def _pulse_end(self, back) -> None:
        """Timer callback of local_pulse: restore the state saved before the flash (without remembering it as PC feedback)."""
        self._pulse_timer = None
        small, large, r, g, b = back
        self.apply_feedback(small, large, r, g, b, remember=False)

    def set_triggers(self, vals) -> None:
        """Trigger effects from the PC (trig3 packet = profile + game effects, trig2 = profile, old trig = resistance only).
        Only the HID report can do this - the kernel driver has no such function."""
        vals = tuple(vals)
        self.triggers = trig2_from_legacy(vals) if len(vals) == 4 else vals
        if self.rumble is not None:
            self.rumble.set_triggers(self.triggers)

    def set_raw_sticks(self, secs) -> None:
        """Drift measurement from the window: sticks without the sender's deadzone for secs seconds (0 = back at once).
        It returns by itself after the time - a receiver that disappears during the measurement will not leave the pad
        without a deadzone. The state is read again: a stick that was clipped to 128 must show its real position (and vice versa)."""
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

    def cancel_timers(self) -> None:
        """Cancel every pending timer of this pad (state / touch flush, Wake-on-LAN hold, flash, raw-stick window)."""
        for name in ("_state_timer", "_touch_timer", "wol_timer", "_pulse_timer", "_raw_timer"):
            t = getattr(self, name)
            if t is not None:
                t.cancel()
                setattr(self, name, None)

    def stop_feedback(self) -> None:
        """Close the rumble backend (which also stops the motors) and drop it."""
        if self.rumble is not None:
            self.rumble.close()
            self.rumble = None
