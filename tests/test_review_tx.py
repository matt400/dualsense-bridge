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
    r = R.Rumble(1, Dev(ff=True), "evdev")
    check(r.methods == ["evdev"], "start: tylko evdev FF")
    r._open_hidraw = lambda given=None: (setattr(r, "fd", os.open(path2, os.O_RDWR)) or True)
    def broken(small, large):
        raise OSError(errno.EIO, "Input/output error")
    r._send_hidraw = broken
    try:
        r.set(50, 60)
        raised = None
    except OSError as e:
        raised = e
    check(raised is None, f"błąd evdev i zapasowego HID nie wychodzi z set(): {raised!r}")
    check(r.methods == [], f"oba zepsute sposoby wyłączone: {r.methods}")
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
sender_net.emit = sender_ctrl.net.emit = sup.emit = lambda t, i, *v: sent.append((t, v))
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

print("WSZYSTKO OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
