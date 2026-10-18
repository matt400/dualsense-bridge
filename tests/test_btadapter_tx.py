"""Nadajnik: wybór kontrolera Bluetooth (sender_btadapter) - sztuczny /sys z dowiązaniami jak w jądrze, tryby
auto / builtin / manual, wpięcie i wyjęcie dongla w trakcie pracy, polecenie BT_ADAPTER z okna i bity btstate."""
import asyncio, json, os, sys, tempfile
from pathlib import Path
TX = os.path.abspath(sys.argv[1])
sys.path.insert(0, TX)
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_btadapter as A
import sender_bt as SB
from proto import (BT_ADAPTER, BTA_AUTO, BTA_BUILTIN, BTA_MANUAL, BTF_HAS_BUILTIN, BTF_HAS_USB, BTF_MODE_SHIFT,
                   BTF_ON_USB, BTR_OK, T_BTSTATE)
ok = lambda m: print("OK  ", m)
TMP = Path(tempfile.mkdtemp())
C.BT_FILE = str(TMP / "bt.json")                # nie w folderze nadajnika


class Sys:
    """/sys/class/bluetooth: kontrolery na UART (wbudowany), USB wymiennym i USB stałym (laptop)."""
    PARENTS = {"uart": "devices/platform/soc/898000.serial/serial0/serial0-0",
               "usb": "devices/platform/soc/a8f8800.usb/xhci-hcd/usb1/1-1",
               "fixed": "devices/pci0000:00/0000:00:14.0/usb3/3-10"}

    def __init__(s):
        s.root = Path(tempfile.mkdtemp())
        s.cls = s.root / "class/bluetooth"
        s.cls.mkdir(parents=True)
        s.n = 0

    def add(s, name, bus):
        usbdev = s.root / s.PARENTS[bus]
        dev = (usbdev / f"{usbdev.name}:1.0" if bus != "uart" else usbdev) / "bluetooth" / name
        (dev / f"rfkill{s.n}").mkdir(parents=True)
        (dev / f"rfkill{s.n}/soft").write_text("0\n")
        if bus != "uart":
            (usbdev / "removable").write_text("fixed\n" if bus == "fixed" else "unknown\n")
        s.n += 1
        (s.cls / name).symlink_to(dev)
        (s.cls / f"{name}:256").symlink_to(dev)       # połączenie - nie kontroler

    def drop(s, name):
        (s.cls / name).unlink()
        (s.cls / f"{name}:256").unlink()

    def blocked(s, name):
        return next((s.cls / name).glob("rfkill*/soft")).read_text().strip() == "1"


# ---------- wybór ----------
fs = Sys(); fs.add("hci0", "uart"); fs.add("hci1", "usb")
ctrls = A.scan(fs.cls)
assert [(c.name, c.rank) for c in ctrls] == [("hci0", A.BUILTIN), ("hci1", A.USB)], ctrls
assert A.choose(ctrls, BTA_AUTO).name == "hci1" and A.choose(ctrls, BTA_BUILTIN).name == "hci0"
assert A.choose(ctrls, BTA_MANUAL) is None and A.choose((), BTA_AUTO) is None
sniffed = []
sniff = lambda name: sniffed.append(name) or True
assert A.apply(ctrls, BTA_AUTO, fs.cls, sniff).name == "hci1"
assert fs.blocked("hci0") and not fs.blocked("hci1") and sniffed == ["hci1"]
ok("telefon / płytka: dongle USB przed wbudowanym (UART), wbudowany zablokowany, dongle bez SNIFF")

fs = Sys(); fs.add("hci0", "usb"); fs.add("hci1", "uart")        # inna kolejność wykrywania - decyduje szyna
assert A.apply(A.scan(fs.cls), BTA_AUTO, fs.cls, sniff).name == "hci0" and fs.blocked("hci1")
ok("numery hciN zamienione: nadal dongle")

fs = Sys(); fs.add("hci0", "fixed"); fs.add("hci1", "usb")        # laptop: wbudowany Bluetooth też na USB
assert A.choose(A.scan(fs.cls), BTA_AUTO).name == "hci1" and A.choose(A.scan(fs.cls), BTA_BUILTIN).name == "hci0"
ok("laptop: wbudowany na stałym porcie USB odróżniony od dongla (removable)")

fs = Sys(); fs.add("hci0", "uart")
(next((fs.cls / "hci0").glob("rfkill*/soft"))).write_text("1\n")  # blokada z poprzedniego startu (rfkill ją pamięta)
assert A.apply(A.scan(fs.cls), BTA_AUTO, fs.cls, sniff).name == "hci0" and not fs.blocked("hci0")
ok("jeden kontroler: odblokowany - wyjęty dongle nie zostawia systemu bez Bluetooth")

fs = Sys(); fs.add("hci0", "uart"); fs.add("hci1", "usb")
next((fs.cls / "hci1").glob("rfkill*/soft")).write_text("1\n")
assert A.apply(A.scan(fs.cls), BTA_MANUAL, fs.cls, sniff) is None and fs.blocked("hci1") and not fs.blocked("hci0")
A.unblock_all(A.scan(fs.cls), fs.cls)
assert not fs.blocked("hci1")
ok("ręcznie: nic nie zmienia; przejście na ręczny z okna odblokowuje wszystko")

assert A.mode_from("BUILTIN") == BTA_BUILTIN and A.mode_from("cokolwiek") == BTA_AUTO
assert A.load_mode(str(TMP / "brak.json"), BTA_BUILTIN) == BTA_BUILTIN
(TMP / "zly.json").write_text("[1]")
assert A.load_mode(str(TMP / "zly.json"), BTA_AUTO) == BTA_AUTO
A.save_mode(str(TMP / "m.json"), BTA_MANUAL)
assert json.loads((TMP / "m.json").read_text()) == {"mode": "manual"} and A.load_mode(str(TMP / "m.json"), BTA_AUTO) == BTA_MANUAL
ok("tryb: sender.toml domyślnie, bt.json (z okna) ma pierwszeństwo, zły plik = domyślny")

# ---------- nadajnik: panel Bluetooth ----------
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
        fs.add("hci1", "usb")                                     # dongle wpięty w trakcie pracy
        calls.clear()
        await b.refresh()
        assert b.pick.name == "hci1" and b.adapter == "/org/bluez/hci1" and fs.blocked("hci0")
        assert ["set-property", "org.bluez", "/org/bluez/hci1", "org.bluez.Adapter1", "Powered", "b", "true"] in calls
        b.send(full=False)
        assert out[-1][1][1] & (BTF_HAS_USB | BTF_ON_USB) == BTF_HAS_USB | BTF_ON_USB
        ok("wpięty dongle przejmuje panel bez restartu (BlueZ: hci1, włączony), bity btstate")

        b.on_command(BT_ADAPTER, BTA_BUILTIN, bytes(6))
        await b.busy
        assert b.last[:2] == (BT_ADAPTER, BTR_OK) and b.pick.name == "hci0" and fs.blocked("hci1")
        assert A.load_mode(C.BT_FILE, BTA_AUTO) == BTA_BUILTIN, "wybór z okna zapisany"
        assert out[-1][0] == T_BTSTATE and out[-1][1][1] >> BTF_MODE_SHIFT == BTA_BUILTIN
        b.dups.clear()
        b.on_command(BT_ADAPTER, BTA_MANUAL, bytes(6))
        await b.busy
        assert not fs.blocked("hci0") and not fs.blocked("hci1") and b.pick is None
        b.dups.clear()
        b.on_command(BT_ADAPTER, 99, bytes(6))
        await b.busy
        assert b.last[1] != BTR_OK and b.mode == BTA_MANUAL, "nieznany tryb odrzucony"
        ok("BT_ADAPTER z okna: wbudowany / ręcznie, zapis w bt.json, zły tryb odrzucony")

        b.dups.clear()
        b.on_command(BT_ADAPTER, BTA_AUTO, bytes(6))
        await b.busy
        fs.drop("hci1")                                            # dongle wyjęty
        await b.refresh()
        assert b.pick.name == "hci0" and not fs.blocked("hci0")
        ok("wyjęty dongle: wbudowany wraca sam")

        plain = SB.Bluetooth(run=run, emit=emit)                   # bez /sys (testy, starszy układ) - bez wyboru
        await plain.refresh()
        plain.send(full=False)
        assert out[-1][1][1] >> BTF_MODE_SHIFT == 0, "tryb 0 = okno chowa przełącznik"
    finally:
        A.no_sniff = A_no_sniff

asyncio.run(main())
assert SB.parse_objects(objects("hci0", "hci1"), "/org/bluez/hci1")[1] == "/org/bluez/hci1"
assert SB.parse_objects(objects("hci0"), "/org/bluez/hci1")[1] == "/org/bluez/hci0", "brak wybranego w BlueZ - pierwszy"
ok("BlueZ: panel na wybranym kontrolerze, gdy BlueZ go zna")
