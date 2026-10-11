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

print("WSZYSTKO OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
