"""Autostart (rx_autostart): zadanie Harmonogramu przy logowaniu zamiast wpisu Run (opóźnionego przez Windows),
migracja starego wpisu, wpis Run jako zapas przy błędzie schtasks, wyłączanie."""
import os, sys, types
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_autostart as A
ok = lambda msg: print("OK  ", msg)
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

# ---------- XML zadania ----------
xml = A.task_xml(r"C:\Py\pythonw.exe", r'"C:\a & b\receiver_controller.py" --config x.toml', r"C:\a & b", r"PC\Ala")
root = ET.fromstring(xml.split("\n", 1)[1])                       # bez deklaracji UTF-16 (to tekst, nie bajty)
get = lambda path: root.find(path, NS).text
assert get("t:Triggers/t:LogonTrigger/t:UserId") == r"PC\Ala"
assert root.find("t:Triggers/t:LogonTrigger/t:Delay", NS) is None, "bez opóźnienia"
assert get("t:Settings/t:ExecutionTimeLimit") == "PT0S", "bez limitu 72 h"
assert get("t:Settings/t:DisallowStartIfOnBatteries") == "false" and get("t:Settings/t:StopIfGoingOnBatteries") == "false"
assert get("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
assert get("t:Actions/t:Exec/t:Command") == r"C:\Py\pythonw.exe"
assert get("t:Actions/t:Exec/t:Arguments") == r'"C:\a & b\receiver_controller.py" --config x.toml', "znaki XML uciekane"
assert get("t:Actions/t:Exec/t:WorkingDirectory") == r"C:\a & b"
ok("XML: przy logowaniu, bez opóźnienia i limitu czasu, także na baterii, poprawnie uciekane ścieżki")

assert A.split_command(r'"C:\Py 3\pythonw.exe" "C:\r\receiver_controller.py" --pads 2') == \
    (r"C:\Py 3\pythonw.exe", r'"C:\r\receiver_controller.py" --pads 2')
assert A.split_command(r"C:\Py\pythonw.exe C:\r\rc.py") == (r"C:\Py\pythonw.exe", r"C:\r\rc.py")
ok("komenda z wpisu Run -> program + argumenty (z cudzysłowami i bez)")

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
assert not A.migrate() and not runs, "bez starego wpisu nic się nie dzieje"
ok("migracja: stary wpis Run -> zadanie, wpis usunięty; drugi raz nic")

rc[0] = 1
reg[A.NAME] = r'"C:\Py\pythonw.exe" "C:\r\receiver_controller.py"'
assert not A.migrate() and A.NAME in reg, "schtasks nie działa -> wpis Run zostaje (autostart dalej działa)"
reg.clear()
msg = A.enable(r"C:\Py\pythonw.exe", r'"C:\r\receiver_controller.py"')
assert "Run key" in msg and reg[A.NAME].startswith(r"C:\Py\pythonw.exe"), "enable: zapas we wpisie Run"
rc[0] = 0
msg = A.enable(r"C:\Py\pythonw.exe", r'"C:\r\receiver_controller.py"')
assert "Task Scheduler" in msg and A.NAME not in reg
assert A.disable() == "Autostart disabled." and runs[-1][:3] == ("/delete", "/tn", A.NAME)
ok("błąd schtasks: wpis Run jako zapas; enable usuwa stary wpis; disable usuwa zadanie")

# ---------- HIDMaestro: zadanie z najwyższymi uprawnieniami ----------
assert A.LEAST in xml and A.HIGHEST not in xml, "domyślnie zwykłe uprawnienia"
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
    and "receiver_controller.py" in written[-1], "zadanie bez uprawnień -> to samo zadanie z najwyższymi"
qout[0] = "<Task>" + A.HIGHEST + "</Task>"
n = len(written)
assert not A.elevate("a.exe", "") and len(written) == n, "już podniesione: bez zmian"
qrc[0] = 1
assert not A.elevate("a.exe", "") and len(written) == n, "brak zadania (autostart wyłączony): nic nie tworzy"
ok("HIDMaestro: zadanie autostartu z najwyższymi uprawnieniami tylko, gdy autostart jest włączony")

# ---------- zadanie startu jako administrator (bez UAC) ----------
written.clear(); runs.clear()
assert A.ensure_admin_task(r"C:\Py\pythonw.exe", r'"C:\r\receiver_controller.py"')
assert runs[-1][2] == A.ADMIN_NAME and A.HIGHEST in written[-1] and "<Triggers></Triggers>" in written[-1], "bez wyzwalacza"
qrc[0], qout[0] = 0, "<Task>" + A.HIGHEST + "</Task>"
assert A.run_admin_task() and runs[-1] == ("/run", "/tn", A.ADMIN_NAME)
qout[0] = "<Task>" + A.LEAST + "</Task>"
assert not A.run_admin_task() and runs[-1][0] == "/query", "zadanie bez najwyższych uprawnień: nie startuje (byłaby pętla)"
qrc[0] = 1
assert not A.run_admin_task(), "brak zadania"
ok("zadanie DualSenseBridgeAdmin: tworzone bez wyzwalacza, uruchamiane tylko z najwyższymi uprawnieniami")
print("AUTOSTART OK")
