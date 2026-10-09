"""DualSense UDP receiver (Windows): wirtualny DS4 (ViGEm) + serwer DSU (Cemuhook) + okno z baterią
i remapowaniem przycisków. Protokół: proto.py.

Pliki: proto.py (wspólny), rx_config.py, rx_state.py, rx_buttons.py (remap + remap.json),
       rx_shape.py (strefy martwe, krzywe, gyro), rx_hotkeys.py (skróty PS), rx_games.py (profil wg gry),
       rx_gyro.py (gyro jako mysz), rx_feedback.py (wibracja, lightbar, potwierdzenia),
       rx_winapi.py, rx_mouse.py, rx_report.py, rx_net.py, rx_dsu.py, rx_ds4.py,
       rx_tray.py, rx_gui.py, rx_web.py + rx_remap_ui.html (edytor remapowania).

Wirtualny DS4 powstaje dopiero po pierwszym pakiecie z danego slotu i znika, gdy nadajnik
zgłosi rozłączenie pada (pakiet 8) albo milczy dłużej niż unplug_s.

Po starcie widać tylko ikonę w zasobniku. Klik w ikonę (albo prawy klik: Show) pokazuje okno z baterią;
przycisk "Remap" przy padzie otwiera w przeglądarce edytor SVG (zmiany działają od razu, Save = zapis do
remap.json). Adres edytora (z tokenem, tylko 127.0.0.1) jest też wypisany w konsoli / logu.
Profil jest przypisany do pada (MAC), nie do slotu, więc zamiana kolejności padów go nie gubi.
Skróty (PS jako modyfikator, ps_modifier): PS + klik touchpada = mysz / dotyk DS4, PS + Share = zrzut ekranu,
PS + d-pad góra/dół = czułość myszy, PS + d-pad lewo/prawo = zmiana profilu, długie PS = Game Bar / Big Picture.
Profile gier: profil "pad@gra.exe" włącza się sam, gdy ta gra jest na pierwszym planie (zakładka w edytorze).
Gyro: celowanie żyroskopem jako prawa gałka albo mysz, ustawiane w profilu (edytor -> Gyro aim).
W trybie myszy: 1 palec = kursor, tap = LPM, 2 palce = przewijanie, tap 2 palcami = PPM (scroll_sens, scroll_natural).
Konfiguracja: --config plik.toml|json (klucze jak w DEFAULTS), flagi CLI nadpisują plik.
Autostart z Windows: --autostart on|off (wpis w HKCU\\...\\Run, uruchamia pythonw bez konsoli).
"""
import gc
import sys
import time

import rx_config
from rx_config import C


def main():
    rx_config.init()
    sys.setswitchinterval(0.001)   # domyślne 5 ms = tyle może czekać wątek sieciowy, gdy inny trzyma GIL

    import rx_winapi
    rx_winapi.init_process()
    rx_winapi.ensure_single_instance()

    import rx_buttons
    import rx_ds4
    import rx_dsu
    import rx_feedback
    import rx_games
    import rx_gyro
    import rx_hotkeys
    import rx_mouse
    import rx_net
    import rx_state as S
    import rx_web

    S.init(rx_buttons.RemapStore(C.REMAP_PATH))
    rx_mouse.init(C.NUM_PADS)
    rx_hotkeys.init(C.NUM_PADS)
    rx_ds4.init()
    rx_net.init()
    rx_dsu.start()
    rx_web.start()

    gc.collect()
    gc.freeze()        # obiekty ze startu nie są już skanowane przez GC - krótsze, rzadsze pauzy w wątkach czasu rzeczywistego
    for i in range(C.NUM_PADS):
        S.start_thread(f"slot{i}", rx_ds4.slot_thread, i)
    S.start_thread("feedback", rx_feedback.feedback_thread, rx_net.send_feedback)
    S.start_thread("gyro mouse", rx_gyro.mouse_thread)
    if C.GAME_PROFILES:
        S.start_thread("game watch", rx_games.watch_thread)

    print(f"Virtual DS4 pads are created on demand (slots 0..{C.NUM_PADS - 1}).")
    print(f"Listening for events on UDP {C.IN_PORT}"
          + (f" (only from {C.ALLOWED_IP})" if C.ALLOWED_IP else " (any source - consider --allow IP)")
          + (", HMAC on" if C.KEY else ", NO authentication - consider --key")
          + (", DSU batching on" if C.DSU_BATCH else "") + "...")

    use_gui = C.USE_GUI
    if use_gui:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            print("tkinter not available - running without GUI")
            use_gui = False

    try:
        if use_gui:
            import rx_gui
            net_thread = S.start_thread("net", rx_net.net_loop)
            try:
                rx_gui.run_gui()
            except Exception as e:     # np. brak ekranu - działaj dalej bez okna
                print(f"GUI unavailable: {e!r} - running without GUI")
                while not S.stop.is_set() and net_thread.is_alive():
                    S.stop.wait(0.5)
            S.stop.set()
            net_thread.join(2)
        else:
            S.run_forever("net", rx_net.net_loop)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        S.stop.set()
        rx_web.stop()
        for i in range(C.NUM_PADS):
            S.want[i].clear()
            rx_net.clear_slot(i)
            S.wake[i].set()
        time.sleep(0.3)   # daj wątkom slotów odłączyć wirtualne DS4
        rx_winapi.end_timer()
        if C.log_handle is not None:
            try:
                C.log_handle.flush()
                C.log_handle.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
