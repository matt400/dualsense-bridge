"""Sender: port / max_pads are validated, rumble is case-insensitive.
    python3 tests/test_config_validation_tx.py sender"""
import sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- config validation ----------
def net_cfg(**kw):
    return dict(sender_config.DEFAULTS, host="127.0.0.1", **kw)

for key, bad in (("port", 0), ("port", -5), ("port", 70000), ("port", "abc"), ("max_pads", 0), ("max_pads", -1)):
    try:
        sender_config._apply_network(net_cfg(**{key: bad}))
        check(False, f"{key} = {bad!r} is rejected")
    except SystemExit as e:
        check(key in str(e), f"{key} = {bad!r} is rejected with the setting's name")
sender_config._apply_network(net_cfg(port=65535, max_pads=4))
check(C.PORT == 65535 and C.MAX_PADS == 4, "valid port / max_pads pass")
sender_config._apply_network(net_cfg(port="5005", max_pads=2))
check(C.PORT == 5005 and C.MAX_PADS == 2, "numbers given as text pass")
for typed, want in (("Auto", "auto"), (" EVDEV ", "evdev"), ("HidRaw", "hidraw"), ("OFF", "off"), ("nonsense", "auto")):
    sender_config._apply_runtime(dict(sender_config.DEFAULTS, rumble=typed))
    check(C.RUMBLE == want, f"rumble {typed!r} -> {want!r}")
sender_config.init(["127.0.0.1"])

sys.exit(1 if fails else 0)
