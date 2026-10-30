"""Sender: the firmware threshold for the DualSense vibration v2 flag (DS_FEATURE_VERSION(2, 21) in hid-playstation)."""
import sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_rumble as R

ver = lambda major, minor: (major << 8) | minor
assert R.VIBRATION_V2_FROM == ver(2, 21) == 0x0215, hex(R.VIBRATION_V2_FROM)
assert not ver(2, 20) >= R.VIBRATION_V2_FROM, "2.20 still uses the v1 vibration flag"
assert ver(2, 21) >= R.VIBRATION_V2_FROM and ver(2, 33) >= R.VIBRATION_V2_FROM and ver(3, 0) >= R.VIBRATION_V2_FROM
assert not ver(1, 99) >= R.VIBRATION_V2_FROM
print("OK   rumble: v2 vibration from firmware 2.21 (0x0215)")
