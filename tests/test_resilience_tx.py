import asyncio, sys
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import sender_controller as M, sender_streams as st, sender_supervisor as sup, sender_net as net
net.init()
sup.discover = lambda: {}
beats = {"n": 0}
orig = st.heartbeat
async def flaky():
    beats["n"] += 1
    if beats["n"] == 1:
        raise KeyError("nieoczekiwany błąd")
    await orig()
M.streams.heartbeat = flaky
async def run():
    task = asyncio.create_task(M.main())
    await asyncio.sleep(1.0)
    alive = not task.done()
    task.cancel()
    try: await task
    except (asyncio.CancelledError, Exception): pass
    print(("PASS" if alive and beats["n"] >= 2 else "FAIL"), "nadajnik działa dalej po błędzie w pętli heartbeat", beats)
asyncio.run(run())
