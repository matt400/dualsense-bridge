"""End to end over UDP with an HMAC key: the sender and the receiver without auth.json switch to protocol v3 by themselves (probe
ping), the pad state and a PC command work, and a recorded packet sent again does nothing.
    python3 tests/test_e2e_key.py receiver sender"""
import asyncio, os, socket, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
TX = sys.argv[2]
from rxenv import *                              # noqa  (receiver: argv[1])
sys.path.insert(1, TX)
sys.path.insert(1, TX + "/../common")
from proto import CMD_POWER_OFF, VER3
# --- receiver z kluczem na losowym porcie ---
N.send_peer = ORIG_SEND_PEER
N.send_repeat = ORIG_SEND_REPEAT
C.IN_PORT, C.KEY = 0, b"wspolny klucz"
N.init()
port = N.in_sock.getsockname()[1]
threading.Thread(target=N.net_loop, daemon=True).start()
# --- sender with the same key ---
import sender_config; sender_config.init(["127.0.0.1", "--key", "wspolny klucz"])
from sender_config import C as TC
TC.PORT, TC.AUTH_FILE = port, os.path.join(TMP, "auth_tx.json")
import sender_net as net, sender_state as state, sender_streams as streams
from sender_ctrl import Ctrl
net.init()
got = []
net.on_command = lambda i, cmd, arg: got.append((i, cmd))
sniffed = []
real_send = net.send_raw


def sniff(data):
    sniffed.append(data)
    real_send(data)


net.send_raw = sniff


async def main():
    loop = asyncio.get_running_loop()
    assert net.open_sock(loop)
    c = Ctrl(0, "aa:bb:cc:dd:ee:01", None, None, None)
    state.active[c.key] = c
    pinger = asyncio.create_task(net.pinger())
    hb = asyncio.create_task(streams.heartbeat())
    await asyncio.sleep(1.6)                      # the v3 probe ping goes with every 4th ping (1/s)
    assert net.codec.v3 and N.codec.v3, (net.codec.v3, N.codec.v3)
    print("OK   both sides switched to v3 without configuration (probe ping)")
    c.send_ident(); c.pad[0] = 255; c.send_state(True)
    await asyncio.sleep(0.4)
    assert S.alive[0] and S.pad_id[0] == "aa:bb:cc:dd:ee:01" and S.states[0]["lx"] == 255, (S.alive[0], S.pad_id[0])
    assert sniffed[-1][0] >> 4 == VER3
    print("OK   pad state and identifier on v3")
    N.send_cmd(0, CMD_POWER_OFF)
    await asyncio.sleep(0.3)
    assert got == [(0, CMD_POWER_OFF)], got
    print("OK   PC command on v3 run once (3 copies)")
    # attack: a recorded state packet (lx = 0) sent again from another socket after 4 s - the receiver does not accept it
    c.pad[0] = 0; c.send_state(True)
    recorded = sniffed[-1]
    await asyncio.sleep(0.1)
    c.pad[0] = 200; c.send_state(True)
    await asyncio.sleep(4.2)
    assert S.states[0]["lx"] == 200
    stale = N.codec.stale
    atk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    atk.sendto(recorded, ("127.0.0.1", port))
    await asyncio.sleep(0.3)
    assert S.states[0]["lx"] == 200 and N.codec.stale == stale + 1, (S.states[0]["lx"], N.codec.stale)
    print("OK   a recorded packet sent again a few seconds later is rejected (freshness token)")
    pinger.cancel(); hb.cancel()

asyncio.run(main())
S.stop.set()
print("E2E KEY OK")
