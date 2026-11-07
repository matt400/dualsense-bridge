"""Receiver: the pinned HIDMaestro downloaded by itself from GitHub Releases - reading the zip over HTTP Range (only the needed
entries), versions in separate folders, the pin file with SHA-256 of every file, network errors; self-elevation to administrator.
    python3 tests/test_hm_update.py receiver"""
import hashlib, io, json, os, sys, tempfile, time, zipfile
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_hm, rx_hm_update as U

REAL_PIN = (U.PINNED_TAG, dict(U.PINNED_SHA256))

from chk import check, fails, ok                              # noqa: E402  (shared helper, tests/chk.py)

sha = lambda b: hashlib.sha256(b).hexdigest()


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
    data = SERVED[url.replace("github.com", "storage")]
    rng = (headers or {}).get("Range")
    if not rng:
        return Resp(data)
    a, b = map(int, rng[6:].split("-"))
    return Resp(data[a:b + 1], 206, {"Content-Range": f"bytes {a}-{b}/{len(data)}"}, url.replace("github.com", "storage"))


ROOT = tempfile.mkdtemp()
U.root = lambda: ROOT
U._get = opener
def pin(tag, dll=DLL_BYTES):
    """Pin version `tag`, whose zip (make_zip) has DLL `dll`; the hashes match its content."""
    SERVED[U.zip_url(tag).replace("github.com", "storage")] = make_zip(dll)
    U.PINNED_TAG = tag
    U.PINNED_SHA256 = {U.DLL: sha(dll), U.DEPS[0]: sha(b"MZsdk"), U.DEPS[1]: sha(b"MZwinrt")}

# ---------- first download: only the DLL and dependencies, through Range, from the link that follows from the pin ----------
pin("v1.12.0")
z1 = SERVED[U.zip_url("v1.12.0").replace("github.com", "storage")]
got = []
U.on_installed = got.append
st = U.check()
d = os.path.join(ROOT, "v1.12.0")
check(st.version == "v1.12.0" and st.latest == "v1.12.0" and not st.error and got == ["v1.12.0"], f"downloaded version: {st}")
check(sorted(os.listdir(d)) == sorted([U.DLL, *U.DEPS]), f"only the DLL and dependencies, flat: {os.listdir(d)}")
check(open(os.path.join(d, U.DLL), "rb").read() == DLL_BYTES, "the root DLL, byte for byte")
ranged = [h["Range"] for u, h in calls if "Range" in h]
total = sum(int(r[6:].split("-")[1]) - int(r[6:].split("-")[0]) + 1 for r in ranged)
check(total < len(z1) * 0.5, f"only part of the zip downloaded ({total} of {len(z1)} B)")
check(all(u == U.zip_url("v1.12.0") or "storage" in u for u, _ in calls) and not any("api.github.com" in u for u, _ in calls),
      "the fixed x64 zip link, no GitHub API request")
check(not any(n.startswith(".") for n in os.listdir(ROOT)), "no temp folder")
check(U.newest_dll() == os.path.join(d, U.DLL) and rx_hm.find_dll("") == os.path.join(d, U.DLL),
      "rx_hm takes the downloaded version when it has no path of its own")

# ---------- the pinned version already on disk: no request at all ----------
calls.clear()
st = U.check()
check(not calls and st.version == "v1.12.0" and not st.error, "pinned version present: a check is local, no request")

# ---------- a moved pin: installed at once, the old folder removed ----------
pin("v1.13.0", b"MZ" + b"new" * 1000)
st = U.check()
check(st.version == "v1.13.0" and got[-1] == "v1.13.0" and sorted(os.listdir(ROOT)) == ["v1.13.0"],
      f"moved pin: new version at once, the old one removed: {os.listdir(ROOT)}")
check(U.version_key("v1.10.0") > U.version_key("v1.9.2"), "versions compared numerically")

# ---------- errors: damaged zip, no DLL, network - the downloaded version stays ----------
pin("v1.14.0", b"MZ" + os.urandom(50_000))
url14 = U.zip_url("v1.14.0").replace("github.com", "storage")
bad = bytearray(SERVED[url14])
ent = next(i for i in zipfile.ZipFile(io.BytesIO(bytes(bad))).infolist() if i.filename == "HIDMaestro.Core.dll")
mid = ent.header_offset + 30 + len(ent.filename) + ent.compress_size // 2      # middle of the root DLL data
bad[mid:mid + 64] = bytes(x ^ 0xFF for x in bad[mid:mid + 64])
SERVED[url14] = bytes(bad)
st = U.check()
check(st.error and st.version == "v1.13.0" and not os.path.exists(os.path.join(ROOT, "v1.14.0")),
      f"damaged zip: error, version unchanged, no half-made folder: {st.error}")
check(not any(n.startswith(".") for n in os.listdir(ROOT)), "temp folder cleaned up after the error")
b = io.BytesIO()
with zipfile.ZipFile(b, "w") as zz:
    zz.writestr("README.md", "x")
pin("v1.15.0")
SERVED[U.zip_url("v1.15.0").replace("github.com", "storage")] = b.getvalue()
st = U.check()
check("not in the release zip" in st.error and st.version == "v1.13.0", "zip without the DLL: a clear error")
def offline(url, headers=None, timeout=0):
    raise OSError("network down")
U._get = offline
pin("v1.15.1")
st = U.check()
check("network down" in st.error and st.version == "v1.13.0" and not st.busy, "no network: error, the version stays")
U._get = opener
st = U.check()
check(st.version == "v1.15.1" and not st.error, f"the next check after the network is back installs the pin and clears the error: {st}")

# ---------- the version loaded in the process stays until a restart (the CLR loads DLL dependencies only on use) ----------
import types
rx_hm._loaded = types.SimpleNamespace(path=os.path.join(ROOT, "v1.15.1", U.DLL))
pin("v1.16.0", b"MZ" + b"16" * 1000)
st = U.check()
old = os.path.join(ROOT, "v1.15.1")
check(st.version == "v1.16.0" and os.path.isdir(old) and sorted(os.listdir(old)) == sorted([U.DLL, *U.DEPS]),
      f"new version downloaded, the loaded one (v1.15.1) stays with its dependencies: {sorted(os.listdir(ROOT))}")
rx_hm._loaded = None
U.cleanup(keep="v1.16.0")
check(not os.path.exists(old) and os.path.isdir(os.path.join(ROOT, "v1.16.0")), "after a restart the old version removed")

# ---------- SHA-256 of the files, no downgrade ----------
check(U.version_key(REAL_PIN[0]) is not None and set(REAL_PIN[1]) == {U.DLL, *U.DEPS}
      and all(len(h) == 64 and int(h, 16) >= 0 for h in REAL_PIN[1].values()), f"pin in hm_pin.json: tag and SHA-256 of the DLL + dependencies: {REAL_PIN[0]}")
check(U.zip_url("v2.1.0") == "https://github.com/hifihedgehog/HIDMaestro/releases/download/v2.1.0/HIDMaestro-v2.1.0.zip",
      "fixed link to the x64 zip")
ROOT_PIN = tempfile.mkdtemp()
U.root = lambda: ROOT_PIN
pin("v1.21.0", b"MZ" + b"pin2" * 500)
U.check()
U.PINNED_SHA256 = {**U.PINNED_SHA256, U.DLL: sha(b"MZ" + b"good" * 500)}      # zip replaced: a different DLL than the pin
SERVED[U.zip_url("v1.22.0").replace("github.com", "storage")] = make_zip(b"MZ" + b"evil" * 500)
U.PINNED_TAG = "v1.22.0"
st = U.check()
check("does not match the pinned SHA-256" in st.error and st.version == "v1.21.0" and not os.path.exists(os.path.join(ROOT_PIN, "v1.22.0")),
      f"replaced file: refusal, version unchanged: {st.error}")
check(not any(n.startswith(".") for n in os.listdir(ROOT_PIN)), "after a refusal the temp folder cleaned up")
U.PINNED_SHA256 = {}
st = U.check()
check("no pinned SHA-256" in st.error and st.version == "v1.21.0", f"no hashes in the pin: nothing is installed unchecked: {st.error}")
calls.clear()
U.PINNED_TAG = "v1.0.0"                                      # pin older than the downloaded version: no downgrade
pin("v1.0.0")
st = U.check()
check(st.version == "v1.21.0" and not st.error and not calls, "pin older than the downloaded version: no download")
U.root = lambda: ROOT

# ---------- the pin file ----------
PF = os.path.join(tempfile.mkdtemp(), "hm_pin.json")
def write_pin(obj):
    with open(PF, "w", encoding="utf-8") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))
H = "ab" * 32
GOOD = {"tag": "v3.0.0", "files": {U.DLL: H, U.DEPS[0]: H, U.DEPS[1]: H}}
write_pin(GOOD)
pn = U.read_pin(PF)
check(pn.tag == "v3.0.0" and pn.sha256 == GOOD["files"], "a valid pin file")
write_pin({**GOOD, "files": {**GOOD["files"], "THIRD-PARTY-NOTICES.txt": H}})
check(U.read_pin(PF).sha256["THIRD-PARTY-NOTICES.txt"] == H, "extra files may be pinned too")
def refused(obj, text):
    write_pin(obj)
    try:
        U.read_pin(PF)
    except ValueError as e:
        check(text in str(e), f"{text}: {e}")
    else:
        check(False, f"a pin file must be refused: {text}")
refused("[1, 2]", "JSON object")
refused("{not json", "")
refused({**GOOD, "tag": "latest"}, "not a version")
refused({"files": GOOD["files"]}, "not a version")
refused({"tag": "v3.0.0"}, "files must list")
refused({"tag": "v3.0.0", "files": {U.DLL: H}}, "files must list")
refused({**GOOD, "files": {**GOOD["files"], U.DLL: H.upper()}}, "not a SHA-256")
refused({**GOOD, "files": {**GOOD["files"], U.DLL: "abc"}}, "not a SHA-256")
refused({**GOOD, "files": {**GOOD["files"], U.DLL: 7}}, "not a SHA-256")
refused({**GOOD, "files": {**GOOD["files"], "..\\evil.dll": H}}, "plain file name")
refused({**GOOD, "files": {**GOOD["files"], "sub/evil.dll": H}}, "plain file name")
try:
    U.read_pin(PF + ".missing")
    check(False, "a missing pin file: OSError")
except OSError:
    ok("a missing pin file: OSError")
real_here, real_vars = U.HERE, (U.PINNED_TAG, U.PINNED_SHA256, U.PIN_ERROR)
U.HERE = os.path.dirname(PF)
os.remove(PF)
tag, hashes, err = U._load_pin()
check(tag == "" and hashes == {} and "hm_pin.json" in err, f"no pin file: no pin and a readable reason, no exception: {err}")
U.PINNED_TAG, U.PINNED_SHA256, U.PIN_ERROR = tag, hashes, err
real_calls = len(calls)
st = U.check()
check("hm_pin.json" in st.error and st.version == "v1.16.0" and len(calls) == real_calls, f"no pin: a clear error, nothing downloaded: {st.error}")
U.HERE = real_here
check(U._load_pin() == (REAL_PIN[0], REAL_PIN[1], ""), "the hm_pin.json shipped in the repo loads")
U.PINNED_TAG, U.PINNED_SHA256, U.PIN_ERROR = real_vars
U.PINNED_TAG, U.PINNED_SHA256 = REAL_PIN

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
