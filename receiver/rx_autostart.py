"""Receiver: autostart with Windows through Task Scheduler - starts right after logon.

Autostart used to be an entry in HKCU\\...\\Run. Windows launches such programs with a delay and one after another once the desktop
settles down - the receiver started up to a minute after logon, so a pad switched on right after the PC booted did not
switch the screen to console mode for a long time. A "at logon" task starts without that delay, without a run time limit
(a default task is killed after 72 h) and also on battery.

  enable(exe, args)  the DualSenseBridge task (schtasks /xml) and removal of the old Run entry; on error the Run entry stays
  disable()          removes the task and the Run entry
  migrate()          at receiver startup: an old Run entry exists -> replaced by the task (once, in the background)
  ensure_admin_task(exe, args)  the DualSenseBridgeAdmin task without a trigger, with the highest privileges (created by a
                     receiver running as administrator) - a manual receiver start goes through it
  run_admin_task(args)  start the receiver as administrator without a UAC prompt (schtasks /run DualSenseBridgeAdmin) - only
                     when the task has the same arguments as this start
  elevate()          a task with the highest privileges (HIDMaestro creates pads only as administrator) - only an administrator
                     process can change it, so a receiver running as administrator does it; the task command
                     stays its own
"""
import os
import re
import subprocess
import tempfile
from xml.sax.saxutils import escape, unescape

NAME = "DualSenseBridge"
ADMIN_NAME = "DualSenseBridgeAdmin"     # start on demand as administrator (no UAC) - no trigger
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def user_id():
    """The current user as "DOMAIN\\user" (just the user name when there is no domain)."""
    dom, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{user}" if dom else user


LEAST, HIGHEST = "<RunLevel>LeastPrivilege</RunLevel>", "<RunLevel>HighestAvailable</RunLevel>"


def task_xml(exe, args, workdir, user, elevated=False, logon=True) -> str:
    """Task definition: at this user's logon, no delay and no time limit, normal priority.
    elevated: the highest privileges (administrator without a UAC prompt); logon=False: no trigger (start on demand)."""
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
    """Run schtasks.exe with the arguments, without a console window; returns the CompletedProcess."""
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)


def _run_value():
    """The old autostart entry (the command) or None."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, NAME)[0]
    except OSError:
        return None


def _set_run(cmd) -> None:
    """cmd = a command -> the Run entry; None -> remove the entry (a missing entry is not an error)."""
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if cmd is not None:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, cmd)
            return
        try:
            winreg.DeleteValue(k, NAME)
        except FileNotFoundError:
            pass


_ACTION = re.compile(r"<Command>(.*?)</Command>\s*(?:<Arguments>(.*?)</Arguments>)?", re.S)
_XML_QUOTES = {"&quot;": '"', "&apos;": "'"}


def task_action(xml):
    """(program, arguments) from a task definition (schtasks /query /xml), or None. Only when the script from the arguments exists:
    schtasks returns the XML in the console code page, so a path with non-ASCII characters may come back garbled - then
    None (the caller keeps its own command)."""
    m = _ACTION.search(xml or "")
    if m is None:
        return None
    exe, args = unescape(m[1], _XML_QUOTES).strip(), unescape(m[2] or "", _XML_QUOTES).strip()
    script = split_command(args)[0] if args else exe
    return (exe, args) if os.path.isfile(script) else None


def split_command(cmd):
    """A command from a Run entry -> (program, arguments): the program is quoted or ends at the first space."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end], cmd[end + 1:].strip()
    exe, _sep, args = cmd.partition(" ")
    return exe, args.strip()


def create_task(exe, args, elevated=False, name=NAME, logon=True):
    """Create the task for `exe args` (the working folder is the script's folder) -> (ok, error description)."""
    script = split_command(args)[0] if args else exe
    return _create_xml(task_xml(exe, args, os.path.dirname(os.path.abspath(script)), user_id(), elevated, logon), name)


def _create_xml(xml, name=NAME):
    """Create a task from an XML definition -> (ok, error description). The XML is UTF-16 (that is how schtasks reads it); the temporary
    file is always removed."""
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


def enable(exe, args, elevated=False) -> str:
    """Enable autostart: create the logon task and drop the old Run entry; when the task cannot be created, fall back to the Run entry.
    Returns a log message."""
    ok, err = create_task(exe, args, elevated)
    cmd = subprocess.list2cmdline([exe]) + " " + args
    if ok:
        _set_run(None)
        return f"Autostart enabled (Task Scheduler, at logon, no delay):\n{cmd}"
    _set_run(cmd)
    return f"Task Scheduler failed ({err}) - autostart via the Run key (Windows starts it with a delay):\n{cmd}"


def disable() -> str:
    """Disable autostart: delete the task and the Run entry. Returns a log message."""
    removed = _schtasks("/delete", "/tn", NAME, "/f").returncode == 0
    had_run = _run_value() is not None
    _set_run(None)
    return "Autostart disabled." if removed or had_run else "Autostart was not enabled."


def migrate() -> bool | None:
    """The old Run entry -> a task at logon. -> True when replaced. On error the Run entry stays (autostart keeps working)."""
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
    except Exception as e:          # migration must not stop the receiver
        print(f"Autostart: migration failed: {e!r}")
        return False


def elevate(exe, args):
    """An existing autostart task without elevated privileges -> the same task with the highest ones. -> True when changed.
    No task / already elevated / an error -> False (the receiver carries on). The definition is rebuilt (the XML from schtasks
    /query comes in the console code page), but with the task's own command (task_action) - the exe, args of this process only
    when it cannot be read. Earlier it always used the arguments of the current process: a manual start without --config (or
    with other options) replaced the autostart and at the next logon the receiver started with different settings."""
    try:
        r = _schtasks("/query", "/tn", NAME, "/xml")
        if r.returncode != 0 or LEAST not in r.stdout:
            return False
        exe, args = task_action(r.stdout) or (exe, args)
        ok, err = create_task(exe, args, elevated=True)
        if not ok:
            print(f"Autostart: cannot raise to highest privileges ({err})")
        return ok
    except Exception as e:
        print(f"Autostart: elevation failed: {e!r}")
        return False


def ensure_admin_task(exe, args):
    """The start-on-demand task with the highest privileges - refreshed on every start as administrator (the same
    command as the autostart; the receiver folder may have moved). -> True when it exists."""
    try:
        ok, err = create_task(exe, args, elevated=True, name=ADMIN_NAME, logon=False)
        if not ok:
            print(f"Admin start task: cannot create ({err})")
        return ok
    except Exception as e:
        print(f"Admin start task: {e!r}")
        return False


def run_admin_task(args=None):
    """The receiver as administrator without UAC: an existing ADMIN_NAME task with the highest privileges -> schtasks /run.
    -> True when it started. No task / ordinary privileges in the task -> False (then the UAC prompt). args: the arguments
    of this start (rx_config.autostart_command) - a task with different ones -> False: the task would run the receiver with its own,
    and the --config / --pads of this start would be lost silently."""
    try:
        r = _schtasks("/query", "/tn", ADMIN_NAME, "/xml")
        if r.returncode != 0 or HIGHEST not in r.stdout:
            return False
        own = task_action(r.stdout) if args is not None else None
        if own is not None and own[1] != args:
            print("Admin start task has other arguments than this start - asking through UAC instead")
            return False
        return _schtasks("/run", "/tn", ADMIN_NAME).returncode == 0
    except Exception as e:
        print(f"Admin start task: cannot run ({e!r})")
        return False
