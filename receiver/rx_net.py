"""Receiver: odbiór pakietów z nadajnika, obsługa zdarzeń, wykrywanie ciszy."""
import socket
import time

import rx_console
import rx_feedback as F
import rx_hotkeys as hotkeys
import rx_master as master
import rx_mouse as mouse
import rx_state as S
from proto import (BODY, RTT_NONE, Codec, T_BYE, T_CLICK, T_CMD, T_FB, T_IDENT, T_INFO, T_MOUSE, T_PING, T_PONG, T_VERSION,
                   T_STATE, T_TOUCH, clamp)
from rx_buttons import MASK_ALL, PS_BIT, profile_size

from rx_config import C
from rx_winapi import bind_udp, boost_thread

RX_TYPES = {T_STATE, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_BYE, T_IDENT, T_INFO, T_VERSION}   # typy, które receiver przyjmuje
ST_STATE, ST_TOUCH, ST_MOUSE, ST_INFO = BODY[T_STATE], BODY[T_TOUCH], BODY[T_MOUSE], BODY[T_INFO]


class LinkStats:
    """Utrata pakietów (z luk w numerach sekwencyjnych - nadajnik numeruje wszystkie pakiety jednym licznikiem)
    i pakiety stanu na sekundę per slot. Wyniki co sekundę do S.link_loss / S.pps (okno w GUI)."""

    WINDOW = 1.0

    def __init__(self, n):
        self.last = None
        self.expected = self.got = 0
        self.states = [0] * n
        self.t0 = time.monotonic()

    def packet(self, sq):
        if self.last is not None:
            d = (sq - self.last) & 0xFFFF
            if d == 0 or d >= 0x8000:
                return                       # duplikat albo spóźniony - nie przesuwa licznika
            if d < 1000:                     # większy skok = restart nadajnika (nowy losowy numer), nie strata
                self.expected += d
                self.got += 1
        self.last = sq

    def tick(self, now):
        dt = now - self.t0
        if dt < self.WINDOW:
            return
        S.link_loss = max(0.0, 100.0 * (1 - self.got / self.expected)) if self.expected >= 10 else None
        S.pps[:] = [n / dt for n in self.states]
        self.expected = self.got = 0
        self.states = [0] * len(self.states)
        self.t0 = now

codec = None
in_sock = None
_warned_idx = set()
_digital = [None] * 4    # ostatni stan przycisków (maska, d-pad, spusty wciśnięte) per slot - zmiana = obsługa skrótów


def init():
    global codec, in_sock
    codec = Codec(C.KEY)
    in_sock = bind_udp(C.IN_PORT, "pad events", 1 << 20)
    in_sock.settimeout(0.1)   # pozwala wykrywać ciszę ze strony nadajnika


def close():
    """Zamyka gniazdo odbiorcze (po zatrzymaniu pętli sieci) - port wolny dla nowego procesu po restarcie."""
    if in_sock is not None:
        in_sock.close()


def send_peer(pkt):
    p = S.peer
    if p is not None:
        try:
            in_sock.sendto(pkt, p)
        except OSError:
            pass


def send_feedback(i, vals):
    send_peer(codec.pack(T_FB, i, *vals))


def send_cmd(i, cmd, arg=0):
    """Polecenie do nadajnika (3x - UDP może zgubić pakiet; nadajnik ignoruje powtórki)."""
    for _ in range(3):
        send_peer(codec.pack(T_CMD, i, cmd, arg))


def batt_codes(pct, flags):
    """-> (kod baterii DSU, bajt statusu baterii DS4)."""
    if pct > 100:
        return 5, 0x1B
    if flags & 1:
        full = pct >= 100
        return (0xEF if full else 0xEE), 0x10 | (11 if full else min(10, pct // 10))
    dsu = 5 if pct >= 90 else 4 if pct >= 60 else 3 if pct >= 30 else 2 if pct >= 10 else 1
    return dsu, min(8, pct * 8 // 100)


def clear_slot(i, forget_id=False):
    S.alive[i] = False
    S.tp_down[i] = 0
    S.swallow[i] = False
    S.last_v[i] = None
    S.last_touch[i] = None
    S.batt_info[i] = (255, 0)
    S.info[i] = None
    S.seen.forget(lambda k: k[1] == i)
    _digital[i] = None
    hotkeys.reset(i)
    master.reset(i)
    if forget_id:                      # pad fizycznie odszedł - następny w tym slocie przyśle swój ident
        S.sender_info[i] = None
        with S.profile_lock:
            S.pad_id[i] = None
            S.override[i] = None
            S.active[i] = S.active_key(i)
            S.remap_maps[i] = S.store.get(S.active[i])
    S.reset_state(i)
    mouse.reset(i)


def handle(t, i, data):
    if t == T_BYE:   # nadajnik: pad rozłączony -> odłącz wirtualny DS4
        was = S.want[i].is_set() or S.alive[i]
        clear_slot(i, forget_id=True)
        S.want[i].clear()
        S.wake[i].set()
        S.ignore_until[i] = time.monotonic() + 1.0   # spóźnione pakiety nie mogą go odtworzyć
        if was:
            print(f"Controller {i}: disconnected by sender")
        return
    S.last_seen[i] = time.monotonic()
    if not S.alive[i]:
        S.alive[i] = True
        print(f"Controller {i}: data stream started")
    if not S.want[i].is_set():
        S.want[i].set()   # wątek slotu utworzy wirtualny DS4
        rx_console.pad_connected(i)       # pad master z już znanym identyfikatorem -> auto-ON trybu konsoli
    if t == T_STATE:
        now_us = time.monotonic_ns() // 1000
        v = ST_STATE.unpack_from(data, 4)
        if v == S.last_v[i]:
            return      # powtórka (heartbeat) - nic się nie zmieniło
        S.last_v[i] = v
        mask = v[8] & MASK_ALL
        bd, b4 = batt_codes(v[9], v[10])
        S.batt_info[i] = (v[9], v[10])
        lx, ly, lt, rx, ry, rt = v[:6]
        hx, hy = clamp(v[6], -1, 1), clamp(v[7], -1, 1)
        dig = (mask, hx, hy, lt >> 5, rt >> 5)   # spusty z dokładnością 1/8 (histereza spustu w trybie konsoli)
        if dig != _digital[i]:              # skróty / master / tryb konsoli tylko przy zmianie przycisków,
            _digital[i] = dig               # a nie przy każdej próbce gyro i gałek (250 Hz)
            hotkeys.on_state(i, mask, hx, hy)          # najpierw skróty PS (nowe naciśnięcie PS), potem master,
            master.on_state(i, mask, hx, hy, lt, rt)   # który może oznaczyć to PS jako zużyte w kombinacji
            rx_console.on_state(i, mask, hx, hy, lt, rt)
        with S.lock:
            s = S.states[i]
            s["raw"] = (mask, hx, hy, lt, rt)
            s["fwd"] = hotkeys.forward(i, mask, hx, hy)
            s["rawst"] = (lx, ly, rx, ry)
            s["acc"], s["gyro"] = v[11:14], v[14:17]      # przed recompute - gyro wpływa na prawą gałkę / mysz
            S.recompute(s, S.remap_maps[i])
            s["batt_dsu"], s["batt_ds4"] = bd, b4
            s["ts"] = now_us         # czas PRZYJŚCIA próbki - dla DSU lepszy niż czas budowania pakietu (jitter)
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
            if not S.mouse_mode[i]:   # w trybie myszy dotyk jest tylko zapamiętany
                s["ver"] += 1
        if not S.mouse_mode[i]:
            S.notify(i)
    elif t == T_CLICK:
        down = data[4] if data[4] <= 2 else 1     # 0 = puszczony, 1 = klik, 2 = klik dwoma palcami (PPM)
        if down == S.tp_down[i]:
            return
        S.tp_down[i] = down
        if down:
            rx_console.on_touch_click(i)
        if down and S.states[i]["raw"][0] & PS_BIT:   # PS + klik touchpada = zmiana trybu (na surowym PS)
            S.swallow[i] = True
            hotkeys.mark_used(i)
            if not master.check(i, "mouse"):
                return
            S.mouse_mode[i] = not S.mouse_mode[i]
            F.pulse(i, "mouse" if S.mouse_mode[i] else "ds4")
            mouse.reset(i)
            with S.lock:
                S.states[i]["tp"] = False
                S.states[i]["ver"] += 1
            S.notify(i)
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
        if S.mouse_mode[i]:
            dx, dy, fl = ST_MOUSE.unpack_from(data, 4)
            mouse.mouse_rel(i, dx, dy, fl)
    elif t == T_IDENT:
        pid = BODY[T_IDENT].unpack_from(data, 4)[0].rstrip(b"\0").decode("ascii", "replace").lower()
        if pid and pid != S.pad_id[i]:
            S.pad_id[i] = pid
            S.override[i] = None
            S.reload_profile(i)
            rx_console.pad_connected(i)   # nowy pad w slocie - jeśli to master, auto-ON trybu konsoli
            n = profile_size(S.remap_maps[i])
            print(f"Controller {i}: id {pid}, profile {S.profile_label(S.active[i])}"
                  + (f" ({n} changes)" if n else ""))
    elif t == T_VERSION:
        v = BODY[T_VERSION].unpack_from(data, 4)
        if v != S.sender_info[i]:
            S.sender_info[i] = v
            print(f"Controller {i}: sender v{v[0]}, rumble methods {v[1]}, reason {v[2]}")
    elif t == T_INFO:
        rtt, pad_hz, motion_hz = ST_INFO.unpack_from(data, 4)
        S.info[i] = (None if rtt == RTT_NONE else rtt / 10, pad_hz, motion_hz, time.monotonic())


def check_stale(now):
    """Cisza od nadajnika: najpierw reset stanu (stale_s), potem odłączenie wirtualnego DS4 (unplug_s)."""
    for i in range(C.NUM_PADS):
        silent = now - S.last_seen[i]
        if S.alive[i] and silent > C.STALE_S:
            print(f"Controller {i}: no data for {C.STALE_S}s - state reset")
            clear_slot(i)
        if S.want[i].is_set() and silent > C.UNPLUG_S:
            print(f"Controller {i}: no data for {C.UNPLUG_S}s - unplugging virtual DS4")
            S.want[i].clear()
            S.wake[i].set()


def net_loop():
    boost_thread()
    link = LinkStats(C.NUM_PADS)
    stats = {}
    bad = 0
    t_print = t_check = time.monotonic()
    while not S.stop.is_set():
        try:
            data, addr = in_sock.recvfrom(2048)
        except socket.timeout:
            data = None
        except OSError as e:
            # obcy, za duży datagram (WSAEMSGSIZE) albo ICMP nie mogą zatrzymywać odbioru na 10 ms
            if getattr(e, "winerror", None) not in (10040, 10054):
                time.sleep(0.01)
            continue

        now = time.monotonic()
        if now - t_check >= 0.1:
            t_check = now
            check_stale(now)
            link.tick(now)
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
        link.packet(sq)
        if t == T_STATE and i < C.NUM_PADS:
            link.states[i] += 1

        if C.DEBUG:
            key = (t, i, len(data))
            stats[key] = stats.get(key, 0) + 1
            if now - t_print >= 1.0:
                print("packets/s (type, ctrl, len):", stats, f"| rejected total: {bad}")
                stats.clear()
                t_print = now

        if t == T_PING:   # ping -> pong (pomiar RTT po stronie nadajnika)
            S.peer = addr
            send_peer(codec.pack(T_PONG, i, BODY[T_PING].unpack_from(data, 4)[0]))
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
        if not S.seen.fresh((t, i), sq, now):
            continue
        S.peer = addr
        try:
            handle(t, i, data)
        except Exception as e:
            print("handle error:", repr(e))
