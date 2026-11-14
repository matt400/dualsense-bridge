"""Sender: regressions from the sixth review - trigger effect watchdog, a transient hidraw error (no descriptor leak),
UTF-8 texts cut on a character boundary, an HMAC key on one side only."""
import asyncio, errno, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_events as events
import sender_report as RP
import sender_rumble as R
from proto import BODY, T_EVENT, T_PING, T_STATE, T_STATE2, T_TRIG, T_TRIG2, TFX_CLICK, TFX_RES, TRIG2_OFF, Codec, cstr

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


class Dev:
    def capabilities(self):
        return {}


def last_trig(path):
    """The last trigger effects from BT reports (78 B) with trigger flags, or None."""
    data = open(path, "rb").read()
    for k in range(len(data) - 78, -1, -78):
        c = data[k + 3:k + 78]
        if c[0] & (RP.FLAG0_LEFT_TRIGGER | RP.FLAG0_RIGHT_TRIGGER):
            return bytes(c[RP.LEFT_TRIGGER_AT:RP.LEFT_TRIGGER_AT + 11]), bytes(c[RP.RIGHT_TRIGGER_AT:RP.RIGHT_TRIGGER_AT + 11])
    return None


async def main():
    # ---------- trigger effect watchdog ----------
    R.TRIG_WATCHDOG_S = 0.3
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    fx = (TFX_RES, 2, 5, 0, TFX_CLICK, 5, 7, 0, 0, 0, 0, 0)
    r.set_triggers(fx)
    check(last_trig(path) == (RP.trigger_effect(2, 5), RP.weapon_effect(5, 7)), "trigger effects set")
    await asyncio.sleep(0.2)
    r.set_triggers(fx)                                       # refresh from the PC before the watchdog time
    await asyncio.sleep(0.2)
    check(r.fx == fx, "refreshed effects persist")
    await asyncio.sleep(0.25)
    check(r.fx == TRIG2_OFF and last_trig(path) == (RP.OFF_EFFECT, RP.OFF_EFFECT),
          "no refreshes from the PC (receiver closed, PC asleep) -> the triggers go back to normal")
    n = os.path.getsize(path)
    await asyncio.sleep(0.4)
    check(os.path.getsize(path) == n and r._trig_watch is None, "without effects the watchdog is idle and sends nothing")
    r.set_triggers(fx)
    check(r._trig_watch is not None, "new effects from the PC - the watchdog is on again")
    r.close()
    check(r._trig_watch is None, "closing stops the trigger watchdog")

    # ---------- transient hidraw error ----------
    path2 = os.path.join(TMP, "hidraw2")
    open(path2, "wb").close()
    r = R.Rumble(1, Dev(), "hidraw", hidraw=(path2, True))
    real, calls = R.os.write, []
    def flaky(fd, data):
        calls.append(fd)
        if len(calls) <= 2:
            raise OSError(errno.EIO, "Input/output error")
        return real(fd, data)
    R.os.write = flaky
    r.set(10, 20); r.set(11, 20); r.set(12, 20)
    R.os.write = real
    check(r.methods == ["hidraw"] and r.fails["hidraw"] == 0 and os.path.getsize(path2) > 0,
          f"2x EIO (clogged Bluetooth) and it still works - the HID report is not turned off ({r.methods})")
    fd = r.fd
    def gone(fd, data):
        raise OSError(errno.ENODEV, "No such device")
    R.os.write = gone
    r.set(13, 20)
    R.os.write = real
    check(r.methods == [] and r.fd is None, "ENODEV (pad gone) - turned off at once, descriptor closed")
    try:
        os.fstat(fd)
        closed = False
    except OSError:
        closed = True
    check(closed, "the hidraw descriptor really closed (no leak)")
    r.close()
    r = R.Rumble(2, Dev(), "hidraw", hidraw=(path2, True))
    R.os.write = gone
    check(r.set_triggers(TRIG2_OFF[:8] + (1, 100, 40, 0)) is False and r.methods == [], "a trigger effect write error counts the same")
    R.os.write = real
    r.close()

asyncio.run(main())

# ---------- UTF-8 na granicy znaku ----------
events.set_enabled(True)
events.add(proto.EV_ERROR, proto.E_ERROR, 255, "błąd: " + "żółć" * 30)
sent = []
events.flush(lambda *a: sent.append(a))
arg = sent[0][-1]
check(len(arg) <= events.ARG_MAX and "�" not in cstr(arg) and cstr(arg).endswith("…"),
      f"event argument cut on a character boundary, with an ellipsis ({cstr(arg)[-6:]!r})")
import sender_net as net
import sender_update as U
net.codec, out = Codec(), []
net.emit = lambda t, i, *v: out.append((t, v))
U.status, U.fail_msg = proto.UPD_FAILED, "błąd: nie można pobrać - " + "ąę" * 80
U.send_build()
msg = next(v[0] for t, v in out if t == proto.T_UPDMSG)
check(len(msg) <= BODY[proto.T_UPDMSG].size and "�" not in cstr(msg), "git pull error reason without half a letter")

# ---------- HMAC key on one side only ----------
keyed, plain = Codec(b"sekret"), Codec()
state = (128,) * 6 + (0, 0, 0, 80, 0, 0, 0, 8192, 0, 0, 0)
legit = Codec().pack(T_STATE2, 0, *state, 1, 2, 3)
check(plain.parse(legit)[0] == T_STATE2, "no key on both sides: state2 accepted as before")
check(plain.parse(keyed.pack(T_PING, 255, 1)) is None and time.monotonic() - plain.mismatch_t < 1,
      "no key: a ping with HMAC (16 B) recognized as a keyed packet")
check(plain.parse(keyed.pack(T_STATE, 0, *state)) is None, "... and state + HMAC (= state2 length) is no longer taken for state2")
check(plain.parse(keyed.pack(T_TRIG, 0, 1, 2, 3, 4)) is None, "trig + HMAC (= trig2 length) rejected - no random trigger effects")
plain.mismatch_t -= proto.KEY_MISMATCH_S + 1
check(plain.parse(legit)[0] == T_STATE2, "after KEY_MISMATCH_S of silence a state2 without a key is accepted again")
other = Codec(b"inny")
check(other.parse(keyed.pack(T_STATE, 0, *state)) is None and time.monotonic() - other.mismatch_t < 1,
      "another key: rejected and reported")
check(keyed.parse(Codec(b"sekret").pack(T_TRIG2, 0, *TRIG2_OFF))[0] == T_TRIG2, "the same key: works")
fresh = Codec(b"sekret")
fresh.parse(b"\x00garbage-from-other-app")
check(fresh.mismatch_t == float("-inf"), "foreign datagrams on the port do not pose as another key")

if fails:
    print("FAIL", fails)
    sys.exit(1)
print("HUNT6 TX OK")
