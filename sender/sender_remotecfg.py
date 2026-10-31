"""Sender: settings changed from the receiver window (the cfgset / cfgval packets, the Sender tab).

How a change goes:
  1. The receiver sends cfgset (read / change / reset). Only a packet in protocol v3 that passed the HMAC check is obeyed: a sender
     without a key, or a packet in v2, gets a refusal (CFGR_NO_KEY / CFGR_NO_V3) and changes nothing. v3 also protects against a
     recorded packet being replayed later. remote_cfg_file = "" switches remote changes off altogether (CFGR_OFF).
  2. The value is checked against cfgspec (type and limits) and put into sender_config.CHANGES; sender_config.refresh() recomputes
     C.*, so a module that reads C.* when it needs the value sees the new one at once. What is copied at the start of something
     takes the new value the next time: the cfgspec WHEN_STREAM settings at the next haptics / speaker stream, the WHEN_PAD ones
     when a pad connects. The answer says which case it was (CFGR_OK / CFGR_LATER).
  3. The latency settings (rt_priority, low_latency, cpu_latency_us) are applied again through sender_rt.apply, the stick
     deadzone is pushed to the pads that are connected now.
  4. CHANGES go to remote_cfg_file (atomically, off the event loop), so they survive a restart. sender.toml itself is never
     written: it stays the user's file, and "reset" in the window just drops the change.
  5. The sender answers with cfgval: the value in use, the value from sender.toml and whether a change is in force.

The receiver keeps what the user chose in its own settings.json and sends the differences when the sender comes online, so a sender
with a freshly installed system gets its settings back.
"""
from __future__ import annotations

from typing import Any, Final

import cfgspec
import sender_config
import sender_net as net
import sender_rt
import sender_state as state
from proto import (CFG_ALL, CFG_GET, CFG_RESET, CFG_SET, CFGF_REMOTE, CFGR_INVALID, CFGR_LATER, CFGR_NO_KEY, CFGR_NO_V3, CFGR_OFF,
                   CFGR_OK, CFGR_UNKNOWN, T_CFGVAL)
from sender_config import C

NO_PAD: Final = 255                  # the idx of a packet that is not about one pad
REPEAT_S: Final = 0.05               # the answer to a whole read goes out a second time after this long (a lost packet)
LATENCY_KEYS: Final = frozenset({"rt_priority", "low_latency", "cpu_latency_us"})


def _number(spec: cfgspec.Spec, raw: Any) -> float:
    """The value of a setting as the packet carries it; a value the file holds in a form we cannot send falls back to the default."""
    for candidate in (raw, sender_config.DEFAULTS[spec.key]):
        try:
            if spec.kind == cfgspec.KIND_CHOICE:
                return float(spec.choices.index(str(candidate).strip().lower()))
            return float(candidate)     # a bool is 0 / 1
        except (ValueError, TypeError):
            continue
    return 0.0


def _reply(key: int, result: int, ref: int) -> None:
    """Answer about one setting (``key`` = CFG_ALL: about the whole request): the value in use, sender.toml's value, the flags."""
    if key >= len(cfgspec.SPECS):
        net.emit(T_CFGVAL, NO_PAD, key, result, ref, 0, 0.0, 0.0)
        return
    spec = cfgspec.SPECS[key]
    now = sender_config.CHANGES.get(spec.key, sender_config.BASE.get(spec.key))
    flags = CFGF_REMOTE if spec.key in sender_config.CHANGES else 0
    net.emit(T_CFGVAL, NO_PAD, key, result, ref, flags, _number(spec, now), _number(spec, sender_config.BASE.get(spec.key)))


def _send_all(ref: int) -> None:
    """One cfgval for every setting."""
    for key in range(len(cfgspec.SPECS)):
        _reply(key, CFGR_OK, ref)


def _reply_all(ref: int) -> None:
    """Answer a read: every setting now and once more shortly after (UDP); the receiver takes a repeat as the same value."""
    _send_all(ref)
    state.call_later_if_running(REPEAT_S, _send_all, ref)


def _save() -> None:
    """Write the changes to remote_cfg_file (queued for the writer thread: no fsync on the event loop); an error is only logged."""
    path = C.REMOTE_CFG_FILE
    try:
        state.write_json_later(path, {"version": 1, "values": dict(sender_config.CHANGES)}, indent=2, sort_keys=True)
    except OSError as e:
        print(f"Remote config: cannot write {path}: {e!r}")


def _after_change(keys: set[str]) -> None:
    """Things that C.* alone does not change: the latency tweaks of the process and the deadzone of the pads already connected."""
    if keys & LATENCY_KEYS:
        done = sender_rt.apply(C.LOW_LATENCY, C.CPU_LATENCY_US, C.RT_PRIORITY)
        print("Low latency (changed in the receiver window): " + (", ".join(done) if done else "nothing to change"))
    if "stick_deadzone" in keys:
        for pad in state.active.values():
            pad.refresh_deadzone()


def _set(key: int, ref: int, number: float) -> None:
    """Change one setting: check the value, apply it, save it and answer."""
    spec = cfgspec.SPECS[key]
    try:
        value = cfgspec.from_wire(spec, number)
    except ValueError:
        print(f"Remote config: {spec.key} = {number!r} refused (outside the allowed values)")
        _reply(key, CFGR_INVALID, ref)
        return
    sender_config.CHANGES[spec.key] = value
    sender_config.refresh()
    _after_change({spec.key})
    _save()
    print(f"Remote config: {spec.key} = {value!r}" + ("" if spec.when == cfgspec.WHEN_NOW else " (counts from the next " +
          ("audio stream" if spec.when == cfgspec.WHEN_STREAM else "pad connection") + ")"))
    _reply(key, CFGR_OK if spec.when == cfgspec.WHEN_NOW else CFGR_LATER, ref)


def _reset(key: int, ref: int) -> None:
    """Drop the change of one setting, or of all (CFG_ALL): the value from sender.toml (or the default) is in force again."""
    keys = set(sender_config.CHANGES) if key == CFG_ALL else {cfgspec.SPECS[key].key} & set(sender_config.CHANGES)
    for name in keys:
        del sender_config.CHANGES[name]
    if keys:
        sender_config.refresh()
        _after_change(keys)
        _save()
        print(f"Remote config: back to the config file's value: {', '.join(sorted(keys))}")
    if key == CFG_ALL:
        _reply_all(ref)
    else:
        _reply(key, CFGR_OK, ref)


def on_packet(authenticated: bool, op: int, key: int, ref: int, number: float) -> None:
    """Handle a cfgset packet (net.on_cfg).

    Args:
        authenticated: The packet is v3 and the sender has a key (so the HMAC and the freshness token were checked).
        op: CFG_GET, CFG_SET or CFG_RESET.
        key: The id of the setting (position in cfgspec.SPECS) or CFG_ALL.
        ref: The reference of the request, repeated in every answer.
        number: The new value (CFG_SET only).
    """
    if not authenticated:
        _reply(CFG_ALL, CFGR_NO_V3 if net.codec.key else CFGR_NO_KEY, ref)
        return
    if not C.REMOTE_CFG_FILE:
        _reply(CFG_ALL, CFGR_OFF, ref)
        return
    if op == CFG_GET:
        _reply_all(ref)
    elif op == CFG_SET and key < len(cfgspec.SPECS):
        _set(key, ref, number)
    elif op == CFG_RESET and (key < len(cfgspec.SPECS) or key == CFG_ALL):
        _reset(key, ref)
    else:
        _reply(key, CFGR_UNKNOWN, ref)
