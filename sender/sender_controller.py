#!/usr/bin/env python3
"""DualSense UDP sender (Linux, evdev) - protokół v2, patrz proto.py.

  doas python3 sender_controller.py 172.16.42.2 --key SEKRET --stats
  doas python3 sender_controller.py --config sender.toml

Pliki: proto.py (wspólny), sender_config.py, sender_state.py, sender_net.py,
       sender_ctrl.py, sender_streams.py, sender_supervisor.py, sender_control.py, sender_rumble.py (wibracja).
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
import traceback

import sender_config
import sender_control as control
import sender_net as net
import sender_streams as streams
import sender_state as state
import sender_supervisor as sup
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
            else:
                print(f"{name} error #{errors} - restarting: {e!r}")
            await asyncio.sleep(min(5.0, 0.2 * errors))


async def main():
    print(f"Sending to {C.HOST}:{C.PORT}" + (" (HMAC on)" if C.KEY else " (NO authentication - consider --key)"))
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


if __name__ == "__main__":
    sender_config.init()
    net.init()
    net.on_feedback = sup.dispatch_feedback
    net.on_command = control.on_command
    try:
        os.nice(-10)
    except OSError:
        pass
    if C.RT_PRIORITY:
        try:   # opcjonalnie: planista czasu rzeczywistego (wymaga roota/doas); 0 = wyłączone
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(C.RT_PRIORITY))
            print(f"Realtime scheduling: SCHED_FIFO {C.RT_PRIORITY}")
        except (OSError, AttributeError) as e:
            print(f"Realtime scheduling unavailable: {e}")
    gc.collect()
    gc.freeze()   # obiekty ze startu poza zasięgiem GC - krótsze pauzy
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped.")
