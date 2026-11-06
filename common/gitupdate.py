"""The code version and the git update, shared by the receiver (rx_update) and the sender (sender_update).

Lives in common/ next to proto.py; the entry scripts put common/ on sys.path, so both sides `import gitupdate`.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from typing import Any, Final, NamedTuple

from proto import UPD_DONE, UPD_FAILED, UPD_NO_CHANGE, UPD_NO_GIT, UPD_NOT_GIT

GIT_PATHS: Final = (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe")
GIT_TIMEOUT_S: Final = 15
GIT_PULL_TIMEOUT_S: Final = 120
SSH_CONNECT_TIMEOUT_S: Final = 15
DEFAULT_BRANCH: Final = "main"                          # when the clone has no origin/HEAD
COMMIT_ABBREV_LEN: Final = 7                            # the commit hash length shown and sent in the build packet
# the sender's deploy key (read-only) and its known_hosts - in .git, so they never end up in the repository;
# created by sender/setup-update-key.sh. The service (root -> the repository owner) has no SSH agent or password for the key.
DEPLOY_KEY: Final = os.path.join(".git", "dualsense-deploy-key")
KNOWN_HOSTS: Final = os.path.join(".git", "dualsense-known-hosts")
# the generic ending of every connection error - the real reason is in the lines before it
_GENERIC_ERROR_LINES: Final = ("fatal: could not read from remote repository", "please make sure you have the correct access rights",
                               "and the repository exists")
# the first character of a fetch-summary line ("   a1b2..c3d4  main -> origin/main"): git's flag column (space, +, -, *, !, =, t)
_FETCH_REF_FLAGS: Final = " *+-=!t"
_AUTH_ERROR_MARKERS: Final = ("permission denied (publickey", "host key verification failed", "could not read username",
                              "authentication failed", "repository not found", "terminal prompts disabled", "passphrase")
NO_WINDOW: Final = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # creationflags on Windows: no console window flashing up


class GitVersion(NamedTuple):
    """The code version of a repository."""

    commit: str         # a 7-character commit hash ("" when unknown)
    commit_time: int    # the commit's unix time
    subject: str        # the commit title


class PullResult(NamedTuple):
    """The outcome of :func:`git_pull`."""

    state: int          # an UPD_* constant
    error: str          # git's message on error, otherwise ""
    old_commit: str
    new_commit: str
    changed_files: list[str]


def find_git() -> str | None:
    """Locate the git executable: on PATH first, then the usual Windows install locations; None if not found."""
    if found := shutil.which("git"):
        return found
    per_user = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")
    return next((p for p in (*GIT_PATHS, per_user) if os.path.isfile(p)), None)


def ssh_command(root: str) -> str | None:
    """Build ``GIT_SSH_COMMAND`` for the sender.

    BatchMode means no password prompt (the service has no terminal, git would hang), and the deploy key is used if there is
    one.

    Args:
        root: The repository root, where the deploy key and known_hosts live (in ``.git``).

    Returns:
        The command line, or None to leave git's own setup unchanged (Windows: its own ssh / plink / Git Credential Manager).
    """
    if os.name != "posix" or "GIT_SSH_COMMAND" in os.environ or "GIT_SSH" in os.environ:
        return None
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}"]
    key = os.path.join(root, DEPLOY_KEY)
    if os.path.isfile(key):
        cmd += ["-i", key, "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={os.path.join(root, KNOWN_HOSTS)}",
                "-o", "StrictHostKeyChecking=accept-new"]
    return " ".join(shlex.quote(part) for part in cmd)


def _git_process_kwargs(root: str) -> dict[str, Any]:
    """Return the ``subprocess`` keyword arguments for running git in ``root``.

    On POSIX it disables terminal prompts (https without credentials becomes an error instead of waiting for a terminal;
    on Windows the Git Credential Manager may show a login window) and installs the SSH command. Only when running as root
    on a repository owned by someone else does it add user / group switching (with an empty supplementary group list) and a
    HOME that matches that user: the files after a pull then do not belong to root, and git does not refuse to work in
    someone else's repository ("dubious ownership").
    """
    env = dict(os.environ)
    if os.name == "posix":
        env["GIT_TERMINAL_PROMPT"] = "0"
    if ssh := ssh_command(root):
        env["GIT_SSH_COMMAND"] = ssh
    if os.name != "posix" or sys.platform == "win32" or os.geteuid() != 0:     # (sys.platform: POSIX-only calls below are
        return {"env": env}                                                    # skipped by type checkers on Windows)
    stat = os.stat(root)
    if stat.st_uid == 0:
        return {"env": env}
    import pwd      # POSIX only: imported here so that the module still loads on Windows
    try:
        entry = pwd.getpwuid(stat.st_uid)
        home, name = entry.pw_dir, entry.pw_name
    except KeyError:
        home, name = root, str(stat.st_uid)
    env.update(HOME=home, USER=name, LOGNAME=name)
    # extra_groups=[]: without root's groups (e.g. 0) - the git process has only the owner's privileges
    return {"user": stat.st_uid, "group": stat.st_gid, "extra_groups": [], "env": env}


def is_auth_error(message: str | None) -> bool:
    """Tell whether a git error is an access error to a private repository (SSH key / login).

    The fix is ``sender/setup-update-key.sh``.
    """
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _AUTH_ERROR_MARKERS)


def _is_noise(line: str) -> bool:
    """Tell whether a git output line carries no reason for an error.

    Noise is the generic ending of a connection error, git's hints (``hint:``) and the fetch progress (``From ...``,
    ``   a1b2..c3d4  main -> origin/main``, `` * [new branch] ...``).
    """
    lowered = line.lower()
    return lowered.startswith((*_GENERIC_ERROR_LINES, "hint:", "from ")) or (" -> " in line and line[:1] in _FETCH_REF_FLAGS)


def git_error(text: str | None) -> str:
    """Reduce git's output to the reason for an error, in one line.

    The reason starts at the first ``fatal:`` / ``error:`` line (with the files it concerns); hints, fetch progress and the
    generic "Could not read from remote repository..." are left out.
    """
    raw = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    useful = [line for line in raw if not _is_noise(line)] or raw
    start = next((k for k, line in enumerate(useful) if line.lstrip().lower().startswith(("fatal:", "error:"))), 0)
    return " / ".join(line.strip() for line in useful[start:start + 3])


def git(root: str, *args: str, git_exe: str | None = None, timeout: float = GIT_TIMEOUT_S,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run git in ``root`` (text output; without a console window on Windows).

    Args:
        root: The repository root (the working directory of the process).
        *args: The git arguments.
        git_exe: The git executable; None = :func:`find_git` or plain ``git``.
        timeout: Seconds before the process is killed.
        env: Extra environment variables for git.

    Returns:
        The completed process; the caller checks ``returncode``.
    """
    kwargs = _git_process_kwargs(root)
    if env:
        kwargs["env"] = {**kwargs["env"], **env}
    return subprocess.run([git_exe or find_git() or "git", "-c", "safe.directory=*", *args], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=NO_WINDOW, **kwargs)


def git_version(root: str, git_exe: str | None = None) -> GitVersion:
    """Return the code version in the repository, or ``GitVersion("", 0, "")`` if it cannot be read."""
    try:
        out = git(root, "log", "-1", "--format=%h%x00%ct%x00%s", f"--abbrev={COMMIT_ABBREV_LEN}", git_exe=git_exe)
        commit, commit_time, subject = out.stdout.strip().split("\0", 2)
        return GitVersion(commit[:COMMIT_ABBREV_LEN], int(commit_time), subject)
    except (OSError, ValueError, subprocess.SubprocessError):
        return GitVersion("", 0, "")


def branch_problem(root: str, git_exe: str | None = None) -> str:
    """Tell why a pull would not bring the remote's default branch: a side branch or a detached HEAD.

    ``git pull`` only moves the checked-out branch, so on ``feat/x`` it reports "up to date" while ``main`` moves on.

    Args:
        root: The repository root.
        git_exe: The git executable; None = :func:`find_git`.

    Returns:
        The reason with the command that fixes it, or "" when the default branch is checked out.
    """
    remote_head = git(root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD", git_exe=git_exe).stdout.strip()
    main = remote_head.removeprefix("origin/") or DEFAULT_BRANCH
    head = git(root, "symbolic-ref", "-q", "--short", "HEAD", git_exe=git_exe).stdout.strip()
    if not head:
        return f"detached HEAD, not on {main} - run: git checkout {main}"
    return "" if head == main else f"on branch {head}, not {main} - run: git checkout {main}"


def git_pull(root: str, git_exe: str | None = None, env: dict[str, str] | None = None,
             timeout: float = GIT_PULL_TIMEOUT_S, default_branch_only: bool = False) -> PullResult:
    """Run ``git pull --ff-only``: a fast-forward to the version from GitHub; local changes give an error, nothing is overwritten.

    Args:
        root: The repository root.
        git_exe: The git executable; None = :func:`find_git`.
        env: Extra variables for git. The receiver passes some that suppress login windows - otherwise a pull from a
            background process waited for an invisible Git Credential Manager window until the timeout.
        timeout: Seconds before the pull is given up.
        default_branch_only: Refuse (UPD_FAILED, see :func:`branch_problem`) instead of pulling a side branch.

    Returns:
        The UPD_* state, git's message on error, the old and the new commit and the changed files.
    """
    if not os.path.isdir(os.path.join(root, ".git")):
        return PullResult(UPD_NOT_GIT, "", "", "", [])
    git_exe = git_exe or find_git()
    if git_exe is None:
        return PullResult(UPD_NO_GIT, "", "", "", [])
    try:
        if default_branch_only and (problem := branch_problem(root, git_exe)):
            return PullResult(UPD_FAILED, problem, "", "", [])
        old = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        pull = git(root, "pull", "--ff-only", git_exe=git_exe, timeout=timeout, env=env)
        if pull.returncode:
            return PullResult(UPD_FAILED, git_error(pull.stderr or pull.stdout), old, old, [])
        new = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        if new == old:
            return PullResult(UPD_NO_CHANGE, "", old, new, [])
        changed = git(root, "diff", "--name-only", old, new, git_exe=git_exe).stdout.splitlines()   # names with spaces
    except (OSError, subprocess.SubprocessError) as e:
        return PullResult(UPD_FAILED, str(e), "", "", [])
    return PullResult(UPD_DONE, "", old, new, changed)
