"""Receiver: a config file value of the wrong type is reported and replaced by the default, it does not end the start with a
bare ValueError (an autostarted receiver has no console to show it in).
    python3 tests/test_config_types.py receiver"""
import ctypes, os, sys, tempfile, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
sys.path.insert(0, os.path.dirname(__file__))
class _F:
    def __getattr__(s, n): return _F()
    def __call__(s, *a, **k): return 1
ctypes.windll = types.SimpleNamespace(kernel32=_F(), user32=_F(), winmm=_F(), shcore=_F())
import rx_config
from chk import check, fails

tmp = tempfile.mkdtemp()
def load(text):
    path = os.path.join(tmp, "t.toml")
    with open(path, "w") as f:
        f.write(text)
    return rx_config.init(["--config", path])

D = rx_config.DEFAULTS
for key, bad in (("in_port", '"abc"'), ("dsu_port", "nan"), ("pads", '""'), ("pad_hz", "[1]"), ("stale_s", '"x"'), ("mouse_sens", "1e999"),
                 ("low_batt", "{a = 1}"), ("scroll_natural", '"no"'), ("remap_file", "[1]"), ("log_file", "5"), ("acc_sign", "-1"),
                 ("gyro_sign", '["a", 1, 1]'), ("idle_lightbar", '["x"]'), ("game_ignore", '"obs64.exe"'), ("mouse_pads", '"ab"')):
    try:
        c = load(f"{key} = {bad}\n")
    except Exception as e:                                       # noqa: BLE001
        check(False, f"{key} = {bad} does not raise (got {e!r})")
        continue
    check(c.NUM_PADS == D["pads"] and c.IN_PORT == D["in_port"] and c.STALE_S == D["stale_s"] and c.MOUSE_PADS == set(D["mouse_pads"]),
          f"{key} = {bad}: the defaults are used")

c = load("pads = 3\nstale_s = 1\nmouse_sens = 0.25\ngyro_sign = [1, -1, 1]\nscroll_natural = true\nlow_batt = 20\nmouse_pads = [0, 1]\n")
check(c.NUM_PADS == 3 and c.STALE_S == 1 and c.MOUSE_SENS == 0.25 and c.GYRO_SIGN == (1, -1, 1) and c.SCROLL_NATURAL and c.LOW_BATT == 20
      and c.MOUSE_PADS == {0, 1}, "valid values pass unchanged")
c = load('in_port = "5006"\nkey = 1234\nidle_lightbar = [1, 2]\n')
check(c.IN_PORT == 5006 and c.KEY == b"1234" and c.IDLE_RGB == (1, 2, 0), "a number in quotes, a numeric key and a short colour list still pass")
c = load("gyro_res = 0\n")
check(c.GYRO_RES == D["gyro_res"], "gyro_res = 0 (a divisor) falls back to the default")
c = load("gyro_sign = [1, 1]\n")
check(c.GYRO_SIGN == tuple(D["gyro_sign"]), "a sign list of the wrong length falls back to the default")

sys.exit(1 if fails else 0)
