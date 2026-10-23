"""Sender: the UDP socket, sending packets, receiving feedback and pongs, pinging, keeping the socket alive."""
import asyncio
import collections
import socket
import time

from proto import (BODY, E_NET_DOWN, E_NO_RECEIVER, RTT_NONE, Codec, EpochClock, SeqFilter, T_BTCMD, T_CMD, T_FB, T_PING,
                   T_PONG, T_PONG2, T_SCFG, T_TRIG, T_TRIG2, T_TRIG3)
import sender_events as events
import sender_state as state
from sender_config import C

codec = None
clock = EpochClock()                  # the sender's clock in pings and v3 stamps (random offset per process)
sock = None
sock_bad = False
tx_count = 0
rtts = collections.deque(maxlen=64)   # bounded so it does not grow forever while --stats is off
pings_sent = 0
pongs = 0
fb_filter = SeqFilter(1.0)            # with a time window: a receiver restart (random seq) does not block feedback
on_feedback = None                    # callback(idx, (small, large, r, g, b)) set in main
on_command = None                     # callback(idx, cmd, arg) set in main
on_triggers = None                    # callback(idx, trig2 values (12) or trig (4)) set in main
on_scfg = None                        # callback(flags, mac, broadcast, button, hold ms) set in main
on_bt = None                          # callback(BT_* operation, argument, MAC 6 B) set in main (sender_bt)
scfg_t = 0.0                          # when the scfg packet arrived - the receiver knows build2 and events
NEW_RECEIVER_S = 30.0
LEARN_S = 5.0                         # every this often (while the PC is up) the PC's MAC is learned from ARP for Wake-on-LAN
BUILD_S = 5.0                         # every this often the sender's code version (build packet) goes to the receiver window
PING_S = 0.25                         # 4 pings/s: the 2 s median is stable, and a single spike does not change it
recent_rtt = collections.deque(maxlen=8)   # latest measurements - for the info packet (receiver GUI window)
last_pong = 0.0
REPEAT_DELAYS = (0.01, 0.03)          # emit_repeat repeats [s]
PROBE_EVERY = 4                       # with a key, before switching to v3: an extra v3 ping every this many pings (1/s)


class PcClock:
    """The PC clock as seen from the phone: an NTP-style offset computed from pong2 packets (PC time at the moment of reply).

    How it works: every pong2 gives (t0 = ping sent, pc_us = PC time when it answered, t3 = pong received). The
    offset estimate is pc_us minus the midpoint of t0 and t3. We keep the measurement with the smallest RTT out of the
    last SYNC_N (~3 s): its error is at most half that RTT (Ethernet: ~0.1 ms), and the short window limits crystal
    drift (50 ppm = 0.15 ms per 3 s). Without a pong2 for SYNC_MAX_AGE_S (an older receiver, a network gap) there is no
    clock - the state then travels in a normal packet, without a timestamp."""

    SYNC_N, SYNC_MAX_AGE_S = 12, 3.0
    JUMP_US = 1_000_000               # an offset that changed by this much = another receiver process (another PC clock)

    def __init__(self) -> None:
        self.samples = collections.deque(maxlen=self.SYNC_N)   # (rtt ns, offset µs)
        self.offset_us = None
        self.t_sync = 0.0

    def pong(self, t0_ns, pc_us, t3_ns) -> None:
        """Feed one pong2 measurement into the estimator; implausible RTTs are discarded."""
        rtt = t3_ns - t0_ns
        if rtt < 0 or rtt > 1_000_000_000:
            return
        now = time.monotonic()
        if now - self.t_sync > self.SYNC_MAX_AGE_S:
            # after a gap the old measurements no longer fit: a PC restart (another clock) or crystal drift (50 ppm = 180 ms per hour);
            # the smallest-RTT measurement from before the gap would beat the new ones for SYNC_N pongs
            self.samples.clear()
        off = pc_us - (t0_ns + t3_ns) // 2000
        if self.offset_us is not None and abs(off - self.offset_us) > self.JUMP_US:
            # the receiver was restarted within SYNC_MAX_AGE_S: its clock has another random offset - the old
            # measurements (often with a smaller RTT) would win for SYNC_N pongs, and v3 stamps would be rejected
            self.samples.clear()
        self.samples.append((rtt, off))
        self.offset_us = min(self.samples)[1]
        self.t_sync = now

    def now_us(self):
        """PC time now [µs, low 32 bits] or None (not synchronised)."""
        if self.offset_us is None or time.monotonic() - self.t_sync > self.SYNC_MAX_AGE_S:
            return None
        return (clock.ns() // 1000 + self.offset_us) & 0xFFFFFFFF or 1    # 0 is reserved

    def now_ms(self):
        """PC time now [ms, low 32 bits] - the v3 freshness stamp for the receiver - or None (not synchronised)."""
        if self.offset_us is None or time.monotonic() - self.t_sync > self.SYNC_MAX_AGE_S:
            return None
        return ((clock.ns() // 1000 + self.offset_us) // 1000) & 0xFFFFFFFF


pc_clock = PcClock()


def rtt_x10():
    """Median of the latest ping measurements in tenths of a ms (for T_INFO); RTT_NONE if there was no reply for 3 s."""
    if not recent_rtt or time.monotonic() - last_pong > 3.0:
        return RTT_NONE
    return min(RTT_NONE - 1, round(sorted(recent_rtt)[len(recent_rtt) // 2] * 10))


def init() -> None:
    """Create the packet codec (HMAC key, v3 freshness token from the PC clock, replay-state file) and warn if there is no key."""
    global codec
    codec = Codec(C.KEY, token=lambda: pc_clock.now_ms(), now_ms=clock.ms32,
                  state_path=getattr(C, "AUTH_FILE", "") or None)
    if not C.KEY:
        print("WARNING: no HMAC key - anyone on the local network can send commands to this sender "
              "(update, Bluetooth pairing, pad power-off). Set the same key on both sides (docs/SECURITY.md)")


def open_sock(loop) -> bool:
    """(Re)create the UDP socket connected to the receiver and register it with the event loop; True on success.

    The previous socket, if any, is unregistered and closed first. Two priority hints are set on a best-effort basis:
    DSCP EF in the IP header (TOS 0xB8) and the Linux socket priority 6, which maps to the Wi-Fi "voice" queue.
    connect() on a UDP socket only fixes the destination, so later send() calls skip per-packet address handling.
    The socket is non-blocking and readable events are handled by on_readable.
    """
    global sock, sock_bad
    if sock is not None:
        loop.remove_reader(sock)
        sock.close()
        sock = None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0xB8)     # DSCP EF
    except OSError:
        pass
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_PRIORITY, 6)   # Linux: high-priority queue (Wi-Fi: the "voice" category)
    except (OSError, AttributeError):
        pass
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
        try:
            asyncio.get_running_loop().remove_reader(sock)
        except RuntimeError:
            pass
        sock.close()
        sock = None


def send_raw(data) -> None:
    """Send an already packed datagram. A full buffer or a refused connection just drops the packet (UDP);
    any other OS error marks the socket as bad so that sock_keeper recreates it."""
    global sock_bad, tx_count
    s = sock
    if s is None:
        return
    try:
        s.send(data)
        tx_count += 1
    except (BlockingIOError, ConnectionRefusedError):
        pass
    except OSError:
        sock_bad = True


def emit(ptype, idx, *vals) -> None:
    """Pack a packet of type ptype for slot idx with the given field values and send it."""
    data = codec.pack(ptype, idx, *vals)
    if data is not None:              # None: v3 without the PC clock (before the first pong2) - the receiver would reject it anyway
        send_raw(data)


def emit_repeat(ptype, idx, *vals, delays=REPEAT_DELAYS) -> None:
    """Send a packet that must not get lost (bye): the same one (same number - the receiver handles it once) now and
    repeated after each of `delays` seconds. Three copies at once were lost together in a single burst of losses
    (interference, a clogged queue), so the repeats are spread in time with loop.call_later."""
    data = codec.pack(ptype, idx, *vals)
    if data is None:
        return
    send_raw(data)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        for _ in delays:
            send_raw(data)
        return
    for d in delays:
        loop.call_later(d, send_raw, data)


def on_readable() -> None:
    """Socket read callback: drain every waiting datagram and dispatch it by packet type.

    Packets that fail authentication/parsing (codec.parse returns None) are ignored. Commands, feedback, trigger
    settings, Bluetooth requests and scfg pass through a SeqFilter first, so UDP duplicates (the receiver repeats
    important packets) and old reordered packets are dropped; each stream gets its own key so they do not block each
    other. Pong packets update the RTT statistics and the PC clock estimate.
    """
    global pongs, last_pong, scfg_t
    s = sock
    if s is None:
        return
    while True:
        try:
            data = s.recv(256)
        except (BlockingIOError, InterruptedError, OSError):
            return
        p = codec.parse(data)
        if p is None:
            continue
        t, idx, sq = p
        if t == T_PONG2:              # the PC clock for latency measurement (RTT is computed by the plain pong right after it)
            t0, pc_us = BODY[T_PONG2].unpack_from(data, 4)
            pc_clock.pong(t0, pc_us, clock.ns())
        elif t == T_PONG:
            rtt = (clock.ns() - BODY[T_PONG].unpack_from(data, 4)[0]) / 1e6
            rtts.append(rtt)
            recent_rtt.append(rtt)
            last_pong = time.monotonic()
            pongs += 1
        elif t == T_FB:
            if not fb_filter.fresh(idx, sq, time.monotonic()):
                continue
            if on_feedback is not None:
                on_feedback(idx, BODY[T_FB].unpack_from(data, 4))
        elif t == T_CMD:
            if fb_filter.fresh(("cmd", idx), sq, time.monotonic()) and on_command is not None:
                on_command(idx, *BODY[T_CMD].unpack_from(data, 4))
        elif t in (T_TRIG, T_TRIG2, T_TRIG3):   # trig3: + game effects (v11+), trig2: profile (v8+), trig: resistance only
            if fb_filter.fresh(("trig", idx), sq, time.monotonic()) and on_triggers is not None:
                on_triggers(idx, BODY[t].unpack_from(data, 4))
        elif t == T_BTCMD:              # phone Bluetooth from the receiver window (v10+)
            if fb_filter.fresh(("bt",), sq, time.monotonic()) and on_bt is not None:
                on_bt(*BODY[T_BTCMD].unpack_from(data, 4))
        elif t == T_SCFG:
            now = time.monotonic()
            if fb_filter.fresh(("scfg",), sq, now):
                scfg_t = now
                if on_scfg is not None:
                    on_scfg(*BODY[T_SCFG].unpack_from(data, 4))


def new_receiver(now=None):
    """The receiver sent scfg recently - it knows the build2 and event packets."""
    return scfg_t and (time.monotonic() if now is None else now) - scfg_t < NEW_RECEIVER_S


def _receiver_watch(alive, was) -> None:
    """On every ping: flush events to the receiver while it answers; losing the answer is itself an event (delivered after it returns)."""
    if alive:
        events.flush(emit)
    elif was:
        events.warn(E_NO_RECEIVER)


async def pinger() -> None:
    """Main housekeeping loop, four iterations per second.

    Each iteration sends a ping carrying the sender's clock (the pong gives the RTT), plus - while an HMAC key is set
    but v3 is not active yet - an occasional v3 ping that a v3-capable receiver answers, which switches both sides to
    v3. It also triggers the periodic build packet, the Wake-on-LAN MAC learning, the receiver-alive tracking (events),
    a one-time warning when no pong ever arrived, and, with --stats, a traffic/RTT summary every 5 seconds.
    """
    global pings_sent
    warned = False
    alive = False
    t_learn = 0.0
    last_print = time.monotonic()
    tx_prev = 0
    while True:
        t0 = clock.ns()
        emit(T_PING, 255, t0)
        if codec.key and not codec.v3 and pings_sent % PROBE_EVERY == 0:
            send_raw(codec.pack(T_PING, 255, t0, v3=True))   # a v3 receiver answers with v3 -> switch (an older one rejects it)
        pings_sent += 1
        if pings_sent % round(BUILD_S / PING_S) == 1:
            import sender_update                # sender_update imports sender_net
            sender_update.send_build()
        await asyncio.sleep(PING_S)
        now = time.monotonic()
        was, alive = alive, now - last_pong < 3.0
        _receiver_watch(alive, was)
        if alive and now - t_learn >= LEARN_S:
            t_learn = now
            import sender_wol                   # sender_wol imports sender_net
            sender_wol.learn()
        if pings_sent >= 4 / PING_S and pongs == 0 and not warned:
            print("WARNING: no reply from receiver - check IP/port, firewall and --key (receiver downgraded to a "
                  "version without protocol v3? delete auth.json next to the sender)")
            warned = True
        if C.STATS and time.monotonic() - last_print >= 5.0:
            now = time.monotonic()
            print(f"TX {(tx_count - tx_prev) / (now - last_print):.0f} pkt/s")
            tx_prev, last_print = tx_count, now
            for c in list(state.active.values()):
                for name, mt in c.meters.items():
                    r = mt.report()
                    if r:
                        print(f"Pad {c.idx} {name}: {r[0]:.0f} Hz, przerwa p99 {r[1]:.1f} ms, max {r[2]:.1f} ms")
            if rtts:
                avg = sum(rtts) / len(rtts)
                print(f"RTT avg {avg:.1f} ms, max {max(rtts):.1f} ms, jitter {max(rtts) - min(rtts):.1f} ms "
                      f"({len(rtts)} pongs, {pings_sent - pongs} lost total)")
                rtts.clear()
            else:
                print("RTT: no pongs in last 5 s")


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
        await asyncio.sleep(1.0)
