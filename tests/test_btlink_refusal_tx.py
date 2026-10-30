"""Sender: a controller without Read RSSI / Link Quality refuses at once; the reading does not wait the whole deadline and keeps its socket.
    python3 tests/test_btlink_refusal_tx.py sender"""
import struct, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_btlink as BL
from pathlib import Path

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


OP = BL.OPCODE_RSSI
def complete(opcode, status, handle=None, tail=b""):
    body = struct.pack("<BHB", 1, opcode, status) + (struct.pack("<H", handle) + tail if handle is not None else b"")
    return bytes([4, 0x0E, len(body)]) + body
def cmd_status(opcode, status):
    return bytes([4, 0x0F, 4, status, 1]) + struct.pack("<H", opcode)

# ---------- btlink: a refusal is an answer ----------
check(BL.classify_reply(complete(OP, 1), OP, 42) == (True, None), "Complete 'unknown command' (status only, no handle): final, no value")
check(BL.classify_reply(complete(OP, 1, 42, b"\x00"), OP, 42) == (True, None), "Complete with an error status and the handle: final, no value")
check(BL.classify_reply(complete(OP, 1, 43, b"\x00"), OP, 42) == (False, None), "an error for another handle: not ours, keep waiting")
check(BL.classify_reply(complete(BL.OPCODE_LINK_QUALITY, 1), OP, 42) == (False, None), "an error for another command: keep waiting")
check(BL.classify_reply(cmd_status(OP, 1), OP, 42) == (True, None), "Command Status with an error for this opcode: final")
check(BL.classify_reply(cmd_status(OP, 0), OP, 42) == (False, None), "Command Status 0 (accepted): keep waiting for the Complete")
check(BL.classify_reply(cmd_status(BL.OPCODE_LINK_QUALITY, 1), OP, 42) == (False, None), "Command Status for another opcode: keep waiting")
check(BL.classify_reply(complete(OP, 0, 42, b"\xfb"), OP, 42) == (True, b"\xfb"), "a good Complete: final, with the value")


class Refusing:
    """Answers every command at once with an error; any wait beyond a few ms would mean it was ignored."""
    def __init__(self, reply):
        self.reply, self.closed = reply, False
    def send(self, data):
        pass
    def settimeout(self, value):
        pass
    def recv(self, n):
        return self.reply
    def close(self):
        self.closed = True


for name, reply in (("Complete with an error", complete(OP, 1)), ("Command Status with an error", cmd_status(OP, 1))):
    raw = BL.RawHci(Path("/nonexistent"))
    sock = Refusing(reply)
    raw.socks[3] = sock
    t0 = time.monotonic()
    got = raw.command(3, OP, 42)
    check(got is None and time.monotonic() - t0 < BL.REPLY_WAIT_S / 2, f"RawHci.command: {name} returns None at once")
    check(raw.socks.get(3) is sock and not sock.closed, f"RawHci.command: {name} keeps the socket open")

sys.exit(1 if fails else 0)
