"""Receiver: przegląd bezpieczeństwa - protokół v3 z kluczem (znaczniki, nagrania, ping jako źródło zegara nadajnika,
ostrzeżenia), DSU tylko lokalnie z limitem klientów, HIDMaestro czeka po publikacji, lista problemów w Ustawieniach,
zwykły tekst w etykietach okna.
    python3 tests/test_security.py receiver"""
import os, socket, struct, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import proto
import rx_dsu
import rx_events as ev
import rx_hm_update as U
import rx_net
import rx_security
from proto import BODY, FRESH_PAST_MS, T_PING, T_PONG2, T_STATE, Codec, EpochClock

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


ST = (128, 128, 0, 128, 128, 0, 0, 0, 0, 80, 0, 0, 0, 8192, 0, 0, 0)

# ---------- protokół v3 po stronie receivera ----------
C.KEY = b"sekret"
rx_net._sender_clock = None
codec = rx_net.make_codec()
check(not codec.v3 and codec.state_path == os.path.join(TMP, "auth.json"), "receiver z kluczem: auth.json obok remap.json")
tx_clock = EpochClock()                                         # zegar nadajnika
tx = Codec(b"sekret", token=lambda: rx_net.clock.ms32(), now_ms=tx_clock.ms32)

check(codec.parse(tx.pack(T_STATE, 0, *ST))[0] == T_STATE, "przed przejściem: podpisany v2 przyjęty (starszy nadajnik)")
t0 = tx_clock.ns()
probe = tx.pack(T_PING, 255, t0, v3=True)
check(codec.parse(probe)[0] == T_PING and codec.v3, "próbny ping v3 nadajnika: receiver przechodzi na v3")
arr = time.perf_counter_ns()
rx_net.note_ping(t0, arr)
check(rx_net.sender_token() is not None and proto.token_fresh(rx_net.sender_token(), tx_clock.ms32()),
      "znacznik dla nadajnika = jego zegar (z pingu) - świeży po jego stronie")
rx_net.note_ping(t0 - 10_000_000_000, arr + 1000)           # powtórzony stary ping (ping nie jest sprawdzany)
check(rx_net._sender_clock == (t0, arr), "powtórzony stary ping nie cofa zegara nadajnika (polecenia nie przestaną działać)")

tx.v3 = True
check(codec.parse(Codec(b"sekret").pack(T_STATE, 0, *ST)) is None and codec.legacy_t < 0,
      "zaraz po przejściu: v2 odrzucony bez ostrzeżenia (pakiety jeszcze w drodze)")
codec.upgraded_t = float("-inf")
pkt = tx.pack(T_STATE, 0, *ST)
check(codec.parse(pkt)[0] == T_STATE and codec.parse(pkt) is None, "stan v3 przyjęty raz, dokładna powtórka odrzucona")
check(codec.parse(Codec(b"sekret").pack(T_STATE, 0, *ST)) is None and codec.legacy_t > 0,
      "po przejściu podpisany v2 odrzucony (nagranie kombinacji pada sprzed aktualizacji)")
rx_net._first_ping[0] -= 5        # nagranie przychodzi po synchronizacji zegara (tuż po starcie - bez wpisu, test_loss_indicator_restart)
stale = Codec(b"sekret", token=lambda: (rx_net.clock.ms32() - FRESH_PAST_MS - 1000) & 0xFFFFFFFF); stale.v3 = True
check(codec.parse(stale.pack(T_STATE, 0, *ST)) is None and codec.stale == 1, "przeterminowany znacznik odrzucony")
rx_net.codec = codec
seq = max((e.seq for e in ev.since(0)), default=0)
rx_net.check_key(time.monotonic())
texts = [e.text for e in ev.since(seq)]
check(any("v2" in t for t in texts) and any("przeterminowane" in t for t in texts),
      f"dziennik: ostrzeżenia o pakietach v2 po przejściu i przeterminowanych: {texts}")

# pong2: zegar PC z losowym przesunięciem, pomiar sieci liczony w tym samym zegarze
base = rx_net.clock.base
rx_net.clock.base = 123_456_789_000
arr = time.perf_counter_ns()
pc_us = (arr + rx_net.clock.base) // 1000 - 700
check(rx_net._latency((pc_us & 0xFFFFFFFF, 1500, 300), arr) == (arr, 1500, 300, 700),
      "pomiar sieci (state2) w zegarze z przesunięciem - ten sam co w pong2")
rx_net.clock.base = base

codec2 = rx_net.make_codec()
check(codec2.v3, "restart receivera: przejście zapamiętane (auth.json)")
rx_net.codec = Codec(); C.KEY = b""

# ---------- DSU: tylko ten komputer, limit klientów ----------
probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); probe.bind(("127.0.0.1", 0))
C.DSU_PORT = probe.getsockname()[1]; probe.close()
check(C.DSU_BIND == "127.0.0.1" and C.DSU_MAX_CLIENTS == 4, "domyślnie DSU na 127.0.0.1, najwyżej 4 klientów")
rx_dsu.start()
check(rx_dsu.dsu.getsockname()[0] == "127.0.0.1", "gniazdo DSU niedostępne z sieci")
clients = []
for _ in range(6):
    cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); cli.bind(("127.0.0.1", 0))
    body = struct.pack("<I", 0x100002) + bytes(8)
    cli.sendto(struct.pack("<4sHHII", b"DSUC", 1001, len(body), 0, 1) + body, ("127.0.0.1", C.DSU_PORT))
    clients.append(cli)
time.sleep(0.5)
check(len(rx_dsu.dsu_clients) == 4, f"limit klientów DSU (podrobione adresy): {len(rx_dsu.dsu_clients)}")
S.stop.set(); time.sleep(0.3); S.stop.clear()

# ---------- HIDMaestro: nowe wydanie czeka ----------
U.root = lambda: TMP
U._save_state = lambda *a: None
installs = []
U.install = lambda tag, url, opener=None: installs.append(tag)
U.cleanup = lambda keep: None
U.refresh_status = lambda: U.status
now = time.time()
U.status = U.Status(version="v1.0.0")
U.latest_release = lambda: ("v1.1.0", "https://example/zip", now - 3600)
st = U.check(force=True)
check(not installs and st.held_until > now + 6 * 86400 and st.latest == "v1.1.0",
      f"wydanie sprzed godziny: czeka {U.MIN_AGE_DAYS} dni (także przy „Sprawdź teraz”)")
U.latest_release = lambda: ("v1.1.0", "https://example/zip", now - 8 * 86400)
U.check(force=True)
check(installs == ["v1.1.0"], "wydanie sprzed 8 dni: instalacja")
installs.clear(); U.status = U.Status(version="v1.0.0")
U.latest_release = lambda: ("v1.1.0", "https://example/zip", now - 3600)
U.check(force=True)
check(not installs, "wydanie sprzed godziny czeka też po ponownym sprawdzeniu")
U.check(skip_wait=True)
check(installs == ["v1.1.0"], "„Zainstaluj teraz”: instalacja bez czekania")
installs.clear(); U.status = U.Status(version="v1.0.0")
U.latest_release = lambda: ("v1.1.0", "https://example/zip", 0.0)
U.check(skip_wait=True)
check(installs == ["v1.1.0"], "„Zainstaluj teraz”: także bez czytelnej daty publikacji")
installs.clear(); U.status = U.Status(version="v1.1.0")
U.check(skip_wait=True)
check(not installs, "„Zainstaluj teraz”: nic do zrobienia, gdy wersja jest już aktualna")
installs.clear(); U.status = U.Status()
U.latest_release = lambda: ("v1.2.0", "https://example/zip", now - 60)
U.check(force=True)
check(installs == ["v1.2.0"], "pierwsza instalacja (brak jakiejkolwiek wersji) nie czeka")
check(U._published({"published_at": "2026-10-01T12:00:00Z"}) > 0 and U._published({}) == 0.0, "czas publikacji z API")

# ---------- lista problemów (Ustawienia -> Bezpieczeństwo, dziennik przy starcie) ----------
codes = {i.code for i in rx_security.issues()}
check("key" in codes and "dsu" not in codes, f"bez klucza: problem 'key'; DSU lokalnie - bez problemu: {codes}")
C.KEY, C.DSU_BIND = b"x", "0.0.0.0"
codes = {i.code for i in rx_security.issues()}
check(codes == {"dsu"}, f"DSU w sieci: problem 'dsu': {codes}")
C.KEY, C.DSU_BIND = b"", "127.0.0.1"
check(rx_security.harden()[0] is False, "zabezpieczenie folderu poza Windows / bez administratora: odmowa, bez wyjątku")

# ---------- okno: tekst z sieci jako zwykły tekst ----------
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rx_qt_bt import ElidedLabel
    from rx_qt_widgets import label
    check(label("<b>x</b>").textFormat() == Qt.TextFormat.PlainText, "etykiety okna: zwykły tekst (bez HTML)")
    el = ElidedLabel("name"); el.set_full("<img src=x>Pad")
    check(el.textFormat() == Qt.TextFormat.PlainText and el.toolTip() == "<qt>&lt;img src=x&gt;Pad</qt>",
          "nazwa Bluetooth z tagami: zwykły tekst i podpowiedź z ucieczką HTML")
except ImportError:
    print("SKIP okno (brak PySide6)")

if fails:
    sys.exit(1)
print("SECURITY OK")
