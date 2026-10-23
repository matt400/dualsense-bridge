"""Sender: the UDP socket, sending packets, receiving feedback and pongs, pinging, keeping the socket alive."""
from __future__ import annotations

import asyncio
import collections
import contextlib
import socket
import time
from collections.abc import Callable
from typing import Any, Final

import sender_events as events
import sender_state as state
from proto import (BODY, E_NET_DOWN, E_NO_RECEIVER, NS_PER_MS, RTT_NONE, UINT32_MASK, Codec, EpochClock, SeqFilter, T_BTCMD,
                   T_CMD, T_FB, T_PING, T_PONG, T_PONG2, T_SCFG, T_TRIG, T_TRIG2, T_TRIG3)
from sender_config import C

# the packet header is 4 bytes (see proto.HDR); the body of every received packet is unpacked right after it
HEADER_LEN: Final = 4
RECV_SIZE: Final = 256                # larger than any packet the receiver sends to the sender
NS_PER_US: Final = 1000
TOS_DSCP_EF: Final = 0xB8             # DSCP "expedited forwarding" in the IP TOS byte
SO_PRIORITY_VOICE: Final = 6          # Linux socket priority that maps to the Wi-Fi "voice" queue
BROADCAST_ALL: Final = 255            # the idx of packets that belong to no pad
NEW_RECEIVER_S: Final = 30.0
LEARN_S: Final = 5.0                  # every this often (while the PC is up) the PC's MAC is learned from ARP for Wake-on-LAN
BUILD_S: Final = 5.0                  # every this often the sender's code version (build packet) goes to the receiver window
PING_S: Final = 0.25                  # 4 pings/s: the 2 s median is stable, and a single spike does not change it
ALIVE_S: Final = 3.0                  # no pong for this long = the receiver is gone
NO_REPLY_WARN_S: Final = 4.0          # no pong at all this long after the start = probably a wrong address / key
STATS_EVERY_S: Final = 5.0
SOCK_RETRY_S: Final = 1.0
REPEAT_DELAYS: Final = (0.01, 0.03)   # emit_repeat repeats [s]
PROBE_EVERY: Final = 4                # with a key, before switching to v3: an extra v3 ping every this many pings (1/s)
RTT_HISTORY: Final = 64
RTT_RECENT: Final = 8

codec: Codec = Codec()                # replaced by init() once the key is known
clock = EpochClock()                  # the sender's clock in pings and v3 stamps (random offset per process)
sock: socket.socket | None = None
sock_bad = False
tx_count = 0
rtts: collections.deque[float] = collections.deque(maxlen=RTT_HISTORY)    # bounded so it does not grow while --stats is off
pings_sent = 0
pongs = 0
fb_filter = SeqFilter(1.0)            # with a time window: a receiver restart (random seq) does not block feedback
on_feedback: Callable[[int, tuple[int, int, int, int, int]], None] | None = None   # (idx, (small, large, r, g, b)); set in main
on_command: Callable[..., None] | None = None       # callback(idx, cmd, arg) set in main
on_triggers: Callable[[int, tuple[Any, ...]], None] | None = None   # callback(idx, trig2 values (12) or trig (4)) set in main
on_scfg: Callable[..., None] | None = None          # callback(flags, mac, broadcast, button, hold ms) set in main
on_bt: Callable[..., None] | None = None            # callback(BT_* operation, argument, MAC 6 B) set in main (sender_bt)
scfg_t = 0.0                          # when the scfg packet arrived - the receiver knows build2 and events
recent_rtt: collections.deque[float] = collections.deque(maxlen=RTT_RECENT)   # latest measurements - for the info packet
last_pong = 0.0


class PcClock:
    """The PC clock as seen from the phone: an NTP-style offset computed from pong2 packets (PC time at the moment of reply).

    How it works: every pong2 gives (t0 = ping sent, pc_us = PC time when it answered, t3 = pong received). The
    offset estimate is pc_us minus the midpoint of t0 and t3. We keep the measurement with the smallest RTT out of the
    last SYNC_N (~3 s): its error is at most half that RTT (Ethernet: ~0.1 ms), and the short window limits crystal
    drift (50 ppm = 0.15 ms per 3 s). Without a pong2 for SYNC_MAX_AGE_S (an older receiver, a network gap) there is no
    clock - the state then travels in a normal packet, without a timestamp.
    """

    SYNC_N, SYNC_MAX_AGE_S = 12, 3.0
    JUMP_US = 1_000_000               # an offset that changed by this much = another receiver process (another PC clock)
    MAX_RTT_NS = 1_000_000_000        # a longer round trip is a stall, not a measurement

    def __init__(self) -> None:
        """Create an unsynchronised clock."""
        self.samples: collections.deque[tuple[int, int]] = collections.deque(maxlen=self.SYNC_N)   # (rtt ns, offset µs)
        self.offset_us: int | None = None
        self.t_sync = 0.0

    def pong(self, t0_ns: int, pc_us: int, t3_ns: int) -> None:
        """Feed one pong2 measurement into the estimator; implausible RTTs are discarded."""
        rtt = t3_ns - t0_ns
        if rtt < 0 or rtt > self.MAX_RTT_NS:
            return
        now = time.monotonic()
        if now - self.t_sync > self.SYNC_MAX_AGE_S:
            # after a gap the old measurements no longer fit: a PC restart (another clock) or crystal drift (50 ppm = 180 ms
            # per hour); the smallest-RTT measurement from before the gap would beat the new ones for SYNC_N pongs
            self.samples.clear()
        off = pc_us - (t0_ns + t3_ns) // (2 * NS_PER_US)
        if self.offset_us is not None and abs(off - self.offset_us) > self.JUMP_US:
            # the receiver was restarted within SYNC_MAX_AGE_S: its clock has another random offset - the old
            # measurements (often with a smaller RTT) would win for SYNC_N pongs, and v3 stamps would be rejected
            self.samples.clear()
        self.samples.append((rtt, off))
        self.offset_us = min(self.samples)[1]
        self.t_sync = now

    def _pc_time_us(self) -> int | None:
        """Return the PC time now in µs (not truncated), or None while the clock is not synchronised."""
        if self.offset_us is None or time.monotonic() - self.t_sync > self.SYNC_MAX_AGE_S:
            return None
        return clock.ns() // NS_PER_US + self.offset_us

    def now_us(self) -> int | None:
        """Return the PC time now [µs, low 32 bits] or None (not synchronised); 0 is reserved, so it becomes 1."""
        t = self._pc_time_us()
        return None if t is None else (t & UINT32_MASK) or 1

    def now_ms(self) -> int | None:
        """Return the PC time now [ms, low 32 bits] - the v3 freshness stamp for the receiver - or None (not synchronised)."""
        t = self._pc_time_us()
        return None if t is None else (t // (NS_PER_MS // NS_PER_US)) & UINT32_MASK


pc_clock = PcClock()


def rtt_x10() -> int:
    """Return the median of the latest ping measurements in tenths of a ms (for T_INFO); RTT_NONE if no reply for 3 s."""
    if not recent_rtt or time.monotonic() - last_pong > ALIVE_S:
        return RTT_NONE
    return min(RTT_NONE - 1, round(sorted(recent_rtt)[len(recent_rtt) // 2] * 10))


def init() -> None:
    """Create the packet codec (HMAC key, v3 freshness token from the PC clock, replay-state file) and warn if there is no key."""
    global codec
    # the lambda looks pc_clock up at call time: the clock object is replaced when the sender resynchronises (and in tests)
    codec = Codec(C.KEY, token=lambda: pc_clock.now_ms(), now_ms=clock.ms32,
                  state_path=getattr(C, "AUTH_FILE", "") or None)
    if not C.KEY:
        print("WARNING: no HMAC key - anyone on the local network can send commands to this sender "
              "(update, Bluetooth pairing, pad power-off). Set the same key on both sides (docs/SECURITY.md)")


def open_sock(loop: asyncio.AbstractEventLoop) -> bool:
    """(Re)create the UDP socket connected to the receiver and register it with the event loop.

    The previous socket, if any, is unregistered and closed first. Two priority hints are set on a best-effort basis:
    DSCP EF in the IP header and the Linux socket priority 6, which maps to the Wi-Fi "voice" queue.
    connect() on a UDP socket only fixes the destination, so later send() calls skip per-packet address handling.
    The socket is non-blocking and readable events are handled by on_readable.

    Returns:
        True on success.
    """
    global sock, sock_bad
    if sock is not None:
        loop.remove_reader(sock)
        sock.close()
        sock = None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    with contextlib.suppress(OSError):                     # a hint only: some networks / sandboxes refuse it
        s.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, TOS_DSCP_EF)
    with contextlib.suppress(OSError, AttributeError):     # SO_PRIORITY exists on Linux only
        s.setsockopt(socket.SOL_SOCKET, socket.SO_PRIORITY, SO_PRIORITY_VOICE)
    try:
        s.connect((C.PC_ADDR, C.PORT))
    except OSError:
        s.close()
        return False
    s.setblocking(False)
    loop.add_reader(s, on_readable)
    sock, sock_bad = s, False
    return True


def close_sock() -> None:
    """Unregister and close the socket (safe to call with no loop or no socket)."""
    global sock
    if sock is not None:
        with contextlib.suppress(RuntimeError):            # no running loop: the reader is gone with it
            asyncio.get_running_loop().remove_reader(sock)
        sock.close()
        sock = None


def send_raw(data: bytes) -> None:
    """Send an already packed datagram.

    A full buffer or a refused connection just drops the packet (UDP); any other OS error marks the socket as bad so
    that sock_keeper recreates it.
    """
    global sock_bad, tx_count
    s = sock
    if s is None:
        return
    try:
        s.send(data)
        tx_count += 1
    except (BlockingIOError, ConnectionRefusedError):
        pass                          # UDP: a lost packet is part of the protocol
    except OSError:
        sock_bad = True


def emit(ptype: int, idx: int, *vals: int | bytes) -> None:
    """Pack a packet of type ptype for slot idx with the given field values and send it."""
    data = codec.pack(ptype, idx, *vals)
    if data is not None:              # None: v3 without the PC clock (before the first pong2) - the receiver would reject it anyway
        send_raw(data)


def emit_repeat(ptype: int, idx: int, *vals: int | bytes, delays: tuple[float, ...] = REPEAT_DELAYS) -> None:
    """Send a packet that must not get lost (bye): the same one now and again after each of ``delays`` seconds.

    The copies carry the same number, so the receiver handles the packet once. Three copies at once were lost
    together in a single burst of losses (interference, a clogged queue), so the repeats are spread in time with
    loop.call_later.
    """
    data = codec.pack(ptype, idx, *vals)
    if data is None:
        return
    send_raw(data)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:              # no loop (shutdown): nothing to wait with, send the copies back to back
        for _ in delays:
            send_raw(data)
        return
    for delay in delays:
        loop.call_later(delay, send_raw, data)


def _on_pong(data: bytes) -> None:
    """Record the RTT of a plain pong (the receiver's answer to our ping) and note that the receiver is alive."""
    global pongs, last_pong
    rtt = (clock.ns() - BODY[T_PONG].unpack_from(data, HEADER_LEN)[0]) / NS_PER_MS
    rtts.append(rtt)
    recent_rtt.append(rtt)
    last_pong = time.monotonic()
    pongs += 1


def _on_scfg(sq: int, data: bytes) -> None:
    """Take the Wake-on-LAN settings from the receiver; the arrival time also tells that it knows build2 and events."""
    global scfg_t
    now = time.monotonic()
    if fb_filter.fresh(("scfg",), sq, now):
        scfg_t = now
        if on_scfg is not None:
            on_scfg(*BODY[T_SCFG].unpack_from(data, HEADER_LEN))


def _on_control(t: int, idx: int, sq: int, data: bytes) -> None:
    """Handle feedback, commands, trigger settings and Bluetooth requests.

    Each stream passes through its own SeqFilter key, so UDP duplicates (the receiver repeats important packets) and
    old reordered packets are dropped without blocking the other streams.
    """
    now = time.monotonic()
    if t == T_FB:
        if fb_filter.fresh(idx, sq, now) and on_feedback is not None:
            on_feedback(idx, BODY[T_FB].unpack_from(data, HEADER_LEN))
    elif t == T_CMD:
        if fb_filter.fresh(("cmd", idx), sq, now) and on_command is not None:
            on_command(idx, *BODY[T_CMD].unpack_from(data, HEADER_LEN))
    elif t in (T_TRIG, T_TRIG2, T_TRIG3):   # trig3: + game effects (v11+), trig2: profile (v8+), trig: resistance only
        if fb_filter.fresh(("trig", idx), sq, now) and on_triggers is not None:
            on_triggers(idx, BODY[t].unpack_from(data, HEADER_LEN))
    elif t == T_BTCMD and fb_filter.fresh(("bt",), sq, now) and on_bt is not None:   # phone Bluetooth from the window (v10+)
        on_bt(*BODY[T_BTCMD].unpack_from(data, HEADER_LEN))


def _dispatch(t: int, idx: int, sq: int, data: bytes) -> None:
    """Handle one authenticated packet by type; pongs update the RTT statistics and the PC clock estimate."""
    if t == T_PONG2:                  # the PC clock for latency measurement (RTT is computed by the plain pong right after it)
        t0, pc_us = BODY[T_PONG2].unpack_from(data, HEADER_LEN)
        pc_clock.pong(t0, pc_us, clock.ns())
    elif t == T_PONG:
        _on_pong(data)
    elif t == T_SCFG:
        _on_scfg(sq, data)
    else:
        _on_control(t, idx, sq, data)


def on_readable() -> None:
    """Socket read callback: drain every waiting datagram and dispatch it by packet type.

    Packets that fail authentication/parsing (codec.parse returns None) are ignored.
    """
    s = sock
    if s is None:
        return
    while True:
        try:
            data = s.recv(RECV_SIZE)
        except OSError:               # BlockingIOError / InterruptedError are OSErrors: the queue is drained (or the socket died)
            return
        p = codec.parse(data)
        if p is not None:
            _dispatch(p[0], p[1], p[2], data)


def new_receiver(now: float | None = None) -> bool:
    """Return True if the receiver sent scfg recently - it then knows the build2 and event packets."""
    return bool(scfg_t) and (time.monotonic() if now is None else now) - scfg_t < NEW_RECEIVER_S


def _receiver_watch(alive: bool, was: bool) -> None:
    """On every ping: flush events to the receiver while it answers; losing the answer is itself an event (delivered after it returns)."""
    if alive:
        events.flush(emit)
    elif was:
        events.warn(E_NO_RECEIVER)


def _send_pings() -> None:
    """Send the ping carrying the sender's clock, plus (while a key is set but v3 is not active) an occasional v3 ping.

    A v3-capable receiver answers the v3 ping with v3, which switches both sides; an older one rejects it.
    """
    global pings_sent
    t0 = clock.ns()
    emit(T_PING, BROADCAST_ALL, t0)
    if codec.key and not codec.v3 and pings_sent % PROBE_EVERY == 0:
        probe = codec.pack(T_PING, BROADCAST_ALL, t0, v3=True)
        if probe is not None:
            send_raw(probe)
    pings_sent += 1


def _print_stats(last_print: float, tx_prev: int) -> tuple[float, int]:
    """Print the --stats summary (packet rate, per-device report rates, RTT) and return the new (time, tx_count) marks."""
    now = time.monotonic()
    print(f"TX {(tx_count - tx_prev) / (now - last_print):.0f} pkt/s")
    for c in list(state.active.values()):
        for name, meter in c.meters.items():
            report = meter.report()
            if report:
                print(f"Pad {c.idx} {name}: {report[0]:.0f} Hz, przerwa p99 {report[1]:.1f} ms, max {report[2]:.1f} ms")
    if rtts:
        avg = sum(rtts) / len(rtts)
        print(f"RTT avg {avg:.1f} ms, max {max(rtts):.1f} ms, jitter {max(rtts) - min(rtts):.1f} ms "
              f"({len(rtts)} pongs, {pings_sent - pongs} lost total)")
        rtts.clear()
    else:
        print("RTT: no pongs in last 5 s")
    return now, tx_count


async def pinger() -> None:
    """Main housekeeping loop, four iterations per second.

    Each iteration sends a ping (see _send_pings; the pong gives the RTT). It also triggers the periodic build
    packet, the Wake-on-LAN MAC learning, the receiver-alive tracking (events), a one-time warning when no pong ever
    arrived, and, with --stats, a traffic/RTT summary every 5 seconds.
    """
    warned = False
    alive = False
    t_learn = 0.0
    last_print = time.monotonic()
    tx_prev = 0
    while True:
        _send_pings()
        if pings_sent % round(BUILD_S / PING_S) == 1:
            import sender_update                # sender_update imports sender_net
            sender_update.send_build()
        await asyncio.sleep(PING_S)
        now = time.monotonic()
        was, alive = alive, now - last_pong < ALIVE_S
        _receiver_watch(alive, was)
        if alive and now - t_learn >= LEARN_S:
            t_learn = now
            import sender_wol                   # sender_wol imports sender_net
            sender_wol.learn()
        if pings_sent >= NO_REPLY_WARN_S / PING_S and pongs == 0 and not warned:
            print("WARNING: no reply from receiver - check IP/port, firewall and --key (receiver downgraded to a "
                  "version without protocol v3? delete auth.json next to the sender)")
            warned = True
        if C.STATS and time.monotonic() - last_print >= STATS_EVERY_S:
            last_print, tx_prev = _print_stats(last_print, tx_prev)


async def sock_keeper() -> None:
    """Once a second make sure a usable socket exists (create it, or recreate it after a send error).

    Logs only on transitions (network up -> down and back) so a long outage does not flood the log, and raises an
    event to the receiver window when the network goes down.
    """
    loop = asyncio.get_running_loop()
    up = True
    while True:
        if sock is None or sock_bad:
            ok = open_sock(loop)
            if ok != up:
                print("Network OK" if ok else "Network unavailable - retrying every 1 s")
                if not ok:
                    events.warn(E_NET_DOWN)
                up = ok
        await asyncio.sleep(SOCK_RETRY_S)
