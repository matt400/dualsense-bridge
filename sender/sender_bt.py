"""Sender: Bluetooth telefonu sterowany z okna receivera - skan, parowanie, łączenie i rozłączanie padów bez ekranu
na telefonie (proto: btcmd -> btstate + btdev).

BlueZ przez D-Bus: `busctl --json=short` (systemd, bez dodatkowych pakietów) - stan adaptera i urządzeń
(ObjectManager.GetManagedObjects), Pair / Connect / Disconnect / RemoveDevice, Powered / Trusted. Wyszukiwanie BlueZ
kończy, gdy rozłączy się klient D-Bus, który je zaczął - jednorazowe `busctl call StartDiscovery` nic by nie dało.
Skan to więc proces `bluetoothctl --timeout N scan on`: trzyma sesję przez N s i sam ją kończy (auto-stop), a
zatrzymanie z okna to zakończenie procesu. Skan zabiera radio połączonym padom (skoki opóźnienia), dlatego zawsze ma
limit BT_SCAN_MAX_S, a parowanie i łączenie go przerywają (BlueZ łączy pewniej bez wyszukiwania).

Bez otwartego panelu w oknie: tylko stan adaptera co IDLE_S (kropka w nagłówku). Po każdym btcmd przez BT_ACTIVE_S
(okno ponawia BT_LIST, dopóki panel jest otwarty) i w trakcie skanu / operacji: stan i lista urządzeń co sekundę.
Operacje idą po jednej (BlueZ odrzuca równoległe); druga w trakcie -> wynik BTR_BUSY. Brak busctl, bluetoothd,
adaptera albo uprawnień to stan BTS_* w oknie, nie wyjątek - pętla działa dalej.
Przy kilku kontrolerach (wbudowany + dongle USB) wybór robi sender_btadapter - przy każdym odświeżeniu stanu, więc
dongle wpięty w trakcie pracy przejmuje pady bez restartu; tryb (auto / wbudowany / ręcznie) można zmienić z okna.
"""
import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from subprocess import DEVNULL, PIPE
from typing import Any

import sender_btadapter as btadapter
import sender_net as net
import sender_state as state
from proto import (BODY, BTA_MANUAL, BTA_PICK, BTC_ACTIVE, BTC_BLOCKED, BTC_FIXED, BTC_PICKED, BTC_USB,
                   BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTD_TRUSTED, BTF_BUSY, BTF_HAS_BUILTIN, BTF_HAS_USB,
                   BTF_MODE_SHIFT, BTF_ON_USB, BTF_SCANNING, BTR_AUTH, BTR_BUSY, BTR_DENIED, BTR_FAILED, BTR_NONE,
                   BTR_NOT_FOUND, BTR_OFF, BTR_OK, BTR_RUNNING, BTR_TIMEOUT, BTS_BLOCKED, BTS_DENIED, BTS_ERROR,
                   BTS_NO_ADAPTER, BTS_NO_SERVICE, BTS_NO_TOOL, BTS_OFF, BTS_OK, BT_ACTIVE_S, BT_ADAPTER,
                   BT_ADAPTER_USE, BT_CONNECT, BT_DEV_MAX, BT_DISCONNECT, BT_LIST, BT_PAIR, BT_POWER, BT_REMOVE,
                   BT_RSSI_NONE, BT_SCAN_MAX_S, BT_SCAN_START, BT_SCAN_STOP, T_BTCTRL, T_BTDEV, T_BTSTATE, clamp,
                   mac_bytes, mac_text, utf8_field)
from sender_config import C

BTF_LIST = 4                 # flaga btstate (poza proto.BTF_*, bo znaczy tylko "runda listy idzie zaraz za mną")
BUSCTL, BLUETOOTHCTL = "busctl", "bluetoothctl"
BLUEZ, ADAPTER, DEVICE = "org.bluez", "org.bluez.Adapter1", "org.bluez.Device1"
CALL_S, CONNECT_S, PAIR_S = 5.0, 25.0, 40.0   # limity wywołań D-Bus [s] (parowanie czeka na pada)
IDLE_S = 5.0                 # stan adaptera bez otwartego panelu
TICK_S = 1.0                 # stan i lista przy otwartym panelu / skanie
DUP_S = 0.5                  # btcmd przychodzi 3x (UDP) - powtórki tej samej operacji w tym czasie pomijane
NAME_MAX = BODY[T_BTDEV].size - 12
CTRL_NAME_MAX = BODY[T_BTCTRL].size - 6
# błąd z busctl (komunikat BlueZ / D-Bus, małe litery) -> wynik operacji / stan adaptera
RESULTS = (("authentication", BTR_AUTH), ("access denied", BTR_DENIED), ("permission", BTR_DENIED),
           ("not authorized", BTR_DENIED), ("does not exist", BTR_NOT_FOUND), ("unknown object", BTR_NOT_FOUND),
           ("not ready", BTR_OFF), ("rfkill", BTR_OFF), ("in progress", BTR_BUSY), ("busy", BTR_BUSY),
           ("timed out", BTR_TIMEOUT), ("timeout", BTR_TIMEOUT))
STATES = (("access denied", BTS_DENIED), ("permission", BTS_DENIED), ("not authorized", BTS_DENIED),
          ("org.bluez was not provided", BTS_NO_SERVICE), ("serviceunknown", BTS_NO_SERVICE),
          ("not activatable", BTS_NO_SERVICE), ("name has no owner", BTS_NO_SERVICE), ("not found", BTS_NO_SERVICE))

Runner = Callable[[Iterable[str], float], Awaitable[tuple[int, str, str]]]


@dataclass(frozen=True, slots=True)
class Device:
    mac: str
    name: str
    rssi: int | None
    flags: int

    @property
    def sort_key(self) -> tuple:
        """Połączone, sparowane, pady, potem nazwa - bez RSSI, które skacze i przestawiałoby listę w oknie."""
        f = self.flags
        return not f & BTD_CONNECTED, not f & BTD_PAIRED, not f & BTD_PAD, self.name.lower(), self.mac


class BtError(Exception):
    """Nieudane wywołanie busctl: komunikat i wynik BTR_* do okna."""

    def __init__(self, msg: str, result: int = BTR_FAILED):
        super().__init__(msg)
        self.result = result


async def run_cmd(argv: Iterable[str], timeout: float) -> tuple[int, str, str]:
    """Program bez powłoki -> (kod, stdout, stderr); przekroczony czas = zabity proces i kod -1."""
    proc = await asyncio.create_subprocess_exec(*argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()              # bez tego zostaje proces-zombie
        return -1, "", "timed out"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def classify(msg: str, table: tuple[tuple[str, int], ...], default: int) -> int:
    low = (msg or "").lower()
    return next((code for key, code in table if key in low), default)


def dev_path(adapter: str, mac: str) -> str:
    return f"{adapter}/dev_{mac.upper().replace(':', '_')}"


def _val(props: dict[str, Any], key: str, default: Any = None) -> Any:
    """Wartość właściwości z JSON busctl ({"type": "b", "data": true}) albo default."""
    v = props.get(key)
    return v.get("data", default) if isinstance(v, dict) else default


def is_pad(props: dict[str, Any]) -> bool:
    """Pad do gier: ikona BlueZ albo klasa urządzenia peryferia / joystick-gamepad (DualSense: 0x002508)."""
    cls = _val(props, "Class", 0) or 0
    return _val(props, "Icon") == "input-gaming" or ((cls >> 8) & 0x1F == 0x05 and (cls >> 2) & 0x0F in (1, 2))


def parse_objects(text: str, prefer: str = "", info: dict[str, Any] | None = None) -> tuple[int, str, list[Device]]:
    """Wynik `busctl --json=short call ... GetManagedObjects` -> (BTS_*, ścieżka adaptera, urządzenia).
    Adapter wybrany przez sender_btadapter (prefer, np. /org/bluez/hci1), inaczej pierwszy włączony, a gdy żaden nie
    jest włączony - pierwszy (hci0); urządzenia bez nazwy i bez parowania (beacony, telefony w pobliżu) pominięte.
    info (słownik) dostaje "address" wybranego adaptera i "count" - ile adapterów widzi BlueZ (skan: select)."""
    objects: dict[str, dict[str, Any]] = json.loads(text)["data"][0]
    adapters = {path: ifaces[ADAPTER] for path, ifaces in objects.items() if ADAPTER in ifaces}
    if info is not None:
        info.update(address="", count=len(adapters))
    if not adapters:
        return BTS_NO_ADAPTER, "", []
    path = prefer if prefer in adapters else min(adapters, key=lambda p: (not _val(adapters[p], "Powered", False), p))
    a = adapters[path]
    if info is not None:
        info["address"] = str(_val(a, "Address", "") or "").upper()
    if _val(a, "Powered", False):
        st = BTS_OK
    else:
        st = BTS_BLOCKED if _val(a, "PowerState") == "off-blocked" else BTS_OFF
    devices = []
    for p, ifaces in objects.items():
        d = ifaces.get(DEVICE)
        if d is None or not p.startswith(path + "/") or not (mac := mac_bytes(_val(d, "Address", ""))):
            continue
        paired, name = bool(_val(d, "Paired", False)), _val(d, "Name") or ""
        if not (name or paired):
            continue
        flags = ((BTD_PAIRED if paired else 0) | (BTD_CONNECTED if _val(d, "Connected", False) else 0)
                 | (BTD_TRUSTED if _val(d, "Trusted", False) else 0) | (BTD_PAD if is_pad(d) else 0))
        devices.append(Device(mac_text(mac), _val(d, "Alias") or name or mac_text(mac), _val(d, "RSSI"), flags))
    devices.sort(key=lambda d: d.sort_key)
    return st, path, devices[:BT_DEV_MAX]


async def _scan_feed(proc: asyncio.subprocess.Process, address: str, wait_s: float = 3.0) -> None:
    """Skan na wybranym kontrolerze: polecenia do bluetoothctl dopiero, gdy wypisze ten kontroler (BlueZ podaje
    kontrolery asynchronicznie po starcie - wcześniejsze "select" kończyło się "Controller ... not available"), najdłużej
    wait_s. Potem czyta wyjście do końca procesu: pełny potok zatrzymałby bluetoothctl w trakcie skanu."""
    loop = asyncio.get_running_loop()
    end, seen, sent = loop.time() + wait_s, "", False
    try:
        while True:
            try:
                chunk = await (proc.stdout.read(4096) if sent else
                               asyncio.wait_for(proc.stdout.read(4096), max(0.0, end - loop.time())))
            except TimeoutError:
                chunk = None
            if not sent:
                seen = (seen + (chunk or b"").decode(errors="replace").upper())[-256:]
                if chunk is None or address in seen:
                    proc.stdin.write(f"select {address}\nscan on\n".encode())
                    await proc.stdin.drain()
                    sent = True
                    continue
            if not chunk:
                return
    except (OSError, ValueError):            # proces zakończony (zatrzymanie skanu) - potok zamknięty
        return


class Bluetooth:
    """Stan i operacje Bluetooth nadajnika. run: wykonawca poleceń (testy podstawiają własny)."""

    def __init__(self, run: Runner = run_cmd, emit: Callable[..., None] | None = None, sysfs: Path | None = None):
        self.run = run
        self.emit = emit or (lambda *a: net.emit(*a))
        self.state = BTS_ERROR
        self.adapter = ""
        self.adapter_addr = ""                       # adres (MAC) adaptera self.adapter
        self.adapter_count = 0                       # ile adapterów widzi BlueZ
        self.devices: list[Device] = []
        self.last = (BTR_NONE, BTR_NONE, b"")       # ostatnia operacja: (BT_*, BTR_*, MAC 6 B)
        self.busy: asyncio.Task | None = None       # zadanie operacji (testy czekają na nie)
        self.running = False                         # operacja trwa (do jej końca, przed wysłaniem wyniku)
        self.scan: asyncio.subprocess.Process | None = None
        # start / stop skanu po kolei: start to kilka wywołań (refresh, zasilanie, uruchomienie bluetoothctl) - drugi
        # start albo stop w trakcie widział jeszcze self.scan = None, więc pierwszy proces zostawał osierocony (skan
        # zabierał radio padom do BT_SCAN_MAX_S i "Zatrzymaj" w oknie go nie zatrzymywał)
        self.scan_lock = asyncio.Lock()
        self.scan_end = 0.0
        self.active_until = 0.0
        self.gen = 0
        self.dups: dict[tuple[int, int, bytes], float] = {}
        self.wake: asyncio.Event | None = None
        # wybór kontrolera (sender_btadapter): tryb BTA_*, ostatnio widziane kontrolery, wybrany
        self.sysfs = sysfs                           # /sys/class/bluetooth; None = bez wyboru kontrolera (testy)
        self.mode: int | None = None                 # wczytany przy pierwszym użyciu (konfiguracja jest już gotowa)
        self.key = ""                                # BTA_PICK: identyfikator wybranego kontrolera
        self.cgen = 0                                # runda listy kontrolerów
        self.ctrls: tuple[btadapter.Ctrl, ...] | None = None
        self.pick: btadapter.Ctrl | None = None
        self.adapter_err = ""

    # ---------- polecenia z okna (wątek pętli asyncio, z on_readable) ----------
    def on_command(self, op: int, arg: int, mac: bytes, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.active_until = now + BT_ACTIVE_S
        if op != BT_LIST:
            key = (op, arg, mac)
            if now - self.dups.get(key, -DUP_S) < DUP_S:
                return
            self.dups = {k: t for k, t in self.dups.items() if now - t < DUP_S}
            self.dups[key] = now
            if op == BT_SCAN_START:
                state.spawn(self.start_scan(clamp(arg or BT_SCAN_MAX_S, 1, BT_SCAN_MAX_S)))
            elif op == BT_SCAN_STOP:
                state.spawn(self.stop_scan())
            elif op in OPS:
                self.start_op(op, arg, mac)
        self.poke()

    def poke(self) -> None:
        if self.wake is not None:
            self.wake.set()

    def start_op(self, op: int, arg: int, mac: bytes) -> None:
        if self.running:
            self.last = (op, BTR_BUSY, mac)
            return
        self.running, self.last = True, (op, BTR_RUNNING, mac)
        self.busy = state.spawn(self._op(op, arg, mac))

    async def _op(self, op: int, arg: int, mac: bytes) -> None:
        what = " ".join(filter(None, (OP_NAMES[op], mac_text(mac), "on" if op == BT_POWER and arg else "")))
        res = BTR_FAILED
        try:
            try:
                await self.refresh()
                await OPS[op](self, arg, mac_text(mac))
                res = BTR_OK
            except BtError as e:
                res = e.result
                print(f"Bluetooth {what} failed: {e}")
            except Exception as e:                   # operacja nie może zatrzymać nadajnika
                print(f"Bluetooth {what} error: {e!r}")
            else:
                print(f"Bluetooth {what}: OK")
            await self.refresh()
        finally:
            # zawsze: wyjątek po operacji (np. w odświeżeniu) zostawiał running = True - każda następna operacja
            # z okna dostawała "trwa inna operacja" aż do restartu nadajnika
            self.running, self.last = False, (op, res, mac)
        self.send(full=True)
        self.poke()

    # ---------- D-Bus ----------
    async def busctl(self, *args: str, timeout: float = CALL_S) -> str:
        try:
            rc, out, err = await self.run((BUSCTL, "--json=short", f"--timeout={timeout:g}", *args), timeout + 2)
        except FileNotFoundError:
            raise BtError("busctl not found (systemd)", BTR_FAILED) from None
        except OSError as e:                         # brak prawa wykonania, fork bez pamięci (EAGAIN / ENOMEM)
            raise BtError(f"cannot run busctl: {e}", BTR_DENIED if isinstance(e, PermissionError) else BTR_FAILED) from None
        if rc:
            msg = (err or out).strip() or f"exit {rc}"
            raise BtError(msg, classify(msg, RESULTS, BTR_FAILED))
        return out

    async def call(self, path: str, iface: str, method: str, *args: str, timeout: float = CALL_S) -> str:
        return await self.busctl("call", BLUEZ, path, iface, method, *args, timeout=timeout)

    async def set_prop(self, path: str, iface: str, name: str, value: bool) -> None:
        await self.busctl("set-property", BLUEZ, path, iface, name, "b", "true" if value else "false")

    async def refresh(self) -> None:
        """Stan adaptera i lista urządzeń z BlueZ. Błąd = stan BTS_* (okno pokazuje powód), lista bez zmian."""
        await self.check_adapters()
        try:
            rc, out, err = await self.run((BUSCTL, "--json=short", f"--timeout={CALL_S:g}", "call", BLUEZ, "/",
                                           "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"), CALL_S + 2)
        except FileNotFoundError:
            self.state, self.adapter, self.devices = BTS_NO_TOOL, "", []
            return
        except OSError as e:                         # inny błąd uruchomienia busctl - stan w oknie, nie wyjątek w pętli
            self.state = BTS_DENIED if isinstance(e, PermissionError) else BTS_ERROR
            print(f"Bluetooth: cannot run busctl: {e}")
            return
        if rc:
            self.state = classify(err or out, STATES, BTS_ERROR)
            return
        try:
            info: dict[str, Any] = {}
            self.state, self.adapter, self.devices = parse_objects(out, self.prefer(), info)
            self.adapter_addr, self.adapter_count = info["address"], info["count"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            print(f"Bluetooth: unexpected busctl output: {e!r}")
            self.state = BTS_ERROR

    # ---------- wybór kontrolera ----------
    def get_mode(self) -> int:
        """Tryb BTA_* (i self.key przy BTA_PICK): bt.json z okna, inaczej bt_adapter z sender.toml."""
        if self.mode is None:
            default = btadapter.mode_from(getattr(C, "BT_ADAPTER", "auto"))
            self.mode, self.key = btadapter.load_mode(getattr(C, "BT_FILE", ""), default)
        return self.mode

    def prefer(self) -> str:
        return f"/org/bluez/{self.pick.name}" if self.pick is not None else ""

    async def check_adapters(self, force: bool = False) -> None:
        """Kontrolery z /sys; przy zmianie zestawu (wpięty / wyjęty dongle) albo trybu - wybór od nowa. Błąd zapisu
        (nadajnik bez roota) raz w logu, nie wyjątek."""
        if self.sysfs is None:
            return
        try:
            ctrls = btadapter.scan(self.sysfs)
        except OSError:
            ctrls = ()
        if not force and ctrls == self.ctrls and not self.adapter_err:
            return                                   # po błędzie: ponowienie przy każdym odświeżeniu, aż się uda
        self.ctrls, old = ctrls, self.pick
        mode = self.get_mode()
        try:
            self.pick = await asyncio.to_thread(btadapter.apply, ctrls, mode, self.key, self.sysfs)
        except OSError as e:
            self.pick = btadapter.choose(ctrls, mode, self.key)
            if str(e) != self.adapter_err:
                print(f"Bluetooth adapter: cannot switch ({e}) - run the sender as root")
            self.adapter_err = str(e) or repr(e)
            return
        recovered = bool(self.adapter_err)           # wcześniej nieudane: wybrany dopiero teraz odblokowany
        if recovered:
            print("Bluetooth adapter: switched after an earlier error")
            self.adapter_err = ""
        if len(ctrls) > 1 or force:
            print(btadapter.describe(ctrls, self.pick, mode))
        if self.pick is not None and (self.pick != old or recovered):
            # odblokowany kontroler BlueZ zwykle włącza sam (AutoEnable); gdy nie - tu, bez czekania na okno
            with contextlib.suppress(BtError):
                await self.set_prop(self.prefer(), ADAPTER, "Powered", True)

    async def set_mode(self, mode: int, key: str = "") -> None:
        """Z okna: BTA_AUTO / BTA_MANUAL (BT_ADAPTER) albo konkretny kontroler (BT_ADAPTER_USE -> BTA_PICK)."""
        if self.sysfs is None:
            raise BtError("adapter choice disabled")
        if mode not in btadapter.MODE_NAMES or (mode == BTA_PICK) != bool(key):
            raise BtError(f"unknown adapter mode {mode}")
        was = self.get_mode()
        self.mode, self.key = mode, key
        btadapter.save_mode(getattr(C, "BT_FILE", ""), mode, key)
        if mode == BTA_MANUAL and was != BTA_MANUAL:
            await asyncio.to_thread(btadapter.unblock_all, self.ctrls or btadapter.scan(self.sysfs), self.sysfs)
        await self.check_adapters(force=True)

    async def use_adapter(self, index: int) -> None:
        """Kontroler hciN z listy w oknie - zapamiętany po identyfikatorze urządzenia, nie po numerze."""
        await self.check_adapters()
        c = next((c for c in self.ctrls or () if c.index == index), None)
        if c is None:
            raise BtError(f"no controller hci{index}", BTR_NOT_FOUND)
        await self.set_mode(BTA_PICK, c.key)

    def adapter_flags(self) -> int:
        """Bity btstate: jakie kontrolery są, czy używany to USB, tryb (0 = bez wyboru - okno chowa przełącznik)."""
        if self.sysfs is None:
            return 0
        ctrls = self.ctrls or ()
        return ((BTF_HAS_BUILTIN if any(not c.usb for c in ctrls) else 0) | (BTF_HAS_USB if any(c.usb for c in ctrls) else 0)
                | (BTF_ON_USB if self.pick is not None and self.pick.usb else 0) | self.get_mode() << BTF_MODE_SHIFT)

    def ctrl_rows(self) -> list[tuple[int, int, str]]:
        """Kontrolery do okna: (BTC_*, N z hciN, nazwa urządzenia)."""
        picked = self.get_mode() == BTA_PICK
        rows = []
        for c in self.ctrls or ():
            flags = ((BTC_USB if c.usb else 0) | (BTC_FIXED if c.rank == btadapter.USB_FIXED else 0)
                     | (BTC_ACTIVE if c == self.pick else 0)
                     | (BTC_BLOCKED if btadapter.is_blocked(c.name, self.sysfs) else 0)
                     | (BTC_PICKED if picked and c.key == self.key else 0))
            rows.append((flags, c.index, c.label))
        return rows

    def no_adapter(self) -> BtError:
        """Brak adaptera; przy braku uprawnień (nadajnik nie jako root) okno pokazuje "brak uprawnień", a nie
        ogólne "nie udało się"."""
        return BtError(f"no adapter (state {self.state})", BTR_DENIED if self.state == BTS_DENIED else BTR_FAILED)

    def need(self, mac: str, *, known: bool = True) -> str:
        """Ścieżka D-Bus urządzenia; known: musi być na liście BlueZ (wykryte skanem albo sparowane)."""
        if self.state in (BTS_OFF, BTS_BLOCKED):
            raise BtError("adapter powered off", BTR_OFF)
        if not self.adapter:
            raise self.no_adapter()
        if known and mac not in {d.mac for d in self.devices}:
            raise BtError("unknown device - scan first", BTR_NOT_FOUND)
        return dev_path(self.adapter, mac)

    async def power(self, on: bool) -> None:
        if self.state == BTS_BLOCKED and on:
            raise BtError("adapter blocked (rfkill)", BTR_OFF)
        if not self.adapter:
            raise self.no_adapter()
        await self.set_prop(self.adapter, ADAPTER, "Powered", on)
        self.state = BTS_OK if on else BTS_OFF

    async def ensure_on(self) -> None:
        if self.state in (BTS_OFF, BTS_BLOCKED):
            await self.power(True)

    async def pair(self, mac: str) -> None:
        """Parowanie (BlueZ bez agenta = NoInputNoOutput, jak DualSense), zaufanie (pad sam się potem łączy) i
        połączenie. Już sparowany -> tylko zaufanie i połączenie."""
        await self.stop_scan()
        path = self.need(mac)
        if not next(d for d in self.devices if d.mac == mac).flags & BTD_PAIRED:
            try:
                await self.call(path, DEVICE, "Pair", timeout=PAIR_S)
            except BtError as e:
                if "already exists" not in str(e).lower():
                    raise
        await self.set_prop(path, DEVICE, "Trusted", True)
        await self.call(path, DEVICE, "Connect", timeout=CONNECT_S)

    async def connect(self, mac: str) -> None:
        await self.stop_scan()
        await self.call(self.need(mac), DEVICE, "Connect", timeout=CONNECT_S)

    async def disconnect(self, mac: str) -> None:
        await self.call(self.need(mac), DEVICE, "Disconnect", timeout=CONNECT_S)

    async def remove(self, mac: str) -> None:
        """Zapomnij urządzenie (np. pad sparowany potem z konsolą - stare klucze i tak nie działają)."""
        path = self.need(mac)
        await self.call(self.adapter, ADAPTER, "RemoveDevice", "o", path)

    async def disconnect_pad(self, mac: str) -> bool:
        """Wyłączenie pada (okno / bezczynność): Disconnect przez D-Bus na kontrolerze, z którym pad jest połączony.
        `bluetoothctl disconnect` szuka urządzenia tylko na domyślnym kontrolerze bluetoothctl (hci0) - przy padzie
        na donglu (hci1) kończył się "Device ... not available" i pad się nie wyłączał. -> False, gdy D-Bus nie
        pomógł (brak busctl / BlueZ, pad nieznany) - wtedy zostaje bluetoothctl."""
        try:
            out = await self.busctl("call", BLUEZ, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
            objects: dict[str, dict[str, Any]] = json.loads(out)["data"][0]
        except (BtError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            return False
        want = mac.upper()
        found = [(p, d) for p, ifaces in objects.items() if isinstance(d := ifaces.get(DEVICE), dict)
                 and str(_val(d, "Address", "")).upper() == want]
        path = next((p for p, d in found if _val(d, "Connected", False)), None)
        if path is None:
            return False
        try:
            await self.call(path, DEVICE, "Disconnect", timeout=CONNECT_S)
        except BtError as e:
            print(f"Bluetooth disconnect {mac} failed: {e}")
            return False
        return True

    # ---------- skan ----------
    def scanning(self) -> bool:
        return self.scan is not None and self.scan.returncode is None

    async def start_scan(self, secs: int) -> None:
        """Skan przez secs s (proces bluetoothctl kończy sesję wyszukiwania sam); trwający - nowy limit."""
        async with self.scan_lock:
            await self._start_scan(secs)

    async def _start_scan(self, secs: int) -> None:
        await self._stop_scan()
        await self.refresh()
        try:
            await self.ensure_on()
            if not self.adapter:
                raise self.no_adapter()
            if self.adapter_count > 1 and self.adapter_addr:
                # kilka kontrolerów (wbudowany zablokowany + dongle): `bluetoothctl scan on` skanuje domyślnym
                # kontrolerem bluetoothctl - pierwszym znanym BlueZ (hci0, zwykle zablokowany wbudowany), a nie wybranym.
                # Skan kończył się od razu błędem "NotReady" i nowego pada nie dało się sparować z okna.
                # Tryb interaktywny: "select <adres>" i "scan on" przez stdin (--timeout działa tylko bez trybu
                # interaktywnego - limit pilnuje _scan_watch).
                self.scan = await asyncio.create_subprocess_exec(BLUETOOTHCTL, stdin=PIPE, stdout=PIPE, stderr=DEVNULL)
                state.spawn(_scan_feed(self.scan, self.adapter_addr))
                limit = secs
            else:
                self.scan = await asyncio.create_subprocess_exec(BLUETOOTHCTL, "--timeout", str(secs), "scan", "on",
                                                                 stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
                limit = secs + 1                     # zapas: bluetoothctl kończy sam
        except FileNotFoundError:
            self.last = (BT_SCAN_START, BTR_FAILED, b"")
            print("Bluetooth scan failed: bluetoothctl not found (install bluez)")
        except OSError as e:                         # bez tego wyjątek kończył zadanie skanu bez wyniku dla okna
            self.last = (BT_SCAN_START, BTR_DENIED if isinstance(e, PermissionError) else BTR_FAILED, b"")
            print(f"Bluetooth scan failed: cannot run bluetoothctl: {e}")
        except BtError as e:
            self.last = (BT_SCAN_START, e.result, b"")
            print(f"Bluetooth scan failed: {e}")
        else:
            self.scan_end = time.monotonic() + secs
            self.last = (BT_SCAN_START, BTR_OK, b"")
            print(f"Bluetooth scan for {secs}s")
            state.spawn(self._scan_watch(self.scan, limit))
        self.poke()

    async def _scan_watch(self, proc: asyncio.subprocess.Process, limit: float) -> None:
        """Koniec skanu: proces sam się kończy (--timeout); bez tego (tryb interaktywny, bluetoothctl bez --timeout)
        zatrzymanie po limit s - skan zabiera radio padom, nie może trwać dłużej niż obiecany limit."""
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), limit)
        except TimeoutError:
            if self.scan is proc:
                async with self.scan_lock:
                    if self.scan is proc:
                        await self._stop_scan()
            await proc.wait()
        if self.scan is proc:
            self.scan = None
            print("Bluetooth scan finished")
            self.poke()

    async def stop_scan(self) -> None:
        """Zatrzymanie skanu - także takiego, który właśnie się uruchamia (czeka na koniec startu)."""
        async with self.scan_lock:
            await self._stop_scan()

    async def _stop_scan(self) -> None:
        proc, self.scan = self.scan, None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        print("Bluetooth scan stopped")

    def kill_scan(self) -> None:
        """Zamykanie nadajnika (bez pętli): skan nie może zostać po nim."""
        if self.scan is not None and self.scan.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.scan.kill()

    # ---------- do okna ----------
    def active(self, now: float) -> bool:
        return now < self.active_until or self.scanning() or self.running

    def send(self, full: bool, now: float | None = None) -> None:
        """btstate (+ runda btdev, gdy full): stan, skan, ostatnia operacja, lista urządzeń."""
        now = time.monotonic() if now is None else now
        scanning = self.scanning()
        flags = ((BTF_SCANNING if scanning else 0) | (BTF_BUSY if self.running else 0) | (BTF_LIST if full else 0)
                 | self.adapter_flags())
        left = clamp(round(self.scan_end - now), 0, 255) if scanning else 0
        devs = self.devices if full else []
        if full:
            self.gen = (self.gen + 1) & 0xFFFF
        conn = sum(1 for d in self.devices if d.flags & BTD_CONNECTED)
        op, res, mac = self.last
        self.emit(T_BTSTATE, 255, self.state, flags, left, conn, len(devs), self.gen, op, res, mac or bytes(6))
        for k, d in enumerate(devs):
            rssi = BT_RSSI_NONE if d.rssi is None else clamp(d.rssi, BT_RSSI_NONE + 1, 127)
            self.emit(T_BTDEV, 255, self.gen, k, len(devs), mac_bytes(d.mac), rssi, d.flags, utf8_field(d.name, NAME_MAX, "…"))
        if full and self.sysfs is not None:          # kontrolery nadajnika (panel: lista z nazwami, "Użyj")
            rows = self.ctrl_rows()
            self.cgen = (self.cgen + 1) & 0xFFFF
            for k, (cflags, index, label) in enumerate(rows):
                self.emit(T_BTCTRL, 255, self.cgen, k, len(rows), cflags, index, utf8_field(label, CTRL_NAME_MAX, "…"))

    async def loop(self) -> None:
        """Stan do receivera: co IDLE_S sam stan, przy otwartym panelu / skanie / operacji co TICK_S z listą.
        Tylko do receivera, który zna nowe pakiety (przysłał scfg) - starszy odrzuca nieznane długości."""
        self.wake = asyncio.Event()
        t_sent = 0.0
        try:
            while True:
                now = time.monotonic()
                active = self.active(now)
                if net.new_receiver(now) and (active or now - t_sent >= IDLE_S):
                    t_sent = now
                    if not self.running:
                        await self.refresh()          # w trakcie operacji odświeża ją sama (BlueZ zajęty)
                    self.send(full=active)
                self.wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), TICK_S if active else IDLE_S / 5)
        finally:
            self.kill_scan()


OPS: dict[int, Callable[[Bluetooth, int, str], Awaitable[None]]] = {
    BT_PAIR: lambda b, _arg, mac: b.pair(mac),
    BT_CONNECT: lambda b, _arg, mac: b.connect(mac),
    BT_DISCONNECT: lambda b, _arg, mac: b.disconnect(mac),
    BT_REMOVE: lambda b, _arg, mac: b.remove(mac),
    BT_POWER: lambda b, arg, _mac: b.power(bool(arg)),
    BT_ADAPTER: lambda b, arg, _mac: b.set_mode(arg),
    BT_ADAPTER_USE: lambda b, arg, _mac: b.use_adapter(arg),
}
OP_NAMES = {BT_LIST: "list", BT_SCAN_START: "scan", BT_SCAN_STOP: "scan stop", BT_PAIR: "pair", BT_CONNECT: "connect",
            BT_DISCONNECT: "disconnect", BT_REMOVE: "remove", BT_POWER: "power", BT_ADAPTER: "adapter",
            BT_ADAPTER_USE: "use adapter"}


bt = Bluetooth(sysfs=btadapter.SYSFS)
