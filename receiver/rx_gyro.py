"""Receiver: gyro as a mouse - smooth cursor movement driven by the pad's rotation speed.

rx_state.recompute computes the velocity [px/s] on every state packet and stores it in the pad state as "gyro_vel".
This thread integrates that velocity every ~2 ms, independently of the packet rhythm, so the cursor glides
instead of jumping in 4 ms batches of movement.
"""
import time

import rx_state as S
import rx_winapi as W
from rx_mouse import MOVE
from rx_winapi import boost_thread

STEP_S = 0.002
MAX_DT = 0.02          # a longer gap (thread sleep, stall) must not turn into a cursor jump


def mouse_thread() -> None:
    """Integrate each pad's gyro velocity into whole-pixel mouse moves.

    Sleeps on S.gyro_wake while no pad has a velocity. Otherwise, every STEP_S it multiplies the velocity by the elapsed
    time (capped at MAX_DT) and adds the fractional remainder left from the previous step. Only the integer part is
    sent as a relative mouse move; the fraction is carried over, so slow movements are not lost to rounding.
    """
    boost_thread()
    rem = [[0.0, 0.0] for _ in S.states]       # sub-pixel remainder per pad
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
