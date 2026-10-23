"""Receiver: the sender's Bluetooth (a phone without a screen) controlled from the window - the adapter state, the device list and commands
(proto: btcmd -> btstate + btdev, sender v10+).

Packets arrive in the network thread (on_packet), the window reads them in its own: the state and the list are immutable objects replaced
with a single assignment, so no locks are needed. The sender's list comes in rounds (btstate with BTF_LIST and the device count, then
btdev with numbers); it is replaced only after the whole round - a lost packet leaves the previous list, not a gappy one.
Commands go out 3x (UDP, the same packet spread over time), the sender drops repeats. A command goes only to a sender
that has sent btstate - an older one would reject the unknown length and the window would wait forever.
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

# operations on a device / the adapter
OPS = frozenset({BT_PAIR, BT_CONNECT, BT_DISCONNECT, BT_REMOVE, BT_POWER, BT_ADAPTER, BT_ADAPTER_USE})
BTF_LIST = 4                 # btstate flag: a list round follows right after it (sender_bt.BTF_LIST)
STALE_S = 12.0               # a state older than this (without the panel the sender sends it every 5 s) = unknown
KEEPALIVE_S = 2.0            # an open panel renews BT_LIST this often (the sender sends the list for BT_ACTIVE_S)
PENDING_S = 3.0              # a command without a (lost) sender answer stops being "in progress" after this many seconds

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
    """A device from the sender's list: MAC, name, RSSI (None outside a scan) and the BTD_* flags."""
    mac: str
    name: str
    rssi: int | None
    flags: int

    @property
    def paired(self) -> bool:
        """Whether the device is paired with the phone."""
        return bool(self.flags & BTD_PAIRED)

    @property
    def connected(self) -> bool:
        """Whether the device is connected."""
        return bool(self.flags & BTD_CONNECTED)

    @property
    def trusted(self) -> bool:
        """Whether the device is trusted (it may reconnect by itself)."""
        return bool(self.flags & BTD_TRUSTED)

    @property
    def pad(self) -> bool:
        """Whether the device is a game controller."""
        return bool(self.flags & BTD_PAD)

    def bars(self) -> int:
        """Signal strength 0-4 (0 = no measurement - outside a scan BlueZ gives no RSSI)."""
        if self.rssi is None:
            return 0
        return sum(self.rssi >= t for t in (-90, -78, -66, -55))


@dataclass(frozen=True, slots=True)
class Controller:
    """A Bluetooth controller of the sender (built-in / USB dongle) from the btctrl list."""
    index: int            # N from hciN
    name: str             # device name
    flags: int

    @property
    def usb(self) -> bool:
        """Whether the controller is on USB."""
        return bool(self.flags & BTC_USB)

    @property
    def fixed(self) -> bool:
        """Whether the USB controller sits on a fixed port (a laptop)."""
        return bool(self.flags & BTC_FIXED)

    @property
    def active(self) -> bool:
        """Whether the sender uses this controller now."""
        return bool(self.flags & BTC_ACTIVE)

    @property
    def blocked(self) -> bool:
        """Whether the controller is blocked (rfkill)."""
        return bool(self.flags & BTC_BLOCKED)

    @property
    def picked(self) -> bool:
        """Whether the controller was chosen manually in the window."""
        return bool(self.flags & BTC_PICKED)


@dataclass(frozen=True, slots=True)
class Status:
    """The last btstate: adapter state BTS_*, flags, seconds of scan left, connected devices, the last operation with its result and MAC,
    and the receive time."""
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
        """Whether a scan is running."""
        return bool(self.flags & BTF_SCANNING)

    @property
    def busy(self) -> bool:
        """Whether an operation is running."""
        return bool(self.flags & BTF_BUSY) or self.result == BTR_RUNNING

    @property
    def mode(self) -> int:
        """The controller choice mode BTA_* (0 = a sender without choice - the switch is hidden)."""
        return self.flags >> BTF_MODE_SHIFT & 3

    @property
    def has_usb(self) -> bool:
        """Whether the sender has a USB controller."""
        return bool(self.flags & BTF_HAS_USB)

    @property
    def has_builtin(self) -> bool:
        """Whether the sender has a built-in controller."""
        return bool(self.flags & BTF_HAS_BUILTIN)

    @property
    def on_usb(self) -> bool:
        """Whether the USB controller is in use."""
        return bool(self.flags & BTF_ON_USB)


@dataclass(slots=True)
class _Round:
    """A device / controller list round being collected: its generation, the expected count and the entries received so far."""
    gen: int
    total: int
    got: dict[int, Device] = field(default_factory=dict)


status: Status | None = None
devices: tuple[Device, ...] = ()
controllers: tuple[Controller, ...] = ()       # the sender's controllers (a sender with controller choice)
_cround: _Round | None = None
pending: tuple[int, str, float] | None = None   # the sent command (op, MAC, time) - the button is "in progress" at once
_round: _Round | None = None
_logged: tuple[int, int, str] | None = None


def reset() -> None:
    """Forget everything (a new sender session)."""
    global status, devices, pending, _round, _logged, controllers, _cround
    status, devices, pending, _round, _logged, controllers, _cround = None, (), None, None, None, (), None


# ---------- network thread ----------
def on_packet(t: int, data: bytes, now: float | None = None) -> None:
    """Dispatch a Bluetooth packet (btstate / btdev / btctrl) received in the network thread."""
    now = time.monotonic() if now is None else now
    v = BODY[t].unpack_from(data, 4)
    if t == T_BTSTATE:
        _on_state(v, now)
    elif t == T_BTDEV:
        _on_dev(v)
    elif t == T_BTCTRL:
        _on_ctrl(v)


def _on_state(v: tuple, now: float) -> None:
    """Store the new state; a BTF_LIST state starts (or, with 0 devices, clears) a device round; log a finished operation."""
    global status, devices, _round, controllers
    st, flags, left, conn, n, gen, op, res, mac = v
    prev, status = status, Status(st, flags, left, conn, op, res, mac_text(mac), now)
    if prev is None:
        print(f"Sender Bluetooth: {STATE_TEXT.get(st, st)}")
    if not flags & (BTF_HAS_USB | BTF_HAS_BUILTIN):   # an older sender or no controllers - the list is empty
        controllers = ()
    if flags & BTF_LIST:
        if n == 0:
            devices, _round = (), None
        else:
            _round = _Round(gen, n)
    _log_result(status)


def _on_dev(v: tuple) -> None:
    """Add a device to the current round; the list is replaced only when all `total` devices of that round have arrived."""
    global devices, _round
    gen, k, total, mac, rssi, flags, name = v
    r = _round
    if r is None or r.gen != gen or r.total != total or k >= total:
        return
    r.got[k] = Device(mac_text(mac), cstr(name) or mac_text(mac), None if rssi == BT_RSSI_NONE else rssi, flags)
    if len(r.got) == total:
        devices, _round = tuple(r.got[i] for i in range(total)), None


def _on_ctrl(v: tuple) -> None:
    """A controller round: the list is replaced only when it is complete (like devices)."""
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
    """Log an operation result once (the state arrives every second with the same last operation)."""
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


# ---------- window ----------
def supported(now: float | None = None) -> bool:
    """The sender supports Bluetooth from the window and has answered recently."""
    s = status
    return s is not None and (time.monotonic() if now is None else now) - s.t < STALE_S


def device(mac: str) -> Device | None:
    """The device with this MAC from the current list, or None."""
    return next((d for d in devices if d.mac == mac), None)


def name(mac: str) -> str:
    """The device name, or the MAC when it is not in the list."""
    d = device(mac)
    return d.name if d is not None else mac


def result_text(op: int, result: int, mac: str = "") -> str:
    """A readable (translated) description of an operation and its result for the log / window."""
    what = _(OP_TEXT.get(op, "Operacja"))
    who = f" {name(mac)}" if mac else ""
    if result == BTR_RUNNING:
        return f"{what}{who}…"
    tail = _(DONE_TEXT.get(op, "gotowe")) if result == BTR_OK else _(RESULT_TEXT.get(result, "nie udało się"))
    return f"{what}{who}: {tail}"


def busy_op(mac: str, now: float | None = None) -> int | None:
    """The operation in progress for a device (from the sender or just sent) -> BT_* or None."""
    now = time.monotonic() if now is None else now
    s = status
    if s is not None and s.result == BTR_RUNNING and s.mac == mac:
        return s.op
    p = pending
    if p is not None and p[1] == mac and now - p[2] < PENDING_S:
        return p[0]
    return None


def any_busy(now: float | None = None) -> bool:
    """Whether any operation is in progress (reported by the sender or just sent)."""
    now = time.monotonic() if now is None else now
    s, p = status, pending
    return (s is not None and s.busy) or (p is not None and now - p[2] < PENDING_S)


def send(op: int, arg: int = 0, mac: str = "") -> bool:
    """A command to the sender (3x). -> False when the sender does not know it (no btstate) or there is no address."""
    global pending, _logged
    if not supported() or rx_net.codec is None:
        return False
    raw = mac_bytes(mac) or bytes(6)
    if op in OPS:                      # the operation is in progress at once (scan and keepalive do not block the buttons)
        pending = (op, mac_text(raw), time.monotonic())
        _logged = None                 # the same operation a second time also reaches the log
    # the same packet (the same number) now and twice later - like the pad commands (rx_net.send_repeat): three copies
    # at once vanished together in one loss burst, and each with its own number was a separate command for the sender
    rx_net.send_repeat(rx_net.codec.pack(T_BTCMD, 255, op, arg, raw), 3)
    return True


def keepalive() -> bool:
    """The panel is open: the sender sends the device list every second for BT_ACTIVE_S."""
    return send(BT_LIST)


def scan(on: bool, secs: int = BT_SCAN_MAX_S) -> bool:
    """Start a scan for `secs` seconds (at most BT_SCAN_MAX_S) or stop it."""
    return send(BT_SCAN_START, secs) if on else send(BT_SCAN_STOP)


def pair(mac: str) -> bool:
    """Ask the sender to pair the device and connect to it."""
    return send(BT_PAIR, 0, mac)


def connect(mac: str) -> bool:
    """Ask the sender to connect the device."""
    return send(BT_CONNECT, 0, mac)


def disconnect(mac: str) -> bool:
    """Ask the sender to disconnect the device."""
    return send(BT_DISCONNECT, 0, mac)


def remove(mac: str) -> bool:
    """Ask the sender to forget (remove) the device."""
    return send(BT_REMOVE, 0, mac)


def power(on: bool) -> bool:
    """Switch the sender's Bluetooth adapter on / off."""
    return send(BT_POWER, int(on))


def adapter(mode: int) -> bool:
    """The controller choice mode BTA_AUTO / BTA_MANUAL (only for a sender that reports it in btstate)."""
    s = status
    return s is not None and s.mode != 0 and send(BT_ADAPTER, mode)


def use_controller(index: int) -> bool:
    """A specific hciN controller from the list - the sender remembers it by the device identifier."""
    s = status
    return s is not None and s.mode != 0 and send(BT_ADAPTER_USE, index)


def active_controller() -> Controller | None:
    """The controller the sender uses now, or None."""
    return next((c for c in controllers if c.active), None)


def indicator(now: float | None = None) -> tuple[str, str]:
    """The Bluetooth dot in the header: (level, description). Level: ok / scan / warn / bad / "" (grey - unknown)."""
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
