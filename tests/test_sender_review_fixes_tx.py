"""Sender: regressions from the review - fsync off the event loop, raw HCI sockets, small-uptime timers, gyro bias after a
stream restart, one-time config migration, the rumble fallback only in "auto", English messages in the install files.
    python3 tests/test_sender_review_fixes_tx.py sender"""
import asyncio
import errno
import os
import re
import socket
import sys
import tempfile
import time
import types

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_btadapter as BA
import sender_btlink as BL
import sender_migrate as MIG
import sender_net as net
import sender_rumble as R
import sender_state as state
import sender_streams as ST
import sender_update as UPD
import sender_wol as W
from evdev import ecodes as ec

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


# ---------- 4 + 5: raw HCI sockets ----------
def refuse(*a, **kw):
    raise OSError(errno.EPERM, "Operation not permitted")

real_socket = BL.socket.socket
BL.socket.socket = refuse
try:
    try:
        got = BL.RawHci(BL.Path("/nonexistent")).handle(0, "aa:bb:cc:dd:ee:01")
        err = None
    except OSError as e:
        got, err = "raised", e
finally:
    BL.socket.socket = real_socket
check(got is None and err is None, f"RawHci.handle: a socket that cannot be created gives None, no exception ({got!r}, {err!r})")


class Chatty:
    """Keeps answering with other commands' replies, so the wait ends by the deadline, not by an exception."""
    def __init__(self):
        self.timeouts = []
    def send(self, data):
        pass
    def settimeout(self, value):
        self.timeouts.append(value)
    def recv(self, n):
        time.sleep(0.05)
        return bytes([4, 0x0E, 7, 1]) + (BL.OPCODE_LINK_QUALITY).to_bytes(2, "little") + bytes([0]) + (7).to_bytes(2, "little") + b"\x01"
    def close(self):
        self.closed = True


raw = BL.RawHci(BL.Path("/nonexistent"))
sock = Chatty()
raw.socks[3] = sock
t0 = time.monotonic()
check(raw.command(3, BL.OPCODE_RSSI, 42) is None, "RawHci.command: no matching reply by the deadline gives None")
check(3 not in raw.socks and getattr(sock, "closed", False), "RawHci.command: the socket is closed after the deadline, so a late reply cannot reach the next command")
check(time.monotonic() - t0 < BL.REPLY_WAIT_S + 0.1 and sock.timeouts and max(sock.timeouts) <= BL.REPLY_WAIT_S,
      f"RawHci.command: the whole wait stays within REPLY_WAIT_S (per-recv timeouts {sock.timeouts})")


# ---------- 6: guards compared with time.monotonic() work right after boot ----------
real_mono = time.monotonic
time.monotonic = lambda: 5.0           # 5 s of uptime: shorter than every guard below
try:
    check(UPD._last == float("-inf") and 5.0 - UPD._last >= UPD.REPEAT_GUARD_S, "update: the first request is not inside the repeat guard")
    check(W._last_sent == float("-inf") and 5.0 - W._last_sent >= W.REPEAT_GUARD_S, "WoL: the first send is not inside the repeat guard")
    check(net.last_pong == float("-inf"), "net: no pong yet = -inf")
    check(not W.receiver_alive(), "WoL: a receiver that never answered is not 'alive' at a small uptime")
    check(not (5.0 - net.last_pong < net.ALIVE_S), "net: not alive before the first pong at a small uptime")
finally:
    time.monotonic = real_mono
src = open(os.path.join(sys.argv[1], "sender_ctrl.py"), encoding="utf-8").read()
check('self.powering_off = float("-inf")' in src, "Ctrl.powering_off starts at -inf (power-off retry guard)")


# ---------- 7: a restarted motion stream keeps the autobias-corrected gyro bias ----------
stored_table = {"bias": [10.0, 20.0, 30.0]}
real_stored = ST.calib.stored
ST.calib.stored = lambda key: stored_table
try:
    pad = types.SimpleNamespace(idx=0, key="aa:bb:cc:dd:ee:01", gyro_bias=[0.0, 0.0, 0.0], bias_known=False)
    check(ST._start_cal(pad, 0.0) is None and pad.gyro_bias == [10.0, 20.0, 30.0] and pad.bias_known, "first start: the saved bias is applied")
    pad.gyro_bias[0] += 3.5                       # what autobias learned since
    check(ST._start_cal(pad, 1.0) is None and pad.gyro_bias == [13.5, 20.0, 30.0], f"stream restart: the learned bias stays ({pad.gyro_bias})")
finally:
    ST.calib.stored = real_stored


# ---------- 8: the config migration runs once per key ----------
toml = os.path.join(TMP, "sender.toml")
open(toml, "w").write("host = \"1.2.3.4\"\npad_min_dt = 0.004\n")
check([d[0] for d in MIG.migrate(toml, log=lambda m: None)] == ["pad_min_dt"], "first run: the old default is migrated")
open(toml, "w").write("host = \"1.2.3.4\"\npad_min_dt = 0.004   # my own value\n")
check(MIG.migrate(toml, log=lambda m: None) == [] and "pad_min_dt = 0.004" in open(toml).read(),
      "later run: a value equal to the old default, set by the user afterwards, is kept")
toml2 = os.path.join(TMP, "other.toml")
open(toml2, "w").write("host = \"1.2.3.4\"\npad_min_dt = 0.002\n")
MIG.migrate(toml2, log=lambda m: None)
open(toml2, "w").write("host = \"1.2.3.4\"\npad_min_dt = 0.004\n")
check(MIG.migrate(toml2, log=lambda m: None) == [], "a file with no old defaults on the first run is also marked as done")


# ---------- 9: the HID fallback only in "auto" ----------
class Dev:
    def capabilities(self):
        return {ec.EV_FF: [ec.FF_RUMBLE]}
    def upload_effect(self, eff):
        raise OSError(errno.ENODEV, "No such device")
    def write(self, *a):
        pass


async def fallback():
    for mode, expect_open in (("evdev", False), ("auto", True)):
        r = R.Rumble(1, Dev(), mode)
        opened = []
        r._open_hidraw = lambda given=None, o=opened: (o.append(1) or False)
        opened.clear()
        r.set(50, 60)
        check(bool(opened) == expect_open, f"mode {mode!r}: evdev failure {'opens' if expect_open else 'does not open'} hidraw (opened={bool(opened)})")
        r.close()

asyncio.run(fallback())


# ---------- 10: install files, the unit and the example config are English ----------
root = os.path.dirname(os.path.abspath(sys.argv[1]))
polish = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]|\b(Brak|Klucz|klucz|Uruchomienie|prawdziwa|nie jest|sklonuj|skopiuj|widocznym)\b")
for rel in ("sender/install-service.sh", "sender/setup-update-key.sh", "sender/service/dualsense-sender.service", "sender/sender.example.toml"):
    bad = [ln.strip() for ln in open(os.path.join(root, rel), encoding="utf-8") if polish.search(ln)]
    check(not bad, f"{rel}: no Polish text {bad}")

print("ALL OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
