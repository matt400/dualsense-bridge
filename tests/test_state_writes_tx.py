"""Sender: state files are written through the worker thread, not with an fsync on the event loop.
    python3 tests/test_state_writes_tx.py sender"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_btadapter as BA
import sender_net as net
import sender_state as state

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- 3: state files are written through the worker thread, not with an fsync on the loop ----------
saved = []
codec = proto.Codec(b"k" * 16, state_path=os.path.join(TMP, "auth.json"), writer=lambda path, obj: saved.append((path, obj)))
codec._upgrade()
check(saved == [(os.path.join(TMP, "auth.json"), {"v3": proto.key_fingerprint(b"k" * 16)})] and codec.v3,
      "Codec._upgrade saves the fingerprint through the given writer")
plain = proto.Codec(b"k" * 16, state_path=os.path.join(TMP, "auth2.json"))
plain._upgrade()
check(os.path.exists(os.path.join(TMP, "auth2.json")), "without a writer the default write_json still saves the file")
net.init()
check(net.codec.writer is state.write_json_later, "the sender's codec uses state.write_json_later")

calls = []
real_later = BA.write_json_later
BA.write_json_later = lambda path, obj, **kw: calls.append((path, obj, kw))
try:
    BA.save_mode(os.path.join(TMP, "bt.json"), BA.BTA_PICK, "hci1")
finally:
    BA.write_json_later = real_later
check(calls == [(os.path.join(TMP, "bt.json"), {"mode": "pick", "device": "hci1"}, {"indent": 2})], f"btadapter.save_mode goes through write_json_later: {calls}")


async def loop_not_blocked():
    """While save_mode runs inside the loop, the write happens in the writer thread."""
    seen = []
    real = proto.write_file
    import threading
    main_thread = threading.get_ident()
    def spy(path, content):
        seen.append(threading.get_ident())
        real(path, content)
    state.write_file = spy
    try:
        BA.save_mode(os.path.join(TMP, "bt2.json"), BA.BTA_AUTO)
        state.flush_writes()
    finally:
        state.write_file = real
    check(seen and seen[0] != main_thread, "save_mode: the file is written by a worker thread")

asyncio.run(loop_not_blocked())

sys.exit(1 if fails else 0)
