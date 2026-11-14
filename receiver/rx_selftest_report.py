"""Receiver: the report of the pad self-test (no Qt) - a fixed, versioned text for Claude: Markdown tables and one JSON block.

The text is always English, whatever language the window speaks: it is read by a person pasting it into a chat, not shown in the window.
The layout is fixed (``REPORT_VERSION``): a change that renames or moves a field bumps the version, so an old report is still readable.

    # Pad self-test report (version 1)
    summary line, the limits of the measurement, a table of the steps, a few small tables with the numbers that matter,
    then ```json with everything (the same numbers, every series with n / percentiles).

Every measured series carries its sample count and percentiles (never just a mean); the latency tables say the window and the sample count.
Files go to ``reports/`` next to settings.json (``save``), written with proto.write_file.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

import rx_selftest as T
from proto import write_file
from rx_config import C

REPORT_VERSION: Final = 1
SCHEMA: Final = f"dualsense-bridge/selftest/{REPORT_VERSION}"
FILE_PREFIX: Final = "selftest-"
LIMITS: Final = (
    ("Vibration times run from the PC's send to the END OF THE WRITE to the pad on the sender (hidraw write returned / force-feedback "
     "call returned). When the motor really spins, and the Bluetooth delay to the pad, are not covered."),
    ("Sender timestamps are converted to the PC clock with the offset from the pong2 packets; a segment that spans both machines "
     "(to_sender, total) carries an error of up to half the round trip time (see idle.rtt_ms)."),
    "The audio step measures the path (frames, buffers, underruns, overruns, write times), not the sound: crackling is judged by ear.",
    "The microphone is not supported and is not measured.",
    "The radio channel / band is not known to the sender and is not reported.",
)


def reports_dir() -> str:
    """The folder of the reports: ``reports/`` next to settings.json."""
    return os.path.join(os.path.dirname(C.SETTINGS_PATH), "reports")


def build(run: T.SelfTest) -> dict[str, Any]:
    """The report data of a finished (or aborted) run: plain numbers, strings, lists and dicts - it goes straight into the JSON block."""
    steps = {r.id: {"status": r.status, "seconds": r.seconds, **({"note": r.note} if r.note else {}), **r.data} for r in run.results}
    outcome = "aborted" if run.error == "aborted" else "error" if run.error else "complete"
    return {
        "schema": SCHEMA,
        "report_version": REPORT_VERSION,
        "started": run.started_at.isoformat(timespec="seconds"),
        "result": outcome,
        "slot": run.slot,
        "plan": {"latency_window_s": run.plan.window_s, "idle_s": run.plan.idle_s, "pulse_s": run.plan.pulse_s,
                 "levels": list(run.plan.levels)},
        "limits": list(LIMITS),
        "steps": steps,
        **({"error": run.error} if run.error and run.error != "aborted" else {}),
    }


def _num(v: Any, digits: int = 1) -> str:
    """A number for a table cell: ``-`` for nothing, no trailing zeros."""
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits else str(round(v))
    return str(v)


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    """A Markdown table (empty list for no rows)."""
    if not rows:
        return []
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_num(c) for c in row) + " |" for row in rows]
    return [*out, ""]


def _latency_rows(block: Mapping[str, Any] | None) -> list[list[Any]]:
    """The rows (segment, n, p50, p95, p99, max) of one latency block."""
    if not block:
        return []
    rows = []
    for seg in ("total", "bt", "phone", "net", "pc"):
        p = block.get(seg)
        if p:
            rows.append([seg, p["n"], p["p50"], p["p95"], p["p99"], p["max"]])
    return rows


def _series_rows(series: Mapping[str, Mapping[str, Any]]) -> list[list[Any]]:
    """The rows (name, n, min, p50, p95, max) of a dict of series."""
    return [[k, v["n"], v.get("min"), v.get("p50"), v.get("p95"), v.get("max")] for k, v in series.items() if v.get("n")]


def _steps_table(steps: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Which step ran and how it ended."""
    return _table(["step", "status", "s", "note"], [[sid, st["status"], st["seconds"], st.get("note", "")] for sid, st in steps.items()])


def _vibration_lines(v: Mapping[str, Any]) -> list[str]:
    """The vibration tables: start / stop writes per way, the watchdog, the game cases."""
    out: list[str] = []
    pulses = v.get("pulses") or {}
    for name in ("start", "stop"):
        for way, segs in (pulses.get(name) or {}).items():
            out += [f"**{name}, {way}** (ms, our send -> end of the write)", ""]
            out += _table(["segment", "n", "min", "p50", "p95", "max"], _series_rows(segs))
    if pulses:
        out += [(f"pulses sent {pulses.get('sent')}, no record from the sender {pulses.get('unmatched')}, writes that failed "
                 f"{pulses.get('failed')}, refreshes {pulses.get('refresh')}"), ""]
    wd = v.get("watchdog")
    if wd:
        out += [(f"watchdog: expected {_num(wd['expected_ms'])} ms after the last command, measured "
                 f"{json.dumps(wd['stop_after_last_command_ms'])}"), ""]
    for key in ("game_stale", "game_gap"):
        g = v.get(key)
        if g:
            out += [f"{key}: {json.dumps(g)}", ""]
    return out


def _audio_lines(a: Mapping[str, Any]) -> list[str]:
    """One line per audio path with the counters of both ends."""
    out = [f"_{a.get('note', '')}_", ""]
    for name in ("haptics", "speaker"):
        p = a.get(name)
        if p:
            out += [f"**{name}**: {json.dumps(p)}", ""]
    return out


def render(data: Mapping[str, Any]) -> str:
    """The report as text: Markdown, then the JSON block with all the data."""
    steps = data["steps"]
    meta = steps.get("meta", {})
    ways = "+".join(meta.get("rumble_ways") or []) or "none"
    lines = [f"# Pad self-test report (version {data['report_version']})", "",
             (f"{data['started']}, pad slot {data['slot']}, result: {data['result']}. Sender v{meta.get('sender_version')}, "
              f"receiver {meta.get('receiver_commit')}, sender {meta.get('sender_commit')}, {meta.get('pad_family')}, "
              f"virtual pad: {meta.get('backend')}, rumble: {ways}."), "",
             "## Limits", ""]
    lines += [*(f"- {text}" for text in data["limits"]), "", "## Steps", "", *_steps_table(steps)]
    idle = steps.get("idle", {})
    if idle.get("series"):
        lines += [f"## Idle ({idle['seconds']} s at rest)", "",
                  *_table(["series", "n", "min", "p50", "p95", "max"], _series_rows(idle["series"]))]
        lines += [f"lost {idle['lost']}, late {idle['late']}, duplicated {idle['duplicated']}", ""]
    lat = steps.get("latency", {})
    if lat.get("samples"):
        lines += [f"## Latency (window {lat['window_s']} s, {lat['samples']} samples, ms)", "", "all state changes:", ""]
        lines += _table(["segment", "n", "p50", "p95", "p99", "max"], _latency_rows(lat.get("all_changes")))
        if lat.get("buttons"):
            lines += [f"buttons only ({lat['button_samples']} samples):", ""]
            lines += _table(["segment", "n", "p50", "p95", "p99", "max"], _latency_rows(lat["buttons"]))
    if steps.get("vibration", {}).get("pulses"):
        lines += ["## Vibration", "", *_vibration_lines(steps["vibration"])]
    if steps.get("audio", {}).get("haptics") or steps.get("audio", {}).get("speaker"):
        lines += ["## Audio path", "", *_audio_lines(steps["audio"])]
    if steps.get("cpu", {}).get("at_end"):
        lines += ["## Sender CPU and writes", "", json.dumps(steps["cpu"]), ""]
    lines += ["## JSON", "", "```json", json.dumps(data, indent=1, sort_keys=False), "```", ""]
    return "\n".join(lines)


def file_name(when: datetime) -> str:
    """``selftest-YYYY-MM-DD-HHMMSS.md``."""
    return f"{FILE_PREFIX}{when:%Y-%m-%d-%H%M%S}.md"


def save(text: str, when: datetime) -> str:
    """Write the report into ``reports/`` (created when missing) and return its path."""
    folder = reports_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, file_name(when))
    write_file(path, text)
    return path
