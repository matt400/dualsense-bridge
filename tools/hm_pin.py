"""Move the HIDMaestro pin (receiver/hm_pin.json) to a release, or check that the pinned release is still intact.

    python3 tools/hm_pin.py v2.1.1                 # take that release, write the new pin
    python3 tools/hm_pin.py v2.1.1 --verify-zip    # also compare the whole zip (~160 MB) with the SHA-256 in the notes
    python3 tools/hm_pin.py v2.1.1 --no-notes      # skip the cross-check against the notes (only after checking by hand)
    python3 tools/hm_pin.py --check                # the pinned release still matches hm_pin.json? (exit 1 when not)

A new pin downloads only what the receiver takes from the zip (the DLL and its two Windows SDK projections, through HTTP Range, the
same code as the receiver), computes their SHA-256 and compares the DLL with the "SDK SHA-256" in the release notes. The notes
sometimes print only a prefix of it; a prefix of at least 12 digits is compared as a prefix. When the notes cannot be read or have
no hash, nothing is written (--no-notes overrides that). The pin is a reviewed commit: look at the diff, run ./tests/run_tests.sh,
then check a pad on Windows (the first pad creation installs the new driver).

--check is what the weekly workflow runs: if the author deletes or replaces the pinned release, the receiver would refuse it
(or fail to download it), and this tells the maintainer first.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "common"), str(ROOT / "receiver")]

import rx_hm_update as U  # noqa: E402
from proto import write_file  # noqa: E402

PIN_PATH: Final = ROOT / "receiver" / U.PIN_FILE
NOTES_API: Final = f"https://api.github.com/repos/{U.REPO}/releases/tags/"
PINNED_NAMES: Final = (U.DLL, *U.DEPS)
MIN_PREFIX: Final = 12                       # the shortest prefix of the SDK hash that is compared
_SDK_HASH: Final = re.compile(rf"SDK SHA-256\W{{0,12}}([0-9a-f]{{{MIN_PREFIX},64}})", re.IGNORECASE)


def sha256_file(path: str) -> str:
    """Return the SHA-256 of a file as lowercase hex."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(U.CHUNK):
            digest.update(block)
    return digest.hexdigest()


def release_notes(tag: str) -> str:
    """Return the release notes (markdown) of `tag` from the GitHub API.

    Raises:
        OSError: The request failed.
        ValueError: The answer is not JSON.
    """
    with U._get(NOTES_API + tag, {"Accept": "application/vnd.github+json"}) as r:
        return str(json.load(r).get("body") or "")


def notes_sdk_hash(notes: str) -> str | None:
    """Return the "SDK SHA-256" of the notes (lowercase; a prefix of at least MIN_PREFIX digits or all 64), or None."""
    m = _SDK_HASH.search(notes)
    return m.group(1).lower() if m else None


def notes_zip_hash(notes: str, tag: str) -> str | None:
    """Return the SHA-256 the notes print for the x64 zip of `tag` (not the arm64 one), or None."""
    m = re.search(r"SHA-256 of\W{0,3}" + re.escape(f"HIDMaestro-{tag}.zip") + r"\W{0,8}([0-9a-f]{64})", notes, re.IGNORECASE)
    return m.group(1).lower() if m else None


def download(tag: str, dest: str, names: Sequence[str] = PINNED_NAMES) -> dict[str, str]:
    """Fetch the files the receiver takes from release `tag` into `dest`; return {name: sha256} for those of `names` that arrived."""
    raw = U.HttpRange(U.zip_url(tag))
    with zipfile.ZipFile(io.BufferedReader(raw, U.CHUNK)) as zf:
        U.extract(zf, dest, raw.prefetch)
    return {n: sha256_file(os.path.join(dest, n)) for n in names if os.path.isfile(os.path.join(dest, n))}


def zip_sha256(tag: str) -> str:
    """Return the SHA-256 of the whole x64 zip of `tag` (streamed, ~160 MB)."""
    digest = hashlib.sha256()
    with U._get(U.zip_url(tag)) as r:
        while block := r.read(U.CHUNK):
            digest.update(block)
    return digest.hexdigest()


def render_pin(tag: str, hashes: Mapping[str, str]) -> str:
    """Return the text of hm_pin.json for `tag` (the files in the order of PINNED_NAMES)."""
    return json.dumps({"tag": tag, "files": {n: hashes[n] for n in PINNED_NAMES}}, indent=2) + "\n"


def differences(pinned: Mapping[str, str], actual: Mapping[str, str]) -> list[str]:
    """Return one line per pinned file whose SHA-256 in `actual` is different or missing."""
    return [f"{n}: pinned {want}, the release has {actual.get(n, 'nothing')}" for n, want in pinned.items() if actual.get(n) != want]


def check_notes(tag: str, dll_sha: str, verify_zip: bool) -> list[str]:
    """Compare the DLL (and with verify_zip the whole zip) with the hashes in the release notes; return the problems found."""
    try:
        notes = release_notes(tag)
    except (OSError, ValueError) as e:
        return [f"cannot read the release notes: {e}"]
    want = notes_sdk_hash(notes)
    if want is None:
        return ["the release notes have no 'SDK SHA-256'"]
    if not dll_sha.startswith(want):
        return [f"{U.DLL} is {dll_sha}, the release notes say {want}"]
    print(f"{U.DLL} matches the SDK SHA-256 of the release notes ({len(want)} hex digits)")
    if verify_zip:
        zip_want = notes_zip_hash(notes, tag)
        if zip_want is None:
            return ["the release notes have no SHA-256 of the zip"]
        zip_got = zip_sha256(tag)
        if zip_got != zip_want:
            return [f"the zip is {zip_got}, the release notes say {zip_want}"]
        print("the whole zip matches the SHA-256 of the release notes")
    return []


def move_pin(tag: str, pin_path: Path, *, verify_zip: bool, no_notes: bool) -> int:
    """Write the pin for `tag`; return the exit code (0 = written)."""
    if U.version_key(tag) is None:
        print(f"{tag!r} is not a version tag (like v2.1.1)")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        actual = download(tag, tmp)
    missing = [n for n in PINNED_NAMES if n not in actual]
    if missing:
        print(f"the release has no {', '.join(missing)}")
        return 1
    for name, digest in actual.items():
        print(f"{digest}  {name}")
    if not no_notes:
        problems = check_notes(tag, actual[U.DLL], verify_zip)
        for p in problems:
            print(f"not written: {p}")
        if problems:
            print("check the release by hand and run again with --no-notes if it is right")
            return 1
    write_file(str(pin_path), render_pin(tag, actual))
    print(f"{pin_path} now pins {tag}. Review the diff, run ./tests/run_tests.sh, check a pad on Windows.")
    return 0


def check_pin(pin_path: Path) -> int:
    """Download the pinned release and compare it with the pin file; return the exit code (0 = intact)."""
    try:
        pin = U.read_pin(str(pin_path))
    except (OSError, ValueError) as e:
        print(f"{pin_path}: {e}")
        return 1
    try:
        with tempfile.TemporaryDirectory() as tmp:
            actual = download(pin.tag, tmp, tuple(pin.sha256))
    except (OSError, zipfile.BadZipFile) as e:
        print(f"the pinned release {pin.tag} cannot be downloaded: {e}")
        return 1
    bad = differences(pin.sha256, actual)
    for line in bad:
        print(line)
    print(f"{pin.tag}: {'does NOT match' if bad else 'matches'} {pin_path.name}")
    return 1 if bad else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tool; return the process exit code."""
    ap = argparse.ArgumentParser(description="Move the HIDMaestro pin, or check the pinned release.")
    ap.add_argument("tag", nargs="?", help="the release to pin, like v2.1.1")
    ap.add_argument("--check", action="store_true", help="check that the pinned release still matches the pin file")
    ap.add_argument("--verify-zip", action="store_true", help="also compare the whole zip with the SHA-256 in the release notes")
    ap.add_argument("--no-notes", action="store_true", help="do not cross-check the DLL with the release notes")
    ap.add_argument("--pin", type=Path, default=PIN_PATH, help="the pin file (default: receiver/hm_pin.json)")
    args = ap.parse_args(argv)
    if args.check == bool(args.tag):
        ap.error("give a release tag, or --check")
    if args.check:
        return check_pin(args.pin)
    return move_pin(args.tag, args.pin, verify_zip=args.verify_zip, no_notes=args.no_notes)


if __name__ == "__main__":
    sys.exit(main())
