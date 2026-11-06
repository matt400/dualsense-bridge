"""Autostart (rx_autostart): a Task Scheduler task at logon instead of a Run entry (delayed by Windows),
migration of the old entry, a Run entry as the fallback when schtasks fails, disabling."""
import os, sys, types
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_autostart as A
from chk import ok
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

# ---------- XML zadania ----------
xml = A.task_xml(r"C:\Py\pythonw.exe", r'"C:\a & b\receiver_controller.py" --config x.toml', r"C:\a & b", r"PC\Ala")
root = ET.fromstring(xml.split("\n", 1)[1])                       # no UTF-16 declaration (this is text, not bytes)
get = lambda path: root.find(path, NS).text
assert get("t:Triggers/t:LogonTrigger/t:UserId") == r"PC\Ala"
assert root.find("t:Triggers/t:LogonTrigger/t:Delay", NS) is None, "no delay"
assert get("t:Settings/t:ExecutionTimeLimit") == "PT0S", "no 72 h limit"
assert get("t:Settings/t:DisallowStartIfOnBatteries") == "false" and get("t:Settings/t:StopIfGoingOnBatteries") == "false"
assert get("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
assert get("t:Actions/t:Exec/t:Command") == r"C:\Py\pythonw.exe"
assert get("t:Actions/t:Exec/t:Arguments") == r'"C:\a & b\receiver_controller.py" --config x.toml', "znaki XML uciekane"
assert get("t:Actions/t:Exec/t:WorkingDirectory") == r"C:\a & b"
ok("XML: at logon, no delay and no time limit, on battery too, paths escaped correctly")

assert A.split_command(r'"C:\Py 3\pythonw.exe" "C:\r\receiver_controller.py" --pads 2') == \
    (r"C:\Py 3\pythonw.exe", r'"C:\r\receiver_controller.py" --pads 2')
assert A.split_command(r"C:\Py\pythonw.exe C:\r\rc.py") == (r"C:\Py\pythonw.exe", r"C:\r\rc.py")
ok("command from the Run entry -> program + arguments (with and without quotes)")

# ---------- enable / migrate / disable (schtasks i rejestr podmienione) ----------
reg, runs, rc = {}, [], [0]
def fake_schtasks(*args):
    runs.append(args)
    if args[0] == "/create":
        assert open(args[args.index("/xml") + 1], encoding="utf-16").read().startswith("<?xml"), "plik XML w UTF-16"
    return types.SimpleNamespace(returncode=rc[0], stdout="", stderr="Access denied" if rc[0] else "")
A._schtasks = fake_schtasks
A._run_value = lambda: reg.get(A.NAME)
A._set_run = lambda cmd: reg.pop(A.NAME, None) if cmd is None else reg.__setitem__(A.NAME, cmd)

reg[A.NAME] = r'"C:\Py\pythonw.exe" "C:\r\receiver_controller.py"'
assert A.migrate() and A.NAME not in reg and runs[-1][:3] == ("/create", "/tn", A.NAME)
runs.clear()
assert not A.migrate() and not runs, "without an old entry nothing happens"
ok("migration: old Run entry -> task, entry removed; nothing the second time")

rc[0] = 1
reg[A.NAME] = r'"C:\Py\pythonw.exe" "C:\r\receiver_controller.py"'
assert not A.migrate() and A.NAME in reg, "schtasks fails -> the Run entry stays (autostart still works)"
reg.clear()
msg = A.enable(r"C:\Py\pythonw.exe", r'"C:\r\receiver_controller.py"')
assert "Run key" in msg and reg[A.NAME].startswith(r"C:\Py\pythonw.exe"), "enable: zapas we wpisie Run"
rc[0] = 0
msg = A.enable(r"C:\Py\pythonw.exe", r'"C:\r\receiver_controller.py"')
assert "Task Scheduler" in msg and A.NAME not in reg
assert A.disable() == "Autostart disabled." and runs[-1][:3] == ("/delete", "/tn", A.NAME)
ok("schtasks error: Run entry as the fallback; enable removes the old entry; disable removes the task")

# ---------- HIDMaestro: task with the highest privileges ----------
assert A.LEAST in xml and A.HIGHEST not in xml, "normal privileges by default"
assert A.HIGHEST in A.task_xml("a.exe", "", "C:\\", "PC\\Ala", elevated=True)
written = []
def query_schtasks(*args):
    runs.append(args)
    if args[0] == "/query":
        return types.SimpleNamespace(returncode=qrc[0], stdout=qout[0], stderr="")
    if args[0] == "/create":
        written.append(open(args[args.index("/xml") + 1], encoding="utf-16").read())
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")
A._schtasks = query_schtasks
qrc, qout = [0], ["<Task>" + A.LEAST + "</Task>"]
assert A.elevate(r"C:\Py\pythonw.exe", r'"C:\r\receiver_controller.py"') and A.HIGHEST in written[-1] \
    and "receiver_controller.py" in written[-1], "task without privileges -> the same task with the highest ones"
qout[0] = "<Task>" + A.HIGHEST + "</Task>"
n = len(written)
assert not A.elevate("a.exe", "") and len(written) == n, "already elevated: no change"
qrc[0] = 1
assert not A.elevate("a.exe", "") and len(written) == n, "no task (autostart off): creates nothing"
ok("HIDMaestro: autostart task with the highest privileges only when autostart is on")

# ---------- start task as administrator (no UAC) ----------
written.clear(); runs.clear()
assert A.ensure_admin_task(r"C:\Py\pythonw.exe", r'"C:\r\receiver_controller.py"')
assert runs[-1][2] == A.ADMIN_NAME and A.HIGHEST in written[-1] and "<Triggers></Triggers>" in written[-1], "no trigger"
qrc[0], qout[0] = 0, "<Task>" + A.HIGHEST + "</Task>"
assert A.run_admin_task() and runs[-1] == ("/run", "/tn", A.ADMIN_NAME)
qout[0] = "<Task>" + A.LEAST + "</Task>"
assert not A.run_admin_task() and runs[-1][0] == "/query", "task without the highest privileges: does not start (it would loop)"
qrc[0] = 1
assert not A.run_admin_task(), "brak zadania"
ok("DualSenseBridgeAdmin task: created without a trigger, run only with the highest privileges")
print("AUTOSTART OK")
