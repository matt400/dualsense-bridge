#!/usr/bin/env python3
"""Throwaway probe: how does a Bluetooth DualSense deliver its microphone? (Linux, stdlib only, run with doas.)

  doas python3 mic_probe.py listen            # 1) passive: which input reports arrive, ids / sizes / rates
  doas python3 mic_probe.py enable [variant]  # 2) turn the mic on with a 0x31 output report, then capture reports
  doas python3 mic_probe.py matrix            # 3) every variant in turn, asks y/n whether the mic seems to deliver data
  doas python3 mic_probe.py mic36             # 4) report 0x36 (the speaker's report) with config / extra sub-packet variants;
                                              #    talk CONTINUOUSLY until it ends (~1.5 min), judged automatically

Each capture is saved to mic_probe_<name>.bin (length-prefixed raw reports) and a summary goes to
mic_probe_results.json - paste that file back. During a capture TALK or TAP the pad's mic hole for the full time,
the script then compares busy and quiet bytes to find where audio could live. No imports from the repo on purpose.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import select
import struct
import sys
import time
import zlib

DUALSENSE = (0x054C, (0x0CE6, 0x0DF2))
CAPTURE_S = 6.0
RESULTS = "mic_probe_results.json"

# 0x31 common part offsets (hid-playstation): valid_flag0=0, valid_flag1=1, headphone=4, speaker=5, mic volume=6,
# audio_flags=7. valid_flag0: 0x10 headphone vol, 0x20 speaker vol, 0x40 mic vol, 0x80 audio control.
# audio_flags: bits 0-1 = input path (0 = auto, 1 = headset mic, 2 = internal mic), bits 4-5 = output path.
VARIANTS: dict[str, dict[int, int]] = {
    "mic_vol_only": {0: 0x40, 6: 0x40},
    "mic_vol_max": {0: 0x40, 6: 0xFF},
    "internal_mic_path": {0: 0xC0, 6: 0x40, 7: 0x02},
    "internal_mic_path_speaker": {0: 0xF0, 4: 0x40, 5: 0x70, 6: 0x40, 7: 0x32},
    "headset_mic_path": {0: 0xC0, 6: 0x40, 7: 0x01},
    "audio_auto": {0: 0xC0, 6: 0x40, 7: 0x00},
}


def find_hidraw() -> str:
    """Return the hidraw node of the first Bluetooth DualSense."""
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(os.path.join(node, "device", "uevent")).read()
        except OSError:
            continue
        for line in uevent.splitlines():
            if line.startswith("HID_ID=0005:"):          # bus 5 = Bluetooth
                _, vid, pid = line.split("=")[1].split(":")
                if int(vid, 16) == DUALSENSE[0] and int(pid, 16) in DUALSENSE[1]:
                    return "/dev/" + os.path.basename(node)
    raise SystemExit("No Bluetooth DualSense hidraw found (is the pad connected? run with doas)")


def output_31(variant: dict[int, int], seq: int) -> bytes:
    """Build a 78-byte BT output report 0x31 with the given common-part bytes set."""
    buf = bytearray(78)
    buf[0] = 0x31
    buf[1] = (seq & 0x0F) << 4
    buf[2] = 0x10
    for off, val in variant.items():
        buf[3 + off] = val
    buf[74:78] = struct.pack("<I", zlib.crc32(bytes([0xA2]) + bytes(buf[:74])))
    return bytes(buf)


def report_36(cfg: bytes, extra: bytes, seq: int) -> bytes:
    """Build a 398-byte BT output report 0x36: config (0x91, 7 B), zero haptics (0x92, 64 B), optional extra sub-packets."""
    buf = bytearray(398)
    buf[0] = 0x36
    buf[1] = (seq & 0x0F) << 4
    body = b"\x91\x07" + cfg + b"\x92\x40" + bytes(64) + extra
    buf[2:2 + len(body)] = body
    buf[394:398] = struct.pack("<I", zlib.crc32(bytes([0xA2]) + bytes(buf[:394])))
    return bytes(buf)


def mic36_variants() -> dict[str, tuple[bytes, bytes]]:
    """Name -> (7 config bytes, extra sub-packets). The known-good config is FE 10 10 10 10 10 <counter>."""
    base = [0xFE, 0x10, 0x10, 0x10, 0x10, 0x10]
    out: dict[str, tuple[bytes, bytes]] = {"baseline": (bytes(base) + b"\x00", b"")}
    out["cfg0=FF"] = (bytes([0xFF, *base[1:]]) + b"\x00", b"")
    for pos in range(1, 6):
        for val in (0x11, 0x12, 0x14, 0x30):
            cfg = list(base)
            cfg[pos] = val
            out[f"cfg{pos}={val:02X}"] = (bytes(cfg) + b"\x00", b"")
    for pid in range(0x14, 0x19):                    # unknown sub-packet ids, sized, 4 payload bytes
        out[f"extra_{pid:02X}"] = (bytes(base) + b"\x00", bytes([0x80 | pid, 4, 1, 0, 0, 0]))
    out["state_10"] = (bytes(base) + b"\x00", b"\x90\x2f" + bytes(47))
    return out


def run_mic36(fd: int) -> None:
    """Stream each 0x36 variant at 10.667 ms for 3 s while the user talks; flag new report ids / sizes / busy offsets."""
    variants = mic36_variants()
    input(f"{len(variants)} variants x 3 s. Press Enter, then TALK / tap the mic hole non-stop until it says done ...")
    base_busy: set[int] = set()
    results: dict[str, object] = {}
    seq = 0
    for name, (cfg, extra) in variants.items():
        rows: list[bytes] = []
        t0 = time.monotonic()
        nxt = t0
        while (now := time.monotonic()) - t0 < 3.0:
            if now >= nxt:
                seq += 1
                os.write(fd, report_36(cfg[:6] + bytes([seq & 0xFF]), extra, seq))
                nxt += 0.010667
            if select.select([fd], [], [], max(0.0, min(0.002, nxt - time.monotonic())))[0]:
                rows.append(os.read(fd, 4096))
        ids = sorted({(r[0], len(r)) for r in rows})
        r31 = [r for r in rows if r[0] == 0x31 and len(r) == 78]
        busy = {o for o in range(78) if len({r[o] for r in r31}) > 12} if r31 else set()
        if name == "baseline":
            base_busy = busy
        new_ids = [f"0x{i:02x}/{n}" for i, n in ids if (i, n) != (0x31, 78)]
        new_off = sorted(busy - base_busy)
        d4 = {o: sum(1 for r in rows if len(r) > o and r[o] == 0xD4) for o in range(1, 4)}   # mic marker 0xD4 per the community notes
        flag = bool(new_ids or new_off or any(d4.values()))
        print(f"{name:12s} reports={len(rows):4d} new_ids={new_ids} new_busy_offsets={new_off} D4@1-3={list(d4.values())}{'   <-- !!' if flag else ''}")
        results[name] = {"new_ids": new_ids, "new_busy": new_off, "count": len(rows), "d4": d4}
        if flag or name == "cfg0=FF":
            summarize("mic36_" + name.replace("=", "_"), [(0.0, r) for r in rows])
    save("mic36", {"variants": results})
    print("done - you can stop talking")


def capture(fd: int, seconds: float) -> list[tuple[float, bytes]]:
    """Read raw reports for `seconds`; returns (time, report) pairs."""
    out: list[tuple[float, bytes]] = []
    t0 = time.monotonic()
    while (now := time.monotonic()) - t0 < seconds:
        if select.select([fd], [], [], 0.1)[0]:
            out.append((now - t0, os.read(fd, 4096)))
    return out


def summarize(name: str, reports: list[tuple[float, bytes]]) -> dict[str, object]:
    """Per report id: count, sizes, rate; per byte offset of the busiest id: how many distinct values were seen."""
    ids: dict[int, list[int]] = collections.defaultdict(list)
    for _, r in reports:
        ids[r[0]].append(len(r))
    info = {f"0x{i:02x}": {"count": len(s), "sizes": sorted(set(s)), "hz": round(len(s) / CAPTURE_S, 1)} for i, s in ids.items()}
    with open(f"mic_probe_{name}.bin", "wb") as f:
        for _, r in reports:
            f.write(struct.pack("<H", len(r)) + r)
    varying: dict[str, list[int]] = {}
    for i, s in ids.items():
        rows = [r for _, r in reports if r[0] == i]
        width = min(len(r) for r in rows)
        # offsets whose value takes many different values are the interesting ones (sticks sit still, audio does not)
        varying[f"0x{i:02x}"] = [o for o in range(width) if len({r[o] for r in rows}) > 12][:60]
    print(f"[{name}] {len(reports)} reports")
    for k, v in info.items():
        print(f"   id {k}: {v}   busy offsets: {varying.get(k)}")
    return {"reports": info, "busy_offsets": varying}


def save(name: str, data: dict[str, object]) -> None:
    """Merge one result into the JSON file."""
    try:
        old = json.load(open(RESULTS))
    except (OSError, ValueError):
        old = {}
    old[name] = data
    with open(RESULTS, "w") as f:
        json.dump(old, f, indent=1)


def run_variant(fd: int, name: str, variant: dict[int, int] | None) -> dict[str, object]:
    """Send the variant (if any), then capture while the user talks."""
    if variant is not None:
        for i in range(3):
            os.write(fd, output_31(variant, i))
            time.sleep(0.05)
    input(f"[{name}] press Enter, then TALK / tap the mic hole for {CAPTURE_S:.0f} s ...")
    res = summarize(name, capture(fd, CAPTURE_S))
    res["variant"] = None if variant is None else {str(k): v for k, v in variant.items()}
    return res


def main() -> None:
    """Dispatch the subcommand."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "listen"
    fd = os.open(find_hidraw(), os.O_RDWR)
    try:
        if cmd == "listen":
            save("listen", run_variant(fd, "listen", None))
        elif cmd == "enable":
            name = sys.argv[2] if len(sys.argv) > 2 else "internal_mic_path"
            save(name, run_variant(fd, name, VARIANTS[name]))
        elif cmd == "mic36":
            run_mic36(fd)
        elif cmd == "matrix":
            save("listen", run_variant(fd, "listen", None))
            for name, v in VARIANTS.items():
                res = run_variant(fd, name, v)
                res["user_saw_new_data"] = input("   did anything new show up (new id / bigger size)? y/n: ").strip()
                save(name, res)
        else:
            raise SystemExit(__doc__)
    finally:
        os.close(fd)
    print(f"\nSaved {RESULTS} (+ mic_probe_*.bin) - paste the JSON back.")


if __name__ == "__main__":
    main()
