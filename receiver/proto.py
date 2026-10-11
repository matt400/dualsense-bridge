"""Wspólny protokół v2 sendera i receivera (ten plik musi być identyczny po obu stronach).

Nagłówek "!BBH" = (VER<<4 | typ), idx kontrolera, numer sekwencyjny; potem treść,
opcjonalnie 8 B HMAC-SHA256 (gdy ustawiony klucz).
  1 stan    "6BbbHBB6i"  lx ly lt rx ry rt hatx haty maska bateria% flagi acc xyz gyro xyz
  2 dotyk   "BBHHBBHH"   2 palce: aktywny id x y   (skala DS4: 1920x943)
  3 klik    "B"          przycisk touchpada: 0 puszczony, 1 wciśnięty, 2 wciśnięty z 2 palcami (PPM w trybie myszy)
  4 mysz    "hhB"        dx dy flagi: bit0 = tap (LPM), bit1 = przewijanie (dx, dy = suma przesunięć
                         dwóch palców, a nie ruch kursora), bit2 = tap dwoma palcami (PPM),
                         bity 4-7 = numer tapu 1..15 (tap jest wysyłany z powtórkami, odbiornik klika raz)
  5 ping / 7 pong "Q"    pomiar opóźnienia (idx = 255)
  6 feedback (PC -> nadajnik) "5B" mała_wibracja duża_wibracja R G B
  8 bye     ""           nadajnik: pad rozłączony -> odłącz wirtualny DS4
  9 ident   "17s"        nadajnik: stały identyfikator pada (MAC) - klucz profilu remapowania
 10 cmd     "BB"         PC -> nadajnik: polecenie, argument (CMD_POWER_OFF = wyłącz pada)
 11 info    "HHH"        nadajnik -> PC co 1 s: ping x10 [ms] (0xFFFF = brak), Hz raportów pada, Hz ruchu
 12 version "BBB"        nadajnik -> PC razem z ident: wersja nadajnika, sposoby wibracji (bit 1 = sterownik evdev FF,
                         bit 2 = raport HID przez hidraw; 0 = brak), powód braku (WHY_*)
 13 build   "7sIB"       nadajnik -> PC co 5 s i po aktualizacji (idx = 255): commit kodu nadajnika (7 znaków skrótu),
                         czas commita (unix), stan aktualizacji UPD_*; starszy receiver ignoruje nieznany typ

Na końcu: wspólne funkcje gita (wersja kodu, aktualizacja git pull) - receiver i nadajnik aktualizują się tak samo.
"""
import hmac
import json
import os
import random
import shutil
import struct
import subprocess
import threading

VER = 2
HDR0 = VER << 4

(T_STATE, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_FB, T_PONG, T_BYE, T_IDENT, T_CMD, T_INFO, T_VERSION,
 T_BUILD) = range(1, 14)
SENDER_VERSION = 5
RUMBLE_EVDEV, RUMBLE_HIDRAW = 1, 2
# dlaczego nie ma wibracji przez raport HID (pakiet version): ok, rumble=off, brak uprawnień, jądro bez hidraw,
# nie znaleziono hidraw pada, inny błąd otwarcia
WHY_OK, WHY_OFF, WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OPEN = range(6)
CMD_POWER_OFF, CMD_UPDATE = 1, 2
# stan aktualizacji nadajnika (pakiet build): brak, trwa, bez zmian, zaktualizowano (restart), błędy
UPD_NONE, UPD_RUNNING, UPD_NO_CHANGE, UPD_DONE, UPD_NOT_GIT, UPD_NO_GIT, UPD_FAILED = range(7)
RTT_NONE = 0xFFFF

HDR = struct.Struct("!BBH")
BODY = {
    T_STATE: struct.Struct("!6BbbHBB6i"),
    T_TOUCH: struct.Struct("!BBHHBBHH"),
    T_CLICK: struct.Struct("!B"),
    T_MOUSE: struct.Struct("!hhB"),
    T_PING: struct.Struct("!Q"),
    T_FB: struct.Struct("!5B"),
    T_PONG: struct.Struct("!Q"),
    T_BYE: struct.Struct("!"),
    T_IDENT: struct.Struct("!17s"),
    T_CMD: struct.Struct("!BB"),
    T_INFO: struct.Struct("!HHH"),
    T_VERSION: struct.Struct("!BBB"),           # wersja nadajnika, sposoby wibracji (bity), powód WHY_*
    T_BUILD: struct.Struct("!7sIB"),            # commit nadajnika, czas commita, stan aktualizacji UPD_*
}
BODY_LEN = {t: s.size for t, s in BODY.items()}
assert BODY_LEN == {1: 36, 2: 12, 3: 1, 4: 5, 5: 8, 6: 5, 7: 8, 8: 0, 9: 17, 10: 2, 11: 6, 12: 3, 13: 12}, BODY_LEN

# kody evdev przycisków DualSense -> bit w masce (kolejność = pozycja bitu w pakiecie stanu)
PAD_KEYS = (304, 305, 307, 308, 310, 311, 314, 315, 316, 317, 318)
KEY_BIT = {c: 1 << k for k, c in enumerate(PAD_KEYS)}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Codec:
    """Pakowanie / parsowanie pakietów z opcjonalnym HMAC. pack() jest bezpieczne wątkowo."""

    def __init__(self, key=b""):
        self.key = key
        self._seq = random.getrandbits(16)
        self._lock = threading.Lock()

    def pack(self, ptype, idx, *vals):
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFF
            sq = self._seq
        msg = HDR.pack(HDR0 | ptype, idx, sq) + BODY[ptype].pack(*vals)
        if self.key:
            msg += hmac.digest(self.key, msg, "sha256")[:8]
        return msg

    def parse(self, data):
        """-> (typ, idx, seq) albo None (zły podpis / wersja / typ / długość)."""
        n = len(data)
        if self.key:
            if n < 12:
                return None
            n -= 8
            if not hmac.compare_digest(hmac.digest(self.key, memoryview(data)[:n], "sha256")[:8], data[n:]):
                return None
        if n < 4 or data[0] >> 4 != VER:
            return None
        t = data[0] & 0x0F
        st = BODY.get(t)
        if st is None or n - 4 != st.size:
            return None
        return t, data[1], (data[2] << 8) | data[3]


class SeqFilter:
    """Odrzuca spóźnione / zdublowane pakiety. Po `window` s ciszy akceptuje dowolny numer
    (druga strona mogła się zrestartować i zacząć od losowego seq)."""

    def __init__(self, window=1.0):
        self.window = window
        self._seen = {}

    def fresh(self, key, sq, now):
        p = self._seen.get(key)
        if p is not None and now - p[1] < self.window and not 0 < ((sq - p[0]) & 0xFFFF) < 0x8000:
            return False
        self._seen[key] = (sq, now)
        return True

    def forget(self, match):
        for k in [k for k in self._seen if match(k)]:
            del self._seen[k]


def load_config(path, defaults=None):
    """Wczytuje .toml / .json; ostrzega o nieznanych kluczach (literówki)."""
    if not path:
        return {}
    with open(path, "rb") as f:
        if path.lower().endswith(".json"):
            cfg = json.load(f)
        else:
            import tomllib
            cfg = tomllib.load(f)
    if defaults:
        for k in cfg:
            if k not in defaults:
                print(f"WARNING: unknown config key {k!r} in {path} (ignored)")
    return cfg


# ---------------- git: wersja kodu i aktualizacja (receiver i nadajnik) ----------------
GIT_PATHS = (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_git():
    if found := shutil.which("git"):
        return found
    local = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")
    return next((p for p in (*GIT_PATHS, local) if os.path.isfile(p)), None)


def _owner(root):
    """Root (usługa nadajnika) uruchamia gita jako właściciel repozytorium: pliki po pull nie należą do roota,
    a git nie odmawia pracy w cudzym repozytorium ("dubious ownership"). -> kwargs dla subprocess."""
    if os.name != "posix" or os.geteuid() != 0:
        return {}
    st = os.stat(root)
    if st.st_uid == 0:
        return {}
    import pwd
    try:
        home = pwd.getpwuid(st.st_uid).pw_dir
    except KeyError:
        home = root
    return {"user": st.st_uid, "group": st.st_gid, "env": {**os.environ, "HOME": home}}


def git(root, *args, git_exe=None, timeout=15):
    """-> CompletedProcess (tekst). Bez okna konsoli na Windows."""
    return subprocess.run([git_exe or find_git() or "git", "-c", "safe.directory=*", *args], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW, **_owner(root))


def git_version(root, git_exe=None):
    """Wersja kodu w repozytorium: (skrót commita 7 znaków, czas commita unix, tytuł commita) albo ("", 0, "")."""
    try:
        p = git(root, "log", "-1", "--format=%h%x00%ct%x00%s", "--abbrev=7", git_exe=git_exe)
        h, ts, subject = p.stdout.strip().split("\0", 2)
        return h[:7], int(ts), subject
    except (OSError, ValueError, subprocess.SubprocessError):
        return "", 0, ""


def git_pull(root, git_exe=None):
    """git pull --ff-only (tylko przewinięcie do wersji z GitHuba, lokalne zmiany -> błąd, nic nie nadpisane).
    -> (UPD_*, komunikat gita przy błędzie, stary commit, nowy commit, zmienione pliki)."""
    if not os.path.isdir(os.path.join(root, ".git")):
        return UPD_NOT_GIT, "", "", "", []
    git_exe = git_exe or find_git()
    if git_exe is None:
        return UPD_NO_GIT, "", "", "", []
    try:
        old = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        p = git(root, "pull", "--ff-only", git_exe=git_exe, timeout=120)
        if p.returncode:
            lines = [x.strip() for x in (p.stderr or p.stdout).splitlines() if x.strip()]
            return UPD_FAILED, " / ".join(lines[-3:]), old, old, []
        new = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        if new == old:
            return UPD_NO_CHANGE, "", old, new, []
        files = git(root, "diff", "--name-only", old, new, git_exe=git_exe).stdout.split()
    except (OSError, subprocess.SubprocessError) as e:
        return UPD_FAILED, str(e), "", "", []
    return UPD_DONE, "", old, new, files
