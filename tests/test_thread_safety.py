"""Receiver: parallel write_file calls use separate .tmp files; SeqFilter.forget survives a parallel fresh(); Codec.pack numbers
packets uniquely from several threads (no lock) and builds the same bytes as header + body.
    python3 tests/test_thread_safety.py receiver"""
import json, os, sys, threading
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import proto
from proto import T_STATE, SeqFilter

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- write_file: two parallel writes of the same file do not share a temporary file ----------
path = os.path.join(TMP, "shared.json")
errors = []
def writer(n):
    try:
        for k in range(50):
            proto.write_json(path, {"w": n, "k": k})
    except OSError as e:
        errors.append(e)
threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
for t in threads: t.start()
for t in threads: t.join()
check(not errors, f"parallel writes of the same file without errors: {errors[:1]}")
check(isinstance(json.load(open(path, encoding="utf-8")), dict), "after the race the file is complete JSON")
check(not [n for n in os.listdir(TMP) if n.startswith("shared.json")and n != "shared.json"], "no abandoned .tmp files")

temps = []
real_replace = os.replace
os.replace = lambda src, dst: temps.append(src) or real_replace(src, dst)
try:
    proto.write_file(path, "a"); proto.write_file(path, "b")
finally:
    os.replace = real_replace
check(len(temps) == 2 and all(t.endswith(".tmp") and t != path + ".tmp" for t in temps), "the temp file name contains the pid and the thread")

# ---------- SeqFilter.forget: a new key from another thread during iteration does not break the dict ----------
flt = SeqFilter()
for slot in range(4):
    flt.fresh((T_STATE, slot), 1, 0.0)
def match(k):
    flt.fresh((T_STATE, 99), 1, 0.0)              # this is how the network thread would behave during forget()
    return k[1] == 1
try:
    flt.forget(match)
    ok = True
except RuntimeError:
    ok = False
check(ok, "forget with a parallel fresh() does not raise RuntimeError")
check(flt.fresh((T_STATE, 1), 1, 0.1) and not flt.fresh((T_STATE, 2), 1, 0.1), "a forgotten stream accepts an old number, the rest do not")

# ---------- Codec.pack: unique sequence numbers from several threads, the same bytes as header + body ----------
codec = proto.Codec()
seqs, lock = [], threading.Lock()
def packer():
    mine = [proto.HDR.unpack_from(codec.pack(proto.T_PING, 255, 1))[2] for _ in range(4000)]
    with lock:
        seqs.extend(mine)
threads = [threading.Thread(target=packer) for _ in range(5)]
for t in threads: t.start()
for t in threads: t.join()
check(len(set(seqs)) == len(seqs) == 20000, f"5 threads x 4000 packets: every sequence number once ({len(set(seqs))} distinct)")
bad = []
for ptype, body in proto.BODY.items():
    vals = body.unpack(bytes(body.size))
    pkt = codec.pack(ptype, 3, *vals)
    seq = proto.HDR.unpack_from(pkt)[2]
    if pkt != proto.HDR.pack(proto.HDR0 | proto.WIRE.get(ptype, ptype), 3, seq) + body.pack(*vals):
        bad.append(ptype)
check(not bad, f"one Struct per type packs exactly header + body for every type ({bad})")

sys.exit(1 if fails else 0)
