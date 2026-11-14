"""Receiver: the pad self-test (rx_selftest, rx_selftest_report) - the numbers, the report, the packets and a whole run against a
simulated sender that answers like sender_selftest does (the old sender: no packet of the test may reach it).
    python3 tests/test_selftest.py receiver"""
import json, os, re, sys, threading, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa

import proto
from chk import check, fails, wait_for                          # noqa: E402  (shared helper, tests/chk.py)
from proto import (BODY, PK_DUALSENSE, RUMBLE_HIDRAW, SELFTEST_SENDER, SR_COUNT, SR_DONE, SR_FB, SR_HAPT, SR_SPK, SR_SYS, SR_WRITES,
                   SRC_PC, SRC_REPEAT, SRC_WATCHDOG, SRF_PINNED, SRF_RT, ST_BEGIN, ST_END, ST_HAPTICS, ST_RECORDS, ST_SNAP, ST_SPEAKER,
                   T_STCMD, T_STRES)
import rx_diag
import rx_haptics
import rx_latency
import rx_selftest as T
import rx_selftest_report as R
import rx_speaker
import rx_testmode

ADDR = ("192.0.2.1", 5005)
HID = RUMBLE_HIDRAW << 4


def stres_bytes(kind, flags, ref, *vals, i=0):
    return N.codec.pack(T_STRES, i, kind, flags, ref, *vals, *([0] * (6 - len(vals))))


def deliver(data, i=0):
    N._on_selftest_record(i, int.from_bytes(data[2:4], "big"), data, ADDR, time.monotonic())


def pad_ready(version=SELFTEST_SENDER):
    S.alive[0] = True
    S.sender_info[0] = (version, RUMBLE_HIDRAW, 0)
    S.pad_kind[0] = (PK_DUALSENSE, 0)
    S.pad_id[0] = "aa:bb:cc:dd:ee:01"
    S.info[0] = (12.0, 250.0, 250.0, time.monotonic())          # (rtt ms, pad Hz, motion Hz, time): fresh round-trip data
    rx_testmode.active = True
    S.set_blocked(0, True)


# ---------- the numbers ----------
vals = list(range(1, 101))
check([proto.nearest_rank(vals, p) for p in (0, 50, 95, 99, 100)] == [1, 50, 95, 99, 100], "nearest rank: p0 / p50 / p95 / p99 / p100 of 1..100")
check(proto.nearest_rank([7], 99) == 7, "nearest rank of a single value")
pc = rx_latency.pct_of([1000 * v for v in vals])
check((pc.n, pc.p50, pc.p95, pc.p99, pc.max) == (100, 50.0, 95.0, 99.0, 100.0), f"pct_of: microseconds in, milliseconds out ({pc})")
rows = [(2000, 1000, 3000, 800)] * 9 + [(None, 1000, 3000, 800)]
ws = rx_latency.window_stats(rows)
check(ws.n == 10 and ws.total.max == 6.8 and ws.bt.n == 9, f"window_stats: a missing bt counts 0 in the total, not in the bt series ({ws})")
check(rx_latency.window_stats([]) is None, "window_stats of nothing is None")
check(rx_latency.window_stats([(None, 1, 2, 3)]).bt is None, "no bt at all: the bt segment is None")
check(T.series_stats([]) == {"n": 0}, "an empty series is just n = 0")
check(T.series_stats([5, 1, 9])["p50"] == 5 and T.series_stats([5, 1, 9])["max"] == 9, "series_stats: sorted percentiles")
check(T.span_us(0xFFFFFF00, 0x100) == 0x200 and T.span_us(10, 4) == -6, "span_us: across the 32-bit wrap and negative")
check(T.pc_us(5_000_000) == 5000 and T.pc_us((1 << 32) * 1000 + 7000) == 7, "pc_us: ns -> the low 32 bits of microseconds")

# ---------- pairing our packets with the sender's records ----------
base = 1_000_000_000
taps = [T.Tap(0, 200, 1, base), T.Tap(0, 200, 2, base + 500_000_000), T.Tap(0, 0, 3, base + 600_000_000), T.Tap(0, 0, 4, base + 5_000_000_000)]
check(T.classify(taps) == ["start", "refresh", "stop", "idle"], f"classify: start / refresh / stop / idle ({T.classify(taps)})")
sp = T.pc_us(base)
recs = {1: [T.FbRec(SRC_PC, 2, 1, sp + 400, sp + 500, sp + 1500, 0, 200, 1000, 0)],
        3: [T.FbRec(SRC_PC, 2, 3, T.pc_us(base + 600_000_000) + 300, T.pc_us(base + 600_000_000) + 350, T.pc_us(base + 600_000_000) + 900,
                    0, 0, 550, 0)]}
sw = T.summarize_writes(taps, recs)
check(sw["start"]["hidraw"]["total"] == {"n": 1, "min": 1.5, "p50": 1.5, "p95": 1.5, "max": 1.5}, f"start write: send -> end of write {sw['start']}")
check(sw["start"]["hidraw"]["queue"]["p50"] == 0.1 and sw["start"]["hidraw"]["write"]["p50"] == 1.0, "start write: queue 0.1 ms, write 1.0 ms")
check(sw["stop"]["hidraw"]["total"]["p50"] == 0.9, "stop write: 0.9 ms")
check((sw["refresh"], sw["unmatched"], sw["failed"]) == (1, 0, 0), "a refresh is counted, not measured; every start / stop has its record")
sw2 = T.summarize_writes(taps, {1: [T.FbRec(SRC_PC, 2, 1, sp + 400, sp + 500, 0, 0, 200, 0, 5)]})
check((sw2["unmatched"], sw2["failed"]) == (1, 1), "a write that never finished is failed; a packet with no record is unmatched")

# ---------- the sender's records arrive (Inbox) ----------
run = T.SelfTest(0)
T.inbox.clear()
T.current = run
deliver(stres_bytes(SR_SYS, SRF_RT | SRF_PINNED, 7, 1, 2400000, 2800000, 35, 0x0123, 4 | 2 << 8))
check(not T.inbox.sys, "nothing is taken while the run is not collecting")
run.collecting = True
rec = stres_bytes(SR_SYS, SRF_RT | SRF_PINNED, 7, 1, 2400000, 2800000, 35, 0x0123, 4 | 2 << 8)
deliver(rec)
check(T.inbox.sys == [(SRF_RT | SRF_PINNED, 1, 2400000, 2800000, 35, 0x0123, 4 | 2 << 8)], f"SR_SYS keeps its flags ({T.inbox.sys})")
d = T.sys_dict(T.inbox.sys[0])
check((d["governor"], d["cpu_mhz"], d["cpu_max_mhz"], d["load1"], d["cpus"], d["fast_cpus"], d["realtime"], d["pm_qos"], d["pinned_to_fast_cores"])
      == ("performance", 2400, 2800, 0.35, 4, 2, True, False, True), f"sys_dict decodes the record ({d})")
deliver(stres_bytes(SR_FB, SRC_PC | HID, 42, 1, 2, 3, 200 << 8, 900, 0))
deliver(stres_bytes(SR_FB, SRC_PC | HID, 42, 1, 2, 3, 200 << 8, 900, 0))
deliver(stres_bytes(SR_FB, SRC_WATCHDOG | HID, 0, 10, 20, 30, 0, 100, 0))
deliver(stres_bytes(SR_FB, SRC_REPEAT | HID, 0, 11, 21, 31, 5, 100, 0))
check(len(T.inbox.fb[42]) == 1 and len(T.inbox.watchdog) == 1 and T.inbox.repeats == 1, "records: one per write (a repeated packet counts once), watchdog and repeat apart")
deliver(stres_bytes(SR_FB, SRC_PC | HID, 5, 1, 2, 3, 0, 1, 0), i=1)
check(5 not in T.inbox.fb, "a record for another pad is ignored")
deliver(stres_bytes(SR_COUNT, 0, 7, 400, 1, 900, 2500, 3))
deliver(stres_bytes(SR_WRITES, 0, 7, 120, 800, 1500, 2000, 4000, 2))
deliver(stres_bytes(SR_HAPT, 0, 7, 50, 49, 1, 2, 3, 4))
deliver(stres_bytes(SR_HAPT, 1, 7, 5, 6, 7, 8, 9, 400 << 16 | 123))
deliver(stres_bytes(SR_DONE, 0, 7, 3))
check(T.inbox.count == (400, 1, 900, 2500, 3, 0) and T.inbox.writes[0][0] == 120 and T.inbox.done == 3, "SR_COUNT, SR_WRITES and SR_DONE are kept")
sd = T.stream_dict(T.inbox.streams[(SR_HAPT, 0)], T.inbox.streams[(SR_HAPT, 1)])
check((sd["chunks"], sd["played"], sd["underruns"], sd["overruns"], sd["dropped"], sd["repeated"], sd["late"], sd["lost"], sd["stale"],
       sd["resyncs"], sd["unwritten"]) == (50, 49, 1, 2, 3, 4, 5, 6, 7, 8, 9) and sd["depth_ms"] == 12.3 and sd["buffer_ms"] == 40.0,
      f"stream_dict: the two parts become named counters, depth and buffer in ms ({sd})")
T.current = None
T.inbox.clear()

# ---------- the packets (a new sender and an old one) ----------
req = N.codec.pack(T_STCMD, 0, ST_SNAP, ST_HAPTICS, 513, 0, 0)
check(N.codec.parse(req) == (T_STCMD, 0, (req[2] << 8) | req[3]), "stcmd packs and parses (wire type 10, 10 bytes)")
check(BODY[T_STCMD].unpack_from(req, 4) == (ST_SNAP, ST_HAPTICS, 513, 0, 0), "stcmd body round trip")
ans = stres_bytes(SR_FB, SRC_PC | HID, 9, 1, 2, 3, 4, 5, 6)
check(N.codec.parse(ans)[0] == T_STRES and len(ans) == 4 + 28, "stres packs and parses (wire type 12, 28 bytes)")
saved = {k: proto.BY_SIZE.pop(k) for k in ((10, 10), (12, 28))}      # an older peer does not know these lengths
check(Codec().parse(req) is None and Codec().parse(ans) is None, "an older peer drops stcmd / stres (unknown length) and nothing else breaks")
check(Codec().parse(N.codec.pack(proto.T_CMD, 0, 1, 0)) is not None, "... while the old cmd of the same wire type still parses")
proto.BY_SIZE.update(saved)

# ---------- refusals ----------
S.alive[0] = False
check(T.SelfTest.refusal(0) == "pad", "no run for a pad that is not connected")
S.alive[0] = True
rx_testmode.active = False
check(T.SelfTest.refusal(0) == "block", "no run without the Test view (the output to the game must be blocked)")
pad_ready()
check(T.SelfTest.refusal(0) is None, "a connected pad in the Test view with the lock on may run")
S.set_blocked(0, False)
check(T.SelfTest.refusal(0) == "block", "no run with the lock off")
pad_ready()

# ---------- a whole run against a simulated new sender ----------
class Sim(threading.Thread):
    """A sender that knows the self-test: answers the requests in `sent` the way sender_selftest does."""

    def __init__(self):
        super().__init__(daemon=True)
        self.seen, self.ops, self.pos, self.halt = set(), [], 0, False

    def reply(self, kind, flags, ref, *vals):
        deliver(stres_bytes(kind, flags, ref, *vals))

    def run(self):
        while not self.halt:
            while self.pos < len(sent):
                t, _i, _sq, pkt = sent[self.pos]
                self.pos += 1
                if t == T_STCMD:
                    op, arg, ref, _a, _b = BODY[T_STCMD].unpack_from(pkt, 4)
                    if (op, arg, ref) not in self.seen:
                        self.seen.add((op, arg, ref))
                        self.ops.append((op, arg))
                        self.answer(op, arg, ref)
            time.sleep(0.003)

    def stream(self, kind, ref):
        self.reply(kind, 0, ref, 50, 50, 0, 0, 0, 0)
        self.reply(kind, 1, ref, 0, 0, 0, 0, 0, 400 << 16 | 123)

    def records(self, ref):
        taps = T.current._taps_copy()
        names = T.classify(taps)
        for k, tap in enumerate(taps):
            recv = T.pc_us(tap.send_ns) + 500
            self.reply(SR_FB, SRC_PC | HID, tap.seq, recv, recv + 100, recv + 1000, tap.small | tap.large << 8, 900, 0)
            if names[k] == "start" and k + 1 < len(taps) and taps[k + 1].send_ns - tap.send_ns > 1_200_000_000:
                self.reply(SR_FB, SRC_WATCHDOG | HID, 0, recv, recv + 1_500_000, recv + 1_501_000, 0, 1000, 0)
        self.reply(SR_DONE, 0, ref, len(taps))

    def answer(self, op, arg, ref):
        if op == ST_BEGIN:
            self.reply(SR_SYS, SRF_RT, ref, 1, 2400000, 2800000, 35, 0x0123, 4 | 2 << 8)
        elif op == ST_SNAP and arg == ST_RECORDS:
            self.records(ref)
        elif op == ST_SNAP:
            self.stream(SR_HAPT if arg == ST_HAPTICS else SR_SPK, ref)
        elif op == ST_END:
            self.reply(SR_SYS, 0, ref, 2, 1800000, 2800000, 80, 0x0123, 4 | 2 << 8)
            self.reply(SR_COUNT, 0, ref, 400, 0, 900, 2500, 0)
            self.reply(SR_WRITES, 0, ref, 120, 800, 1500, 2000, 4000, 0)
            self.stream(SR_HAPT, ref)
            self.stream(SR_SPK, ref)
            self.records(ref)


class StubLink:
    """A haptics / speaker link that only counts what the test stream puts into it."""

    def __init__(self):
        self.sent = self.dropped = self.overruns = 0
        self.injected = []

    def inject(self, units, tail=()):
        self.injected = list(units)
        self.sent += len(self.injected)


class Feeder(threading.Thread):
    """Latency samples as the network thread would add them (constant, so the percentiles are known)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.halt = False

    def run(self):
        k = 0
        while not self.halt:
            t = time.perf_counter_ns()
            rx_latency.add(0, (t - 800_000, 1000, 2000, 3000), t)
            if k % 5 == 0:
                rx_latency.add_button(0, (t - 800_000, 1000, 2000, 3000), t)
            k += 1
            time.sleep(0.02)


PLAN = T.Plan(window_s=1, idle_s=0.6, pulse_s=0.1, gap_s=0.15, levels=(100, 200), ack_s=1.0, result_s=1.0, stream_tail_s=0.1, tone_s=0.15,
              sweep_s=0.2)
hap, spk = StubLink(), StubLink()
rx_haptics.unavailable = lambda i: None
rx_speaker.unavailable = lambda i: None
rx_haptics.link = lambda i: hap
rx_speaker.speaker = lambda i: spk
S.stop.clear()
fb_thread = threading.Thread(target=F.feedback_thread, args=(N.send_feedback,), daemon=True)
fb_thread.start()
pad_ready()
rx_latency.clear(0)
rx_latency.reset_buttons(0)
sent.clear()
sim, feeder = Sim(), Feeder()
sim.start()
feeder.start()
run = T.SelfTest(0, PLAN)
run.start()
seen_steps, out_of_range = set(), []
while not run.finished:
    step, idx, frac, samples = run.snapshot()
    seen_steps.add(step)
    if not (0.0 <= frac <= 1.0 and 0 <= idx < len(T.STEPS)):
        out_of_range.append((step, idx, frac))
    time.sleep(0.05)
check(not out_of_range, f"snapshot() stays in range while the run goes ({out_of_range})")
run.join(5)
feeder.halt = sim.halt = True
res = {r.id: r for r in run.results}
check([r.id for r in run.results] == list(T.STEPS), f"every step ran, in order ({[r.id for r in run.results]})")
check({"latency", "vibration"} <= seen_steps, f"the window sees the long steps go by ({seen_steps})")
check(res["meta"].status == T.OK and res["meta"].data["sender_version"] == SELFTEST_SENDER and res["meta"].data["rumble_ways"] == ["hidraw"],
      f"meta: versions and rumble ways ({res['meta'].data})")
check(res["meta"].data["pad_mac"] == "aa:bb:cc:xx:xx:xx" and res["meta"].data["pad_firmware"] == "0x0123", "meta: the pad MAC is masked, the firmware comes from the sender")
check(res["meta"].data["pad_family"] == "DualSense" and "backend" in res["meta"].data, "meta: the pad family and the virtual pad backend")
idle = res["idle"].data
check(res["idle"].status == T.OK and idle["series"]["rtt_ms"]["n"] > 0 and idle["series"]["rtt_ms"]["p50"] == 12.0, f"idle: round trip series with percentiles ({idle['series']['rtt_ms']})")
lat = res["latency"].data
check(res["latency"].status == T.OK and lat["samples"] >= 10 and lat["window_s"] == 1, f"latency: samples of the window ({lat['samples']})")
check(lat["all_changes"]["total"]["p50"] == 6.8 and lat["all_changes"]["total"]["p99"] == 6.8 and lat["all_changes"]["pc"]["p50"] == 0.8,
      f"latency: the percentiles of the constant samples ({lat['all_changes']['total']})")
check(lat["button_samples"] >= 1 and lat["buttons"]["n"] == lat["button_samples"], "latency: the button samples are a series of their own")
vib = res["vibration"].data
check(res["vibration"].status == T.OK, f"vibration step ok ({res['vibration'].status} {res['vibration'].note})")
p = vib["pulses"]
check(p["start"]["hidraw"]["total"]["n"] == 5 and p["stop"]["hidraw"]["total"]["n"] == 5 and p["unmatched"] == 0,
      f"vibration: 5 pulses -> 5 start and 5 stop writes, all with a record ({p['start']['hidraw']['total']}, unmatched {p['unmatched']})")
check(p["start"]["hidraw"]["total"]["p50"] == 1.5 and p["start"]["hidraw"]["queue"]["p50"] == 0.1, "vibration: send -> end of the write 1.5 ms, queue 0.1 ms")
check(p["pulse_order_to_send_ms"]["n"] == 5, "vibration: the PC's own reaction time of every pulse")
check(vib["watchdog"]["expected_ms"] == 1500.0 and vib["watchdog"]["stop_after_last_command_ms"]["p50"] == 1501.0, f"vibration: the watchdog case ({vib['watchdog']})")
check(900 <= vib["game_stale"]["stopped_after_ms"] <= 1700 and vib["game_stale"]["expected_ms"] == 1000.0, f"vibration: game stopped sending -> motors off after ~STALE ({vib['game_stale']})")
check(150 <= vib["game_gap"]["stopped_after_ms"] <= 900 and vib["game_gap"]["expected_ms"] == 250.0, f"vibration: game sends no motors -> off after ~GAP ({vib['game_gap']})")
au = res["audio"].data
check(res["audio"].status == T.OK and au["haptics"]["receiver"]["sent"] == len(hap.injected) > 0 and au["speaker"]["receiver"]["sent"] == len(spk.injected) > 0,
      "audio: both streams injected and counted on the receiver side")
check(au["haptics"]["sender"]["chunks"] == 50 and au["haptics"]["sender"]["depth_ms"] == 12.3 and au["haptics"]["sent_minus_received"] == len(hap.injected) - 50,
      f"audio: the sender's counters next to ours ({au['haptics']['sender']})")
check("not the sound" in au["note"], "audio: the report says it measures the path, not the sound")
cpu = res["cpu"].data
check(res["cpu"].status == T.OK and cpu["at_start"]["governor"] == "performance" and cpu["at_end"]["load1"] == 0.8 and cpu["at_start"]["realtime"], f"cpu: start and end records ({cpu['at_start']})")
check(cpu["hidraw_writes_in_test"]["reports"]["n"] == 120 and cpu["hidraw_since_connect"]["writes"] == 400, "cpu: hidraw write numbers (this test, since connect)")
check(res["mic"].status == T.SKIPPED and res["mic"].data == {"supported": False}, "mic: only the unsupported flag, no measurement")
ops = sim.ops
check(ops[0] == (ST_BEGIN, 0) and ops.count((ST_END, 0)) == 1 and ops[-1] == (ST_END, 0), f"the sender is asked BEGIN ... END once, END last ({ops})")
check((ST_SNAP, ST_HAPTICS) in ops and (ST_SNAP, ST_SPEAKER) in ops and (ST_SNAP, ST_RECORDS) in ops, "SNAP asked for the records and for both streams")
check(N.fb_tap is None and not rx_testmode.latency_probe and F.hold_until[0] == 0.0 and T.current is None and not run.collecting, "cleanup: tap, probe, hold and the run are given back")
check(run.error == "" and run.finished, "a run that ended by itself has no error")

# ---------- the report ----------
data = R.build(run)
text = R.render(data)
check(text.startswith("# Pad self-test report (version 1)"), "the report starts with its title and version")
check(data["schema"] == "dualsense-bridge/selftest/1" and data["report_version"] == 1 and data["result"] == "complete", "schema, version, result")
block = re.search(r"```json\n(.*)\n```", text, re.S)
check(block is not None and json.loads(block.group(1)) == json.loads(json.dumps(data)), "the JSON block holds all the data")
check("END OF THE WRITE" in text and "not the sound" in text and "not supported" in text and "half the round trip" in text, "the limits are written out")
check("| segment | n | p50 | p95 | p99 | max |" in text and "window 1 s" in text, "latency table: window, sample count and percentiles")
check(set(data["steps"]) == set(T.STEPS) and data["steps"]["mic"]["status"] == "skipped", "every step has its status in the report")
check("aa:bb:cc:dd:ee:01" not in text, "the full pad MAC is not in the report")
when = datetime(2026, 11, 14, 10, 20, 30)
path = R.save(text, when)
check(os.path.basename(path) == "selftest-2026-11-14-102030.md" and os.path.dirname(path) == os.path.join(TMP, "reports"), f"the file goes to reports/ next to settings.json ({path})")
check(open(path, encoding="utf-8").read() == text, "the file holds exactly the text that is copied")

# ---------- stopping a run ----------
pad_ready()
sent.clear()
sim, feeder = Sim(), Feeder()
sim.start()
feeder.start()
run = T.SelfTest(0, PLAN)
run.start()
check(wait_for(lambda: run.snapshot()[0] == "latency", 5), "the run reaches the latency step")
run.abort()
run.join(3)
feeder.halt = sim.halt = True
check(run.finished and run.results[-1].id == "latency" and run.results[-1].status == T.ABORTED, "aborted in the middle of a step: that step is marked")
asked = [BODY[T_STCMD].unpack_from(p[3], 4)[0] for p in sent if p[0] == T_STCMD]
check(asked[-1] == ST_END and N.fb_tap is None and not rx_testmode.latency_probe and T.current is None, "an abort still ends the sender's measurement and cleans up")
ad = R.build(run)
check(ad["result"] == "aborted" and "latency" in ad["steps"] and "vibration" not in ad["steps"], "the report of an aborted run has what was measured so far")
check(json.loads(re.search(r"```json\n(.*)\n```", R.render(ad), re.S).group(1))["result"] == "aborted", "... and says so")

# ---------- an old sender: nothing of the test is sent to it ----------
pad_ready(version=SELFTEST_SENDER - 1)
sent.clear()
run = T.SelfTest(0, PLAN)
run.start()
run.join(30)
res = {r.id: r for r in run.results}
check(run.finished and [r.id for r in run.results] == list(T.STEPS), "a run against an old sender goes through every step")
check([p for p in sent if p[0] == T_STCMD] == [], "no stcmd packet reaches a sender older than the self-test")
check(all(res[s].status == T.SKIPPED for s in ("vibration", "audio", "cpu", "mic")), "the steps that need the sender are skipped")
check("older than the self-test" in res["meta"].note and res["meta"].data["sender_version"] == SELFTEST_SENDER - 1, f"meta says why ({res['meta'].note})")
check(res["idle"].status == T.OK and res["latency"].status == T.NO_DATA, "the receiver-only steps still run (idle: measured, latency: nobody pressed anything)")
check(all(p[3][4] == 0 and p[3][5] == 0 for p in sent if p[0] == proto.T_FB), "no motor command in a run against an old sender")
text = R.render(R.build(run))
check("older than the self-test" in text and "skipped" in text, "the report of that run says what was skipped and why")

S.stop.set(); F.fb_event.set(); fb_thread.join(2)
sys.exit(1 if fails else 0)
