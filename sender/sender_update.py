"""Sender: aktualizacja na polecenie receivera (przycisk "Aktualizuj" w oknie na PC).

CMD_UPDATE -> git pull --ff-only w folderze repozytorium (nadrzędnym dla sender/), jako właściciel repozytorium,
gdy nadajnik działa jako root (usługa) -> pakiet build ze stanem (UPD_*) -> po pobraniu nowego kodu restart nadajnika
(os.execv: ten sam PID, więc systemd nic nie zauważa; ręcznie uruchomiony też wraca).
Pakiet build (commit, czas commita, stan) idzie co 5 s - okno receivera pokazuje wersję nadajnika.
"""
import asyncio
import os
import time

import proto
import sender_net as net
import sender_state as state
from proto import T_BUILD, UPD_DONE, UPD_NONE, UPD_RUNNING

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = "sender/requirements.txt"
REPEAT_GUARD_S = 30.0          # polecenie przychodzi 3x (UDP) - kolejne w tym czasie są ignorowane
TEXT = {proto.UPD_NO_CHANGE: "already up to date", UPD_DONE: "updated - restarting",
        proto.UPD_NOT_GIT: "folder is not a git clone (git clone https://github.com/matt400/dualsense-bridge)",
        proto.UPD_NO_GIT: "git not installed (doas apk add git)", proto.UPD_FAILED: "git pull failed"}

version = ("", 0, "")          # (commit, czas commita, tytuł) - ustawiane w init()
status = UPD_NONE
_last = 0.0


def init():
    global version
    version = proto.git_version(ROOT)
    if version[0]:
        print(f"Sender version {version[0]} ({time.strftime('%Y-%m-%d', time.localtime(version[1]))}): {version[2]}")


def send_build():
    net.emit(T_BUILD, 255, version[0].encode("ascii", "replace")[:7], version[1], status)


async def run():
    """Polecenie aktualizacji z receivera."""
    global status, _last
    now = time.monotonic()
    if status == UPD_RUNNING or now - _last < REPEAT_GUARD_S:
        return
    _last, status = now, UPD_RUNNING
    print("Update requested from PC - git pull")
    send_build()
    st, msg, old, new, files = await asyncio.to_thread(proto.git_pull, ROOT)
    status = st
    detail = msg or (f"{old[:7]} -> {new[:7]}" if st == UPD_DONE else ROOT if st == proto.UPD_NOT_GIT else "")
    print(f"Update: {TEXT.get(st, st)}" + (f" ({detail})" if detail else ""))
    if st == UPD_DONE and REQUIREMENTS in files:
        print(f"Update: {REQUIREMENTS} changed - install new packages (doas apk add ... / pip install -r)")
    for _ in range(3):                       # wynik musi dotrzeć do receivera przed restartem
        send_build()
        await asyncio.sleep(0.05)
    if st == UPD_DONE:
        restart()


def restart():
    """Zakończ main() (sprzątanie padów jak przy SIGTERM), potem __main__ robi os.execv."""
    state.restart = True
    if state.main_task is not None:
        state.main_task.cancel()
