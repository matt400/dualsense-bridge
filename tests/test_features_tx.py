"""Nadajnik: opór spustów w raporcie HID (sender_rumble), zegar PC z pong2 (PcClock), opóźnienie Bluetooth z
MSC_TIMESTAMP (BtDelay) i pakiet state2 z pomiarem (Ctrl.send_state)."""
import asyncio, os, sys, tempfile, time, types, zlib
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_ctrl
import sender_net as net
net.clock.base = 0   # pingi i zegar PC w perf_counter (test podaje czasy pong2)
import sender_rumble as R
from proto import BODY, CAP_TRIGGERS, LAT_NONE, T_PONG2, T_STATE, T_STATE2, T_TRIG, Codec
from sender_ctrl import Ctrl
from sender_streams import BtDelay

TMP = tempfile.mkdtemp()
ok = lambda m: print("OK  ", m)

# ---------- efekt spustu ----------
off = R.trigger_effect(0, 0)
assert off == bytes([R.TRIG_OFF]) + bytes(10)
fx = R.trigger_effect(2, 6)
assert len(fx) == 11 and fx[0] == 0x21
zones = int.from_bytes(fx[1:3], "little")
forces = int.from_bytes(fx[3:7], "little")
assert zones == sum(1 << z for z in range(2, 10)), bin(zones)
assert all((forces >> (3 * z)) & 7 == (5 if z >= 2 else 0) for z in range(10)), "siła 6 -> 5 (3 bity) w strefach 2-9"
assert not any(fx[7:]) and R.trigger_effect(0, 8)[1:3] == b"\xff\x03" and R.trigger_effect(10, 3)[0] == R.TRIG_OFF
ok("efekt Feedback 0x21: strefy od początku, 3 bity siły na strefę; siła 0 / zła strefa = Off")

rep = R.output_report(True, 2, 0, 0, None, None, (R.trigger_effect(0, 0), fx))
c = rep[3:]
assert c[0] & (R.FLAG0_LEFT_TRIGGER | R.FLAG0_RIGHT_TRIGGER) == 0x0C and c[0] & 0x03
assert c[R.RIGHT_TRIGGER_AT:R.RIGHT_TRIGGER_AT + 11] == fx and c[R.LEFT_TRIGGER_AT] == R.TRIG_OFF
assert int.from_bytes(rep[-4:], "little") == zlib.crc32(b"\xa2" + rep[:-4]) & 0xFFFFFFFF
plain = R.output_report(True, 2, 0, 0)[3:]
assert plain[0] & 0x0C == 0 and not any(plain[10:32]), "raport bez spustów nie rusza efektu na padzie"
ok("raport: flagi spustów, R2 od bajtu 10, L2 od 21, CRC; zwykła wibracja bez flag spustów")


class Dev:
    def capabilities(self):
        return {}


async def rumble():
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    assert r.bits & CAP_TRIGGERS
    r.rgb = (1, 2, 3)
    r.cur = (10, 20)
    assert r.set_triggers((0, 0, 3, 7))
    data = open(path, "rb").read()
    last = data[-78:][3:]
    assert last[R.RIGHT_TRIGGER_AT] == 0x21 and last[2:4] == bytes([10, 20]) and last[44:47] == bytes([1, 2, 3]), \
        "opór w raporcie razem z bieżącą wibracją i kolorem (nie zeruje ich)"
    size = len(open(path, "rb").read())
    r.close()
    tail = open(path, "rb").read()[size:]
    assert tail and tail[-78:][3:][R.RIGHT_TRIGGER_AT] == R.TRIG_OFF, "zamknięcie nadajnika zdejmuje opór"
    assert all(rep[3:][2:4] == b"\0\0" for rep in (tail[k:k + 78] for k in range(0, len(tail), 78))), \
        "raporty przy zamknięciu bez wibracji - zdjęcie oporu nie może włączyć silników od nowa (pad trzyma moc)"
    ev = R.Rumble(1, Dev(), "off")
    assert not ev.bits & CAP_TRIGGERS and ev.set_triggers((1, 1, 1, 1)) is False
asyncio.run(rumble())
ok("Rumble.set_triggers: raport HID, bit CAP_TRIGGERS, wyłączenie przy zamknięciu, bez HID - nic")

# ---------- zegar PC (pong2) ----------
pc = net.PcClock()
assert pc.now_us() is None
base = time.perf_counter_ns()
pc.pong(base, 9_000_000, base + 2_000_000)              # RTT 2 ms
pc.pong(base, 9_000_000 + 50, base + 400_000)           # RTT 0.4 ms - dokładniejszy
assert pc.offset_us == 9_000_050 - (base + base + 400_000) // 2000
pc.pong(base + 10, 1, base - 5)                         # ujemny RTT - odrzucony
assert len(pc.samples) == 2
expect = (time.perf_counter_ns() // 1000 + pc.offset_us) & 0xFFFFFFFF
assert abs(pc.now_us() - expect) < 2000
pc.t_sync -= pc.SYNC_MAX_AGE_S + 1
assert pc.now_us() is None, "bez pong2 (stary receiver / przerwa) - brak zegara"
ok("PcClock: przesunięcie z pomiaru o najmniejszym RTT, wygasa bez pong2")

# ---------- opóźnienie Bluetooth ponad minimum ----------
b = BtDelay()
assert b.sample(1_000_000, 500) == 0
assert b.sample(1_004_000, 4_500) == 0 and b.sample(1_008_800, 9_000) == 300
assert b.sample(1_012_000, 12_500) == 0
assert b.sample(4_000_000, 3_000_500) == 0, "stare minimum wypada z okna 2 s"
b2 = BtDelay()
b2.sample(10_000, 0xFFFFFF00)
assert b2.sample(10_456, 100) == 100, "przepełnienie 32-bitowego licznika pada"
ok("BtDelay: nadwyżka nad minimum z 2 s, okno przesuwne, przepełnienie licznika pada")

# ---------- state2: pomiar tylko dla świeżej zmiany i tylko z zegarem PC ----------
out = []
def emit(t, i, *v):
    BODY[t].pack(*v)
    out.append((t, i, v))
net.emit = sender_ctrl.net.emit = emit
c = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
net.pc_clock = types.SimpleNamespace(now_us=lambda: None)
c.send_state(force=True)
assert out[-1][0] == T_STATE, "bez zegara PC - zwykły pakiet stanu (starszy receiver)"
net.pc_clock = types.SimpleNamespace(now_us=lambda: 777)
c.bt_us = 250
c.mark_event(types.SimpleNamespace(sec=(time.time_ns() // 1000 - 1500) // 1_000_000,
                                   usec=(time.time_ns() // 1000 - 1500) % 1_000_000))
c.pad[0] = 1
c.send_state()
t, i, v = out[-1]
assert t == T_STATE2 and v[-3] == 777 and 1000 <= v[-2] < 50_000 and v[-1] == 250, v[-3:]
c.send_state(force=True)
assert out[-1][0] == T_STATE2 and out[-1][2][-2:] == (LAT_NONE, LAT_NONE), "powtórka (heartbeat) bez pomiaru"
c.mark_event(types.SimpleNamespace())                    # zdarzenie bez czasu (testy) - bez pomiaru
assert c.t_event is None
ok("Ctrl.send_state: state2 z czasem w zegarze PC, telefon i Bluetooth tylko dla świeżej zmiany")


def ev_ago(us):
    t = time.time_ns() // 1000 - us
    return types.SimpleNamespace(sec=t // 1_000_000, usec=t % 1_000_000)


c.set_abs(0, c.pad[0] + 1 if c.pad[0] < 255 else 254)    # zmiana gałki ...
c.send_state()
c.set_abs(0, 128)
c.send_state()
c.set_abs(0, 129)                                        # ... szum w strefie martwej: wartość 128 bez zmian
c.mark_event(ev_ago(40_000))
n = len(out)
c.send_state()
assert len(out) == n and c.t_event is None, "zmiana bez skutku nie zostawia swojego czasu dla następnej"
c.mask = 1                                               # prawdziwe naciśnięcie
c.mark_event(ev_ago(300))
c.send_state()
assert out[-1][0] == T_STATE2 and 300 <= out[-1][2][-2] < 5_000, f"opóźnienie naciśnięcia, nie szumu: {out[-1][2][-2]}"
ok("Ctrl.send_state: szum gałki w strefie martwej nie zawyża pomiaru następnego naciśnięcia")

pc = net.PcClock()
base = time.perf_counter_ns()
pc.pong(base, 5_000_000, base + 100_000)                 # RTT 0.1 ms, stary zegar PC
pc.t_sync -= pc.SYNC_MAX_AGE_S + 1                       # przerwa (PC wyłączony / restart)
pc.pong(base, 9_000_000, base + 2_000_000)               # nowy zegar, gorszy RTT
assert len(pc.samples) == 1 and pc.offset_us == 9_000_000 - (base + base + 2_000_000) // 2000, \
    "po przerwie pomiary sprzed niej nie wygrywają mniejszym RTT"
ok("PcClock: pomiary sprzed przerwy w pongach są odrzucane")

# ---------- odbiór: trig i pong2 z sieci ----------
cod = Codec()
got = []
net.on_triggers = lambda idx, vals: got.append((idx, vals))
net.pc_clock = net.PcClock()


class Sock:
    def __init__(self, pkts):
        self.pkts = list(pkts)
    def recv(self, n):
        if not self.pkts:
            raise BlockingIOError
        return self.pkts.pop(0)


net.codec = cod
net.sock = Sock([cod.pack(T_TRIG, 1, 0, 0, 3, 6), cod.pack(T_PONG2, 255, time.perf_counter_ns() - 100_000, 5_000_000)])
net.on_readable()
assert got == [(1, (0, 0, 3, 6))] and net.pc_clock.offset_us is not None
ok("sender_net: pakiet trig -> callback, pong2 -> zegar PC")
print("FEATURES TX OK")
