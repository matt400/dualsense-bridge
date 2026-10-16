"""Receiver: współdzielony stan padów. Moduły używają go jako `S.xxx` (listy powstają w init())."""
import threading
import time
import traceback

import rx_padcal
from proto import SeqFilter
from rx_buttons import apply_remap, apply_sticks, pressed_inputs, profile_size
from rx_config import C
from rx_shape import gyro_mouse_velocity, gyro_rates, gyro_stick, merge_triggers, shape_stick, shape_trigger

lock = threading.Lock()
profile_lock = threading.RLock()            # wybór profilu (gra / edytor / skróty / ident) z kilku wątków
stop = threading.Event()
restart = False                            # True = po czystym zamknięciu uruchom receiver od nowa (zapis ustawień w oknie)
seen = SeqFilter(1.0)                      # odrzucanie spóźnionych / zdublowanych pakietów
MACS = [bytes([0, 2, 3, 4, 5, 6 + s]) for s in range(5)]
SENDER_TIMEOUT = 3.0                       # cisza dłuższa niż tyle (3 pingi) = nadajnik offline

peer = None        # adres ostatniego nadajnika (dla feedbacku / pong)
sender_build = None  # (commit, czas commita, stan aktualizacji UPD_*, czas odbioru) z pakietu build nadajnika
sender_update_msg = ""  # powód błędu ostatniej aktualizacji nadajnika (pakiet updmsg)
sender_wol = None  # (MAC komputera, broadcast) - tego użyje Wake-on-LAN nadajnika (pakiet build2); "" = nie zna
last_pkt = 0.0     # kiedy przyszedł ostatni poprawny pakiet - do wskaźnika online
store = None       # RemapStore
dsu_wake = threading.Event()
dsu_active = False              # serwer DSU ma subskrybentów - bez nich zmiany stanu go nie budzą (rx_dsu)
gyro_wake = threading.Event()   # budzi wątek myszy-żyroskopu (rx_gyro)
game = None                     # program na pierwszym planie (małe litery, np. "eldenring.exe") albo None
link_loss = None                # utrata pakietów od nadajnika w ostatniej sekundzie [%] (None = brak danych)

states = wake = want = plugged = last_seen = alive = ignore_until = None
mouse_mode = tp_down = swallow = last_v = last_touch = batt_info = None
pad_id = remap_maps = None
override = active = info = pps = sender_info = None
probe = gyro_cal = None


def new_state():
    return {
        "lx": 128, "ly": 128, "rx": 128, "ry": 128, "lt": 0, "rt": 0,   # gałki/lt/rt/hx/hy/mask: PO remapowaniu
        "hx": 0, "hy": 0, "mask": 0,
        "raw": (0, 0, 0, 0, 0),                                          # (maska, hatx, haty, l2, r2) surowe
        "fwd": (0, 0, 0),                    # (maska, hatx, haty) przekazywane do remapu - bez wejść zużytych przez skróty
        "rawst": (128, 128, 128, 128),                                   # (lx, ly, rx, ry) surowe
        "acc": (0, 0, 0), "gyro": (0, 0, 0),
        "touch": ((0, 0, 0, 0), (0, 0, 0, 0)), "tp": False,
        "rtp": 0,                            # klik touchpada na żywo (0/1/2) także w trybie myszy - widok Test
        "blocked": False,                    # widok Test: gra dostaje neutralny stan, mysz i skróty nie działają
        "batt_dsu": 5, "batt_ds4": 0x1B,
        "ver": 0,
        "ts": 0,                                                         # kiedy (us) przyszła ostatnia zmiana stanu
        "gyro_on": False,                    # gyro aktywne (tryb gałki lub myszy i spełniony warunek "hold")
        "gyro_vel": None,                    # prędkość kursora z gyro [px/s] w trybie myszy, None = wyłączone
        "lat": None,                         # pomiar opóźnienia ostatniej zmiany (rx_net -> rx_ds4 -> rx_latency)
        "blat": None,                        # to samo, tylko dla zmiany przycisku / D-pada / spustu (przycisk -> gra)
    }


def init(store_):
    global states, wake, want, plugged, last_seen, alive, ignore_until, mouse_mode
    global tp_down, swallow, last_v, last_touch, batt_info, pad_id, remap_maps, store
    global override, active, info, pps, sender_info, probe, gyro_cal
    n = C.NUM_PADS
    store = store_
    states = [new_state() for _ in range(n)]
    wake = [threading.Event() for _ in range(n)]     # budzi wątek pada po zmianie stanu
    want = [threading.Event() for _ in range(n)]     # ustawione = wirtualny DS4 ma istnieć
    plugged = [False] * n                            # wirtualny DS4 jest podłączony do ViGEm
    last_seen = [0.0] * n
    alive = [False] * n
    ignore_until = [0.0] * n                         # krótkie "wyciszenie" slotu po pakiecie bye
    mouse_mode = [i in C.MOUSE_PADS for i in range(n)]
    tp_down = [0] * n                                # ostatnia wartość kliku touchpada (0/1/2)
    swallow = [False] * n
    last_v = [None] * n                              # ostatni pakiet stanu / dotyku - powtórki pomijamy
    last_touch = [None] * n
    batt_info = [(255, 0)] * n                       # (% , flagi) - dla okna
    pad_id = [None] * n                              # identyfikator pada z pakietu ident
    active = [store.key_for(None, i) for i in range(n)]   # klucz profilu w użyciu
    remap_maps = [store.get(k) for k in active]
    override = [None] * n                            # profil wybrany ręcznie (PS + d-pad), None = automatycznie
    info = [None] * n                                # (ping ms | None, Hz pada, Hz ruchu, czas) z pakietu info
    pps = [0.0] * n                                  # pakiety stanu na sekundę
    sender_info = [None] * n                         # (wersja nadajnika, sposoby wibracji, powód) z pakietu version; None = stary
    probe = [None] * n                               # pomiar dryfu (rx_drift): lista próbek (lx, ly, rx, ry) albo None
    gyro_cal = [None] * n                            # ostatni pakiet calres: (stan, flagi, zapisany, bieżący, czas, odebrano)


def run_forever(name, fn, *args):
    """Pętla wątku w tle, której nieoczekiwany wyjątek nie wyłączy na stałe: błąd jest wypisywany
    (pierwszy z pełnym śladem), a pętla startuje ponownie po chwili. Kończy się, gdy fn wróci przy S.stop."""
    errors = 0
    while not stop.is_set():
        try:
            fn(*args)
            return
        except Exception as e:
            errors += 1
            if errors == 1:
                print(f"{name} thread error - restarting:\n{traceback.format_exc()}")
            else:
                print(f"{name} thread error #{errors} - restarting: {e!r}")
            stop.wait(min(5.0, 0.2 * errors))


def start_thread(name, fn, *args):
    t = threading.Thread(target=run_forever, args=(name, fn, *args), name=name, daemon=True)
    t.start()
    return t


def sender_online(now=None):
    return (now or time.monotonic()) - last_pkt < SENDER_TIMEOUT


push = None        # rx_ds4.push: raport do wirtualnego DS4 od razu w wątku zmiany (bez rx_ds4 - tylko budzenie)
on_remap = None    # rx_feedback: nowy profil slotu -> od razu wyślij opór spustów (bez czekania na pętlę)


def notify(i):
    """Zmiana stanu slotu i: raport DS4 od razu (rx_ds4.push), serwer DSU wysyła swoim wątkiem."""
    if push is not None:
        push(i)
    else:
        wake[i].set()
    if dsu_active:              # bez klientów DSU: bez ~1000 pustych wybudzeń/s wątku DSU (walczył o GIL z wątkiem sieci)
        dsu_wake.set()


def recompute(s, prof):
    """Przelicza stan wyjściowy z surowych wejść wg profilu (wołać pod lock):
    remap przycisków -> zamiana / odwrócenie gałek -> kształtowanie gałek i spustów -> gyro."""
    tun, g = prof["tuning"], prof["gyro"]
    lt, rt = s["raw"][3:5]
    s["mask"], s["hx"], s["hy"], lt, rt = apply_remap(prof["buttons"], *s["fwd"], lt, rt)
    s["lt"], s["rt"] = shape_trigger(lt, tun.get("l2")), shape_trigger(rt, tun.get("r2"))
    lx, ly, rx, ry = apply_sticks(prof["sticks"], *s["rawst"])
    lx, ly = shape_stick(lx, ly, tun.get("left"))
    rx, ry = shape_stick(rx, ry, tun.get("right"))
    s["gyro_on"] = (bool(g) and g["mode"] != "off" and not s["blocked"]
                    and (g["hold"] == "always" or g["hold"] in pressed_inputs(*s["raw"])))
    s["gyro_vel"] = None
    if s["gyro_on"]:
        rates = gyro_rates(s["gyro"], g, C.GYRO_RES)
        if g["mode"] == "stick":
            rx, ry = gyro_stick(rx, ry, rates, g)
        else:
            s["gyro_vel"] = gyro_mouse_velocity(rates, g)
    s["lx"], s["ly"], s["rx"], s["ry"] = lx, ly, rx, ry


def set_remap(i, prof):
    """Ustaw profil dla slotu i i natychmiast przelicz aktualny stan z surowych wejść."""
    with lock:
        remap_maps[i] = prof
        s = states[i]
        recompute(s, prof)
        s["ver"] += 1
    notify(i)
    if s["gyro_vel"]:
        gyro_wake.set()
    if on_remap is not None:
        on_remap()


def profile_key(i):
    """Klucz profilu pada (bez gry)."""
    return store.key_for(pad_id[i], i)


def game_key(base, exe):
    return f"{base}@{exe}"


def profile_label(key):
    return key.split("@", 1)[1] if "@" in key else "default"


def active_key(i):
    """Profil do użycia: ręcznie wybrany > profil gry na pierwszym planie > profil pada."""
    base = profile_key(i)
    if override[i] and override[i] in store.keys_for(base):
        return override[i]
    if game and C.GAME_PROFILES and store.has(game_key(base, game)):
        return game_key(base, game)
    return base


def effective_profile(i, key, drift=True):
    """Profil do użycia na padzie: profil gry dziedziczy z profilu pada efekty spustów, których sam nie ustawia
    (pozostałe części profilu gry zastępują profil pada w całości, jak dotąd); korekcja dryfu gałek tego pada
    (rx_padcal) działa w każdym profilu jako minimum strefy martwej (drift=False - bez niej: liczba zmian profilu)."""
    prof = store.get(key)
    base = profile_key(i)
    if key != base:
        prof["triggers"] = merge_triggers(store.get(base)["triggers"], prof["triggers"])
    return rx_padcal.apply_drift(prof, rx_padcal.drift(base)) if drift else prof


def profile_changes(i):
    """Liczba zmian profilu w użyciu (karta pada, log) - bez korekcji dryfu, która nie jest częścią profilu."""
    return profile_size(effective_profile(i, active[i], drift=False))


NEUTRAL_OUT = {**new_state(), "ver": None}


def output(i):
    """Stan slotu dla gry (raport DS4, DSU) - wołać pod lock. Zablokowany slot (widok Test) = neutralny stan: puszczone
    przyciski, gałki w środku, bez ruchu i dotyku; bateria i czas próbki jak naprawdę."""
    s = states[i]
    if not s["blocked"]:
        return s
    return {**NEUTRAL_OUT, "batt_dsu": s["batt_dsu"], "batt_ds4": s["batt_ds4"], "ts": s["ts"], "ver": s["ver"]}


def set_blocked(i, on):
    """Widok Test: wyjście slotu do gry neutralne (on) albo z powrotem na żywo. -> True, gdy się zmieniło."""
    with lock:
        s = states[i]
        if s["blocked"] == on:
            return False
        s["blocked"] = on
        recompute(s, remap_maps[i])
        s["lat"] = s["blat"] = None
        s["ver"] += 1
    notify(i)
    if s["gyro_vel"]:
        gyro_wake.set()
    return True


def reload_profile(i):
    """Ustawia profil wg active_key(). -> True, gdy zmienił się klucz profilu."""
    with profile_lock:
        key = active_key(i)
        changed, active[i] = key != active[i], key
        set_remap(i, effective_profile(i, key))
        return changed


def use_profile(i, key):
    """Ręczny wybór profilu (edytor, skrót) do następnej zmiany okna. -> False, gdy profil nie istnieje."""
    with profile_lock:
        if key not in store.keys_for(profile_key(i)):
            return False
        override[i] = key
        reload_profile(i)
        return True


def cycle_profile(i, step):
    """Następny / poprzedni profil pada (ręcznie, do zmiany gry). -> nowy klucz."""
    with profile_lock:
        keys = store.keys_for(profile_key(i))
        cur = active[i] if active[i] in keys else keys[0]
        use_profile(i, keys[(keys.index(cur) + step) % len(keys)])
        return active[i]


def set_game(exe):
    """Zmiana programu na pierwszym planie. -> sloty, którym zmienił się profil."""
    global game
    with profile_lock:
        if exe == game:
            return []
        game = exe
        changed = []
        for i in range(len(states)):
            override[i] = None
            if reload_profile(i):
                changed.append(i)
        return changed


def reset_state(i):
    """Neutralny stan pada (puszczone przyciski, wyśrodkowane drążki)."""
    with lock:
        ver, blocked = states[i]["ver"] + 1, states[i]["blocked"]
        states[i].update(new_state())
        states[i]["ver"], states[i]["blocked"] = ver, blocked
        states[i]["ts"] = time.monotonic_ns() // 1000
    notify(i)
