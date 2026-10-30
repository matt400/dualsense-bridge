"""Ctrl+C / SIGTERM: pad musi wysłać 'bye' zanim gniazdo zostanie zamknięte."""
import asyncio, sys, socket
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 0)); rx.settimeout(0.2)
C.PORT = rx.getsockname()[1]
import sender_controller as main_mod, sender_net as net, sender_supervisor as sup, sender_state as state, sender_streams as st
from sender_ctrl import Ctrl
from proto import Codec, T_BYE
class Dev:
    path = "/dev/input/event9"; name = "x"
    def absinfo(s, c): raise OSError
    def active_keys(s): return []
    def grab(s): pass
    def ungrab(s): pass
    def close(s): pass
    async def async_read_loop(s):
        await asyncio.sleep(3600)
        yield
net.init()
sup.discover = dict
async def runner():
    task = asyncio.create_task(main_mod.main())
    await asyncio.sleep(0.3)
    c = Ctrl(0, "aa:bb:cc:dd:ee:ff", Dev(), None, None); state.active[c.key] = c
    t = asyncio.create_task(sup.run_ctrl(c)); state.bg_tasks.add(t); t.add_done_callback(state.bg_tasks.discard)
    await asyncio.sleep(0.3)
    task.cancel()                      # jak SIGTERM / Ctrl+C
    try:
        await task
    except asyncio.CancelledError:
        pass
asyncio.run(runner())
codec = Codec(); types = []
try:
    while True:
        p = codec.parse(rx.recv(256))
        if p: types.append(p[0])
except TimeoutError:
    pass
print("PASS" if types.count(T_BYE) == 1 + len(net.REPEAT_DELAYS) else "FAIL", "bye z powtórkami przy zamykaniu nadajnika:", types.count(T_BYE), "; gniazdo zamknięte:", net.sock is None)
