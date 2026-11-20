"""Sender: a config file value of the wrong type is reported and replaced by the default, it does not end the start with a bare
ValueError (the service would restart in a loop with a traceback that does not name the setting).
    python3 tests/test_config_types_tx.py sender"""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
sys.path.insert(0, os.path.dirname(__file__))
import sender_config
from chk import check, fails

tmp = tempfile.mkdtemp()
def load(text):
    path = os.path.join(tmp, "t.toml")
    with open(path, "w") as f:
        f.write('host = "127.0.0.1"\n' + text)
    return sender_config.init(["--config", path])

D = sender_config.DEFAULTS
for key, bad in (("stick_deadzone", '"abc"'), ("rescan_s", '""'), ("pad_min_dt", "nan"), ("heartbeat_s", "1e999"), ("rt_priority", "[1, 2]"),
                 ("speaker_volume", "{a = 1}"), ("idle_off_min", '"x"'), ("tap_max_s", "true"), ("grab_pad", '"false"'), ("bt_adapter", "[1]"),
                 ("port", '"abc"'), ("max_pads", "nan")):
    try:
        c = load(f"{key} = {bad}\n")
    except Exception as e:                                       # noqa: BLE001
        check(False, f"{key} = {bad} does not raise (got {e!r})")
        continue
    check(c.PORT == D["port"] and c.STICK_DZ == D["stick_deadzone"] and c.GRAB_PAD is True and c.MAX_PADS == D["max_pads"],
          f"{key} = {bad}: the defaults are used")

c = load("stick_deadzone = 5\nrescan_s = 3\ntap_max_s = 0.3\ngrab_pad = false\nport = 6000\ntouch_coalesce_ms = 1.5\n")
check(c.STICK_DZ == 5 and c.RESCAN_S == 3 and c.TAP_MAX_S == 0.3 and c.GRAB_PAD is False and c.PORT == 6000
      and c.TOUCH_COALESCE_S == 0.0015, "valid values pass unchanged")
c = load('port = "6001"\n')
check(c.PORT == 6001, "a number in quotes still passes")

sys.exit(1 if fails else 0)
