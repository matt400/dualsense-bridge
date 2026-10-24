"""Receiver: regresje z przeglądu kodu - neutralny pierwszy raport wirtualnego DualSense, zabezpieczenie katalogu głównego
repozytorium (z .git), blokada aktualizacji HIDMaestro przy nieczytelnej dacie publikacji, zapis remap.json poza blokadą,
zapisy atomowe, ustawienia niezmienione po nieudanym zapisie, SO_EXCLUSIVEADDRUSE.
    python3 tests/test_report_hardening_writes.py receiver"""
import ctypes, json, os, socket, sys, tempfile, threading, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import rx_backup
import rx_buttons
import rx_hm_update as U
import rx_security
import rx_settings
import rx_winapi
from rx_hm import Pad

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


# ---------- 1. wirtualny DualSense: pierwszy raport jest neutralny, nie same zera ----------
class Event:
    def __iadd__(self, fn): return self
    def __isub__(self, fn): return self

first = []
backend = types.SimpleNamespace(IntPtr=lambda addr: addr,
                                submit_fn=lambda ctrl, ptr, n: first.append(ctypes.string_at(ptr, n)))
ctrl = types.SimpleNamespace(OutputReceived=Event(), Dispose=lambda: None)
pad = Pad(backend, ctrl, lambda *a: None)
r = first[0]
check(len(first) == 1 and len(r) == 63, "konstruktor wysyła dokładnie jeden raport testowy")
check(r[:4] == bytes((128, 128, 128, 128)), f"gałki na środku, nie 0/0: {r[:4].hex()}")
check(r[7] & 0x0F == 8, f"D-pad w pozycji neutralnej (8), nie „w górę” (0): {r[7] & 0x0F}")
check(r[4] == 0 and r[5] == 0 and r[7] & 0xF0 == 0 and r[8] == 0 and r[9] == 0, "bez przycisków i spustów")
check(bytes(pad.buf) == r, "bufor pada (stały adres) trzyma ten sam raport")
pad.close()


# ---------- 2. HIDMaestro: nieczytelna data publikacji blokuje aktualizację ----------
real_save_state = U._save_state                                    # pkt 5 sprawdza prawdziwą funkcję
U.root = lambda: TMP
U._save_state = lambda *a: None
installs = []
U.install = lambda tag, url, opener=None: installs.append(tag)
U.cleanup = lambda keep: None
U.refresh_status = lambda: U.status
U.status = U.Status(version="v1.0.0")
U.latest_release = lambda: ("v1.1.0", "https://example/zip", 0.0)
st = U.check(force=True)
check(not installs and "publication date" in st.error and st.version == "v1.0.0",
      f"data nieczytelna: bez instalacji, czytelny błąd: {st.error!r}")
now = time.time()
U.latest_release = lambda: ("v1.1.0", "https://example/zip", now - 8 * 86400)
st = U.check(force=True)
check(installs == ["v1.1.0"] and not st.error, "przy następnym sprawdzeniu z poprawną datą: instalacja")
installs.clear(); U.status = U.Status()
U.latest_release = lambda: ("v1.2.0", "https://example/zip", 0.0)
U.check(force=True)
check(installs == ["v1.2.0"], "pierwsza instalacja nie zależy od daty (nie ma czego chronić)")


# ---------- 3. RemapStore: zapis (fsync) poza blokadą, ostatni zapis wygrywa ----------
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
check(held == [False], f"write_json wywołany bez trzymania _lock: {held}")
check(waited < 0.15, f"get() z wątku sieciowego nie czeka na zapis: {waited * 1000:.0f} ms")

ts = [threading.Thread(target=rs.save, args=(f"aa:bb:cc:dd:ee:{k:02d}", prof)) for k in range(2, 8)]
[t.start() for t in ts]; [t.join() for t in ts]
on_disk = sorted(json.load(open(path, encoding="utf-8"))["profiles"])
check(on_disk == sorted(rs.saved) and len(on_disk) == 7, f"równoległe zapisy: plik trzyma ostatni stan ({len(on_disk)} profili)")
ok, _msg = rs.delete("aa:bb:cc:dd:ee:02")
check(ok and "aa:bb:cc:dd:ee:02" not in json.load(open(path, encoding="utf-8"))["profiles"], "usuwanie też zapisuje plik")


# ---------- 4. rx_settings.update: nieudany zapis nie zmienia działających ustawień ----------
real_save = rx_settings.save
def failing_save(s, p):
    raise OSError("dysk pełny")
before = rx_settings.cur
changed = rx_settings.replace(before.mouse, sens=before.mouse.sens + 0.25)
rx_settings.save = failing_save
ok, msg = rx_settings.update(os.path.join(TMP, "settings_fail.json"), mouse=changed)
rx_settings.save = real_save
check(ok is False and "dysk pełny" in msg, f"błąd zapisu zwrócony: {msg!r}")
check(rx_settings.cur is before, "ustawienie nie zostało zastosowane w pamięci")
ok, msg = rx_settings.update(os.path.join(TMP, "settings_ok.json"), mouse=changed)
check(ok and rx_settings.cur.mouse.sens == changed.sens and os.path.exists(os.path.join(TMP, "settings_ok.json")),
      "udany zapis: zastosowane i zapisane")
ok, _msg = rx_settings.update(None, mouse=before.mouse)
check(ok and rx_settings.cur.mouse.sens == before.mouse.sens, "bez ścieżki: tylko zastosowane (jak dotąd)")


# ---------- 5. zapisy atomowe: stan HIDMaestro, meta.json kopii ----------
state_dir = tempfile.mkdtemp()
U.root = lambda: state_dir
real_replace = os.replace
def broken_replace(a, b):
    raise OSError("przerwane")
real_save_state(1.0, "v1")
state_file = os.path.join(state_dir, "state.json")
os.replace = broken_replace
try:
    real_save_state(2.0, "v2")                                      # błąd I/O jest ignorowany, stary plik zostaje cały
finally:
    os.replace = real_replace
check(json.load(open(state_file, encoding="utf-8")) == {"checked": 1.0, "latest": "v1"}, "state.json: przerwany zapis nie psuje pliku")
check(os.listdir(state_dir) == ["state.json"], f"bez śmieciowego .tmp: {os.listdir(state_dir)}")

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


# ---------- 6. bind_udp: wyłączny dostęp do adresu (Windows) ----------
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
      "SO_EXCLUSIVEADDRUSE ustawione przed bind (gdy system je ma)")
check(opts == [], "bez tej opcji (Linux) nic nie jest ustawiane")


# ---------- 7. zabezpieczenie folderu: katalog główny repozytorium z .git ----------
repo = tempfile.mkdtemp()
code = os.path.join(repo, "receiver"); os.makedirs(code)
rx_security.code_dir = lambda: code
check(rx_security.repo_root() is None and rx_security.protected_paths() == [code],
      "bez .git (kopia z zipa): tylko folder receivera, folder nadrzędny nietknięty")
os.makedirs(os.path.join(repo, ".git"))
check(rx_security.repo_root() == repo and rx_security.protected_paths() == [repo, os.path.join(repo, ".git")],
      "klon gita: katalog główny i .git")

hardened, owned = [], {}
fake = types.SimpleNamespace(is_admin=lambda: True, harden_dir=lambda p: (hardened.append(p), (True, ""))[1],
                             admin_owned=lambda p: owned.get(p, True))
rx_security._winapi = lambda: fake
ok, _msg = rx_security.harden()
check(ok and hardened == [repo], f"harden() zabezpiecza katalog główny (obejmuje receiver/ i .git): {hardened}")
check(not any(i.code == "folder" for i in rx_security.issues()), "wszystko należy do administratorów: brak problemu")
owned[os.path.join(repo, ".git")] = False
check(any(i.code == "folder" for i in rx_security.issues()), "sam .git zwykłego użytkownika wystarcza do zgłoszenia problemu")
owned.clear(); owned[repo] = False
check(any(i.code == "folder" for i in rx_security.issues()), "katalog główny zwykłego użytkownika: problem")
os.rmdir(os.path.join(repo, ".git"))
hardened.clear()
rx_security.harden()
check(hardened == [code], "bez .git: harden_dir dostaje tylko folder receivera")

print("REGRESJE OK" if not fails else f"REGRESJE BŁĘDY: {len(fails)}")
sys.exit(1 if fails else 0)
