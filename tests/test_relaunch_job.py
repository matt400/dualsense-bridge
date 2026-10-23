"""Receiver: a restarted receiver must leave the Task Scheduler job (otherwise it is killed with the old process)."""
import sys
sys.path.insert(0, sys.argv[1])
import rxenv  # noqa: F401 - Windows stubs
import rx_winapi as W

ok = lambda m: print("OK  ", m)
started: list[int] = []


def popen(cmd, **kw):
    started.append(kw.get("creationflags", 0))
    if refuse and kw.get("creationflags", 0) & W.CREATE_BREAKAWAY_FROM_JOB:
        raise PermissionError(5, "Access is denied")


W.subprocess.Popen = popen
refuse = False
W._spawn(["python", "x.py"], {"creationflags": W.DETACHED_PROCESS | W.CREATE_NEW_PROCESS_GROUP})
assert started == [W.DETACHED_PROCESS | W.CREATE_NEW_PROCESS_GROUP | W.CREATE_BREAKAWAY_FROM_JOB], started
ok("restart: nowy proces startuje z CREATE_BREAKAWAY_FROM_JOB (zachowuje inne flagi)")

started.clear(); W._spawn(["python", "x.py"], {})
assert started == [W.CREATE_BREAKAWAY_FROM_JOB], started
ok("restart z konsoli (bez flag): też z breakaway")

started.clear(); refuse = True
W._spawn(["python", "x.py"], {"creationflags": W.DETACHED_PROCESS})
assert started == [W.DETACHED_PROCESS | W.CREATE_BREAKAWAY_FROM_JOB, W.DETACHED_PROCESS], started
ok("job nie pozwala na breakaway: ponowna próba bez flagi (receiver nie znika)")
print("RELAUNCH JOB OK")
