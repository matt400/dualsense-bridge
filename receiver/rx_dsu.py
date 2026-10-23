"""Receiver: DSU (Cemuhook) server - gyro / accelerometer / touch data for emulators."""
import random
import struct
import threading
import time
import zlib

import rx_state as S
from proto import clamp
from rx_buttons import PS_BIT, bits_from
from rx_config import C
from rx_winapi import bind_udp, boost_thread

DSU_B1 = {314: 0x01, 317: 0x02, 318: 0x04, 315: 0x08}                      # share L3 R3 options
# the bits as the protocol lists them, from the most significant: Y (square) 0x80, B (cross) 0x40, A (circle) 0x20, X (triangle) 0x10, R1,
# L1, R2, L2
DSU_B2 = {308: 0x80, 304: 0x40, 305: 0x20, 307: 0x10, 311: 0x08, 310: 0x04}
DSU_DPAD = {(hx, hy): ((0x10 if hy == -1 else 0) | (0x20 if hx == 1 else 0) |
                       (0x40 if hy == 1 else 0) | (0x80 if hx == -1 else 0))
            for hx in (-1, 0, 1) for hy in (-1, 0, 1)}
DSU_HDR = struct.Struct("<4sHHII")
DSU_INFO = struct.Struct("<BBBB6sB")
DSU_DATA = struct.Struct("<BBBB6sBBIBBBBBBBB12s12sQ6f")   # the whole 0x100002 data packet in a single pack
DSU_TOUCH = struct.Struct("<BBHHBBHH")
ZERO12 = bytes(12)
SERVER_ID = random.getrandbits(32)

dsu = None
_threads = []
dsu_clients = {}
dsu_lock = threading.Lock()
_resend = [False]           # a new subscriber: tx_thread sends the state of all connected pads right away
_cache = {}
_acache = {}
ANALOG_BITS = ((0, 0x80), (0, 0x40), (0, 0x20), (0, 0x10),               # d-pad: left, down, right, up
               (1, 0x80), (1, 0x40), (1, 0x20), (1, 0x10), (1, 0x08), (1, 0x04))  # Y B A X R1 L1


def analog_bytes(b1, b2):
    """The 10 analog button bytes (0 / 255) - some emulators read the buttons from here."""
    k = (b1 & 0xF0, b2 & 0xFC)
    r = _acache.get(k)
    if r is None:
        r = _acache[k] = bytes(255 if (b1, b2)[n] & m else 0 for n, m in ANALOG_BITS)
    return r


def dsu_bits(mask):
    """Button mask -> (byte 1, byte 2) of the DSU buttons, memoised per mask."""
    r = _cache.get(mask)
    if r is None:
        r = _cache[mask] = bits_from(mask, DSU_B1, DSU_B2)
    return r


def dsu_pkt(event, payload):
    """Wrap a payload into a DSU server packet: the "DSUS" header (protocol 1001, length, server id) + the event number + the payload; the
    CRC32 is computed over the whole packet with the CRC field zeroed."""
    body = struct.pack("<I", event) + payload
    pkt = bytearray(DSU_HDR.pack(b"DSUS", 1001, len(body), 0, SERVER_ID) + body)
    struct.pack_into("<I", pkt, 8, zlib.crc32(pkt) & 0xFFFFFFFF)
    return bytes(pkt)


def shared(slot):
    """The shared part of a slot description (slot, connection state, model, connection type, MAC, battery); slots beyond NUM_PADS look
    like "not connected"."""
    if slot < C.NUM_PADS:
        return DSU_INFO.pack(slot, 2 if S.alive[slot] else 0, 2, 2, S.MACS[slot], S.states[slot]["batt_dsu"])
    return DSU_INFO.pack(slot, 0, 0, 0, bytes(6), 0)


def dsu_data(slot, s, counter):
    """Build the DSU data packet (event 0x100002) for a slot: buttons, sticks, analog buttons, two touch points, timestamp, accelerometer
    in g and gyro in deg/s (with the configured signs)."""
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
        slot, 2 if S.alive[slot] else 0, 2, 2, S.MACS[slot], s["batt_dsu"], 1, counter,
        b1, b2, 1 if mask & PS_BIT else 0, 1 if (s["tp"] and not mm) else 0,
        s["lx"], 255 - s["ly"], s["rx"], 255 - s["ry"], analog, touch,
        s["ts"] or time.monotonic_ns() // 1000,
        ax / C.ACC_RES * k0, ay / C.ACC_RES * k1, az / C.ACC_RES * k2,
        gx / C.GYRO_RES * g0, gy / C.GYRO_RES * g1, gz / C.GYRO_RES * g2)
    return dsu_pkt(0x100002, payload)


def rx_thread():
    """Receive thread: answer the protocol-version and slot-info requests and register data subscriptions.

    A subscription is just the client address with the time of its last request (clients must renew it); the number of
    clients is capped so that forged source addresses cannot turn the server into a stream source.
    """
    while not S.stop.is_set():
        try:
            data, addr = dsu.recvfrom(2048)
        except TimeoutError:
            continue                                   # the timeout only exists to check S.stop
        except OSError as e:
            if getattr(e, "winerror", None) not in (10040, 10054):   # datagram too large / ICMP - just carry on
                time.sleep(0.01)
            continue
        if len(data) < 20 or data[:4] != b"DSUC":
            continue
        event = struct.unpack_from("<I", data, 16)[0]
        try:
            if event == 0x100000:      # protocol version
                dsu.sendto(dsu_pkt(0x100000, struct.pack("<H", 1001)), addr)
            elif event == 0x100001:    # slot info
                count = struct.unpack_from("<i", data, 20)[0] if len(data) >= 24 else 0
                for slot in data[24:24 + clamp(count, 0, 4)]:
                    dsu.sendto(dsu_pkt(0x100001, shared(slot) + b"\x00"), addr)
            elif event == 0x100002:    # data subscription
                with dsu_lock:
                    new = addr not in dsu_clients
                    if new and len(dsu_clients) >= getattr(C, "DSU_MAX_CLIENTS", 4):
                        continue            # limit: forged addresses cannot turn the server into a stream source
                    dsu_clients[addr] = time.monotonic()
                    S.dsu_active = True
                    if new:
                        _resend[0] = True
                if new:
                    S.dsu_wake.set()       # a new client gets the state at once
        except OSError:
            pass


def tx_thread():
    """Send data to subscribers after a state change (at most dsu_hz), and without changes every dsu_tx_interval."""
    n = C.NUM_PADS
    counters = [0] * n
    last_ver = [-1] * n
    last_sent = [0.0] * n
    sent_alive = [False] * n   # whether the last sent packet described a connected pad
    interval = 1.0 / C.DSU_HZ if C.DSU_HZ else 0.0
    last = 0.0
    boost_thread()
    while not S.stop.is_set():
        S.dsu_wake.wait(0.1 if S.dsu_active else 0.5)
        d = last + interval - time.perf_counter()
        if d > 0:
            time.sleep(d)
        S.dsu_wake.clear()
        last = time.perf_counter()
        with dsu_lock:
            if dsu_clients:
                t_now = time.monotonic()
                for a in [a for a, t in dsu_clients.items() if t_now - t > 5]:
                    del dsu_clients[a]
            clients = list(dsu_clients)
            S.dsu_active = bool(clients)
            resend, _resend[0] = _resend[0], False
        if resend:
            # a new client (emulator) did not know the state: without this a motionless pad (the same "ver") would get it only after
            # dsu_tx_interval, and until then the client saw empty data - a game starting with a tilted view / empty gyro
            last_ver = [-1] * n
        if not clients:
            continue
        mono = time.monotonic()
        with S.lock:
            pkts = []
            for slot in range(n):
                al = S.alive[slot]
                if not al and not sent_alive[slot]:
                    continue    # disconnected pad: the client knows its state from the 0x100001 reply
                s = S.states[slot]
                if s["ver"] != last_ver[slot] or mono - last_sent[slot] > C.DSU_TX_INTERVAL:
                    last_ver[slot] = s["ver"]
                    last_sent[slot] = mono
                    sent_alive[slot] = al
                    counters[slot] += 1
                    pkts.append(dsu_data(slot, S.output(slot), counters[slot]))   # Test view: neutral
        if C.DSU_BATCH and pkts:
            batch = b"".join(pkts)
            for a in clients:
                try:
                    dsu.sendto(batch, a)
                except OSError:
                    pass
        else:
            for a in clients:
                for pkt in pkts:
                    try:
                        dsu.sendto(pkt, a)
                    except OSError:
                        pass


def close():
    """After S.stop: wait for the server threads and close the socket - the port is free for a new process after a restart."""
    for t in _threads:
        t.join(1.0)
    if dsu is not None:
        dsu.close()


def start():
    """Bind the DSU UDP socket and start the receive and send threads."""
    global dsu
    dsu = bind_udp(C.DSU_PORT, "DSU server", host=getattr(C, "DSU_BIND", "127.0.0.1"))
    if dsu.getsockname()[0] != "127.0.0.1":
        print(f"DSU server on {C.DSU_BIND}:{C.DSU_PORT} - reachable from the network (dsu_bind)")
    # the receive thread checks S.stop; closing the socket from another thread does not interrupt recvfrom on Linux
    dsu.settimeout(0.25)
    print(f"DSU server on UDP {C.DSU_PORT} (slots 0..{C.NUM_PADS - 1}).")
    _threads[:] = [S.start_thread("dsu rx", rx_thread), S.start_thread("dsu tx", tx_thread)]
