"""Receiver: pad master - jeden pad, któremu wolno więcej (np. gdy grają dzieci).

Master to pad o identyfikatorze rx_settings.cur.master.pad (MAC, ten sam klucz co profil w remap.json), więc
zostaje masterem niezależnie od slotu i kolejności łączenia. Ustawia się go w oknie (korona na karcie pada,
Ustawienia -> Master) albo na padzie: mute przytrzymany CLAIM_S sekund (claim_hold; pad mignie na złoto).

  allowed(i, funkcja)  czy pad i może użyć funkcji z MASTER_FEATURES (tryb konsoli, PS, skróty...);
                       funkcja niezastrzeżona albo brak mastera w ustawieniach = każdy może;
                       master ustawiony, ale nie podłączony -> wg when_absent (all / nobody)
  mute + przycisk      akcje systemowe (pauza, zamknięcie gry, uśpienie, wyłączenie PC); groźne wymagają
                       przytrzymania HOLD_S (krótka wibracja = "trzymaj dalej"), przycisk z kombinacji nie trafia do gry

Działa na SUROWYCH wejściach (przed remapowaniem), jak skróty PS. Wątek sieci woła tylko on_state() i filter_inputs();
akcje systemowe idą osobnym wątkiem.
"""
import threading
from dataclasses import replace

import rx_feedback as F
import rx_settings
import rx_state as S
import rx_winapi as W
from rx_buttons import BIT, BUTTON_CODES, DIRS, pressed_inputs
from rx_config import C

CLAIM_S = 3.0          # mute przytrzymany tyle (bez innych przycisków) = ten pad zostaje masterem
HOLD_S = 1.2           # tyle trzeba trzymać mute + przycisk dla akcji "groźnych" (zamknięcie gry, uśpienie, wyłączenie)
# programy, których "zamknij grę" nie zamyka (pulpit, Steam, ten receiver)
NO_CLOSE = frozenset({"explorer.exe", "steam.exe", "steamwebhelper.exe", "python.exe", "pythonw.exe",
                      "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe", "lockapp.exe"})


class Slot:
    __slots__ = ("mute", "gen", "used", "prev", "sup", "combo", "timer")

    def __init__(self):
        self.mute = self.used = False
        self.gen = 0              # numer naciśnięcia mute / kombinacji - spóźniony timer nic nie zrobi
        self.prev = frozenset()   # wejścia wciśnięte w poprzednim stanie
        self.sup = frozenset()    # wejścia zużyte w kombinacji (nie trafiają do gry do puszczenia)
        self.combo = None         # przycisk trzymanej kombinacji czekającej na HOLD_S
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


def set_master(i, save=True):
    """Pad w slocie i zostaje masterem (i = None: bez mastera). -> (ok, komunikat)."""
    pad = "" if i is None else S.profile_key(i)
    if pad == rx_settings.cur.master.pad:
        return True, "Bez zmian"
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
    """Każda zmiana surowego stanu pada, przed przeliczeniem wyjścia (jak rx_hotkeys.on_state)."""
    h = slots[i]
    now = frozenset(pressed_inputs(mask, hx, hy, lt, rt))
    mute = "mute" in now
    if mute and not h.mute:
        h.mute, h.used = True, False
        h.gen += 1
        if rx_settings.cur.master.claim_hold:
            _timer(h, CLAIM_S, _claim, i, h.gen)
    elif not mute and h.mute:
        h.mute = False
        h.gen += 1                          # unieważnia czekające timery (claim, akcja z przytrzymaniem)
        h.combo = None
    if h.mute:
        for b in sorted(now - h.prev - {"mute"}):
            h.used = True                   # mute użyty w kombinacji - to już nie "przejęcie mastera"
            action = _action_for(b)
            if action is None or not check(i, "actions"):
                continue
            h.sup |= {b}
            if rx_settings.MASTER_ACTIONS[action][1]:
                h.gen += 1
                h.combo = b
                F.pulse(i, "hold")          # "trzymaj dalej"
                _timer(h, HOLD_S, _held, i, h.gen, b, action)
            else:
                _run(_do, i, action)
    if h.combo and h.combo not in now:
        h.combo = None                      # puszczony przed czasem - bez akcji
        h.gen += 1
    h.sup = h.sup & now                     # zużyte wejście wraca do gry dopiero po puszczeniu
    h.prev = now


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


def _claim(i, gen):
    h = slots[i]
    if h.gen != gen or not h.mute or h.used or not rx_settings.cur.master.claim_hold:
        return
    h.used = True
    ok, msg = set_master(i)
    if not ok:
        print(f"Controller {i}: master not saved: {msg}")
    elif msg == "Bez zmian":
        F.pulse(i, "master")


def _held(i, gen, button, action):
    h = slots[i]
    if h.gen != gen or h.combo != button or not h.mute:
        return
    h.combo = None
    _do(i, action)


# ---------------- akcje ----------------
def _do(i, action):
    try:
        if action == "close_game":
            exe = S.game or W.foreground_exe()
            if not exe or exe in NO_CLOSE:
                print(f"Controller {i}: close game - foreground is {exe or 'nothing'}, not closing")
                F.pulse(i, "denied")
                return
            print(f"Controller {i}: close game ({exe})")
        print(f"Controller {i}: master action {action}")
        F.pulse(i, "action")                 # przed uśpieniem / wyłączeniem - potem pad może już nie dostać pakietu
        if action == "pause":
            W.send_keys(W.VK_ESCAPE)
        elif action == "close_game":
            W.close_foreground()
        elif action == "sleep":
            W.sleep_pc()
        elif action == "shutdown":
            W.shutdown_pc()
    except Exception as e:
        print(f"Controller {i}: master action {action} failed: {e!r}")
