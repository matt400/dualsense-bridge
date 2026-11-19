"""Receiver: a plugged virtual pad is published before its flag - whoever sees S.plugged[i] also finds rx_ds4.outs[i].

_publish used to raise S.plugged first and assign outs a few lines later; a thread reading in between (the window, a test
waiting for "both plugged") saw a plugged slot without a pad (test_vpad failed now and then on that).
    python3 tests/test_vpad_publish_order.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_ds4
import rx_state as S
import vgamepad

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

seen = []


class Watched(list):
    """S.plugged that records, at the moment a slot is flagged, whether its Out is already there."""

    def __setitem__(self, i, value):
        if value:
            seen.append(rx_ds4.outs[i] is not None and rx_ds4.outs[i].pad is pad)
        super().__setitem__(i, value)


rx_ds4.init()
S.plugged = Watched(S.plugged)
pad = vgamepad.VDS4Gamepad()
rx_ds4._publish(0, pad, "vigem")
check(seen == [True], f"the Out is in place when the slot is flagged as plugged: {seen}")
check(S.plugged[0] and rx_ds4.outs[0].pad is pad and rx_ds4.kinds[0] == "vigem", "slot published")
rx_ds4._release_slot(0)
check(not S.plugged[0] and rx_ds4.outs[0] is None, "release: flag down and the Out gone")
sys.exit(1 if fails else 0)
