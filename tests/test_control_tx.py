import asyncio, os, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_net as net, sender_ctrl, sender_control as ctl, sender_state as state
net.clock.base = 0   # pings on the perf_counter clock (the test gives the pong times)
from sender_ctrl import Ctrl
from proto import Codec, T_CMD, T_INFO, T_PONG, CMD_POWER_OFF, RTT_NONE
out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, i, v))
net.init()
LOG = os.environ["BTLOG"]
open(LOG, "w").close()
mac = "aa:bb:cc:dd:ee:ff"

# note_input: stick noise does not count as use
c = Ctrl(0, mac, None, None, None)
c.last_input = 0.0; c.pad = [130, 126, 5, 128, 131, 0]; c.note_input(10.0); assert c.last_input == 0.0
c.pad[0] = 200; c.note_input(11.0); assert c.last_input == 11.0
c.pad[0] = 128; c.mask = 1; c.note_input(12.0); assert c.last_input == 12.0
print("OK   activity: noise ignored, stick / button counted")

# a command from the PC through the socket: on_readable -> on_command -> bluetoothctl disconnect
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
assert lines.count(f"disconnect {mac}") == 1, lines          # 3 repeats -> one power-off
print("OK   power-off command: one 'bluetoothctl disconnect' despite 3 repeats, unknown slot ignored")

# pad over USB (the key is not a MAC) - no bluetoothctl
open(LOG, "w").close()
u = Ctrl(1, "usb-0000:00:14.0-1/input3", None, None, None)
asyncio.run(ctl.power_off(u, "test")); assert open(LOG).read() == ""

# brak bluetoothctl w systemie
path = os.environ["PATH"]; os.environ["PATH"] = "/nonexistent"
c.powering_off = 0.0
asyncio.run(ctl.power_off(c, "test")); os.environ["PATH"] = path
print("OK   pad over USB skipped, missing bluetoothctl does not crash the sender")

# a ping for the info packet
net.recent_rtt.clear(); assert net.rtt_x10() == RTT_NONE
net.sock = FakeSock([pc.pack(T_PONG, 255, time.perf_counter_ns() - 2_000_000)]); net.on_readable()
assert 15 <= net.rtt_x10() <= 40, net.rtt_x10()

# info every second + power-off after idle
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
assert open(LOG).read().count(f"disconnect {mac}") == 1, "idle 1.5 s -> one power-off"
print(f"OK   info: {infos[-1]} (ping x10, pad Hz, motion Hz), power-off after idle")
print("CONTROL OK")
