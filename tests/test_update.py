"""Receiver: aktualizacja (rx_update) na prawdziwych repozytoriach git w katalogu tymczasowym."""
import os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
import rx_update as U

ok = lambda m: print("OK  ", m)
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout


def commit(repo, path, text, msg):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", msg)


os.environ.update({k: ENV[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")})
tmp = tempfile.mkdtemp()
origin, work = os.path.join(tmp, "origin"), os.path.join(tmp, "work")
os.makedirs(origin)
git(origin, "init", "-q", "-b", "main")
commit(origin, "receiver/rx_x.py", "a = 1\n", "start")
commit(origin, "receiver/requirements.txt", "vgamepad\n", "reqs")
git(tmp, "clone", "-q", origin, work)

r = U.update(work, pip=False)
assert r.ok and not r.changed and "najnowszą" in r.text, r
ok("brak zmian: 'Masz najnowszą wersję', bez restartu")

commit(origin, "receiver/rx_x.py", "a = 2\n", "fix")
commit(origin, "receiver/rx_y.py", "b = 1\n", "feat")
r = U.update(work, pip=False)
assert r.ok and r.changed and "(2 " in r.text and r.old[:7] in r.text and r.new[:7] in r.text, r
assert open(os.path.join(work, "receiver/rx_x.py")).read() == "a = 2\n"
ok("nowe commity: pobrane, liczba zmian i wersje w komunikacie, restart potrzebny")

called = []
U._install_requirements = lambda root: called.append(root) or "nowe biblioteki zainstalowane"
commit(origin, "receiver/requirements.txt", "vgamepad\npycaw\n", "deps")
r = U.update(work)
assert r.changed and called == [work] and "biblioteki" in r.text, r
commit(origin, "receiver/rx_x.py", "a = 3\n", "no deps")
called.clear(); U.update(work)
assert not called, "pip tylko przy zmianie requirements.txt"
ok("zmiana requirements.txt -> pip install, bez zmiany - bez pip")

with open(os.path.join(work, "remap.json"), "w") as f:
    f.write("{}")                           # plik użytkownika (nieśledzony) nie przeszkadza
commit(work, "receiver/rx_x.py", "lokalnie\n", "local")
commit(origin, "receiver/rx_x.py", "a = 4\n", "upstream")
r = U.update(work, pip=False)
assert not r.ok and not r.changed and "git pull" in r.text, r
assert open(os.path.join(work, "receiver/rx_x.py")).read() == "lokalnie\n", "nic nie nadpisane ani scalone"
ok("lokalne zmiany rozjechane z GitHubem: błąd gita, pliki nietknięte")

r = U.update(tmp, pip=False)
assert not r.ok and "ZIP" in r.text
U.find_git, real = (lambda: None), U.find_git
r = U.update(work, pip=False)
U.find_git = real
assert not r.ok and "Git for Windows" in r.text
assert U.ROOT == os.path.dirname(os.path.abspath(sys.argv[1])), "repozytorium = folder nad receiver/"
ok("folder bez .git (ZIP) i brak programu git: czytelny komunikat")

from proto import UPD_FAILED, UPD_NO_CHANGE
b = ("a1b2c3d", 0, UPD_FAILED)
assert "szczegóły w logu" in U.sender_text(b), "starszy nadajnik bez powodu: odesłanie do logu"
t = U.sender_text(b, "git@github.com: Permission denied (publickey).")
assert "Permission denied (publickey)" in t and "setup-update-key.sh" in t, t
t = U.sender_text(b, "fatal: unable to access: Could not resolve host: github.com")
assert "Could not resolve host" in t and "setup-update-key.sh" not in t, "błąd sieci - bez podpowiedzi o kluczu"
assert "najnowsza" in U.sender_text(("a1b2c3d", 0, UPD_NO_CHANGE), "stary powód"), "powód tylko przy błędzie"
ok("wynik aktualizacji nadajnika: powód błędu z telefonu w oknie, podpowiedź klucza tylko przy braku dostępu")
# ---------- sprawdzanie GitHuba w tle: ile zmian czeka (bez pobierania kodu) ----------
chk = os.path.join(tmp, "chk")
git(tmp, "clone", "-q", origin, chk)
a = U.check_available(chk)
assert a == U.Available(0), a
commit(origin, "receiver/rx_x.py", "a = 5\n", "Pierwsza nowa")
commit(origin, "receiver/rx_y.py", "b = 2\n", "Druga nowa: tryb X")
before = open(os.path.join(chk, "receiver/rx_x.py")).read()
a = U.check_available(chk)
assert a.behind == 2 and a.subject == "Druga nowa: tryb X" and len(a.commit) == 7, a
assert open(os.path.join(chk, "receiver/rx_x.py")).read() == before, "sprawdzenie nie zmienia plików"
git(chk, "checkout", "-q", "--detach")
assert U.check_available(chk) is None, "bez gałęzi śledzącej -> brak informacji (nie błąd)"
assert U.check_available(tmp) is None, "nie klon -> brak informacji"
git(chk, "remote", "set-url", "origin", os.path.join(tmp, "nie-ma"))
assert U.check_available(chk) is None, "brak sieci / repo -> brak informacji"
ok("sprawdzanie GitHuba: liczba zmian i tytuł najnowszej, bez zmiany plików; błędy -> brak informacji")
print("UPDATE OK")
