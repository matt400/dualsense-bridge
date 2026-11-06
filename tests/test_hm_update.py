"""Receiver: HIDMaestro downloaded by itself from GitHub Releases - reading the zip over HTTP Range (only the needed entries),
versions in separate folders, a check once a day, network errors; self-elevation to administrator.
    python3 tests/test_hm_update.py receiver"""
import io, json, os, sys, tempfile, time, zipfile
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_hm, rx_hm_update as U

REAL_PIN = (U.PINNED_TAG, dict(U.PINNED_SHA256))
U.PINNED_TAG = ""                      # the sections below follow the newest release (through the API); the pinned version has its own section

from chk import check, fails, ok                              # noqa: E402  (shared helper, tests/chk.py)


DLL_BYTES = b"MZ" + os.urandom(200_000)
def make_zip(dll=DLL_BYTES):
    """Layout as in a HIDMaestro release: "\\" separator, the DLL in the root and in the app folders, big junk."""
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
    return {"tag_name": tag, "published_at": "2020-01-01T00:00:00Z", "assets": [{"name": f"HIDMaestro-{tag}-arm64.zip", "browser_download_url": url + "arm"},
                                        {"name": f"HIDMaestro-{tag}.zip", "browser_download_url": url}]}

# ---------- first download: only the DLL and dependencies, through Range ----------
z1 = make_zip()
RELEASE = [release("v1.12.0", z1)]
got = []
U.on_installed = got.append
st = U.check()
d = os.path.join(ROOT, "v1.12.0")
check(st.version == "v1.12.0" and not st.error and got == ["v1.12.0"], f"downloaded version: {st}")
check(sorted(os.listdir(d)) == sorted([U.DLL, *U.DEPS]), f"only the DLL and dependencies, flat: {os.listdir(d)}")
check(open(os.path.join(d, U.DLL), "rb").read() == DLL_BYTES, "the root DLL, byte for byte")
ranged = [h["Range"] for u, h in calls if "Range" in h]
total = sum(int(r[6:].split("-")[1]) - int(r[6:].split("-")[0]) + 1 for r in ranged)
check(total < len(z1) * 0.5, f"only part of the zip downloaded ({total} of {len(z1)} B)")
check(all("arm" not in u for u, _ in calls), "x64 zip, not arm64")
check(not any(n.startswith(".") for n in os.listdir(ROOT)), "no temp folder")
check(U.newest_dll() == os.path.join(d, U.DLL) and rx_hm.find_dll("") == os.path.join(d, U.DLL),
      "rx_hm takes the downloaded version when it has no path of its own")

# ---------- once a day; force; the same version is not downloaded twice ----------
calls.clear()
U.check()
check(not calls, "within a day no request to GitHub")
U.check(force=True)
check(len(calls) == 1 and calls[0][0] == U.API and got == ["v1.12.0"], "force: only the request, the same version not downloaded")

# ---------- new version: new folder, the old one removed ----------
z2 = make_zip(b"MZ" + b"new" * 1000)
RELEASE[0] = release("v1.13.0", z2)
st = U.check(force=True)
check(st.version == "v1.13.0" and got[-1] == "v1.13.0" and os.listdir(ROOT) == ["state.json", "v1.13.0"] or
      sorted(os.listdir(ROOT)) == ["state.json", "v1.13.0"], f"new version, the old one removed: {os.listdir(ROOT)}")
check(U.version_key("v1.10.0") > U.version_key("v1.9.2"), "versions compared numerically")

# ---------- errors: damaged zip, no DLL, network - the downloaded version stays ----------
bad = bytearray(make_zip(b"MZ" + os.urandom(50_000)))
RELEASE[0] = release("v1.14.0", b"")
ent = next(i for i in zipfile.ZipFile(io.BytesIO(bytes(bad))).infolist() if i.filename == "HIDMaestro.Core.dll")
mid = ent.header_offset + 30 + len(ent.filename) + ent.compress_size // 2      # middle of the root DLL data
bad[mid:mid + 64] = bytes(x ^ 0xFF for x in bad[mid:mid + 64])
SERVED[next(k for k in SERVED if "v1.14.0" in k)] = bytes(bad)
st = U.check(force=True)
check(st.error and st.version == "v1.13.0" and not os.path.exists(os.path.join(ROOT, "v1.14.0")),
      f"damaged zip: error, version unchanged, no half-made folder: {st.error}")
check(not any(n.startswith(".") for n in os.listdir(ROOT)), "temp folder cleaned up after the error")
b = io.BytesIO()
with zipfile.ZipFile(b, "w") as zz:
    zz.writestr("README.md", "x")
RELEASE[0] = release("v1.15.0", b.getvalue())
st = U.check(force=True)
check("not in the release zip" in st.error and st.version == "v1.13.0", "zip without the DLL: a clear error")
def offline(url, headers=None, timeout=0):
    raise OSError("network down")
U._get = offline
st = U.check(force=True)
check("network down" in st.error and st.version == "v1.13.0" and not st.busy, "no network: error, the version stays")
U._get = opener

# ---------- the version loaded in the process stays until a restart (the CLR loads DLL dependencies only on use) ----------
import types
rx_hm._loaded = types.SimpleNamespace(path=os.path.join(ROOT, "v1.13.0", U.DLL))
RELEASE[0] = release("v1.16.0", make_zip(b"MZ" + b"16" * 1000))
st = U.check(force=True)
old = os.path.join(ROOT, "v1.13.0")
check(st.version == "v1.16.0" and os.path.isdir(old) and sorted(os.listdir(old)) == sorted([U.DLL, *U.DEPS]),
      f"new version downloaded, the loaded one (v1.13.0) stays with its dependencies: {sorted(os.listdir(ROOT))}")
rx_hm._loaded = None
U.cleanup(keep="v1.16.0")
check(not os.path.exists(old) and os.path.isdir(os.path.join(ROOT, "v1.16.0")), "after a restart the old version removed")

# ---------- pinned version: fixed link, no API, no waiting, SHA-256 of the files, no downgrade ----------
import hashlib
sha = lambda b: hashlib.sha256(b).hexdigest()
check(U.version_key(REAL_PIN[0]) is not None and set(REAL_PIN[1]) == {U.DLL, *U.DEPS}
      and all(len(h) == 64 and int(h, 16) >= 0 for h in REAL_PIN[1].values()), f"pin in the code: tag and SHA-256 of the DLL + dependencies: {REAL_PIN[0]}")
check(U.zip_url("v2.1.0") == "https://github.com/hifihedgehog/HIDMaestro/releases/download/v2.1.0/HIDMaestro-v2.1.0.zip",
      "fixed link to the x64 zip")
real_root = U.root
ROOT_PIN = tempfile.mkdtemp()
U.root = lambda: ROOT_PIN
def pin(tag, dll):
    """Pin version `tag`, whose zip (make_zip) has DLL `dll`; the hashes match its content."""
    SERVED[U.zip_url(tag).replace("github.com", "storage")] = make_zip(dll)
    U.PINNED_TAG = tag
    U.PINNED_SHA256 = {U.DLL: sha(dll), U.DEPS[0]: sha(b"MZsdk"), U.DEPS[1]: sha(b"MZwinrt")}
DLL_P1, DLL_P2 = b"MZ" + b"pin1" * 500, b"MZ" + b"pin2" * 500
pin("v1.20.0", DLL_P1)
RELEASE[0] = release("v9.9.9", z1)                           # a newer release on GitHub has no effect
calls.clear()
st = U.check(force=True)
check(st.version == "v1.20.0" and not st.error and st.latest == "v1.20.0", f"the pinned version installed, not the newest from GitHub: {st}")
check(all(u != U.API for u, _ in calls) and any(u == U.zip_url("v1.20.0") for u, _ in calls), "no API request, download from the fixed link")
check(open(os.path.join(ROOT_PIN, "v1.20.0", U.DLL), "rb").read() == DLL_P1, "the pinned version's DLL")
pin("v1.21.0", DLL_P2)                                       # a receiver update moved the pin; the last check a moment ago
st = U.check()
check(st.version == "v1.21.0" and not st.error, f"moved pin: installed at once (no waiting and no daily interval): {st}")
check(sorted(os.listdir(ROOT_PIN)) == ["state.json", "v1.21.0"], f"the old version removed: {os.listdir(ROOT_PIN)}")
U.PINNED_SHA256 = {**U.PINNED_SHA256, U.DLL: sha(b"MZ" + b"good" * 500)}      # zip replaced: a different DLL than the pin
SERVED[U.zip_url("v1.22.0").replace("github.com", "storage")] = make_zip(b"MZ" + b"evil" * 500)
U.PINNED_TAG = "v1.22.0"
st = U.check(force=True)
check("does not match the pinned SHA-256" in st.error and st.version == "v1.21.0" and not os.path.exists(os.path.join(ROOT_PIN, "v1.22.0")),
      f"replaced file: refusal, version unchanged: {st.error}")
check(not any(n.startswith(".") for n in os.listdir(ROOT_PIN)), "after a refusal the temp folder cleaned up")
U.verify(os.path.join(ROOT_PIN, "v1.21.0"), "v1.0.1")
ok("a release other than the pinned one is not checked (the 'newest' mode)")
calls.clear()
U.PINNED_TAG = "v1.0.0"                                      # pin older than the downloaded version: no downgrade
st = U.check(force=True)
check(st.version == "v1.21.0" and not any(u.endswith(".zip") for u, _ in calls), "pin older than the downloaded version: no download")
U.PINNED_TAG, U.PINNED_SHA256, U.root = "", REAL_PIN[1], real_root

# ---------- receiver: turning auto-download on / off, backend change after the download ----------
import rx_ds4, rx_settings
from dataclasses import replace
def vpad(**kw):
    rx_settings.apply(replace(rx_settings.cur, vpad=replace(rx_settings.VpadConfig(), **kw)))
vpad()
check(rx_ds4.hm_auto_enabled(), "download on by default")
for kw in ({"backend": "vigem"}, {"hm_auto": False}, {"hm_dll": "C:\\HM"}):
    vpad(**kw)
    check(not rx_ds4.hm_auto_enabled(), f"no download: {kw}")
vpad()
import rx_events as ev
seq = ev.since(0)[-1].seq if ev.since(0) else 0
rx_hm._loaded = None
rx_ds4.hm_installed("v1.13.0")
check(any("następnym połączeniu" in e.text for e in ev.since(seq)), "nothing loaded: HIDMaestro from the next connection")
rx_hm._loaded = object()
seq = ev.since(0)[-1].seq if ev.since(0) else 0
rx_ds4.hm_installed("v1.14.0")
check(any("po restarcie" in e.text for e in ev.since(seq)), "an older one loaded: the new one after a restart")
check(rx_hm.get("") is rx_hm._loaded, "one HIDMaestro version per process")
rx_hm._loaded = None

# ---------- self-elevation to administrator ----------
import receiver_controller as RC, rx_winapi, rx_autostart
real = (RC.os.name, rx_winapi.is_admin, rx_winapi.relaunch_admin, sys.argv, rx_autostart.run_admin_task, RC._guard_path)
ran = []
guard = os.path.join(ROOT, ".elevate_try")
RC._guard_path = lambda: guard
try:
    RC.os.name = "nt"
    # administrator task: start without UAC
    task = []
    rx_autostart.run_admin_task = lambda *a: task.append(1) or True
    rx_winapi.is_admin = lambda: False
    rx_winapi.relaunch_admin = lambda cmd: ran.append(cmd) or True
    sys.argv = ["receiver_controller.py"]
    try:
        RC.elevate()
        check(False, "administrator task: exit")
    except SystemExit as e:
        check(e.code == 0 and task == [1] and not ran and os.path.exists(guard), "start through the task, no UAC prompt")
    try:
        RC.elevate()                               # the task gave an unprivileged process (account without admin): no loop
    except SystemExit:
        pass
    check(task == [1] and len(ran) == 1, "right after an attempt through the task: UAC prompt instead of a second task")
    os.utime(guard, (time.time() - 120, time.time() - 120))
    rx_autostart.run_admin_task = lambda *a: task.append(2) and False
    ran.clear()
    try:
        RC.elevate()
    except SystemExit:
        pass
    check(task[-1] == 2 and len(ran) == 1, "no task: UAC prompt")
    ran.clear(); task.clear()
    rx_autostart.run_admin_task = lambda *a: task.append(3) or True
    rx_winapi.relaunch_admin = lambda cmd: ran.append(cmd) or True
    rx_winapi.is_admin = lambda: False
    sys.argv = ["receiver_controller.py", "--autostart", "on"]
    try:
        RC.elevate()
        check(False, "no administrator: exit after starting through UAC")
    except SystemExit as e:
        check(e.code == 0 and ran and ran[-1][-2:] == ["--autostart", "on"], f"UAC z tymi samymi argumentami: {ran}")
    rx_winapi.relaunch_admin = lambda cmd: False
    RC.elevate()
    ok("refused in UAC: the receiver keeps running")
    sys.argv = ["receiver_controller.py", "--no-elevate"]
    ran.clear(); rx_winapi.relaunch_admin = lambda cmd: ran.append(cmd) or True
    RC.elevate()
    check(not ran, "--no-elevate: no UAC")
    rx_winapi.is_admin = lambda: True
    sys.argv = ["receiver_controller.py"]
    RC.elevate()
    check(not ran, "already administrator: no UAC")
    check(not task, "--autostart and the earlier cases: the task is not used")
finally:
    RC.os.name, rx_winapi.is_admin, rx_winapi.relaunch_admin, sys.argv, rx_autostart.run_admin_task, RC._guard_path = real

# ---------- .NET 10: runtime choice, missing = a clear error and install through winget ----------
import types as _t, subprocess, importlib.util
RT = lambda v: _t.SimpleNamespace(name="Microsoft.NETCore.App", version=v)
real_rt, real_clr, real_run, real_os = rx_hm.net_runtimes, rx_hm._clr_ready, subprocess.run, rx_hm.os.name
try:
    rx_hm._clr_ready = False
    rx_hm.net_runtimes = lambda: [RT("8.0.20"), RT("9.0.9")]
    check(rx_hm.best_runtime() is None, "only .NET 8 / 9: no runtime for HIDMaestro")
    try:
        rx_hm._load_clr("x.dll")
        check(False, "no .NET 10: an error before the CLR is loaded")
    except rx_hm.HMError as e:
        check(e.need_dotnet and "8.0.20" in str(e) and "9.0.9" in str(e), f"komunikat z znalezionymi wersjami: {e}")
    rx_hm.net_runtimes = lambda: [RT("8.0.20"), RT("10.0.2"), RT("10.0.12"), RT("11.0.0-preview")]
    check(rx_hm.best_runtime().version == "11.0.0-preview" or rx_hm.best_runtime().version.startswith("1"), "najnowszy >= 10")
    rx_hm.os.name = "nt"
    rx_hm.net_runtimes = lambda: [RT("8.0.20")]
    check(rx_hm.need_dotnet(), "window: .NET 10 needs to be installed")
    cmds = []
    def fake_run(cmd, **kw):
        cmds.append(cmd)
        rx_hm.net_runtimes = lambda: [RT("10.0.12")]
        return _t.SimpleNamespace(returncode=0, stdout="Successfully installed", stderr="")
    subprocess.run = fake_run
    ok_, msg = rx_hm.install_dotnet()
    check(ok_ and cmds[0][:4] == ["winget", "install", "--id", rx_hm.WINGET_NET] and "--silent" in cmds[0], f"winget: {cmds}")
    check(not rx_hm.need_dotnet(), ".NET 10 present after the install")
    rx_hm.net_runtimes = list
    subprocess.run = lambda cmd, **kw: _t.SimpleNamespace(returncode=1, stdout="No package found", stderr="")
    ok_, msg = rx_hm.install_dotnet()
    check(not ok_ and "No package found" in msg, f"winget error: {msg}")
    rx_hm.net_runtimes = lambda: None          # no pythonnet/clr_loader: unknown whether .NET is there
    check(not rx_hm.need_dotnet(), "no pythonnet: the window does not offer a .NET install")
    if importlib.util.find_spec("pythonnet") is None:
        try:
            rx_hm._load_clr("x.dll")
            check(False, "no pythonnet: error")
        except rx_hm.HMError as e:
            check(not e.need_dotnet and "pythonnet" in str(e), f"no pythonnet: a message about pythonnet: {e}")
finally:
    rx_hm.net_runtimes, rx_hm._clr_ready, subprocess.run, rx_hm.os.name = real_rt, real_clr, real_run, real_os

# ---------- HIDMaestro 2.0: LICENSE next to the DLL, error diagnostics ----------
def zip_with(*entries):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in entries:
            z.writestr(n, d)
    return zipfile.ZipFile(io.BytesIO(b.getvalue()))
zf = zip_with(("HIDMaestro.Core.dll", b"MZ"), ("LICENSE", b"MIT"), ("HIDMaestroTest\\LICENSE", b"other"),
              ("HIDMaestroTest\\WinRT.Runtime.dll", b"MZ"))
check([U._name(i) for i in U.pick(zf.infolist())] == ["HIDMaestro.Core.dll", "HIDMaestroTest/WinRT.Runtime.dll", "LICENSE"],
      "LICENSE from the DLL folder goes with the version")
zf = zip_with(("HIDMaestro.Core.dll", b"MZ"), ("HIDMaestroTest\\LICENSE", b"other"))
check([U._name(i) for i in U.pick(zf.infolist())] == ["HIDMaestro.Core.dll"], "a foreign LICENSE from a subfolder is not taken")
zf = zip_with(("HIDMaestro.Core.dll", b"MZ"))
check([U._name(i) for i in U.pick(zf.infolist())] == ["HIDMaestro.Core.dll"], "release without LICENSE: no error")

class HMResourceInUseException(Exception): pass
class Plain(Exception): pass
check("another program" in rx_hm.describe_error(HMResourceInUseException("key held")) and
      rx_hm.describe_error(HMResourceInUseException("key held")).startswith("HMResourceInUseException: key held"),
      "2.0 exception: type, message and hint")
check(rx_hm.describe_error(Plain("x")) == "Plain: x", "another exception: no hint")
issue = _t.SimpleNamespace(File="p/dualsense.json", JsonPath="$.inputReportSize", Reason="unknown key")
check(rx_hm.profile_issues(_t.SimpleNamespace(ProfileLoadIssues=[issue])) == ["p/dualsense.json: $.inputReportSize: unknown key"],
      "ProfileLoadIssues: file, path, reason")
check(rx_hm.profile_issues(_t.SimpleNamespace()) == [] and rx_hm.profile_issues(_t.SimpleNamespace(ProfileLoadIssues=[issue] * 5))[-1] == "... +2 more",
      "HIDMaestro 1.x without ProfileLoadIssues: empty; more than 3: a summary")

print("HM UPDATE", "OK" if not fails else f"FAILURES: {len(fails)}")
if fails:
    sys.exit(1)
