#!/usr/bin/env python3
"""Deterministic $0 JSON EDL validator for trip-cinema-studio.

Enforces:
  - Every `source_path` exists
  - `in_sec >= 0`, `out_sec > in_sec`, and `out_sec <= source_duration_sec`
  - No >2 consecutive still photos
  - Pacing variance (no >4 consecutive identical shot durations)
  - All `locked_by_user` pins from manifest are included in the EDL
  - Zero generative I2V on shots flagged `has_human_closeup: true`
  - Valid 16:9 aspect handling for portrait assets
  - Optional beat-alignment check against `beat_map.json`
"""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
from typing import Any


VALID_PORTRAIT_MODES = {"split_diptych_pair", "blurred_bg_pillarbox"}
GENERATIVE_I2V_MODES = {"i2v", "generative_i2v", "veo", "omni", "gemini_omni_i2v"}


def _nearest_beat_delta(cut_sec: float, beats: list[float]) -> float | None:
    if not beats:
        return None
    idx = bisect.bisect_left(beats, cut_sec)
    candidates: list[float] = []
    if idx < len(beats):
        candidates.append(abs(beats[idx] - cut_sec))
    if idx > 0:
        candidates.append(abs(beats[idx - 1] - cut_sec))
    return min(candidates) if candidates else None


def lint_edl(
    edl: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    beat_map: dict[str, Any] | None = None,
    beat_tolerance_sec: float = 0.18,
    strict_beats: bool = False,
) -> dict[str, Any]:
    """Validate an EDL dict and return structured errors, warnings, and stats."""
    errors: list[str] = []
    warnings: list[str] = []

    shots = edl.get("shots")
    if not isinstance(shots, list) or not shots:
        return {
            "valid": False,
            "errors": ["EDL must contain a non-empty 'shots' array."],
            "warnings": [],
            "stats": {"shot_count": 0},
        }

    manifest_by_path: dict[str, dict[str, Any]] = {}
    locked_paths: set[str] = set()
    if manifest:
        for asset in manifest.get("assets", []):
            sp = str(Path(asset["source_path"]).resolve())
            manifest_by_path[sp] = asset
            if asset.get("locked_by_user"):
                locked_paths.add(sp)

    seen_edl_paths: set[str] = set()
    consec_photos = 0
    prev_dur: float | None = None
    identical_run = 0
    total_timeline_sec = 0.0
    cut_times: list[float] = []

    for idx, shot in enumerate(shots):
        sid = shot.get("shot_id") or f"shot_{idx + 1:03d}"
        mtype = shot.get("media_type", "photo")
        src = shot.get("source_path")

        if mtype != "graphic":
            if not src or not Path(src).expanduser().exists():
                errors.append(f"[{sid}] Missing source_path on disk: {src}")
            else:
                seen_edl_paths.add(str(Path(src).expanduser().resolve()))

        in_sec = float(shot.get("in_sec", 0.0))
        out_sec = float(shot.get("out_sec", 0.0))
        dur = out_sec - in_sec
        if in_sec < 0:
            errors.append(f"[{sid}] in_sec ({in_sec}) cannot be negative.")
        if dur <= 0:
            errors.append(f"[{sid}] out_sec ({out_sec}) must be greater than in_sec ({in_sec}).")

        src_dur = shot.get("source_duration_sec")
        if src_dur is None and src:
            m_asset = manifest_by_path.get(str(Path(src).expanduser().resolve()))
            if m_asset:
                src_dur = m_asset.get("duration_sec")
        if mtype == "video" and src_dur is not None and out_sec > float(src_dur) + 0.05:
            errors.append(f"[{sid}] out_sec ({out_sec:.2f}s) exceeds source duration ({float(src_dur):.2f}s).")

        # Consecutive still photo guard (>2)
        if mtype == "photo":
            consec_photos += 1
            if consec_photos > 2:
                errors.append(f"[{sid}] More than 2 consecutive still photos ({consec_photos} in a row).")
        else:
            consec_photos = 0

        # Pacing variance guard (>4 identical durations)
        rounded_dur = round(dur, 2)
        if prev_dur is not None and abs(rounded_dur - prev_dur) <= 0.05:
            identical_run += 1
            if identical_run > 4:
                errors.append(f"[{sid}] Pacing monotony: >4 consecutive shots with ~{rounded_dur}s duration.")
        else:
            identical_run = 1
        prev_dur = rounded_dur

        # Human closeup safety: NEVER allow generative I2V on human faces
        is_human = bool(shot.get("has_human_closeup", False))
        if not is_human and src:
            m_asset = manifest_by_path.get(str(Path(src).expanduser().resolve()))
            if m_asset:
                is_human = bool(m_asset.get("has_human_closeup", False))
        motion = str(shot.get("motion_type", "ken_burns")).lower()
        use_i2v = bool(shot.get("use_i2v", False)) or (motion in GENERATIVE_I2V_MODES)
        if is_human and use_i2v:
            errors.append(f"[{sid}] Generative I2V is strictly forbidden on shots with has_human_closeup=true.")

        # 16:9 aspect handling for portrait media
        orient = shot.get("orientation")
        if not orient and src:
            m_asset = manifest_by_path.get(str(Path(src).expanduser().resolve()))
            if m_asset:
                orient = m_asset.get("orientation")
        if orient == "portrait":
            handling = shot.get("aspect_handling")
            if handling not in VALID_PORTRAIT_MODES:
                errors.append(
                    f"[{sid}] Portrait asset requires aspect_handling in {sorted(VALID_PORTRAIT_MODES)}, got {handling!r}."
                )
            elif handling == "split_diptych_pair":
                pair_src = shot.get("pair_source_path")
                if not pair_src or not Path(pair_src).expanduser().exists():
                    errors.append(f"[{sid}] split_diptych_pair requires an existing pair_source_path.")
                else:
                    seen_edl_paths.add(str(Path(pair_src).expanduser().resolve()))

        total_timeline_sec += max(0.0, dur)
        cut_times.append(round(total_timeline_sec, 3))

    # Locked-by-user pin enforcement
    missing_pins = sorted(locked_paths - seen_edl_paths)
    for mp in missing_pins:
        errors.append(f"Missing locked_by_user pinned asset from EDL: {mp}")

    # Beat alignment check
    beat_stats: dict[str, Any] = {}
    if beat_map and beat_map.get("beats_sec"):
        beats = sorted(float(b) for b in beat_map["beats_sec"])
        aligned = 0
        for ct in cut_times[:-1]:
            delta = _nearest_beat_delta(ct, beats)
            if delta is not None and delta <= beat_tolerance_sec:
                aligned += 1
        total_cuts = max(1, len(cut_times) - 1)
        ratio = round(aligned / total_cuts, 3)
        beat_stats = {
            "aligned_cuts": aligned,
            "total_internal_cuts": len(cut_times) - 1,
            "alignment_ratio": ratio,
            "tolerance_sec": beat_tolerance_sec,
        }
        if ratio < 0.5:
            msg = f"Low beat alignment ({aligned}/{total_cuts} cuts within {beat_tolerance_sec}s of a beat)."
            if strict_beats:
                errors.append(msg)
            else:
                warnings.append(msg)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "shot_count": len(shots),
            "estimated_duration_sec": round(total_timeline_sec, 2),
            "locked_pins_checked": len(locked_paths),
            "beat_alignment": beat_stats,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic $0 JSON EDL validator for trip-cinema-studio.")
    parser.add_argument("--edl", required=True, help="Path to EDL JSON file.")
    parser.add_argument("--manifest", help="Optional path to ./work/manifest.json for locked_by_user pin checks.")
    parser.add_argument("--beat-map", help="Optional path to ./work/beat_map.json for beat alignment checks.")
    parser.add_argument("--beat-tolerance", type=float, default=0.18, help="Beat alignment tolerance in seconds (default: 0.18).")
    parser.add_argument("--strict-beats", action="store_true", help="Treat low beat alignment as a validation error.")
    args = parser.parse_args()

    edl = json.loads(Path(args.edl).expanduser().read_text(encoding="utf-8"))
    manifest = json.loads(Path(args.manifest).expanduser().read_text(encoding="utf-8")) if args.manifest else None
    beat_map = json.loads(Path(args.beat_map).expanduser().read_text(encoding="utf-8")) if args.beat_map else None

    report = lint_edl(
        edl=edl,
        manifest=manifest,
        beat_map=beat_map,
        beat_tolerance_sec=args.beat_tolerance,
        strict_beats=args.strict_beats,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
