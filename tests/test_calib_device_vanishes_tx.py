"""Sender: a motion device lost in the middle of a calibration from the window is reported (CAL_FEW).
    python3 tests/test_calib_device_vanishes_tx.py sender"""
import sys, time, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_calib as calib
import sender_ctrl
import sender_net
import sender_state
import sender_streams as streams
import sender_supervisor as sup
from proto import CAL_FEW, T_CALRES

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- calibration: the motion device vanishes while the pad is calibrating ----------
sent = []
sender_net.emit = sender_ctrl.net.emit = calib.net.emit = lambda t, i, *v: sent.append((t, i, v))
sender_state.saved_cal = None


class Dev:
    path = "/dev/input/event91"
    name = "Wireless Controller"
    def absinfo(self, code):
        return types.SimpleNamespace(value=0)
    def active_keys(self):
        return []
    def grab(self):
        pass


c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:20", Dev(), None, None)
c.cal_request = 2
stream = streams._MotionStream.__new__(streams._MotionStream)
stream.c, stream.cal = c, None
stream.autobias = types.SimpleNamespace(reset=lambda now: None)
stream._start_user_calibration(time.monotonic())
check(c.cal_request == 0 and c.cal_running, "starting a calibration zeroes cal_request and marks it running")
sent.clear()
sup._reset_lost_stream(c, "motion")
check(any(t == T_CALRES and v[0] == CAL_FEW for t, _i, v in sent), "motion lost mid-calibration: CAL_FEW goes to the window")
check(not c.cal_running and c.cal_request == 0, "the flags are cleared afterwards")
sent.clear()
sup._reset_lost_stream(c, "motion")
check(not any(t == T_CALRES for t, _i, _v in sent), "no calibration pending: nothing is reported")

c.cal_running = True
stream.cal = types.SimpleNamespace(user=True, feed=lambda now, gyro: (proto.CAL_OK, [1.0, 2.0, 3.0]))
real_finish, calib.finish = calib.finish, lambda c_, result: None
try:
    stream.sample = [0] * 12
    stream._advance_calibration(5.0)
finally:
    calib.finish = real_finish
check(not c.cal_running and stream.cal is None, "a finished calibration clears the running flag")

sys.exit(1 if fails else 0)
