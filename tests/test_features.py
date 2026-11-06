"""Receiver: settings backups (rx_backup), pad -> virtual DS4 latency measurement (state2 / pong2, rx_latency)
and fixed trigger resistance from the profile (rx_shape, rx_feedback, trig packet)."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, store, sent)
import proto
import rx_backup as BK
import rx_latency as L
import rx_net
import rx_status as ST
from proto import (BODY, LAT_NONE, STATE_FIELDS, T_PONG, T_PONG2, T_STATE, T_STATE2, T_TRIG, TRIG_FORCE_MAX, Codec)
from rx_buttons import normalize_profile
from rx_shape import legacy_values, normalize_triggers, trigger_values

from chk import ok
C.CONSOLE_PATH = os.path.join(TMP, "console.json")


def write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- settings backups ----------
assert BK.list_backups() == [] and BK.create("update", "a" * 40) is None, "without settings files there is nothing to back up"
write(C.SETTINGS_PATH, {"v": 1}); write(C.REMAP_PATH, {"profiles": {"x": 1}})
b = BK.create("update", "1111111aaaa", "2222222bbbb")
assert b.files == ("settings.json", "remap.json") and b.commit == "1111111" and b.new == "2222222" and b.reason == "update"
assert os.path.dirname(BK.backup_dir()) == os.path.dirname(C.SETTINGS_PATH)
names = [b.name] + [BK.create("manual").name for _ in range(BK.KEEP + 2)]
lst = BK.list_backups()
assert len(lst) == BK.KEEP and lst[0].name == names[-1] and b.name not in {x.name for x in lst}, "the KEEP newest stay"
assert len({x.name for x in lst}) == BK.KEEP, "backups from the same second do not overwrite each other"
ok("backups: only existing files, version metadata, KEEP newest, unique names")

oldest = lst[-1]
write(C.SETTINGS_PATH, {"v": 2}); write(C.CONSOLE_PATH, {"c": 1})
okr, msg = BK.restore(oldest.name, "3333333")
assert okr and read(C.SETTINGS_PATH) == {"v": 1} and read(C.CONSOLE_PATH) == {"c": 1}, "a file outside the backup untouched"
after = BK.list_backups()
assert after[0].reason == "restore" and "console.json" in after[0].files and len(after) == BK.KEEP, \
    "before restoring a backup of the current ones; the oldest (restored) one may have gone - the data was already in memory"
assert not any(f.endswith(".tmp") for f in os.listdir(TMP))
assert BK.restore("../" + os.path.basename(TMP), "")[0] is False and BK.restore("nie-ma", "")[0] is False
okr2, _m = BK.restore(after[0].name)
assert okr2 and read(C.SETTINGS_PATH) == {"v": 2}, "a restore can be undone"
os.makedirs(os.path.join(BK.backup_dir(), "pusty"))
assert all(x.name != "pusty" for x in BK.list_backups()), "a folder without settings files skipped"
ok("restore: a backup of the current ones first, atomic write, undo, protection against ../")

# an update makes a backup after fetching the code, and a backup error does not spoil the result
import rx_update
BK.create = (lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
assert "disk full" in rx_update._backup("a", "b")
import importlib; importlib.reload(BK)
assert rx_update._backup("aaaaaaa", "bbbbbbb") in ("kopia ustawień zapisana", "settings backed up")
ok("update: backup after git pull, a backup error only in the message")

# ---------- protocol: state2 / pong2 recognized by length ----------
cod = Codec()
st = (128, 128, 128, 128, 0, 0, 0, 0, 3, 80, 0, 1, 2, 3, 4, 5, 6)
assert len(st) == STATE_FIELDS
p1, p2 = cod.pack(T_STATE, 0, *st), cod.pack(T_STATE2, 0, *st, 123456, 1500, LAT_NONE)
assert p1[0] & 0x0F == p2[0] & 0x0F == T_STATE and len(p2) - len(p1) == 8
assert cod.parse(p1)[0] == T_STATE and cod.parse(p2)[0] == T_STATE2
assert cod.parse(cod.pack(T_PONG2, 255, 1, 2))[0] == T_PONG2 and cod.parse(cod.pack(T_PONG, 255, 1))[0] == T_PONG
assert cod.parse(p2[:-1]) is None and cod.parse(cod.pack(T_TRIG, 0, 1, 2, 3, 4))[0] == T_TRIG
kc = Codec(b"klucz")
assert kc.parse(kc.pack(T_STATE2, 1, *st, 1, 2, 3))[0] == T_STATE2, "with an HMAC signature too"
root = os.path.abspath(os.path.join(os.path.dirname(proto.__file__), ".."))
assert os.path.basename(os.path.dirname(os.path.abspath(proto.__file__))) == "common"
assert not os.path.exists(os.path.join(root, "sender", "proto.py")) and not os.path.exists(os.path.join(root, "receiver", "proto.py")), \
    "proto.py must be only in common/"
ok("protocol: state2 / pong2 / trig by length, signature, one proto.py in common/")

# ---------- measurement: the network segment from the PC clock ----------
arr = 5_000_000_000                                   # ns
assert rx_net._latency((arr // 1000 - 700, 1500, 300), arr) == (arr, 1500, 300, 700)
assert rx_net._latency((0, 1500, 300), arr) is None and rx_net._latency((1, LAT_NONE, 300), arr) is None
wrap = rx_net._latency(((arr // 1000 - 700) & 0xFFFFFFFF, 1500, 300), arr)
assert wrap[3] == 700, "32-bit send time - overflow does not matter"
assert rx_net._latency((arr // 1000 + 50, 1500, 300), arr)[3] == 0, "clocks offset by a fraction of the RTT -> 0, not negative"
assert rx_net._latency((arr // 1000 - 3_000_000, 1500, 300), arr) is None, "network > 1 s = a bad clock, not a measurement"
ok("network measurement: PC clock, 32-bit overflow, negative -> 0, absurd rejected")

# ---------- rx_latency: segment means, mean / p95 / max of the sum, window ----------
now = time.perf_counter_ns()
assert L.summary(0, now) is None
for k in range(L.MIN_SAMPLES - 1):
    L.add(0, (now - 2_000_000, 1000, 200, 500), now - 1_000_000)
assert L.summary(0, now) is None, "too few samples"
L.add(0, (now - 2_000_000, 1000, LAT_NONE, 500), now - 1_000_000)
sm = L.summary(0, now)
assert (sm.phone, sm.net, sm.pc, sm.bt, sm.n) == (1.0, 0.5, 1.0, 0.2, L.MIN_SAMPLES), sm
assert abs(sm.avg - (9 * 2.7 + 2.5) / 10) < 1e-9 and sm.p95 == sm.max == 2.7, sm
L.add(0, (now - 5_000_000_000, 1, 1, 1), now)
assert L.summary(0, now).n == L.MIN_SAMPLES, "a sample with a DS4 attached after 5 s of waiting rejected"
assert L.summary(0, now + int(L.WINDOW_S * 1e9) + 1) is None, "old samples drop out of the window"
rows = [(None, 1000, 0, 0)] * 95 + [(None, 5000, 0, 0)] * 4 + [(None, 20_000, 0, 0)]
st100 = L._summarize(rows)
assert (st100.p95, st100.max, st100.n) == (1.0, 20.0, 100) and abs(st100.avg - (95 + 20 + 20) / 100) < 1e-9, st100
assert L._summarize(rows + [(None, 5000, 0, 0)]).p95 == 5.0, "p95 = 95th percentile by the nearest-rank method"
parts = ST.latency_parts(sm)
assert [k for k, *_ in parts] == ["bt", "phone", "net", "pc"]
assert [k for k, *_ in ST.latency_parts(L.Summary(sm.avg, sm.p95, sm.max, None, sm.phone, sm.net, sm.pc, sm.n))] \
    == ["phone", "net", "pc"], "older sender without pad timestamps: no Bluetooth segment"
assert ST.stats_text(sm) == "zwykle ≤ 2.7 · max 2.7"
S.alive[0] = True; S.info[0] = None; S.link_loss = 0.0
assert ST.latency_level(sm, 0, time.monotonic()) == "ok"
assert ST.latency_level(L.Summary(20, 30, 40, None, 1, 1, 1, 50), 0, time.monotonic()) == "bad"
S.link_loss = 3.0
assert ST.latency_level(sm, 0, time.monotonic()) == "warn", "packet loss raises the level despite a low latency"
S.link_loss = 0.0
L.clear(0); assert L.summary(0, now) is None
ok("rx_latency: segment means, mean / p95 / max of the sum, 5 s window, levels, clearing")

# handle(): state2 stores the measurement of the oldest pending change, a repeat does not overwrite it
S.want[0].set(); S.alive[0] = True
S.ignore_until[0] = 0
S.states[0]["lat"] = None; S.last_v[0] = None
d = cod.pack(T_STATE2, 0, *st, (arr // 1000 - 400) & 0xFFFFFFFF, 900, 100)
rx_net.handle(T_STATE2, 0, d, arr)
assert S.states[0]["lat"] == (arr, 900, 100, 400), S.states[0]["lat"]
st2 = (130, *st[1:])
rx_net.handle(T_STATE2, 0, cod.pack(T_STATE2, 0, *st2, 1, 5, 5), arr + 10)
assert S.states[0]["lat"] == (arr, 900, 100, 400), "the measurement of the oldest unsent change stays"
rx_net.clear_slot(0)
assert S.states[0]["lat"] is None and not S.alive[0]
ok("handle: measurement from state2 into the slot state (rx_ds4 ends it in ViGEm), slot clearing")

# ---------- trigger resistance: normalization, profile, sending from the feedback loop ----------
assert normalize_triggers({"l2": {"start": 15, "force": 99}, "r2": {"force": 0}, "x": {}}) == \
    {"l2": {"mode": "res", "start": 9, "force": TRIG_FORCE_MAX}}, "old format: ranges, strength 0 = no entry"
assert normalize_triggers("x") == {} and normalize_triggers({"r2": "x"}) == {}
p = normalize_profile({"triggers": {"r2": {"start": 3, "force": 6}}})
assert legacy_values(trigger_values(p["triggers"])) == (0, 0, 3, 6) and trigger_values({}) == (0,) * 12
ok("trigger resistance: normalization and packet values")

trig = []
send = lambda i, v, n: trig.append((i, v, n))
S.alive[0] = True
F.trig_sent[0] = None
S.remap_maps[0] = normalize_profile({})
F.triggers_tick(0, 100.0, send)
assert trig == [(0, (0, 0, 0, 0), F.TRIG_REPEAT)], \
    "after connecting the state always goes out - the pad may still have effects from the previous receiver run"
F.triggers_tick(0, 100.0 + 3 * F.TRIG_REFRESH_S, send)
assert len(trig) == 1, "no effects - no refresh"
trig.clear()
F.trig_sent[0] = None
S.remap_maps[0] = p
F.triggers_tick(0, 100.0, send); F.triggers_tick(0, 100.5, send)
assert trig == [(0, (0, 0, 3, 6), F.TRIG_REPEAT)], "change at once, a few copies (UDP)"
F.triggers_tick(0, 100.0 + F.TRIG_REFRESH_S, send)
assert trig[-1] == (0, (0, 0, 3, 6), 1), "refresh (pad after reconnecting)"
S.remap_maps[0] = normalize_profile({})
F.triggers_tick(0, 103.0, send)
assert trig[-1] == (0, (0, 0, 0, 0), F.TRIG_REPEAT), "removing the resistance reaches the pad too"
n = len(trig); F.triggers_tick(0, 110.0, send)
assert len(trig) == n, "no resistance - no refresh"
ok("feedback: resistance on a profile change, refresh, turning off")

sent.clear()
rx_net.send_triggers(1, (0, 0, 3, 6), 2); rx_net.flush_repeats()
pk = [x for x in sent if x[0] == T_TRIG]
assert len(pk) == 2 and pk[0][1] == 1 and BODY[T_TRIG].unpack_from(pk[0][3], 4) == (0, 0, 3, 6), pk
ok("rx_net.send_triggers: trig packet to the sender")

S.alive[0] = True
S.sender_info[0] = None
assert "older" in ST.triggers_status(0)[0] or "starszej" in ST.triggers_status(0)[0]
S.sender_info[0] = (6, 3, 0); assert ST.triggers_status(0)[1] == "warn"
S.sender_info[0] = (6, 7, 0); assert ST.triggers_status(0) == ("", "")
S.alive[0] = False; S.sender_info[0] = None; assert ST.triggers_status(0) == ("", "")
ok("resistance status: old sender / no HID / works / pad not connected")

# ---------- attaching the virtual DS4: a change from before it is not latency ----------
import threading
import rx_ds4


class SlowPad:                                            # tworzenie DS4 w ViGEm trwa (tu 0.3 s)
    def __init__(self):
        time.sleep(0.3)

    def register_notification(self, cb):
        pass

    def unregister_notification(self):
        pass

    def update_extended_report(self, rep):
        pass


rx_ds4.vg.VDS4Gamepad = SlowPad
rx_ds4.init()
L.clear(0); L.reset_buttons(0)
arr = time.perf_counter_ns()
with S.lock:                                              # the first state after connecting, before the DS4 exists
    S.states[0]["lat"] = (arr, 500, LAT_NONE, 800)
    S.states[0]["blat"] = (arr, 500, LAT_NONE, 800)
S.want[0].set()
t = threading.Thread(target=rx_ds4.slot_thread, args=(0,), daemon=True)
t.start()
end = time.monotonic() + 3
while not S.plugged[0] and time.monotonic() < end:
    time.sleep(0.01)
time.sleep(0.05)
S.want[0].clear(); S.wake[0].set(); t.join(2)
assert S.plugged[0] is False and not L._samples.get(0) and not L._buttons.get(0), \
    "the DS4 creation time does not go into the measurement (a max / mean jump after every connection)"
S.push = None
ok("virtual DS4: a change from before attaching is not a latency sample")
print("FEATURES OK")
