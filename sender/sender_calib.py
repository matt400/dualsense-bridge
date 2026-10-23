"""Sender: gyro calibration (at startup and on request from the receiver window) and saving the bias per pad (MAC).

A pad with a saved calibration (gyro_cal_file) does not calibrate at startup - it uses the stored bias right away, so
it does not have to be put down after switching on. Autobias (sender_streams) keeps fine-tuning the bias in memory;
the saved value does not change.
Calibration from the window (CMD_GYRO_CAL): the pad lies still for arg seconds; the result (calres packet) goes back
to the receiver.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Final, TypeAlias

import sender_net as net
from proto import CAL_FEW, CAL_MAX_S, CAL_MOVED, CAL_OK, CALF_STORED, T_CALRES, clamp, write_json
from sender_config import BURST_DELAYS, C

if TYPE_CHECKING:
    from sender_ctrl import Ctrl

Bias: TypeAlias = list[float]        # gyro bias [x, y, z]
CalResult: TypeAlias = tuple[int, Bias | None]    # (CAL_* status, bias when CAL_OK)

MIN_SAMPLES: Final = 20              # fewer samples than this in the window = no data (CAL_FEW)
MIN_REQUEST_S: Final = 1             # shortest calibration the window may ask for
DUP_S: Final = 0.3       # the command arrives 3x (UDP) - repeats within this time do not restart the calibration
BIAS_DECIMALS: Final = 1             # the stored bias is rounded: more digits are noise
AXES: Final = 3
_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", re.IGNORECASE)
_saved: dict[str, dict[str, Any]] | None = None     # MAC -> {"bias": [x, y, z], "t": save time (unix)}


def _load() -> dict[str, dict[str, Any]]:
    """Return the saved-calibration table, reading gyro_cal_file lazily on first use and caching it.

    Entries are normalised (lower-case MAC, three float bias values, integer timestamp); malformed entries or an
    unreadable file are reported and ignored, so a damaged file can never prevent the sender from starting.
    """
    global _saved
    if _saved is None:
        _saved = {}
        if C.GYRO_CAL_FILE:
            try:
                with open(C.GYRO_CAL_FILE, encoding="utf-8") as f:
                    raw = json.load(f)
                _saved = {str(k).lower(): {"bias": [float(x) for x in v["bias"]][:AXES], "t": int(v.get("t", 0))}
                          for k, v in raw.items() if len(v["bias"]) == AXES}
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                print(f"Gyro calibration file {C.GYRO_CAL_FILE} ignored: {e!r}")
    return _saved


def stored(key: str | None) -> dict[str, Any] | None:
    """Return the saved calibration of a pad as {"bias", "t"}, or None (none saved / pad without a MAC)."""
    return _load().get((key or "").lower())


def save(key: str | None, bias: Iterable[float]) -> bool:
    """Save a pad's bias (only pads with a MAC - a USB pad without uniq has a changing key).

    The table is updated in memory first (so the new value is used even if the file cannot be written), then written
    atomically with proto.write_json.

    Returns:
        True if saved.
    """
    if not key or not _MAC.fullmatch(key):
        return False
    table = _load()
    table[key.lower()] = {"bias": [round(b, BIAS_DECIMALS) for b in bias], "t": int(time.time())}
    if not C.GYRO_CAL_FILE:
        return True
    try:
        write_json(C.GYRO_CAL_FILE, table, indent=1, sort_keys=True)
    except OSError as e:
        print(f"Cannot write {C.GYRO_CAL_FILE}: {e!r}")
        return False
    return True


class GyroCal:
    """Collect gyro samples for `secs` seconds; feed() returns None (still collecting) or (CAL_*, bias | None).

    The pad must lie still: when the time is up, too few samples give CAL_FEW, a spread of any axis larger than
    CAL_MAX_RANGE gives CAL_MOVED, otherwise the bias is the per-axis mean of all samples (CAL_OK).
    """
    __slots__ = ("end", "samples", "user")

    def __init__(self, now: float, secs: float, user: bool) -> None:
        """Start a window of ``secs`` seconds at ``now``; ``user`` tells that the window asked for it (the result is saved)."""
        self.end = now + secs
        self.samples: list[tuple[float, ...]] = []
        self.user = user

    def feed(self, now: float, gyro: Iterable[float]) -> CalResult | None:
        """Add one gyro sample (x, y, z); once the window has ended, evaluate and return the result."""
        if now < self.end:
            self.samples.append(tuple(gyro))
            return None
        if len(self.samples) < MIN_SAMPLES:
            return CAL_FEW, None
        axes = list(zip(*self.samples, strict=True))
        if any(max(a) - min(a) > C.CAL_MAX_RANGE for a in axes):
            return CAL_MOVED, None
        return CAL_OK, [sum(a) / len(a) for a in axes]


def report(c: Ctrl, status: int, repeat: bool = False) -> None:
    """Send a calres packet to the receiver: status, the saved bias and the bias the sender is using right now.

    With repeat=True a calibration result is sent two more times (BURST_DELAYS) with the same values - a lost packet
    then cannot leave the window showing "no response" for a calibration that succeeded and is saved; the receiver
    drops the repeats.
    """
    st = stored(c.key)
    sb = [round(x) for x in st["bias"]] if st else [0, 0, 0]
    vals = (status, CALF_STORED if st else 0, *sb, *(round(x) for x in c.gyro_bias), st["t"] if st else 0)
    net.emit(T_CALRES, c.idx, *vals)
    if not repeat:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for delay in BURST_DELAYS:
        loop.call_later(delay, lambda: net.emit(T_CALRES, c.idx, *vals))


def request(c: Ctrl, secs: int, now: float | None = None) -> None:
    """Command from the window: calibrate for `secs` seconds (stream_motion starts it on the next sample).

    The duration is clamped; a duplicate arriving within DUP_S is ignored. A pad that has no motion sensor yet (e.g.
    still connecting) cannot be calibrated, so a failure status is reported straight away.
    """
    now = time.monotonic() if now is None else now
    secs = clamp(secs, MIN_REQUEST_S, CAL_MAX_S)
    if now - c.cal_cmd_t < DUP_S:
        return
    c.cal_cmd_t = now
    if c.motion_dev is None:            # a pad without a motion sensor (e.g. still connecting) - nothing to calibrate
        report(c, CAL_FEW, repeat=True)
        return
    c.cal_request = secs


def startup_done(c: Ctrl, result: CalResult) -> None:
    """Result of the startup calibration (not saved - only a calibration from the window is saved)."""
    status, bias = result
    if status == CAL_OK and bias is not None:
        c.gyro_bias = bias
        print(f"Controller {c.idx}: gyro bias {[round(b) for b in bias]}")
    else:
        print(f"Controller {c.idx}: calibration skipped (pad was moving)")


def finish(c: Ctrl, result: CalResult) -> None:
    """Result of a calibration requested from the window: save it and answer the receiver.

    On success the new bias becomes active and is persisted; if the pad moved or too few samples arrived, the bias
    stays unchanged. In every case the receiver is told the outcome (with repeats against packet loss).
    """
    status, bias = result
    if status == CAL_OK and bias is not None:
        c.gyro_bias = bias
        saved = save(c.key, bias)
        print(f"Controller {c.idx}: gyro calibrated {[round(b) for b in bias]}" + (" (saved)" if saved else ""))
    else:
        print(f"Controller {c.idx}: gyro calibration rejected ({'pad was moving' if status == CAL_MOVED else 'no data'})")
    report(c, status, repeat=True)
