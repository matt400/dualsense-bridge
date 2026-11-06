"""Receiver: the mute button and the LEDs - the game's mute LED / player LEDs from the output report (flags, bytes), what the feedback
thread sends to the sender (gating by version, repeats, refresh), and the mute button from the state packet to the DualSense report.
    python3 tests/test_leds.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_hm
import rx_report
from rx_buttons import BUTTON_CODES
from proto import LEDS_MUTE, LEDS_PLAYER, LEDS_SENDER, MUTE_BIT, MUTE_ON, MUTE_PULSE, T_FB, T_LEDS, Codec

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

# ---------- parse_leds / out_used: the bytes of hid-playstation's struct dualsense_output_report_common ----------
data = bytearray(47)
check(rx_hm.parse_leds(0x02, data) is None, "without validity flags no LED changes")
data[1], data[8], data[43] = 0x01, 2, 0x15
check(rx_hm.parse_leds(0x02, data) == (2, None), "flag 0x01 = mute LED (byte 8), player LEDs without a flag = None")
data[1] = 0x10
check(rx_hm.parse_leds(0x02, data) == (None, 0x15), "flaga 0x10 = diody gracza (bajt 43)")
data[1] = 0x11 | 0x04
check(rx_hm.parse_leds(0x02, data) == (2, 0x15), "both flags (together with the lightbar colour)")
check(rx_hm.parse_leds(0x05, data) is None and rx_hm.parse_leds(0x02, data[:46]) is None, "another report / too short: ignored")
check(rx_hm.out_used(0, 0x01) == (8,) and rx_hm.out_used(0, 0x10) == (43,) and rx_hm.out_used(0, 0x11) == (8, 43), "out_used reads only the flagged bytes")
check(rx_hm.out_used(0, 0x04) == (44, 45, 46) and rx_hm.out_used(0, 0) == (), "colour bytes as before, nothing without flags")
check(rx_hm.parse_output(0x02, bytearray([0, 0x11]) + bytearray(45)) == (None, None, None), "the LEDs do not pose as motors, colour or triggers")

# ---------- F.game: LEDs are stored, merged, wake the thread only on a change ----------
F.reset(0)
check(F.fb[0]["leds"] == F.NO_LEDS and F.leds_target(0) is None, "start: the game set nothing")
F.fb_event.clear()
F.game(0, leds=(MUTE_ON, None))
check(F.fb_event.is_set() and F.fb[0]["leds"] == (MUTE_ON, None), "the mute LED from the game wakes the thread")
check(F.leds_target(0) == (LEDS_MUTE, MUTE_ON, 0), "packet: only the mute flag")
F.fb_event.clear()
F.game(0, leds=(MUTE_ON, None))
check(not F.fb_event.is_set(), "the same report (games repeat them every frame) does not wake the thread")
F.game(0, leds=(None, 0x0A))
check(F.fb[0]["leds"] == (MUTE_ON, 0x0A) and F.leds_target(0) == (LEDS_MUTE | LEDS_PLAYER, MUTE_ON, 0x0A), "None leaves the other LED")
check(F.fb_event.is_set(), "player LEDs wake the thread")
F.game(0, large=10, small=10)
check(F.fb[0]["leds"] == (MUTE_ON, 0x0A), "a report with motors and no LEDs does not clear them")

# ---------- leds_tick: version gate, repeats, refresh, reset ----------
sent = []
send = lambda i, vals, repeat: sent.append((i, tuple(vals), repeat))
S.sender_info[0] = (LEDS_SENDER - 1, 0xFF, 0)
F.leds_sent[0] = None
F.leds_tick(0, 100.0, send)
check(not sent and F.leds_sent[0] is None, "v14 sender (no leds packet): nothing is sent")
S.sender_info[0] = None
F.leds_tick(0, 100.0, send)
check(not sent, "no sender info: nothing")
S.sender_info[0] = (LEDS_SENDER, 3, 0)
F.leds_tick(0, 100.0, send)
check(sent == [(0, (LEDS_MUTE | LEDS_PLAYER, MUTE_ON, 0x0A), 3)], f"v15: a change goes at once, TRIG_REPEAT times: {sent}")
F.leds_tick(0, 101.0, send)
check(len(sent) == 1, "no change: nothing before the refresh")
F.leds_tick(0, 100.0 + F.IDLE_REFRESH_S + 0.1, send)
check(len(sent) == 2 and sent[-1][2] == 1, "refresh every IDLE_REFRESH_S, once (lost packet, new pad connection)")
F.game(0, leds=(MUTE_PULSE, None))
F.leds_tick(0, 111.0, send)
check(sent[-1] == (0, (LEDS_MUTE | LEDS_PLAYER, MUTE_PULSE, 0x0A), 3), "a new value: at once again")
F.leds_sent[0] = None
F.leds_tick(0, 112.0, send)
check(len(sent) == 4, "after leds_sent is cleared (the pad disconnected) the values go out at once")
F.reset(0)
n = len(sent)
F.leds_tick(0, 120.0, send)
check(len(sent) == n and F.fb[0]["leds"] == F.NO_LEDS, "reset (new virtual pad): the game set nothing, nothing goes out")

# ---------- the thread: LEDs go out through the send_leds callback, a dead pad forgets what was sent ----------
import threading
F.reset(0)
S.alive[0] = True
got = []
th = threading.Thread(target=F.feedback_thread, args=(lambda i, v: None, None, lambda i, v, r: got.append((i, tuple(v), r))), daemon=True)
th.start()
F.game(0, leds=(MUTE_ON, 0x04))
deadline = time.monotonic() + 3
while not got and time.monotonic() < deadline:
    time.sleep(0.02)
check(got and got[0] == (0, (LEDS_MUTE | LEDS_PLAYER, MUTE_ON, 0x04), 3), f"the feedback thread sends the LEDs: {got[:1]}")
S.alive[0] = False
F.fb_event.set()
time.sleep(0.2)
check(F.leds_sent[0] is None, "pad without a signal: sent values forgotten (when it comes back the pad gets the LEDs at once)")
S.alive[0] = True
S.sender_info[0] = (LEDS_SENDER, 3, 0)
S.stop.set(); F.fb_event.set(); th.join(2); S.stop.clear()

# ---------- the packet from rx_net.send_leds ----------
pkts = []
N.send_repeat = lambda pkt, n: pkts.append((bytes(pkt), n))
N.send_leds(1, (LEDS_MUTE, MUTE_ON, 0), 3)
check(pkts and pkts[0][1] == 3 and pkts[0][0][0] & 0x0F == T_FB and len(pkts[0][0]) == 4 + 3 + (8 if N.codec.key else 0)
      and pkts[0][0][1] == 1 and pkts[0][0][4:7] == bytes([LEDS_MUTE, MUTE_ON, 0]), f"leds packet: fb type, length 3, idx 1: {pkts[:1]}")
check(Codec().parse(pkts[0][0])[0] == T_LEDS if not N.codec.key else True, "recognized as T_LEDS")

# ---------- the mute button: state packet -> s["mute"] -> bit 2 of buttons[2] in the DualSense report ----------
state(0)
check(S.states[0]["mute"] is False, "start: mute puszczony")
state(0, mask=MUTE_BIT)
check(S.states[0]["mute"] is True and S.states[0]["mask"] == 0 and S.states[0]["raw"][0] == 0,
      "mute bit from the sender: s['mute'] = True, and the button mask (remap, shortcuts, master) does not see it")
rep = rx_report.build_ds5_report(S.output(0), 0, False)
check(rep[9] & 0x04 == 0x04 and rep[9] & 0x03 == 0, f"raport DualSense: buttons[2] bit 2 = mute ({rep[9]:#x})")
state(0, mask=BIT[BUTTON_CODES["cross"]] | MUTE_BIT)
rep = rx_report.build_ds5_report(S.output(0), 0, False)
check(rep[9] == 0x04 and rep[7] & 0xF0, f"mute together with a button: the mute bit in buttons[2], the button in buttons[0] ({rep[7]:#x} {rep[9]:#x})")
state(0)
check(S.states[0]["mute"] is False and rx_report.build_ds5_report(S.output(0), 0, False)[9] & 0x04 == 0, "puszczony mute znika z raportu")
check(rx_report.build_ds5_report(S.NEUTRAL_OUT, 0, False)[9] == 0, "neutral report (Test view, new pad) without mute")
state(0, mask=MUTE_BIT)
with_mute = bytes(rx_report.build_report(S.output(0), 5, False))
state(0)
check(with_mute[:7] == bytes(rx_report.build_report(S.output(0), 5, False))[:7], "DS4 (ViGEm) has no mute button: sticks and buttons unchanged")
state(0, mask=0x7FF)
check(S.states[0]["mask"] != 0 and S.states[0]["mute"] is False, "all plain buttons without mute: mute is a separate bit above them")

sys.exit(1 if fails else 0)
