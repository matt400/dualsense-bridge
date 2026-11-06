"""Sender: settings changed from the receiver window (cfgset / cfgval, sender_remotecfg).
Only v3 with a key (v2, no key, a replayed recording - refusal), value checks, applied at once (C.*), written to
remote_cfg.json and loaded after a restart, reset to the value from sender.toml, sender_rt.apply modes, dead zone refresh.
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
from proto import (BODY, CFG_ALL, CFG_GET, CFG_RESET, CFG_SET, CFGF_LAST, CFGF_REMOTE, CFGR_INVALID, CFGR_LATER, CFGR_NO_KEY, CFGR_NO_V3, CFGR_OFF,
                   CFGR_OK, CFGR_UNKNOWN, T_CFGSET, T_CFGVAL, T_PING, T_PONG, T_PONG2, Codec, EpochClock)
from sender_ctrl import Ctrl

from chk import check, fails, raises                          # noqa: E402  (shared helper, tests/chk.py)

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

# ---------- receiver (v3) on the other side, as in test_security_tx ----------
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
check(net.codec.v3 and net.codec.key == b"sekret", "setup: the sender with a key is already on v3")


def sync():
    """Refresh the sender's PC clock (it expires after 3 s without pong2): without it no v3 packets leave the sender."""
    t = net.clock.ns()
    sender_t0.append((t, time.perf_counter_ns()))
    net.sock = FakeSock([pc.pack(T_PONG2, 255, t, pc_clock.ns() // 1000), pc.pack(T_PONG, 255, t)])
    net.on_readable()


def ask(*packets, codec=pc):
    """Send packets to the sender, return the cfgval replies as tuples (id, result, ref, flags, value, from file)."""
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


# ---------- spec: consistent with what the sender clamps by itself ----------
check(len({s.key for s in cfgspec.SPECS}) == N and N < CFG_ALL, "setting ids unique and fit in a byte (255 = all)")
check(all(s.key in sender_config.DEFAULTS for s in cfgspec.SPECS), "every setting from cfgspec exists in the sender's DEFAULTS")
for name in ("host", "port", "key", "stats", "bt_adapter", "slots_file", "wol_file", "gyro_cal_file", "bt_file", "auth_file", "remote_cfg_file"):
    check(name not in ID, f"{name} cannot be changed from the receiver")
check(set(sender_config.DEFAULTS) - set(ID) == {"host", "port", "key", "stats", "bt_adapter", "slots_file", "wol_file", "gyro_cal_file",
                                                "bt_file", "auth_file", "remote_cfg_file"}, "outside the list only the connection, the key, paths, bt_adapter and stats")
from sender_opus import OPUS_BYTES_MAX, OPUS_BYTES_MIN
from sender_config import HAPTICS_BUFFER_MAX_MS, HAPTICS_BUFFER_MIN_MS, SPEAKER_BUFFER_MAX_MS, SPEAKER_BUFFER_MIN_MS, RT_PRIORITY_MAX
for name, lo, hi in (("haptics_buffer_ms", HAPTICS_BUFFER_MIN_MS, HAPTICS_BUFFER_MAX_MS), ("speaker_buffer_ms", SPEAKER_BUFFER_MIN_MS, SPEAKER_BUFFER_MAX_MS),
                     ("speaker_opus_bytes", OPUS_BYTES_MIN, OPUS_BYTES_MAX), ("rt_priority", 0, RT_PRIORITY_MAX)):
    s = cfgspec.SPECS[ID[name]]
    check((s.lo, s.hi) == (lo, hi), f"limits of {name} in cfgspec = the limits sender_config clamps to ({lo}..{hi})")
for s in cfgspec.SPECS:     # the default value is within the limits and survives a round trip through the packet
    d = sender_config.DEFAULTS[s.key]
    ok = cfgspec.from_wire(s, cfgspec.to_wire(s, cfgspec.check(s, d))) == d
    check(ok, f"default value {s.key} = {d!r} is within the limits and survives the packet")

# ---------- values: types, limits, NaN, choice index ----------
sp = cfgspec.SPECS[ID["haptics_buffer_ms"]]
for bad in (5, 5000, 20.5, True, "20", float("nan"), None):
    raises(ValueError, lambda: cfgspec.check(sp, bad), f"cfgspec.check let {bad!r} through")
check(cfgspec.check(sp, 20.0) == 20 and isinstance(cfgspec.check(sp, 20.0), int), "an integer as a float -> int")
check(cfgspec.from_wire(cfgspec.SPECS[ID["tap_click"]], 1.0) is True, "bool z 1.0")
for bad in (0.5, 2.0, math.inf, math.nan):
    raises(ValueError, lambda: cfgspec.from_wire(cfgspec.SPECS[ID["tap_click"]], bad), f"bool accepted {bad}")
for bad in (-1.0, 4.0, 1.5, math.inf):
    raises(ValueError, lambda: cfgspec.from_wire(cfgspec.SPECS[ID["rumble"]], bad), f"choice accepted {bad}")
check(cfgspec.from_wire(cfgspec.SPECS[ID["rumble"]], 3.0) == "off", "choice = index in the list")

# ---------- read: every setting, values = what is in effect ----------
r = ask(pc.pack(T_CFGSET, 255, CFG_GET, CFG_ALL, 9, 0.0))
check([x[0] for x in r] == list(range(N)) and all(x[1] == CFGR_OK and x[2] == 9 and x[3] & ~CFGF_LAST == 0 for x in r), "read: cfgval for every setting, no change, with the same ref")
check([x[0] for x in r if x[3] & CFGF_LAST] == [N - 1], "read: only the last setting has CFGF_LAST (the receiver learns how many the sender knows)")
vals = {cfgspec.SPECS[x[0]].key: x[4] for x in r}
check(vals["haptics_buffer_ms"] == 20 and vals["rumble"] == 0.0 and vals["tap_click"] == 1.0 and all(x[4] == x[5] for x in r),
      "values = defaults, value from the file = value in use")

# ---------- change: at once in C.*, written to the file, a reply ----------
r = ask(setting("tap_click", False, 2))
check(r == [(ID["tap_click"], CFGR_OK, 2, CFGF_REMOTE, 0.0, 1.0)], f"a change that works at once: result OK, change flag, value and value from the file: {r}")
check(C.TAP_CLICK is False, "C.TAP_CLICK zmienione od razu")
check(saved() == {"version": 1, "values": {"tap_click": False}}, "saved to remote_cfg.json")
r = ask(setting("haptics_buffer_ms", 40, 3), setting("rumble", "off", 4))
check([(x[0], x[1], x[2]) for x in r] == [(ID["haptics_buffer_ms"], CFGR_LATER, 3), (ID["rumble"], CFGR_LATER, 4)], "settings counted from the next stream / pad: result LATER")
check(C.HAPTICS_BUFFER_MS == 40.0 and C.RUMBLE == "off", "C.* already has the new values (the next stream / pad picks them up)")
check(saved()["values"] == {"tap_click": False, "haptics_buffer_ms": 40, "rumble": "off"}, "all three changes in the file")
check(sender_config.BASE["haptics_buffer_ms"] == 20, "the value from sender.toml (BASE) untouched")
r = ask(pc.pack(T_CFGSET, 255, CFG_GET, CFG_ALL, 5, 0.0))
d = {cfgspec.SPECS[x[0]].key: x for x in r}
check(d["haptics_buffer_ms"][3] == CFGF_REMOTE and d["haptics_buffer_ms"][4] == 40 and d["haptics_buffer_ms"][5] == 20 and d["pad_min_dt"][3] == 0,
      "read after a change: the change flag only on changed ones, the value from the file next to it")

# ---------- bad values and unknown settings: nothing changes ----------
before = dict(sender_config.CHANGES)
for name, value in (("haptics_buffer_ms", 5000), ("haptics_buffer_ms", 3), ("speaker_opus_bytes", 10), ("rt_priority", 100), ("tap_max_s", 0.0)):
    r = ask(pc.pack(T_CFGSET, 255, CFG_SET, ID[name], 6, float(value)))
    check(r and r[0][1] == CFGR_INVALID, f"{name} = {value}: odmowa (poza granicami)")
for number in (math.nan, math.inf, 2.5):
    r = ask(pc.pack(T_CFGSET, 255, CFG_SET, ID["haptics_motors"], 6, number))
    check(r and r[0][1] == CFGR_INVALID, f"choice with the number {number}: refusal")
r = ask(pc.pack(T_CFGSET, 255, CFG_SET, ID["haptics_buffer_ms"], 6, 33.5))
check(r and r[0][1] == CFGR_INVALID, "a fraction for an integer: refusal")
r = ask(pc.pack(T_CFGSET, 255, CFG_SET, N + 5, 7, 1.0), pc.pack(T_CFGSET, 255, CFG_SET, CFG_ALL, 7, 1.0), pc.pack(T_CFGSET, 255, CFG_RESET, N + 5, 7, 0.0),
        pc.pack(T_CFGSET, 255, 9, 0, 7, 0.0))
check([x[1] for x in r] == [CFGR_UNKNOWN] * 4, "nieznany identyfikator, SET na wszystkie, nieznana operacja: UNKNOWN")
check(sender_config.CHANGES == before and C.HAPTICS_BUFFER_MS == 40.0, "the refusals changed nothing")

# ---------- a repeat of the same packet (3 copies) and a recording ----------
pkt = setting("battery_poll_s", 9.0, 8)
r = ask(pkt, pkt, pkt)
check(len(r) == 1 and C.BATT_POLL_S == 9.0, "three copies of the same packet = one change, one reply")
r = ask(pkt)
check(r == [] and C.BATT_POLL_S == 9.0, "an exact repeat (recording) rejected by the number filter / v3")

# ---------- reset: the value from sender.toml comes back ----------
r = ask(pc.pack(T_CFGSET, 255, CFG_RESET, ID["haptics_buffer_ms"], 10, 0.0))
check(r == [(ID["haptics_buffer_ms"], CFGR_OK, 10, 0, 20.0, 20.0)] and C.HAPTICS_BUFFER_MS == 20.0, f"reset of one: the value from the file in use, no change flag: {r}")
check("haptics_buffer_ms" not in saved()["values"], "reset usuwa wpis z remote_cfg.json")
r = ask(pc.pack(T_CFGSET, 255, CFG_RESET, ID["pad_min_dt"], 11, 0.0))
check(r and r[0][1] == CFGR_OK and r[0][3] == 0, "reset of a setting that was not changed: OK, no effect")
r = ask(pc.pack(T_CFGSET, 255, CFG_RESET, CFG_ALL, 12, 0.0))
check(len(r) == N and sender_config.CHANGES == {} and saved()["values"] == {} and C.TAP_CLICK is True and C.RUMBLE == "auto" and C.BATT_POLL_S == 5.0,
      "reset of all: cfgval for each, empty file, everything as in sender.toml")

# ---------- effects outside C.*: process latency and the pad dead zone ----------
calls = []
real_apply = sender_rt.apply
sender_rt.apply = lambda *a: calls.append(a) or ["x"]
ask(setting("rt_priority", 20, 13))
check(calls == [(C.LOW_LATENCY, C.CPU_LATENCY_US, 20)], f"rt_priority: sender_rt.apply with the current values: {calls}")
calls.clear()
ask(setting("cpu_latency_us", 77, 14), setting("low_latency", False, 15))
check(calls == [(True, 77, 20), (False, 77, 20)], f"cpu_latency_us and low_latency: apply with the whole set: {calls}")
calls.clear()
ask(setting("tap_click", True, 16))
check(not calls, "other settings do not touch the process")
sender_rt.apply = real_apply


class FakePad:
    def __init__(s): s.n = 0
    def refresh_deadzone(s): s.n += 1


state.active = {"a": FakePad(), "b": FakePad()}
ask(setting("stick_deadzone", 9, 17))
check(C.STICK_DZ == 9 and [p.n for p in state.active.values()] == [1, 1], "stick_deadzone: the new value at once on connected pads")
state.active = {}


class Stub:
    stick_dz = 3
    _raw_timer = None
stub = Stub()
Ctrl.refresh_deadzone(stub)
check(stub.stick_dz == 9, "Ctrl.refresh_deadzone takes the new dead zone")
stub.stick_dz, stub._raw_timer = 0, object()
Ctrl.refresh_deadzone(stub)
check(stub.stick_dz == 0, "w czasie pomiaru dryfu (CMD_RAW_STICKS) martwa strefa zostaje 0")

# ---------- sender_rt.apply: turning on and off ----------
fakes = {"realtime": lambda p: f"SCHED_FIFO {p}", "hold_cpu_latency": lambda us: f"latency {us}", "pin_fast_cpus": lambda: "fast cores 0",
         "short_switch_interval": lambda: "switch"}
saved_fns = {k: getattr(sender_rt, k) for k in fakes}
for k, f in fakes.items():
    setattr(sender_rt, k, f)
sender_rt._realtime_on = False
check(sender_rt.apply(True, 50, 10) == ["SCHED_FIFO 10", "latency 50", "fast cores 0", "switch"], "apply: turns real-time and latency on")
for k, f in saved_fns.items():
    setattr(sender_rt, k, f)
sender_rt._qos = io.BytesIO()
sender_rt._affinity = set(os.sched_getaffinity(0))
sender_rt._realtime_on = True
back = []
real_normal = sender_rt.normal_scheduler
def fake_normal():
    sender_rt._realtime_on = False                              # like the real function after a successful change
    back.append(1)
    return "normal scheduler"
sender_rt.normal_scheduler = fake_normal
sys.setswitchinterval(0.0005)
out = sender_rt.apply(False, 50, 0)
sender_rt.normal_scheduler = real_normal
check(out == ["normal scheduler", "CPU sleep allowed", "all cores", "GIL switch interval 5 ms"] and back == [1] and sender_rt._qos is None and sender_rt._affinity is None
      and abs(sys.getswitchinterval() - 0.005) < 1e-9, f"apply: turns off real-time, the PM QoS request, core pinning and the short GIL interval: {out}")
check(sender_rt.apply(False, 50, 0) == [], "apply: turning off again changes nothing")

# ---------- v2 with a key (before switching to v3) and no key: refusal ----------
state.flush_writes()
os.remove(C.AUTH_FILE)                                          # the remembered switch to v3 removed: a new codec starts in v2
net.init()                                                      # new codec: a key, not switched to v3 yet
net.fb_filter = proto.SeqFilter(1.0)                            # another "receiver process": a random start of the numbers (the filter accepts it after 1 s of silence)
check(net.codec.key == b"sekret" and not net.codec.v3, "codec with a key before the switch")
v2 = Codec(b"sekret")
before = dict(sender_config.CHANGES)
r = ask(setting("tap_click", False, 20, codec=v2), codec=v2)
check(r and r[0][0] == CFG_ALL and r[0][1] == CFGR_NO_V3 and r[0][2] == 20, f"signed v2 packet: refusal NO_V3 (without v3 a recording could be replayed): {r}")
check(sender_config.CHANGES == before and C.TAP_CLICK is True, "v2 changes nothing")
r = ask(v2.pack(T_CFGSET, 255, CFG_GET, CFG_ALL, 21, 0.0), codec=v2)
check(len(r) == 1 and r[0][1] == CFGR_NO_V3, "a read in v2 refused too - one reply, not 35")

C.KEY = b""
net.init()
net.fb_filter = proto.SeqFilter(1.0)
plain = Codec()
r = ask(setting("tap_click", False, 22, codec=plain), codec=plain)
check(r and r[0][1] == CFGR_NO_KEY and C.TAP_CLICK is True, f"sender without a key: refusal NO_KEY, changes nothing: {r}")
check(not any(s[0] >> 4 == proto.VER3 for s in net.sock.sent), "the reply without a key is a plain v2 packet (unsigned)")
C.KEY = b"sekret"
net.init()
net.codec.v3 = True
net.fb_filter = proto.SeqFilter(1.0)

# ---------- remote changes off: remote_cfg_file = "" ----------
C.REMOTE_CFG_FILE = ""
r = ask(pc.pack(T_CFGSET, 255, CFG_SET, ID["tap_click"], 30, 0.0))
check(r and r[0][0] == CFG_ALL and r[0][1] == CFGR_OFF and C.TAP_CLICK is True, f"remote_cfg_file = \"\": refusal OFF, nothing changes: {r}")
r = ask(pc.pack(T_CFGSET, 255, CFG_GET, CFG_ALL, 31, 0.0))
check(len(r) == 1 and r[0][1] == CFGR_OFF, "a read with changes turned off refused too")
C.REMOTE_CFG_FILE = os.path.join(TMP, "remote_cfg.json")

# ---------- after a restart: file loaded, bad entries skipped ----------
good = {"version": 1, "values": {"haptics_buffer_ms": 60, "tap_click": False, "rumble": "evdev", "speaker_opus_mode": "celt",
                                 "host": "10.0.0.9", "key": "x", "nope": 1, "speaker_buffer_ms": 5, "gestures": "yes", "rt_priority": 5.5}}
with open(C.REMOTE_CFG_FILE, "w") as f:
    json.dump(good, f)
cfg_path = os.path.join(TMP, "sender.toml")
with open(cfg_path, "w") as f:
    f.write('host = "127.0.0.1"\nhaptics_buffer_ms = 30\nremote_cfg_file = "{}"\n'.format(C.REMOTE_CFG_FILE.replace("\\", "/")))
sender_config.init(["--config", cfg_path])
check(sender_config.CHANGES == {"haptics_buffer_ms": 60, "tap_click": False, "rumble": "evdev", "speaker_opus_mode": "celt"},
      f"after a restart only allowed values within limits are loaded (host, key, unknown, out of range, wrong type skipped): {sender_config.CHANGES}")
check(sender_config.BASE["haptics_buffer_ms"] == 30 and C.HAPTICS_BUFFER_MS == 60.0 and C.TAP_CLICK is False and C.RUMBLE == "evdev"
      and C.SPEAKER_OPUS_MODE == "celt" and C.HOST == "127.0.0.1" and not C.KEY, "changes from the receiver win over sender.toml, host and key unchanged")
for broken in ("{not json", "[1, 2]", '{"values": 5}', ""):
    with open(C.REMOTE_CFG_FILE, "w") as f:
        f.write(broken)
    check(sender_config.load_changes(C.REMOTE_CFG_FILE) == {}, f"uszkodzony plik {broken!r}: ignorowany, sender startuje")
check(sender_config.load_changes(os.path.join(TMP, "brak.json")) == {} and sender_config.load_changes("") == {}, "no file and \"\": nothing to load")

if fails:
    sys.exit(1)
print("REMOTE CFG TX OK")
