"""Nadajnik: efekty spustów z trig2 - klik (Weapon 0x25), wibracja spustu (Vibration 0x26), wibracja z gry na
spustach (L2 = duży silnik, R2 = mały, po RFX_HOLD_S powrót do efektu), stary trig dalej działa."""
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
ok = lambda m: print("OK  ", m)

# ---------- kodowanie efektów ----------
w = R.weapon_effect(5, 6)
assert len(w) == 11 and w[0] == 0x25 and int.from_bytes(w[1:3], "little") == (1 << 5) | (1 << 6) and w[3] == 5
assert not any(w[4:])
assert R.weapon_effect(1, 6)[0] == R.weapon_effect(8, 6)[0] == R.weapon_effect(5, 0)[0] == R.TRIG_OFF
assert R.weapon_effect(7, 99)[3] == 7 and int.from_bytes(R.weapon_effect(7, 1)[1:3], "little") == 0x180
ok("klik: Weapon 0x25, strefy punktu i punktu + 1, siła - 1; punkt poza 2-7 / siła 0 = Off")

v = R.vibration_effect(3, 4, 40)
assert v[0] == 0x26 and int.from_bytes(v[1:3], "little") == sum(1 << z for z in range(3, 10))
amps = int.from_bytes(v[3:7], "little")
assert all((amps >> (3 * z)) & 7 == (3 if z >= 3 else 0) for z in range(10)) and v[9] == 40 and not v[7] | v[8] | v[10]
assert R.vibration_effect(0, 0, 40)[0] == R.vibration_effect(0, 3, 0)[0] == R.vibration_effect(10, 3, 9)[0] == R.TRIG_OFF
ok("wibracja spustu: Vibration 0x26, strefy od początku, 3 bity amplitudy, częstotliwość w bajcie 9")

assert R.effect(TFX_RES, 2, 6, 0) == R.trigger_effect(2, 6) and R.effect(TFX_CLICK, 4, 3, 0) == R.weapon_effect(4, 3)
assert R.effect(TFX_VIB, 0, 2, 9) == R.vibration_effect(0, 2, 9) and R.effect(TFX_OFF, 9, 9, 9) == R.OFF_EFFECT
assert R.effect(42, 1, 1, 1) == R.OFF_EFFECT
assert R.rumble_amp(255, 100) == 8 and R.rumble_amp(0, 200) == 0 and R.rumble_amp(255, 50) == 4
assert R.rumble_amp(10, 100) == 0 and R.rumble_amp(255, 255) == 8 and R.rumble_amp(64, 200) == 4
ok("tryby trig2 -> efekt; siła wibracji z gry: silnik x % -> amplituda 0-8 (słabe drgania silnika = brak)")
assert trig2_from_legacy((3, 6, 0, 0)) == (TFX_RES, 3, 6, 0, TFX_OFF, 0, 0, 0, 0, 0, 0, 0)
ok("stary trig (sam opór) -> trig2")


class Dev:
    def capabilities(self):
        return {}


def _trig(c):
    if not c[0] & (R.FLAG0_LEFT_TRIGGER | R.FLAG0_RIGHT_TRIGGER):
        return None
    return bytes(c[R.LEFT_TRIGGER_AT:R.LEFT_TRIGGER_AT + 11]), bytes(c[R.RIGHT_TRIGGER_AT:R.RIGHT_TRIGGER_AT + 11])


def last_flags(path):
    """Ostatni raport BT (78 B) -> (L2, R2) albo None, gdy bez flag spustów."""
    return _trig(open(path, "rb").read()[-78:][3:])


def last(path):
    """Efekty, które pad ma teraz: z ostatniego raportu BT z flagami spustów (raporty bez flag ich nie ruszają)."""
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
    assert os.path.getsize(path) > n and last_flags(path) is None, "bez wibracji z gry - zwykła wibracja nie rusza spustów"
    ok("trig2: opór na L2, klik na R2 w jednym raporcie; wibracja bez flag spustów")

    r.set(0, 0)
    await asyncio.sleep(0.05)
    assert r.set_triggers((*base, RFX_ON, 100, 40, 0))
    assert last(path) == (R.trigger_effect(2, 5), R.weapon_effect(5, 7)), "włączenie bez wibracji - efekty bez zmian"
    r.set(0, 255)                                   # mały silnik 0, duży 255 -> drga tylko L2
    assert last(path) == (R.vibration_effect(0, 8, 40), R.weapon_effect(5, 7)), last(path)
    r.set(128, 255)                                 # mały silnik -> R2 też drga (klik ustępuje)
    assert last(path) == (R.vibration_effect(0, 8, 40), R.vibration_effect(0, 4, 40)), last(path)
    await asyncio.sleep(0.03)                       # powtórka zmiany po REPEAT_S
    n = os.path.getsize(path)
    r.set(128, 255); await asyncio.sleep(0.03)
    assert os.path.getsize(path) == n, "bez zmian - nic nie idzie"
    r.set(0, 0)
    assert last(path) == (R.vibration_effect(0, 8, 40), R.vibration_effect(0, 4, 40)), "chwilowa cisza - spust dalej drga"
    r.set(0, 200); await asyncio.sleep(0.03)
    assert last(path) == (R.vibration_effect(0, 6, 40), R.vibration_effect(0, 4, 40)), "R2 trzyma drganie w RFX_HOLD_S"
    r.set(0, 0)
    await asyncio.sleep(RFX_HOLD_S + 0.1)
    assert last(path) == (R.trigger_effect(2, 5), R.weapon_effect(5, 7)), ("po ciszy spusty wracają do efektów", last(path))
    ok("wibracja z gry: L2 wg dużego silnika, R2 wg małego, zastępuje efekt, po RFX_HOLD_S powrót (timer)")

    r.set(0, 255)
    assert last(path)[0] == R.vibration_effect(0, 8, 40)
    r.set_triggers((*base, 0, 100, 40, 0))          # wyłączenie wibracji z gry w trakcie
    assert last(path) == (R.trigger_effect(2, 5), R.weapon_effect(5, 7)) and r._rv == [None, None]
    r.set_triggers((*base, RFX_ON, 50, 120, 0))
    assert last(path)[0] == R.vibration_effect(0, 4, 120), "siła 50 % i inna częstotliwość"
    r.set_triggers((3, 6, 0, 0))                    # stary trig: sam opór, bez wibracji z gry
    assert last(path) == (R.trigger_effect(3, 6), R.OFF_EFFECT) and r.fx == trig2_from_legacy((3, 6, 0, 0))
    r.set_triggers((TFX_VIB, 1, 3, 25, TFX_OFF, 0, 0, 0, 0, 0, 0, 0))
    assert last(path) == (R.vibration_effect(1, 3, 25), R.OFF_EFFECT)
    r.close()
    assert last(path) == (R.OFF_EFFECT, R.OFF_EFFECT) and r._rv_timer is None, "zamknięcie zdejmuje efekty"
    ok("zmiana ustawień w trakcie wibracji, stary trig, wibracja spustu jako tryb, zamknięcie = Off")

    # wibracja z gry bez efektów (wszystko Off) i pad bez raportu HID
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
    ok("USB, efekty Off + wibracja z gry; bez raportu HID - nic")

    # Ctrl: stary trig zamieniany na trig2, efekty zapamiętane do podłączenia pada
    c = sender_ctrl.Ctrl.__new__(sender_ctrl.Ctrl)
    c.rumble = None
    c.set_triggers((0, 0, 4, 2))
    assert c.triggers == trig2_from_legacy((0, 0, 4, 2))
    c.set_triggers(TRIG2_OFF)
    assert c.triggers == TRIG2_OFF and not any(c.triggers)
    ok("Ctrl.set_triggers: trig i trig2, pamięć do podłączenia pada")


asyncio.run(main())

# ---------- sieć: trig2 i trig -> callback ----------
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
