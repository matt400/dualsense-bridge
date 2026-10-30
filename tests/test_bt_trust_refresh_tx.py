"""Sender: trust_pads copes with a refresh that replaced the list during the await.
    python3 tests/test_bt_trust_refresh_tx.py sender"""
import asyncio
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])

import sender_bt
import sender_ctrl
import sender_net as net
from proto import BTD_PAD, BTD_PAIRED, BTD_TRUSTED

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))
TMP = tempfile.mkdtemp()


# ---------- 3. trust_pads when a refresh replaced the list during the await ----------
async def t_trust():
    d = sender_bt.Device("aa:bb:cc:dd:ee:01", "pad", -50, BTD_PAD | BTD_PAIRED)
    fake = type("F", (), {})()
    fake.devices, fake.untrusted, fake.adapter = [d], set(), "hci0"
    async def set_prop(*a):
        fake.devices = [sender_bt.Device(d.mac, d.name, -70, d.flags)]      # a refresh with a new RSSI
    fake.set_prop = set_prop
    await sender_bt.Bluetooth.trust_pads(fake)
    check(len(fake.devices) == 1 and fake.devices[0].flags & BTD_TRUSTED, "trust_pads: survives a refresh that replaced the list")
asyncio.run(t_trust())

sys.exit(1 if fails else 0)
