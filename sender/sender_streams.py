"""Sender: pętle odczytu urządzeń evdev (pad, ruch, touchpad, bateria) i heartbeat."""
import array
import asyncio
import collections
import fcntl
import time

from evdev import ecodes as ec

import sender_calib as calib
import sender_state as state
import sender_wol as wol
from sender_config import AUTOBIAS_MAX_STEP, BURST_DELAYS, C, IDLE_TP, TOUCH_H, TOUCH_W, PAD_ABS
from proto import CAL_RUNNING, KEY_BIT, LAT_NONE, clamp


def _eviocgmtslots(nslots):
    """ioctl EVIOCGMTSLOTS: _IOC(_IOC_READ, 'E', 0x0a, rozmiar) dla bufora (kod + wartość na slot) po 4 B."""
    return (2 << 30) | ((4 * (nslots + 1)) << 16) | (ord("E") << 8) | 0x0A


def read_mt(dev, nslots=2):
    """Aktualny stan palców prosto z jądra: [(tracking_id, x, y), ...] dla slotów 0..nslots-1.
    Potrzebne po SYN_DROPPED - zdarzenia przepadły, ale jądro wie, które palce leżą i gdzie."""
    try:
        total = max(nslots, dev.absinfo(ec.ABS_MT_SLOT).max + 1)
    except Exception:
        total = nslots
    vals = {}
    for code in (ec.ABS_MT_TRACKING_ID, ec.ABS_MT_POSITION_X, ec.ABS_MT_POSITION_Y):
        buf = array.array("i", [code] + [0] * total)
        fcntl.ioctl(dev.fd, _eviocgmtslots(total), buf, True)
        vals[code] = buf[1:]
    return [(vals[ec.ABS_MT_TRACKING_ID][k], vals[ec.ABS_MT_POSITION_X][k], vals[ec.ABS_MT_POSITION_Y][k])
            for k in range(nslots)]


class RateMeter:
    """Realna częstotliwość raportów urządzenia i przerwy między nimi (tylko przy --stats)."""

    def __init__(self):
        self.t_prev = 0.0
        self.gaps = []

    def tick(self, now):
        if self.t_prev and len(self.gaps) < 4096:
            self.gaps.append(now - self.t_prev)
        self.t_prev = now

    def report(self):
        g, self.gaps = sorted(self.gaps), []
        if len(g) < 10:
            return None
        return len(g) / sum(g), g[int(len(g) * 0.99)] * 1000, g[-1] * 1000   # Hz, p99 ms, max ms


class BtDelay:
    """Opóźnienie Bluetooth ponad minimum: pad stempluje każdy raport własnym zegarem (MSC_TIMESTAMP z urządzenia
    ruchu, µs), jądro - czasem odbioru. Różnica = stała nieznana (zegary) + opóźnienie radia; jej minimum z ostatnich
    WINDOW_US to najlepszy przypadek, a nadwyżka nad nim - kolejka / retransmisje BT. Krótkie okno: dryf kwarców
    pada i telefonu (50 ppm = 0.1 ms na 2 s) nie udaje opóźnienia. Minimum w oknie: kolejka monotoniczna, O(1)."""

    WINDOW_US = 2_000_000

    def __init__(self):
        self.epoch, self.last = 0, None
        self.win = collections.deque()        # (czas jądra, różnica), różnice rosnące

    def sample(self, kernel_us, pad_us):
        pad_us &= 0xFFFFFFFF                  # licznik pada ma 32 bity (przepełnienie co ~71 min)
        if self.last is not None and pad_us < self.last - 0x80000000:
            self.epoch += 1 << 32
        self.last = pad_us
        d = kernel_us - pad_us - self.epoch
        win = self.win
        while win and win[-1][1] >= d:
            win.pop()
        win.append((kernel_us, d))
        while win[0][0] < kernel_us - self.WINDOW_US:
            win.popleft()
        return min(LAT_NONE - 1, d - win[0][1])


async def stream_pad(c):
    loop = asyncio.get_running_loop()
    dirty = burst = False
    dropping = False
    meter = c.meters.setdefault("pad", RateMeter()) if C.STATS else None
    async for e in c.pad_dev.async_read_loop():
        et = e.type
        if et == ec.EV_SYN and e.code == ec.SYN_REPORT:
            c.reports["pad"] += 1
            if meter is not None:
                meter.tick(time.monotonic())
        if dropping:
            # bufor jądra się przepełnił (np. seria raportów po zacięciu BT): zdarzenia do najbliższego
            # SYN_REPORT są niepełne, więc je pomijamy i czytamy cały stan z urządzenia
            if et == ec.EV_SYN and e.code == ec.SYN_REPORT:
                dropping = dirty = burst = False
                c.resync()
                c.send_state()
                for d in BURST_DELAYS:
                    loop.call_later(d, c.send_state, True)
                wol.on_mask(c)            # kombinacja puszczona w zgubionych zdarzeniach nie może odpalić Wake-on-LAN
            continue
        if et == ec.EV_ABS:
            code = e.code
            if code in PAD_ABS:
                c.set_abs(code, e.value)
                dirty = True
            elif code == 16 or code == 17:
                c.hat[code - 16] = clamp(e.value, -1, 1)
                dirty = True
        elif et == ec.EV_KEY:
            bit = KEY_BIT.get(e.code)
            if bit:
                if e.value:
                    c.mask |= bit
                else:
                    c.mask &= ~bit
                dirty = burst = True
        elif et == ec.EV_SYN and e.code == ec.SYN_DROPPED:
            dropping = True
        elif et == ec.EV_SYN and e.code == ec.SYN_REPORT and dirty:
            dirty = False
            c.mark_event(e)
            c.note_input(time.monotonic())
            if burst:
                burst = False
                c.send_state()
                for d in BURST_DELAYS:
                    loop.call_later(d, c.send_state, True)
                wol.on_mask(c)            # PS + przycisk przytrzymane przy milczącym receiverze = Wake-on-LAN
            else:
                c.request_send()


async def heartbeat():
    n = 0
    while True:
        now = time.monotonic()
        slow = n % 5 == 0
        for c in list(state.active.values()):
            if now - c.t_state >= C.HEARTBEAT_S * 2.0:
                c.send_state(True)
            if slow:
                c.send_ident()
                c.send_click()
                c.send_touch(force=True)      # także bez touchpada: zgubiony pakiet "puszczenia" palca dojdzie
        n += 1
        await asyncio.sleep(C.HEARTBEAT_S)


def _start_cal(c, now):
    """Kalibracja przy starcie: pad z zapisaną kalibracją (z okna receivera) używa jej od razu, bez czekania."""
    st = calib.stored(c.key)
    if st is not None:
        c.gyro_bias = list(st["bias"])
        print(f"Controller {c.idx}: gyro bias {[round(b) for b in c.gyro_bias]} (saved calibration)")
        return None
    if C.CAL_S <= 0:
        return None
    print(f"Controller {c.idx}: calibrating gyro for {C.CAL_S}s - keep the pad still")
    return calib.GyroCal(now, C.CAL_S, user=False)


async def stream_motion(c):
    v = [0] * 6
    last = 0.0
    cal = _start_cal(c, time.monotonic())
    win_t0, win_n, win_sum = time.monotonic(), 0, [0, 0, 0]
    win_min, win_max = [1 << 60] * 3, [-(1 << 60)] * 3
    msum, mn = [0] * 6, 0       # suma próbek od ostatniego pakietu: wysyłamy ŚREDNIĄ, nie przypadkową ostatnią próbkę
    meter = c.meters.setdefault("motion", RateMeter()) if C.STATS else None
    bt = BtDelay()
    dropping = False
    async for e in c.motion_dev.async_read_loop():
        if e.type == ec.EV_SYN and e.code == ec.SYN_DROPPED:
            dropping = True
            continue
        if dropping:
            if e.type != ec.EV_SYN or e.code != ec.SYN_REPORT:
                continue
            dropping = False
            for k in range(6):        # oś, której zmiana przepadła, nie może zostać ze starą wartością
                try:
                    v[k] = c.motion_dev.absinfo(k).value
                except Exception:
                    pass
        if e.type == ec.EV_ABS and 0 <= e.code <= 5:
            v[e.code] = e.value
        elif e.type == ec.EV_MSC and e.code == ec.MSC_TIMESTAMP:
            c.bt_us = bt.sample(e.sec * 1_000_000 + e.usec, e.value)
        elif e.type == ec.EV_SYN and e.code == ec.SYN_REPORT:
            now = time.monotonic()
            c.reports["motion"] += 1
            if meter is not None:
                meter.tick(now)
            if c.cal_request:                 # kalibracja z okna receivera (pad leży nieruchomo)
                cal = calib.GyroCal(now, c.cal_request, user=True)
                c.cal_request = 0
                calib.report(c, CAL_RUNNING)
            if cal is not None and (r := cal.feed(now, v[3:6])) is not None:
                (calib.finish if cal.user else calib.startup_done)(c, r)
                cal = None
            bias = c.gyro_bias
            if C.AUTOBIAS and cal is None:
                for k in range(3):
                    x = v[3 + k]
                    win_sum[k] += x
                    if x < win_min[k]:
                        win_min[k] = x
                    if x > win_max[k]:
                        win_max[k] = x
                win_n += 1
                if now - win_t0 >= C.AUTOBIAS_S:
                    if win_n >= 20 and all(win_max[k] - win_min[k] <= C.AUTOBIAS_RANGE for k in range(3)):
                        for k in range(3):
                            target = win_sum[k] / win_n
                            if abs(target - bias[k]) <= AUTOBIAS_MAX_STEP:
                                bias[k] += (target - bias[k]) * 0.5
                    win_t0, win_n, win_sum = now, 0, [0, 0, 0]
                    win_min, win_max = [1 << 60] * 3, [-(1 << 60)] * 3
            if cal is not None and not cal.user:
                continue                      # kalibracja startowa: ruchu jeszcze nie wysyłamy
            for k in range(6):
                msum[k] += v[k]
            mn += 1
            if now - last < C.MOTION_MIN_DT:
                continue        # próbki wpadają szybciej niż limit - zostają uśrednione do następnego pakietu
            last = now
            m = c.motion
            a = [x / mn for x in msum]
            msum, mn = [0] * 6, 0
            new = [round(a[0]), round(a[1]), round(a[2]),
                   round(a[3] - bias[0]), round(a[4] - bias[1]), round(a[5] - bias[2])]
            if c.pending or any(abs(new[k] - m[k]) > (C.DB_ACC if k < 3 else C.DB_GYRO) for k in range(6)):
                c.motion = new
                c.mark_event(e)
                c.request_send()


def _same_finger(a, b):
    """Czy palec (on, id, x, y) nie zmienił się w stopniu wartym wysłania (deadzone)."""
    if a[0] != b[0]:
        return False
    if not a[0]:
        return True
    return (a[1] == b[1] and abs(a[2] - b[2]) < C.TOUCH_DEADZONE and abs(a[3] - b[3]) < C.TOUCH_DEADZONE)


class _Session:
    """Jeden kontakt z touchpadem: od położenia pierwszego palca do podniesienia ostatniego."""
    __slots__ = ("t0", "moved", "max_n", "started", "scrolled", "clicked", "kind", "pend")

    def __init__(self, now):
        self.t0, self.moved, self.max_n = now, 0, 0
        self.started = self.scrolled = self.clicked = False
        self.kind = 0                 # 0 = nic, 1 = kursor, 2 = przewijanie (pend dotyczy tego rodzaju)
        self.pend = [0, 0]            # ruch wstrzymany do przekroczenia touch_start_move


async def stream_touch(c):
    """Touchpad -> (a) pakiety dotyku DS4/DSU, (b) pakiety myszy (kursor, tap, przewijanie 2 palcami).

    Zasady myszy: 1 palec = kursor (rusza dopiero po touch_start_move, więc tap nie szarpie kursorem),
    2 palce = przewijanie, a do podniesienia wszystkich palców kursor stoi (puszczanie palców po kolei
    nie odpala kliknięcia ani skoku). Krótki tap 1 palcem = LPM, 2 palcami = PPM."""
    try:
        maxx = c.touch_dev.absinfo(ec.ABS_MT_POSITION_X).max or 1919
        maxy = c.touch_dev.absinfo(ec.ABS_MT_POSITION_Y).max or 1079
    except Exception:
        maxx, maxy = 1919, 1079
    loop = asyncio.get_running_loop()
    slot = 0
    # x / y = pozycja zapamiętana w slocie jądra (None = nieznana). Jądro pomija zdarzenie osi, gdy wartość w slocie
    # się nie zmieniła - także dla NOWEGO palca. Pozycja przeżywa więc podniesienie palca, a nowy palec przyłożony
    # w tym samym x albo y (np. przy krawędzi: 0 / max) bierze ją stąd; wyzerowana gubiła taki dotyk i tap.
    # Palec, który już leży (pad połączony z dłonią na touchpadzie, touchpad dołączony później), też jest "on":
    # jądro nie wyśle mu ABS_MT_TRACKING_ID, dopóki go nie podniesie - bez tego jego ruch nie dawał ani kursora,
    # ani dotyku DS4. Taki kontakt nie kończy się tapem (jak po SYN_DROPPED).
    fingers = [{"on": tid >= 0, "id": tid & 0x7F if tid >= 0 else 0, "x": x, "y": y}
               for tid, x, y in _kernel_slots(c.touch_dev, 2)]
    prev = [None, None]            # poprzednia pozycja palca (x, y); None = świeży palec, brak delty
    sess = None
    last_tp = IDLE_TP
    dropping = False
    resynced = any(f["on"] for f in fingers)
    async for e in c.touch_dev.async_read_loop():
        et = e.type
        if dropping:
            if et != ec.EV_SYN or e.code != ec.SYN_REPORT:
                continue              # niepełna ramka po przepełnieniu bufora - pomijamy
            dropping = False
            _resync_touch(c, fingers, prev)
            try:
                slot = c.touch_dev.absinfo(ec.ABS_MT_SLOT).value
            except Exception:
                slot = 0
            if sess is not None:
                sess.clicked = True   # ten kontakt nie może już skończyć się tapem
            resynced = True
            # dalej: zwykła obsługa SYN_REPORT z odtworzonym stanem palców
        if et == ec.EV_ABS:
            if e.code == ec.ABS_MT_SLOT:
                slot = e.value
            elif slot in (0, 1):
                f = fingers[slot]
                if e.code == ec.ABS_MT_TRACKING_ID:
                    prev[slot] = None              # nowy / podniesiony palec: bez delty, pozycja zostaje (jak w jądrze)
                    if e.value >= 0:
                        f.update(on=True, id=e.value & 0x7F)
                    else:
                        f["on"] = False
                elif e.code == ec.ABS_MT_POSITION_X:
                    f["x"] = e.value
                elif e.code == ec.ABS_MT_POSITION_Y:
                    f["y"] = e.value
        elif et == ec.EV_SYN and e.code == ec.SYN_DROPPED:
            dropping = True
            continue
        elif et == ec.EV_KEY and e.code == ec.BTN_LEFT:
            # klik z dwoma palcami na touchpadzie = 2 (w trybie myszy prawy przycisk); wartość trzymana do puszczenia
            c.tp = (2 if C.GESTURES and fingers[0]["on"] and fingers[1]["on"] else 1) if e.value else 0
            if e.value and sess is not None:
                sess.clicked = True      # fizyczny klik w trakcie dotyku unieważnia tap do podniesienia palców
            c.send_click()
            for d in BURST_DELAYS:
                loop.call_later(d, c.send_click)
        elif et == ec.EV_SYN and e.code == ec.SYN_REPORT:
            on = fingers[0]["on"] or fingers[1]["on"]
            now = time.monotonic()
            if on:
                c.last_input = now
            if sess is None:
                if not on:
                    continue
                sess = _Session(now)
                if resynced:
                    sess.clicked = True   # palec położony w trakcie zgubionych zdarzeń - bez tapu
            resynced = False

            # --- mysz: delty palców z pozycji znanych w tej ramce ---
            deltas = []
            for k, f in enumerate(fingers):
                if f["on"] and f["x"] is not None and f["y"] is not None:
                    p = prev[k]
                    deltas.append((f["x"] - p[0], f["y"] - p[1]) if p else (0, 0))
                    prev[k] = (f["x"], f["y"])
            n = len(deltas)
            if n > sess.max_n:
                sess.max_n = n
            sess.moved += sum(abs(dx) + abs(dy) for dx, dy in deltas)
            kind = dx = dy = 0
            if C.GESTURES and n >= 2:
                kind, dx, dy = 2, deltas[0][0] + deltas[1][0], deltas[0][1] + deltas[1][1]
            elif n >= 1 and not (C.GESTURES and sess.max_n >= 2):
                kind, (dx, dy) = 1, deltas[0]
            if kind and (dx or dy):
                if kind != sess.kind:
                    sess.kind, sess.pend = kind, [0, 0]
                sess.pend[0] += dx
                sess.pend[1] += dy
                if sess.started or sess.moved >= C.TOUCH_START_MOVE:
                    sess.started = True
                    if kind == 2:
                        sess.scrolled = True
                    c.send_mouse(sess.pend[0], sess.pend[1], 2 if kind == 2 else 0)
                    sess.pend = [0, 0]

            # --- dotyk DS4/DSU: deadzone sprawdza OBA palce (drugi nie może zostać połknięty przez pierwszy) ---
            tp = []
            for f in fingers:
                if f["on"] and f["x"] is not None and f["y"] is not None:
                    tp.append((1, f["id"],
                               clamp(f["x"] * TOUCH_W // maxx, 0, TOUCH_W),
                               clamp(f["y"] * TOUCH_H // maxy, 0, TOUCH_H)))
                else:
                    tp.append((0, f["id"], 0, 0))
            tp = tuple(tp)
            if tp != last_tp and not (_same_finger(tp[0], last_tp[0]) and _same_finger(tp[1], last_tp[1])):
                last_tp = c.touch = tp
                c.send_touch()

            # --- koniec kontaktu: tap ---
            if not on:
                if (C.TAP_CLICK and not sess.clicked and now - sess.t0 < C.TAP_MAX_S
                        and sess.moved < C.TAP_MAX_MOVE):
                    if C.GESTURES and sess.max_n >= 2:
                        if not sess.scrolled:
                            c.send_tap(4)
                    elif sess.max_n >= 1:
                        c.send_tap(1)
                sess = None


def _kernel_slots(dev, n):
    """Stan slotów MT z jądra [(tracking_id, x, y)]; bez ioctl (inna platforma, testy) - palce podniesione, pozycja
    nieznana (None)."""
    try:
        return read_mt(dev, n)
    except (OSError, AttributeError, TypeError, ValueError):
        return [(-1, None, None)] * n


def _resync_touch(c, fingers, prev):
    """Odtwarza palce i przycisk touchpada ze stanu jądra; bez ioctl (inna platforma) - podnosi wszystkie palce."""
    for k, (tid, x, y) in enumerate(_kernel_slots(c.touch_dev, len(fingers))):
        fingers[k].update(on=tid >= 0, x=x, y=y)       # pozycja także podniesionego palca (patrz stream_touch)
        if tid >= 0:
            fingers[k]["id"] = tid & 0x7F
        prev[k] = None                # brak delty w pierwszej ramce - kursor nie skoczy
    try:
        tp = ec.BTN_LEFT in c.touch_dev.active_keys()
    except (OSError, AttributeError):
        return
    if bool(c.tp) != tp:
        c.tp = (2 if C.GESTURES and fingers[0]["on"] and fingers[1]["on"] else 1) if tp else 0
        c.send_click()


async def stream_battery(c):
    path = f"/sys/class/power_supply/ps-controller-battery-{(c.key or '').lower()}"
    while True:
        try:
            with open(path + "/capacity") as f:
                pct = int(f.read().strip())
            with open(path + "/status") as f:
                st = f.read().strip().lower()
            c.batt = clamp(pct, 0, 100)
            c.flags = 1 if st in ("charging", "full") else 0
        except (OSError, ValueError):
            c.batt, c.flags = 255, 0
        await asyncio.sleep(C.BATT_POLL_S)
