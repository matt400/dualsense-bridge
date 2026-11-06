"""Sender: gyro calibration from the receiver window (CMD_GYRO_CAL, calres packet, per-pad storage, start without calibration
for a stored pad) and sticks without a dead zone during the drift measurement (CMD_RAW_STICKS, reverts by itself after a while)."""
import asyncio, json, os, sys, tempfile, time, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
TMP = tempfile.mkdtemp()
C.GYRO_CAL_FILE = os.path.join(TMP, "gyro_cal.json")
from evdev import ecodes as ec
import proto
import sender_calib as calib
import sender_control
import sender_ctrl
import sender_net
import sender_state
import sender_streams
from proto import (BODY, CAL_FEW, CAL_MOVED, CAL_OK, CAL_RUNNING, CAL_STATUS, CALF_STORED, CAP_CALIB, CMD_GYRO_CAL,
                   CMD_RAW_STICKS, T_CALRES, T_CMD, T_STATE, T_VERSION, Codec)

from chk import ok
cod = Codec()
sent = []
sender_net.emit = sender_ctrl.net.emit = calib.net.emit = lambda t, i, *v: sent.append((t, i, v))
E = lambda t, c, v: types.SimpleNamespace(type=t, code=c, value=v)
MAC = "aa:bb:cc:dd:ee:10"

# ---------- protocol ----------
pkt = cod.pack(T_CALRES, 1, CAL_OK, CALF_STORED, 1, -2, 3, 4, -5, 6, 1_700_000_000)
assert cod.parse(pkt)[0] == T_CALRES and BODY[T_CALRES].unpack_from(pkt, 4)[2:5] == (1, -2, 3)
assert cod.parse(cod.pack(proto.T_INFO, 1, 1, 2, 3))[0] == proto.T_INFO, "plain info unchanged"
assert cod.parse(cod.pack(T_CMD, 0, CMD_GYRO_CAL, 3))[0] == T_CMD and proto.SENDER_VERSION >= 9
ok("protocol: calres as a longer variant of info, calibration and raw stick commands")

# ---------- GyroCal: result from samples ----------
t0 = 100.0
cal = calib.GyroCal(t0, 1.0, user=True)
for k in range(50):
    assert cal.feed(t0 + k * 0.01, (1000 + k % 3, -500, 200)) is None
st, bias = cal.feed(t0 + 1.01, (0, 0, 0))
assert st == CAL_OK and abs(bias[0] - 1001) < 1 and bias[1:] == [-500, 200], (st, bias)
cal = calib.GyroCal(t0, 1.0, user=True)
for k in range(50):
    cal.feed(t0 + k * 0.01, (0 if k % 2 else C.CAL_MAX_RANGE + 10, 0, 0))
assert cal.feed(t0 + 2, (0, 0, 0)) == (CAL_MOVED, None)
cal = calib.GyroCal(t0, 1.0, user=True)
cal.feed(t0, (0, 0, 0))
assert cal.feed(t0 + 2, (0, 0, 0)) == (CAL_FEW, None)
ok("GyroCal: mean of a lying pad, motion above calibrate_max_range and too few samples rejected")

# ---------- per-pad storage ----------
assert calib.save(MAC.upper(), [1.04, 2, 3]) and calib.stored(MAC)["bias"] == [1.0, 2, 3]
assert not calib.save("usb-0000:00:14.0-1/input0", [1, 2, 3]), "pad without a MAC (USB) - not stored"
data = json.load(open(C.GYRO_CAL_FILE))
assert list(data) == [MAC] and data[MAC]["t"] > 0
calib._saved = None
assert calib.stored(MAC)["bias"] == [1.0, 2.0, 3.0], "read from the file after a restart"
open(C.GYRO_CAL_FILE, "w").write("{zły json")
calib._saved = None
assert calib.stored(MAC) is None, "broken file = no calibration, no exception"
calib._saved = None
os.remove(C.GYRO_CAL_FILE)
ok("calibration stored per MAC (atomically), USB not stored, a broken file ignored")


# ---------- commands from the window ----------
class Dev:
    """Pad / motion device: absinfo from the axis values; motion: reports every 5 ms with the given gyro."""
    path = "/dev/input/event90"

    def __init__(self, axes=None, gyro=None, n=0):
        self.axes, self.gyro, self.n = axes or {}, gyro, n

    def absinfo(self, c):
        return types.SimpleNamespace(value=self.axes.get(c, 0))

    def active_keys(self):
        return []

    async def async_read_loop(self):
        for _k in range(self.n):
            for code, v in enumerate((0, 0, 8192, *self.gyro)):
                yield E(ec.EV_ABS, code, v)
            yield E(ec.EV_SYN, ec.SYN_REPORT, 0)
            await asyncio.sleep(0.005)
        await asyncio.sleep(3600)


c = sender_ctrl.Ctrl(0, MAC, Dev({0: 130, 1: 126, 3: 128, 4: 131}), None, None)
sender_state.active[c.key] = c
c.resync()
assert c.pad[0] == 128 and c.pad[1] == 128 and c.pad[4] == 128, "the sender dead zone clamps to the centre"
sender_control.on_command(0, CMD_GYRO_CAL, 3)
assert sent[-1][0] == T_CALRES and sent[-1][2][0] == CAL_FEW, "a pad without a motion sensor - CAL_FEW at once"
sent.clear()
sender_control.on_command(0, CMD_GYRO_CAL, 0)
assert sent == [(T_CALRES, 0, (CAL_STATUS, 0, 0, 0, 0, 0, 0, 0, 0))], sent
ok("calibration command: state (arg 0) and the reply for a pad without a motion sensor")


async def raw():
    sent.clear()
    sender_control.on_command(0, CMD_RAW_STICKS, 1)
    assert c.stick_dz == 0 and c.pad[0] == 130 and c.pad[1] == 126 and c.pad[4] == 131, c.pad
    await asyncio.sleep(0.02)
    assert any(t == T_STATE and v[0] == 130 for t, _i, v in sent), "state with the real position sent at once"
    await asyncio.sleep(1.05)
    assert c.stick_dz == C.STICK_DZ and c.pad[0] == 128, "po czasie strefa martwa wraca sama"
    sender_control.on_command(0, CMD_RAW_STICKS, 200)
    assert c._raw_timer.when() - asyncio.get_running_loop().time() <= proto.RAW_STICKS_MAX_S + 0.1, "time limit"
    sender_control.on_command(0, CMD_RAW_STICKS, 0)
    assert c.stick_dz == C.STICK_DZ and c._raw_timer is None and c.pad[0] == 128
    c.cancel_timers()

asyncio.run(raw())
ok("raw sticks: no dead zone at once (state read), back after a while or with command 0, 30 s limit")


async def gyro_cal():
    sent.clear()
    m = Dev(gyro=(1200, -800, 300), n=400)
    c.motion_dev = m
    C.CAL_S, C.AUTOBIAS, C.MOTION_MIN_DT = 0, False, 0
    task = asyncio.create_task(sender_streams.stream_motion(c))
    sender_control.on_command(0, CMD_GYRO_CAL, 1)
    sender_control.on_command(0, CMD_GYRO_CAL, 1)              # UDP repeat - does not start over
    t0 = time.monotonic()
    while not any(t == T_CALRES and v[0] == CAL_OK for t, _i, v in sent) and time.monotonic() - t0 < 3:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    c.cancel_timers()
    res = [v for t, _i, v in sent if t == T_CALRES]
    assert [v[0] for v in res] == [CAL_RUNNING, CAL_OK, CAL_OK, CAL_OK], res
    assert res[1] == res[2] == res[3], "result 3x with the same values (UDP; the receiver drops repeats)"
    st, flags, *rest = res[-1]
    assert flags & CALF_STORED and tuple(rest[:3]) == tuple(rest[3:6]) == (1200, -800, 300) and rest[6] > 0, res[-1]
    states = [v for t, _i, v in sent if t == T_STATE]
    assert states[0][14:17] == (1200, -800, 300), "during calibration the motion keeps flowing (chart in the window)"
    assert states[-1][14:17] == (0, 0, 0), f"after calibration the gyro of a lying pad = 0: {states[-1][14:17]}"
    assert calib.stored(MAC)["bias"] == [1200, -800, 300]

asyncio.run(gyro_cal())
ok("calibration from the window: reply 'in progress', then the result with the stored and current bias, gyro zero afterwards")


async def startup():
    sent.clear()
    C.CAL_S = 1.0
    c2 = sender_ctrl.Ctrl(1, MAC, None, Dev(gyro=(1200, -800, 300), n=20), None)
    task = asyncio.create_task(sender_streams.stream_motion(c2))
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    c2.cancel_timers()
    states = [v for t, _i, v in sent if t == T_STATE]
    assert states and states[0][14:17] == (0, 0, 0), "stored bias at once, no 1 s calibration at start"
    c2.send_ident()
    ver = next(v for t, _i, v in sent if t == T_VERSION)
    assert ver[0] == proto.SENDER_VERSION and ver[1] & CAP_CALIB, ver

asyncio.run(startup())
ok("pad start with a stored calibration: no start-up calibration, motion from the first report; version with CAP_CALIB")
