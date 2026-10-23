"""Receiver: regresje z dziesiątego przeglądu - nowy klient DSU od razu dostaje stan padów, zapis plików ustawień
atomowy z fsync (bez pliku .tmp po błędzie), wspólne funkcje sprawdzające ustawień i konsoli.
    python3 tests/test_atomic_writes.py receiver"""
import json, os, socket, struct, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import proto
import rx_console
import rx_dsu
import rx_settings
from rx_console import ConsoleConfig

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


# ---------- 1. DSU: nowy subskrybent od razu dostaje stan pada, który się nie zmienia ----------
probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); probe.bind(("127.0.0.1", 0))
C.DSU_PORT = probe.getsockname()[1]; probe.close()
C.DSU_TX_INTERVAL = 30.0          # bez poprawki stan bez zmian przyszedłby dopiero po tylu sekundach
C.DSU_HZ = 0
S.alive[0] = True
rx_dsu.start()


def subscribe(cli):
    body = struct.pack("<I", 0x100002) + bytes(8)
    cli.sendto(struct.pack("<4sHHII", b"DSUC", 1001, len(body), 0, 1) + body, ("127.0.0.1", C.DSU_PORT))


def data_packets(cli, wait):
    cli.settimeout(wait)
    got = []
    try:
        while True:
            pkt, _a = cli.recvfrom(4096)
            got += [pkt[k:k + 100] for k in range(0, len(pkt), 100) if struct.unpack_from("<I", pkt, k + 16)[0] == 0x100002]
            cli.settimeout(0.05)
    except TimeoutError:
        pass
    return got

a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); a.bind(("127.0.0.1", 0))
subscribe(a)
check(len(data_packets(a, 1.0)) >= 1, "pierwszy klient DSU dostaje stan pada")
time.sleep(0.3)                   # stan pada się nie zmienia (ten sam "ver")
b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); b.bind(("127.0.0.1", 0))
t0 = time.monotonic()
subscribe(b)
pk = data_packets(b, 1.0)
check(len(pk) >= 1 and pk[0][20] == 0 and pk[0][21] == 2, "drugi klient DSU dostaje stan od razu, a nie po dsu_tx_interval")
check(time.monotonic() - t0 < 2.0, "bez czekania na odświeżenie")
S.stop.set(); rx_dsu.close(); S.stop.clear(); a.close(); b.close()
S.alive[0] = False


# ---------- 2. zapis atomowy: fsync, brak .tmp po błędzie, stary plik nietknięty ----------
path = os.path.join(TMP, "atomic.json")
synced = []
real_fsync, real_replace = os.fsync, os.replace
os.fsync = lambda fd: synced.append(fd) or real_fsync(fd)
proto.write_json(path, {"a": "ą"}, ensure_ascii=False)
os.fsync = real_fsync
check(synced and json.load(open(path, encoding="utf-8")) == {"a": "ą"}, "write_json robi fsync przed podmianą pliku")

def boom(*_a):
    raise OSError("dysk pełny")
os.replace = boom
try:
    proto.write_json(path, {"a": 2})
    check(False, "błąd zapisu przechodzi dalej")
except OSError:
    pass
os.replace = real_replace
check(not os.path.exists(path + ".tmp"), "nieudany zapis usuwa plik .tmp")
check(json.load(open(path, encoding="utf-8")) == {"a": "ą"}, "nieudany zapis zostawia poprzedni plik")

sp = os.path.join(TMP, "settings_h10.json")
rx_settings.save(rx_settings.defaults(), sp)
check(rx_settings.load(sp) == rx_settings.defaults(), "settings.json: zapis i odczyt przez wspólny zapis atomowy")
cp = os.path.join(TMP, "console_h10.json")
rx_console.save(ConsoleConfig(), cp)
check(ConsoleConfig.from_dict(json.load(open(cp, encoding="utf-8")))[0] == ConsoleConfig(), "console.json jak dotąd")


# ---------- 3. konsola: wspólne funkcje sprawdzające, zły typ z ręcznej edycji nie zatrzymuje startu ----------
check(rx_console._choice is rx_settings._choice and rx_console._bool is rx_settings._bool, "jedne funkcje sprawdzające")
cfg, fixed = ConsoleConfig.from_dict({"version": ConsoleConfig().version, "toggle_button": ["r2"], "startup_mode": {"x": 1},
                                      "double_click_ms": "400", "auto_on_master": "tak", "tv_scale": [200]})
check(fixed and cfg.toggle_button == "r2" and cfg.startup_mode == "extend" and cfg.double_click_ms == 400
      and cfg.auto_on_master is True and cfg.tv_scale == 200, "złe typy w console.json -> wartości domyślne")
check(ConsoleConfig.from_dict({"double_click_ms": 250.0})[0].double_click_ms == 250, "liczba z JSON jako int")

if fails:
    print("FAIL", len(fails), "błędów")
    sys.exit(1)
print("WSZYSTKO OK")
