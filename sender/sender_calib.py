"""Sender: kalibracja żyroskopu (przy starcie i na żądanie z okna receivera) i zapis biasu per pad (MAC).

Pad z zapisaną kalibracją (gyro_cal_file) nie kalibruje się przy starcie - od razu używa zapisanego biasu, więc
nie trzeba go odkładać po włączeniu. Autobias (sender_streams) dalej dostraja bias w pamięci, zapis się nie zmienia.
Kalibracja z okna (CMD_GYRO_CAL): pad leży nieruchomo przez arg sekund; wynik (pakiet calres) wraca do receivera.
"""
import json
import os
import re
import time

import sender_net as net
from proto import CAL_FEW, CAL_MAX_S, CAL_MOVED, CAL_OK, CALF_STORED, T_CALRES, clamp
from sender_config import C

MIN_SAMPLES = 20
DUP_S = 0.3              # polecenie przychodzi 3x (UDP) - powtórki w tym czasie nie zaczynają kalibracji od nowa
_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", re.IGNORECASE)
_saved = None            # MAC -> {"bias": [x, y, z], "t": czas zapisu (unix)}


def _load():
    global _saved
    if _saved is None:
        _saved = {}
        if C.GYRO_CAL_FILE:
            try:
                with open(C.GYRO_CAL_FILE, encoding="utf-8") as f:
                    raw = json.load(f)
                _saved = {str(k).lower(): {"bias": [float(x) for x in v["bias"]][:3], "t": int(v.get("t", 0))}
                          for k, v in raw.items() if len(v["bias"]) == 3}
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                print(f"Gyro calibration file {C.GYRO_CAL_FILE} ignored: {e!r}")
    return _saved


def stored(key):
    """Zapisana kalibracja pada -> {"bias", "t"} albo None (brak / pad bez MAC)."""
    return _load().get((key or "").lower())


def save(key, bias):
    """Zapisuje bias pada (tylko pady z MAC - USB bez uniq ma zmienny klucz). -> True, gdy zapisano."""
    if not _MAC.fullmatch(key or ""):
        return False
    data = _load()
    data[key.lower()] = {"bias": [round(b, 1) for b in bias], "t": int(time.time())}
    if not C.GYRO_CAL_FILE:
        return True
    tmp = C.GYRO_CAL_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, C.GYRO_CAL_FILE)
    except OSError as e:
        print(f"Cannot write {C.GYRO_CAL_FILE}: {e!r}")
        return False
    return True


class GyroCal:
    """Zbieranie próbek gyro przez `secs` sekund; feed() -> None (trwa) albo (CAL_*, bias | None)."""
    __slots__ = ("end", "samples", "user")

    def __init__(self, now, secs, user):
        self.end, self.samples, self.user = now + secs, [], user

    def feed(self, now, gyro):
        if now < self.end:
            self.samples.append(tuple(gyro))
            return None
        if len(self.samples) < MIN_SAMPLES:
            return CAL_FEW, None
        axes = list(zip(*self.samples, strict=True))
        if any(max(a) - min(a) > C.CAL_MAX_RANGE for a in axes):
            return CAL_MOVED, None
        return CAL_OK, [sum(a) / len(a) for a in axes]


def report(c, status):
    """Pakiet calres do receivera: stan, zapisany bias i ten, którego nadajnik teraz używa."""
    st = stored(c.key)
    sb = [round(x) for x in st["bias"]] if st else [0, 0, 0]
    net.emit(T_CALRES, c.idx, status, CALF_STORED if st else 0, *sb, *(round(x) for x in c.gyro_bias),
             st["t"] if st else 0)


def request(c, secs, now=None):
    """Polecenie z okna: kalibracja przez secs sekund (stream_motion zacznie ją przy następnej próbce)."""
    now = time.monotonic() if now is None else now
    secs = clamp(secs, 1, CAL_MAX_S)
    if now - c.cal_cmd_t < DUP_S:
        return
    c.cal_cmd_t = now
    if c.motion_dev is None:            # pad bez czujnika ruchu (np. jeszcze się łączy) - nie ma czego kalibrować
        report(c, CAL_FEW)
        return
    c.cal_request = secs


def startup_done(c, result):
    """Wynik kalibracji przy starcie (bez zapisu - zapisuje tylko kalibracja z okna)."""
    status, bias = result
    if status == CAL_OK:
        c.gyro_bias = bias
        print(f"Controller {c.idx}: gyro bias {[round(b) for b in bias]}")
    else:
        print(f"Controller {c.idx}: calibration skipped (pad was moving)")


def finish(c, result):
    """Wynik kalibracji z okna: zapis + odpowiedź; ruch / za mało próbek - bias bez zmian."""
    status, bias = result
    if status == CAL_OK:
        c.gyro_bias = bias
        saved = save(c.key, bias)
        print(f"Controller {c.idx}: gyro calibrated {[round(b) for b in bias]}" + (" (saved)" if saved else ""))
    else:
        print(f"Controller {c.idx}: gyro calibration rejected ({'pad was moving' if status == CAL_MOVED else 'no data'})")
    report(c, status)
