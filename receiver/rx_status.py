"""Receiver: teksty i poziomy jakości do okna (łącze, bateria) - czyste funkcje, bez Qt, łatwe do przetestowania."""
from rx_i18n import _
import rx_state as S
from proto import RUMBLE_EVDEV, RUMBLE_HIDRAW, WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OFF

INFO_MAX_AGE = 3.0   # pakiet "info" starszy niż tyle = brak danych o pingu i Hz


def _info(i, now):
    inf = S.info[i]
    return inf if inf and now - inf[3] < INFO_MAX_AGE else None


def link_parts(i, now):
    """[(rodzaj, tekst)] dla podłączonego pada: ping i Hz z nadajnika, utrata i pakiety/s liczone tutaj."""
    if not S.alive[i]:
        return []
    parts = []
    inf = _info(i, now)
    if inf:
        rtt, pad_hz, _motion_hz, _t = inf
        parts.append(("ping", _("ping {ms} ms").format(ms=f"{rtt:.1f}") if rtt is not None else "ping —"))
        parts.append(("hz", _("pad {hz} Hz").format(hz=pad_hz)))
    if S.link_loss is not None:
        parts.append(("loss", _("strata {pct}%").format(pct=f"{S.link_loss:.1f}")))
    parts.append(("pps", _("{n} pkt/s").format(n=f"{S.pps[i]:.0f}")))
    return parts


def link_text(i, now):
    return "  ·  ".join(t for _, t in link_parts(i, now))


def link_lines(i, now):
    """Te same dane w dwóch krótkich liniach (karta pada jest wąska): sieć (ping, utrata) i tempo (Hz, pakiety/s)."""
    parts = link_parts(i, now)
    return ("  ·  ".join(t for k, t in parts if k in ("ping", "loss")),
            "  ·  ".join(t for k, t in parts if k in ("hz", "pps")))


def link_level(i, now):
    """"ok" = dobrze, "warn" = zauważalne (ping > 5 ms / strata > 1 %), "bad" = źle (ping > 20 ms / strata > 5 %)."""
    inf = _info(i, now)
    rtt = (inf[0] if inf else None) or 0
    loss = S.link_loss or 0.0
    if rtt > 20 or loss > 5:
        return "bad"
    if rtt > 5 or loss > 1:
        return "warn"
    return "ok"


def battery_level(pct, charging, low_batt):
    """"charge" | "bad" (poniżej progu) | "warn" (do 30 %) | "ok"."""
    return "charge" if charging else "bad" if pct <= low_batt else "warn" if pct <= 30 else "ok"


def is_low(i, low_batt):
    """Pad podłączony, bateria znana, nie ładuje się i jest poniżej progu."""
    pct, flags = S.batt_info[i]
    return bool(S.alive[i]) and pct <= 100 and not flags & 1 and pct <= low_batt



def rumble_status(i):
    """Wibracja pada wg nadajnika -> (tekst, poziom, podpowiedź) dla karty w oknie."""
    info = S.sender_info[i]
    if info is None:
        return (_("wibracja: stary nadajnik"), "warn",
                _("Skopiuj nowe pliki sender/ na telefon - starszy nadajnik nie wibruje bez obsługi wibracji w jądrze."))
    bits, why = info[1], info[2]
    if not bits:
        text, tip = {
            WHY_OFF: (_("wibracja: wyłączona"), _("W sender.toml ustaw rumble = \"auto\".")),
            WHY_ACCESS: (_("wibracja: brak uprawnień"),
                         _("Nadajnik nie może otworzyć /dev/hidraw - uruchom go przez doas albo zainstaluj usługę: "
                           "doas ./install-service.sh")),
            WHY_NO_HIDRAW: (_("wibracja: brak w jądrze"),
                            _("Jądro telefonu nie ma CONFIG_PLAYSTATION_FF ani CONFIG_HIDRAW - bez jednej z nich pad nie wibruje.")),
            WHY_NOT_FOUND: (_("wibracja: nie znaleziono pada"),
                            _("Nadajnik nie znalazł /dev/hidraw tego pada - na telefonie: doas python3 sender_rumble.py --test")),
        }.get(why, (_("wibracja: niedostępna"), _("Na telefonie: doas python3 sender_rumble.py --test")))
        return text, "bad", tip
    ways = [w for b, w in ((RUMBLE_EVDEV, _("sterownik")), (RUMBLE_HIDRAW, "HID")) if bits & b]
    return _("wibracja: {ways}").format(ways=" + ".join(ways)), "", ""
