"""Sender: the in-place motion average (_MotionStream._send_average) gives the same packets as the old list-building version.
    python3 tests/test_motion_average_tx.py sender"""
import random, sys, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_streams as S
from sender_config import C

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

AX, GA = S.MOTION_AXES, S.GYRO_AT


def reference(sums, count, bias, old, pending):
    """The previous implementation, kept here as the oracle: returns (new values, whether a packet is sent)."""
    mean = [t / count for t in sums]
    new = [round(mean[k]) for k in range(GA)] + [round(mean[GA + k] - bias[k]) for k in range(S.GYRO_AXES)]
    send = pending or any(abs(new[k] - old[k]) > (C.DB_ACC if k < GA else C.DB_GYRO) for k in range(AX))
    return new, send


def make(old, bias, pending):
    sent = []
    c = types.SimpleNamespace(motion=old, gyro_bias=bias, pending=pending, mark_event=lambda e: sent.append("mark"),
                              request_send=lambda: sent.append("send"))
    st = S._MotionStream.__new__(S._MotionStream)
    st.c, st.sums, st.count, st._new = c, [0] * AX, 0, [0] * AX
    return st, c, sent


rng = random.Random(7)
bad = 0
for _ in range(3000):
    old = [rng.randint(-300, 300) for _ in range(AX)]
    bias = [rng.uniform(-200, 200) for _ in range(3)]
    pending = rng.random() < 0.1
    count = rng.randint(1, 6)
    sums = [rng.randint(-2000, 2000) * count // 3 for _ in range(AX)]
    want, want_send = reference(sums, count, bias, old, pending)
    st, c, sent = make(list(old), bias, pending)
    st.sums[:], st.count = sums, count
    ident = id(c.motion)
    st._send_average(None)
    expected = want if want_send else old
    if c.motion != expected or bool(sent) != want_send or st.sums != [0] * AX or st.count != 0 or id(c.motion) != ident:
        bad += 1
check(bad == 0, f"3000 random averages: same values, same decision to send, sums reset, list kept ({bad} differences)")

st, c, sent = make([0] * AX, [0.0] * 3, False)
st.sums[:], st.count = [1] * AX, 1
st._send_average(None)
check(c.motion == [0] * AX and not sent, "a change inside the deadband is not sent and does not touch c.motion")

sys.exit(1 if fails else 0)
