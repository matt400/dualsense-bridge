"""Sender: trigger effects from trig2 - click (Weapon 0x25), trigger vibration (Vibration 0x26), game rumble on the
triggers (L2 = big motor, R2 = small, back to the effect after RFX_HOLD_S), the old trig still works."""
import asyncio, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_ctrl
import sender_net as net
import sender_rumble as R
from proto import (BODY, CAP_TRIG_FX, RFX_HOLD_S, RFX_ON, T_TRIG, T_TRIG2, TFX_CLICK, TFX_OFF, TFX_RES, TFX_VIB,
                   TRIG2_OFF, Codec, trig2_from_legacy)

TMP = tempfile.mkdtemp()
from chk import ok

# ---------- effect encoding ----------
w = R.weapon_effect(5, 6)
assert len(w) == 11 and w[0] == 0x25 and int.from_bytes(w[1:3], "little") == (1 << 5) | (1 << 6) and w[3] == 5
assert not any(w[4:])
assert R.weapon_effect(1, 6)[0] == R.weapon_effect(8, 6)[0] == R.weapon_effect(5, 0)[0] == R.TRIG_OFF
assert R.weapon_effect(7, 99)[3] == 7 and int.from_bytes(R.weapon_effect(7, 1)[1:3], "little") == 0x180
ok("click: Weapon 0x25, zones of the point and point + 1, strength - 1; a point outside 2-7 / strength 0 = Off")

v = R.vibration_effect(3, 4, 40)
assert v[0] == 0x26 and int.from_bytes(v[1:3], "little") == sum(1 << z for z in range(3, 10))
amps = int.from_bytes(v[3:7], "little")
assert all((amps >> (3 * z)) & 7 == (3 if z >= 3 else 0) for z in range(10)) and v[9] == 40 and not v[7] | v[8] | v[10]
assert R.vibration_effect(0, 0, 40)[0] == R.vibration_effect(0, 3, 0)[0] == R.vibration_effect(10, 3, 9)[0] == R.TRIG_OFF
ok("trigger vibration: Vibration 0x26, zones from the start, 3 amplitude bits, frequency in byte 9")

assert R.effect(TFX_RES, 2, 6, 0) == R.trigger_effect(2, 6) and R.effect(TFX_CLICK, 4, 3, 0) == R.weapon_effect(4, 3)
assert R.effect(TFX_VIB, 0, 2, 9) == R.vibration_effect(0, 2, 9) and R.effect(TFX_OFF, 9, 9, 9) == R.OFF_EFFECT
assert R.effect(42, 1, 1, 1) == R.OFF_EFFECT
assert R.rumble_amp(255, 100) == 8 and R.rumble_amp(0, 200) == 0 and R.rumble_amp(255, 50) == 4
assert R.rumble_amp(10, 100) == 0 and R.rumble_amp(255, 255) == 8 and R.rumble_amp(64, 200) == 4
ok("trig2 modes -> effect; game rumble strength: motor x % -> amplitude 0-8 (weak motor rumble = none)")
assert trig2_from_legacy((3, 6, 0, 0)) == (TFX_RES, 3, 6, 0, TFX_OFF, 0, 0, 0, 0, 0, 0, 0)
ok("old trig (resistance only) -> trig2")


class Dev:
    def capabilities(self):
        return {}


def _trig(c):
    if not c[0] & (R.FLAG0_LEFT_TRIGGER | R.FLAG0_RIGHT_TRIGGER):
        return None
    return bytes(c[R.LEFT_TRIGGER_AT:R.LEFT_TRIGGER_AT + 11]), bytes(c[R.RIGHT_TRIGGER_AT:R.RIGHT_TRIGGER_AT + 11])


def last_flags(path):
    """The last BT report (78 B) -> (L2, R2), or None without trigger flags."""
    return _trig(open(path, "rb").read()[-78:][3:])


def last(path):
    """The effects the pad has now: from the last BT report with trigger flags (reports without flags do not touch them)."""
    data = open(path, "rb").read()
    return next((t for k in range(len(data) - 78, -1, -78) if (t := _trig(data[k + 3:k + 78])) is not None), None)


async def main():
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    assert r.bits & CAP_TRIG_FX
    base = (TFX_RES, 2, 5, 0, TFX_CLICK, 5, 7, 0)
    assert r.set_triggers((*base, 0, 0, 0, 0))
    assert last(path) == (R.trigger_effect(2, 5), R.weapon_effect(5, 7))
    n = os.path.getsize(path)
    r.set(30, 60)
    assert os.path.getsize(path) > n and last_flags(path) is None, "no game rumble - plain rumble does not touch the triggers"
    ok("trig2: resistance on L2, click on R2 in one report; rumble without trigger flags")

    r.set(0, 0)
    await asyncio.sleep(0.05)
    assert r.set_triggers((*base, RFX_ON, 100, 40, 0))
    assert last(path) == (R.trigger_effect(2, 5), R.weapon_effect(5, 7)), "turning on without rumble - effects unchanged"
    r.set(0, 255)                                   # small motor 0, big 255 -> only L2 vibrates
    assert last(path) == (R.vibration_effect(0, 8, 40), R.weapon_effect(5, 7)), last(path)
    r.set(128, 255)                                 # small motor -> R2 vibrates too (the click gives way)
    assert last(path) == (R.vibration_effect(0, 8, 40), R.vibration_effect(0, 4, 40)), last(path)
    await asyncio.sleep(0.03)                       # repeat of a change after REPEAT_S
    n = os.path.getsize(path)
    r.set(128, 255); await asyncio.sleep(0.03)
    assert os.path.getsize(path) == n, "no change - nothing goes out"
    r.set(0, 0)
    assert last(path) == (R.vibration_effect(0, 8, 40), R.vibration_effect(0, 4, 40)), "a brief silence - the trigger keeps vibrating"
    r.set(0, 200); await asyncio.sleep(0.03)
    assert last(path) == (R.vibration_effect(0, 6, 40), R.vibration_effect(0, 4, 40)), "R2 trzyma drganie w RFX_HOLD_S"
    r.set(0, 0)
    await asyncio.sleep(RFX_HOLD_S + 0.1)
    assert last(path) == (R.trigger_effect(2, 5), R.weapon_effect(5, 7)), ("after silence the triggers go back to their effects", last(path))
    ok("game rumble: L2 by the big motor, R2 by the small one, replaces the effect, back after RFX_HOLD_S (timer)")

    r.set(0, 255)
    assert last(path)[0] == R.vibration_effect(0, 8, 40)
    r.set_triggers((*base, 0, 100, 40, 0))          # game rumble turned off midway
    assert last(path) == (R.trigger_effect(2, 5), R.weapon_effect(5, 7)) and r._rv == [None, None]
    r.set_triggers((*base, RFX_ON, 50, 120, 0))
    assert last(path)[0] == R.vibration_effect(0, 4, 120), "strength 50 % and another frequency"
    r.set_triggers((3, 6, 0, 0))                    # old trig: resistance only, no game rumble
    assert last(path) == (R.trigger_effect(3, 6), R.OFF_EFFECT) and r.fx == trig2_from_legacy((3, 6, 0, 0))
    r.set_triggers((TFX_VIB, 1, 3, 25, TFX_OFF, 0, 0, 0, 0, 0, 0, 0))
    assert last(path) == (R.vibration_effect(1, 3, 25), R.OFF_EFFECT)
    r.close()
    assert last(path) == (R.OFF_EFFECT, R.OFF_EFFECT) and r._rv_timer is None, "closing removes the effects"
    ok("settings change during rumble, old trig, trigger vibration as a mode, close = Off")

    # game rumble without effects (all Off) and a pad without the HID report
    path2 = os.path.join(TMP, "hidraw2")
    open(path2, "wb").close()
    r = R.Rumble(1, Dev(), "hidraw", hidraw=(path2, False))
    r.set_triggers((*TRIG2_OFF[:8], RFX_ON, 100, 40, 0))
    r.set(255, 0)
    rep = open(path2, "rb").read()[-63:]
    assert rep[0] == 0x02 and rep[1 + R.RIGHT_TRIGGER_AT] == 0x26 and rep[1 + R.LEFT_TRIGGER_AT] == R.TRIG_OFF, "USB"
    r.close()
    ev = R.Rumble(2, Dev(), "off")
    assert not ev.bits & CAP_TRIG_FX and ev.set_triggers((*TRIG2_OFF[:8], RFX_ON, 100, 40, 0)) is False
    ev.set(255, 255)
    ok("USB, effects Off + game rumble; without the HID report - nothing")

    # Ctrl: the old trig turned into trig2, effects remembered until the pad connects
    c = sender_ctrl.Ctrl.__new__(sender_ctrl.Ctrl)
    c.rumble = None
    c.set_triggers((0, 0, 4, 2))
    assert c.triggers == trig2_from_legacy((0, 0, 4, 2))
    c.set_triggers(TRIG2_OFF)
    assert c.triggers == TRIG2_OFF and not any(c.triggers)
    ok("Ctrl.set_triggers: trig and trig2, memory until the pad connects")


asyncio.run(main())

# ---------- network: trig2 and trig -> callback ----------
cod = Codec()
got = []
net.on_triggers = lambda idx, vals: got.append((idx, vals))


class Sock:
    def __init__(self, pkts):
        self.pkts = list(pkts)
    def recv(self, n):
        if not self.pkts:
            raise BlockingIOError
        return self.pkts.pop(0)


vals = (TFX_CLICK, 5, 7, 0, TFX_VIB, 0, 3, 30, RFX_ON, 120, 40, 0)
net.codec = cod
net.sock = Sock([cod.pack(T_TRIG2, 1, *vals), cod.pack(T_TRIG, 0, 0, 0, 3, 6)])
net.on_readable()
assert got == [(1, vals), (0, (0, 0, 3, 6))], got
assert BODY[T_TRIG2].size == 12
ok("sender_net: trig2 i trig -> callback")
print("TRIGFX TX OK")
