"""Sender: blocking calls must not run on the event loop that reads the pads:
  - the rumble backend (a synchronous HID feature request over Bluetooth) is opened in a worker thread, and a pad that
    disappears while it opens does not leak the hidraw descriptor
  - the /dev/input scan (opens every new node) runs in a worker thread, its result is applied on the loop
  - the sysfs adapter scan (btadapter.scan) runs in a worker thread
  - a big BlueZ GetManagedObjects answer (crowded scan) is parsed in a worker thread, a small one inline
python3 tests/test_blocking_calls_tx.py sender"""
import asyncio, json, sys, threading, time, types
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import sender_bt as SB
import sender_supervisor as sup

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

MAIN = threading.get_ident()
SLOW_S = 0.3


async def ticker(counter):
    """Counts loop iterations: a blocked loop does not count."""
    while True:
        await asyncio.sleep(0.01)
        counter[0] += 1


# ---------- 1. rumble backend ----------
class SlowRumble:
    made, closed, thread = [], [], None
    def __init__(self, idx, dev, mode, **kw):
        SlowRumble.thread = threading.get_ident()
        time.sleep(SLOW_S)                       # the firmware request over Bluetooth
        SlowRumble.made.append(self)
    def close(self):
        SlowRumble.closed.append(self)

sup.Rumble = SlowRumble
pad = types.SimpleNamespace(idx=0, pad_dev=None)


async def rumble_cases():
    ticks = [0]
    t = asyncio.create_task(ticker(ticks))
    r = await sup._open_rumble(pad)
    t.cancel()
    check(isinstance(r, SlowRumble) and SlowRumble.thread != MAIN, "rumble backend is created in a worker thread")
    check(ticks[0] >= SLOW_S / 0.01 / 2, f"the loop kept running while it opened ({ticks[0]} ticks in {SLOW_S} s)")

    SlowRumble.made.clear(); SlowRumble.closed.clear()
    task = asyncio.create_task(sup._open_rumble(pad))
    await asyncio.sleep(SLOW_S / 3)              # the thread is still inside the constructor
    task.cancel()
    try:
        await task
        check(False, "cancelled while opening: CancelledError reaches the caller")
    except asyncio.CancelledError:
        check(True, "cancelled while opening: CancelledError reaches the caller")
    await asyncio.sleep(SLOW_S)                  # the thread finishes
    check(len(SlowRumble.made) == 1 and SlowRumble.closed == SlowRumble.made, "the late backend is closed, not leaked")

asyncio.run(rumble_cases())


# ---------- 2. the /dev/input scan ----------
seen = {}
def slow_discover():
    seen["discover_thread"] = threading.get_ident()
    time.sleep(SLOW_S)
    return {"aa:bb:cc:dd:ee:01": {}}
def apply_scan(groups):
    seen["apply_thread"], seen["groups"] = threading.get_ident(), groups
sup.discover, sup._apply_scan = slow_discover, apply_scan


async def scan_case():
    ticks = [0]
    t = asyncio.create_task(ticker(ticks))
    await sup._scan_once()
    t.cancel()
    check(seen["discover_thread"] != MAIN, "discover() runs in a worker thread")
    check(seen["apply_thread"] == MAIN and "aa:bb:cc:dd:ee:01" in seen["groups"], "the scan result is applied on the event loop")
    check(ticks[0] >= SLOW_S / 0.01 / 2, f"the loop kept running during the scan ({ticks[0]} ticks)")

asyncio.run(scan_case())


# ---------- 3. BlueZ answer ----------
def var(t, v):
    return {"type": t, "data": v}

def objects(n):
    out = {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": var("s", "11:22:33:44:55:66"), "Powered": var("b", True)}}}
    for k in range(n):
        mac = f"AA:BB:CC:DD:{k // 256:02X}:{k % 256:02X}"
        out[SB.dev_path("/org/bluez/hci0", mac)] = {"org.bluez.Device1": {
            "Address": var("s", mac), "Name": var("s", f"Device {k}"), "Paired": var("b", False),
            "UUIDs": var("as", ["0000110b-0000-1000-8000-00805f9b34fb"] * 6)}}
    return json.dumps({"type": "a{oa{sa{sv}}}", "data": [out]})

parsed_in = []
real_parse = SB.parse_objects
def spy(text, prefer="", info=None):
    parsed_in.append(threading.get_ident())
    return real_parse(text, prefer, info)
SB.parse_objects = spy


async def bt_cases():
    for n, big in ((3, False), (150, True)):
        text = objects(n)
        check((len(text) > SB.PARSE_OFFLOAD_BYTES) == big, f"fixture with {n} devices is {'above' if big else 'below'} the offload size")

        async def run(argv, timeout, text=text):
            return 0, text, ""
        b = SB.Bluetooth(run=run, emit=lambda *a: None)
        parsed_in.clear()
        await b.refresh()
        check(len(parsed_in) == 1 and (parsed_in[0] != MAIN) == big,
              f"{n} devices: parsed {'in a worker thread' if big else 'inline'}")
        check(len(b.devices) == min(n, SB.BT_DEV_MAX) and b.adapter == "/org/bluez/hci0", f"{n} devices: the list is the same either way")

asyncio.run(bt_cases())

# ---------- 4. adapter scan ----------
async def adapter_case():
    btadapter = SB.btadapter
    seen = {}
    def slow_scan(root):
        seen["thread"] = threading.get_ident()
        time.sleep(SLOW_S)
        return ()
    real = btadapter.scan
    btadapter.scan = slow_scan
    try:
        b = SB.Bluetooth(run=None, emit=lambda *a: None)
        b.sysfs = "/nonexistent"
        ticks = [0]
        t = asyncio.create_task(ticker(ticks))
        await b.check_adapters()
        t.cancel()
    finally:
        btadapter.scan = real
    check(seen.get("thread") not in (None, MAIN), "btadapter.scan runs in a worker thread")
    check(ticks[0] >= SLOW_S / 0.01 / 2, f"the loop kept running during the adapter scan ({ticks[0]} ticks)")

asyncio.run(adapter_case())
print("SENDER OK" if not fails else f"FAILED: {len(fails)}")
sys.exit(1 if fails else 0)
