"""Sender: Wake-on-LAN from the pad - holding PS + a chosen button while the receiver does not answer wakes the PC.

The combination only works when the receiver is silent (no pong for RECEIVER_TIMEOUT_S): while the PC is up, the same
buttons go to the receiver (e.g. the master action PS + Options = pause), so nothing collides. A long press of PS alone
is unusable - it switches the pad on, and ~10 s of it switches the pad off.

The settings come from the receiver window (scfg packet) and are saved in wol.json next to the sender - while the PC
is off there is nowhere else to get them. The PC's MAC: typed in the window or (by default) read from the phone's ARP
table while the PC is up; the broadcast address: typed in, or the broadcast of the network the PC traffic goes
through, plus 255.255.255.255. The magic packet also goes straight to the PC's IP (the phone's ARP entry usually
still remembers it).
"""
import asyncio
import ipaddress
import json
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, replace

import sender_events as events
import sender_net as net
from proto import E_WOL, E_WOL_FAIL, KEY_BIT, PAD_KEYS, SCFG_EVENTS, SCFG_WOL, WOL_PORTS, mac_bytes, mac_text, magic_packet, write_json
from sender_config import C

PS_BIT = KEY_BIT[316]
# buttons to pair with PS (without PS itself; D-pad and triggers are outside the mask)
WOL_KEYS = tuple(k for k in PAD_KEYS if k != 316)
RECEIVER_TIMEOUT_S = 3.0
REPEAT_GUARD_S = 5.0          # the next send no sooner than this many seconds later
HOLD_MS = (300, 5000)
_INET = re.compile(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)(?:\s+brd\s+(\d+\.\d+\.\d+\.\d+))?")


@dataclass(frozen=True, slots=True)
class WolCfg:
    """Immutable Wake-on-LAN settings; a changed configuration is a new object (so == detects real changes)."""
    enabled: bool = True
    mac: str = ""                         # the PC's MAC typed in the window; "" = from the phone's ARP table
    bcast: str = ""                       # broadcast typed in the window; "" = the network to the PC + 255.255.255.255
    button: int = PAD_KEYS.index(315)     # bit number of the button paired with PS (default Options)
    hold_ms: int = 1000
    events: bool = False                  # events for the log in the receiver window (event packet)


cfg = WolCfg()
learned = ""                  # the PC's MAC from the ARP table (remembered in wol.json)
_bcast_cache = (None, None)   # (local IP, broadcast of its network)
_last_sent = 0.0


# ---------------- settings ----------------
def load(path=None) -> None:
    """Load wol.json into cfg and learned. A missing or broken file = default values.

    Only known fields of WolCfg are taken from the file, and the result is passed through _valid(), so a hand-edited
    or damaged file can never crash the service at startup. Finally the events switch is applied.
    """
    global cfg, learned
    path = path or C.WOL_FILE
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):             # e.g. a hand-typed number / list - must not crash the service startup
            raise ValueError("expected a JSON object")
        learned = mac_text(mac_bytes(raw.pop("learned_mac", "")))
        cfg = _valid(replace(WolCfg(), **{k: v for k, v in raw.items() if k in WolCfg.__dataclass_fields__}))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as e:
        print(f"WoL: {path} ignored ({e!r})")
    events.set_enabled(cfg.events)


def save(path=None) -> None:
    """Write the current settings plus the learned MAC to wol.json (atomically); errors are logged only."""
    path = path or C.WOL_FILE
    if not path:
        return
    try:
        write_json(path, {**asdict(cfg), "learned_mac": learned}, indent=2)
    except OSError as e:
        print(f"WoL: cannot write {path}: {e!r}")


def _valid(c):
    """Replace out-of-range values (hand editing, a damaged packet) with the defaults and normalise the rest.

    The MAC is re-formatted, the broadcast must be a valid IPv4 address, the button must be one that may be paired
    with PS, and the hold time is clamped to HOLD_MS.
    """
    d = WolCfg()
    return replace(c, enabled=bool(c.enabled), events=bool(c.events),
                   mac=mac_text(mac_bytes(c.mac)) if isinstance(c.mac, str) else "",
                   bcast=c.bcast if isinstance(c.bcast, str) and _is_ip(c.bcast) else "",
                   button=c.button if isinstance(c.button, int) and 0 <= c.button < len(PAD_KEYS)
                   and PAD_KEYS[c.button] in WOL_KEYS else d.button,
                   hold_ms=min(HOLD_MS[1], max(HOLD_MS[0], int(c.hold_ms))) if isinstance(c.hold_ms, int) else d.hold_ms)


def _is_ip(text) -> bool:
    """True if text is a valid dotted IPv4 address."""
    try:
        ipaddress.IPv4Address(text)
    except ValueError:
        return False
    return True


def apply_scfg(flags, mac, bcast, button, hold_ms) -> bool:
    """Apply an scfg packet from the receiver to cfg; the file is written only if something actually changed.

    Returns True when the configuration changed. The raw packet fields are converted (flag bits -> booleans, bytes ->
    MAC / IP text) and validated before they replace the current configuration. Events are switched along with it.
    """
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


# ---------------- addresses ----------------
def arp_lookup(ip, path: str="/proc/net/arp"):
    """MAC for an IP from the kernel ARP table ("" = no entry / incomplete entry).

    Each row of /proc/net/arp is "IP  HW type  Flags  HW address ..."; the flag bit 0x2 (ATF_COM) marks a complete
    entry, so half-resolved entries (all-zero MAC) are skipped.
    """
    try:
        with open(path) as f:
            next(f, None)
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[0] == ip and int(p[2], 16) & 0x2:      # ATF_COM: complete entry
                    return mac_text(mac_bytes(p[3]))
    except (OSError, ValueError):
        pass
    return ""


def subnet_broadcast(local_ip, ip_output=None):
    """Broadcast address of the network that holds local_ip (parsed from `ip -o -4 addr show`), or None.

    Uses the explicit "brd" field when present, otherwise computes the broadcast from the address and prefix length.
    ip_output can be passed in (tests); otherwise the `ip` command is run with a 3 s timeout.
    """
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
    """The local IP address of the sender's UDP socket (the interface that routes to the PC), or None."""
    s = net.sock
    try:
        return s.getsockname()[0] if s is not None else None
    except OSError:
        return None


def auto_bcast():
    """Broadcast of the network the sender uses to reach the PC (cached per local IP).

    Called from the asyncio loop (build packet every 5 s, Wake-on-LAN): `ip addr` runs in a separate thread, and until
    its result arrives the previous value is returned (None = only 255.255.255.255 and the PC's IP). Earlier,
    subprocess.run stopped the whole loop - including pad reading - for the time the program took to run (limit 3 s)."""
    ip = local_ip()
    if not ip:
        return None
    if ip != _bcast_cache[0] and _bcast_pending != ip:
        _refresh_bcast(ip)
    return _bcast_cache[1] if ip == _bcast_cache[0] else None


_bcast_pending = None         # the local IP whose broadcast is being read in a thread


def _refresh_bcast(ip) -> None:
    """Start reading the broadcast address for `ip` in a worker thread; the result lands in _bcast_cache.

    _bcast_pending marks a lookup in progress so that auto_bcast does not start duplicates. Without a running event
    loop (tools, tests) the lookup is simply done synchronously.
    """
    global _bcast_cache, _bcast_pending

    def done(fut) -> None:
        global _bcast_cache, _bcast_pending
        _bcast_pending = None
        try:
            _bcast_cache = (ip, fut.result())
        except Exception as e:                     # the thread must not leave "in progress" set forever
            print(f"WoL: cannot read the broadcast address: {e!r}")
            _bcast_cache = (ip, None)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:                           # no loop (tools, tests) - right away
        _bcast_pending, _bcast_cache = None, (ip, subnet_broadcast(ip))
        return
    _bcast_pending = ip
    loop.run_in_executor(None, subnet_broadcast, ip).add_done_callback(done)


def target_mac():
    """MAC to wake: the one typed in the window, otherwise the learned one; bytes, or None if unknown."""
    return mac_bytes(cfg.mac or learned)


def targets():
    """Destination addresses of the magic packet: a broadcast (typed in, or the network to the PC plus the general one)
    and the PC's own IP. Duplicates are removed while the order is kept."""
    out = [cfg.bcast] if cfg.bcast else [b for b in (auto_bcast(), "255.255.255.255") if b]
    return list(dict.fromkeys([*out, C.PC_ADDR]))


def learn() -> None:
    """While the PC answers: remember its MAC from the ARP table (it will be needed when the PC is off)."""
    global learned
    mac = arp_lookup(C.PC_ADDR)
    if mac and mac != learned:
        learned = mac
        print(f"WoL: PC MAC {mac} (from ARP)")
        save()


def send(mac, addrs):
    """Send the magic packet to every address on every WOL_PORTS port. Returns a list of errors, one per failed send
    ([] = all sent). Duplicates are not removed: fire() compares the number of errors with the number of sends (the same
    error on both ports is two failed sends - once merged, "no network" looked like a partial success)."""
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
    return errors


def receiver_alive(now=None):
    """True if a pong from the receiver arrived within the last RECEIVER_TIMEOUT_S seconds."""
    return (time.monotonic() if now is None else now) - net.last_pong < RECEIVER_TIMEOUT_S


# ---------------- the combination on the pad ----------------
def on_mask(c) -> None:
    """Pad button state changed: PS + the chosen button held -> start the hold_ms countdown, released -> cancel it.

    The countdown is a call_later timer that fires fire() when it expires; keeping it in c.wol_timer lets a release
    cancel it, and prevents a second timer while the combination stays held.
    """
    want = PS_BIT | (1 << cfg.button)
    held = cfg.enabled and c.mask & want == want
    t = c.wol_timer
    if held and t is None:
        c.wol_timer = asyncio.get_running_loop().call_later(cfg.hold_ms / 1000, fire, c)
    elif not held and t is not None:
        t.cancel()
        c.wol_timer = None


def fire(c, now=None) -> bool:
    """The combination was held long enough: if the receiver is silent, send the magic packet. Returns True if sent.

    Guards: nothing happens while the receiver answers (the combination then belongs to the receiver) or within
    REPEAT_GUARD_S of the previous send. Without a known MAC an error event is raised. The send counts as failed only
    if every (address, port) attempt failed; the pad confirms the outcome with a local pulse ("wol" / "fail").
    """
    global _last_sent
    c.wol_timer = None
    now = time.monotonic() if now is None else now
    if receiver_alive(now):
        return False                  # the PC is up - the combination belongs to the receiver
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
    failed = send(mac, addrs)
    errors = list(dict.fromkeys(failed))          # for the log without repeats (the same error on each port)
    if len(failed) >= len(addrs) * len(WOL_PORTS):
        print(f"Controller {c.idx}: Wake-on-LAN failed: {'; '.join(errors)}")
        events.error(E_WOL_FAIL, c.idx, errors[0])
        c.local_pulse("fail")
        return False
    print(f"Controller {c.idx}: Wake-on-LAN {mac_text(mac)} -> {', '.join(addrs)}"
          + (f" (errors: {'; '.join(errors)})" if errors else ""))
    events.info(E_WOL, c.idx, mac_text(mac))
    c.local_pulse("wol")
    return True
