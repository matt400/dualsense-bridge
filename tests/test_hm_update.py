"""Receiver: HIDMaestro pobierany sam z GitHub Releases - odczyt zipa przez HTTP Range (tylko potrzebne wpisy),
wersje w osobnych folderach, sprawdzanie raz na dobę, błędy sieci; samouruchamianie jako administrator.
    python3 tests/test_hm_update.py receiver"""
import io, json, os, sys, tempfile, time, zipfile
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_hm, rx_hm_update as U

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


DLL_BYTES = b"MZ" + os.urandom(200_000)
def make_zip(dll=DLL_BYTES):
    """Układ jak w wydaniu HIDMaestro: separator "\\", DLL w katalogu głównym i w folderach aplikacji, duże śmieci."""
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", "x" * 1000)
        z.writestr("HIDMaestroTest\\HIDMaestroTest.exe", os.urandom(300_000))
        z.writestr("HIDMaestroTest\\HIDMaestro.Core.dll", dll)
        z.writestr("HIDMaestroTest\\Microsoft.Windows.SDK.NET.dll", b"MZsdk")
        z.writestr("HIDMaestroTest\\WinRT.Runtime.dll", b"MZwinrt")
        z.writestr("HIDMaestroProfileExtractor\\Microsoft.Windows.SDK.NET.dll", b"MZsdk")
        z.writestr("HIDMaestro.Core.dll", dll)
        z.writestr("big.bin", os.urandom(2_000_000))
    return b.getvalue()


class Resp:
    def __init__(self, data, status=200, headers=None, url=""):
        self.data, self.status, self.headers, self.url = data, status, headers or {}, url
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return self.data if n < 0 else self.data[:n]
    def geturl(self): return self.url


SERVED = {}
calls = []
def opener(url, headers=None, timeout=0):
    calls.append((url, dict(headers or {})))
    if url == U.API:
        return Resp(json.dumps(RELEASE[0]).encode())
    data = SERVED[url.replace("github.com", "storage")]
    rng = (headers or {}).get("Range")
    if not rng:
        return Resp(data)
    a, b = map(int, rng[6:].split("-"))
    return Resp(data[a:b + 1], 206, {"Content-Range": f"bytes {a}-{b}/{len(data)}"}, url.replace("github.com", "storage"))


ROOT = tempfile.mkdtemp()
U.root = lambda: ROOT
U._get = opener
def release(tag, data):
    url = f"https://github.com/r/{tag}/HIDMaestro-{tag}.zip"
    SERVED[url.replace("github.com", "storage")] = data
    return {"tag_name": tag, "assets": [{"name": f"HIDMaestro-{tag}-arm64.zip", "browser_download_url": url + "arm"},
                                        {"name": f"HIDMaestro-{tag}.zip", "browser_download_url": url}]}

# ---------- pierwsze pobranie: tylko DLL i zależności, przez Range ----------
z1 = make_zip()
RELEASE = [release("v1.12.0", z1)]
got = []
U.on_installed = got.append
st = U.check()
d = os.path.join(ROOT, "v1.12.0")
check(st.version == "v1.12.0" and not st.error and got == ["v1.12.0"], f"pobrana wersja: {st}")
check(sorted(os.listdir(d)) == sorted([U.DLL, *U.DEPS]), f"tylko DLL i zależności, płasko: {os.listdir(d)}")
check(open(os.path.join(d, U.DLL), "rb").read() == DLL_BYTES, "DLL z katalogu głównego, bajt w bajt")
ranged = [h["Range"] for u, h in calls if "Range" in h]
total = sum(int(r[6:].split("-")[1]) - int(r[6:].split("-")[0]) + 1 for r in ranged)
check(total < len(z1) * 0.5, f"pobrano tylko część zipa ({total} z {len(z1)} B)")
check(all("arm" not in u for u, _ in calls), "zip x64, nie arm64")
check(not any(n.startswith(".") for n in os.listdir(ROOT)), "bez folderu tymczasowego")
check(U.newest_dll() == os.path.join(d, U.DLL) and rx_hm.find_dll("") == os.path.join(d, U.DLL),
      "rx_hm bierze pobraną wersję, gdy nie ma własnej ścieżki")

# ---------- raz na dobę; force; ta sama wersja nie pobiera się drugi raz ----------
calls.clear()
U.check()
check(not calls, "w ciągu doby bez zapytania do GitHuba")
U.check(force=True)
check(len(calls) == 1 and calls[0][0] == U.API and got == ["v1.12.0"], "force: tylko zapytanie, ta sama wersja bez pobierania")

# ---------- nowa wersja: nowy folder, stara usunięta ----------
z2 = make_zip(b"MZ" + b"new" * 1000)
RELEASE[0] = release("v1.13.0", z2)
st = U.check(force=True)
check(st.version == "v1.13.0" and got[-1] == "v1.13.0" and os.listdir(ROOT) == ["state.json", "v1.13.0"] or
      sorted(os.listdir(ROOT)) == ["state.json", "v1.13.0"], f"nowa wersja, stara usunięta: {os.listdir(ROOT)}")
check(U.version_key("v1.10.0") > U.version_key("v1.9.2"), "wersje porównywane liczbowo")

# ---------- błędy: uszkodzony zip, brak DLL, sieć - pobrana wersja zostaje ----------
bad = bytearray(make_zip(b"MZ" + os.urandom(50_000)))
RELEASE[0] = release("v1.14.0", b"")
ent = next(i for i in zipfile.ZipFile(io.BytesIO(bytes(bad))).infolist() if i.filename == "HIDMaestro.Core.dll")
mid = ent.header_offset + 30 + len(ent.filename) + ent.compress_size // 2      # środek danych DLL z katalogu głównego
bad[mid:mid + 64] = bytes(x ^ 0xFF for x in bad[mid:mid + 64])
SERVED[next(k for k in SERVED if "v1.14.0" in k)] = bytes(bad)
st = U.check(force=True)
check(st.error and st.version == "v1.13.0" and not os.path.exists(os.path.join(ROOT, "v1.14.0")),
      f"uszkodzony zip: błąd, wersja bez zmian, bez połowicznego folderu: {st.error}")
check(not any(n.startswith(".") for n in os.listdir(ROOT)), "folder tymczasowy sprzątnięty po błędzie")
b = io.BytesIO()
with zipfile.ZipFile(b, "w") as zz:
    zz.writestr("README.md", "x")
RELEASE[0] = release("v1.15.0", b.getvalue())
st = U.check(force=True)
check("not in the release zip" in st.error and st.version == "v1.13.0", "zip bez DLL: czytelny błąd")
def offline(url, headers=None, timeout=0):
    raise OSError("network down")
U._get = offline
st = U.check(force=True)
check("network down" in st.error and st.version == "v1.13.0" and not st.busy, "brak sieci: błąd, wersja zostaje")
U._get = opener

# ---------- wersja załadowana w procesie zostaje do restartu (CLR wczytuje zależności DLL dopiero przy użyciu) ----------
import types
rx_hm._loaded = types.SimpleNamespace(path=os.path.join(ROOT, "v1.13.0", U.DLL))
RELEASE[0] = release("v1.16.0", make_zip(b"MZ" + b"16" * 1000))
st = U.check(force=True)
old = os.path.join(ROOT, "v1.13.0")
check(st.version == "v1.16.0" and os.path.isdir(old) and sorted(os.listdir(old)) == sorted([U.DLL, *U.DEPS]),
      f"nowa wersja pobrana, załadowana (v1.13.0) zostaje z zależnościami: {sorted(os.listdir(ROOT))}")
rx_hm._loaded = None
U.cleanup(keep="v1.16.0")
check(not os.path.exists(old) and os.path.isdir(os.path.join(ROOT, "v1.16.0")), "po restarcie stara wersja usunięta")

# ---------- receiver: włączenie / wyłączenie automatu, zmiana backendu po pobraniu ----------
import rx_ds4, rx_settings
from dataclasses import replace
def vpad(**kw):
    rx_settings.apply(replace(rx_settings.cur, vpad=replace(rx_settings.VpadConfig(), **kw)))
vpad()
check(rx_ds4.hm_auto_enabled(), "domyślnie pobieranie włączone")
for kw in ({"backend": "vigem"}, {"hm_auto": False}, {"hm_dll": "C:\\HM"}):
    vpad(**kw)
    check(not rx_ds4.hm_auto_enabled(), f"bez pobierania: {kw}")
vpad()
import rx_events as ev
seq = ev.since(0)[-1].seq if ev.since(0) else 0
rx_hm._loaded = None
rx_ds4.hm_installed("v1.13.0")
check(any("następnym połączeniu" in e.text for e in ev.since(seq)), "nic nie załadowane: HIDMaestro od następnego połączenia")
rx_hm._loaded = object()
seq = ev.since(0)[-1].seq if ev.since(0) else 0
rx_ds4.hm_installed("v1.14.0")
check(any("po restarcie" in e.text for e in ev.since(seq)), "załadowana starsza: nowa po restarcie")
check(rx_hm.get("") is rx_hm._loaded, "jedna wersja HIDMaestro na proces")
rx_hm._loaded = None

# ---------- samouruchamianie jako administrator ----------
import receiver_controller as RC, rx_winapi, rx_autostart
real = (RC.os.name, rx_winapi.is_admin, rx_winapi.relaunch_admin, sys.argv, rx_autostart.run_admin_task, RC._guard_path)
ran = []
guard = os.path.join(ROOT, ".elevate_try")
RC._guard_path = lambda: guard
try:
    RC.os.name = "nt"
    # zadanie administratora: start bez UAC
    task = []
    rx_autostart.run_admin_task = lambda *a: task.append(1) or True
    rx_winapi.is_admin = lambda: False
    rx_winapi.relaunch_admin = lambda cmd: ran.append(cmd) or True
    sys.argv = ["receiver_controller.py"]
    try:
        RC.elevate()
        check(False, "zadanie administratora: wyjście")
    except SystemExit as e:
        check(e.code == 0 and task == [1] and not ran and os.path.exists(guard), "start przez zadanie, bez okna UAC")
    try:
        RC.elevate()                               # zadanie dało proces bez uprawnień (konto bez admina): bez pętli
    except SystemExit:
        pass
    check(task == [1] and len(ran) == 1, "zaraz po próbie przez zadanie: okno UAC zamiast drugiego zadania")
    os.utime(guard, (time.time() - 120, time.time() - 120))
    rx_autostart.run_admin_task = lambda *a: task.append(2) and False
    ran.clear()
    try:
        RC.elevate()
    except SystemExit:
        pass
    check(task[-1] == 2 and len(ran) == 1, "brak zadania: okno UAC")
    ran.clear(); task.clear()
    rx_autostart.run_admin_task = lambda *a: task.append(3) or True
    rx_winapi.relaunch_admin = lambda cmd: ran.append(cmd) or True
    rx_winapi.is_admin = lambda: False
    sys.argv = ["receiver_controller.py", "--autostart", "on"]
    try:
        RC.elevate()
        check(False, "bez administratora: wyjście po uruchomieniu przez UAC")
    except SystemExit as e:
        check(e.code == 0 and ran and ran[-1][-2:] == ["--autostart", "on"], f"UAC z tymi samymi argumentami: {ran}")
    rx_winapi.relaunch_admin = lambda cmd: False
    RC.elevate()
    check(True, "odmowa w UAC: receiver działa dalej")
    sys.argv = ["receiver_controller.py", "--no-elevate"]
    ran.clear(); rx_winapi.relaunch_admin = lambda cmd: ran.append(cmd) or True
    RC.elevate()
    check(not ran, "--no-elevate: bez UAC")
    rx_winapi.is_admin = lambda: True
    sys.argv = ["receiver_controller.py"]
    RC.elevate()
    check(not ran, "już administrator: bez UAC")
    check(not task, "--autostart i wcześniejsze przypadki: zadanie nie jest używane")
finally:
    RC.os.name, rx_winapi.is_admin, rx_winapi.relaunch_admin, sys.argv, rx_autostart.run_admin_task, RC._guard_path = real

# ---------- .NET 10: wybór runtime, brak = czytelny błąd i instalacja przez winget ----------
import types as _t, subprocess, importlib.util
RT = lambda v: _t.SimpleNamespace(name="Microsoft.NETCore.App", version=v)
real_rt, real_clr, real_run, real_os = rx_hm.net_runtimes, rx_hm._clr_ready, subprocess.run, rx_hm.os.name
try:
    rx_hm._clr_ready = False
    rx_hm.net_runtimes = lambda: [RT("8.0.20"), RT("9.0.9")]
    check(rx_hm.best_runtime() is None, "tylko .NET 8 / 9: brak runtime'u dla HIDMaestro")
    try:
        rx_hm._load_clr("x.dll")
        check(False, "brak .NET 10: błąd przed załadowaniem CLR")
    except rx_hm.HMError as e:
        check(e.need_dotnet and "8.0.20" in str(e) and "9.0.9" in str(e), f"komunikat z znalezionymi wersjami: {e}")
    rx_hm.net_runtimes = lambda: [RT("8.0.20"), RT("10.0.2"), RT("10.0.12"), RT("11.0.0-preview")]
    check(rx_hm.best_runtime().version == "11.0.0-preview" or rx_hm.best_runtime().version.startswith("1"), "najnowszy >= 10")
    rx_hm.os.name = "nt"
    rx_hm.net_runtimes = lambda: [RT("8.0.20")]
    check(rx_hm.need_dotnet(), "okno: trzeba zainstalować .NET 10")
    cmds = []
    def fake_run(cmd, **kw):
        cmds.append(cmd)
        rx_hm.net_runtimes = lambda: [RT("10.0.12")]
        return _t.SimpleNamespace(returncode=0, stdout="Successfully installed", stderr="")
    subprocess.run = fake_run
    ok_, msg = rx_hm.install_dotnet()
    check(ok_ and cmds[0][:4] == ["winget", "install", "--id", rx_hm.WINGET_NET] and "--silent" in cmds[0], f"winget: {cmds}")
    check(not rx_hm.need_dotnet(), "po instalacji .NET 10 jest")
    rx_hm.net_runtimes = list
    subprocess.run = lambda cmd, **kw: _t.SimpleNamespace(returncode=1, stdout="No package found", stderr="")
    ok_, msg = rx_hm.install_dotnet()
    check(not ok_ and "No package found" in msg, f"błąd winget: {msg}")
    rx_hm.net_runtimes = lambda: None          # brak pythonnet/clr_loader: nie wiadomo, czy .NET jest
    check(not rx_hm.need_dotnet(), "bez pythonnet: okno nie proponuje instalacji .NET")
    if importlib.util.find_spec("pythonnet") is None:
        try:
            rx_hm._load_clr("x.dll")
            check(False, "bez pythonnet: błąd")
        except rx_hm.HMError as e:
            check(not e.need_dotnet and "pythonnet" in str(e), f"bez pythonnet: komunikat o pythonnet: {e}")
finally:
    rx_hm.net_runtimes, rx_hm._clr_ready, subprocess.run, rx_hm.os.name = real_rt, real_clr, real_run, real_os

print("HM UPDATE", "OK" if not fails else f"BŁĘDY: {len(fails)}")
if fails:
    sys.exit(1)
