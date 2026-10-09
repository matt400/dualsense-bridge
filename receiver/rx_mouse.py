"""Receiver: touchpad jako mysz (ruch z resztą ułamkową, tap LPM / PPM, przewijanie dwoma palcami, przycisk).

Flagi pakietu T_MOUSE: bit0 = tap (LPM), bit1 = przewijanie (dx, dy to suma przesunięć palców), bit2 = tap PPM,
bity 4-7 = numer tapu 1..15 (sender powtarza pakiet tapu, numer pozwala wykonać klik tylko raz)."""
import time

import rx_winapi as W
from rx_config import C

MOVE, LDOWN, LUP, RDOWN, RUP, WHEEL, HWHEEL = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0800, 0x1000
mrem = []     # reszty ułamkowe ruchu per slot
srem = []     # reszty ułamkowe przewijania per slot
sens = []     # czułość myszy per slot (zmieniana skrótem PS + d-pad góra/dół)
mdown = []    # wciśnięty przycisk per slot: 0 = żaden, 1 = lewy, 2 = prawy
last_tap = [] # (numer, czas) ostatniego wykonanego tapu per slot
BTN = {1: (LDOWN, LUP), 2: (RDOWN, RUP)}


def init(n):
    mrem[:] = [[0.0, 0.0] for _ in range(n)]
    srem[:] = [[0.0, 0.0] for _ in range(n)]
    sens[:] = [C.MOUSE_SENS] * n
    mdown[:] = [0] * n
    last_tap[:] = [(0, 0.0)] * n


def reset(i):
    """Puszcza przycisk i zeruje reszty ułamkowe (rozłączenie pada, zmiana trybu)."""
    mouse_button(i, 0)
    mrem[i] = [0.0, 0.0]
    srem[i] = [0.0, 0.0]


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
    fx = dx * sens[i] + mrem[i][0]
    fy = dy * sens[i] + mrem[i][1]
    sx, sy = int(fx), int(fy)
    mrem[i][0], mrem[i][1] = fx - sx, fy - sy
    if sx or sy:
        W.send_mouse(MOVE, sx, sy)


def mouse_button(i, btn):
    """Fizyczny klik touchpada: 0 = puść, 1 = lewy, 2 = prawy. Zmiana przycisku w trakcie puszcza poprzedni."""
    cur = mdown[i]
    if btn == cur:
        return
    if cur:
        W.send_mouse(BTN[cur][1])
    if btn in BTN:
        W.send_mouse(BTN[btn][0])
    mdown[i] = btn if btn in BTN else 0


def mouse_click(i, down):
    mouse_button(i, 1 if down else 0)
