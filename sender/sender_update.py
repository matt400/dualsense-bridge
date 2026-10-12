"""Sender: aktualizacja na polecenie receivera (przycisk "Aktualizuj" w oknie na PC).

CMD_UPDATE -> git pull --ff-only w folderze repozytorium (nadrzędnym dla sender/), jako właściciel repozytorium,
gdy nadajnik działa jako root (usługa) -> pakiet build ze stanem (UPD_*) -> po pobraniu nowego kodu restart nadajnika
(os.execv: ten sam PID, więc systemd nic nie zauważa; ręcznie uruchomiony też wraca).
Pakiet build (commit, czas commita, stan) idzie co 5 s - okno receivera pokazuje wersję nadajnika.
"""
import asyncio
import os
import socket
import time

import proto
import sender_net as net
import sender_state as state
from proto import T_BUILD, T_BUILD2, T_UPDMSG, UPD_DONE, UPD_NONE, UPD_RUNNING

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = "sender/requirements.txt"
T_UPDMSG_MAX = proto.BODY[T_UPDMSG].size
REPEAT_GUARD_S = 30.0          # polecenie przychodzi 3x (UDP) - kolejne w tym czasie są ignorowane
TEXT = {proto.UPD_NO_CHANGE: "already up to date", UPD_DONE: "updated - restarting",
        proto.UPD_NOT_GIT: "folder is not a git clone (git clone https://github.com/matt400/dualsense-bridge)",
        proto.UPD_NO_GIT: "git not installed (doas apk add git)", proto.UPD_FAILED: "git pull failed"}

version = ("", 0, "")          # (commit, czas commita, tytuł) - ustawiane w init()
status = UPD_NONE
fail_msg = ""                  # powód ostatniego błędu git pull - idzie z każdym pakietem build, póki stan to błąd
_last = 0.0


def init():
    global version
    version = proto.git_version(ROOT)
    if version[0]:
        print(f"Sender version {version[0]} ({time.strftime('%Y-%m-%d', time.localtime(version[1]))}): {version[2]}")


def send_build():
    """Pakiet build; przy błędzie aktualizacji najpierw powód (updmsg) - okno pokaże go także po zgubionym pakiecie
    i po ponownym kliknięciu Aktualizuj w ciągu REPEAT_GUARD_S (wtedy nadajnik nie próbuje drugi raz)."""
    if status == proto.UPD_FAILED and fail_msg:
        net.emit(T_UPDMSG, 255, fail_msg.encode("utf-8")[:T_UPDMSG_MAX])
    commit = version[0].encode("ascii", "replace")[:7]
    if net.new_receiver():            # nowy receiver: razem z adresami Wake-on-LAN (okno pokazuje wykryty MAC)
        import sender_wol
        mac = sender_wol.target_mac() or bytes(6)
        bcast = sender_wol.cfg.bcast or sender_wol.auto_bcast()
        net.emit(T_BUILD2, 255, commit, version[1], status, mac, socket.inet_aton(bcast) if bcast else bytes(4))
    else:
        net.emit(T_BUILD, 255, commit, version[1], status)


async def run():
    """Polecenie aktualizacji z receivera."""
    global status, _last, fail_msg
    now = time.monotonic()
    if status == UPD_RUNNING or now - _last < REPEAT_GUARD_S:
        return
    _last, status = now, UPD_RUNNING
    print("Update requested from PC - git pull")
    send_build()
    st, msg, old, new, files = await asyncio.to_thread(proto.git_pull, ROOT)
    status, fail_msg = st, msg if st == proto.UPD_FAILED else ""
    detail = msg or (f"{old[:7]} -> {new[:7]}" if st == UPD_DONE else ROOT if st == proto.UPD_NOT_GIT else "")
    print(f"Update: {TEXT.get(st, st)}" + (f" ({detail})" if detail else ""))
    if st == proto.UPD_FAILED and proto.is_auth_error(msg):
        print("Update: no access to the private repository from the service - run once on the phone: "
              f"{os.path.join(ROOT, 'sender', 'setup-update-key.sh')}")
    if st == UPD_DONE and REQUIREMENTS in files:
        print(f"Update: {REQUIREMENTS} changed - install new packages (doas apk add ... / pip install -r)")
    for _ in range(3):                       # wynik musi dotrzeć do receivera przed restartem (powód błędu: send_build)
        send_build()
        await asyncio.sleep(0.05)
    if st == UPD_DONE:
        restart()


def restart():
    """Zakończ main() (sprzątanie padów jak przy SIGTERM), potem __main__ robi os.execv."""
    state.restart = True
    if state.main_task is not None:
        state.main_task.cancel()
