"""Sender: a calibration request does not outlive a lost motion stream.
    python3 tests/test_calib_lost_stream_tx.py sender"""
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])

import sender_calib
import sender_ctrl
import sender_net as net
import sender_supervisor as sup
from proto import CAL_FEW

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))
TMP = tempfile.mkdtemp()


# ---------- 2. a calibration request does not outlive a lost motion stream ----------
c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
c.cal_request = 5
seen = []
sender_calib.report = lambda c_, status, repeat=False: seen.append(status)
sup.calib.report = sender_calib.report
sup._reset_lost_stream(c, "motion")
check(c.cal_request == 0 and seen == [CAL_FEW], f"lost motion stream: request dropped and the window told ({c.cal_request}, {seen})")

sys.exit(1 if fails else 0)
