"""Receiver: regressions from the eleventh review - HIDMaestro download with one request per file (instead of hundreds of 64 KiB),
running as administrator (a marker in the temp folder, a second copy without the UAC prompt, the administrator lock
seen from an unprivileged process), autostart tasks with their own arguments.
    python3 tests/test_hm_download_elevation.py receiver"""
import hashlib, io, json, os, sys, tempfile, time, types, zipfile
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_autostart as A
import rx_config
import rx_hm_update as U
import rx_winapi

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- HIDMaestro: every needed zip file with one Range request ----------
def make_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", "x" * 1000)
        z.writestr("HIDMaestroTest\\Microsoft.Windows.SDK.NET.dll", b"MZsdk" + os.urandom(900_000))
        z.writestr("HIDMaestroTest\\WinRT.Runtime.dll", b"MZwinrt" + os.urandom(300_000))
        z.writestr("HIDMaestro.Core.dll", b"MZ" + os.urandom(1_500_000))
        z.writestr("big.bin", os.urandom(3_000_000))
    return b.getvalue()


class Resp:
    def __init__(self, data, status, headers=None, url=""):
        self.data, self.status, self.headers, self.url = data, status, headers or {}, url
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return self.data if n < 0 else self.data[:n]
    def geturl(self): return self.url


ZIP = make_zip()
ranges = []
ignore_range = [False]
def opener(url, headers=None, timeout=0):
    rng = (headers or {}).get("Range")
    ranges.append(rng)
    a, b = map(int, rng[6:].split("-"))
    if ignore_range[0] and b - a > 100_000:    # a server / proxy that answers an entry download with the whole file instead of the range
        return Resp(ZIP, 200, {}, url)
    return Resp(ZIP[a:b + 1], 206, {"Content-Range": f"bytes {a}-{b}/{len(ZIP)}"}, url)


ROOT = tempfile.mkdtemp()
U.root = lambda: ROOT
_zip = zipfile.ZipFile(io.BytesIO(ZIP))                           # the install checks the files against the pin: pin this zip
U.PINNED_SHA256 = {U.DLL: hashlib.sha256(_zip.read("HIDMaestro.Core.dll")).hexdigest(),
                   U.DEPS[0]: hashlib.sha256(_zip.read("HIDMaestroTest\\Microsoft.Windows.SDK.NET.dll")).hexdigest(),
                   U.DEPS[1]: hashlib.sha256(_zip.read("HIDMaestroTest\\WinRT.Runtime.dll")).hexdigest()}
folder, fetched = U.install("v2.0.0", "https://example/zip", opener)
names = sorted(os.listdir(folder))
check(names == sorted([U.DLL, *U.DEPS]), f"pliki: {names}")
check(open(os.path.join(folder, U.DLL), "rb").read() == zipfile.ZipFile(io.BytesIO(ZIP)).read("HIDMaestro.Core.dll"),
      "DLL bajt w bajt")
# 3 files of ~0.3-1.5 MB: ~45 requests of 64 KiB before (~900 in the real zip), now a request per file + the zip directory
check(len(ranges) <= 8, f"HTTP requests: {len(ranges)} (one per file, not per 64 KiB)")
check(fetched < len(ZIP) * 0.6, f"without big.bin: downloaded {fetched} of {len(ZIP)} B")

ranges.clear()
ignore_range[0] = True
try:
    U.install("v2.0.1", "https://example/zip", opener)
    check(False, "server without Range: error")
except OSError as e:
    check("Range" in str(e) and not os.path.exists(os.path.join(ROOT, "v2.0.1")),
          f"a 200 reply to a Range request = an error, not the start of the file as the entry: {e}")
ignore_range[0] = False


# ---------- single instance: the lock of an administrator receiver seen from an unprivileged process ----------
class Fn:                                                         # DLL function: call + argtypes / restype
    def __init__(self, v): self.v = v
    def __call__(self, *a): return self.v

class FakeK:
    def __init__(self, create=0, open_=0):
        self.CreateMutexW, self.OpenMutexW = Fn(create), Fn(open_)

real_dll, real_err = getattr(rx_winapi.ctypes, "WinDLL", None), getattr(rx_winapi.ctypes, "get_last_error", None)
try:
    rx_winapi.ctypes.get_last_error = lambda: 5            # ERROR_ACCESS_DENIED
    rx_winapi.ctypes.WinDLL = lambda *a, **k: FakeK(create=0)
    try:
        rx_winapi.ensure_single_instance()
        check(False, "administrator lock: exit")
    except SystemExit as e:
        check(e.code == 1, "CreateMutex = NULL + ERROR_ACCESS_DENIED = the receiver is already running")
    check(rx_winapi.instance_running(), "OpenMutex without access = the lock exists")
    rx_winapi.ctypes.get_last_error = lambda: 2            # ERROR_FILE_NOT_FOUND
    check(not rx_winapi.instance_running(), "no lock = nothing is running")
    rx_winapi.ctypes.WinDLL = lambda *a, **k: FakeK(open_=1234)
    check(rx_winapi.instance_running(), "lock opened = running")
finally:
    for name, v in (("WinDLL", real_dll), ("get_last_error", real_err)):
        if v is None:
            delattr(rx_winapi.ctypes, name)
        else:
            setattr(rx_winapi.ctypes, name, v)


# ---------- running as administrator ----------
import receiver_controller as RC
check(RC._guard_path().startswith(tempfile.gettempdir()), "marker in the temp folder (the receiver folder can be read-only)")
real = (RC.os.name, rx_winapi.is_admin, rx_winapi.relaunch_admin, rx_winapi.instance_running, sys.argv, A.run_admin_task,
        RC._guard_path)
guard = os.path.join(ROOT, "guard")
RC._guard_path = lambda: guard
uac, task = [], []
try:
    RC.os.name = "nt"
    sys.argv = ["receiver_controller.py", "--pads", "1"]
    rx_winapi.is_admin = lambda: False
    rx_winapi.relaunch_admin = lambda cmd: uac.append(cmd) or True
    A.run_admin_task = lambda args=None: task.append(args) or True
    rx_winapi.instance_running = lambda: True
    RC.elevate()
    check(not uac and not task, "receiver already running: the second copy without the UAC prompt (it exits right away anyway)")
    rx_winapi.instance_running = lambda: False
    try:
        RC.elevate()
    except SystemExit:
        pass
    check(task and "--pads 1" in task[-1] and not uac, f"the task gets this start's arguments to compare: {task}")
    check(os.path.exists(guard), "attempt marker written")
    rx_winapi.is_admin = lambda: True
    RC.elevate()
    check(not os.path.exists(guard), "privileged process: marker removed - the next start is again without UAC")
finally:
    (RC.os.name, rx_winapi.is_admin, rx_winapi.relaunch_admin, rx_winapi.instance_running, sys.argv, A.run_admin_task,
     RC._guard_path) = real


# ---------- autostart tasks: their own arguments ----------
script = os.path.join(ROOT, "receiver_controller.py")
open(script, "w").close()
task_args = rx_config.autostart_command(["--config", os.path.join(ROOT, "my.toml")])[1].replace(
    os.path.abspath(sys.argv[0]), script)
task_xml = A.task_xml(r"C:\Py\pythonw.exe", task_args, ROOT, r"PC\Ala")
check(A.task_action(task_xml) == (r"C:\Py\pythonw.exe", task_args), f"komenda z XML zadania: {A.task_action(task_xml)}")
check(A.task_action(task_xml.replace(script, script + "x")) is None, "the script does not exist (e.g. mangled characters): None")
check(A.task_action("<Task/>") is None, "XML without an action: None")

written, runs = [], []
def schtasks(xml_):
    def run(*args):
        runs.append(args)
        if args[0] == "/query":
            return types.SimpleNamespace(returncode=0, stdout=xml_, stderr="")
        if args[0] == "/create":
            written.append(open(args[args.index("/xml") + 1], encoding="utf-16").read())
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return run

A._schtasks = schtasks(task_xml)                                  # autostart with --config, normal privileges
check(A.elevate(r"C:\Py\pythonw.exe", f'"{script}"') and A.HIGHEST in written[-1]
      and A.task_action(written[-1]) == (r"C:\Py\pythonw.exe", task_args),
      "elevating the autostart keeps its command (--config), not the arguments of a manual start")
A._schtasks = schtasks(task_xml.replace(script, script + "x"))
check(A.elevate(r"C:\Py\pythonw.exe", f'"{script}"') and f'"{script}"' in written[-1],
      "the task command cannot be read: as before - this process's command")

admin_xml = A.task_xml(r"C:\Py\pythonw.exe", task_args, ROOT, r"PC\Ala", elevated=True, logon=False)
A._schtasks = schtasks(admin_xml)
runs.clear()
check(A.run_admin_task(task_args) and runs[-1][0] == "/run", "the same arguments: start through the task")
runs.clear()
check(not A.run_admin_task(f'"{script}" --pads 1') and runs[-1][0] == "/query",
      "other arguments: no task (UAC prompt with this start's arguments)")
check(A.run_admin_task() and runs[-1][0] == "/run", "no arguments to compare: as before")

print("HUNT11", "OK" if not fails else f"FAILURES: {len(fails)}")
if fails:
    sys.exit(1)
