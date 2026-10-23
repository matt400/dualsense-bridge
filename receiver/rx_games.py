"""Receiver: foreground program.

Automatic choice of the remap profile (profile "<pad>@<game.exe>") and stopping game rumble when the program changes
(leaving the game / alt-tab - rx_feedback.stop_game_rumble). Trigger effects survive a program change; they are
released only when the program that set them closes (release_closed).
"""
import time
from typing import Any

import rx_feedback as F
import rx_state as S
import rx_winapi as W
from rx_config import C

# programs that are not games - they never reach the "recent games" list in the editor (the profile still switches
# to the default one when you move to them from a game)
NOT_GAMES = frozenset("""
explorer.exe msedge.exe chrome.exe firefox.exe opera.exe brave.exe vivaldi.exe python.exe pythonw.exe
applicationframehost.exe searchhost.exe searchapp.exe shellexperiencehost.exe startmenuexperiencehost.exe
lockapp.exe textinputhost.exe systemsettings.exe taskmgr.exe cmd.exe powershell.exe pwsh.exe
windowsterminal.exe conhost.exe openconsole.exe code.exe notepad.exe discord.exe steam.exe steamwebhelper.exe
epicgameslauncher.exe gamebar.exe gamebarftserver.exe obs64.exe spotify.exe
""".split())
RECENT_MAX = 12
POLL_S = 0.5

recent = []      # recently active programs (newest first) - suggestions in the game profile editor


def is_game(exe) -> bool:
    """Whether a foreground executable counts as a game: it is neither in the built-in NOT_GAMES list nor in the user's game_ignore."""
    return exe not in NOT_GAMES and exe not in C.GAME_IGNORE


def in_game(exe=None):
    """Whether a game is in the foreground.

    A program outside the console-mode app list (desktop, Steam / Big Picture, launchers, this receiver - Settings ->
    Console mode) and outside NOT_GAMES. exe=None -> the program read last (S.game, refreshed every POLL_S). An empty
    console-mode list leaves NOT_GAMES alone.
    """
    exe = S.game if exe is None else exe
    if not exe:
        return False
    import rx_console          # rx_console -> rx_master -> rx_feedback -> (lazily) this module
    apps = rx_console.ctl.cfg.allowed_apps if rx_console.ctl is not None else rx_console.DEFAULT_APPS
    return exe not in apps and is_game(exe)


def poll_once() -> None:
    """One foreground check.

    On a program change: stop game rumble, update the recent list, let S.set_game pick the profile for every connected
    pad (with a confirmation pulse on the pad), wake the feedback loop when the desktop <-> game state flips, and
    release trigger effects of programs that have exited.
    """
    try:
        exe = W.foreground_exe()
    except OSError:
        return
    if not exe:
        return
    if exe != S.game:
        if S.game is not None:
            F.stop_game_rumble(f"{S.game} -> {exe}")
        changed = True
    else:
        changed = False
    if is_game(exe) and recent[:1] != [exe]:
        recent[:] = [exe, *[e for e in recent if e != exe][:RECENT_MAX - 1]]
    for i in S.set_game(exe):
        if not S.alive[i]:
            continue
        label = S.profile_label(S.active[i])
        print(f"Controller {i}: {exe} -> profile {label}")
        F.pulse(i, "auto_default" if label == "default" else "auto_profile")
    if changed:
        F.fb_event.set()       # game <-> desktop: the game's lightbar colour applies only in a game (rx_feedback._target)
    release_closed()


_procs: list[Any] = [0.0, None]           # (time, set of running programs) - at most one process snapshot per PROCS_S
PROCS_S = 2.0


def release_closed(now=None) -> None:
    """Release trigger effects of games whose process no longer runs (rx_feedback.forget_ended_games).

    The process
    snapshot is taken (and cached for PROCS_S) only when some slot holds effects of a program that is not in the foreground
    - usually never.
    """
    now = time.monotonic() if now is None else now

    def running(exe) -> bool:
        if _procs[1] is None or now - _procs[0] >= PROCS_S:
            try:
                _procs[:] = [now, W.running_exes()]
            except (OSError, AttributeError):
                return True    # unknown - triggers stay (a switch away from the game or a pad disconnect releases them)
        return exe in _procs[1]
    F.forget_ended_games(running)


def watch_thread() -> None:
    """Thread body: poll the foreground program every POLL_S until the receiver stops."""
    while not S.stop.is_set():
        poll_once()
        S.stop.wait(POLL_S)
