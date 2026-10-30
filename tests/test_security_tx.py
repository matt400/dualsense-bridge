"""Nadajnik: przegląd bezpieczeństwa - protokół v3 z kluczem (znacznik świeżości, odrzucanie powtórzonych nagrań
i podpisanych pakietów v2 po przejściu, zapamiętane przejście), próbny ping v3, zegar PC po restarcie receivera.
    python3 tests/test_security_tx.py sender"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1", "--key", "sekret"])
from sender_config import C
import proto
import sender_net as net
from proto import (BODY, CMD_POWER_OFF, FRESH_PAST_MS, T_CMD, T_PING, T_PONG, T_PONG2, T_STATE, Codec, EpochClock,
                   token_fresh)

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


class FakeSock:
    def __init__(s, pkts=()):
        s.pkts, s.sent = list(pkts), []
    def recv(s, n):
        if s.pkts:
            return s.pkts.pop(0)
        raise BlockingIOError
    def send(s, data):
        s.sent.append(data)


TMP = tempfile.mkdtemp()
C.AUTH_FILE = os.path.join(TMP, "auth.json")
net.init()
check(net.codec.key == b"sekret" and not net.codec.v3, "klucz z --key, przed przejściem format v2")

# ---------- receiver (v3) po drugiej stronie: zegar PC, znaczniki z zegara nadajnika ----------
pc_clock = EpochClock()
sender_t0 = []                                   # ostatni ping nadajnika (t0 w jego zegarze, czas odbioru)
def pc_token():
    if not sender_t0:
        return None
    t0, at = sender_t0[-1]
    return ((t0 + time.perf_counter_ns() - at) // 1_000_000) & 0xFFFFFFFF
pc = Codec(b"sekret", token=pc_token, now_ms=pc_clock.ms32)
pc.v3 = True

cmds = []
net.on_command = lambda i, cmd, arg: cmds.append((i, cmd, arg))

# ---------- próbny ping v3 i przejście ----------
net.sock = FakeSock()
net.pings_sent = 0
t0 = net.clock.ns()
net.emit(T_PING, 255, t0)
net.send_raw(net.codec.pack(T_PING, 255, t0, v3=True))
v2, v3 = net.sock.sent
check(v2[0] >> 4 == proto.VER and v3[0] >> 4 == proto.VER3, "z kluczem przed przejściem: ping v2 i próbny ping v3")
check(pc.parse(v2) is None and pc.legacy_t > 0, "receiver po przejściu odrzuca podpisany v2 (nagranie / stara wersja)")
p = pc.parse(v3)
check(p is not None and p[0] == T_PING, "próbny ping v3 bez zegara PC (znacznik 0) przyjęty - ping nie jest sprawdzany")
sender_t0.append((t0, time.perf_counter_ns()))

pong2 = pc.pack(T_PONG2, 255, t0, pc_clock.ns() // 1000)
pong = pc.pack(T_PONG, 255, t0)
net.sock = FakeSock([pong2, pong])
net.on_readable()
check(net.codec.v3 and net.pc_clock.offset_us is not None, "pong v3 od receivera: nadajnik przechodzi na v3, zegar PC znany")
check(open(C.AUTH_FILE).read().strip() == f'{{"v3": "{proto.key_fingerprint(b"sekret")}"}}', "przejście zapisane (odcisk klucza)")

# ---------- polecenia: świeże raz, powtórka odrzucona, przeterminowane odrzucone, v2 odrzucone ----------
cmd = pc.pack(T_CMD, 0, CMD_POWER_OFF, 0)
net.sock = FakeSock([cmd, cmd])
net.on_readable()
check(cmds == [(0, CMD_POWER_OFF, 0)], f"polecenie v3 wykonane raz, dokładna powtórka odrzucona: {cmds}")

old = Codec(b"sekret", token=lambda: (net.clock.ms32() - FRESH_PAST_MS - 2000) & 0xFFFFFFFF); old.v3 = True
cmds.clear(); stale = net.codec.stale
net.sock = FakeSock([old.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds and net.codec.stale == stale + 1, "nagranie sprzed kilku sekund (znacznik poza oknem) odrzucone")

legacy = Codec(b"sekret")
net.codec.upgraded_t = float("-inf")             # zaraz po przejściu v2 to pakiety w drodze (bez ostrzeżenia)
net.sock = FakeSock([legacy.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds and net.codec.legacy_t > 0, "po przejściu podpisany pakiet v2 (nagranie sprzed aktualizacji) odrzucony")

other = EpochClock()                              # nagranie z poprzedniego uruchomienia nadajnika: inny zegar
rec = Codec(b"sekret", token=other.ms32); rec.v3 = True
net.sock = FakeSock([rec.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds, "znacznik z innego uruchomienia (losowe przesunięcie zegara) odrzucony")

wrong = Codec(b"inny", token=pc_token); wrong.v3 = True
net.sock = FakeSock([wrong.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds and time.monotonic() - net.codec.mismatch_t < 1, "inny klucz: odrzucone i zgłoszone jako różnica klucza")

# ---------- nadajnik w v3: stan z czasem PC, bez zegara PC nic nie wychodzi ----------
net.sock = FakeSock()
net.emit(T_STATE, 0, *([0] * 17))
p = pc.parse(net.sock.sent[-1])
check(p is not None and p[0] == T_STATE, "stan v3 ze znacznikiem czasu PC przyjęty przez receiver")
saved = net.pc_clock
net.pc_clock = net.PcClock()
net.sock = FakeSock()
net.emit(T_STATE, 0, *([0] * 17))
net.emit(T_PING, 255, net.clock.ns())
check(len(net.sock.sent) == 1 and pc.parse(net.sock.sent[0])[0] == T_PING,
      "bez zegara PC: stan nie wychodzi (receiver i tak by go odrzucił), ping tak - z niego receiver odpowie")
net.pc_clock = saved

# ---------- restart nadajnika: zapamiętane przejście ----------
net.codec = None
net.init()
check(net.codec.v3, "restart nadajnika: od razu v3 (bez okna na nagrane pakiety v2)")
C.KEY = b"nowy"; net.init()
check(not net.codec.v3, "zmiana klucza: przejście od nowa (inny odcisk)")
C.KEY = b""; net.init()
check(not net.codec.v3 and net.codec.pack(T_PING, 255, 1)[0] >> 4 == proto.VER, "bez klucza: zawsze v2 jak dotąd")
C.KEY = b"sekret"; net.init()

# ---------- zegar PC: skok przesunięcia (nowy proces receivera) czyści stare pomiary ----------
clk = net.PcClock()
t = net.clock.ns()
clk.pong(t - 100_000, 5_000_000_000, t)
first = clk.offset_us
clk.pong(t - 2_000_000, 9_000_000_000, t)         # większy RTT, ale zegar PC przesunięty o 4000 s
check(clk.offset_us != first and abs(clk.offset_us - first - 4_000_000_000) < 10_000,
      "nowe przesunięcie zegara PC wygrywa od razu mimo większego RTT (inaczej znaczniki v3 odrzucane przez ~3 s)")

check(token_fresh(5, 0xFFFFFFF0) and token_fresh(0xFFFFFFF0, 10) and not token_fresh(0, 10_000),
      "okno świeżości odporne na przekręcenie 32-bitowego licznika ms")

if fails:
    sys.exit(1)
print("SECURITY TX OK")
