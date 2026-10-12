"""Nadajnik: instalacja usługi (OpenRC / systemd) skryptem install-service.sh i blokada drugiej instancji."""
import os, subprocess, sys, tempfile
TX = os.path.abspath(sys.argv[1])
sys.path.insert(0, TX)
ok = lambda m: print("OK  ", m)
SCRIPT = os.path.join(TX, "install-service.sh")


def run(root, init, *args):
    env = dict(os.environ, ROOT=root, INIT=init)
    return subprocess.run(["sh", SCRIPT, *args], env=env, capture_output=True, text=True)


def openrc_argv(root):
    """Argumenty nadajnika tak, jak zobaczy je OpenRC: conf.d wczytany przez powłokę, command_args z init.d
    przepuszczone przez eval (jak w supervise-daemon.sh)."""
    init_d = os.path.join(root, "etc/init.d/dualsense-sender")
    conf_d = os.path.join(root, "etc/conf.d/dualsense-sender")
    cmd = (f". '{conf_d}'; " + "\n".join(l for l in open(init_d).read().splitlines()
                                      if l.startswith((": ", "command_args=")))
           + '\neval "set -- $command_args"; for a in "$@"; do printf "%s\\0" "$a"; done')
    out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, check=True).stdout
    return out.split("\0")[:-1]


def systemd_argv(unit):
    """ExecStart wg reguł systemd: słowa w "..." z \\ i \\", %% -> %, $$ -> $."""
    line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))[len("ExecStart="):]
    words, cur, i, quoted, has = [], "", 0, False, False
    while i < len(line):
        ch = line[i]
        if quoted:
            if ch == "\\":
                i += 1; cur += line[i]
            elif ch == '"':
                quoted = False
            else:
                cur += ch
        elif ch == '"':
            quoted = has = True
        elif ch == " ":
            if cur or has:
                words.append(cur)
            cur, has = "", False
        else:
            cur += ch; has = True
        i += 1
    if cur or has:
        words.append(cur)
    return [w.replace("%%", "%").replace("$$", "$") for w in words]


NASTY = ["192.168.1.4", "--key", "my \"sekret\" $HOME %h 'q' \\x & | ;`id`"]

# ---------- OpenRC ----------
root = tempfile.mkdtemp()
r = run(root, "openrc", "192.168.1.4", "--key", "X")
assert r.returncode == 0, r.stderr
init = open(os.path.join(root, "etc/init.d/dualsense-sender")).read()
conf = open(os.path.join(root, "etc/conf.d/dualsense-sender")).read()
assert init.startswith("#!/sbin/openrc-run") and 'supervisor="supervise-daemon"' in init
assert os.access(os.path.join(root, "etc/init.d/dualsense-sender"), os.X_OK)
assert openrc_argv(root) == ["-u", f"{TX}/sender_controller.py", "192.168.1.4", "--key", "X"], openrc_argv(root)
assert oct(os.stat(os.path.join(root, "etc/conf.d/dualsense-sender")).st_mode & 0o777) == "0o600", "conf.d z kluczem tylko dla roota"
assert "Wyłącz swój poprzedni autostart" in r.stdout
r = run(root, "openrc", "--remove")
assert r.returncode == 0 and not os.listdir(os.path.join(root, "etc/init.d")), r.stderr
ok("OpenRC: init.d + conf.d (katalog i argumenty nadajnika), usuwanie")
root = tempfile.mkdtemp()
r = run(root, "openrc", *NASTY)
assert r.returncode == 0, r.stderr
assert openrc_argv(root) == ["-u", f"{TX}/sender_controller.py", *NASTY], openrc_argv(root)
ok("OpenRC: klucz ze spacją, cudzysłowem, $, %, ', \\, &, |, ; i ` przechodzi bez zmian")

# ---------- systemd ----------
root = tempfile.mkdtemp()
r = run(root, "systemd", "192.168.1.4")
assert r.returncode == 0, r.stderr
unit = open(os.path.join(root, "etc/systemd/system/dualsense-sender.service")).read()
assert f'ExecStart=/usr/bin/python3 -u {TX}/sender_controller.py "192.168.1.4"' in unit and f"WorkingDirectory={TX}" in unit
assert "@DIR@" not in unit and "@ARGS@" not in unit and "Restart=always" in unit
assert systemd_argv(unit) == ["/usr/bin/python3", "-u", f"{TX}/sender_controller.py", "192.168.1.4"], systemd_argv(unit)
ok("systemd: unit z katalogiem i argumentami, restart po awarii")
root = tempfile.mkdtemp()
r = run(root, "systemd", *NASTY)
assert r.returncode == 0, r.stderr
unit = open(os.path.join(root, "etc/systemd/system/dualsense-sender.service")).read()
assert systemd_argv(unit) == ["/usr/bin/python3", "-u", f"{TX}/sender_controller.py", *NASTY], systemd_argv(unit)
assert f"WorkingDirectory={TX}" in unit
ok("systemd: klucz ze znakami specjalnymi przechodzi bez zmian (cudzysłowy, %%, $$)")
r = run(tempfile.mkdtemp(), "openrc", "192.168.1.4", "--key", "a\nb")
assert r.returncode == 1 and "nową linią" in r.stderr, r.stderr
ok("argument z nową linią: czytelny błąd zamiast zepsutego pliku usługi")

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
