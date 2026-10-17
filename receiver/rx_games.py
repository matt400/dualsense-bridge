"""Receiver: program na pierwszym planie -> automatyczny wybór profilu remapowania (profil "<pad>@<gra.exe>")
i koniec wibracji z gry przy zmianie programu (wyjście z gry / alt-tab - rx_feedback.stop_game_rumble)."""
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


def in_game(exe=None):
    """Na pierwszym planie jest gra: program spoza listy programów trybu konsoli (pulpit, Steam / Big Picture,
    launchery, ten receiver - Ustawienia -> Tryb konsoli) i spoza NOT_GAMES. exe=None -> ostatnio odczytany program
    (S.game, co POLL_S). Pusta lista trybu konsoli = sama NOT_GAMES."""
    exe = S.game if exe is None else exe
    if not exe:
        return False
    import rx_console          # rx_console -> rx_master -> rx_feedback -> (leniwie) ten moduł
    apps = rx_console.ctl.cfg.allowed_apps if rx_console.ctl is not None else rx_console.DEFAULT_APPS
    return exe not in apps and is_game(exe)


def poll_once():
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
        F.fb_event.set()       # gra <-> pulpit: kolor lightbara z gry obowiązuje tylko w grze (rx_feedback._target)


def watch_thread():
    while not S.stop.is_set():
        poll_once()
        S.stop.wait(POLL_S)
