"""Sender: files that can hold the HMAC key keep their permissions.
  - proto.write_file (atomic: temp file + replace) gave the new file the default 0644 - sender.toml (0600, set by
    install-service.sh) became readable by every user after the first config migration, and so did sender.toml.bak
  - a warning in the log when sender.toml holds a key but other users can read it (installs hit by the bug above)
python3 tests/test_file_modes_tx.py sender"""
import contextlib, io, os, stat, sys, tempfile
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import proto, sender_migrate

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)

TMP = tempfile.mkdtemp()
os.umask(0o022)                                   # the usual umask: a fresh file would be 0644

# ---------- 1. write_file keeps the permissions of the file it replaces ----------
for want in (0o600, 0o640, 0o664):
    path = os.path.join(TMP, f"f{want:o}.txt")
    with open(path, "w") as f:
        f.write("old")
    os.chmod(path, want)
    proto.write_file(path, "new")
    check(open(path).read() == "new" and mode(path) == want, f"write_file over a {want:o} file keeps {want:o} ({mode(path):o})")
path = os.path.join(TMP, "fresh.txt")
proto.write_file(path, b"bytes")
check(open(path, "rb").read() == b"bytes" and mode(path) == 0o644, "a new file gets the default permissions")
check(not [n for n in os.listdir(TMP) if n.endswith(".tmp")], "no .tmp left behind")

# ---------- 2. config migration: sender.toml and its backup stay private ----------
toml = os.path.join(TMP, "sender.toml")
with open(toml, "w") as f:
    f.write('host = "192.168.1.4"\nkey = "SECRETKEY"\npad_min_dt = 0.004\n')
os.chmod(toml, 0o600)
with open(toml + ".bak", "w") as f:               # a backup left by an earlier run of the buggy version
    f.write("stale")
os.chmod(toml + ".bak", 0o644)
done = sender_migrate.migrate(toml, log=lambda m: None)
check(done and "pad_min_dt = 0.001" in open(toml).read(), "the old default was migrated")
check(mode(toml) == 0o600, f"sender.toml stays 0600 after the migration ({mode(toml):o})")
check(mode(toml + ".bak") == 0o600 and "SECRETKEY" in open(toml + ".bak").read(),
      f"sender.toml.bak is 0600 too, also when an older wide one existed ({mode(toml + '.bak'):o})")

# ---------- 3. a key in a readable config file is reported ----------
def start(path):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sender_config.init(["--config", path])
    return out.getvalue()

os.chmod(toml, 0o644)
check("readable by other users" in start(toml) and "chmod 600" in start(toml), "key in a 0644 sender.toml: warning with the fix")
os.chmod(toml, 0o600)
check("readable by other users" not in start(toml), "0600 sender.toml: no warning")
nokey = os.path.join(TMP, "nokey.toml")
with open(nokey, "w") as f:
    f.write('host = "192.168.1.4"\n')
check("readable by other users" not in start(nokey), "a 0644 config without a key: no warning")
sender_config.init(["127.0.0.1"])

print("SENDER OK" if not fails else f"FAILED: {len(fails)}")
sys.exit(1 if fails else 0)
