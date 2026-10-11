"""Nadajnik: instalacja usługi (OpenRC / systemd) skryptem install-service.sh i blokada drugiej instancji."""
import os, subprocess, sys, tempfile
TX = os.path.abspath(sys.argv[1])
sys.path.insert(0, TX)
ok = lambda m: print("OK  ", m)
SCRIPT = os.path.join(TX, "install-service.sh")


def run(root, init, *args):
    env = dict(os.environ, ROOT=root, INIT=init)
    return subprocess.run(["sh", SCRIPT, *args], env=env, capture_output=True, text=True)


# ---------- OpenRC ----------
root = tempfile.mkdtemp()
r = run(root, "openrc", "192.168.1.4", "--key", "X")
assert r.returncode == 0, r.stderr
init = open(os.path.join(root, "etc/init.d/dualsense-sender")).read()
conf = open(os.path.join(root, "etc/conf.d/dualsense-sender")).read()
assert init.startswith("#!/sbin/openrc-run") and 'supervisor="supervise-daemon"' in init
assert os.access(os.path.join(root, "etc/init.d/dualsense-sender"), os.X_OK)
assert f'SENDER_DIR="{TX}"' in conf and 'SENDER_ARGS="192.168.1.4 --key X"' in conf, conf
assert "Wyłącz swój poprzedni autostart" in r.stdout
r = run(root, "openrc", "--remove")
assert r.returncode == 0 and not os.listdir(os.path.join(root, "etc/init.d")), r.stderr
ok("OpenRC: init.d + conf.d (katalog i argumenty nadajnika), usuwanie")

# ---------- systemd ----------
root = tempfile.mkdtemp()
r = run(root, "systemd", "192.168.1.4")
assert r.returncode == 0, r.stderr
unit = open(os.path.join(root, "etc/systemd/system/dualsense-sender.service")).read()
assert f"ExecStart=/usr/bin/python3 -u {TX}/sender_controller.py 192.168.1.4" in unit and f"WorkingDirectory={TX}" in unit
assert "@" not in unit and "Restart=always" in unit
ok("systemd: unit z katalogiem i argumentami, restart po awarii")

# ---------- bez sender.toml i bez argumentów ----------
if not os.path.exists(os.path.join(TX, "sender.toml")):
    r = run(tempfile.mkdtemp(), "openrc")
    assert r.returncode == 1 and "sender.toml" in r.stderr, r.stderr
    ok("bez sender.toml i argumentów: czytelny błąd")

# ---------- jedna instancja ----------
import sender_controller as M
name = b"\0dualsense-bridge-test-" + str(os.getpid()).encode()
assert M.single_instance(name) is True
first = M._lock
assert M.single_instance(name) is False, "druga instancja nie startuje"
first.close()
assert M.single_instance(name) is True, "po zakończeniu pierwszej - start możliwy"
ok("blokada drugiej instancji nadajnika (usługa + stary autostart)")
print("SERVICE OK")
