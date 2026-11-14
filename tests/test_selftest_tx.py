"""Sender: the pad self-test (sender_selftest) - the system record from a fake sysfs, rumble write records (PC command, repeat, watchdog) with
the times taken on the writer thread, the results sent on request, the cost when nothing is armed, and the request packet from sender_net.
    python3 tests/test_selftest_tx.py sender"""
import asyncio, os, sys, tempfile, time
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_net as net
import sender_rt
import sender_rumble as R
import sender_selftest as ST
from proto import (BODY, GOVERNORS, SELFTEST_SENDER, SENDER_VERSION, SR_COUNT, SR_DONE, SR_FB, SR_HAPT, SR_SPK, SR_SYS, SR_WRITES, SRC_PC,
                   SRC_REPEAT, SRC_WATCHDOG, SRF_RT, ST_BEGIN, ST_END, ST_HAPTICS, ST_MAX_S, ST_RECORDS, ST_SNAP, ST_SPEAKER, T_FB, T_STCMD, T_STRES)

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

OFFSET_US = 5_000_000                       # the PC clock is this far ahead of the sender's
out = []                                    # (kind, flags, ref, values): what the sender emitted as stres
NOW = [0]


def emit(ptype, idx, *values):
    assert ptype == T_STRES, ptype
    kind, flags, ref, *vals = values
    out.append((kind, flags, ref, tuple(vals)))


def take(kind):
    return [r for r in out if r[0] == kind]


class Dev:
    def capabilities(self):
        return {}


class Stream:
    """A haptics / speaker stream as the sender keeps one: counters, a ring with a depth, a target."""

    def __init__(self):
        self.stats = SimpleNamespace(chunks=100, played=99, underruns=1, overruns=2, dropped=3, repeated=4, late=5, lost=6, stale=7, resyncs=8,
                                     unwritten=9)
        self.ring = SimpleNamespace(depth=15)
        self.target = 60


# ---------- the system record from a fake sysfs ----------
sysroot = os.path.join(TMP, "sys")
cpudir = os.path.join(sysroot, ST.SYS_CPU)
for n in range(4):
    os.makedirs(os.path.join(cpudir, f"cpu{n}", "cpufreq"))
os.makedirs(os.path.join(cpudir, "cpufreq"), exist_ok=True)
def put(path, text):
    with open(path, "w") as f:
        f.write(text)
fq = os.path.join(cpudir, "cpu0", "cpufreq")
put(os.path.join(fq, "scaling_governor"), "schedutil\n"); put(os.path.join(fq, "scaling_cur_freq"), "1800000\n"); put(os.path.join(fq, "cpuinfo_max_freq"), "2400000\n")
gov, cur, top, cpus = ST.cpu_info(sysroot)
check((GOVERNORS[gov - 1], cur, top, cpus) == ("schedutil", 1800000, 2400000, 4), f"cpu_info: governor, clocks in kHz, number of CPUs ({gov} {cur} {top} {cpus})")
fq1 = os.path.join(cpudir, "cpu2", "cpufreq")
put(os.path.join(fq1, "scaling_governor"), "performance\n"); put(os.path.join(fq1, "scaling_cur_freq"), "2200000\n"); put(os.path.join(fq1, "cpuinfo_max_freq"), "3000000\n")
gov2, cur2, top2, _n = ST.cpu_info(sysroot, fast=[2, 3])
check((GOVERNORS[gov2 - 1], cur2, top2) == ("performance", 2200000, 3000000), "cpu_info: the first fast core is the one that is read")
check(ST.cpu_info(os.path.join(TMP, "nothing")) == (0, 0, 0, 0), "cpu_info: nothing readable is zeros, not an exception")
put(os.path.join(fq, "scaling_governor"), "something-new\n")
check(ST.cpu_info(sysroot)[0] == 0, "a governor this code does not know is code 0")
check(ST.write_summary([]) == (0, 0, 0, 0, 0) and ST.write_summary([5, 1, 3, 2, 4, 100]) == (6, 3, 100, 100, 100), "write_summary: count, p50, p95, p99, max")
check(SENDER_VERSION == SELFTEST_SENDER, "the sender announces the version that knows the self-test")


async def main():
    loop = asyncio.get_running_loop()
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    R.WATCHDOG_S = 0.15
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True), writer_loop=loop)
    check(r._writer is not None, "setup: a pad with a writer thread")
    pad = SimpleNamespace(rumble=r, haptics=Stream(), speaker=Stream())
    sender_rt_status = sender_rt.status
    sender_rt.status = lambda: (True, False, True)
    ST.bind(emit, time.monotonic_ns, lambda: OFFSET_US, lambda i: pad if i == 0 else None)

    # ---------- nothing armed: no record, the hot path only reads one attribute ----------
    check(ST.armed is False, "setup: not armed")
    r.set(30, 40)
    check(ST._records == [], "not armed: a rumble write creates no record")

    # ---------- begin: armed, the system record answers ----------
    ST.on_command(5, ST_BEGIN, 0, 1, 0, 0)
    check(ST.armed is False, "a request for a slot without a pad is ignored")
    ST.on_command(0, ST_BEGIN, 0, 11, 0, 0)
    check(ST.armed and ST.slot == 0 and ST.ref == 11, "ST_BEGIN arms the measurement of the slot")
    sysrec = take(SR_SYS)
    check(len(sysrec) == 1 and sysrec[0][2] == 11 and sysrec[0][1] & SRF_RT, f"the answer is the system record with the request's reference ({sysrec})")
    check(r._writer._collect, "the writer starts collecting the time of every write")

    # ---------- a command from the PC: a record with the packet's sequence number ----------
    t0 = time.monotonic_ns()
    ST.fb_arrived(0, 777)
    r.set(100, 200)
    await asyncio.sleep(0.03)
    recs = [x for x in ST._records if x.src == SRC_PC]
    check(len(recs) == 1 and recs[0].seq == 777 and (recs[0].small, recs[0].large, recs[0].way) == (100, 200, 2), "the write carries the command's sequence number and motors")
    rec = recs[0]
    check(t0 <= rec.recv_ns <= rec.enq_ns <= rec.done_ns and rec.dur_ns > 0 and rec.err == 0, "times in order: arrival, hand-over, end of the write (taken on the writer thread)")
    # a refused command that arrived long ago belongs to no write
    ST._pending[0] = (555, time.monotonic_ns() - 5 * ST.PENDING_S * ST.NS_PER_S)
    r.set(100, 201)
    await asyncio.sleep(0.02)
    stale = [x for x in ST._records if x.src == SRC_PC and (x.small, x.large) == (100, 201)]
    check(stale and stale[0].seq == 0, "a command that arrived too long ago is not matched to the write")
    # the sender's own repeat is a record of its own
    await asyncio.sleep(0.06)
    check(any(x.src == SRC_REPEAT for x in ST._records), "the sender's own repeat of the report is recorded apart from the PC's command")
    # the watchdog: no refresh -> the motors stop, the record says when the last command came
    r.set(60, 70)
    cmd_ns = time.monotonic_ns()
    await asyncio.sleep(0.5)
    wd = [x for x in ST._records if x.src == SRC_WATCHDOG]
    check(r.cur == (0, 0) and len(wd) == 1 and (wd[0].small, wd[0].large) == (0, 0), f"the watchdog stop is a record ({r.cur}, {len(wd)})")
    check(abs(wd[0].recv_ns - cmd_ns) < 50_000_000 and 0.1e9 < wd[0].done_ns - wd[0].recv_ns < 0.4e9, "the watchdog record's start is the last command, so the stop time is ~WATCHDOG_S")

    # ---------- records so far, the measurement goes on ----------
    out.clear()
    ST.on_command(0, ST_SNAP, ST_RECORDS, 12, 0, 0)
    fb = take(SR_FB)
    check(ST.armed and len(fb) == len(ST._records) and take(SR_DONE)[0][3][0] == len(ST._records), "ST_SNAP / ST_RECORDS sends every rumble record + the closing one and keeps measuring")
    first = fb[0]
    check(first[1] == SRC_PC | 2 << 4 and first[2] == 777, "a record: source and way in the flags, the packet's sequence number as its reference")
    recv_us, enq_us, done_us, motors, dur_us, err = first[3]
    check(recv_us == (recs[0].recv_ns // 1000 + OFFSET_US) & proto.UINT32_MASK and motors == 100 | 200 << 8 and dur_us > 0 and err == 0,
          f"a record: times in the PC clock (offset added), motors packed, write time ({first[3]})")
    check(done_us - recv_us == (rec.done_ns - rec.recv_ns) // 1000 or abs((done_us - recv_us) - (rec.done_ns - rec.recv_ns) // 1000) <= 1, "a record: the difference of two times survives the conversion")

    # ---------- the streams ----------
    out.clear()
    ST.on_command(0, ST_SNAP, ST_HAPTICS, 13, 0, 0)
    ST.on_command(0, ST_SNAP, ST_SPEAKER, 14, 0, 0)
    h, s = take(SR_HAPT), take(SR_SPK)
    check([x[1] for x in h] == [0, 1] and h[0][3][:6] == (100, 99, 1, 2, 3, 4) and h[1][3][:5] == (5, 6, 7, 8, 9), f"haptics counters in two parts ({h})")
    depth, target = h[1][3][5] & 0xFFFF, h[1][3][5] >> 16
    check(depth == round(15 / 3000 * 10000) and target == round(60 / 3000 * 10000), f"haptics: depth and buffer target in 0.1 ms ({depth} {target})")
    check((s[1][3][5] & 0xFFFF, s[1][3][5] >> 16) == (1500, 6000), "speaker: depth and buffer target in 0.1 ms from the 10 ms frame")
    pad.haptics = None
    out.clear()
    ST.on_command(0, ST_SNAP, ST_HAPTICS, 15, 0, 0)
    check(take(SR_HAPT) == [], "a stream that does not run sends nothing")

    # ---------- end: everything, and the measurement stops ----------
    out.clear()
    ST.on_command(0, ST_END, 0, 16, 0, 0)
    kinds = [x[0] for x in out]
    check(not ST.armed and kinds[0] == SR_SYS and kinds[-1] == SR_DONE, f"ST_END disarms and sends the system record first, the closing record last ({kinds})")
    check(SR_COUNT in kinds and SR_WRITES in kinds and SR_FB in kinds, "ST_END: the writer's counters, the write times of this test, the rumble records")
    cnt = take(SR_COUNT)[0][3]
    check(cnt[0] >= 3 and cnt[1] == 0, f"SR_COUNT: writes since connect, no errors ({cnt})")
    wr = take(SR_WRITES)[0]
    check(wr[1] == 0 and wr[3][0] >= 3 and wr[3][1] <= wr[3][2] <= wr[3][3] <= wr[3][4], f"SR_WRITES: count and percentiles of the writes in the test ({wr})")
    n_before = len(ST._records)
    r.set(10, 20)
    await asyncio.sleep(0.02)
    check(len(ST._records) == n_before, "after ST_END a write creates no record, the results stay for another ST_END")
    out.clear()
    ST.on_command(0, ST_END, 0, 17, 0, 0)
    check(len(take(SR_FB)) == n_before and take(SR_DONE)[0][2] == 17, "a repeated ST_END sends the results again (a lost answer), with the new reference")

    # ---------- an old measurement nobody ended expires ----------
    ST.on_command(0, ST_BEGIN, 0, 18, 0, 0)
    ST._t_armed -= ST_MAX_S + 1
    ST.fb_arrived(0, 1)
    check(not ST.armed, "a measurement nobody ended (the receiver went away) disarms itself after ST_MAX_S")

    # ---------- a full list does not grow without bound ----------
    ST.on_command(0, ST_BEGIN, 0, 19, 0, 0)
    for _ in range(ST.REC_MAX + 5):
        ST.new_fb(0, 1, 1, 2, SRC_PC)
    check(len(ST._records) == ST.REC_MAX, "the record list is bounded")
    check(ST.new_fb(3, 1, 1, 2, SRC_PC) is None, "a pad that is not the measured one gets no record")
    ST.on_command(0, ST_END, 0, 20, 0, 0)

    # ---------- the writer: always-on counters, detail only on request ----------
    w = r._writer
    check(w.stats.n >= 3 and w.stats.errors == 0 and w.stats.total_ns > 0 and w.stats.max_ns > 0, "the writer's counters are always on")
    w.start_detail(2)
    for _k in range(5):
        w.write(b"\x01" * 4, "hid")
    await asyncio.sleep(0.05)
    check(len(w.detail_of("hid")) == 2, "the detail is bounded by the limit it was started with")
    w.stop_detail()
    n = len(w.detail_of("hid"))
    w.write(b"\x01" * 4, "hid")
    await asyncio.sleep(0.03)
    check(len(w.detail_of("hid")) == n, "after stop_detail nothing more is collected")

    # ---------- the request packet through sender_net ----------
    got = []
    net.on_selftest = lambda idx, *a: got.append((idx, a))
    net.on_feedback = lambda idx, vals: None
    codec = proto.Codec()
    def feed(t, *v):
        data = codec.pack(t, 0, *v)
        net._on_control(t, 0, (data[2] << 8) | data[3], data)
        return data
    req = codec.pack(T_STCMD, 0, ST_SNAP, ST_HAPTICS, 300, 0, 0)
    sq = (req[2] << 8) | req[3]
    for _k in range(3):
        net._on_control(T_STCMD, 0, sq, req)
    check(got == [(0, (ST_SNAP, ST_HAPTICS, 300, 0, 0))], f"stcmd reaches the self-test once, its three copies run it once ({got})")
    other = codec.pack(T_STCMD, 0, ST_SNAP, ST_SPEAKER, 301, 0, 0)
    net._on_control(T_STCMD, 0, (other[2] << 8) | other[3], other)
    check(len(got) == 2 and got[1][1][1] == ST_SPEAKER, "another argument is another request even right after the first")
    ST.begin(0, 21)
    ST._pending.clear()
    feed(T_FB, 1, 2, 3, 4, 5)
    check(ST._pending.get(0) is not None, "a feedback packet seen while armed is remembered with its sequence number")
    ST.disarm()
    ST._pending.clear()
    feed(T_FB, 1, 2, 3, 4, 5)
    check(not ST._pending, "... and not when nothing is armed")

    sender_rt.status = sender_rt_status
    r.close()

asyncio.run(main())
sys.exit(1 if fails else 0)
