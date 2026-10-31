"""Sender: Bluetooth.loop refreshes BlueZ once for the 3 UDP copies of one BT_LIST, not once per copy.
    python3 tests/test_bt_report_gap_tx.py sender"""
import asyncio, contextlib, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_bt as SB
import sender_net as net
from proto import BT_LIST

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

calls = []


async def fake_run(argv, timeout):
    calls.append(time.monotonic())
    return 1, "", "org.bluez was not provided"                # a refresh that fails fast: only the number of calls matters


async def main():
    net.scfg_t = time.monotonic()                              # a receiver that knows the new packets
    bt = SB.Bluetooth(run=fake_run, emit=lambda *a: None, sysfs=None)
    task = asyncio.create_task(bt.loop())
    await asyncio.sleep(0.6)                                   # past the first report and its gap
    start = len(calls)
    for _ in range(3):                                         # the window's BT_LIST, sent three times in a row
        bt.on_command(BT_LIST, 0, bytes(6))
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    check(len(calls) - start == 1, f"3 copies of BT_LIST within 30 ms: {len(calls) - start} refresh(es), not 3")
    await asyncio.sleep(1.2)                                   # the deferred copies and the tick are over
    before = len(calls)
    bt.on_command(BT_LIST, 0, bytes(6))
    await asyncio.sleep(0.1)
    check(len(calls) - before == 1, "a later BT_LIST (after the gap) is answered at once")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


asyncio.run(main())
sys.exit(1 if fails else 0)
