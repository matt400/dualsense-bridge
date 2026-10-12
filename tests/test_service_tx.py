"""Nadajnik: instalacja usługi systemd skryptem install-service.sh i blokada drugiej instancji."""
import os, subprocess, sys, tempfile
TX = os.path.abspath(sys.argv[1])
sys.path.insert(0, TX)
ok = lambda m: print("OK  ", m)
SCRIPT = os.path.join(TX, "install-service.sh")


def run(root, *args):
    env = dict(os.environ, ROOT=root)
    return subprocess.run(["sh", SCRIPT, *args], env=env, capture_output=True, text=True)


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

# ---------- systemd ----------
root = tempfile.mkdtemp()
r = run(root, "192.168.1.4")
assert r.returncode == 0, r.stderr
unit = open(os.path.join(root, "etc/systemd/system/dualsense-sender.service")).read()
assert f'ExecStart=/usr/bin/python3 -u {TX}/sender_controller.py "192.168.1.4"' in unit and f"WorkingDirectory={TX}" in unit
assert "@DIR@" not in unit and "@ARGS@" not in unit and "Restart=always" in unit
assert systemd_argv(unit) == ["/usr/bin/python3", "-u", f"{TX}/sender_controller.py", "192.168.1.4"], systemd_argv(unit)
ok("systemd: unit z katalogiem i argumentami, restart po awarii")
root = tempfile.mkdtemp()
r = run(root, *NASTY)
assert r.returncode == 0, r.stderr
unit = open(os.path.join(root, "etc/systemd/system/dualsense-sender.service")).read()
assert systemd_argv(unit) == ["/usr/bin/python3", "-u", f"{TX}/sender_controller.py", *NASTY], systemd_argv(unit)
assert f"WorkingDirectory={TX}" in unit
assert oct(os.stat(os.path.join(root, "etc/systemd/system/dualsense-sender.service")).st_mode & 0o777) == "0o600", \
    "unit z kluczem tylko dla roota"
assert "Wyłącz swój poprzedni autostart" in r.stdout and "journalctl -u dualsense-sender" in r.stdout
r = run(root, "--remove")
assert r.returncode == 0 and not os.listdir(os.path.join(root, "etc/systemd/system")), r.stderr
ok("systemd: klucz ze spacją, cudzysłowem, $, %, ', \\, &, |, ; i ` przechodzi bez zmian; usuwanie")
r = run(tempfile.mkdtemp(), "192.168.1.4", "--key", "a\nb")
assert r.returncode == 1 and "nową linią" in r.stderr, r.stderr
ok("argument z nową linią: czytelny błąd zamiast zepsutego pliku usługi")

# ---------- bez sender.toml i bez argumentów ----------
if not os.path.exists(os.path.join(TX, "sender.toml")):
    r = run(tempfile.mkdtemp())
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
