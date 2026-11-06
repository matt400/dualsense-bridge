"""Receiver: Link.write (the HIDMaestro audio thread) reads on_write once.
rx_speaker.detach() clears on_write from another thread; two reads (the check and the call) gave a rare TypeError on the audio thread.
    python3 tests/test_audio_write_race.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, store, sent)
import rx_haptics as H

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

calls = []


class Racing(H.Link):
    """A Link whose on_write disappears after the first read - as with detach() in the middle of write()."""
    reads = 0

    @property
    def on_write(self):
        self.reads += 1
        return calls.append if self.reads == 1 else None

    @on_write.setter
    def on_write(self, value):
        pass


lk = Racing(0)
try:
    lk.write(memoryview(bytes(64)), 64)
    err = None
except TypeError as e:
    err = e
check(err is None, f"write does not raise when on_write disappears midway ({err!r})")
check(calls == [64], f"the callback got the write position ({calls})")

plain = H.Link(0)
seen = []
plain.on_write = seen.append
plain.write(memoryview(bytes(32)), 32)
plain.on_write = None
plain.write(memoryview(bytes(32)), 32)
check(seen == [32] and plain.written == 64, f"plain on_write: called, and skipped once cleared ({seen}, {plain.written})")

sys.exit(1 if fails else 0)
