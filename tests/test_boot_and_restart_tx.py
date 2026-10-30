"""Sender: guards compared with time.monotonic() work right after boot; a restarted motion stream keeps the autobias-corrected gyro bias.
    python3 tests/test_boot_and_restart_tx.py sender"""
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_net as net
import sender_streams as ST
import sender_update as UPD
import sender_wol as W

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- 6: guards compared with time.monotonic() work right after boot ----------
real_mono = time.monotonic
time.monotonic = lambda: 5.0           # 5 s of uptime: shorter than every guard below
try:
    check(UPD._last == float("-inf") and 5.0 - UPD._last >= UPD.REPEAT_GUARD_S, "update: the first request is not inside the repeat guard")
    check(W._last_sent == float("-inf") and 5.0 - W._last_sent >= W.REPEAT_GUARD_S, "WoL: the first send is not inside the repeat guard")
    check(net.last_pong == float("-inf"), "net: no pong yet = -inf")
    check(not W.receiver_alive(), "WoL: a receiver that never answered is not 'alive' at a small uptime")
    check(not (5.0 - net.last_pong < net.ALIVE_S), "net: not alive before the first pong at a small uptime")
finally:
    time.monotonic = real_mono
src = open(os.path.join(sys.argv[1], "sender_ctrl.py"), encoding="utf-8").read()
check('self.powering_off = float("-inf")' in src, "Ctrl.powering_off starts at -inf (power-off retry guard)")

# ---------- 7: a restarted motion stream keeps the autobias-corrected gyro bias ----------
stored_table = {"bias": [10.0, 20.0, 30.0]}
real_stored = ST.calib.stored
ST.calib.stored = lambda key: stored_table
try:
    pad = types.SimpleNamespace(idx=0, key="aa:bb:cc:dd:ee:01", gyro_bias=[0.0, 0.0, 0.0], bias_known=False)
    check(ST._start_cal(pad, 0.0) is None and pad.gyro_bias == [10.0, 20.0, 30.0] and pad.bias_known, "first start: the saved bias is applied")
    pad.gyro_bias[0] += 3.5                       # what autobias learned since
    check(ST._start_cal(pad, 1.0) is None and pad.gyro_bias == [13.5, 20.0, 30.0], f"stream restart: the learned bias stays ({pad.gyro_bias})")
finally:
    ST.calib.stored = real_stored

sys.exit(1 if fails else 0)
