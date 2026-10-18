"""Receiver: Bluetooth nadajnika (telefon bez ekranu) sterowany z okna - stan adaptera, lista urządzeń i polecenia
(proto: btcmd -> btstate + btdev, nadajnik v10+).

Pakiety przychodzą w wątku sieci (on_packet), okno czyta je w swoim: stan i lista to niezmienne obiekty podmieniane
jednym przypisaniem, więc bez blokad. Lista z nadajnika idzie rundami (btstate z BTF_LIST i ile urządzeń, potem
btdev z numerami); podmieniamy ją dopiero po całej rundzie - zgubiony pakiet zostawia poprzednią listę, nie dziurawą.
Polecenia idą 3x (UDP, ten sam pakiet rozłożony w czasie), nadajnik odrzuca powtórki. Polecenie tylko do nadajnika,
który przysłał btstate - starszy odrzuciłby nieznaną długość i okno czekałoby w nieskończoność.
"""
import time
from dataclasses import dataclass, field

import rx_events as ev
import rx_net
from proto import (BODY, BTC_ACTIVE, BTC_BLOCKED, BTC_FIXED, BTC_PICKED, BTC_USB, BTD_CONNECTED, BTD_PAD, BTD_PAIRED,
                   BTD_TRUSTED, BTF_BUSY, BTF_HAS_BUILTIN, BTF_HAS_USB, BTF_MODE_SHIFT, BTF_ON_USB, BTF_SCANNING,
                   BTR_AUTH, BTR_BUSY, BTR_DENIED, BTR_FAILED, BTR_NONE, BTR_NOT_FOUND, BTR_OFF, BTR_OK, BTR_RUNNING,
                   BTR_TIMEOUT, BTS_BLOCKED, BTS_DENIED, BTS_ERROR, BTS_NO_ADAPTER, BTS_NO_SERVICE, BTS_NO_TOOL,
                   BTS_OFF, BTS_OK, BT_ADAPTER, BT_ADAPTER_USE, BT_CONNECT, BT_DISCONNECT, BT_LIST, BT_PAIR,
                   BT_POWER, BT_REMOVE, BT_RSSI_NONE, BT_SCAN_MAX_S, BT_SCAN_START, BT_SCAN_STOP, T_BTCMD, T_BTCTRL,
                   T_BTDEV, T_BTSTATE, cstr, mac_bytes, mac_text)
from rx_i18n import _

# operacje na urządzeniu / adapterze
OPS = frozenset({BT_PAIR, BT_CONNECT, BT_DISCONNECT, BT_REMOVE, BT_POWER, BT_ADAPTER, BT_ADAPTER_USE})
BTF_LIST = 4                 # flaga btstate: zaraz za nim runda listy (sender_bt.BTF_LIST)
STALE_S = 12.0               # stan starszy niż tyle (bez panelu nadajnik wysyła go co 5 s) = nieznany
KEEPALIVE_S = 2.0            # otwarty panel ponawia BT_LIST co tyle (nadajnik wysyła listę przez BT_ACTIVE_S)
PENDING_S = 3.0              # polecenie bez odpowiedzi nadajnika (zgubione) przestaje być "w toku" po tylu sekundach

STATE_TEXT = {
    BTS_OK: "włączony", BTS_OFF: "wyłączony", BTS_BLOCKED: "zablokowany (rfkill)", BTS_NO_ADAPTER: "brak adaptera",
    BTS_NO_SERVICE: "usługa bluetooth nie działa", BTS_DENIED: "brak uprawnień (nadajnik musi działać jako root)",
    BTS_NO_TOOL: "brak busctl (systemd) na telefonie", BTS_ERROR: "błąd odczytu stanu",
}
OP_TEXT = {BT_PAIR: "Parowanie", BT_CONNECT: "Łączenie", BT_DISCONNECT: "Rozłączanie", BT_REMOVE: "Usuwanie",
           BT_POWER: "Zasilanie Bluetooth", BT_SCAN_START: "Skanowanie", BT_ADAPTER: "Zmiana kontrolera",
           BT_ADAPTER_USE: "Zmiana kontrolera"}
DONE_TEXT = {BT_PAIR: "sparowano i połączono", BT_CONNECT: "połączono", BT_DISCONNECT: "rozłączono",
             BT_REMOVE: "usunięto z telefonu", BT_POWER: "gotowe", BT_SCAN_START: "rozpoczęte",
             BT_ADAPTER: "gotowe", BT_ADAPTER_USE: "gotowe"}
RESULT_TEXT = {
    BTR_FAILED: "nie udało się", BTR_TIMEOUT: "brak odpowiedzi urządzenia",
    BTR_NOT_FOUND: "telefon nie widzi urządzenia - uruchom skanowanie",
    BTR_AUTH: "parowanie odrzucone - włącz tryb parowania na padzie", BTR_DENIED: "brak uprawnień",
    BTR_OFF: "Bluetooth telefonu wyłączony", BTR_BUSY: "trwa inna operacja - spróbuj za chwilę",
}


@dataclass(frozen=True, slots=True)
class Device:
    mac: str
    name: str
    rssi: int | None
    flags: int

    @property
    def paired(self) -> bool:
        return bool(self.flags & BTD_PAIRED)

    @property
    def connected(self) -> bool:
        return bool(self.flags & BTD_CONNECTED)

    @property
    def trusted(self) -> bool:
        return bool(self.flags & BTD_TRUSTED)

    @property
    def pad(self) -> bool:
        return bool(self.flags & BTD_PAD)

    def bars(self) -> int:
        """Siła sygnału 0-4 (0 = brak pomiaru - poza skanem BlueZ nie podaje RSSI)."""
        if self.rssi is None:
            return 0
        return sum(self.rssi >= t for t in (-90, -78, -66, -55))


@dataclass(frozen=True, slots=True)
class Controller:
    """Kontroler Bluetooth nadajnika (wbudowany / dongle USB) z listy btctrl."""
    index: int            # N z hciN
    name: str             # nazwa urządzenia
    flags: int

    @property
    def usb(self) -> bool:
        return bool(self.flags & BTC_USB)

    @property
    def fixed(self) -> bool:
        return bool(self.flags & BTC_FIXED)

    @property
    def active(self) -> bool:
        return bool(self.flags & BTC_ACTIVE)

    @property
    def blocked(self) -> bool:
        return bool(self.flags & BTC_BLOCKED)

    @property
    def picked(self) -> bool:
        return bool(self.flags & BTC_PICKED)


@dataclass(frozen=True, slots=True)
class Status:
    state: int
    flags: int
    scan_left: int
    connected: int
    op: int
    result: int
    mac: str
    t: float

    @property
    def scanning(self) -> bool:
        return bool(self.flags & BTF_SCANNING)

    @property
    def busy(self) -> bool:
        return bool(self.flags & BTF_BUSY) or self.result == BTR_RUNNING

    @property
    def mode(self) -> int:
        """Tryb wyboru kontrolera BTA_* (0 = nadajnik bez wyboru - przełącznik ukryty)."""
        return self.flags >> BTF_MODE_SHIFT & 3

    @property
    def has_usb(self) -> bool:
        return bool(self.flags & BTF_HAS_USB)

    @property
    def has_builtin(self) -> bool:
        return bool(self.flags & BTF_HAS_BUILTIN)

    @property
    def on_usb(self) -> bool:
        return bool(self.flags & BTF_ON_USB)


@dataclass(slots=True)
class _Round:
    gen: int
    total: int
    got: dict[int, Device] = field(default_factory=dict)


status: Status | None = None
devices: tuple[Device, ...] = ()
controllers: tuple[Controller, ...] = ()       # kontrolery nadajnika (nadajnik z wyborem kontrolera)
_cround: _Round | None = None
pending: tuple[int, str, float] | None = None   # wysłane polecenie (op, MAC, czas) - przycisk "w toku" od razu
_round: _Round | None = None
_logged: tuple[int, int, str] | None = None


def reset() -> None:
    global status, devices, pending, _round, _logged, controllers, _cround
    status, devices, pending, _round, _logged, controllers, _cround = None, (), None, None, None, (), None


# ---------- wątek sieci ----------
def on_packet(t: int, data: bytes, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    v = BODY[t].unpack_from(data, 4)
    if t == T_BTSTATE:
        _on_state(v, now)
    elif t == T_BTDEV:
        _on_dev(v)
    elif t == T_BTCTRL:
        _on_ctrl(v)


def _on_state(v: tuple, now: float) -> None:
    global status, devices, _round, controllers
    st, flags, left, conn, n, gen, op, res, mac = v
    prev, status = status, Status(st, flags, left, conn, op, res, mac_text(mac), now)
    if prev is None:
        print(f"Sender Bluetooth: {STATE_TEXT.get(st, st)}")
    if not flags & (BTF_HAS_USB | BTF_HAS_BUILTIN):   # starszy nadajnik albo brak kontrolerów - lista pusta
        controllers = ()
    if flags & BTF_LIST:
        if n == 0:
            devices, _round = (), None
        else:
            _round = _Round(gen, n)
    _log_result(status)


def _on_dev(v: tuple) -> None:
    global devices, _round
    gen, k, total, mac, rssi, flags, name = v
    r = _round
    if r is None or r.gen != gen or r.total != total or k >= total:
        return
    r.got[k] = Device(mac_text(mac), cstr(name) or mac_text(mac), None if rssi == BT_RSSI_NONE else rssi, flags)
    if len(r.got) == total:
        devices, _round = tuple(r.got[i] for i in range(total)), None


def _on_ctrl(v: tuple) -> None:
    """Runda kontrolerów: podmiana listy dopiero, gdy jest cała (jak urządzenia)."""
    global controllers, _cround
    gen, k, total, flags, index, name = v
    r = _cround
    if r is None or r.gen != gen or r.total != total:
        r = _cround = _Round(gen, total)
    if k >= total:
        return
    r.got[k] = Controller(index, cstr(name) or f"hci{index}", flags)
    if len(r.got) == total:
        controllers, _cround = tuple(r.got[i] for i in range(total)), None


def _log_result(s: Status) -> None:
    """Wynik operacji raz do dziennika (stan przychodzi co sekundę z tą samą ostatnią operacją)."""
    global _logged, pending
    key = (s.op, s.result, s.mac)
    if s.result in (BTR_NONE, BTR_RUNNING) or s.op == BT_LIST or key == _logged:
        if s.result == BTR_RUNNING:
            _logged = None
        return
    _logged = key
    if pending is not None and pending[:2] == (s.op, s.mac):
        pending = None
    if s.op == BT_SCAN_START and s.result == BTR_OK:
        return
    text = f"Bluetooth: {result_text(s.op, s.result, s.mac)}"
    (ev.info if s.result == BTR_OK else ev.warn)(text)


# ---------- okno ----------
def supported(now: float | None = None) -> bool:
    """Nadajnik obsługuje Bluetooth z okna i odezwał się niedawno."""
    s = status
    return s is not None and (time.monotonic() if now is None else now) - s.t < STALE_S


def device(mac: str) -> Device | None:
    return next((d for d in devices if d.mac == mac), None)


def name(mac: str) -> str:
    d = device(mac)
    return d.name if d is not None else mac


def result_text(op: int, result: int, mac: str = "") -> str:
    what = _(OP_TEXT.get(op, "Operacja"))
    who = f" {name(mac)}" if mac else ""
    if result == BTR_RUNNING:
        return f"{what}{who}…"
    tail = _(DONE_TEXT.get(op, "gotowe")) if result == BTR_OK else _(RESULT_TEXT.get(result, "nie udało się"))
    return f"{what}{who}: {tail}"


def busy_op(mac: str, now: float | None = None) -> int | None:
    """Operacja w toku dla urządzenia (z nadajnika albo właśnie wysłana) -> BT_* albo None."""
    now = time.monotonic() if now is None else now
    s = status
    if s is not None and s.result == BTR_RUNNING and s.mac == mac:
        return s.op
    p = pending
    if p is not None and p[1] == mac and now - p[2] < PENDING_S:
        return p[0]
    return None


def any_busy(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    s, p = status, pending
    return (s is not None and s.busy) or (p is not None and now - p[2] < PENDING_S)


def send(op: int, arg: int = 0, mac: str = "") -> bool:
    """Polecenie do nadajnika (3x). -> False, gdy nadajnik go nie zna (brak btstate) albo brak adresu."""
    global pending, _logged
    if not supported() or rx_net.codec is None:
        return False
    raw = mac_bytes(mac) or bytes(6)
    if op in OPS:                      # operacja w toku od razu (skan i keepalive nie blokują przycisków)
        pending = (op, mac_text(raw), time.monotonic())
        _logged = None                 # ta sama operacja drugi raz też trafi do dziennika
    # ten sam pakiet (ten sam numer) teraz i dwa razy później - jak polecenia pada (rx_net.send_repeat): trzy kopie
    # naraz ginęły razem przy jednej serii strat, a każda z osobnym numerem była dla nadajnika osobnym poleceniem
    rx_net.send_repeat(rx_net.codec.pack(T_BTCMD, 255, op, arg, raw), 3)
    return True


def keepalive() -> bool:
    """Panel otwarty: nadajnik wysyła listę urządzeń co sekundę przez BT_ACTIVE_S."""
    return send(BT_LIST)


def scan(on: bool, secs: int = BT_SCAN_MAX_S) -> bool:
    return send(BT_SCAN_START, secs) if on else send(BT_SCAN_STOP)


def pair(mac: str) -> bool:
    return send(BT_PAIR, 0, mac)


def connect(mac: str) -> bool:
    return send(BT_CONNECT, 0, mac)


def disconnect(mac: str) -> bool:
    return send(BT_DISCONNECT, 0, mac)


def remove(mac: str) -> bool:
    return send(BT_REMOVE, 0, mac)


def power(on: bool) -> bool:
    return send(BT_POWER, int(on))


def adapter(mode: int) -> bool:
    """Tryb wyboru kontrolera BTA_AUTO / BTA_MANUAL (tylko nadajnik, który go podaje w btstate)."""
    s = status
    return s is not None and s.mode != 0 and send(BT_ADAPTER, mode)


def use_controller(index: int) -> bool:
    """Konkretny kontroler hciN z listy - nadajnik zapamiętuje go po identyfikatorze urządzenia."""
    s = status
    return s is not None and s.mode != 0 and send(BT_ADAPTER_USE, index)


def active_controller() -> Controller | None:
    return next((c for c in controllers if c.active), None)


def indicator(now: float | None = None) -> tuple[str, str]:
    """Kropka stanu Bluetooth w nagłówku: (poziom, opis). Poziom: ok / scan / warn / bad / "" (szary - nieznany)."""
    now = time.monotonic() if now is None else now
    s = status
    if s is None:
        return "", _("Bluetooth nadajnika: brak danych (nadajnik offline albo starsza wersja - Aktualizuj)")
    if now - s.t >= STALE_S:
        return "", _("Bluetooth nadajnika: brak danych od {s} s").format(s=int(now - s.t))
    head = _("Bluetooth nadajnika: {state}").format(state=_(STATE_TEXT.get(s.state, "błąd odczytu stanu")))
    if s.state != BTS_OK:
        return ("warn" if s.state == BTS_OFF else "bad"), head
    head += "  ·  " + _("połączone: {n}").format(n=s.connected)
    if (c := active_controller()) is not None:
        head += "  ·  " + c.name
    elif s.on_usb:
        head += "  ·  " + _("dongle USB")
    if s.scanning:
        return "scan", head + "  ·  " + _("skanowanie {s} s").format(s=s.scan_left)
    return "ok", head
