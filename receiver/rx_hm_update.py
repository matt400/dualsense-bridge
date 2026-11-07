r"""Receiver: the pinned HIDMaestro (hifihedgehog/HIDMaestro), downloaded from GitHub Releases when it is not on disk yet.

A release is a ~160 MB zip (SDK + test applications), and the receiver needs only HIDMaestro.Core.dll (the driver is inside it)
and two Windows SDK projection DLLs (plus LICENSE from its folder - the licence travels with every version).
The zip is read through HTTP Range: the zip directory from the end of the file, then only those entries, each in one request
(prefetch): GitHub's signed link to the storage expires after ~5 min, so a request per 64 KiB block (~900 TLS connections)
would not make it on a slow link.
zipfile checks the CRC32 of every entry, and the whole version goes to its folder atomically (tmp + rename).

Protection against a malicious or broken release (a hijacked author account, a swapped asset): the DLL is loaded into the
administrator process and installs a driver, and a release has no Authenticode signature. So the receiver never follows
`releases/latest`: hm_pin.json fixes the version (the download link follows from the tag) and the SHA-256 of every file taken
from the zip, and a file that does not match is refused. A newer HIDMaestro arrives only with a reviewed commit that moves the pin
(tools/hm_pin.py computes it); a receiver that pulls that commit installs the new version at its next check. To try another
version on one machine, point Settings -> Pads at its DLL.

  receiver/hm_pin.json                              the pin: {"tag": "v2.1.0", "files": {name: sha256}}
  receiver/hidmaestro/v2.1.0/HIDMaestro.Core.dll    a version in its own folder: a newer one does not overwrite a DLL loaded
                                                    in the running process (the CLR holds the file) - it works after a restart

  read_pin(path)    parse and validate a pin file
  newest_dll()      the DLL of the newest downloaded version or None (rx_hm.candidates)
  check()           make sure the pinned version is on disk (download it when it is not) -> Status
  thread()          the receiver thread: a check after startup and then hourly (a failed download is retried)
"""
from __future__ import annotations

import hashlib
import io
import os
import posixpath
import re
import shutil
import threading
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final

from proto import read_json_object
from rx_config import HERE

REPO: Final = "hifihedgehog/HIDMaestro"
DLL: Final = "HIDMaestro.Core.dll"
DEPS: Final = ("Microsoft.Windows.SDK.NET.dll", "WinRT.Runtime.dll")   # Windows SDK projections (TFM net10.0-windows10.0.26100)
# the HIDMaestro licence from the DLL folder in the zip (optional - older releases may not have it)
LICENSE: Final = "LICENSE"
PIN_FILE: Final = "hm_pin.json"
START_DELAY_S: Final = 20                   # after the receiver starts: pads and the window first, then the network
TIMEOUT_S: Final = 30
CHUNK: Final = 1 << 16
WATCH_EVERY_S: Final = 3600          # the thread checks hourly: a failed download is retried, a settings change is noticed
ZIP_LOCAL_HEADER_LEN: Final = 30
# a zip entry up to this many bytes is fetched in one request (in memory); a larger one in pieces
HTTP_PARTIAL: Final = 206
PREFETCH_MAX: Final = 128 << 20
LOCAL_SLACK: Final = 1 << 12                # an entry's local header can be longer than in the zip directory (the extra field)
UA: Final = {"User-Agent": "dualsense-bridge"}
_TAG: Final = re.compile(r"^v?(\d+(?:\.\d+)*)$")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class Status:
    """What the updater knows: the downloaded version, the pinned one and the progress."""

    version: str = ""        # the downloaded version (the newest in the folder), "" = none
    latest: str = ""         # the version to have: the pinned one, "" = no valid pin
    busy: bool = False       # a download is running
    error: str = ""          # the last error (pin file, network, zip) - "" = OK


status = Status()
_lock = threading.Lock()
# fn(version) after a new version is downloaded (rx_ds4: HIDMaestro from the next pad connection)
on_installed: Callable[[str], None] | None = None


def root() -> str:
    """Return the folder holding the downloaded HIDMaestro versions."""
    return os.path.join(HERE, "hidmaestro")


def version_key(tag: str | None) -> tuple[int, ...] | None:
    """Turn a version tag ("v2.0.3") into a tuple of ints for ordering, or None when it is not a version."""
    m = _TAG.match(tag or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def version_tuple(tag: str | None) -> tuple[int, ...]:
    """Like version_key, but a non-version sorts before every version (an empty tuple) - for comparisons."""
    return version_key(tag) or ()


@dataclass(frozen=True, slots=True)
class Pin:
    """The pinned HIDMaestro release: its tag and the SHA-256 of every file taken from its zip."""

    tag: str
    sha256: dict[str, str]


def read_pin(path: str) -> Pin:
    """Read and validate a pin file: {"tag": "v2.1.0", "files": {"<file name>": "<sha256>", ...}}.

    The files must include the DLL and its dependencies; every name is a plain file name (it becomes a path in the version folder).

    Raises:
        OSError: The file is missing or unreadable.
        ValueError: The file is not valid JSON or does not describe a pin.
    """
    raw = read_json_object(path)
    tag, files = raw.get("tag"), raw.get("files")
    if not isinstance(tag, str) or version_key(tag) is None:
        raise ValueError(f"tag {tag!r} is not a version")
    if not isinstance(files, dict) or not {DLL, *DEPS} <= files.keys():
        raise ValueError(f"files must list {DLL} and {', '.join(DEPS)}")
    for name, digest in files.items():
        if posixpath.basename(name) != name or "\\" in name or name in ("", ".", ".."):
            raise ValueError(f"{name!r} is not a plain file name")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError(f"{name}: not a SHA-256 (64 lowercase hex digits)")
    return Pin(tag, dict(files))


def _load_pin() -> tuple[str, dict[str, str], str]:
    """Return (tag, hashes, error) from receiver/hm_pin.json; an unusable pin is an error text, never an exception."""
    try:
        pin = read_pin(os.path.join(HERE, PIN_FILE))
    except (OSError, ValueError) as e:
        return "", {}, f"{PIN_FILE}: {e}"
    return pin.tag, pin.sha256, ""


# Plain module variables (tests switch them); PINNED_TAG == "" means there is no usable pin and PIN_ERROR says why.
PINNED_TAG: str
PINNED_SHA256: dict[str, str]
PIN_ERROR: str
PINNED_TAG, PINNED_SHA256, PIN_ERROR = _load_pin()


def installed() -> list[tuple[str, str]]:
    """Return the downloaded versions, newest first: [(tag, folder)]; folders without the DLL (interrupted downloads) do not count."""
    try:
        names = os.listdir(root())
    except OSError:
        return []
    found = [(n, os.path.join(root(), n)) for n in names
             if version_key(n) and os.path.isfile(os.path.join(root(), n, DLL))]
    return sorted(found, key=lambda t: version_tuple(t[0]), reverse=True)


def newest_dll() -> str | None:
    """Return the path of the DLL of the newest downloaded version, or None."""
    found = installed()
    return os.path.join(found[0][1], DLL) if found else None


def in_use() -> str | None:
    """Return the folder of the HIDMaestro version loaded in this process, or None.

    The CLR locks only files already loaded - the dependencies (Microsoft.Windows.SDK.NET.dll, WinRT.Runtime.dll) are
    loaded only on the first use of their types.
    """
    import rx_hm                              # rx_hm imports this module - import here
    b = rx_hm.loaded()
    path = getattr(b, "path", None)
    return os.path.normcase(os.path.dirname(os.path.abspath(path))) if path else None


def cleanup(keep: str) -> None:
    """Remove older versions and leftovers of interrupted downloads.

    The folder of the version loaded in this process stays until a restart: rmtree would remove its unlocked DLL
    dependencies (loaded lazily), and the HIDMaestro already loaded would crash on the next pad connection.
    Locked files stay until next time.
    """
    busy = in_use()
    for n in os.listdir(root()) if os.path.isdir(root()) else ():
        p = os.path.join(root(), n)
        if (os.path.isdir(p) and n != keep and (version_key(n) or n.startswith("."))
                and os.path.normcase(os.path.abspath(p)) != busy):
            shutil.rmtree(p, ignore_errors=True)


def _get(url: str, headers: Mapping[str, str] | None = None, timeout: float = TIMEOUT_S) -> Any:
    """Make an HTTP GET with the project's User-Agent; returns the response (usable as a context manager)."""
    return urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=timeout)


def zip_url(tag: str) -> str:
    """Return the download link of the x64 zip of release `tag` (what the GitHub API calls browser_download_url)."""
    return f"https://github.com/{REPO}/releases/download/{tag}/HIDMaestro-{tag}.zip"


def pinned_release() -> tuple[str, str]:
    """Return (tag, x64 zip url) of the pinned release. The DLL is identical in the x64 and arm64 zips - we take the plain one.

    Raises:
        OSError: There is no usable pin (PIN_ERROR says why).
    """
    if not PINNED_TAG:
        raise OSError(PIN_ERROR or "no pinned HIDMaestro version")
    return PINNED_TAG, zip_url(PINNED_TAG)


class HttpRange(io.RawIOBase):
    """A remote file readable with seeking (HTTP Range) - zipfile reads the directory from the end and single entries.

    The address after GitHub's redirect (a signed link to the storage) is fixed once, the size comes from Content-Range.
    prefetch(start, end) fetches a range in one request; reads from it are served from memory.
    """

    def __init__(self, url: str, opener: Callable[..., Any] | None = None) -> None:
        """Probe the server with a 1-byte Range request: it must answer 206; the final URL and the total size are taken from it."""
        super().__init__()
        self._get = opener = opener or _get
        with opener(url, {"Range": "bytes=0-0"}) as r:
            if r.status != HTTP_PARTIAL:
                raise OSError(f"server ignores Range (HTTP {r.status})")
            self.url = r.geturl()
            self.size = int(r.headers["Content-Range"].rsplit("/", 1)[1])
        self.pos = 0
        self.fetched = 0                     # how many bytes were actually fetched (log, tests)
        self.requests = 1                    # how many HTTP requests (log, tests)
        self._buf, self._buf_at = memoryview(b""), 0

    def _fetch(self, start: int, n: int) -> bytes:
        """One Range request for n bytes at `start` (an error when the server ignores Range)."""
        with self._get(self.url, {"Range": f"bytes={start}-{start + n - 1}"}) as r:
            if r.status != HTTP_PARTIAL:              # 200 = the whole file from the start: the bytes are not from this place
                raise OSError(f"server ignored Range (HTTP {r.status})")
            data = r.read(n)
        self.requests += 1
        self.fetched += len(data)
        return data

    def prefetch(self, start: int, end: int) -> None:
        """Bytes [start, end) in one request; a range that is too large (PREFETCH_MAX) is left to be read in pieces."""
        start, end = max(0, start), min(self.size, end)
        held = self._buf_at <= start and end <= self._buf_at + len(self._buf)
        if end <= start or end - start > PREFETCH_MAX or held:
            return
        self._buf, self._buf_at = memoryview(b""), 0           # the previous entry is no longer needed (memory)
        self._buf, self._buf_at = memoryview(self._fetch(start, end - start)), start

    def readable(self) -> bool:
        """Tell that reading is supported."""
        return True

    def seekable(self) -> bool:
        """Tell that seeking is supported."""
        return True

    def tell(self) -> int:
        """Return the read position."""
        return self.pos

    def seek(self, off: int, whence: int = io.SEEK_SET) -> int:
        """Move the read position (SEEK_SET / SEEK_CUR / SEEK_END); no network traffic."""
        self.pos = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence] + off
        return self.pos

    def readinto(self, b: Any) -> int:
        """Read into b: from the prefetched buffer when the position lies in it, otherwise with a Range request."""
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        off = self.pos - self._buf_at
        data = self._buf[off:off + n] if 0 <= off < len(self._buf) else self._fetch(self.pos, n)
        if not data:
            raise OSError("connection closed before the end of the requested range")
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


def _name(info: zipfile.ZipInfo) -> str:
    r"""Return a zip entry path with forward slashes (the HIDMaestro zip is packed on Windows - the separator is "\")."""
    return info.filename.replace("\\", "/")


def pick(infos: Iterable[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    """Choose the entries to extract.

    The DLL from the zip root (or the first one found) and its WinRT dependencies from the folder of the application
    that ships it (HIDMaestroTest) - without test applications, samples and second copies of the DLL.
    """
    files = [i for i in infos if not i.is_dir()]
    by_name: dict[str, zipfile.ZipInfo] = {}
    for i in sorted(files, key=lambda i: _name(i).count("/")):     # the shallowest first: the DLL from the root directory
        by_name.setdefault(posixpath.basename(_name(i)), i)
    if DLL not in by_name:
        raise OSError(f"{DLL} not in the release zip")
    picked = [by_name[n] for n in (DLL, *DEPS) if n in by_name]
    folder = posixpath.dirname(_name(picked[0]))
    # only from the DLL folder, not someone else's from a subfolder
    lic = next((i for i in files if _name(i) == posixpath.join(folder, LICENSE)), None)
    return picked + ([lic] if lic else [])


def entry_span(info: zipfile.ZipInfo) -> tuple[int, int]:
    """Return (start, end) of a zip entry: the local header and the data (with a margin for a longer extra field)."""
    start = info.header_offset
    header = ZIP_LOCAL_HEADER_LEN + len(info.orig_filename.encode("utf-8")) + len(info.extra)
    return start, start + header + info.compress_size + LOCAL_SLACK


def extract(zf: zipfile.ZipFile, dest: str, prefetch: Callable[[int, int], None] | None = None) -> list[str]:
    """Extract the DLL and its dependencies from an open zip into dest (flat); return the names.

    prefetch(start, end) fetches an entry at once.
    """
    os.makedirs(dest, exist_ok=True)
    names: list[str] = []
    for i in pick(zf.infolist()):
        if prefetch is not None:
            prefetch(*entry_span(i))
        name = posixpath.basename(_name(i))
        with zf.open(i) as src, open(os.path.join(dest, name), "wb") as out:
            shutil.copyfileobj(src, out, CHUNK)          # CRC32 is checked on read (BadZipFile on an error)
        names.append(name)
    with open(os.path.join(dest, DLL), "rb") as f:
        if f.read(2) != b"MZ":
            raise OSError(f"{DLL} is not a Windows DLL")
    return names


def verify(folder: str, tag: str) -> None:
    """Check the files taken from the release against PINNED_SHA256.

    Raises:
        OSError: There are no pinned hashes, a file is missing, or its SHA-256 differs - the caller discards the download.
    """
    if not PINNED_SHA256:
        raise OSError(f"HIDMaestro {tag}: no pinned SHA-256 - not installing")
    for name, want in PINNED_SHA256.items():
        digest = hashlib.sha256()
        with open(os.path.join(folder, name), "rb") as f:
            while block := f.read(CHUNK):
                digest.update(block)
        if digest.hexdigest() != want:
            raise OSError(f"{name} of {tag} does not match the pinned SHA-256 - not installing")


def install(tag: str, url: str, opener: Callable[..., Any] | None = None) -> tuple[str, int]:
    """Download version `tag` into hidmaestro/<tag> (first .<tag>.tmp, then rename); returns (folder, downloaded bytes)."""
    os.makedirs(root(), exist_ok=True)
    final, tmp = os.path.join(root(), tag), os.path.join(root(), f".{tag}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    raw = HttpRange(url, opener)
    try:
        with zipfile.ZipFile(io.BufferedReader(raw, CHUNK)) as zf:
            names = extract(zf, tmp, raw.prefetch)
        verify(tmp, tag)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(f"HIDMaestro {tag} downloaded ({raw.fetched / 1e6:.1f} MB of {raw.size / 1e6:.0f} MB zip, "
          f"{raw.requests} requests): {', '.join(names)}")
    return final, raw.fetched


def _set(**kw: Any) -> None:
    """Replace fields of the immutable status object."""
    global status
    status = replace(status, **kw)


def refresh_status() -> Status:
    """Refresh the status from disk: the downloaded version and the pinned one."""
    have = installed()
    _set(version=have[0][0] if have else "", latest=PINNED_TAG)
    return status


def check() -> Status:
    """Make sure the pinned version is on disk and download it when it is not.

    A failure (no usable pin, network, zip, hash) is kept in status.error and does not touch the downloaded version.
    """
    if not _lock.acquire(blocking=False):
        return status                         # already running (a background thread or the button in the window)
    try:
        _check()
    except Exception as e:  # noqa: BLE001 - a background update must never raise: pin, network, zip and hash errors all end up in status.error
        _set(error=f"{type(e).__name__}: {e}")
        print(f"HIDMaestro update check failed: {e!r}")
    finally:
        _set(busy=False)
        _lock.release()
    return status


def _check() -> None:
    """The check itself (under the lock): install the pinned version unless this or a newer one is already downloaded."""
    refresh_status()
    _set(error="")
    tag, url = pinned_release()
    if status.version and version_tuple(status.version) >= version_tuple(tag):
        return                                # nothing to do, and a pin older than the downloaded version is no downgrade
    _set(busy=True)
    install(tag, url)
    _set(version=tag)
    cleanup(keep=tag)
    if on_installed is not None:
        on_installed(tag)


def thread(stop: threading.Event, enabled: Callable[[], bool]) -> None:
    """Run the receiver thread; enabled() tells whether downloading is allowed (the settings may change meanwhile)."""
    refresh_status()
    if status.version:
        cleanup(keep=status.version)
    if stop.wait(START_DELAY_S):
        return
    while not stop.is_set():
        if enabled():
            check()
        if stop.wait(WATCH_EVERY_S):
            return
