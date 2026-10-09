"""Receiver: gyro jako mysz - płynny ruch kursora z prędkości obrotu pada.

Prędkość [px/s] liczy rx_state.recompute (przy każdym pakiecie stanu, w stanie "gyro_vel"); ten wątek całkuje ją
co ~2 ms niezależnie od rytmu pakietów, więc kursor sunie płynnie, a nie skacze o paczki ruchu co 4 ms."""
import time

import rx_state as S
import rx_winapi as W
from rx_mouse import MOVE
from rx_winapi import boost_thread

STEP_S = 0.002
MAX_DT = 0.02          # dłuższa przerwa (uśpienie wątku, zator) nie może dać skoku kursora


def mouse_thread():
    boost_thread()
    rem = [[0.0, 0.0] for _ in S.states]
    last = time.perf_counter()
    while not S.stop.is_set():
        if not any(s["gyro_vel"] for s in S.states):
            S.gyro_wake.wait(0.25)
            S.gyro_wake.clear()
            last = time.perf_counter()
            continue
        time.sleep(STEP_S)
        now = time.perf_counter()
        dt, last = min(now - last, MAX_DT), now
        for i, s in enumerate(S.states):
            v = s["gyro_vel"]
            if not v:
                rem[i] = [0.0, 0.0]
                continue
            fx, fy = v[0] * dt + rem[i][0], v[1] * dt + rem[i][1]
            sx, sy = int(fx), int(fy)
            rem[i] = [fx - sx, fy - sy]
            if sx or sy:
                W.send_mouse(MOVE, sx, sy)
