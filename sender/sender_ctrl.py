"""Sender: state of a single pad (the Ctrl class) - building and sending packets, rumble, lightbar."""
from __future__ import annotations

import asyncio
import contextlib
import errno
import glob
import os
import time
import zlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from evdev import InputDevice, InputEvent

import sender_net as net
import sender_state as state
from proto import (CAP_CALIB, CAP_HAPTICS, CAP_SPEAKER, IDENT_LEN, KEY_BIT, LAT_NONE, LEDS_MUTE, LEDS_PLAYER, MOUSE_TAP_ID_MAX,
                   MOUSE_TAP_ID_SHIFT, MUTE_BIT, RAW_STICKS_MAX_S, SENDER_VERSION, T_CLICK, T_IDENT, T_MOUSE, T_STATE, T_STATE2,
                   T_TOUCH, T_VERSION, TRIG2_OFF, UINT32_MASK, WHY_OFF, clamp, trig2_from_legacy)
from sender_config import (AXIS_MAX, BURST_DELAYS, C, DEVICE_READ_ERRORS, HAT_AXES, IDLE_TP, L2_AXIS, NEUTRAL_PAD, PAD_ABS,
                           R2_AXIS, STICK_AXES, STICK_CENTER, US_PER_S)
from sender_haptics import HapticsStream
from sender_speaker import Report3x, SpeakerStream

if TYPE_CHECKING:
    from sender_rumble import Rumble

LED_GLOB: Final = "/sys/class/leds/*:rgb:indicator"     # the kernel's RGB lightbar of a DualSense
DEFAULT_LED_MAX_BRIGHTNESS: Final = "255"
LED_FAIL_MAX: Final = 3                     # failed lightbar writes in a row before the lightbar is given up (until reconnect)
LED_FATAL_ERRNO: Final = frozenset({errno.ENODEV, errno.ENOENT, errno.ENXIO})     # the LED itself is gone - no point retrying
DEFAULT_LIGHTBAR: Final = (0, 0, 64)        # the colour a flash returns to when nothing else is known
RGB_LEN: Final = 3
INT16_MIN, INT16_MAX = -32768, 32767        # the mouse packet's dx / dy are signed 16-bit
NS_PER_US: Final = 1000
BUS_USB: Final = 0x03                       # evdev input_id.bustype of a pad on USB (Bluetooth is 0x05)
# "Deliberate use" of the pad (for the idle power-off): a trigger pulled beyond this, or a stick moved more than this
# many units from the centre. Smaller values are stick noise or a pad lying still.
TRIGGER_PRESS_MIN: Final = 30
STICK_MOVE_MIN: Final = 40


class LocalPulse(NamedTuple):
    """A confirmation the sender gives on the pad by itself, without the receiver."""

    small: int          # the weak motor
    large: int          # the strong motor
    rgb: tuple[int, int, int]
    seconds: float


LOCAL_PULSES: Final = {
    "wol": LocalPulse(0, 200, (255, 255, 255), 0.5),     # Wake-on-LAN was sent - white flash
    "fail": LocalPulse(0, 120, (255, 0, 0), 0.6),        # not sent (no MAC / network) - red
}


def find_led(pad_dev: InputDevice, pattern: str = LED_GLOB) -> str | None:
    """Find the sysfs LED that belongs to the same HID device as the pad's evdev node (the lightbar by default).

    The pad's /sys/class/input/eventN path is compared against the parent HID device of every LED matching ``pattern``
    (``*:rgb:indicator`` = the lightbar): the LED whose HID device is a prefix of the input path is the pad's own.

    Args:
        pad_dev: The pad's evdev device.
        pattern: The glob of the LED directories to look through.

    Returns:
        The LED directory, or None (no kernel driver for that LED / no permission).
    """
    try:
        event_path = os.path.realpath(f"/sys/class/input/{os.path.basename(os.fsdecode(pad_dev.path))}")
        for led in glob.glob(pattern):
            hid = os.path.dirname(os.path.dirname(os.path.realpath(led)))
            if event_path.startswith(hid + "/"):
                return led
    except OSError:
        return None
    return None


def pad_ident(key: str) -> str:
    """Return a stable pad identifier (at most IDENT_LEN bytes): the MAC from uniq; a hash for long keys.

    The receiver uses it to select the per-pad profile, so it must stay the same across reconnects and restarts.
    """
    if len(key) <= IDENT_LEN:
        return key
    return "h" + format(zlib.crc32(key.encode()) & UINT32_MASK, "08x")


class Ctrl:
    """Everything the sender knows about one pad: its evdev devices, the current input state and the send machinery.

    The input streams (sender_streams) write into the state fields (pad axes, hat, button mask, motion, touch); the
    send_* methods turn that state into packets for slot ``idx``. Sending is rate-limited by timers kept on the object,
    and feedback from the PC (rumble, lightbar, adaptive triggers) flows back through apply_feedback / set_triggers.

    Attributes:
        idx: The pad's slot number.
        key: The device key (the pad's MAC, or its phys path).
        pad_dev: The evdev device of buttons, sticks and triggers.
        motion_dev: The evdev device of the motion sensor (None until attached).
        touch_dev: The evdev device of the touchpad (None until attached).
        rumble: The rumble backend (set in run_ctrl), or None.
        haptics: The HD haptics stream of this pad (created by the first chunk from the PC), or None.
        speaker: The speaker stream of this pad (created by the first frame from the PC), or None.
        extra: The tasks of the motion / touchpad streams (attached and detached independently of the pad).
    """

    def __init__(self, idx: int, key: str, pad: InputDevice, motion: InputDevice | None, touch: InputDevice | None) -> None:
        """Create the state of a pad whose devices were just found."""
        self.idx, self.key = idx, key
        self.pad_dev, self.motion_dev, self.touch_dev = pad, motion, touch
        self.pad = list(NEUTRAL_PAD)
        self.hat = [0, 0]
        self.mask = 0
        self.motion = [0] * 6
        self.batt, self.flags = 255, 0
        self.touch = IDLE_TP
        self.tp, self.tap_id = 0, 0   # tp: 0 / 1 / 2 (click with two fingers)
        self.t_state, self.pending = 0.0, False
        self.last_key: tuple[int, ...] | None = None
        self.rumble: Rumble | None = None
        self.haptics: HapticsStream | None = None
        self.speaker: SpeakerStream | None = None
        self._speaker_failed: Rumble | None = None    # the rumble backend whose encoder could not be created (logged once, not retried)
        self.audio_report: Report3x | None = None     # the report both streams write (0x32 / 0x36), shared so one tick plays both
        self.led: str | None = None
        self.led_color: tuple[int, int, int] | None = None
        self._led_max: str | None = None          # max_brightness of the LED, read once (it never changes)
        self._led_fails = 0                       # lightbar writes that failed in a row
        self._led_want: tuple[int, int, int] | None = None   # the newest colour asked for while a write is running
        self._led_busy = False                    # a sysfs write runs in a worker thread
        self.reports = {"pad": 0, "motion": 0}   # device report counters (Hz in the info packet)
        self.last_input = time.monotonic()        # the last deliberate use of the pad (for idle power-off)
        self.powering_off = float("-inf")
        self.closing = False          # run_ctrl is cleaning up - do not attach any new devices
        self.sealed = False           # set once "bye" is out: a late repeat (call_later) must not send for a slot the PC has released
        self.extra: set[asyncio.Task[Any]] = set()
        self.meters: dict[str, Any] = {}          # RateMeter per device (only with --stats)
        self._state_timer: asyncio.Handle | None = None
        self.last_touch_send = 0.0
        self._touch_timer: asyncio.TimerHandle | None = None
        # latency measurement (state2 packet): kernel time [µs, evdev event clock] of the oldest unsent change,
        # the Bluetooth delay above the minimum at that moment, and the current one (from sender_streams.BtDelay)
        self.t_event: int | None = None
        self.ev_bt = self.bt_us = LAT_NONE
        self.mute_down = False      # the mute button is "held" for a moment after each press (sender_mute); not part of self.mask
        self.leds: tuple[int | None, int | None] = (None, None)   # the game's (mute LED, player LEDs) from the PC; None = not set
        self.triggers: tuple[Any, ...] = TRIG2_OFF     # trigger effects from the PC (trig2 values: L2, R2, rumble from the game)
        self.fb: tuple[int, int, int, int, int] | None = None   # the last feedback from the PC (small, large, r, g, b)
        self.wol_timer: asyncio.TimerHandle | None = None       # countdown of holding the Wake-on-LAN combination (sender_wol)
        self._pulse_timer: asyncio.TimerHandle | None = None
        self._pulse_back: tuple[int, int, int, int, int] | None = None   # (0, 0, r, g, b) - the state after a running flash
        self.stick_dz = C.STICK_DZ    # stick deadzone; 0 while the drift measurement from the window runs (CMD_RAW_STICKS)
        self._raw_timer: asyncio.TimerHandle | None = None
        self.gyro_bias = [0.0, 0.0, 0.0]   # gyro bias subtracted before sending (calibration / autobias)
        self.bias_known = False       # gyro_bias holds a saved / calibrated value: a restarted motion stream keeps it (autobias refines it)
        self.cal_request = 0          # calibration from the window: this many seconds (stream_motion starts it), 0 = none
        self.starting = False         # the supervisor is still setting the pad up (rumble backend, grab): the heartbeat leaves it alone
        self.cal_running = False      # a calibration from the window is collecting samples (cal_request is already 0 by then)
        self.cal_cmd_t = -1.0         # when the last calibration command arrived (UDP repeats)

    def is_bluetooth(self) -> bool:
        """False for a pad plugged in over USB: there is no Bluetooth link to drop, so powering it off by disconnecting is pointless.

        A stand-in device without bus information (tests) counts as Bluetooth.
        """
        return getattr(getattr(self.pad_dev, "info", None), "bustype", None) != BUS_USB

    def devices(self) -> list[InputDevice]:
        """Return the evdev devices currently attached to this pad (pad, motion, touchpad - whichever exist)."""
        return [d for d in (self.pad_dev, self.motion_dev, self.touch_dev) if d]

    def set_abs(self, code: int, value: int) -> None:
        """Store an absolute-axis value: clamp it to 0..AXIS_MAX and snap sticks within the deadzone to the centre."""
        v = 0 if value < 0 else AXIS_MAX if value > AXIS_MAX else value
        if self.stick_dz and code in STICK_AXES and abs(v - STICK_CENTER) <= self.stick_dz:
            v = STICK_CENTER
        self.pad[code] = v

    def init_state(self) -> None:
        """Read the initial pad state from the device and locate the lightbar."""
        self.resync()
        self.led = find_led(self.pad_dev)

    def note_input(self, now: float) -> None:
        """Remember the time of use if the state is deliberate use, not stick noise or a pad lying still.

        "Deliberate" means any button / D-pad pressed, a trigger pulled beyond TRIGGER_PRESS_MIN, or a stick moved more
        than STICK_MOVE_MIN units from the centre. The timestamp feeds the idle power-off.
        """
        p = self.pad
        if (self.mask or self.hat[0] or self.hat[1] or p[L2_AXIS] > TRIGGER_PRESS_MIN or p[R2_AXIS] > TRIGGER_PRESS_MIN
                or any(abs(p[k] - STICK_CENTER) > STICK_MOVE_MIN for k in STICK_AXES)):
            self.last_input = now

    def _axis_value(self, axis: int) -> int | None:
        """Read one axis from the device; None when it cannot be read (the device is going away)."""
        try:
            return self.pad_dev.absinfo(axis).value
        except DEVICE_READ_ERRORS:
            return None

    def resync(self) -> None:
        """Read the complete pad state from the device (at start, and after SYN_DROPPED when the evdev buffer overflowed).

        Axes and the hat come from the current absinfo values, the button mask from the list of keys that are down.
        Individual read errors are ignored: whatever cannot be read keeps its previous value.
        """
        for axis in PAD_ABS:
            value = self._axis_value(axis)
            if value is not None:
                self.set_abs(axis, value)
        for i, axis in enumerate(HAT_AXES):
            value = self._axis_value(axis)
            if value is not None:
                self.hat[i] = clamp(value, -1, 1)
        mask = 0
        with contextlib.suppress(OSError):      # the device is going away: keep the buttons we have already read
            for key in self.pad_dev.active_keys():
                mask |= KEY_BIT.get(key, 0)
            self.mask = mask

    # ---- sending ----
    def send_ident(self) -> None:
        """Tell the receiver which pad this is (identifier) and what the sender can do (version, capability bits, rumble mode)."""
        net.emit(T_IDENT, self.idx, pad_ident(self.key).encode("ascii", "replace")[:IDENT_LEN])
        r = self.rumble
        net.emit(T_VERSION, self.idx, SENDER_VERSION, (r.bits if r is not None else 0) | CAP_CALIB,
                 r.why if r is not None else WHY_OFF)

    def mark_event(self, e: InputEvent) -> None:
        """Remember the time of the oldest unsent change after a kernel event changed the state (button, stick, motion).

        The kernel timestamp of the event (evdev clock) is later subtracted from the send time to measure how long the
        change sat on the phone; the Bluetooth delay seen at that moment is saved with it.
        """
        if self.t_event is None:
            sec = getattr(e, "sec", None)            # events in tests have no time
            if sec is not None:
                self.t_event = sec * US_PER_S + e.usec
                self.ev_bt = self.bt_us

    def _delay_fields(self, fresh: bool, event_us: int | None) -> tuple[int, int]:
        """The phone and Bluetooth delay of a change for the state2 packet; LAT_NONE for a repeat (no measurement)."""
        if fresh and event_us is not None:
            age = time.time_ns() // NS_PER_US - event_us            # the evdev event clock = CLOCK_REALTIME
            if 0 <= age < LAT_NONE:
                return age, self.ev_bt
        return LAT_NONE, LAT_NONE

    def send_state(self, force: bool = False) -> None:
        """Pack and send the full pad state, unless nothing changed since the last packet.

        The state key is a tuple of everything that goes into the packet. A repeat of an identical state (heartbeat,
        burst) carries no latency measurement. When the PC clock is synchronised, a state2 packet is sent that also
        carries the PC time and the measured phone / Bluetooth delay of the change; otherwise a plain state packet.

        Args:
            force: Send even if the state is identical to the last packet.
        """
        if self.sealed:
            return
        key = (*self.pad, *self.hat, self.mask | MUTE_BIT if self.mute_down else self.mask, self.batt, self.flags, *self.motion)
        self.pending = False
        if not force and key == self.last_key:
            # a change with no effect (stick noise inside the deadzone, the same value): its time must not stay as the "oldest
            # unsent change" - the next real press would get a delay measured from that noise (up to ~100 ms)
            self.t_event = None
            return
        fresh = key != self.last_key
        self.last_key = key
        self.t_state = time.monotonic()
        event_us, self.t_event = self.t_event, None
        pc = net.pc_clock.now_us()
        if pc is None:                                   # a receiver without pong2 (older) or no synchronisation
            net.emit(T_STATE, self.idx, *key)
            return
        phone, bt = self._delay_fields(fresh, event_us)
        net.emit(T_STATE2, self.idx, *key, pc, phone, bt)

    def request_send(self) -> None:
        """Send the changed state, limited by pad_min_dt.

        The first change after silence goes out in this very loop iteration (call_soon - tens of µs), later ones inside
        the window merge into one packet at its end - nothing is lost. call_soon instead of sending at once: one pad report
        lands in three evdev devices (buttons / sticks, motion, touchpad), and their events arrive in the same loop
        iteration - stick and motion changes from one report go out in one packet, not two (the second only after
        pad_min_dt).
        """
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

    def send_touch(self, force: bool = False) -> None:
        """Send the touch packet, merged every touch_coalesce_ms.

        A change rejected by the window is not lost - it is sent late (trailing flush), so e.g. lifting a finger does not
        wait for the heartbeat.

        Args:
            force: Send now, ignoring the coalescing window.
        """
        if self.sealed:
            return
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
        if not self.sealed:
            net.emit(T_CLICK, self.idx, int(self.tp))

    def send_tap(self, flags: int) -> None:
        """Send a tap (MOUSE_TAP / MOUSE_TAP_RIGHT) with a sequence number in the high bits, three times.

        A lost packet does not lose the click, and the receiver performs the click only once, going by the number.

        Args:
            flags: The tap kind: MOUSE_TAP (left button) or MOUSE_TAP_RIGHT.
        """
        self.tap_id = self.tap_id % MOUSE_TAP_ID_MAX + 1
        tap_flags = flags | (self.tap_id << MOUSE_TAP_ID_SHIFT)
        self.send_mouse(0, 0, tap_flags)
        loop = asyncio.get_running_loop()
        for delay in BURST_DELAYS:
            loop.call_later(delay, self.send_mouse, 0, 0, tap_flags)

    def send_mouse(self, dx: int, dy: int, flags: int) -> None:
        """Send a mouse movement / button packet; dx and dy are clamped to the signed 16-bit packet fields."""
        if self.sealed:
            return
        net.emit(T_MOUSE, self.idx, clamp(dx, INT16_MIN, INT16_MAX), clamp(dy, INT16_MIN, INT16_MAX), flags)

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
    def _led_max_brightness(self, led: str) -> str:
        """The LED's max_brightness (what to write to ``brightness`` to light it fully); read from sysfs once, then remembered."""
        if self._led_max is None:
            try:
                with open(f"{led}/max_brightness") as f:
                    self._led_max = f.read().strip()
            except OSError:
                return DEFAULT_LED_MAX_BRIGHTNESS      # not remembered: the next colour change tries again
        return self._led_max

    def _led_io(self, led: str, rgb: tuple[int, int, int]) -> OSError | None:
        """Write the colour to the sysfs LED (multi_intensity and brightness); blocking, so it runs in a worker thread.

        Each write makes the driver send an HID output report - over Bluetooth a synchronous round trip to the pad.

        Returns:
            The error of a failed write (not raised: the outcome is handled on the event loop), or None.
        """
        try:
            with open(f"{led}/multi_intensity", "w") as f:
                f.write("{} {} {}".format(*rgb))
            with open(f"{led}/brightness", "w") as f:
                f.write(self._led_max_brightness(led))
        except OSError as e:
            return e
        return None

    def _write_led(self, rgb: tuple[int, int, int]) -> None:
        """Write the colour to the sysfs LED (multi_intensity and brightness) without stopping the event loop.

        The write runs in a worker thread: on the loop a slow Bluetooth round trip stopped the read loops of every pad. One
        write at a time; a colour that arrives while one is running replaces the one waiting (only the latest matters) and is
        written right after it. Without a running loop (tests, tools) the write is done at once.

        One failed write is not the end of the lightbar (the pad may be busy, or reconnecting): the colour stays unchanged, so
        the next feedback from the PC tries again. After LED_FAIL_MAX failures in a row, or at once when the LED node is gone,
        the lightbar is unavailable until the pad reconnects.
        """
        led = self.led
        if not led:
            return
        loop = state.running_loop()
        if loop is None:
            self._led_done(rgb, self._led_io(led, rgb))
            return
        self._led_want = rgb
        if self._led_busy:
            return
        self._led_busy = True
        loop.run_in_executor(None, self._led_io, led, rgb).add_done_callback(lambda fut: self._led_finished(rgb, fut))

    def _led_finished(self, rgb: tuple[int, int, int], fut: asyncio.Future[OSError | None]) -> None:
        """Event-loop callback of a worker-thread write: record the outcome, then write the colour that arrived meanwhile."""
        self._led_busy = False
        err = fut.result()
        self._led_done(rgb, err)
        want = self._led_want
        if err is None and self.led and want is not None and want != rgb:
            self._write_led(want)

    def _led_done(self, rgb: tuple[int, int, int], err: OSError | None) -> None:
        """Bookkeeping of one lightbar write: the colour on success, the failure counter (and giving up) on an error."""
        if err is not None:
            self._led_fails += 1
            if self._led_fails >= LED_FAIL_MAX or err.errno in LED_FATAL_ERRNO:
                print(f"Controller {self.idx}: lightbar unavailable: {err!r}")
                self.led = None
            return
        self._led_fails = 0
        self.led_color = rgb

    def apply_feedback(self, small: int, large: int, r: int, g: int, b: int, remember: bool = True) -> None:
        """Apply rumble (small and large motor) and the lightbar colour sent by the PC.

        When the HID rumble backend owns the lightbar, colour and rumble go out in one HID report; otherwise rumble goes
        through the driver and the colour is written to the sysfs LED.

        Args:
            small: The weak motor, 0..255.
            large: The strong motor, 0..255.
            r: Lightbar red.
            g: Lightbar green.
            b: Lightbar blue.
            remember: Store the values as the "last PC state" (and cancel a running local flash, so the flash cannot
                restore something stale).
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
        # While a write runs, led_color is the previous colour; compare with the one on its way, or a return to the old
        # colour would be skipped and the pad would end up on the in-flight one.
        current = self._led_want if self._led_busy else self.led_color
        if self.led and (r, g, b) != current:
            self._write_led((r, g, b))

    def led_rgb(self) -> Sequence[int] | None:
        """Return the current lightbar colour (HID report or sysfs), or None."""
        if self.rumble is not None and self.rumble.rgb is not None:
            return self.rumble.rgb
        if self.led:
            try:
                with open(f"{self.led}/multi_intensity") as f:
                    return tuple(int(x) for x in f.read().split()[:RGB_LEN])
            except (OSError, ValueError):
                return None
        return None

    def local_pulse(self, kind: str) -> None:
        """Confirm something on the pad without the receiver (Wake-on-LAN): rumble and colour for a moment.

        Afterwards the previous colour returns, without rumble: the receiver is silent, and the last feedback from the PC
        (e.g. game rumble from before the PC was switched off, already stopped by the watchdog) must not switch it on
        again. A flash during a flash returns to the colour from before the first one, not to the flash colour.

        Args:
            kind: A key of LOCAL_PULSES ("wol" or "fail").
        """
        pulse = LOCAL_PULSES[kind]
        if self._pulse_timer is not None:
            self._pulse_timer.cancel()
            back = self._pulse_back
        else:
            back = (0, 0, *(self.fb[2:] if self.fb else self.led_rgb() or DEFAULT_LIGHTBAR))
        self._pulse_back = back
        self.apply_feedback(pulse.small, pulse.large, *pulse.rgb, remember=False)
        self._pulse_timer = state.call_later_if_running(pulse.seconds, self._pulse_end, back)

    def _pulse_end(self, back: tuple[int, int, int, int, int]) -> None:
        """Timer callback of local_pulse: restore the state saved before the flash (without remembering it as PC feedback)."""
        self._pulse_timer = None
        small, large, r, g, b = back
        self.apply_feedback(small, large, r, g, b, remember=False)

    def set_triggers(self, vals: Sequence[Any]) -> None:
        """Take trigger effects from the PC; only the HID report can apply them - the kernel driver has no such function.

        Args:
            vals: A trig3 packet (profile + game effects), trig2 (profile) or the old trig (resistance only).
        """
        vals = tuple(vals)
        self.triggers = trig2_from_legacy(vals) if len(vals) == len(TRIG2_OFF) // 3 else vals
        if self.rumble is not None:
            self.rumble.set_triggers(self.triggers)

    def apply_leds(self, flags: int, mute: int, player: int) -> None:
        """Take the mute button LED and the player LEDs the game set; only the HID report can apply them.

        Args:
            flags: LEDS_MUTE / LEDS_PLAYER: which of the two values is valid (the other one stays as it was).
            mute: The mute LED (MUTE_OFF / MUTE_ON / MUTE_PULSE).
            player: The player LEDs, 5 bits.
        """
        self.leds = (mute if flags & LEDS_MUTE else self.leds[0], player if flags & LEDS_PLAYER else self.leds[1])
        if self.rumble is not None:
            self.rumble.set_leds(*self.leds)

    def on_haptics(self, frame: int, flags: int, data: memoryview) -> None:
        """Take a chunk of the game's HD haptics from the PC; the first one starts the pad's stream.

        Only a Bluetooth pad with a HID report can play it (the CAP_HAPTICS bit the receiver saw in the version packet).

        Args:
            frame: The number of the chunk's first frame (3 kHz frames).
            flags: HAPT_START / HAPT_END.
            data: The samples (signed 8-bit stereo); copied by the stream.
        """
        r = self.rumble
        if r is None or not r.bits & CAP_HAPTICS or self.closing:
            return
        if self.haptics is None or self.haptics.rumble is not r:
            self.haptics = HapticsStream(self.idx, r, C.HAPTICS_BUFFER_MS, self._report())
        self.haptics.feed(frame, flags, data)

    def _report(self) -> Report3x:
        """The pad's audio report, created on first use (both streams of the pad write into the same one)."""
        if self.audio_report is None:
            self.audio_report = Report3x()
        return self.audio_report

    def _haptics_drives(self) -> bool:
        """Whether the haptics stream is writing reports (its tick is then the pad's clock for the speaker as well)."""
        return self.haptics is not None and self.haptics.writing

    def on_speaker(self, frame: int, flags: int, data: memoryview) -> None:
        """Take 10 ms of the game's sound from the PC; the first one starts the pad's speaker stream.

        Only a Bluetooth pad with a HID report can play it, and only when libopus is installed here (the CAP_SPEAKER bit the
        receiver saw in the version packet).

        Args:
            frame: The number of the frame (10 ms frames).
            flags: SPK_START / SPK_END.
            data: The samples (48 kHz mono, signed 16-bit little endian); copied by the stream.
        """
        r = self.rumble
        if r is None or not r.bits & CAP_SPEAKER or self.closing or self._speaker_failed is r:
            return
        if self.speaker is None or self.speaker.rumble is not r:
            if self.speaker is not None:
                self.speaker.close()
            try:
                self.speaker = SpeakerStream(self.idx, r, self._report(), self._haptics_drives, C.SPEAKER_BUFFER_MS,
                                             C.SPEAKER_VOLUME, C.SPEAKER_OPUS_BYTES, C.SPEAKER_OPUS_MODE, C.SPEAKER_OPUS_COMPLEXITY)
            except OSError as e:
                print(f"Controller {self.idx}: speaker unavailable ({e})")
                self.speaker, self._speaker_failed = None, r
                return
        self.speaker.feed(frame, flags, data)

    def set_raw_sticks(self, secs: int) -> None:
        """Run the drift measurement from the window: sticks without the sender's deadzone for ``secs`` seconds.

        It returns by itself after the time - a receiver that disappears during the measurement will not leave the pad
        without a deadzone. The state is read again: a stick that was clipped to the centre must show its real position
        (and vice versa).

        Args:
            secs: How long to leave the deadzone off (0 = back at once).
        """
        if self._raw_timer is not None:
            self._raw_timer.cancel()
            self._raw_timer = None
        secs = clamp(secs, 0, RAW_STICKS_MAX_S)
        self.stick_dz = 0 if secs else C.STICK_DZ
        loop = state.running_loop()
        if secs and loop is not None:
            self._raw_timer = loop.call_later(secs, self.set_raw_sticks, 0)
        if self.pad_dev is not None:
            self.resync()
            if loop is not None:
                self.request_send()

    def refresh_deadzone(self) -> None:
        """Take a changed stick_deadzone (the receiver window); not while the drift measurement runs, it holds the deadzone at 0."""
        if self._raw_timer is None:
            self.stick_dz = C.STICK_DZ

    def cancel_timers(self) -> None:
        """Cancel every pending timer of this pad (state / touch flush, Wake-on-LAN hold, flash, raw-stick window)."""
        for name in ("_state_timer", "_touch_timer", "wol_timer", "_pulse_timer", "_raw_timer"):
            timer = getattr(self, name)
            if timer is not None:
                timer.cancel()
                setattr(self, name, None)

    def stop_feedback(self) -> None:
        """Close the rumble backend (which also stops the motors) and drop it; running audio streams end first."""
        if self.speaker is not None:
            self.speaker.close()
            self.speaker = None
        if self.haptics is not None:
            self.haptics.close()
            self.haptics = None
        if self.rumble is not None:
            self.rumble.close()
            self.rumble = None
