"""Receiver: DSU (Cemuhook) server - gyro / accelerometer / touch data for emulators."""
from __future__ import annotations

import contextlib
import random
import socket
import struct
import threading
import time
import zlib
from collections.abc import Mapping
from typing import Any, Final

import rx_state as S
from proto import clamp
from rx_buttons import PS_BIT, bits_from
from rx_config import C
from rx_winapi import bind_udp, boost_thread

# ---- protocol (Cemuhook DSU) ----
PROTOCOL_VERSION: Final = 1001
EVENT_VERSION: Final = 0x100000
EVENT_INFO: Final = 0x100001
EVENT_DATA: Final = 0x100002
MAX_SLOTS_QUERY: Final = 4         # a slot-info request lists at most this many slots
CLIENT_HDR_LEN: Final = 20         # "DSUC" + version + length + crc + client id + event number
INFO_COUNT_END: Final = 24         # the slot-info request: 4 bytes of count after the header, then one byte per slot
RECV_SIZE: Final = 2048
CLIENT_TTL_S: Final = 5.0          # a subscription expires when the client does not renew it
WSA_MSGSIZE: Final = 10040         # datagram too large
WSA_CONNRESET: Final = 10054       # ICMP port unreachable
STATE_CONNECTED: Final = 2
STATE_DISCONNECTED: Final = 0
MODEL_FULL_GYRO: Final = 2
CONNECTION_BLUETOOTH: Final = 2
STICK_MAX: Final = 255
MAX_CLIENTS: Final = 4
IDLE_WAIT_ACTIVE_S: Final = 0.1    # how long the send thread sleeps without a wake-up: with subscribers ...
IDLE_WAIT_S: Final = 0.5           # ... and without

DSU_B1: Final = {314: 0x01, 317: 0x02, 318: 0x04, 315: 0x08}                      # share L3 R3 options
# the bits as the protocol lists them, from the most significant: Y (square) 0x80, B (cross) 0x40, A (circle) 0x20, X (triangle) 0x10, R1,
# L1, R2, L2
DSU_B2: Final = {308: 0x80, 304: 0x40, 305: 0x20, 307: 0x10, 311: 0x08, 310: 0x04}
DSU_DPAD: Final = {(hx, hy): ((0x10 if hy == -1 else 0) | (0x20 if hx == 1 else 0) |
                       (0x40 if hy == 1 else 0) | (0x80 if hx == -1 else 0))
            for hx in (-1, 0, 1) for hy in (-1, 0, 1)}
DSU_HDR_EVENT: Final = struct.Struct("<4sHHIII")    # "DSUS", version, length, crc, server id + the event number that opens every payload
DSU_INFO: Final = struct.Struct("<BBBB6sB")
DSU_DATA: Final = struct.Struct("<BBBB6sBBIBBBBBBBB12s12sQ6f")   # the whole 0x100002 data packet in a single pack
DSU_TOUCH: Final = struct.Struct("<BBHHBBHH")
ZERO12: Final = bytes(12)
SERVER_ID: Final = random.getrandbits(32)

dsu: socket.socket | None = None
_threads: list[threading.Thread] = []
dsu_clients: dict[tuple[str, int], float] = {}
dsu_lock = threading.Lock()
_resend = [False]           # a new subscriber: tx_thread sends the state of all connected pads right away
_cache: dict[int, tuple[int, ...]] = {}
_acache: dict[tuple[int, int], bytes] = {}
ANALOG_BITS: Final = ((0, 0x80), (0, 0x40), (0, 0x20), (0, 0x10),               # d-pad: left, down, right, up
               (1, 0x80), (1, 0x40), (1, 0x20), (1, 0x10), (1, 0x08), (1, 0x04))  # Y B A X R1 L1


def analog_bytes(b1: int, b2: int) -> bytes:
    """Return the 10 analog button bytes (0 / 255) - some emulators read the buttons from here."""
    k = (b1 & 0xF0, b2 & 0xFC)
    r = _acache.get(k)
    if r is None:
        r = _acache[k] = bytes(STICK_MAX if (b1, b2)[n] & m else 0 for n, m in ANALOG_BITS)
    return r


def dsu_bits(mask: int) -> tuple[int, ...]:
    """Map a button mask to (byte 1, byte 2) of the DSU buttons, memoised per mask."""
    r = _cache.get(mask)
    if r is None:
        r = _cache[mask] = bits_from(mask, DSU_B1, DSU_B2)
    return r


def dsu_pkt(event: int, payload: bytes) -> bytes:
    """Wrap a payload into a DSU server packet.

    The "DSUS" header (protocol version, length, server id) + the event number + the payload; the CRC32 is computed over
    the whole packet with the CRC field zeroed.
    """
    length = 4 + len(payload)                                    # the event number + the payload
    head = DSU_HDR_EVENT.pack(b"DSUS", PROTOCOL_VERSION, length, 0, SERVER_ID, event)
    crc = zlib.crc32(payload, zlib.crc32(head))                  # over the whole packet with the CRC field zeroed, no joined copy
    return DSU_HDR_EVENT.pack(b"DSUS", PROTOCOL_VERSION, length, crc, SERVER_ID, event) + payload


def shared(slot: int) -> bytes:
    """Return the shared part of a slot description (slot, connection state, model, connection type, MAC, battery).

    Slots beyond NUM_PADS look like "not connected".
    """
    if slot < C.NUM_PADS:
        state = STATE_CONNECTED if S.alive[slot] else STATE_DISCONNECTED
        return DSU_INFO.pack(slot, state, MODEL_FULL_GYRO, CONNECTION_BLUETOOTH, S.MACS[slot], S.states[slot]["batt_dsu"])
    return DSU_INFO.pack(slot, 0, 0, 0, bytes(6), 0)


def build_data_packet(slot: int, s: Mapping[str, Any], counter: int) -> bytes:
    """Build the DSU data packet (event 0x100002) for a slot.

    Buttons, sticks, analog buttons, two touch points, timestamp, accelerometer in g and gyro in deg/s (with the
    configured signs).
    """
    mask = s["mask"]
    mm = S.mouse_mode[slot]
    bb1, bb2 = dsu_bits(mask)
    b1 = bb1 | DSU_DPAD[(s["hx"], s["hy"])]
    b2 = bb2 | (0x02 if s["rt"] else 0) | (0x01 if s["lt"] else 0)
    # analog: d-pad(4) | Y B A X | R1 L1 | R2 L2 (the triggers with their real value)
    analog = analog_bytes(b1, b2) + bytes((s["rt"], s["lt"]))
    touch = ZERO12 if mm else DSU_TOUCH.pack(*s["touch"][0], *s["touch"][1])
    k0, k1, k2 = C.ACC_SIGN
    g0, g1, g2 = C.GYRO_SIGN
    ax, ay, az = s["acc"]
    gx, gy, gz = s["gyro"]
    payload = DSU_DATA.pack(
        slot, STATE_CONNECTED if S.alive[slot] else STATE_DISCONNECTED, MODEL_FULL_GYRO, CONNECTION_BLUETOOTH,
        S.MACS[slot], s["batt_dsu"], 1, counter,
        b1, b2, 1 if mask & PS_BIT else 0, 1 if (s["tp"] and not mm) else 0,
        s["lx"], STICK_MAX - s["ly"], s["rx"], STICK_MAX - s["ry"], analog, touch,
        s["ts"] or time.monotonic_ns() // 1000,
        ax / C.ACC_RES * k0, ay / C.ACC_RES * k1, az / C.ACC_RES * k2,
        gx / C.GYRO_RES * g0, gy / C.GYRO_RES * g1, gz / C.GYRO_RES * g2)
    return dsu_pkt(EVENT_DATA, payload)


def _recv() -> tuple[bytes, tuple[str, int]] | None:
    """Receive one datagram; None on the timeout (it only exists to check S.stop) or a harmless socket error."""
    try:
        return dsu.recvfrom(RECV_SIZE)
    except TimeoutError:
        return None
    except OSError as e:
        if getattr(e, "winerror", None) not in (WSA_MSGSIZE, WSA_CONNRESET):   # datagram too large / ICMP - just carry on
            time.sleep(0.01)
        return None


def _subscribe(addr: tuple[str, int]) -> None:
    """Register (or renew) a data subscription; the number of clients is capped so forged addresses cannot become a stream source."""
    with dsu_lock:
        new = addr not in dsu_clients
        if new and len(dsu_clients) >= getattr(C, "DSU_MAX_CLIENTS", MAX_CLIENTS):
            return
        dsu_clients[addr] = time.monotonic()
        S.dsu_active = True
        if new:
            _resend[0] = True
    if new:
        S.dsu_wake.set()       # a new client gets the state at once


def _handle_request(data: bytes, addr: tuple[str, int]) -> None:
    """Answer one client packet: protocol version, slot info or a data subscription."""
    event = struct.unpack_from("<I", data, CLIENT_HDR_LEN - 4)[0]
    if event == EVENT_VERSION:
        dsu.sendto(dsu_pkt(EVENT_VERSION, struct.pack("<H", PROTOCOL_VERSION)), addr)
    elif event == EVENT_INFO:
        count = struct.unpack_from("<i", data, CLIENT_HDR_LEN)[0] if len(data) >= INFO_COUNT_END else 0
        for slot in data[INFO_COUNT_END:INFO_COUNT_END + clamp(count, 0, MAX_SLOTS_QUERY)]:
            dsu.sendto(dsu_pkt(EVENT_INFO, shared(slot) + b"\x00"), addr)
    elif event == EVENT_DATA:
        _subscribe(addr)


def rx_thread() -> None:
    """Receive thread: answer the protocol-version and slot-info requests and register data subscriptions.

    A subscription is just the client address with the time of its last request (clients must renew it).
    """
    while not S.stop.is_set():
        got = _recv()
        if got is None:
            continue
        data, addr = got
        if len(data) < CLIENT_HDR_LEN or data[:4] != b"DSUC":
            continue
        with contextlib.suppress(OSError):         # a client that vanished mid-answer
            _handle_request(data, addr)


class _TxState:
    """Per-slot bookkeeping of what the send thread has already sent."""

    def __init__(self, n: int) -> None:
        """Start with nothing sent for `n` slots."""
        self.n = n
        self.counters = [0] * n
        self.last_ver = [-1] * n
        self.last_sent = [0.0] * n
        self.sent_alive = [False] * n   # whether the last sent packet described a connected pad

    def collect(self, mono: float) -> list[bytes]:
        """Build the data packets of the slots that changed (or are due for a refresh); call without holding S.lock."""
        pkts: list[bytes] = []
        with S.lock:
            for slot in range(self.n):
                al = S.alive[slot]
                if not al and not self.sent_alive[slot]:
                    continue    # disconnected pad: the client knows its state from the 0x100001 reply
                s = S.states[slot]
                if s["ver"] != self.last_ver[slot] or mono - self.last_sent[slot] > C.DSU_TX_INTERVAL:
                    self.last_ver[slot] = s["ver"]
                    self.last_sent[slot] = mono
                    self.sent_alive[slot] = al
                    self.counters[slot] += 1
                    pkts.append(build_data_packet(slot, S.output(slot), self.counters[slot]))   # Test view: neutral
        return pkts


def _live_clients() -> tuple[list[tuple[str, int]], bool]:
    """Drop expired subscriptions; return the remaining clients and whether a new one asked for the state."""
    with dsu_lock:
        if dsu_clients:
            t_now = time.monotonic()
            for a in [a for a, t in dsu_clients.items() if t_now - t > CLIENT_TTL_S]:
                del dsu_clients[a]
        clients = list(dsu_clients)
        S.dsu_active = bool(clients)
        resend, _resend[0] = _resend[0], False
    return clients, resend


def _send(pkts: list[bytes], clients: list[tuple[str, int]]) -> None:
    """Send the packets to every client, as one datagram when DSU_BATCH is on; a vanished client's subscription just expires."""
    if C.DSU_BATCH and pkts:
        batch = b"".join(pkts)
        for a in clients:
            with contextlib.suppress(OSError):
                dsu.sendto(batch, a)
    else:
        for a in clients:
            for pkt in pkts:
                with contextlib.suppress(OSError):
                    dsu.sendto(pkt, a)


def tx_thread() -> None:
    """Send data to subscribers after a state change (at most dsu_hz), and without changes every dsu_tx_interval."""
    tx = _TxState(C.NUM_PADS)
    interval = 1.0 / C.DSU_HZ if C.DSU_HZ else 0.0
    last = 0.0
    boost_thread()
    while not S.stop.is_set():
        S.dsu_wake.wait(IDLE_WAIT_ACTIVE_S if S.dsu_active else IDLE_WAIT_S)
        d = last + interval - time.perf_counter()
        if d > 0:
            time.sleep(d)
        S.dsu_wake.clear()
        last = time.perf_counter()
        clients, resend = _live_clients()
        if resend:
            # a new client (emulator) did not know the state: without this a motionless pad (the same "ver") would get it only after
            # dsu_tx_interval, and until then the client saw empty data - a game starting with a tilted view / empty gyro
            tx.last_ver = [-1] * tx.n
        if clients:
            _send(tx.collect(time.monotonic()), clients)


def close() -> None:
    """After S.stop: wait for the server threads and close the socket - the port is free for a new process after a restart."""
    for t in _threads:
        t.join(1.0)
    if dsu is not None:
        dsu.close()


def start() -> None:
    """Bind the DSU UDP socket and start the receive and send threads."""
    global dsu
    dsu = bind_udp(C.DSU_PORT, "DSU server", host=getattr(C, "DSU_BIND", "127.0.0.1"))
    if dsu.getsockname()[0] != "127.0.0.1":
        print(f"DSU server on {C.DSU_BIND}:{C.DSU_PORT} - reachable from the network (dsu_bind)")
    # the receive thread checks S.stop; closing the socket from another thread does not interrupt recvfrom on Linux
    dsu.settimeout(0.25)
    print(f"DSU server on UDP {C.DSU_PORT} (slots 0..{C.NUM_PADS - 1}).")
    _threads[:] = [S.start_thread("dsu rx", rx_thread), S.start_thread("dsu tx", tx_thread)]
