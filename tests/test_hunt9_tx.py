"""Nadajnik: regresje z dziewiątego przeglądu - Bluetooth z okna odporny na każdy błąd uruchomienia busctl /
bluetoothctl (operacja nie zostaje "w toku" na zawsze) i autobias żyroskopu nie psuje świeżej kalibracji z okna.
    python3 tests/test_hunt9_tx.py sender"""
import asyncio, sys, types
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
C.GYRO_CAL_FILE = ""
from evdev import ecodes as ec
import sender_bt as SB
import sender_calib as calib
import sender_ctrl
import sender_streams
from proto import (BT_CONNECT, BT_SCAN_START, BTR_BUSY, BTR_DENIED, BTR_FAILED, BTR_RUNNING, BTS_DENIED, BTS_ERROR,
                   BTS_OK)

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

MAC = bytes(range(1, 7))


# ---------- 1. busctl nie daje się uruchomić (brak prawa wykonania, fork bez pamięci) ----------
def failing(exc):
    async def run(argv, timeout):
        raise exc
    return run

async def bt_ops():
    b = SB.Bluetooth(run=failing(PermissionError(13, "Permission denied")), emit=lambda *a: None)
    await b.refresh()
    check(b.state == BTS_DENIED, "refresh: PermissionError -> stan 'brak uprawnień' zamiast wyjątku w pętli")
    b.start_op(BT_CONNECT, 0, MAC)
    await b.busy
    check(not b.running, "operacja po błędzie busctl nie zostaje 'w toku' (wcześniej running = True na zawsze)")
    check(b.last[1] == BTR_DENIED, f"wynik operacji: brak uprawnień ({b.last[1]})")
    b.start_op(BT_CONNECT, 0, MAC)
    check(b.last[1] == BTR_RUNNING, "następna operacja startuje (nie BTR_BUSY)")
    await b.busy

    b = SB.Bluetooth(run=failing(OSError(11, "Resource temporarily unavailable")), emit=lambda *a: None)
    await b.refresh()
    check(b.state == BTS_ERROR, "refresh: inny OSError -> stan 'błąd', bez wyjątku")
    b.start_op(BT_CONNECT, 0, MAC)
    await b.busy
    check(not b.running and b.last[1] == BTR_FAILED, "inny OSError: operacja zakończona wynikiem 'nie udało się'")
    check(BTR_BUSY != b.last[1], "bez fałszywego 'trwa inna operacja'")

    # skan: bluetoothctl nie daje się uruchomić -> wynik dla okna zamiast wyjątku w zadaniu
    b = SB.Bluetooth(run=failing(OSError()), emit=lambda *a: None)

    async def ok_refresh():
        b.state, b.adapter = BTS_OK, "/org/bluez/hci0"
    b.refresh = ok_refresh
    orig = SB.asyncio.create_subprocess_exec

    async def no_exec(*a, **k):
        raise PermissionError(13, "Permission denied")
    SB.asyncio.create_subprocess_exec = no_exec
    try:
        await b.start_scan(5)
        err = None
    except Exception as e:
        err = e
    finally:
        SB.asyncio.create_subprocess_exec = orig
    check(err is None and b.last == (BT_SCAN_START, BTR_DENIED, b""), f"skan bez uprawnień: wynik w oknie ({err!r}, {b.last})")
    check(b.scan is None and not b.scanning(), "skan nie jest zgłaszany jako trwający")

asyncio.run(bt_ops())


# ---------- 2. autobias po kalibracji z okna nie bierze próbek sprzed niej ----------
sender_ctrl.net.emit = calib.net.emit = lambda *a: None
C.CAL_S = 0
clock = [1000.0]
real_time = sender_streams.time
sender_streams.time = types.SimpleNamespace(monotonic=lambda: clock[0])
E = lambda t, c, v: types.SimpleNamespace(type=t, code=c, value=v)
c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:19", None, None, None)
c.request_send = lambda: None
c.mark_event = lambda e: None
seen = {}

class Motion:
    async def async_read_loop(self):
        def frame(g):
            return [E(ec.EV_ABS, 3, g), E(ec.EV_ABS, 4, 0), E(ec.EV_ABS, 5, 0), E(ec.EV_SYN, ec.SYN_REPORT, 0)]
        for k in range(150):                    # 0.6 s w ręce: gyro 500-700 (drżenie w zakresie autobias_range)
            clock[0] += 0.004
            for e in frame(500 + (k % 5) * 50):
                yield e
        c.cal_request = 3                       # kalibracja z okna: pad odłożony, prawdziwy bias = 100
        for _ in range(int(3.2 / 0.004)):
            clock[0] += 0.004
            for e in frame(100):
                yield e
        seen["after"] = c.gyro_bias[0]
        for _ in range(int(1.5 / 0.004)):       # pad dalej leży - autobias tylko potwierdza bias
            clock[0] += 0.004
            for e in frame(100):
                yield e
        seen["later"] = c.gyro_bias[0]

c.motion_dev = Motion()
asyncio.run(sender_streams.stream_motion(c))
sender_streams.time = real_time
check(abs(seen["after"] - 100) < 1, f"zaraz po kalibracji bias = zmierzony (było 348 - próbki sprzed kalibracji): {seen['after']:.0f}")
check(abs(seen["later"] - 100) < 1, f"po kolejnym oknie autobiasu bias bez zmian: {seen['later']:.0f}")

if fails:
    print("FAIL", len(fails), "błędów")
    sys.exit(1)
print("WSZYSTKO OK")
