"""Receiver: texts and quality levels for the window (link, battery) - pure functions, no Qt, easy to test."""
from rx_i18n import _
import rx_latency
import rx_state as S
from proto import CAP_TRIG_FX, CAP_TRIGGERS, RUMBLE_EVDEV, RUMBLE_HIDRAW, WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OFF

INFO_MAX_AGE = 3.0   # an "info" packet older than this = no ping / Hz data
LAT_WARN_MS, LAT_BAD_MS = 6.0, 15.0     # pad -> game latency (mean): above = yellow / red


def fresh_info(i, now):
    """The latest "info" packet of the sender (rtt, pad Hz, motion Hz, receive time) or None when it is too old."""
    inf = S.info[i]
    return inf if inf and now - inf[3] < INFO_MAX_AGE else None


def link_parts(i, now):
    """[(kind, text)] for a connected pad: ping and Hz come from the sender, loss and packets/s are computed here."""
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
    """The link parts joined into one line."""
    return "  ·  ".join(t for _, t in link_parts(i, now))


def ping_text(i, now):
    """"ping 1.2 ms" (pad card, while there is no pad -> game latency measurement yet) or "—"."""
    inf = fresh_info(i, now)
    return _("ping {ms} ms").format(ms=f"{inf[0]:.1f}") if inf and inf[0] is not None else "—"


def link_level(i, now) -> str:
    """"ok" = good, "warn" = noticeable (ping > 5 ms / loss > 1 %), "bad" = poor (ping > 20 ms / loss > 5 %)."""
    inf = fresh_info(i, now)
    rtt = (inf[0] if inf else None) or 0
    loss = S.link_loss or 0.0
    if rtt > 20 or loss > 5:
        return "bad"
    if rtt > 5 or loss > 1:
        return "warn"
    return "ok"


def latency(i):
    """Pad -> game latency over the last seconds (rx_latency.Summary) or None (no samples / pad disconnected)."""
    return rx_latency.summary(i) if S.alive[i] else None


def button_latency(i):
    """Button -> game latency over the last presses (rx_latency.Summary) or None (pad disconnected / no presses).
    It survives a short silence of the pad - it restarts only when the pad in the slot changes or on a reset from the window.
    """
    return rx_latency.buttons(i) if S.alive[i] else None


def lat_level(summary) -> str:
    """Quality level of the latency alone (mean): "ok" | "warn" | "bad" | "" (no measurement)."""
    if summary is None:
        return ""
    return "bad" if summary.avg > LAT_BAD_MS else "warn" if summary.avg > LAT_WARN_MS else "ok"


def latency_level(summary, i, now):
    """Quality level from the latency (mean) and the packet loss: "ok" | "warn" | "bad"."""
    order = ("ok", "warn", "bad")
    return max((x for x in (link_level(i, now), lat_level(summary)) if x), key=order.index, default="ok")


def stats_text(summary):
    """"usually <= 3.1 · max 4.0" - the second, small part of the latency label on the pad card. "usually" = the 95th percentile
    (95 of 100 measurements no slower) - "p95" meant nothing to the user; the explanation is in the tooltip.
    """
    return _("zwykle ≤ {p95} · max {max}").format(p95=f"{summary.p95:.1f}", max=f"{summary.max:.1f}")


def latency_parts(summary):
    """[(segment key, description, ms)] in the order of the signal path; Bluetooth only when the sender measures it."""
    rows = [("bt", _("Bluetooth (ponad minimum)"), summary.bt), ("phone", _("Telefon (jądro → wysłanie)"), summary.phone),
            ("net", _("Sieć"), summary.net), ("pc", _("PC (odbiór → wirtualny pad)"), summary.pc)]
    return [r for r in rows if r[2] is not None]


def low_pct():
    """Low-battery threshold [%] - shared by the card warning and the lightbar blinking (Settings -> Lightbar)."""
    import rx_settings
    return rx_settings.cur.lightbar.low_pct


def battery_level(pct, charging, low_batt) -> str:
    """"charge" | "bad" (below the threshold) | "warn" (up to 30 %) | "ok"."""
    return "charge" if charging else "bad" if pct <= low_batt else "warn" if pct <= 30 else "ok"


def is_low(i, low_batt):
    """The pad is connected, the battery is known, it is not charging and it is below the threshold."""
    pct, flags = S.batt_info[i]
    return bool(S.alive[i]) and pct <= 100 and not flags & 1 and pct <= low_batt



def rumble_status(i):
    """Pad rumble according to the sender -> (text, level, tooltip) for the card in the window."""
    info = S.sender_info[i]
    if info is None:
        return (_("wibracja: stary nadajnik"), "warn",
                _("Skopiuj nowe pliki sender/ na telefon - starszy nadajnik nie wibruje bez obsługi wibracji w jądrze."))
    bits, why = info[1] & (RUMBLE_EVDEV | RUMBLE_HIDRAW), info[2]     # the remaining bits are other capabilities of the sender
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
    """Whether the sender will set trigger effects for this pad -> (text, level); ("", "") = works or the pad is disconnected.
    fx: the profile uses click / trigger vibration / game vibration (needs a sender with CAP_TRIG_FX).
    """
    if not S.alive[i]:
        return "", ""
    info = S.sender_info[i]
    if info is None or info[0] < 6:
        return _("Nadajnik w starszej wersji nie ustawia oporu - kliknij Aktualizuj."), "warn"
    if not info[1] & CAP_TRIGGERS:
        return _("Opór wymaga raportu HID na telefonie (jak wibracja przez HID) - nadajnik go nie ma."), "warn"
    if fx and not info[1] & CAP_TRIG_FX:
        return _("Klik i wibracja spustu wymagają nowszego nadajnika - kliknij Aktualizuj (opór działa)."), "warn"
    import rx_master        # lazily: rx_master pulls in threads and WinAPI, and the status is also read without them
    if not rx_master.allowed(i, "triggers"):
        return _("Efekty spustów są tylko dla pada master (Ustawienia -> Master)."), "warn"
    return "", ""
