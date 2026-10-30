"""Sender: kontroler Bluetooth i link policy bez SNIFF.
  - wybór kontrolera (dongle USB zamiast wbudowanego) i brak SNIFF działały tylko przy działającym receiverze: telefon
    startujący przy wyłączonym PC (pad budzi PC przez Wake-on-LAN) łączył pady przez wbudowane radio / z SNIFF
  - jądro przywraca SNIFF przy każdym włączeniu kontrolera (Write Default Link Policy) - jednorazowe ustawienie przy
    wyborze znikało, gdy BlueZ włączał kontroler chwilę później (odblokowanie rfkill, Powered, restart bluetoothd)
python3 tests/test_bt_link_policy_tx.py sender"""
import asyncio, ctypes, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_btadapter as A
import sender_bt as SB
import sender_net as net

ok = lambda m: print("OK  ", m)
TMP = Path(tempfile.mkdtemp())
C.BT_FILE = str(TMP / "bt.json")
SNIFF = A.HCI_LP_RSWITCH | A.HCI_LP_SNIFF


# ---------- układ struct hci_dev_info (include/net/bluetooth/hci_sock.h) ----------
class HciDevInfo(ctypes.Structure):
    _fields_ = [("dev_id", ctypes.c_uint16), ("name", ctypes.c_char * 8), ("bdaddr", ctypes.c_uint8 * 6),
                ("flags", ctypes.c_uint32), ("type", ctypes.c_uint8), ("features", ctypes.c_uint8 * 8),
                ("pkt_type", ctypes.c_uint32), ("link_policy", ctypes.c_uint32), ("link_mode", ctypes.c_uint32),
                ("acl_mtu", ctypes.c_uint16), ("acl_pkts", ctypes.c_uint16), ("sco_mtu", ctypes.c_uint16),
                ("sco_pkts", ctypes.c_uint16), ("stat", ctypes.c_uint32 * 10)]


assert ctypes.sizeof(HciDevInfo) == A.DEV_INFO_SIZE
assert (HciDevInfo.flags.offset, HciDevInfo.link_policy.offset) == (A.DEV_FLAGS_AT, A.DEV_POLICY_AT)
assert A.HCIGETDEVINFO == (2 << 30) | (4 << 16) | (ord("H") << 8) | 211


class FakeSock:
    def __init__(s, *a): pass
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def fileno(s): return 99


asked = []


def fake_ioctl(fd, req, buf, mutate):
    assert req == A.HCIGETDEVINFO and len(buf) == A.DEV_INFO_SIZE and mutate
    info = HciDevInfo.from_buffer(buf)
    asked.append(info.dev_id)
    info.flags, info.link_policy = A.HCI_UP | 0x10, SNIFF


real_socket, real_ioctl = A.socket.socket, A.fcntl.ioctl
A.socket.socket, A.fcntl.ioctl = FakeSock, fake_ioctl
try:
    assert A.dev_info("hci1") == (True, SNIFF) and asked == [1]
finally:
    A.socket.socket, A.fcntl.ioctl = real_socket, real_ioctl
ok("HCIGETDEVINFO: rozmiar i pola struct hci_dev_info (flagi, link policy) jak w jądrze")

# ---------- ensure_no_sniff: tylko gdy kontroler włączony i ma SNIFF ----------
done = []
sniff = lambda name: done.append(name) or True
assert A.ensure_no_sniff("hci1", sniff, lambda n: (True, SNIFF)) is True and done == ["hci1"]
assert A.ensure_no_sniff("hci1", sniff, lambda n: (True, A.HCI_LP_RSWITCH)) is None, "już bez SNIFF"
assert A.ensure_no_sniff("hci1", sniff, lambda n: (False, SNIFF)) is None, "wyłączony - po włączeniu"
assert A.ensure_no_sniff("hci1", sniff, lambda n: None) is True and done == ["hci1", "hci1"], "bez odczytu - jak dawniej"
assert A.ensure_no_sniff("hci1", lambda n: False, lambda n: (True, SNIFF)) is False
ok("brak SNIFF ustawiany tylko, gdy kontroler działa i jądro przywróciło SNIFF")


# ---------- pętla bez receivera: wybór kontrolera i SNIFF ----------
class Sys:
    def __init__(s):
        s.root = Path(tempfile.mkdtemp())
        s.cls = s.root / "class/bluetooth"
        s.cls.mkdir(parents=True)

    def add(s, name, usb):
        if usb:
            u = s.root / f"devices/platform/soc/a8f8800.usb/xhci-hcd/usb1/1-{name[3:]}"
            u.mkdir(parents=True)
            for f, v in (("idVendor", "2357"), ("idProduct", "0604"), ("product", "UB5A")):
                (u / f).write_text(v + "\n")
            dev = u / f"{u.name}:1.0/bluetooth" / name
        else:
            p = s.root / "devices/platform/soc/898000.serial/serial0/serial0-0"
            (p / "of_node").mkdir(parents=True, exist_ok=True)
            (p / "of_node/compatible").write_text("qcom,wcn3990-bt\0")
            dev = p / "bluetooth" / name
        (dev / "rfkill1").mkdir(parents=True)
        (dev / "rfkill1/soft").write_text("0\n")
        (s.cls / name).symlink_to(dev)

    def blocked(s, name):
        return A.is_blocked(name, s.cls)


calls, sent = [], []


async def run(argv, timeout):
    calls.append(list(argv)[3:])
    return 0, "", ""


async def main():
    fs = Sys(); fs.add("hci0", usb=False); fs.add("hci1", usb=True)
    policy = {"hci1": SNIFF}
    real = A.dev_info, A.no_sniff, A.SYSFS

    def no_sniff(name):
        policy[name] = A.HCI_LP_RSWITCH
        return True
    A.dev_info = lambda name: (True, policy.get(name, SNIFF))
    A.no_sniff = no_sniff
    SB.IDLE_S = 0.2
    try:
        b = SB.Bluetooth(run=run, emit=lambda *a: sent.append(a), sysfs=fs.cls)
        net.scfg_t = 0.0                              # receiver nie działa (PC wyłączony)
        task = asyncio.create_task(b.loop())
        await asyncio.sleep(0.3)
        assert b.pick is not None and b.pick.name == "hci1", "dongle wybrany bez receivera"
        assert fs.blocked("hci0") and not fs.blocked("hci1") and policy["hci1"] == A.HCI_LP_RSWITCH
        assert ["set-property", "org.bluez", "/org/bluez/hci1", "org.bluez.Adapter1", "Powered", "b", "true"] in calls
        assert not sent, "bez receivera nic nie idzie do sieci"
        policy["hci1"] = SNIFF                        # bluetoothd włączył kontroler od nowa - jądro przywróciło SNIFF
        await asyncio.sleep(0.5)
        assert policy["hci1"] == A.HCI_LP_RSWITCH, "SNIFF poprawiony przy kolejnym sprawdzeniu"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        A.dev_info, A.no_sniff, A.SYSFS = real
        SB.IDLE_S = 5.0


asyncio.run(main())
ok("bez receivera: dongle wybrany, wbudowany zablokowany, SNIFF wyłączony i pilnowany po ponownym włączeniu")
