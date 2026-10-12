"""Nadajnik: kalibracja żyroskopu z okna receivera (CMD_GYRO_CAL, pakiet calres, zapis per pad, start bez kalibracji
dla zapisanego pada) i gałki bez strefy martwej na czas pomiaru dryfu (CMD_RAW_STICKS, sam wraca po czasie)."""
import asyncio, json, os, sys, tempfile, time, types
sys.path.insert(0, sys.argv[1])
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

ok = lambda m: print("OK  ", m)
cod = Codec()
sent = []
sender_net.emit = sender_ctrl.net.emit = calib.net.emit = lambda t, i, *v: sent.append((t, i, v))
E = lambda t, c, v: types.SimpleNamespace(type=t, code=c, value=v)
MAC = "aa:bb:cc:dd:ee:10"

# ---------- protokół ----------
pkt = cod.pack(T_CALRES, 1, CAL_OK, CALF_STORED, 1, -2, 3, 4, -5, 6, 1_700_000_000)
assert cod.parse(pkt)[0] == T_CALRES and BODY[T_CALRES].unpack_from(pkt, 4)[2:5] == (1, -2, 3)
assert cod.parse(cod.pack(proto.T_INFO, 1, 1, 2, 3))[0] == proto.T_INFO, "zwykły info bez zmian"
assert cod.parse(cod.pack(T_CMD, 0, CMD_GYRO_CAL, 3))[0] == T_CMD and proto.SENDER_VERSION >= 9
ok("protokół: calres jako dłuższy wariant info, polecenia kalibracji i surowych gałek")

# ---------- GyroCal: wynik z próbek ----------
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
ok("GyroCal: średnia z leżącego pada, ruch ponad calibrate_max_range i za mało próbek odrzucone")

# ---------- zapis per pad ----------
assert calib.save(MAC.upper(), [1.04, 2, 3]) and calib.stored(MAC)["bias"] == [1.0, 2, 3]
assert not calib.save("usb-0000:00:14.0-1/input0", [1, 2, 3]), "pad bez MAC (USB) - nie zapisujemy"
data = json.load(open(C.GYRO_CAL_FILE))
assert list(data) == [MAC] and data[MAC]["t"] > 0
calib._saved = None
assert calib.stored(MAC)["bias"] == [1.0, 2.0, 3.0], "odczyt z pliku po restarcie"
open(C.GYRO_CAL_FILE, "w").write("{zły json")
calib._saved = None
assert calib.stored(MAC) is None, "zepsuty plik = brak kalibracji, bez wyjątku"
calib._saved = None
os.remove(C.GYRO_CAL_FILE)
ok("zapis kalibracji per MAC (atomowo), USB bez zapisu, zepsuty plik ignorowany")


# ---------- polecenia z okna ----------
class Dev:
    """Urządzenie pada / ruchu: absinfo z wartości osi; ruch: raporty co 5 ms z danym gyro."""
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
assert c.pad[0] == 128 and c.pad[1] == 128 and c.pad[4] == 128, "strefa martwa nadajnika przycina do środka"
sender_control.on_command(0, CMD_GYRO_CAL, 3)
assert sent[-1][0] == T_CALRES and sent[-1][2][0] == CAL_FEW, "pad bez czujnika ruchu - od razu CAL_FEW"
sent.clear()
sender_control.on_command(0, CMD_GYRO_CAL, 0)
assert sent == [(T_CALRES, 0, (CAL_STATUS, 0, 0, 0, 0, 0, 0, 0, 0))], sent
ok("polecenie kalibracji: stan (arg 0) i odpowiedź dla pada bez czujnika ruchu")


async def raw():
    sent.clear()
    sender_control.on_command(0, CMD_RAW_STICKS, 1)
    assert c.stick_dz == 0 and c.pad[0] == 130 and c.pad[1] == 126 and c.pad[4] == 131, c.pad
    await asyncio.sleep(0.02)
    assert any(t == T_STATE and v[0] == 130 for t, _i, v in sent), "stan z prawdziwym położeniem wysłany od razu"
    await asyncio.sleep(1.05)
    assert c.stick_dz == C.STICK_DZ and c.pad[0] == 128, "po czasie strefa martwa wraca sama"
    sender_control.on_command(0, CMD_RAW_STICKS, 200)
    assert c._raw_timer.when() - asyncio.get_running_loop().time() <= proto.RAW_STICKS_MAX_S + 0.1, "limit czasu"
    sender_control.on_command(0, CMD_RAW_STICKS, 0)
    assert c.stick_dz == C.STICK_DZ and c._raw_timer is None and c.pad[0] == 128
    c.cancel_timers()

asyncio.run(raw())
ok("surowe gałki: bez strefy martwej od razu (odczyt stanu), powrót po czasie albo poleceniem 0, limit 30 s")


async def gyro_cal():
    sent.clear()
    m = Dev(gyro=(1200, -800, 300), n=400)
    c.motion_dev = m
    C.CAL_S, C.AUTOBIAS, C.MOTION_MIN_DT = 0, False, 0
    task = asyncio.create_task(sender_streams.stream_motion(c))
    sender_control.on_command(0, CMD_GYRO_CAL, 1)
    sender_control.on_command(0, CMD_GYRO_CAL, 1)              # powtórka UDP - nie zaczyna od nowa
    t0 = time.monotonic()
    while not any(t == T_CALRES and v[0] == CAL_OK for t, _i, v in sent) and time.monotonic() - t0 < 3:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    c.cancel_timers()
    res = [v for t, _i, v in sent if t == T_CALRES]
    assert [v[0] for v in res] == [CAL_RUNNING, CAL_OK], res
    st, flags, *rest = res[-1]
    assert flags & CALF_STORED and tuple(rest[:3]) == tuple(rest[3:6]) == (1200, -800, 300) and rest[6] > 0, res[-1]
    states = [v for t, _i, v in sent if t == T_STATE]
    assert states[0][14:17] == (1200, -800, 300), "w trakcie kalibracji ruch idzie dalej (wykres w oknie)"
    assert states[-1][14:17] == (0, 0, 0), f"po kalibracji gyro leżącego pada = 0: {states[-1][14:17]}"
    assert calib.stored(MAC)["bias"] == [1200, -800, 300]

asyncio.run(gyro_cal())
ok("kalibracja z okna: odpowiedź 'trwa', potem wynik z zapisanym i bieżącym biasem, gyro po niej zerowe")


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
    assert states and states[0][14:17] == (0, 0, 0), "zapisany bias od razu, bez 1 s kalibracji przy starcie"
    c2.send_ident()
    ver = next(v for t, _i, v in sent if t == T_VERSION)
    assert ver[0] == proto.SENDER_VERSION and ver[1] & CAP_CALIB, ver

asyncio.run(startup())
ok("start pada z zapisaną kalibracją: bez kalibracji startowej, ruch od pierwszego raportu; wersja z CAP_CALIB")
