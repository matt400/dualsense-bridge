"""Receiver: real pad -> virtual DS4 latency, split into segments (window: the pad card).

Every sample is one pad state change, measured from the moment the phone kernel received the report from the pad until
the report was handed to the virtual DS4 (ViGEm):

  bt     Bluetooth delay above the best case of the last 2 s (pad clock vs the kernel receive time) -
         radio queueing and retransmissions; the constant part of the radio delay (~1-2 ms) cannot be measured
         without a clock shared with the pad
  phone  phone kernel -> packet send (sender wake-up, merging of changes within the pad_min_dt window)
  net    network: send -> receive on the PC, in the PC clock (the sender knows the clock offset from the pongs, like NTP)
  pc     packet receive -> report in ViGEm (remap, pad_hz limit, driver call)

Two counters per slot:
  all changes (button, stick, motion) from the last WINDOW_S - summary()
  buttons, D-pad and triggers only (crossing the TRIG_ON threshold) - the last BUTTON_KEEP presses / releases with no
  time limit (presses are rare, 5 s would give just a few samples) - buttons(); reset from the window: reset_buttons()
Result: the mean, p95 and max of the total plus the mean of every segment (the segment bar adds up to the mean).
"""
import collections
import math
import threading
import time
from dataclasses import dataclass
from statistics import fmean

from proto import LAT_NONE

WINDOW_S = 5.0
MIN_SAMPLES = 10
BUTTON_KEEP = 100
MAX_PC_US = 1_000_000
_lock = threading.Lock()
_samples = collections.defaultdict(lambda: collections.deque(maxlen=8000))   # slot -> (time ns, bt, phone, net, pc)
_buttons = collections.defaultdict(lambda: collections.deque(maxlen=BUTTON_KEEP))   # slot -> (bt, phone, net, pc)


@dataclass(frozen=True, slots=True)
class Summary:
    avg: float            # mean of the total [ms]
    p95: float            # 95th percentile of the total [ms]
    max: float            # worst sample [ms]
    bt: float | None      # segment means [ms]; bt None = the sender has no pad timestamps
    phone: float
    net: float
    pc: float
    n: int                # number of samples


def _row(lat, done_ns):
    """lat = (receive ns, phone µs, bt µs, net µs) from rx_net -> (bt | None, phone, net, pc) or None.

    The PC segment is the time from the packet receive to `done_ns`. A sample whose PC time is negative or above
    MAX_PC_US is dropped. bt is None when the sender sent no pad timestamp (LAT_NONE).
    """
    arr, phone, bt, net = lat
    pc = (done_ns - arr) // 1000
    if not 0 <= pc < MAX_PC_US:       # the change waited for a virtual DS4 that did not exist - that is not latency
        return None
    return None if bt == LAT_NONE else bt, phone, net, pc


def add(i, lat, done_ns):
    """Record a sample for slot i: the report with this change reached ViGEm at done_ns."""
    if (r := _row(lat, done_ns)) is not None:
        with _lock:
            _samples[i].append((done_ns, *r))


def add_button(i, lat, done_ns):
    """Record a button sample for slot i: the report with a button / D-pad / trigger (threshold) change reached ViGEm at done_ns."""
    if (r := _row(lat, done_ns)) is not None:
        with _lock:
            _buttons[i].append(r)


def clear(i):
    """Restart the continuous measurement of a slot (silence, another pad). The button counter stays - see reset_buttons."""
    with _lock:
        _samples.pop(i, None)


def reset_buttons(i):
    """Clear the button samples of slot i (the reset button in the window)."""
    with _lock:
        _buttons.pop(i, None)


def _mean(xs):
    """Mean of microsecond values in milliseconds, None for an empty list."""
    return fmean(xs) / 1000 if xs else None


def _summarize(rows):
    """[(bt | None, phone, net, pc) µs] -> Summary. The total of a row is the sum of its segments (a missing bt counts as 0);
    p95 uses the nearest-rank method on the sorted totals."""
    bt, phone, net, pc = zip(*rows, strict=True)
    totals = sorted((b or 0) + p + n + c for b, p, n, c in rows)
    n = len(totals)
    return Summary(avg=fmean(totals) / 1000, p95=totals[max(0, math.ceil(n * 0.95) - 1)] / 1000, max=totals[-1] / 1000,
                   bt=_mean([b for b in bt if b is not None]), phone=_mean(phone), net=_mean(net), pc=_mean(pc), n=n)


def summary(i, now_ns=None):
    """-> Summary of all changes from the last WINDOW_S or None (too few samples: the pad is idle, an older sender,
    no PC clock)."""
    start = (now_ns or time.perf_counter_ns()) - int(WINDOW_S * 1e9)
    with _lock:
        d = _samples.get(i)
        rows = [r[1:] for r in d if r[0] >= start] if d else []
    return _summarize(rows) if len(rows) >= MIN_SAMPLES else None


def buttons(i):
    """-> Summary of the last BUTTON_KEEP presses / releases or None (none yet)."""
    with _lock:
        rows = list(_buttons.get(i, ()))
    return _summarize(rows) if rows else None
