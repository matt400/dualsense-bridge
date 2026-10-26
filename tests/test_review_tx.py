"""Nadajnik: regresje z przeglądu bugów (wibracja przy chwilowym błędzie zapisu, lista plików z aktualizacji)."""
import asyncio, errno, os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_rumble as R
from evdev import ecodes as ec

TMP = tempfile.mkdtemp()
fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


class Dev:
    """Pad bez evdev FF (albo z FF, który się psuje)."""
    def __init__(self, ff=False):
        self.ff = ff
    def capabilities(self):
        return {ec.EV_FF: [ec.FF_RUMBLE]} if self.ff else {}
    def upload_effect(self, eff):
        raise OSError(errno.ENOSYS, "Function not implemented")
    def write(self, *a):
        pass


async def main():
    # chwilowy EAGAIN przy zapisie raportu HID nie może wyłączyć wibracji na zawsze
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    real = r._send_hidraw
    def busy(small, large):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
    r._send_hidraw = busy
    r.set(100, 200)
    check(r.methods == ["hidraw"], f"EAGAIN przy zapisie: HID zostaje w użyciu ({r.methods})")
    r._send_hidraw = real
    await asyncio.sleep(0.5)
    r.set(100, 200)
    check(os.path.getsize(path) > 0, "po chwilowym błędzie kolejne odświeżenie dochodzi do pada")
    r.close()

    # evdev FF psuje się, zapas przez HID też rzuca - wyjątek nie może wyjść z set() (wywalał callback pętli asyncio)
    path2 = os.path.join(TMP, "hidraw2")
    open(path2, "wb").close()
    r = R.Rumble(1, Dev(ff=True), "auto")           # zapas przez HID tylko w trybie auto (w "evdev" wybór z konfiguracji zostaje)
    check(r.methods == ["evdev"], "start: tylko evdev FF")
    r._open_hidraw = lambda given=None: (setattr(r, "fd", os.open(path2, os.O_RDWR)) or True)
    def broken(small, large):
        raise OSError(errno.EIO, "Input/output error")
    r._send_hidraw = broken
    try:
        r.set(50, 60)
        await asyncio.sleep(0.1)                    # the fallback is opened in a worker thread
        raised = None
    except OSError as e:
        raised = e
    check(raised is None, f"błąd evdev i zapasowego HID nie wychodzi z set(): {raised!r}")
    check(r.methods == ["hidraw"], f"pojedynczy EIO nie wyłącza raportu HID: {r.methods}")
    for k in range(R.FAIL_MAX):
        r.set(50 + k + 1, 60)                       # zmiana = zapis (bez niej set() nic nie wysyła)
    check(r.methods == [] and r.fd is None, f"stały EIO: oba zepsute sposoby wyłączone, deskryptor zamknięty: {r.methods}")
    r.close()

asyncio.run(main())

# git pull: lista zmienionych plików z nazwami ze spacjami (requirements w folderze ze spacją itp.)
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
os.environ.update({k: ENV[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")})
def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, env=ENV, check=True, capture_output=True)
origin, work = os.path.join(TMP, "origin"), os.path.join(TMP, "work")
os.makedirs(origin)
git(origin, "init", "-q", "-b", "main")
open(os.path.join(origin, "a.txt"), "w").write("1")
git(origin, "add", "."); git(origin, "commit", "-qm", "a")
git(TMP, "clone", "-q", origin, work)
open(os.path.join(origin, "my notes.txt"), "w").write("2")
git(origin, "add", "."); git(origin, "commit", "-qm", "b")
st, _msg, _old, _new, files = proto.git_pull(work)
check(st == proto.UPD_DONE and files == ["my notes.txt"], f"zmienione pliki z nazwą ze spacją: {files}")

# utrata samego urządzenia ruchu / touchpada: odbiornik nie może zostać z ostatnim gyro ani przyłożonym palcem
import types
import sender_ctrl, sender_net, sender_state, sender_supervisor as sup
from proto import T_STATE, T_TOUCH
sender_config.C.CAL_S, sender_config.C.MOTION_MIN_DT = 0, 0
sender_net.init()
sent = []
sender_net.emit = sender_ctrl.net.emit = lambda t, i, *v: sent.append((t, v))
sup.emit_repeat = lambda t, i, *v, **k: sent.append((t, v))
E = lambda t, c, v: types.SimpleNamespace(type=t, code=c, value=v)


class LostDev:
    """Urządzenie, które po kilku zdarzeniach znika (OSError jak przy odłączeniu)."""
    path = "/dev/input/event99"
    def __init__(self, events):
        self.events = events
    def absinfo(self, c):
        raise OSError
    async def async_read_loop(self):
        for e in self.events:
            yield e
            await asyncio.sleep(0)
        raise OSError(errno.ENODEV, "No such device")
    def ungrab(self): pass
    def close(self): pass
    def grab(self): pass


async def lost():
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:01", None, None, None)
    sender_state.active[c.key] = c
    sup.attach(c, "motion", LostDev([E(ec.EV_ABS, 4, 50000), E(ec.EV_SYN, ec.SYN_REPORT, 0)] * 3))
    sup.attach(c, "touch", LostDev([E(ec.EV_ABS, ec.ABS_MT_SLOT, 0), E(ec.EV_ABS, ec.ABS_MT_TRACKING_ID, 5),
                                    E(ec.EV_ABS, ec.ABS_MT_POSITION_X, 900), E(ec.EV_ABS, ec.ABS_MT_POSITION_Y, 500),
                                    E(ec.EV_SYN, ec.SYN_REPORT, 0)]))
    await asyncio.sleep(0.2)
    states = [v for t, v in sent if t == T_STATE]
    touches = [v for t, v in sent if t == T_TOUCH]
    check(any(v[15] == 50000 for v in states), "gyro z urządzenia ruchu doszło do odbiornika")
    check(states[-1][11:17] == (0, 0, 0, 0, 0, 0), f"utrata urządzenia ruchu -> wysłane zerowe gyro: {states[-1][11:17]}")
    check(any(v[0] for v in touches), "dotyk palca doszedł do odbiornika")
    check(touches[-1] == (0, 0, 0, 0, 0, 0, 0, 0), f"utrata touchpada -> wysłany brak palców: {touches[-1]}")
    sender_state.active.pop(c.key)

asyncio.run(lost())

# jeden raport pada (gałka + ruch, dwa urządzenia evdev naraz) -> jeden pakiet od razu, nie dwa (drugi po limiterze)
import time as _time
import sender_streams


class ReportDev:
    """Jak jądro: jeden raport HID pada trafia naraz do urządzeń przycisków / gałek i ruchu (oba gotowe do odczytu
    w tej samej iteracji pętli)."""
    path = "/dev/input/event98"
    report = None
    def __init__(self, events):
        self.events = events
    def absinfo(self, c):
        raise OSError
    async def async_read_loop(self):
        await ReportDev.report
        for e in self.events:
            yield e
        await asyncio.sleep(1)


async def same_report():
    ReportDev.report = asyncio.get_running_loop().create_future()
    c = sender_ctrl.Ctrl(1, "aa:bb:cc:dd:ee:02", ReportDev([E(ec.EV_ABS, 0, 10), E(ec.EV_SYN, ec.SYN_REPORT, 0)]),
                         ReportDev([E(ec.EV_ABS, 4, 9000), E(ec.EV_SYN, ec.SYN_REPORT, 0)]), None)
    sender_config.C.PAD_MIN_DT = sender_config.C.MOTION_MIN_DT = 0.004
    c.t_state = 0.0
    sent.clear()
    tasks = [asyncio.create_task(sender_streams.stream_pad(c)), asyncio.create_task(sender_streams.stream_motion(c))]
    await asyncio.sleep(0.01)
    t0 = _time.monotonic()
    ReportDev.report.set_result(None)                      # raport pada przyszedł
    while not any(t == T_STATE for t, v in sent) and _time.monotonic() - t0 < 1:
        await asyncio.sleep(0)
    first = _time.monotonic() - t0
    await asyncio.sleep(0.02)
    states = [v for t, v in sent if t == T_STATE]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    c.cancel_timers()
    check(len(states) == 1 and states[0][0] == 10 and states[0][15] == 9000,
          f"gałka i ruch z jednego raportu w jednym pakiecie: {[(v[0], v[15]) for v in states]}")
    check(first < 0.002, f"pakiet od razu (bez czekania na okno limitera): {first * 1000:.2f} ms")

asyncio.run(same_report())

# migracja sender.toml: tylko stare wartości domyślne, własne zostają, komentarze i układ też
import sender_migrate
toml = os.path.join(TMP, "sender.toml")
src = ('host = "192.168.1.4"\n'
       'pad_min_dt = 0.004   # mój komentarz\n'
       '# motion_min_dt = 0.004   # zakomentowane - bez zmian\n'
       'motion_min_dt = 0.002\n'
       'touch_coalesce_ms = 5\n'
       'key = "a b"\n')
open(toml, "w").write(src)
done = sender_migrate.migrate(toml, log=lambda m: None)
got = open(toml).read()
check([d[0] for d in done] == ["pad_min_dt", "touch_coalesce_ms"], f"zmienione tylko stare wartości domyślne: {done}")
check(got == src.replace("pad_min_dt = 0.004   # mój", "pad_min_dt = 0.001   # mój").replace("touch_coalesce_ms = 5", "touch_coalesce_ms = 2"),
      f"komentarze, zakomentowane linie i własne wartości (motion_min_dt = 0.002) bez zmian:\n{got}")
check(open(toml + ".bak").read() == src, "kopia przed zmianą: sender.toml.bak")
check(sender_migrate.migrate(toml, log=lambda m: None) == [], "drugi raz - nic do zmiany")
import tomllib
cfgd = tomllib.loads(got)
check(cfgd["pad_min_dt"] == 0.001 and cfgd["touch_coalesce_ms"] == 2 and cfgd["key"] == "a b", "plik dalej poprawnym TOML")
open(toml, "w").write(src)
os.remove(toml + ".migrated")                     # świeży plik: stan migracji jest per plik
sender_config.init(["--config", toml])
check(sender_config.C.PAD_MIN_DT == 0.001 and sender_config.C.TOUCH_COALESCE_S == 0.002 and sender_config.C.MOTION_MIN_DT == 0.002,
      "start nadajnika migruje sam (po aktualizacji przyciskiem nic nie trzeba robić)")

print("WSZYSTKO OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
