"""Receiver: the numbers behind the Diagnostics tab (rx_diag) and their hooks in rx_net - no Qt."""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, S)
import rx_diag
import rx_net
from proto import BTL_LQ, BTL_RSSI, T_BTLINK, Codec, mac_bytes

ok = lambda m: print("OK  ", m)
cod = Codec()
MAC = "aa:bb:cc:dd:ee:01"
rx_diag.init(2)
NS = rx_diag.NS_PER_MS

# ---------- Series ----------
s = rx_diag.Series()
assert s.last() is None and s.stats() is None
for v in (3, None, 1, 5):
    s.add(v)
assert s.last() == 5 and s.stats() == (1, 3, 5), "gaps are left out of the statistics"
s.add(None)
assert s.last() is None
for _ in range(rx_diag.HISTORY + 10):
    s.add(1)
assert len(s.values) == rx_diag.HISTORY
s.clear()
assert not s.values
ok("Series: last / stats without gaps, fixed length, clear")

# ---------- jitter (RFC 3550) ----------
n = rx_diag.NetDiag()
n.ping(0, 1000 * NS, now=10.0)
assert n.jitter_ms is None, "the first ping has nothing to compare with"
n.ping(100 * NS, 1100 * NS, now=10.1)
assert n.jitter_ms == 0.0, "an even spacing = no jitter"
n.ping(200 * NS, 1204 * NS, now=10.2)
assert abs(n.jitter_ms - 0.25) < 1e-9, n.jitter_ms
n.ping(300 * NS, 1300 * NS, now=10.3)      # arrives 4 ms earlier than the previous spacing says
assert abs(n.jitter_ms - (0.25 + (4 - 0.25) / 16)) < 1e-9, n.jitter_ms
n.ping(300 * NS, 1300 * NS, now=10.35)
j = n.jitter_ms
n.ping(250 * NS, 1400 * NS, now=10.4)
assert n.jitter_ms == j, "a repeated / replayed ping does not count"
n.ping(400 * NS, 1500 * NS, now=12.0)
assert n.jitter_ms is None, "a silence longer than PING_RESTART_S restarts the estimate"
ok("jitter: RFC 3550 estimate, replay ignored, restart after a gap")

# ---------- LinkStats -> rx_diag.net ----------
ls = rx_net.LinkStats(2)
for sq in (1, 2, 3, 5, 6, 6, 4):                  # one lost (4), a duplicate (6) ... then 4 arrives late
    ls.packet(sq, now=1.0)
ls.packet(10, now=1.0)                             # 7, 8, 9 lost
ls.expected += 20
ls.got += 20
ls.tick(ls.t0 + 2.0)
assert rx_diag.net.dup == 1 and rx_diag.net.late == 1, (rx_diag.net.dup, rx_diag.net.late)
assert rx_diag.net.lost == 4, rx_diag.net.lost      # packet 4 (it came late) + 7, 8, 9
assert rx_diag.net.loss_pct is not None and rx_diag.net.loss_pct > 0
ok("LinkStats: late / duplicate / lost totals reach rx_diag.net")

# ---------- connect / bye ----------
now = time.monotonic()
rx_diag.init(2)
S.alive[0] = False
S.want[0].clear()
rx_net._mark_alive(0, now)
assert S.connected_at[0] == now and rx_diag.pads[0].connects == 1
rx_net._mark_alive(0, now + 1)
assert rx_diag.pads[0].connects == 1 and S.connected_at[0] == now, "a running pad is not a new connection"
rx_net._on_bye(0)
assert S.connected_at[0] is None and not S.alive[0]
ok("connected_at and the connects counter follow _mark_alive / _on_bye")

# ---------- btlink ----------
def btlink(mac, rssi, q, flags, hci=0):
    return cod.pack(T_BTLINK, 255, mac_bytes(mac), rssi, q, flags, hci)


S.pad_id[0] = MAC
now = time.monotonic()
assert rx_net._on_control(T_BTLINK, 255, 1, btlink(MAC.upper(), -4, 200, BTL_RSSI | BTL_LQ, 1), ("10.0.0.2", 5005), now, 0)
lk = rx_diag.link_of(0, now)
assert lk is not None and (lk.rssi, lk.quality, lk.hci) == (-4, 200, 1), "the MAC finds the slot (any case)"
rx_diag.on_btlink(btlink(MAC, 0, 120, BTL_LQ), now)
lk = rx_diag.link_of(0, now)
assert lk.rssi is None and lk.quality == 120, "no BTL_RSSI flag = no RSSI"
rx_diag.on_btlink(btlink(MAC, -30, 0, BTL_RSSI | BTL_LQ), now)
lk = rx_diag.link_of(0, now)
assert lk.rssi == -30 and lk.quality is None, "link quality 0 = the controller does not compute it: no data, not 0 %"
rx_diag.on_btlink(btlink("11:22:33:44:55:66", -1, 1, BTL_RSSI), now)
assert rx_diag.link_of(1, now) is None, "an unknown MAC is ignored"
assert rx_diag.link_of(0, now + rx_diag.LINK_STALE_S + 0.1) is None, "a stale reading is not shown"
ok("btlink: MAC -> slot, flags, unknown MAC, staleness")

# ---------- sample() ----------
rx_diag.init(2)
S.pad_id[0] = MAC
S.last_pkt = now
S.alive[0], S.alive[1] = True, False
S.info[0] = (7.5, 250.0, 0.0, now)
S.pps[0] = 240.0
rx_diag.on_btlink(btlink(MAC, -2, 255, BTL_RSSI | BTL_LQ), now)
rx_diag.sample(now)
p0, p1 = rx_diag.pads
assert rx_diag.net.rtt.last() == 7.5
assert p0.hz.last() == 250.0 and p0.pps.last() == 240.0 and p0.rssi.last() == -2 and p0.quality.last() == 100
assert p1.hz.last() is None and p1.rssi.last() is None, "a pad that is not connected gets gaps"
rx_diag.sample(now + 10)
assert rx_diag.net.rtt.last() is None and p0.hz.last() is None, "a silent sender: gaps everywhere"
ok("sample: values while online, gaps when not")

# ---------- levels ----------
L = rx_diag
assert [L.rtt_level(x) for x in (None, 1, 10, 30)] == ["", "ok", "warn", "bad"]
assert [L.jitter_level(x) for x in (None, 0.5, 3, 9)] == ["", "ok", "warn", "bad"]
assert [L.loss_level(x) for x in (None, 0, 1, 5)] == ["", "ok", "warn", "bad"]
assert [L.rssi_level(x) for x in (None, -30, -70, -85)] == ["", "ok", "warn", "bad"]
assert [L.quality_level(x) for x in (None, 95, 60, 30)] == ["", "ok", "warn", "bad"]
ok("level functions")

print("DIAG OK")
