"""Sender: the firmware version from feature report 0x20 picks the vibration flag (v2 from 2.21, as in hid-playstation).
    python3 tests/test_rumble_fw_tx.py sender"""
import os
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_rumble as R

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

answer: dict[str, object] = {}


def fake_ioctl(fd, request, buf, mutate):
    """The pad's answer to HIDIOCGFEATURE: report id + the version (little endian) at its offset, or an error."""
    if isinstance(answer.get("error"), OSError):
        raise answer["error"]
    assert request == R._hidiocgfeature(R.FW_SIZE) and mutate and len(buf) == R.FW_SIZE and buf[0] == R.FW_REPORT
    buf[0] = answer["id"]
    buf[R.FW_VERSION_AT], buf[R.FW_VERSION_AT + 1] = answer["ver"] & 0xFF, answer["ver"] >> 8
    return 0


R.fcntl.ioctl = fake_ioctl


def fw(major, minor, report_id=R.FW_REPORT):
    answer.clear(); answer.update(id=report_id, ver=(major << 8) | minor)
    return R.firmware_version(3)


check(fw(2, 21) == 0x0215, "version read little endian from its offset (2.21 = 0x0215)")
check(fw(2, 20) == 0x0214 and fw(3, 0) == 0x0300, "other versions read the same way")
check(fw(2, 21, report_id=0x05) is None, "another report id in the answer (another pad) -> unknown")
answer.clear(); answer["error"] = OSError(13, "EACCES")
check(R.firmware_version(3) is None, "ioctl fails (no access) -> unknown")


class Pad:
    """An evdev pad without force feedback: only the hidraw way is opened."""
    path = "/dev/input/event0"
    def capabilities(self): return {}


node = os.path.join(tempfile.mkdtemp(), "hidraw0")
open(node, "wb").close()
for (major, minor), want in (((2, 20), False), ((2, 21), True), ((2, 33), True)):
    fw(major, minor)
    r = R.Rumble(0, Pad(), "hidraw", hidraw=(node, True))
    check(r.v2 is want and r.fw == (major << 8) | minor, f"firmware {major}.{minor}: vibration {'v2' if want else 'v1'} ({r.v2})")
    r.close()
answer.clear(); answer["error"] = OSError(5, "EIO")
r = R.Rumble(0, Pad(), "hidraw", hidraw=(node, True))
check(r.v2 is None, "firmware unknown -> both flags, like the kernel driver")
r.close()

sys.exit(1 if fails else 0)
