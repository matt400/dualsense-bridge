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
check(rx_hm.parse_leds(0x02, data) is None, "bez flag ważności żadna dioda się nie zmienia")
data[1], data[8], data[43] = 0x01, 2, 0x15
check(rx_hm.parse_leds(0x02, data) == (2, None), "flaga 0x01 = dioda mute (bajt 8), diody gracza bez flagi = None")
data[1] = 0x10
check(rx_hm.parse_leds(0x02, data) == (None, 0x15), "flaga 0x10 = diody gracza (bajt 43)")
data[1] = 0x11 | 0x04
check(rx_hm.parse_leds(0x02, data) == (2, 0x15), "obie flagi (razem z kolorem lightbara)")
check(rx_hm.parse_leds(0x05, data) is None and rx_hm.parse_leds(0x02, data[:46]) is None, "inny raport / za krótki: ignorowany")
check(rx_hm.out_used(0, 0x01) == (8,) and rx_hm.out_used(0, 0x10) == (43,) and rx_hm.out_used(0, 0x11) == (8, 43), "out_used czyta tylko bajty z flagą")
check(rx_hm.out_used(0, 0x04) == (44, 45, 46) and rx_hm.out_used(0, 0) == (), "bajty koloru jak dotąd, bez flag nic")
check(rx_hm.parse_output(0x02, bytearray([0, 0x11]) + bytearray(45)) == (None, None, None), "diody nie udają silników, koloru ani spustów")

# ---------- F.game: LEDs are stored, merged, wake the thread only on a change ----------
F.reset(0)
check(F.fb[0]["leds"] == F.NO_LEDS and F.leds_target(0) is None, "start: gra nic nie ustawiła")
F.fb_event.clear()
F.game(0, leds=(MUTE_ON, None))
check(F.fb_event.is_set() and F.fb[0]["leds"] == (MUTE_ON, None), "dioda mute od gry budzi wątek")
check(F.leds_target(0) == (LEDS_MUTE, MUTE_ON, 0), "pakiet: tylko flaga mute")
F.fb_event.clear()
F.game(0, leds=(MUTE_ON, None))
check(not F.fb_event.is_set(), "ten sam raport (gry powtarzają je co klatkę) nie budzi wątku")
F.game(0, leds=(None, 0x0A))
check(F.fb[0]["leds"] == (MUTE_ON, 0x0A) and F.leds_target(0) == (LEDS_MUTE | LEDS_PLAYER, MUTE_ON, 0x0A), "None zostawia drugą diodę")
check(F.fb_event.is_set(), "diody gracza budzą wątek")
F.game(0, large=10, small=10)
check(F.fb[0]["leds"] == (MUTE_ON, 0x0A), "raport z silnikami bez diod ich nie kasuje")

# ---------- leds_tick: version gate, repeats, refresh, reset ----------
sent = []
send = lambda i, vals, repeat: sent.append((i, tuple(vals), repeat))
S.sender_info[0] = (LEDS_SENDER - 1, 0xFF, 0)
F.leds_sent[0] = None
F.leds_tick(0, 100.0, send)
check(not sent and F.leds_sent[0] is None, "nadajnik v14 (bez pakietu leds): nic nie jest wysyłane")
S.sender_info[0] = None
F.leds_tick(0, 100.0, send)
check(not sent, "brak informacji o nadajniku: nic")
S.sender_info[0] = (LEDS_SENDER, 3, 0)
F.leds_tick(0, 100.0, send)
check(sent == [(0, (LEDS_MUTE | LEDS_PLAYER, MUTE_ON, 0x0A), 3)], f"v15: zmiana idzie od razu, TRIG_REPEAT razy: {sent}")
F.leds_tick(0, 101.0, send)
check(len(sent) == 1, "bez zmiany: nic przed odświeżeniem")
F.leds_tick(0, 100.0 + F.IDLE_REFRESH_S + 0.1, send)
check(len(sent) == 2 and sent[-1][2] == 1, "odświeżenie co IDLE_REFRESH_S, raz (zgubiony pakiet, nowe połączenie pada)")
F.game(0, leds=(MUTE_PULSE, None))
F.leds_tick(0, 111.0, send)
check(sent[-1] == (0, (LEDS_MUTE | LEDS_PLAYER, MUTE_PULSE, 0x0A), 3), "nowa wartość: znowu od razu")
F.leds_sent[0] = None
F.leds_tick(0, 112.0, send)
check(len(sent) == 4, "po zerowaniu leds_sent (pad się rozłączył) wartości idą od razu")
F.reset(0)
n = len(sent)
F.leds_tick(0, 120.0, send)
check(len(sent) == n and F.fb[0]["leds"] == F.NO_LEDS, "reset (nowy wirtualny pad): gra nic nie ustawiła, nic nie idzie")

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
check(got and got[0] == (0, (LEDS_MUTE | LEDS_PLAYER, MUTE_ON, 0x04), 3), f"wątek feedbacku wysyła diody: {got[:1]}")
S.alive[0] = False
F.fb_event.set()
time.sleep(0.2)
check(F.leds_sent[0] is None, "pad bez sygnału: wysłane wartości zapomniane (po powrocie pad dostanie diody od razu)")
S.alive[0] = True
S.sender_info[0] = (LEDS_SENDER, 3, 0)
S.stop.set(); F.fb_event.set(); th.join(2); S.stop.clear()

# ---------- the packet from rx_net.send_leds ----------
pkts = []
N.send_repeat = lambda pkt, n: pkts.append((bytes(pkt), n))
N.send_leds(1, (LEDS_MUTE, MUTE_ON, 0), 3)
check(pkts and pkts[0][1] == 3 and pkts[0][0][0] & 0x0F == T_FB and len(pkts[0][0]) == 4 + 3 + (8 if N.codec.key else 0)
      and pkts[0][0][1] == 1 and pkts[0][0][4:7] == bytes([LEDS_MUTE, MUTE_ON, 0]), f"pakiet leds: typ fb, długość 3, idx 1: {pkts[:1]}")
check(Codec().parse(pkts[0][0])[0] == T_LEDS if not N.codec.key else True, "rozpoznany jako T_LEDS")

# ---------- the mute button: state packet -> s["mute"] -> bit 2 of buttons[2] in the DualSense report ----------
state(0)
check(S.states[0]["mute"] is False, "start: mute puszczony")
state(0, mask=MUTE_BIT)
check(S.states[0]["mute"] is True and S.states[0]["mask"] == 0 and S.states[0]["raw"][0] == 0,
      "bit mute z nadajnika: s['mute'] = True, a maska przycisków (remap, skróty, master) go nie widzi")
rep = rx_report.build_ds5_report(S.output(0), 0, False)
check(rep[9] & 0x04 == 0x04 and rep[9] & 0x03 == 0, f"raport DualSense: buttons[2] bit 2 = mute ({rep[9]:#x})")
state(0, mask=BIT[BUTTON_CODES["cross"]] | MUTE_BIT)
rep = rx_report.build_ds5_report(S.output(0), 0, False)
check(rep[9] == 0x04 and rep[7] & 0xF0, f"mute razem z przyciskiem: bit mute w buttons[2], przycisk w buttons[0] ({rep[7]:#x} {rep[9]:#x})")
state(0)
check(S.states[0]["mute"] is False and rx_report.build_ds5_report(S.output(0), 0, False)[9] & 0x04 == 0, "puszczony mute znika z raportu")
check(rx_report.build_ds5_report(S.NEUTRAL_OUT, 0, False)[9] == 0, "neutralny raport (widok Test, nowy pad) bez mute")
state(0, mask=MUTE_BIT)
with_mute = bytes(rx_report.build_report(S.output(0), 5, False))
state(0)
check(with_mute[:7] == bytes(rx_report.build_report(S.output(0), 5, False))[:7], "DS4 (ViGEm) nie ma przycisku mute: sticki i przyciski bez zmian")
state(0, mask=0x7FF)
check(S.states[0]["mask"] != 0 and S.states[0]["mute"] is False, "wszystkie zwykłe przyciski bez mute: mute to osobny bit nad nimi")

sys.exit(1 if fails else 0)
