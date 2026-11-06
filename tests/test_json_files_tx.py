"""Sender + common: the settings / state JSON files are read through proto.read_json_object.
  - a file whose top level is not an object is refused with a ValueError (not an AttributeError from .get)
  - one malformed entry in gyro_cal.json / slots.json drops that entry only, the rest of the file still loads
python3 tests/test_json_files_tx.py sender"""
import json, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config
sender_config.init(["127.0.0.1"])
import proto
import sender_calib as calib
import sender_state as state
import sender_supervisor as sup
from sender_config import C

from chk import check, fails, raises                          # noqa: E402  (shared helper, tests/chk.py)

TMP = tempfile.mkdtemp()


def put(name, obj):
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))
    return path


# ---- read_json_object ----
check(proto.read_json_object(put("ok.json", {"a": 1})) == {"a": 1}, "an object is returned as a dict")
for name, content in (("list.json", [1, 2]), ("num.json", 5), ("bad.json", "{not json")):
    path = put(name, content)
    raises(ValueError, lambda: proto.read_json_object(path), f"{name}: refused with ValueError")
raises(FileNotFoundError, lambda: proto.read_json_object(os.path.join(TMP, "missing.json")), "a missing file raises FileNotFoundError")

# ---- gyro_cal.json: a bad entry does not throw away the good ones ----
C.GYRO_CAL_FILE = put("gyro_cal.json", {"AA:BB:CC:DD:EE:01": {"bias": [1, 2, 3], "t": 5},
                                        "aa:bb:cc:dd:ee:02": {"bias": [1, 2]},
                                        "aa:bb:cc:dd:ee:03": "junk",
                                        "aa:bb:cc:dd:ee:04": {"bias": [1, "x", 3]}})
calib._saved = None
check(calib.stored("aa:bb:cc:dd:ee:01") == {"bias": [1.0, 2.0, 3.0], "t": 5}, "gyro_cal.json: the good entry loads")
check(all(calib.stored(f"aa:bb:cc:dd:ee:0{n}") is None for n in (2, 3, 4)), "gyro_cal.json: malformed entries are skipped")
C.GYRO_CAL_FILE = put("gyro_cal2.json", [1, 2, 3])
calib._saved = None
check(calib.stored("aa:bb:cc:dd:ee:01") is None, "gyro_cal.json that is not an object: nothing loaded, no exception")

# ---- slots.json ----
C.SLOTS_FILE = put("slots.json", {"aa:bb:cc:dd:ee:01": 1, "aa:bb:cc:dd:ee:02": "x", "aa:bb:cc:dd:ee:03": 99,
                                  "aa:bb:cc:dd:ee:04": True})
state.saved_slots = None
check(sup._load_slots() == {"aa:bb:cc:dd:ee:01": 1}, f"slots.json: only valid slot numbers stay ({state.saved_slots})")
C.SLOTS_FILE = put("slots2.json", "null")
state.saved_slots = None
check(sup._load_slots() == {}, "slots.json with null: empty table, no exception")

sys.exit(1 if fails else 0)
