"""Sender: the update refuses a side branch or a detached HEAD instead of reporting "up to date".
    python3 tests/test_update_branch_tx.py sender"""
import os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import gitupdate
import proto

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

TMP = tempfile.mkdtemp()
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
os.environ.update({k: ENV[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")})
def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()
def commit(repo, name):
    open(os.path.join(repo, name), "w").write(name)
    git(repo, "add", "."); git(repo, "commit", "-qm", name)

def setup(default):
    """origin with `default` + feat/x, cloned into work; returns (origin, work)."""
    origin, work = tempfile.mkdtemp(dir=TMP), tempfile.mkdtemp(dir=TMP)
    git(origin, "init", "-q", "-b", default)
    commit(origin, "a")
    git(origin, "branch", "feat/x")
    git(TMP, "clone", "-q", origin, work)
    git(work, "checkout", "-q", "feat/x")
    commit(origin, "b")                                       # the default branch moves on, feat/x does not
    return origin, work

# the case from the phone: on a side branch, a plain pull says "up to date"
origin, work = setup("main")
st, *_ = gitupdate.git_pull(work)
check(st == proto.UPD_NO_CHANGE, "plain pull on a side branch: no change (the bug)")
st, msg, *_ = gitupdate.git_pull(work, default_branch_only=True)
check(st == proto.UPD_FAILED and "feat/x" in msg and "git checkout main" in msg, f"side branch reported: {st} {msg!r}")

# detached HEAD
git(work, "checkout", "-q", "--detach")
st, msg, *_ = gitupdate.git_pull(work, default_branch_only=True)
check(st == proto.UPD_FAILED and "detached" in msg and "git checkout main" in msg, f"detached HEAD reported: {msg!r}")

# back on main: the pull goes through
git(work, "checkout", "-q", "main")
st, msg, old, new, files = gitupdate.git_pull(work, default_branch_only=True)
check(st == proto.UPD_DONE and files == ["b"], f"default branch pulls: {st} {msg!r} {files}")

# the default branch is taken from origin/HEAD (here master), with "main" as the fallback
_o, work = setup("master")
check("git checkout master" in gitupdate.branch_problem(work), "origin/HEAD names the default branch")
git(work, "checkout", "-q", "master")
check(gitupdate.branch_problem(work) == "", "on the default branch: no problem")
git(work, "remote", "set-head", "origin", "-d")
check("not main" in gitupdate.branch_problem(work), "no origin/HEAD: main is assumed")

sys.exit(1 if fails else 0)
