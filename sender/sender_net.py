"""Sender: gniazdo UDP, wysyłanie pakietów, odbiór feedbacku i pongów, ping, utrzymanie gniazda."""
import asyncio
import collections
import socket
import time

from proto import (BODY, E_NET_DOWN, E_NO_RECEIVER, RTT_NONE, Codec, SeqFilter, T_CMD, T_FB, T_PING, T_PONG, T_PONG2,
                   T_SCFG, T_TRIG, T_TRIG2)
import sender_events as events
import sender_state as state
from sender_config import C

codec = None
sock = None
sock_bad = False
tx_count = 0
rtts = collections.deque(maxlen=64)   # ograniczone, żeby nie rosło bez końca przy wyłączonym --stats
pings_sent = 0
pongs = 0
fb_filter = SeqFilter(1.0)            # z oknem czasowym: restart receivera (losowe seq) nie blokuje feedbacku
on_feedback = None                    # callback(idx, (small, large, r, g, b)) ustawiany w main
on_command = None                     # callback(idx, cmd, arg) ustawiany w main
on_triggers = None                    # callback(idx, wartości trig2 (12) albo trig (4)) ustawiany w main
on_scfg = None                        # callback(flagi, mac, broadcast, przycisk, przytrzymanie ms) ustawiany w main
scfg_t = 0.0                          # kiedy przyszedł pakiet scfg - receiver zna build2 i zdarzenia
NEW_RECEIVER_S = 30.0
LEARN_S = 5.0                         # co tyle (przy działającym PC) MAC komputera z ARP do Wake-on-LAN
BUILD_S = 5.0                         # co tyle wersja kodu nadajnika (pakiet build) do okna receivera
PING_S = 0.25                         # 4 pingi/s: mediana z 2 s jest stabilna, a pojedynczy skok jej nie zmienia
recent_rtt = collections.deque(maxlen=8)   # ostatnie pomiary - do pakietu info (okno w GUI receivera)
last_pong = 0.0


class PcClock:
    """Zegar PC widziany z telefonu: przesunięcie jak w NTP z pongów pong2 (czas PC w chwili odpowiedzi).
    Bierzemy pomiar o najmniejszym RTT z ostatnich SYNC_N (~3 s): jego błąd to najwyżej połowa tego RTT (Ethernet:
    ~0.1 ms), a krótkie okno ogranicza dryf kwarców (50 ppm = 0.15 ms na 3 s). Bez pong2 przez SYNC_MAX_AGE_S
    (starszy receiver, przerwa w sieci) = brak zegara - stan idzie zwykłym pakietem, bez pomiaru."""

    SYNC_N, SYNC_MAX_AGE_S = 12, 3.0

    def __init__(self):
        self.samples = collections.deque(maxlen=self.SYNC_N)   # (rtt ns, przesunięcie µs)
        self.offset_us = None
        self.t_sync = 0.0

    def pong(self, t0_ns, pc_us, t3_ns):
        rtt = t3_ns - t0_ns
        if rtt < 0 or rtt > 1_000_000_000:
            return
        now = time.monotonic()
        if now - self.t_sync > self.SYNC_MAX_AGE_S:
            # po przerwie stare pomiary nie pasują: restart PC (inny zegar) albo dryf kwarców (50 ppm = 180 ms na godzinę);
            # pomiar o najmniejszym RTT sprzed przerwy wygrywałby z nowymi przez SYNC_N pongów
            self.samples.clear()
        self.samples.append((rtt, pc_us - (t0_ns + t3_ns) // 2000))
        self.offset_us = min(self.samples)[1]
        self.t_sync = now

    def now_us(self):
        """Czas PC teraz [µs, młodsze 32 bity] albo None (brak synchronizacji)."""
        if self.offset_us is None or time.monotonic() - self.t_sync > self.SYNC_MAX_AGE_S:
            return None
        return (time.perf_counter_ns() // 1000 + self.offset_us) & 0xFFFFFFFF or 1    # 0 zarezerwowane


pc_clock = PcClock()


def rtt_x10():
    """Mediana ostatnich pomiarów ping w dziesiątych ms (do T_INFO); RTT_NONE, gdy brak odpowiedzi od 3 s."""
    if not recent_rtt or time.monotonic() - last_pong > 3.0:
        return RTT_NONE
    return min(RTT_NONE - 1, round(sorted(recent_rtt)[len(recent_rtt) // 2] * 10))


def init():
    global codec
    codec = Codec(C.KEY)


def open_sock(loop):
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
        s.setsockopt(socket.SOL_SOCKET, socket.SO_PRIORITY, 6)   # Linux: kolejka wysokiego priorytetu (Wi-Fi: kategoria "voice")
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


def close_sock():
    global sock
    if sock is not None:
        try:
            asyncio.get_running_loop().remove_reader(sock)
        except RuntimeError:
            pass
        sock.close()
        sock = None


def emit(ptype, idx, *vals):
    global sock_bad, tx_count
    s = sock
    if s is None:
        return
    try:
        s.send(codec.pack(ptype, idx, *vals))
        tx_count += 1
    except (BlockingIOError, ConnectionRefusedError):
        pass
    except OSError:
        sock_bad = True


def on_readable():
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
        if t == T_PONG2:              # zegar PC do pomiaru opóźnienia (RTT liczy zwykły pong zaraz po nim)
            t0, pc_us = BODY[T_PONG2].unpack_from(data, 4)
            pc_clock.pong(t0, pc_us, time.perf_counter_ns())
        elif t == T_PONG:
            rtt = (time.perf_counter_ns() - BODY[T_PONG].unpack_from(data, 4)[0]) / 1e6
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
        elif t in (T_TRIG, T_TRIG2):    # trig2: efekty spustów (v8+), trig: sam opór (starszy receiver)
            if fb_filter.fresh(("trig", idx), sq, time.monotonic()) and on_triggers is not None:
                on_triggers(idx, BODY[t].unpack_from(data, 4))
        elif t == T_SCFG:
            now = time.monotonic()
            if fb_filter.fresh(("scfg",), sq, now):
                scfg_t = now
                if on_scfg is not None:
                    on_scfg(*BODY[T_SCFG].unpack_from(data, 4))


def new_receiver(now=None):
    """Receiver przysłał niedawno scfg - zna pakiety build2 i event."""
    return scfg_t and (time.monotonic() if now is None else now) - scfg_t < NEW_RECEIVER_S


def _receiver_watch(alive, was):
    """Co ping: zdarzenia do receivera, gdy odpowiada; utrata odpowiedzi = zdarzenie (dojdzie po powrocie)."""
    if alive:
        events.flush(emit)
    elif was:
        events.warn(E_NO_RECEIVER)


async def pinger():
    global pings_sent
    warned = False
    alive = False
    t_learn = 0.0
    last_print = time.monotonic()
    tx_prev = 0
    while True:
        emit(T_PING, 255, time.perf_counter_ns())
        pings_sent += 1
        if pings_sent % round(BUILD_S / PING_S) == 1:
            import sender_update                # sender_update importuje sender_net
            sender_update.send_build()
        await asyncio.sleep(PING_S)
        now = time.monotonic()
        was, alive = alive, now - last_pong < 3.0
        _receiver_watch(alive, was)
        if alive and now - t_learn >= LEARN_S:
            t_learn = now
            import sender_wol                   # sender_wol importuje sender_net
            sender_wol.learn()
        if pings_sent >= 4 / PING_S and pongs == 0 and not warned:
            print("WARNING: no reply from receiver - check IP/port, firewall and --key")
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


async def sock_keeper():
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
