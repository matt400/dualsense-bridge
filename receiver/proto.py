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
"""
import hmac
import json
import random
import struct
import threading

VER = 2
HDR0 = VER << 4

T_STATE, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_FB, T_PONG, T_BYE, T_IDENT, T_CMD, T_INFO = range(1, 12)
CMD_POWER_OFF = 1
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
}
BODY_LEN = {t: s.size for t, s in BODY.items()}
assert BODY_LEN == {1: 36, 2: 12, 3: 1, 4: 5, 5: 8, 6: 5, 7: 8, 8: 0, 9: 17, 10: 2, 11: 6}, BODY_LEN

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
