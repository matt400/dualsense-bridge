"""Sender: Bluetooth controller choice (sender_btadapter) - a fake /sys with symlinks as in the kernel: device
names, auto / pick / manual modes, plugging and unplugging a dongle while running, window commands (BT_ADAPTER,
BT_ADAPTER_USE), the controller list (btctrl) and btstate bits."""
import asyncio, json, os, sys, tempfile
from pathlib import Path
TX = os.path.abspath(sys.argv[1])
sys.path.insert(0, TX)
sys.path.insert(1, TX + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_btadapter as A
import sender_bt as SB
from proto import (BT_ADAPTER, BT_ADAPTER_USE, BTA_AUTO, BTA_MANUAL, BTA_PICK, BTC_ACTIVE, BTC_BLOCKED, BTC_FIXED,
                   BTC_PICKED, BTC_USB, BTF_HAS_BUILTIN, BTF_HAS_USB, BTF_MODE_SHIFT, BTF_ON_USB, BTR_NOT_FOUND, BTR_OK,
                   T_BTCTRL, T_BTSTATE, cstr)
from chk import ok
TMP = Path(tempfile.mkdtemp())
C.BT_FILE = str(TMP / "bt.json")                # not in the sender folder
DONGLE = {"vid": "2357", "pid": "0604", "product": "TP-Link UB5A Adapter", "maker": "Realtek"}


class Sys:
    """/sys/class/bluetooth: controllers on UART (built-in, device tree), removable USB and fixed USB (laptop)."""

    def __init__(s):
        s.root = Path(tempfile.mkdtemp())
        s.cls = s.root / "class/bluetooth"
        s.cls.mkdir(parents=True)
        s.n = 0

    def add(s, name, bus, vid="0bda", pid="8771", product="", maker="", serial="", compat="qcom,wcn3990-bt"):
        s.n += 1
        if bus == "uart":
            parent = s.root / "devices/platform/soc/898000.serial/serial0/serial0-0"
            (parent / "of_node").mkdir(parents=True, exist_ok=True)
            if compat:
                (parent / "of_node/compatible").write_text(compat + "\0")
            dev = parent / "bluetooth" / name
        else:
            usb = s.root / ("devices/pci0000:00/0000:00:14.0/usb3/3-10" if bus == "fixed"
                            else f"devices/platform/soc/a8f8800.usb/xhci-hcd/usb1/1-{s.n}")
            usb.mkdir(parents=True)
            for f, v in (("idVendor", vid), ("idProduct", pid), ("product", product), ("manufacturer", maker),
                         ("serial", serial), ("removable", "fixed" if bus == "fixed" else "unknown")):
                if v:
                    (usb / f).write_text(v + "\n")
            dev = usb / f"{usb.name}:1.0/bluetooth" / name
        (dev / f"rfkill{s.n}").mkdir(parents=True)
        (dev / f"rfkill{s.n}/soft").write_text("0\n")
        (s.cls / name).symlink_to(dev)
        (s.cls / f"{name}:256").symlink_to(dev)       # a connection - not a controller

    def drop(s, name):
        (s.cls / name).unlink()
        (s.cls / f"{name}:256").unlink()

    def blocked(s, name):
        return A.is_blocked(name, s.cls)


sniffed = []
sniff = lambda name: sniffed.append(name) or True

# ---------- nazwy i rodzaje ----------
fs = Sys(); fs.add("hci0", "uart"); fs.add("hci1", "usb", **DONGLE)
ctrls = A.scan(fs.cls)
assert [(c.name, c.rank, c.label) for c in ctrls] == [("hci0", A.BUILTIN, "Qualcomm WCN3990"),
                                                      ("hci1", A.USB, "Realtek TP-Link UB5A Adapter")], ctrls
assert ctrls[0].key == "builtin:qcom,wcn3990-bt" and ctrls[1].key == "usb:2357:0604", [c.key for c in ctrls]
fs2 = Sys(); fs2.add("hci0", "usb", vid="0a12", pid="0001"); fs2.add("hci1", "uart", compat="")
fs2.add("hci2", "usb", product="Bluetooth Radio", maker="Realtek", serial="00E04C239987")
c = A.scan(fs2.cls)
assert [x.label for x in c] == ["USB 0a12:0001", "Bluetooth", "Realtek Bluetooth Radio"], [x.label for x in c]
assert c[2].key == "usb:0bda:8771:00E04C239987", "the serial number tells two identical dongles apart"
ok("names: device tree (Qualcomm WCN3990), USB descriptor (manufacturer + product), no name - vid:pid / generic")

# ---------- choice ----------
assert A.choose(ctrls, BTA_AUTO).name == "hci1" and A.choose(ctrls, BTA_MANUAL) is None and A.choose((), BTA_AUTO) is None
assert A.choose(ctrls, BTA_PICK, "builtin:qcom,wcn3990-bt").name == "hci0"
assert A.choose(ctrls, BTA_PICK, A.RULE_BUILTIN).name == "hci0", "sender.toml: builtin"
assert A.choose(ctrls, BTA_PICK, "usb:dead:beef").name == "hci1", "the selected one is missing - like auto"
assert A.apply(ctrls, BTA_AUTO, "", fs.cls, sniff).name == "hci1"
assert fs.blocked("hci0") and not fs.blocked("hci1") and sniffed == ["hci1"]
ok("auto: dongle before the built-in one, the rest blocked, the selected one without SNIFF; pick by device key")

fs = Sys(); fs.add("hci0", "usb"); fs.add("hci1", "uart")        # a different detection order - the bus decides
assert A.apply(A.scan(fs.cls), BTA_AUTO, "", fs.cls, sniff).name == "hci0" and fs.blocked("hci1")
fs = Sys(); fs.add("hci0", "fixed", product="Intel Wireless"); fs.add("hci1", "usb")   # laptop
assert A.choose(A.scan(fs.cls), BTA_AUTO).name == "hci1" and A.scan(fs.cls)[0].rank == A.USB_FIXED
ok("hciN numbers swapped: still the dongle; laptop: built-in on a fixed USB port told apart from a dongle")

fs = Sys(); fs.add("hci0", "uart")
(next((fs.cls / "hci0").glob("rfkill*/soft"))).write_text("1\n")  # block left from the previous start (rfkill remembers it)
assert A.apply(A.scan(fs.cls), BTA_AUTO, "", fs.cls, sniff).name == "hci0" and not fs.blocked("hci0")
fs = Sys(); fs.add("hci0", "uart"); fs.add("hci1", "usb")
A.set_blocked("hci1", True, fs.cls)
assert A.apply(A.scan(fs.cls), BTA_MANUAL, "", fs.cls, sniff) is None and fs.blocked("hci1")
A.unblock_all(A.scan(fs.cls), fs.cls)
assert not fs.blocked("hci1")
ok("one controller: unblocked; manual: changes nothing, switching to manual unblocks everything")

assert A.mode_from("BUILTIN") == (BTA_PICK, A.RULE_BUILTIN) and A.mode_from("cokolwiek") == (BTA_AUTO, "")
assert A.load_mode(str(TMP / "brak.json"), (BTA_MANUAL, "")) == (BTA_MANUAL, "")
(TMP / "zly.json").write_text("[1]")
assert A.load_mode(str(TMP / "zly.json"), (BTA_AUTO, "")) == (BTA_AUTO, "")
(TMP / "bez.json").write_text('{"mode": "pick"}')
assert A.load_mode(str(TMP / "bez.json"), (BTA_AUTO, "")) == (BTA_AUTO, ""), "pick without a device = default"
A.save_mode(str(TMP / "m.json"), BTA_PICK, "usb:2357:0604")
assert json.loads((TMP / "m.json").read_text()) == {"mode": "pick", "device": "usb:2357:0604"}
assert A.load_mode(str(TMP / "m.json"), (BTA_AUTO, "")) == (BTA_PICK, "usb:2357:0604")
ok("mode: sender.toml by default, bt.json (from the window, with the device) wins, a bad file = default")

# ---------- sender: Bluetooth panel ----------
out = []
emit = lambda t, i, *v: out.append((t, v))
calls = []


def objects(*names):
    return json.dumps({"data": [{f"/org/bluez/{n}": {"org.bluez.Adapter1": {"Powered": {"type": "b", "data": True}}}
                                 for n in names}]})


async def run(argv, timeout):
    argv = list(argv)
    calls.append(argv[3:])
    if argv[-1] == "GetManagedObjects":
        return 0, objects(*sorted(p.name for p in fs.cls.glob("hci*") if ":" not in p.name)), ""
    return 0, "", ""


def ctrl_rows():
    """Ostatnia runda btctrl -> [(flagi, N, nazwa)]."""
    rows = [v for t, v in out if t == T_BTCTRL]
    gen = rows[-1][0]
    return [(f, n, cstr(name)) for g, _k, _tot, f, n, name in rows if g == gen]


async def main():
    global fs
    fs = Sys(); fs.add("hci0", "uart")
    A_no_sniff, A.no_sniff = A.no_sniff, sniff
    try:
        b = SB.Bluetooth(run=run, emit=emit, sysfs=fs.cls)
        await b.refresh()
        assert b.pick.name == "hci0" and b.adapter == "/org/bluez/hci0"
        b.send(full=False)
        flags = out[-1][1][1]
        assert flags & BTF_HAS_BUILTIN and not flags & (BTF_HAS_USB | BTF_ON_USB) and flags >> BTF_MODE_SHIFT == BTA_AUTO
        assert not [t for t, _v in out if t == T_BTCTRL], "controller list only while the panel is open"
        fs.add("hci1", "usb", **DONGLE)                           # dongle plugged in while running
        calls.clear()
        await b.refresh()
        assert b.pick.name == "hci1" and b.adapter == "/org/bluez/hci1" and fs.blocked("hci0")
        assert ["set-property", "org.bluez", "/org/bluez/hci1", "org.bluez.Adapter1", "Powered", "b", "true"] in calls
        b.send(full=True)
        assert ctrl_rows() == [(BTC_BLOCKED, 0, "Qualcomm WCN3990"),
                               (BTC_USB | BTC_ACTIVE, 1, "Realtek TP-Link UB5A Adapter")], ctrl_rows()
        st = [v for t, v in out if t == T_BTSTATE][-1]
        assert st[1] & (BTF_HAS_USB | BTF_ON_USB) == BTF_HAS_USB | BTF_ON_USB
        ok("a plugged-in dongle takes over the panel without a restart; controller list with names and state (btctrl), btstate bits")

        b.on_command(BT_ADAPTER_USE, 0, bytes(6))                # "Use" on the built-in one
        await b.busy
        assert b.last[:2] == (BT_ADAPTER_USE, BTR_OK) and b.pick.name == "hci0" and fs.blocked("hci1")
        assert A.load_mode(C.BT_FILE, (BTA_AUTO, "")) == (BTA_PICK, "builtin:qcom,wcn3990-bt"), "zapisany po kluczu"
        b.send(full=True)
        assert ctrl_rows()[0][0] == BTC_ACTIVE | BTC_PICKED and ctrl_rows()[1][0] == BTC_USB | BTC_BLOCKED
        b.dups.clear()
        b.on_command(BT_ADAPTER_USE, 7, bytes(6))
        await b.busy
        assert b.last[1] == BTR_NOT_FOUND and b.pick.name == "hci0", "non-existent hci7"
        ok("Use: a specific controller, remembered by device id; unknown number = NOT_FOUND")

        fs.drop("hci0"); fs.drop("hci1")                         # restart telefonu: numery zamienione
        fs.add("hci0", "usb", **DONGLE); fs.add("hci1", "uart")
        await b.refresh()
        assert b.pick.name == "hci1" and b.pick.label == "Qualcomm WCN3990" and fs.blocked("hci0")
        ok("the choice survives swapped hciN numbers (device key, not the number)")

        b.dups.clear()
        b.on_command(BT_ADAPTER, BTA_MANUAL, bytes(6))
        await b.busy
        assert not fs.blocked("hci0") and not fs.blocked("hci1") and b.pick is None
        b.dups.clear()
        for bad in (99, BTA_PICK):                                # BTA_PICK only through BT_ADAPTER_USE
            b.on_command(BT_ADAPTER, bad, bytes(6))
            await b.busy
            b.dups.clear()
            assert b.last[1] != BTR_OK and b.mode == BTA_MANUAL, bad
        b.on_command(BT_ADAPTER, BTA_AUTO, bytes(6))
        await b.busy
        assert b.pick.name == "hci0"
        fs.drop("hci0")                                           # dongle unplugged
        await b.refresh()
        assert b.pick.name == "hci1" and not fs.blocked("hci1")
        ok("manual unblocks everything, a bad mode rejected, auto goes back to the dongle, dongle unplugged - built-in alone")

        # rfkill write error: the selected one unblocked BEFORE blocking the rest, and a failed change is retried
        fs = Sys(); fs.add("hci0", "uart"); fs.add("hci1", "usb", **DONGLE)
        A.set_blocked("hci1", True, fs.cls)                       # dongle zablokowany (np. po trybie wbudowanym)
        real_set, fail = A.set_blocked, [True]

        def flaky(name, block, sysfs=A.SYSFS):
            if fail[0] and name == "hci0" and block:              # blocking the built-in one fails
                raise OSError(5, "Input/output error")
            real_set(name, block, sysfs)
        A.set_blocked = flaky
        try:
            b = SB.Bluetooth(run=run, emit=emit, sysfs=fs.cls)
            calls.clear()
            await b.refresh()
            assert not fs.blocked("hci1") and not fs.blocked("hci0"), "never both radios blocked"
            assert b.adapter_err and b.pick.name == "hci1"
            fail[0] = False
            await b.refresh()                                     # the same set of controllers - retry
            assert fs.blocked("hci0") and not fs.blocked("hci1") and not b.adapter_err
            assert ["set-property", "org.bluez", "/org/bluez/hci1", "org.bluez.Adapter1", "Powered", "b", "true"] in calls
        finally:
            A.set_blocked = real_set
        ok("rfkill error halfway: the selected one already unblocked; a failed change retried on the next refresh")

        plain = SB.Bluetooth(run=run, emit=emit)                   # no /sys (tests, an older layout) - no choice
        await plain.refresh()
        n = len(out)
        plain.send(full=True)
        assert out[-1][1][1] >> BTF_MODE_SHIFT == 0 and not [t for t, _v in out[n:] if t == T_BTCTRL], \
            "mode 0 = the window hides the choice, no controller list"
    finally:
        A.no_sniff = A_no_sniff

asyncio.run(main())
assert SB.parse_objects(objects("hci0", "hci1"), "/org/bluez/hci1")[1] == "/org/bluez/hci1"
assert SB.parse_objects(objects("hci0"), "/org/bluez/hci1")[1] == "/org/bluez/hci0", "brak wybranego w BlueZ - pierwszy"
assert BTC_FIXED == 2
ok("BlueZ: panel on the selected controller when BlueZ knows it")
