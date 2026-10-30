"""Sender: the sender.toml migration runs once per key.
    python3 tests/test_migrate_once_tx.py sender"""
import os
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_migrate as MIG

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- 8: the config migration runs once per key ----------
toml = os.path.join(TMP, "sender.toml")
open(toml, "w").write("host = \"1.2.3.4\"\npad_min_dt = 0.004\n")
check([d[0] for d in MIG.migrate(toml, log=lambda m: None)] == ["pad_min_dt"], "first run: the old default is migrated")
open(toml, "w").write("host = \"1.2.3.4\"\npad_min_dt = 0.004   # my own value\n")
check(MIG.migrate(toml, log=lambda m: None) == [] and "pad_min_dt = 0.004" in open(toml).read(),
      "later run: a value equal to the old default, set by the user afterwards, is kept")
toml2 = os.path.join(TMP, "other.toml")
open(toml2, "w").write("host = \"1.2.3.4\"\npad_min_dt = 0.002\n")
MIG.migrate(toml2, log=lambda m: None)
open(toml2, "w").write("host = \"1.2.3.4\"\npad_min_dt = 0.004\n")
check(MIG.migrate(toml2, log=lambda m: None) == [], "a file with no old defaults on the first run is also marked as done")

sys.exit(1 if fails else 0)
