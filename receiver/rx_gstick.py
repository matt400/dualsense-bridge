"""Receiver: gyro -> gałka analogowa - przechył pada (akcelerometr) zamienia się na wychylenie gałki.

Czysta logika, bez Qt i bez rx_state (testy na Linuksie, wątek 250 Hz w rx_gstick_io, okno w rx_qt_gstick):
  Config     ustawienia jednego pada (zapis per MAC: rx_gstick_io, gyrostick.json obok remap.json)
  tilt_basis, angles   wektor grawitacji -> (pitch, roll) w stopniach względem środka neutralnego
  Engine     maszyna stanów: sprzęgło, uśpienie, strefa martwa ze "snap-back", filtr EMA, krzywa, sprint, próg delta
  Sink       warstwa abstrakcji wirtualnego pada (StateSink w rx_gstick_io; ViGEm / vJoy / uinput = kolejne ujścia)

Układ osi pada jak w raportach DS4 / DualSense: X w prawo, Y w górę (z płaszczyzny pada leżącego płasko), Z do gracza;
leżący pad czyta ay = +1 g. Pochylenie PRZODU w dół = pitch dodatni = gałka w górę / do przodu (Y +); przechył w prawo
(prawa strona w dół) = roll dodatni = gałka w prawo (X +). Gdyby czujniki miały inny znak (inny firmware), odwracają
to przełączniki "Odwróć pitch / roll" w oknie.

Potok jednej próbki (Engine.step, ~250 razy na sekundę):
  acc -> pitch / roll [°] względem środka -> inwersje i ratio X/Y -> wektor w stopniach (raw)
  raw w martwej strefie -> wyjście 0 od razu, filtr przejmuje raw (snap-back: żadnego śladu po filtrze)
  inaczej: EMA (wygładzanie) -> strefa martwa i zasięg (promieniowo) -> krzywa -> wyjście -1..1
Wyjście idzie do ujścia tylko, gdy zmiana przekracza próg delta (albo to zero, skraj zakresu, sprint, zmiana stanu).
"""
import math
from dataclasses import dataclass, fields, replace

from rx_buttons import BIT, BUTTON_CODES, INPUTS

# ---------------- ustawienia ----------------
TARGETS = {"left": "Lewa gałka", "right": "Prawa gałka", "dpad": "D-pad", "none": "Brak (tylko podgląd)"}
CURVES = {"linear": "Liniowa", "cubic": "Wykładnicza (sześcienna)", "aggressive": "Agresywna"}
FEEDBACKS = {"off": "Wyłączony", "haptic": "Wibracja", "led": "Kolor lightbara", "both": "Wibracja i kolor"}
CLUTCHES = ("always", *INPUTS)                          # wejście, które trzeba trzymać ("always" = bez sprzęgła)
SPRINTS = ("none", *(n for n in BUTTON_CODES if n != "ps"))   # wirtualny przycisk wciskany na skraju zakresu
# zakresy wspólne dla walidacji pliku i suwaków w oknie: nazwa -> (min, max)
LIMITS = {"deadzone": (0.0, 20.0),      # martwa strefa [°]
          "outer": (10.0, 80.0),        # przechył dający pełne wychylenie [°]
          "smoothing": (0.0, 100.0),    # wygładzanie EMA [%]: 0 = brak, 100 = stała czasowa SMOOTH_MAX_S
          "ratio": (0.5, 2.0),          # czułość pionowa względem poziomej (nadgarstki nie przechylają się tak samo)
          "idle_s": (0.0, 10.0),        # uśpienie po bezruchu [s], 0 = wyłączone
          "delta": (0.0, 5.0),          # najmniejsza zmiana wysyłana do gry [% pełnego zakresu]
          "sprint_at": (50.0, 100.0)}   # sprint od tylu % zasięgu
MIN_SPAN = 5.0                          # zasięg zawsze o tyle stopni większy niż martwa strefa
DPAD_AT = 0.5                           # tryb D-pad: kierunek od połowy wychylenia
SMOOTH_MAX_S = 0.150
I16_MAX = 32767                         # pełny zakres wyjścia -32768..+32767 (raport DS4 ma 8 bitów - ujście skaluje)


def _clamp(v, name):
    lo, hi = LIMITS[name]
    return min(hi, max(lo, v))


@dataclass(frozen=True, slots=True)
class Config:
    enabled: bool = False
    target: str = "left"            # które wirtualne wejście przejmuje gyro (fizyczne jest wtedy pomijane)
    invert_pitch: bool = False
    invert_roll: bool = False
    deadzone: float = 3.0
    outer: float = 30.0
    smoothing: float = 25.0
    ratio: float = 1.0
    curve: str = "linear"
    idle_s: float = 1.5
    delta: float = 0.8
    clutch: str = "always"
    sprint: str = "none"
    sprint_at: float = 95.0
    feedback: str = "off"
    focus_release: bool = True      # utrata fokusu / zmiana programu zeruje gałkę
    center: tuple = None            # środek neutralny: wektor grawitacji [g] z kalibracji; None = pad leży płasko


def normalize(raw):
    """Słownik z pliku -> Config; zła wartość = domyślna (komunikat w logu), nieznane klucze pomijane."""
    raw = raw if isinstance(raw, dict) else {}
    base = Config()
    values = {}
    for f in fields(Config):
        if f.name not in raw:
            continue
        v, default = raw[f.name], getattr(base, f.name)
        try:
            if f.name == "center":
                values[f.name] = _center(v)
            elif f.name in LIMITS:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
                    raise ValueError("expected a number")
                values[f.name] = round(_clamp(float(v), f.name), 3)
            elif isinstance(default, bool):
                if not isinstance(v, bool):
                    raise ValueError("expected true / false")
                values[f.name] = v
            else:
                allowed = {"target": TARGETS, "curve": CURVES, "feedback": FEEDBACKS, "clutch": CLUTCHES,
                           "sprint": SPRINTS}[f.name]
                if not isinstance(v, str) or v not in allowed:
                    raise ValueError(f"expected one of {tuple(allowed)}")
                values[f.name] = v
        except (ValueError, TypeError) as e:
            print(f"Gyro stick: bad {f.name} = {v!r} ({e}) - using {default!r}")
    cfg = replace(base, **values)
    return replace(cfg, outer=max(cfg.outer, round(cfg.deadzone + MIN_SPAN, 3)))


def _center(v):
    """[x, y, z] w g (długość około 1) albo None."""
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 3 or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in v):
        raise ValueError("expected [x, y, z]")
    c = tuple(float(x) for x in v)
    if not all(math.isfinite(x) for x in c) or not 0.6 <= math.sqrt(sum(x * x for x in c)) <= 1.4:
        raise ValueError("expected a gravity vector of about 1 g")
    return tuple(round(x, 5) for x in c)


def to_raw(cfg):
    """Config -> słownik do pliku JSON (środek tylko, gdy skalibrowany)."""
    out = {f.name: getattr(cfg, f.name) for f in fields(Config)}
    if cfg.center is None:
        del out["center"]
    else:
        out["center"] = list(cfg.center)
    return out


def to_i16(v):
    """-1..1 -> -32768..32767 (zakres z opisu funkcji; raport DS4 / DualSense ma 8 bitów, XInput / vJoy 16)."""
    return max(-32768, min(I16_MAX, round(v * (32768 if v < 0 else I16_MAX))))


# ---------------- geometria ----------------
def tilt_basis(center):
    """Środek neutralny (wektor grawitacji w g albo None = pad płasko) -> (uy, uz, pitch0, roll0).
    (uy, uz) to kierunek "w górę" w płaszczyźnie prostopadłej do osi X pada, pitch0 / roll0 - kąty środka."""
    cy, cz = (center[1], center[2]) if center else (1.0, 0.0)
    n = math.hypot(cy, cz)
    if n < 0.35:                         # środek zapisany przy padzie na sztorc - kąty nie mają sensu: pad płasko
        cy, cz, n, center = 1.0, 0.0, 1.0, None
    uy, uz = cy / n, cz / n
    p0, r0 = angles(*center, uy, uz) if center else (0.0, 0.0)
    return uy, uz, p0, r0


def angles(ax, ay, az, uy, uz):
    """Wektor grawitacji [g] -> (pitch, roll) w stopniach w układzie wyznaczonym przez (uy, uz): pitch dodatni = przód
    w dół, roll dodatni = prawa strona w dół. pitch to obrót wokół osi X, roll liczony od kierunku "w górę"."""
    gu = ay * uy + az * uz                       # składowa "w górę" (kierunek neutralny w płaszczyźnie YZ)
    gf = az * uy - ay * uz                       # składowa "do tyłu"; przód w dół = więcej "do tyłu" wskazuje w górę
    return math.degrees(math.atan2(gf, math.hypot(ax, gu))), math.degrees(math.atan2(-ax, gu))


def curve(m, name):
    """Krzywa odpowiedzi 0..1 -> 0..1: linear, cubic (precyzja przy małych przechyłach), aggressive (szybki start)."""
    if name == "cubic":
        return m * m * m
    if name == "aggressive":
        return math.sqrt(m)
    return m


def to_u8(v):
    """-1..1 -> bajt gałki raportu DS4 (128 = środek)."""
    return max(0, min(255, 128 + round(v * 127)))


def drive_state(s, gs):
    """Nakłada wyjście gyro `gs` = (cel, x, y, maska) na stan wyjściowy `s` (słownik rx_state; wołać pod lock).
    Cel przejęty przez gyro ignoruje fizyczne wejście; D-pad: kierunek od połowy wychylenia (DPAD_AT); y > 0 = góra."""
    target, x, y, mask = gs
    if target == "left":
        s["lx"], s["ly"] = to_u8(x), to_u8(-y)
    elif target == "right":
        s["rx"], s["ry"] = to_u8(x), to_u8(-y)
    elif target == "dpad":
        s["hx"] = 1 if x >= DPAD_AT else -1 if x <= -DPAD_AT else 0
        s["hy"] = -1 if y >= DPAD_AT else 1 if y <= -DPAD_AT else 0
    if mask:
        s["mask"] |= mask


def sprint_mask(name):
    """Nazwa wirtualnego przycisku sprintu -> maska bitowa raportu (0 = brak)."""
    code = BUTTON_CODES.get(name)
    return BIT[code] if code else 0


# ---------------- ujście ----------------
class Sink:
    """Warstwa abstrakcji wirtualnego pada: silnik zna tylko ten interfejs, nie ViGEm / HIDMaestro / uinput.
    drive(): gyro przejmuje wejście `target` ("left" | "right" | "dpad") wartościami x, y z -1..1 (y > 0 = góra) i trzyma
    przycisk `button` (maska bitowa, 0 = brak); release(): gyro oddaje wejście (fizyczne znów działa)."""

    def drive(self, slot, target, x, y, button):
        raise NotImplementedError

    def release(self, slot):
        raise NotImplementedError


# ---------------- maszyna stanów ----------------
ST_OFF, ST_HOLD, ST_SAFE, ST_IDLE, ST_CAL, ST_RUN = range(6)   # wyłączone / sprzęgło puszczone / czeka na wyzerowanie /
#                                                                 uśpione / kalibracja / steruje
Z_DEAD, Z_ACTIVE, Z_OUTER = range(3)                            # strefa wychylenia
EV_NONE, EV_DEAD_OUT, EV_DEAD_IN, EV_OUTER_IN, EV_OUTER_OUT = range(5)   # przejścia przez progi stref (sygnalizacja)

HYST_DEG = 0.4            # wyjście z martwej strefy dopiero o tyle poza jej brzegiem (szum czujnika na granicy)
OUTER_LEAVE = 0.9         # strefa skrajna kończy się poniżej 90 % zasięgu
SPRINT_HYST = 0.05
REARM_DEG = 2.0           # po zerowaniu (fokus, profil, uśpienie) gałka rusza dopiero, gdy pad wróci poniżej max(martwa, 2°)
STILL_DPS, STILL_G = 0.5, 0.004   # "nieruchomy": obrót poniżej 0,5 °/s i akcelerometr w granicach 0,004 g (pad na stole;
#                                   trzymany w dłoni drży mocniej, więc nie zasypia z utrzymanym przechyłem)
WAKE_DPS, WAKE_G = 2.0, 0.02      # uśpiony pad budzi dopiero wyraźny ruch
CAL_S, CAL_MIN_SAMPLES, CAL_MAX_DPS = 0.4, 20, 12.0   # kalibracja środka: czas uśredniania, minimum próbek, najwyższy obrót
MAX_DT = 0.05
FORCE = ("force",)        # znacznik: następne wyjście trzeba wysłać niezależnie od progu delta (zmiana celu itp.)


class Engine:
    """Stan jednego pada. step() raz na próbkę (wątek 250 Hz): -> True, gdy trzeba wysłać `out` do ujścia.
    out = None (gyro nie steruje - fizyczne wejście działa) albo (x, y, sprint) z x, y w -1..1 (y > 0 = góra).
    Pola do podglądu: state, zone, raw / filt [°], xy (z krzywą), mag (0..1 przed krzywą), event (przejście przez próg)."""

    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self._cfg = None                 # konfiguracja, dla której policzono cache (_apply)
        self._basis = tilt_basis(None)
        self.t = 0.0
        self.armed = False               # bez wyzerowania pada nie ma skoku gałki po włączeniu / powrocie do okna
        self.asleep = False
        self.dead = True
        self.filt = None
        self.zone = Z_DEAD
        self.sprint = False
        self.state = ST_OFF
        self.raw = self.filt_xy = self.xy = (0.0, 0.0)
        self.mag = 0.0
        self.out = None
        self.sent = None
        self.send = False
        self.event = EV_NONE
        self._ref = None                 # próbka odniesienia do wykrywania bezruchu
        self._since = 0.0
        self._cal = None                 # [koniec, sx, sy, sz, n, max_dps] w trakcie kalibracji środka
        self.cal_result = None           # (ok, środek | None) po kalibracji - odbiera rx_gstick_io

    # ---------- sterowanie z zewnątrz ----------
    @property
    def calibrating(self):
        return self._cal is not None

    def start_center(self, now, secs=CAL_S):
        """Kalibracja środka: przez `secs` s uśrednia pozycję pada (wynik: cal_result)."""
        self.cal_result = None
        self._cal = [now + secs, 0.0, 0.0, 0.0, 0, 0.0]

    def release(self, rearm=True):
        """Twarde czyszczenie: gyro oddaje wejście, filtr i stany wyzerowane; rearm - gałka rusza znów dopiero po
        powrocie pada w okolice środka (bez skoku po zmianie okna / profilu)."""
        if rearm:
            self.armed = False
        self.sent = None
        self._neutral(ST_SAFE)

    def snapshot(self):
        """Krotka dla podglądu w oknie: (stan, strefa, raw °, po filtrze °, wyjście -1..1, mag 0..1, sprint, steruje)."""
        return (self.state, self.zone, self.raw, self.filt_xy, self.xy, self.mag, self.sprint, self.out is not None)

    # ---------- wnętrze ----------
    def _apply(self, cfg):
        """Nowa konfiguracja: przelicz środek neutralny; zmiana celu / włączenia / sprintu - pełne wyzerowanie."""
        old, self._cfg = self._cfg, cfg
        if old is None or old.center != cfg.center:
            self._basis = tilt_basis(cfg.center)
        if old is None or (old.enabled, old.target, old.sprint) != (cfg.enabled, cfg.target, cfg.sprint):
            self.armed = False
            self.sprint = False
            self.sent = FORCE

    def _neutral(self, state):
        """Gyro nie steruje: wejście fizyczne działa, podgląd zerowy."""
        self.state, self.out = state, None
        self.filt = None
        self.dead, self.zone, self.sprint, self.mag = True, Z_DEAD, False, 0.0
        self.raw = self.filt_xy = self.xy = (0.0, 0.0)
        return self._decide()

    def _decide(self):
        """Czy wysłać `out`: zmiana wyjścia ponad próg delta; zawsze zero (snap-back), skraj zakresu, zmiana przycisku,
        przejęcie i oddanie wejścia."""
        out, sent = self.out, self.sent
        if sent is FORCE:
            send = True
        elif out == sent:
            send = False
        elif out is None or sent is None:
            send = True
        else:
            x, y, sp = out
            send = (sp != sent[2] or (x == 0.0 and y == 0.0) or x * x + y * y >= 0.998
                    or max(abs(x - sent[0]), abs(y - sent[1])) >= self._cfg.delta / 100)
        if send:
            self.sent = out
        self.send = send
        return send

    def _calibrate(self, now, ax, ay, az, gdps, alive):
        c = self._cal
        c[1] += ax
        c[2] += ay
        c[3] += az
        c[4] += 1
        c[5] = max(c[5], gdps)
        if now >= c[0] or not alive:
            self._cal = None
            n = c[4]
            if alive and n >= CAL_MIN_SAMPLES and c[5] <= CAL_MAX_DPS:
                try:
                    self.cal_result = (True, _center((c[1] / n, c[2] / n, c[3] / n)))
                except ValueError:
                    self.cal_result = (False, None)
            else:
                self.cal_result = (False, None)
            self.armed = False           # nowy środek: gałka rusza od zera, nie od skoku
        return self._neutral(ST_CAL)

    def _still(self, now, ax, ay, az, gdps, idle_s):
        """Uśpienie: pad nieruchomy (leży) dłużej niż idle_s -> True (zero na gałce), budzi go wyraźny ruch."""
        ref = self._ref
        dev = 1e9 if ref is None else max(abs(ax - ref[0]), abs(ay - ref[1]), abs(az - ref[2]))
        if not idle_s:
            self.asleep = False
        if self.asleep:
            if gdps > WAKE_DPS or dev > WAKE_G:
                self.asleep, self._ref, self._since = False, (ax, ay, az), now
                self.armed = False       # podniesiony przechylony pad nie wysyła skoku gałki
            return self.asleep
        if not idle_s or gdps > STILL_DPS or dev > STILL_G:
            self._ref, self._since = (ax, ay, az), now
        elif now - self._since >= idle_s:
            self.asleep = True
        return self.asleep

    def step(self, now, ax, ay, az, gdps, held, alive=True):
        """Jedna próbka: now [s, zegar monotoniczny], acc [g], gdps = wartość obrotu [°/s], held = sprzęgło trzymane,
        alive = pad podłączony. -> True, gdy trzeba wysłać `out` (sam `sent` jest już zaktualizowany)."""
        cfg = self.cfg
        if cfg is not self._cfg:
            self._apply(cfg)
        self.event = EV_NONE
        dt = min(MAX_DT, max(0.0005, now - self.t)) if self.t else 0.004
        self.t = now
        if self._cal is not None:
            return self._calibrate(now, ax, ay, az, gdps, alive)
        if not (cfg.enabled and alive):
            return self._neutral(ST_OFF)
        g2 = ax * ax + ay * ay + az * az
        if not 0.09 < g2 < 9.0:          # swobodny spadek / uderzenie: akcelerometr nie mierzy przechyłu - bez zmian
            self.send = False
            return False
        uy, uz, p0, r0 = self._basis
        p, r = angles(ax, ay, az, uy, uz)
        pitch, roll = p - p0, r - r0
        vx = -roll if cfg.invert_roll else roll
        vy = (-pitch if cfg.invert_pitch else pitch) * cfg.ratio
        if not held:
            send = self._neutral(ST_HOLD)
            self.raw = (vx, vy)          # podgląd pokazuje przechył także przy puszczonym sprzęgle
            self._ref = None             # po wciśnięciu sprzęgła odliczanie uśpienia rusza od nowa
            return send
        if self._still(now, ax, ay, az, gdps, cfg.idle_s):
            return self._neutral(ST_IDLE)
        raw_r = math.hypot(vx, vy)
        dz = cfg.deadzone
        if not self.armed:
            if raw_r >= max(dz, REARM_DEG):
                send = self._neutral(ST_SAFE)
                self.raw = (vx, vy)
                return send
            self.armed = True
        self.state = ST_RUN
        self.raw = (vx, vy)
        # snap-back: w martwej strefie wyjście jest zerem od razu, a filtr podąża za pozycją (nie ma śladu po nim)
        if self.dead:
            if raw_r > dz + HYST_DEG:
                self.dead = False
        elif raw_r < dz:
            self.dead = True
        if self.dead:
            self.filt = (vx, vy)
        else:
            tau = cfg.smoothing / 100 * SMOOTH_MAX_S
            a = 1.0 - math.exp(-dt / tau) if tau > 0 else 1.0
            fx, fy = self.filt if self.filt is not None else (vx, vy)
            self.filt = (fx + a * (vx - fx), fy + a * (vy - fy))
        fx, fy = self.filt
        self.filt_xy = (fx, fy)
        fr = math.hypot(fx, fy)
        m = 0.0 if self.dead or fr <= dz else min(1.0, (fr - dz) / (cfg.outer - dz))
        mc = curve(m, cfg.curve)
        self.xy = (fx / fr * mc, fy / fr * mc) if m > 0 else (0.0, 0.0)
        self.mag = m
        self._zones(m, cfg)
        self.out = (self.xy[0], self.xy[1], self.sprint) if cfg.target != "none" else None
        return self._decide()

    def _zones(self, m, cfg):
        """Strefa wychylenia (z histerezą skraju), zdarzenie przejścia i stan sprintu."""
        prev = self.zone
        zone = Z_DEAD if m <= 0 else Z_OUTER if m >= 1.0 or (prev == Z_OUTER and m >= OUTER_LEAVE) else Z_ACTIVE
        self.zone = zone
        if zone != prev:
            self.event = (EV_OUTER_IN if zone == Z_OUTER else EV_DEAD_IN if zone == Z_DEAD
                          else EV_DEAD_OUT if prev == Z_DEAD else EV_OUTER_OUT)
        at = cfg.sprint_at / 100
        self.sprint = cfg.sprint != "none" and (m >= at - (SPRINT_HYST if self.sprint else 0.0))
