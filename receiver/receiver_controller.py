"""DualSense UDP receiver (Windows): wirtualny DS4 (ViGEm) + serwer DSU (Cemuhook) + jedno okno (PySide6)
z baterią, łączem i remapowaniem. Protokół: proto.py.

Pliki: proto.py (wspólny), rx_config.py, rx_state.py, rx_buttons.py (remap + remap.json),
       rx_shape.py (strefy martwe, krzywe, gyro), rx_hotkeys.py (skróty PS), rx_games.py (profil wg gry),
       rx_gyro.py (gyro jako mysz), rx_feedback.py (wibracja, lightbar, potwierdzenia),
       rx_winapi.py, rx_mouse.py, rx_report.py, rx_net.py, rx_dsu.py, rx_ds4.py,
       rx_console.py + rx_audio.py (tryb konsoli: monitor / TV, console.json),
       rx_settings.py (settings.json: mysz, pad master), rx_master.py (pad master: uprawnienia, PS + przycisk),
       okno: rx_qt.py (okno, zasobnik), rx_qt_pad.py (rysunek pada), rx_qt_panels.py, rx_qt_widgets.py, rx_qt_style.py,
       rx_qt_console.py (okno ustawień: konsola, mysz, master, okno), rx_qt_icons.py (ikony przycisków),
       rx_i18n.py (język okna: polski / angielski), rx_events.py + rx_qt_log.py (dziennik zdarzeń w oknie),
       rx_lightbar.py (kolor lightbara: stały / wg baterii, miganie przy niskiej baterii),
       rx_editor.py (model edycji profilu), rx_profiles.py, rx_status.py, rx_icon.py.

Wirtualny DS4 powstaje dopiero po pierwszym pakiecie z danego slotu i znika, gdy nadajnik
zgłosi rozłączenie pada (pakiet 8) albo milczy dłużej niż unplug_s.

Po starcie widać tylko ikonę w zasobniku. Klik w ikonę pokazuje jedno okno: pady z baterią i łączem po lewej,
rysunek pada na środku (klik w przycisk, spust albo gałkę = remapowanie), profile / gyro / lista zmian po prawej
(zmiany działają od razu, Save = zapis do remap.json). Krzyżyk i minimalizacja chowają okno do zasobnika.
Profil jest przypisany do pada (MAC), nie do slotu, więc zamiana kolejności padów go nie gubi.
Skróty (PS jako modyfikator, ps_modifier): PS + klik touchpada = mysz / dotyk DS4, PS + Share = zrzut ekranu,
PS + d-pad góra/dół = czułość myszy, PS + d-pad lewo/prawo = zmiana profilu, długie PS = Game Bar / Big Picture.
Profile gier: profil "pad@gra.exe" włącza się sam, gdy ta gra jest na pierwszym planie (zakładka w edytorze).
Gyro: celowanie żyroskopem jako prawa gałka albo mysz, ustawiane w profilu (edytor -> Gyro aim).
W trybie myszy: 1 palec = kursor, tap = LPM, 2 palce = przewijanie, tap 2 palcami = PPM (scroll_sens, scroll_natural).
Tryb konsoli: podwójne kliknięcie wybranego przycisku (domyślnie R2) przełącza ekran i dźwięk monitor <-> TV, po ciszy
z pada samo wraca na monitor; start ustawia startup_mode (extend / internal). Ustawienia: tray -> Ustawienia (console.json).
Pad master (rx_master): wybrane funkcje tylko dla jednego pada; PS + przycisk = pauza / zamknij grę / uśpij /
wyłącz PC; PS + R3 przytrzymane 3 s = ten pad zostaje masterem; połączenie mastera włącza tryb konsoli.
Ustawienia: okno -> Ustawienia -> Master (settings.json).
Konfiguracja: --config plik.toml|json (klucze jak w DEFAULTS), flagi CLI nadpisują plik.
Autostart z Windows: --autostart on|off (zadanie Harmonogramu przy logowaniu, bez opóźnienia; pythonw bez konsoli).
Administrator: na Windows receiver sam uruchamia się od nowa jako administrator (okno UAC; HIDMaestro tworzy pady
tylko tak), zadanie autostartu dostaje najwyższe uprawnienia (bez UAC przy logowaniu); --no-elevate wyłącza.
"""
import gc
import os
import sys
import time

import rx_config
from rx_config import C


def _startup_events(rx_winapi):
    """Dziennik: ile po starcie Windows wystartował receiver (opóźniony autostart = późny tryb konsoli) i w tle
    przeniesienie starego autostartu z wpisu Run do Harmonogramu zadań (start zaraz po zalogowaniu)."""
    import threading

    import rx_events as ev
    from rx_i18n import _
    if (up := rx_winapi.uptime_s()) is not None:
        print(f"Receiver started {up:.0f}s after Windows start")
        ev.info(_("Receiver uruchomiony {s} s po starcie Windows").format(s=round(up)))
    if os.name != "nt":
        return

    def migrate():
        import rx_autostart
        if rx_autostart.migrate():
            ev.info(_("Autostart przeniesiony do Harmonogramu zadań - receiver startuje zaraz po zalogowaniu"))
        # receiver działa jako administrator (HIDMaestro): zadanie autostartu dostaje najwyższe uprawnienia - od
        # następnego logowania startuje tak sam, bez okna UAC
        if not rx_winapi.is_admin():
            return
        cmd = rx_config.autostart_command()
        if rx_autostart.elevate(*cmd):
            ev.info(_("Autostart ustawiony na uprawnienia administratora (HIDMaestro)"))
        rx_autostart.ensure_admin_task(*cmd)       # następne ręczne uruchomienie jako administrator bez okna UAC
    threading.Thread(target=migrate, name="autostart", daemon=True).start()


def _gamebar():
    """PS wirtualnego DS4 / Win+G bez zainstalowanego Game Bar -> okno "wybierz aplikację ms-gamebar" (rx_gamebar)."""
    import rx_gamebar
    try:
        for change in rx_gamebar.suppress():
            print(f"Game Bar: {change}")
    except Exception as e:
        print(f"Game Bar: cannot update registry ({e!r})")


NO_ELEVATE = "--no-elevate"


ELEVATE_GUARD_S = 60       # zadanie administratora uruchomione przed chwilą, a proces znów bez uprawnień -> UAC (bez pętli)


def _guard_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".elevate_try")


def _recent_try():
    """Ten start to skutek naszego schtasks /run sprzed chwili (zadanie nie dało uprawnień, np. konto bez admina)."""
    try:
        return time.time() - os.path.getmtime(_guard_path()) < ELEVATE_GUARD_S
    except OSError:
        return False


def elevate():
    """Windows bez uprawnień administratora: receiver od nowa jako administrator i wyjście. Najpierw przez zadanie
    DualSenseBridgeAdmin (bez okna UAC; tworzy je receiver działający jako administrator), bez niego - okno UAC.
    Odmowa w UAC / --no-elevate -> dalej bez uprawnień (wirtualne pady przez ViGEmBus). Przed rx_config.init:
    --autostart on idzie zawsze przez UAC (zadanie uruchomiłoby receiver bez tej flagi)."""
    if os.name != "nt" or NO_ELEVATE in sys.argv[1:]:
        return
    import rx_winapi
    if rx_winapi.is_admin():
        return
    if not any(a.startswith("--autostart") for a in sys.argv[1:]) and not _recent_try():
        import rx_autostart
        try:
            with open(_guard_path(), "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
        if rx_autostart.run_admin_task():
            print("Started as administrator through the DualSenseBridgeAdmin task (no UAC)")
            sys.exit(0)
    cmd = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    if rx_winapi.relaunch_admin(cmd):
        sys.exit(0)
    print("Administrator rights declined (UAC) - running without them: virtual pads via ViGEmBus")


def main():
    elevate()
    rx_config.init()
    sys.setswitchinterval(0.001)   # domyślne 5 ms = tyle może czekać wątek sieciowy, gdy inny trzyma GIL

    import rx_winapi
    rx_winapi.init_process()
    rx_winapi.ensure_single_instance()

    import rx_buttons
    import rx_console
    import rx_ds4
    import rx_dsu
    import rx_feedback
    import rx_games
    import rx_gyro
    import rx_hotkeys
    import rx_master
    import rx_mouse
    import rx_net
    import rx_settings
    import rx_state as S
    import rx_testmode

    S.init(rx_buttons.RemapStore(C.REMAP_PATH))
    rx_settings.apply(rx_settings.load(C.SETTINGS_PATH, rx_settings.defaults(C.MOUSE_SENS, C.LOW_BATT)))
    import rx_i18n
    rx_i18n.set_lang(rx_settings.cur.ui.lang)     # język okna i powiadomień (Ustawienia -> Okno)
    rx_mouse.init(C.NUM_PADS)
    rx_hotkeys.init(C.NUM_PADS)
    rx_master.init(C.NUM_PADS)
    rx_testmode.init(C.NUM_PADS)
    rx_ds4.init()
    rx_net.init()
    rx_dsu.start()
    rx_console.init()   # ekran i dźwięk wg startup_mode (extend: oba ekrany aktywne, nigdy bez obrazu)
    _gamebar()
    _startup_events(rx_winapi)

    gc.collect()
    gc.freeze()        # obiekty ze startu nie są już skanowane przez GC - krótsze, rzadsze pauzy w wątkach czasu rzeczywistego
    slot_threads = [S.start_thread(f"slot{i}", rx_ds4.slot_thread, i) for i in range(C.NUM_PADS)]
    fb_thread = S.start_thread("feedback", rx_feedback.feedback_thread, rx_net.send_feedback, rx_net.send_triggers)
    if os.name == "nt":
        import rx_hm_update
        rx_hm_update.on_installed = rx_ds4.hm_installed
        S.start_thread("hidmaestro update", rx_hm_update.thread, S.stop, rx_ds4.hm_auto_enabled)
    S.start_thread("gyro mouse", rx_gyro.mouse_thread)
    if C.GAME_PROFILES:
        S.start_thread("game watch", rx_games.watch_thread)

    print(f"Virtual pads are created on demand (slots 0..{C.NUM_PADS - 1}).")
    print(f"Listening for events on UDP {C.IN_PORT}"
          + (f" (only from {C.ALLOWED_IP})" if C.ALLOWED_IP else " (any source - consider --allow IP)")
          + (", HMAC on" if C.KEY else ", NO authentication - consider --key")
          + (", DSU batching on" if C.DSU_BATCH else "") + "...")

    use_gui = C.USE_GUI
    if use_gui:
        try:
            import PySide6.QtWidgets  # noqa: F401
        except ImportError:
            print("PySide6 not installed (pip install -r requirements.txt) - running without GUI")
            use_gui = False

    try:
        if use_gui:
            import rx_qt
            net_thread = S.start_thread("net", rx_net.net_loop)
            try:
                rx_qt.run_gui()
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
        rx_feedback.fb_event.set()          # wątek feedbacku kończy się zaraz - potem efekty spustów nie wrócą
        fb_thread.join(1.0)
        rx_feedback.release_triggers(rx_net.send_triggers)   # pad nie może zostać z twardym spustem po wyjściu
        for i in range(C.NUM_PADS):
            S.want[i].clear()
            rx_net.clear_slot(i)
            S.wake[i].set()
        deadline = time.monotonic() + 2.0
        for t in slot_threads:      # wątki slotów odłączają wirtualne DS4 od ViGEm (koniec wątku = odłączone)
            t.join(max(0.0, deadline - time.monotonic()))
        console_on = rx_console.is_on()
        rx_console.close(restart=S.restart)
        rx_net.close()
        rx_dsu.close()
        rx_winapi.release_single_instance()
        rx_winapi.end_timer()
        if S.restart:               # porty, ViGEm i blokada instancji są już zwolnione - nowy proces startuje jak z autostartu
            try:
                rx_winapi.relaunch({rx_console.RESTART_ENV: "1" if console_on else "0"}, admin=S.restart == "admin")
            except OSError as e:
                print(f"Restart failed: {e!r} - start the receiver manually")
        if C.log_handle is not None:
            try:
                C.log_handle.flush()
                C.log_handle.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
