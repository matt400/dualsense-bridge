"""Receiver: a temporary swap of the players (P1 <-> P2) while exactly two pads are connected.

Steam / Windows number the players by the order in which the virtual pads were plugged in, so the swap never touches the
virtual pads: it only changes which physical pad feeds which virtual one. Nothing is unplugged or re-plugged, so a game
sees no disconnect and keeps its own player assignment.

  route(i)   the partner slot while the swap is on, else i. A swap is its own inverse, so one function serves both ways:
             virtual pad j is fed by physical pad route(j), and a change of physical pad i goes to virtual pad route(i)
             (rx_ds4.push / Out.send), the game's rumble / lightbar / trigger writes for virtual pad j go to physical pad
             route(j) (rx_feedback.game, make_cb).
  toggle()   on / off (a master action, see rx_master); refused unless exactly two pads are connected
  end()      back to normal; called when a pad connects or leaves, so the swap never outlives the pair it was made for

Per-pad things (remap profile, tuning, tilt-to-stick, lightbar, battery) stay with the physical pad - the pad is simply
"another player" for a while. The state of the game feedback (rumble, colour, trigger effects) belongs to the virtual pad,
so it changes hands together with the routing (_exchange_feedback). The swap is not saved: a restart starts without it.

route() is on the hot path (every packet): it reads one global and allocates nothing.
"""
from __future__ import annotations

import threading
from typing import Final

import rx_events as ev
import rx_state as S
from rx_i18n import _

PLAYERS: Final = 2                      # the swap exists only for exactly this many connected pads
pair: tuple[int, int] | None = None     # the two swapped slots (ascending) or None
_lock = threading.Lock()                # toggle / end from different threads (a master action thread, the network thread)


def route(i: int) -> int:
    """Return the slot that the virtual pad / physical pad i is wired to (i itself when there is no swap)."""
    p = pair
    if p is None:
        return i
    return p[1] if i == p[0] else p[0] if i == p[1] else i


def active() -> bool:
    """Tell whether the players are swapped right now."""
    return pair is not None


def connected() -> list[int]:
    """Return the slots whose pad is connected (the ones that have a virtual pad of their own)."""
    return [i for i, w in enumerate(S.want) if w.is_set()]


def _exchange_feedback(a: int, b: int) -> None:
    """Hand the game feedback of the two slots over (it belongs to the virtual pad, not to the physical one).

    Without it a running rumble would stay on the old physical pad until the game happened to resend it, and the
    sender would be told to keep a stale colour / trigger effect. The cached "what was sent last" values are dropped so the
    feedback thread sends the new situation at once.
    """
    import rx_feedback as F                 # rx_feedback imports route() from here - import here
    F.fb[a], F.fb[b] = F.fb[b], F.fb[a]
    for sent in (F.fb_sent, F.trig_sent):
        sent[a] = sent[b] = None
    F.fb_event.set()


def _flip(new: tuple[int, int] | None) -> None:
    """Switch the routing to `new` (None = off) and refresh both virtual pads; call under _lock."""
    global pair
    target = new or pair
    if target is None:
        return
    a, b = target
    pair = new
    _exchange_feedback(a, b)
    for i in (a, b):                    # send the new situation to both virtual pads at once, without waiting for a change
        S.notify(i)


def toggle() -> tuple[bool, str]:
    """Turn the swap on (exactly two pads connected) or off; returns (ok, message for the log)."""
    with _lock:
        if pair is not None:
            _flip(None)
            ev.info(_("Gracze wrócili na swoje miejsca (P1 i P2 jak na początku)"))
            return True, "off"
        pads = connected()
        if len(pads) != PLAYERS:
            return False, _("Zamiana graczy działa tylko przy dwóch połączonych padach")
        a, b = pads
        _flip((a, b))
        ev.info(_("Zamieniono graczy: {a} steruje teraz jako P{pb}, {b} jako P{pa}").format(
            a=ev.pad_name(a), b=ev.pad_name(b), pa=a + 1, pb=b + 1))
        return True, "on"


def end(why: str = "") -> None:
    """Go back to normal (a pad connected or left - the pair the swap was made for no longer exists); a no-op without a swap."""
    with _lock:
        if pair is None:
            return
        _flip(None)
        ev.info(_("Zamiana graczy wyłączona ({why})").format(why=why))
