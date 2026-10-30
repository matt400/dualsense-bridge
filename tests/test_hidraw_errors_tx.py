"""Nadajnik: regresje z szóstego przeglądu - strażnik efektów spustów, chwilowy błąd hidraw (bez wycieku deskryptora),
cięcie tekstów UTF-8 na granicy znaku, klucz HMAC tylko po jednej stronie."""
import asyncio, errno, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_events as events
import sender_rumble as R
from proto import BODY, T_EVENT, T_PING, T_STATE, T_STATE2, T_TRIG, T_TRIG2, TFX_CLICK, TFX_RES, TRIG2_OFF, Codec, cstr

TMP = tempfile.mkdtemp()
fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


class Dev:
    def capabilities(self):
        return {}


def last_trig(path):
    """Ostatnie efekty spustów z raportów BT (78 B) z flagami spustów albo None."""
    data = open(path, "rb").read()
    for k in range(len(data) - 78, -1, -78):
        c = data[k + 3:k + 78]
        if c[0] & (R.FLAG0_LEFT_TRIGGER | R.FLAG0_RIGHT_TRIGGER):
            return bytes(c[R.LEFT_TRIGGER_AT:R.LEFT_TRIGGER_AT + 11]), bytes(c[R.RIGHT_TRIGGER_AT:R.RIGHT_TRIGGER_AT + 11])
    return None


async def main():
    # ---------- strażnik efektów spustów ----------
    R.TRIG_WATCHDOG_S = 0.3
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    fx = (TFX_RES, 2, 5, 0, TFX_CLICK, 5, 7, 0, 0, 0, 0, 0)
    r.set_triggers(fx)
    check(last_trig(path) == (R.trigger_effect(2, 5), R.weapon_effect(5, 7)), "efekty spustów ustawione")
    await asyncio.sleep(0.2)
    r.set_triggers(fx)                                       # odświeżenie z PC przed czasem strażnika
    await asyncio.sleep(0.2)
    check(r.fx == fx, "odświeżane efekty trwają")
    await asyncio.sleep(0.25)
    check(r.fx == TRIG2_OFF and last_trig(path) == (R.OFF_EFFECT, R.OFF_EFFECT),
          "brak odświeżeń z PC (receiver zamknięty, PC uśpiony) -> spusty wracają do zwykłych")
    n = os.path.getsize(path)
    await asyncio.sleep(0.4)
    check(os.path.getsize(path) == n and r._trig_watch is None, "bez efektów strażnik nie działa i nic nie wysyła")
    r.set_triggers(fx)
    check(r._trig_watch is not None, "nowe efekty z PC - strażnik znowu czuwa")
    r.close()
    check(r._trig_watch is None, "zamknięcie zatrzymuje strażnika spustów")

    # ---------- chwilowy błąd hidraw ----------
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
          f"2x EIO (zatkany Bluetooth) i dalej działa - raport HID nie jest wyłączany ({r.methods})")
    fd = r.fd
    def gone(fd, data):
        raise OSError(errno.ENODEV, "No such device")
    R.os.write = gone
    r.set(13, 20)
    R.os.write = real
    check(r.methods == [] and r.fd is None, "ENODEV (pada nie ma) - wyłączone od razu, deskryptor zamknięty")
    try:
        os.fstat(fd)
        closed = False
    except OSError:
        closed = True
    check(closed, "deskryptor hidraw naprawdę zamknięty (bez wycieku)")
    r.close()
    r = R.Rumble(2, Dev(), "hidraw", hidraw=(path2, True))
    R.os.write = gone
    check(r.set_triggers(TRIG2_OFF[:8] + (1, 100, 40, 0)) is False and r.methods == [], "błąd zapisu efektów spustów liczy się tak samo")
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
      f"argument zdarzenia ucięty na granicy znaku, z wielokropkiem ({cstr(arg)[-6:]!r})")
import sender_net as net
import sender_update as U
net.codec, out = Codec(), []
net.emit = lambda t, i, *v: out.append((t, v))
U.status, U.fail_msg = proto.UPD_FAILED, "błąd: nie można pobrać - " + "ąę" * 80
U.send_build()
msg = next(v[0] for t, v in out if t == proto.T_UPDMSG)
check(len(msg) <= BODY[proto.T_UPDMSG].size and "�" not in cstr(msg), "powód błędu git pull bez pół litery")

# ---------- klucz HMAC tylko po jednej stronie ----------
keyed, plain = Codec(b"sekret"), Codec()
state = (128,) * 6 + (0, 0, 0, 80, 0, 0, 0, 8192, 0, 0, 0)
legit = Codec().pack(T_STATE2, 0, *state, 1, 2, 3)
check(plain.parse(legit)[0] == T_STATE2, "bez klucza po obu stronach: state2 przyjęty jak dotąd")
check(plain.parse(keyed.pack(T_PING, 255, 1)) is None and time.monotonic() - plain.mismatch_t < 1,
      "bez klucza: ping z HMAC (16 B) rozpoznany jako pakiet z kluczem")
check(plain.parse(keyed.pack(T_STATE, 0, *state)) is None, "... i stan + HMAC (= długość state2) już nie jest brany za state2")
check(plain.parse(keyed.pack(T_TRIG, 0, 1, 2, 3, 4)) is None, "trig + HMAC (= długość trig2) odrzucony - nie losowe efekty spustów")
plain.mismatch_t -= proto.KEY_MISMATCH_S + 1
check(plain.parse(legit)[0] == T_STATE2, "po KEY_MISMATCH_S ciszy state2 bez klucza znowu przyjmowany")
other = Codec(b"inny")
check(other.parse(keyed.pack(T_STATE, 0, *state)) is None and time.monotonic() - other.mismatch_t < 1,
      "inny klucz: odrzucone i zgłoszone")
check(keyed.parse(Codec(b"sekret").pack(T_TRIG2, 0, *TRIG2_OFF))[0] == T_TRIG2, "ten sam klucz: działa")
fresh = Codec(b"sekret")
fresh.parse(b"\x00garbage-from-other-app")
check(fresh.mismatch_t == float("-inf"), "obce datagramy na porcie nie udają innego klucza")

if fails:
    print("FAIL", fails)
    sys.exit(1)
print("HUNT6 TX OK")
