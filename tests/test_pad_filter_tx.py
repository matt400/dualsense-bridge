"""Pad discovery accepts only Sony DualSense devices, and resync() keeps the buttons when the device cannot be read."""
import sys, types
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import sender_supervisor as sup, sender_state as state
from sender_ctrl import Ctrl

class Dev:
    def __init__(s, path, name, vendor, product):
        s.path, s.name, s.uniq, s.phys = path, name, "aa:bb:cc:dd:ee:ff", ""
        s.info = types.SimpleNamespace(vendor=vendor, product=product)
    def close(s): pass

devs = {"/dev/input/event1": Dev("/dev/input/event1", "Wireless Controller", 0x054C, 0x0CE6),
        "/dev/input/event2": Dev("/dev/input/event2", "Xbox Wireless Controller", 0x045E, 0x0B13),
        "/dev/input/event3": Dev("/dev/input/event3", "Wireless Controller", 0x054C, 0x05C4)}   # DualShock 4
sup.evdev.list_devices = lambda: list(devs)
sup.evdev.InputDevice = lambda p: devs[p]
sup._node_id = lambda p: None
state.active.clear()
got = sup.discover()
paths = [d.path for g in got.values() for d in g.values()]
assert paths == ["/dev/input/event1"], paths

class Broken:
    path = "/dev/input/event1"
    def absinfo(s, a): raise OSError(19, "gone")
    def active_keys(s): raise OSError(19, "gone")
c = Ctrl(0, "aa:bb:cc:dd:ee:ff", Broken(), None, None)
c.mask = 0b101
c.resync()
assert c.mask == 0b101, bin(c.mask)
print("OK   pad filter by vendor/product, resync keeps buttons when the device is gone")
