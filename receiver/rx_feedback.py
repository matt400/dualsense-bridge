"""Receiver: feedback do pada (wibracja, lightbar) z gry przez ViGEm + krótkie potwierdzenia skrótów,
oraz opór spustów z aktywnego profilu (gra przez DS4 go nie ustawi - robi to nadajnik raportem HID)."""
import threading
import time

import rx_state as S
from proto import clamp
from rx_config import C
from rx_shape import trigger_values

TRIG_REFRESH_S = 2.0   # opór spustów odświeżany co tyle (pad po ponownym połączeniu / zgubiony pakiet)
TRIG_REPEAT = 3        # zmiana oporu wysyłana tyle razy (UDP)

# potwierdzenia na padzie: (czas s, mała wibracja, duża wibracja, kolor lightbara | None = bez zmiany koloru)
PULSES = {
    "mouse": (0.18, 0, 150, (0, 90, 255)),        # touchpad -> mysz
    "ds4": (0.18, 0, 150, (255, 255, 255)),       # touchpad -> dotyk DS4
    "profile": (0.25, 0, 190, (0, 220, 90)),      # zmiana profilu (gra / ręcznie)
    "default": (0.25, 0, 190, (255, 170, 0)),     # powrót do profilu domyślnego
    "up": (0.07, 160, 0, None),                   # czułość w górę
    "down": (0.07, 80, 0, None),                  # czułość w dół
    "action": (0.10, 130, 0, None),               # zrzut ekranu, Game Bar itp.
    "auto_profile": (0.30, 0, 0, (0, 220, 90)),   # profil gry włączony sam (alt-tab) - tylko kolor, bez wibracji
    "auto_default": (0.30, 0, 0, (255, 170, 0)),
    "master": (0.60, 0, 200, (255, 200, 0)),      # ten pad został masterem - złoty błysk
    "hold": (0.06, 90, 0, None),                  # akcja mastera: trzymaj dalej
    "denied": (0.25, 0, 0, (255, 0, 0)),          # tylko master może - czerwone mignięcie
    "test": (0.6, 200, 255, (255, 255, 255)),     # przycisk "Test wibracji" w oknie
}
fb = []
fb_event = threading.Event()
pulses = []       # aktywne potwierdzenie per slot: (koniec, mała, duża, kolor | None) albo None
fb_sent = []      # (wartość, czas) ostatnio wysłanego feedbacku; None = wyślij przy najbliższej okazji
trig_sent = []    # (opór spustów, czas) ostatnio wysłany per slot; None = nic (pad ma domyślne spusty)
cbs = []


def make_cb(i):
    def cb(client, target, large_motor, small_motor, lightbar, user_data):
        try:
            f = fb[i]
            f["large"], f["small"] = int(large_motor), int(small_motor)
            f["r"] = int(getattr(lightbar, "Red", 0))
            f["g"] = int(getattr(lightbar, "Green", 0))
            f["b"] = int(getattr(lightbar, "Blue", 0))
            f["set"] = True
        except Exception:
            pass
        fb_event.set()
    return cb


def init():
    n = C.NUM_PADS
    fb[:] = [{"small": 0, "large": 0, "r": 0, "g": 0, "b": 0, "set": False} for _ in range(n)]
    fb_sent[:] = [None] * n
    trig_sent[:] = [None] * n
    pulses[:] = [None] * n
    cbs[:] = [make_cb(i) for i in range(n)]    # trzymaj referencje (GC)
    S.on_remap = fb_event.set


def reset(i):
    """Nowy wirtualny DS4 w slocie: gra jeszcze nic nie ustawiła."""
    fb[i].update(set=False, small=0, large=0)
    fb_sent[i] = None


def pulse(i, kind, force=False):
    """Krótkie potwierdzenie na padzie (wibracja i/lub kolor), nałożone na feedback z gry.
    force: także przy confirm = false (test wibracji z okna)."""
    if (C.CONFIRM or force) and 0 <= i < len(pulses) and S.alive[i]:
        d, small, large, rgb = PULSES[kind]
        pulses[i] = (time.monotonic() + d, small, large, rgb)
        fb_event.set()


def _target(i, now):
    """-> (wartość feedbacku do wysłania, czy w ogóle sterujemy tym padem)."""
    f = fb[i]
    base = (tuple(clamp(f[k], 0, 255) for k in ("small", "large", "r", "g", "b")) if f["set"]
            else (0, 0, *C.IDLE_RGB))
    p = pulses[i]
    if p is not None and now >= p[0]:
        pulses[i] = p = None
    if p is None:
        # bez gry i bez potwierdzeń nie wysyłamy nic (pad zostaje z kolorem nadanym przez sterownik),
        # ale po potwierdzeniu trzeba przywrócić kolor - stąd "fb_sent is not None"
        return base, f["set"] or fb_sent[i] is not None
    return (p[1], p[2], *(p[3] or base[2:])), True


def feedback_thread(send, send_trig=None):
    """send(slot, (mała, duża, r, g, b)) - wysyłka do nadajnika (rx_net.send_feedback);
    send_trig(slot, (L2 początek, siła, R2 początek, siła), powtórzenia) - opór spustów (rx_net.send_triggers)."""
    _loop(send, send_trig)


def triggers_tick(i, now, send_trig):
    """Opór spustów z profilu slotu: zmiana -> od razu (kilka razy), stały opór -> odświeżanie co TRIG_REFRESH_S.
    Bez oporu i bez wcześniejszej zmiany nic nie wysyła - pad zostaje z domyślnymi spustami."""
    vals = trigger_values(S.remap_maps[i]["triggers"])
    last = trig_sent[i]
    if last is None and not any(vals):
        return
    if last is None or last[0] != vals:
        trig_sent[i] = (vals, now)
        send_trig(i, vals, TRIG_REPEAT)
    elif any(vals) and now - last[1] >= TRIG_REFRESH_S:
        trig_sent[i] = (vals, now)
        send_trig(i, vals, 1)


def _loop(send, send_trig=None):
    """Wysyła feedback przy zmianie, co 0.5 s w trakcie wibracji (odświeża jej timeout u nadajnika)
    i co 5 s w spoczynku (zgubiony pakiet nie zostawi złego koloru lightbara)."""
    while not S.stop.is_set():
        ends = [p[0] for p in pulses if p is not None]
        fb_event.wait(max(0.0, min([0.5] + [e - time.monotonic() for e in ends])))
        fb_event.clear()
        now = time.monotonic()
        for i in range(C.NUM_PADS):
            if not S.alive[i]:
                fb_sent[i] = pulses[i] = trig_sent[i] = None
                continue
            if send_trig is not None:
                triggers_tick(i, now, send_trig)
            val, ours = _target(i, now)
            if not ours:
                continue
            last = fb_sent[i]
            if last is None or last[0] != val or now - last[1] >= (0.5 if val[0] or val[1] else 5.0):
                fb_sent[i] = (val, now)
                send(i, val)
