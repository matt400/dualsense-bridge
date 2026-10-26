"""Receiver window.

Window mode is either "maximized" (the window fills the work area of the chosen screen) or "size" (a fixed resolution
picked in Settings, centred); full screen is the TV mode. "Maximized" is done by hand (geometry = work area) and not with
the native maximize: Windows + Qt on two screens with different scaling sometimes keep the old client size after a native
maximize, which left the content in a part of the window and the rest white.

Where it sits and how big it is - the chosen screen, full screen (the TV mode), centring after the screens change
(the console mode switches the monitor <-> TV and changes the resolution), and coming to the front.

`ScreenPlacement` is the base class of the main window (rx_qt.MainWindow): the window supplies the settings object, the
full screen button and `apply_scale`; everything about the screen lives here.
"""
import os
from typing import Any

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QResizeEvent, QScreen
from PySide6.QtWidgets import QApplication, QMainWindow, QToolButton

import rx_games
import rx_winapi as W
from rx_i18n import _
from rx_qt_style import TV_SCALE

FIT_MARGIN = 24        # a window fitted to the screen: this many px of slack from the work area's edge
REAPPLY_MS = 250       # after showing, the window is placed once more: the native frame / DPI of the screen are known only then
MODE_MAXIMIZED = "maximized"
MODE_SIZE = "size"
MIN_WINDOW = QSize(800, 560)          # a chosen resolution is never smaller than this (the layout would not fit)
SCREEN_SETTLE_MS = 800 # screen changes (console mode, TV resolution) arrive in bursts - the window is placed after quiet


def parse_size(text: object) -> QSize | None:
    """"1280x720" -> QSize; None when it is not a usable size (garbage in the ini file is ignored)."""
    try:
        w, h = (int(v) for v in str(text).lower().replace("×", "x").split("x"))
    except ValueError:
        return None
    return QSize(w, h) if w >= MIN_WINDOW.width() and h >= MIN_WINDOW.height() else None


def screens() -> list[tuple[str, QScreen]]:
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

    def apply_scale(self, k: float) -> None:
        """The interface scale; implemented by the main window, which owns the widgets that have to be resized."""
        raise NotImplementedError

    def screen_choice(self) -> str:
        """The screen name chosen in Settings (QScreen.name()), or "" = where the window was last."""
        return str(self.settings.value("screen", "") or "")

    def set_screen_choice(self, name: str) -> None:
        """Remember the chosen screen and move the window there."""
        self.settings.setValue("screen", name)
        self.settings.sync()
        self.place_on_screen()

    def window_mode(self) -> str:
        """How the window looks outside full screen: MODE_MAXIMIZED (default) or MODE_SIZE (a fixed resolution)."""
        return MODE_SIZE if str(self.settings.value("window_mode", MODE_MAXIMIZED)) == MODE_SIZE else MODE_MAXIMIZED

    def window_size(self) -> QSize:
        """The resolution used in MODE_SIZE (what the user chose / last dragged to)."""
        return self._pref

    def set_window_mode(self, mode: str, size: QSize | None = None) -> None:
        """Remember the window mode (and the resolution for MODE_SIZE) and apply it now."""
        self.settings.setValue("window_mode", MODE_SIZE if mode == MODE_SIZE else MODE_MAXIMIZED)
        if size is not None and size.isValid():
            self.settings.setValue("window_size", f"{size.width()}x{size.height()}")
            self._pref = size
        self.settings.sync()
        if self.isVisible() and not self.fullscreen():
            self.place_on_screen()

    def fullscreen(self) -> bool:
        """Whether full screen (TV mode) is the saved choice."""
        return str(self.settings.value("fullscreen", "false")).lower() == "true"

    def set_fullscreen(self, on: bool) -> None:
        """Full screen / window (remembered).

        Full screen = the TV mode: a bigger interface and pad control; it takes the chosen screen (Settings -> Window) or
        the current one.
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

    def target_screen(self) -> QScreen:
        """The screen chosen in Settings -> Window.

        When it is absent (the monitor off in the console mode) or nothing was chosen - the one the window is on now
        (Windows moves windows from a disconnected screen to the remaining one).
        """
        name = self.screen_choice()
        return (dict(screens()).get(name) if name else None) or self.screen() or QApplication.primaryScreen()

    def place_on_screen(self) -> QScreen:
        """The window on the screen (target_screen): filling its work area, or a chosen size in the centre.

        MODE_MAXIMIZED fills the work area exactly (the native maximize is not used, see the module docstring); MODE_SIZE
        keeps the chosen resolution (shrunk when the screen is smaller) in the centre. Full screen is left to the caller
        (showFullScreen). It does not show the window (one hidden in the tray stays hidden). -> the screen.
        """
        target = self.target_screen()
        area = target.availableGeometry()
        self._fitting = True
        try:
            state = self.windowState()
            if state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen) and not self.fullscreen():
                self.setWindowState(state & ~(Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen))
            frame = self.frameGeometry().size() - self.geometry().size()      # the frame and title bar (0 before showing)
            if self.window_mode() == MODE_MAXIMIZED:
                size = (area.size() - frame).expandedTo(self.minimumSize())
                self.resize(size)
                self.move(area.left(), area.top())
            else:
                room = area.size() - frame - QSize(FIT_MARGIN, FIT_MARGIN)
                size = self.window_size().boundedTo(room).expandedTo(self.minimumSize())
                if size != self.size():
                    self.resize(size)
                g = self.frameGeometry()
                g.moveCenter(area.center())
                # still bigger than the screen despite shrinking (the layout's minimum size) - the top left corner stays visible
                self.move(max(g.left(), area.left()), max(g.top(), area.top()))
        finally:
            self._fitting = False
        return target

    def _show_windowed(self) -> None:
        """Show the window in window mode on its screen.

        Placed before and after showing (the real frame and the screen's DPI are known only once the window is shown) and
        once more shortly after, which fixes the content drawn in a part of the window on mixed-DPI screens.
        """
        self.place_on_screen()
        if self.isFullScreen() or self.isMaximized():
            self.showNormal()
        else:
            self.show()
        self.place_on_screen()
        QTimer.singleShot(REAPPLY_MS, self._reapply_windowed)

    def _reapply_windowed(self) -> None:
        """The delayed second placement of _show_windowed (skipped when the mode changed in the meantime)."""
        if self.isVisible() and not self.fullscreen() and not rx_games.in_game():
            self.place_on_screen()

    def resizeEvent(self, e: QResizeEvent) -> None:
        """A size changed by the user (not a fit to the screen) = the new window size for big screens."""
        super().resizeEvent(e)
        if self._fitting or not self.isVisible() or self.windowState() != Qt.WindowState.WindowNoState:
            return
        if self.fullscreen() or self.window_mode() != MODE_SIZE:
            return                             # a filled window / full screen is not a size the user chose
        if getattr(self, "_screen_timer", None) is not None and self._screen_timer.isActive():
            return                             # Windows shrinks the window when screens change - this is not the user's choice
        room = self.target_screen().availableGeometry().size() - QSize(FIT_MARGIN, FIT_MARGIN)
        if e.size().width() < room.width() and e.size().height() < room.height():
            self._pref = e.size()
            self.settings.setValue("window_size", f"{e.size().width()}x{e.size().height()}")

    def _watch_screens(self) -> None:
        """The console mode switches the picture monitor <-> TV and changes the TV resolution and scaling.

        After every such change the window returns to the centre and fits on the screen (without being pulled to the front).
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

    def _screen_added(self, sc: QScreen, settle: bool = True) -> None:
        """Watch a screen's geometry and DPI; a new screen counts as a change."""
        sc.geometryChanged.connect(self._screens_changed)
        sc.availableGeometryChanged.connect(self._screens_changed)
        sc.logicalDotsPerInchChanged.connect(self._screens_changed)
        if settle:
            self._screens_changed()

    def _screens_changed(self, *_args: Any) -> None:
        """A screen changed: (re)start the settle timer, since changes arrive in bursts."""
        self._screen_timer.start()

    def _screens_settled(self) -> None:
        """The screens stopped changing: re-centre the window (after a game when one is in front) and restore its window state.

        A game changed the resolution - we rearrange after leaving it (_slow); before, nothing retried this and the window
        stayed shifted / too big when the game restored the resolution while still in the foreground.
        """
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
                self._show_windowed()

    def bring_to_front(self) -> None:
        """Show the window on its screen (centred, in the right mode) and take the foreground.

        Which a background process needs extra help for.
        """
        if self.fullscreen():
            self.place_on_screen()
            self.showFullScreen()
        else:
            self._show_windowed()
        self.raise_()
        self.activateWindow()
        if os.name == "nt":
            W.force_foreground(int(self.winId()))   # a background process: activateWindow alone only flashes the taskbar
