"""Receiver: virtual pads on demand.

A DualSense through HIDMaestro (rx_hm) or a DS4 through ViGEmBus (vgamepad); feedback from the game goes to
rx_feedback.

Backend (Settings -> Pads, rx_settings.VpadConfig): auto = HIDMaestro when the DLL is there and the receiver runs as
administrator, otherwise ViGEm; hidmaestro = like auto, but a missing / failing HIDMaestro is a warning in the log (ViGEm
still serves as a fallback); vigem = always ViGEm. Changing the backend in the window unplugs and recreates the pads (no restart).

Fixed player order (fixed_slots): games, Steam and Windows number pads by the order in which devices appear.
When the pad of slot 2 connects and slot 1 is empty, slot 1 first gets a placeholder pad (a neutral state), so pad 2
never becomes player 1. The placeholder disappears together with the last pad of a higher slot.
"""
import ctypes
import gc
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

try:
    import vgamepad as vg
except Exception as e:      # noqa: BLE001 - no / broken ViGEmBus: vgamepad connects to it already at import - without this the receiver
    # crashed at startup (silently from autostart); this way the window, DSU and mouse work, and the error is in the log
    vg, VG_ERROR = None, e
else:
    VG_ERROR = None

import rx_events as ev
import rx_feedback as F
import rx_swap
import rx_haptics
import rx_hm
import rx_latency
import rx_settings
import rx_state as S
from rx_config import C
from rx_i18n import _
from rx_report import build_report
from rx_winapi import boost_thread

DS4_REPORT_SIZE = 63    # bytes of the extended DS4 report that vgamepad takes
DS4_REPORT_EX = getattr(vg, "DS4_REPORT_EX", ctypes.c_ubyte * DS4_REPORT_SIZE)
_vcli = DS4_LIGHTBAR_COLOR = None   # without them (tests on Linux, another vgamepad version) - pad.register_notification
if vg is not None:
    try:
        from vgamepad.win.vigem_commons import DS4_REPORT_EX  # type: ignore[no-redef]  # the real class replaces the fallback
    except ImportError:
        pass
    try:
        from vgamepad.win import vigem_client as _vcli  # type: ignore[no-redef]
        from vgamepad.win.vigem_commons import DS4_LIGHTBAR_COLOR  # type: ignore[no-redef]
    except ImportError:
        _vcli = DS4_LIGHTBAR_COLOR = None
VIGEM_ERROR_NONE = 0x20000000

PLUG_ORDER_WAIT = 2.0   # a slot waits at most this long for earlier slots to plug their DS4 (the player order in a game)


class Out:
    """A slot's plugged virtual pad and the buffer of its report.

    The report is sent by the thread that changed the state (push - without waking the slot thread), or by the slot
    thread (a top-up after the pad_hz limit); lock: one report at a time. A pad with submit() (HIDMaestro) has its own
    buffer at a fixed address and its own report format (build); without it this is vgamepad: the DS4 report is copied
    into a DS4_REPORT_EX structure.
    """
    __slots__ = ("pad", "raw", "build", "rep", "ba", "view", "counter", "last", "lock")
    rep: Any       # a DS4_REPORT_EX for a vgamepad pad, None for a pad with its own buffer (HIDMaestro)
    view: Any      # a ctypes view of `ba` used to copy the report into `rep`, None as above

    def __init__(self, pad: Any) -> None:
        """Wrap a created pad.

        Pick the report builder and set up the buffer (the pad's own one, or a bytearray + a ctypes view for vgamepad).
        """
        self.pad = pad
        self.raw = hasattr(pad, "submit")
        self.build = getattr(pad, "build", build_report)
        if self.raw:
            self.rep = self.view = None
            self.ba = pad.buf                            # the report is built directly in the pad's buffer - no copy
        else:
            self.rep = DS4_REPORT_EX()
            self.ba = bytearray(DS4_REPORT_SIZE)                      # a buffer reused for every report
            self.view = (ctypes.c_char * DS4_REPORT_SIZE).from_buffer(self.ba)
        self.counter, self.last = 0, 0.0
        self.lock = threading.Lock()

    def send(self, idx: int) -> None:
        """Send the freshest state to the virtual pad of slot idx (call under self.lock); ends the change latency measurement.

        The state is the one of the physical pad that feeds this virtual pad: its own, or the partner's while the players
        are swapped (rx_swap).
        """
        src = rx_swap.route(idx)
        with S.lock:
            s = S.states[src]
            self.build(S.output(src), self.counter, S.mouse_mode[src], self.ba)     # Test view: neutral
            lat, s["lat"] = s["lat"], None
            blat, s["blat"] = s["blat"], None
        self.counter += 1
        if self.raw:
            self.pad.submit()
        else:
            ctypes.memmove(ctypes.addressof(self.rep), self.view, DS4_REPORT_SIZE)
            self.pad.update_extended_report(self.rep)
        self.last = time.perf_counter()
        if lat is not None or blat is not None:
            done = time.perf_counter_ns()
            if lat is not None:
                rx_latency.add(src, lat, done)
            if blat is not None:
                rx_latency.add_button(src, blat, done)


outs: list[Out | None] = []   # Out per slot or None (the virtual pad is not plugged)
kinds: list[str | None] = []   # the backend of the plugged pad per slot: "hm" | "vigem" | None (window: the pad card)
failing: list[bool] = []   # the slot cannot create a pad (a backend error) - higher slots do not wait for it
interval = 0.0            # 1 / pad_hz
gen = 0                   # a backend change in the settings: plugged pads unplug and are created again in the new one
hm_note: str | None = None   # the last reason HIDMaestro is not used (window: the Pads tab)
_hm_warned: str | None = None   # this reason has already reached the log (no repeats on every connection)

LABELS = {"hm": "DualSense (HIDMaestro)", "vigem": "DS4 (ViGEmBus)"}


def init() -> None:
    """Prepare the per-slot lists, the minimum report interval (1 / pad_hz) and hook push into S.notify.

    Warn about a missing vgamepad / ViGEmBus.
    """
    global interval
    F.init()
    n = C.NUM_PADS
    outs[:] = [None] * n
    kinds[:] = [None] * n
    failing[:] = [False] * n
    interval = 1.0 / C.PAD_HZ if C.PAD_HZ else 0.0
    S.push = push
    if VG_ERROR is not None:
        print(f"WARNING: vgamepad / ViGEmBus unavailable ({VG_ERROR!r}) - no virtual DS4 pads (install ViGEmBus); "
              "window, DSU and touchpad mouse still work")
    if ctypes.sizeof(DS4_REPORT_EX) < DS4_REPORT_SIZE:
        print(f"WARNING: DS4_REPORT_EX smaller than {DS4_REPORT_SIZE} bytes - report may not work")


def push(idx: int) -> None:
    """A slot state change (S.notify).

    The report to ViGEm at once in the thread that brought it (usually the network thread) - without switching to the
    slot thread. Too early per pad_hz, the DS4 not plugged or busy with another report -> the slot thread sends the
    freshest state (changes within the pad_hz window merge into one report, nothing is lost).
    """
    v = rx_swap.route(idx)                   # the virtual pad fed by this physical pad (the partner's while the players are swapped)
    o = outs[v] if v < len(outs) else None
    if o is None or not o.lock.acquire(blocking=False):
        S.wake[v].set()
        return
    try:
        if time.perf_counter() - o.last < interval:
            S.wake[v].set()
            return
        S.wake[v].clear()                    # the report below already contains this change
        o.send(v)
    except Exception:  # noqa: BLE001 - any report error goes to the slot thread, which logs it
        S.wake[v].set()                      # a ViGEm error will be handled (and logged) by the slot thread
    finally:
        o.lock.release()


def notification_type(lightbar_type: Any) -> Any:
    """The ViGEm callback type for a DS4 as in ViGEmClient.h.

    (client, target, large motor, small motor, DS4_LIGHTBAR_COLOR by value, data). vgamepad declares the colour as a
    byte - ctypes then passed a number instead of the colour.
    """
    factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)      # CALLBACK = __stdcall (on x64 the same as cdecl)
    return factory(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte, lightbar_type, ctypes.c_void_p)


def register_feedback(pad: Any, cb: Callable[..., None], vcli: Any = None, lightbar_type: Any = None) -> None:
    """Rumble and lightbar colour from the game -> cb.

    Directly through ViGEmClient with the real colour type; without access to it - through vgamepad (rumble works, the
    game colour is unknown). The reference to the callback stays in pad.cmp_func (collected by the GC = a process crash
    on the first rumble).
    """
    vcli = vcli or _vcli
    lightbar_type = lightbar_type or DS4_LIGHTBAR_COLOR
    if vcli is None or lightbar_type is None or not hasattr(pad, "_devicep"):
        pad.register_notification(cb)
        return
    fn = notification_type(lightbar_type)(cb)
    pad.cmp_func = fn
    err = vcli.vigem_target_ds4_register_notification(pad._busp, pad._devicep, fn, None)
    if err != VIGEM_ERROR_NONE:
        raise OSError(f"vigem_target_ds4_register_notification = {err:#x}")


def fixed_slots() -> bool:
    """Whether the fixed player order (placeholder pads) is enabled."""
    return rx_settings.cur.vpad.fixed_slots


def needed(idx: int) -> bool:
    """The slot should have a virtual pad.

    Its pad is connected or (fixed order) a pad of a higher slot is connected - then this slot holds the place with a
    placeholder pad, so that the player numbering in the game matches the slot number.
    """
    if S.want[idx].is_set():
        return True
    return fixed_slots() and any(w.is_set() for w in S.want[idx + 1:])


def wait_turn(idx: int, timeout: float = PLUG_ORDER_WAIT) -> bool:
    """Virtual pads are plugged in slot order.

    Windows / Steam / XInput number players by the order in which devices appear, so slot 0 (the same pad thanks to the
    sender's slots.json) stays player 1 even when the packets of slot 1 arrived a fraction of a second earlier. It waits
    only for slots that also need a pad (and have no backend error), at most timeout.
    """
    end = time.monotonic() + timeout
    while not S.stop.is_set() and time.monotonic() < end:
        if not any(needed(j) and not S.plugged[j] and not failing[j] for j in range(idx)):
            return True
        time.sleep(0.02)
    return False


def backend_changed(old: rx_settings.Settings, new: rx_settings.Settings) -> None:
    """Settings: another backend / DLL -> pads are created again; another order -> the slot threads check it at once."""
    global gen, _hm_warned
    if (old.vpad.backend, old.vpad.hm_dll) != (new.vpad.backend, new.vpad.hm_dll):
        rx_hm.reset()
        _hm_warned = None
        gen += 1
    if old.vpad != new.vpad and S.wake is not None:
        for w in S.wake:
            w.set()


rx_settings.on_change(backend_changed)


def _note_hm(text: str, warn: bool) -> None:
    """The reason for working without HIDMaestro: the window (hm_note) and once in the log (a warning or information)."""
    global hm_note, _hm_warned
    hm_note = text
    if text == _hm_warned:
        return
    _hm_warned = text
    print(f"HIDMaestro not used: {text}")
    (ev.warn if warn else ev.info)(_("HIDMaestro nieużywany - wirtualne pady przez ViGEmBus: {why}").format(why=text))


def hm_auto_enabled() -> bool:
    """Downloading HIDMaestro from GitHub: the backend uses it, the automatic mode is on and there is no own DLL path."""
    cfg = rx_settings.cur.vpad
    return cfg.backend != "vigem" and cfg.hm_auto and not cfg.hm_dll


def hm_installed(tag: str) -> None:
    """rx_hm_update downloaded a new version.

    Nothing was loaded -> the next pad connection already goes through HIDMaestro (plugged pads stay - no switching
    during a game); an older one is loaded -> the new one after a restart.
    """
    global _hm_warned
    if rx_hm.loaded() is None:
        rx_hm.reset()
        _hm_warned = None
        ev.info(_("HIDMaestro {v} pobrany - pady przejdą na DualSense przy następnym połączeniu").format(v=tag))
    else:
        ev.info(_("HIDMaestro {v} pobrany - zadziała po restarcie receivera").format(v=tag))


def hm_backend() -> rx_hm.Backend | None:
    """-> the HIDMaestro backend or None (the vigem setting or HIDMaestro unavailable - the reason in hm_note)."""
    global hm_note
    cfg = rx_settings.cur.vpad
    if cfg.backend == "vigem":
        hm_note = None
        return None
    try:
        b = rx_hm.get(cfg.hm_dll)
    except rx_hm.HMError as e:
        # auto without a DLL is an ordinary situation (HIDMaestro not installed) - information only
        _note_hm(str(e), warn=not (e.missing and cfg.backend == "auto"))
        return None
    hm_note = None
    return b


def vigem_pad(idx: int) -> Any:
    """Create a ViGEm virtual DS4 and register its rumble / lightbar callback (a failed registration is only reported)."""
    if vg is None:
        raise RuntimeError(f"vgamepad / ViGEmBus: {VG_ERROR!r}")
    pad = vg.VDS4Gamepad()
    try:
        register_feedback(pad, F.cbs[idx])
    except Exception as e:  # noqa: BLE001 - the pad still works without feedback, so only report
        print(f"Rumble/lightbar notification unavailable for pad {idx}: {e!r}")
    return pad


def create_pad(idx: int) -> tuple[Any, str]:
    """-> (pad, kind).

    HIDMaestro when available; its creation error = this pad through ViGEm (log), the next connection tries HIDMaestro
    again (PnP errors can be transient).
    """
    hm = hm_backend()
    if hm is not None:
        def on_fb(motors: tuple[int, int] | None, rgb: Sequence[int] | None, trig: Any = None,
                  leds: tuple[int | None, int | None] | None = None) -> None:
            large, small = motors or (None, None)
            F.game(idx, large, small, rgb=rgb, trig=trig, leds=leds)
        try:
            pad = rx_haptics.create_composite(hm, idx, on_fb) if rx_haptics.composite_wanted(idx) else None
            return (pad if pad is not None else hm.create(idx, on_fb)), "hm"
        except Exception as e:  # noqa: BLE001 - any HIDMaestro / .NET failure falls back to ViGEm
            _note_hm(_("tworzenie pada: {err}").format(err=rx_hm.describe_error(e)), warn=True)
    return vigem_pad(idx), "vigem"


def close_pad(pad: Any) -> None:
    """HIDMaestro: Dispose removes the device. vgamepad.

    Unregister the callback; the device is unplugged by the destructor (the last reference disappears at the caller,
    then gc.collect).
    """
    if hasattr(pad, "close"):
        pad.close()
        return
    try:
        pad.unregister_notification()
    except Exception:  # noqa: BLE001, S110 - best effort: the pad may already be gone
        pass


def _pump(idx: int, o: Out, my_gen: int) -> None:
    """Send the freshest state of a slot while its pad is needed (at most pad_hz; changes inside the window merge)."""
    while needed(idx) and not S.stop.is_set() and gen == my_gen:
        if not S.wake[idx].wait(0.5):
            continue
        d = o.last + interval - time.perf_counter()
        if d > 0:
            time.sleep(d)          # further changes in this window merge into one report
        if not needed(idx) or S.stop.is_set() or gen != my_gen:
            break
        with o.lock:
            if not S.wake[idx].is_set():
                continue           # in the meantime push (the network thread) has sent it - no second identical one
            S.wake[idx].clear()
            o.send(idx)


def _publish(idx: int, pad: Any, kind: str) -> Out:
    """Wrap a freshly created pad and make it visible to push() and the window; -> its Out.

    The caller keeps `pad`, so it can unplug it even when a step here fails.
    """
    o = Out(pad)
    with S.lock:       # a change from before plugging waited for the pad creation (wait_turn, PnP: up to several s) - that is not
        S.states[idx]["lat"] = S.states[idx]["blat"] = None     # latency, just a spike of the max and mean after connecting
    kinds[idx] = kind
    S.plugged[idx] = True
    failing[idx] = False
    role = "" if S.want[idx].is_set() else " (placeholder - keeps player order)"
    print(f"Controller {idx}: virtual {LABELS[kind]} plugged in{role}")
    outs[idx] = o
    S.wake[idx].set()   # send the current state at once
    return o


def _slot_error(idx: int, e: Exception, last_err: str | None) -> str:
    """Report a virtual pad error once per distinct message; -> the message to remember."""
    err_str = repr(e)
    if err_str != last_err:
        print(f"Slot {idx}: virtual pad error: {e!r} (is ViGEmBus installed?)")
        ev.error(_("{pad}: błąd wirtualnego pada: {err} (czy ViGEmBus jest zainstalowany?)").format(
            pad=ev.pad_name(idx), err=err_str))
    return err_str


def _release_slot(idx: int) -> None:
    """Mark the slot unplugged and stop push() from using its Out; a report in progress finishes first."""
    S.plugged[idx] = False
    kinds[idx] = None
    o, outs[idx] = outs[idx], None       # push from other threads will no longer use it ...
    if o is not None:
        with o.lock:                     # ... and a report in progress finishes before the unplug
            o.pad = None


def _close_quietly(idx: int, pad: Any) -> None:
    """close_pad with the error only logged (the slot is being torn down anyway)."""
    try:
        close_pad(pad)
    except Exception as e:  # noqa: BLE001 - an unplug error must not stop the slot thread
        print(f"Controller {idx}: unplug error: {e!r}")


def slot_thread(idx: int) -> None:
    """The virtual pad exists while needed(idx) (the physical pad is connected or a placeholder for the fixed order).

    Event-driven: sleeps until data arrives, sends the freshest state, at most pad_hz. On any error the pad is unplugged
    and retried after a second; the pad is always unplugged and the game feedback reset in the end.
    """
    last_err: str | None = None
    while len(outs) <= idx:                   # without init() (tests) - the slot thread must not fail on an empty list
        outs.append(None)
        kinds.append(None)
        failing.append(False)
    boost_thread()
    while not S.stop.is_set():
        if not (S.want[idx].wait(0.25) or needed(idx)):
            continue
        if S.stop.is_set():
            break
        pad = o = None
        my_gen = gen
        order = rx_hm.ORDER_WAIT if rx_settings.cur.vpad.backend != "vigem" else PLUG_ORDER_WAIT
        if idx and not wait_turn(idx, order):
            print(f"Controller {idx}: earlier slot not ready - plugging in anyway (player order may differ)")
        try:
            F.reset(rx_swap.route(idx))          # the game feedback of this virtual pad lives with the physical pad it is fed by
            pad, kind = create_pad(idx)
            o = _publish(idx, pad, kind)
            last_err = None
            _pump(idx, o, my_gen)
        except Exception as e:  # noqa: BLE001 - any backend / driver error: unplug and retry, never kill the thread
            failing[idx] = True
            last_err = _slot_error(idx, e, last_err)
            # want stays: another attempt in a moment in the same loop. Clearing it pretended the pad disconnected -
            # the next packet "connected" it again (rx_console.pad_connected: console mode auto-ON every second)
            S.stop.wait(1.0)
        finally:
            _release_slot(idx)
            if pad is not None:
                o = None                         # drop our references first: vgamepad unplugs in the destructor
                _close_quietly(idx, pad)
                pad = None
                gc.collect()
                print(f"Controller {idx}: virtual pad unplugged")
            rx_haptics.detach(idx)
            # rumble and colour from the game belonged to the unplugged pad: without this, a pad that connects in this slot vibrated with
            # the game's old rumble (refreshed every 0.5 s) before a new one is created - and on a backend error forever
            F.reset(rx_swap.route(idx))
