"""Receiver: feedback do pada (wibracja, lightbar) z gry przez wirtualny pad (ViGEm / HIDMaestro) + krótkie potwierdzenia skrótów,
oraz efekty spustów z aktywnego profilu (gra przez DS4 ich nie ustawi - robi to nadajnik raportem HID): opór, klik,
wibracja spustu i wibracja z gry na spustach (liczona na nadajniku z tych samych pakietów wibracji). Funkcja mastera
"triggers" (Ustawienia -> Master) = efekty tylko na padzie mastera, pozostałe pady mają zwykłe spusty.

Lightbar: kolor z gry obowiązuje tylko, gdy na pierwszym planie jest gra (rx_games.in_game: nie pulpit, Steam,
launcher) i pad ma "Gra może zmienić kolor" (PadLight.game). Poza grą i przy wyłączonej opcji - kolor z ustawień pada
(stały / wg baterii, rx_lightbar). Steam ustawia kolor wirtualnego pada także na pulpicie - wcześniej przykrywał
własny kolor aż do odłączenia pada. Kolor z PC jest pamiętany, więc po powrocie do gry wraca od razu. Pierwszy taki
kolor trafia do dziennika - inaczej "mój kolor nie działa" bez śladu. Niska bateria miga
na czerwono także nad kolorem gry; potwierdzenia (np. złoty błysk mastera) zawsze na wierzchu."""
import threading
import time

import rx_events as ev
import rx_lightbar as L
import rx_settings
import rx_state as S
from proto import CAP_TRIG_FX, TRIG2_OFF, clamp
from rx_config import C
from rx_i18n import _
from rx_shape import legacy_values, trigger_values

_games = None          # rx_games (importuje ten moduł) - leniwie, przy pierwszym użyciu


def in_game():
    global _games
    if _games is None:
        import rx_games
        _games = rx_games
    return _games.in_game()


FX_SENDER = 8          # od tej wersji nadajnik zna trig2 (z bitem CAP_TRIG_FX w pakiecie version)

TRIG_REFRESH_S = 2.0   # efekty spustów odświeżane co tyle (pad po ponownym połączeniu / zgubiony pakiet)
TRIG_REPEAT = 3        # zmiana efektów wysyłana tyle razy (UDP)
GAME_RUMBLE_STALE_S = 3.0   # wibracja z gry bez nowego raportu silników dłużej niż tyle -> 0. Gry w trakcie wibracji
                            # wysyłają raporty co klatkę; ostatni "silniki 0" (wejście do menu, koniec jazdy) bywa
                            # zgubiony (HIDMaestro nadpisuje stare pakiety) i pad wibrował bez końca

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
trig_sent = []    # (efekty spustów, czas) ostatnio wysłane per slot; None = nic (pad ma domyślne spusty)
cbs = []


def make_cb(i):
    """Callback ViGEm (wątek ViGEm) dla slotu i. Nazwy parametrów jak w vgamepad.dummy_callback - vgamepad porównuje
    sygnaturę z nazwami i inną odrzuca (TypeError = brak wibracji z gier). led_number to kolor lightbara
    DS4_LIGHTBAR_COLOR (rejestracja przez rx_ds4.register_feedback) albo liczba bez znaczenia (rejestracja przez
    vgamepad, który deklaruje ten argument jako bajt) - wtedy kolor gry jest nieznany i lightbar zostaje własny."""
    def cb(client, target, large_motor, small_motor, led_number, user_data):
        try:
            f = fb[i]
            f["large"], f["small"] = int(large_motor), int(small_motor)
            f["t"] = time.monotonic()
            if hasattr(led_number, "Red"):
                f["r"], f["g"], f["b"] = int(led_number.Red), int(led_number.Green), int(led_number.Blue)
                f["note"] = f["note"] or not f["rgb"]
                f["rgb"] = True
            f["set"] = True
        except Exception:
            pass
        fb_event.set()
    return cb


def game(i, large=None, small=None, rgb=None):
    """Wibracja / kolor lightbara z gry dla slotu i (wątek backendu wirtualnego pada); None = bez zmiany tej części.
    Raport wyjściowy DualSense niesie silniki i kolor osobno, z własnymi flagami ważności."""
    try:
        f = fb[i]
    except IndexError:
        return
    if large is not None:
        f["large"], f["small"] = int(large), int(small or 0)
        f["t"] = time.monotonic()
    if rgb is not None:
        f["r"], f["g"], f["b"] = (int(c) for c in rgb)
        f["note"] = f["note"] or not f["rgb"]
        f["rgb"] = True
    f["set"] = True
    fb_event.set()


def stop_game_rumble(why):
    """Program na pierwszym planie się zmienił (wyjście z gry, alt-tab): wibracja z gry -> 0. Gra, która kończy się
    w trakcie wibracji (np. Forza Horizon przy wyjściu), nie wysyła już "silniki 0", a receiver odświeżał ostatnią
    wartość co 0.5 s - pad wibrował bez końca. Gra, która wróci na pierwszy plan, sama wyśle nową wibrację."""
    for i, f in enumerate(fb):
        if f["large"] or f["small"]:
            f["large"] = f["small"] = 0
            print(f"Controller {i}: game rumble stopped ({why})")
            fb_event.set()


def init():
    n = C.NUM_PADS
    fb[:] = [{"small": 0, "large": 0, "r": 0, "g": 0, "b": 0, "set": False, "rgb": False, "note": False,
              "t": 0.0}
             for _ in range(n)]
    fb_sent[:] = [None] * n
    trig_sent[:] = [None] * n
    pulses[:] = [None] * n
    cbs[:] = [make_cb(i) for i in range(n)]    # trzymaj referencje (GC)
    S.on_remap = fb_event.set


rx_settings.on_change(lambda _old, _new: fb_event.set())    # nowy kolor / tryb lightbara działa od razu


def reset(i):
    """Nowy / odłączony wirtualny DS4 w slocie: gra jeszcze nic nie ustawiła (bez init() - nic do czyszczenia)."""
    if i < len(fb):
        fb[i].update(set=False, small=0, large=0, rgb=False, note=False)
        fb_sent[i] = None


def pulse(i, kind, force=False):
    """Krótkie potwierdzenie na padzie (wibracja i/lub kolor), nałożone na feedback z gry.
    force: także przy confirm = false (test wibracji z okna)."""
    if (C.CONFIRM or force) and 0 <= i < len(pulses) and S.alive[i]:
        d, small, large, rgb = PULSES[kind]
        pulses[i] = (time.monotonic() + d, small, large, rgb)
        fb_event.set()


def is_low(i, low_pct):
    """Bateria znana, nie ładuje się i jest na progu albo poniżej (ten sam test co karta pada w oknie)."""
    pct, flags = S.batt_info[i]
    return pct <= 100 and not flags & 1 and pct <= low_pct


def _note_game_color(i):
    """Pierwszy kolor lightbara z gry / programu na PC po utworzeniu wirtualnego pada - wpis w dzienniku."""
    f = fb[i]
    f["note"] = False
    rgb = tuple(clamp(f[k], 0, 255) for k in ("r", "g", "b"))
    game = rx_settings.cur.lightbar.get(S.profile_key(i)).game
    playing = in_game()
    print(f"Controller {i}: lightbar colour {L.rgb_hex(rgb)} set by a program on the PC ({S.game})"
          + ("" if game and playing else " - ignored (own colour always)" if not game else " - used only in a game"))
    text = (_("{pad}: gra ustawiła kolor lightbara {hex} - ma pierwszeństwo przed kolorem z ustawień "
              "(Ustawienia → Lightbar → „Gra może zmienić kolor”)") if game and playing else
            _("{pad}: program na PC ustawił kolor lightbara {hex} - pominięty (zawsze kolor z ustawień)") if not game else
            _("{pad}: program na PC ustawił kolor lightbara {hex} - poza grą świeci kolor z ustawień, w grze ten"))
    ev.info(text.format(pad=ev.pad_name(i), hex=L.rgb_hex(rgb)))


def _target(i, now):
    """-> (wartość feedbacku do wysłania, czy w ogóle sterujemy tym padem, czy lightbar miga)."""
    f = fb[i]
    cfg = rx_settings.cur.lightbar
    light = cfg.get(S.profile_key(i))
    motors = tuple(clamp(f[k], 0, 255) for k in ("small", "large")) if f["set"] else (0, 0)
    if f["set"] and f["rgb"] and light.game and in_game():   # gra ma pierwszeństwo (kolor tylko, gdy go znamy), chyba
                                                             # że pad ma "zawsze mój kolor" albo to nie gra (Steam)
        rgb, ours = tuple(clamp(f[k], 0, 255) for k in ("r", "g", "b")), True
    else:
        rgb, ours = L.own_color(light, S.batt_info[i][0], cfg.low_pct, C.IDLE_RGB)
        ours = ours or f["set"]
    base = (*motors, *rgb)
    blink = L.blinking(light, is_low(i, cfg.low_pct))
    if blink:
        base = (*base[:2], *L.blink_rgb(base[2:], now))
    p = pulses[i]
    if p is not None and now >= p[0]:
        pulses[i] = p = None
    if p is None:
        # bez gry, bez własnego koloru i bez potwierdzeń nie wysyłamy nic (pad zostaje z kolorem nadanym przez
        # sterownik), ale po potwierdzeniu trzeba przywrócić kolor - stąd "fb_sent is not None"
        return base, ours or blink or fb_sent[i] is not None, blink
    return (p[1], p[2], *(p[3] or base[2:])), True, blink


def feedback_thread(send, send_trig=None):
    """send(slot, (mała, duża, r, g, b)) - wysyłka do nadajnika (rx_net.send_feedback);
    send_trig(slot, wartości trig2 (12) albo trig (4), powtórzenia) - efekty spustów (rx_net.send_triggers)."""
    _loop(send, send_trig)


def sender_fx(i):
    """Nadajnik tego pada zna trig2 (klik, wibracja spustu, wibracja z gry)."""
    info = S.sender_info[i]
    return info is not None and info[0] >= FX_SENDER and bool(info[1] & CAP_TRIG_FX)


def trigger_target(i):
    """Wartości do wysłania: trig2 dla nowego nadajnika, trig (sam opór) dla starszego; bez efektów, gdy
    efekty spustów są tylko dla mastera, a to nie master."""
    import rx_master        # rx_master importuje ten moduł
    vals = trigger_values(S.remap_maps[i]["triggers"]) if rx_master.allowed(i, "triggers") else TRIG2_OFF
    return vals if sender_fx(i) else legacy_values(vals)


def triggers_tick(i, now, send_trig):
    """Efekty spustów z profilu slotu: zmiana -> od razu (kilka razy), stałe efekty -> odświeżanie co TRIG_REFRESH_S.
    Pierwszy przebieg po połączeniu pada wysyła stan zawsze, także "bez efektów": nadajnik i pad mogą mieć efekty
    z poprzedniego uruchomienia receivera (restart po aktualizacji, zamknięcie w trakcie gry z klikiem na R2) - bez
    tego spust zostawał twardy, gdy profil w użyciu po starcie nie ma efektów. Potem bez efektów nic nie odświeża."""
    vals = trigger_target(i)
    last = trig_sent[i]
    if last is None or last[0] != vals:
        trig_sent[i] = (vals, now)
        send_trig(i, vals, TRIG_REPEAT)
    elif any(vals) and now - last[1] >= TRIG_REFRESH_S:
        trig_sent[i] = (vals, now)
        send_trig(i, vals, 1)


def release_triggers(send_trig):
    """Zamykanie receivera (wątek feedbacku już stoi): pady, którym ustawiliśmy efekty spustów, dostają "bez efektów".
    Nadajnik trzyma efekt do następnej zmiany, więc bez tego opór / klik na spuście zostawał po wyjściu z receivera
    (także przed restartem i aktualizacją) aż do rozłączenia pada. Nadajnik ma też własnego strażnika (sender_rumble),
    który zwalnia spusty, gdy odświeżenia z PC ustaną (uśpienie / awaria PC)."""
    for i, last in enumerate(trig_sent):
        if last is not None and any(last[0]):
            send_trig(i, TRIG2_OFF if len(last[0]) == len(TRIG2_OFF) else (0, 0, 0, 0), TRIG_REPEAT)
            trig_sent[i] = None


def _loop(send, send_trig=None):
    """Wysyła feedback przy zmianie, co 0.5 s w trakcie wibracji (odświeża jej timeout u nadajnika)
    i co 5 s w spoczynku (zgubiony pakiet nie zostawi złego koloru lightbara)."""
    blink = False
    while not S.stop.is_set():
        now = time.monotonic()
        waits = [0.5] + [e - now for e in (p[0] for p in pulses if p is not None)]   # 0.5 s: też wygasanie
        if blink:
            waits.append(L.next_edge(now) + 0.002)
        fb_event.wait(max(0.0, min(waits)))
        fb_event.clear()
        now = time.monotonic()
        blink = False
        for i in range(C.NUM_PADS):
            if not S.alive[i]:
                fb_sent[i] = pulses[i] = trig_sent[i] = None
                continue
            if send_trig is not None:
                triggers_tick(i, now, send_trig)
            if fb[i]["note"]:
                _note_game_color(i)
            f = fb[i]
            if (f["large"] or f["small"]) and now - f["t"] > GAME_RUMBLE_STALE_S:
                f["large"] = f["small"] = 0
                print(f"Controller {i}: no rumble report from the game for {GAME_RUMBLE_STALE_S:g}s - rumble stopped")
            val, ours, b = _target(i, now)
            blink = blink or b
            if not ours:
                continue
            last = fb_sent[i]
            if last is None or last[0] != val or now - last[1] >= (0.5 if val[0] or val[1] else 5.0):
                fb_sent[i] = (val, now)
                send(i, val)
