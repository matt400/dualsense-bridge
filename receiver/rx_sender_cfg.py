"""Receiver: the sender's own settings, changed from the Sender tab (no Qt; the window is rx_qt_sendercfg).

What the user chose lives in settings.json (``sender.values``: only what differs from the sender's sender.toml). The sender keeps
its own copy in remote_cfg.json and applies a change at once, so this module only has to
  * ask for every setting (cfgset CFG_GET) when the sender comes online, and fill ``S.sender_cfg`` from the cfgval answers,
  * push the differences once after that ("reconcile"): a sender with a fresh system, or one whose remote_cfg.json was deleted,
    gets the settings from this PC back,
  * send a change (CFG_SET) or a return to the file's value (CFG_RESET) when the user saves the Sender tab.

Only possible with an HMAC key and protocol v3 (``can_send``): the sender refuses anything else, so we do not even try, and the
tab says why. An older sender does not know the packets and stays silent: the tab shows "no answer".

UDP loses packets: a request goes out three times (the same number, the sender handles it once), a read is repeated by the sender
and, if some settings are still missing after RETRY_S, asked for again (at most MAX_TRIES times).
"""
from __future__ import annotations

import itertools
from typing import Any, Final, NamedTuple

import cfgspec
import rx_events as ev
import rx_net
import rx_settings
import rx_state as S
from proto import (BODY, HEADER_LEN, CFG_ALL, CFG_GET, CFG_RESET, CFG_SET, CFGF_REMOTE, CFGR_INVALID, CFGR_NO_KEY, CFGR_NO_V3,
                   CFGR_OFF, CFGR_OK, CFGR_UNKNOWN, NO_PAD, T_CFGSET, T_CFGVAL)
from rx_i18n import _

RETRY_S: Final = 2.0            # a read that is still incomplete after this long is asked for again
MAX_TRIES: Final = 5
SEND_COPIES: Final = 3
REF_MASK: Final = 0xFFFF
_ref = itertools.count(1)


class Row(NamedTuple):
    """The last cfgval of one setting."""

    value: cfgspec.Value        # in use now
    base: cfgspec.Value         # the value from sender.toml (or the default): "reset" goes back to it
    remote: bool                # a change from the window is in force
    result: int                 # CFGR_* of the last answer
    t: float                    # when it arrived (monotonic)


def can_send() -> bool:
    """True when the sender would obey us: we have an HMAC key and already speak v3."""
    c = rx_net.codec
    return c is not None and bool(c.key) and c.v3


def _request(op: int, key: int, value: float = 0.0) -> None:
    """Send one cfgset (three copies of the same packet)."""
    if not can_send() or S.peer is None:
        return
    rx_net.send_repeat(rx_net.codec.pack(T_CFGSET, NO_PAD, op, key, next(_ref) & REF_MASK, value), SEND_COPIES)


def request_all() -> None:
    """Ask the sender for the value of every setting."""
    _request(CFG_GET, CFG_ALL)


def set_value(name: str, value: Any) -> None:
    """Change a setting on the sender (the value is checked first; a bad one raises ValueError)."""
    spec = cfgspec.SPECS[cfgspec.BY_KEY[name]]
    _request(CFG_SET, cfgspec.BY_KEY[name], cfgspec.to_wire(spec, cfgspec.check(spec, value)))


def reset(name: str | None = None) -> None:
    """Drop the change of one setting (``None``: of all) - the sender uses the value from its sender.toml again."""
    _request(CFG_RESET, CFG_ALL if name is None else cfgspec.BY_KEY[name])


def sync(old: rx_settings.SenderConfig, new: rx_settings.SenderConfig) -> None:
    """Send what the user changed in the Sender tab: new or changed values as SET, removed ones as RESET."""
    for name, value in new.values.items():
        if old.values.get(name) != value:
            set_value(name, value)
    for name in old.values.keys() - new.values.keys():
        reset(name)


def complete() -> bool:
    """True when the sender has answered for every setting."""
    return len(S.sender_cfg) >= len(cfgspec.SPECS)


def reconcile() -> int:
    """Push the settings chosen here that the sender does not have; returns how many were sent."""
    sent = 0
    for name, value in rx_settings.cur.sender.values.items():
        row = S.sender_cfg.get(name)
        if row is not None and (row.value != value or not row.remote):
            set_value(name, value)
            sent += 1
    if sent:
        ev.info(_("Ustawienia nadajnika z tego komputera wysłane ponownie ({n})").format(n=sent))
    return sent


def _value(spec: cfgspec.Spec, number: float) -> cfgspec.Value:
    """The value of a packet as the setting's type; a number the sender should not send falls back to the lower limit."""
    try:
        return cfgspec.from_wire(spec, number)
    except ValueError:
        return spec.choices[0] if spec.kind == cfgspec.KIND_CHOICE else spec.lo


def on_packet(data: bytes, now: float) -> None:
    """Handle a cfgval: remember the row, or the reason the sender refuses all of it (no key / not v3 / switched off)."""
    key, result, _echo, flags, number, base = BODY[T_CFGVAL].unpack_from(data, HEADER_LEN)
    if key >= len(cfgspec.SPECS):
        S.sender_cfg_result = result if result in (CFGR_NO_KEY, CFGR_NO_V3, CFGR_OFF, CFGR_UNKNOWN) else CFGR_UNKNOWN
        return
    spec = cfgspec.SPECS[key]
    S.sender_cfg_result = CFGR_OK
    S.sender_cfg[spec.key] = Row(_value(spec, number), _value(spec, base), bool(flags & CFGF_REMOTE), result, now)
    if result == CFGR_INVALID:
        ev.warn(_("Nadajnik odrzucił wartość ustawienia {name}").format(name=spec.key))
    S.sender_cfg_gen += 1


class Watch:
    """Asks for the settings when the sender comes online and pushes the differences once the answer is complete."""

    def __init__(self) -> None:
        """Start idle."""
        self.pending = False
        self.tries = 0
        self.t_ask = 0.0

    def online(self) -> None:
        """The sender came online: forget the old answers and read again."""
        S.sender_cfg.clear()
        S.sender_cfg_result = None
        self.pending, self.tries, self.t_ask = True, 0, 0.0

    def offline(self) -> None:
        """The sender went away: nothing is pending (the next online starts again)."""
        self.pending = False

    def tick(self, now: float) -> None:
        """Ask (again) until every setting has arrived, then reconcile once."""
        if not self.pending:
            return
        if complete():
            self.pending = False
            reconcile()
        elif S.sender_cfg_result not in (None, CFGR_OK):
            self.pending = False            # a refusal: nothing to retry
        elif now - self.t_ask >= RETRY_S and self.tries < MAX_TRIES:
            self.tries += 1
            self.t_ask = now
            if can_send():
                request_all()
            else:
                self.pending = False

