import asyncio, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_net as net, sender_ctrl, sender_control as ctl, sender_state as state
from sender_ctrl import Ctrl
from proto import BODY, T_INFO
net.init()
out = []
def emit(t, i, *v):
    BODY[t].pack(*v)                    # jak prawdziwy emit: pakowanie rzuci wyjątek dla złych wartości
    out.append((t, i, v))
net.emit = sender_ctrl.net.emit = emit
async def main():
    a = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None); a.reports["pad"] = 5000
    state.active[a.key] = a
    task = asyncio.create_task(ctl.info_loop())
    await asyncio.sleep(1.1)
    b = Ctrl(0, a.key, None, None, None)        # szybki reconnect: nowy Ctrl, liczniki od zera
    state.active[a.key] = b
    await asyncio.sleep(1.1)
    alive = not task.done()
    exc = task.exception() if task.done() else None
    task.cancel()
    print("PASS" if alive else f"FAIL info_loop padł: {exc!r}", "pętla info przeżywa reconnect pada")
    infos = [v for t, i, v in out if t == T_INFO]
    print("PASS" if all(x >= 0 for v in infos for x in v) else "FAIL", "Hz nieujemne:", infos)
asyncio.run(main())
