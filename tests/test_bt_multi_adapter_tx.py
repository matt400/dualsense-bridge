"""Sender: Bluetooth przy kilku kontrolerach (wbudowany zablokowany + dongle USB).
  - skan z okna: bluetoothctl skanował domyślnym kontrolerem (hci0 - zablokowany wbudowany), a nie wybranym donglem
  - wyłączenie pada (okno / bezczynność): `bluetoothctl disconnect` szukał pada tylko na domyślnym kontrolerze
python3 tests/test_bt_multi_adapter_tx.py sender"""
import asyncio, json, os, stat, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import sender_bt as SB, sender_control as ctl
from proto import BT_SCAN_START, BTR_OK, BTS_OK

ok = lambda m: print("OK  ", m)
DONGLE, PAD = "00:1A:7D:DA:71:13", "aa:bb:cc:dd:ee:01"


def var(sig, v): return {"type": sig, "data": v}


def objects(connected_on="hci1"):
    out = {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": var("s", "11:22:33:44:55:66"), "Powered": var("b", False),
                                                       "PowerState": var("s", "off-blocked")}},
           "/org/bluez/hci1": {"org.bluez.Adapter1": {"Address": var("s", DONGLE), "Powered": var("b", True)}}}
    for hci in ("hci0", "hci1"):                    # pad sparowany z oboma kontrolerami, połączony z jednym
        out[SB.dev_path(f"/org/bluez/{hci}", PAD)] = {"org.bluez.Device1": {
            "Address": var("s", PAD.upper()), "Name": var("s", "DualSense Wireless Controller"),
            "Paired": var("b", True), "Connected": var("b", hci == connected_on)}}
    return json.dumps({"type": "a{oa{sa{sv}}}", "data": [out]})


class Bus:
    def __init__(s, text=None, missing=False):
        s.text, s.missing, s.calls = text, missing, []

    async def __call__(s, argv, timeout):
        argv = list(argv)
        if s.missing:
            raise FileNotFoundError(argv[0])
        s.calls.append(argv[3:])
        if argv[-1] == "GetManagedObjects":
            return 0, s.text, ""
        return 0, "", ""


# ---------- parse_objects: adres i liczba adapterów ----------
info = {}
st, path, devs = SB.parse_objects(objects(), "/org/bluez/hci1", info)
assert (st, path) == (BTS_OK, "/org/bluez/hci1") and info == {"address": DONGLE, "count": 2}, info
assert SB.parse_objects(objects())[:2] == (BTS_OK, "/org/bluez/hci1"), "bez info - jak dotąd"
ok("BlueZ: adres wybranego kontrolera i liczba kontrolerów (do wyboru kontrolera w skanie)")

# ---------- skan: select <dongle> + scan on, limit czasu bez --timeout ----------
BIN = tempfile.mkdtemp()
LOG, IN = os.path.join(BIN, "args.log"), os.path.join(BIN, "stdin.log")
fake = os.path.join(BIN, "bluetoothctl")
with open(fake, "w") as fh:          # jak bluetoothctl: kontrolery wypisuje chwilę po starcie, potem czyta polecenia
    fh.write(f'#!/bin/sh\necho "$*" >> "{LOG}"\n[ "$1" = disconnect ] && exit 0\nsleep 0.2\n'
             f'printf "Agent registered\\n[NEW] Controller 11:22:33:44:55:66 phone\\n'
             f'[NEW] Controller {DONGLE} dongle [default]\\n"\nexec cat >> "{IN}"\n')
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
SB.BLUETOOTHCTL = fake


async def settle(t=0.6):
    end = time.monotonic() + t
    while time.monotonic() < end:
        await asyncio.sleep(0.02)


async def t_scan():
    b = SB.Bluetooth(run=Bus(objects()), emit=lambda *a: None)
    await b.refresh()
    assert b.adapter == "/org/bluez/hci1" and b.adapter_addr == DONGLE and b.adapter_count == 2
    b.on_command(BT_SCAN_START, 1, bytes(6)); await settle()
    assert b.scanning() and b.last == (BT_SCAN_START, BTR_OK, b"")
    assert open(LOG).read().split() == [], "bez 'scan on' w argumentach - skanowałby domyślny kontroler (hci0)"
    assert open(IN).read().splitlines() == [f"select {DONGLE}", "scan on"], open(IN).read()
    await settle(1.2)                                   # bluetoothctl interaktywny nie zna --timeout - limit nasz
    assert not b.scanning() and b.scan is None, "auto-stop po czasie także w trybie interaktywnym"

    one = json.loads(objects())                         # jeden kontroler: jak dotąd (--timeout N scan on)
    del one["data"][0]["/org/bluez/hci0"]
    open(LOG, "w").close()
    b = SB.Bluetooth(run=Bus(json.dumps(one)), emit=lambda *a: None)
    b.on_command(BT_SCAN_START, 5, bytes(6)); await settle(0.3)
    assert open(LOG).read().split() == ["--timeout", "5", "scan", "on"]
    b.kill_scan(); await settle(0.1)
    ok("skan przy dwóch kontrolerach: 'select <dongle>' + 'scan on' po wypisaniu kontrolera, limit czasu; "
       "jeden kontroler - bez zmian")

asyncio.run(t_scan())


# ---------- wyłączenie pada przez D-Bus na kontrolerze, z którym jest połączony ----------
class C:
    idx, key, powering_off = 0, PAD, 0.0


async def t_power():
    bus = Bus(objects("hci1"))
    ctl.sender_bt.bt = SB.Bluetooth(run=bus, emit=lambda *a: None)
    open(LOG, "w").close()
    await ctl.power_off(C(), "test")
    dis = [c for c in bus.calls if "Disconnect" in c]
    assert dis and dis[0][2] == SB.dev_path("/org/bluez/hci1", PAD), bus.calls
    assert open(LOG).read() == "", "bez bluetoothctl (szukałby pada na hci0)"

    old = os.environ["PATH"]; os.environ["PATH"] = BIN + os.pathsep + old
    try:
        bus = Bus(objects(connected_on=""))             # pad nigdzie nie połączony -> zapas: bluetoothctl
        ctl.sender_bt.bt = SB.Bluetooth(run=bus, emit=lambda *a: None)
        await ctl.power_off(C(), "test")
        assert not [c for c in bus.calls if "Disconnect" in c] and f"disconnect {PAD}" in open(LOG).read()
        open(LOG, "w").close()                          # brak busctl -> zapas: bluetoothctl
        ctl.sender_bt.bt = SB.Bluetooth(run=Bus(missing=True), emit=lambda *a: None)
        await ctl.power_off(C(), "test")
        assert f"disconnect {PAD}" in open(LOG).read()
    finally:
        os.environ["PATH"] = old
    ok("wyłączenie pada: Disconnect przez D-Bus na kontrolerze z połączeniem (dongle), zapas bluetoothctl")

asyncio.run(t_power())
print("HUNT 15 (NADAJNIK) OK")
