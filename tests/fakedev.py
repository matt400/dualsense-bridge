"""Fake devices for the sender tests: scripted evdev devices (a list of (delay, event)) and a hidraw node as a file.

    from fakedev import Dev, Ev, SYN, frame
    dev = Dev(frame(0, (ec.ABS_MT_SLOT, 0), (ec.ABS_MT_POSITION_X, 900)) + frame(0.05, ...))

An Exception in the list is raised by the read loop (a device that vanishes).
"""
import asyncio
import itertools
import os
import tempfile
import zlib

from evdev import ecodes as ec


class Ev:
    """One input event."""

    def __init__(self, t, c, v):
        self.type, self.code, self.value = t, c, v


SYN = Ev(ec.EV_SYN, ec.SYN_REPORT, 0)
DROP = Ev(ec.EV_SYN, ec.SYN_DROPPED, 0)


class AbsInfo:
    """What InputDevice.absinfo() returns: the current value and the maximum."""

    def __init__(self, value=0, mx=1919):
        self.value, self.max = value, mx


class Dev:
    """An evdev device that plays `evs` = [(delay_s, Ev | Exception)]; `absv` = {code: current value}, `keys` = held keys."""

    path = "/dev/input/event7"
    name = "DualSense Wireless Controller"

    def __init__(self, evs, absv=None, keys=()):
        self.evs, self.absv, self.keys, self.closed = evs, absv or {}, list(keys), False

    def absinfo(self, c):
        return AbsInfo(self.absv.get(c, 0), 1 if c == ec.ABS_MT_SLOT else 1919)

    def active_keys(self):
        return self.keys

    def grab(self):
        pass

    def ungrab(self):
        pass

    def close(self):
        self.closed = True

    async def async_read_loop(self):
        for dt, e in self.evs:
            if dt:
                await asyncio.sleep(dt)
            if isinstance(e, Exception):
                raise e
            yield e


def frame(dt, *pairs):
    """One report: ABS events (code, value) after `dt` seconds, then SYN_REPORT."""
    return [(dt if k == 0 else 0, Ev(ec.EV_ABS, c, v)) for k, (c, v) in enumerate(pairs)] + [(0, SYN)]


# ---------- a hidraw node as a plain file: the sender's Rumble writes its output reports into it ----------
REPORT_SIZES = {0x31: 78, 0x32: 142, 0x36: 398}     # Bluetooth output reports by id
_TMP = tempfile.mkdtemp()
_files = itertools.count()


def new_hidraw():
    """An empty file to open as the pad's hidraw."""
    path = os.path.join(_TMP, f"hidraw{next(_files)}")
    open(path, "wb").close()
    return path


def reports(path):
    """The output reports written to a fake hidraw file, split by their id."""
    data = open(path, "rb").read()
    out, k = [], 0
    while k < len(data):
        n = REPORT_SIZES[data[k]]
        out.append(data[k:k + n])
        k += n
    return out


def common(rep):
    """The common block of a 0x31 report (the part shared with USB)."""
    return rep[3:50]


def crc_ok(rep):
    """The report ends with the Bluetooth CRC32 (seed 0xA2), little endian."""
    return int.from_bytes(rep[-4:], "little") == zlib.crc32(b"\xa2" + rep[:-4]) & 0xFFFFFFFF
