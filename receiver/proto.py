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
 10 cmd     "BB"         PC -> nadajnik: polecenie, argument (CMD_POWER_OFF = wyłącz pada; v9+: CMD_GYRO_CAL = kalibracja
                         żyroskopu leżącego pada przez arg sekund, 0 = tylko stan; CMD_RAW_STICKS = gałki bez strefy
                         martwej nadajnika przez arg sekund, 0 = koniec - pomiar dryfu w oknie)
 11 info    "HHH"        nadajnik -> PC co 1 s: ping x10 [ms] (0xFFFF = brak), Hz raportów pada, Hz ruchu
 12 version "BBB"        nadajnik -> PC razem z ident: wersja nadajnika, sposoby wibracji (bit 1 = sterownik evdev FF,
                         bit 2 = raport HID przez hidraw; 0 = brak), powód braku (WHY_*)
 13 build   "7sIB"       nadajnik -> PC co 5 s i po aktualizacji (idx = 255): commit kodu nadajnika (7 znaków skrótu),
                         czas commita (unix), stan aktualizacji UPD_*; starszy receiver ignoruje nieznany typ
 14 updmsg  "120s"       nadajnik -> PC po aktualizacji (idx = 255): powód błędu git pull (UTF-8, ucięty) - okno pokazuje
                         go zamiast "szczegóły w logu na telefonie"; starszy receiver ignoruje nieznany typ
 15 trig    "4B"         PC -> nadajnik: stały opór spustów z profilu gry: L2 początek 0-9, L2 siła 0-8, R2 początek,
                         R2 siła (siła 0 = bez oporu); tylko przez raport HID (hidraw); tylko do nadajnika v6+
  0 event   "HBBH56s"    nadajnik -> PC (idx = slot pada albo 255): zdarzenie do dziennika w oknie - numer (powtórka
                         nie dubluje wpisu), poziom EV_*, kod E_*, wiek [s] (zdarzenie sprzed połączenia z receiverem,
                         np. wysłany Wake-on-LAN), argument (UTF-8); tylko gdy receiver włączył je w pakiecie scfg

Warianty dłuższe (typ w nagłówku ten sam, rozpoznawane po długości - typ ma tylko 4 bity, a starsza strona odrzuca
nieznaną długość, więc wariant idzie tylko do strony, o której wiadomo, że go zna):
  state2 (typ 1) "6BbbHBB6iIHH" stan + pomiar opóźnienia: czas wysłania w zegarze PC [µs, młodsze 32 bity], czas na
                         telefonie od zdarzenia w jądrze do wysłania [µs], opóźnienie Bluetooth ponad minimum z ostatnich
                         2 s [µs] (LAT_NONE = brak). Nadajnik wysyła go dopiero, gdy dostaje pong2 (receiver go zna)
  pong2  (typ 7) "QQ"    PC -> nadajnik zaraz przed zwykłym pong: echo pingu, czas PC [µs] - nadajnik liczy przesunięcie
                         zegarów (jak NTP, z pomiaru o najmniejszym RTT); starszy nadajnik odrzuca długość, bierze pong
  scfg   (typ 10) "B6s4sBH"  PC -> nadajnik (idx = 255), przy zmianie i co 5 s: flagi SCFG_*, MAC komputera do Wake-on-LAN
                         (zera = sam z tablicy ARP telefonu), adres broadcast (zera = sam), przycisk kombinacji z PS (numer
                         bitu w masce przycisków), czas przytrzymania [ms]; nadajnik zapisuje to u siebie (wol.json), bo
                         przy wyłączonym PC nie ma tego skąd wziąć; starszy nadajnik odrzuca długość
  build2 (typ 13) "7sIB6s4s" nadajnik -> PC: build + MAC komputera i broadcast, których użyje Wake-on-LAN (zera = nie zna);
                         tylko do receivera, od którego przyszedł scfg (starszy odrzuciłby długość i nie znałby wersji)
  calres (typ 11) "BB3i3iI" nadajnik -> PC: odpowiedź na CMD_GYRO_CAL - stan CAL_*, flagi CALF_*, zapisany bias gyro (x, y, z
                         w jednostkach evdev), bias używany teraz (z kalibracji przy starcie / autobias), czas zapisu (unix);
                         tylko w odpowiedzi na polecenie (receiver v9+ je wysyła, więc zna ten wariant)
  trig2  (typ 15) "12B"  PC -> nadajnik: efekty spustów - L2: tryb TFX_*, a, b, c; R2: tryb, a, b, c; wibracja z gry na
                         spusty: flagi RFX_*, siła [%], częstotliwość [Hz], 0. Parametry trybu:
                           TFX_RES   a = początek 0-9 (strefa = 10 % skoku), b = siła 1-8
                           TFX_CLICK a = punkt kliku 2-7 (20-70 % skoku), b = siła 1-8
                           TFX_VIB   a = początek 0-9, b = amplituda 1-8, c = częstotliwość 1-255 Hz
                         Wibracja z gry (RFX_ON): L2 drga wg dużego silnika, R2 wg małego; na czas wibracji zastępuje
                         efekt spustu, po RFX_HOLD_S ciszy spust wraca do efektu. Tylko do nadajnika z CAP_TRIG_FX
                         (v8+); starszy dostaje zwykły trig (sam opór)
Bluetooth telefonu sterowany z okna (v10+; telefon bez ekranu). Nadajnik ogłasza obsługę sam - pakietem btstate (idx 255)
do receivera, od którego przyszedł scfg; receiver wysyła btcmd dopiero po btstate (starszy nadajnik odrzuciłby długość):
  btcmd   (typ 10) "BB6s"      PC -> nadajnik (idx 255): operacja BT_*, argument (BT_SCAN_START: sekundy, najwyżej
                               BT_SCAN_MAX_S; BT_POWER: 1 / 0), MAC urządzenia (zera = bez). BT_LIST = "panel otwarty":
                               przez BT_ACTIVE_S nadajnik co sekundę wysyła stan i listę urządzeń, potem tylko stan co 5 s
  btstate (typ 12) "BBBBBHBB6s" nadajnik -> PC (idx 255): stan adaptera BTS_*, flagi BTF_*, sekundy skanu do końca,
                               połączone urządzenia, urządzenia na liście, runda listy, ostatnia operacja BT_*, jej wynik
                               BTR_*, jej MAC; zaraz po nim (w aktywnym trybie) urządzenia rundy
  btdev   (typ 9)  "HBB6sbB32s" nadajnik -> PC (idx 255): runda, numer, ile w rundzie, MAC, RSSI [dBm] (BT_RSSI_NONE =
                               brak - poza skanem), flagi BTD_*, nazwa (UTF-8, ucięta); receiver podmienia listę dopiero,
                               gdy ma całą rundę (zgubiony pakiet = lista z poprzedniej rundy, nie dziurawa)

Na końcu: wspólne funkcje gita (wersja kodu, aktualizacja git pull) - receiver i nadajnik aktualizują się tak samo.
"""
import hmac
import json
import os
import random
import shlex
import shutil
import struct
import subprocess
import threading
import time

VER = 2
HDR0 = VER << 4

(T_EVENT, T_STATE, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_FB, T_PONG, T_BYE, T_IDENT, T_CMD, T_INFO, T_VERSION,
 T_BUILD, T_UPDMSG, T_TRIG) = range(16)
T_STATE2, T_PONG2, T_SCFG, T_CALRES, T_BUILD2, T_TRIG2 = 0x101, 0x107, 0x10A, 0x10B, 0x10D, 0x10F   # warianty dłuższe
T_BTDEV, T_BTSTATE, T_BTCMD = 0x109, 0x10C, 0x20A                                                 # Bluetooth (v10+)
WIRE = {T_STATE2: T_STATE, T_PONG2: T_PONG, T_SCFG: T_CMD, T_CALRES: T_INFO, T_BUILD2: T_BUILD, T_TRIG2: T_TRIG,
        T_BTDEV: T_IDENT, T_BTSTATE: T_VERSION, T_BTCMD: T_CMD}
SENDER_VERSION = 10
RUMBLE_EVDEV, RUMBLE_HIDRAW, CAP_TRIGGERS = 1, 2, 4     # bity pakietu version (CAP_TRIGGERS: opór spustów działa)
CAP_TRIG_FX = 8                                         # bit pakietu version: nadajnik zna trig2 (klik, wibracja, z gry)
CAP_CALIB = 16                                          # bit pakietu version: CMD_GYRO_CAL i CMD_RAW_STICKS (v9+)
LAT_NONE = 0xFFFF                                       # pole opóźnienia w state2: brak pomiaru (powtórka, brak danych)
TRIG_START_MAX, TRIG_FORCE_MAX = 9, 8                   # opór spustu: strefa początku 0-9 (co 10 %), siła 1-8
TFX_OFF, TFX_RES, TFX_CLICK, TFX_VIB = range(4)         # tryb efektu spustu (pakiet trig2)
TRIG_CLICK_MIN, TRIG_CLICK_MAX = 2, 7                   # punkt kliku: strefa 2-7 (pad: koniec = punkt + 1, max 8)
TRIG_AMP_MAX, TRIG_FREQ_MAX = 8, 255                    # wibracja spustu: amplituda 1-8, częstotliwość 1-255 Hz
RFX_ON = 1                                              # flaga trig2: wibracja z gry na spusty
RFX_GAIN_MAX = 200                                      # siła wibracji z gry na spustach [%]
RFX_HOLD_S = 0.3                                        # tyle ciszy silników, zanim spust wróci do swojego efektu
TRIG2_OFF = (TFX_OFF, 0, 0, 0) * 2 + (0, 0, 0, 0)       # trig2: wszystko wyłączone
# dlaczego nie ma wibracji przez raport HID (pakiet version): ok, rumble=off, brak uprawnień, jądro bez hidraw,
# nie znaleziono hidraw pada, inny błąd otwarcia
WHY_OK, WHY_OFF, WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OPEN = range(6)
CMD_POWER_OFF, CMD_UPDATE, CMD_GYRO_CAL, CMD_RAW_STICKS = 1, 2, 3, 4
CAL_MAX_S, RAW_STICKS_MAX_S = 10, 30                    # najdłuższa kalibracja gyro / gałki bez strefy martwej [s]
# stan w pakiecie calres: skalibrowano, pad się ruszał, za mało próbek (brak czujnika ruchu), sam stan (arg 0), trwa
CAL_OK, CAL_MOVED, CAL_FEW, CAL_STATUS, CAL_RUNNING = range(5)
CALF_STORED = 1                                         # flaga calres: nadajnik ma zapisany bias tego pada
# stan aktualizacji nadajnika (pakiet build): brak, trwa, bez zmian, zaktualizowano (restart), błędy
UPD_NONE, UPD_RUNNING, UPD_NO_CHANGE, UPD_DONE, UPD_NOT_GIT, UPD_NO_GIT, UPD_FAILED = range(7)
RTT_NONE = 0xFFFF
SCFG_WOL, SCFG_EVENTS = 1, 2                            # flagi pakietu scfg: Wake-on-LAN, zdarzenia do dziennika
EV_INFO, EV_WARN, EV_ERROR = range(3)                   # poziom zdarzenia (pakiet event)
# kody zdarzeń nadajnika (pakiet event) - tekst w oknie receivera (rx_events.SENDER_TEXT), argument w {arg}
(E_START, E_PAD_ON, E_PAD_OFF, E_POWER_OFF, E_WOL, E_WOL_FAIL, E_NO_RECEIVER, E_NET_DOWN, E_ERROR) = range(1, 10)
WOL_PORTS = (9, 7)                                      # magic packet: discard i echo (karty sieciowe słuchają każdego)
# Bluetooth telefonu z okna (btcmd / btstate / btdev): operacje, stan adaptera, flagi, wynik operacji, flagi urządzenia
BT_LIST, BT_SCAN_START, BT_SCAN_STOP, BT_PAIR, BT_CONNECT, BT_DISCONNECT, BT_REMOVE, BT_POWER = range(8)
# działa, wyłączony, zablokowany (rfkill), brak adaptera, brak bluetoothd, brak uprawnień, brak busctl, inny błąd
BTS_OK, BTS_OFF, BTS_BLOCKED, BTS_NO_ADAPTER, BTS_NO_SERVICE, BTS_DENIED, BTS_NO_TOOL, BTS_ERROR = range(8)
BTF_SCANNING, BTF_BUSY = 1, 2                           # flagi btstate: trwa skan, trwa operacja
# brak, trwa, udało się, nieudane, przekroczony czas, nieznane urządzenie, odrzucone parowanie, brak uprawnień,
# adapter wyłączony / zablokowany, adapter zajęty (inna operacja)
BTR_NONE, BTR_RUNNING, BTR_OK, BTR_FAILED, BTR_TIMEOUT, BTR_NOT_FOUND, BTR_AUTH, BTR_DENIED, BTR_OFF, BTR_BUSY = range(10)
BTD_PAIRED, BTD_CONNECTED, BTD_TRUSTED, BTD_PAD = 1, 2, 4, 8   # flagi btdev (BTD_PAD = pad do gier)
BT_SCAN_MAX_S = 30            # skan zabiera radio połączonym padom (skoki opóźnienia) - zawsze z limitem
BT_ACTIVE_S = 6.0             # tyle po ostatnim btcmd nadajnik wysyła listę urządzeń co sekundę
BT_RSSI_NONE = -128
BT_DEV_MAX = 32               # najwięcej urządzeń w rundzie

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
    T_UPDMSG: struct.Struct("!120s"),           # powód błędu aktualizacji nadajnika (UTF-8)
    T_TRIG: struct.Struct("!4B"),               # opór spustów: L2 początek, L2 siła, R2 początek, R2 siła
    T_EVENT: struct.Struct("!HBBH56s"),         # numer, poziom EV_*, kod E_*, wiek [s], argument (UTF-8)
    T_STATE2: struct.Struct("!6BbbHBB6iIHH"),   # stan + czas wysłania (zegar PC), telefon, Bluetooth [µs]
    T_PONG2: struct.Struct("!QQ"),              # echo pingu, czas PC [µs]
    T_SCFG: struct.Struct("!B6s4sBH"),          # flagi SCFG_*, MAC PC, broadcast, bit przycisku kombinacji, przytrzymanie ms
    T_CALRES: struct.Struct("!BB3i3iI"),        # stan CAL_*, flagi CALF_*, zapisany bias xyz, bieżący bias xyz, czas zapisu
    T_BUILD2: struct.Struct("!7sIB6s4s"),       # build + MAC PC i broadcast, których użyje Wake-on-LAN
    T_TRIG2: struct.Struct("!12B"),             # efekty L2, R2 (tryb, a, b, c) + wibracja z gry (flagi, %, Hz, 0)
    T_BTCMD: struct.Struct("!BB6s"),            # operacja BT_*, argument, MAC
    T_BTSTATE: struct.Struct("!BBBBBHBB6s"),    # BTS_*, BTF_*, skan [s], połączone, na liście, runda, op, BTR_*, MAC
    T_BTDEV: struct.Struct("!HBB6sbB32s"),      # runda, numer, ile, MAC, RSSI, BTD_*, nazwa
}
STATE_FIELDS = 17                               # pola stanu w state / state2 (bez pomiaru opóźnienia)
BODY_LEN = {t: s.size for t, s in BODY.items()}
assert BODY_LEN == {0: 62, 1: 36, 2: 12, 3: 1, 4: 5, 5: 8, 6: 5, 7: 8, 8: 0, 9: 17, 10: 2, 11: 6, 12: 3, 13: 12, 14: 120,
                    15: 4, T_STATE2: 44, T_PONG2: 16, T_SCFG: 14, T_CALRES: 30, T_BUILD2: 22, T_TRIG2: 12,
                    T_BTCMD: 8, T_BTSTATE: 15, T_BTDEV: 44}, BODY_LEN
# (typ w nagłówku, długość treści) -> typ w programie
BY_SIZE = {(WIRE.get(t, t), s.size): t for t, s in BODY.items()}
assert len(BY_SIZE) == len(BODY), "dwa typy z tą samą długością w jednym typie nagłówka"

# kody evdev przycisków DualSense -> bit w masce (kolejność = pozycja bitu w pakiecie stanu)
PAD_KEYS = (304, 305, 307, 308, 310, 311, 314, 315, 316, 317, 318)
KEY_BIT = {c: 1 << k for k, c in enumerate(PAD_KEYS)}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def trig2_from_legacy(vals):
    """Pakiet trig (L2 początek, siła, R2 początek, siła) -> wartości trig2 (sam opór, bez wibracji z gry)."""
    sides = ((TFX_RES, start, force, 0) if force else (TFX_OFF, 0, 0, 0) for start, force in (vals[:2], vals[2:4]))
    return (*(v for side in sides for v in side), 0, 0, 0, 0)


def mac_bytes(text):
    """"aa:bb:cc:dd:ee:ff" / "AA-BB-..." / "aabbccddeeff" -> 6 B albo None (zły zapis)."""
    h = "".join(c for c in (text or "") if c not in ":-. ").lower()
    if len(h) != 12:
        return None
    try:
        b = bytes.fromhex(h)
    except ValueError:
        return None
    return b if any(b) else None


def mac_text(b):
    return ":".join(f"{x:02x}" for x in b) if b and any(b) else ""


def magic_packet(mac):
    """Wake-on-LAN: 6 x 0xFF i 16 x MAC (6 B)."""
    return b"\xff" * 6 + bytes(mac) * 16


def cstr(b):
    """Pole tekstowe pakietu (dopełnione zerami) -> str."""
    return b.rstrip(b"\0").decode("utf-8", "replace")


def utf8_field(text, size, more=""):
    """str -> bajty UTF-8 najwyżej `size`, ucięte na granicy znaku (cstr() nie pokaże pół litery); ucięty tekst
    kończy się `more` (np. "…"), żeby druga strona wiedziała, że to nie cała nazwa."""
    raw = (text or "").encode("utf-8")
    if len(raw) <= size:
        return raw
    tail = more.encode("utf-8")
    return raw[:size - len(tail)].decode("utf-8", "ignore").encode("utf-8") + tail


KEY_MISMATCH_S = 5.0          # tyle po pakiecie z innym ustawieniem klucza HMAC odrzucamy też długości niejednoznaczne


class Codec:
    """Pakowanie / parsowanie pakietów z opcjonalnym HMAC. pack() jest bezpieczne wątkowo.

    Druga strona z innym ustawieniem klucza (klucz tylko po jednej stronie albo różne klucze) -> pakiety odrzucone
    i mismatch_t = czas ostatniego takiego pakietu (okno pokazuje ostrzeżenie). Bez klucza nie wystarczy sama
    długość: stan + 8 B HMAC ma tyle co state2 (trig + HMAC = trig2, pong + HMAC = pong2), więc receiver bez klucza
    brał stan nadajnika z kluczem za state2 z bajtami HMAC jako pomiarem opóźnienia - pady "pół działały" (bez
    identyfikatora, profilu, wibracji) zamiast jasnego błędu. Nadajnik z kluczem wysyła też pakiety, których długość
    z HMAC nie pasuje do żadnego typu bez niego (ping, ident, info - kilka razy na sekundę); po takim pakiecie przez
    KEY_MISMATCH_S odrzucamy również te niejednoznaczne."""

    def __init__(self, key=b""):
        self.key = key
        self._seq = random.getrandbits(16)
        self._lock = threading.Lock()
        self.mismatch_t = float("-inf")

    def pack(self, ptype, idx, *vals):
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFF
            sq = self._seq
        msg = HDR.pack(HDR0 | WIRE.get(ptype, ptype), idx, sq) + BODY[ptype].pack(*vals)
        if self.key:
            msg += hmac.digest(self.key, msg, "sha256")[:8]
        return msg

    def parse(self, data):
        """-> (typ, idx, seq) albo None (zły podpis / wersja / typ / długość). Wariant dłuższy -> jego typ (T_STATE2...)."""
        n = len(data)
        if n < 4 or data[0] >> 4 != VER:
            return None
        wire = data[0] & 0x0F
        if self.key:
            if n < 12 or not hmac.compare_digest(hmac.digest(self.key, memoryview(data)[:n - 8], "sha256")[:8], data[n - 8:]):
                if (wire, n - 4) in BY_SIZE or (wire, n - 12) in BY_SIZE:
                    self.mismatch_t = time.monotonic()  # nasz pakiet, zły podpis: brak klucza albo inny klucz
                return None
            n -= 8
        elif n >= 12 and (wire, n - 12) in BY_SIZE:     # długość pasuje do pakietu z HMAC
            if (wire, n - 4) not in BY_SIZE:            # ... i do żadnego bez niego: druga strona ma klucz
                self.mismatch_t = time.monotonic()
                return None
            if time.monotonic() - self.mismatch_t < KEY_MISMATCH_S:
                return None                             # niejednoznaczna (stan + HMAC = state2) przy stronie z kluczem
        t = BY_SIZE.get((wire, n - 4))
        if t is None:
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


def write_file(path, data):
    """Zapis atomowy (plik tymczasowy + fsync + zamiana): przerwany zapis, zanik zasilania albo uśpienie / wyłączenie
    PC zaraz po zapisie (akcje mastera) nie zostawią pustego ani uciętego pliku - bez fsync os.replace mógł podmienić
    plik, którego dane nie doszły jeszcze na dysk. Nieudany zapis usuwa plik tymczasowy. data: str (UTF-8) albo bajty."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json(path, obj, **kw):
    """write_file() dla JSON (kw jak w json.dumps, np. indent, sort_keys); na końcu znak nowej linii."""
    write_file(path, json.dumps(obj, **kw) + "\n")


# ---------------- git: wersja kodu i aktualizacja (receiver i nadajnik) ----------------
GIT_PATHS = (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe")
# klucz wdrożeniowy (tylko do odczytu) nadajnika i jego known_hosts - w .git, więc nigdy nie trafiają do repozytorium;
# tworzy je sender/setup-update-key.sh. Usługa (root -> właściciel repozytorium) nie ma agenta SSH ani hasła do klucza.
DEPLOY_KEY = os.path.join(".git", "dualsense-deploy-key")
KNOWN_HOSTS = os.path.join(".git", "dualsense-known-hosts")
# ogólne zakończenie każdego błędu połączenia - prawdziwy powód jest w liniach przed nim
_GENERIC = ("fatal: could not read from remote repository", "please make sure you have the correct access rights",
            "and the repository exists")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_git():
    if found := shutil.which("git"):
        return found
    local = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")
    return next((p for p in (*GIT_PATHS, local) if os.path.isfile(p)), None)


def ssh_command(root):
    """GIT_SSH_COMMAND dla nadajnika: BatchMode (bez pytania o hasło - usługa nie ma terminala, git by wisiał) i klucz
    wdrożeniowy, jeśli jest. None = bez zmian (Windows: własne ssh / plink / Git Credential Manager)."""
    if os.name != "posix" or "GIT_SSH_COMMAND" in os.environ or "GIT_SSH" in os.environ:
        return None
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    key = os.path.join(root, DEPLOY_KEY)
    if os.path.isfile(key):
        cmd += ["-i", key, "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={os.path.join(root, KNOWN_HOSTS)}",
                "-o", "StrictHostKeyChecking=accept-new"]
    return " ".join(shlex.quote(c) for c in cmd)


def _owner(root):
    """Root (usługa nadajnika) uruchamia gita jako właściciel repozytorium: pliki po pull nie należą do roota,
    a git nie odmawia pracy w cudzym repozytorium ("dubious ownership"). -> kwargs dla subprocess."""
    env = dict(os.environ)
    if os.name == "posix":            # nadajnik: https bez danych logowania = błąd zamiast czekania na terminal
        env["GIT_TERMINAL_PROMPT"] = "0"  # (Windows: Git Credential Manager może pokazać okno logowania)
    if ssh := ssh_command(root):
        env["GIT_SSH_COMMAND"] = ssh
    if os.name != "posix" or os.geteuid() != 0:
        return {"env": env}
    st = os.stat(root)
    if st.st_uid == 0:
        return {"env": env}
    import pwd
    try:
        pw = pwd.getpwuid(st.st_uid)
        home, name = pw.pw_dir, pw.pw_name
    except KeyError:
        home, name = root, str(st.st_uid)
    env.update(HOME=home, USER=name, LOGNAME=name)
    # extra_groups=[]: bez grup roota (np. 0) - proces gita ma tylko uprawnienia właściciela
    return {"user": st.st_uid, "group": st.st_gid, "extra_groups": [], "env": env}


_AUTH = ("permission denied (publickey", "host key verification failed", "could not read username",
         "authentication failed", "repository not found", "terminal prompts disabled", "passphrase")


def is_auth_error(msg):
    """Błąd dostępu do prywatnego repozytorium (klucz SSH / logowanie) - rozwiązanie: sender/setup-update-key.sh."""
    m = (msg or "").lower()
    return any(k in m for k in _AUTH)


def _noise(line):
    """Linie bez powodu błędu: ogólne zakończenie błędu połączenia, podpowiedzi gita (hint:), postęp pobierania
    ("From ...", "   a1b2..c3d4  main -> origin/main", " * [new branch] ...")."""
    low = line.lower()
    return low.startswith((*_GENERIC, "hint:", "from ")) or (" -> " in line and line[:1] in " *+-=!t")


def git_error(text):
    """Powód błędu gita w jednej linii: od pierwszej linii "fatal:" / "error:" (z plikami, których dotyczy), bez
    podpowiedzi, postępu pobierania i ogólnego "Could not read from remote repository..."."""
    raw = [x.rstrip() for x in (text or "").splitlines() if x.strip()]
    useful = [x for x in raw if not _noise(x)] or raw
    start = next((k for k, x in enumerate(useful) if x.lstrip().lower().startswith(("fatal:", "error:"))), 0)
    return " / ".join(x.strip() for x in useful[start:start + 3])


def git(root, *args, git_exe=None, timeout=15, env=None):
    """-> CompletedProcess (tekst). Bez okna konsoli na Windows. env: dodatkowe zmienne środowiska dla gita."""
    kw = _owner(root)
    if env:
        kw["env"] = {**kw["env"], **env}
    return subprocess.run([git_exe or find_git() or "git", "-c", "safe.directory=*", *args], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW, **kw)


def git_version(root, git_exe=None):
    """Wersja kodu w repozytorium: (skrót commita 7 znaków, czas commita unix, tytuł commita) albo ("", 0, "")."""
    try:
        p = git(root, "log", "-1", "--format=%h%x00%ct%x00%s", "--abbrev=7", git_exe=git_exe)
        h, ts, subject = p.stdout.strip().split("\0", 2)
        return h[:7], int(ts), subject
    except (OSError, ValueError, subprocess.SubprocessError):
        return "", 0, ""


def git_pull(root, git_exe=None, env=None, timeout=120):
    """git pull --ff-only (tylko przewinięcie do wersji z GitHuba, lokalne zmiany -> błąd, nic nie nadpisane).
    env: dodatkowe zmienne dla gita (receiver: bez okien logowania - inaczej pull z procesu w tle czekał na niewidoczne
    okno Git Credential Manager do końca limitu czasu). -> (UPD_*, komunikat gita przy błędzie, stary commit, nowy
    commit, zmienione pliki)."""
    if not os.path.isdir(os.path.join(root, ".git")):
        return UPD_NOT_GIT, "", "", "", []
    git_exe = git_exe or find_git()
    if git_exe is None:
        return UPD_NO_GIT, "", "", "", []
    try:
        old = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        p = git(root, "pull", "--ff-only", git_exe=git_exe, timeout=timeout, env=env)
        if p.returncode:
            return UPD_FAILED, git_error(p.stderr or p.stdout), old, old, []
        new = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        if new == old:
            return UPD_NO_CHANGE, "", old, new, []
        files = git(root, "diff", "--name-only", old, new, git_exe=git_exe).stdout.splitlines()   # nazwy ze spacjami
    except (OSError, subprocess.SubprocessError) as e:
        return UPD_FAILED, str(e), "", "", []
    return UPD_DONE, "", old, new, files
