"""Sender: the UDP link to the receiver - the socket, sending packets, receiving feedback and pongs, pinging, keeping the socket alive.

All the state lives in one ``Net`` object, ``link``; the handlers of incoming packets are the typed fields of
``link.handlers`` (set in ``sender_controller.main``). The functions the rest of the sender calls (``emit``,
``emit_repeat``, ``send_raw``, ``new_receiver``, ...) are module-level names bound to ``link``'s methods, so a test can
replace one of them (``net.emit = fake``) or build its own ``Net`` without touching module globals.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import errno
import socket
import struct
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

import sender_config
import sender_events as events
import sender_selftest as selftest
import sender_state as state
from proto import (BODY, E_NET_DOWN, E_NO_RECEIVER, HAPT_CHUNK, HAPT_DATA_AT, HEADER_LEN, NS_PER_MS, RTT_NONE, SPK_BYTES,
                   SPK_DATA_AT, T_BTCMD, T_CFGSET, T_CMD, T_FB, T_HAPT, T_LEDS, T_PING, T_PONG2, T_SCFG, T_SPK, T_STCMD,
                   T_TRIG3, UINT32_MASK, VER3, VER_SHIFT, Codec, EpochClock, Field, SeqFilter)
from sender_config import C

# the packet header is 4 bytes (see proto.HDR); the body of every received packet is unpacked right after it
HAPT_HEAD: Final = struct.Struct("!IB")     # the hapt body before the samples: the first frame's number, flags
HAPT_AT: Final = HEADER_LEN + HAPT_DATA_AT   # where the samples of a hapt packet start
SPK_AT: Final = HEADER_LEN + SPK_DATA_AT     # where the samples of a spk packet start
RECV_SIZE: Final = 1024               # larger than any packet the receiver sends to the sender (the largest: spk, 981 B with a v3 trailer)
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
RESOLVE_AFTER_S: Final = 10.0         # no pong for this long: a host NAME may point elsewhere now (the PC got a new DHCP lease)
REPEAT_DELAYS: Final = (0.01, 0.03)   # emit_repeat repeats [s]
# send errors that only mean "this packet did not go": a full device queue, a neighbour that does not answer (the PC is off)
DROP_ERRNO: Final = frozenset({errno.ENOBUFS, errno.EHOSTUNREACH})
MAX_RTT_NS: Final = 1_000_000_000     # a pong later than this answers a stale (replayed) ping, not a measurement
RTT_HISTORY: Final = 64
RTT_RECENT: Final = 8

CLOCK: Final = EpochClock()           # the sender's clock in pings and v3 stamps (random offset per process)
FbValues = tuple[int, int, int, int, int]    # feedback: small motor, large motor, r, g, b


# ---- the handlers of incoming packets: what sender_controller wires in (the packet's body fields, unpacked) ----
class FeedbackFn(Protocol):
    """fb: rumble + lightbar of one pad."""

    def __call__(self, idx: int, vals: FbValues, /) -> None: ...  # noqa: D102


class CommandFn(Protocol):
    """cmd: a PC command (CMD_*) with its argument."""

    def __call__(self, idx: int, cmd: int, arg: int, /) -> None: ...  # noqa: D102


class TriggersFn(Protocol):
    """trig / trig2 / trig3: the trigger effects (4, 12 or 12 + raw game effects + flags values)."""

    def __call__(self, idx: int, vals: Sequence[Any], /) -> None: ...  # noqa: D102


class LedsFn(Protocol):
    """leds: LEDS_* flags, the mute LED, the player LEDs."""

    def __call__(self, idx: int, vals: tuple[int, int, int], /) -> None: ...  # noqa: D102


class AudioFn(Protocol):
    """hapt / spk: the first frame number, the HAPT_* / SPK_* flags and the samples (a view into the datagram, no copy)."""

    def __call__(self, idx: int, frame: int, flags: int, data: memoryview, /) -> None: ...  # noqa: D102


class ScfgFn(Protocol):
    """scfg: SCFG_* flags, the PC's MAC, the broadcast address, the Wake-on-LAN button bit, its hold time in ms."""

    def __call__(self, flags: int, mac: bytes, bcast: bytes, button: int, hold_ms: int, /) -> object: ...  # noqa: D102


class BtFn(Protocol):
    """btcmd: a BT_* operation, its argument, the MAC (6 B)."""

    def __call__(self, op: int, arg: int, mac: bytes, /) -> None: ...  # noqa: D102


class CfgFn(Protocol):
    """cfgset: whether it came authenticated (v3 + key), the CFG_* operation, the setting id, the reference, the value."""

    def __call__(self, authenticated: bool, op: int, key: int, ref: int, number: float, /) -> None: ...  # noqa: D102


class SelftestFn(Protocol):
    """stcmd: the ST_* operation, its argument, the request reference, a 32-bit and a 16-bit value."""

    def __call__(self, idx: int, op: int, arg: int, request: int, a: int, b: int, /) -> None: ...  # noqa: D102


@dataclass(slots=True)
class Handlers:
    """Who handles each kind of incoming packet; None = the packet is ignored (not wired in, or a test)."""

    feedback: FeedbackFn | None = None
    command: CommandFn | None = None
    triggers: TriggersFn | None = None
    leds: LedsFn | None = None
    haptics: AudioFn | None = None
    speaker: AudioFn | None = None
    scfg: ScfgFn | None = None
    bt: BtFn | None = None
    cfg: CfgFn | None = None
    selftest: SelftestFn | None = None


class PcClock:
    """The PC clock as seen from the phone: an NTP-style offset computed from pong2 packets (PC time at the moment of reply).

    How it works: every pong2 gives (t0 = ping sent, pc_us = PC time when it answered, t3 = pong received). The
    offset estimate is pc_us minus the midpoint of t0 and t3. We keep the measurement with the smallest RTT out of the
    last SYNC_N (~3 s): its error is at most half that RTT (Ethernet: ~0.1 ms), and the short window limits crystal
    drift (50 ppm = 0.15 ms per 3 s). Without a pong2 for SYNC_MAX_AGE_S (a network gap, a receiver restart) there is no
    clock - the state then travels in a normal packet, without a timestamp.
    """

    SYNC_N, SYNC_MAX_AGE_S = 12, 3.0
    JUMP_US = 1_000_000               # an offset that changed by this much = another receiver process (another PC clock)
    MAX_RTT_NS = 1_000_000_000        # a longer round trip is a stall, not a measurement

    def __init__(self, clock: EpochClock | None = None) -> None:
        """Create an unsynchronised clock that converts readings of ``clock`` (the sender's ping clock, CLOCK by default)."""
        self.clock = CLOCK if clock is None else clock
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

    def _pc_time_us(self, now: float | None = None) -> int | None:
        """Return the PC time now in µs (not truncated), or None while the clock is not synchronised.

        ``now`` is a ``time.monotonic()`` reading the caller already has (for the age of the synchronisation only; the
        PC time itself comes from ``clock``).
        """
        if self.offset_us is None or (time.monotonic() if now is None else now) - self.t_sync > self.SYNC_MAX_AGE_S:
            return None
        return self.clock.ns() // NS_PER_US + self.offset_us

    def now_us(self, now: float | None = None) -> int | None:
        """Return the PC time now [µs, low 32 bits] or None (not synchronised); 0 is reserved, so it becomes 1.

        ``now``: an optional ``time.monotonic()`` reading of the caller, see ``_pc_time_us``.
        """
        t = self._pc_time_us(now)
        return None if t is None else (t & UINT32_MASK) or 1

    def now_ms(self) -> int | None:
        """Return the PC time now [ms, low 32 bits] - the v3 freshness stamp for the receiver - or None (not synchronised)."""
        t = self._pc_time_us()
        return None if t is None else (t // (NS_PER_MS // NS_PER_US)) & UINT32_MASK


class Net:
    """The UDP link to the receiver: socket, codec, clocks, counters and the handlers of incoming packets.

    Attributes:
        codec: The packet codec (replaced by ``init`` once the key is known).
        clock: The sender's clock in pings and v3 stamps.
        pc_clock: The PC clock as seen from here (from pong2), for the state2 send time.
        sock: The UDP socket connected to the receiver, or None.
        sock_bad: A send failed in a way that needs a new socket (``sock_keeper`` recreates it).
        handlers: The handlers of incoming packets.
        tx_count: Datagrams sent.
        rtts: Ping round trips [ms] since the last --stats print (bounded, so it does not grow while --stats is off).
        recent_rtt: The latest round trips, for the info packet.
        pings_sent / pongs: Pings sent, pongs received.
        fb_filter: Duplicate / reorder filter of the control packets (with a time window: a receiver restart, with a
            random sequence, does not block feedback).
        scfg_t: When scfg last arrived (the receiver sends it every 5 s while it runs).
        last_pong: When the last pong arrived; -inf = none yet (with a small uptime "now - 0.0" would look like a live receiver).
    """

    def __init__(self, clock: EpochClock = CLOCK) -> None:
        """Create an idle link: no socket, a codec without a key until ``init``."""
        self.codec = Codec()
        self.clock = clock
        self.pc_clock = PcClock(clock)
        self.sock: socket.socket | None = None
        self.sock_bad = False
        self.handlers = Handlers()
        self.tx_count = 0
        self.rtts: collections.deque[float] = collections.deque(maxlen=RTT_HISTORY)
        self.recent_rtt: collections.deque[float] = collections.deque(maxlen=RTT_RECENT)
        self.pings_sent = self.pongs = 0
        self.fb_filter = SeqFilter(1.0)
        self.scfg_t = 0.0
        self.last_pong = float("-inf")

    def rtt_x10(self) -> int:
        """Return the median of the latest ping measurements in tenths of a ms (for T_INFO); RTT_NONE if no reply for 3 s."""
        if not self.recent_rtt or time.monotonic() - self.last_pong > ALIVE_S:
            return RTT_NONE
        return min(RTT_NONE - 1, round(sorted(self.recent_rtt)[len(self.recent_rtt) // 2] * 10))

    def init(self) -> None:
        """Create the packet codec (HMAC key, v3 freshness token from the PC clock) and warn if there is no key."""
        # the lambda looks pc_clock up at call time: the clock object is replaced when the sender resynchronises (and in tests)
        self.codec = Codec(C.KEY, token=lambda: self.pc_clock.now_ms(), now_ms=self.clock.ms32)
        if not C.KEY:
            print("WARNING: no HMAC key - anyone on the local network can send commands to this sender "
                  "(update, Bluetooth pairing, pad power-off). Set the same key on both sides (docs/SECURITY.md)")

    def open_sock(self, loop: asyncio.AbstractEventLoop) -> bool:
        """(Re)create the UDP socket connected to the receiver and register it with the event loop.

        The previous socket, if any, is unregistered and closed first. Two priority hints are set on a best-effort basis:
        DSCP EF in the IP header and the Linux socket priority 6, which maps to the Wi-Fi "voice" queue.
        connect() on a UDP socket only fixes the destination, so later send() calls skip per-packet address handling.
        The socket is non-blocking and readable events are handled by ``on_readable``.

        Returns:
            True on success.
        """
        if self.sock is not None:
            loop.remove_reader(self.sock)
            self.sock.close()
            self.sock = None
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
        loop.add_reader(s, self.on_readable)
        self.sock, self.sock_bad = s, False
        return True

    def close_sock(self) -> None:
        """Unregister and close the socket (safe to call with no loop or no socket)."""
        if self.sock is not None:
            with contextlib.suppress(RuntimeError):            # no running loop: the reader is gone with it
                asyncio.get_running_loop().remove_reader(self.sock)
            self.sock.close()
            self.sock = None

    def send_raw(self, data: bytes) -> None:
        """Send an already packed datagram.

        A full buffer, a refused connection or an unreachable host just drops the packet (UDP); any other OS error marks the
        socket as bad so that ``sock_keeper`` recreates it.
        """
        s = self.sock
        if s is None:
            return
        try:
            s.send(data)
            self.tx_count += 1
        except (BlockingIOError, ConnectionRefusedError):
            pass                          # UDP: a lost packet is part of the protocol
        except OSError as e:
            if e.errno not in DROP_ERRNO:
                self.sock_bad = True

    def emit(self, ptype: int, idx: int, *vals: Field) -> None:
        """Pack a packet of type ptype for slot idx with the given field values and send it."""
        data = self.codec.pack(ptype, idx, *vals)
        if data is not None:              # None: v3 without the PC clock (before the first pong2) - the receiver would reject it anyway
            self.send_raw(data)

    def emit_repeat(self, ptype: int, idx: int, *vals: Field, delays: tuple[float, ...] = REPEAT_DELAYS) -> None:
        """Send a packet that must not get lost (bye): the same one now and again after each of ``delays`` seconds.

        The copies carry the same number, so the receiver handles the packet once. Three copies sent at once get lost
        together in a single burst of losses (interference, a clogged queue), so the repeats are spread in time with
        loop.call_later.
        """
        data = self.codec.pack(ptype, idx, *vals)
        if data is None:
            return
        self.send_raw(data)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:              # no loop (shutdown): nothing to wait with, send the copies back to back
            for _ in delays:
                self.send_raw(data)
            return
        for delay in delays:
            loop.call_later(delay, self.send_raw, data)

    def _on_pong(self, data: bytes) -> None:
        """Handle a pong2 (the receiver's answer to our ping): the PC clock estimate, the RTT and that the receiver is alive."""
        now_ns = self.clock.ns()
        t0, pc_us = BODY[T_PONG2].unpack_from(data, HEADER_LEN)
        self.pc_clock.pong(t0, pc_us, now_ns)
        rtt_ns = now_ns - t0
        if not 0 <= rtt_ns <= MAX_RTT_NS:
            # the answer to a replayed old ping (a ping is exempt from the v3 freshness check, the pong is fresh) or to a ping of a
            # previous process (another clock base): not a round trip, it must not reach the statistics or the info packet
            return
        rtt = rtt_ns / NS_PER_MS
        self.rtts.append(rtt)
        self.recent_rtt.append(rtt)
        self.last_pong = time.monotonic()
        self.pongs += 1

    def _on_scfg(self, sq: int, data: bytes) -> None:
        """Take the Wake-on-LAN and log settings from the receiver; the arrival time also tells that the receiver runs."""
        now = time.monotonic()
        if self.fb_filter.fresh(("scfg",), sq, now):
            self.scfg_t = now
            values = BODY[T_SCFG].unpack_from(data, HEADER_LEN)
            if self.handlers.scfg is not None:
                self.handlers.scfg(*values)

    def _on_setting(self, t: int, idx: int, sq: int, data: bytes, now: float) -> None:
        """Handle the packets that set something on the sender: the game's LEDs (leds), a setting (cfgset), the self-test (stcmd)."""
        h, fresh = self.handlers, self.fb_filter.fresh
        if t == T_STCMD:
            # one stream per operation and argument: the three copies of a request share a number, a later request has a new one
            if fresh(("st", idx, data[HEADER_LEN], data[HEADER_LEN + 1]), sq, now) and h.selftest is not None:
                h.selftest(idx, *BODY[T_STCMD].unpack_from(data, HEADER_LEN))
        elif t == T_LEDS:
            if fresh(("leds", idx), sq, now) and h.leds is not None:
                h.leds(idx, BODY[T_LEDS].unpack_from(data, HEADER_LEN))
        elif fresh(("cfg", data[HEADER_LEN + 1]), sq, now) and h.cfg is not None:   # one stream per setting id (see _on_control)
            # authenticated = v3 (HMAC + freshness token, so no replay) AND a key: v3 cannot be parsed without one, the check is explicit
            h.cfg(bool(self.codec.key) and data[0] >> VER_SHIFT == VER3, *BODY[T_CFGSET].unpack_from(data, HEADER_LEN))

    def _on_control(self, t: int, idx: int, sq: int, data: bytes) -> None:
        """Handle feedback, commands, trigger settings, haptics chunks, speaker frames, settings and Bluetooth requests.

        Each stream passes through its own SeqFilter key, so UDP duplicates (the receiver repeats important packets) and
        old reordered packets are dropped without blocking the other streams. A packet that asks for something different each
        time (a command, a setting, a Bluetooth operation) has the thing it asks for in its key: the receiver sends such a request
        three times with one number, and with a single key per type the repeats of an earlier request would be dropped as old once a
        later request (another command, another setting) arrived - if the first copy got lost, the request would never run.
        """
        now = time.monotonic()
        h, fresh = self.handlers, self.fb_filter.fresh
        if t == T_FB:
            if fresh(idx, sq, now) and h.feedback is not None:
                if selftest.armed:          # the self-test is measuring: the write this packet causes is matched by the number
                    selftest.fb_arrived(idx, sq)
                h.feedback(idx, BODY[T_FB].unpack_from(data, HEADER_LEN))
        elif t == T_CMD:
            if fresh(("cmd", idx, data[HEADER_LEN]), sq, now) and h.command is not None:
                h.command(idx, *BODY[T_CMD].unpack_from(data, HEADER_LEN))
        elif t == T_TRIG3:
            if fresh(("trig", idx), sq, now) and h.triggers is not None:
                h.triggers(idx, BODY[t].unpack_from(data, HEADER_LEN))
        elif t in (T_LEDS, T_CFGSET, T_STCMD):  # the game's LEDs, a setting from the window, the pad self-test
            self._on_setting(t, idx, sq, data, now)
        elif t == T_BTCMD and fresh(("bt", data[HEADER_LEN]), sq, now) and h.bt is not None:   # phone Bluetooth (window)
            h.bt(*BODY[T_BTCMD].unpack_from(data, HEADER_LEN))
        elif t == T_HAPT and fresh(("hapt", idx), sq, now) and h.haptics is not None:   # HD haptics: no copy of the samples
            frame, flags = HAPT_HEAD.unpack_from(data, HEADER_LEN)
            h.haptics(idx, frame, flags, memoryview(data)[HAPT_AT:HAPT_AT + HAPT_CHUNK])
        elif t == T_SPK and fresh(("spk", idx), sq, now) and h.speaker is not None:   # the speaker: no copy either
            frame, flags = HAPT_HEAD.unpack_from(data, HEADER_LEN)
            h.speaker(idx, frame, flags, memoryview(data)[SPK_AT:SPK_AT + SPK_BYTES])

    def _dispatch(self, t: int, idx: int, sq: int, data: bytes) -> None:
        """Handle one authenticated packet by type; pongs update the RTT statistics and the PC clock estimate."""
        if t == T_PONG2:
            self._on_pong(data)
        elif t == T_SCFG:
            self._on_scfg(sq, data)
        else:
            self._on_control(t, idx, sq, data)

    def on_readable(self) -> None:
        """Socket read callback: drain every waiting datagram and dispatch it by packet type.

        Packets that fail authentication/parsing (``codec.parse`` returns None) are ignored.
        """
        s = self.sock
        if s is None:
            return
        while True:
            try:
                data = s.recv(RECV_SIZE)
            except OSError:               # BlockingIOError / InterruptedError are OSErrors: the queue is drained (or the socket died)
                return
            p = self.codec.parse(data)
            if p is not None:
                self._dispatch(p[0], p[1], p[2], data)

    def new_receiver(self, now: float | None = None) -> bool:
        """Return True if the receiver sent scfg recently (it runs and wants the Bluetooth state)."""
        return bool(self.scfg_t) and (time.monotonic() if now is None else now) - self.scfg_t < NEW_RECEIVER_S

    def _receiver_watch(self, alive: bool, was: bool) -> None:
        """On every ping: flush events to the receiver while it answers; losing the answer is an event too (sent after it returns)."""
        if alive:
            events.flush(self.emit)
        elif was:
            events.warn(E_NO_RECEIVER)

    def _send_pings(self) -> None:
        """Send the ping carrying the sender's clock (the receiver answers with pong2)."""
        self.emit(T_PING, BROADCAST_ALL, self.clock.ns())
        self.pings_sent += 1

    def _print_stats(self, last_print: float, tx_prev: int) -> tuple[float, int]:
        """Print the --stats summary (packet rate, per-device report rates, RTT) and return the new (time, tx_count) marks."""
        now = time.monotonic()
        print(f"TX {(self.tx_count - tx_prev) / (now - last_print):.0f} pkt/s")
        for c in list(state.active.values()):
            for name, meter in c.meters.items():
                report = meter.report()
                if report:
                    print(f"Pad {c.idx} {name}: {report[0]:.0f} Hz, gap p99 {report[1]:.1f} ms, max {report[2]:.1f} ms")
        if self.rtts:
            avg = sum(self.rtts) / len(self.rtts)
            print(f"RTT avg {avg:.1f} ms, max {max(self.rtts):.1f} ms, jitter {max(self.rtts) - min(self.rtts):.1f} ms "
                  f"({len(self.rtts)} pongs, {self.pings_sent - self.pongs} lost total)")
            self.rtts.clear()
        else:
            print("RTT: no pongs in last 5 s")
        return now, self.tx_count

    async def pinger(self) -> None:
        """Main housekeeping loop, four iterations per second.

        Each iteration sends a ping (see _send_pings; the pong gives the RTT). It also triggers the periodic build
        packet, the Wake-on-LAN MAC learning, the receiver-alive tracking (events), a one-time warning when no pong ever
        arrived, and, with --stats, a traffic/RTT summary every 5 seconds.
        """
        warned = False
        alive = False
        t_learn = float("-inf")
        last_print = time.monotonic()
        tx_prev = 0
        while True:
            self._send_pings()
            if self.pings_sent % round(BUILD_S / PING_S) == 1:
                import sender_update                # sender_update imports sender_net
                sender_update.send_build()
            await asyncio.sleep(PING_S)
            now = time.monotonic()
            was, alive = alive, now - self.last_pong < ALIVE_S
            self._receiver_watch(alive, was)
            if alive and now - t_learn >= LEARN_S:
                t_learn = now
                import sender_wol                   # sender_wol imports sender_net
                sender_wol.learn()
            if self.pings_sent >= NO_REPLY_WARN_S / PING_S and self.pongs == 0 and not warned:
                print("WARNING: no reply from receiver - check IP/port, firewall, --key and that both sides run the same version")
                warned = True
            if C.STATS and time.monotonic() - last_print >= STATS_EVERY_S:
                last_print, tx_prev = self._print_stats(last_print, tx_prev)

    async def _reresolve(self, started: float) -> bool:
        """Look the receiver's host name up again once it has been silent for RESOLVE_AFTER_S; True when its address changed.

        The address is resolved at startup only, so after a new DHCP lease of the PC a name would keep pointing at the old
        one until a restart. The lookup runs in a worker thread (DNS can take seconds and must not stop pad reading).
        A literal IP and a failed lookup change nothing; while the receiver answers nothing is done at all.
        """
        if time.monotonic() - max(self.last_pong, started) < RESOLVE_AFTER_S or sender_config.is_ip(C.HOST):
            return False
        try:
            addr = await asyncio.to_thread(sender_config.resolve, C.HOST)
        except OSError:
            return False
        if addr == C.PC_ADDR:
            return False
        print(f"Receiver {C.HOST} moved: {C.PC_ADDR} -> {addr}")
        C.PC_ADDR = addr
        return True

    async def sock_keeper(self) -> None:
        """Once a second make sure a usable socket exists (create it, or recreate it after a send error or a new receiver address).

        Logs only on transitions (network up -> down and back) so a long outage does not flood the log, and raises an
        event to the receiver window when the network goes down. A receiver that stays silent for RESOLVE_AFTER_S gets its
        host name resolved again every RESOLVE_AFTER_S (see _reresolve).
        """
        loop = asyncio.get_running_loop()
        started = t_check = time.monotonic()
        up = True
        while True:
            if time.monotonic() - t_check >= RESOLVE_AFTER_S:
                t_check = time.monotonic()
                if await self._reresolve(started):
                    self.sock_bad = True
            if self.sock is None or self.sock_bad:
                ok = self.open_sock(loop)
                if ok != up:
                    print("Network OK" if ok else "Network unavailable - retrying every 1 s")
                    if not ok:
                        events.warn(E_NET_DOWN)
                    up = ok
            await asyncio.sleep(SOCK_RETRY_S)


link = Net()
handlers = link.handlers
# the module-level API (``net.emit(...)`` etc. everywhere in the sender) - bound to the one link
init, open_sock, close_sock = link.init, link.open_sock, link.close_sock
send_raw, emit, emit_repeat, on_readable = link.send_raw, link.emit, link.emit_repeat, link.on_readable
new_receiver, rtt_x10 = link.new_receiver, link.rtt_x10
pinger, sock_keeper = link.pinger, link.sock_keeper
