"""Nadajnik: Wake-on-LAN z pada (sender_wol), zdarzenia do dziennika receivera (sender_events), pakiety scfg / event /
build2 i lokalne potwierdzenie na padzie (Ctrl.local_pulse)."""
import asyncio, json, os, socket, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
TMP = tempfile.mkdtemp()
C.WOL_FILE = os.path.join(TMP, "wol.json")
import proto
import sender_events as E
import sender_net as net
import sender_update
import sender_wol as W
from proto import (BODY, E_NO_RECEIVER, E_WOL, E_WOL_FAIL, EV_ERROR, EV_INFO, KEY_BIT, PAD_KEYS, SCFG_EVENTS, SCFG_WOL,
                   T_BUILD, T_BUILD2, T_EVENT, T_SCFG, Codec, mac_bytes, magic_packet)
from sender_ctrl import Ctrl

ok = lambda m: print("OK  ", m)
cod = Codec()

# ---------- protokół ----------
assert cod.parse(cod.pack(T_SCFG, 255, 3, b"\1" * 6, b"\0" * 4, 7, 1000))[0] == T_SCFG
assert cod.parse(cod.pack(T_EVENT, 1, 5, 0, 1, 0, b"x"))[0] == T_EVENT
assert cod.parse(cod.pack(T_BUILD2, 255, b"abc", 1, 0, b"\1" * 6, b"\0" * 4))[0] == T_BUILD2
assert cod.parse(cod.pack(T_BUILD, 255, b"abc", 1, 0))[0] == T_BUILD, "zwykły build bez zmian"
pkt = magic_packet(bytes.fromhex("aabbccddeeff"))
assert len(pkt) == 102 and pkt[:6] == b"\xff" * 6 and pkt[6:12] == pkt[-6:] == bytes.fromhex("aabbccddeeff")
assert mac_bytes("AA-BB-CC-DD-EE-FF") == bytes.fromhex("aabbccddeeff") and mac_bytes("00:00:00:00:00:00") is None
assert mac_bytes("aa:bb") is None and mac_bytes("zz:bb:cc:dd:ee:ff") is None
ok("protokół: scfg / event / build2 po długości, magic packet, zapis MAC")

# ---------- zdarzenia: tylko włączone, kolejka do odpowiedzi receivera, powtórki ----------
out = []
emit = lambda *a: out.append(a)
E.set_enabled(False); E.info(E_WOL, 0, "x")
assert E.pending() == 0, "wyłączone - nic nie czeka"
E.set_enabled(True)
E.info(E_WOL, 1, "aa:bb:cc:dd:ee:ff")
t0 = time.monotonic()
E.flush(emit, t0 + 42)
assert len(out) == 1 and out[0][0] == T_EVENT and out[0][1] == 1 and out[0][3:6] == (EV_INFO, E_WOL, 42), out
E.flush(emit, t0 + 42)
assert len(out) == 2 and out[0][2] == out[1][2] and E.pending() == 0, "SENDS razy z tym samym numerem, potem znika"
for k in range(E.KEEP + 5):
    E.error(E_WOL_FAIL, 0, "e" * 200)
assert E.pending() == E.KEEP and len(E._queue[0][5]) == E.ARG_MAX, "kolejka ograniczona, argument ucięty"
E.set_enabled(False); assert E.pending() == 0
ok("zdarzenia: tylko włączone, wiek w sekundach, SENDS powtórek z jednym numerem, limit kolejki i argumentu")

E.set_enabled(True); out.clear()
net._receiver_watch(False, True)
assert E.pending() == 1 and E._queue[0][3] == E_NO_RECEIVER, "utrata receivera = zdarzenie"
net._receiver_watch(False, False)
assert E.pending() == 1, "czeka na odpowiedź receivera"
real_emit = net.emit
net.emit = emit
net._receiver_watch(True, False); net._receiver_watch(True, True)
net.emit = real_emit
assert [o[4] for o in out] == [E_NO_RECEIVER] * 2 and E.pending() == 0
E.set_enabled(False)
ok("pętla pingu: utrata kontaktu z receiverem jako zdarzenie, wysłane po jego powrocie")

# ---------- ustawienia z receivera: scfg -> wol.json ----------
assert W.cfg == W.WolCfg() and not os.path.exists(C.WOL_FILE)
opt = PAD_KEYS.index(315)
assert not W.apply_scfg(SCFG_WOL, bytes(6), bytes(4), opt, 1000), "bez zmian - bez zapisu"
assert not os.path.exists(C.WOL_FILE)
assert W.apply_scfg(SCFG_WOL | SCFG_EVENTS, bytes.fromhex("aabbccddeeff"), socket.inet_aton("192.168.1.255"),
                    PAD_KEYS.index(307), 1500)
assert W.cfg == W.WolCfg(True, "aa:bb:cc:dd:ee:ff", "192.168.1.255", PAD_KEYS.index(307), 1500, True) and E.enabled
saved = json.load(open(C.WOL_FILE))
assert saved["mac"] == "aa:bb:cc:dd:ee:ff" and saved["events"] is True and "learned_mac" in saved
W.cfg = W.WolCfg(); E.set_enabled(False)
W.load()
assert W.cfg.mac == "aa:bb:cc:dd:ee:ff" and W.cfg.hold_ms == 1500 and E.enabled, "po restarcie nadajnika z pliku"
W.apply_scfg(0, bytes(6), bytes(4), PAD_KEYS.index(316), 99999)
assert W.cfg.button == opt and W.cfg.hold_ms == W.HOLD_MS[1] and not W.cfg.enabled, "PS jako para / zły czas -> domyślne"
json.dump({"enabled": "tak", "mac": 5, "bcast": "nie-ip", "button": 99, "hold_ms": "x", "nowy": 1}, open(C.WOL_FILE, "w"))
W.load()
assert W.cfg == W.WolCfg(enabled=True), W.cfg
open(C.WOL_FILE, "w").write("{zepsuty")
W.load(); assert W.cfg == W.WolCfg(enabled=True)
for raw in (5, ["x"], "tekst", None):                     # poprawny JSON, ale nie obiekt - start usługi nie może paść
    json.dump(raw, open(C.WOL_FILE, "w"))
    W.load(); assert W.cfg == W.WolCfg(enabled=True), raw
ok("scfg: zapis do wol.json tylko przy zmianie, wczytanie po restarcie, złe wartości -> domyślne")

# ---------- adresy: ARP, broadcast sieci ----------
arp = os.path.join(TMP, "arp")
open(arp, "w").write("IP address       HW type     Flags       HW address            Mask     Device\n"
                     "192.168.1.4      0x1         0x2         11:22:33:44:55:66     *        eth0\n"
                     "192.168.1.9      0x1         0x0         00:00:00:00:00:00     *        eth0\n")
assert W.arp_lookup("192.168.1.4", arp) == "11:22:33:44:55:66"
assert W.arp_lookup("192.168.1.9", arp) == "" and W.arp_lookup("10.0.0.1", arp) == "" and W.arp_lookup("x", "/nie") == ""
iproute = ("1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
           "3: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global dynamic eth0\\       valid_lft 80000sec\n")
busybox = "2: usb0    inet 172.16.42.1/26 scope global usb0\n"
assert W.subnet_broadcast("192.168.1.20", iproute) == "192.168.1.255"
assert W.subnet_broadcast("172.16.42.1", busybox) == "172.16.42.63", "bez brd - z maski"
assert W.subnet_broadcast("10.1.1.1", iproute) is None
W.cfg = W.WolCfg()
W._bcast_cache = ("192.168.1.20", "192.168.1.255")
W.local_ip = lambda: "192.168.1.20"
assert W.targets() == ["192.168.1.255", "255.255.255.255", C.PC_ADDR]
W.cfg = W.WolCfg(bcast="10.0.0.255")
assert W.targets() == ["10.0.0.255", C.PC_ADDR], "wpisany broadcast zamiast automatycznych"
W.cfg = W.WolCfg()
real_lookup = W.arp_lookup
W.arp_lookup = lambda ip: "11:22:33:44:55:66" if ip == C.PC_ADDR else ""
W.learned = ""; W.learn()
assert W.learned == "11:22:33:44:55:66" and json.load(open(C.WOL_FILE))["learned_mac"] == W.learned
W.arp_lookup = real_lookup
assert W.target_mac() == bytes.fromhex("112233445566")
W.cfg = W.WolCfg(mac="aa:bb:cc:dd:ee:ff"); assert W.target_mac() == bytes.fromhex("aabbccddeeff"), "wpisany ma pierwszeństwo"
ok("adresy: MAC z ARP (tylko kompletny wpis, zapamiętany), broadcast z `ip` (brd albo maska), cele wysyłki")

# ---------- wysyłka: prawdziwe gniazdo UDP na localhost ----------
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 0)); rx.settimeout(2)
real_ports = proto.WOL_PORTS
W.WOL_PORTS = (rx.getsockname()[1],)
assert W.send(bytes.fromhex("aabbccddeeff"), ["127.0.0.1"]) == []
assert rx.recv(200) == magic_packet(bytes.fromhex("aabbccddeeff"))
errs = W.send(bytes(6), ["999.1.1.1"])
assert len(errs) == 1 and "999.1.1.1" in errs[0], errs
W.WOL_PORTS = real_ports
errs = W.send(bytes(6), ["999.1.1.1", "998.1.1.1"])
assert len(errs) == 2 * len(W.WOL_PORTS), "każda nieudana wysyłka liczy się osobno (fire porównuje z liczbą wysyłek)"
ok("magic packet wysłany na adres i port; zły adres = opis błędu, bez wyjątku")

# brak sieci: ten sam błąd na każdym porcie i adresie = porażka, a nie "wysłano" (czerwony błysk, zdarzenie błędu)
class _Pad:
    idx, wol_timer = 0, None

    def __init__(self):
        self.pulses = []

    def local_pulse(self, kind):
        self.pulses.append(kind)


real_send, real_targets = W.send, W.targets
W.targets = lambda: ["192.168.1.255", "255.255.255.255", "192.168.1.4"]
W.send = lambda mac, addrs: [f"{a}: Network is unreachable" for a in addrs for _p in W.WOL_PORTS]
W.cfg = W.WolCfg(mac="aa:bb:cc:dd:ee:ff", events=True); E.set_enabled(True); E._queue.clear()
net.last_pong, W._last_sent = 0.0, 0.0
pad = _Pad()
assert W.fire(pad) is False and pad.pulses == ["fail"] and E._queue[-1][3] == E_WOL_FAIL, (pad.pulses, list(E._queue))
W.send = lambda mac, addrs: [f"{addrs[0]}: Network is unreachable"] * len(W.WOL_PORTS)   # jeden adres zawiódł
W._last_sent = 0.0
pad = _Pad()
assert W.fire(pad) is True and pad.pulses == ["wol"], "część adresów doszła - wysłano"
W.send, W.targets = real_send, real_targets
E.set_enabled(False); E._queue.clear()
ok("Wake-on-LAN bez sieci (ten sam błąd wszędzie) = błąd, nie sukces; częściowy błąd = wysłano")


# ---------- kombinacja na padzie ----------
class FakeCtrl:
    def __init__(self):
        self.idx, self.mask, self.wol_timer, self.pulses = 0, 0, None, []

    def local_pulse(self, kind):
        self.pulses.append(kind)


sent = []
W.send = lambda mac, addrs: sent.append((mac, addrs)) or []
W.cfg = W.WolCfg(mac="aa:bb:cc:dd:ee:ff", hold_ms=300, events=True)
E.set_enabled(True); E._queue.clear()
PS, OPT, TRI = KEY_BIT[316], KEY_BIT[315], KEY_BIT[307]


async def scenario():
    c = FakeCtrl()
    net.last_pong = 0.0                              # receiver nigdy nie odpowiedział
    W._last_sent = 0.0
    c.mask = PS; W.on_mask(c)
    assert c.wol_timer is None, "samo PS - nic (tak włącza się pada)"
    c.mask = PS | OPT; W.on_mask(c)
    assert c.wol_timer is not None
    await asyncio.sleep(0.1)
    c.mask = PS; W.on_mask(c)
    assert c.wol_timer is None
    await asyncio.sleep(0.4)
    assert not sent, "puszczone przed czasem - bez wysyłki"
    c.mask = PS | OPT; W.on_mask(c)
    c.mask = PS | OPT | TRI; W.on_mask(c)           # inny przycisk w trakcie nie przerywa przytrzymania
    await asyncio.sleep(0.4)
    assert len(sent) == 1 and sent[0][0] == bytes.fromhex("aabbccddeeff") and c.pulses == ["wol"], (sent, c.pulses)
    assert E._queue[-1][3] == E_WOL and c.wol_timer is None
    c.mask = 0; W.on_mask(c); c.mask = PS | OPT; W.on_mask(c)
    await asyncio.sleep(0.4)
    assert len(sent) == 1, "kolejne wysłanie najwcześniej po REPEAT_GUARD_S"
    W._last_sent = 0.0
    net.last_pong = time.monotonic()                 # PC działa: kombinacja należy do receivera
    c.mask = 0; W.on_mask(c); c.mask = PS | OPT; W.on_mask(c)
    await asyncio.sleep(0.4)
    assert len(sent) == 1 and c.pulses == ["wol"], "receiver odpowiada - bez Wake-on-LAN"
    net.last_pong = 0.0
    W.cfg = W.WolCfg(enabled=False, mac="aa:bb:cc:dd:ee:ff", hold_ms=300)
    c.mask = 0; W.on_mask(c); c.mask = PS | OPT; W.on_mask(c)
    assert c.wol_timer is None, "wyłączone w oknie receivera"
    W.cfg = W.WolCfg(hold_ms=300, events=True); W.learned = ""
    c.mask = 0; W.on_mask(c); c.mask = PS | OPT; W.on_mask(c)
    await asyncio.sleep(0.4)
    assert len(sent) == 1 and c.pulses == ["wol", "fail"] and E._queue[-1][2:4] == [EV_ERROR, E_WOL_FAIL], \
        "nieznany MAC - czerwony błysk i zdarzenie błędu"

asyncio.run(scenario())
ok("kombinacja: PS + przycisk przytrzymane hold_ms przy milczącym receiverze; puszczenie anuluje; ochrona przed "
   "powtórką; działający receiver / wyłączone / brak MAC")


# ---------- build2 tylko dla nowego receivera ----------
out.clear()
net.emit = emit
W.cfg = W.WolCfg(); W.learned = "11:22:33:44:55:66"; W._bcast_cache = ("192.168.1.20", "192.168.1.255")
net.scfg_t = 0.0
sender_update.send_build()
assert out[-1][0] == T_BUILD, "starszy receiver (bez scfg) dostaje zwykły build"
net.scfg_t = time.monotonic()
sender_update.send_build()
assert out[-1][0] == T_BUILD2 and out[-1][5] == bytes.fromhex("112233445566") and out[-1][6] == socket.inet_aton("192.168.1.255")
net.emit = real_emit
ok("build2 z MAC i broadcastem Wake-on-LAN tylko po scfg od receivera")

# ---------- lokalne potwierdzenie na padzie (bez receivera) ----------


class Rum:
    owns_lightbar, rgb = True, (0, 0, 64)

    def __init__(self):
        self.calls = []

    def set(self, small, large, rgb=None):
        self.calls.append((small, large, rgb))


async def pulse():
    c = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
    c.rumble = Rum()
    c.local_pulse("wol")
    assert c.rumble.calls[-1] == (0, 200, (255, 255, 255)) and c.fb is None, "błysk nie jest feedbackiem z PC"
    await asyncio.sleep(0.6)
    assert c.rumble.calls[-1] == (0, 0, (0, 0, 64)), "powrót do koloru sprzed błysku"
    c.apply_feedback(0, 0, 1, 2, 3)
    c.local_pulse("fail")
    c.apply_feedback(10, 20, 4, 5, 6)                # PC odpowiedział w trakcie - jego stan wygrywa
    await asyncio.sleep(0.7)
    assert c.rumble.calls[-1] == (10, 20, (4, 5, 6)) and c.fb == (10, 20, 4, 5, 6), c.rumble.calls[-3:]
    # PC zgasł w trakcie wibracji gry: ostatni feedback ma moc silników - powrót po błysku jej nie włącza od nowa
    c.apply_feedback(180, 255, 7, 8, 9)
    c.local_pulse("wol")
    await asyncio.sleep(0.6)
    assert c.rumble.calls[-1] == (0, 0, (7, 8, 9)), c.rumble.calls[-3:]
    # błysk w trakcie błysku wraca do koloru sprzed pierwszego, nie do koloru błysku
    c.fb = None
    c.rumble.rgb = (0, 0, 64)
    c.local_pulse("wol")
    c.rumble.rgb = (255, 255, 255)                   # raport HID ma teraz kolor błysku
    c.local_pulse("fail")
    await asyncio.sleep(0.7)
    assert c.rumble.calls[-1] == (0, 0, (0, 0, 64)), c.rumble.calls[-3:]
    c.cancel_timers()

asyncio.run(pulse())
ok("lokalny błysk: wibracja i kolor na chwilę, powrót do poprzedniego; nowy feedback z PC go kasuje")
print("WOL TX OK")
