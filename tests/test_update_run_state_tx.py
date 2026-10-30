"""Sender: an exception in sender_update.run() does not leave the state "running".
    python3 tests/test_update_run_state_tx.py sender"""
import asyncio, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_update as UPD

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- sender_update.run(): no stuck "running" ----------
async def update_cases():
    real_pull, real_build = proto.git_pull, UPD.send_build
    calls = []
    def boom(*_a):
        raise RuntimeError("git exploded")
    proto.git_pull = boom
    UPD.send_build = lambda: calls.append(UPD.status)
    try:
        UPD._last = float("-inf")
        try:
            await UPD.run()
        except RuntimeError:
            pass
        check(UPD.status != proto.UPD_RUNNING and UPD.status == proto.UPD_FAILED, f"an exception leaves a failure, not 'running' (status {UPD.status})")
        check(UPD.fail_msg == UPD.INTERRUPTED, "the failure reason is set for the window")
        check(calls and calls[-1] == proto.UPD_FAILED, "the final state is sent")
        UPD._last = float("-inf")
        proto.git_pull = lambda *_a: (proto.UPD_NO_CHANGE, "", "", "", [])
        await UPD.run()
        check(UPD.status == proto.UPD_NO_CHANGE, "the next update can run")
    finally:
        proto.git_pull, UPD.send_build = real_pull, real_build

asyncio.run(update_cases())

sys.exit(1 if fails else 0)
