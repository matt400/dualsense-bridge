"""Nadajnik: aktualizacja na polecenie receivera (git pull + restart), pakiet build z wersją, git jako właściciel repo."""
import asyncio, os, subprocess, sys, tempfile, types
from unittest import mock
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import proto, sender_control, sender_net as net, sender_state as state, sender_update as U
from proto import BODY, CMD_UPDATE, T_BUILD, UPD_DONE, UPD_NO_CHANGE, UPD_NOT_GIT, UPD_RUNNING

ok = lambda m: print("OK  ", m)
ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
os.environ.update(ENV)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def commit(repo, path, text, msg):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", msg)


tmp = tempfile.mkdtemp()
origin, work = os.path.join(tmp, "origin"), os.path.join(tmp, "work")
os.makedirs(origin)
git(origin, "init", "-q", "-b", "main")
commit(origin, "sender/sender_x.py", "a = 1\n", "Pierwsza wersja nadajnika")
git(tmp, "clone", "-q", origin, work)

# ---------- wersja ----------
h, ts, subject = proto.git_version(work)
assert len(h) == 7 and ts > 1_600_000_000 and subject == "Pierwsza wersja nadajnika", (h, ts, subject)
assert proto.git_version(tmp) == ("", 0, ""), "nie repozytorium -> pusta wersja"
U.ROOT = work
U.init()
sent = []
net.emit = lambda t, i, *v: sent.append((t, i, *v))
U.send_build()
assert sent[-1] == (T_BUILD, 255, h.encode(), ts, 0) and BODY[T_BUILD].size == 12
ok("wersja kodu (commit, czas, tytuł) i pakiet build")


async def main():
    # ---------- aktualizacja: bez zmian, potem nowa wersja -> restart ----------
    state.main_task = asyncio.get_running_loop().create_future()
    sent.clear()
    await U.run()
    states = [v[4] for v in sent if v[0] == T_BUILD]
    assert states[0] == UPD_RUNNING and states[-1] == UPD_NO_CHANGE and not state.restart, states
    U._last = 0.0
    commit(origin, "sender/sender_x.py", "a = 2\n", "Nowa funkcja")
    sent.clear()
    await U.run()
    assert [v[4] for v in sent][-1] == UPD_DONE and state.restart and state.main_task.cancelled(), sent
    assert open(os.path.join(work, "sender/sender_x.py")).read() == "a = 2\n"
    ok("polecenie aktualizacji: stan 'trwa' -> wynik; nowy kod -> restart (main anulowany, potem execv)")

    # ---------- powtórki polecenia (UDP 3x) są ignorowane ----------
    state.restart = False
    calls = []
    real = proto.git_pull
    U.proto.git_pull = lambda root: calls.append(root) or (UPD_NO_CHANGE, "", "", "", [])
    await U.run()
    assert not calls, "drugie polecenie w ciągu 30 s - nic"
    U._last = 0.0
    sender_control.on_command(255, CMD_UPDATE, 0); sender_control.on_command(255, CMD_UPDATE, 0)
    await asyncio.sleep(0.3)
    assert calls == [work], f"3 kopie polecenia = jedna aktualizacja: {calls}"
    U.proto.git_pull = real
    ok("polecenie z receivera (idx 255) uruchamia jedną aktualizację mimo powtórek")

    # ---------- folder bez gita ----------
    U.ROOT, U._last = tmp, 0.0
    sent.clear()
    await U.run()
    assert sent[-1][4] == UPD_NOT_GIT and not state.restart
    ok("folder nadajnika bez gita: stan NOT_GIT do receivera, bez restartu")

asyncio.run(main())

# ---------- root uruchamia gita jako właściciel repozytorium ----------
st = types.SimpleNamespace(st_uid=1000, st_gid=1000)
with mock.patch("os.geteuid", return_value=0), mock.patch("os.stat", return_value=st), \
        mock.patch("proto.ssh_command", return_value=None), \
        mock.patch("pwd.getpwuid", return_value=types.SimpleNamespace(pw_dir="/home/user", pw_name="user")):
    kw = proto._git_process_kwargs(work)
assert kw["user"] == 1000 and kw["group"] == 1000 and kw["extra_groups"] == [] and kw["env"]["HOME"] == "/home/user" \
    and kw["env"]["USER"] == "user", kw
with mock.patch("os.geteuid", return_value=1000):
    kw = proto._git_process_kwargs(work)
assert "user" not in kw and kw["env"]["GIT_TERMINAL_PROMPT"] == "0" and "BatchMode=yes" in kw["env"]["GIT_SSH_COMMAND"], kw
ok("usługa (root): git jako właściciel repozytorium (bez grup roota); git nigdy nie czeka na hasło")
print("UPDATE TX OK")
