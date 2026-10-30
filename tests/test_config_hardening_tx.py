"""Sender: configuration hardening regressions:
  - a period of 0 in heartbeat_s / rescan_s / battery_poll_s made a busy loop (under SCHED_FIFO) - now clamped
  - the default host was a private LAN address, so "no receiver host" could never be reported - now there is no default
  - the receiver's host name was resolved once at startup: a new DHCP lease of the PC needed a sender restart
python3 tests/test_config_hardening_tx.py sender"""
import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config
from sender_config import C, DEFAULTS, MIN_PERIOD_S
import sender_net as net

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


# ---- the periods of the polling loops are never below MIN_PERIOD_S ----
sender_config.init(["127.0.0.1"])
check(C.HEARTBEAT_S > 0 and C.RESCAN_S > 0 and C.BATT_POLL_S > 0, "default periods are positive")
for key, attr in (("heartbeat_s", "HEARTBEAT_S"), ("rescan_s", "RESCAN_S"), ("battery_poll_s", "BATT_POLL_S")):
    for bad in (0, -1, 0.0001):
        cfg = dict(sender_config.DEFAULTS, host="127.0.0.1", **{key: bad})
        sender_config._apply_pad_tuning(cfg)
        check(getattr(C, attr) == MIN_PERIOD_S, f"{key} = {bad} -> {MIN_PERIOD_S}")
    cfg = dict(sender_config.DEFAULTS, host="127.0.0.1", **{key: 0.2})
    sender_config._apply_pad_tuning(cfg)
    check(getattr(C, attr) == 0.2, f"{key} = 0.2 stays as typed")

# ---- no built-in host: an explicit error ----
check(DEFAULTS["host"] == "", "there is no default receiver host")
try:
    sender_config.init([])
    check(False, "init() without a host exits")
except SystemExit as e:
    check("host" in str(e).lower(), "init() without a host exits with a message")
sender_config.init(["127.0.0.1"])

# ---- the host name is resolved again when the receiver stays silent ----
check(sender_config.is_ip("192.168.1.4") and sender_config.is_ip("::1") and not sender_config.is_ip("desktop.local"),
      "is_ip: literal addresses vs names")
looked_up = []
answers = {"value": "10.0.0.7"}
def fake_resolve(host):
    looked_up.append(host)
    if answers["value"] is None:
        raise OSError("no such host")
    return answers["value"]
sender_config.resolve = fake_resolve


async def reresolve_cases():
    C.HOST, C.PC_ADDR = "desktop.local", "10.0.0.5"
    now = time.monotonic()
    net.last_pong = now
    check(not await net._reresolve(now - 100), "receiver answers: no lookup") ; check(not looked_up, "  (the resolver was not called)")
    net.last_pong = now - 100
    check(not await net._reresolve(now), "just started: wait before the first lookup"); check(not looked_up, "  (the resolver was not called)")
    check(await net._reresolve(now - 100) and C.PC_ADDR == "10.0.0.7", "silent receiver, new address: PC_ADDR updated")
    check(not await net._reresolve(now - 100), "same address again: no change")
    answers["value"] = None
    check(not await net._reresolve(now - 100) and C.PC_ADDR == "10.0.0.7", "failed lookup: the old address stays")
    answers["value"] = "10.0.0.9"
    C.HOST = "10.0.0.7"
    looked_up.clear()
    check(not await net._reresolve(now - 100) and not looked_up, "a literal IP is never looked up")

    # sock_keeper recreates the socket after the address changed
    C.HOST, C.PC_ADDR = "desktop.local", "127.0.0.1"
    answers["value"] = "127.0.0.2"
    net.sock, net.sock_bad, net.last_pong = None, False, 0.0
    net.RESOLVE_AFTER_S = net.SOCK_RETRY_S = 0.05
    task = asyncio.create_task(net.sock_keeper())
    await asyncio.sleep(0.4)
    task.cancel()
    check(C.PC_ADDR == "127.0.0.2" and net.sock is not None and net.sock.getpeername()[0] == "127.0.0.2",
          "sock_keeper: the socket now points at the new address")
    net.close_sock()

asyncio.run(reresolve_cases())
print("SENDER OK" if not fails else f"FAILED: {len(fails)}")
sys.exit(1 if fails else 0)
