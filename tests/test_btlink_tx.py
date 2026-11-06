"""Sender: Bluetooth link quality of the pads (RSSI, link quality) for the Diagnostics tab - HCI frames, connection list, reader, btlink packet.
    python3 tests/test_btlink_tx.py sender"""
import asyncio, struct, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_bt as SB
import sender_btlink as BL
from proto import BODY, BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTL_LQ, BTL_RSSI, T_BTLINK, mac_bytes

from chk import check, fails, raises                          # noqa: E402  (shared helper, tests/chk.py)


PAD, PAD2, OTHER = "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "11:22:33:44:55:66"

# ---------- ramki HCI ----------
check(BL.hci_command(BL.OPCODE_RSSI, 0x2A).hex() == "010514022a00", "Read RSSI: packet 01 | opcode 0x1405 LE | length 2 | handle")
check(BL.hci_command(BL.OPCODE_LINK_QUALITY, 0x0102).hex() == "010314020201", "Read Link Quality: opcode 0x1403")


def parse_reply(packet, opcode, handle):
    return BL.classify_reply(packet, opcode, handle)[1]


def complete(opcode, handle, value, status=0):
    return bytes([4, 0x0E, 7, 1]) + struct.pack("<HBH", opcode, status, handle) + bytes([value & 0xFF])


check(parse_reply(complete(BL.OPCODE_RSSI, 42, -5), BL.OPCODE_RSSI, 42) == b"\xfb", "Command Complete: the value after status and handle (-5 dB)")
check(parse_reply(complete(BL.OPCODE_RSSI, 42, 0), BL.OPCODE_RSSI, 43) is None, "wrong handle: a reply for another pad is skipped")
check(parse_reply(complete(BL.OPCODE_LINK_QUALITY, 42, 9), BL.OPCODE_RSSI, 42) is None, "wrong opcode: a reply to another command is skipped")
check(parse_reply(complete(BL.OPCODE_RSSI, 42, 0, status=1), BL.OPCODE_RSSI, 42) is None, "status != 0 (unknown command): no value")
check(parse_reply(bytes([4, 0x0F, 4, 0, 1, 0]), BL.OPCODE_RSSI, 42) is None and parse_reply(b"\x04\x0e", BL.OPCODE_RSSI, 1) is None,
      "Command Status and a too short packet: no value")

# ---------- controller connection list (HCIGETCONNLIST) ----------
def entry(handle, mac, link_type):
    return struct.pack("<H6sBBHI", handle, bytes.fromhex(mac.replace(":", ""))[::-1], link_type, 1, 1, 0)


def conn_list(*entries):
    return struct.pack("<HH", 0, len(entries)) + b"".join(entries) + bytes(16 * (BL.CONN_MAX - len(entries)))


check(BL.find_handle(conn_list(entry(7, OTHER, 1), entry(42, PAD, 1)), PAD) == 42, "pad handle among several connections")
check(BL.find_handle(conn_list(entry(9, PAD, 0x80), entry(42, PAD, 1)), PAD) == 42, "ACL connection wins over LE of the same address")
check(BL.find_handle(conn_list(entry(9, PAD, 0x80)), PAD) == 9, "LE alone: better than nothing")
check(BL.find_handle(conn_list(entry(7, OTHER, 1)), PAD) is None and BL.find_handle(b"", PAD) is None, "pad not on the list / empty buffer")
check(BL.find_handle(conn_list(entry(7, PAD, 1)), "nie-mac") is None, "a bad address string does not crash the read")
check(BL.find_handle(struct.pack("<HH", 0, 200) + entry(5, OTHER, 1), PAD) is None, "a bad entry count (more than the buffer) does not read past it")


# ---------- reader with a fake transport ----------
class Fake:
    """Two controllers; the pad is on hci1. rssi / quality = what the commands return (None = unsupported)."""
    def __init__(self, rssi=-5, quality=220, on=1):
        self.rssi, self.quality, self.on = rssi, quality, on
        self.calls = []

    def devices(self):
        return [0, 1]

    def handle(self, dev, mac):
        self.calls.append(("handle", dev, mac))
        return 42 if dev == self.on and mac == PAD else None

    def command(self, dev, opcode, handle):
        self.calls.append(("command", dev, opcode, handle))
        v = self.rssi if opcode == BL.OPCODE_RSSI else self.quality
        if v is None:
            return None
        return struct.pack("b", v) if opcode == BL.OPCODE_RSSI else bytes([v])


fake = Fake()
reader = BL.LinkReader(fake)
check(reader.read(PAD) == BL.Link(-5, 220, 1), "read: RSSI -5 dB, quality 220, controller hci1")
check([c for c in fake.calls if c[0] == "handle"] == [("handle", 0, PAD), ("handle", 1, PAD)], "the first read looks for the pad across the controllers")
fake.calls.clear()
check(reader.read(PAD) is not None and fake.calls[0] == ("handle", 1, PAD), "the next read starts from the remembered controller")
check(reader.read(PAD2) is None and reader.sample([PAD, PAD2]) == [(PAD, BL.Link(-5, 220, 1))], "a pad not connected on any controller is skipped")
check(BL.LinkReader(None).read(PAD) is None, "no transport (no permission, tests) - no reading")

only_rssi = BL.LinkReader(Fake(rssi=-12, quality=None))
check(only_rssi.read(PAD) == BL.Link(-12, None, 1), "controller without Read Link Quality: RSSI only")
none = BL.LinkReader(Fake(rssi=None, quality=None))
check(none.read(PAD) == BL.Link(None, None, 1) and none.warned, "controller without both commands: empty reading + one warning in the log")

# ---------- real transport: connection list through ioctl, command through the socket (fakes) ----------
raw = BL.RawHci(BL.Path("/nonexistent"))
check(raw.devices() == [], "no sysfs: no controllers")
import tempfile, pathlib
d = pathlib.Path(tempfile.mkdtemp())
for n in ("hci0", "hci2", "rfkill1", "hci10"):
    (d / n).mkdir()
check(BL.RawHci(d).devices() == [0, 2, 10], "controllers from sysfs: hciN ascending, no junk")


class DummySock:
    def __init__(self, replies):
        self.sent, self.replies = [], list(replies)

    def send(self, data):
        self.sent.append(data)

    def recv(self, n):
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        pass


def fake_ioctl(sock, req, buf, mutate):
    assert req == BL.HCIGETCONNLIST and struct.unpack_from("<HH", buf, 0) == (3, BL.CONN_MAX), "request: controller 3, room for CONN_MAX entries"
    data = conn_list(entry(42, PAD, 1))
    buf[:len(data)] = data[:len(buf)]


raw = BL.RawHci(d)
raw.query = DummySock([])
BL.fcntl.ioctl, real_ioctl = fake_ioctl, BL.fcntl.ioctl
try:
    check(raw.handle(3, PAD) == 42 and raw.handle(3, OTHER) is None, "RawHci.handle: uchwyt z HCIGETCONNLIST")
finally:
    BL.fcntl.ioctl = real_ioctl

raw.socks[3] = DummySock([b"\x04\x0e\x04\x01\x01\x10\x00", complete(BL.OPCODE_RSSI, 41, 0), complete(BL.OPCODE_RSSI, 42, -9)])
check(raw.command(3, BL.OPCODE_RSSI, 42) == b"\xf7" and raw.socks[3].sent == [BL.hci_command(BL.OPCODE_RSSI, 42)],
      "RawHci.command: sends the command and skips other replies (another opcode / handle)")
raw.socks[3] = DummySock([OSError("device gone")])
check(raw.command(3, BL.OPCODE_RSSI, 42) is None and 3 not in raw.socks, "socket error: no value, socket forgotten (created again)")

# ---------- event filter: the kernel may reject the struct size ----------
class Picky:
    def __init__(self, ok_len):
        self.ok_len, self.tried = ok_len, []

    def setsockopt(self, level, opt, data):
        self.tried.append(len(data))
        if len(data) != self.ok_len:
            raise OSError(22, "Invalid argument")


p = Picky(14)
BL.set_filter(p)
check(p.tried == [16, 14], f"filter: after 16 B is refused tries 14 B ({p.tried})")
p = Picky(24)
BL.set_filter(p)
check(p.tried == [16, 14, 24], "filter: tries three sizes in turn")
raises(OSError, lambda: BL.set_filter(Picky(99)), "filter: all sizes refused raises an error")

# ---------- Bluetooth.send_links: btlink packet, rate limit ----------
async def t_send():
    emitted = []
    b = SB.Bluetooth(run=None, emit=lambda *a: emitted.append(a), links=BL.LinkReader(Fake()))
    b.devices = [SB.Device(PAD, "DualSense", None, BTD_PAIRED | BTD_CONNECTED | BTD_PAD),
                 SB.Device(PAD2, "DualSense", None, BTD_PAIRED | BTD_PAD),               # not connected
                 SB.Device(OTHER, "Słuchawki", None, BTD_PAIRED | BTD_CONNECTED)]         # not a pad
    await b.send_links(100.0)
    check(len(emitted) == 1 and emitted[0][0] == T_BTLINK and emitted[0][1] == 255, f"one btlink: only the connected pad ({len(emitted)})")
    args = emitted[0][2:]
    check(args == (mac_bytes(PAD), -5, 220, BTL_RSSI | BTL_LQ, 1), f"fields: MAC, RSSI, quality, flags, hci: {args}")
    check(BODY[T_BTLINK].unpack(BODY[T_BTLINK].pack(*args)) == args, "the frame fits in BODY[T_BTLINK]")
    await b.send_links(100.5)
    check(len(emitted) == 1, "at most once per LINK_S")
    await b.send_links(100.0 + SB.LINK_S)
    check(len(emitted) == 2, "after LINK_S a reading again")

    emitted.clear()
    b2 = SB.Bluetooth(run=None, emit=lambda *a: emitted.append(a), links=BL.LinkReader(Fake(rssi=None, quality=140)))
    b2.devices = b.devices[:1]
    await b2.send_links(10.0)
    check(emitted[0][2:] == (mac_bytes(PAD), 0, 140, BTL_LQ, 1), "quality only: BTL_RSSI flag off, RSSI = 0")
    emitted.clear()
    b3 = SB.Bluetooth(run=None, emit=lambda *a: emitted.append(a), links=BL.LinkReader(Fake(on=9)))
    b3.devices = b.devices[:1]
    await b3.send_links(10.0)
    check(not emitted, "a pad the controller does not know (e.g. disconnected a moment ago): no packet")

    check(SB.Bluetooth(run=None, emit=lambda *a: None).links.hci is None, "no sysfs (tests) means no HCI transport - the loop does not read the link")

asyncio.run(t_send())

print("BTLINK TX", "FAIL" if fails else "OK")
sys.exit(1 if fails else 0)
