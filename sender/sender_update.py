"""Sender: update on a command from the receiver (the "Update" button in the PC window).

CMD_UPDATE -> git pull --ff-only in the repository folder (the parent of sender/), as the repository owner when the
sender runs as root (the service) -> build packet carrying the state (UPD_*) -> once new code is pulled the sender
restarts (os.execv: same PID, so systemd notices nothing; a manually started sender comes back too).
The build packet (commit, commit time, state) is sent every 5 s - the receiver window shows the sender's version.
"""
import asyncio
import os
import socket
import time

import proto
import sender_batt
import sender_net as net
import sender_state as state
from proto import T_BUILD, T_BUILD2, T_BUILD3, T_UPDMSG, UPD_DONE, UPD_NONE, UPD_RUNNING, utf8_field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = "sender/requirements.txt"
T_UPDMSG_MAX = proto.BODY[T_UPDMSG].size
REPEAT_GUARD_S = 30.0          # the command arrives 3x (UDP) - further ones within this time are ignored
TEXT = {proto.UPD_NO_CHANGE: "already up to date", UPD_DONE: "updated - restarting",
        proto.UPD_NOT_GIT: "folder is not a git clone (git clone https://github.com/matt400/dualsense-bridge)",
        proto.UPD_NO_GIT: "git not installed (doas apk add git)", proto.UPD_FAILED: "git pull failed"}

version = ("", 0, "")          # (commit, commit time, title) - set in init()
status = UPD_NONE
fail_msg = ""                  # reason of the last git pull failure - sent with every build packet while the state is a failure
_last = 0.0


def init() -> None:
    """Read the current git version of the sender's checkout once at startup and log it."""
    global version
    version = proto.git_version(ROOT)
    if version[0]:
        print(f"Sender version {version[0]} ({time.strftime('%Y-%m-%d', time.localtime(version[1]))}): {version[2]}")


def send_build() -> None:
    """Send the build packet (commit, commit time, update status) to the receiver.

    After a failed update the failure reason (updmsg) goes first - the window shows it even if the build packet is
    lost, and also after the user clicks Update again within REPEAT_GUARD_S (the sender then does not try twice).
    A receiver that understands the extended packet (new_receiver) additionally gets the Wake-on-LAN target MAC and
    broadcast address, so the window can show the detected MAC; an older receiver gets the plain build packet.
    A device with a battery (a phone) also sends its charge level in build3 - only to a receiver that announced it
    (SCFG_BATT), and only when there is a battery to read.
    """
    if status == proto.UPD_FAILED and fail_msg:
        net.emit(T_UPDMSG, 255, utf8_field(fail_msg, T_UPDMSG_MAX, "…"))   # git speaks Polish here: no half letters
    commit = version[0].encode("ascii", "replace")[:7]
    if net.new_receiver():            # new receiver: together with the Wake-on-LAN addresses (the window shows the detected MAC)
        import sender_wol
        mac = sender_wol.target_mac() or bytes(6)
        bcast = sender_wol.cfg.bcast or sender_wol.auto_bcast()
        addr = socket.inet_aton(bcast) if bcast else bytes(4)
        batt = sender_batt.read() if net.batt_receiver() else None
        if batt is None:
            net.emit(T_BUILD2, 255, commit, version[1], status, mac, addr)
        else:
            net.emit(T_BUILD3, 255, commit, version[1], status, mac, addr, batt)
    else:
        net.emit(T_BUILD, 255, commit, version[1], status)


async def run() -> None:
    """Perform an update requested by the receiver.

    The command arrives several times (UDP redundancy), so a call is ignored while an update is running or within
    REPEAT_GUARD_S of the previous one. Steps: mark the state as running and tell the receiver, run the blocking
    `git pull` in a worker thread (so the event loop keeps serving the pads), log the outcome, warn if the
    requirements file changed or the repository needs credentials, then send the final state three times (UDP) so it
    reaches the receiver before a possible restart. If new code was pulled, the sender restarts.
    """
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
    for _ in range(3):                       # the result must reach the receiver before the restart (failure reason: send_build)
        send_build()
        await asyncio.sleep(0.05)
    if st == UPD_DONE:
        restart()


def restart() -> None:
    """End main() (pads are cleaned up as on SIGTERM); afterwards the __main__ block performs os.execv.

    Cancelling the main task is enough: state.restart tells the entry point to re-exec the process instead of exiting.
    """
    state.restart = True
    if state.main_task is not None:
        state.main_task.cancel()
