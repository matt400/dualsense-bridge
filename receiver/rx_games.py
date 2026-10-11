"""Receiver: program na pierwszym planie -> automatyczny wybór profilu remapowania (profil "<pad>@<gra.exe>")."""
import rx_feedback as F
import rx_state as S
import rx_winapi as W
from rx_config import C

# programy, które nie są grami - nie trafiają na listę "ostatnie gry" w edytorze (profil i tak przełącza się
# na domyślny, gdy przejdziesz do nich z gry)
NOT_GAMES = frozenset("""
explorer.exe msedge.exe chrome.exe firefox.exe opera.exe brave.exe vivaldi.exe python.exe pythonw.exe
applicationframehost.exe searchhost.exe searchapp.exe shellexperiencehost.exe startmenuexperiencehost.exe
lockapp.exe textinputhost.exe systemsettings.exe taskmgr.exe cmd.exe powershell.exe pwsh.exe
windowsterminal.exe conhost.exe openconsole.exe code.exe notepad.exe discord.exe steam.exe steamwebhelper.exe
epicgameslauncher.exe gamebar.exe gamebarftserver.exe obs64.exe spotify.exe
""".split())
RECENT_MAX = 12
POLL_S = 0.5

recent = []      # ostatnio aktywne programy (najnowszy pierwszy) - podpowiedzi w edytorze profili gier


def is_game(exe):
    return exe not in NOT_GAMES and exe not in C.GAME_IGNORE


def poll_once():
    try:
        exe = W.foreground_exe()
    except OSError:
        return
    if not exe:
        return
    if is_game(exe) and recent[:1] != [exe]:
        recent[:] = [exe, *[e for e in recent if e != exe][:RECENT_MAX - 1]]
    for i in S.set_game(exe):
        if not S.alive[i]:
            continue
        label = S.profile_label(S.active[i])
        print(f"Controller {i}: {exe} -> profile {label}")
        F.pulse(i, "auto_default" if label == "default" else "auto_profile")


def watch_thread():
    while not S.stop.is_set():
        poll_once()
        S.stop.wait(POLL_S)
