"""DualSense UDP receiver (Windows).

A virtual DS4 (ViGEm) + a DSU (Cemuhook) server + one window (PySide6) with battery, link and remapping. Protocol:
proto.py.

Files: proto.py (shared), rx_config.py, rx_state.py, rx_buttons.py (remap + remap.json),
       rx_shape.py (deadzones, curves, gyro), rx_hotkeys.py (PS shortcuts), rx_games.py (profile per game),
       rx_gyro.py (gyro as a mouse), rx_gstick.py + rx_gstick_io.py (pad tilt as a stick),
       rx_feedback.py (rumble, lightbar, confirmations),
       rx_winapi.py, rx_mouse.py, rx_report.py, rx_net.py, rx_dsu.py, rx_ds4.py, rx_haptics.py (game HD haptics),
       rx_speaker.py (pad speaker),
       rx_console.py + rx_audio.py (console mode: monitor / TV, console.json),
       rx_settings.py (settings.json: mouse, master pad), rx_master.py (master pad: permissions, PS + button),
       window: rx_qt.py (window, tray), rx_qt_sidebar.py (the side menu), rx_qt_diag.py + rx_diag.py (the Diagnostics tab and its data),
       rx_qt_screens.py (screen, full screen, placement), rx_qt_pad.py (pad drawing),
       rx_qt_panels.py, rx_qt_widgets.py, rx_qt_style.py,
       rx_qt_console.py (settings window: console, mouse, master, window), rx_qt_icons.py (button icons),
       rx_i18n.py (window language: Polish / English), rx_events.py + rx_qt_log.py (event log in the window),
       rx_lightbar.py (lightbar colour: fixed / by battery, blinking on low battery),
       rx_editor.py (profile editing model), rx_profiles.py, rx_status.py, rx_icon.py.

The virtual DS4 is created only after the first packet from a given slot and disappears when the sender
reports the pad disconnect (packet 8) or stays silent longer than unplug_s.

After startup only the tray icon is visible. A click on the icon shows the single window: pads with battery and link on the left,
the pad drawing in the middle (a click on a button, trigger or stick = remapping), profiles / gyro / the change list on the right
(changes take effect at once, Save = write to remap.json). The cross and minimise hide the window to the tray.
A profile belongs to a pad (MAC), not to a slot, so swapping the pad order does not lose it.
Shortcuts (PS as a modifier, ps_modifier): PS + touchpad click = mouse / DS4 touch, PS + Share = screenshot,
PS + d-pad up/down = mouse sensitivity, PS + d-pad left/right = profile change, long PS = Game Bar / Big Picture.
Game profiles: a "pad@game.exe" profile turns on by itself when that game is in the foreground (a tab in the editor).
Gyro: aiming with the gyroscope as the right stick or a mouse, set in the profile (editor -> Gyro aim).
In mouse mode: 1 finger = cursor, tap = left click, 2 fingers = scrolling, 2-finger tap = right click (scroll_sens, scroll_natural).
Console mode: a double click of the chosen button (R2 by default) switches screen and sound monitor <-> TV, after silence
from the pad it returns to the monitor by itself; startup sets startup_mode (extend / internal). Settings: tray -> Settings (console.json).
Master pad (rx_master): selected features for one pad only; PS + button = pause / close the game / sleep /
shut down the PC; PS + R3 held for 3 s = this pad becomes the master; the master connecting turns console mode on.
Settings: window -> Settings -> Master (settings.json).
Configuration: receiver.toml next to the receiver (no argument) or --config file.toml|json (keys as in DEFAULTS);
CLI flags override the file.
Autostart with Windows: --autostart on|off (a Task Scheduler task at logon, no delay; pythonw without a console).
Administrator: on Windows the receiver restarts itself as administrator (a UAC prompt; HIDMaestro creates pads
only that way), the autostart task gets the highest privileges (no UAC at logon); --no-elevate disables it.
"""
import gc
import os
import sys
import threading
import time
from types import ModuleType

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))   # proto.py is shared, it lives in common/

import rx_config
from rx_config import C


def _startup_events(rx_winapi: ModuleType) -> None:
    """Log how long after logon (or Windows start) the receiver started (a delayed autostart = a late console mode).

    In the background also moves the old autostart from the Run entry to Task Scheduler (start right after logon).
    """
    import threading

    import rx_events as ev
    from rx_i18n import _
    if (logon := rx_winapi.since_logon_s()) is not None:
        print(f"Receiver started {logon:.0f}s after logon")
        ev.info(_("Receiver uruchomiony {s} s po zalogowaniu do Windows").format(s=round(logon)))
    elif (up := rx_winapi.uptime_s()) is not None:
        print(f"Receiver started {up:.0f}s after Windows start")
        ev.info(_("Receiver uruchomiony {s} s po starcie Windows").format(s=round(up)))
    import rx_security
    rx_security.log_issues()
    if os.name != "nt":
        return

    def migrate() -> None:
        """Autostart housekeeping thread.

        Migrate the old Run entry, then raise the task to the highest privileges and refresh the admin task.
        """
        import rx_autostart
        if rx_autostart.migrate():
            ev.info(_("Autostart przeniesiony do Harmonogramu zadań - receiver startuje zaraz po zalogowaniu"))
        # the receiver runs as administrator (HIDMaestro): the autostart task gets the highest privileges - from the
        # next logon it starts like that on its own, without a UAC prompt
        if not rx_winapi.is_admin():
            return
        cmd = rx_config.autostart_command()
        if rx_autostart.elevate(*cmd):
            ev.info(_("Autostart ustawiony na uprawnienia administratora (HIDMaestro)"))
        rx_autostart.ensure_admin_task(*cmd)       # the next manual start as administrator without a UAC prompt
    threading.Thread(target=migrate, name="autostart", daemon=True).start()


def _gamebar() -> None:
    """The PS of the virtual DS4 / Win+G without Game Bar installed -> the "choose an app for ms-gamebar" window (rx_gamebar)."""
    import rx_gamebar
    try:
        for change in rx_gamebar.suppress():
            print(f"Game Bar: {change}")
    except Exception as e:      # noqa: BLE001 - registry trouble must not stop the startup
        print(f"Game Bar: cannot update registry ({e!r})")


NO_ELEVATE = "--no-elevate"


RESTART_GUARD_S = 20.0     # restart: cleanup longer than this -> the new process starts anyway, the old one exits at once
ELEVATE_GUARD_S = 60       # the admin task was started a moment ago and the process again has no privileges -> UAC (no loop)


def _guard_path() -> str:
    """The marker of an attempt through the task - in the user's temp folder.

    The receiver folder can be read-only (e.g. Program Files), and without a marker a task that gives no privileges
    started the receiver in a loop.
    """
    import tempfile
    return os.path.join(tempfile.gettempdir(), "dualsense-bridge-elevate_try")


def _recent_try() -> bool:
    """This start is the result of our schtasks /run from a moment ago (the task gave no privileges, e.g. a non-admin account)."""
    try:
        return 0 <= time.time() - os.path.getmtime(_guard_path()) < ELEVATE_GUARD_S
    except OSError:
        return False


def _clear_guard() -> None:
    """The process has privileges: the attempt worked - the next manual start (even right away) goes through the task again, without UAC."""
    try:
        os.remove(_guard_path())
    except OSError:
        pass


def elevate() -> None:
    """Windows without administrator rights: the receiver again as administrator, then exit.

    First through the DualSenseBridgeAdmin task (no UAC prompt; created by a receiver running as administrator), without
    it - a UAC prompt. A refusal in UAC / --no-elevate -> carry on without privileges (virtual pads through ViGEmBus).
    Before rx_config.init: --autostart on always goes through UAC (the task would run the receiver without that flag).
    The receiver already running -> no elevation (the second copy exits soon anyway - earlier it first showed a UAC
    prompt).
    """
    if os.name != "nt" or NO_ELEVATE in sys.argv[1:]:
        return
    import rx_winapi
    if rx_winapi.is_admin():
        _clear_guard()
        return
    autostart = any(a.startswith("--autostart") for a in sys.argv[1:])
    if not autostart and rx_winapi.instance_running():
        return
    if not autostart and not _recent_try():
        import rx_autostart
        from proto import write_file
        try:
            write_file(_guard_path(), str(os.getpid()))        # atomic: a half-written guard would still carry a fresh mtime
        except OSError:
            pass
        # the task starts the receiver with the arguments stored in it - other arguments of this start (--config, --pads...)
        # would be lost silently, so then a UAC prompt with those arguments
        if rx_autostart.run_admin_task(rx_config.autostart_command()[1]):
            print("Started as administrator through the DualSenseBridgeAdmin task (no UAC)")
            sys.exit(0)
    cmd = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    if rx_winapi.relaunch_admin(cmd):
        sys.exit(0)
    print("Administrator rights declined (UAC) - running without them: virtual pads via ViGEmBus")


_restart_once = threading.Lock()      # a new process starts once: a normal shutdown or the restart guard


def _restart_now(console_on: bool, force: bool = False) -> None:
    """Start the new receiver process once (the guard calls it with force when cleanup hangs and then kills this process)."""
    import rx_console
    import rx_state as S
    import rx_winapi
    if not _restart_once.acquire(blocking=False):
        return
    if force:
        print(f"Shutdown takes more than {RESTART_GUARD_S:g}s - restarting anyway")
        rx_winapi.release_single_instance()
    try:
        rx_winapi.relaunch({rx_console.RESTART_ENV: "1" if console_on else "0"}, admin=S.restart == "admin")
    except OSError as e:
        print(f"Restart failed: {e!r} - start the receiver manually")
    if force:
        os._exit(0)          # the old process must not stay (ports, virtual pads)


def _init_modules() -> None:
    """Load the settings and initialise every module that holds per-pad state; nothing runs in the background yet."""
    import rx_buttons
    import rx_console
    import rx_diag
    import rx_ds4
    import rx_dsu
    import rx_gstick_io
    import rx_haptics
    import rx_hotkeys
    import rx_i18n
    import rx_master
    import rx_mouse
    import rx_net
    import rx_settings
    import rx_speaker
    import rx_state as S
    import rx_testmode
    import rx_winapi

    S.init(rx_buttons.RemapStore(C.REMAP_PATH))
    rx_settings.apply(rx_settings.load(C.SETTINGS_PATH, rx_settings.defaults(C.MOUSE_SENS, C.LOW_BATT)))
    rx_i18n.set_lang(rx_settings.cur.ui.lang)     # window and notification language (Settings -> Window)
    rx_diag.init(C.NUM_PADS)
    rx_mouse.init(C.NUM_PADS)
    rx_hotkeys.init(C.NUM_PADS)
    rx_master.init(C.NUM_PADS)
    rx_testmode.init(C.NUM_PADS)
    rx_ds4.init()
    rx_haptics.init()
    rx_speaker.init()
    rx_gstick_io.runner.init()
    rx_net.init()
    rx_dsu.start()
    rx_console.init()   # screen and sound per startup_mode (extend: both screens active, never without a picture)
    _gamebar()
    _startup_events(rx_winapi)


def _start_workers() -> tuple[list[threading.Thread], threading.Thread]:
    """Start the background threads (one per pad slot, feedback, HIDMaestro updates, gyro mouse, game watch).

    Returns the slot threads and the feedback thread - the ones shutdown has to join.
    """
    import rx_ds4
    import rx_feedback
    import rx_games
    import rx_gstick_io
    import rx_gyro
    import rx_net
    import rx_state as S

    gc.collect()
    gc.freeze()        # objects from startup are no longer scanned by the GC - shorter, rarer pauses in the real-time threads
    slot_threads = [S.start_thread(f"slot{i}", rx_ds4.slot_thread, i) for i in range(C.NUM_PADS)]
    fb_thread = S.start_thread("feedback", rx_feedback.feedback_thread, rx_net.send_feedback, rx_net.send_triggers,
                              rx_net.send_leds)
    if os.name == "nt":
        import rx_hm_update
        rx_hm_update.on_installed = rx_ds4.hm_installed
        S.start_thread("hidmaestro update", rx_hm_update.thread, S.stop, rx_ds4.hm_auto_enabled)
    S.start_thread("gyro mouse", rx_gyro.mouse_thread)
    S.start_thread("gyro stick", rx_gstick_io.thread)     # pad tilt -> stick; the engine steps only here
    # always (also without game profiles): a change of the foreground program stops game rumble
    # that ended during the rumble; game profiles are switched only with game_profiles (rx_state.reload_profile)
    S.start_thread("game watch", rx_games.watch_thread)
    return slot_threads, fb_thread


def _announce_listening() -> None:
    """Print how the receiver listens: the port, the allowed source, authentication and DSU batching."""
    print(f"Virtual pads are created on demand (slots 0..{C.NUM_PADS - 1}).")
    print(f"Listening for events on UDP {C.IN_PORT}"
          + (f" (only from {C.ALLOWED_IP})" if C.ALLOWED_IP else " (any source - consider --allow IP)")
          + (", HMAC on" if C.KEY else ", NO authentication - consider --key")
          + (", DSU batching on" if C.DSU_BATCH else "") + "...")


def _gui_available() -> bool:
    """The window is wanted (use_gui) and PySide6 is installed; otherwise the receiver runs headless."""
    if not C.USE_GUI:
        return False
    try:
        import PySide6.QtWidgets  # noqa: F401
    except ImportError:
        print("PySide6 not installed (pip install -r requirements.txt) - running without GUI")
        return False
    return True


def _run(use_gui: bool) -> None:
    """Block until the receiver is told to stop: the network loop runs in a thread next to the window, or alone without one."""
    import rx_net
    import rx_state as S
    if not use_gui:
        S.run_forever("net", rx_net.net_loop)
        return
    import rx_qt
    net_thread = S.start_thread("net", rx_net.net_loop)
    try:
        rx_qt.run_gui()
    except Exception as e:     # noqa: BLE001 - e.g. no display - carry on without the window
        print(f"GUI unavailable: {e!r} - running without GUI")
        while not S.stop.is_set() and net_thread.is_alive():
            S.stop.wait(0.5)
    S.stop.set()
    net_thread.join(2)


def _shutdown(slot_threads: list[threading.Thread], fb_thread: threading.Thread) -> None:
    """Stop everything in the order that leaves no stuck state behind.

    Release the pad triggers, disconnect the virtual pads, restore the screens, close the sockets - then start the new
    process if a restart was requested.
    """
    import rx_console
    import rx_dsu
    import rx_feedback
    import rx_haptics
    import rx_net
    import rx_state as S
    import rx_winapi

    S.stop.set()
    console_on = rx_console.is_on()
    guard: threading.Timer | None = None
    if S.restart:            # a hung cleanup (pad driver, screen switching) must not block the restart
        guard = threading.Timer(RESTART_GUARD_S, _restart_now, (console_on, True))
        guard.daemon = True
        guard.start()
    rx_feedback.fb_event.set()          # the feedback thread ends soon - afterwards the trigger effects would not come back
    fb_thread.join(1.0)
    rx_feedback.release_triggers(rx_net.send_triggers)   # the pad must not stay with a hard trigger after exit
    rx_haptics.stop_all()               # ends a running haptics stream (HAPT_END) before the socket closes
    for i in range(C.NUM_PADS):
        S.want[i].clear()
        rx_net.clear_slot(i)
        S.wake[i].set()
    deadline = time.monotonic() + 2.0
    for t in slot_threads:      # slot threads disconnect the virtual DS4 from ViGEm (thread end = disconnected)
        t.join(max(0.0, deadline - time.monotonic()))
    rx_console.close(restart=S.restart)
    rx_net.close()
    rx_dsu.close()
    rx_winapi.release_single_instance()
    rx_winapi.end_timer()
    if guard is not None:       # the cleanup finished in time: the guard has nothing left to do
        guard.cancel()
    if S.restart:               # ports, ViGEm and the instance lock are already released - the new process starts as from autostart
        _restart_now(console_on)
    if C.log_handle is not None:
        try:
            C.log_handle.flush()
            C.log_handle.close()
        except (OSError, ValueError):     # the log file is already gone or closed - nothing left to save
            pass


def main() -> None:
    """Start the receiver.

    Elevate, load the configuration, initialise the modules, start the worker threads and run the GUI (or the network
    loop alone), then shut everything down in order - optionally restarting the process.
    """
    elevate()
    rx_config.init()
    sys.setswitchinterval(0.001)   # the default 5 ms is how long the network thread may wait while another one holds the GIL

    import rx_winapi
    rx_winapi.init_process()
    rx_winapi.ensure_single_instance()

    _init_modules()
    slot_threads, fb_thread = _start_workers()
    _announce_listening()
    try:
        _run(_gui_available())
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        _shutdown(slot_threads, fb_thread)


if __name__ == "__main__":
    main()
