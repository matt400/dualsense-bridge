"""Receiver: regresje z przeglądu bugów (każdy test przechodzi PO poprawkach, przed nimi - nie)."""
import json, os, sys, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_buttons as B, rx_config, rx_console, rx_master, rx_profiles as P, rx_settings
from proto import T_BYE, T_IDENT, T_MOUSE, T_STATE
from rx_settings import MasterConfig, MouseConfig, Settings

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.01)
    return pred()

# ---------- odwrócenie gałki: pełne wychylenie w obie strony ----------
inv = {"left": {"src": "left", "inv_x": True, "inv_y": True}}
check(B.apply_sticks(inv, 255, 0, 128, 128)[:2] == (0, 255), "odwrócona gałka: 255 -> 0 i 0 -> 255 (było 255 -> 1)")
check(B.apply_sticks(inv, 128, 128, 128, 128)[:2] == (128, 128), "odwrócona gałka: środek zostaje środkiem")
check(all(abs(B._inv(B._inv(v)) - v) <= 1 for v in range(256)), "podwójne odwrócenie wraca do wartości (±1)")

# ---------- remap.json / API: zły typ nie wywraca receivera ----------
for bad in ({"buttons": [1, 2]}, {"sticks": ["left"]}, {"buttons": "x", "sticks": 5}, [1], "x", None):
    try:
        prof = B.normalize_profile(bad)
        good = prof == {"buttons": {}, "sticks": {}, "tuning": {}, "gyro": {}, "triggers": {}}
    except Exception as e:
        good = False
        print("   ", repr(e))
    check(good, f"normalize_profile({bad!r}) -> pusty profil zamiast wyjątku")
path = os.path.join(TMP, "bad_remap.json")
for content in ({"profiles": [1, 2]}, {"profiles": "x"}, [1]):
    json.dump(content, open(path, "w"))
    try:
        st = B.RemapStore(path)
        good = st.saved == {}
    except Exception as e:
        good = False
        print("   ", repr(e))
    check(good, f"remap.json {content!r} -> brak profili zamiast awarii przy starcie")
ok_, _msg = P.apply(0, S.profile_key(0), {"buttons": ["cross"], "sticks": [1]})
check(ok_ and S.remap_maps[0]["buttons"] == {}, "zły profil (np. ręcznie edytowany remap.json) nie wywraca zastosowania")

# ---------- klucz HMAC liczbą w .toml / .json ----------
cfg = os.path.join(TMP, "key.json")
json.dump({"key": 1234}, open(cfg, "w"))
k = rx_config.init(["--config", cfg]).KEY
rx_config.init(["--remap-file", C.REMAP_PATH])
check(k == b"1234", f"key = 1234 (liczba) -> b'1234' jak w nadajniku, a nie AttributeError: {k!r}")

# ---------- PS trzymane przy włączaniu pada ----------
PS = BIT[316]
H.reset(1); N._digital[1] = None
calls.clear()
C.PS_HOLD_ACTION = "gamebar"
state(1, mask=PS)                                   # pierwszy stan po połączeniu: PS trzymane od włączania
time.sleep(C.PS_HOLD_S + 0.15)
check(not any(c[0] == "k" for c in calls), "PS trzymane od połączenia nie otwiera Game Bar")
state(1, mask=0); time.sleep(0.02)
check(not S.states[1]["mask"] & PS, "puszczenie PS z włączania pada nie wysyła PS do gry")
time.sleep(0.1)
state(1, mask=PS); state(1, mask=0)
check(S.states[1]["mask"] & PS, "kolejne krótkie PS działa normalnie (impuls dla gry)")
time.sleep(0.15); state(1)

# ---------- master: "zamknij grę" zamyka okno, które sprawdził ----------
A = "aa:aa:aa:aa:aa:01"
feed(T_IDENT, 0, A.encode()); state(0)
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=A, actions={"pause": "", "close_game": "triangle",
                                                                        "sleep": "", "shutdown": ""})))
closed = []
W.close_window = lambda hwnd: closed.append(hwnd) or True
fg = [(0x111, "explorer.exe")]
W.foreground_window = lambda: fg[0]
S.game = "game.exe"                                 # rx_games widział grę pół sekundy temu ...
rx_master._do(0, "close_game")                      # ... ale teraz aktywny jest pulpit
check(closed == [], "nieaktualne S.game: pulpit na pierwszym planie nie jest zamykany")
fg[0] = (0x222, "game.exe")
def switch_after_check():
    r = fg[0]
    fg[0] = (0x333, "steam.exe")                    # alt-tab tuż po sprawdzeniu
    return r
W.foreground_window = switch_after_check
rx_master._do(0, "close_game")
check(closed == [0x222], f"WM_CLOSE idzie do sprawdzonego okna gry, a nie do okna aktywnego chwilę później: {closed}")
S.game = None
rx_settings.apply(rx_settings.defaults())

# ---------- tryb konsoli: restart w trakcie "Przetestuj" ----------
saved_ctl = rx_console.ctl
c = rx_console.Console(rx_console.ConsoleConfig())
rx_console.ctl = c
c.on, c._revert = True, (time.monotonic() + 10, c.cfg)
check(not rx_console.is_on(), "test trybu konsoli w toku: restart nie zapamiętuje ON (test sam wraca do OFF)")
c._revert = None
check(rx_console.is_on(), "zwykły tryb ON: restart go zachowuje")
rx_console.ctl = saved_ctl

# ---------- okno: wyjątek w wątku aktualizacji ----------
try:
    import rx_qt
except Exception as e:                              # bez PySide6 - pomijamy tylko ten fragment
    print(f"SKIP aktualizacja z okna ({e!r})")
else:
    import rx_update
    out = []
    win = types.SimpleNamespace(update_done=types.SimpleNamespace(emit=out.append))
    real_update = rx_update.update
    rx_update.update = lambda: (_ for _ in ()).throw(OSError("git zawieszony"))
    try:
        rx_qt.MainWindow._update_thread(win)
    finally:
        rx_update.update = real_update
    check(len(out) == 1 and not out[0].ok and not out[0].changed and "git" in out[0].text,
          "błąd aktualizacji wraca do okna (przycisk nie zostaje na zawsze w 'Aktualizuję…')")

# ---------- odłączenie po ciszy: inny pad w tym slocie nie jest "masterem" ----------
connected = []
real_pc = rx_console.pad_connected
rx_console.pad_connected = lambda i, ident=False: connected.append((i, rx_master.is_master(i)))
M_ID = "aa:aa:aa:aa:aa:09"
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=M_ID)))
feed(T_IDENT, 1, M_ID.encode()); state(1)
check(rx_master.is_master(1), "master w slocie 1")
S.last_seen[1] = time.monotonic() - C.UNPLUG_S - 1
N.check_stale(time.monotonic())
check(S.pad_id[1] is None and not rx_master.is_master(1), "po unplug_s ciszy slot zapomina identyfikator pada")
connected.clear()
feed(T_IDENT, 1, b"bb:bb:bb:bb:bb:09"); state(1)
check(connected and not any(m for _i, m in connected), f"inny pad w slocie mastera nie włącza trybu konsoli: {connected}")
rx_console.pad_connected = real_pc
c = rx_console.Console(rx_console.ConsoleConfig())
applied = []
c._apply = lambda on, *a, **k: applied.append(on)
c._auto_on(1)                                       # zadanie z kolejki dla slotu, w którym mastera już nie ma
check(applied == [], "auto-ON z kolejki sprawdza mastera w chwili wykonania")
rx_settings.apply(rx_settings.defaults())
feed(T_BYE, 1)

# ---------- błąd ViGEm nie udaje ponownego połączenia pada ----------
import threading, rx_ds4
attempts = []
class BrokenPad:
    def __init__(self):
        attempts.append(1)
        raise OSError("ViGEmBus not installed")
rx_ds4.vg.VDS4Gamepad = BrokenPad
connected.clear()
rx_console.pad_connected = lambda i, ident=False: connected.append(i)
S.ignore_until[1] = 0.0
S.want[0].clear()                                   # slot 0 nie czeka w kolejce (wait_turn)
from dataclasses import replace as _replace
rx_settings.apply(_replace(rx_settings.cur, vpad=rx_settings.VpadConfig(fixed_slots=False)))   # bez zastępczego slotu 0
t = threading.Thread(target=rx_ds4.slot_thread, args=(1,), daemon=True)
t.start()
end = time.monotonic() + 2.4
while time.monotonic() < end:
    state(1); time.sleep(0.05)
rx_console.pad_connected = real_pc
check(len(attempts) >= 2, f"ViGEm: kolejne próby utworzenia pada ({len(attempts)})")
check(connected.count(1) == 1, f"błąd ViGEm nie jest nowym połączeniem pada (pad_connected {connected.count(1)}x)")
S.want[1].clear(); S.stop.set(); S.wake[1].set(); t.join(3); S.stop.clear()

# ---------- okno ustawień: suwaki myszy nie obcinają wartości z settings.json ----------
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import rx_qt_console
except Exception as e:
    print(f"SKIP suwaki myszy ({e!r})")
else:
    d = rx_qt_console.ConsoleSettings()
    hi = {k: v[1] for k, v in rx_settings.MOUSE_LIMITS.items()}
    m = MouseConfig(**hi)
    d._load_mouse(m)
    got = d.collect_settings().mouse
    check(got == m, f"maksymalne wartości myszy przechodzą przez okno bez zmian: {got}")

# ---------- powód błędu aktualizacji nadajnika (pakiet updmsg) trafia do okna ----------
from proto import T_PING, T_UPDMSG
pkts = [(N.codec.pack(T_UPDMSG, 255, "Permission denied (publickey) żółw".encode()), ("10.0.0.9", 5005)),
        (N.codec.pack(T_PING, 255, 1), ("10.0.0.9", 5005))]
class FakeSock:
    def recvfrom(self, n):
        if not pkts:
            S.stop.set()
            raise TimeoutError
        return pkts.pop(0)
    def sendto(self, *a):
        pass
real_sock, real_send = N.in_sock, N.send_peer
N.in_sock = FakeSock()
S.sender_update_msg = ""
N.net_loop()
S.stop.clear()
N.in_sock = real_sock
check(S.sender_update_msg == "Permission denied (publickey) żółw" and S.peer == ("10.0.0.9", 5005),
      f"pakiet z powodem błędu aktualizacji nadajnika: {S.sender_update_msg!r}")

# ---------- Ustawienia otwarte, master zmieniony koroną / PS + R3: Zapisz go nie cofa ----------
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import rx_qt_console
except Exception as e:
    print(f"SKIP master w otwartych Ustawieniach ({e!r})")
else:
    MA, MB = "aa:aa:aa:aa:aa:31", "bb:bb:bb:bb:bb:32"
    C.SETTINGS_PATH = os.path.join(TMP, "settings_master.json")
    feed(T_IDENT, 0, MA.encode()); state(0); feed(T_IDENT, 1, MB.encode()); state(1)
    rx_master.set_master(0, save=False)
    d = rx_qt_console.ConsoleSettings(); d.present()
    rx_master.set_master(1, save=False)                       # korona na karcie pada 2
    d.save_btn.click()
    check(rx_settings.cur.master.pad == MB, f"Zapisz nie cofa mastera zmienionego koroną: {rx_settings.cur.master.pad}")
    d.present(); d.master_pad.set_data(MA); d.master_pad.activated.emit(d.master_pad.currentIndex())
    d.save_btn.click()
    check(rx_settings.cur.master.pad == MA, "wybór mastera w oknie (także powrót do pierwotnego) działa")
    d.close()
    rx_settings.apply(rx_settings.defaults())

# ---------- tryb konsoli: pusta lista dozwolonych programów = bez blokady ----------
cc = rx_console.Console(rx_console.ConsoleConfig(allowed_apps=()))
FG[0] = "explorer.exe"
check(cc.blocked_by() is None, "pusta lista dozwolonych: pulpit nie blokuje (wcześniej blokowało wszystko)")
FG[0] = "eldenring.exe"
check(cc.blocked_by() is None, "pusta lista dozwolonych: bez blokady także w grze")
cc = rx_console.Console(rx_console.ConsoleConfig())
check(cc.blocked_by() == "eldenring.exe", "domyślna lista: gra blokuje")
FG[0] = None

# ---------- touchpad jako mysz idzie za masterem (korona przenosiła tylko uprawnienia) ----------
rx_settings.apply(rx_settings.defaults())
MA, MB = "aa:aa:aa:aa:aa:41", "bb:bb:bb:bb:bb:42"
feed(T_IDENT, 0, MA.encode()); state(0); feed(T_IDENT, 1, MB.encode()); state(1)
S.mouse_mode[:] = [i in C.MOUSE_PADS for i in range(C.NUM_PADS)]
check(S.mouse_mode[:2] == [True, False], "bez mastera: mysz wg mouse_pads (slot 1)")
rx_master.set_master(1, save=False)                       # korona na karcie pada 2
check(S.mouse_mode[:2] == [False, True], f"master = pad 2 -> jego touchpad to mysz, pad 1 dotyk DS4: {S.mouse_mode[:2]}")
calls.clear()
feed(T_MOUSE, 1, 30, 0, 0)
check(any(c[0] == "m" for c in calls), "ruch na touchpadzie mastera rusza kursorem")
rx_master.set_master(0, save=False)
check(S.mouse_mode[:2] == [True, False], "master z powrotem na pad 1 -> mysz wraca")
rx_master.set_master(None, save=False)
check(S.mouse_mode[:2] == [True, False], "bez mastera -> mouse_pads")
rx_master.set_master(1, save=False)
feed(T_BYE, 1)                                            # master się rozłączył
check(S.mouse_mode[0] is True, "master niepodłączony -> mysz wg mouse_pads (pad 1 nie zostaje bez myszy)")
time.sleep(1.1); feed(T_IDENT, 1, MB.encode()); state(1)  # master wraca
check(S.mouse_mode[:2] == [False, True], "master wrócił -> znów jego touchpad to mysz")
rx_settings.apply(rx_settings.defaults())

# ---------- raport DS4 od razu w wątku sieci (bez przełączania na wątek slotu), seria zlana bez utraty ----------
import threading, rx_ds4
rx_settings.apply(_replace(rx_settings.cur, vpad=rx_settings.VpadConfig(fixed_slots=False)))   # mechanika jednego slotu
reports = []
class FastPad:
    def register_notification(self, cb): pass
    def unregister_notification(self): pass
    def update_extended_report(self, rep):
        reports.append((threading.get_ident(), time.perf_counter(), bytes(rep)[0]))
rx_ds4.vg.VDS4Gamepad = FastPad
rx_ds4.init()
S.ignore_until[0] = 0.0
feed(T_BYE, 0); S.ignore_until[0] = 0.0
state(0)
S.want[0].set()
t = threading.Thread(target=rx_ds4.slot_thread, args=(0,), daemon=True); t.start()
end = time.monotonic() + 2
while rx_ds4.outs[0] is None and time.monotonic() < end:
    time.sleep(0.01)
time.sleep(0.05); reports.clear()
me = threading.get_ident()
t0 = time.perf_counter()
state(0, sticks=(10, 128, 128, 128))
check(reports and reports[0][0] == me and reports[0][2] == 10,
      f"raport z wątku sieci, zanim obsługa pakietu wróciła: {[(r[0] == me, r[2]) for r in reports]}")
for x in (20, 30, 40):                                    # seria szybsza niż pad_hz (1 ms)
    state(0, sticks=(x, 128, 128, 128))
time.sleep(0.05)
check(reports[-1][2] == 40, f"seria szybsza niż pad_hz: ostatni stan dosłany przez wątek slotu: {[r[2] for r in reports]}")
check(len(reports) <= 4, "seria zlana (nie więcej raportów niż zmian)")
import random as _r
rng = _r.Random(5); reports.clear(); seq = []
for k in range(300):                                      # zmiany w nieregularnych odstępach wokół limitu pad_hz
    v = 1 + k % 250
    state(0, sticks=(v, 128, 128, 128)); seq.append(v)
    time.sleep(rng.choice((0, 0, 0.0004, 0.0009, 0.0011, 0.002)))
time.sleep(0.05)
vals = [r[2] for r in reports]
dups = sum(1 for a, b in zip(vals, vals[1:]) if a == b)
check(dups == 0, f"bez zdublowanych raportów (push + wątek slotu): {dups} duplikatów na {len(vals)}")
check(vals[-1] == seq[-1], "ostatni stan zawsze dochodzi")
S.want[0].clear(); S.wake[0].set(); t.join(2)
check(rx_ds4.outs[0] is None and not S.plugged[0], "odłączenie: push nie używa już odłączonego pada")
state(0, sticks=(50, 128, 128, 128))                      # po odłączeniu tylko budzi wątek slotu, bez wyjątku
S.push = None

# ---------- git: sprawdzanie w tle i aktualizacja nie biegną naraz ----------
import rx_update
held = threading.Event(); release = threading.Event()
def hold():
    with rx_update._git_lock:
        held.set(); release.wait(2)
th = threading.Thread(target=hold); th.start(); held.wait(2)
real_isdir = rx_update.os.path.isdir
rx_update.os.path.isdir = lambda p: True
t0 = time.monotonic(); r = rx_update.check_available(TMP, git="git")
check(r is None and time.monotonic() - t0 < 0.1, "trwa aktualizacja -> sprawdzenie w tle pominięte od razu")
rx_update.proto.git_pull, real_pull = (lambda root, git, **kw: (rx_update.UPD_NO_CHANGE, "", "a", "a", [])), rx_update.proto.git_pull
threading.Timer(0.3, release.set).start()
t0 = time.monotonic(); rx_update.update(TMP, git="git", pip=False)
check(time.monotonic() - t0 >= 0.25, "aktualizacja czeka na koniec trwającego sprawdzenia")
rx_update.proto.git_pull, rx_update.os.path.isdir = real_pull, real_isdir
th.join(2)

print("WSZYSTKO OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
