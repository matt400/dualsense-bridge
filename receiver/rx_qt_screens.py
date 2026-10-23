"""Receiver window: where it sits and how big it is - the chosen screen, full screen (the TV mode), centring after the screens
change (the console mode switches the monitor <-> TV and changes the resolution), and coming to the front.

`ScreenPlacement` is the base class of the main window (rx_qt.MainWindow): the window supplies the settings object, the
full screen button and `apply_scale`; everything about the screen lives here.
"""
import os

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QToolButton

import rx_games
import rx_winapi as W
from rx_i18n import _
from rx_qt_style import TV_SCALE

FIT_MARGIN = 24        # a window fitted to the screen: this many px of slack from the work area's edge
SCREEN_SETTLE_MS = 800 # screen changes (console mode, TV resolution) arrive in bursts - the window is placed after quiet


def screens():
    """[(key, QScreen)] - the key is the screen name from Windows (e.g. DISPLAY2), or its number when it has none."""
    return [(sc.name() or f"#{n}", sc) for n, sc in enumerate(QApplication.screens(), 1)]


class ScreenPlacement(QMainWindow):
    """Screen, full screen and placement of the main window (see the module docstring)."""

    settings: QSettings        # the window's persistent settings (screen, full screen, size)
    fs_btn: QToolButton        # the full screen button in the header
    _fitting: bool             # place_on_screen is resizing the window - this is not the user's choice
    _pref: QSize               # the size the user chose, restored on a bigger screen
    _screen_timer: QTimer      # fires once the screens stopped changing
    _place_pending: bool       # the screens changed during a game - the window is placed after it

    def apply_scale(self, k) -> None:
        """The interface scale; implemented by the main window, which owns the widgets that have to be resized."""
        raise NotImplementedError

    def screen_choice(self):
        """The screen name chosen in Settings (QScreen.name()), or "" = where the window was last."""
        return str(self.settings.value("screen", "") or "")

    def set_screen_choice(self, name) -> None:
        """Remember the chosen screen and move the window there."""
        self.settings.setValue("screen", name)
        self.settings.sync()
        self.place_on_screen()

    def fullscreen(self):
        """Whether full screen (TV mode) is the saved choice."""
        return str(self.settings.value("fullscreen", "false")).lower() == "true"

    def set_fullscreen(self, on) -> None:
        """Full screen / window (remembered). Full screen = the TV mode: a bigger interface and pad control; it takes
        the chosen screen (Settings -> Window) or the current one.
        """
        self.settings.setValue("fullscreen", bool(on))
        self.settings.sync()
        self.fs_btn.setToolTip(_("Wyjdź z pełnego ekranu (F11)") if on else _("Pełny ekran (F11)"))
        self.apply_scale(TV_SCALE if on else 1.0)
        if self.isVisible():
            self.bring_to_front()

    def toggle_fullscreen(self) -> None:
        """F11 / the header button: switch between the window and full screen."""
        self.set_fullscreen(not self.fullscreen())

    def target_screen(self):
        """The screen chosen in Settings -> Window; when it is absent (the monitor off in the console mode) or nothing was
        chosen - the one the window is on now (Windows moves windows from a disconnected screen to the remaining one).
        """
        name = self.screen_choice()
        return (dict(screens()).get(name) if name else None) or self.screen() or QApplication.primaryScreen()

    def place_on_screen(self):
        """The window always in the centre of the screen (target_screen) and entirely on it: one bigger than the work area (a TV
        1280x720 at 200 % scaling) is shrunk, and on a bigger screen it returns to the size chosen by the user.
        It does not show the window (one hidden in the tray stays hidden). -> the screen.
        """
        target = self.target_screen()
        area = target.availableGeometry()
        self._fitting = True
        try:
            state = self.windowState()
            if state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen):
                self.setWindowState(state & ~(Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen))
            frame = self.frameGeometry().size() - self.geometry().size()      # the frame and title bar (0 before showing)
            room = area.size() - frame - QSize(FIT_MARGIN, FIT_MARGIN)
            size = self._pref.boundedTo(room).expandedTo(self.minimumSize())
            if size != self.size():
                self.resize(size)
            g = self.frameGeometry()
            g.moveCenter(area.center())
            # still bigger than the screen despite shrinking (the layout's minimum size) - the top left corner with the buttons stays
            # visible
            self.move(max(g.left(), area.left()), max(g.top(), area.top()))
        finally:
            self._fitting = False
        return target

    def resizeEvent(self, e) -> None:
        """A size changed by the user (not a fit to the screen) = the new window size for big screens."""
        super().resizeEvent(e)
        if self._fitting or not self.isVisible() or self.windowState() != Qt.WindowState.WindowNoState:
            return
        if getattr(self, "_screen_timer", None) is not None and self._screen_timer.isActive():
            return                             # Windows shrinks the window when screens change - this is not the user's choice
        room = self.target_screen().availableGeometry().size() - QSize(FIT_MARGIN, FIT_MARGIN)
        if e.size().width() < room.width() and e.size().height() < room.height():
            self._pref = e.size()

    def _watch_screens(self) -> None:
        """The console mode switches the picture monitor <-> TV and changes the TV resolution and scaling - after every such
        change the window returns to the centre and fits on the screen (without being pulled to the front).
        """
        self._screen_timer = QTimer(self)
        self._screen_timer.setSingleShot(True)
        self._screen_timer.setInterval(SCREEN_SETTLE_MS)
        self._screen_timer.timeout.connect(self._screens_settled)
        app = QApplication.instance()
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self._screens_changed)
        app.primaryScreenChanged.connect(self._screens_changed)
        for sc in app.screens():
            self._screen_added(sc, settle=False)

    def _screen_added(self, sc, settle: bool = True) -> None:
        """Watch a screen's geometry and DPI; a new screen counts as a change."""
        sc.geometryChanged.connect(self._screens_changed)
        sc.availableGeometryChanged.connect(self._screens_changed)
        sc.logicalDotsPerInchChanged.connect(self._screens_changed)
        if settle:
            self._screens_changed()

    def _screens_changed(self, *_args) -> None:
        """A screen changed: (re)start the settle timer, since changes arrive in bursts."""
        self._screen_timer.start()

    def _screens_settled(self) -> None:
        # a game changed the resolution - we rearrange after leaving it (_slow); before, nothing retried this and the window
        # stayed shifted / too big when the game restored the resolution while still in the foreground
        """The screens stopped changing: re-centre the window (after a game when one is in front) and restore its window state."""
        self._place_pending = rx_games.in_game()
        if self._place_pending:
            return
        target = self.place_on_screen()
        print(f"Screens changed - window centred on {target.name() or 'screen'} "
              f"({target.availableGeometry().width()}x{target.availableGeometry().height()})")
        if self.isVisible():
            if self.fullscreen():
                self.showFullScreen()
            else:
                self.showNormal()

    def bring_to_front(self) -> None:
        """Show the window on its screen (centred, in the right mode) and take the foreground, which a background process needs extra help
        for.
        """
        if self.isFullScreen() and not self.fullscreen():
            self.showNormal()
        self.place_on_screen()
        if self.fullscreen():
            self.showFullScreen()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if os.name == "nt":
            W.force_foreground(int(self.winId()))   # a background process: activateWindow alone only flashes the taskbar
