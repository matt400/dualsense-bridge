"""Receiver: wirtualne DS4 (ViGEm) na żądanie; feedback z gry trafia do rx_feedback."""
import ctypes
import gc
import time

import vgamepad as vg

import rx_feedback as F
import rx_state as S
from rx_config import C
from rx_report import build_report
from rx_winapi import boost_thread

try:
    from vgamepad.win.vigem_commons import DS4_REPORT_EX
except ImportError:
    DS4_REPORT_EX = vg.DS4_REPORT_EX

PLUG_ORDER_WAIT = 2.0   # max tyle slot czeka, aż wcześniejsze sloty podłączą swoje DS4 (kolejność graczy w grze)


def init():
    F.init()
    if ctypes.sizeof(DS4_REPORT_EX) < 63:
        print("WARNING: DS4_REPORT_EX smaller than 63 bytes - report may not work")


def wait_turn(idx, timeout=PLUG_ORDER_WAIT):
    """Wirtualne pady są podłączane w kolejności slotów: Windows / Steam / XInput numerują graczy wg kolejności
    pojawienia się urządzeń, więc slot 0 (ten sam pad dzięki slots.json nadajnika) zostaje graczem 1 także wtedy,
    gdy pakiety slotu 1 przyszły ułamek sekundy wcześniej. Czeka tylko na sloty, które też chcą pada, max timeout."""
    end = time.monotonic() + timeout
    while not S.stop.is_set() and time.monotonic() < end:
        if not any(S.want[j].is_set() and not S.plugged[j] for j in range(idx)):
            return True
        time.sleep(0.02)
    return False


def slot_thread(idx):
    """Wirtualny DS4 istnieje tylko, gdy want[idx] jest ustawione (pad fizycznie podłączony).
    Event-driven: śpi do nadejścia danych, wysyła najświeższy stan, max pad_hz."""
    last_err = None
    interval = 1.0 / C.PAD_HZ if C.PAD_HZ else 0.0
    boost_thread()
    while not S.stop.is_set():
        if not S.want[idx].wait(0.25):
            continue
        if S.stop.is_set():
            break
        pad = None
        if idx and not wait_turn(idx):
            print(f"Controller {idx}: earlier slot not ready - plugging in anyway (player order may differ)")
        try:
            pad = vg.VDS4Gamepad()
            F.reset(idx)
            try:
                pad.register_notification(F.cbs[idx])
            except Exception as e:
                print(f"Rumble/lightbar notification unavailable for pad {idx}: {e!r}")
            S.plugged[idx] = True
            last_err = None
            print(f"Controller {idx}: virtual DS4 plugged in")
            rep = DS4_REPORT_EX()
            ba = bytearray(63)                              # bufor używany ponownie przy każdym raporcie
            view = (ctypes.c_char * 63).from_buffer(ba)
            counter = 0
            last = 0.0
            S.wake[idx].set()   # od razu wyślij aktualny stan
            while S.want[idx].is_set() and not S.stop.is_set():
                if not S.wake[idx].wait(0.5):
                    continue
                d = last + interval - time.perf_counter()
                if d > 0:
                    time.sleep(d)          # kolejne zmiany w tym oknie zlewają się w jeden raport
                S.wake[idx].clear()
                if not S.want[idx].is_set() or S.stop.is_set():
                    break
                with S.lock:
                    build_report(S.states[idx], counter, S.mouse_mode[idx], ba)
                counter += 1
                ctypes.memmove(ctypes.addressof(rep), view, 63)
                pad.update_extended_report(rep)
                last = time.perf_counter()
        except Exception as e:
            err_str = repr(e)
            if err_str != last_err:
                print(f"Slot {idx}: virtual DS4 error: {e!r} (is ViGEmBus installed?)")
                last_err = err_str
            # want zostaje: ponowna próba za chwilę w tej samej pętli. Wyczyszczenie udawało rozłączenie pada -
            # następny pakiet "łączył" go od nowa (rx_console.pad_connected: auto-ON trybu konsoli co sekundę)
            S.stop.wait(1.0)
        finally:
            S.plugged[idx] = False
            if pad is not None:
                try:
                    pad.unregister_notification()
                except Exception:
                    pass
                try:
                    del view, ba
                except NameError:
                    pass
                del pad            # destruktor odłącza urządzenie od ViGEm
                gc.collect()
                print(f"Controller {idx}: virtual DS4 unplugged")
