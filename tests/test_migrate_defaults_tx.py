"""Sender: the sender.toml migration changes only old default values and keeps own values, comments and layout.
    python3 tests/test_migrate_defaults_tx.py sender"""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# migracja sender.toml: tylko stare wartości domyślne, własne zostają, komentarze i układ też
import sender_migrate
toml = os.path.join(TMP, "sender.toml")
src = ('host = "192.168.1.4"\n'
       'pad_min_dt = 0.004   # mój komentarz\n'
       '# motion_min_dt = 0.004   # zakomentowane - bez zmian\n'
       'motion_min_dt = 0.002\n'
       'touch_coalesce_ms = 5\n'
       'key = "a b"\n')
open(toml, "w").write(src)
done = sender_migrate.migrate(toml, log=lambda m: None)
got = open(toml).read()
check([d[0] for d in done] == ["pad_min_dt", "touch_coalesce_ms"], f"zmienione tylko stare wartości domyślne: {done}")
check(got == src.replace("pad_min_dt = 0.004   # mój", "pad_min_dt = 0.001   # mój").replace("touch_coalesce_ms = 5", "touch_coalesce_ms = 2"),
      f"komentarze, zakomentowane linie i własne wartości (motion_min_dt = 0.002) bez zmian:\n{got}")
check(open(toml + ".bak").read() == src, "kopia przed zmianą: sender.toml.bak")
check(sender_migrate.migrate(toml, log=lambda m: None) == [], "drugi raz - nic do zmiany")
import tomllib
cfgd = tomllib.loads(got)
check(cfgd["pad_min_dt"] == 0.001 and cfgd["touch_coalesce_ms"] == 2 and cfgd["key"] == "a b", "plik dalej poprawnym TOML")
open(toml, "w").write(src)
os.remove(toml + ".migrated")                     # świeży plik: stan migracji jest per plik
sender_config.init(["--config", toml])
check(sender_config.C.PAD_MIN_DT == 0.001 and sender_config.C.TOUCH_COALESCE_S == 0.002 and sender_config.C.MOTION_MIN_DT == 0.002,
      "start nadajnika migruje sam (po aktualizacji przyciskiem nic nie trzeba robić)")

sys.exit(1 if fails else 0)
