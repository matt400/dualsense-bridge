"""Sender: git pull reports changed files whose names contain spaces.
    python3 tests/test_update_changed_files_tx.py sender"""
import os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# git pull: lista zmienionych plików z nazwami ze spacjami (requirements w folderze ze spacją itp.)
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
os.environ.update({k: ENV[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")})
def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, env=ENV, check=True, capture_output=True)
origin, work = os.path.join(TMP, "origin"), os.path.join(TMP, "work")
os.makedirs(origin)
git(origin, "init", "-q", "-b", "main")
open(os.path.join(origin, "a.txt"), "w").write("1")
git(origin, "add", "."); git(origin, "commit", "-qm", "a")
git(TMP, "clone", "-q", origin, work)
open(os.path.join(origin, "my notes.txt"), "w").write("2")
git(origin, "add", "."); git(origin, "commit", "-qm", "b")
st, _msg, _old, _new, files = proto.git_pull(work)
check(st == proto.UPD_DONE and files == ["my notes.txt"], f"zmienione pliki z nazwą ze spacją: {files}")

sys.exit(1 if fails else 0)
