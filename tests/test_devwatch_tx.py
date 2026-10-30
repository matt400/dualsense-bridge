"""Nadajnik: nowy węzeł w /dev/input budzi supervisora od razu (inotify), a nie po rescan_s."""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C, SETTLE_S
import sender_supervisor as sup

ok = lambda m: print("OK  ", m)
TMP = tempfile.mkdtemp()


async def main():
    w = sup.DevWatch()
    assert w.start(TMP.encode()), "inotify na Linuksie"
    t0 = time.monotonic()
    asyncio.get_running_loop().call_later(0.1, lambda: open(os.path.join(TMP, "event7"), "w").close())
    await w.wait(5.0)
    dt = time.monotonic() - t0
    assert dt < 1.0, f"nowy węzeł budzi od razu, a nie po timeout ({dt:.2f} s)"
    assert not w.event.is_set(), "po obudzeniu zdarzenie skasowane"
    t0 = time.monotonic()
    await w.wait(0.2)
    assert 0.15 < time.monotonic() - t0 < 1.0, "bez zmian: czeka do timeout"
    w.close()
    assert w.fd is None
    ok("inotify: nowy węzeł eventN budzi supervisora od razu; bez zmian - timeout")

    bad = sup.DevWatch()
    assert not bad.start(os.path.join(TMP, "nie-ma").encode()) and bad.fd is None
    t0 = time.monotonic()
    await bad.wait(0.1)
    assert time.monotonic() - t0 >= 0.09
    bad.close()
    ok("brak katalogu: zwykłe skanowanie co rescan_s, bez wyjątku")

    C.RESCAN_S = 2.0
    sup._first_seen.clear()
    assert sup.next_scan_s(100.0) == 2.0
    sup._first_seen["aa"] = 100.0
    assert abs(sup.next_scan_s(100.1) - (SETTLE_S - 0.1 + 0.02)) < 1e-9, "niekompletny pad: skan zaraz po SETTLE_S"
    assert sup.next_scan_s(105.0) == 0.05
    sup._first_seen.clear()
    ok("niekompletny pad: następny skan po SETTLE_S, a nie po rescan_s")

asyncio.run(main())

# ---------- bye: ten sam pakiet powtórzony w czasie (jeden numer - odbiorca obsłuży go raz) ----------
import socket
import sender_net as net
from proto import T_BYE
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 0)); rx.settimeout(1.0)
C.PC_ADDR, C.PORT = "127.0.0.1", rx.getsockname()[1]
net.init()

async def bye():
    assert net.open_sock(asyncio.get_running_loop())
    t0 = time.monotonic()
    net.emit_repeat(T_BYE, 1)
    got = []
    for _ in range(3):
        got.append((await asyncio.get_running_loop().sock_recv(rx, 64), time.monotonic() - t0))
    net.close_sock()
    return got

rx.setblocking(False)
got = asyncio.run(bye())
assert len({d for d, _ in got}) == 1, "trzy kopie tego samego pakietu (ten sam numer)"
assert got[-1][1] >= net.REPEAT_DELAYS[-1] - 0.005, f"powtórki rozłożone w czasie: {[round(t, 3) for _, t in got]}"
print("OK   bye: jeden pakiet, powtórki po", net.REPEAT_DELAYS, "s")
print("DEVWATCH OK")
