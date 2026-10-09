"""Receiver: teksty i poziomy jakości do okna (łącze, bateria) - czyste funkcje, bez Qt, łatwe do przetestowania."""
import rx_state as S

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
        parts.append(("ping", f"ping {rtt:.1f} ms" if rtt is not None else "ping —"))
        parts.append(("hz", f"pad {pad_hz} Hz"))
    if S.link_loss is not None:
        parts.append(("loss", f"loss {S.link_loss:.1f}%"))
    parts.append(("pps", f"{S.pps[i]:.0f} pkt/s"))
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
