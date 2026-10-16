"""Receiver: odbiór pakietów z nadajnika, obsługa zdarzeń, wykrywanie ciszy."""
import socket
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
                   T_BTDEV, T_BTSTATE, T_BUILD, T_BUILD2, T_BYE, T_CALRES, T_CLICK, T_CMD, T_EVENT, T_FB, T_IDENT, T_INFO,
                   T_MOUSE, T_PING, T_PONG, T_PONG2, T_SCFG, T_STATE, T_STATE2, T_TOUCH, T_TRIG, T_TRIG2, T_UPDMSG,
                   T_VERSION, TRIG2_OFF, clamp, cstr, mac_bytes, mac_text)
from rx_buttons import BUTTON_CODES, MASK_ALL, PS_BIT, TRIG_ON

from rx_config import C
from rx_i18n import _
from rx_winapi import bind_udp, boost_thread

RX_TYPES = {T_STATE, T_STATE2, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_BYE, T_IDENT, T_INFO, T_VERSION,   # typy, które
            T_CALRES}                                                                                  # receiver przyjmuje
ST_TOUCH, ST_MOUSE, ST_INFO = BODY[T_TOUCH], BODY[T_MOUSE], BODY[T_INFO]
STATES = (T_STATE, T_STATE2)
MAX_PROBE = 20_000              # pomiar dryfu: bezpiecznik długości listy próbek
MAX_NET_US = 1_000_000          # sieć dłużej niż 1 s = zły zegar (np. zaraz po restarcie), nie pomiar
SCFG_S = 5.0                    # co tyle ustawienia Wake-on-LAN / zdarzeń do nadajnika (po jego restarcie też je ma)
KEY_WARN_S = 60.0               # ostrzeżenie o innym kluczu HMAC w nadajniku najwyżej co tyle


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
_pressed = [None] * 4    # (maska, d-pad, L2 >= TRIG_ON, R2 >= TRIG_ON) per slot - zmiana = próbka "przycisk -> gra"
_key_warned = [float("-inf")]   # kiedy ostatnio ostrzegliśmy o innym kluczu HMAC


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
    for _n in range(3):
        send_peer(codec.pack(T_CMD, i, cmd, arg))


def send_triggers(i, vals, repeat=1):
    """Efekty spustów do nadajnika: 12 wartości = trig2, 4 = stary trig (sam opór); zmiana idzie kilka razy
    (zgubiony pakiet)."""
    t = T_TRIG2 if len(vals) == len(TRIG2_OFF) else T_TRIG
    for _n in range(repeat):
        send_peer(codec.pack(t, i, *vals))


def scfg_values(s=None):
    """Ustawienia dla nadajnika (pakiet scfg): Wake-on-LAN i zdarzenia do dziennika."""
    s = s or rx_settings.cur
    w = s.wol
    flags = (SCFG_WOL if w.enabled else 0) | (SCFG_EVENTS if s.log.sender else 0)
    return (flags, mac_bytes(w.mac) or bytes(6), socket.inet_aton(w.bcast) if w.bcast else bytes(4),
            PAD_KEYS.index(BUTTON_CODES[w.button]), w.hold_ms)


def send_scfg(repeat=1):
    if codec is None or S.peer is None:      # start (ustawienia wczytane przed gniazdem) / nadajnik jeszcze nieznany
        return
    for _n in range(repeat):
        send_peer(codec.pack(T_SCFG, 255, *scfg_values()))


def _settings_changed(old, new):
    if (old.wol, old.log.sender) != (new.wol, new.log.sender):
        send_scfg(3)


rx_settings.on_change(_settings_changed)


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
    _digital[i] = _pressed[i] = None
    hotkeys.reset(i)
    master.reset(i)
    rx_latency.clear(i)                # pomiary opóźnienia poprzedniego pada nie mieszają się z następnym
    with S.lock:
        S.states[i]["lat"] = S.states[i]["blat"] = None
    if forget_id:                      # pad fizycznie odszedł - następny w tym slocie przyśle swój ident
        rx_latency.reset_buttons(i)    # ostatnie naciśnięcia (bez limitu czasu) należały do tamtego pada
        S.sender_info[i] = None
        S.gyro_cal[i] = None           # kalibracja żyroskopu należała do tamtego pada
        with S.profile_lock:
            S.pad_id[i] = None
            S.override[i] = None
            S.active[i] = S.active_key(i)
            S.remap_maps[i] = S.effective_profile(i, S.active[i])
    S.reset_state(i)
    mouse.reset(i)
    if forget_id:
        master.mouse_follows_master()  # master odszedł -> mysz wg mouse_pads


def _latency(lat, arr):
    """Pola pomiaru z pakietu state2 + czas odbioru [ns] -> (odbiór, phone, bt, net) albo None (brak pomiaru)."""
    pc_us, phone, bt = lat
    if not arr or not pc_us or phone == LAT_NONE:
        return None
    net = (arr // 1000 - pc_us) & 0xFFFFFFFF
    if net >= 0x80000000:          # odebrany "przed" wysłaniem: błąd przesunięcia zegarów (do połowy RTT) - to ~0
        net -= 1 << 32
    if not -MAX_NET_US < net < MAX_NET_US:
        return None
    return arr, phone, bt, max(0, net)


def handle(t, i, data, arr=0):
    """Pakiet od nadajnika; arr = czas odbioru (perf_counter_ns) - do pomiaru opóźnienia."""
    if t == T_BYE:   # nadajnik: pad rozłączony -> odłącz wirtualny DS4
        was = S.want[i].is_set() or S.alive[i]
        clear_slot(i, forget_id=True)
        S.want[i].clear()
        S.wake[i].set()
        S.ignore_until[i] = time.monotonic() + 1.0   # spóźnione pakiety nie mogą go odtworzyć
        if was:
            print(f"Controller {i}: disconnected by sender")
            ev.info(_("{pad} rozłączony").format(pad=ev.pad_name(i)))
        return
    S.last_seen[i] = time.monotonic()
    if not S.alive[i]:
        S.alive[i] = True
        print(f"Controller {i}: data stream started")
    if not S.want[i].is_set():
        ev.info(_("{pad} połączony").format(pad=ev.pad_name(i)))   # po krótkiej ciszy DS4 został - to nie nowe połączenie
        S.want[i].set()   # wątek slotu utworzy wirtualny DS4
        rx_console.pad_connected(i)       # pad master z już znanym identyfikatorem -> auto-ON trybu konsoli
    if t in STATES:
        now_us = time.monotonic_ns() // 1000
        v = BODY[t].unpack_from(data, 4)
        lat = _latency(v[STATE_FIELDS:], arr) if t == T_STATE2 else None
        v = v[:STATE_FIELDS]
        if v == S.last_v[i]:
            return      # powtórka (heartbeat) - nic się nie zmieniło
        S.last_v[i] = v
        mask = v[8] & MASK_ALL
        bd, b4 = batt_codes(v[9], v[10])
        S.batt_info[i] = (v[9], v[10])
        lx, ly, lt, rx, ry, rt = v[:6]
        hx, hy = clamp(v[6], -1, 1), clamp(v[7], -1, 1)
        dig = (mask, hx, hy, lt >> 5, rt >> 5)   # spusty z dokładnością 1/8 (histereza spustu w trybie konsoli)
        btn = (mask, hx, hy, lt >= TRIG_ON, rt >= TRIG_ON)
        blocked = S.states[i]["blocked"]    # widok Test: gra dostaje neutralny stan, skróty i master nie działają
        pressed = (lat is not None and not blocked and _pressed[i] is not None   # pierwszy stan po połączeniu
                   and btn != _pressed[i])                                       # to nie naciśnięcie
        _pressed[i] = btn
        if dig != _digital[i]:              # skróty / master / tryb konsoli tylko przy zmianie przycisków,
            _digital[i] = dig               # a nie przy każdej próbce gyro i gałek (250 Hz)
            testmode.sync(i)                # widok Test otwarty / zamknięty - skróty slotu od czystego stanu
            if testmode.on_state(i, mask, time.monotonic()):   # PS + △ mastera: wyjście z testu (także z
                blocked = False                                 # testowanego pada) - wejścia już połknięte
            elif not blocked and testmode.shortcuts_on():      # widok Test: skróty żadnego pada nie działają
                hotkeys.on_state(i, mask, hx, hy)          # najpierw skróty PS (nowe naciśnięcie PS), potem master,
                master.on_state(i, mask, hx, hy, lt, rt)   # który może oznaczyć to PS jako zużyte w kombinacji
                rx_console.on_state(i, mask, hx, hy, lt, rt)
        with S.lock:
            s = S.states[i]
            s["raw"] = (mask, hx, hy, lt, rt)
            s["fwd"] = hotkeys.forward(i, mask, hx, hy)
            s["rawst"] = (lx, ly, rx, ry)
            if (p := S.probe[i]) is not None and len(p) < MAX_PROBE:   # pomiar dryfu (rx_drift)
                p.append(s["rawst"])
            s["acc"], s["gyro"] = v[11:14], v[14:17]      # przed recompute - gyro wpływa na prawą gałkę / mysz
            S.recompute(s, S.remap_maps[i])
            s["batt_dsu"], s["batt_ds4"] = bd, b4
            s["ts"] = now_us         # czas PRZYJŚCIA próbki - dla DSU lepszy niż czas budowania pakietu (jitter)
            if s["lat"] is None and not blocked:   # pomiar najstarszej zmiany czekającej na raport (rx_ds4 - ViGEm)
                s["lat"] = lat
            if pressed and s["blat"] is None:   # osobno: najstarsza czekająca zmiana przycisku / spustu
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
            if not S.mouse_mode[i]:   # w trybie myszy dotyk jest tylko zapamiętany
                s["ver"] += 1
        if not S.mouse_mode[i]:
            S.notify(i)
    elif t == T_CLICK:
        down = data[4] if data[4] <= 2 else 1     # 0 = puszczony, 1 = klik, 2 = klik dwoma palcami (PPM)
        if down == S.tp_down[i]:
            return
        S.tp_down[i] = down
        S.states[i]["rtp"] = down          # widok Test: klik widać także w trybie myszy i przy blokadzie
        if S.states[i]["blocked"]:
            S.swallow[i] = False
            if not S.mouse_mode[i]:
                # stan na żywo także pod blokadą (gra i tak dostaje neutralny raport): klik trzymany przed testem
                # i puszczony w trakcie zostawał w stanie "wciśnięty" - po teście gra widziała wciśnięty touchpad,
                # dopóki nie kliknęło się go jeszcze raz (powtórki z nadajnika odrzuca porównanie z tp_down)
                with S.lock:
                    S.states[i]["tp"] = bool(down)
            return
        shortcuts = testmode.shortcuts_on()           # widok Test: klik zostaje klikiem, bez skrótów
        if down and shortcuts:
            rx_console.on_touch_click(i)
        if down and shortcuts and S.states[i]["raw"][0] & PS_BIT:   # PS + klik touchpada = zmiana trybu (surowe PS)
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
            with S.profile_lock:              # wątek gry (set_game) nie może przeliczyć profilu w pół zmiany pada
                S.pad_id[i] = pid
                S.override[i] = None
                S.reload_profile(i)
            rx_console.pad_connected(i)   # nowy pad w slocie - jeśli to master, auto-ON trybu konsoli
            master.mouse_follows_master()  # touchpad jako mysz: pad master (mógł właśnie dojść albo zmienić slot)
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
    """Odpowiedź nadajnika na kalibrację żyroskopu -> S.gyro_cal (okno); wynik kalibracji także do dziennika."""
    v = BODY[T_CALRES].unpack_from(data, 4)
    prev = S.gyro_cal[i]
    S.gyro_cal[i] = (v[0], v[1], v[2:5], v[5:8], v[8], time.monotonic())
    if prev is not None and prev[:5] == S.gyro_cal[i][:5]:
        return                          # powtórka (polecenie idzie 3x - odpowiedź też)
    if v[0] == CAL_OK:
        print(f"Controller {i}: gyro calibrated, bias {list(v[2:5])}")
        ev.info(_("{pad}: żyroskop skalibrowany").format(pad=ev.pad_name(i)))
    elif v[0] == CAL_MOVED:
        print(f"Controller {i}: gyro calibration rejected - pad was moving")
        ev.warn(_("{pad}: kalibracja żyroskopu przerwana - pad się ruszał").format(pad=ev.pad_name(i)))


def on_build(data, now, t=T_BUILD):
    """Pakiet build / build2: (commit nadajnika, czas commita, stan aktualizacji UPD_*) -> S.sender_build;
    build2 także adresy, których użyje Wake-on-LAN nadajnika -> S.sender_wol."""
    v = BODY[t].unpack_from(data, 4)
    b = (cstr(v[0]), v[1], v[2])
    if S.sender_build is None or S.sender_build[:3] != b:
        print(f"Sender build {b[0] or '?'} (update state {v[2]})")
    S.sender_build = (*b, now)
    if t == T_BUILD2:
        S.sender_wol = (mac_text(v[3]), socket.inet_ntoa(v[4]) if any(v[4]) else "")


def on_event(i, data):
    """Pakiet event -> dziennik, gdy zdarzenia z nadajnika są włączone w panelu (powtórki odrzuca numer zdarzenia)."""
    if rx_settings.cur.log.sender:
        return ev.on_sender_event(i, data)
    return None


def check_stale(now):
    """Cisza od nadajnika: najpierw reset stanu (stale_s), potem odłączenie wirtualnego DS4 (unplug_s)."""
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
            # jak po pakiecie bye: następny pad w tym slocie (może inny) musi przysłać swój identyfikator - inaczej
            # dostałby profil poprzedniego, a gdy tamten był masterem, włączyłby tryb konsoli i uprawnienia mastera
            clear_slot(i, forget_id=True)


def check_key(now):
    """Pakiety nadajnika odrzucone przez inne ustawienie klucza HMAC (proto.Codec.mismatch_t) -> ostrzeżenie w dzienniku,
    najwyżej co KEY_WARN_S. Bez tego pady po prostu nie działały, a jedyną wskazówką był log nadajnika."""
    if now - codec.mismatch_t < 1.0 and now - _key_warned[0] >= KEY_WARN_S:
        _key_warned[0] = now
        print("Rejecting sender packets: HMAC key differs between sender and receiver (--key / key in config)")
        ev.warn(_("Odrzucono pakiety nadajnika: inny klucz HMAC (key) w nadajniku i w receiverze - ustaw ten sam "
                  "albo usuń go z obu"))


class SenderWatch:
    """Nadajnik online / offline -> dziennik; pierwszy kontakt i co SCFG_S -> ustawienia dla nadajnika (scfg)."""

    def __init__(self):
        self.online, self.t_scfg = False, 0.0

    def tick(self, now):
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
    boost_thread()
    link = LinkStats(C.NUM_PADS)
    watch = SenderWatch()
    stats = {}
    bad = 0
    t_print = t_check = time.monotonic()
    while not S.stop.is_set():
        try:
            data, addr = in_sock.recvfrom(2048)
            arr = time.perf_counter_ns()        # czas odbioru (zegar PC jak w pong2) - początek odcinka "pc"
        except TimeoutError:
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
            watch.tick(now)
            testmode.tick(now)          # PS + △ trzymane bez żadnej zmiany na padzie też kończy test
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
        link.packet(sq)
        if t in STATES and i < C.NUM_PADS:
            link.states[i] += 1

        if C.DEBUG:
            key = (t, i, len(data))
            stats[key] = stats.get(key, 0) + 1
            if now - t_print >= 1.0:
                print("packets/s (type, ctrl, len):", stats, f"| rejected total: {bad}")
                stats.clear()
                t_print = now

        if t == T_UPDMSG:  # powód błędu aktualizacji nadajnika (przychodzi przed pakietem build z wynikiem)
            S.peer = addr
            S.sender_update_msg = BODY[T_UPDMSG].unpack_from(data, 4)[0].rstrip(b"\0").decode("utf-8", "ignore")
            continue
        if t in (T_BUILD, T_BUILD2):  # wersja kodu nadajnika i stan jego aktualizacji (cały nadajnik, idx 255)
            S.peer = addr
            on_build(data, now, t)
            continue
        if t in (T_BTSTATE, T_BTDEV):  # Bluetooth nadajnika (idx 255): stan adaptera, lista urządzeń
            S.peer = addr
            if S.seen.fresh((WIRE[t], 255), sq, now):
                rx_bt.on_packet(t, data, now)
            continue
        if t == T_EVENT:  # zdarzenie nadajnika do dziennika (idx = slot pada albo 255)
            on_event(i, data)
            continue
        if t == T_PING:   # ping -> pong2 (zegar PC dla pomiaru opóźnienia) i pong (RTT; starszy nadajnik zna tylko ten)
            S.peer = addr
            t0 = BODY[T_PING].unpack_from(data, 4)[0]
            send_peer(codec.pack(T_PONG2, i, t0, time.perf_counter_ns() // 1000))
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
        if not S.seen.fresh((WIRE.get(t, t), i), sq, now):     # state i state2: jeden licznik
            continue
        S.peer = addr
        try:
            handle(t, i, data, arr)
        except Exception as e:
            print("handle error:", repr(e))
            ev.error(_("Błąd obsługi pakietu: {err}").format(err=repr(e)))
