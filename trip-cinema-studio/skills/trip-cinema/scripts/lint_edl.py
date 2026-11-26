#!/usr/bin/env python3
"""Deterministic $0 JSON EDL v2.0 validator for trip-cinema-studio.

Enforces:
  - Style Pack loading from `styles/<style_id>.json` with per-style rules
  - Every photo/video/tile `source_path` and graphic `backdrop_path` exists
  - `in_sec >= 0`, positive duration, and source span within `source_duration_sec`
  - Per-style `max_consecutive_photos` limit (replaces fixed >2 limit)
  - Pacing variance (per-style `max_identical_duration_run`, default 4)
  - Closed-form 5-phase cubic speed-ramp source span S_req and >=50fps guardrails
  - Transition overlap handle headroom (>=0.4s) on outgoing & incoming video shots
  - J-cut and L-cut audio source handle headroom
  - Multi-video / hybrid `tiles[]` layout integrity
  - `graphic` shot validation (`chapter_title`, `route_map`, `trip_wrapped`,
    `cast_intro`, `text_behind_subject`, `blooper_title`)
  - All `locked_by_user` pins from manifest are included in the EDL
  - Zero generative I2V on shots/tiles flagged `has_human_closeup: true`
  - Valid 16:9 portrait layout handling when `aspect_profile != "9:16"`
  - Optional beat-alignment check against `beat_map.json` using overlap-adjusted cut times
"""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
from typing import Any


VALID_STYLE_IDS = {
    "cinema_doc",
    "analog_scrapbook",
    "wes_postcard",
    "kinetic_montage",
    "editorial_bento",
    "super8_nostalgia",
    "sitcom_cast",
}
VALID_MEDIA_TYPES = {"photo", "video", "graphic"}
VALID_GRAPHIC_TYPES = {
    "chapter_title",
    "route_map",
    "trip_wrapped",
    "cast_intro",
    "text_behind_subject",
    "blooper_title",
}
VALID_LAYOUTS = {
    "single",
    "split_diptych_pair",
    "blurred_bg_pillarbox",
    "split_2up_video",
    "triptych_3up_video",
    "bento_living_grid",
    "polaroid_stack",
    "super8_frame",
}
VALID_PORTRAIT_MODES = {
    "split_diptych_pair",
    "blurred_bg_pillarbox",
    "split_2up_video",
    "triptych_3up_video",
    "bento_living_grid",
    "polaroid_stack",
    "super8_frame",
}
MULTI_VIDEO_REQUIRED_TILES = {
    "split_2up_video": (2, 2),
    "triptych_3up_video": (3, 3),
    "bento_living_grid": (2, 4),
}
VALID_TRANSITIONS_OUT = {
    "cut",
    "dissolve",
    "whip_pan_left",
    "whip_pan_right",
    "whip_pan_up",
    "whip_pan_down",
    "light_leak",
    "film_burn",
    "flash_cut",
    "zoom_punch",
    "paper_wipe",
}
GENERATIVE_I2V_MODES = {
    "i2v",
    "generative_i2v",
    "veo",
    "omni",
    "gemini_omni_i2v",
    "omni_i2v_scenic",
}
MIN_TRANSITION_HANDLE_SEC = 0.4


def _resolve_path_str(raw_path: str) -> str:
    return str(Path(raw_path).expanduser().resolve())


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


def _default_styles_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "styles"


def load_style_pack(
    style_id: str | None,
    styles_dir: Path | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Load styles/<style_id>.json with graceful fallback for v1.0 EDLs."""
    errors: list[str] = []
    warnings: list[str] = []
    resolved_dir = styles_dir or _default_styles_dir()

    effective_id = style_id or "cinema_doc"
    if style_id is not None and style_id not in VALID_STYLE_IDS:
        errors.append(
            f"Unknown style_id {style_id!r}; expected one of {sorted(VALID_STYLE_IDS)}."
        )
        effective_id = "cinema_doc"

    style_path = resolved_dir / f"{effective_id}.json"
    if style_path.exists():
        try:
            style_data = json.loads(style_path.read_text(encoding="utf-8"))
            return style_data, errors, warnings
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Failed to parse style pack {style_path}: {exc}")
    elif style_id is not None:
        errors.append(f"Style pack JSON not found on disk: {style_path}")
    else:
        warnings.append(f"Default style pack {style_path} not found; using built-in cinema_doc defaults.")

    fallback: dict[str, Any] = {
        "style_id": effective_id,
        "transitions": {
            "allowed": sorted(VALID_TRANSITIONS_OUT),
            "default_duration_sec": 0.45,
        },
        "pacing_rules": {
            "max_consecutive_photos": 2,
            "max_identical_duration_run": 4,
            "allow_speed_ramps": True,
            "max_speed_ramp_ratio": 0.20,
        },
    }
    return fallback, errors, warnings


def compute_cubic_ramp_source_span(speed_profile: dict[str, Any]) -> tuple[float, float]:
    """Return (S_req, output_duration_sec) for a 5-phase piecewise cubic speed ramp.

    Closed-form integral of cubic smoothstep v(u) = v0 + (v1 - v0)*(3u^2 - 2u^3):
        S_req = v_hi*d1 + ((v_hi + v_lo)/2)*d2 + v_lo*d3 + ((v_lo + v_hi)/2)*d4 + v_hi*d5
    """
    v_hi = float(speed_profile.get("v_hi", 3.5))
    v_lo = float(speed_profile.get("v_lo", 0.5))
    d1 = float(speed_profile.get("d1", 0.30))
    d2 = float(speed_profile.get("d2", 0.35))
    d3 = float(speed_profile.get("d3", 1.60))
    d4 = float(speed_profile.get("d4", 0.35))
    d5 = float(speed_profile.get("d5", 0.30))
    out_dur = d1 + d2 + d3 + d4 + d5
    mid_v = 0.5 * (v_hi + v_lo)
    s_req = (v_hi * d1) + (mid_v * d2) + (v_lo * d3) + (mid_v * d4) + (v_hi * d5)
    return s_req, out_dur


def _validate_focal_anchor(anchor: Any, label: str, errors: list[str]) -> None:
    if anchor is None:
        return
    fx: float | None = None
    fy: float | None = None
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        try:
            fx = float(anchor[0])
            fy = float(anchor[1])
        except (TypeError, ValueError):
            errors.append(f"{label} focal_anchor array values must be numeric: {anchor!r}")
            return
    elif isinstance(anchor, dict) and "x" in anchor and "y" in anchor:
        try:
            fx = float(anchor["x"])
            fy = float(anchor["y"])
        except (TypeError, ValueError):
            errors.append(f"{label} focal_anchor object x/y must be numeric: {anchor!r}")
            return
    else:
        errors.append(f"{label} focal_anchor must be [x, y] or {{'x': x, 'y': y}}, got {anchor!r}")
        return

    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        errors.append(f"{label} focal_anchor ({fx}, {fy}) must be normalized within [0.0, 1.0].")


def _resolve_output_duration(shot: dict[str, Any]) -> float:
    if shot.get("duration_sec") is not None:
        return float(shot["duration_sec"])
    sp = shot.get("speed_profile")
    if isinstance(sp, dict) and sp.get("mode") == "cubic_ramp":
        _, ramp_out = compute_cubic_ramp_source_span(sp)
        return ramp_out
    in_sec = float(shot.get("in_sec", 0.0))
    out_sec = float(shot.get("out_sec", 0.0))
    raw_span = out_sec - in_sec
    if isinstance(sp, dict) and sp.get("mode") == "constant":
        rate = float(sp.get("rate", 1.0))
        if rate > 0:
            return raw_span / rate
    return raw_span


def _resolve_transition_out(shot: dict[str, Any]) -> str:
    t_out = shot.get("transition_out")
    if t_out:
        return str(t_out)
    legacy = str(shot.get("transition", "cut"))
    if legacy == "xfade_dissolve":
        return "dissolve"
    return "cut"


def _lookup_manifest_asset(
    raw_path: str | None,
    manifest_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not raw_path:
        return None
    return manifest_by_path.get(_resolve_path_str(raw_path))


def lint_edl(
    edl: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    beat_map: dict[str, Any] | None = None,
    beat_tolerance_sec: float = 0.18,
    strict_beats: bool = False,
    styles_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate an EDL v1.0 or v2.0 dict and return structured errors, warnings, and stats."""
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

    # Load Style Pack rules
    style_pack, style_errs, style_warns = load_style_pack(edl.get("style_id"), styles_dir=styles_dir)
    errors.extend(style_errs)
    warnings.extend(style_warns)

    active_style_id = str(style_pack.get("style_id", edl.get("style_id") or "cinema_doc"))
    pacing = style_pack.get("pacing_rules") or {}
    transitions_cfg = style_pack.get("transitions") or {}
    max_consec_photos = int(pacing.get("max_consecutive_photos", 2))
    max_identical_run = int(pacing.get("max_identical_duration_run", 4))
    allow_speed_ramps = bool(pacing.get("allow_speed_ramps", True))
    max_speed_ramp_ratio = float(pacing.get("max_speed_ramp_ratio", 0.20))
    allowed_transitions = set(transitions_cfg.get("allowed") or VALID_TRANSITIONS_OUT)
    default_trans_dur = float(transitions_cfg.get("default_duration_sec", 0.45))

    aspect_profile = str(edl.get("aspect_profile") or "16:9")
    if aspect_profile not in {"16:9", "9:16"}:
        errors.append(f"Invalid aspect_profile {aspect_profile!r}; expected '16:9' or '9:16'.")

    manifest_by_path: dict[str, dict[str, Any]] = {}
    locked_paths: set[str] = set()
    if manifest:
        for asset in manifest.get("assets", []):
            sp_raw = asset.get("source_path")
            if not sp_raw:
                continue
            sp = _resolve_path_str(sp_raw)
            manifest_by_path[sp] = asset
            if asset.get("locked_by_user"):
                locked_paths.add(sp)

    # Precompute shot output durations for neighbor J/L-cut sanity checks
    shot_out_durs = [_resolve_output_duration(s) for s in shots]

    seen_edl_paths: set[str] = set()
    consec_photos = 0
    prev_dur: float | None = None
    identical_run = 0
    timeline_cursor = 0.0
    cut_times: list[float] = []

    video_shots_count = 0
    photo_shots_count = 0
    graphic_shots_count = 0
    multi_tile_shots_count = 0
    speed_ramp_shots_count = 0
    sonic_postcard_count = 0
    transition_overlaps_count = 0

    for idx, shot in enumerate(shots):
        sid = str(shot.get("shot_id") or f"shot_{idx + 1:03d}")
        mtype = str(shot.get("media_type", "photo"))
        if mtype not in VALID_MEDIA_TYPES:
            errors.append(f"[{sid}] Invalid media_type {mtype!r}; expected one of {sorted(VALID_MEDIA_TYPES)}.")

        src = shot.get("source_path")
        tiles = shot.get("tiles")
        has_tiles = isinstance(tiles, list) and len(tiles) > 0
        layout = str(shot.get("layout") or shot.get("aspect_handling") or "single")
        if layout not in VALID_LAYOUTS:
            errors.append(f"[{sid}] Invalid layout {layout!r}; expected one of {sorted(VALID_LAYOUTS)}.")

        in_sec = float(shot.get("in_sec", 0.0))
        out_sec = float(shot.get("out_sec", 0.0))
        out_dur = shot_out_durs[idx]

        if in_sec < 0:
            errors.append(f"[{sid}] in_sec ({in_sec}) cannot be negative.")
        if out_dur <= 0:
            errors.append(
                f"[{sid}] Shot duration must be positive (duration_sec={shot.get('duration_sec')}, in_sec={in_sec}, out_sec={out_sec})."
            )

        _validate_focal_anchor(shot.get("focal_anchor"), f"[{sid}]", errors)

        # Resolve source metadata (duration, fps, human closeup, orientation)
        m_asset = _lookup_manifest_asset(src, manifest_by_path)
        src_dur: float | None = shot.get("source_duration_sec")
        if src_dur is None and m_asset is not None:
            src_dur = m_asset.get("duration_sec")
        if src_dur is not None:
            src_dur = float(src_dur)

        src_fps: float | None = shot.get("source_fps")
        if src_fps is None and m_asset is not None:
            src_fps = m_asset.get("fps")
        if src_fps is not None:
            src_fps = float(src_fps)

        # ---- 1. Graphic shots ----
        if mtype == "graphic":
            graphic_shots_count += 1
            gtype = shot.get("graphic_type")
            if gtype is not None and gtype not in VALID_GRAPHIC_TYPES:
                errors.append(
                    f"[{sid}] Invalid graphic_type {gtype!r}; expected one of {sorted(VALID_GRAPHIC_TYPES)}."
                )
            elif gtype is None and shot.get("overlay") in {None, "none"}:
                warnings.append(f"[{sid}] media_type='graphic' should specify graphic_type.")

            backdrop = shot.get("backdrop_path")
            if backdrop:
                if not Path(backdrop).expanduser().exists():
                    errors.append(f"[{sid}] Missing backdrop_path on disk: {backdrop}")
                else:
                    seen_edl_paths.add(_resolve_path_str(backdrop))
            elif gtype == "chapter_title" and active_style_id == "cinema_doc":
                warnings.append(
                    f"[{sid}] cinema_doc chapter_title should provide backdrop_path (blurred hero photo, never flat black)."
                )

            if src:
                if not Path(src).expanduser().exists():
                    errors.append(f"[{sid}] Missing graphic source_path on disk: {src}")
                else:
                    seen_edl_paths.add(_resolve_path_str(src))
            elif gtype in {"text_behind_subject", "cast_intro"} and not backdrop:
                errors.append(f"[{sid}] graphic_type={gtype!r} requires source_path or backdrop_path.")

            mask_path = shot.get("mask_path")
            if mask_path and not Path(mask_path).expanduser().exists():
                warnings.append(
                    f"[{sid}] mask_path not found on disk ({mask_path}); renderer will use non-masked fallback."
                )

            if gtype == "trip_wrapped":
                stats = shot.get("stats")
                if not isinstance(stats, list) or not stats:
                    warnings.append(f"[{sid}] trip_wrapped shot has no stats[] entries.")
                else:
                    for s_idx, stat in enumerate(stats):
                        cov = stat.get("coverage_pct")
                        if cov is not None and float(cov) < 30.0:
                            warnings.append(
                                f"[{sid}] trip_wrapped stat[{s_idx}] ({stat.get('label')!r}) has coverage_pct={cov}% < 30%; omit low-coverage metrics."
                            )

            if gtype == "route_map":
                waypoints = shot.get("waypoints")
                if waypoints is not None:
                    if not isinstance(waypoints, list) or len(waypoints) < 2:
                        warnings.append(f"[{sid}] route_map waypoints should contain at least 2 stops.")
                    else:
                        for w_idx, wp in enumerate(waypoints):
                            if "lat" not in wp or "lon" not in wp:
                                errors.append(f"[{sid}] route_map waypoints[{w_idx}] missing lat/lon.")

            consec_photos = 0

        # ---- 2. Multi-tile layouts (`tiles[]`) ----
        elif has_tiles:
            multi_tile_shots_count += 1
            if layout in MULTI_VIDEO_REQUIRED_TILES:
                min_t, max_t = MULTI_VIDEO_REQUIRED_TILES[layout]
                if not (min_t <= len(tiles) <= max_t):
                    errors.append(
                        f"[{sid}] layout={layout!r} requires {min_t}..{max_t} tiles, got {len(tiles)}."
                    )
            elif len(tiles) < 2 or len(tiles) > 4:
                errors.append(f"[{sid}] tiles[] must contain 2..4 tiles, got {len(tiles)}.")

            lead_tile = shot.get("audio_lead_tile")
            if lead_tile is not None:
                if not isinstance(lead_tile, int) or lead_tile < -1 or lead_tile >= len(tiles):
                    errors.append(
                        f"[{sid}] audio_lead_tile ({lead_tile}) out of range [-1, {len(tiles) - 1}]."
                    )

            has_any_video_tile = False
            for t_idx, tile in enumerate(tiles):
                t_label = f"[{sid}.tiles[{t_idx}]]"
                t_src = tile.get("source_path")
                t_mtype = str(tile.get("media_type", "video"))
                if t_mtype not in {"photo", "video"}:
                    errors.append(f"{t_label} invalid media_type {t_mtype!r}; expected 'photo' or 'video'.")
                if not t_src or not Path(t_src).expanduser().exists():
                    errors.append(f"{t_label} missing source_path on disk: {t_src}")
                else:
                    seen_edl_paths.add(_resolve_path_str(t_src))

                _validate_focal_anchor(tile.get("focal_anchor"), t_label, errors)

                t_asset = _lookup_manifest_asset(t_src, manifest_by_path)
                t_human = bool(tile.get("has_human_closeup", False)) or bool(
                    t_asset.get("has_human_closeup", False) if t_asset else False
                )
                motion_lower = str(shot.get("motion_type", "")).lower()
                if t_human and (bool(shot.get("use_i2v", False)) or motion_lower in GENERATIVE_I2V_MODES):
                    errors.append(
                        f"{t_label} generative I2V is strictly forbidden when tile has_human_closeup=true."
                    )

                if t_mtype == "video":
                    has_any_video_tile = True
                    t_in = float(tile.get("in_sec", 0.0))
                    t_out = tile.get("out_sec")
                    t_src_dur = tile.get("source_duration_sec")
                    if t_src_dur is None and t_asset is not None:
                        t_src_dur = t_asset.get("duration_sec")
                    if t_in < 0:
                        errors.append(f"{t_label} in_sec ({t_in}) cannot be negative.")
                    if t_out is not None:
                        t_out_f = float(t_out)
                        if t_out_f <= t_in:
                            errors.append(f"{t_label} out_sec ({t_out_f}) must be greater than in_sec ({t_in}).")
                        elif t_out_f - t_in < out_dur - 0.05:
                            errors.append(
                                f"{t_label} video span ({t_out_f - t_in:.2f}s) is shorter than shot duration ({out_dur:.2f}s)."
                            )
                        if t_src_dur is not None and t_out_f > float(t_src_dur) + 0.05:
                            errors.append(
                                f"{t_label} out_sec ({t_out_f:.2f}s) exceeds tile source_duration_sec ({float(t_src_dur):.2f}s)."
                            )
                    elif t_src_dur is not None and t_in + out_dur > float(t_src_dur) + 0.05:
                        errors.append(
                            f"{t_label} in_sec ({t_in:.2f}s) + shot duration ({out_dur:.2f}s) exceeds tile source_duration_sec ({float(t_src_dur):.2f}s)."
                        )

            if has_any_video_tile:
                video_shots_count += 1
                consec_photos = 0
            else:
                photo_shots_count += 1
                consec_photos += 1
                if consec_photos > max_consec_photos:
                    errors.append(
                        f"[{sid}] More than {max_consec_photos} consecutive still photo shots ({consec_photos} in a row; style '{active_style_id}' limit)."
                    )

        # ---- 3. Single photo/video shots ----
        else:
            if layout in MULTI_VIDEO_REQUIRED_TILES:
                errors.append(f"[{sid}] layout={layout!r} requires a non-empty tiles[] array.")
            if not src or not Path(src).expanduser().exists():
                errors.append(f"[{sid}] Missing source_path on disk: {src}")
            else:
                seen_edl_paths.add(_resolve_path_str(src))

            if mtype == "photo":
                photo_shots_count += 1
                consec_photos += 1
                if consec_photos > max_consec_photos:
                    errors.append(
                        f"[{sid}] More than {max_consec_photos} consecutive still photos ({consec_photos} in a row; style '{active_style_id}' limit)."
                    )
            else:
                video_shots_count += 1
                consec_photos = 0

        # ---- 4. Human closeup & Speed profile (closed-form cubic ramp span S_req) ----
        is_human = bool(shot.get("has_human_closeup", False))
        if not is_human and m_asset is not None:
            is_human = bool(m_asset.get("has_human_closeup", False))

        audio_cfg = shot.get("audio") or {}
        if not isinstance(audio_cfg, dict):
            errors.append(f"[{sid}] shot.audio must be an object.")
            audio_cfg = {}

        if audio_cfg.get("sonic_postcard"):
            sonic_postcard_count += 1
        if audio_cfg.get("duck_db") is not None and float(audio_cfg["duck_db"]) > 0:
            errors.append(f"[{sid}] audio.duck_db ({audio_cfg['duck_db']}) must be <= 0 dB.")

        sp = shot.get("speed_profile")
        eff_src_end = out_sec if out_sec > in_sec else (in_sec + max(0.0, out_dur))

        if sp is not None:
            if not isinstance(sp, dict):
                errors.append(f"[{sid}] speed_profile must be an object.")
            elif mtype != "video":
                errors.append(f"[{sid}] speed_profile is only valid on media_type='video' shots.")
            else:
                sp_mode = str(sp.get("mode", "constant"))
                if sp_mode == "cubic_ramp":
                    speed_ramp_shots_count += 1
                    v_hi = float(sp.get("v_hi", 3.5))
                    v_lo = float(sp.get("v_lo", 0.5))
                    phase_durs = [
                        float(sp.get(k, d_def))
                        for k, d_def in (("d1", 0.30), ("d2", 0.35), ("d3", 1.60), ("d4", 0.35), ("d5", 0.30))
                    ]
                    if v_hi <= 0 or v_lo <= 0:
                        errors.append(f"[{sid}] cubic_ramp v_hi ({v_hi}) and v_lo ({v_lo}) must be > 0.")
                    if any(d < 0 for d in phase_durs) or sum(phase_durs) <= 0:
                        errors.append(f"[{sid}] cubic_ramp phase durations d1..d5 must be >= 0 with positive sum.")

                    s_req, ramp_out_dur = compute_cubic_ramp_source_span(sp)
                    if shot.get("duration_sec") is not None and abs(float(shot["duration_sec"]) - ramp_out_dur) > 0.05:
                        errors.append(
                            f"[{sid}] cubic_ramp phase sum d1..d5 ({ramp_out_dur:.3f}s) does not match duration_sec ({float(shot['duration_sec']):.3f}s)."
                        )
                    if out_sec > in_sec and (out_sec - in_sec) < s_req - 0.05:
                        errors.append(
                            f"[{sid}] cubic_ramp requires S_req={s_req:.2f}s of source, but (out_sec - in_sec) is only {out_sec - in_sec:.2f}s."
                        )
                    if src_dur is not None and (in_sec + s_req) > src_dur + 0.05:
                        errors.append(
                            f"[{sid}] cubic_ramp consumes S_req={s_req:.2f}s from in_sec={in_sec:.2f}s ({in_sec + s_req:.2f}s), exceeding source_duration_sec ({src_dur:.2f}s)."
                        )
                    eff_src_end = in_sec + s_req

                    if not allow_speed_ramps:
                        warnings.append(
                            f"[{sid}] Style '{active_style_id}' discourages speed ramps (allow_speed_ramps=false)."
                        )
                    if is_human:
                        warnings.append(
                            f"[{sid}] Avoid applying cubic_ramp to emotional/human close-ups (has_human_closeup=true)."
                        )
                    if src_fps is not None and src_fps < 50.0:
                        errors.append(
                            f"[{sid}] cubic_ramp requires >=50fps source footage to avoid stutter; got {src_fps:.1f}fps."
                        )
                    if audio_cfg.get("sonic_postcard"):
                        errors.append(
                            f"[{sid}] Never apply cubic_ramp to a sonic_postcard shot with featured diegetic audio."
                        )
                    if not audio_cfg.get("mute", False):
                        warnings.append(
                            f"[{sid}] cubic_ramp should set audio.mute=true to avoid chipmunk pitch warp above 1.3x."
                        )

                elif sp_mode == "constant":
                    rate = float(sp.get("rate", 1.0))
                    if rate <= 0:
                        errors.append(f"[{sid}] speed_profile.rate ({rate}) must be > 0.")
                    else:
                        s_req = out_dur * rate
                        if src_dur is not None and (in_sec + s_req) > src_dur + 0.05:
                            errors.append(
                                f"[{sid}] constant speed ({rate:.2f}x) consumes {s_req:.2f}s from in_sec={in_sec:.2f}s, exceeding source_duration_sec ({src_dur:.2f}s)."
                            )
                        eff_src_end = in_sec + s_req

                        if rate < 0.85 and src_fps is not None and src_fps < 50.0:
                            errors.append(
                                f"[{sid}] Slow-motion rate ({rate:.2f}x) requires >=50fps source footage; got {src_fps:.1f}fps."
                            )
                        if (rate > 1.3 or rate < 0.85) and audio_cfg.get("sonic_postcard"):
                            errors.append(
                                f"[{sid}] Never speed-scale ({rate:.2f}x) a sonic_postcard shot with featured diegetic audio."
                            )
                        if rate > 1.3 and not audio_cfg.get("mute", False):
                            warnings.append(
                                f"[{sid}] Speed rate {rate:.2f}x (>1.3x) should set audio.mute=true to avoid pitch warp."
                            )
                else:
                    errors.append(f"[{sid}] Unknown speed_profile.mode {sp_mode!r}; expected 'constant' or 'cubic_ramp'.")
        elif mtype == "video" and not has_tiles:
            if out_sec <= in_sec and shot.get("duration_sec") is None:
                errors.append(f"[{sid}] out_sec ({out_sec}) must be greater than in_sec ({in_sec}).")
            if src_dur is not None and eff_src_end > src_dur + 0.05:
                errors.append(
                    f"[{sid}] out_sec ({eff_src_end:.2f}s) exceeds source duration ({src_dur:.2f}s)."
                )

        # ---- 5. Pacing variance guard ----
        rounded_dur = round(out_dur, 2)
        if prev_dur is not None and abs(rounded_dur - prev_dur) <= 0.05:
            identical_run += 1
            if identical_run > max_identical_run:
                errors.append(
                    f"[{sid}] Pacing monotony: >{max_identical_run} consecutive shots with ~{rounded_dur}s duration."
                )
        else:
            identical_run = 1
        prev_dur = rounded_dur

        # ---- 6. Human closeup safety: NEVER allow generative I2V on human faces ----
        motion = str(shot.get("motion_type", "ken_burns_in")).lower()
        use_i2v = bool(shot.get("use_i2v", False)) or (motion in GENERATIVE_I2V_MODES)
        if is_human and use_i2v:
            errors.append(f"[{sid}] Generative I2V is strictly forbidden on shots with has_human_closeup=true.")

        # ---- 7. 16:9 aspect handling for single portrait media ----
        orient = shot.get("orientation")
        if not orient and m_asset is not None:
            orient = m_asset.get("orientation")
        if aspect_profile == "16:9" and orient == "portrait" and mtype in {"photo", "video"} and not has_tiles:
            handling = shot.get("layout") or shot.get("aspect_handling") or edl.get("vertical_layout")
            if handling not in VALID_PORTRAIT_MODES:
                errors.append(
                    f"[{sid}] Portrait asset in 16:9 requires layout/aspect_handling in {sorted(VALID_PORTRAIT_MODES)}, got {handling!r}."
                )
            elif handling == "split_diptych_pair":
                pair_src = shot.get("pair_source_path")
                if not pair_src or not Path(pair_src).expanduser().exists():
                    errors.append(f"[{sid}] split_diptych_pair requires an existing pair_source_path (or tiles[]).")
                else:
                    seen_edl_paths.add(_resolve_path_str(pair_src))

        # ---- 8. Transition overlap handle headroom (>= 0.4s) ----
        trans_out = _resolve_transition_out(shot)
        if trans_out not in VALID_TRANSITIONS_OUT:
            errors.append(
                f"[{sid}] Invalid transition_out {trans_out!r}; expected one of {sorted(VALID_TRANSITIONS_OUT)}."
            )
        elif trans_out != "cut" and trans_out not in allowed_transitions:
            warnings.append(
                f"[{sid}] transition_out={trans_out!r} is outside style '{active_style_id}' recommended transitions {sorted(allowed_transitions)}."
            )

        overlap_sec = 0.0
        if trans_out != "cut" and idx < len(shots) - 1:
            trans_dur = float(shot.get("transition_duration_sec", default_trans_dur))
            if trans_dur <= 0:
                errors.append(f"[{sid}] transition_duration_sec ({trans_dur}) must be > 0 for {trans_out!r}.")
            else:
                transition_overlaps_count += 1
                overlap_sec = trans_dur
                req_handle = max(MIN_TRANSITION_HANDLE_SEC, trans_dur)
                if out_dur < trans_dur:
                    errors.append(
                        f"[{sid}] Shot duration ({out_dur:.2f}s) is shorter than transition_duration_sec ({trans_dur:.2f}s)."
                    )
                if mtype == "video" and not has_tiles and src_dur is not None:
                    tail_headroom = src_dur - eff_src_end
                    if tail_headroom < req_handle - 0.02:
                        errors.append(
                            f"[{sid}] transition_out={trans_out!r} requires >= {req_handle:.2f}s outgoing tail handle "
                            f"(after {eff_src_end:.2f}s), but source_duration_sec={src_dur:.2f}s leaves only {tail_headroom:.2f}s."
                        )
                elif has_tiles:
                    for t_idx, tile in enumerate(tiles):
                        if str(tile.get("media_type", "video")) != "video":
                            continue
                        t_in = float(tile.get("in_sec", 0.0))
                        t_out = tile.get("out_sec")
                        t_end = float(t_out) if t_out is not None else (t_in + out_dur)
                        t_asset = _lookup_manifest_asset(tile.get("source_path"), manifest_by_path)
                        t_src_dur = tile.get("source_duration_sec")
                        if t_src_dur is None and t_asset is not None:
                            t_src_dur = t_asset.get("duration_sec")
                        if t_src_dur is not None:
                            t_tail = float(t_src_dur) - t_end
                            if t_tail < req_handle - 0.02:
                                errors.append(
                                    f"[{sid}.tiles[{t_idx}]] transition_out={trans_out!r} requires >= {req_handle:.2f}s outgoing tail handle, "
                                    f"but tile source leaves only {t_tail:.2f}s."
                                )

                next_shot = shots[idx + 1]
                next_sid = str(next_shot.get("shot_id") or f"shot_{idx + 2:03d}")
                next_mtype = str(next_shot.get("media_type", "photo"))
                next_tiles = next_shot.get("tiles")
                next_has_tiles = isinstance(next_tiles, list) and len(next_tiles) > 0
                if next_mtype == "video" and not next_has_tiles:
                    next_in = float(next_shot.get("in_sec", 0.0))
                    if next_in < req_handle - 0.02:
                        errors.append(
                            f"[{next_sid}] Incoming video after [{sid}] transition_out={trans_out!r} requires >= {req_handle:.2f}s "
                            f"head handle (in_sec >= {req_handle:.2f}s), got in_sec={next_in:.2f}s."
                        )
                elif next_has_tiles:
                    for nt_idx, ntile in enumerate(next_tiles):
                        if str(ntile.get("media_type", "video")) != "video":
                            continue
                        nt_in = float(ntile.get("in_sec", 0.0))
                        if nt_in < req_handle - 0.02:
                            errors.append(
                                f"[{next_sid}.tiles[{nt_idx}]] Incoming video tile after [{sid}] transition_out={trans_out!r} "
                                f"requires >= {req_handle:.2f}s head handle (in_sec >= {req_handle:.2f}s), got in_sec={nt_in:.2f}s."
                            )

        # ---- 9. J-cut & L-cut audio source headroom ----
        j_cut = float(audio_cfg.get("j_cut_sec", 0.0))
        l_cut = float(audio_cfg.get("l_cut_sec", 0.0))
        if j_cut < 0:
            errors.append(f"[{sid}] audio.j_cut_sec ({j_cut}) cannot be negative.")
        if l_cut < 0:
            errors.append(f"[{sid}] audio.l_cut_sec ({l_cut}) cannot be negative.")

        if j_cut > 0:
            if idx == 0:
                warnings.append(f"[{sid}] audio.j_cut_sec={j_cut:.2f}s on the first shot has no preceding clip to overlap.")
            else:
                if j_cut > shot_out_durs[idx - 1]:
                    warnings.append(
                        f"[{sid}] audio.j_cut_sec ({j_cut:.2f}s) exceeds previous shot duration ({shot_out_durs[idx - 1]:.2f}s)."
                    )
            if mtype == "video" and not has_tiles and in_sec < j_cut - 0.02:
                errors.append(
                    f"[{sid}] audio.j_cut_sec ({j_cut:.2f}s) exceeds available pre-roll head handle (in_sec={in_sec:.2f}s)."
                )

        if l_cut > 0:
            if idx == len(shots) - 1:
                warnings.append(f"[{sid}] audio.l_cut_sec={l_cut:.2f}s on the final shot extends past timeline end.")
            else:
                if l_cut > shot_out_durs[idx + 1]:
                    warnings.append(
                        f"[{sid}] audio.l_cut_sec ({l_cut:.2f}s) exceeds next shot duration ({shot_out_durs[idx + 1]:.2f}s)."
                    )
            if mtype == "video" and not has_tiles and src_dur is not None:
                tail_avail = src_dur - eff_src_end
                if tail_avail < l_cut - 0.02:
                    errors.append(
                        f"[{sid}] audio.l_cut_sec ({l_cut:.2f}s) exceeds available tail handle "
                        f"({tail_avail:.2f}s; source ends at {src_dur:.2f}s, shot uses through {eff_src_end:.2f}s)."
                    )

        timeline_cursor += max(0.0, out_dur - overlap_sec)
        cut_times.append(round(timeline_cursor, 3))

    # Speed ramp ratio check across video shots
    if video_shots_count > 0 and speed_ramp_shots_count > 0:
        ramp_ratio = speed_ramp_shots_count / video_shots_count
        if ramp_ratio > max_speed_ramp_ratio + 1e-6:
            warnings.append(
                f"Speed-ramped shots ({speed_ramp_shots_count}/{video_shots_count} = {ramp_ratio:.0%}) "
                f"exceed style '{active_style_id}' recommended cap ({max_speed_ramp_ratio:.0%})."
            )

    # Locked-by-user pin enforcement
    missing_pins = sorted(locked_paths - seen_edl_paths)
    for mp in missing_pins:
        errors.append(f"Missing locked_by_user pinned asset from EDL: {mp}")

    # Beat alignment check (using overlap-adjusted timeline_cursor)
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
            "style_id": active_style_id,
            "aspect_profile": aspect_profile,
            "max_consecutive_photos": max_consec_photos,
            "shot_count": len(shots),
            "video_shots": video_shots_count,
            "photo_shots": photo_shots_count,
            "graphic_shots": graphic_shots_count,
            "multi_tile_shots": multi_tile_shots_count,
            "speed_ramp_shots": speed_ramp_shots_count,
            "sonic_postcard_shots": sonic_postcard_count,
            "transition_overlaps": transition_overlaps_count,
            "estimated_duration_sec": round(timeline_cursor, 2),
            "locked_pins_checked": len(locked_paths),
            "beat_alignment": beat_stats,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic $0 JSON EDL v2.0 validator for trip-cinema-studio.")
    parser.add_argument("--edl", required=True, help="Path to EDL JSON file.")
    parser.add_argument("--manifest", help="Optional path to ./work/manifest.json for locked_by_user pin checks.")
    parser.add_argument("--beat-map", help="Optional path to ./work/beat_map.json for beat alignment checks.")
    parser.add_argument("--styles-dir", help="Optional override directory for styles/<style_id>.json presets.")
    parser.add_argument("--beat-tolerance", type=float, default=0.18, help="Beat alignment tolerance in seconds (default: 0.18).")
    parser.add_argument("--strict-beats", action="store_true", help="Treat low beat alignment as a validation error.")
    args = parser.parse_args()

    edl = json.loads(Path(args.edl).expanduser().read_text(encoding="utf-8"))
    manifest = json.loads(Path(args.manifest).expanduser().read_text(encoding="utf-8")) if args.manifest else None
    beat_map = json.loads(Path(args.beat_map).expanduser().read_text(encoding="utf-8")) if args.beat_map else None
    styles_dir = Path(args.styles_dir).expanduser() if args.styles_dir else None

    report = lint_edl(
        edl=edl,
        manifest=manifest,
        beat_map=beat_map,
        beat_tolerance_sec=args.beat_tolerance,
        strict_beats=args.strict_beats,
        styles_dir=styles_dir,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
