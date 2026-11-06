"""Receiver: sender settings from the Sender tab - settings.json section, cfgval handling, push on online, gating by key / v3.
    python3 tests/test_sender_cfg.py receiver"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import cfgspec
import rx_sender_cfg as SC
import rx_settings
from proto import (BODY, HEADER_LEN, CFG_ALL, CFG_GET, CFG_RESET, CFG_SET, CFGF_LAST, CFGF_REMOTE, CFGR_NO_KEY, CFGR_OK, CFGR_UNKNOWN, T_CFGSET, T_CFGVAL,
                   Codec)
from chk import check, fails                                  # noqa: E402

# ---------- settings.json: sekcja "sender" ----------
path = os.path.join(TMP, "sender_settings.json")
good = rx_settings.Settings(sender=rx_settings.SenderConfig(values={"max_pads": 4, "rumble": "off", "pad_min_dt": 0.002}))
rx_settings.save(good, path)
check(rx_settings.load(path).sender.values == good.sender.values, "sender section: write and read give the same")
raw = json.load(open(path, encoding="utf-8"))
raw["sender"]["values"].update({"max_pads": 99, "nieznane": 1, "low_latency": "tak", "rumble": "evdev"})
json.dump(raw, open(path, "w", encoding="utf-8"))
loaded = rx_settings.load(path).sender.values
check("max_pads" not in loaded and "nieznane" not in loaded and "low_latency" not in loaded,
      "a bad value, an unknown name and a wrong type are skipped (with a message)")
check(loaded.get("rumble") == "evdev" and loaded.get("pad_min_dt") == 0.002, "the rest of the table stays")
check(rx_settings.load(os.path.join(TMP, "nie_ma.json")).sender.values == {}, "no file: empty section")

# ---------- codecs: sender (tx) and receiver (N.codec) with a v3 key ----------
clock = [1_000_000]
def codecs(v3=True, key=b"k" * 32):
    a = Codec(key, token=lambda: clock[0], now_ms=lambda: clock[0])
    b = Codec(key, token=lambda: clock[0], now_ms=lambda: clock[0])
    a.v3 = b.v3 = v3
    return a, b
tx, N.codec = codecs()
S.peer = ("127.0.0.1", 5005)
wire: list[bytes] = []
N.send_peer = lambda pkt: wire.append(pkt)
N.send_repeat = lambda pkt, n: [wire.append(pkt) for _ in range(n)]

def requests():
    out = []
    for pkt in wire:
        p = codecs()[0].parse(pkt)          # a new codec for each copy: the repeat filter will not reject the copy
        check(p is not None and p[0] == T_CFGSET, "the packet on the wire is a cfgset, signed and fresh")
        out.append(BODY[T_CFGSET].unpack_from(pkt, HEADER_LEN))
    wire.clear()
    return out

def cfgval(name, value, base, remote=False, result=CFGR_OK):
    spec = cfgspec.SPECS[cfgspec.BY_KEY[name]]
    data = tx.pack(T_CFGVAL, 255, cfgspec.BY_KEY[name], result, 7, CFGF_REMOTE if remote else 0,
                   cfgspec.to_wire(spec, value), cfgspec.to_wire(spec, base))
    p = N.codec.parse(data)
    N._on_control(p[0], p[1], p[2], data, S.peer, time.monotonic(), 0)

check(SC.can_send(), "key and v3: sending allowed")
SC.set_value("max_pads", 3)
req = requests()
check(len(req) == 3 and req[0][0] == CFG_SET and req[0][1] == cfgspec.BY_KEY["max_pads"] and req[0][3] == 3.0,
      "a change = 3 copies of the same cfgset with the value")
check(len({r[2] for r in req}) == 1, "the copies have the same reference (the sender acts once)")
SC.set_value("rumble", "hidraw")
check(requests()[0][3] == float(cfgspec.RUMBLE_CHOICES.index("hidraw")), "a choice goes as an index")
try:
    SC.set_value("max_pads", 99)
    check(False, "a bad value rejected before sending")
except ValueError:
    check(not wire, "a bad value rejected before sending, nothing went out")
SC.reset("max_pads")
check(requests()[0][:2] == (CFG_RESET, cfgspec.BY_KEY["max_pads"]), "back to the file = cfgset RESET with the setting number")
SC.reset()
check(requests()[0][:2] == (CFG_RESET, CFG_ALL), "everything back = RESET for CFG_ALL")
SC.request_all()
check(requests()[0][:2] == (CFG_GET, CFG_ALL), "read = GET for CFG_ALL")

# ---------- gate: without a key or without v3 nothing goes out ----------
for label, (v3, key) in {"no v3": (False, b"k" * 32), "no key": (False, b"")}.items():
    _t, N.codec = codecs(v3, key)
    check(not SC.can_send(), f"{label}: can_send = False")
    SC.set_value("max_pads", 3); SC.request_all(); SC.reset("max_pads")
    check(not wire, f"{label}: nothing sent")
tx, N.codec = codecs()

# ---------- cfgval trafia do tabeli ----------
S.sender_cfg.clear(); S.sender_cfg_result = None
gen = S.sender_cfg_gen
cfgval("max_pads", 3, 2, remote=True)
row = S.sender_cfg["max_pads"]
check((row.value, row.base, row.remote) == (3, 2, True) and S.sender_cfg_gen == gen + 1, "cfgval: value, file, change flag")
check(S.sender_cfg_result == CFGR_OK, "a reply for a setting = the sender is listening")
cfgval("speaker_opus_mode", "celt", "hybrid")
check(S.sender_cfg["speaker_opus_mode"].value == "celt" and not S.sender_cfg["speaker_opus_mode"].remote, "a choice comes back as text")
data = tx.pack(T_CFGVAL, 255, CFG_ALL, CFGR_NO_KEY, 7, 0, 0.0, 0.0)
p = N.codec.parse(data)
N._on_control(p[0], p[1], p[2], data, S.peer, time.monotonic(), 0)
check(S.sender_cfg_result == CFGR_NO_KEY, "a refusal of the whole request remembered (the window shows the reason)")

# ---------- a full read, then one reconcile ----------
S.sender_cfg.clear(); S.sender_cfg_result = None
rx_settings.apply(rx_settings.Settings(sender=rx_settings.SenderConfig(values={"max_pads": 4, "tap_click": False})))
wire.clear()
watch = SC.Watch()
watch.online()
watch.tick(100.0)
check(requests()[0][:2] == (CFG_GET, CFG_ALL), "sender online: read of every setting")
for k, spec in enumerate(cfgspec.SPECS):
    base = spec.choices[0] if spec.kind == cfgspec.KIND_CHOICE else (spec.lo if spec.kind != cfgspec.KIND_BOOL else True)
    cfgval(spec.key, base, base)
check(SC.complete(), "after replies for every setting the read is complete")
watch.tick(100.1)
sent_now = requests()
names = sorted(cfgspec.SPECS[r[1]].key for r in sent_now[::3])
check(len(sent_now) == 6 and names == ["max_pads", "tap_click"] and all(r[0] == CFG_SET for r in sent_now),
      "reconcile: only what the sender lacks (2 settings x 3 copies)")
watch.tick(200.0)
check(not wire, "reconcile only once")

# ---------- retrying an incomplete read and no retries after a refusal ----------
S.sender_cfg.clear(); S.sender_cfg_result = None
watch.online(); watch.tick(300.0); requests()
cfgval("max_pads", 2, 2)
watch.tick(300.5)
check(not wire, "before RETRY_S has passed no new query")
watch.tick(300.0 + SC.RETRY_S + 0.1)
check(requests()[0][:2] == (CFG_GET, CFG_ALL), "incomplete read: ask again")
for n in range(SC.MAX_TRIES + 2):
    watch.tick(400.0 + n * 10)
wire.clear()
watch.tick(900.0)
check(not wire, "po MAX_TRIES koniec ponawiania")
S.sender_cfg.clear(); S.sender_cfg_result = None
watch.online(); watch.tick(1000.0); wire.clear()
S.sender_cfg_result = CFGR_NO_KEY
watch.tick(1100.0)
check(not wire and not watch.pending, "after the sender refuses no retry")

# ---------- a change in the settings goes to the sender (rx_settings listener) ----------
rx_settings.apply(rx_settings.Settings())
wire.clear()
rx_settings.apply(rx_settings.Settings(sender=rx_settings.SenderConfig(values={"gestures": False})))
r = requests()
check(len(r) == 3 and r[0][:2] == (CFG_SET, cfgspec.BY_KEY["gestures"]) and r[0][3] == 0.0, "saving the settings with a new value = SET")
rx_settings.apply(rx_settings.Settings(sender=rx_settings.SenderConfig(values={"gestures": False})))
check(not wire, "the same section a second time: nothing goes out")
rx_settings.apply(rx_settings.Settings())
check(requests()[0][:2] == (CFG_RESET, cfgspec.BY_KEY["gestures"]), "a removed entry = RESET of that setting")
rx_settings.apply(rx_settings.Settings(mouse=rx_settings.MouseConfig(sens=0.7)))
check(not wire, "a change in another section does not touch the sender")

# ---------- version compatibility: a newer sender (more settings) and an older one (does not know a setting) ----------
def raw_cfgval(key, result, value=1.0):
    data = tx.pack(T_CFGVAL, 255, key, result, 9, 0, value, value)
    p = N.codec.parse(data)
    N._on_control(p[0], p[1], p[2], data, S.peer, time.monotonic(), 0)

S.sender_cfg.clear(); S.sender_cfg_result = None
cfgval("max_pads", 2, 2)
raw_cfgval(len(cfgspec.SPECS), CFGR_OK)                  # an id added in a newer sender, sent as part of its normal answer
check(S.sender_cfg_result == CFGR_OK, "newer sender: an unknown setting in the reply is not a refusal of the whole read")
check(len(S.sender_cfg) == 1, "an unknown setting creates no row")
raw_cfgval(cfgspec.BY_KEY["max_pads"], CFGR_UNKNOWN, 0.0)   # an older sender that does not know a setting this receiver asked for
check("max_pads" not in S.sender_cfg and S.sender_cfg_result == CFGR_OK,
      "older sender does not know a setting: no row of zeros, the rest works")
raw_cfgval(CFG_ALL, CFGR_NO_KEY)
check(S.sender_cfg_result == CFGR_NO_KEY, "a refusal of the whole request (CFG_ALL) still works")

# ---------- an older sender knows fewer settings: the read still completes, the differences go out ----------
def answer_read(count, last_flag):
    """The sender's answer to a read: settings 0..count-1, the last one with CFGF_LAST when the sender is v17+."""
    for k in range(count):
        spec = cfgspec.SPECS[k]
        v = cfgspec.to_wire(spec, cfgspec.check(spec, spec_default(spec)))
        flags = CFGF_LAST if last_flag and k == count - 1 else 0
        data = tx.pack(T_CFGVAL, 255, k, CFGR_OK, 7, flags, v, v)
        p = N.codec.parse(data)
        N._on_control(p[0], p[1], p[2], data, S.peer, time.monotonic(), 0)

def spec_default(spec):
    return spec.choices[0] if spec.kind == cfgspec.KIND_CHOICE else (False if spec.kind == cfgspec.KIND_BOOL else spec.lo)

OLD = 5                                                     # an older sender: only the first OLD settings exist there
rx_settings.cur = rx_settings.Settings(sender=rx_settings.SenderConfig(values={"max_pads": 3}))
check(cfgspec.BY_KEY["max_pads"] < OLD, "the setting from the test is known to the old sender")

watch = SC.Watch(); watch.online(); watch.tick(2000.0); wire.clear()
answer_read(OLD, last_flag=True)
check(S.sender_cfg_count == OLD and SC.complete(), "v17+: CFGF_LAST tells how many settings the sender knows - the read is complete")
watch.tick(2000.1)
r = requests()
check(not watch.pending and [x[:2] for x in r[::3]] == [(CFG_SET, cfgspec.BY_KEY["max_pads"])], "the reconcile goes out at once")

watch = SC.Watch(); watch.online(); watch.tick(3000.0); wire.clear()
answer_read(OLD, last_flag=False)                           # a v16 sender: no flag
watch.tick(3000.0 + SC.RETRY_S + 0.1)
check(watch.pending and S.sender_cfg_count is None, "no flag: one more query first (it could have been a loss)")
requests()
answer_read(OLD, last_flag=False)                           # the repeat brings nothing new
watch.tick(3000.0 + 2 * SC.RETRY_S + 0.2)
check(S.sender_cfg_count == OLD and SC.complete(), "no flag: the list did not grow during the whole period - that is all the sender knows")
watch.tick(3000.0 + 2 * SC.RETRY_S + 0.3)
r = requests()
check(not watch.pending and [x[:2] for x in r[::3]] == [(CFG_SET, cfgspec.BY_KEY["max_pads"])], "no flag: the reconcile goes out too")

watch = SC.Watch(); watch.online(); watch.tick(4000.0); wire.clear()
answer_read(OLD, last_flag=False)
S.sender_cfg.pop(cfgspec.SPECS[2].key)                      # a hole in the run: a lost packet, not the end of the list
watch.tick(4000.0 + SC.RETRY_S + 0.1); requests()
watch.tick(4000.0 + 2 * SC.RETRY_S + 0.2)
check(S.sender_cfg_count is None and watch.pending, "a hole in the list is a loss: keep asking, the count stays unsettled")
rx_settings.cur = rx_settings.Settings()

sys.exit(1 if fails else 0)
