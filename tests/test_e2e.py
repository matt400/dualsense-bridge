import asyncio, os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
TX = sys.argv[2]
from rxenv import *                              # noqa  (receiver: argv[1])
sys.path.insert(1, TX)
sys.path.insert(1, TX + "/../common")
from proto import CMD_POWER_OFF
# --- receiver na losowym porcie ---
N.send_peer = ORIG_SEND_PEER
N.send_repeat = ORIG_SEND_REPEAT
C.IN_PORT = 0
N.init()
port = N.in_sock.getsockname()[1]
threading.Thread(target=N.net_loop, daemon=True).start()
threading.Thread(target=F.feedback_thread, args=(N.send_feedback,), daemon=True).start()
# --- nadajnik ---
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C as TC
TC.PORT = port
import sender_net as net, sender_control as ctl, sender_state as state, sender_streams as streams
from sender_ctrl import Ctrl
net.init()
got = {"cmd": [], "fb": []}
net.on_command = lambda i, cmd, arg: got["cmd"].append((i, cmd))
net.on_feedback = lambda i, v: got["fb"].append((i, v))
# Wake-on-LAN i zdarzenia: ustawienia z okna receivera idą do nadajnika (scfg), zdarzenia wracają do dziennika
import rx_events as ev, rx_settings as R, sender_events, sender_wol
from proto import E_WOL
TC.WOL_FILE = os.path.join(TMP, "wol_e2e.json")
net.on_scfg = sender_wol.apply_scfg
R.apply(R.Settings(log=R.LogConfig(sender=True), wol=R.WolConfig(mac="aa:bb:cc:dd:ee:ff", button="triangle")))
ev.clear()

async def main():
    loop = asyncio.get_running_loop()
    assert net.open_sock(loop)
    c = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
    state.active[c.key] = c
    c.send_ident(); c.pad[0] = 255; c.send_state(True)
    pinger = asyncio.create_task(net.pinger())
    info = asyncio.create_task(ctl.info_loop())
    hb = asyncio.create_task(streams.heartbeat())       # jak w prawdziwym nadajniku: bez niego receiver po 0.5 s resetuje slot
    await asyncio.sleep(1.3)
    print("   stan:", S.alive[0], S.pad_id[0], S.states[0]["lx"])
    assert S.alive[0] and S.pad_id[0] == "aa:bb:cc:dd:ee:ff" and S.states[0]["lx"] == 255
    assert S.info[0] is not None and S.info[0][0] is not None and S.info[0][0] < 50, S.info[0]
    assert sender_wol.cfg.mac == "aa:bb:cc:dd:ee:ff" and sender_wol.cfg.button == 2 and sender_events.enabled, sender_wol.cfg
    assert os.path.exists(TC.WOL_FILE), "nadajnik zapisał ustawienia z receivera"
    sender_events.info(E_WOL, 0, "aa:bb:cc:dd:ee:ff")
    await asyncio.sleep(0.6)
    sev = [e for e in ev.since(0) if e.sender]
    assert len(sev) == 1 and "aa:bb:cc:dd:ee:ff" in sev[0].text, [e.text for e in ev.since(0)]
    assert any(not e.sender and "online" in e.text.lower() for e in ev.since(0)), "nadajnik online w dzienniku"
    N.send_cmd(0, CMD_POWER_OFF)
    F.pulse(0, "profile")
    await asyncio.sleep(0.5)
    assert got["cmd"] == [(0, CMD_POWER_OFF)], got["cmd"]          # 3 kopie tego samego pakietu - nadajnik wykonuje raz
    fbv = [v for _, v in got["fb"]]
    assert any(v[1] == 190 for v in fbv) and fbv[-1][:2] == (0, 0), fbv   # przed pulsem: kolor domyślny lightbara
    hb.cancel()
    for _ in range(3):
        net.emit(8, 0)                                                # bye
    await asyncio.sleep(0.2)
    assert not S.alive[0] and not S.want[0].is_set()
    pinger.cancel(); info.cancel()
    print(f"OK   end-to-end UDP: stan, ident -> profil, ping {S.info[0] and S.info[0][0]} ms, info, scfg -> wol.json, "
          f"zdarzenie nadajnika w dzienniku, wyłączenie (3 kopie, wykonane raz), potwierdzenie {fbv[0]} -> {fbv[-1]}, bye")
asyncio.run(main())
print("E2E OK")
