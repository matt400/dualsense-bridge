"""Tools: tools/hm_pin.py - the SDK hash and zip hash read from release notes (full and truncated), the pin file it writes, the
cross-check that refuses a pin the notes do not confirm, --check against a replaced release.
    python3 tests/test_hm_pin.py receiver"""
import hashlib, importlib.util, io, os, sys, tempfile, zipfile
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_hm_update as U

from chk import check, fails, ok                              # noqa: E402  (shared helper, tests/chk.py)

spec = importlib.util.spec_from_file_location("hm_pin", Path(__file__).resolve().parents[1] / "tools" / "hm_pin.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

sha = lambda b: hashlib.sha256(b).hexdigest()
H = "593509d1b48168a9" + "0" * 48

# ---------- the hashes in the release notes ----------
check(P.notes_sdk_hash(f"Validated (SDK SHA-256 `{H}`, stamped 2.1.0)") == H, "full SDK hash in backticks")
check(P.notes_sdk_hash("(SDK SHA-256 `593509d1b48168a9...`, stamped `2.0.2+52b59f4c`)") == "593509d1b48168a9",
      "truncated SDK hash (the 2.0.2 notes): a prefix")
check(P.notes_sdk_hash(f"**SDK SHA-256:** {H.upper()}") == H, "bold label, upper case: lower-cased")
check(P.notes_sdk_hash("SDK SHA-256 `abcdef`") is None, "a prefix shorter than 12 digits is not accepted")
check(P.notes_sdk_hash("no hash here") is None, "no hash in the notes")
ZH = "bec5fa3936a9a2a0e0d0835974a37196cb6502c36796577167c94b49f62d326c"
notes = f"SHA-256 of `HIDMaestro-v2.0.2.zip`: `{ZH}`\nSHA-256 of `HIDMaestro-v2.0.2-arm64.zip`: `{'ed' * 32}`"
check(P.notes_zip_hash(notes, "v2.0.2") == ZH, "the x64 zip hash, not the arm64 one")
check(P.notes_zip_hash(notes, "v2.0.3") is None, "another tag: nothing")

# ---------- the pin file it writes ----------
hashes = {U.DLL: "a" * 64, U.DEPS[0]: "b" * 64, U.DEPS[1]: "c" * 64}
tmp = Path(tempfile.mkdtemp())
(tmp / "p.json").write_text(P.render_pin("v9.0.0", hashes), encoding="utf-8")
pn = U.read_pin(str(tmp / "p.json"))
check(pn.tag == "v9.0.0" and pn.sha256 == hashes and list(pn.sha256) == [U.DLL, *U.DEPS], "render_pin: what read_pin accepts, in a stable order")
check(P.differences(hashes, hashes) == [] and len(P.differences(hashes, {**hashes, U.DLL: "d" * 64})) == 1
      and "nothing" in P.differences(hashes, {U.DLL: hashes[U.DLL]})[0], "differences: changed and missing files")

# ---------- a fake release served over Range ----------
DLL = b"MZ" + os.urandom(50_000)
def make_zip(dll):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("HIDMaestro.Core.dll", dll)
        z.writestr("HIDMaestroTest\\Microsoft.Windows.SDK.NET.dll", b"MZsdk")
        z.writestr("HIDMaestroTest\\WinRT.Runtime.dll", b"MZwinrt")
        z.writestr("big.bin", os.urandom(300_000))
    return b.getvalue()
class Resp:
    def __init__(self, data, status=200, headers=None, url=""):
        self.data, self.status, self.headers, self.url, self.pos = data, status, headers or {}, url, 0
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1):
        part = self.data[self.pos:] if n < 0 else self.data[self.pos:self.pos + n]
        self.pos += len(part)
        return part
    def geturl(self): return self.url
ZIPS = {}
def opener(url, headers=None, timeout=0):
    if url not in ZIPS:
        raise OSError("HTTP Error 404: Not Found")       # what urllib raises for a deleted release
    data = ZIPS[url]
    rng = (headers or {}).get("Range")
    if not rng:
        return Resp(data)
    a, b = map(int, rng[6:].split("-"))
    return Resp(data[a:b + 1], 206, {"Content-Range": f"bytes {a}-{b}/{len(data)}"}, url)
U._get = opener
ZIPS[U.zip_url("v5.0.0")] = ZIP5 = make_zip(DLL)
real = {U.DLL: sha(DLL), U.DEPS[0]: sha(b"MZsdk"), U.DEPS[1]: sha(b"MZwinrt")}

with tempfile.TemporaryDirectory() as d:
    got = P.download("v5.0.0", d)
check(got == real, "download: the SHA-256 of the DLL and its two dependencies, from the zip")

# ---------- moving the pin ----------
pin_file = tmp / "hm_pin.json"
def notes_for(*lines):
    P.release_notes = lambda tag: "\n".join(lines)
notes_for(f"(SDK SHA-256 `{real[U.DLL]}`)")
check(P.move_pin("v5.0.0", pin_file, verify_zip=False, no_notes=False) == 0 and U.read_pin(str(pin_file)).sha256 == real,
      "the notes confirm the DLL: the pin is written")
before = pin_file.read_text(encoding="utf-8")
notes_for(f"(SDK SHA-256 `{real[U.DLL][:16]}...`)")
check(P.move_pin("v5.0.0", pin_file, verify_zip=False, no_notes=False) == 0, "a truncated hash in the notes: compared as a prefix")
notes_for(f"(SDK SHA-256 `{'f' * 64}`)")
ZIPS[U.zip_url("v5.0.1")] = make_zip(b"MZ" + b"other")
check(P.move_pin("v5.0.1", pin_file, verify_zip=False, no_notes=False) == 1 and pin_file.read_text(encoding="utf-8") == before,
      "the notes disagree with the DLL: refused, the pin file untouched")
notes_for("a release without any hash")
check(P.move_pin("v5.0.1", pin_file, verify_zip=False, no_notes=False) == 1 and pin_file.read_text(encoding="utf-8") == before,
      "no hash in the notes: refused")
def no_api(tag):
    raise OSError("api down")
P.release_notes = no_api
check(P.move_pin("v5.0.1", pin_file, verify_zip=False, no_notes=False) == 1 and pin_file.read_text(encoding="utf-8") == before,
      "notes unreadable: refused (a pin is never moved unchecked by accident)")
check(P.move_pin("v5.0.1", pin_file, verify_zip=False, no_notes=True) == 0 and U.read_pin(str(pin_file)).tag == "v5.0.1",
      "--no-notes: written after a conscious choice")
notes_for(f"(SDK SHA-256 `{real[U.DLL]}`)", f"SHA-256 of `HIDMaestro-v5.0.0.zip`: `{sha(ZIP5)}`")
check(P.move_pin("v5.0.0", pin_file, verify_zip=True, no_notes=False) == 0, "--verify-zip: the whole zip matches the notes")
notes_for(f"(SDK SHA-256 `{real[U.DLL]}`)", f"SHA-256 of `HIDMaestro-v5.0.0.zip`: `{'1' * 64}`")
check(P.move_pin("v5.0.0", pin_file, verify_zip=True, no_notes=False) == 1, "--verify-zip: a zip that differs from the notes is refused")
check(P.move_pin("latest", pin_file, verify_zip=False, no_notes=True) == 2, "not a version tag: refused before any download")

# ---------- --check ----------
P.move_pin("v5.0.0", pin_file, verify_zip=False, no_notes=True)
check(P.check_pin(pin_file) == 0, "--check: the release still matches the pin")
ZIPS[U.zip_url("v5.0.0")] = make_zip(b"MZ" + b"replaced")
check(P.check_pin(pin_file) == 1, "--check: a replaced release is reported")
del ZIPS[U.zip_url("v5.0.0")]
check(P.check_pin(pin_file) == 1, "--check: a release that disappeared is reported")
check(P.check_pin(tmp / "missing.json") == 1, "--check: no pin file")
ok("hm_pin tool")

print("HM PIN", "OK" if not fails else f"FAILURES: {len(fails)}")
if fails:
    sys.exit(1)
