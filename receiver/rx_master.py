"""Receiver: pad master - jeden pad, któremu wolno więcej (np. gdy grają dzieci).

Master to pad o identyfikatorze rx_settings.cur.master.pad (MAC, ten sam klucz co profil w remap.json), więc
zostaje masterem niezależnie od slotu i kolejności łączenia. Ustawia się go w oknie (korona na karcie pada,
Ustawienia -> Master) albo na padzie: PS + R3 przytrzymane CLAIM_S sekund (claim_hold; pad mignie na złoto).

  allowed(i, funkcja)  czy pad i może użyć funkcji z MASTER_FEATURES (tryb konsoli, PS, skróty...);
                       funkcja niezastrzeżona albo brak mastera w ustawieniach = każdy może;
                       master ustawiony, ale nie podłączony -> wg when_absent (all / nobody)
  PS + przycisk        akcje systemowe (pauza, zamknięcie gry, uśpienie, wyłączenie PC); groźne wymagają
                       przytrzymania HOLD_S (krótka wibracja = "trzymaj dalej"); przycisk z kombinacji nie trafia do gry,
                       a PS po puszczeniu też nie (jak przy skrótach PS z rx_hotkeys). Przyciski skrótów PS (Share,
                       D-pad, klik touchpada) i R3 (przejęcie mastera) nie są dostępne dla akcji.

Działa na SUROWYCH wejściach (przed remapowaniem), jak skróty PS. Wątek sieci woła tylko on_state() i filter_inputs();
akcje systemowe idą osobnym wątkiem.
"""
from rx_i18n import _
import threading
from dataclasses import replace

import rx_events as ev
import rx_feedback as F
import rx_mouse
import rx_settings
import rx_state as S
import rx_winapi as W
from rx_buttons import BIT, BUTTON_CODES, DIRS, pressed_inputs
from rx_config import C

MOD = "ps"             # modyfikator akcji mastera
CLAIM_BUTTON = "r3"    # PS + R3 trzymane CLAIM_S = ten pad zostaje masterem
CLAIM_S = 3.0
HOLD_S = 1.2           # tyle trzeba trzymać PS + przycisk dla akcji "groźnych" (zamknięcie gry, uśpienie, wyłączenie)
# programy, których "zamknij grę" nie zamyka (pulpit, Steam, ten receiver)
NO_CLOSE = frozenset({"explorer.exe", "steam.exe", "steamwebhelper.exe", "python.exe", "pythonw.exe",
                      "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe", "lockapp.exe"})


class Slot:
    __slots__ = ("mod", "gen", "prev", "sup", "combo", "timer", "fresh")

    def __init__(self):
        self.mod = False          # PS trzymany
        self.fresh = True         # pierwszy stan po połączeniu: wejścia już trzymane to nie nowe naciśnięcia
        self.gen = 0              # numer naciśnięcia PS / kombinacji - spóźniony timer nic nie zrobi
        self.prev = frozenset()   # wejścia wciśnięte w poprzednim stanie
        self.sup = frozenset()    # wejścia zużyte w kombinacji (nie trafiają do gry do puszczenia)
        self.combo = None         # przycisk trzymanej kombinacji czekającej na HOLD_S / CLAIM_S
        self.timer = None


slots = []


def init(n):
    slots[:] = [Slot() for _ in range(n)]


def reset(i):
    if slots[i].timer is not None:
        slots[i].timer.cancel()
    slots[i] = Slot()


# ---------------- kto jest masterem ----------------
def master_slot():
    """Slot podłączonego mastera albo None (brak mastera w ustawieniach albo nie jest podłączony)."""
    pad = rx_settings.cur.master.pad
    if not pad:
        return None
    return next((i for i in range(len(slots)) if (S.alive[i] or S.want[i].is_set()) and S.profile_key(i) == pad), None)


def is_master(i):
    pad = rx_settings.cur.master.pad
    return bool(pad) and S.profile_key(i) == pad


def allowed(i, feature):
    m = rx_settings.cur.master
    if not m.pad or feature not in m.only_master:
        return True
    ms = master_slot()
    return m.when_absent == "all" if ms is None else i == ms


def check(i, feature):
    """allowed() + czerwone mignięcie na padzie, gdy nie wolno (dziecko wie, że to nie awaria)."""
    if allowed(i, feature):
        return True
    print(f"Controller {i}: {feature} - only the master pad can do that")
    F.pulse(i, "denied")
    return False


def mouse_follows_master(force=False):
    """Touchpad jako mysz idzie za masterem: pad master = mysz, pozostałe = dotyk DS4. Master nie jest podłączony
    (albo force po zdjęciu mastera) -> mysz wg mouse_pads z konfiguracji. Bez mastera w ustawieniach nic nie zmienia
    (ręczne przełączenie PS + klik touchpada zostaje). Woła się po zmianie mastera i po połączeniu / odejściu pada."""
    if not rx_settings.cur.master.pad and not force:
        return
    ms = master_slot()
    for i in range(len(slots)):
        on = i == ms if ms is not None else i in C.MOUSE_PADS
        if rx_mouse.set_mode(i, on):
            print(f"Controller {i}: touchpad -> {'MOUSE' if on else 'DS4 touch'} mode (follows the master pad)")


def master_label(pad):
    """"Pad 2 (aa:bb:...)" dla podłączonego mastera, sam klucz dla niepodłączonego, "brak" bez mastera."""
    if not pad:
        return _("brak")
    i = next((i for i in range(len(slots)) if S.pad_id[i] and S.profile_key(i) == pad), None)
    return f"{ev.pad_name(i)} ({pad})" if i is not None else pad


def _master_changed(old, new):
    if old.master.pad != new.master.pad:
        mouse_follows_master(force=True)
        if slots:                      # nie przy wczytaniu settings.json na starcie (przed init)
            ev.info(_("Master: {pad}").format(pad=master_label(new.master.pad)))


rx_settings.on_change(_master_changed)


def set_master(i, save=True):
    """Pad w slocie i zostaje masterem (i = None: bez mastera). -> (ok, komunikat)."""
    if i is not None and S.pad_id[i] is None:
        return False, _("Pad {n} nie przysłał jeszcze identyfikatora (MAC) - spróbuj za chwilę").format(n=i + 1)
    pad = "" if i is None else S.profile_key(i)
    if pad == rx_settings.cur.master.pad:
        return True, _("Bez zmian")
    ok, msg = rx_settings.update(C.SETTINGS_PATH if save else None,
                                 master=replace(rx_settings.cur.master, pad=pad))
    if ok and i is not None:
        print(f"Controller {i}: is now the master pad ({pad})")
        F.pulse(i, "master")
    return ok, msg


# ---------------- wątek sieci ----------------
def _action_for(button):
    return next((a for a, b in rx_settings.cur.master.actions.items() if b == button), None)


def on_state(i, mask, hx, hy, lt, rt):
    """Każda zmiana surowego stanu przycisków pada, przed przeliczeniem wyjścia (jak rx_hotkeys.on_state)."""
    h = slots[i]
    now = frozenset(pressed_inputs(mask, hx, hy, lt, rt))
    if h.fresh:
        h.fresh, h.prev, h.mod = False, now, MOD in now
        return
    mod = MOD in now
    if mod != h.mod:
        h.mod = mod
        h.gen += 1                          # unieważnia czekające timery (przejęcie, akcja z przytrzymaniem)
        h.combo = None
    if h.mod:
        for b in sorted(now - h.prev - {MOD}):
            if b == CLAIM_BUTTON and rx_settings.cur.master.claim_hold:
                _consume(i, h, b)
                _timer(h, CLAIM_S, _claim, i, h.gen, b)
                continue
            action = _action_for(b)
            if action is None:
                continue
            _consume(i, h, b)
            if not check(i, "actions"):
                continue
            if rx_settings.MASTER_ACTIONS[action][1]:
                F.pulse(i, "hold")          # "trzymaj dalej"
                _timer(h, HOLD_S, _held, i, h.gen, b, action)
            else:
                _run(_do, i, action)
    if h.combo and h.combo not in now:
        h.combo = None                      # puszczony przed czasem - bez akcji
        h.gen += 1
    h.sup = h.sup & now                     # zużyte wejście wraca do gry dopiero po puszczeniu
    h.prev = now


def _consume(i, h, b):
    """Przycisk zużyty w kombinacji: nie idzie do gry, a PS po puszczeniu też nie (i nie ma Game Bar z długiego PS)."""
    import rx_hotkeys                       # rx_hotkeys importuje rx_master - import tutaj
    rx_hotkeys.mark_used(i)
    h.sup |= {b}
    h.gen += 1
    h.combo = b


def filter_inputs(i, mask, hx, hy):
    """Wejścia dla gry bez tych zużytych w kombinacjach mastera i bez PS, gdy temu padowi go nie wolno."""
    h = slots[i]
    for b in h.sup:
        if b in BUTTON_CODES:
            mask &= ~BIT[BUTTON_CODES[b]]
        else:
            dx, dy = DIRS[b]
            if dx and hx == dx:
                hx = 0
            if dy and hy == dy:
                hy = 0
    if mask & BIT[BUTTON_CODES["ps"]] and not allowed(i, "ps_game"):
        mask &= ~BIT[BUTTON_CODES["ps"]]
    return mask, hx, hy


def _timer(h, delay, fn, *args):
    if h.timer is not None:
        h.timer.cancel()
    h.timer = threading.Timer(delay, fn, args)
    h.timer.daemon = True
    h.timer.start()


def _run(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _claim(i, gen, button):
    h = slots[i]
    if h.gen != gen or h.combo != button or not h.mod or not rx_settings.cur.master.claim_hold:
        return
    h.combo = None
    ok, msg = set_master(i)
    if not ok:
        print(f"Controller {i}: master not saved: {msg}")
    elif msg == _("Bez zmian"):
        F.pulse(i, "master")


def _held(i, gen, button, action):
    h = slots[i]
    if h.gen != gen or h.combo != button or not h.mod:
        return
    h.combo = None
    _do(i, action)


# ---------------- akcje ----------------
def _do(i, action):
    try:
        hwnd = 0
        if action == "close_game":
            # okno sprawdzane i zamykane to TO SAMO okno: S.game bywa sprzed pół sekundy (rx_games), a "aktywne okno"
            # w chwili zamykania mogło już być pulpitem lub Steamem (alt-tab w trakcie przytrzymania)
            hwnd, exe = W.foreground_window()
            if not hwnd or not exe or exe in NO_CLOSE:
                print(f"Controller {i}: close game - foreground is {exe or 'nothing'}, not closing")
                F.pulse(i, "denied")
                return
            print(f"Controller {i}: close game ({exe})")
        print(f"Controller {i}: master action {action}")
        F.pulse(i, "action")                 # przed uśpieniem / wyłączeniem - potem pad może już nie dostać pakietu
        if action == "pause":
            W.send_keys(W.VK_ESCAPE)
        elif action == "close_game":
            W.close_window(hwnd)
        elif action in ("sleep", "shutdown"):
            import rx_console                # rx_console importuje rx_master - import tutaj
            rx_console.power_off(_("wyłączanie komputera") if action == "shutdown" else _("usypianie komputera"))
            W.sleep_pc() if action == "sleep" else W.shutdown_pc()
    except Exception as e:
        print(f"Controller {i}: master action {action} failed: {e!r}")
        ev.error(_("{pad}: akcja mastera {action} nieudana: {err}").format(pad=ev.pad_name(i), action=action, err=repr(e)))
