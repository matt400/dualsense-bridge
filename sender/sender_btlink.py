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

For Bluetooth Classic the RSSI is not dBm: the controller reports it relative to the Golden Receive Power Range (0 = inside the
range, negative = that many dB below it, positive = above). A controller may support neither command (the status is "unknown
command"): the value is then missing, and the window shows a dash.
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
FILTER: Final = struct.pack("<IIIH", 1 << HCI_EVENT_PKT, (1 << EVT_CMD_COMPLETE) | (1 << EVT_CMD_STATUS), 0, 0)
REPLY_MIN_LEN: Final = 10                 # Command Complete with a status, a handle and one value: 4 + 3 + 3 bytes
REPLY_WAIT_S: Final = 0.15                # how long one command may wait for its answer
HCI_NAME = re.compile(r"hci(\d+)$")


@dataclass(frozen=True, slots=True)
class Link:
    """One pad's reading: RSSI [dB, relative to the golden range] and link quality (0-255); None = the controller did not answer."""
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


def parse_reply(packet: bytes, opcode: int, handle: int) -> bytes | None:
    """The return parameters (after the status byte) of the Command Complete for `opcode` and `handle`.

    Returns:
        The bytes after the status and the echoed handle, or None for another event, another command / handle, a short packet
        or a non-zero status. A Command Status (the controller refused before running it) also gives None.
    """
    if len(packet) < REPLY_MIN_LEN or packet[0] != HCI_EVENT_PKT or packet[1] != EVT_CMD_COMPLETE:
        return None
    op, status, echoed = struct.unpack_from("<HBH", packet, 4)
    if op != opcode or status != 0 or echoed != handle:
        return None
    return packet[9:]


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
            self.query = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
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
            s.setsockopt(SOL_HCI, HCI_FILTER, FILTER)
            addr = struct.pack("<HHH", AF_BLUETOOTH, dev, HCI_CHANNEL_RAW)     # struct sockaddr_hci
            if self.libc.bind(s.fileno(), addr, len(addr)) != 0:
                raise OSError(ctypes.get_errno(), "bind(HCI) failed")
            s.settimeout(REPLY_WAIT_S)
        except OSError:
            s.close()
            raise
        self.socks[dev] = s
        return s

    def command(self, dev: int, opcode: int, handle: int) -> bytes | None:
        """Send the command and wait (REPLY_WAIT_S at most) for its Command Complete."""
        try:
            s = self._sock(dev)
            s.send(hci_command(opcode, handle))
            deadline = time.monotonic() + REPLY_WAIT_S
            while time.monotonic() < deadline:
                reply = parse_reply(s.recv(260), opcode, handle)
                if reply is not None:
                    return reply
        except OSError:                 # no permission, the controller went away, the timeout of the socket
            self.drop(dev)
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
