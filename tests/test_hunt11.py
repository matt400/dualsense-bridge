"""Receiver: regresje z jedenastego przeglądu - pobieranie HIDMaestro jednym zapytaniem na plik (zamiast setek po 64 KiB),
uruchamianie jako administrator (znacznik w folderze tymczasowym, druga kopia bez okna UAC, blokada administratora
widziana z procesu bez uprawnień), zadania autostartu z własnymi argumentami.
    python3 tests/test_hunt11.py receiver"""
import io, json, os, sys, tempfile, time, types, zipfile
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_autostart as A
import rx_config
import rx_hm_update as U
import rx_winapi

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


# ---------- 1. HIDMaestro: każdy potrzebny plik zipa jednym zapytaniem Range ----------
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
    if ignore_range[0] and b - a > 100_000:    # serwer / proxy, który na pobranie wpisu oddaje cały plik zamiast zakresu
        return Resp(ZIP, 200, {}, url)
    return Resp(ZIP[a:b + 1], 206, {"Content-Range": f"bytes {a}-{b}/{len(ZIP)}"}, url)


ROOT = tempfile.mkdtemp()
U.root = lambda: ROOT
folder, fetched = U.install("v2.0.0", "https://example/zip", opener)
names = sorted(os.listdir(folder))
check(names == sorted([U.DLL, *U.DEPS]), f"pliki: {names}")
check(open(os.path.join(folder, U.DLL), "rb").read() == zipfile.ZipFile(io.BytesIO(ZIP)).read("HIDMaestro.Core.dll"),
      "DLL bajt w bajt")
# 3 pliki po ~0.3-1.5 MB: wcześniej ~45 zapytań po 64 KiB (w prawdziwym zipie ~900), teraz zapytanie na plik + katalog zipa
check(len(ranges) <= 8, f"zapytań HTTP: {len(ranges)} (jedno na plik, nie na 64 KiB)")
check(fetched < len(ZIP) * 0.6, f"bez big.bin: pobrano {fetched} z {len(ZIP)} B")

ranges.clear()
ignore_range[0] = True
try:
    U.install("v2.0.1", "https://example/zip", opener)
    check(False, "serwer bez Range: błąd")
except OSError as e:
    check("Range" in str(e) and not os.path.exists(os.path.join(ROOT, "v2.0.1")),
          f"odpowiedź 200 na zapytanie z Range = błąd, a nie bajty z początku pliku jako wpis: {e}")
ignore_range[0] = False

U.status = U.Status(version="v2.0.0")
real_load, real_latest = U._load_state, U.latest_release
U._load_state = lambda: (time.time() + 10 * 24 * 3600, "v2.0.0")   # zegar cofnięty: ostatnie sprawdzenie "w przyszłości"
asked = []
U.latest_release = lambda: asked.append(1) or ("v2.0.0", "https://example/zip")
try:
    U.check()
    check(asked, "zegar cofnięty: sprawdzenie nie jest wstrzymane do daty z przyszłości")
finally:
    U._load_state, U.latest_release = real_load, real_latest


# ---------- 2. jedna instancja: blokada receivera-administratora widziana z procesu bez uprawnień ----------
class Fn:                                                         # funkcja DLL: wywołanie + argtypes / restype
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
        check(False, "blokada administratora: wyjście")
    except SystemExit as e:
        check(e.code == 1, "CreateMutex = NULL + ERROR_ACCESS_DENIED = receiver już działa")
    check(rx_winapi.instance_running(), "OpenMutex bez dostępu = blokada istnieje")
    rx_winapi.ctypes.get_last_error = lambda: 2            # ERROR_FILE_NOT_FOUND
    check(not rx_winapi.instance_running(), "brak blokady = nic nie działa")
    rx_winapi.ctypes.WinDLL = lambda *a, **k: FakeK(open_=1234)
    check(rx_winapi.instance_running(), "blokada otwarta = działa")
finally:
    for name, v in (("WinDLL", real_dll), ("get_last_error", real_err)):
        if v is None:
            delattr(rx_winapi.ctypes, name)
        else:
            setattr(rx_winapi.ctypes, name, v)


# ---------- 3. uruchamianie jako administrator ----------
import receiver_controller as RC
check(RC._guard_path().startswith(tempfile.gettempdir()), "znacznik w folderze tymczasowym (folder receivera bywa tylko do odczytu)")
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
    check(not uac and not task, "receiver już działa: druga kopia bez okna UAC (i tak zaraz wyjdzie)")
    rx_winapi.instance_running = lambda: False
    try:
        RC.elevate()
    except SystemExit:
        pass
    check(task and "--pads 1" in task[-1] and not uac, f"zadanie dostaje argumenty tego startu do porównania: {task}")
    check(os.path.exists(guard), "znacznik próby zapisany")
    rx_winapi.is_admin = lambda: True
    RC.elevate()
    check(not os.path.exists(guard), "proces z uprawnieniami: znacznik usunięty - następny start znów bez UAC")
finally:
    (RC.os.name, rx_winapi.is_admin, rx_winapi.relaunch_admin, rx_winapi.instance_running, sys.argv, A.run_admin_task,
     RC._guard_path) = real


# ---------- 4. zadania autostartu: własne argumenty ----------
script = os.path.join(ROOT, "receiver_controller.py")
open(script, "w").close()
task_args = rx_config.autostart_command(["--config", os.path.join(ROOT, "my.toml")])[1].replace(
    os.path.abspath(sys.argv[0]), script)
task_xml = A.task_xml(r"C:\Py\pythonw.exe", task_args, ROOT, r"PC\Ala")
check(A.task_action(task_xml) == (r"C:\Py\pythonw.exe", task_args), f"komenda z XML zadania: {A.task_action(task_xml)}")
check(A.task_action(task_xml.replace(script, script + "x")) is None, "skrypt nie istnieje (np. przekłamane znaki): None")
check(A.task_action("<Task/>") is None, "XML bez akcji: None")

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

A._schtasks = schtasks(task_xml)                                  # autostart z --config, zwykłe uprawnienia
check(A.elevate(r"C:\Py\pythonw.exe", f'"{script}"') and A.HIGHEST in written[-1]
      and A.task_action(written[-1]) == (r"C:\Py\pythonw.exe", task_args),
      "podniesienie autostartu zostawia jego komendę (--config), a nie argumenty ręcznego startu")
A._schtasks = schtasks(task_xml.replace(script, script + "x"))
check(A.elevate(r"C:\Py\pythonw.exe", f'"{script}"') and f'"{script}"' in written[-1],
      "komendy zadania nie da się odczytać: jak dotąd - komenda tego procesu")

admin_xml = A.task_xml(r"C:\Py\pythonw.exe", task_args, ROOT, r"PC\Ala", elevated=True, logon=False)
A._schtasks = schtasks(admin_xml)
runs.clear()
check(A.run_admin_task(task_args) and runs[-1][0] == "/run", "te same argumenty: start przez zadanie")
runs.clear()
check(not A.run_admin_task(f'"{script}" --pads 1') and runs[-1][0] == "/query",
      "inne argumenty: bez zadania (okno UAC z argumentami tego startu)")
check(A.run_admin_task() and runs[-1][0] == "/run", "bez argumentów do porównania: jak dotąd")

print("HUNT11", "OK" if not fails else f"BŁĘDY: {len(fails)}")
if fails:
    sys.exit(1)
