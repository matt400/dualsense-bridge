"""Receiver: bad config input (a hand-edited remap.json, a numeric HMAC key) does not break it.
    python3 tests/test_input_parsing_edges.py receiver"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_buttons as B, rx_config, rx_profiles as P

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.01)
    return pred()


# ---------- remap.json / API: zły typ nie wywraca receivera ----------
for bad in ({"buttons": [1, 2]}, {"sticks": ["left"]}, {"buttons": "x", "sticks": 5}, [1], "x", None):
    try:
        prof = B.normalize_profile(bad)
        good = prof == {"buttons": {}, "sticks": {}, "tuning": {}, "gyro": {}, "triggers": {}}
    except Exception as e:
        good = False
        print("   ", repr(e))
    check(good, f"normalize_profile({bad!r}) -> pusty profil zamiast wyjątku")
path = os.path.join(TMP, "bad_remap.json")
for content in ({"profiles": [1, 2]}, {"profiles": "x"}, [1]):
    json.dump(content, open(path, "w"))
    try:
        st = B.RemapStore(path)
        good = st.saved == {}
    except Exception as e:
        good = False
        print("   ", repr(e))
    check(good, f"remap.json {content!r} -> brak profili zamiast awarii przy starcie")
ok_, _msg = P.apply(0, S.profile_key(0), {"buttons": ["cross"], "sticks": [1]})
check(ok_ and S.remap_maps[0]["buttons"] == {}, "zły profil (np. ręcznie edytowany remap.json) nie wywraca zastosowania")

# ---------- klucz HMAC liczbą w .toml / .json ----------
cfg = os.path.join(TMP, "key.json")
json.dump({"key": 1234}, open(cfg, "w"))
k = rx_config.init(["--config", cfg]).KEY
rx_config.init(["--pads", "2", "--remap-file", C.REMAP_PATH])
check(k == b"1234", f"key = 1234 (liczba) -> b'1234' jak w nadajniku, a nie AttributeError: {k!r}")

sys.exit(1 if fails else 0)
