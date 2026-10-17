"""Receiver: wirtualne pady na żądanie - DualSense przez HIDMaestro (rx_hm) albo DS4 przez ViGEmBus (vgamepad);
feedback z gry trafia do rx_feedback.

Backend (Ustawienia -> Pady, rx_settings.VpadConfig): auto = HIDMaestro, gdy jest DLL i receiver działa jako
administrator, inaczej ViGEm; hidmaestro = jak auto, ale brak / błąd HIDMaestro to ostrzeżenie w dzienniku (dalej
ViGEm jako zapas); vigem = zawsze ViGEm. Zmiana backendu w oknie odłącza i tworzy pady od nowa (bez restartu).

Stała kolejność graczy (fixed_slots): gry, Steam i Windows numerują pady wg kolejności pojawienia się urządzeń.
Gdy połączy się pad slotu 2, a slot 1 jest pusty, slot 1 dostaje najpierw pad zastępczy (neutralny stan), więc pad 2
nigdy nie zostaje graczem 1. Zastępczy pad znika razem z ostatnim padem z wyższego slotu."""
import ctypes
import gc
import threading
import time

try:
    import vgamepad as vg
except Exception as e:      # brak / uszkodzony ViGEmBus: vgamepad łączy się z nim już przy imporcie - bez tego receiver
    vg, VG_ERROR = None, e  # padał przy starcie (z autostartu po cichu); tak działa okno, DSU i mysz, a błąd jest w dzienniku
else:
    VG_ERROR = None

import rx_events as ev
import rx_feedback as F
import rx_hm
import rx_latency
import rx_settings
import rx_state as S
from rx_config import C
from rx_i18n import _
from rx_report import build_report
from rx_winapi import boost_thread

DS4_REPORT_EX = getattr(vg, "DS4_REPORT_EX", ctypes.c_ubyte * 63)
_vcli = DS4_LIGHTBAR_COLOR = None   # bez nich (testy na Linuksie, inna wersja vgamepad) - pad.register_notification
if vg is not None:
    try:
        from vgamepad.win.vigem_commons import DS4_REPORT_EX
    except ImportError:
        pass
    try:
        from vgamepad.win import vigem_client as _vcli
        from vgamepad.win.vigem_commons import DS4_LIGHTBAR_COLOR
    except ImportError:
        _vcli = DS4_LIGHTBAR_COLOR = None
VIGEM_ERROR_NONE = 0x20000000

PLUG_ORDER_WAIT = 2.0   # max tyle slot czeka, aż wcześniejsze sloty podłączą swoje DS4 (kolejność graczy w grze)


class Out:
    """Podłączony wirtualny pad slotu i bufor jego raportu. Raport wysyła wątek, który zmienił stan (push - bez
    budzenia wątku slotu), albo wątek slotu (dosłanie po limicie pad_hz); lock: jeden raport naraz.
    Pad z submit() (HIDMaestro) ma własny bufor o stałym adresie i własny format raportu (build); bez niego to
    vgamepad: raport DS4 kopiowany do struktury DS4_REPORT_EX."""
    __slots__ = ("pad", "raw", "build", "rep", "ba", "view", "counter", "last", "lock")

    def __init__(self, pad):
        self.pad = pad
        self.raw = hasattr(pad, "submit")
        self.build = getattr(pad, "build", build_report)
        if self.raw:
            self.rep = self.view = None
            self.ba = pad.buf                            # raport budowany wprost w buforze pada - bez kopii
        else:
            self.rep = DS4_REPORT_EX()
            self.ba = bytearray(63)                      # bufor używany ponownie przy każdym raporcie
            self.view = (ctypes.c_char * 63).from_buffer(self.ba)
        self.counter, self.last = 0, 0.0
        self.lock = threading.Lock()

    def send(self, idx):
        """Najświeższy stan slotu do wirtualnego pada (wołać pod self.lock); kończy pomiar opóźnienia zmiany."""
        with S.lock:
            s = S.states[idx]
            self.build(S.output(idx), self.counter, S.mouse_mode[idx], self.ba)     # widok Test: neutralny
            lat, s["lat"] = s["lat"], None
            blat, s["blat"] = s["blat"], None
        self.counter += 1
        if self.raw:
            self.pad.submit()
        else:
            ctypes.memmove(ctypes.addressof(self.rep), self.view, 63)
            self.pad.update_extended_report(self.rep)
        self.last = time.perf_counter()
        if lat is not None or blat is not None:
            done = time.perf_counter_ns()
            if lat is not None:
                rx_latency.add(idx, lat, done)
            if blat is not None:
                rx_latency.add_button(idx, blat, done)


outs = []                 # Out per slot albo None (wirtualny pad niepodłączony)
kinds = []                # backend podłączonego pada per slot: "hm" | "vigem" | None (okno: karta pada)
failing = []              # slot nie może utworzyć pada (błąd backendu) - wyższe sloty nie czekają na niego
interval = 0.0            # 1 / pad_hz
gen = 0                   # zmiana backendu w ustawieniach: podłączone pady odłączają się i powstają w nowym
hm_note = None            # ostatni powód, dla którego HIDMaestro nie jest używany (okno: zakładka Pady)
_hm_warned = None         # ten powód już trafił do dziennika (bez powtórek przy każdym podłączeniu)

LABELS = {"hm": "DualSense (HIDMaestro)", "vigem": "DS4 (ViGEmBus)"}


def init():
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
    if ctypes.sizeof(DS4_REPORT_EX) < 63:
        print("WARNING: DS4_REPORT_EX smaller than 63 bytes - report may not work")


def push(idx):
    """Zmiana stanu slotu (S.notify): raport do ViGEm od razu w wątku, który ją przyniósł (zwykle wątek sieci) -
    bez przełączenia na wątek slotu. Za wcześnie wg pad_hz, DS4 niepodłączony albo zajęty innym raportem ->
    wątek slotu dośle najświeższy stan (zmiany w oknie pad_hz zlewają się w jeden raport, nic nie ginie)."""
    o = outs[idx] if idx < len(outs) else None
    if o is None or not o.lock.acquire(blocking=False):
        S.wake[idx].set()
        return
    try:
        if time.perf_counter() - o.last < interval:
            S.wake[idx].set()
            return
        S.wake[idx].clear()                  # raport poniżej zawiera już tę zmianę
        o.send(idx)
    except Exception:
        S.wake[idx].set()                    # błąd ViGEm obsłuży (i zaloguje) wątek slotu
    finally:
        o.lock.release()


def notification_type(lightbar_type):
    """Typ callbacku ViGEm dla DS4 jak w ViGEmClient.h: (klient, cel, duży silnik, mały silnik, DS4_LIGHTBAR_COLOR
    przez wartość, dane). vgamepad deklaruje kolor jako bajt - ctypes podawał wtedy liczbę zamiast koloru."""
    factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)      # CALLBACK = __stdcall (na x64 to samo co cdecl)
    return factory(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte, lightbar_type, ctypes.c_void_p)


def register_feedback(pad, cb, vcli=None, lightbar_type=None):
    """Wibracja i kolor lightbara z gry -> cb. Bezpośrednio przez ViGEmClient z prawdziwym typem koloru; bez dostępu
    do niego - przez vgamepad (wibracja działa, kolor gry nieznany). Referencja do callbacku zostaje w pad.cmp_func
    (zebrany przez GC = awaria procesu przy pierwszej wibracji)."""
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


def fixed_slots():
    return rx_settings.cur.vpad.fixed_slots


def needed(idx):
    """Slot ma mieć wirtualny pad: jego pad jest połączony albo (stała kolejność) połączony jest pad wyższego slotu -
    wtedy ten slot trzyma miejsce padem zastępczym, żeby numeracja graczy w grze zgadzała się z numerem slotu."""
    if S.want[idx].is_set():
        return True
    return fixed_slots() and any(w.is_set() for w in S.want[idx + 1:])


def wait_turn(idx, timeout=PLUG_ORDER_WAIT):
    """Wirtualne pady są podłączane w kolejności slotów: Windows / Steam / XInput numerują graczy wg kolejności
    pojawienia się urządzeń, więc slot 0 (ten sam pad dzięki slots.json nadajnika) zostaje graczem 1 także wtedy,
    gdy pakiety slotu 1 przyszły ułamek sekundy wcześniej. Czeka tylko na sloty, które też potrzebują pada (i nie
    mają błędu backendu), max timeout."""
    end = time.monotonic() + timeout
    while not S.stop.is_set() and time.monotonic() < end:
        if not any(needed(j) and not S.plugged[j] and not failing[j] for j in range(idx)):
            return True
        time.sleep(0.02)
    return False


def backend_changed(old, new):
    """Ustawienia: inny backend / DLL -> pady powstają od nowa; inna kolejność -> wątki slotów sprawdzają ją od razu."""
    global gen, _hm_warned
    if (old.vpad.backend, old.vpad.hm_dll) != (new.vpad.backend, new.vpad.hm_dll):
        rx_hm.reset()
        _hm_warned = None
        gen += 1
    if old.vpad != new.vpad and S.wake is not None:
        for w in S.wake:
            w.set()


rx_settings.on_change(backend_changed)


def _note_hm(text, warn):
    """Powód pracy bez HIDMaestro: okno (hm_note) i raz w dzienniku (ostrzeżenie albo informacja)."""
    global hm_note, _hm_warned
    hm_note = text
    if text == _hm_warned:
        return
    _hm_warned = text
    print(f"HIDMaestro not used: {text}")
    (ev.warn if warn else ev.info)(_("HIDMaestro nieużywany - wirtualne pady przez ViGEmBus: {why}").format(why=text))


def hm_backend():
    """-> backend HIDMaestro albo None (ustawienie vigem albo HIDMaestro niedostępny - powód w hm_note)."""
    global hm_note
    cfg = rx_settings.cur.vpad
    if cfg.backend == "vigem":
        hm_note = None
        return None
    try:
        b = rx_hm.get(cfg.hm_dll)
    except rx_hm.HMError as e:
        # auto bez DLL to zwykła sytuacja (HIDMaestro nie zainstalowany) - tylko informacja
        _note_hm(str(e), warn=not (e.missing and cfg.backend == "auto"))
        return None
    hm_note = None
    return b


def vigem_pad(idx):
    if vg is None:
        raise RuntimeError(f"vgamepad / ViGEmBus: {VG_ERROR!r}")
    pad = vg.VDS4Gamepad()
    try:
        register_feedback(pad, F.cbs[idx])
    except Exception as e:
        print(f"Rumble/lightbar notification unavailable for pad {idx}: {e!r}")
    return pad


def create_pad(idx):
    """-> (pad, rodzaj). HIDMaestro, gdy jest; jego błąd przy tworzeniu = ten pad przez ViGEm (dziennik), następne
    podłączenie znów próbuje HIDMaestro (błędy PnP bywają chwilowe)."""
    hm = hm_backend()
    if hm is not None:
        def on_fb(motors, rgb):
            F.game(idx, *(motors or (None, None)), rgb=rgb)
        try:
            return hm.create(idx, on_fb), "hm"
        except Exception as e:
            _note_hm(_("tworzenie pada: {err}").format(err=repr(e)), warn=True)
    return vigem_pad(idx), "vigem"


def close_pad(pad):
    """HIDMaestro: Dispose usuwa urządzenie. vgamepad: bez callbacku; urządzenie odłącza destruktor (ostatnia referencja
    znika u wołającego, potem gc.collect)."""
    if hasattr(pad, "close"):
        pad.close()
        return
    try:
        pad.unregister_notification()
    except Exception:
        pass


def slot_thread(idx):
    """Wirtualny pad istnieje, gdy needed(idx) (pad fizycznie podłączony albo zastępczy dla stałej kolejności).
    Event-driven: śpi do nadejścia danych, wysyła najświeższy stan, max pad_hz."""
    last_err = None
    while len(outs) <= idx:                   # bez init() (testy) - wątek slotu nie może paść na pustej liście
        outs.append(None)
        kinds.append(None)
        failing.append(False)
    boost_thread()
    while not S.stop.is_set():
        if not (S.want[idx].wait(0.25) or needed(idx)):
            continue
        if S.stop.is_set():
            break
        pad = None
        my_gen = gen
        order = rx_hm.ORDER_WAIT if rx_settings.cur.vpad.backend != "vigem" else PLUG_ORDER_WAIT
        if idx and not wait_turn(idx, order):
            print(f"Controller {idx}: earlier slot not ready - plugging in anyway (player order may differ)")
        try:
            F.reset(idx)
            pad, kind = create_pad(idx)
            o = Out(pad)
            with S.lock:       # zmiana sprzed podłączenia czekała na tworzenie pada (wait_turn, PnP: do kilku s) - to nie
                S.states[idx]["lat"] = S.states[idx]["blat"] = None     # opóźnienie, tylko skok max i średniej po połączeniu
            kinds[idx] = kind
            S.plugged[idx] = True
            failing[idx] = False
            last_err = None
            role = "" if S.want[idx].is_set() else " (placeholder - keeps player order)"
            print(f"Controller {idx}: virtual {LABELS[kind]} plugged in{role}")
            outs[idx] = o
            S.wake[idx].set()   # od razu wyślij aktualny stan
            while needed(idx) and not S.stop.is_set() and gen == my_gen:
                if not S.wake[idx].wait(0.5):
                    continue
                d = o.last + interval - time.perf_counter()
                if d > 0:
                    time.sleep(d)          # kolejne zmiany w tym oknie zlewają się w jeden raport
                if not needed(idx) or S.stop.is_set() or gen != my_gen:
                    break
                with o.lock:
                    if not S.wake[idx].is_set():
                        continue           # w międzyczasie wysłał go push (wątek sieci) - bez drugiego takiego samego
                    S.wake[idx].clear()
                    o.send(idx)
        except Exception as e:
            failing[idx] = True
            err_str = repr(e)
            if err_str != last_err:
                print(f"Slot {idx}: virtual pad error: {e!r} (is ViGEmBus installed?)")
                ev.error(_("{pad}: błąd wirtualnego pada: {err} (czy ViGEmBus jest zainstalowany?)").format(
                    pad=ev.pad_name(idx), err=err_str))
                last_err = err_str
            # want zostaje: ponowna próba za chwilę w tej samej pętli. Wyczyszczenie udawało rozłączenie pada -
            # następny pakiet "łączył" go od nowa (rx_console.pad_connected: auto-ON trybu konsoli co sekundę)
            S.stop.wait(1.0)
        finally:
            S.plugged[idx] = False
            kinds[idx] = None
            o, outs[idx] = outs[idx], None       # push z innych wątków już go nie użyje ...
            if o is not None:
                with o.lock:                     # ... a trwający raport kończy się przed odłączeniem
                    o.pad = None
            if pad is not None:
                o = None
                try:
                    close_pad(pad)
                except Exception as e:
                    print(f"Controller {idx}: unplug error: {e!r}")
                pad = None
                gc.collect()
                print(f"Controller {idx}: virtual pad unplugged")
            # wibracja i kolor z gry należały do odłączonego pada: bez tego pad, który połączy się w tym slocie, wibrował
            # starą wibracją gry (odświeżaną co 0.5 s), zanim powstanie nowy - a przy błędzie backendu bez końca
            F.reset(idx)
