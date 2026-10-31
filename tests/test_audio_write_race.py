"""Receiver: Link.write (wątek audio HIDMaestro) czyta on_write jeden raz.
rx_speaker.detach() zeruje on_write z innego wątku; dwa odczyty (sprawdzenie i wywołanie) dawały rzadki TypeError na wątku audio.
    python3 tests/test_audio_write_race.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import rx_haptics as H

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

calls = []


class Racing(H.Link):
    """Link, którego on_write znika po pierwszym odczycie - jak przy detach() w środku write()."""
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
check(err is None, f"write nie rzuca, gdy on_write zniknie w trakcie ({err!r})")
check(calls == [64], f"wywołanie zwrotne dostało pozycję zapisu ({calls})")

plain = H.Link(0)
seen = []
plain.on_write = seen.append
plain.write(memoryview(bytes(32)), 32)
plain.on_write = None
plain.write(memoryview(bytes(32)), 32)
check(seen == [32] and plain.written == 64, f"zwykły on_write: wywołany, a po wyzerowaniu pomijany ({seen}, {plain.written})")

sys.exit(1 if fails else 0)
