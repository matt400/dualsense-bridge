"""Nadajnik: efekty spustów ustawione przez grę (pakiet trig3) - surowe 11 B do pada na spuście bez efektu w profilu,
profil wygrywa, trig2 / zamknięcie / strażnik je zwalniają, kodowanie i odbiór pakietu.
    python3 tests/test_gametrig_tx.py sender"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_net as net
import sender_rumble as R
from proto import (BODY, CAP_TRIG_RAW, RFX_ON, SENDER_VERSION, T_TRIG3, TFX_CLICK, TFX_OFF, TFX_RES, TRG3_L2, TRG3_R2,
                   TRIG2_OFF, Codec)

TMP = tempfile.mkdtemp()
ok = lambda m: print("OK  ", m)

G_L2 = bytes([0x25, 0x30, 0x00, 0x05] + [0] * 7)          # klik z gry
G_R2 = bytes([0x21, 0xFF, 0x03, 0x92, 0x24, 0x49, 0x12] + [0] * 4)   # opór z gry
Z = bytes(11)


def trig3(vals=TRIG2_OFF, l2=None, r2=None):
    flags = (TRG3_L2 if l2 is not None else 0) | (TRG3_R2 if r2 is not None else 0)
    return (*vals, l2 or Z, r2 or Z, flags)


# ---------- rozbiór pakietu ----------
assert R.split_triggers((3, 6, 0, 0)) == ((TFX_RES, 3, 6, 0, *(0,) * 8), (None, None))
assert R.split_triggers(TRIG2_OFF) == (TRIG2_OFF, (None, None))
assert R.split_triggers(trig3(l2=G_L2)) == (TRIG2_OFF, (G_L2, None)), "flaga TRG3_R2 zgaszona - R2 bez efektu gry"
assert R.game_effect(G_R2) == G_R2 and R.game_effect(None) == R.OFF_EFFECT and R.game_effect(Z) == R.OFF_EFFECT
assert SENDER_VERSION >= 11
ok("trig3 -> trig2 + efekty gry (flagi), tryb 0 = Off, wersja nadajnika 11")

c = Codec()
data = c.pack(T_TRIG3, 1, *trig3((TFX_RES, 2, 5, 0, *(0,) * 8), G_L2, G_R2))
p = c.parse(data)
assert p[:2] == (T_TRIG3, 1) and len(data) == 4 + 35
assert BODY[T_TRIG3].unpack_from(data, 4) == trig3((TFX_RES, 2, 5, 0, *(0,) * 8), G_L2, G_R2)
got = []
net.on_triggers = lambda i, v: got.append((i, v))
net.fb_filter = net.fb_filter.__class__(1.0)


class Sock:
    def __init__(self, pkts):
        self.pkts = list(pkts)

    def recv(self, n):
        if not self.pkts:
            raise BlockingIOError
        return self.pkts.pop(0)


net.codec, net.sock = c, Sock([data, data])
net.on_readable()
assert got == [(1, trig3((TFX_RES, 2, 5, 0, *(0,) * 8), G_L2, G_R2))], got
ok("pakiet trig3: 35 B treści, odbiór w nadajniku raz (powtórka odrzucona)")


class Dev:
    def capabilities(self):
        return {}


def _trig(c):
    if not c[0] & (R.FLAG0_LEFT_TRIGGER | R.FLAG0_RIGHT_TRIGGER):
        return None
    return bytes(c[R.LEFT_TRIGGER_AT:R.LEFT_TRIGGER_AT + 11]), bytes(c[R.RIGHT_TRIGGER_AT:R.RIGHT_TRIGGER_AT + 11])


def last(path):
    data = open(path, "rb").read()
    return next((t for k in range(len(data) - 78, -1, -78) if (t := _trig(data[k + 3:k + 78])) is not None), None)


async def main():
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    assert r.bits & CAP_TRIG_RAW, "raport HID = nadajnik zna efekty gry"
    assert r.set_triggers(trig3(l2=G_L2, r2=G_R2))
    assert last(path) == (G_L2, G_R2), last(path)
    ok("profil bez efektów: efekty gry idą do pada bez zmian")

    prof = (TFX_CLICK, 5, 7, 0, TFX_OFF, 0, 0, 0, 0, 0, 0, 0)
    r.set_triggers(trig3(prof, l2=G_L2, r2=G_R2))
    assert last(path) == (R.weapon_effect(5, 7), G_R2), "L2 z profilu, R2 (bez efektu w profilu) z gry"
    r.set_triggers(trig3(prof, r2=G_R2))
    assert last(path) == (R.weapon_effect(5, 7), G_R2)
    r.set_triggers(trig3(TRIG2_OFF, r2=G_R2))
    assert last(path) == (R.OFF_EFFECT, G_R2), "L2 bez profilu i bez efektu gry = Off"
    ok("profil wygrywa na swoim spuście, na drugim efekt gry")

    r.set(0, 0)
    r.set_triggers((*prof[:8], RFX_ON, 100, 40, 0))   # trig2: efekty gry zapomniane
    assert r.game_fx == (None, None) and last(path) == (R.weapon_effect(5, 7), R.OFF_EFFECT)
    r.set_triggers(trig3(l2=G_L2))
    r.set(0, 0)
    r.set(10, 20)
    assert last(path) == (G_L2, R.OFF_EFFECT), "wibracja bez RFX nie rusza efektów gry"
    ok("trig2 zwalnia efekty gry; zwykła wibracja ich nie rusza")

    r._trig_watch.cancel()
    r._trig_expired()
    assert r.game_fx == (None, None) and last(path) == (R.OFF_EFFECT, R.OFF_EFFECT), "strażnik: brak odświeżeń -> Off"
    r.set_triggers(trig3(r2=G_R2))
    r.close()
    assert last(path) == (R.OFF_EFFECT, R.OFF_EFFECT), "zamknięcie zdejmuje efekty gry"
    ok("strażnik odświeżeń i zamknięcie zwalniają spusty gry")



asyncio.run(main())
print("GAME TRIGGERS TX OK")
