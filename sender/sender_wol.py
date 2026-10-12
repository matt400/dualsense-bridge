"""Sender: Wake-on-LAN z pada - PS + wybrany przycisk przytrzymane, gdy receiver nie odpowiada, budzą komputer.

Kombinacja działa tylko wtedy, gdy receiver milczy (brak ponga od RECEIVER_TIMEOUT_S): przy działającym PC te same
przyciski idą do receivera (np. akcja mastera PS + Options = pauza), więc nic nie koliduje. Długie samo PS się nie
nadaje - nim włącza się pada, a ~10 s wyłącza.

Ustawienia przychodzą z okna receivera (pakiet scfg) i są zapisywane w wol.json obok nadajnika - przy wyłączonym PC
nie ma ich skąd wziąć. MAC komputera: wpisany w oknie albo (domyślnie) odczytany z tablicy ARP telefonu, póki PC
działa; adres broadcast: wpisany albo broadcast sieci, przez którą idzie ruch do PC, plus 255.255.255.255. Magic packet
idzie też prosto na IP komputera (wpis ARP telefonu zwykle jeszcze go pamięta).
"""
import asyncio
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, replace

import sender_events as events
import sender_net as net
from proto import E_WOL, E_WOL_FAIL, KEY_BIT, PAD_KEYS, SCFG_EVENTS, SCFG_WOL, WOL_PORTS, mac_bytes, mac_text, magic_packet
from sender_config import C

PS_BIT = KEY_BIT[316]
WOL_KEYS = tuple(k for k in PAD_KEYS if k != 316)      # przyciski do pary z PS (bez samego PS; D-pad i spusty poza maską)
RECEIVER_TIMEOUT_S = 3.0
REPEAT_GUARD_S = 5.0          # kolejne wysłanie najwcześniej po tylu sekundach
HOLD_MS = (300, 5000)
_INET = re.compile(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)(?:\s+brd\s+(\d+\.\d+\.\d+\.\d+))?")


@dataclass(frozen=True, slots=True)
class WolCfg:
    enabled: bool = True
    mac: str = ""                         # MAC komputera wpisany w oknie; "" = z tablicy ARP telefonu
    bcast: str = ""                       # broadcast wpisany w oknie; "" = sieć do PC + 255.255.255.255
    button: int = PAD_KEYS.index(315)     # numer bitu przycisku do pary z PS (domyślnie Options)
    hold_ms: int = 1000
    events: bool = False                  # zdarzenia do dziennika w oknie receivera (pakiet event)


cfg = WolCfg()
learned = ""                  # MAC komputera z tablicy ARP (zapamiętany w wol.json)
_bcast_cache = (None, None)   # (lokalny IP, broadcast jego sieci)
_last_sent = 0.0


# ---------------- ustawienia ----------------
def load(path=None):
    """wol.json -> cfg, learned. Brak / zły plik = wartości domyślne."""
    global cfg, learned
    path = path or C.WOL_FILE
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        learned = mac_text(mac_bytes(raw.pop("learned_mac", "")))
        cfg = _valid(replace(WolCfg(), **{k: v for k, v in raw.items() if k in WolCfg.__dataclass_fields__}))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as e:
        print(f"WoL: {path} ignored ({e!r})")
    events.set_enabled(cfg.events)


def save(path=None):
    path = path or C.WOL_FILE
    if not path:
        return
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({**asdict(cfg), "learned_mac": learned}, f, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        print(f"WoL: cannot write {path}: {e!r}")


def _valid(c):
    """Wartości spoza zakresu (ręczna edycja, uszkodzony pakiet) -> domyślne."""
    d = WolCfg()
    return replace(c, enabled=bool(c.enabled), events=bool(c.events),
                   mac=mac_text(mac_bytes(c.mac)) if isinstance(c.mac, str) else "",
                   bcast=c.bcast if isinstance(c.bcast, str) and _is_ip(c.bcast) else "",
                   button=c.button if isinstance(c.button, int) and 0 <= c.button < len(PAD_KEYS)
                   and PAD_KEYS[c.button] in WOL_KEYS else d.button,
                   hold_ms=min(HOLD_MS[1], max(HOLD_MS[0], int(c.hold_ms))) if isinstance(c.hold_ms, int) else d.hold_ms)


def _is_ip(text):
    try:
        ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return True


def apply_scfg(flags, mac, bcast, button, hold_ms):
    """Pakiet scfg z receivera -> cfg (zapis do wol.json tylko przy zmianie)."""
    global cfg
    new = _valid(WolCfg(enabled=bool(flags & SCFG_WOL), events=bool(flags & SCFG_EVENTS), mac=mac_text(mac),
                        bcast=socket.inet_ntoa(bcast) if any(bcast) else "", button=button, hold_ms=hold_ms))
    if new == cfg:
        return False
    cfg = new
    events.set_enabled(cfg.events)
    print(f"WoL: {'on' if cfg.enabled else 'off'}, PS + {PAD_KEYS[cfg.button]} for {cfg.hold_ms} ms, "
          f"MAC {cfg.mac or 'auto'}, broadcast {cfg.bcast or 'auto'}; events to PC {'on' if cfg.events else 'off'}")
    save()
    return True


# ---------------- adresy ----------------
def arp_lookup(ip, path="/proc/net/arp"):
    """MAC dla IP z tablicy ARP jądra ("" = brak / niekompletny wpis)."""
    try:
        with open(path) as f:
            next(f, None)
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[0] == ip and int(p[2], 16) & 0x2:      # ATF_COM: wpis kompletny
                    return mac_text(mac_bytes(p[3]))
    except (OSError, ValueError):
        pass
    return ""


def subnet_broadcast(local_ip, ip_output=None):
    """Broadcast sieci z adresem local_ip (z `ip -o -4 addr show`) albo None."""
    if ip_output is None:
        try:
            ip_output = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    for m in _INET.finditer(ip_output):
        if m[1] == local_ip:
            return m[3] or str(ipaddress.IPv4Network(f"{m[1]}/{m[2]}", strict=False).broadcast_address)
    return None


def local_ip():
    s = net.sock
    try:
        return s.getsockname()[0] if s is not None else None
    except OSError:
        return None


def auto_bcast():
    """Broadcast sieci, przez którą nadajnik wysyła do PC (zapamiętany dla danego lokalnego IP)."""
    global _bcast_cache
    ip = local_ip()
    if ip and ip != _bcast_cache[0]:
        _bcast_cache = (ip, subnet_broadcast(ip))
    return _bcast_cache[1] if ip else None


def target_mac():
    return mac_bytes(cfg.mac or learned)


def targets():
    """Adresy dla magic packetu: broadcast (wpisany albo sieć do PC + ogólny) i IP komputera."""
    out = [cfg.bcast] if cfg.bcast else [b for b in (auto_bcast(), "255.255.255.255") if b]
    return list(dict.fromkeys([*out, C.PC_ADDR]))


def learn():
    """Póki PC odpowiada: zapamiętaj jego MAC z tablicy ARP (przyda się, gdy będzie wyłączony)."""
    global learned
    mac = arp_lookup(C.PC_ADDR)
    if mac and mac != learned:
        learned = mac
        print(f"WoL: PC MAC {mac} (from ARP)")
        save()


def send(mac, addrs):
    """Magic packet na każdy adres i port WOL_PORTS. -> lista błędów ([] = wysłano)."""
    pkt = magic_packet(mac)
    errors = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for a in addrs:
            for port in WOL_PORTS:
                try:
                    s.sendto(pkt, (a, port))
                except OSError as e:
                    errors.append(f"{a}: {e.strerror or e}")
    return list(dict.fromkeys(errors))


def receiver_alive(now=None):
    return (time.monotonic() if now is None else now) - net.last_pong < RECEIVER_TIMEOUT_S


# ---------------- kombinacja na padzie ----------------
def on_mask(c):
    """Zmiana przycisków pada: PS + przycisk trzymane -> odliczanie hold_ms, puszczenie -> anulowanie."""
    want = PS_BIT | (1 << cfg.button)
    held = cfg.enabled and c.mask & want == want
    t = c.wol_timer
    if held and t is None:
        c.wol_timer = asyncio.get_running_loop().call_later(cfg.hold_ms / 1000, fire, c)
    elif not held and t is not None:
        t.cancel()
        c.wol_timer = None


def fire(c, now=None):
    """Kombinacja przytrzymana: receiver milczy -> magic packet. -> True, gdy wysłano."""
    global _last_sent
    c.wol_timer = None
    now = time.monotonic() if now is None else now
    if receiver_alive(now):
        return False                  # PC działa - kombinacja należy do receivera
    if now - _last_sent < REPEAT_GUARD_S:
        return False
    _last_sent = now
    mac = target_mac()
    if mac is None:
        print(f"Controller {c.idx}: Wake-on-LAN - PC MAC unknown (set it in the receiver window)")
        events.error(E_WOL_FAIL, c.idx, "MAC?")
        c.local_pulse("fail")
        return False
    addrs = targets()
    errors = send(mac, addrs)
    if len(errors) >= len(addrs) * len(WOL_PORTS):
        print(f"Controller {c.idx}: Wake-on-LAN failed: {'; '.join(errors)}")
        events.error(E_WOL_FAIL, c.idx, errors[0])
        c.local_pulse("fail")
        return False
    print(f"Controller {c.idx}: Wake-on-LAN {mac_text(mac)} -> {', '.join(addrs)}"
          + (f" (errors: {'; '.join(errors)})" if errors else ""))
    events.info(E_WOL, c.idx, mac_text(mac))
    c.local_pulse("wol")
    return True
