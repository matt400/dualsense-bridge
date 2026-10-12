"""Nadajnik: aktualizacja z prywatnego repozytorium przez SSH z usługi (bez agenta SSH i terminala).

Zamiast GitHuba: atrapa ssh, która jak GitHub wpuszcza tylko klucz dodany jako deploy key (plik "authorized"),
a po wpuszczeniu uruchamia git-upload-pack na lokalnym repozytorium - git pull działa naprawdę.
"""
import asyncio, os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
TX = os.path.abspath(sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import proto, sender_net as net, sender_state as state, sender_update as U
from proto import BODY, T_BUILD, T_UPDMSG, UPD_DONE, UPD_FAILED

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
os.environ.update(ENV)
# bez globalnej / systemowej konfiguracji gita (np. url.insteadOf środowiska CI) - "github.com" to tylko atrapa
os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
for k in [k for k in os.environ if k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) or k == "GIT_CONFIG_COUNT"]:
    del os.environ[k]                                    # konfiguracja z linii poleceń środowiska (url.insteadOf)
tmp = tempfile.mkdtemp()
server, work, fake = (os.path.join(tmp, d) for d in ("server", "work", "bin"))
authorized, log = os.path.join(tmp, "authorized"), os.path.join(tmp, "ssh.log")
open(authorized, "w").close()

def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout

# ---------- atrapa GitHuba ----------
os.makedirs(fake)
with open(os.path.join(fake, "ssh"), "w") as f:
    f.write(f"""#!{sys.executable}
import os, subprocess, sys
a = sys.argv[1:]
open({log!r}, "a").write(repr(a) + "\\n")
key = a[a.index("-i") + 1] if "-i" in a else None
pub = open(key + ".pub").read().split()[1] if key else None
if not pub or pub not in open({authorized!r}).read().split():
    sys.stderr.write("git@github.com: Permission denied (publickey).\\n")
    sys.exit(255)
sys.exit(subprocess.call(["sh", "-c", a[-1]], cwd={server!r}))
""")
os.chmod(os.path.join(fake, "ssh"), 0o755)
os.environ["PATH"] = fake + os.pathsep + os.environ["PATH"]

origin = os.path.join(server, "matt400", "dualsense-bridge.git")
os.makedirs(origin)
git(origin, "init", "-q", "--bare", "-b", "main")
seed = os.path.join(tmp, "seed")
git(tmp, "clone", "-q", origin, seed)
open(os.path.join(seed, "a.txt"), "w").write("1")
git(seed, "add", "."); git(seed, "commit", "-qm", "start"); git(seed, "push", "-q", "origin", "main")
git(tmp, "clone", "-q", origin, work)
git(work, "remote", "set-url", "origin", "https://github.com/matt400/dualsense-bridge.git")   # klon przez https
os.makedirs(os.path.join(work, "sender"))
SCRIPT = os.path.join(work, "sender", "setup-update-key.sh")
with open(os.path.join(TX, "setup-update-key.sh")) as src, open(SCRIPT, "w") as dst:
    dst.write(src.read())

def setup():
    return subprocess.run(["sh", SCRIPT], capture_output=True, text=True)

# ---------- czytelny powód błędu ----------
generic = ("git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.\n\n"
           "Please make sure you have the correct access rights\nand the repository exists.\n")
check(proto.git_error(generic) == "git@github.com: Permission denied (publickey).",
      f"powód błędu bez ogólnego 'Could not read from remote repository': {proto.git_error(generic)!r}")
check(proto.is_auth_error(proto.git_error(generic)) and not proto.is_auth_error("Could not resolve host: github.com"),
      "błąd dostępu rozpoznany (sieć to nie dostęp)")

# ---------- skrypt: klucz, adres SSH, instrukcja ----------
r = setup()
key = os.path.join(work, ".git", "dualsense-deploy-key")
check(r.returncode == 2 and os.path.isfile(key) and oct(os.stat(key).st_mode & 0o777) == "0o600",
      f"pierwsze uruchomienie: klucz bez hasła (600), kod 2 = trzeba dodać na GitHubie: {r.returncode} {r.stderr}")
check(git(work, "remote", "get-url", "origin").strip() == "git@github.com:matt400/dualsense-bridge.git",
      "adres https zamieniony na SSH (klucz wdrożeniowy działa tylko przez SSH)")
pub = open(key + ".pub").read().strip()
check(pub in r.stdout and "github.com/matt400/dualsense-bridge/settings/keys/new" in r.stdout
      and "Allow write access" in r.stdout, "wypisany klucz publiczny i dokładne kroki na GitHubie")
check(not any(".git/" in x for x in git(work, "status", "--porcelain", "--ignored").split()),
      "klucz w .git - nie trafia do repozytorium")

# ---------- usługa przed dodaniem klucza: czytelny błąd, bez czekania na hasło ----------
open(log, "w").close()
st, msg, *_ = proto.git_pull(work)
calls = open(log).read()
check(st == UPD_FAILED and "Permission denied (publickey)" in msg and proto.is_auth_error(msg), f"brak dostępu: {st} {msg!r}")
check("BatchMode=yes" in calls and f"'-i', '{key}'" in calls and "IdentitiesOnly=yes" in calls,
      "git z usługi: ssh bez pytania o hasło, tylko klucz wdrożeniowy")

# ---------- po dodaniu klucza na GitHubie ----------
open(authorized, "w").write(pub.split()[1] + "\n")
r = setup()
check(r.returncode == 0 and r.stdout.startswith("OK"), f"drugie uruchomienie: dostęp potwierdzony: {r.returncode} {r.stdout}{r.stderr}")
open(os.path.join(seed, "a.txt"), "w").write("2")
git(seed, "commit", "-qam", "nowa wersja"); git(seed, "push", "-q", "origin", "main")
st, msg, old, new, files = proto.git_pull(work)
check(st == UPD_DONE and files == ["a.txt"] and open(os.path.join(work, "a.txt")).read() == "2",
      f"git pull z usługi przez klucz wdrożeniowy: {st} {msg!r} {files}")

# ---------- powód błędu dochodzi do okna na PC ----------
open(authorized, "w").close()
sent = []
net.emit = lambda t, i, *v: sent.append((t, i, *v))
U.ROOT = work
U._last, U.status = 0.0, 0


async def update():
    state.main_task = asyncio.get_running_loop().create_future()
    await U.run()

asyncio.run(update())
kinds = [s[0] for s in sent]
msgs = [s[2].rstrip(b"\0").decode() for s in sent if s[0] == T_UPDMSG]
check(msgs and "Permission denied (publickey)" in msgs[0] and BODY[T_UPDMSG].size == 120,
      f"pakiet z powodem błędu: {msgs[:1]}")
check(kinds.index(T_UPDMSG) < len(kinds) - 1 - kinds[::-1].index(T_BUILD) and sent[-1][4] == UPD_FAILED,
      "powód wysłany przed pakietem build z wynikiem")
long = "ż" * 200
U.ROOT = os.path.join(tmp, "nie-ma")                                    # nie klon -> bez powodu
sent.clear(); U._last = 0.0
asyncio.run(update())
check(not [s for s in sent if s[0] == T_UPDMSG], "bez błędu gita - bez pakietu z powodem")
check(len(long.encode()[:120].decode("utf-8", "ignore")) == 60, "długi powód ucięty bez psucia UTF-8 (odbiornik: ignore)")

# ---------- własne GIT_SSH_COMMAND użytkownika ma pierwszeństwo ----------
os.environ["GIT_SSH_COMMAND"] = "ssh -F /dev/null"
check(proto.ssh_command(work) is None, "GIT_SSH_COMMAND z otoczenia nie jest nadpisywany")
del os.environ["GIT_SSH_COMMAND"]

print("WSZYSTKO OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
