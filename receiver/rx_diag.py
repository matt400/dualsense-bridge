"""Receiver: the numbers behind the Diagnostics tab - the network link to the sender and the Bluetooth link of each pad (no Qt).

Everything here is cheap and always on, so the tab shows the last minute the moment it is opened:

  network   round trip (the sender's ping median, from the info packet), jitter (the interarrival jitter of the sender's pings,
            RFC 3550: how much the spacing of the pings at the PC differs from their spacing at the sender - it needs no common
            clock), packet loss from gaps in the sender's sequence numbers, and the late / duplicated packets that arrived;
  pad       the pad's report rate over Bluetooth (the info packet), the state packets per second reaching the PC, the radio link
            read by the sender (btlink: RSSI and link quality, see sender_btlink), how many times the pad connected.

`sample()` is called by the window's slow refresh (SAMPLE_S) and appends one value per series; a series holds HISTORY values
(about a minute). A missing value (the pad is off, the sender has no reading) is None, which the charts draw as a gap.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Final

import rx_state as S
from proto import BODY, BTL_LQ, BTL_RSSI, T_BTLINK, mac_text

SAMPLE_S: Final = 0.4                  # the window's slow refresh - one sample per series
HISTORY: Final = 150                   # samples kept (150 x 0.4 s = 60 s)
LINK_STALE_S: Final = 4.0              # a btlink reading older than this is not shown (the sender sends one a second)
PING_RESTART_S: Final = 1.0            # a longer gap between the sender's pings restarts the jitter estimate
NS_PER_MS: Final = 1_000_000
JITTER_GAIN: Final = 16                # RFC 3550: J += (|D| - J) / 16

# the levels follow the window's colour classes ("ok", "warn", "bad", "" = no judgement)
RTT_WARN_MS, RTT_BAD_MS = 8.0, 25.0
JITTER_WARN_MS, JITTER_BAD_MS = 2.0, 8.0
LOSS_WARN_PCT, LOSS_BAD_PCT = 0.5, 2.0
RSSI_WARN_DB, RSSI_BAD_DB = -3, -10    # relative to the golden receive power range: 0 = inside it, negative = below
QUALITY_WARN_PCT, QUALITY_BAD_PCT = 78, 50


class Series:
    """A fixed-length history of one value; None = no value at that moment."""

    def __init__(self) -> None:
        """Start empty."""
        self.values: deque[float | None] = deque(maxlen=HISTORY)

    def add(self, v: float | None) -> None:
        """Append a sample (the oldest one falls out)."""
        self.values.append(v)

    def last(self) -> float | None:
        """The newest sample, None when it is a gap or the series is empty."""
        return self.values[-1] if self.values else None

    def stats(self) -> tuple[float, float, float] | None:
        """(min, average, max) of the real samples, None when there are none."""
        real = [v for v in self.values if v is not None]
        return (min(real), sum(real) / len(real), max(real)) if real else None

    def clear(self) -> None:
        """Forget the history."""
        self.values.clear()


@dataclass
class Link:
    """The last btlink reading of a pad: RSSI [dB, relative to the golden range] and quality (0-255); None = not read."""
    rssi: int | None
    quality: int | None
    hci: int
    t: float


@dataclass
class PadDiag:
    """The history and the counters of one pad slot."""
    hz: Series = field(default_factory=Series)            # the pad's report rate [Hz]
    pps: Series = field(default_factory=Series)           # state packets per second reaching the PC
    rssi: Series = field(default_factory=Series)
    quality: Series = field(default_factory=Series)       # as a percentage (0-100)
    link: Link | None = None
    connects: int = 0                                     # connections since the receiver started


class NetDiag:
    """The sender <-> receiver link: histories, the running jitter and the loss counters of the last second."""

    def __init__(self) -> None:
        """Start with empty histories."""
        self.rtt, self.jitter, self.loss, self.pps = Series(), Series(), Series(), Series()
        self.jitter_ms: float | None = None               # the running RFC 3550 estimate
        self.lost = self.late = self.dup = 0               # totals since the receiver started
        self.loss_pct: float | None = None                 # the last second (None = too few packets to say)
        self._prev: tuple[int, int] | None = None          # (the sender's ping time, the arrival time) of the previous ping [ns]
        self._last_ping = 0.0                              # monotonic time of the last ping (silence = the estimate is stale)
        self._pps = 0.0                                    # state packets per second, all pads, last second

    def ping(self, sent_ns: int, arrived_ns: int, now: float | None = None) -> None:
        """One ping of the sender: update the interarrival jitter (RFC 3550, in ns, published in ms).

        D = (arrival spacing) - (send spacing) of two consecutive pings; the estimate follows |D| with gain 1/16. A silence
        longer than PING_RESTART_S or a clock that goes back (the sender restarted) starts the estimate over.
        """
        now = time.monotonic() if now is None else now
        prev = self._prev
        self._prev = (sent_ns, arrived_ns)
        restart = now - self._last_ping > PING_RESTART_S
        self._last_ping = now
        if prev is None or restart:
            self.jitter_ms = None
            return
        if sent_ns <= prev[0]:
            return                                         # a repeated or replayed ping: no spacing to measure
        d = abs((arrived_ns - prev[1]) - (sent_ns - prev[0])) / NS_PER_MS
        self.jitter_ms = d if self.jitter_ms is None else self.jitter_ms + (d - self.jitter_ms) / JITTER_GAIN

    def window(self, loss_pct: float | None, lost: int, late: int, dup: int, pps: float) -> None:
        """The counters of one finished second (from rx_net.LinkStats.tick)."""
        self.loss_pct = loss_pct
        self.lost += lost
        self.late += late
        self.dup += dup
        self._pps = pps


net = NetDiag()
pads: list[PadDiag] = []


def init(n: int) -> None:
    """Create the per-pad records for `n` slots and forget the network history."""
    global net
    net = NetDiag()
    pads[:] = [PadDiag() for _ in range(n)]


def on_ping(sent_ns: int, arrived_ns: int) -> None:
    """The network thread: a ping of the sender arrived."""
    net.ping(sent_ns, arrived_ns)


def pad_connected(i: int) -> None:
    """A pad connected in slot i: count it (the window shows the number of reconnects, a sign of a bad link)."""
    if 0 <= i < len(pads):
        pads[i].connects += 1
        pads[i].link = None


def slot_of(mac: str) -> int | None:
    """The slot whose pad has this MAC (S.pad_id holds it lower case, from the ident packet)."""
    mac = mac.lower()
    return next((i for i, pid in enumerate(S.pad_id or ()) if pid == mac), None)


def on_btlink(data: bytes, now: float | None = None) -> None:
    """A btlink packet (the network thread): the radio link of one connected pad.

    A pad that has not sent its ident yet is unknown here - the next reading, a second later, finds it.
    """
    now = time.monotonic() if now is None else now
    mac, rssi, quality, flags, hci = BODY[T_BTLINK].unpack_from(data, 4)
    i = slot_of(mac_text(mac))
    if i is None or i >= len(pads):
        return
    pads[i].link = Link(rssi if flags & BTL_RSSI else None, quality if flags & BTL_LQ else None, hci, now)


def link_of(i: int, now: float | None = None) -> Link | None:
    """The pad's latest btlink reading, None when there is none or it is too old."""
    now = time.monotonic() if now is None else now
    lk = pads[i].link if 0 <= i < len(pads) else None
    return lk if lk is not None and now - lk.t < LINK_STALE_S else None


def rtt_ms() -> float | None:
    """The sender's round trip: the newest value any pad's info packet carries."""
    best = max((x for x in (S.info or ()) if x is not None and x[0] is not None), key=lambda x: x[3], default=None)
    return best[0] if best is not None and time.monotonic() - best[3] < S.SENDER_TIMEOUT else None


def sample(now: float | None = None) -> None:
    """One sample of every series (the window's slow refresh). Pads that are not connected get gaps."""
    now = time.monotonic() if now is None else now
    online = S.sender_online(now)
    net.rtt.add(rtt_ms() if online else None)
    net.jitter.add(net.jitter_ms if online and now - net._last_ping < S.SENDER_TIMEOUT else None)
    net.loss.add(net.loss_pct if online else None)
    net.pps.add(net._pps if online else None)
    for i, p in enumerate(pads):
        alive = bool(S.alive[i])
        info = S.info[i] if alive else None
        p.hz.add(info[1] if info is not None and now - info[3] < S.SENDER_TIMEOUT else None)
        p.pps.add(S.pps[i] if alive else None)
        lk = link_of(i, now) if alive else None
        p.rssi.add(lk.rssi if lk is not None else None)
        p.quality.add(None if lk is None or lk.quality is None else lk.quality * 100 / 255)


def _level(v: float | None, warn: float, bad: float, higher_is_worse: bool = True) -> str:
    """"ok" / "warn" / "bad" for a value against two thresholds, "" when there is no value."""
    if v is None:
        return ""
    if higher_is_worse:
        return "bad" if v >= bad else "warn" if v >= warn else "ok"
    return "bad" if v <= bad else "warn" if v <= warn else "ok"


def rtt_level(ms: float | None) -> str:
    """Colour class of the round trip."""
    return _level(ms, RTT_WARN_MS, RTT_BAD_MS)


def jitter_level(ms: float | None) -> str:
    """Colour class of the jitter."""
    return _level(ms, JITTER_WARN_MS, JITTER_BAD_MS)


def loss_level(pct: float | None) -> str:
    """Colour class of the packet loss."""
    return _level(pct, LOSS_WARN_PCT, LOSS_BAD_PCT)


def rssi_level(db: float | None) -> str:
    """Colour class of the RSSI (relative to the golden range: 0 and above is fine)."""
    return _level(db, RSSI_WARN_DB, RSSI_BAD_DB, higher_is_worse=False)


def quality_level(pct: float | None) -> str:
    """Colour class of the link quality (as a percentage)."""
    return _level(pct, QUALITY_WARN_PCT, QUALITY_BAD_PCT, higher_is_worse=False)
