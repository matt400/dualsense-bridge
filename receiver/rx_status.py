"""Receiver: teksty i poziomy jakości do okna (łącze, bateria) - czyste funkcje, bez Qt, łatwe do przetestowania."""
from rx_i18n import _
import rx_latency
import rx_state as S
from proto import CAP_TRIG_FX, CAP_TRIGGERS, RUMBLE_EVDEV, RUMBLE_HIDRAW, WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OFF

INFO_MAX_AGE = 3.0   # pakiet "info" starszy niż tyle = brak danych o pingu i Hz
LAT_WARN_MS, LAT_BAD_MS = 6.0, 15.0     # opóźnienie pad -> gra (średnia): powyżej = żółty / czerwony


def fresh_info(i, now):
    inf = S.info[i]
    return inf if inf and now - inf[3] < INFO_MAX_AGE else None


def link_parts(i, now):
    """[(rodzaj, tekst)] dla podłączonego pada: ping i Hz z nadajnika, utrata i pakiety/s liczone tutaj."""
    if not S.alive[i]:
        return []
    parts = []
    inf = fresh_info(i, now)
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


def ping_text(i, now):
    """"ping 1.2 ms" (karta pada, gdy nie ma jeszcze pomiaru opóźnienia pad -> gra) albo "—"."""
    inf = fresh_info(i, now)
    return _("ping {ms} ms").format(ms=f"{inf[0]:.1f}") if inf and inf[0] is not None else "—"


def link_level(i, now):
    """"ok" = dobrze, "warn" = zauważalne (ping > 5 ms / strata > 1 %), "bad" = źle (ping > 20 ms / strata > 5 %)."""
    inf = fresh_info(i, now)
    rtt = (inf[0] if inf else None) or 0
    loss = S.link_loss or 0.0
    if rtt > 20 or loss > 5:
        return "bad"
    if rtt > 5 or loss > 1:
        return "warn"
    return "ok"


def latency(i):
    """Opóźnienie pad -> gra z ostatnich sekund (rx_latency.Summary) albo None (brak próbek / pad odłączony)."""
    return rx_latency.summary(i) if S.alive[i] else None


def button_latency(i):
    """Opóźnienie przycisk -> gra z ostatnich naciśnięć (rx_latency.Summary) albo None (pad odłączony / brak naciśnięć).
    Zostaje po chwilowej ciszy pada - liczy się tylko przy zmianie pada w slocie albo resecie z okna."""
    return rx_latency.buttons(i) if S.alive[i] else None


def lat_level(summary):
    """Poziom jakości samego opóźnienia (średnia): "ok" | "warn" | "bad" | "" (brak pomiaru)."""
    if summary is None:
        return ""
    return "bad" if summary.avg > LAT_BAD_MS else "warn" if summary.avg > LAT_WARN_MS else "ok"


def latency_level(summary, i, now):
    """Poziom jakości z opóźnienia (średnia) i utraty pakietów: "ok" | "warn" | "bad"."""
    order = ("ok", "warn", "bad")
    return max((x for x in (link_level(i, now), lat_level(summary)) if x), key=order.index, default="ok")


def stats_text(summary):
    """"p95 3.1 · max 4.0" - druga, mała część napisu opóźnienia na karcie pada."""
    return f"p95 {summary.p95:.1f} · max {summary.max:.1f}"


def latency_parts(summary):
    """[(klucz odcinka, opis, ms)] w kolejności drogi sygnału; Bluetooth tylko, gdy nadajnik go mierzy."""
    rows = [("bt", _("Bluetooth (ponad minimum)"), summary.bt), ("phone", _("Telefon (jądro → wysłanie)"), summary.phone),
            ("net", _("Sieć"), summary.net), ("pc", _("PC (odbiór → wirtualny DS4)"), summary.pc)]
    return [r for r in rows if r[2] is not None]


def low_pct():
    """Próg niskiej baterii [%] - wspólny dla ostrzeżenia na karcie i migania lightbara (Ustawienia -> Lightbar)."""
    import rx_settings
    return rx_settings.cur.lightbar.low_pct


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
    bits, why = info[1] & (RUMBLE_EVDEV | RUMBLE_HIDRAW), info[2]     # reszta bitów to inne możliwości nadajnika
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


def triggers_status(i, fx=False):
    """Czy nadajnik ustawi efekty spustów temu padowi -> (tekst, poziom); ("", "") = działa albo pad niepodłączony.
    fx: profil używa kliku / wibracji spustu / wibracji z gry (trzeba nadajnika z CAP_TRIG_FX)."""
    if not S.alive[i]:
        return "", ""
    info = S.sender_info[i]
    if info is None or info[0] < 6:
        return _("Nadajnik w starszej wersji nie ustawia oporu - kliknij Aktualizuj."), "warn"
    if not info[1] & CAP_TRIGGERS:
        return _("Opór wymaga raportu HID na telefonie (jak wibracja przez HID) - nadajnik go nie ma."), "warn"
    if fx and not info[1] & CAP_TRIG_FX:
        return _("Klik i wibracja spustu wymagają nowszego nadajnika - kliknij Aktualizuj (opór działa)."), "warn"
    import rx_master        # leniwie: rx_master ciągnie wątki i WinAPI, a status jest czytany też bez nich
    if not rx_master.allowed(i, "triggers"):
        return _("Efekty spustów są tylko dla pada master (Ustawienia -> Master)."), "warn"
    return "", ""
