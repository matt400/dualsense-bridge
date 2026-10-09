"""Receiver: lokalny edytor remapowania w przeglądarce (rx_remap_ui.html - SVG pada).

Serwer słucha WYŁĄCZNIE na 127.0.0.1. Strona i API wymagają losowego tokena (generowany przy każdym starcie,
jest w URL-u wypisanym w konsoli i otwieranym przyciskiem Remap), a nagłówek Host musi być lokalny - to
chroni przed obcymi stronami w przeglądarce (CSRF) i DNS rebinding.

  GET  /?t=TOKEN        strona edytora
  GET  /api/pads        konfiguracja: lista wejść, pady, ich profile (pad + gry; live i zapisane), gra, ostatnie gry
  GET  /api/live        stan na żywo: fizyczny i po remapowaniu (*_out) - odpytywane co ~70 ms
  POST /api/apply       {"slot": n, "key": klucz profilu (brak = profil pada), "profile": {...}, "save": bool}
  POST /api/use         {"slot": n, "key": ...}           - profil wybrany w edytorze działa od razu na padzie
                                                           (podgląd i test), do następnej zmiany aktywnego okna
  POST /api/delete      {"slot": n, "key": "pad@gra.exe"}  - usuwa profil gry
  POST /api/power       {"slot": n}                       - wyłącza pada (nadajnik rozłącza Bluetooth)
"""
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import rx_buttons as B
import rx_games
import rx_mouse
import rx_net
import rx_state as S
from proto import CMD_POWER_OFF
from rx_config import C

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rx_remap_ui.html")
token = secrets.token_urlsafe(9)
server = None
base_url = None
MAX_BODY = 64 * 1024
EXE_RE = re.compile(r"[a-z0-9_.()+\-][a-z0-9_.()+\- ]{0,78}\.exe")


def _profile(key):
    return {"key": key, "label": S.profile_label(key), "profile": S.store.get(key),
            "saved": S.store.get_saved(key), "in_file": S.store.in_file(key)}


def _pads():
    pads = []
    for i in range(C.NUM_PADS):
        base = S.profile_key(i)
        pads.append({"slot": i, "id": S.pad_id[i], "base": base, "active": S.active[i],
                     "connected": bool(S.alive[i]), "mouse": bool(S.mouse_mode[i]), "mouse_sens": rx_mouse.sens[i],
                     "profiles": [_profile(k) for k in S.store.keys_for(base)]})
    return {"inputs": [{"name": n, "label": B.LABELS[n]} for n in B.INPUTS], "pads": pads,
            "game": S.game, "recent": list(rx_games.recent), "game_profiles": C.GAME_PROFILES}


def _key_for(slot, key):
    """Klucz profilu z żądania, sprawdzony: profil pada albo "<pad>@<gra.exe>" tego samego pada; inaczej None."""
    base = S.profile_key(slot)
    if not key or key == base:
        return base
    if isinstance(key, str) and key.startswith(base + "@") and EXE_RE.fullmatch(key[len(base) + 1:]):
        return key
    return None


def _slot(body):
    slot = body.get("slot")
    return slot if isinstance(slot, int) and not isinstance(slot, bool) and 0 <= slot < C.NUM_PADS else None


def _live():
    out = []
    for i in range(C.NUM_PADS):
        s = S.states[i]
        raw, st = s["raw"], s["rawst"]
        # *_out = to, co widzi gra (po remapowaniu); bez sufiksu = fizyczne wejścia pada
        out.append({"connected": bool(S.alive[i]), "active": S.active[i], "gyro_on": bool(s["gyro_on"]),
                    "pressed": sorted(B.pressed_inputs(*raw)),
                    "ls": [st[0], st[1]], "rs": [st[2], st[3]], "l2": raw[3], "r2": raw[4],
                    "pressed_out": sorted(B.pressed_inputs(s["mask"], s["hx"], s["hy"], s["lt"], s["rt"])),
                    "ls_out": [s["lx"], s["ly"]], "rs_out": [s["rx"], s["ry"]], "l2_out": s["lt"], "r2_out": s["rt"]})
    return {"pads": out, "game": S.game}


def _apply(body):
    slot = _slot(body)
    if slot is None:
        return 400, {"ok": False, "msg": "bad slot"}
    key = _key_for(slot, body.get("key"))
    if key is None:
        return 400, {"ok": False, "msg": "bad profile name - use the game's .exe file name, e.g. eldenring.exe"}
    prof = B.normalize_profile(body.get("profile"))
    if body.get("save"):
        ok, msg = S.store.save(key, prof)
    else:
        S.store.set_live(key, prof)
        ok, msg = True, "Applied (not saved)"
    S.reload_profile(slot)
    return 200, {"ok": ok, "msg": msg, "active": S.active[slot], **_profile(key)}


def _delete(body):
    slot = _slot(body)
    key = _key_for(slot, body.get("key")) if slot is not None else None
    if key is None or not B.is_game_key(key):
        return 400, {"ok": False, "msg": "only game profiles can be deleted"}
    with S.profile_lock:
        ok, msg = S.store.delete(key)
        if S.override[slot] == key:
            S.override[slot] = None
        S.reload_profile(slot)
    return 200, {"ok": ok, "msg": msg, "active": S.active[slot]}


def _use(body):
    slot = _slot(body)
    key = _key_for(slot, body.get("key")) if slot is not None else None
    if key is None or not S.use_profile(slot, key):
        return 400, {"ok": False, "msg": "no such profile"}
    return 200, {"ok": True, "active": S.active[slot]}


def _power(body):
    slot = _slot(body)
    if slot is None or not S.alive[slot]:
        return 400, {"ok": False, "msg": "pad not connected"}
    rx_net.send_cmd(slot, CMD_POWER_OFF)
    return 200, {"ok": True, "msg": "Power off sent"}


POST_ROUTES = {"/api/apply": _apply, "/api/use": _use, "/api/delete": _delete, "/api/power": _power}


class Handler(BaseHTTPRequestHandler):
    server_version = "padbridge"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        h = self.headers.get("Host", "")
        h = h.rsplit(":", 1)[0] if not h.endswith("]") else h
        return h.strip("[]") in ("127.0.0.1", "localhost")

    def _tok_ok(self, tok):
        return hmac.compare_digest((tok or "").encode(), token.encode())

    def do_GET(self):
        u = urlparse(self.path)
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        if u.path == "/":
            if not self._tok_ok(parse_qs(u.query).get("t", [""])[0]):
                return self._send(403, b"forbidden - use the URL printed by the receiver", "text/plain")
            try:
                with open(HTML_PATH, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                return self._send(500, b"rx_remap_ui.html missing", "text/plain")
        if not self._tok_ok(self.headers.get("X-Token")):
            return self._send(403, {"ok": False, "msg": "bad token"})
        if u.path == "/api/pads":
            return self._send(200, _pads())
        if u.path == "/api/live":
            return self._send(200, _live())
        self._send(404, {"ok": False, "msg": "not found"})

    def do_POST(self):
        if not self._host_ok() or not self._tok_ok(self.headers.get("X-Token")):
            return self._send(403, {"ok": False, "msg": "forbidden"})
        route = POST_ROUTES.get(urlparse(self.path).path)
        if route is None:
            return self._send(404, {"ok": False, "msg": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= MAX_BODY:
                return self._send(400, {"ok": False, "msg": "bad length"})
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, OSError):
            return self._send(400, {"ok": False, "msg": "bad json"})
        code, resp = route(body)
        self._send(code, resp)


def start():
    """Uruchamia serwer edytora. -> URL (z tokenem) albo None, gdy wyłączony / brak wolnego portu."""
    global server, base_url
    if not C.REMAP_UI:
        return None
    base = C.REMAP_UI_PORT
    for port in ([0] if base == 0 else range(base, base + 10)):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        print(f"Remap editor: no free port in {base}..{base + 9} - disabled")
        return None
    server.daemon_threads = True
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"{base_url}/?t={token}"
    print(f"Remap editor: {url}")
    return url


def stop():
    if server is not None:
        server.shutdown()


def _app_browser():
    """Ścieżka do Edge / Chrome (do trybu --app: osobne okno bez kart i paska adresu) albo None."""
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        for rel in (("Microsoft", "Edge", "Application", "msedge.exe"),
                    ("Google", "Chrome", "Application", "chrome.exe")):
            p = os.path.join(base, *rel)
            if os.path.isfile(p):
                return p
    return None


def open_ui(slot=0):
    """Otwiera edytor jako osobne okno aplikacji (Edge/Chrome --app); bez nich - w domyślnej przeglądarce."""
    if base_url is None:
        return False
    url = f"{base_url}/?t={token}&pad={slot + 1}"
    exe = _app_browser() if C.REMAP_UI_APP else None
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--window-size=1260,800"], close_fds=True)
            return True
        except OSError:
            pass
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False
