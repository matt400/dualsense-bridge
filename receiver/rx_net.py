"""Receiver: receiving packets from the sender, handling events, detecting silence."""
import os
import socket
import threading
import time

import rx_bt
import rx_console
import rx_events as ev
import rx_feedback as F
import rx_hotkeys as hotkeys
import rx_latency
import rx_master as master
import rx_mouse as mouse
import rx_settings
import rx_state as S
import rx_testmode as testmode
from proto import (BODY, CAL_MOVED, CAL_OK, LAT_NONE, PAD_KEYS, RTT_NONE, SCFG_EVENTS, SCFG_WOL, STATE_FIELDS, WIRE, Codec,
                   EpochClock,
                   T_BTCTRL, T_BTDEV, T_BTSTATE, T_BUILD, T_BUILD2, T_BYE, T_CALRES, T_CLICK, T_CMD, T_EVENT, T_FB, T_IDENT, T_INFO,
                   T_MOUSE, T_PING, T_PONG, T_PONG2, T_SCFG, T_STATE, T_STATE2, T_TOUCH, T_TRIG, T_TRIG2, T_TRIG3, T_UPDMSG,
                   T_VERSION, TRIG2_OFF, clamp, cstr, mac_bytes, mac_text)
from rx_buttons import BUTTON_CODES, MASK_ALL, PS_BIT, TRIG_ON

from rx_config import C
from rx_i18n import _
from rx_winapi import bind_udp, boost_thread, recv_timeout

RX_TYPES = {T_STATE, T_STATE2, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_BYE, T_IDENT, T_INFO, T_VERSION,   # the packet types the
            T_CALRES}                                                                                  # receiver accepts
ST_TOUCH, ST_MOUSE, ST_INFO = BODY[T_TOUCH], BODY[T_MOUSE], BODY[T_INFO]
STATES = (T_STATE, T_STATE2)
MAX_PROBE = 20_000              # drift measurement: a safeguard on the length of the sample list
MAX_NET_US = 1_000_000          # a network time over 1 s = a bad clock (e.g. right after a restart), not a measurement
SCFG_S = 5.0                    # Wake-on-LAN / event settings go to the sender this often (it has them after its own restart too)
WSAETIMEDOUT = 10060
KEY_WARN_S = 60.0               # a warning about a different HMAC key in the sender at most this often


class LinkStats:
    """Packet loss (from gaps in the sequence numbers - the sender numbers all packets with one counter)
    and state packets per second per slot. Results go every second to S.link_loss / S.pps (the GUI window).

    A jump in the number is a loss when it fits the silence: at most as many packets as the sender would send in that time (the measured
    rate with a margin). A bigger jump = a sender restart (a new random number). Before, the threshold was fixed (1000):
    a signal gap longer than ~1-4 s (250-1000 packets/s) counted as a restart and the indicator showed 0 %
    loss exactly during the biggest dropouts."""

    WINDOW = 1.0
    MIN_JUMP = 1000                  # a smaller jump is always a loss (as before)
    RATE_MARGIN, RATE_MIN = 1.5, 50.0

    def __init__(self, n):
        """Start empty counters for `n` pad slots."""
        self.last = None
        self.t_last = 0.0
        self.rate = 0.0              # packets/s from the last window (all types)
        self.expected = self.got = 0
        self.states = [0] * n
        self.t0 = time.monotonic()

    def packet(self, sq, now=None):
        """Account for one packet by its sequence number: a gap within the plausible limit counts as lost packets, a duplicate or late one
        is ignored, a bigger jump is taken as a sender restart."""
        now = time.monotonic() if now is None else now
        if self.last is not None:
            d = (sq - self.last) & 0xFFFF
            if d == 0 or d >= 0x8000:
                return                       # a duplicate or a late one - does not move the counter
            limit = max(self.MIN_JUMP, (now - self.t_last) * max(self.rate, self.RATE_MIN) * self.RATE_MARGIN)
            if d <= limit:
                self.expected += d
                self.got += 1
        self.last, self.t_last = sq, now

    def tick(self, now):
        """Once per WINDOW: publish the loss percentage and the state packets per second, update the measured rate and reset the
        counters."""
        dt = now - self.t0
        if dt < self.WINDOW:
            return
        S.link_loss = max(0.0, 100.0 * (1 - self.got / self.expected)) if self.expected >= 10 else None
        if self.got:
            self.rate = self.got / dt
        S.pps[:] = [n / dt for n in self.states]
        self.expected = self.got = 0
        self.states = [0] * len(self.states)
        self.t0 = now

codec = None
clock = EpochClock()     # the PC clock in pong2 and in v3 tokens (a random offset per process)
in_sock = None
_sender_clock = None     # (the sender's time from the last ping [ns], perf_counter_ns of its arrival) - the v3 token for the sender
_warned_idx = set()
_digital = [None] * 4    # the last button state (mask, d-pad, triggers pressed) per slot - a change = shortcut handling
_pressed = [None] * 4    # (mask, d-pad, L2 >= TRIG_ON, R2 >= TRIG_ON) per slot - a change = a "button -> game" sample
_key_warned = [float("-inf")]   # when we last warned about a different HMAC key
_auth_warned = {"legacy": float("-inf"), "stale": float("-inf")}
_first_ping = [None]     # when (monotonic) this process got the sender's first ping
CLOCK_SYNC_S = 1.0       # for this long after the first ping the sender may still stamp packets with the previous process's clock


def sender_token():
    """The v3 token for the sender: its clock now [ms, 32 bits] (the last ping + the time since its arrival), or None."""
    sc = _sender_clock
    if sc is None:
        return None
    return ((sc[0] + time.perf_counter_ns() - sc[1]) // 1_000_000) & 0xFFFFFFFF


def note_ping(t0, arr):
    """The sender's clock from a ping. A v3 ping is not checked for freshness, so a replayed old recording
    must not turn the clock back: only a newer time is accepted, unless the sender was silent for > 1 s (a restart = a new clock)."""
    global _sender_clock
    if _first_ping[0] is None:
        _first_ping[0] = time.monotonic()
    sc = _sender_clock
    if sc is None or arr - sc[1] > 1_000_000_000 or t0 >= sc[0] + (arr - sc[1]) - 500_000_000:
        _sender_clock = (t0, arr)


def make_codec():
    """A Codec with the key from the configuration; the switch to v3 is remembered in auth.json next to remap.json."""
    return Codec(C.KEY, token=sender_token, now_ms=clock.ms32,
                 state_path=os.path.join(os.path.dirname(C.REMAP_PATH), "auth.json"))


def init():
    """Create the codec and bind the UDP receiving socket (with a read timeout so that silence can be detected); warn when no HMAC key is
    set."""
    global codec, in_sock
    codec = make_codec()
    if not C.KEY:
        print("WARNING: no HMAC key - anyone on the local network can drive this PC through the receiver "
              "(mouse, master actions). Set the same key on both sides (docs/SECURITY.md)")
    in_sock = bind_udp(C.IN_PORT, "pad events", 1 << 20)
    recv_timeout(in_sock, 0.1)   # lets us detect silence from the sender (the timeout is in the kernel - no select() per packet)


def close():
    """Close the receiving socket (after the network loop has stopped) - the port is free for a new process after a restart."""
    flush_repeats()
    if in_sock is not None:
        in_sock.close()


def send_peer(pkt):
    """Send a packet to the sender (ignored while it is unknown or the packet is None); send errors are dropped."""
    p = S.peer
    if p is not None and pkt is not None:     # None: v3 without the sender's clock (before the first ping)
        try:
            in_sock.sendto(pkt, p)
        except OSError:
            pass


REPEAT_DELAYS = (0.01, 0.03, 0.06)   # repeats of packets that must not get lost (commands, triggers, scfg) [s]
_repeats = set()                      # scheduled repeats (threading.Timer)
_repeats_lock = threading.Lock()


def _repeat_fire(pkt, timer):
    """Timer callback: send a scheduled repeat unless flush_repeats already did."""
    with _repeats_lock:
        if timer not in _repeats:
            return                    # already sent by flush_repeats
        _repeats.discard(timer)
    send_peer(pkt)


def send_repeat(pkt, n):
    """The same packet (the same number - the sender handles it once) now and n-1 times later, after REPEAT_DELAYS.
    Copies sent at once got lost together in one burst of losses, and each had its own number, so the sender ran the
    command several times (only its repeat locks protected it)."""
    if pkt is None:
        return
    send_peer(pkt)
    for d in REPEAT_DELAYS[:max(0, n - 1)]:
        timer = threading.Timer(d, _repeat_fire)
        timer.args, timer.daemon = (pkt, timer), True
        with _repeats_lock:
            _repeats.add(timer)
        timer.start()


def flush_repeats():
    """Scheduled repeats at once (receiver shutdown - the socket is about to vanish; tests)."""
    with _repeats_lock:
        pending = list(_repeats)
        _repeats.clear()
    for timer in pending:
        timer.cancel()
        send_peer(timer.args[0])


def send_feedback(i, vals):
    """Send the rumble / lightbar values of slot i."""
    send_peer(codec.pack(T_FB, i, *vals))


def send_cmd(i, cmd, arg=0):
    """A command to the sender (3x - UDP may drop a packet; the sender ignores repeats)."""
    send_repeat(codec.pack(T_CMD, i, cmd, arg), 3)


def send_triggers(i, vals, repeat=1):
    """Trigger effects to the sender: 15 values = trig3 (with the game's effects), 12 = trig2, 4 = the old trig (resistance only);
    a change goes several times (a lost packet)."""
    t = T_TRIG3 if len(vals) > len(TRIG2_OFF) else T_TRIG2 if len(vals) == len(TRIG2_OFF) else T_TRIG
    send_repeat(codec.pack(t, i, *vals), repeat)


def scfg_values(s=None):
    """Settings for the sender (the scfg packet): Wake-on-LAN and events for the log."""
    s = s or rx_settings.cur
    w = s.wol
    flags = (SCFG_WOL if w.enabled else 0) | (SCFG_EVENTS if s.log.sender else 0)
    return (flags, mac_bytes(w.mac) or bytes(6), socket.inet_aton(w.bcast) if w.bcast else bytes(4),
            PAD_KEYS.index(BUTTON_CODES[w.button]), w.hold_ms)


def send_scfg(repeat=1):
    """Send the settings packet (Wake-on-LAN, events) to the sender, `repeat` times."""
    if codec is None or S.peer is None:      # startup (settings loaded before the socket) / the sender not known yet
        return
    send_repeat(codec.pack(T_SCFG, 255, *scfg_values()), repeat)


def _settings_changed(old, new):
    """A settings listener: when the Wake-on-LAN or sender-event settings change, push them to the sender."""
    if (old.wol, old.log.sender) != (new.wol, new.log.sender):
        send_scfg(3)


rx_settings.on_change(_settings_changed)


def batt_codes(pct, flags):
    """-> (the DSU battery code, the DS4 battery status byte)."""
    if pct > 100:
        return 5, 0x1B
    if flags & 1:
        full = pct >= 100
        return (0xEF if full else 0xEE), 0x10 | (11 if full else min(10, pct // 10))
    dsu = 5 if pct >= 90 else 4 if pct >= 60 else 3 if pct >= 30 else 2 if pct >= 10 else 1
    return dsu, min(8, pct * 8 // 100)


def clear_slot(i, forget_id=False):
    """Reset everything known about the pad in slot i (state, profile effects, shortcuts, latency, mouse); forget_id also forgets the pad
    identity - for a pad that really left."""
    S.alive[i] = False
    S.tp_down[i] = 0
    S.swallow[i] = False
    S.last_v[i] = None
    S.last_touch[i] = None
    S.batt_info[i] = (255, 0)
    S.info[i] = None
    S.seen.forget(lambda k: k[1] == i)
    _digital[i] = _pressed[i] = None
    hotkeys.reset(i)
    master.reset(i)
    rx_latency.clear(i)                # the previous pad's latency measurements do not mix with the next one's
    with S.lock:
        S.states[i]["lat"] = S.states[i]["blat"] = None
    if forget_id:                      # the pad physically left - the next one in this slot will send its own ident
        rx_latency.reset_buttons(i)    # the last presses (no time limit) belonged to that pad
        S.sender_info[i] = None
        S.gyro_cal[i] = None           # the gyro calibration belonged to that pad
        with S.profile_lock:
            S.pad_id[i] = None
            S.override[i] = None
            S.active[i] = S.active_key(i)
            S.remap_maps[i] = S.effective_profile(i, S.active[i])
    S.reset_state(i)
    mouse.reset(i)
    if forget_id:
        master.mouse_follows_master()  # the master left -> the mouse follows mouse_pads


def _latency(lat, arr):
    """Measurement fields from a state2 packet + the arrival time [ns] -> (arrival, phone, bt, net) or None (no measurement)."""
    pc_us, phone, bt = lat
    if not arr or not pc_us or phone == LAT_NONE:
        return None
    net = ((arr + clock.base) // 1000 - pc_us) & 0xFFFFFFFF      # pc_us in the pong2 clock (with the offset)
    if net >= 0x80000000:          # received "before" it was sent: a clock offset error (up to half the RTT) - this is ~0
        net -= 1 << 32
    if not -MAX_NET_US < net < MAX_NET_US:
        return None
    return arr, phone, bt, max(0, net)


def handle(t, i, data, arr=0):
    """A packet from the sender; arr = the arrival time (perf_counter_ns) - for latency measurement."""
    if t == T_BYE:   # the sender: the pad disconnected -> unplug the virtual DS4
        was = S.want[i].is_set() or S.alive[i]
        clear_slot(i, forget_id=True)
        S.want[i].clear()
        S.wake[i].set()
        S.ignore_until[i] = time.monotonic() + 1.0   # late packets must not recreate it
        if was:
            print(f"Controller {i}: disconnected by sender")
            ev.info(_("{pad} rozłączony").format(pad=ev.pad_name(i)))
        return
    S.last_seen[i] = time.monotonic()
    if not S.alive[i]:
        S.alive[i] = True
        print(f"Controller {i}: data stream started")
    if not S.want[i].is_set():
        ev.info(_("{pad} połączony").format(pad=ev.pad_name(i)))   # after a short silence the DS4 stayed - this is not a new connection
        S.want[i].set()   # the slot thread will create the virtual DS4
        rx_console.pad_connected(i)       # the master pad with an already known id -> auto-ON of the console mode
    if t in STATES:
        now_us = time.monotonic_ns() // 1000
        v = BODY[t].unpack_from(data, 4)
        lat = _latency(v[STATE_FIELDS:], arr) if t == T_STATE2 else None
        v = v[:STATE_FIELDS]
        if v == S.last_v[i]:
            return      # a repeat (heartbeat) - nothing changed
        S.last_v[i] = v
        mask = v[8] & MASK_ALL
        bd, b4 = batt_codes(v[9], v[10])
        S.batt_info[i] = (v[9], v[10])
        lx, ly, lt, rx, ry, rt = v[:6]
        hx, hy = clamp(v[6], -1, 1), clamp(v[7], -1, 1)
        dig = (mask, hx, hy, lt >> 5, rt >> 5)   # triggers to 1/8 precision (the trigger hysteresis in the console mode)
        btn = (mask, hx, hy, lt >= TRIG_ON, rt >= TRIG_ON)
        blocked = S.states[i]["blocked"]    # the Test view: the game gets a neutral state, shortcuts and the master do not work
        pressed = (lat is not None and not blocked and _pressed[i] is not None   # the first state after connecting
                   and btn != _pressed[i])                                       # is not a press
        _pressed[i] = btn
        if dig != _digital[i]:              # shortcuts / master / console mode only on a button change,
            _digital[i] = dig               # not on every gyro and stick sample (250 Hz)
            testmode.sync(i)                # the Test view opened / closed - the slot's shortcuts start from a clean state
            # the master's Options + Create: leaving the test (also from the pad under test) -
            if testmode.on_state(i, (mask, hx, hy, lt, rt)):
                blocked = False                                 # the inputs have already been swallowed
            elif not blocked and testmode.shortcuts_on():      # the Test view: no pad's shortcuts work
                hotkeys.on_state(i, mask, hx, hy)          # PS shortcuts first (a new PS press), then the master,
                master.on_state(i, mask, hx, hy, lt, rt)   # which may mark that PS as used in a combination
                rx_console.on_state(i, mask, hx, hy, lt, rt)
        with S.lock:
            s = S.states[i]
            s["raw"] = (mask, hx, hy, lt, rt)
            s["fwd"] = hotkeys.forward(i, mask, hx, hy)
            s["rawst"] = (lx, ly, rx, ry)
            if (p := S.probe[i]) is not None and len(p) < MAX_PROBE:   # drift measurement (rx_drift)
                p.append(s["rawst"])
            s["acc"], s["gyro"] = v[11:14], v[14:17]      # before recompute - the gyro affects the right stick / the mouse
            S.recompute(s, S.remap_maps[i])
            s["batt_dsu"], s["batt_ds4"] = bd, b4
            s["ts"] = now_us         # the arrival time of the sample - better for DSU than the packet build time (jitter)
            if s["lat"] is None and not blocked:   # measuring the oldest change waiting for a report (rx_ds4 - ViGEm)
                s["lat"] = lat
            if pressed and s["blat"] is None:   # separately: the oldest waiting button / trigger change
                s["blat"] = lat
            s["ver"] += 1
        S.notify(i)
        if s["gyro_vel"]:
            S.gyro_wake.set()
    elif t == T_TOUCH:
        v = ST_TOUCH.unpack_from(data, 4)
        if v == S.last_touch[i]:
            return
        S.last_touch[i] = v
        touch = ((v[0] & 1, v[1], min(v[2], 1919), min(v[3], 942)),
                 (v[4] & 1, v[5], min(v[6], 1919), min(v[7], 942)))
        with S.lock:
            s = S.states[i]
            s["touch"] = touch
            if not S.mouse_mode[i]:   # in the mouse mode the touch is only remembered
                s["ver"] += 1
        if not S.mouse_mode[i]:
            S.notify(i)
    elif t == T_CLICK:
        down = data[4] if data[4] <= 2 else 1     # 0 = released, 1 = click, 2 = a two-finger click (right button)
        if down == S.tp_down[i]:
            return
        S.tp_down[i] = down
        S.states[i]["rtp"] = down          # the Test view: the click is visible in the mouse mode and under the lock too
        if S.states[i]["blocked"]:
            S.swallow[i] = False
            if not S.mouse_mode[i]:
                # the live state also under the lock (the game gets a neutral report anyway): a click held before the test
                # and released during it stayed "pressed" in the state - after the test the game saw a pressed touchpad
                # until it was clicked once more (the sender's repeats are rejected by the comparison with tp_down)
                with S.lock:
                    S.states[i]["tp"] = bool(down)
            return
        shortcuts = testmode.shortcuts_on()           # the Test view: a click stays a click, no shortcuts
        if down and shortcuts:
            rx_console.on_touch_click(i)
        if down and shortcuts and S.states[i]["raw"][0] & PS_BIT:   # PS + touchpad click = a mode change (raw PS)
            S.swallow[i] = True
            hotkeys.mark_used(i)
            if not master.check(i, "mouse"):
                return
            mouse.set_mode(i, not S.mouse_mode[i])
            F.pulse(i, "mouse" if S.mouse_mode[i] else "ds4")
            print(f"Controller {i}: touchpad -> {'MOUSE' if S.mouse_mode[i] else 'DS4 touch'} mode")
            return
        if not down and S.swallow[i]:
            S.swallow[i] = False
            return
        if S.mouse_mode[i]:
            mouse.mouse_button(i, down)
        else:
            with S.lock:
                S.states[i]["tp"] = bool(down)
                S.states[i]["ver"] += 1
            S.notify(i)
    elif t == T_MOUSE:
        if S.mouse_mode[i] and not S.states[i]["blocked"]:
            dx, dy, fl = ST_MOUSE.unpack_from(data, 4)
            mouse.mouse_rel(i, dx, dy, fl)
    elif t == T_IDENT:
        pid = BODY[T_IDENT].unpack_from(data, 4)[0].rstrip(b"\0").decode("ascii", "replace").lower()
        if pid and pid != S.pad_id[i]:
            with S.profile_lock:              # the game thread (set_game) must not recompute the profile halfway through a pad change
                S.pad_id[i] = pid
                S.override[i] = None
                S.reload_profile(i)
            rx_console.pad_connected(i, ident=True)   # a new pad in the slot - if it is the master, auto-ON of the console mode
            master.mouse_follows_master()  # touchpad as a mouse: the master pad (it may have just arrived or changed slot)
            n = S.profile_changes(i)
            print(f"Controller {i}: id {pid}, profile {S.profile_label(S.active[i])}"
                  + (f" ({n} changes)" if n else ""))
            ev.info(_("{pad}: pad {id}").format(pad=ev.pad_name(i), id=pid))
    elif t == T_VERSION:
        v = BODY[T_VERSION].unpack_from(data, 4)
        if v != S.sender_info[i]:
            S.sender_info[i] = v
            print(f"Controller {i}: sender v{v[0]}, rumble methods {v[1]}, reason {v[2]}")
    elif t == T_INFO:
        rtt, pad_hz, motion_hz = ST_INFO.unpack_from(data, 4)
        S.info[i] = (None if rtt == RTT_NONE else rtt / 10, pad_hz, motion_hz, time.monotonic())
    elif t == T_CALRES:
        on_calres(i, data)


def on_calres(i, data):
    """The sender's answer to a gyro calibration -> S.gyro_cal (the window); the calibration result goes to the log too."""
    v = BODY[T_CALRES].unpack_from(data, 4)
    prev = S.gyro_cal[i]
    S.gyro_cal[i] = (v[0], v[1], v[2:5], v[5:8], v[8], time.monotonic())
    if prev is not None and prev[:5] == S.gyro_cal[i][:5]:
        return                          # a repeat (the command goes 3x - so does the answer)
    if v[0] == CAL_OK:
        print(f"Controller {i}: gyro calibrated, bias {list(v[2:5])}")
        ev.info(_("{pad}: żyroskop skalibrowany").format(pad=ev.pad_name(i)))
    elif v[0] == CAL_MOVED:
        print(f"Controller {i}: gyro calibration rejected - pad was moving")
        ev.warn(_("{pad}: kalibracja żyroskopu przerwana - pad się ruszał").format(pad=ev.pad_name(i)))


def on_build(data, now, t=T_BUILD):
    """A build / build2 packet: (the sender's commit, the commit time, the update state UPD_*) -> S.sender_build;
    build2 also the addresses the sender's Wake-on-LAN will use -> S.sender_wol."""
    v = BODY[t].unpack_from(data, 4)
    b = (cstr(v[0]), v[1], v[2])
    if S.sender_build is None or S.sender_build[:3] != b:
        print(f"Sender build {b[0] or '?'} (update state {v[2]})")
    S.sender_build = (*b, now)
    if t == T_BUILD2:
        S.sender_wol = (mac_text(v[3]), socket.inet_ntoa(v[4]) if any(v[4]) else "")


def on_event(i, data):
    """An event packet -> the log, when sender events are on in the panel (repeats are rejected by the event number)."""
    if rx_settings.cur.log.sender:
        return ev.on_sender_event(i, data)
    return None


def check_stale(now):
    """Silence from the sender: first a state reset (stale_s), then unplugging the virtual DS4 (unplug_s)."""
    for i in range(C.NUM_PADS):
        silent = now - S.last_seen[i]
        if S.alive[i] and silent > C.STALE_S:
            print(f"Controller {i}: no data for {C.STALE_S}s - state reset")
            ev.warn(_("{pad}: przerwa w sygnale ponad {s} s").format(pad=ev.pad_name(i), s=f"{C.STALE_S:g}"))
            clear_slot(i)
        if S.want[i].is_set() and silent > C.UNPLUG_S:
            print(f"Controller {i}: no data for {C.UNPLUG_S}s - unplugging virtual DS4")
            ev.warn(_("{pad}: brak sygnału przez {s} s - odłączony").format(pad=ev.pad_name(i), s=f"{C.UNPLUG_S:g}"))
            S.want[i].clear()
            S.wake[i].set()
            # like after a bye packet: the next pad in this slot (maybe another) must send its identifier - otherwise it
            # would get the previous one's profile, and if that was the master, it would switch on the console mode and the master's rights
            clear_slot(i, forget_id=True)


def check_key(now):
    """Sender packets rejected because of a different HMAC key setting (proto.Codec.mismatch_t) -> a warning in the log,
    at most every KEY_WARN_S. Without it the pads simply did not work, and the only hint was the sender's log."""
    if now - codec.mismatch_t < 1.0 and now - _key_warned[0] >= KEY_WARN_S:
        _key_warned[0] = now
        print("Rejecting sender packets: HMAC key differs between sender and receiver (--key / key in config)")
        ev.warn(_("Odrzucono pakiety nadajnika: inny klucz HMAC (key) w nadajniku i w receiverze - ustaw ten sam "
                  "albo usuń go z obu"))
    if now - codec.legacy_t < 1.0 and now - _auth_warned["legacy"] >= KEY_WARN_S:
        _auth_warned["legacy"] = now
        print("Rejecting signed v2 packets: the sender switched to protocol v3 earlier - an old sender version or a "
              "replayed recording (after a downgrade delete auth.json next to the receiver)")
        ev.warn(_("Odrzucono podpisane pakiety starego formatu (v2): nadajnik w starej wersji albo powtórzone nagranie "
                  "- zaktualizuj nadajnik"))
    if (now - codec.stale_t < 1.0 and now - _auth_warned["stale"] >= KEY_WARN_S
            and _first_ping[0] is not None and codec.stale_t - _first_ping[0] > CLOCK_SYNC_S):
        # without the ping condition: a quick receiver restart (< 3 s) gave a false alarm - until the first pong2 from the
        # new process the sender stamps v3 packets with the old one's clock (another random offset), and that is not a recording
        _auth_warned["stale"] = now
        print(f"Rejected sender packets with an expired freshness token ({codec.stale} total): replay or clock problem")
        ev.warn(_("Odrzucono przeterminowane pakiety nadajnika (powtórzone nagranie albo problem z zegarem)"))


class SenderWatch:
    """The sender online / offline -> the log; the first contact and every SCFG_S -> the settings for the sender (scfg)."""

    def __init__(self):
        """Start as offline with no settings sent yet."""
        self.online, self.t_scfg = False, 0.0

    def tick(self, now):
        """Log a change of the sender's online state and, while online, send the settings every SCFG_S."""
        online = S.sender_online(now)
        if online != self.online:
            self.online = online
            if online:
                ev.info(_("Nadajnik online ({ip})").format(ip=S.peer[0] if S.peer else "?"))
            else:
                ev.warn(_("Nadajnik offline - brak pakietów od {s} s").format(s=f"{S.SENDER_TIMEOUT:g}"))
                S.sender_wol = None
        if online and now - self.t_scfg >= SCFG_S:
            self.t_scfg = now
            send_scfg()


def net_loop():
    """The network thread: receive a datagram (a timeout is the idle tick), run the periodic checks every 0.1 s, then authenticate / parse
    the packet and dispatch it by type; per-pad packets go to handle() after the freshness and slot checks."""
    boost_thread()
    link = LinkStats(C.NUM_PADS)
    watch = SenderWatch()
    stats = {}
    bad = 0
    t_print = t_check = time.monotonic()
    while not S.stop.is_set():
        try:
            data, addr = in_sock.recvfrom(2048)
            arr = time.perf_counter_ns()        # the arrival time (the PC clock as in pong2) - the start of the "pc" segment
        except (TimeoutError, BlockingIOError):   # the receive timeout (SO_RCVTIMEO): Windows / Linux
            data = None
        except OSError as e:
            if getattr(e, "winerror", None) == WSAETIMEDOUT:
                data = None                     # the receive timeout (if it did not arrive as a TimeoutError)
            else:
                # a foreign, too large datagram (WSAEMSGSIZE) or ICMP must not stall reception for 10 ms
                if getattr(e, "winerror", None) not in (10040, 10054):
                    time.sleep(0.01)
                continue

        now = time.monotonic()
        if now - t_check >= 0.1:
            t_check = now
            check_stale(now)
            link.tick(now)
            watch.tick(now)
            check_key(now)
        if not data:
            continue
        if C.ALLOWED_IP and addr[0] != C.ALLOWED_IP:
            continue

        p = codec.parse(data)
        if p is None:
            bad += 1
            continue
        t, i, sq = p
        S.last_pkt = now
        link.packet(sq, now)
        if t in STATES and i < C.NUM_PADS:
            link.states[i] += 1

        if C.DEBUG:
            key = (t, i, len(data))
            stats[key] = stats.get(key, 0) + 1
            if now - t_print >= 1.0:
                print("packets/s (type, ctrl, len):", stats, f"| rejected total: {bad}")
                stats.clear()
                t_print = now

        if t == T_UPDMSG:  # the reason for a sender update failure (arrives before the build packet with the result)
            S.peer = addr
            S.sender_update_msg = BODY[T_UPDMSG].unpack_from(data, 4)[0].rstrip(b"\0").decode("utf-8", "ignore")
            continue
        if t in (T_BUILD, T_BUILD2):  # the sender's code version and its update state (the whole sender, idx 255)
            S.peer = addr
            on_build(data, now, t)
            continue
        if t in (T_BTSTATE, T_BTDEV, T_BTCTRL):  # the sender's Bluetooth (idx 255): state, devices, controllers
            S.peer = addr
            if S.seen.fresh((WIRE[t], 255), sq, now):
                rx_bt.on_packet(t, data, now)
            continue
        if t == T_EVENT:  # a sender event for the log (idx = the pad's slot or 255)
            on_event(i, data)
            continue
        if t == T_PING:   # ping -> pong2 (the PC clock for latency measurement) and pong (RTT; an older sender knows only this)
            S.peer = addr
            t0 = BODY[T_PING].unpack_from(data, 4)[0]
            note_ping(t0, arr)
            send_peer(codec.pack(T_PONG2, i, t0, clock.ns() // 1000))
            send_peer(codec.pack(T_PONG, i, t0))
            continue
        if t not in RX_TYPES:
            continue
        if i >= C.NUM_PADS:
            if i not in _warned_idx:
                _warned_idx.add(i)
                print(f"Ignoring controller index {i}: receiver has only {C.NUM_PADS} slot(s) (--pads)")
            continue
        if now < S.ignore_until[i] and t != T_BYE:
            continue
        if not S.seen.fresh((WIRE.get(t, t), i), sq, now):     # state and state2: one counter
            continue
        S.peer = addr
        try:
            handle(t, i, data, arr)
        except Exception as e:
            print("handle error:", repr(e))
            ev.error(_("Błąd obsługi pakietu: {err}").format(err=repr(e)))
