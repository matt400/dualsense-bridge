"""Receiver: an exception in the update thread, mouse sliders keep the values of settings.json, the sender's update failure reason reaches the window.
    python3 tests/test_window_edges.py receiver"""
import os, sys, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_settings
from rx_settings import MouseConfig

from chk import check, fails, wait_for                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- window: an exception in the update thread ----------
try:
    import rx_qt
except Exception as e:                              # no PySide6 - skip only this part
    print(f"SKIP update from the window ({e!r})")
else:
    import rx_update
    out = []
    win = types.SimpleNamespace(update_done=types.SimpleNamespace(emit=out.append), update_progress=types.SimpleNamespace(emit=lambda _p: None))
    real_update = rx_update.update
    rx_update.update = lambda **_: (_ for _ in ()).throw(OSError("git zawieszony"))
    try:
        rx_qt.MainWindow._update_thread(win)
    finally:
        rx_update.update = real_update
    check(len(out) == 1 and not out[0].ok and not out[0].changed and "git" in out[0].text,
          "an update error returns to the window (the button does not stay on 'Aktualizuję…' forever)")

# ---------- settings window: the mouse sliders do not clamp values from settings.json ----------
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import rx_qt_console
except Exception as e:
    print(f"SKIP suwaki myszy ({e!r})")
else:
    d = rx_qt_console.ConsoleSettings()
    hi = {k: v[1] for k, v in rx_settings.MOUSE_LIMITS.items()}
    m = MouseConfig(**hi)
    d._load_mouse(m)
    got = d.collect_settings().mouse
    check(got == m, f"maximum mouse values pass through the window unchanged: {got}")

# ---------- the sender update error reason (updmsg packet) reaches the window ----------
from proto import T_PING, T_UPDMSG
pkts = [(N.codec.pack(T_UPDMSG, 255, "Permission denied (publickey) żółw".encode()), ("10.0.0.9", 5005)),
        (N.codec.pack(T_PING, 255, 1), ("10.0.0.9", 5005))]
class FakeSock:
    def recvfrom(self, n):
        if not pkts:
            S.stop.set()
            raise TimeoutError
        return pkts.pop(0)
    def sendto(self, *a):
        pass
real_sock, real_send = N.in_sock, N.send_peer
N.in_sock = FakeSock()
S.sender_update_msg = ""
N.net_loop()
S.stop.clear()
N.in_sock = real_sock
check(S.sender_update_msg == "Permission denied (publickey) żółw" and S.peer == ("10.0.0.9", 5005),
      f"packet with the sender update error reason: {S.sender_update_msg!r}")

sys.exit(1 if fails else 0)
