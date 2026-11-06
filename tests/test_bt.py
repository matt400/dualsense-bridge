"""Phone Bluetooth from the receiver window: protocol (btcmd / btstate / btdev), sender (sender_bt: BlueZ through busctl,
scan through bluetoothctl with a limit, adapter and permission errors), receiver (rx_bt: list rounds, state, commands) and end to end
over UDP on localhost.   python3 tests/test_bt.py receiver sender"""
import asyncio, json, os, stat, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(__file__))
TX = sys.argv[2]
from rxenv import *                                              # noqa  (receiver: argv[1])
sys.path.insert(1, TX)
sys.path.insert(1, TX + "/../common")
import proto
from proto import (BODY, BT_ACTIVE_S, BT_CONNECT, BT_DISCONNECT, BT_LIST, BT_PAIR, BT_POWER, BT_REMOVE, BT_RSSI_NONE,
                   BT_SCAN_MAX_S, BT_SCAN_START, BT_SCAN_STOP, BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTD_TRUSTED,
                   BTF_BUSY, BTF_SCANNING, BTR_AUTH, BTR_BUSY, BTR_DENIED, BTR_FAILED, BTR_NOT_FOUND, BTR_OFF, BTR_OK,
                   BTR_RUNNING, BTS_BLOCKED, BTS_DENIED, BTS_ERROR, BTS_NO_ADAPTER, BTS_NO_SERVICE, BTS_NO_TOOL, BTS_OFF,
                   BTS_OK, T_BTCMD, T_BTDEV, T_BTSTATE, T_CMD, T_IDENT, T_VERSION, Codec, mac_bytes)
from chk import ok
PAD, PAD2, PHONE, TV = "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "11:22:33:44:55:66", "77:88:99:aa:bb:cc"

# ---------- protocol ----------
cod = Codec()
assert proto.SENDER_VERSION >= 10
for t, vals in ((T_BTCMD, (BT_PAIR, 0, mac_bytes(PAD))), (T_BTSTATE, (BTS_OK, 1, 30, 2, 3, 7, BT_PAIR, BTR_OK, bytes(6))),
                (T_BTDEV, (7, 0, 3, mac_bytes(PAD), -60, BTD_PAD, b"DualSense"))):
    pkt = cod.pack(t, 255, *vals)
    assert cod.parse(pkt)[0] == t and BODY[t].unpack_from(pkt, 4)[:3] == vals[:3], t
assert cod.parse(cod.pack(T_CMD, 0, 1, 0))[0] == T_CMD and cod.parse(cod.pack(T_VERSION, 0, 9, 3, 0))[0] == T_VERSION
assert cod.parse(cod.pack(T_IDENT, 0, PAD.encode()))[0] == T_IDENT, "old types with the same headers unchanged"
k = Codec(b"klucz")
assert k.parse(k.pack(T_BTSTATE, 255, BTS_OFF, 0, 0, 0, 0, 1, 0, 0, bytes(6)))[0] == T_BTSTATE, "with HMAC too"
f = proto.utf8_field("Pad Łukasza – żółty ąęść", 24)                      # 26 characters, 33 bytes
assert len(f) <= 24 and f.decode("utf-8").startswith("Pad Łukasza"), "cut on a UTF-8 character boundary"
ok("protocol: btcmd / btstate / btdev as longer variants of cmd / version / ident, HMAC, name cut on a character")

# ---------- sender: BlueZ through busctl (substituted runner) ----------
import sender_config; sender_config.init(["127.0.0.1"])
import sender_bt as SB, sender_net as net, sender_state as state

def var(sig, v): return {"type": sig, "data": v}

class FakeBlueZ:
    """BlueZ for the command runner: GetManagedObjects, Pair, Connect, Disconnect, RemoveDevice, Powered, Trusted."""
    def __init__(s):
        s.powered, s.power_state, s.calls, s.fail, s.missing, s.delay = True, "on", [], {}, None, 0.0
        s.pairable, s.bond_needs_pairable = False, True     # BlueZ bonds on pairing only with Pairable=true
        s.devs = {PAD: {"Name": "DualSense Wireless Controller", "Class": 0x002508, "Paired": False, "Connected": False,
                        "RSSI": -58},
                  PAD2: {"Name": "DualSense Wireless Controller", "Icon": "input-gaming", "Paired": True, "Trusted": True,
                         "Connected": True},
                  PHONE: {"Name": "Telefon sąsiada", "Class": 0x5a020c, "RSSI": -91},
                  "de:ad:be:ef:00:01": {"RSSI": -70}}                            # beacon without a name - skipped
    def objects(s):
        out = {"/org/bluez": {"org.bluez.AgentManager1": {}},
               "/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": var("s", "00:11:22:33:44:55"),
                                                          "Powered": var("b", s.powered), "PowerState": var("s", s.power_state)}}}
        for mac, p in s.devs.items():
            props = {"Address": var("s", mac.upper()), "Alias": var("s", p.get("Name") or mac.replace(":", "-").upper())}
            for key, sig in (("Name", "s"), ("Class", "u"), ("Icon", "s"), ("Paired", "b"), ("Trusted", "b"),
                             ("Connected", "b"), ("RSSI", "n")):
                if key in p:
                    props[key] = var(sig, p[key])
            out[SB.dev_path("/org/bluez/hci0", mac)] = {"org.bluez.Device1": props}
        return json.dumps({"type": "a{oa{sa{sv}}}", "data": [out]})
    async def __call__(s, argv, timeout):
        argv = list(argv)
        assert argv[0] == SB.BUSCTL and argv[1] == "--json=short" and argv[2].startswith("--timeout="), argv
        if s.missing:
            raise FileNotFoundError(argv[0])
        cmd = argv[3:]
        s.calls.append(tuple(c for c in cmd if c != "org.bluez"))
        if s.delay:
            await asyncio.sleep(s.delay)
        if cmd[-1] == "GetManagedObjects":
            return (1, "", s.fail["objects"]) if "objects" in s.fail else (0, s.objects(), "")
        mac = next((m for m in s.devs if cmd[2 if cmd[0] == "set-property" else 2] == SB.dev_path("/org/bluez/hci0", m)), None)
        if cmd[0] == "set-property":
            if cmd[4] in s.fail:
                return 1, "", s.fail[cmd[4]]
            if cmd[3] == "org.bluez.Adapter1":
                setattr(s, {"Powered": "powered", "Pairable": "pairable"}[cmd[4]], cmd[-1] == "true")
            else:
                s.devs[mac][cmd[4]] = cmd[-1] == "true"
            return 0, "", ""
        if cmd[0] == "get-property":
            v = s.devs[mac].get(cmd[4])
            return (1, "", "Unknown property") if v is None else (0, json.dumps(var("b", v)), "")
        method = cmd[4]
        if method in s.fail:
            return 1, "", s.fail[method]
        if method == "RemoveDevice":
            del s.devs[next(m for m in s.devs if SB.dev_path("/org/bluez/hci0", m) == cmd[-1])]
        elif method == "Pair":
            s.devs[mac]["Paired"] = True
            s.devs[mac]["Bonded"] = s.pairable or not s.bond_needs_pairable
        elif method in ("Connect", "Disconnect"):
            s.devs[mac]["Connected"] = method == "Connect"
        return 0, "", ""

bz = FakeBlueZ()
st, path, devs = SB.parse_objects(bz.objects())
assert st == BTS_OK and path == "/org/bluez/hci0" and [d.mac for d in devs] == [PAD2, PAD, PHONE], devs
assert devs[0].flags == BTD_PAIRED | BTD_CONNECTED | BTD_TRUSTED | BTD_PAD and devs[1].flags == BTD_PAD and devs[1].rssi == -58
assert devs[2].flags == 0 and devs[0].rssi is None, "the phone is not a pad; RSSI only from discovery"
bz.powered = False
assert SB.parse_objects(bz.objects())[0] == BTS_OFF
bz.power_state = "off-blocked"
assert SB.parse_objects(bz.objects())[0] == BTS_BLOCKED
bz.powered, bz.power_state = True, "on"
assert SB.parse_objects(json.dumps({"type": "a{oa{sa{sv}}}", "data": [{"/org/bluez": {}}]}))[:2] == (BTS_NO_ADAPTER, "")
many = {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": var("b", True)}}}
many |= {SB.dev_path("/org/bluez/hci0", f"aa:00:00:00:00:{k:02x}"): {"org.bluez.Device1": {
    "Address": var("s", f"AA:00:00:00:00:{k:02X}"), "Name": var("s", f"Urządzenie {k:02}")}} for k in range(1, 60)}
assert len(SB.parse_objects(json.dumps({"data": [many]}))[2]) == proto.BT_DEV_MAX
two = {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": var("b", False), "PowerState": var("s", "off-blocked")}},
       "/org/bluez/hci1": {"org.bluez.Adapter1": {"Powered": var("b", True)}}}
assert SB.parse_objects(json.dumps({"data": [two]}))[:2] == (BTS_OK, "/org/bluez/hci1"), "wbudowany zablokowany -> dongle"
two["/org/bluez/hci1"]["org.bluez.Adapter1"]["Powered"] = var("b", False)
assert SB.parse_objects(json.dumps({"data": [two]}))[:2] == (BTS_BLOCKED, "/org/bluez/hci0"), "none powered -> hci0"
ok("BlueZ: first powered adapter (USB dongle), powered / off / rfkill, pads (class, icon), no beacons, order, list limit")

import rxenv
rx_sent = rxenv.sent                           # receiver packets to the sender (rxenv)
rxenv.S.peer = ("127.0.0.1", 5005)              # sender known: BT commands have somewhere to go
tx_out = []                                    # sender packets (emit)
emit = lambda t, i, *v: tx_out.append((t, i, v))
def states(): return [v for t, _i, v in tx_out if t == T_BTSTATE]
def dev_rows(): return [v for t, _i, v in tx_out if t == T_BTDEV]

async def settle(n=20):
    for _k in range(n):
        await asyncio.sleep(0.01)

async def t_ops():
    b = SB.Bluetooth(run=bz, emit=emit)
    await b.refresh()
    b.on_command(BT_PAIR, 0, mac_bytes(PAD)); b.on_command(BT_PAIR, 0, mac_bytes(PAD)); b.on_command(BT_PAIR, 0, mac_bytes(PAD))
    assert b.last == (BT_PAIR, BTR_RUNNING, mac_bytes(PAD)) and b.active(time.monotonic())
    await b.busy
    pad = SB.dev_path("/org/bluez/hci0", PAD)
    ops = [c for c in bz.calls if c[-1] != "GetManagedObjects"]
    assert ops == [("set-property", "/org/bluez/hci0", "org.bluez.Adapter1", "Pairable", "b", "true"),
                   ("call", pad, "org.bluez.Device1", "Pair"), ("get-property", pad, "org.bluez.Device1", "Bonded"),
                   ("set-property", "/org/bluez/hci0", "org.bluez.Adapter1", "Pairable", "b", "false"),
                   ("set-property", pad, "org.bluez.Device1", "Trusted", "b", "true"),
                   ("call", pad, "org.bluez.Device1", "Connect")], ops
    assert bz.devs[PAD]["Bonded"], "Pairable before Pair -> key stored"
    assert not bz.pairable, "after pairing the adapter does not stay open for pairing"
    assert b.last == (BT_PAIR, BTR_OK, mac_bytes(PAD)) and bz.devs[PAD]["Connected"] and bz.devs[PAD]["Trusted"]
    s = states()[-1]
    assert s[:5] == (BTS_OK, SB.BTF_LIST, 0, 2, 3) and s[6:8] == (BT_PAIR, BTR_OK), s
    rows = dev_rows()[-3:]
    assert [r[1:3] for r in rows] == [(0, 3), (1, 3), (2, 3)] and {r[0] for r in rows} == {s[5]}, "runda: numer i ile"
    assert rows[0][3] == mac_bytes(PAD) and rows[0][5] & BTD_CONNECTED, "connected pad at the top of the list"
    assert rows[2][4] == -91 and rows[0][4] == -58 and rows[2][6].rstrip(b"\0").decode() == "Telefon sąsiada"
    ok("pairing: 3 repeats = one, Pairable -> Pair -> Bonded? -> Trusted -> Connect, result and a list round to the window")

    bz.calls.clear(); tx_out.clear()
    b.on_command(BT_PAIR, 0, mac_bytes(PAD2))
    await b.busy
    assert ("call", SB.dev_path("/org/bluez/hci0", PAD2), "org.bluez.Device1", "Pair") not in bz.calls, "already paired: no Pair"
    b.dups.clear()
    b.on_command(BT_DISCONNECT, 0, mac_bytes(PAD)); await b.busy
    assert not bz.devs[PAD]["Connected"] and b.last[1] == BTR_OK
    b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await b.busy
    assert bz.devs[PAD]["Connected"]
    b.on_command(BT_REMOVE, 0, mac_bytes(PHONE)); await b.busy
    assert PHONE not in bz.devs and ("call", "/org/bluez/hci0", "org.bluez.Adapter1", "RemoveDevice", "o",
                                     SB.dev_path("/org/bluez/hci0", PHONE)) in bz.calls
    b.on_command(BT_CONNECT, 0, mac_bytes(TV)); await b.busy
    assert b.last == (BT_CONNECT, BTR_NOT_FOUND, mac_bytes(TV)), "unknown device - scan first"
    ok("connect, disconnect, forget (RemoveDevice), paired without another Pair, unknown = NOT_FOUND")

    bz.devs[PAD]["Paired"] = False; b.dups.clear()
    bz.fail["Pair"] = "Call failed: Authentication Failed"
    b.on_command(BT_PAIR, 0, mac_bytes(PAD)); await b.busy
    assert b.last[1] == BTR_AUTH
    bz.fail["Pair"] = "Call failed: Already Exists"; b.dups.clear()
    b.on_command(BT_PAIR, 0, mac_bytes(PAD)); await b.busy
    assert b.last[1] == BTR_OK, "Already Exists = sparowany - dalej Trusted i Connect"
    del bz.fail["Pair"]
    bz.fail["Connect"] = "Call failed: Connection timed out"; b.dups.clear()
    b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await b.busy
    assert b.last[1] == proto.BTR_TIMEOUT
    del bz.fail["Connect"]
    bz.delay = 0.2; b.dups.clear()
    b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await settle(3)
    b.on_command(BT_DISCONNECT, 0, mac_bytes(PAD2))
    assert b.last == (BT_DISCONNECT, BTR_BUSY, mac_bytes(PAD2)), "druga operacja w trakcie pierwszej"
    await b.busy; bz.delay = 0
    ok("BlueZ errors: pairing rejected, already paired, timeout, operation during another one = BUSY")

    pad = SB.dev_path("/org/bluez/hci0", PAD)
    bz.devs[PAD] |= {"Paired": True, "Bonded": False, "Trusted": False, "Connected": False}
    bz.calls.clear(); b.dups.clear()
    b.on_command(BT_PAIR, 0, mac_bytes(PAD)); await b.busy
    assert b.last[1] == BTR_AUTH and PAD not in bz.devs, "paired without a key: forgotten, to be paired again"
    assert ("call", pad, "org.bluez.Device1", "Pair") not in bz.calls
    bz.devs[PAD] = {"Name": "DualSense Wireless Controller", "Class": 0x002508, "Paired": False, "RSSI": -58}
    bz.pairable, bz.fail["Pairable"] = False, "Call failed: Not supported"; b.dups.clear()
    b.on_command(BT_PAIR, 0, mac_bytes(PAD)); await b.busy
    assert b.last[1] == BTR_AUTH and bz.devs[PAD]["Bonded"] is False and not bz.devs[PAD].get("Connected"), \
        "adapter without bonding: an error in the window instead of a pad that would not come back after power-off"
    del bz.fail["Pairable"]
    bz.devs[PAD] |= {"Bonded": True, "Trusted": False}
    await b.refresh()
    assert bz.devs[PAD]["Trusted"] and next(d for d in b.devices if d.mac == PAD).flags & BTD_TRUSTED, "trust_pads"
    bz.calls.clear(); await b.refresh()
    assert not [c for c in bz.calls if c[0] == "set-property"], "trusted - no further calls on refresh"
    bz.devs[PAD]["Trusted"] = False; bz.fail["Trusted"] = "Call failed: Access denied"; bz.calls.clear()
    await b.refresh(); await b.refresh()
    assert sum(c[0] == "set-property" for c in bz.calls) == 1, "failed Trusted not retried on every refresh"
    del bz.fail["Trusted"]
    bz.devs[PHONE] = {"Name": "Telefon", "Paired": True, "Trusted": False}
    await b.refresh()
    assert not bz.devs[PHONE]["Trusted"], "non-pads without automatic trust"
    b.untrusted = {PAD}; b.devices = [d for d in b.devices if d.mac != PAD]   # connect without trust_pads in refresh
    bz.devs[PAD] |= {"Trusted": False, "Connected": False}; b.dups.clear()
    b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await b.busy
    assert bz.devs[PAD]["Trusted"] and bz.devs[PAD]["Connected"], "connecting sets trust too"
    b.untrusted.clear()
    bz.devs[PAD] |= {"Trusted": False, "Connected": False, "Icon": "phone", "Class": 0}; b.dups.clear()
    b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await b.busy
    assert not bz.devs[PAD]["Trusted"] and bz.devs[PAD]["Connected"], "non-pad: connect without trust"
    bz.devs[PAD] |= {"Paired": False, "Connected": False}; bz.calls.clear(); b.dups.clear()
    b.on_command(BT_PAIR, 0, mac_bytes(PAD)); await b.busy
    assert b.last[1] == BTR_DENIED and not [c for c in bz.calls if c[0] != "call" or c[-1] != "GetManagedObjects"], \
        "pairing from the window only for pads (a foreign BT keyboard = key injection into the sender)"
    bz.devs[PAD] |= {"Icon": "input-gaming", "Class": 0x002508}
    ok("bonding and trust: Pairable, paired without a key = forgotten, trust on refresh and on connect")

    bz.powered = False; b.dups.clear()
    await b.refresh()
    assert b.state == BTS_OFF
    b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await b.busy
    assert b.last[1] == BTR_OFF
    b.on_command(BT_POWER, 1, bytes(6)); await b.busy
    assert bz.powered and b.state == BTS_OK and b.last == (BT_POWER, BTR_OK, bytes(6))
    bz.powered, bz.power_state = False, "off-blocked"; b.dups.clear()
    b.on_command(BT_POWER, 1, bytes(6)); await b.busy
    assert b.last[1] == BTR_OFF and not bz.powered, "rfkill - cannot be turned on from the window"
    bz.powered, bz.power_state = True, "on"
    ok("power: adapter off = OFF for operations, BT_POWER turns it on, rfkill blocks")

    for err, want in (("Failed to connect to bus: Access denied", BTS_DENIED),
                      ("Call failed: The name org.bluez was not provided by any .service files", BTS_NO_SERVICE),
                      ("Call failed: something odd", BTS_ERROR)):
        bz.fail["objects"] = err
        await b.refresh()
        assert b.state == want, (err, b.state)
    del bz.fail["objects"]
    bz.missing = True
    await b.refresh()
    assert b.state == BTS_NO_TOOL and b.devices == []
    b.dups.clear(); b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await b.busy
    assert b.last[1] == BTR_FAILED, "no busctl - a result, not an exception"
    bz.missing = False
    await b.refresh()
    assert b.state == BTS_OK
    ok("resilience: no permissions, no bluetoothd, no busctl, odd reply - BTS_* state, the sender keeps running")

    tx_out.clear()
    b.active_until = 0
    b.send(full=False)
    assert states()[-1][1] == 0 and states()[-1][4] == 0 and not dev_rows(), "no panel: state only, no list"
    b.on_command(BT_LIST, 0, bytes(6), now=100.0)
    assert b.active(100.0 + BT_ACTIVE_S - 0.1) and not b.active(100.0 + BT_ACTIVE_S + 0.1)
    b.on_command(BT_LIST, 0, bytes(6), now=200.0); b.on_command(BT_LIST, 0, bytes(6), now=200.0)
    assert b.active_until == 200.0 + BT_ACTIVE_S, "BT_LIST without the repeat filter - it is a keepalive"
    ok("activity: BT_LIST extends list mode by BT_ACTIVE_S, without it state only")

asyncio.run(t_ops())

# ---------- skan: bluetoothctl --timeout N scan on (limit, przerwanie, brak programu) ----------
BIN = tempfile.mkdtemp()
LOG = os.path.join(BIN, "args.log")
fake = os.path.join(BIN, "bluetoothctl")
with open(fake, "w") as fh:
    fh.write(f'#!/bin/sh\necho "$@" >> "{LOG}"\nexec sleep "$2"\n')
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)

async def t_scan():
    SB.BLUETOOTHCTL = fake
    b = SB.Bluetooth(run=bz, emit=emit)
    b.on_command(BT_SCAN_START, 200, bytes(6)); await settle()
    assert b.scanning() and open(LOG).read().split() == ["--timeout", str(BT_SCAN_MAX_S), "scan", "on"], "limit 30 s"
    tx_out.clear(); b.send(full=True)
    s = states()[-1]
    assert s[1] & BTF_SCANNING and BT_SCAN_MAX_S - 1 <= s[2] <= BT_SCAN_MAX_S and b.last == (BT_SCAN_START, BTR_OK, b"")
    proc = b.scan
    b.on_command(BT_SCAN_STOP, 0, bytes(6)); await settle()
    assert not b.scanning() and proc.returncode is not None, "aborted from the window - process ended"
    b.on_command(BT_SCAN_START, 1, bytes(6)); await settle()
    assert b.scanning()
    await asyncio.sleep(1.3)
    assert not b.scanning() and b.scan is None, "auto-stop po czasie"
    bz.devs[PAD]["Connected"] = False; b.dups.clear()
    b.on_command(BT_SCAN_START, 10, bytes(6)); await settle()
    b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await b.busy
    assert not b.scanning() and bz.devs[PAD]["Connected"], "connecting aborts the scan (BlueZ, pad latency)"
    bz.powered = False; b.dups.clear()
    b.on_command(BT_SCAN_START, 5, bytes(6)); await settle()
    assert bz.powered and b.scanning(), "the scan turns a powered-off adapter on by itself"
    b.kill_scan(); await settle()
    SB.BLUETOOTHCTL = os.path.join(BIN, "brak-programu"); b.dups.clear()
    b.on_command(BT_SCAN_START, 5, bytes(6)); await settle()
    assert not b.scanning() and b.last == (BT_SCAN_START, BTR_FAILED, b""), "brak bluetoothctl"
    SB.BLUETOOTHCTL = fake
    ok("scan: bluetoothctl --timeout (at most 30 s), stop from the window, auto-stop, aborted by connecting, turns the adapter on")

asyncio.run(t_scan())

# ---------- prawdziwy proces busctl (podstawiony skrypt w PATH) ----------
with open(os.path.join(BIN, "busctl"), "w") as fh:
    fh.write(f"#!/bin/sh\ncase \"$*\" in *GetManagedObjects*) cat '{BIN}/objects.json';; *Connect*) echo 'Call failed: "
             "Access denied' >&2; exit 1;; esac\n")
os.chmod(os.path.join(BIN, "busctl"), 0o755)
with open(os.path.join(BIN, "objects.json"), "w") as fh:
    fh.write(FakeBlueZ().objects())

async def t_real():
    old = os.environ["PATH"]
    os.environ["PATH"] = BIN + os.pathsep + old
    try:
        b = SB.Bluetooth(emit=emit)
        await b.refresh()
        assert b.state == BTS_OK and len(b.devices) == 3, (b.state, b.devices)
        b.on_command(BT_CONNECT, 0, mac_bytes(PAD)); await b.busy
        assert b.last[1] == BTR_DENIED
        rc, _o, err = await SB.run_cmd(["sh", "-c", "sleep 5"], 0.2)
        assert rc == -1 and err == "timed out"
    finally:
        os.environ["PATH"] = old
    ok("runner: busctl as a process (JSON from stdout, error from stderr), a timeout kills the process")

asyncio.run(t_real())

# ---------- loop: only to a new receiver, the list only in active mode ----------
async def t_loop():
    b = SB.Bluetooth(run=bz, emit=emit)
    tx_out.clear()
    net.scfg_t = 0.0
    task = asyncio.create_task(b.loop())
    await asyncio.sleep(0.3)
    assert not tx_out, "receiver without scfg (older) - nothing"
    net.scfg_t = time.monotonic()
    b.poke(); await asyncio.sleep(0.3)
    assert len(states()) == 1 and not states()[0][1] & SB.BTF_LIST, "state at once, no list"
    b.on_command(BT_LIST, 0, bytes(6)); await asyncio.sleep(0.3)
    assert states()[-1][1] & SB.BTF_LIST and len(dev_rows()) == states()[-1][4] > 0
    n = len(states())
    await asyncio.sleep(1.2)
    assert len(states()) > n, "active: every second"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

asyncio.run(t_loop())
ok("loop: state only to a receiver with scfg, right after btcmd, the list every second while the panel is open")

# ---------- receiver: list rounds, state, commands only to a new sender ----------
import rx_bt as RB, rx_events as ev
RB.reset(); ev.clear()
def rx(t, *v):
    RB.on_packet(t, N.codec.pack(t, 255, *v))
rx_sent.clear()
assert not RB.pair(PAD) and not rx_sent, "sender without btstate - the command does not go"
lvl, text = RB.indicator()
assert lvl == "" and "brak danych" in text
rx(T_BTSTATE, BTS_OK, SB.BTF_LIST, 0, 1, 2, 5, 0, 0, bytes(6))
assert RB.supported() and RB.devices == () and RB.indicator()[0] == "ok"
rx(T_BTDEV, 5, 0, 2, mac_bytes(PAD), -60, BTD_PAD, b"DualSense")
assert RB.devices == (), "incomplete round - list unchanged"
rx(T_BTDEV, 5, 1, 2, mac_bytes(PHONE), BT_RSSI_NONE, 0, b"")
assert [d.mac for d in RB.devices] == [PAD, PHONE] and RB.devices[0].pad and RB.devices[0].bars() == 3
assert RB.devices[1].rssi is None and RB.devices[1].name == PHONE and RB.devices[1].bars() == 0
rx(T_BTSTATE, BTS_OK, SB.BTF_LIST, 0, 1, 2, 6, 0, 0, bytes(6))
rx(T_BTDEV, 6, 0, 2, mac_bytes(PAD), -40, BTD_PAD | BTD_PAIRED, b"DualSense")         # druga zgubiona
rx(T_BTSTATE, BTS_OK, SB.BTF_LIST, 0, 1, 2, 7, 0, 0, bytes(6))                            # next round
rx(T_BTDEV, 6, 1, 2, mac_bytes(PHONE), -80, 0, b"Telefon")                               # late, from round 6
assert len(RB.devices) == 2 and RB.devices[0].rssi == -60, "a lost round packet - the previous list stays"
rx(T_BTSTATE, BTS_OK, 0, 0, 1, 0, 7, 0, 0, bytes(6))                                     # no BTF_LIST: the list stays
assert len(RB.devices) == 2
long = proto.utf8_field("DualSense Wireless Controller (Łukasz)", SB.NAME_MAX, "…")
assert len(long) <= SB.NAME_MAX and proto.utf8_field("krótka", SB.NAME_MAX, "…") == "krótka".encode()
rx(T_BTSTATE, BTS_OK, SB.BTF_LIST, 0, 0, 1, 8, 0, 0, bytes(6))
rx(T_BTDEV, 8, 0, 1, mac_bytes(PAD), -60, BTD_PAD, long)
assert SB.NAME_MAX == 32 and RB.devices[0].name.startswith("DualSense Wireless Controller") and \
    RB.devices[0].name.endswith("…"), RB.devices[0].name
rx(T_BTSTATE, BTS_OK, SB.BTF_LIST, 0, 0, 0, 9, 0, 0, bytes(6))
assert RB.devices == (), "empty round clears the list"
rx(T_BTSTATE, BTS_OK, BTF_SCANNING, 21, 1, 0, 8, 0, 0, bytes(6))
assert RB.indicator()[0] == "scan" and "21" in RB.indicator()[1] and RB.status.scanning
rx(T_BTSTATE, BTS_OFF, 0, 0, 0, 0, 8, 0, 0, bytes(6))
assert RB.indicator()[0] == "warn"
rx(T_BTSTATE, BTS_DENIED, 0, 0, 0, 0, 8, 0, 0, bytes(6))
assert RB.indicator()[0] == "bad" and "root" in RB.indicator()[1]
assert RB.indicator(time.monotonic() + RB.STALE_S + 1)[0] == "", "old state = unknown"
ok("receiver: the list replaced by a whole round (lost packet = the previous one), RSSI / no RSSI, name cut with …, "
   "state dot")

peer, rxenv.S.peer = rxenv.S.peer, None
assert not RB.pair(PAD), "sender address unknown: the command does not go out"
rxenv.S.peer = peer
rx_sent.clear()
assert RB.pair(PAD)
pk = [p for t, i, _sq, p in rx_sent]
assert len(pk) == 3 and all(BODY[T_BTCMD].unpack_from(p, 4) == (BT_PAIR, 0, mac_bytes(PAD)) for p in pk)
assert len({sq for _t, _i, sq, _p in rx_sent}) == 1, "Bluetooth command: the same packet (number) repeated, not 3 separate ones"
assert RB.busy_op(PAD) == BT_PAIR and RB.any_busy() and RB.busy_op(PHONE) is None
assert RB.busy_op(PAD, time.monotonic() + RB.PENDING_S + 1) is None, "a lost command does not hang forever"
rx(T_BTSTATE, BTS_OK, BTF_BUSY, 0, 0, 0, 9, BT_PAIR, BTR_RUNNING, mac_bytes(PAD))
assert RB.busy_op(PAD) == BT_PAIR and not [e for e in ev.since(0) if "Bluetooth" in e.text]
for _n in range(3):
    rx(T_BTSTATE, BTS_OK, 0, 0, 1, 0, 9, BT_PAIR, BTR_OK, mac_bytes(PAD))
logs = [e.text for e in ev.since(0) if "Bluetooth" in e.text]
assert len(logs) == 1 and "sparowano" in logs[0] and RB.pending is None, logs
RB.pair(PAD)
rx(T_BTSTATE, BTS_OK, 0, 0, 1, 0, 9, BT_PAIR, BTR_OK, mac_bytes(PAD))
assert len([e for e in ev.since(0) if "Bluetooth" in e.text]) == 2, "the same operation a second time - a second entry"
rx(T_BTSTATE, BTS_OK, 0, 0, 1, 0, 9, BT_CONNECT, BTR_AUTH, mac_bytes(PAD))
assert "odrzucone" in ev.since(0)[-1].text and ev.since(0)[-1].level == "warn"
rx_sent.clear()
RB.scan(True); RB.scan(False); RB.power(True); RB.keepalive()
ops = [BODY[T_BTCMD].unpack_from(p, 4)[:2] for t, i, _sq, p in rx_sent]
assert ops == [(BT_SCAN_START, BT_SCAN_MAX_S)] * 3 + [(BT_SCAN_STOP, 0)] * 3 + [(BT_POWER, 1)] * 3 + [(BT_LIST, 0)] * 3
ok("receiver: commands 3x, operation in progress (lost one too), result once to the log, error as a warning")

# ---------- end to end over UDP: window -> sender (BlueZ) -> window ----------
N.send_peer = ORIG_SEND_PEER
C.IN_PORT = 0
N.init()
port = N.in_sock.getsockname()[1]
threading.Thread(target=N.net_loop, daemon=True).start()
from sender_config import C as TC
TC.PORT = port
net.init()
RB.reset()
bz2 = FakeBlueZ()
bt = SB.Bluetooth(run=bz2)                     # emit = sender_net.emit (prawdziwe gniazdo)
net.on_bt = bt.on_command

async def t_e2e():
    loop = asyncio.get_running_loop()
    assert net.open_sock(loop)
    net.emit(proto.T_PING, 255, time.perf_counter_ns())    # the receiver learns the sender address
    net.scfg_t = time.monotonic()
    task = asyncio.create_task(bt.loop())
    for _k in range(50):
        await asyncio.sleep(0.05)
        if RB.supported():
            break
    assert RB.supported() and RB.status.state == BTS_OK and RB.devices == ()
    assert RB.keepalive()
    for _k in range(50):
        await asyncio.sleep(0.05)
        if RB.devices:
            break
    assert [d.mac for d in RB.devices] == [PAD2, PAD, PHONE], RB.devices
    assert RB.pair(PAD)
    for _k in range(60):
        await asyncio.sleep(0.05)
        d = RB.device(PAD)
        if d is not None and d.connected:
            break
    assert RB.device(PAD).connected and RB.device(PAD).paired and RB.status.result == BTR_OK, RB.status
    assert bz2.calls.count(("call", SB.dev_path("/org/bluez/hci0", PAD), "org.bluez.Device1", "Pair")) == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    net.close_sock()

asyncio.run(t_e2e())
S.stop.set()
ok("end to end over UDP: state -> keepalive -> list -> Pair (3 packets, one Pair) -> pad connected in the window")
print("BT OK")
