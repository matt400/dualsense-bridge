"""Receiver: regressions from the code review - a neutral first report of the virtual DualSense, hardening the repository
root (with .git), writing remap.json outside the lock,
atomic writes, settings unchanged after a failed write, SO_EXCLUSIVEADDRUSE.
    python3 tests/test_report_hardening_writes.py receiver"""
import ctypes, json, os, socket, sys, tempfile, threading, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, store, sent)
import rx_backup
import rx_buttons
import rx_hm_update as U
import rx_security
import rx_settings
import rx_winapi
from rx_hm import Pad

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- virtual DualSense: the first report is neutral, not all zeros ----------
class Event:
    def __iadd__(self, fn): return self
    def __isub__(self, fn): return self

first = []
backend = types.SimpleNamespace(IntPtr=lambda addr: addr,
                                submit_fn=lambda ctrl, ptr, n: first.append(ctypes.string_at(ptr, n)))
ctrl = types.SimpleNamespace(OutputReceived=Event(), Dispose=lambda: None)
pad = Pad(backend, ctrl, lambda *a: None)
r = first[0]
check(len(first) == 1 and len(r) == 63, "the constructor sends exactly one test report")
check(r[:4] == bytes((128, 128, 128, 128)), f"sticks centred, not 0/0: {r[:4].hex()}")
check(r[7] & 0x0F == 8, f"D-pad neutral (8), not 'up' (0): {r[7] & 0x0F}")
check(r[4] == 0 and r[5] == 0 and r[7] & 0xF0 == 0 and r[8] == 0 and r[9] == 0, "no buttons and no triggers")
check(bytes(pad.buf) == r, "the pad buffer (fixed address) holds the same report")
pad.close()


# ---------- RemapStore: write (fsync) outside the lock, the last write wins ----------
path = os.path.join(TMP, "remap_lock.json")
rs = rx_buttons.RemapStore(path)
real_write = rx_buttons.write_json
held = []
def slow_write(p, obj, **kw):
    held.append(rs._lock.locked())
    time.sleep(0.4)                                    # wolny dysk / fsync
    real_write(p, obj, **kw)
rx_buttons.write_json = slow_write
prof = {"buttons": {"cross": "circle"}}
t = threading.Thread(target=lambda: rs.save("aa:bb:cc:dd:ee:01", prof))
t.start(); time.sleep(0.1)
t0 = time.monotonic(); rs.get("aa:bb:cc:dd:ee:01"); waited = time.monotonic() - t0
t.join()
rx_buttons.write_json = real_write
check(held == [False], f"write_json called without holding _lock: {held}")
check(waited < 0.15, f"get() from the network thread does not wait for the write: {waited * 1000:.0f} ms")

ts = [threading.Thread(target=rs.save, args=(f"aa:bb:cc:dd:ee:{k:02d}", prof)) for k in range(2, 8)]
[t.start() for t in ts]; [t.join() for t in ts]
on_disk = sorted(json.load(open(path, encoding="utf-8"))["profiles"])
check(on_disk == sorted(rs.saved) and len(on_disk) == 7, f"parallel writes: the file holds the last state ({len(on_disk)} profiles)")
ok, _msg = rs.delete("aa:bb:cc:dd:ee:02")
check(ok and "aa:bb:cc:dd:ee:02" not in json.load(open(path, encoding="utf-8"))["profiles"], "removal writes the file too")


# ---------- rx_settings.update: a failed write does not change the settings in effect ----------
real_save = rx_settings.save
def failing_save(s, p):
    raise OSError("disk full")
before = rx_settings.cur
changed = rx_settings.replace(before.mouse, sens=before.mouse.sens + 0.25)
rx_settings.save = failing_save
ok, msg = rx_settings.update(os.path.join(TMP, "settings_fail.json"), mouse=changed)
rx_settings.save = real_save
check(ok is False and "disk full" in msg, f"write error returned: {msg!r}")
check(rx_settings.cur is before, "the setting was not applied in memory")
ok, msg = rx_settings.update(os.path.join(TMP, "settings_ok.json"), mouse=changed)
check(ok and rx_settings.cur.mouse.sens == changed.sens and os.path.exists(os.path.join(TMP, "settings_ok.json")),
      "successful write: applied and stored")
ok, _msg = rx_settings.update(None, mouse=before.mouse)
check(ok and rx_settings.cur.mouse.sens == before.mouse.sens, "no path: only applied (as before)")


# ---------- atomic writes: backup meta.json ----------
real_replace = os.replace
def broken_replace(a, b):
    raise OSError("przerwane")

bk_dir = tempfile.mkdtemp()
src = os.path.join(bk_dir, "settings.json")
open(src, "w").write("{}")
rx_backup.files = lambda: {"settings.json": src}
metas = []
real_bw = rx_backup.write_json
rx_backup.write_json = lambda p, obj, **kw: (metas.append(os.path.basename(p)), real_bw(p, obj, **kw))
b = rx_backup.create("manual", "abcdef0123", "")
rx_backup.write_json = real_bw
check(metas == [rx_backup.META] and b is not None and b.commit == "abcdef0", f"meta.json zapisany atomowo (write_json): {metas}")


# ---------- bind_udp: exclusive access to the address (Windows) ----------
opts = []
class FakeSock:
    def __init__(self, *a): pass
    def setsockopt(self, *a): opts.append(a)
    def bind(self, addr): pass
    def ioctl(self, *a): pass
real_sock, real_excl = rx_winapi.socket.socket, getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
rx_winapi.socket.socket = FakeSock
try:
    socket.SO_EXCLUSIVEADDRUSE = -5
    rx_winapi.bind_udp(12345, "test", 1 << 20)
    with_opt = list(opts)
    opts.clear()
    del socket.SO_EXCLUSIVEADDRUSE
    rx_winapi.bind_udp(12345, "test")
finally:
    rx_winapi.socket.socket = real_sock
    if real_excl is not None:
        socket.SO_EXCLUSIVEADDRUSE = real_excl
check((socket.SOL_SOCKET, -5, 1) in with_opt and with_opt.index((socket.SOL_SOCKET, -5, 1)) == 0,
      "SO_EXCLUSIVEADDRUSE set before bind (when the system has it)")
check(opts == [], "without this option (Linux) nothing is set")


# ---------- folder hardening: the repository root with .git ----------
repo = tempfile.mkdtemp()
code = os.path.join(repo, "receiver"); os.makedirs(code)
rx_security.code_dir = lambda: code
check(rx_security.repo_root() is None and rx_security.protected_paths() == [code],
      "no .git (copy from a zip): only the receiver folder, the parent folder untouched")
os.makedirs(os.path.join(repo, ".git"))
check(rx_security.repo_root() == repo and rx_security.protected_paths() == [repo, os.path.join(repo, ".git")],
      "git clone: the root and .git")

hardened, owned = [], {}
fake = types.SimpleNamespace(is_admin=lambda: True, harden_dir=lambda p: (hardened.append(p), (True, ""))[1],
                             admin_owned=lambda p: owned.get(p, True))
rx_security._winapi = lambda: fake
ok, _msg = rx_security.harden()
check(ok and hardened == [repo], f"harden() hardens the root (covers receiver/ and .git): {hardened}")
check(not any(i.code == "folder" for i in rx_security.issues()), "everything belongs to administrators: no problem")
owned[os.path.join(repo, ".git")] = False
check(any(i.code == "folder" for i in rx_security.issues()), "a .git of a normal user alone is enough to report a problem")
owned.clear(); owned[repo] = False
check(any(i.code == "folder" for i in rx_security.issues()), "a root of a normal user: a problem")
os.rmdir(os.path.join(repo, ".git"))
hardened.clear()
rx_security.harden()
check(hardened == [code], "no .git: harden_dir gets only the receiver folder")

print("REGRESJE OK" if not fails else f"REGRESSION FAILURES: {len(fails)}")
sys.exit(1 if fails else 0)
