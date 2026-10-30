"""Nadajnik: ustawienia zmieniane z okna receivera (cfgset / cfgval, sender_remotecfg).
Tylko v3 z kluczem (v2, brak klucza, powtórzone nagranie - odmowa), sprawdzanie wartości, zastosowanie od razu (C.*), zapis do
remote_cfg.json i wczytanie po restarcie, reset do wartości z sender.toml, tryby sender_rt.apply, odświeżenie martwej strefy.
    python3 tests/test_remote_cfg_tx.py sender"""
import io, json, math, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1", "--key", "sekret"])
from sender_config import C
import cfgspec
import proto
import sender_net as net
import sender_remotecfg as R
import sender_rt
import sender_state as state
from proto import (BODY, CFG_ALL, CFG_GET, CFG_RESET, CFG_SET, CFGF_REMOTE, CFGR_INVALID, CFGR_LATER, CFGR_NO_KEY, CFGR_NO_V3, CFGR_OFF,
                   CFGR_OK, CFGR_UNKNOWN, T_CFGSET, T_CFGVAL, T_PING, T_PONG, T_PONG2, Codec, EpochClock)
from sender_ctrl import Ctrl

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

ID = cfgspec.BY_KEY
N = len(cfgspec.SPECS)


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
C.REMOTE_CFG_FILE = os.path.join(TMP, "remote_cfg.json")
net.init()
net.on_cfg = R.on_packet

# ---------- receiver (v3) po drugiej stronie, jak w test_security_tx ----------
pc_clock = EpochClock()
sender_t0 = []
def pc_token():
    if not sender_t0:
        return None
    t0, at = sender_t0[-1]
    return ((t0 + time.perf_counter_ns() - at) // 1_000_000) & 0xFFFFFFFF
pc = Codec(b"sekret", token=pc_token, now_ms=pc_clock.ms32)
pc.v3 = True
t0 = net.clock.ns()
sender_t0.append((t0, time.perf_counter_ns()))
net.sock = FakeSock([pc.pack(T_PONG2, 255, t0, pc_clock.ns() // 1000), pc.pack(T_PONG, 255, t0)])
net.on_readable()
check(net.codec.v3 and net.codec.key == b"sekret", "przygotowanie: nadajnik z kluczem jest już w v3")


def sync():
    """Odśwież zegar PC nadajnika (wygasa po 3 s bez pong2): bez niego pakiety v3 z nadajnika nie wychodzą."""
    t = net.clock.ns()
    sender_t0.append((t, time.perf_counter_ns()))
    net.sock = FakeSock([pc.pack(T_PONG2, 255, t, pc_clock.ns() // 1000), pc.pack(T_PONG, 255, t)])
    net.on_readable()


def ask(*packets, codec=pc):
    """Wyślij pakiety do nadajnika, zwróć odpowiedzi cfgval jako krotki (id, wynik, ref, flagi, wartość, z pliku)."""
    if codec is pc:
        sync()
    net.sock = FakeSock(list(packets))
    net.on_readable()
    out = []
    for raw in net.sock.sent:
        p = codec.parse(raw)
        if p is not None and p[0] == T_CFGVAL:
            out.append(BODY[T_CFGVAL].unpack_from(raw, 4))
    return out


def setting(name, value, ref=1, codec=pc):
    spec = cfgspec.SPECS[ID[name]]
    return codec.pack(T_CFGSET, 255, CFG_SET, ID[name], ref, cfgspec.to_wire(spec, value))


def saved():
    state.flush_writes()
    with open(C.REMOTE_CFG_FILE) as f:
        return json.load(f)


# ---------- specyfikacja: spójna z tym, co nadajnik sam przycina ----------
check(len(set(s.key for s in cfgspec.SPECS)) == N and N < CFG_ALL, "identyfikatory ustawień unikalne i mieszczą się w bajcie (255 = wszystkie)")
check(all(s.key in sender_config.DEFAULTS for s in cfgspec.SPECS), "każde ustawienie z cfgspec istnieje w DEFAULTS nadajnika")
for name in ("host", "port", "key", "stats", "bt_adapter", "slots_file", "wol_file", "gyro_cal_file", "bt_file", "auth_file", "remote_cfg_file"):
    check(name not in ID, f"{name} nie do zmiany z receivera")
check(set(sender_config.DEFAULTS) - set(ID) == {"host", "port", "key", "stats", "bt_adapter", "slots_file", "wol_file", "gyro_cal_file",
                                                "bt_file", "auth_file", "remote_cfg_file"}, "poza listą tylko połączenie, klucz, ścieżki, bt_adapter i stats")
from sender_opus import OPUS_BYTES_MAX, OPUS_BYTES_MIN
from sender_config import HAPTICS_BUFFER_MAX_MS, HAPTICS_BUFFER_MIN_MS, SPEAKER_BUFFER_MAX_MS, SPEAKER_BUFFER_MIN_MS, RT_PRIORITY_MAX
for name, lo, hi in (("haptics_buffer_ms", HAPTICS_BUFFER_MIN_MS, HAPTICS_BUFFER_MAX_MS), ("speaker_buffer_ms", SPEAKER_BUFFER_MIN_MS, SPEAKER_BUFFER_MAX_MS),
                     ("speaker_opus_bytes", OPUS_BYTES_MIN, OPUS_BYTES_MAX), ("rt_priority", 0, RT_PRIORITY_MAX)):
    s = cfgspec.SPECS[ID[name]]
    check((s.lo, s.hi) == (lo, hi), f"granice {name} w cfgspec = granice, które przycina sender_config ({lo}..{hi})")
for s in cfgspec.SPECS:     # wartość domyślna mieści się w granicach i przechodzi tam i z powrotem przez pakiet
    d = sender_config.DEFAULTS[s.key]
    ok = cfgspec.from_wire(s, cfgspec.to_wire(s, cfgspec.check(s, d))) == d
    check(ok, f"domyślna wartość {s.key} = {d!r} mieści się w granicach i przechodzi przez pakiet")

# ---------- wartości: typy, granice, NaN, indeks wyboru ----------
sp = cfgspec.SPECS[ID["haptics_buffer_ms"]]
for bad in (5, 5000, 20.5, True, "20", float("nan"), None):
    try:
        cfgspec.check(sp, bad); check(False, f"cfgspec.check przepuściło {bad!r}")
    except ValueError:
        pass
check(cfgspec.check(sp, 20.0) == 20 and isinstance(cfgspec.check(sp, 20.0), int), "liczba całkowita jako float -> int")
check(cfgspec.from_wire(cfgspec.SPECS[ID["tap_click"]], 1.0) is True, "bool z 1.0")
for bad in (0.5, 2.0, math.inf, math.nan):
    try:
        cfgspec.from_wire(cfgspec.SPECS[ID["tap_click"]], bad); check(False, f"bool przyjął {bad}")
    except ValueError:
        pass
for bad in (-1.0, 4.0, 1.5, math.inf):
    try:
        cfgspec.from_wire(cfgspec.SPECS[ID["rumble"]], bad); check(False, f"wybór przyjął {bad}")
    except ValueError:
        pass
check(cfgspec.from_wire(cfgspec.SPECS[ID["rumble"]], 3.0) == "off", "wybór = indeks na liście")

# ---------- odczyt: wszystkie ustawienia, wartości = to, co działa ----------
r = ask(pc.pack(T_CFGSET, 255, CFG_GET, CFG_ALL, 9, 0.0))
check([x[0] for x in r] == list(range(N)) and all(x[1] == CFGR_OK and x[2] == 9 and x[3] == 0 for x in r), "odczyt: cfgval dla każdego ustawienia, bez zmian, z tym samym ref")
vals = {cfgspec.SPECS[x[0]].key: x[4] for x in r}
check(vals["haptics_buffer_ms"] == 20 and vals["rumble"] == 0.0 and vals["tap_click"] == 1.0 and all(x[4] == x[5] for x in r),
      "wartości = domyślne, wartość z pliku = wartość w użyciu")

# ---------- zmiana: od razu w C.*, zapis do pliku, odpowiedź ----------
r = ask(setting("tap_click", False, 2))
check(r == [(ID["tap_click"], CFGR_OK, 2, CFGF_REMOTE, 0.0, 1.0)], f"zmiana działająca od razu: wynik OK, flaga zmiany, wartość i wartość z pliku: {r}")
check(C.TAP_CLICK is False, "C.TAP_CLICK zmienione od razu")
check(saved() == {"version": 1, "values": {"tap_click": False}}, "zapis do remote_cfg.json")
r = ask(setting("haptics_buffer_ms", 40, 3), setting("rumble", "off", 4))
check([(x[0], x[1], x[2]) for x in r] == [(ID["haptics_buffer_ms"], CFGR_LATER, 3), (ID["rumble"], CFGR_LATER, 4)], "ustawienia liczone od następnego strumienia / pada: wynik LATER")
check(C.HAPTICS_BUFFER_MS == 40.0 and C.RUMBLE == "off", "C.* już ma nowe wartości (następny strumień / pad je pobierze)")
check(saved()["values"] == {"tap_click": False, "haptics_buffer_ms": 40, "rumble": "off"}, "w pliku wszystkie trzy zmiany")
check(sender_config.BASE["haptics_buffer_ms"] == 20, "wartość z sender.toml (BASE) nietknięta")
r = ask(pc.pack(T_CFGSET, 255, CFG_GET, CFG_ALL, 5, 0.0))
d = {cfgspec.SPECS[x[0]].key: x for x in r}
check(d["haptics_buffer_ms"][3] == CFGF_REMOTE and d["haptics_buffer_ms"][4] == 40 and d["haptics_buffer_ms"][5] == 20 and d["pad_min_dt"][3] == 0,
      "odczyt po zmianie: flaga zmiany tylko przy zmienionych, obok wartość z pliku")

# ---------- złe wartości i nieznane ustawienia: nic się nie zmienia ----------
before = dict(sender_config.CHANGES)
for name, value in (("haptics_buffer_ms", 5000), ("haptics_buffer_ms", 3), ("speaker_opus_bytes", 10), ("rt_priority", 100), ("tap_max_s", 0.0)):
    r = ask(pc.pack(T_CFGSET, 255, CFG_SET, ID[name], 6, float(value)))
    check(r and r[0][1] == CFGR_INVALID, f"{name} = {value}: odmowa (poza granicami)")
for number in (math.nan, math.inf, 2.5):
    r = ask(pc.pack(T_CFGSET, 255, CFG_SET, ID["haptics_motors"], 6, number))
    check(r and r[0][1] == CFGR_INVALID, f"wybór z liczbą {number}: odmowa")
r = ask(pc.pack(T_CFGSET, 255, CFG_SET, ID["haptics_buffer_ms"], 6, 33.5))
check(r and r[0][1] == CFGR_INVALID, "ułamek dla liczby całkowitej: odmowa")
r = ask(pc.pack(T_CFGSET, 255, CFG_SET, N + 5, 7, 1.0), pc.pack(T_CFGSET, 255, CFG_SET, CFG_ALL, 7, 1.0), pc.pack(T_CFGSET, 255, CFG_RESET, N + 5, 7, 0.0),
        pc.pack(T_CFGSET, 255, 9, 0, 7, 0.0))
check([x[1] for x in r] == [CFGR_UNKNOWN] * 4, "nieznany identyfikator, SET na wszystkie, nieznana operacja: UNKNOWN")
check(sender_config.CHANGES == before and C.HAPTICS_BUFFER_MS == 40.0, "odmowy niczego nie zmieniły")

# ---------- powtórka tego samego pakietu (3 kopie) i nagranie ----------
pkt = setting("battery_poll_s", 9.0, 8)
r = ask(pkt, pkt, pkt)
check(len(r) == 1 and C.BATT_POLL_S == 9.0, "trzy kopie tego samego pakietu = jedna zmiana, jedna odpowiedź")
r = ask(pkt)
check(r == [] and C.BATT_POLL_S == 9.0, "dokładna powtórka (nagranie) odrzucona przez filtr numerów / v3")

# ---------- reset: wartość z sender.toml wraca ----------
r = ask(pc.pack(T_CFGSET, 255, CFG_RESET, ID["haptics_buffer_ms"], 10, 0.0))
check(r == [(ID["haptics_buffer_ms"], CFGR_OK, 10, 0, 20.0, 20.0)] and C.HAPTICS_BUFFER_MS == 20.0, f"reset jednego: wartość z pliku w użyciu, bez flagi zmiany: {r}")
check("haptics_buffer_ms" not in saved()["values"], "reset usuwa wpis z remote_cfg.json")
r = ask(pc.pack(T_CFGSET, 255, CFG_RESET, ID["pad_min_dt"], 11, 0.0))
check(r and r[0][1] == CFGR_OK and r[0][3] == 0, "reset ustawienia, którego nie zmieniono: OK, bez skutków")
r = ask(pc.pack(T_CFGSET, 255, CFG_RESET, CFG_ALL, 12, 0.0))
check(len(r) == N and sender_config.CHANGES == {} and saved()["values"] == {} and C.TAP_CLICK is True and C.RUMBLE == "auto" and C.BATT_POLL_S == 5.0,
      "reset wszystkich: cfgval dla każdego, pusty plik, wszystko jak w sender.toml")

# ---------- zastosowanie poza C.*: opóźnienia procesu i martwa strefa padów ----------
calls = []
real_apply = sender_rt.apply
sender_rt.apply = lambda *a: calls.append(a) or ["x"]
ask(setting("rt_priority", 20, 13))
check(calls == [(C.LOW_LATENCY, C.CPU_LATENCY_US, 20)], f"rt_priority: sender_rt.apply z aktualnymi wartościami: {calls}")
calls.clear()
ask(setting("cpu_latency_us", 77, 14), setting("low_latency", False, 15))
check(calls == [(True, 77, 20), (False, 77, 20)], f"cpu_latency_us i low_latency: apply z całym zestawem: {calls}")
calls.clear()
ask(setting("tap_click", True, 16))
check(not calls, "inne ustawienia nie ruszają procesu")
sender_rt.apply = real_apply


class FakePad:
    def __init__(s): s.n = 0
    def refresh_deadzone(s): s.n += 1


state.active = {"a": FakePad(), "b": FakePad()}
ask(setting("stick_deadzone", 9, 17))
check(C.STICK_DZ == 9 and [p.n for p in state.active.values()] == [1, 1], "stick_deadzone: nowa wartość od razu u podłączonych padów")
state.active = {}


class Stub:
    stick_dz = 3
    _raw_timer = None
stub = Stub()
Ctrl.refresh_deadzone(stub)
check(stub.stick_dz == 9, "Ctrl.refresh_deadzone bierze nową martwą strefę")
stub.stick_dz, stub._raw_timer = 0, object()
Ctrl.refresh_deadzone(stub)
check(stub.stick_dz == 0, "w czasie pomiaru dryfu (CMD_RAW_STICKS) martwa strefa zostaje 0")

# ---------- sender_rt.apply: włączanie i wyłączanie ----------
fakes = {"realtime": lambda p: f"SCHED_FIFO {p}", "hold_cpu_latency": lambda us: f"latency {us}", "pin_fast_cpus": lambda: "fast cores 0",
         "short_switch_interval": lambda: "switch"}
saved_fns = {k: getattr(sender_rt, k) for k in fakes}
for k, f in fakes.items():
    setattr(sender_rt, k, f)
sender_rt._realtime_on = False
check(sender_rt.apply(True, 50, 10) == ["SCHED_FIFO 10", "latency 50", "fast cores 0", "switch"], "apply: włącza real-time i opóźnienia")
for k, f in saved_fns.items():
    setattr(sender_rt, k, f)
sender_rt._qos = io.BytesIO()
sender_rt._affinity = set(os.sched_getaffinity(0))
sender_rt._realtime_on = True
back = []
real_normal = sender_rt.normal_scheduler
def fake_normal():
    sender_rt._realtime_on = False                              # jak prawdziwa funkcja po udanej zmianie
    back.append(1)
    return "normal scheduler"
sender_rt.normal_scheduler = fake_normal
sys.setswitchinterval(0.0005)
out = sender_rt.apply(False, 50, 0)
sender_rt.normal_scheduler = real_normal
check(out == ["normal scheduler", "CPU sleep allowed", "all cores", "GIL switch interval 5 ms"] and back == [1] and sender_rt._qos is None and sender_rt._affinity is None
      and abs(sys.getswitchinterval() - 0.005) < 1e-9, f"apply: wyłącza real-time, żądanie PM QoS, przypięcie do rdzeni i krótki interwał GIL: {out}")
check(sender_rt.apply(False, 50, 0) == [], "apply: ponowne wyłączenie niczego nie zmienia")

# ---------- v2 z kluczem (przed przejściem na v3) i brak klucza: odmowa ----------
state.flush_writes()
os.remove(C.AUTH_FILE)                                          # zapamiętane przejście na v3 usunięte: nowy kodek zaczyna w v2
net.init()                                                      # nowy kodek: klucz, jeszcze bez przejścia na v3
net.fb_filter = proto.SeqFilter(1.0)                            # inny "proces receivera": losowy początek numerów (filtr bierze go po 1 s ciszy)
check(net.codec.key == b"sekret" and not net.codec.v3, "kodek z kluczem przed przejściem")
v2 = Codec(b"sekret")
before = dict(sender_config.CHANGES)
r = ask(setting("tap_click", False, 20, codec=v2), codec=v2)
check(r and r[0][0] == CFG_ALL and r[0][1] == CFGR_NO_V3 and r[0][2] == 20, f"podpisany pakiet v2: odmowa NO_V3 (bez v3 nagranie dałoby się powtórzyć): {r}")
check(sender_config.CHANGES == before and C.TAP_CLICK is True, "v2 niczego nie zmienia")
r = ask(v2.pack(T_CFGSET, 255, CFG_GET, CFG_ALL, 21, 0.0), codec=v2)
check(len(r) == 1 and r[0][1] == CFGR_NO_V3, "odczyt w v2 też odmowa - jedna odpowiedź, nie 35")

C.KEY = b""
net.init()
net.fb_filter = proto.SeqFilter(1.0)
plain = Codec()
r = ask(setting("tap_click", False, 22, codec=plain), codec=plain)
check(r and r[0][1] == CFGR_NO_KEY and C.TAP_CLICK is True, f"nadajnik bez klucza: odmowa NO_KEY, niczego nie zmienia: {r}")
check(not any(s[0] >> 4 == proto.VER3 for s in net.sock.sent), "odpowiedź bez klucza jest zwykłym pakietem v2 (bez podpisu)")
C.KEY = b"sekret"
net.init()
net.codec.v3 = True
net.fb_filter = proto.SeqFilter(1.0)

# ---------- wyłączone zdalne zmiany: remote_cfg_file = "" ----------
C.REMOTE_CFG_FILE = ""
r = ask(pc.pack(T_CFGSET, 255, CFG_SET, ID["tap_click"], 30, 0.0))
check(r and r[0][0] == CFG_ALL and r[0][1] == CFGR_OFF and C.TAP_CLICK is True, f"remote_cfg_file = \"\": odmowa OFF, nic się nie zmienia: {r}")
r = ask(pc.pack(T_CFGSET, 255, CFG_GET, CFG_ALL, 31, 0.0))
check(len(r) == 1 and r[0][1] == CFGR_OFF, "odczyt przy wyłączonych zmianach też odmowa")
C.REMOTE_CFG_FILE = os.path.join(TMP, "remote_cfg.json")

# ---------- po restarcie: plik wczytany, błędne wpisy pominięte ----------
good = {"version": 1, "values": {"haptics_buffer_ms": 60, "tap_click": False, "rumble": "evdev", "speaker_opus_mode": "celt",
                                 "host": "10.0.0.9", "key": "x", "nope": 1, "speaker_buffer_ms": 5, "gestures": "yes", "rt_priority": 5.5}}
with open(C.REMOTE_CFG_FILE, "w") as f:
    json.dump(good, f)
cfg_path = os.path.join(TMP, "sender.toml")
with open(cfg_path, "w") as f:
    f.write('host = "127.0.0.1"\nhaptics_buffer_ms = 30\nremote_cfg_file = "%s"\n' % C.REMOTE_CFG_FILE.replace("\\", "/"))
sender_config.init(["--config", cfg_path])
check(sender_config.CHANGES == {"haptics_buffer_ms": 60, "tap_click": False, "rumble": "evdev", "speaker_opus_mode": "celt"},
      f"po restarcie wczytane tylko dozwolone wartości w granicach (host, key, nieznane, poza zakresem, zły typ pominięte): {sender_config.CHANGES}")
check(sender_config.BASE["haptics_buffer_ms"] == 30 and C.HAPTICS_BUFFER_MS == 60.0 and C.TAP_CLICK is False and C.RUMBLE == "evdev"
      and C.SPEAKER_OPUS_MODE == "celt" and C.HOST == "127.0.0.1" and not C.KEY, "zmiany z receivera wygrywają z plikiem sender.toml, host i klucz bez zmian")
for broken in ("{not json", "[1, 2]", '{"values": 5}', ""):
    with open(C.REMOTE_CFG_FILE, "w") as f:
        f.write(broken)
    check(sender_config.load_changes(C.REMOTE_CFG_FILE) == {}, f"uszkodzony plik {broken!r}: ignorowany, sender startuje")
check(sender_config.load_changes(os.path.join(TMP, "brak.json")) == {} and sender_config.load_changes("") == {}, "brak pliku i \"\": nic do wczytania")

if fails:
    sys.exit(1)
print("REMOTE CFG TX OK")
