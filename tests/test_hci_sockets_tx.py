"""Sender: raw HCI sockets.
    python3 tests/test_hci_sockets_tx.py sender"""
import errno
import sys
import tempfile
import time

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_btlink as BL

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


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

sys.exit(1 if fails else 0)
