"""Sender: wykrywanie padów (evdev), przydział slotów, cykl życia kontrolera."""
import asyncio
import json
import os
import re
import time

import evdev

import sender_events as events
import sender_state as state
import sender_streams as streams
from proto import E_ERROR, E_PAD_OFF, E_PAD_ON, LAT_NONE, T_BYE, write_json
from sender_config import C, IDLE_TP, SETTLE_S
from sender_ctrl import Ctrl
from sender_rumble import Rumble
from sender_net import emit_repeat

_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.IGNORECASE)
_not_pads = {}       # ścieżka -> identyfikator węzła (inode, ctime) urządzenia, które nie jest padem


def _node_id(path):
    """Węzeł /dev/input/eventN jest tworzony od nowa przy każdym podłączeniu - (inode, ctime) go odróżnia."""
    try:
        st = os.stat(path)
        return st.st_ino, st.st_ctime_ns
    except OSError:
        return None
_first_seen = {}     # klucz -> kiedy zobaczyliśmy niekompletną grupę urządzeń


def event_num(dev):
    m = re.search(r"(\d+)$", dev.path)
    return int(m.group(1)) if m else 0


def discover():
    groups = {}
    paths = evdev.list_devices()
    for p in [p for p in _not_pads if p not in paths]:
        del _not_pads[p]
    busy = {d.path for c in state.active.values() for d in c.devices()}
    for path in paths:
        if path in busy:
            continue
        nid = _node_id(path)
        if nid is not None and _not_pads.get(path) == nid:
            continue
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        name = d.name
        if "Wireless Controller" not in name:
            d.close()
            _not_pads[path] = nid
            continue
        key = d.uniq or d.phys or "unknown"
        kind = "touch" if "Touchpad" in name else "motion" if "Motion" in name else "pad"
        g = groups.setdefault(key, {})
        if kind in g:
            d.close()
        else:
            g[kind] = d
    return groups


def close_group(g):
    for d in g.values():
        try:
            d.close()
        except Exception:
            pass


def _load_slots():
    """Zapamiętany przydział pad (MAC) -> slot z poprzednich uruchomień (slots_file)."""
    if state.saved_slots is None:
        state.saved_slots = {}
        if C.SLOTS_FILE:
            try:
                with open(C.SLOTS_FILE, encoding="utf-8") as f:
                    raw = json.load(f)
                state.saved_slots = {str(k): int(v) for k, v in raw.items() if 0 <= int(v) < C.MAX_PADS}
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, AttributeError) as e:
                print(f"Slots file {C.SLOTS_FILE} ignored: {e!r}")
    return state.saved_slots


def _save_slots():
    if not C.SLOTS_FILE:
        return
    try:
        write_json(C.SLOTS_FILE, state.saved_slots, indent=1, sort_keys=True)
    except OSError as e:
        print(f"Cannot write {C.SLOTS_FILE}: {e!r}")


def pick_slot(key):
    """Stały slot pada: ten sam pad (MAC) dostaje ten sam numer także po restarcie nadajnika, więc w grze
    (Steam, XInput) pozostaje tym samym graczem niezależnie od kolejności włączania padów."""
    if key in state.slots:
        return state.slots[key]
    saved = _load_slots()
    taken = {i for k, i in state.slots.items() if k in state.active}
    pref = saved.get(key)
    free = [i for i in range(C.MAX_PADS) if i not in taken]
    if not free:
        return None
    # najpierw zapamiętany slot; potem wolny, którego nie "rezerwuje" inny znany pad; na końcu dowolny wolny
    reserved = {v for k, v in saved.items() if k != key}
    i = pref if pref in free else next((n for n in free if n not in reserved), free[0])
    for k in [k for k, v in state.slots.items() if v == i]:
        del state.slots[k]
    state.slots[key] = i
    if saved.get(key) != i and _MAC.match(key):   # pamiętamy tylko pady z MAC (USB bez uniq ma zmienny klucz)
        for k in [k for k, v in saved.items() if v == i]:
            del saved[k]
        saved[key] = i
        _save_slots()
    return i


def dispatch_feedback(idx, vals):
    if (c := state.ctrl_by_idx(idx)) is not None:
        c.apply_feedback(*vals)


def dispatch_triggers(idx, vals):
    if (c := state.ctrl_by_idx(idx)) is not None:
        c.set_triggers(vals)


def grab(dev, label):
    try:
        dev.grab()
        print(f"{label}: grabbed")
    except OSError as e:
        print(f"{label}: grab failed: {e}")


KIND_ATTR = {"motion": "motion_dev", "touch": "touch_dev"}


def attach(c, kind, dev):
    """Podłącza urządzenie ruchu / touchpada do działającego pada (także gdy pojawi się później niż pad)."""
    setattr(c, KIND_ATTR[kind], dev)
    if kind == "touch":
        if C.GRAB_TOUCH:
            grab(dev, f"Touchpad {c.idx}")
        coro = streams.stream_touch(c)
    else:
        coro = streams.stream_motion(c)
    t = asyncio.create_task(_run_extra(c, kind, dev, coro))
    c.extra.add(t)
    t.add_done_callback(c.extra.discard)


async def _run_extra(c, kind, dev, coro):
    """Koniec strumienia (urządzenie zniknęło) odłącza tylko to urządzenie - pad działa dalej,
    a supervisor dołączy je ponownie, gdy wróci."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except OSError:
        pass
    except Exception as e:
        print(f"Controller {c.idx} {kind} error: {e!r}")
    finally:
        if getattr(c, KIND_ATTR[kind]) is dev:
            setattr(c, KIND_ATTR[kind], None)
        # ostatnie wartości nie mogą zostać w odbiorniku: gyro z chwili utraty (np. 50 °/s) kręciłoby celowaniem
        # żyroskopem bez końca, a palec przyłożony do touchpada zostałby "przyłożony" w grze
        if kind == "touch":
            c.touch = IDLE_TP
            c.tp = 0
        else:
            c.motion = [0] * 6
            c.bt_us = LAT_NONE                # bez urządzenia ruchu nie ma znaczników czasu pada
        if not c.closing:                     # przy zamykaniu pada send_neutral wyśle to samo
            if kind == "touch":
                c.send_touch(force=True)
                c.send_click()
            else:
                c.send_state(force=True)
        for fn in (dev.ungrab, dev.close):
            try:
                fn()
            except Exception:
                pass
        if state.active.get(c.key) is c and not getattr(c, "closing", False):
            print(f"Controller {c.idx}: {kind} device lost - waiting for it to come back")


def incomplete(c):
    return c.motion_dev is None or c.touch_dev is None


async def run_ctrl(c):
    tasks = []
    try:
        c.init_state()
        c.rumble = Rumble(c.idx, c.pad_dev, C.RUMBLE)
        if any(c.triggers):                   # efekty spustów przyszły z PC, zanim pad był gotowy
            c.rumble.set_triggers(c.triggers)
        print(f"== Controller {c.idx} ({c.key})")
        print(f"   Pad:    {c.pad_dev.name} ({c.pad_dev.path})")
        print(f"   Motion: {c.motion_dev.name if c.motion_dev else 'NOT FOUND'}")
        print(f"   Touch:  {c.touch_dev.name if c.touch_dev else 'NOT FOUND'}")
        print(f"   Lightbar: {c.led or 'not found'}")
        if C.GRAB_PAD:
            grab(c.pad_dev, f"Pad {c.idx}")
        c.send_ident()        # receiver od razu wie, który to pad (profil remapowania)
        events.info(E_PAD_ON, c.idx, c.key or "")
        tasks.append(asyncio.create_task(streams.stream_pad(c)))
        tasks.append(asyncio.create_task(streams.stream_battery(c)))
        for kind in ("motion", "touch"):          # osobne zadania: ich utrata nie rozłącza pada
            dev = getattr(c, KIND_ATTR[kind])
            if dev is not None:
                setattr(c, KIND_ATTR[kind], None)
                attach(c, kind, dev)

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, OSError):
                print(f"Controller {c.idx} error: {exc!r}")
                events.error(E_ERROR, c.idx, repr(exc))
    except OSError as e:
        print(f"Controller {c.idx}: device error: {e}")
        events.error(E_ERROR, c.idx, str(e))
    finally:
        c.closing = True
        for t in tasks + list(c.extra):
            t.cancel()
        if c.extra:
            await asyncio.gather(*c.extra, return_exceptions=True)
        c.cancel_timers()
        c.stop_feedback()
        for d in c.devices():
            try:
                d.ungrab()
            except OSError:
                pass
            try:
                d.close()
            except Exception:
                pass
        c.send_neutral()
        emit_repeat(T_BYE, c.idx)
        state.active.pop(c.key, None)
        print(f"Controller {c.idx}: disconnected (waiting for reconnect)")
        events.info(E_PAD_OFF, c.idx)


class DevWatch:
    """inotify na /dev/input: nowy węzeł eventN (pad włączony / połączony) budzi supervisora od razu - bez tego pad
    czekał na kolejny skan do rescan_s (średnio +1 s przy łączeniu). Bez inotify (inna platforma, brak katalogu) -
    zwykłe skanowanie co rescan_s."""

    IN_ATTRIB, IN_CREATE = 0x004, 0x100
    IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000
    DEBOUNCE_S = 0.05             # węzły pada, ruchu i touchpada powstają razem - jeden skan na całą grupę

    def __init__(self):
        self.fd = None
        self.event = asyncio.Event()

    def start(self, path=b"/dev/input"):
        try:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            fd = libc.inotify_init1(self.IN_NONBLOCK | self.IN_CLOEXEC)
            if fd < 0:
                return False
            if libc.inotify_add_watch(fd, path, self.IN_CREATE | self.IN_ATTRIB) < 0:
                os.close(fd)
                return False
            asyncio.get_running_loop().add_reader(fd, self._readable)
        except (OSError, AttributeError, NotImplementedError):
            return False
        self.fd = fd
        return True

    def _readable(self):
        try:
            while os.read(self.fd, 4096):
                pass
        except OSError:
            pass
        self.event.set()

    async def wait(self, timeout):
        """Do zmiany w /dev/input albo timeout s."""
        try:
            await asyncio.wait_for(self.event.wait(), timeout)
        except TimeoutError:
            return
        await asyncio.sleep(self.DEBOUNCE_S)
        self.event.clear()

    def close(self):
        if self.fd is not None:
            try:
                asyncio.get_running_loop().remove_reader(self.fd)
            except RuntimeError:
                pass
            os.close(self.fd)
            self.fd = None


def next_scan_s(now):
    """Za ile następny skan: rescan_s albo wcześniej, gdy niekompletny pad czeka na resztę urządzeń (SETTLE_S)."""
    wait = C.RESCAN_S
    if _first_seen:
        wait = min(wait, max(0.05, SETTLE_S - (now - min(_first_seen.values())) + 0.02))
    return wait


async def supervisor():
    watch = DevWatch()
    watch.start()
    try:
        await _supervise(watch)
    finally:
        watch.close()


async def _supervise(watch):
    first = True
    while True:
        if len(state.active) < C.MAX_PADS or any(incomplete(c) for c in state.active.values()):
            groups = discover()
            now = time.monotonic()
            for k in [k for k in _first_seen if k not in groups]:
                del _first_seen[k]
            order = sorted(groups.items(),
                           key=lambda kv: event_num(kv[1]["pad"]) if "pad" in kv[1] else 1 << 30)
            for key, g in order:
                c = state.active.get(key)
                if c is not None:             # pad już działa: dołącz brakujące urządzenia, które pojawiły się później
                    for kind in ("motion", "touch"):
                        if kind in g and getattr(c, KIND_ATTR[kind]) is None and not getattr(c, "closing", False):
                            dev = g.pop(kind)
                            print(f"Controller {c.idx}: {kind} device attached ({dev.path})")
                            attach(c, kind, dev)
                    close_group(g)
                    continue
                if "pad" not in g:
                    close_group(g)
                    continue
                # nowo pojawiony pad: urządzenia motion/touchpad bywają widoczne ułamek sekundy później
                t0 = _first_seen.setdefault(key, now)
                if not ("motion" in g and "touch" in g) and now - t0 < SETTLE_S:
                    close_group(g)
                    continue
                idx = pick_slot(key)
                if idx is None:
                    close_group(g)
                    continue
                _first_seen.pop(key, None)
                c = Ctrl(idx, key, g["pad"], g.get("motion"), g.get("touch"))
                state.active[key] = c
                state.spawn(run_ctrl(c))
            if first and not state.active:
                print("No DualSense found yet - connect controller (bluetoothctl), waiting...")
            first = False
        await watch.wait(next_scan_s(time.monotonic()))
