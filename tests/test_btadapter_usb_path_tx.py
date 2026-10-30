"""Sender: btadapter recognises USB by path components, not by the substring "/usb".
    python3 tests/test_btadapter_usb_path_tx.py sender"""
import sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_btadapter as BA
from pathlib import Path

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- btadapter: USB by path components ----------
root = Path(tempfile.mkdtemp())
plain = root / "devices/platform/soc/usbphy-bridge/serial0/bluetooth/hci0"
plain.mkdir(parents=True)
check(not BA._is_usb(plain), "a built-in controller on a path that merely contains 'usb' is not USB")
hub = root / "devices/pci0000:00/usb3/3-10/3-10:1.0/bluetooth/hci1"
hub.mkdir(parents=True)
check(BA._is_usb(hub), "a path through a usbN root hub is USB")
desc = root / "devices/odd/bus/1-2"
(desc / "1-2:1.0/bluetooth/hci2").mkdir(parents=True)
(desc / "idVendor").write_text("0a12\n")
check(BA._is_usb(desc / "1-2:1.0/bluetooth/hci2"), "a parent with USB descriptors (idVendor) is USB")

sys.exit(1 if fails else 0)
