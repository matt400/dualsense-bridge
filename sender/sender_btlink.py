"""Sender: the radio link of a connected pad (RSSI and link quality) for the Diagnostics tab in the receiver window.

BlueZ publishes RSSI of a Classic device only while a scan runs (see Device1.RSSI), so for a pad that is already connected
the sender asks the controller itself: the HCI commands Read RSSI and Read Link Quality, sent over a raw HCI socket. No extra
packages (hcitool is deprecated and often missing), it also works on musl; it needs root, which the sender runs as anyway.

How a reading is made:
  1. HCIGETCONNLIST on a socket (any bound state) lists the live connections of one controller; the entry with the pad's
     address gives the connection handle. The list layout is the same for packed and padded kernel structs (the 16-byte entries
     start 4 bytes in), unlike HCIGETCONNINFO, whose entry offset differs between the two.
  2. A socket bound to that controller (HCI_CHANNEL_RAW) sends the command with the handle and waits for the Command Complete
     event with the same opcode and handle (the socket filter lets only Command Complete / Command Status through).

Python's `socket` has no Bluetooth support on some builds (musl distributions), so the socket is created with plain numbers and
bound through libc with a hand-packed sockaddr_hci - the same way for every build.

What the numbers mean depends on the controller. The specification defines the Classic RSSI as dB relative to the Golden Receive
Power Range (0 = inside the range), but most controllers (the Qualcomm one in phones, for example) simply return the power in dBm,
and many return 0 for the link quality because they do not compute it. The receiver therefore reads the RSSI as dBm and treats
a link quality of 0 as "no data". A controller may support neither command (the status is "unknown command"): the value is then
missing, and the window shows a dash.
"""
from __future__ import annotations

import contextlib
import ctypes
import fcntl
import re
import socket
import struct
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from proto import mac_bytes

AF_BLUETOOTH: Final = getattr(socket, "AF_BLUETOOTH", 31)
BTPROTO_HCI: Final = 1
SOL_HCI, HCI_FILTER = 0, 2
HCI_CHANNEL_RAW: Final = 0
HCIGETCONNLIST: Final = 0x800448D4        # _IOR('H', 212, int) -> struct hci_conn_list_req
CONN_LIST_HEAD, CONN_ENTRY, CONN_MAX = 4, 16, 8   # the header (dev_id, conn_num), one hci_conn_info, how many entries we ask for
ENTRY_HANDLE_AT, ENTRY_ADDR_AT, ENTRY_TYPE_AT = 0, 2, 8    # offsets inside an entry: handle, bdaddr (little endian), link type
ACL_LINK: Final = 1
HCI_COMMAND_PKT, HCI_EVENT_PKT = 0x01, 0x04
EVT_CMD_COMPLETE, EVT_CMD_STATUS = 0x0E, 0x0F
OPCODE_LINK_QUALITY: Final = (0x05 << 10) | 0x0003      # OGF 5 (status parameters), OCF 3
OPCODE_RSSI: Final = (0x05 << 10) | 0x0005              # OGF 5, OCF 5
_FILTER_MASKS: Final = (1 << HCI_EVENT_PKT, (1 << EVT_CMD_COMPLETE) | (1 << EVT_CMD_STATUS), 0)    # packet types, events 0-31, events 32-63
# struct hci_filter in the three sizes seen: the kernel's own copy (16 bytes), the bare fields (14), and the userspace one with
# a 64-bit `unsigned long` type_mask (24). Some kernels refuse a size they did not expect, so they are tried in turn.
FILTERS: Final = (
    struct.pack("<IIIHxx", *_FILTER_MASKS, 0),
    struct.pack("<IIIH", *_FILTER_MASKS, 0),
    struct.pack("<QIIHxxxxxx", *_FILTER_MASKS, 0),
)
EVENT_MIN_LEN: Final = 7                  # the shortest event that can be classified (3-byte header + 4 bytes)
REPLY_MIN_LEN: Final = 10                 # Command Complete with a status, a handle and one value: 4 + 3 + 3 bytes
REPLY_WAIT_S: Final = 0.15                # how long one command may wait for its answer
HCI_NAME = re.compile(r"hci(\d+)$")


@dataclass(frozen=True, slots=True)
class Link:
    """One pad's reading: RSSI [dBm on most controllers] and link quality (0-255, 0 = not provided); None = no answer."""
    rssi: int | None
    quality: int | None
    hci: int


class Transport(Protocol):
    """What the reader needs from the HCI layer (replaced by a fake in tests)."""

    def devices(self) -> list[int]:
        """The numbers N of the controllers hciN that exist."""

    def handle(self, dev: int, mac: str) -> int | None:
        """The connection handle of the pad `mac` on controller `dev`, None when it is not connected there."""

    def command(self, dev: int, opcode: int, handle: int) -> bytes | None:
        """Send a command that takes a handle; the return parameters after the status byte, None on a failure or a non-zero status."""


def hci_command(opcode: int, handle: int) -> bytes:
    """The HCI command packet for a command with a single 2-byte parameter, the connection handle."""
    return struct.pack("<BHBH", HCI_COMMAND_PKT, opcode, 2, handle)


def classify_reply(packet: bytes, opcode: int, handle: int) -> tuple[bool, bytes | None]:
    """Decide what one event means for the command `opcode` sent for `handle`.

    A controller that does not support Read RSSI / Read Link Quality answers at once, with a Command Complete that has a
    non-zero status (often without the handle: "unknown command" is 4 + 3 bytes) or with a Command Status that has one.
    Such an answer is final, so the caller can stop waiting instead of sitting out the whole deadline.

    Returns:
        (final, params). `final` is True when the packet is the answer to this command: params are the bytes after the status
        and the echoed handle, or None when the controller refused. `final` False means "not ours, keep waiting": another event,
        another command / handle, a malformed packet, or a Command Status with status 0 (accepted, the Complete follows).
    """
    if len(packet) < EVENT_MIN_LEN or packet[0] != HCI_EVENT_PKT:
        return False, None
    if packet[1] == EVT_CMD_STATUS:                 # 04 0F 04 status ncmd opcode(2)
        refused = packet[3] != 0 and struct.unpack_from("<H", packet, 5)[0] == opcode
        return refused, None
    if packet[1] != EVT_CMD_COMPLETE:
        return False, None
    op, status = struct.unpack_from("<HB", packet, 4)
    if op != opcode:
        return False, None
    if len(packet) >= REPLY_MIN_LEN - 1 and struct.unpack_from("<H", packet, 7)[0] != handle:
        return False, None                          # the same command, but for another pad
    if status != 0:
        return True, None
    return (True, packet[9:]) if len(packet) >= REPLY_MIN_LEN else (False, None)


def find_handle(buf: bytes, mac: str) -> int | None:
    """The handle of the ACL connection to `mac` in an HCIGETCONNLIST answer (`buf`), None when it is not there."""
    raw = mac_bytes(mac)
    if raw is None or len(buf) < CONN_LIST_HEAD:
        return None
    count = struct.unpack_from("<HH", buf, 0)[1]
    want = raw[::-1]                          # the kernel keeps addresses little endian
    found = None
    for k in range(min(count, CONN_MAX)):
        at = CONN_LIST_HEAD + k * CONN_ENTRY
        if len(buf) < at + CONN_ENTRY or buf[at + ENTRY_ADDR_AT:at + ENTRY_ADDR_AT + 6] != want:
            continue
        handle = struct.unpack_from("<H", buf, at + ENTRY_HANDLE_AT)[0]
        if buf[at + ENTRY_TYPE_AT] == ACL_LINK:
            return handle
        found = found if found is not None else handle      # a LE / SCO entry only when there is no ACL one
    return found


def set_filter(s: socket.socket) -> None:
    """Let only Command Complete / Command Status events through the socket (a socket with no filter receives nothing).

    Raises:
        OSError: When the kernel refuses every layout of the filter (the last error is raised).
    """
    err: OSError | None = None
    for flt in FILTERS:
        try:
            s.setsockopt(SOL_HCI, HCI_FILTER, flt)
            return
        except OSError as e:
            err = e
    raise err if err is not None else OSError("no HCI filter layout")


class RawHci:
    """The real transport: raw HCI sockets (Linux, root)."""

    def __init__(self, sysfs: Path) -> None:
        """`sysfs` = /sys/class/bluetooth, where the controllers are listed."""
        self.sysfs = sysfs
        self.socks: dict[int, socket.socket] = {}          # one bound socket per controller (commands)
        self.query: socket.socket | None = None            # the unbound one for HCIGETCONNLIST
        self.libc: ctypes.CDLL | None = None               # libc.bind: loaded with the first socket

    def devices(self) -> list[int]:
        """Controller numbers from sysfs, ascending."""
        try:
            names = [p.name for p in self.sysfs.iterdir()]
        except OSError:
            return []
        return sorted(int(m.group(1)) for n in names if (m := HCI_NAME.match(n)))

    def handle(self, dev: int, mac: str) -> int | None:
        """HCIGETCONNLIST for controller `dev`; None for an error or when the pad is not in the list."""
        if self.query is None:
            try:
                self.query = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
            except OSError:             # no permission, no Bluetooth in the kernel / the Python build: no reading, not a crash
                return None
        buf = bytearray(struct.pack("<HH", dev, CONN_MAX) + bytes(CONN_ENTRY * CONN_MAX))
        try:
            fcntl.ioctl(self.query, HCIGETCONNLIST, buf, True)
        except OSError:
            return None
        return find_handle(bytes(buf), mac)

    def _sock(self, dev: int) -> socket.socket:
        """The socket bound to controller `dev` (created on first use) with the event filter set."""
        s = self.socks.get(dev)
        if s is not None:
            return s
        if self.libc is None:
            self.libc = ctypes.CDLL(None, use_errno=True)
        s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
        try:
            addr = struct.pack("<HHH", AF_BLUETOOTH, dev, HCI_CHANNEL_RAW)     # struct sockaddr_hci
            if self.libc.bind(s.fileno(), addr, len(addr)) != 0:
                raise OSError(ctypes.get_errno(), "bind(HCI) failed")
            set_filter(s)                                                       # after the bind, as BlueZ's hci_send_req does
            s.settimeout(REPLY_WAIT_S)
        except OSError:
            s.close()
            raise
        self.socks[dev] = s
        return s

    def command(self, dev: int, opcode: int, handle: int) -> bytes | None:
        """Send the command and wait (REPLY_WAIT_S at most) for its Command Complete.

        A refusal (a Complete with an error status, or a Command Status with one) is an answer too: None is returned at once and
        the socket stays open, because nothing is left in flight. Without that, a controller that lacks the command made every
        reading wait the whole deadline (per pad, one after another) and reopen its socket.

        When there is no answer at all (the deadline, the socket timeout, an error) the socket is closed: a late reply
        to this command would otherwise sit in its queue and be taken for the answer to the next one.
        """
        try:
            s = self._sock(dev)
            s.send(hci_command(opcode, handle))
            deadline = time.monotonic() + REPLY_WAIT_S
            while (left := deadline - time.monotonic()) > 0:
                s.settimeout(left)      # the whole wait is bounded by the deadline, not REPLY_WAIT_S per received packet
                final, reply = classify_reply(s.recv(260), opcode, handle)
                if final:
                    return reply
        except OSError:                 # no permission, the controller went away, the timeout of the socket
            pass
        self.drop(dev)                  # created again on the next reading
        return None

    def drop(self, dev: int) -> None:
        """Forget the socket of a controller (it is created again on the next reading)."""
        s = self.socks.pop(dev, None)
        if s is not None:
            with contextlib.suppress(OSError):
                s.close()

    def close(self) -> None:
        """Close every socket."""
        for dev in list(self.socks):
            self.drop(dev)
        if self.query is not None:
            with contextlib.suppress(OSError):
                self.query.close()
            self.query = None


class LinkReader:
    """Reads RSSI and link quality of connected pads; blocking calls, run it in a worker thread."""

    def __init__(self, transport: Transport | None) -> None:
        """`transport` None = no reading at all (no controller access, tests)."""
        self.hci = transport
        self.where: dict[str, int] = {}         # pad MAC -> the controller it was last found on
        self.warned = False

    def read(self, mac: str) -> Link | None:
        """One pad's reading, None when it is not connected on any controller or the controller answered neither command."""
        t = self.hci
        if t is None:
            return None
        devs = t.devices()
        first = self.where.get(mac)
        for dev in ([first] if first in devs else []) + [d for d in devs if d != first]:
            handle = t.handle(dev, mac)
            if handle is None:
                continue
            self.where[mac] = dev
            rssi = t.command(dev, OPCODE_RSSI, handle)
            quality = t.command(dev, OPCODE_LINK_QUALITY, handle)
            if rssi is None and quality is None and not self.warned:
                self.warned = True
                print(f"Bluetooth link: hci{dev} answers neither Read RSSI nor Read Link Quality (or no permission) - no link data")
            return Link(struct.unpack("b", rssi[:1])[0] if rssi else None, quality[0] if quality else None, dev)
        return None

    def sample(self, macs: Iterable[str]) -> list[tuple[str, Link]]:
        """Readings of the given pads (the ones that could not be read are left out)."""
        out = []
        for mac in macs:
            link = self.read(mac)
            if link is not None:
                out.append((mac, link))
        return out
