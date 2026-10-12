"""Receiver: touchpad jako mysz (ruch z resztą ułamkową, tap LPM / PPM, przewijanie dwoma palcami, przycisk).

Flagi pakietu T_MOUSE: bit0 = tap (LPM), bit1 = przewijanie (dx, dy to suma przesunięć palców), bit2 = tap PPM,
bity 4-7 = numer tapu 1..15 (sender powtarza pakiet tapu, numer pozwala wykonać klik tylko raz).

Stabilizacja fizycznego kliku (ustawienia w oknie: rx_settings.MouseConfig). Dociskanie palca przesuwa jego środek
na czujniku, więc kursor "uciekał" spod celu:
  rewind  ruch z ostatnich click_rewind_ms przed wciśnięciem jest cofany (te same kroki wstecz - akceleracja
          Windows działa na nie tak samo, więc kursor wraca w to samo miejsce)
  freeze  przez click_freeze_ms po wciśnięciu i po puszczeniu ruch jest pomijany
  drag    przy trzymanym kliku kursor rusza dopiero po drag_threshold jednostkach (świadome przeciąganie)
"""
import time
from collections import deque

import rx_settings
import rx_state as S
import rx_winapi as W
from rx_config import C

MOVE, LDOWN, LUP, RDOWN, RUP, WHEEL, HWHEEL = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0800, 0x1000
mrem = []     # reszty ułamkowe ruchu per slot
srem = []     # reszty ułamkowe przewijania per slot
sens = []     # czułość myszy per slot (zmieniana skrótem PS + d-pad góra/dół)
mdown = []    # wciśnięty przycisk per slot: 0 = żaden, 1 = lewy, 2 = prawy
last_tap = [] # (numer, czas) ostatniego wykonanego tapu per slot
hist = []     # wysłane ruchy kursora (czas, dx, dy) z ostatnich chwil per slot - do cofnięcia przy kliknięciu
frozen = []   # do kiedy (monotonic) ruch kursora jest pomijany per slot
drag = []     # ruch [jednostki touchpada] od wciśnięcia kliku; None = kursor rusza normalnie
BTN = {1: (LDOWN, LUP), 2: (RDOWN, RUP)}


def init(n):
    mrem[:] = [[0.0, 0.0] for _ in range(n)]
    srem[:] = [[0.0, 0.0] for _ in range(n)]
    sens[:] = [rx_settings.cur.mouse.sens] * n
    mdown[:] = [0] * n
    last_tap[:] = [(0, 0.0)] * n
    hist[:] = [deque() for _ in range(n)]
    frozen[:] = [0.0] * n
    drag[:] = [None] * n


def _settings_changed(old, new):
    if new.mouse.sens != old.mouse.sens:
        sens[:] = [new.mouse.sens] * len(sens)     # suwak w oknie ustawia wszystkie pady (PS + ↑/↓ zmienia jeden)


rx_settings.on_change(_settings_changed)


def reset(i):
    """Puszcza przycisk i zeruje reszty ułamkowe (rozłączenie pada, zmiana trybu)."""
    mouse_button(i, 0)
    mrem[i] = [0.0, 0.0]
    srem[i] = [0.0, 0.0]
    hist[i].clear()
    frozen[i] = 0.0
    drag[i] = None


def set_mode(i, on):
    """Touchpad pada i: mysz (on) albo dotyk DS4. Puszcza przycisk i zeruje gest, gra dostaje puszczony klik
    touchpada. -> True, gdy tryb się zmienił."""
    if S.mouse_mode[i] == on:
        return False
    S.mouse_mode[i] = on
    reset(i)
    with S.lock:
        S.states[i]["tp"] = False
        S.states[i]["ver"] += 1
    S.notify(i)
    return True


def mouse_rel(i, dx, dy, flags):
    if flags & 5:
        tid, now = flags >> 4, time.monotonic()
        if tid and tid == last_tap[i][0] and now - last_tap[i][1] < 0.5:
            return                       # powtórka tego samego tapu (redundancja na wypadek zgubionego pakietu)
        last_tap[i] = (tid, now)
        if flags & 1:    # tap = lewy klik
            W.send_click(LDOWN, LUP)
        if flags & 4:    # tap dwoma palcami = prawy klik
            W.send_click(RDOWN, RUP)
    if not (dx or dy):
        return
    if flags & 2:    # przewijanie: 120 = jedna "ząbkowa" działka kółka, drobniejsze wartości = płynne przewijanie
        k = C.SCROLL_SENS if C.SCROLL_NATURAL else -C.SCROLL_SENS
        fx = dx * -k + srem[i][0]          # poziomo: palce w prawo = przewiń w prawo (w trybie "natural" odwrotnie)
        fy = dy * k + srem[i][1]
        sx, sy = int(fx), int(fy)
        srem[i][0], srem[i][1] = fx - sx, fy - sy
        if sy:
            W.send_mouse(WHEEL, data=sy)
        if sx:
            W.send_mouse(HWHEEL, data=sx)
        return
    now = time.monotonic()
    if now < frozen[i]:
        mrem[i] = [0.0, 0.0]
        return                              # tuż po wciśnięciu / puszczeniu kliku: drżenie docisku palca
    if drag[i] is not None:
        drag[i] += abs(dx) + abs(dy)
        if drag[i] < rx_settings.cur.mouse.drag_threshold:
            return                          # klik trzymany bez wyraźnego ruchu - kursor stoi na celu
        drag[i] = None                      # świadome przeciąganie - od teraz zwykły ruch
    fx = dx * sens[i] + mrem[i][0]
    fy = dy * sens[i] + mrem[i][1]
    sx, sy = int(fx), int(fy)
    mrem[i][0], mrem[i][1] = fx - sx, fy - sy
    if sx or sy:
        W.send_mouse(MOVE, sx, sy)
        h = hist[i]
        h.append((now, sx, sy))
        limit = now - rx_settings.cur.mouse.click_rewind_ms / 1000
        while h and h[0][0] < limit:
            h.popleft()


def _rewind(i, now):
    """Cofa ruch kursora z ostatnich click_rewind_ms (kroki w odwrotnej kolejności i z przeciwnym znakiem)."""
    h, limit = hist[i], now - rx_settings.cur.mouse.click_rewind_ms / 1000
    while h:
        t, sx, sy = h.pop()
        if t >= limit:
            W.send_mouse(MOVE, -sx, -sy)
    mrem[i] = [0.0, 0.0]


def mouse_button(i, btn):
    """Fizyczny klik touchpada: 0 = puść, 1 = lewy, 2 = prawy. Zmiana przycisku w trakcie puszcza poprzedni."""
    cur = mdown[i]
    if btn == cur:
        return
    now = time.monotonic()
    m = rx_settings.cur.mouse
    if btn in BTN and not cur:
        _rewind(i, now)                     # kursor wraca tam, gdzie był przed dociśnięciem palca
        drag[i] = 0
    if cur:
        W.send_mouse(BTN[cur][1])
    if btn in BTN:
        W.send_mouse(BTN[btn][0])
    else:
        drag[i] = None
    mdown[i] = btn if btn in BTN else 0
    frozen[i] = now + m.click_freeze_ms / 1000


def mouse_click(i, down):
    mouse_button(i, 1 if down else 0)
