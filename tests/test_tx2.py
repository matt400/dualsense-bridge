import asyncio, os, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_net as net, sender_ctrl, sender_control as ctl, sender_state as state
net.clock.base = 0   # pingi w zegarze perf_counter (test podaje czasy pongów)
from sender_ctrl import Ctrl
from proto import Codec, T_CMD, T_INFO, T_PONG, CMD_POWER_OFF, RTT_NONE
out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, i, v))
net.init()
LOG = os.environ["BTLOG"]
open(LOG, "w").close()
mac = "aa:bb:cc:dd:ee:ff"

# note_input: szum gałek nie liczy się jako użycie
c = Ctrl(0, mac, None, None, None)
c.last_input = 0.0; c.pad = [130, 126, 5, 128, 131, 0]; c.note_input(10.0); assert c.last_input == 0.0
c.pad[0] = 200; c.note_input(11.0); assert c.last_input == 11.0
c.pad[0] = 128; c.mask = 1; c.note_input(12.0); assert c.last_input == 12.0
print("OK   aktywność: szum ignorowany, gałka / przycisk liczone")

# polecenie z PC przez gniazdo: on_readable -> on_command -> bluetoothctl disconnect
class FakeSock:
    def __init__(s, pkts): s.pkts = list(pkts)
    def recv(s, n):
        if s.pkts: return s.pkts.pop(0)
        raise BlockingIOError
pc = Codec()
async def t_cmd():
    state.active.clear(); state.active[mac] = c; c.mask = 0
    net.on_command = ctl.on_command
    net.sock = FakeSock([pc.pack(T_CMD, 0, CMD_POWER_OFF, 0)] * 3 + [pc.pack(T_CMD, 1, CMD_POWER_OFF, 0)])
    net.on_readable()
    await asyncio.sleep(0.5)
asyncio.run(t_cmd())
lines = open(LOG).read().split("\n")
assert lines.count(f"disconnect {mac}") == 1, lines          # 3 powtórki -> jedno wyłączenie
print("OK   polecenie wyłączenia: jedno 'bluetoothctl disconnect' mimo 3 powtórek, nieznany slot zignorowany")

# pad po USB (klucz nie jest MAC-iem) - bez bluetoothctl
open(LOG, "w").close()
u = Ctrl(1, "usb-0000:00:14.0-1/input3", None, None, None)
asyncio.run(ctl.power_off(u, "test")); assert open(LOG).read() == ""

# brak bluetoothctl w systemie
path = os.environ["PATH"]; os.environ["PATH"] = "/nonexistent"
c.powering_off = 0.0
asyncio.run(ctl.power_off(c, "test")); os.environ["PATH"] = path
print("OK   pad po USB pominięty, brak bluetoothctl nie wywraca nadajnika")

# ping do pakietu info
net.recent_rtt.clear(); assert net.rtt_x10() == RTT_NONE
net.sock = FakeSock([pc.pack(T_PONG, 255, time.perf_counter_ns() - 2_000_000)]); net.on_readable()
assert 15 <= net.rtt_x10() <= 40, net.rtt_x10()

# info co sekundę + wyłączanie po bezczynności
open(LOG, "w").close(); out.clear()
C.IDLE_OFF_S = 1.5; c.powering_off = 0.0; c.last_input = time.monotonic()
async def t_info():
    task = asyncio.create_task(ctl.info_loop())
    for _ in range(25):
        c.reports["pad"] += 50; c.reports["motion"] += 48
        await asyncio.sleep(0.1)
    task.cancel()
asyncio.run(t_info())
infos = [v for t, i, v in out if t == T_INFO]
assert len(infos) >= 2 and 400 <= infos[-1][1] <= 560 and 380 <= infos[-1][2] <= 540, infos
assert open(LOG).read().count(f"disconnect {mac}") == 1, "bezczynność 1.5 s -> jedno wyłączenie"
print(f"OK   info: {infos[-1]} (ping x10, Hz pada, Hz ruchu), wyłączenie po bezczynności")
print("SENDER v5 OK")
