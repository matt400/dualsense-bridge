#!/usr/bin/env python3
"""DualSense UDP sender (Linux, evdev) - protokół v2, patrz proto.py.

  doas python3 sender_controller.py 172.16.42.2 --key SEKRET --stats
  doas python3 sender_controller.py --config sender.toml

Pliki: proto.py (wspólny), sender_config.py, sender_state.py, sender_net.py,
       sender_ctrl.py, sender_streams.py, sender_supervisor.py, sender_control.py, sender_rumble.py (wibracja),
       sender_wol.py (Wake-on-LAN: PS + przycisk przy milczącym receiverze), sender_events.py (zdarzenia do okna PC).
Konfiguracja: --config plik.toml|json (klucze jak w DEFAULTS), flagi CLI nadpisują plik.
--stats pokazuje też realną częstotliwość raportów pada i ruchu (Hz, przerwy p99/max).
Wyłączanie pada: przyciskiem "Off" w oknie receivera albo samo po idle_off_min minutach bez użycia
(bluetoothctl disconnect - wymaga bluez). Co sekundę nadajnik wysyła ping i realne Hz pada do okna receivera.
Gesty touchpada (gestures, touch_start_move) i opóźnienie dotyku (touch_coalesce_ms) - patrz sender_config.DEFAULTS.
"""
import asyncio
import gc
import os
import signal
import socket
import sys
import traceback

import sender_config
import sender_control as control
import sender_events as events
import sender_net as net
import sender_rt
import sender_streams as streams
import sender_state as state
import sender_supervisor as sup
import sender_update
import sender_wol as wol
from proto import E_ERROR, E_START
from sender_config import C


async def forever(name, fn):
    """Długo działająca pętla, której nieoczekiwany wyjątek nie zatrzyma całego nadajnika:
    błąd jest wypisywany (pierwszy z pełnym śladem), a pętla startuje ponownie po chwili."""
    errors = 0
    while True:
        try:
            await fn()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            errors += 1
            if errors == 1:
                print(f"{name} error - restarting:\n{traceback.format_exc()}")
                events.error(E_ERROR, 255, f"{name}: {e!r}")
            else:
                print(f"{name} error #{errors} - restarting: {e!r}")
            await asyncio.sleep(min(5.0, 0.2 * errors))


async def main():
    print(f"Sending to {C.HOST}:{C.PORT}" + (" (HMAC on)" if C.KEY else " (NO authentication - consider --key)"))
    state.main_task = asyncio.current_task()
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    except (NotImplementedError, RuntimeError):
        pass
    try:
        await asyncio.gather(*(forever(name, fn) for name, fn in (
            ("socket", net.sock_keeper), ("heartbeat", streams.heartbeat), ("supervisor", sup.supervisor),
            ("pinger", net.pinger), ("info", control.info_loop))))
    finally:
        # najpierw pady: ich sprzątanie wysyła neutralny stan i "bye" - gniazdo musi jeszcze działać
        for t in list(state.bg_tasks):
            t.cancel()
        await asyncio.gather(*state.bg_tasks, return_exceptions=True)
        net.close_sock()


LOCK_NAME = b"\0dualsense-bridge-sender"
_lock = None


def single_instance(name=LOCK_NAME):
    """Jeden nadajnik na telefonie (np. usługa root i stary autostart użytkownika naraz walczyłyby o pady).
    Abstrakcyjne gniazdo Unix: wspólne dla wszystkich użytkowników, znika samo, gdy proces się kończy."""
    global _lock
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.bind(name)
    except OSError:
        s.close()
        return False
    _lock = s
    return True


if __name__ == "__main__":
    sender_config.init()
    if not single_instance():
        raise SystemExit("Another sender is already running (service or autostart) - stop it first.")
    net.init()
    net.on_feedback = sup.dispatch_feedback
    net.on_command = control.on_command
    net.on_triggers = sup.dispatch_triggers
    net.on_scfg = wol.apply_scfg
    wol.load()                         # Wake-on-LAN i zdarzenia: ustawienia z okna receivera zapisane przy ostatnim scfg
    sender_rt.setup(C.LOW_LATENCY, C.CPU_LATENCY_US, C.RT_PRIORITY)
    gc.collect()
    gc.freeze()   # obiekty ze startu poza zasięgiem GC - krótsze pauzy
    sender_update.init()
    events.info(E_START, 255, sender_update.version[0] or "?")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped.")
    if state.restart:                  # po aktualizacji: nowy kod w tym samym procesie (PID dla usługi bez zmian)
        print("Restarting with the updated code")
        sys.stdout.flush()
        # orig_argv: z opcjami interpretera (usługa: python3 -u) - bez -u log po aktualizacji szedłby z opóźnieniem (bufor)
        os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
