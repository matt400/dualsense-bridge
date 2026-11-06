"""Receiver: aktualizacja (rx_update) na prawdziwych repozytoriach git w katalogu tymczasowym."""
import os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import rx_update as U

from chk import ok
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout


def commit(repo, path, text, msg):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", msg)


os.environ.update({k: ENV[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")})
tmp = tempfile.mkdtemp()
origin, work = os.path.join(tmp, "origin"), os.path.join(tmp, "work")
os.makedirs(origin)
git(origin, "init", "-q", "-b", "main")
commit(origin, "receiver/rx_x.py", "a = 1\n", "start")
commit(origin, "receiver/requirements.txt", "vgamepad\n", "reqs")
git(tmp, "clone", "-q", origin, work)

r = U.update(work, pip=False)
assert r.ok and not r.changed and "najnowszą" in r.text, r
ok("no changes: 'Masz najnowszą wersję', no restart")

commit(origin, "receiver/rx_x.py", "a = 2\n", "fix")
commit(origin, "receiver/rx_y.py", "b = 1\n", "feat")
r = U.update(work, pip=False)
assert r.ok and r.changed and "(2 " in r.text and r.old[:7] in r.text and r.new[:7] in r.text, r
assert open(os.path.join(work, "receiver/rx_x.py")).read() == "a = 2\n"
ok("nowe commity: pobrane, liczba zmian i wersje w komunikacie, restart potrzebny")

called = []
U._install_requirements = lambda root: called.append(root) or "nowe biblioteki zainstalowane"
commit(origin, "receiver/requirements.txt", "vgamepad\npycaw\n", "deps")
r = U.update(work)
assert r.changed and called == [work] and "biblioteki" in r.text, r
commit(origin, "receiver/rx_x.py", "a = 3\n", "no deps")
called.clear(); U.update(work)
assert not called, "pip only when requirements.txt changes"
ok("requirements.txt changed -> pip install, unchanged - no pip")

commit(origin, "receiver/rx_x.py", "a = 3b\n", "stages")
stages = []
r = U.update(work, pip=False, progress=stages.append)
assert r.changed and stages == [U.PCT_PULL, U.PCT_PULLED, U.PCT_BACKED_UP, U.PCT_INSTALLED], stages
assert stages == sorted(stages) and stages[-1] < 100, "the stages grow, 100% only after the sender result (window)"
stages.clear(); r = U.update(work, pip=False, progress=stages.append)
assert not r.changed and stages == [U.PCT_PULL, U.PCT_PULLED], "no changes: only the fetch stages"
ok("update progress: stages fetch -> backup -> pip, growing, without changes only the fetch")

with open(os.path.join(work, "remap.json"), "w") as f:
    f.write("{}")                           # a user file (untracked) does not get in the way
commit(work, "receiver/rx_x.py", "lokalnie\n", "local")
commit(origin, "receiver/rx_x.py", "a = 4\n", "upstream")
r = U.update(work, pip=False)
assert not r.ok and not r.changed and "git pull" in r.text, r
assert open(os.path.join(work, "receiver/rx_x.py")).read() == "lokalnie\n", "nothing overwritten or merged"
ok("local changes diverged from GitHub: git error, files untouched")

r = U.update(tmp, pip=False)
assert not r.ok and "ZIP" in r.text
U.gitupdate.find_git, real = (lambda: None), U.gitupdate.find_git
r = U.update(work, pip=False)
U.gitupdate.find_git = real
assert not r.ok and "Git for Windows" in r.text
assert U.ROOT == os.path.dirname(os.path.abspath(sys.argv[1])), "repository = the folder above receiver/"
ok("a folder without .git (ZIP) and no git program: a clear message")

from proto import UPD_FAILED, UPD_NO_CHANGE
b = ("a1b2c3d", 0, UPD_FAILED)
assert "szczegóły w logu" in U.sender_text(b), "older sender without a reason: pointed to the log"
t = U.sender_text(b, "git@github.com: Permission denied (publickey).")
assert "Permission denied (publickey)" in t and "setup-update-key.sh" in t, t
t = U.sender_text(b, "fatal: unable to access: Could not resolve host: github.com")
assert "Could not resolve host" in t and "setup-update-key.sh" not in t, "network error - no hint about the key"
assert "najnowsza" in U.sender_text(("a1b2c3d", 0, UPD_NO_CHANGE), "stary powód"), "reason only on an error"
ok("sender update result: the error reason from the phone in the window, the key hint only when access is missing")
# ---------- background GitHub check: how many changes wait (without fetching the code) ----------
chk = os.path.join(tmp, "chk")
git(tmp, "clone", "-q", origin, chk)
a = U.check_available(chk)
assert a == U.Available(0), a
commit(origin, "receiver/rx_x.py", "a = 5\n", "Pierwsza nowa")
commit(origin, "receiver/rx_y.py", "b = 2\n", "Druga nowa: tryb X")
before = open(os.path.join(chk, "receiver/rx_x.py")).read()
a = U.check_available(chk)
assert a.behind == 2 and a.subject == "Druga nowa: tryb X" and len(a.commit) == 7, a
assert open(os.path.join(chk, "receiver/rx_x.py")).read() == before, "the check does not change files"
git(chk, "checkout", "-q", "--detach")
assert U.check_available(chk) is None, "no tracking branch -> no info (not an error)"
assert U.check_available(tmp) is None, "not a clone -> no info"
git(chk, "remote", "set-url", "origin", os.path.join(tmp, "nie-ma"))
assert U.check_available(chk) is None, "brak sieci / repo -> brak informacji"
ok("GitHub check: number of changes and the title of the newest, no file changes; errors -> no info")

# ---------- code fetched earlier, but the old one runs (failed restart / pull from the console) ----------
pre = os.path.join(tmp, "pre")
git(tmp, "clone", "-q", origin, pre)
start = git(pre, "rev-parse", "HEAD").strip()
commit(origin, "receiver/rx_x.py", "a = 6\n", "pobrane wcześniej")
commit(origin, "receiver/requirements.txt", "vgamepad\nnowa\n", "deps2")
git(pre, "pull", "-q", "--ff-only")                       # nowy kod na dysku, receiver dalej na `start`
U.running, real_running = (lambda root=None: start[:7]), U.running
called.clear()
r = U.update(pre)
assert r.ok and r.changed and r.old == start and "(2 " in r.text, r
assert called == [pre], "requirements.txt changed since the RUNNING version -> pip"
U.running = lambda root=None: git(pre, "rev-parse", "HEAD").strip()[:7]
r = U.update(pre, pip=False)
assert r.ok and not r.changed and "najnowszą" in r.text, r
U.running = lambda root=None: start[:7]
a = U.check_available(pre)
assert a.behind == 2, ("available update counted from the running version", a)
U.running = real_running
ok("code already fetched, an older one runs: restart instead of 'Masz najnowszą wersję'; the badge counts from the running version")

calls = []
import gitupdate
import proto
real_git = gitupdate.git
gitupdate.git = lambda root, *a, **k: calls.append((a, k.get("env"), k.get("timeout"))) or real_git(root, *a, **k)
U.update(work, pip=False)
gitupdate.git = real_git
pull = [c for c in calls if c[0][:1] == ("pull",)]
assert pull and pull[0][1] == U.QUIET_ENV and pull[0][2] == U.PULL_TIMEOUT_S, pull
ok("git pull without login windows and with a time limit (the button does not hang on 'Aktualizuję…')")
print("UPDATE OK")
