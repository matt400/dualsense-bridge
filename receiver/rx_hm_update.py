"""Receiver: HIDMaestro downloaded and updated by itself from GitHub Releases (hifihedgehog/HIDMaestro).

A release is a ~160 MB zip (SDK + test applications), and the receiver needs only HIDMaestro.Core.dll (the driver is inside it)
and two Windows SDK projection DLLs (plus LICENSE from its folder - the licence travels with every version).
The zip is read through HTTP Range: the zip directory from the end of the file, then only those entries -
about 56 MB is downloaded instead of 160, each entry in one request (prefetch). Earlier every 64 KiB was a separate HTTPS
request (~900 TLS connections), and GitHub's signed link to the storage expires after ~5 min - a slow link did not make it in time.
zipfile checks the CRC32 of every entry, and the whole version goes to its folder atomically (tmp + rename).

Protection against a malicious release (a hijacked author account): the DLL is loaded into the administrator process and installs a
driver, and a release has no Authenticode signature or checksums. So a new version waits MIN_AGE_DAYS after
publication (hm_min_age_days in receiver.toml, 0 = at once) - replaced releases usually disappear within days. The first
installation (no version at all) does not wait.

  receiver/hidmaestro/v2.0.3/HIDMaestro.Core.dll   a version in its own folder: a newer one does not overwrite a DLL loaded
                                                    in the running process (the CLR holds the file) - it works after a restart
  receiver/hidmaestro/state.json                    when it was last checked and what the result was

  newest_dll()      the DLL of the newest downloaded version or None (rx_hm.candidates)
  check(force)      one check (at most every CHECK_S, unless force) -> Status
  thread()          the receiver thread: a check after startup and then once a day
"""
import io
import json
import os
import posixpath
import re
import shutil
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass

REPO = "hifihedgehog/HIDMaestro"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
DLL = "HIDMaestro.Core.dll"
DEPS = ("Microsoft.Windows.SDK.NET.dll", "WinRT.Runtime.dll")   # Windows SDK projections (TFM net10.0-windows10.0.26100)
LICENSE = "LICENSE"                  # the HIDMaestro licence from the DLL folder in the zip (optional - older releases may not have it)
CHECK_S = 24 * 3600
MIN_AGE_DAYS = 7                     # a new version installs only this many days after publication (see above)
START_DELAY_S = 20                   # after the receiver starts: pads and the window first, then the network
TIMEOUT_S = 30
CHUNK = 1 << 16
PREFETCH_MAX = 128 << 20             # a zip entry up to this many bytes is fetched in one request (in memory); a larger one in pieces
LOCAL_SLACK = 1 << 12                # an entry's local header can be longer than in the zip directory (the extra field)
UA = {"User-Agent": "dualsense-bridge", "Accept": "application/vnd.github+json"}
_TAG = re.compile(r"^v?(\d+(?:\.\d+)*)$")


@dataclass(frozen=True, slots=True)
class Status:
    version: str = ""        # the downloaded version (the newest in the folder), "" = none
    latest: str = ""         # the newest on GitHub at the last check
    busy: bool = False       # a check / download is running
    error: str = ""          # the last error (network, zip) - "" = OK
    checked: float = 0.0     # time.time() of the last check
    held_until: float = 0.0  # the newer version (latest) waits until this moment (time.time()) - MIN_AGE_DAYS; 0 = not waiting


status = Status()
_lock = threading.Lock()
on_installed = None          # fn(version) after a new version is downloaded (rx_ds4: HIDMaestro from the next pad connection)


def root():
    """The folder holding the downloaded HIDMaestro versions."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "hidmaestro")


def version_key(tag):
    """A version tag ("v2.0.3") -> a tuple of ints for ordering, or None when it is not a version."""
    m = _TAG.match(tag or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def installed():
    """Downloaded versions, newest first: [(tag, folder)] - only folders with the DLL (an interrupted download does not count)."""
    try:
        names = os.listdir(root())
    except OSError:
        return []
    found = [(n, os.path.join(root(), n)) for n in names
             if version_key(n) and os.path.isfile(os.path.join(root(), n, DLL))]
    return sorted(found, key=lambda t: version_key(t[0]), reverse=True)


def newest_dll():
    """The path of the DLL of the newest downloaded version, or None."""
    found = installed()
    return os.path.join(found[0][1], DLL) if found else None


def in_use():
    """The folder of the HIDMaestro version loaded in this process, or None. The CLR locks only files already loaded - the dependencies
    (Microsoft.Windows.SDK.NET.dll, WinRT.Runtime.dll) are loaded only on the first use of their types.
    """
    import rx_hm                              # rx_hm imports this module - import here
    b = rx_hm.loaded()
    path = getattr(b, "path", None)
    return os.path.normcase(os.path.dirname(os.path.abspath(path))) if path else None


def cleanup(keep) -> None:
    """Older versions and leftovers of interrupted downloads. The folder of the version loaded in this process stays until a restart:
    rmtree removed unlocked DLL dependencies from it (loaded lazily) - a HIDMaestro loaded before the update
    crashed on the next pad connection (a missing file). Locked files stay until next time.
    """
    busy = in_use()
    for n in os.listdir(root()) if os.path.isdir(root()) else ():
        p = os.path.join(root(), n)
        if (os.path.isdir(p) and n != keep and (version_key(n) or n.startswith("."))
                and os.path.normcase(os.path.abspath(p)) != busy):
            shutil.rmtree(p, ignore_errors=True)


def _state_path():
    """The file with the time and result of the last check."""
    return os.path.join(root(), "state.json")


def _load_state():
    """-> (time of the last check, the latest tag seen); (0.0, "") when there is no readable state."""
    try:
        with open(_state_path(), encoding="utf-8") as f:
            raw = json.load(f)
        return float(raw.get("checked", 0)), str(raw.get("latest", ""))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0, ""


def _save_state(checked, latest) -> None:
    """Save the check state (best effort - an I/O error is ignored)."""
    try:
        os.makedirs(root(), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"checked": checked, "latest": latest}, f)
        os.replace(tmp, _state_path())
    except OSError:
        pass


def _get(url, headers=None, timeout=TIMEOUT_S):
    """An HTTP GET with the project's User-Agent; returns the response (usable as a context manager)."""
    return urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=timeout)


def _published(rel):
    """The release publication time (time.time()) or 0 (missing / unreadable)."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(rel.get("published_at")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def latest_release():
    """-> (tag, x64 zip url, publication time or 0). The DLL is identical in the x64 and arm64 zips - we take the plain one."""
    with _get(API) as r:
        rel = json.load(r)
    tag = rel.get("tag_name", "")
    zips = [a for a in rel.get("assets", ()) if a.get("name", "").lower().endswith(".zip")]
    asset = next((a for a in zips if "arm64" not in a["name"].lower()), zips[0] if zips else None)
    if not version_key(tag) or asset is None or rel.get("draft") or rel.get("prerelease"):
        raise OSError(f"unexpected release data ({tag!r}, {len(zips)} zip)")
    return tag, asset["browser_download_url"], _published(rel)


def min_age_s():
    """How long a new release must wait before installing, in seconds (hm_min_age_days from the configuration)."""
    from rx_config import C
    return max(0.0, float(getattr(C, "HM_MIN_AGE_DAYS", MIN_AGE_DAYS))) * 86400


class HttpRange(io.RawIOBase):
    """A remote file readable with seeking (HTTP Range) - zipfile reads the directory from the end and single entries.
    The address after GitHub's redirect (a signed link to the storage) is fixed once, the size comes from Content-Range.
    prefetch(start, end) fetches a range in one request; reads from it are served from memory.
    """

    def __init__(self, url, opener=None) -> None:
        """Probe the server with a 1-byte Range request: it must answer 206; the final URL and the total size are taken from it."""
        super().__init__()
        self._get = opener = opener or _get
        with opener(url, {"Range": "bytes=0-0"}) as r:
            if r.status != 206:
                raise OSError(f"server ignores Range (HTTP {r.status})")
            self.url = r.geturl()
            self.size = int(r.headers["Content-Range"].rsplit("/", 1)[1])
        self.pos = 0
        self.fetched = 0                     # how many bytes were actually fetched (log, tests)
        self.requests = 1                    # how many HTTP requests (log, tests)
        self._buf, self._buf_at = memoryview(b""), 0

    def _fetch(self, start, n):
        """One Range request for n bytes at `start` (an error when the server ignores Range)."""
        with self._get(self.url, {"Range": f"bytes={start}-{start + n - 1}"}) as r:
            if r.status != 206:              # 200 = the whole file from the start: the bytes are not from this place
                raise OSError(f"server ignored Range (HTTP {r.status})")
            data = r.read(n)
        self.requests += 1
        self.fetched += len(data)
        return data

    def prefetch(self, start, end) -> None:
        """Bytes [start, end) in one request; a range that is too large (PREFETCH_MAX) is left to be read in pieces."""
        start, end = max(0, start), min(self.size, end)
        held = self._buf_at <= start and end <= self._buf_at + len(self._buf)
        if end <= start or end - start > PREFETCH_MAX or held:
            return
        self._buf, self._buf_at = memoryview(b""), 0           # the previous entry is no longer needed (memory)
        self._buf, self._buf_at = memoryview(self._fetch(start, end - start)), start

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=io.SEEK_SET):
        """Move the read position (SEEK_SET / SEEK_CUR / SEEK_END); no network traffic."""
        self.pos = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence] + off
        return self.pos

    def readinto(self, b):
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


def _name(info):
    """A zip entry path with forward slashes (the HIDMaestro zip is packed on Windows - the separator is "\\")."""
    return info.filename.replace("\\", "/")


def pick(infos):
    """The entries to extract: the DLL from the zip root (or the first one found) and its WinRT dependencies from the folder of the
    application that ships it (HIDMaestroTest) - without test applications, samples and second copies of the DLL.
    """
    files = [i for i in infos if not i.is_dir()]
    by_name = {}
    for i in sorted(files, key=lambda i: _name(i).count("/")):     # the shallowest first: the DLL from the root directory
        by_name.setdefault(posixpath.basename(_name(i)), i)
    if DLL not in by_name:
        raise OSError(f"{DLL} not in the release zip")
    picked = [by_name[n] for n in (DLL, *DEPS) if n in by_name]
    folder = posixpath.dirname(_name(picked[0]))
    # only from the DLL folder, not someone else's from a subfolder
    lic = next((i for i in files if _name(i) == posixpath.join(folder, LICENSE)), None)
    return picked + ([lic] if lic else [])


def entry_span(info):
    """The byte range of a zip entry: the local header and the data (with a margin for a longer extra field) -> (start, end)."""
    start = info.header_offset
    return start, start + 30 + len(info.orig_filename.encode("utf-8")) + len(info.extra) + info.compress_size + LOCAL_SLACK


def extract(zf, dest, prefetch=None):
    """From an open zip: the DLL and its dependencies -> dest (flat). prefetch(start, end): fetch an entry at once. -> the names."""
    os.makedirs(dest, exist_ok=True)
    names = []
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


def install(tag, url, opener=None):
    """Download version `tag` into hidmaestro/<tag> (first .<tag>.tmp, then rename). -> (folder, downloaded bytes)."""
    os.makedirs(root(), exist_ok=True)
    final, tmp = os.path.join(root(), tag), os.path.join(root(), f".{tag}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    raw = HttpRange(url, opener)
    try:
        with zipfile.ZipFile(io.BufferedReader(raw, CHUNK)) as zf:
            names = extract(zf, tmp, raw.prefetch)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(f"HIDMaestro {tag} downloaded ({raw.fetched / 1e6:.1f} MB of {raw.size / 1e6:.0f} MB zip, "
          f"{raw.requests} requests): {', '.join(names)}")
    return final, raw.fetched


def _set(**kw) -> None:
    """Replace fields of the immutable status object."""
    global status
    from dataclasses import replace
    status = replace(status, **kw)


def refresh_status():
    """Refresh the status from disk: the downloaded version and the saved check state."""
    have = installed()
    checked, latest = _load_state()
    _set(version=have[0][0] if have else "", checked=checked, latest=latest or status.latest)
    return status


def check(force=False):
    """One check: a newer version on GitHub -> download. A network error does not touch the downloaded version. -> Status."""
    if not _lock.acquire(blocking=False):
        return status                         # already running (a background thread or the button in the window)
    try:
        _check(force)
    except Exception as e:
        _set(error=f"{type(e).__name__}: {e}")
        print(f"HIDMaestro update check failed: {e!r}")
    finally:
        _set(busy=False)
        _lock.release()
    return status


def _check(force) -> None:
    """The check itself (under the lock): skip when checked recently, ask GitHub for the latest release, hold a fresh one
    until it is old enough (the first installation never waits), otherwise install it, clean up and notify.
    """
    refresh_status()
    # 0 <=: a clock set back (a check "in the future") must not stop checking until that date
    if not force and 0 <= time.time() - status.checked < CHECK_S and status.version:
        return
    _set(busy=True, error="")
    tag, url, published = latest_release()
    now = time.time()
    _save_state(now, tag)
    _set(latest=tag, checked=now, held_until=0.0)
    if status.version and version_key(status.version) >= version_key(tag):
        return
    ready = published + min_age_s()
    if status.version and published and now < ready:      # a fresh release waits (also on "Check now")
        _set(held_until=ready)
        print(f"HIDMaestro {tag} published {(now - published) / 3600:.0f} h ago - installing it after "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(ready))} (hm_min_age_days)")
        return
    install(tag, url)
    _set(version=tag)
    cleanup(keep=tag)
    if on_installed is not None:
        on_installed(tag)


def thread(stop, enabled) -> None:
    """The receiver thread. enabled() - whether downloading is allowed (the settings may change meanwhile)."""
    refresh_status()
    if status.version:
        cleanup(keep=status.version)
    if stop.wait(START_DELAY_S):
        return
    while not stop.is_set():
        if enabled():
            check()
        if stop.wait(3600):                   # every hour it checks whether a day has passed (a sleeping PC, a settings change)
            return
