"""Receiver: autostart z Windows przez Harmonogram zadań - start zaraz po zalogowaniu.

Wcześniej autostart był wpisem w HKCU\\...\\Run. Windows uruchamia takie programy z opóźnieniem i po kolei, gdy pulpit
się uspokoi - receiver startował nawet minutę po zalogowaniu, więc pad włączony od razu po starcie PC długo nie
przełączał ekranu w tryb konsoli. Zadanie „przy logowaniu” startuje bez tego opóźnienia, bez limitu czasu działania
(domyślne zadanie jest zabijane po 72 h) i także na baterii.

  enable(exe, args)  zadanie DualSenseBridge (schtasks /xml) i usunięcie starego wpisu Run; błąd -> zostaje wpis Run
  disable()          usuwa zadanie i wpis Run
  migrate()          przy starcie receivera: jest stary wpis Run -> zamiana na zadanie (raz, w tle)
  ensure_admin_task(exe, args)  zadanie DualSenseBridgeAdmin bez wyzwalacza, z najwyższymi uprawnieniami (tworzy je
                     receiver działający jako administrator) - ręczne uruchomienie receivera startuje przez nie
  run_admin_task()   start receivera jako administrator bez okna UAC (schtasks /run DualSenseBridgeAdmin)
  elevate()          zadanie z najwyższymi uprawnieniami (HIDMaestro tworzy pady tylko jako administrator) - zmienić je
                     może tylko proces administratora, więc robi to receiver uruchomiony jako administrator
"""
import os
import subprocess
import tempfile
from xml.sax.saxutils import escape

NAME = "DualSenseBridge"
ADMIN_NAME = "DualSenseBridgeAdmin"     # start na żądanie jako administrator (bez UAC) - bez wyzwalacza
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def user_id():
    dom, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{user}" if dom else user


LEAST, HIGHEST = "<RunLevel>LeastPrivilege</RunLevel>", "<RunLevel>HighestAvailable</RunLevel>"


def task_xml(exe, args, workdir, user, elevated=False, logon=True):
    """Definicja zadania: przy logowaniu tego użytkownika, bez opóźnienia i bez limitu czasu, zwykły priorytet.
    elevated: najwyższe uprawnienia (administrator bez okna UAC); logon=False: bez wyzwalacza (start na żądanie)."""
    e = escape
    trigger = f"<LogonTrigger><Enabled>true</Enabled><UserId>{e(user)}</UserId></LogonTrigger>" if logon else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>DualSense bridge receiver</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals>
    <Principal id="Author"><UserId>{e(user)}</UserId><LogonType>InteractiveToken</LogonType>
      {HIGHEST if elevated else LEAST}</Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{e(exe)}</Command><Arguments>{e(args)}</Arguments><WorkingDirectory>{e(workdir)}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)


def _run_value():
    """Stary wpis autostartu (komenda) albo None."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, NAME)[0]
    except OSError:
        return None


def _set_run(cmd):
    """cmd = komenda -> wpis Run; None -> usunięcie wpisu (brak wpisu to nie błąd)."""
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if cmd is not None:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, cmd)
            return
        try:
            winreg.DeleteValue(k, NAME)
        except FileNotFoundError:
            pass


def split_command(cmd):
    """Komenda z wpisu Run -> (program, argumenty): program w cudzysłowie albo do pierwszej spacji."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end], cmd[end + 1:].strip()
    exe, _sep, args = cmd.partition(" ")
    return exe, args.strip()


def create_task(exe, args, elevated=False, name=NAME, logon=True):
    """-> (ok, opis błędu)."""
    script = split_command(args)[0] if args else exe
    return _create_xml(task_xml(exe, args, os.path.dirname(os.path.abspath(script)), user_id(), elevated, logon), name)


def _create_xml(xml, name=NAME):
    """Zadanie z definicji XML -> (ok, opis błędu). XML w UTF-16 (tak go czyta schtasks); plik tymczasowy usuwany zawsze."""
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(xml)
        r = _schtasks("/create", "/tn", name, "/xml", path, "/f")
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, repr(e)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def enable(exe, args, elevated=False):
    ok, err = create_task(exe, args, elevated)
    cmd = subprocess.list2cmdline([exe]) + " " + args
    if ok:
        _set_run(None)
        return f"Autostart enabled (Task Scheduler, at logon, no delay):\n{cmd}"
    _set_run(cmd)
    return f"Task Scheduler failed ({err}) - autostart via the Run key (Windows starts it with a delay):\n{cmd}"


def disable():
    removed = _schtasks("/delete", "/tn", NAME, "/f").returncode == 0
    had_run = _run_value() is not None
    _set_run(None)
    return "Autostart disabled." if removed or had_run else "Autostart was not enabled."


def migrate():
    """Stary wpis Run -> zadanie przy logowaniu. -> True, gdy zamieniono. Błąd: wpis Run zostaje (autostart działa)."""
    try:
        cmd = _run_value()
        if not cmd:
            return False
        ok, err = create_task(*split_command(cmd))
        if not ok:
            print(f"Autostart: cannot move to Task Scheduler ({err}) - Run key kept")
            return False
        _set_run(None)
        print("Autostart: moved from the Run key to Task Scheduler (starts right after logon)")
        return True
    except Exception as e:          # migracja nie może zatrzymać receivera
        print(f"Autostart: migration failed: {e!r}")
        return False


def elevate(exe, args):
    """Istniejące zadanie autostartu bez podniesionych uprawnień -> to samo zadanie (exe, args jak przy --autostart on)
    z najwyższymi. -> True, gdy zmieniono. Brak zadania / już podniesione / błąd -> False (receiver działa dalej).
    Definicja powstaje od nowa - XML z schtasks /query przychodzi w stronie kodowej konsoli (polskie znaki w ścieżce)."""
    try:
        r = _schtasks("/query", "/tn", NAME, "/xml")
        if r.returncode != 0 or LEAST not in r.stdout:
            return False
        ok, err = create_task(exe, args, elevated=True)
        if not ok:
            print(f"Autostart: cannot raise to highest privileges ({err})")
        return ok
    except Exception as e:
        print(f"Autostart: elevation failed: {e!r}")
        return False


def ensure_admin_task(exe, args):
    """Zadanie startu na żądanie z najwyższymi uprawnieniami - odświeżane przy każdym starcie jako administrator (ta sama
    komenda co autostart; folder receivera mógł się przenieść). -> True, gdy jest."""
    try:
        ok, err = create_task(exe, args, elevated=True, name=ADMIN_NAME, logon=False)
        if not ok:
            print(f"Admin start task: cannot create ({err})")
        return ok
    except Exception as e:
        print(f"Admin start task: {e!r}")
        return False


def run_admin_task():
    """Receiver jako administrator bez UAC: istniejące zadanie ADMIN_NAME z najwyższymi uprawnieniami -> schtasks /run.
    -> True, gdy wystartowało. Brak zadania / zwykłe uprawnienia w zadaniu -> False (wtedy okno UAC)."""
    try:
        r = _schtasks("/query", "/tn", ADMIN_NAME, "/xml")
        if r.returncode != 0 or HIGHEST not in r.stdout:
            return False
        return _schtasks("/run", "/tn", ADMIN_NAME).returncode == 0
    except Exception as e:
        print(f"Admin start task: cannot run ({e!r})")
        return False
