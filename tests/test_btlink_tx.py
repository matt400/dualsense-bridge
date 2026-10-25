"""Nadajnik: jakość łącza Bluetooth padów (RSSI, link quality) dla zakładki Diagnostyka - ramki HCI, lista połączeń, czytnik, pakiet btlink.
    python3 tests/test_btlink_tx.py sender"""
import asyncio, struct, sys
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import sender_bt as SB
import sender_btlink as BL
from proto import BODY, BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTL_LQ, BTL_RSSI, T_BTLINK, mac_bytes

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


PAD, PAD2, OTHER = "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "11:22:33:44:55:66"

# ---------- ramki HCI ----------
check(BL.hci_command(BL.OPCODE_RSSI, 0x2A).hex() == "010514022a00", "Read RSSI: pakiet 01 | opcode 0x1405 LE | długość 2 | uchwyt")
check(BL.hci_command(BL.OPCODE_LINK_QUALITY, 0x0102).hex() == "010314020201", "Read Link Quality: opcode 0x1403")


def complete(opcode, handle, value, status=0):
    return bytes([4, 0x0E, 7, 1]) + struct.pack("<HBH", opcode, status, handle) + bytes([value & 0xFF])


check(BL.parse_reply(complete(BL.OPCODE_RSSI, 42, -5), BL.OPCODE_RSSI, 42) == b"\xfb", "Command Complete: wartość po statusie i uchwycie (-5 dB)")
check(BL.parse_reply(complete(BL.OPCODE_RSSI, 42, 0), BL.OPCODE_RSSI, 43) is None, "zły uchwyt: odpowiedź dla innego pada jest pomijana")
check(BL.parse_reply(complete(BL.OPCODE_LINK_QUALITY, 42, 9), BL.OPCODE_RSSI, 42) is None, "zły opcode: odpowiedź na inne polecenie jest pomijana")
check(BL.parse_reply(complete(BL.OPCODE_RSSI, 42, 0, status=1), BL.OPCODE_RSSI, 42) is None, "status != 0 (unknown command): brak wartości")
check(BL.parse_reply(bytes([4, 0x0F, 4, 0, 1, 0]), BL.OPCODE_RSSI, 42) is None and BL.parse_reply(b"\x04\x0e", BL.OPCODE_RSSI, 1) is None,
      "Command Status i za krótki pakiet: brak wartości")

# ---------- lista połączeń kontrolera (HCIGETCONNLIST) ----------
def entry(handle, mac, link_type):
    return struct.pack("<H6sBBHI", handle, bytes.fromhex(mac.replace(":", ""))[::-1], link_type, 1, 1, 0)


def conn_list(*entries):
    return struct.pack("<HH", 0, len(entries)) + b"".join(entries) + bytes(16 * (BL.CONN_MAX - len(entries)))


check(BL.find_handle(conn_list(entry(7, OTHER, 1), entry(42, PAD, 1)), PAD) == 42, "uchwyt pada wśród kilku połączeń")
check(BL.find_handle(conn_list(entry(9, PAD, 0x80), entry(42, PAD, 1)), PAD) == 42, "połączenie ACL wygrywa z LE tego samego adresu")
check(BL.find_handle(conn_list(entry(9, PAD, 0x80)), PAD) == 9, "samo LE: lepsze niż nic")
check(BL.find_handle(conn_list(entry(7, OTHER, 1)), PAD) is None and BL.find_handle(b"", PAD) is None, "pada nie ma na liście / pusty bufor")
check(BL.find_handle(conn_list(entry(7, PAD, 1)), "nie-mac") is None, "zły adres tekstowy nie wywala odczytu")
check(BL.find_handle(struct.pack("<HH", 0, 200) + entry(5, OTHER, 1), PAD) is None, "zła liczba wpisów (więcej niż bufor) nie czyta poza nim")


# ---------- czytnik z atrapą transportu ----------
class Fake:
    """Dwa kontrolery; pad jest na hci1. rssi / quality = co zwracają polecenia (None = nieobsługiwane)."""
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
check(reader.read(PAD) == BL.Link(-5, 220, 1), "odczyt: RSSI -5 dB, jakość 220, kontroler hci1")
check([c for c in fake.calls if c[0] == "handle"] == [("handle", 0, PAD), ("handle", 1, PAD)], "pierwszy odczyt szuka pada po kontrolerach")
fake.calls.clear()
check(reader.read(PAD) is not None and fake.calls[0] == ("handle", 1, PAD), "kolejny odczyt zaczyna od zapamiętanego kontrolera")
check(reader.read(PAD2) is None and reader.sample([PAD, PAD2]) == [(PAD, BL.Link(-5, 220, 1))], "pad niepodłączony na żadnym kontrolerze jest pomijany")
check(BL.LinkReader(None).read(PAD) is None, "bez transportu (brak uprawnień, testy) - brak odczytu")

only_rssi = BL.LinkReader(Fake(rssi=-12, quality=None))
check(only_rssi.read(PAD) == BL.Link(-12, None, 1), "kontroler bez Read Link Quality: sam RSSI")
none = BL.LinkReader(Fake(rssi=None, quality=None))
check(none.read(PAD) == BL.Link(None, None, 1) and none.warned, "kontroler bez obu poleceń: odczyt pusty + jedno ostrzeżenie w logu")

# ---------- prawdziwy transport: lista połączeń przez ioctl, polecenie przez gniazdo (atrapy) ----------
raw = BL.RawHci(BL.Path("/nonexistent"))
check(raw.devices() == [], "brak sysfs: brak kontrolerów")
import tempfile, pathlib
d = pathlib.Path(tempfile.mkdtemp())
for n in ("hci0", "hci2", "rfkill1", "hci10"):
    (d / n).mkdir()
check(BL.RawHci(d).devices() == [0, 2, 10], "kontrolery z sysfs: hciN rosnąco, bez śmieci")


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

    def close(self):
        pass


def fake_ioctl(sock, req, buf, mutate):
    assert req == BL.HCIGETCONNLIST and struct.unpack_from("<HH", buf, 0) == (3, BL.CONN_MAX), "żądanie: kontroler 3, miejsce na CONN_MAX wpisów"
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
      "RawHci.command: wysyła polecenie i pomija cudze odpowiedzi (inny opcode / uchwyt)")
raw.socks[3] = DummySock([OSError("device gone")])
check(raw.command(3, BL.OPCODE_RSSI, 42) is None and 3 not in raw.socks, "błąd gniazda: brak wartości, gniazdo zapomniane (powstanie od nowa)")

# ---------- Bluetooth.send_links: pakiet btlink, ograniczenie częstości ----------
async def t_send():
    emitted = []
    b = SB.Bluetooth(run=None, emit=lambda *a: emitted.append(a), links=BL.LinkReader(Fake()))
    b.devices = [SB.Device(PAD, "DualSense", None, BTD_PAIRED | BTD_CONNECTED | BTD_PAD),
                 SB.Device(PAD2, "DualSense", None, BTD_PAIRED | BTD_PAD),               # niepodłączony
                 SB.Device(OTHER, "Słuchawki", None, BTD_PAIRED | BTD_CONNECTED)]         # nie pad
    await b.send_links(100.0)
    check(len(emitted) == 1 and emitted[0][0] == T_BTLINK and emitted[0][1] == 255, f"jeden btlink: tylko podłączony pad ({len(emitted)})")
    args = emitted[0][2:]
    check(args == (mac_bytes(PAD), -5, 220, BTL_RSSI | BTL_LQ, 1), f"pola: MAC, RSSI, jakość, flagi, hci: {args}")
    check(BODY[T_BTLINK].unpack(BODY[T_BTLINK].pack(*args)) == args, "ramka mieści się w BODY[T_BTLINK]")
    await b.send_links(100.5)
    check(len(emitted) == 1, "najwyżej raz na LINK_S")
    await b.send_links(100.0 + SB.LINK_S)
    check(len(emitted) == 2, "po LINK_S znów odczyt")

    emitted.clear()
    b2 = SB.Bluetooth(run=None, emit=lambda *a: emitted.append(a), links=BL.LinkReader(Fake(rssi=None, quality=140)))
    b2.devices = b.devices[:1]
    await b2.send_links(10.0)
    check(emitted[0][2:] == (mac_bytes(PAD), 0, 140, BTL_LQ, 1), "tylko jakość: flaga BTL_RSSI wyłączona, RSSI = 0")
    emitted.clear()
    b3 = SB.Bluetooth(run=None, emit=lambda *a: emitted.append(a), links=BL.LinkReader(Fake(on=9)))
    b3.devices = b.devices[:1]
    await b3.send_links(10.0)
    check(not emitted, "pad, którego kontroler nie zna (np. odłączony chwilę temu): bez pakietu")

    check(SB.Bluetooth(run=None, emit=lambda *a: None).links.hci is None, "bez sysfs (testy) nie ma transportu HCI - pętla nie czyta łącza")

asyncio.run(t_send())

print("BTLINK TX", "FAIL" if fails else "OK")
sys.exit(1 if fails else 0)
