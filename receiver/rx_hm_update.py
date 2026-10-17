"""Receiver: HIDMaestro pobierany i aktualizowany sam z GitHub Releases (hifihedgehog/HIDMaestro).

Wydanie to zip ~160 MB (SDK + aplikacje testowe), a receiverowi wystarczy HIDMaestro.Core.dll (sterownik jest w niej)
i dwie DLL projekcji Windows SDK. Zip czytany jest przez HTTP Range: katalog zip z końca pliku, potem tylko te wpisy -
pobiera się ~56 MB zamiast 160. CRC32 każdego wpisu sprawdza zipfile, a cała wersja trafia do folderu atomowo (tmp + rename).

  receiver/hidmaestro/v1.12.0/HIDMaestro.Core.dll   wersja w swoim folderze: nowsza nie nadpisuje DLL załadowanej
                                                    w działającym procesie (CLR trzyma plik) - działa po restarcie
  receiver/hidmaestro/state.json                    kiedy ostatnio sprawdzano i jaki był wynik

  newest_dll()      DLL najnowszej pobranej wersji albo None (rx_hm.candidates)
  check(force)      jedno sprawdzenie (najwyżej co CHECK_S, chyba że force) -> Status
  thread()          wątek receivera: sprawdzenie po starcie i potem raz na dobę
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
DEPS = ("Microsoft.Windows.SDK.NET.dll", "WinRT.Runtime.dll")   # projekcje Windows SDK (TFM net10.0-windows10.0.26100)
CHECK_S = 24 * 3600
START_DELAY_S = 20                   # po starcie receivera: najpierw pady i okno, potem sieć
TIMEOUT_S = 30
CHUNK = 1 << 16
UA = {"User-Agent": "dualsense-bridge", "Accept": "application/vnd.github+json"}
_TAG = re.compile(r"^v?(\d+(?:\.\d+)*)$")


@dataclass(frozen=True, slots=True)
class Status:
    version: str = ""        # pobrana wersja (najnowsza w folderze), "" = brak
    latest: str = ""         # najnowsza na GitHubie przy ostatnim sprawdzeniu
    busy: bool = False       # trwa sprawdzanie / pobieranie
    error: str = ""          # ostatni błąd (sieć, zip) - "" = OK
    checked: float = 0.0     # time.time() ostatniego sprawdzenia


status = Status()
_lock = threading.Lock()
on_installed = None          # fn(wersja) po pobraniu nowej wersji (rx_ds4: HIDMaestro od następnego podłączenia pada)


def root():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "hidmaestro")


def version_key(tag):
    m = _TAG.match(tag or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def installed():
    """Pobrane wersje od najnowszej: [(tag, folder)] - tylko foldery z DLL (przerwane pobieranie się nie liczy)."""
    try:
        names = os.listdir(root())
    except OSError:
        return []
    found = [(n, os.path.join(root(), n)) for n in names
             if version_key(n) and os.path.isfile(os.path.join(root(), n, DLL))]
    return sorted(found, key=lambda t: version_key(t[0]), reverse=True)


def newest_dll():
    found = installed()
    return os.path.join(found[0][1], DLL) if found else None


def cleanup(keep):
    """Starsze wersje i resztki przerwanych pobrań. Pliki zablokowane (DLL w użyciu) zostają do następnego razu."""
    for n in os.listdir(root()) if os.path.isdir(root()) else ():
        p = os.path.join(root(), n)
        if os.path.isdir(p) and n != keep and (version_key(n) or n.startswith(".")):
            shutil.rmtree(p, ignore_errors=True)


def _state_path():
    return os.path.join(root(), "state.json")


def _load_state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            raw = json.load(f)
        return float(raw.get("checked", 0)), str(raw.get("latest", ""))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0, ""


def _save_state(checked, latest):
    try:
        os.makedirs(root(), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"checked": checked, "latest": latest}, f)
        os.replace(tmp, _state_path())
    except OSError:
        pass


def _get(url, headers=None, timeout=TIMEOUT_S):
    return urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=timeout)


def latest_release():
    """-> (tag, url zipa x64). DLL jest identyczna w zipie x64 i arm64 - bierzemy zwykły."""
    with _get(API) as r:
        rel = json.load(r)
    tag = rel.get("tag_name", "")
    zips = [a for a in rel.get("assets", ()) if a.get("name", "").lower().endswith(".zip")]
    asset = next((a for a in zips if "arm64" not in a["name"].lower()), zips[0] if zips else None)
    if not version_key(tag) or asset is None:
        raise OSError(f"unexpected release data ({tag!r}, {len(zips)} zip)")
    return tag, asset["browser_download_url"]


class HttpRange(io.RawIOBase):
    """Plik zdalny do odczytu z przesuwaniem (HTTP Range) - zipfile czyta katalog z końca i pojedyncze wpisy.
    Adres po przekierowaniu GitHuba (podpisany link do magazynu) ustalany raz, rozmiar z Content-Range."""

    def __init__(self, url, opener=None):
        super().__init__()
        self._get = opener = opener or _get
        with opener(url, {"Range": "bytes=0-0"}) as r:
            if r.status != 206:
                raise OSError(f"server ignores Range (HTTP {r.status})")
            self.url = r.geturl()
            self.size = int(r.headers["Content-Range"].rsplit("/", 1)[1])
        self.pos = 0
        self.fetched = 0                     # ile bajtów faktycznie pobrano (log, testy)

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=io.SEEK_SET):
        self.pos = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence] + off
        return self.pos

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        with self._get(self.url, {"Range": f"bytes={self.pos}-{self.pos + n - 1}"}) as r:
            data = r.read(n)
        b[:len(data)] = data
        self.pos += len(data)
        self.fetched += len(data)
        return len(data)


def _name(info):
    """Ścieżka wpisu zip z ukośnikami (zip HIDMaestro jest pakowany na Windows - separator "\\")."""
    return info.filename.replace("\\", "/")


def pick(infos):
    """Wpisy do wypakowania: DLL z katalogu głównego zipa (albo pierwsza znaleziona) i jej zależności WinRT z folderu
    aplikacji, która ją dostarcza (HIDMaestroTest) - bez aplikacji testowych, przykładów i drugich kopii DLL."""
    files = [i for i in infos if not i.is_dir()]
    by_name = {}
    for i in sorted(files, key=lambda i: _name(i).count("/")):     # najpłycej pierwsze: DLL z katalogu głównego
        by_name.setdefault(posixpath.basename(_name(i)), i)
    if DLL not in by_name:
        raise OSError(f"{DLL} not in the release zip")
    return [by_name[n] for n in (DLL, *DEPS) if n in by_name]


def extract(zf, dest):
    """Z otwartego zipa: DLL i jej zależności -> dest (płasko). -> lista nazw."""
    os.makedirs(dest, exist_ok=True)
    names = []
    for i in pick(zf.infolist()):
        name = posixpath.basename(_name(i))
        with zf.open(i) as src, open(os.path.join(dest, name), "wb") as out:
            shutil.copyfileobj(src, out, CHUNK)          # CRC32 sprawdzany przy odczycie (BadZipFile przy błędzie)
        names.append(name)
    with open(os.path.join(dest, DLL), "rb") as f:
        if f.read(2) != b"MZ":
            raise OSError(f"{DLL} is not a Windows DLL")
    return names


def install(tag, url, opener=None):
    """Pobiera wersję tag do hidmaestro/<tag> (najpierw .<tag>.tmp, potem rename). -> (folder, pobrane bajty)."""
    os.makedirs(root(), exist_ok=True)
    final, tmp = os.path.join(root(), tag), os.path.join(root(), f".{tag}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    raw = HttpRange(url, opener)
    try:
        with zipfile.ZipFile(io.BufferedReader(raw, CHUNK)) as zf:
            names = extract(zf, tmp)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(f"HIDMaestro {tag} downloaded ({raw.fetched / 1e6:.1f} MB of {raw.size / 1e6:.0f} MB zip): {', '.join(names)}")
    return final, raw.fetched


def _set(**kw):
    global status
    from dataclasses import replace
    status = replace(status, **kw)


def refresh_status():
    have = installed()
    checked, latest = _load_state()
    _set(version=have[0][0] if have else "", checked=checked, latest=latest or status.latest)
    return status


def check(force=False):
    """Jedno sprawdzenie: nowsza wersja na GitHubie -> pobranie. Błąd sieci nie rusza pobranej wersji. -> Status."""
    if not _lock.acquire(blocking=False):
        return status                         # już trwa (wątek w tle albo przycisk w oknie)
    try:
        _check(force)
    except Exception as e:
        _set(error=f"{type(e).__name__}: {e}")
        print(f"HIDMaestro update check failed: {e!r}")
    finally:
        _set(busy=False)
        _lock.release()
    return status


def _check(force):
    refresh_status()
    if not force and time.time() - status.checked < CHECK_S and status.version:
        return
    _set(busy=True, error="")
    tag, url = latest_release()
    now = time.time()
    _save_state(now, tag)
    _set(latest=tag, checked=now)
    if status.version and version_key(status.version) >= version_key(tag):
        return
    install(tag, url)
    _set(version=tag)
    cleanup(keep=tag)
    if on_installed is not None:
        on_installed(tag)


def thread(stop, enabled):
    """Wątek receivera. enabled() - czy wolno pobierać (ustawienia mogą się zmienić w trakcie)."""
    refresh_status()
    if status.version:
        cleanup(keep=status.version)
    if stop.wait(START_DELAY_S):
        return
    while not stop.is_set():
        if enabled():
            check()
        if stop.wait(3600):                   # co godzinę sprawdza, czy minęła doba (uśpiony PC, zmiana ustawień)
            return
