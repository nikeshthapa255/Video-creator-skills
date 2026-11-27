#!/usr/bin/env python3
"""Deterministic FFmpeg master renderer and QA extractor for trip-cinema-studio (v0.2.1).

Implements cross-platform Linux / FFmpeg production parity:
  - Declarative Style Pack loading from `styles/<style_id>.json`
    (`color_grade.ffmpeg_vf`, `textures.grain_amount`, `audio.duck_bgm_db`, palette/typography)
  - Separate HLG (`arib-std-b67`) vs. PQ (`smpte2084`) HDR-to-SDR Rec.709 decode
    (never applying PQ `npl=100` Mobius tonemapping to HLG smartphone clips)
  - Video-only (`-an -frames:v`) shot intermediates, probed `xfade` offsets,
    cut-aligned segment assembly (`-c copy` concat), `--workers N` parallel rendering,
    and `--reuse-cache` content-hash caching
  - Clean `xfade` transitions (`dissolve`, `smoothleft`/`smoothright`/`smoothup`/`smoothdown`,
    `wipeleft`, warm `light_leak`/`film_burn`, `zoomin`) with auto-fade collision guard
  - Portrait & framed video pipeline running HDR decode -> `vidstab` -> `speed_profile`
    BEFORE `single`, `blurred_bg_pillarbox`, or `super8_frame` framing
  - `focal_anchor`-aware collage tile crops + balanced `4:5`/`4:3` `polaroid_stack` framing
  - `media_type == "graphic"` support including bespoke `rendered_card_path` passthrough
  - 4-track (`NAT_A`, `NAT_B`, `BGM`, `SFX`) `adelay` + `amix` frame-locked audio
    with J/L-cut overlaps, smooth `sonic_postcard` BGM ducking, and two-pass `-14 LUFS` mastering
  - EDL-aware `--extract-qa --edl` generating `qa_video_alignment.jpg`, `qa_chapter_cards.jpg`,
    `qa_collages.jpg`, `qa_transitions.jpg`, `qa_metrics.json`, `qa_sheet_*.jpg`, and `<=60s` chunks
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any


RENDERER_VERSION = "0.2.1-t1t5"

HLG_TO_SDR_ZSCALE = (
    "zscale=pin=bt2020:min=bt2020nc:p=bt709:m=bt709:r=tv,"
    "format=yuv420p,eq=gamma=0.95:brightness=-0.02"
)
PQ_TO_SDR_MOBIUS = (
    "zscale=tin=smpte2084:pin=bt2020:min=bt2020nc:t=linear:npl=100,"
    "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)

LOOK_FILTERS: dict[str, str] = {
    "warm_film": "eq=contrast=1.06:saturation=1.08:gamma=1.02,colorbalance=rs=0.06:gs=0.02:bs=-0.06:rm=0.04:bm=-0.04",
    "moody_teal_orange": "eq=contrast=1.12:saturation=1.04:brightness=-0.02,colorbalance=rs=-0.05:gs=0.01:bs=0.08:rh=0.07:gh=0.02:bh=-0.06",
    "clean_natural": "eq=contrast=1.03:saturation=1.03:gamma=1.0",
    "soft_atmospheric": "eq=contrast=0.95:brightness=0.03:saturation=0.92:gamma=1.05,colorbalance=rs=0.03:gs=0.02:bs=0.04",
}

DEFAULT_STYLE_PACK: dict[str, Any] = {
    "style_id": "cinema_doc",
    "palette": {
        "bg": "#0F1115",
        "surface": "#181B22",
        "text_primary": "#F5F2EB",
        "text_secondary": "#B9B2A3",
        "accent": "#D4AF37",
        "accent_secondary": "#7A9E9F",
        "border": "#2E333D",
        "swatches": ["#0F1115", "#D4AF37", "#F5F2EB", "#7A9E9F", "#3A404D"],
    },
    "color_grade": {
        "look_id": "warm_film",
        "ffmpeg_vf": "eq=contrast=1.06:saturation=1.06:gamma=1.02,colorbalance=rs=0.05:gs=0.02:bs=-0.05:rm=0.03:bm=-0.03",
    },
    "textures": {
        "grain_amount": 0.14,
        "vignette_intensity": 0.55,
        "letterbox_ratio": 0,
    },
    "layouts": {
        "default_portrait_layout": "split_diptych_pair",
        "gutter_px": 10,
        "pillarbox_dim_alpha": 0.28,
        "pillarbox_blur_sigma": 28.0,
    },
    "cards": {
        "backdrop_blur_sigma": 22.0,
        "backdrop_dim_alpha": 0.45,
        "backdrop_zoom_pct": 0.06,
    },
    "transitions": {
        "default_duration_sec": 0.45,
    },
    "audio": {
        "default_bgm_volume": 0.78,
        "duck_bgm_db": -14.0,
        "sonic_postcard_nat_gain_db": 1.0,
        "default_nat_bed_volume": 0.24,
        "default_j_cut_sec": 0.8,
        "default_l_cut_sec": 0.7,
    },
}

# Maps EDL v2.0 transition names -> (ffmpeg xfade transition, default duration sec, post-fx tag)
XFADE_TRANSITIONS: dict[str, tuple[str, float, str | None]] = {
    "dissolve": ("dissolve", 0.45, None),
    "xfade_dissolve": ("dissolve", 0.45, None),
    "whip_pan_left": ("smoothleft", 0.36, None),
    "whip_pan_right": ("smoothright", 0.36, None),
    "whip_pan_up": ("smoothup", 0.36, None),
    "whip_pan_down": ("smoothdown", 0.36, None),
    "flash_cut": ("dissolve", 0.18, None),
    "paper_wipe": ("wipeleft", 0.38, None),
    "light_leak": ("dissolve", 0.45, "warm_burn"),
    "film_burn": ("dissolve", 0.45, "warm_burn"),
    "zoom_punch": ("zoomin", 0.32, None),
}

VIDEO_CARD_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
IMAGE_CARD_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
MULTI_TILE_LAYOUTS = {
    "split_2up_video",
    "triptych_3up_video",
    "bento_living_grid",
    "polaroid_stack",
    "split_diptych_pair",
}


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _probe_filters() -> set[str]:
    proc = _run(["ffmpeg", "-hide_banner", "-filters"], timeout=10)
    return set(
        re.findall(
            r"\b(zscale|tonemap|signalstats|vidstabdetect|vidstabtransform|acrossfade|loudnorm|xfade|boxblur|adelay|amix)\b",
            proc.stdout or "",
        )
    )


def _probe_video_info(path: Path, dry_run: bool = False) -> dict[str, Any]:
    default_info: dict[str, Any] = {
        "has_audio": True if dry_run else False,
        "is_hdr": False,
        "color_transfer": "",
        "color_primaries": "",
        "color_profile": "SDR_Rec709",
        "duration_sec": None,
        "width": None,
        "height": None,
    }
    if dry_run or not path.exists():
        return default_info
    proc = _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=15,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return default_info
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    transfer = (v.get("color_transfer") or "").lower()
    primaries = (v.get("color_primaries") or "").lower()
    if transfer == "arib-std-b67":
        color_profile = "HDR_HLG"
    elif transfer == "smpte2084":
        color_profile = "HDR_PQ"
    else:
        color_profile = "SDR_Rec709"

    dur_raw = v.get("duration") or (data.get("format") or {}).get("duration")
    dur = float(dur_raw) if dur_raw is not None else None
    w = int(v["width"]) if v.get("width") else None
    h = int(v["height"]) if v.get("height") else None
    rot = 0
    v_tags = {k.lower(): val for k, val in (v.get("tags") or {}).items()}
    if "rotate" in v_tags:
        try:
            rot = abs(int(v_tags["rotate"]))
        except (TypeError, ValueError):
            rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rot = abs(int(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    if w and h and rot in (90, 270):
        w, h = h, w

    return {
        "has_audio": has_audio,
        "is_hdr": color_profile in {"HDR_HLG", "HDR_PQ"},
        "color_transfer": transfer,
        "color_primaries": primaries,
        "color_profile": color_profile,
        "duration_sec": dur,
        "width": w,
        "height": h,
    }


def _hdr_to_sdr_vf(
    info: dict[str, Any],
    avail_filters: set[str],
    shot_hint: dict[str, Any] | None = None,
) -> str:
    """Return HLG-safe or PQ-safe HDR-to-SDR FFmpeg filter chain (or '' for SDR).

    Never applies PQ `npl=100` Mobius tonemapping to HLG (`arib-std-b67`) clips.
    """
    transfer = str(info.get("color_transfer") or "").lower()
    profile = str(info.get("color_profile") or "")
    hint_profile = str((shot_hint or {}).get("color_profile") or "")
    hint_is_hdr = bool((shot_hint or {}).get("is_hdr", False))

    if not transfer and hint_profile in {"HDR_HLG", "HDR_PQ"}:
        profile = hint_profile

    if transfer == "smpte2084" or profile == "HDR_PQ":
        if "zscale" in avail_filters and "tonemap" in avail_filters:
            return PQ_TO_SDR_MOBIUS
        if "zscale" in avail_filters:
            return "zscale=pin=bt2020:min=bt2020nc:p=bt709:m=bt709:r=tv,format=yuv420p,eq=gamma=0.92:brightness=-0.03"
        return "format=yuv420p,eq=gamma=0.92:brightness=-0.03"

    if transfer == "arib-std-b67" or profile == "HDR_HLG" or bool(info.get("is_hdr")) or hint_is_hdr:
        if "zscale" in avail_filters:
            return HLG_TO_SDR_ZSCALE
        return "format=yuv420p,eq=gamma=0.95:brightness=-0.02"

    return ""


def _probe_clip_duration(path: Path, fallback: float) -> float:
    """Probe exact video stream duration of a rendered shot/segment via ffprobe."""
    if not path.is_file():
        return fallback
    proc = _run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_entries", "stream=codec_type,duration:format=duration",
            str(path),
        ],
        timeout=15,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return fallback
    try:
        data = json.loads(proc.stdout)
        v_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        raw = v_stream.get("duration") or (data.get("format") or {}).get("duration")
        if raw is not None:
            val = float(raw)
            if val > 0.01:
                return val
    except (ValueError, json.JSONDecodeError):
        pass
    return fallback


def _probe_media_aspect(
    path: Path,
    media_type: str,
    item: dict[str, Any],
    dry_run: bool = False,
) -> float | None:
    """Determine upright width/height aspect ratio for tile geometry decisions."""
    orient = str(item.get("orientation") or "").lower()
    if item.get("width") and item.get("height"):
        wf, hf = float(item["width"]), float(item["height"])
        if wf > 0 and hf > 0:
            if orient == "portrait" and wf > hf:
                wf, hf = hf, wf
            elif orient == "landscape" and hf > wf:
                wf, hf = hf, wf
            return wf / hf
    if not dry_run and path.is_file():
        if media_type == "photo":
            try:
                from PIL import Image, ImageOps  # type: ignore

                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im)
                    if im.width > 0 and im.height > 0:
                        return im.width / float(im.height)
            except Exception:  # noqa: BLE001
                pass
        else:
            info = _probe_video_info(path, dry_run=False)
            if info.get("width") and info.get("height"):
                return float(info["width"]) / float(info["height"])
    if orient == "portrait":
        return 9.0 / 16.0
    if orient == "landscape":
        return 16.0 / 9.0
    if orient == "square":
        return 1.0
    return None


def _load_style_pack(edl: dict[str, Any]) -> dict[str, Any]:
    """Load declarative Style Pack JSON from `styles/<style_id>.json`."""
    style_id = str(edl.get("style_id") or "cinema_doc").strip()
    candidates: list[Path] = []
    if edl.get("style_pack_path"):
        candidates.append(Path(str(edl["style_pack_path"])).expanduser())
    styles_dir = Path(__file__).resolve().parent.parent / "styles"
    candidates.append(styles_dir / f"{style_id}.json")

    for cand in candidates:
        if cand.is_file():
            try:
                loaded = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return loaded
            except (OSError, json.JSONDecodeError):
                pass
    return dict(DEFAULT_STYLE_PACK)


def _resolve_grade_and_grain(
    edl: dict[str, Any],
    style_pack: dict[str, Any],
) -> tuple[str, str, str]:
    """Return `(look_id, look_vf, grain_vf)` from Style Pack with legacy `look_id` fallback."""
    grade = style_pack.get("color_grade") or {}
    explicit_look = edl.get("look_id") or edl.get("color_look")
    style_look_id = grade.get("look_id") or "clean_natural"

    if explicit_look and not edl.get("style_id"):
        look_id = str(explicit_look)
        look_vf = LOOK_FILTERS.get(look_id, LOOK_FILTERS["clean_natural"])
    elif grade.get("ffmpeg_vf"):
        look_id = str(explicit_look or style_look_id)
        if explicit_look and explicit_look != style_look_id and explicit_look in LOOK_FILTERS:
            look_vf = LOOK_FILTERS[str(explicit_look)]
        else:
            look_vf = str(grade["ffmpeg_vf"])
    else:
        look_id = str(explicit_look or style_look_id)
        look_vf = LOOK_FILTERS.get(look_id, LOOK_FILTERS["clean_natural"])

    grain_amount = float((style_pack.get("textures") or {}).get("grain_amount", 0.14))
    if grain_amount > 0.005:
        c0s = max(1, min(32, int(round(grain_amount * 58.0))))
        grain_vf = f"noise=c0s={c0s}:c0f=t+u"
    else:
        grain_vf = ""
    return look_id, look_vf, grain_vf


def _parse_focal_anchor(raw: Any, default_y: float = 0.5) -> tuple[float, float]:
    """Accept both EDL v1.0 `{x, y}` and v2.0 `[x, y]` focal anchor shapes."""
    fx, fy = 0.5, default_y
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            fx, fy = float(raw[0]), float(raw[1])
        except (TypeError, ValueError):
            fx, fy = 0.5, default_y
    elif isinstance(raw, dict):
        try:
            fx = float(raw.get("x", 0.5))
            fy = float(raw.get("y", default_y))
        except (TypeError, ValueError):
            fx, fy = 0.5, default_y
    return min(0.95, max(0.05, fx)), min(0.95, max(0.05, fy))


def _crop_vf_with_anchor(
    tw: int,
    th: int,
    focal_anchor: Any,
    has_human_closeup: bool = False,
) -> str:
    """Build clamped FFmpeg scale+crop filter centered on focal_anchor (default fy=0.35 for human closeups)."""
    default_y = 0.35 if has_human_closeup else 0.5
    fx, fy = _parse_focal_anchor(focal_anchor, default_y=default_y)
    return (
        f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th}:x='max(0,min(iw-ow,iw*{fx:.3f}-ow/2))':y='max(0,min(ih-oh,ih*{fy:.3f}-oh/2))'"
    )


def _hex_to_rgb(hex_str: str, fallback: tuple[int, int, int] = (15, 17, 21)) -> tuple[int, int, int]:
    cleaned = str(hex_str or "").strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(c * 2 for c in cleaned)
    if len(cleaned) != 6:
        return fallback
    try:
        return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
    except ValueError:
        return fallback


def _hex_for_ffmpeg(hex_str: str, fallback: str = "0x0F1115") -> str:
    r, g, b = _hex_to_rgb(hex_str)
    return f"0x{r:02X}{g:02X}{b:02X}"


def _escape_drawtext(text: str) -> str:
    escaped = str(text or "")
    escaped = escaped.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace("%", "\\%")
    escaped = escaped.replace("\n", " ")
    return escaped


def _fade_vf_parts(shot: dict[str, Any], dur: float) -> list[str]:
    parts: list[str] = []
    fade_in = float(shot.get("fade_in") or 0.0)
    fade_out = float(shot.get("fade_out") or 0.0)
    if fade_in > 0.01:
        parts.append(f"fade=t=in:st=0:d={min(fade_in, dur * 0.45):.3f}")
    if fade_out > 0.01:
        fd = min(fade_out, dur * 0.45)
        parts.append(f"fade=t=out:st={max(0.0, dur - fd):.3f}:d={fd:.3f}")
    return parts


def _letterbox_vf_parts(style_pack: dict[str, Any], width: int, height: int) -> list[str]:
    ratio = float((style_pack.get("textures") or {}).get("letterbox_ratio") or 0.0)
    if ratio <= 1.85 or width <= height:
        return []
    active_h = int(round(width / ratio))
    bar_h = max(0, (height - active_h) // 2)
    if bar_h < 4:
        return []
    return [
        f"drawbox=x=0:y=0:w={width}:h={bar_h}:color=black:t=fill",
        f"drawbox=x=0:y={height - bar_h}:w={width}:h={bar_h}:color=black:t=fill",
    ]


def _smoothstep_src_delta(d_phase: float, v0: float, v1: float, u0: float, u1: float) -> float:
    """Analytical integral of cubic smoothstep velocity v(u)=v0+(v1-v0)(3u^2-2u^3)."""
    def _F(u: float) -> float:
        return v0 * u + (v1 - v0) * ((u ** 3) - 0.5 * (u ** 4))

    return d_phase * (_F(u1) - _F(u0))


def _build_cubic_ramp_slices(sp: dict[str, Any]) -> tuple[list[dict[str, float]], float, float]:
    """Build analytical micro-slices for 5-phase piecewise cubic speed ramps.

    Returns `(slices, total_out_dur, total_src_req)`.
    """
    v_hi = max(0.25, float(sp.get("v_hi", 3.5)))
    v_lo = max(0.20, float(sp.get("v_lo", 0.5)))
    d1 = max(0.0, float(sp.get("d1", 0.30)))
    d2 = max(0.0, float(sp.get("d2", 0.35)))
    d3 = max(0.0, float(sp.get("d3", 1.60)))
    d4 = max(0.0, float(sp.get("d4", 0.35)))
    d5 = max(0.0, float(sp.get("d5", 0.30)))

    slices: list[dict[str, float]] = []
    src_cursor = 0.0

    def _add_slice(out_d: float, src_d: float) -> None:
        nonlocal src_cursor
        if out_d <= 1e-4 or src_d <= 1e-4:
            return
        eff_speed = src_d / out_d
        slices.append(
            {
                "src_start": src_cursor,
                "src_end": src_cursor + src_d,
                "out_dur": out_d,
                "eff_speed": eff_speed,
            }
        )
        src_cursor += src_d

    if d1 > 1e-4:
        _add_slice(d1, v_hi * d1)
    if d2 > 1e-4:
        sub = 3
        for k in range(sub):
            u0, u1 = k / sub, (k + 1) / sub
            _add_slice(d2 / sub, _smoothstep_src_delta(d2, v_hi, v_lo, u0, u1))
    if d3 > 1e-4:
        _add_slice(d3, v_lo * d3)
    if d4 > 1e-4:
        sub = 3
        for k in range(sub):
            u0, u1 = k / sub, (k + 1) / sub
            _add_slice(d4 / sub, _smoothstep_src_delta(d4, v_lo, v_hi, u0, u1))
    if d5 > 1e-4:
        _add_slice(d5, v_hi * d5)

    total_out = sum(s["out_dur"] for s in slices)
    return slices, total_out, src_cursor


def _shot_output_duration(shot: dict[str, Any]) -> float:
    """Resolve exact output timeline duration (sec) for any shot type."""
    sp = shot.get("speed_profile")
    if isinstance(sp, dict):
        mode = str(sp.get("mode") or sp.get("type") or "constant").lower()
        if mode == "cubic_ramp":
            if shot.get("duration_sec"):
                return max(0.2, float(shot["duration_sec"]))
            _, total_out, _ = _build_cubic_ramp_slices(sp)
            if total_out > 0.1:
                return total_out
        elif mode == "constant":
            rate = max(0.1, float(sp.get("rate", 1.0)))
            if shot.get("duration_sec"):
                return max(0.2, float(shot["duration_sec"]))
            in_sec = float(shot.get("in_sec", 0.0))
            out_sec = float(shot.get("out_sec", in_sec + 3.0))
            return max(0.2, (out_sec - in_sec) / rate)

    if shot.get("duration_sec"):
        return max(0.2, float(shot["duration_sec"]))
    in_sec = float(shot.get("in_sec", 0.0))
    out_sec = float(shot.get("out_sec", in_sec + 3.0))
    return max(0.2, out_sec - in_sec)


def _atempo_chain(rate: float) -> str:
    """Build valid chained FFmpeg `atempo` filters (each stage in [0.5, 2.0])."""
    stages: list[str] = []
    rem = max(0.25, min(4.0, rate))
    while rem < 0.5:
        stages.append("atempo=0.5")
        rem /= 0.5
    while rem > 2.0:
        stages.append("atempo=2.0")
        rem /= 2.0
    if abs(rem - 1.0) > 1e-3:
        stages.append(f"atempo={rem:.4f}")
    return ",".join(stages)


def _compose_tail_vf(
    look_vf: str,
    grain_vf: str,
    shot: dict[str, Any],
    dur: float,
    style_pack: dict[str, Any],
    width: int,
    height: int,
) -> str:
    parts = [p for p in [look_vf, grain_vf] if p]
    parts.extend(_letterbox_vf_parts(style_pack, width, height))
    parts.extend(_fade_vf_parts(shot, dur))
    parts.append("format=yuv420p")
    return ",".join(parts)


def _render_ken_burns_shot(
    shot: dict[str, Any],
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    look_vf: str,
    grain_vf: str,
    style_pack: dict[str, Any],
) -> list[str]:
    """Build 2x-supersampled zoompan Ken Burns command anchored to `focal_anchor` (video-only)."""
    dur = _shot_output_duration(shot)
    frames = max(2, int(round(dur * fps)))
    is_human = bool(shot.get("has_human_closeup", False))
    fx, fy = _parse_focal_anchor(shot.get("focal_anchor"), default_y=0.35 if is_human else 0.5)

    super_w = width * 2
    super_h = height * 2
    direction = str(shot.get("ken_burns_dir") or "").lower()
    motion_type = str(shot.get("motion_type") or "").lower()
    if not direction:
        if motion_type == "ken_burns_out":
            direction = "out"
        elif motion_type == "static_locked":
            direction = "static"
        else:
            direction = "in"

    if direction == "static":
        z_expr = "1.0"
    elif direction == "out":
        z_expr = f"max(1.0,1.15-0.15*on/{frames})"
    else:
        z_expr = f"min(1.15,1.0+0.15*on/{frames})"

    x_expr = f"max(0,min(iw-iw/zoom,(iw-iw/zoom)*{fx:.3f}))"
    y_expr = f"max(0,min(ih-ih/zoom,(ih-ih/zoom)*{fy:.3f}))"
    tail_vf = _compose_tail_vf(look_vf, grain_vf, shot, dur, style_pack, width, height)

    vf = (
        f"scale={super_w}:{super_h}:force_original_aspect_ratio=increase,"
        f"crop={super_w}:{super_h}:x='max(0,min(iw-ow,iw*{fx:.3f}-ow/2))':y='max(0,min(ih-oh,ih*{fy:.3f}-oh/2))',"
        f"setsar=1,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={fps},"
        f"{tail_vf}"
    )
    return [
        "ffmpeg", "-y", "-loop", "1", "-i", shot["source_path"],
        "-vf", vf,
        "-an", "-r", str(fps), "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        str(out_clip),
    ]


def _render_multi_tile_shot(
    shot: dict[str, Any],
    layout: str,
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    look_vf: str,
    grain_vf: str,
    style_pack: dict[str, Any],
    avail_filters: set[str],
    dry_run: bool,
) -> list[str]:
    """Render synchronized 2-up, 3-up triptych, Living Bento, or Polaroid grids via hstack/vstack (video-only)."""
    dur = _shot_output_duration(shot)
    n_frames = max(2, int(round(dur * fps)))
    tiles_raw = shot.get("tiles")
    tiles: list[dict[str, Any]] = []
    if isinstance(tiles_raw, list) and len(tiles_raw) >= 2:
        tiles = list(tiles_raw)
    elif shot.get("source_path") and shot.get("pair_source_path"):
        default_mt = shot.get("media_type", "photo")
        tiles = [
            {
                "source_path": shot["source_path"],
                "media_type": default_mt,
                "in_sec": float(shot.get("in_sec", 0.0)),
                "focal_anchor": shot.get("focal_anchor"),
                "has_human_closeup": bool(shot.get("has_human_closeup", False)),
            },
            {
                "source_path": shot["pair_source_path"],
                "media_type": default_mt,
                "in_sec": float(shot.get("in_sec", 0.0)),
                "has_human_closeup": bool(shot.get("has_human_closeup", False)),
            },
        ]
    else:
        raise ValueError(f"Layout {layout!r} on shot {shot.get('shot_id')} requires >=2 tiles or pair_source_path.")

    # Cap tile counts cleanly per layout
    if layout in {"split_2up_video", "split_diptych_pair"}:
        tiles = tiles[:2]
    elif layout == "triptych_3up_video":
        tiles = tiles[:3]
    else:
        tiles = tiles[:4]

    lay_cfg = style_pack.get("layouts") or {}
    gutter = int(lay_cfg.get("gutter_px", 8))
    gutter = max(0, min(32, gutter - (gutter % 2)))  # keep even
    bg_hex = _hex_for_ffmpeg((style_pack.get("palette") or {}).get("bg", "#0F1115"))
    tail_vf = _compose_tail_vf(look_vf, grain_vf, shot, dur, style_pack, width, height)

    inputs: list[str] = []
    tile_filters: list[str] = []

    def _tile_input_and_pre(idx: int, tile: dict[str, Any], tw: int, th: int, extra_pad: str = "") -> str:
        t_src = Path(tile["source_path"])
        t_mt = str(tile.get("media_type", "video")).lower()
        t_in = float(tile.get("in_sec", 0.0))
        if t_mt == "photo":
            inputs.extend(["-loop", "1", "-t", f"{dur + 0.25:.4f}", "-i", str(t_src)])
            hdr_prefix = ""
        else:
            inputs.extend(["-ss", f"{t_in:.4f}", "-t", f"{dur + 0.25:.4f}", "-i", str(t_src)])
            info = _probe_video_info(t_src, dry_run=dry_run)
            hdr_chain = _hdr_to_sdr_vf(info, avail_filters, shot_hint=tile)
            hdr_prefix = f"{hdr_chain}," if hdr_chain else ""

        t_human = bool(tile.get("has_human_closeup", False))
        crop_vf = _crop_vf_with_anchor(tw, th, tile.get("focal_anchor"), has_human_closeup=t_human)
        lbl = f"[t{idx}]"
        chain = f"[{idx}:v]{hdr_prefix}{crop_vf},setsar=1,fps={fps},setpts=PTS-STARTPTS"
        if extra_pad:
            chain += f",{extra_pad}"
        chain += lbl
        tile_filters.append(chain)
        return lbl

    def _polaroid_tile(idx: int, tile: dict[str, Any], col_w: int, right_gutter: int, max_card_w: int | None = None) -> str:
        """Render a balanced 4:5 (portrait) / 4:3 (landscape) / 1:1 Polaroid print with clean white caption margin."""
        t_src = Path(tile["source_path"])
        t_mt = str(tile.get("media_type", "photo")).lower()
        ar = _probe_media_aspect(t_src, t_mt, tile, dry_run=dry_run)
        card_w = min(max_card_w, col_w - 28) if max_card_w else (col_w - 28)
        card_w = max(120, card_w) & ~1
        border = max(16, int(round(card_w * 0.045))) & ~1
        inner_w = max(64, card_w - 2 * border) & ~1
        bottom_margin = max(84, int(round(border * 4.2))) & ~1
        max_inner_h = max(64, height - border - bottom_margin - 48) & ~1
        if ar is not None and ar > 1.15:
            # 4:3 balanced landscape window
            inner_h = min(max_inner_h, int(round(inner_w * 3.0 / 4.0))) & ~1
        elif ar is not None and ar < 0.88:
            # 4:5 balanced portrait window
            inner_h = min(max_inner_h, int(round(inner_w * 5.0 / 4.0))) & ~1
        else:
            # Classic 1:1 instant Polaroid window
            inner_h = min(max_inner_h, inner_w) & ~1
        card_h = min(height - 24, inner_h + border + bottom_margin) & ~1
        pad_chain = (
            f"pad={card_w}:{card_h}:{border}:{border}:color=0xFAF7F2,"
            f"pad={col_w}:{height}:(ow-iw)/2:(oh-ih)/2:color={bg_hex}"
        )
        if right_gutter > 0:
            pad_chain += f",pad={col_w + right_gutter}:{height}:0:0:color={bg_hex}"
        return _tile_input_and_pre(idx, tile, inner_w, inner_h, pad_chain)

    if layout in {"split_2up_video", "split_diptych_pair"} or (layout == "bento_living_grid" and len(tiles) == 2):
        w0 = ((width - gutter) // 2) & ~1
        w1 = width - w0 - gutter
        pad0 = f"pad={w0 + gutter}:{height}:0:0:color={bg_hex}" if gutter > 0 else ""
        l0 = _tile_input_and_pre(0, tiles[0], w0, height, pad0)
        l1 = _tile_input_and_pre(1, tiles[1], w1, height)
        combine = f"{l0}{l1}hstack=inputs=2,{tail_vf}[v]"
    elif layout == "triptych_3up_video" or (layout == "polaroid_stack" and len(tiles) == 3):
        n = len(tiles)
        if n == 2:
            w0 = ((width - gutter) // 2) & ~1
            w1 = width - w0 - gutter
            pad0 = f"pad={w0 + gutter}:{height}:0:0:color={bg_hex}" if gutter > 0 else ""
            l0 = _tile_input_and_pre(0, tiles[0], w0, height, pad0)
            l1 = _tile_input_and_pre(1, tiles[1], w1, height)
            combine = f"{l0}{l1}hstack=inputs=2,{tail_vf}[v]"
        else:
            w0 = ((width - 2 * gutter) // 3) & ~1
            w1 = w0
            w2 = width - w0 - w1 - 2 * gutter
            if layout == "polaroid_stack":
                l0 = _polaroid_tile(0, tiles[0], w0, gutter)
                l1 = _polaroid_tile(1, tiles[1], w1, gutter)
                l2 = _polaroid_tile(2, tiles[2], w2, 0)
            else:
                pad0 = f"pad={w0 + gutter}:{height}:0:0:color={bg_hex}" if gutter > 0 else ""
                pad1 = f"pad={w1 + gutter}:{height}:0:0:color={bg_hex}" if gutter > 0 else ""
                l0 = _tile_input_and_pre(0, tiles[0], w0, height, pad0)
                l1 = _tile_input_and_pre(1, tiles[1], w1, height, pad1)
                l2 = _tile_input_and_pre(2, tiles[2], w2, height)
            combine = f"{l0}{l1}{l2}hstack=inputs=3,{tail_vf}[v]"
    elif layout == "bento_living_grid" and len(tiles) == 3:
        left_w = (int(width * 0.58) & ~1)
        right_w = width - left_w - gutter
        top_h = ((height - gutter) // 2) & ~1
        bot_h = height - top_h - gutter
        pad_l = f"pad={left_w + gutter}:{height}:0:0:color={bg_hex}" if gutter > 0 else ""
        pad_t = f"pad={right_w}:{top_h + gutter}:0:0:color={bg_hex}" if gutter > 0 else ""
        l0 = _tile_input_and_pre(0, tiles[0], left_w, height, pad_l)
        l1 = _tile_input_and_pre(1, tiles[1], right_w, top_h, pad_t)
        l2 = _tile_input_and_pre(2, tiles[2], right_w, bot_h)
        combine = f"{l1}{l2}vstack=inputs=2[rcol];{l0}[rcol]hstack=inputs=2,{tail_vf}[v]"
    elif layout == "bento_living_grid" and len(tiles) >= 4:
        w0 = ((width - gutter) // 2) & ~1
        w1 = width - w0 - gutter
        h0 = ((height - gutter) // 2) & ~1
        h1 = height - h0 - gutter
        pad_w = f"pad={w0 + gutter}:{h0}:0:0:color={bg_hex}" if gutter > 0 else ""
        pad_w_b = f"pad={w0 + gutter}:{h1}:0:0:color={bg_hex}" if gutter > 0 else ""
        l0 = _tile_input_and_pre(0, tiles[0], w0, h0, pad_w)
        l1 = _tile_input_and_pre(1, tiles[1], w1, h0)
        l2 = _tile_input_and_pre(2, tiles[2], w0, h1, pad_w_b)
        l3 = _tile_input_and_pre(3, tiles[3], w1, h1)
        row_pad = f",pad={width}:{h0 + gutter}:0:0:color={bg_hex}" if gutter > 0 else ""
        combine = f"{l0}{l1}hstack=inputs=2{row_pad}[top];{l2}{l3}hstack=inputs=2[bot];[top][bot]vstack=inputs=2,{tail_vf}[v]"
    else:
        # 2-card Polaroid stack fallback
        w0 = ((width - gutter) // 2) & ~1
        w1 = width - w0 - gutter
        max_card_w = min(760, w0 - 64) & ~1
        l0 = _polaroid_tile(0, tiles[0], w0, gutter, max_card_w=max_card_w)
        l1 = _polaroid_tile(1, tiles[1], w1, 0, max_card_w=max_card_w)
        combine = f"{l0}{l1}hstack=inputs=2,{tail_vf}[v]"

    fc = ";".join(tile_filters) + ";" + combine
    return [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", "[v]",
        "-an", "-r", str(fps), "-frames:v", str(n_frames),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        str(out_clip),
    ]


def _build_layout_stage_fc(
    in_label: str,
    layout: str,
    width: int,
    height: int,
    shot: dict[str, Any],
    style_pack: dict[str, Any],
    tail_vf: str,
) -> list[str]:
    """Build layout framing filter stage (`single`, `blurred_bg_pillarbox`, or `super8_frame`) -> `[v]`."""
    lay_cfg = style_pack.get("layouts") or {}
    blur_sigma = float(lay_cfg.get("pillarbox_blur_sigma", 28.0))
    dim_alpha = float(lay_cfg.get("pillarbox_dim_alpha", 0.22))
    dim_brightness = -min(0.32, max(0.08, dim_alpha * 0.65))
    is_human = bool(shot.get("has_human_closeup", False))

    if layout == "super8_frame":
        gate_w = min(width, int(round(height * 4.0 / 3.0))) & ~1
        gate_h = (height - 48) & ~1
        crop_vf = _crop_vf_with_anchor(gate_w, gate_h, shot.get("focal_anchor"), has_human_closeup=is_human)
        return [
            f"{in_label}{crop_vf},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x0A0908,"
            f"setsar=1,{tail_vf}[v]"
        ]
    if layout == "blurred_bg_pillarbox":
        return [
            f"{in_label}split=2[bg0][fg0]",
            f"[bg0]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"gblur=sigma={blur_sigma:.1f},eq=brightness={dim_brightness:.3f}[bg]",
            f"[fg0]scale=-2:{height}:force_original_aspect_ratio=decrease[fg]",
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,{tail_vf}[v]",
        ]
    crop_vf = _crop_vf_with_anchor(width, height, shot.get("focal_anchor"), has_human_closeup=is_human)
    return [f"{in_label}{crop_vf},setsar=1,{tail_vf}[v]"]


def _render_portrait_or_framed_shot(
    shot: dict[str, Any],
    layout: str,
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    look_vf: str,
    grain_vf: str,
    style_pack: dict[str, Any],
    avail_filters: set[str],
    dry_run: bool,
) -> list[str]:
    """Render single portrait (`blurred_bg_pillarbox`) or `super8_frame` still photo clip (video-only)."""
    dur = _shot_output_duration(shot)
    n_frames = max(2, int(round(dur * fps)))
    mtype = str(shot.get("media_type", "photo")).lower()
    src = Path(shot["source_path"])
    tail_vf = _compose_tail_vf(look_vf, grain_vf, shot, dur, style_pack, width, height)

    pre_parts: list[str] = []
    if mtype == "video":
        info = _probe_video_info(src, dry_run=dry_run)
        hdr_chain = _hdr_to_sdr_vf(info, avail_filters, shot_hint=shot)
        if hdr_chain:
            pre_parts.append(hdr_chain)
    pre_parts.extend([f"fps={fps}", "setpts=PTS-STARTPTS"])
    fc_parts = [f"[0:v]{','.join(pre_parts)}[vtimed]"]
    fc_parts.extend(_build_layout_stage_fc("[vtimed]", layout, width, height, shot, style_pack, tail_vf))

    in_args = (
        ["-loop", "1", "-t", f"{dur + 0.25:.4f}", "-i", str(src)]
        if mtype == "photo"
        else ["-ss", f"{float(shot.get('in_sec', 0.0)):.4f}", "-t", f"{dur + 0.25:.4f}", "-i", str(src)]
    )
    return [
        "ffmpeg", "-y",
        *in_args,
        "-filter_complex", ";".join(fc_parts),
        "-map", "[v]",
        "-an", "-r", str(fps), "-frames:v", str(n_frames),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        str(out_clip),
    ]


def _render_video_shot(
    shot: dict[str, Any],
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    look_vf: str,
    grain_vf: str,
    style_pack: dict[str, Any],
    avail_filters: set[str],
    stabilize: bool,
    work_dir: Path,
    dry_run: bool,
    layout: str = "single",
) -> list[list[str]]:
    """Render any single-source video shot (video-only): HDR decode -> vidstab -> speed_profile -> layout framing."""
    in_sec = float(shot.get("in_sec", 0.0))
    dur = _shot_output_duration(shot)
    n_frames = max(2, int(round(dur * fps)))
    src = Path(shot["source_path"])
    info = _probe_video_info(src, dry_run=dry_run)
    hdr_chain = _hdr_to_sdr_vf(info, avail_filters, shot_hint=shot)
    tail_vf = _compose_tail_vf(look_vf, grain_vf, shot, dur, style_pack, width, height)

    sp = shot.get("speed_profile") if isinstance(shot.get("speed_profile"), dict) else None
    sp_mode = str(sp.get("mode") or sp.get("type") or "").lower() if sp else ""

    cmds: list[list[str]] = []

    # Analytical micro-slice piecewise cubic speed ramp (works for landscape, portrait pillarbox, & super8_frame)
    if sp and sp_mode == "cubic_ramp":
        slices, total_out, total_src_req = _build_cubic_ramp_slices(sp)
        if slices:
            target_out = dur if shot.get("duration_sec") else total_out
            target_frames = max(2, int(round(target_out * fps)))
            pre_parts: list[str] = []
            if hdr_chain:
                pre_parts.append(hdr_chain)
            if stabilize and "vidstabdetect" in avail_filters and "vidstabtransform" in avail_filters:
                trf_path = work_dir / f"{out_clip.stem}.trf"
                cmds.append([
                    "ffmpeg", "-y", "-ss", f"{in_sec:.4f}", "-t", f"{total_src_req + 0.50:.4f}", "-i", str(src),
                    "-vf", f"vidstabdetect=shakiness=5:accuracy=15:result={trf_path}",
                    "-f", "null", "-",
                ])
                pre_parts.append(f"vidstabtransform=input={trf_path}:smoothing=15:zoom=0:optzoom=1")
            pre_parts.append("setpts=PTS-STARTPTS")
            pre_chain = ",".join(pre_parts)

            k = len(slices)
            split_labels = "".join(f"[sb{idx}]" for idx in range(k))
            fc_parts = [f"[0:v]{pre_chain},split={k}{split_labels}"]
            slice_out_labels: list[str] = []
            for idx, sl in enumerate(slices):
                pts_mult = 1.0 / max(0.15, sl["eff_speed"])
                blur_seg = ",tmix=frames=2:weights='1 1'" if sl["eff_speed"] > 1.5 else ""
                fc_parts.append(
                    f"[sb{idx}]trim=start={sl['src_start']:.4f}:end={sl['src_end']:.4f},"
                    f"setpts=(PTS-STARTPTS)*{pts_mult:.6f}{blur_seg}[sr{idx}]"
                )
                slice_out_labels.append(f"[sr{idx}]")
            concat_in = "".join(slice_out_labels)
            fc_parts.append(
                f"{concat_in}concat=n={k}:v=1:a=0,fps={fps},trim=duration={target_out + 0.15:.4f},setpts=PTS-STARTPTS[vtimed]"
            )
            fc_parts.extend(_build_layout_stage_fc("[vtimed]", layout, width, height, shot, style_pack, tail_vf))
            cmds.append([
                "ffmpeg", "-y",
                "-ss", f"{in_sec:.4f}", "-t", f"{total_src_req + 0.60:.4f}", "-i", str(src),
                "-filter_complex", ";".join(fc_parts),
                "-map", "[v]",
                "-an", "-r", str(fps), "-frames:v", str(target_frames),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
                str(out_clip),
            ])
            return cmds

    # Constant speed (e.g. 0.5x -> setpts=2.0*PTS, 0.4x -> setpts=2.5*PTS) or 1.0x normal
    rate = 1.0
    if sp and sp_mode == "constant":
        rate = max(0.2, min(8.0, float(sp.get("rate", 1.0))))
    src_span = dur * rate

    pre_parts = []
    if hdr_chain:
        pre_parts.append(hdr_chain)

    if stabilize and "vidstabdetect" in avail_filters and "vidstabtransform" in avail_filters:
        trf_path = work_dir / f"{out_clip.stem}.trf"
        cmds.append([
            "ffmpeg", "-y", "-ss", f"{in_sec:.4f}", "-t", f"{src_span + 0.25:.4f}", "-i", str(src),
            "-vf", f"vidstabdetect=shakiness=5:accuracy=15:result={trf_path}",
            "-f", "null", "-",
        ])
        pre_parts.append(f"vidstabtransform=input={trf_path}:smoothing=15:zoom=0:optzoom=1")

    if abs(rate - 1.0) > 1e-3:
        pts_mult = 1.0 / rate
        pre_parts.append(f"setpts={pts_mult:.4f}*(PTS-STARTPTS)")
    else:
        pre_parts.append("setpts=PTS-STARTPTS")
    pre_parts.append(f"fps={fps}")

    fc_parts = [f"[0:v]{','.join(pre_parts)}[vtimed]"]
    fc_parts.extend(_build_layout_stage_fc("[vtimed]", layout, width, height, shot, style_pack, tail_vf))

    cmds.append([
        "ffmpeg", "-y",
        "-ss", f"{in_sec:.4f}", "-t", f"{src_span + 0.30:.4f}", "-i", str(src),
        "-filter_complex", ";".join(fc_parts),
        "-map", "[v]",
        "-an", "-r", str(fps), "-frames:v", str(n_frames),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        str(out_clip),
    ])
    return cmds


def _render_card_png_pillow(
    shot: dict[str, Any],
    card_png: Path,
    width: int,
    height: int,
    style_pack: dict[str, Any],
) -> bool:
    """Render a styled graphic card PNG with Pillow when installed."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps  # type: ignore
    except ImportError:
        return False

    pal = style_pack.get("palette") or DEFAULT_STYLE_PACK["palette"]
    cards_cfg = style_pack.get("cards") or DEFAULT_STYLE_PACK["cards"]
    bg_rgb = _hex_to_rgb(pal.get("bg", "#0F1115"))
    surface_rgb = _hex_to_rgb(pal.get("surface", "#181B22"))
    accent_rgb = _hex_to_rgb(pal.get("accent", "#D4AF37"))
    accent2_rgb = _hex_to_rgb(pal.get("accent_secondary", "#7A9E9F"))
    text_rgb = _hex_to_rgb(pal.get("text_primary", "#F5F2EB"))
    muted_rgb = _hex_to_rgb(pal.get("text_secondary", "#B9B2A3"))

    gtype = str(shot.get("graphic_type") or shot.get("overlay") or "chapter_title").lower()
    backdrop_raw = shot.get("backdrop_path")
    has_backdrop = bool(backdrop_raw and Path(str(backdrop_raw)).expanduser().is_file())

    canvas = Image.new("RGBA", (width, height), (*bg_rgb, 255))

    def _fit_cover(path_str: str, tw: int, th: int) -> Any:
        resample = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(Path(path_str).expanduser()) as im:
            im = ImageOps.exif_transpose(im).convert("RGBA")
            return ImageOps.fit(im, (tw, th), method=resample)

    if has_backdrop and backdrop_raw:
        try:
            bg_img = _fit_cover(str(backdrop_raw), width, height)
            blur_rad = max(8.0, float(cards_cfg.get("backdrop_blur_sigma", 20.0)))
            bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=blur_rad))
            dim_alpha = int(max(95, min(200, float(cards_cfg.get("backdrop_dim_alpha", 0.48)) * 255 + 24)))
            scrim = Image.new("RGBA", (width, height), (10, 11, 15, dim_alpha))
            canvas = Image.alpha_composite(bg_img, scrim)
            text_rgb = (247, 244, 238)
            muted_rgb = (224, 218, 208)
        except Exception:  # noqa: BLE001
            has_backdrop = False

    def _load_font(size: int, serif: bool = False) -> Any:
        candidates = (
            [
                "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
                "/System/Library/Fonts/Supplemental/Baskerville.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            ]
            if serif
            else [
                "/System/Library/Fonts/HelveticaNeue.ttc",
                "/System/Library/Fonts/SFNS.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
        )
        for fp in candidates:
            if Path(fp).is_file():
                try:
                    return ImageFont.truetype(fp, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    draw = ImageDraw.Draw(canvas)
    inset = 48
    draw.rectangle(
        [inset, inset, width - inset, height - inset],
        outline=(*accent_rgb, 96),
        width=2,
    )

    def _center_text(y: float, text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((width - tw) / 2.0, y), text, font=font, fill=fill)

    if gtype == "text_behind_subject" and shot.get("source_path") and Path(str(shot["source_path"])).expanduser().is_file():
        hero = _fit_cover(str(shot["source_path"]), width, height)
        canvas = hero.copy()
        draw = ImageDraw.Draw(canvas)
        title_str = str(shot.get("title") or "ADVENTURE").upper()
        f_big = _load_font(int(height * 0.16), serif=False)
        _center_text(height * 0.28, title_str, f_big, (*text_rgb, 235))
        mask_raw = shot.get("mask_path")
        if mask_raw and Path(str(mask_raw)).expanduser().is_file():
            try:
                resample = getattr(Image, "Resampling", Image).LANCZOS
                with Image.open(Path(str(mask_raw)).expanduser()) as m_im:
                    mask_l = ImageOps.fit(m_im.convert("L"), (width, height), method=resample)
                canvas = Image.composite(hero, canvas, mask_l)
            except Exception:  # noqa: BLE001
                pass
    elif gtype == "route_map":
        wps = shot.get("waypoints") or []
        f_eye = _load_font(20, serif=False)
        f_title = _load_font(48, serif=True)
        f_lbl = _load_font(22, serif=False)
        f_mono = _load_font(16, serif=False)
        draw.text((88, 70), str(shot.get("eyebrow") or "ROUTE OVERVIEW").upper(), font=f_eye, fill=(*accent_rgb, 240))
        draw.text((88, 102), str(shot.get("title") or "Trip Route"), font=f_title, fill=(*text_rgb, 250))

        # Subtle grid
        for gx in range(160, width, 160):
            draw.line([(gx, inset), (gx, height - inset)], fill=(*accent_rgb, 24), width=1)
        for gy in range(135, height, 135):
            draw.line([(inset, gy), (width - inset, gy)], fill=(*accent_rgb, 24), width=1)

        ep_cfg = shot.get("elevation_profile")
        ep_disabled = ep_cfg is False or (isinstance(ep_cfg, dict) and ep_cfg.get("enabled") is False)
        if ep_disabled:
            elev_pts: list[dict[str, Any]] = []
        elif isinstance(ep_cfg, dict) and isinstance(ep_cfg.get("points"), list) and len(ep_cfg["points"]) >= 2:
            elev_pts = [p for p in ep_cfg["points"] if isinstance(p.get("elevation_m"), (int, float))]
        else:
            elev_pts = [w for w in wps if isinstance(w.get("elevation_m"), (int, float))]
        has_elev = len(elev_pts) >= 2
        map_top = 190
        map_bottom = height - 290 if has_elev else height - 150
        map_pad_x = 260

        if wps:
            lats = [float(w["lat"]) for w in wps]
            lons = [float(w["lon"]) for w in wps]
            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)
            lat_span = max(0.01, max_lat - min_lat)
            lon_span = max(0.01, max_lon - min_lon)
            pts: list[tuple[float, float, dict[str, Any]]] = []
            for w in wps:
                xn = 0.5 if len(wps) == 1 else (float(w["lon"]) - min_lon) / lon_span
                yn = 0.5 if len(wps) == 1 else (float(w["lat"]) - min_lat) / lat_span
                px = map_pad_x + xn * (width - 2 * map_pad_x)
                py = map_bottom - yn * (map_bottom - map_top)
                pts.append((px, py, w))
            if len(pts) >= 2:
                draw.line([(p[0], p[1]) for p in pts], fill=(*accent_rgb, 235), width=5)
            for px, py, w in pts:
                draw.ellipse([px - 11, py - 11, px + 11, py + 11], fill=(*accent_rgb, 90))
                draw.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(*text_rgb, 255), outline=(*accent_rgb, 255), width=2)
                lbl = str(w.get("label") or "")
                if isinstance(w.get("elevation_m"), (int, float)):
                    lbl = f"{lbl} ({int(round(float(w['elevation_m'])))}m)"
                draw.text((px + 16, py - 12), lbl, font=f_lbl, fill=(*text_rgb, 245))

        if has_elev:
            ex0, ey0, ex1, ey1 = 110, height - 245, width - 110, height - 74
            draw.rectangle([ex0, ey0, ex1, ey1], fill=(*surface_rgb, 215), outline=(*accent_rgb, 95), width=2)
            draw.text((ex0 + 24, ey0 + 14), "ELEVATION PROFILE", font=f_mono, fill=(*accent_rgb, 240))
            vals = [float(w["elevation_m"]) for w in elev_pts]
            emin, emax = min(vals), max(vals)
            espan = max(1.0, emax - emin)
            draw.text(
                (ex1 - 240, ey0 + 14),
                f"MIN {int(round(emin))}m  /  PEAK {int(round(emax))}m",
                font=f_mono,
                fill=(*muted_rgb, 230),
            )
            pl, pr, pt, pb = ex0 + 48, ex1 - 48, ey0 + 48, ey1 - 36
            e_coords: list[tuple[float, float]] = []
            for idx, w in enumerate(elev_pts):
                frac = idx / max(1, len(elev_pts) - 1)
                cx = pl + frac * (pr - pl)
                cy = pb - ((float(w["elevation_m"]) - emin) / espan) * (pb - pt)
                e_coords.append((cx, cy))
            draw.line(e_coords, fill=(*accent_rgb, 245), width=4)
            for (cx, cy), w in zip(e_coords, elev_pts):
                draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=(*text_rgb, 255), outline=(*accent_rgb, 255), width=2)
                draw.text((cx - 32, pb + 8), str(w.get("label") or "")[:18], font=f_mono, fill=(*muted_rgb, 220))
    elif gtype == "trip_wrapped":
        f_eye = _load_font(20, serif=False)
        f_title = _load_font(54, serif=True)
        f_lbl = _load_font(18, serif=False)
        f_val = _load_font(62, serif=True)
        f_unit = _load_font(22, serif=False)
        draw.text((112, 86), str(shot.get("eyebrow") or "TRIP WRAPPED").upper(), font=f_eye, fill=(*accent_rgb, 245))
        draw.text((112, 118), str(shot.get("title") or "Journey in Numbers"), font=f_title, fill=(*text_rgb, 250))
        draw.line([(112, 196), (width - 112, 196)], fill=(*accent_rgb, 110), width=2)

        raw_stats = shot.get("stats") or []
        stats = [s for s in raw_stats if s.get("coverage_pct") is None or float(s["coverage_pct"]) >= 30.0][:6]
        cols = 2 if len(stats) <= 4 else 3
        rows = max(1, math.ceil(max(1, len(stats)) / cols))
        gap = 28
        grid_w = width - 224
        grid_h = height - 320
        cw = (grid_w - gap * (cols - 1)) // cols
        ch = min(280, (grid_h - gap * (rows - 1)) // rows)
        start_y = 236 + max(0, (grid_h - (rows * ch + gap * (rows - 1))) // 2)
        for idx, st in enumerate(stats):
            r, c = divmod(idx, cols)
            x0 = 112 + c * (cw + gap)
            y0 = start_y + r * (ch + gap)
            tint = accent_rgb if idx % 2 == 0 else accent2_rgb
            draw.rectangle([x0, y0, x0 + cw, y0 + ch], fill=(*surface_rgb, 215), outline=(*accent_rgb, 85), width=2)
            draw.rectangle([x0, y0, x0 + cw, y0 + 6], fill=(*tint, 240))
            draw.text((x0 + 28, y0 + 28), str(st.get("label") or "").upper(), font=f_lbl, fill=(*muted_rgb, 235))
            val_str = str(st.get("value", ""))
            unit_str = str(st.get("unit") or "")
            draw.text((x0 + 28, y0 + 78), val_str, font=f_val, fill=(*text_rgb, 255))
            if unit_str:
                draw.text((x0 + 28, y0 + 160), unit_str, font=f_unit, fill=(*tint, 240))
    elif gtype == "cast_intro":
        cm = shot.get("cast_member") or {}
        name = str(cm.get("name") or shot.get("title") or "Traveler")
        nickname = str(cm.get("nickname") or shot.get("eyebrow") or "MEET THE CREW")
        role = str(cm.get("role") or shot.get("subtitle") or "")
        if shot.get("source_path") and Path(str(shot["source_path"])).expanduser().is_file():
            try:
                p_w, p_h = 560, 680
                portrait = _fit_cover(str(shot["source_path"]), p_w, p_h)
                frame_box = Image.new("RGBA", (p_w + 40, p_h + 100), (250, 247, 242, 255))
                frame_box.paste(portrait, (20, 20))
                canvas.alpha_composite(frame_box, (180, (height - (p_h + 100)) // 2))
            except Exception:  # noqa: BLE001
                pass
        f_eye = _load_font(24, serif=False)
        f_name = _load_font(76, serif=True)
        f_role = _load_font(30, serif=False)
        tx = 860
        draw.text((tx, height * 0.36), nickname.upper(), font=f_eye, fill=(*accent_rgb, 245))
        draw.text((tx, height * 0.42), name, font=f_name, fill=(*text_rgb, 255))
        draw.line([(tx, height * 0.54), (tx + 220, height * 0.54)], fill=(*accent_rgb, 220), width=3)
        if role:
            draw.text((tx, height * 0.57), role, font=f_role, fill=(*muted_rgb, 235))
    else:
        # chapter_title & blooper_title
        f_eye = _load_font(22, serif=False)
        f_title = _load_font(76, serif=True)
        f_sub = _load_font(26, serif=False)
        eyebrow = str(shot.get("eyebrow") or ("OUTTAKES" if gtype == "blooper_title" else ""))
        title = str(shot.get("title") or ("Bloopers & Outtakes" if gtype == "blooper_title" else "Chapter"))
        sub_parts = [shot.get("subtitle"), shot.get("date_label"), shot.get("coordinates_label")]
        sub = "  ·  ".join(str(p) for p in sub_parts if p)

        if eyebrow:
            _center_text(height * 0.34, eyebrow.upper(), f_eye, (*accent_rgb, 245))
        _center_text(height * 0.42, title, f_title, (*text_rgb, 255))
        draw.line([(width * 0.5 - 110, height * 0.56), (width * 0.5 + 110, height * 0.56)], fill=(*accent_rgb, 220), width=2)
        if sub:
            _center_text(height * 0.60, sub, f_sub, (*muted_rgb, 230))

    card_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(card_png, format="PNG")
    return True


def _render_graphic_shot(
    shot: dict[str, Any],
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    grain_vf: str,
    style_pack: dict[str, Any],
    cards_dir: Path,
    dry_run: bool,
    prev_transition_out: str = "cut",
    shot_transition_out: str = "cut",
    shot_index: int = 0,
    is_last_shot: bool = False,
) -> list[str]:
    """Render `media_type == 'graphic'` via bespoke `rendered_card_path` or styled Pillow/FFmpeg card (video-only)."""
    dur = _shot_output_duration(shot)
    n_frames = max(2, int(round(dur * fps)))
    gtype = str(shot.get("graphic_type") or shot.get("overlay") or "chapter_title").lower()

    # Only auto-apply fade_in on shot 0 (or explicit fade_in > 0); never bake fade_out when transition_out != "cut"
    prev_t = str(prev_transition_out or "cut").lower()
    next_t = str(shot_transition_out or shot.get("transition_out") or shot.get("transition") or "cut").lower()
    fade_parts: list[str] = []
    fd = min(0.45, dur * 0.25)
    explicit_fi = shot.get("fade_in")
    explicit_fo = shot.get("fade_out")
    if explicit_fi is not None and float(explicit_fi) > 0.01:
        fade_parts.append(f"fade=t=in:st=0:d={min(float(explicit_fi), dur * 0.45):.3f}")
    elif explicit_fi is None and shot_index == 0 and prev_t == "cut":
        fade_parts.append(f"fade=t=in:st=0:d={fd:.3f}")
    if next_t == "cut":
        if explicit_fo is not None and float(explicit_fo) > 0.01:
            fo = min(float(explicit_fo), dur * 0.45)
            fade_parts.append(f"fade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}")
        elif explicit_fo is None and is_last_shot:
            fade_parts.append(f"fade=t=out:st={max(0.0, dur - fd):.3f}:d={fd:.3f}")

    # Path A: Pre-rendered Remotion or bespoke PNG/MP4 card (`rendered_card_path` works for ALL graphic_types)
    rendered_card = shot.get("rendered_card_path")
    src_raw = shot.get("source_path")
    pre_path: Path | None = None
    if rendered_card:
        cand = Path(str(rendered_card)).expanduser()
        if cand.is_file() or dry_run:
            pre_path = cand
    elif src_raw and gtype not in {"text_behind_subject", "cast_intro"}:
        cand = Path(str(src_raw)).expanduser()
        if cand.suffix.lower() in (VIDEO_CARD_EXTS | IMAGE_CARD_EXTS) and (cand.is_file() or dry_run):
            pre_path = cand

    if pre_path is not None:
        ext = pre_path.suffix.lower()
        vf_parts = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height},setsar=1,fps={fps}",
        ]
        if grain_vf:
            vf_parts.append(grain_vf)
        vf_parts.extend(fade_parts)
        vf_parts.append("format=yuv420p")
        vf = ",".join(vf_parts)

        if ext in VIDEO_CARD_EXTS:
            return [
                "ffmpeg", "-y",
                "-i", str(pre_path),
                "-vf", vf,
                "-an", "-r", str(fps), "-frames:v", str(n_frames),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
                str(out_clip),
            ]
        return [
            "ffmpeg", "-y",
            "-loop", "1", "-t", f"{dur + 0.25:.4f}", "-i", str(pre_path),
            "-vf", vf,
            "-an", "-r", str(fps), "-frames:v", str(n_frames),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
            str(out_clip),
        ]

    # Path B1: Pillow-rendered card PNG -> smooth subtle slow-zoom FFmpeg clip
    card_png = cards_dir / f"{out_clip.stem}_card.png"
    rendered_png = False
    if not dry_run:
        rendered_png = _render_card_png_pillow(shot, card_png, width, height, style_pack)

    if rendered_png:
        zoom_pct = float((style_pack.get("cards") or {}).get("backdrop_zoom_pct", 0.04))
        z_expr = f"1.0+{zoom_pct:.4f}*on/{n_frames}"
        vf_parts = [
            f"zoompan=z='{z_expr}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={n_frames}:s={width}x{height}:fps={fps}",
        ]
        if grain_vf:
            vf_parts.append(grain_vf)
        vf_parts.extend(fade_parts)
        vf_parts.append("format=yuv420p")
        return [
            "ffmpeg", "-y",
            "-i", str(card_png),
            "-vf", ",".join(vf_parts),
            "-an", "-r", str(fps), "-frames:v", str(n_frames),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
            str(out_clip),
        ]

    # Path B2: Pure-FFmpeg graphic card fallback (works in dry-run & without Pillow)
    pal = style_pack.get("palette") or DEFAULT_STYLE_PACK["palette"]
    bg_hex = _hex_for_ffmpeg(pal.get("bg", "#0F1115"))
    accent_hex = _hex_for_ffmpeg(pal.get("accent", "#D4AF37"))
    text_hex = _hex_for_ffmpeg(pal.get("text_primary", "#F5F2EB"))
    muted_hex = _hex_for_ffmpeg(pal.get("text_secondary", "#B9B2A3"))

    eyebrow = _escape_drawtext(str(shot.get("eyebrow") or gtype.replace("_", " ").upper()))
    title = _escape_drawtext(str(shot.get("title") or "Chapter"))
    sub_parts = [shot.get("subtitle"), shot.get("date_label"), shot.get("coordinates_label")]
    sub = _escape_drawtext(" · ".join(str(p) for p in sub_parts if p))

    backdrop_raw = shot.get("backdrop_path")
    use_backdrop = bool(backdrop_raw and (Path(str(backdrop_raw)).expanduser().is_file() or dry_run))
    if use_backdrop and backdrop_raw:
        in_args = ["-loop", "1", "-t", f"{dur + 0.25:.4f}", "-i", str(Path(str(backdrop_raw)).expanduser())]
        base_vf = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height},setsar=1,gblur=sigma=20,eq=brightness=-0.22",
        ]
        text_hex = "0xF7F4EE"
        muted_hex = "0xDFD9CE"
    else:
        in_args = ["-f", "lavfi", "-t", f"{dur + 0.25:.4f}", "-i", f"color=c={bg_hex}:s={width}x{height}:r={fps}"]
        base_vf = ["setsar=1"]

    draw_vf = [
        f"drawbox=x=48:y=48:w={width - 96}:h={height - 96}:color={accent_hex}@0.35:t=2",
        f"drawbox=x={(width // 2) - 110}:y={int(height * 0.56)}:w=220:h=2:color={accent_hex}@0.9:t=fill",
        f"drawtext=text='{eyebrow}':fontcolor={accent_hex}:fontsize=24:x=(w-text_w)/2:y={int(height * 0.35)}",
        f"drawtext=text='{title}':fontcolor={text_hex}:fontsize=72:x=(w-text_w)/2:y={int(height * 0.43)}",
    ]
    if sub:
        draw_vf.append(f"drawtext=text='{sub}':fontcolor={muted_hex}:fontsize=26:x=(w-text_w)/2:y={int(height * 0.60)}")

    vf_parts = [*base_vf, *draw_vf]
    if grain_vf:
        vf_parts.append(grain_vf)
    vf_parts.extend(fade_parts)
    vf_parts.append("format=yuv420p")

    return [
        "ffmpeg", "-y",
        *in_args,
        "-vf", ",".join(vf_parts),
        "-an", "-r", str(fps), "-frames:v", str(n_frames),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        str(out_clip),
    ]


def _partition_cut_aligned_segments(
    shots: list[dict[str, Any]],
    target_size: int = 12,
    max_size: int = 15,
    has_xfade: bool = True,
) -> list[tuple[int, int]]:
    """Split shot indices into ~10-15 shot cut-aligned ranges (never splitting mid-xfade)."""
    n = len(shots)
    if n <= max_size:
        return [(0, n)]

    def _is_cut_boundary(idx: int) -> bool:
        if idx <= 0 or idx >= n:
            return True
        if not has_xfade:
            return True
        raw_t = str(shots[idx - 1].get("transition_out") or shots[idx - 1].get("transition") or "cut").lower()
        return raw_t not in XFADE_TRANSITIONS

    segments: list[tuple[int, int]] = []
    start = 0
    while start < n:
        if n - start <= max_size:
            segments.append((start, n))
            break
        min_end = min(n, start + max(6, target_size - 4))
        max_end = min(n, start + max_size)
        ideal = min(n, start + target_size)
        candidates = [b for b in range(min_end, max_end + 1) if _is_cut_boundary(b)]
        if candidates:
            best = min(candidates, key=lambda b: abs(b - ideal))
        else:
            # Scan forward to the next hard cut so we never split mid-transition
            best = n
            for b in range(max_end + 1, n):
                if _is_cut_boundary(b):
                    best = b
                    break
        segments.append((start, best))
        start = best
    return segments


def _compute_shot_cache_key(
    shot: dict[str, Any],
    width: int,
    height: int,
    fps: int,
    stabilize: bool,
    style_id: str,
    look_vf: str,
    grain_vf: str,
    style_pack: dict[str, Any],
    prev_transition_out: str,
    shot_transition_out: str,
    shot_index: int,
    is_last_shot: bool,
    eff_layout: str,
) -> str:
    """Compute content-hash key from shot config, style settings, renderer version, and source mtime/size."""
    source_stats: list[dict[str, Any]] = []
    paths_to_check: list[str] = []
    for k in ("source_path", "pair_source_path", "backdrop_path", "mask_path", "rendered_card_path"):
        if shot.get(k):
            paths_to_check.append(str(shot[k]))
    for tile in shot.get("tiles") or []:
        if isinstance(tile, dict) and tile.get("source_path"):
            paths_to_check.append(str(tile["source_path"]))
    for raw_p in sorted(set(paths_to_check)):
        p = Path(raw_p).expanduser()
        if p.exists():
            st = p.stat()
            source_stats.append({"path": str(p.resolve()), "mtime_ns": st.st_mtime_ns, "size": st.st_size})
        else:
            source_stats.append({"path": raw_p, "missing": True})

    payload = {
        "renderer_version": RENDERER_VERSION,
        "shot": shot,
        "width": width,
        "height": height,
        "fps": fps,
        "stabilize": stabilize,
        "style_id": style_id,
        "look_vf": look_vf,
        "grain_vf": grain_vf,
        "layouts": style_pack.get("layouts"),
        "textures": style_pack.get("textures"),
        "cards": style_pack.get("cards"),
        "palette": style_pack.get("palette"),
        "prev_transition_out": prev_transition_out,
        "shot_transition_out": shot_transition_out,
        "is_first_shot": shot_index == 0,
        "is_last_shot": is_last_shot,
        "eff_layout": eff_layout,
        "sources": source_stats,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _build_video_timeline_cmd(
    shots: list[dict[str, Any]],
    clip_paths: list[Path],
    shot_durations: list[float],
    video_timeline: Path,
    fps: int,
    style_pack: dict[str, Any],
    avail_filters: set[str],
) -> tuple[list[str], list[float], float]:
    """Assemble frame-locked video timeline segment using probed shot durations + `xfade` offsets.

    Returns `(ffmpeg_cmd, shot_starts, total_timeline_dur)`.
    """
    if len(clip_paths) == 1:
        cmd = [
            "ffmpeg", "-y", "-i", str(clip_paths[0]),
            "-an", "-c:v", "copy", "-video_track_timescale", "90000",
            str(video_timeline),
        ]
        return cmd, [0.0], shot_durations[0]

    style_trans_dur = float((style_pack.get("transitions") or {}).get("default_duration_sec", 0.45))
    inputs: list[str] = []
    fc_parts: list[str] = []
    for idx, clip in enumerate(clip_paths):
        inputs.extend(["-i", str(clip)])
        fc_parts.append(f"[{idx}:v]fps={fps},settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[vin{idx}]")

    shot_starts: list[float] = [0.0]
    timeline_cursor = shot_durations[0]
    prev_lbl = "[vin0]"
    has_xfade = "xfade" in avail_filters

    for idx in range(1, len(clip_paths)):
        prev_shot = shots[idx - 1]
        is_last = idx == len(clip_paths) - 1
        final_lbl = "[vout]" if is_last else f"[v{idx}]"
        raw_t = str(prev_shot.get("transition_out") or prev_shot.get("transition") or "cut").lower()

        if raw_t in XFADE_TRANSITIONS and has_xfade:
            xf_name, default_d, post_fx = XFADE_TRANSITIONS[raw_t]
            explicit_td = prev_shot.get("transition_duration_sec")
            req_d = float(explicit_td) if explicit_td is not None else (
                default_d if raw_t in {"flash_cut", "whip_pan_left", "whip_pan_right", "whip_pan_up", "whip_pan_down", "zoom_punch"}
                else style_trans_dur
            )
            max_ov = min(shot_durations[idx - 1], shot_durations[idx]) * 0.45
            ov_frames = max(2, int(round(min(req_d, max_ov) * fps)))
            ov = ov_frames / float(fps)
            offset = max(0.0, timeline_cursor - ov)
            shot_starts.append(offset)
            timeline_cursor = offset + shot_durations[idx]

            needs_post = post_fx == "warm_burn"
            mid_lbl = f"[vxf{idx}]" if needs_post else final_lbl
            fc_parts.append(
                f"{prev_lbl}[vin{idx}]xfade=transition={xf_name}:duration={ov:.6f}:offset={offset:.6f}{mid_lbl}"
            )
            if post_fx == "warm_burn":
                fc_parts.append(
                    f"{mid_lbl}colorbalance=rs=0.22:gs=0.08:bs=-0.16:rh=0.24:gh=0.10:bh=-0.14:"
                    f"enable='between(t,{offset:.4f},{offset + ov:.4f})'{final_lbl}"
                )
            prev_lbl = final_lbl
        else:
            shot_starts.append(timeline_cursor)
            timeline_cursor += shot_durations[idx]
            fc_parts.append(f"{prev_lbl}[vin{idx}]concat=n=2:v=1:a=0,settb=AVTB{final_lbl}")
            prev_lbl = final_lbl

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", "[vout]",
        "-an", "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        str(video_timeline),
    ]
    return cmd, shot_starts, timeline_cursor


def _build_4track_audio_mix_cmd(
    edl: dict[str, Any],
    shots: list[dict[str, Any]],
    shot_starts: list[float],
    shot_durations: list[float],
    total_dur: float,
    video_timeline: Path,
    mixed_output: Path,
    style_pack: dict[str, Any],
    dry_run: bool,
) -> list[str]:
    """Build 4-track (`NAT_A`, `NAT_B`, `BGM`, `SFX`) `adelay` + `amix` frame-locked audio mux."""
    audio_defaults = style_pack.get("audio") or DEFAULT_STYLE_PACK["audio"]
    style_bgm_vol = float(audio_defaults.get("default_bgm_volume", 0.78))
    style_duck_db = float(audio_defaults.get("duck_bgm_db", -13.0))
    style_nat_bed = float(audio_defaults.get("default_nat_bed_volume", 0.24))
    style_sp_gain_db = float(audio_defaults.get("sonic_postcard_nat_gain_db", 1.0))
    style_j_def = float(audio_defaults.get("default_j_cut_sec", 0.6))
    style_l_def = float(audio_defaults.get("default_l_cut_sec", 0.5))

    # Input 0: rendered video timeline; Input 1: exact-duration silent anchor bed
    inputs: list[str] = [
        "-i", str(video_timeline),
        "-f", "lavfi", "-t", f"{total_dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
    ]
    next_in = 2
    fc_parts: list[str] = []

    nat_a_labels: list[str] = []
    nat_b_labels: list[str] = []
    sfx_labels: list[str] = []
    sonic_windows: list[tuple[float, float, float]] = []  # (start_sec, end_sec, duck_db)

    sfx_map = edl.get("sfx_map") if isinstance(edl.get("sfx_map"), dict) else {}

    for idx, shot in enumerate(shots):
        acfg = shot.get("audio") if isinstance(shot.get("audio"), dict) else {}
        is_muted = bool(acfg.get("mute", False))
        is_sp = bool(acfg.get("sonic_postcard", False))
        sp_cfg = shot.get("speed_profile") if isinstance(shot.get("speed_profile"), dict) else None
        sp_mode = str(sp_cfg.get("mode") or sp_cfg.get("type") or "").lower() if sp_cfg else ""
        sp_rate = float(sp_cfg.get("rate", 1.0)) if (sp_cfg and sp_mode == "constant") else 1.0

        # Blueprint rule: never warp audio on cubic ramps or speeds > 1.3x
        if sp_mode == "cubic_ramp" or sp_rate > 1.3:
            is_muted = True

        # Optional SFX cue for this shot (placed at shot start + optional offset)
        sfx_raw = acfg.get("sfx_path") or shot.get("sfx_path")
        if not sfx_raw and acfg.get("sfx_id") and sfx_map:
            sfx_raw = sfx_map.get(acfg["sfx_id"])
        if sfx_raw:
            sfx_p = Path(str(sfx_raw)).expanduser()
            if sfx_p.is_file() or dry_run:
                sfx_in_idx = next_in
                next_in += 1
                inputs.extend(["-i", str(sfx_p)])
                sfx_t = max(0.0, shot_starts[idx] + float(acfg.get("sfx_offset_sec", 0.0)))
                delay_ms = int(round(sfx_t * 1000.0))
                lbl = f"[sfx{idx}]"
                delay_part = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
                fc_parts.append(
                    f"[{sfx_in_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                    f"volume=0.85{delay_part}{lbl}"
                )
                sfx_labels.append(lbl)

        # Resolve natural diegetic audio source (single video or audio_lead tile)
        nat_src: Path | None = None
        src_in = float(shot.get("in_sec", 0.0))
        src_dur = float(shot["source_duration_sec"]) if shot.get("source_duration_sec") is not None else None
        mtype = str(shot.get("media_type", "photo")).lower()

        if not is_muted:
            tiles = shot.get("tiles")
            if isinstance(tiles, list) and len(tiles) >= 2:
                lead_idx = shot.get("audio_lead_tile")
                chosen_tile = None
                if isinstance(lead_idx, int):
                    if 0 <= lead_idx < len(tiles):
                        chosen_tile = tiles[lead_idx]
                else:
                    chosen_tile = next((t for t in tiles if t.get("audio_lead")), None)
                if chosen_tile and str(chosen_tile.get("media_type", "")).lower() == "video":
                    nat_src = Path(str(chosen_tile["source_path"])).expanduser()
                    src_in = float(chosen_tile.get("in_sec", 0.0))
                    if chosen_tile.get("source_duration_sec") is not None:
                        src_dur = float(chosen_tile["source_duration_sec"])
            elif mtype == "video" and shot.get("source_path"):
                nat_src = Path(str(shot["source_path"])).expanduser()

        if nat_src is not None:
            info = _probe_video_info(nat_src, dry_run=dry_run)
            if src_dur is None and info.get("duration_sec") is not None:
                src_dur = float(info["duration_sec"])
            if info["has_audio"]:
                raw_trans = str(shot.get("transition") or "").lower()
                j_req = float(acfg.get("j_cut_sec") or (style_j_def if raw_trans == "j_cut" else 0.0))
                l_req = float(acfg.get("l_cut_sec") or (style_l_def if raw_trans == "l_cut" else 0.0))

                # Clamp J/L-cut handles to available source and timeline bounds
                actual_j = max(0.0, min(j_req, src_in / max(0.1, sp_rate), shot_starts[idx]))
                src_out_est = src_in + shot_durations[idx] * sp_rate
                max_l_src = max(0.0, (src_dur - src_out_est) / max(0.1, sp_rate)) if src_dur is not None else l_req
                max_l_tl = max(0.0, total_dur - (shot_starts[idx] + shot_durations[idx]))
                actual_l = max(0.0, min(l_req, max_l_src, max_l_tl))

                stem_out_dur = shot_durations[idx] + actual_j + actual_l
                stem_src_start = max(0.0, src_in - actual_j * sp_rate)
                stem_src_dur = stem_out_dur * sp_rate
                timeline_start = max(0.0, shot_starts[idx] - actual_j)
                delay_ms = int(round(timeline_start * 1000.0))

                nat_in_idx = next_in
                next_in += 1
                inputs.extend(["-ss", f"{stem_src_start:.4f}", "-t", f"{stem_src_dur:.4f}", "-i", str(nat_src)])

                if acfg.get("volume") is not None:
                    nat_vol = float(acfg["volume"])
                elif is_sp:
                    nat_vol = 10.0 ** (style_sp_gain_db / 20.0)
                else:
                    nat_vol = style_nat_bed

                fade_d = min(0.35 if (actual_j > 0 or actual_l > 0) else 0.12, stem_out_dur * 0.25)
                chain_parts = [f"[{nat_in_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo"]
                if abs(sp_rate - 1.0) > 1e-3:
                    atempo_str = _atempo_chain(sp_rate)
                    if atempo_str:
                        chain_parts.append(atempo_str)
                chain_parts.append(f"atrim=0:{stem_out_dur:.4f},asetpts=PTS-STARTPTS")
                chain_parts.append(f"volume={nat_vol:.4f}")
                if fade_d > 0.01:
                    chain_parts.append(f"afade=t=in:st=0:d={fade_d:.3f}")
                    chain_parts.append(f"afade=t=out:st={max(0.0, stem_out_dur - fade_d):.3f}:d={fade_d:.3f}")
                if delay_ms > 0:
                    chain_parts.append(f"adelay={delay_ms}:all=1")

                lbl = f"[nat{idx}]"
                fc_parts.append(",".join(chain_parts) + lbl)
                if idx % 2 == 0:
                    nat_a_labels.append(lbl)
                else:
                    nat_b_labels.append(lbl)

                if is_sp:
                    duck_db = float(acfg.get("duck_db", style_duck_db))
                    sonic_windows.append((timeline_start, timeline_start + stem_out_dur, -abs(duck_db)))
        elif is_sp:
            # Even if natural audio probe was unavailable, honor sonic_postcard duck window
            duck_db = float(acfg.get("duck_db", style_duck_db))
            sonic_windows.append((shot_starts[idx], shot_starts[idx] + shot_durations[idx], -abs(duck_db)))

    bus_labels: list[str] = ["[1:a]"]  # exact-duration silent anchor bed

    if nat_a_labels:
        if len(nat_a_labels) == 1:
            fc_parts.append(f"{nat_a_labels[0]}anull[bus_nat_a]")
        else:
            fc_parts.append(
                f"{''.join(nat_a_labels)}amix=inputs={len(nat_a_labels)}:duration=longest:normalize=0[bus_nat_a]"
            )
        bus_labels.append("[bus_nat_a]")

    if nat_b_labels:
        if len(nat_b_labels) == 1:
            fc_parts.append(f"{nat_b_labels[0]}anull[bus_nat_b]")
        else:
            fc_parts.append(
                f"{''.join(nat_b_labels)}amix=inputs={len(nat_b_labels)}:duration=longest:normalize=0[bus_nat_b]"
            )
        bus_labels.append("[bus_nat_b]")

    if sfx_labels:
        if len(sfx_labels) == 1:
            fc_parts.append(f"{sfx_labels[0]}anull[bus_sfx]")
        else:
            fc_parts.append(
                f"{''.join(sfx_labels)}amix=inputs={len(sfx_labels)}:duration=longest:normalize=0[bus_sfx]"
            )
        bus_labels.append("[bus_sfx]")

    # Track 3: BGM with smooth sonic_postcard ducking envelopes + end fade-out
    bgm_cfg = edl.get("bgm") if isinstance(edl.get("bgm"), dict) else {}
    bgm_raw = edl.get("bgm_path") or bgm_cfg.get("audio_path")
    if bgm_raw:
        bgm_p = Path(str(bgm_raw)).expanduser()
        if bgm_p.is_file() or dry_run:
            bgm_idx = next_in
            next_in += 1
            inputs.extend(["-stream_loop", "-1", "-i", str(bgm_p)])
            base_bgm_vol = float(bgm_cfg.get("volume") if bgm_cfg.get("volume") is not None else style_bgm_vol)
            bgm_chain = [
                f"[{bgm_idx}:a]atrim=0:{total_dur:.4f},asetpts=PTS-STARTPTS",
                "aformat=sample_rates=48000:channel_layouts=stereo",
                f"volume={base_bgm_vol:.4f}",
            ]
            ramp = 0.40
            for win_s, win_e, duck_db in sonic_windows:
                duck_lin = 10.0 ** (duck_db / 20.0)
                t0 = win_s - ramp
                t1 = win_e + ramp
                s_expr = f"t+{-t0:.4f}" if t0 < 0 else f"t-{t0:.4f}"
                bgm_chain.append(
                    f"volume='1-(1-{duck_lin:.4f})"
                    f"*min(1,max(0,({s_expr})/{ramp:.2f}))"
                    f"*min(1,max(0,({t1:.4f}-t)/{ramp:.2f}))':eval=frame"
                )
            end_fade = min(2.0, total_dur * 0.25)
            if end_fade > 0.1:
                bgm_chain.append(f"afade=t=out:st={max(0.0, total_dur - end_fade):.4f}:d={end_fade:.4f}")
            fc_parts.append(",".join(bgm_chain) + "[bus_bgm]")
            bus_labels.append("[bus_bgm]")

    if len(bus_labels) == 1:
        fc_parts.append("[1:a]anull[aout]")
    else:
        fc_parts.append(
            f"{''.join(bus_labels)}amix=inputs={len(bus_labels)}:duration=first:normalize=0,"
            f"atrim=0:{total_dur:.4f},asetpts=PTS-STARTPTS[aout]"
        )

    return [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "256k",
        str(mixed_output),
    ]


def _two_pass_loudnorm(input_av: Path, output_master: Path, dry_run: bool) -> list[list[str]]:
    """Apply two-pass EBU R128 mastering: -14 LUFS, LRA=9, TP=-1.0, linear=true."""
    pass1_cmd = [
        "ffmpeg", "-y", "-i", str(input_av),
        "-af", "loudnorm=I=-14:LRA=9:TP=-1.0:print_format=json",
        "-f", "null", "-",
    ]
    if dry_run:
        pass2_placeholder = [
            "ffmpeg", "-y", "-i", str(input_av),
            "-c:v", "copy",
            "-af", "loudnorm=I=-14:LRA=9:TP=-1.0:linear=true",
            "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart",
            str(output_master),
        ]
        return [pass1_cmd, pass2_placeholder]

    p1 = _run(pass1_cmd, timeout=300)
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p1.stderr or "", re.DOTALL)
    use_copy_silent = False
    af = "loudnorm=I=-14:LRA=9:TP=-1.0:linear=true"
    if match:
        stats = json.loads(match.group(0))
        try:
            in_i = float(stats["input_i"])
            in_lra = float(stats["input_lra"])
            in_tp = float(stats["input_tp"])
            in_th = float(stats["input_thresh"])
            off = float(stats["target_offset"])
            if all(math.isfinite(v) for v in (in_i, in_lra, in_tp, in_th, off)) and in_i > -70.0:
                af = (
                    f"loudnorm=I=-14:LRA=9:TP=-1.0:"
                    f"measured_I={in_i:.2f}:measured_LRA={in_lra:.2f}:"
                    f"measured_TP={in_tp:.2f}:measured_thresh={in_th:.2f}:"
                    f"offset={off:.2f}:linear=true"
                )
            else:
                use_copy_silent = True
        except (KeyError, TypeError, ValueError):
            use_copy_silent = True

    if use_copy_silent:
        pass2_cmd = [
            "ffmpeg", "-y", "-i", str(input_av),
            "-c", "copy", "-movflags", "+faststart",
            str(output_master),
        ]
    else:
        pass2_cmd = [
            "ffmpeg", "-y", "-i", str(input_av),
            "-c:v", "copy", "-af", af,
            "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart",
            str(output_master),
        ]
    p2 = _run(pass2_cmd, timeout=300)
    if p2.returncode != 0:
        raise RuntimeError(f"Loudnorm pass 2 failed:\n{p2.stderr}")
    return [pass1_cmd, pass2_cmd]


def _qa_format_ts(sec: float) -> str:
    mins = int(sec // 60)
    rem = sec - mins * 60
    return f"{mins:02d}:{rem:04.1f}s"


def _qa_compute_edl_timeline(
    edl: dict[str, Any],
    master_dur: float,
    render_report: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """Compute per-shot timeline start/duration/overlap records from render_report or EDL."""
    shots = edl.get("shots") or []
    if not shots:
        return [], 0.0
    style_pack = _load_style_pack(edl)
    style_trans_dur = float((style_pack.get("transitions") or {}).get("default_duration_sec", 0.45))
    fps = int(round(float(edl.get("fps") or 30)))

    rep_starts = (render_report or {}).get("shot_starts_sec")
    rep_durs = (render_report or {}).get("probed_durations")
    use_rep = (
        isinstance(rep_starts, list)
        and len(rep_starts) == len(shots)
        and isinstance(rep_durs, list)
        and len(rep_durs) == len(shots)
    )

    if use_rep:
        starts = [float(x) for x in rep_starts]
        durs = [float(x) for x in rep_durs]
    else:
        durs = [max(2, int(round(_shot_output_duration(s) * fps))) / float(fps) for s in shots]
        starts = [0.0]
        cursor = durs[0]
        for idx in range(1, len(shots)):
            prev_shot = shots[idx - 1]
            raw_t = str(prev_shot.get("transition_out") or prev_shot.get("transition") or "cut").lower()
            if raw_t in XFADE_TRANSITIONS:
                _, default_d, _ = XFADE_TRANSITIONS[raw_t]
                explicit_td = prev_shot.get("transition_duration_sec")
                req_d = float(explicit_td) if explicit_td is not None else (
                    default_d if raw_t in {"flash_cut", "whip_pan_left", "whip_pan_right", "whip_pan_up", "whip_pan_down", "zoom_punch"}
                    else style_trans_dur
                )
                max_ov = min(durs[idx - 1], durs[idx]) * 0.45
                ov = max(2, int(round(min(req_d, max_ov) * fps))) / float(fps)
                offset = max(0.0, cursor - ov)
                starts.append(offset)
                cursor = offset + durs[idx]
            else:
                starts.append(cursor)
                cursor += durs[idx]

    records: list[dict[str, Any]] = []
    for idx, shot in enumerate(shots):
        raw_t = str(shot.get("transition_out") or shot.get("transition") or "cut").lower()
        ov = 0.0
        if idx < len(shots) - 1 and raw_t in XFADE_TRANSITIONS:
            ov = max(0.0, (starts[idx] + durs[idx]) - starts[idx + 1])
        records.append(
            {
                "index": idx,
                "shot": shot,
                "shot_id": str(shot.get("shot_id") or f"S{idx + 1:02d}"),
                "start_sec": starts[idx],
                "dur_sec": durs[idx],
                "transition_out": raw_t if idx < len(shots) - 1 else "cut",
                "overlap_sec": ov,
            }
        )
    expected_dur = starts[-1] + durs[-1]
    return records, expected_dur


def _qa_extract_frame_jpg(master_path: Path, t_sec: float, out_jpg: Path, cell_w: int, cell_h: int) -> bool:
    proc = _run(
        [
            "ffmpeg", "-y", "-ss", f"{max(0.0, t_sec):.3f}", "-i", str(master_path),
            "-frames:v", "1",
            "-vf", f"scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:color=0x101218",
            "-q:v", "3",
            str(out_jpg),
        ],
        timeout=30,
    )
    return proc.returncode == 0 and out_jpg.is_file()


def _qa_render_contact_sheet(
    master_path: Path,
    items: list[tuple[float, str]],
    out_path: Path,
    cols: int,
    cell_w: int,
    cell_h: int,
) -> str | None:
    """Extract frames at target timestamps and render a labeled contact sheet JPEG."""
    if not items:
        return None
    with tempfile.TemporaryDirectory(prefix="trip_qa_") as tmp_s:
        tmp_dir = Path(tmp_s)
        frame_paths: list[tuple[Path | None, str]] = [None] * len(items)  # type: ignore[list-item]

        def _grab(i: int, ts: float, lbl: str) -> tuple[int, Path | None, str]:
            fp = tmp_dir / f"f_{i:04d}.jpg"
            ok = _qa_extract_frame_jpg(master_path, ts, fp, cell_w, cell_h)
            return i, (fp if ok else None), lbl

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(items)))) as pool:
            futs = [pool.submit(_grab, i, ts, lbl) for i, (ts, lbl) in enumerate(items)]
            for fut in as_completed(futs):
                i, fp, lbl = fut.result()
                frame_paths[i] = (fp, lbl)

        n_cols = max(1, min(cols, len(items)))
        n_rows = max(1, math.ceil(len(items) / n_cols))

        try:
            from PIL import Image, ImageDraw, ImageFont  # type: ignore

            canvas = Image.new("RGB", (n_cols * cell_w, n_rows * cell_h), (16, 18, 24))
            draw = ImageDraw.Draw(canvas)
            font: Any = ImageFont.load_default()
            for cand in (
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Menlo.ttc",
            ):
                if Path(cand).is_file():
                    try:
                        font = ImageFont.truetype(cand, 16 if cell_w >= 800 else 13)
                        break
                    except OSError:
                        pass

            for idx, (fp, lbl) in enumerate(frame_paths):
                row, col = divmod(idx, n_cols)
                x0, y0 = col * cell_w, row * cell_h
                draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h - 1], fill=(22, 24, 30), outline=(42, 46, 56))
                if fp is not None and fp.is_file():
                    try:
                        with Image.open(fp) as im:
                            im = im.convert("RGB")
                            im.thumbnail((cell_w - 8, cell_h - 8))
                            px = x0 + (cell_w - im.width) // 2
                            py = y0 + (cell_h - im.height) // 2
                            canvas.paste(im, (px, py))
                    except Exception:  # noqa: BLE001
                        pass
                bbox = draw.textbbox((0, 0), lbl, font=font)
                tw = min(cell_w - 24, (bbox[2] - bbox[0]) + 16)
                th = (bbox[3] - bbox[1]) + 10
                draw.rectangle([x0 + 8, y0 + 8, x0 + 8 + tw, y0 + 8 + th], fill=(10, 12, 16))
                draw.text((x0 + 16, y0 + 12), lbl, font=font, fill=(246, 244, 238))

            out_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(out_path, quality=90)
            return str(out_path)
        except ImportError:
            valid = [fp for fp, _ in frame_paths if fp is not None and fp.is_file()]
            if not valid:
                return None
            inputs: list[str] = []
            for fp in valid:
                inputs.extend(["-i", str(fp)])
            fc = "".join(f"[{i}:v]" for i in range(len(valid))) + f"concat=n={len(valid)}:v=1:a=0,tile={n_cols}x{n_rows}[out]"
            proc = _run(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-q:v", "3", str(out_path)], timeout=60)
            return str(out_path) if (proc.returncode == 0 and out_path.is_file()) else None


def _qa_measure_signalstats(master_path: Path, sample_sec: float) -> dict[str, float]:
    """Run FFmpeg signalstats on a single frame and return YAVG/YMIN/YMAX/SATAVG/YLOW/YHIGH."""
    proc = _run(
        [
            "ffmpeg", "-y", "-ss", f"{max(0.0, sample_sec):.3f}", "-i", str(master_path),
            "-frames:v", "1",
            "-vf", "signalstats,metadata=mode=print:file=-",
            "-f", "null", "-",
        ],
        timeout=30,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    metrics: dict[str, float] = {}
    for key, val in re.findall(r"lavfi\.signalstats\.(YMIN|YLOW|YAVG|YHIGH|YMAX|SATAVG)=([0-9]+(?:\.[0-9]+)?)", combined):
        metrics[key] = round(float(val), 2)
    return metrics


def extract_qa_artifacts(
    master_path: Path,
    qa_dir: Path,
    edl: dict[str, Any] | None = None,
    render_report: dict[str, Any] | None = None,
    chunk_sec: int = 60,
) -> dict[str, Any]:
    """Extract EDL-aware QA sheets, transition strips, collage sheet, signalstats metrics, and <=60s chunks."""
    qa_dir.mkdir(parents=True, exist_ok=True)
    info = _probe_video_info(master_path, dry_run=False)
    dur = float(info["duration_sec"]) if info.get("duration_sec") else 60.0
    is_vertical = bool(info.get("width") and info.get("height") and info["height"] > info["width"])
    chunk_scale = "540:960" if is_vertical else "960:540"
    cell_w, cell_h = (360, 640) if is_vertical else (640, 360)

    end_frame = qa_dir / "end_frame_check.jpg"
    _run(
        ["ffmpeg", "-y", "-sseof", "-0.05", "-i", str(master_path), "-frames:v", "1", "-q:v", "2", str(end_frame)],
        timeout=30,
    )
    if not end_frame.is_file() or end_frame.stat().st_size == 0:
        _run(
            ["ffmpeg", "-y", "-ss", f"{max(0.0, dur - 0.08):.3f}", "-i", str(master_path), "-frames:v", "1", "-q:v", "2", str(end_frame)],
            timeout=30,
        )

    n_chunks = max(1, math.ceil(dur / chunk_sec))
    chunks: list[str] = []
    for idx in range(n_chunks):
        start = idx * chunk_sec
        chunk_out = qa_dir / f"qa_chunk_{idx + 1:02d}.mp4"
        _run(
            [
                "ffmpeg", "-y", "-ss", str(start), "-t", str(chunk_sec),
                "-i", str(master_path),
                "-vf", f"scale={chunk_scale}", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                str(chunk_out),
            ],
            timeout=120,
        )
        if chunk_out.exists():
            chunks.append(str(chunk_out))

    # Overview 9-frame contact sheet (`qa_sheet_01.jpg`, ending at -0.05s)
    overview_items: list[tuple[float, str]] = []
    for k in range(9):
        t_s = max(0.0, dur - 0.05) if k == 8 else min(max(0.0, dur - 0.05), dur * (0.04 + 0.90 * k / 8.0))
        overview_items.append((t_s, f"#{k + 1:02d} | {_qa_format_ts(t_s)}"))
    qa_sheet_path = _qa_render_contact_sheet(master_path, overview_items, qa_dir / "qa_sheet_01.jpg", cols=3, cell_w=cell_w, cell_h=cell_h)

    video_alignment_path: str | None = None
    chapter_cards_path: str | None = None
    collages_path: str | None = None
    transitions_path: str | None = None
    metrics_path: str | None = None

    if edl:
        records, expected_dur = _qa_compute_edl_timeline(edl, dur, render_report=render_report)
        drift_ms = round((dur - expected_dur) * 1000.0, 1)

        video_items: list[tuple[float, str]] = []
        graphic_items: list[tuple[float, str]] = []
        collage_items: list[tuple[float, str]] = []
        transition_items: list[tuple[float, str]] = []
        telemetry_targets: list[tuple[dict[str, Any], float]] = []

        for rec in records:
            shot = rec["shot"]
            sid = rec["shot_id"]
            mtype = str(shot.get("media_type", "photo")).lower()
            layout = str(shot.get("layout") or shot.get("aspect_handling") or "single").lower()
            tiles = shot.get("tiles")
            is_multi = (layout in MULTI_TILE_LAYOUTS) or (isinstance(tiles, list) and len(tiles) >= 2) or bool(shot.get("pair_source_path"))
            has_video_tile = isinstance(tiles, list) and any(str(t.get("media_type", "")).lower() == "video" for t in tiles)

            gtype = str(shot.get("graphic_type") or shot.get("overlay") or "card").lower()
            frac = 0.86 if (mtype == "graphic" and gtype == "route_map") else 0.50
            sample_t = min(max(0.0, dur - 0.05), rec["start_sec"] + rec["dur_sec"] * frac)
            telemetry_targets.append((rec, sample_t))

            if mtype == "video" or has_video_tile or is_multi:
                tag = layout if layout != "single" else str(shot.get("orientation") or "video")
                if isinstance(shot.get("speed_profile"), dict):
                    tag += " ramp"
                video_items.append((sample_t, f"{sid} | {tag} | {_qa_format_ts(sample_t)}"))

            if mtype == "graphic":
                graphic_items.append((sample_t, f"{sid} | {gtype} | {_qa_format_ts(sample_t)}"))

            if is_multi:
                n_t = len(tiles) if isinstance(tiles, list) else 2
                collage_items.append((sample_t, f"{sid} | {layout} ({n_t}t) | {_qa_format_ts(sample_t)}"))

            if rec["transition_out"] != "cut" and rec["overlap_sec"] > 0 and rec["index"] < len(records) - 1:
                nxt = records[rec["index"] + 1]
                ov = rec["overlap_sec"]
                offset = nxt["start_sec"]
                t_pre = max(0.0, offset - 0.05)
                t_mid = min(max(0.0, dur - 0.05), offset + ov * 0.5)
                t_post = min(max(0.0, dur - 0.05), offset + ov + 0.05)
                pair_lbl = f"{sid}->{nxt['shot_id']} {rec['transition_out']}"
                transition_items.append((t_pre, f"{pair_lbl} PRE ({t_pre:.2f}s)"))
                transition_items.append((t_mid, f"{pair_lbl} MID ({t_mid:.2f}s)"))
                transition_items.append((t_post, f"{pair_lbl} POST ({t_post:.2f}s)"))

        if video_items:
            video_alignment_path = _qa_render_contact_sheet(
                master_path, video_items, qa_dir / "qa_video_alignment.jpg", cols=3, cell_w=cell_w, cell_h=cell_h
            )
        if graphic_items:
            chapter_cards_path = _qa_render_contact_sheet(
                master_path, graphic_items, qa_dir / "qa_chapter_cards.jpg", cols=3, cell_w=cell_w, cell_h=cell_h
            )
        if collage_items:
            collage_w, collage_h = (540, 960) if is_vertical else (960, 540)
            collages_path = _qa_render_contact_sheet(
                master_path, collage_items, qa_dir / "qa_collages.jpg", cols=2, cell_w=collage_w, cell_h=collage_h
            )
        if transition_items:
            transitions_path = _qa_render_contact_sheet(
                master_path, transition_items, qa_dir / "qa_transitions.jpg", cols=3, cell_w=cell_w, cell_h=cell_h
            )

        # Signalstats telemetry per shot (parallelized for speed)
        shot_stats_out: list[dict[str, Any]] = [{} for _ in telemetry_targets]

        def _measure_one(pos: int, rec: dict[str, Any], ts: float) -> tuple[int, dict[str, Any]]:
            m = _qa_measure_signalstats(master_path, ts)
            sh = rec["shot"]
            return pos, {
                "shot_id": rec["shot_id"],
                "media_type": str(sh.get("media_type", "photo")).lower(),
                "layout": str(sh.get("layout") or sh.get("aspect_handling") or "single").lower(),
                "sample_sec": round(ts, 3),
                "YAVG": m.get("YAVG"),
                "YMIN": m.get("YMIN"),
                "YMAX": m.get("YMAX"),
                "SATAVG": m.get("SATAVG"),
                "flags": [],
            }

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(telemetry_targets)))) as pool:
            futs = [pool.submit(_measure_one, pos, rec, ts) for pos, (rec, ts) in enumerate(telemetry_targets)]
            for fut in as_completed(futs):
                pos, row = fut.result()
                shot_stats_out[pos] = row

        # Compute SDR photo baseline for detecting lifted/washed HDR clips
        photo_ymins = [r["YMIN"] for r in shot_stats_out if r.get("media_type") == "photo" and r.get("YMIN") is not None]
        photo_yavgs = [r["YAVG"] for r in shot_stats_out if r.get("media_type") == "photo" and r.get("YAVG") is not None]
        photo_sats = [r["SATAVG"] for r in shot_stats_out if r.get("media_type") == "photo" and r.get("SATAVG") is not None]
        baseline = {
            "photo_count": len(photo_yavgs),
            "median_YMIN": round(statistics.median(photo_ymins), 2) if photo_ymins else None,
            "median_YAVG": round(statistics.median(photo_yavgs), 2) if photo_yavgs else None,
            "median_SATAVG": round(statistics.median(photo_sats), 2) if photo_sats else None,
        }

        flagged_shots: list[dict[str, Any]] = []
        for row in shot_stats_out:
            ymin = row.get("YMIN")
            yavg = row.get("YAVG")
            ymax = row.get("YMAX")
            sat = row.get("SATAVG")
            flags: list[str] = []
            if ymin is not None and ymin >= 34.0 and row.get("media_type") != "graphic":
                flags.append("lifted_blacks")
            if (
                row.get("media_type") == "video"
                and baseline["median_YMIN"] is not None
                and ymin is not None
                and (ymin - baseline["median_YMIN"]) >= 18.0
                and (
                    (baseline["median_YAVG"] is not None and yavg is not None and (yavg - baseline["median_YAVG"]) >= 20.0)
                    or (baseline["median_SATAVG"] is not None and sat is not None and sat < max(12.0, baseline["median_SATAVG"] * 0.55))
                )
            ):
                flags.append("washed_out_vs_sdr_photos")
            if yavg is not None and ymin is not None and yavg >= 195.0 and ymin >= 40.0 and row.get("media_type") != "graphic":
                flags.append("overexposed_highlights")
            if yavg is not None and ymax is not None and yavg <= 20.0 and ymax <= 35.0:
                flags.append("near_black_frame")
            row["flags"] = flags
            if flags:
                flagged_shots.append({"shot_id": row["shot_id"], "sample_sec": row["sample_sec"], "flags": flags})

        metrics_payload = {
            "master": str(master_path),
            "probed_duration_sec": round(dur, 3),
            "expected_duration_sec": round(expected_dur, 3),
            "drift_ms": drift_ms,
            "drift_flag": abs(drift_ms) > 250.0,
            "sdr_photo_baseline": baseline,
            "shots": shot_stats_out,
            "flagged_shots": flagged_shots,
            "summary": {
                "shots_analyzed": len(shot_stats_out),
                "flagged_count": len(flagged_shots),
                "video_alignment_count": len(video_items),
                "chapter_card_count": len(graphic_items),
                "collage_count": len(collage_items),
                "transition_count": len(transition_items) // 3,
            },
        }
        mp = qa_dir / "qa_metrics.json"
        mp.write_text(json.dumps(metrics_payload, indent=2) + "\n", encoding="utf-8")
        metrics_path = str(mp)

    return {
        "master": str(master_path),
        "duration_sec": round(dur, 2),
        "end_frame_check": str(end_frame) if end_frame.exists() else None,
        "qa_sheets": [qa_sheet_path] if qa_sheet_path else [],
        "qa_video_alignment": video_alignment_path,
        "qa_chapter_cards": chapter_cards_path,
        "qa_collages": collages_path,
        "qa_transitions": transitions_path,
        "qa_metrics": metrics_path,
        "qa_chunks": chunks,
    }


def render_trip(
    edl: dict[str, Any],
    output_master: Path,
    work_dir: Path,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    stabilize: bool = False,
    dry_run: bool = False,
    workers: int = 1,
    reuse_cache: bool = False,
) -> dict[str, Any]:
    """Render all EDL shots (parallel + cacheable), assemble cut-aligned xfade segments, mix 4-track audio, and master."""
    clips_dir = work_dir / "rendered_shots"
    cards_dir = work_dir / "graphic_cards"
    clips_dir.mkdir(parents=True, exist_ok=True)

    avail_filters = (
        _probe_filters()
        if not dry_run
        else {"zscale", "tonemap", "signalstats", "vidstabdetect", "vidstabtransform", "acrossfade", "loudnorm", "xfade", "boxblur", "adelay", "amix"}
    )

    style_pack = _load_style_pack(edl)
    style_id = str(style_pack.get("style_id") or edl.get("style_id") or "cinema_doc")
    look_id, look_vf, grain_vf = _resolve_grade_and_grain(edl, style_pack)

    raw_shots = edl.get("shots", [])
    if not raw_shots:
        raise ValueError("EDL contains no shots.")

    # Ensure even output dimensions
    width = width & ~1
    height = height & ~1
    workers = max(1, int(workers or 1))

    planned_cmds: list[list[str]] = []
    clip_paths: list[Path] = []
    shots: list[dict[str, Any]] = []
    target_durations: list[float] = []

    default_portrait_layout = str(
        edl.get("vertical_layout")
        or (style_pack.get("layouts") or {}).get("default_portrait_layout")
        or "blurred_bg_pillarbox"
    )

    for raw_shot in raw_shots:
        raw_dur = _shot_output_duration(raw_shot)
        shot_dur = max(2, int(round(raw_dur * fps))) / float(fps)
        s_copy = dict(raw_shot)
        s_copy["duration_sec"] = shot_dur
        shots.append(s_copy)
        target_durations.append(shot_dur)

    # Plan every shot and check content-hash cache
    shot_jobs: list[dict[str, Any]] = []
    cache_hits = 0
    cache_misses = 0

    for idx, shot in enumerate(shots):
        clip_out = clips_dir / f"shot_{idx + 1:03d}.mp4"
        hash_file = clips_dir / f"shot_{idx + 1:03d}.sha256"
        clip_paths.append(clip_out)

        mtype = str(shot.get("media_type", "photo")).lower()
        orient = str(shot.get("orientation", "landscape")).lower()
        layout = str(shot.get("layout") or shot.get("aspect_handling") or "single").lower()
        has_tiles = isinstance(shot.get("tiles"), list) and len(shot["tiles"]) >= 2
        prev_trans_out = (
            str(shots[idx - 1].get("transition_out") or shots[idx - 1].get("transition") or "cut").lower()
            if idx > 0
            else "cut"
        )
        shot_trans_out = str(shot.get("transition_out") or shot.get("transition") or "cut").lower()
        use_stab = stabilize or bool(shot.get("stabilize", False))

        # Determine effective layout consistently for both cache key & dispatch
        if mtype == "graphic":
            eff_layout = "graphic"
        elif layout in {"split_2up_video", "triptych_3up_video", "bento_living_grid", "polaroid_stack"} or (
            layout == "split_diptych_pair" and (has_tiles or shot.get("pair_source_path"))
        ):
            eff_layout = layout
        elif layout == "super8_frame":
            eff_layout = "super8_frame"
        elif orient == "portrait" and width > height:
            eff_layout = layout if layout != "single" else default_portrait_layout
            if eff_layout == "split_diptych_pair" and not (has_tiles or shot.get("pair_source_path")):
                eff_layout = "blurred_bg_pillarbox"
        else:
            eff_layout = layout

        cache_key = _compute_shot_cache_key(
            shot=shot,
            width=width,
            height=height,
            fps=fps,
            stabilize=use_stab,
            style_id=style_id,
            look_vf=look_vf,
            grain_vf=grain_vf,
            style_pack=style_pack,
            prev_transition_out=prev_trans_out,
            shot_transition_out=shot_trans_out,
            shot_index=idx,
            is_last_shot=(idx == len(shots) - 1),
            eff_layout=eff_layout,
        )

        if (
            reuse_cache
            and not dry_run
            and clip_out.is_file()
            and clip_out.stat().st_size > 0
            and hash_file.is_file()
            and hash_file.read_text(encoding="utf-8").strip() == cache_key
        ):
            cache_hits += 1
            continue

        cache_misses += 1

        if mtype == "graphic":
            cmds = [
                _render_graphic_shot(
                    shot, clip_out, width, height, fps, grain_vf, style_pack, cards_dir, dry_run,
                    prev_transition_out=prev_trans_out,
                    shot_transition_out=shot_trans_out,
                    shot_index=idx,
                    is_last_shot=(idx == len(shots) - 1),
                )
            ]
        elif eff_layout in {"split_2up_video", "triptych_3up_video", "bento_living_grid", "polaroid_stack", "split_diptych_pair"}:
            cmds = [
                _render_multi_tile_shot(
                    shot, eff_layout, clip_out, width, height, fps, look_vf, grain_vf, style_pack, avail_filters, dry_run
                )
            ]
        elif mtype == "video":
            cmds = _render_video_shot(
                shot, clip_out, width, height, fps, look_vf, grain_vf, style_pack,
                avail_filters, use_stab, clips_dir, dry_run, layout=eff_layout,
            )
        elif eff_layout in {"super8_frame", "blurred_bg_pillarbox"}:
            cmds = [
                _render_portrait_or_framed_shot(
                    shot, eff_layout, clip_out, width, height, fps, look_vf, grain_vf, style_pack, avail_filters, dry_run
                )
            ]
        else:
            cmds = [_render_ken_burns_shot(shot, clip_out, width, height, fps, look_vf, grain_vf, style_pack)]

        planned_cmds.extend(cmds)
        shot_jobs.append(
            {
                "idx": idx,
                "shot_id": shot.get("shot_id", f"shot_{idx + 1:03d}"),
                "cmds": cmds,
                "hash_file": hash_file,
                "cache_key": cache_key,
            }
        )

    def _exec_shot_job(job: dict[str, Any]) -> None:
        for cmd in job["cmds"]:
            res = _run(cmd, timeout=300)
            if res.returncode != 0:
                raise RuntimeError(f"Shot {job['idx'] + 1} ({job['shot_id']}) render failed:\n{res.stderr}")
        job["hash_file"].write_text(job["cache_key"] + "\n", encoding="utf-8")

    if not dry_run and shot_jobs:
        if workers > 1 and len(shot_jobs) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(_exec_shot_job, job) for job in shot_jobs]
                for fut in as_completed(futs):
                    fut.result()
        else:
            for job in shot_jobs:
                _exec_shot_job(job)

    # Probe exact rendered duration of every shot_NNN.mp4 before computing xfade offsets
    if dry_run:
        probed_durations = list(target_durations)
    else:
        probed_durations = [
            _probe_clip_duration(clip_paths[idx], fallback=target_durations[idx])
            for idx in range(len(shots))
        ]

    # Step 1: Cut-aligned segment video assembly (~10-15 shots per segment + lossless -c copy concat)
    video_timeline = work_dir / "timeline_video.mp4"
    segments = _partition_cut_aligned_segments(shots, target_size=12, max_size=15, has_xfade=("xfade" in avail_filters))
    shot_starts: list[float] = []
    segment_reports: list[dict[str, Any]] = []

    if len(segments) == 1:
        video_cmd, shot_starts, total_dur = _build_video_timeline_cmd(
            shots=shots,
            clip_paths=clip_paths,
            shot_durations=probed_durations,
            video_timeline=video_timeline,
            fps=fps,
            style_pack=style_pack,
            avail_filters=avail_filters,
        )
        planned_cmds.append(video_cmd)
        if not dry_run:
            res = _run(video_cmd, timeout=600)
            if res.returncode != 0:
                raise RuntimeError(f"Video timeline assembly failed:\n{res.stderr}")
            total_dur = _probe_clip_duration(video_timeline, fallback=total_dur)
        segment_reports.append({"segment": 1, "shots": [0, len(shots)], "duration_sec": round(total_dur, 3)})
    else:
        seg_dir = work_dir / "timeline_segments"
        seg_dir.mkdir(parents=True, exist_ok=True)
        seg_paths: list[Path] = []
        seg_offset = 0.0
        for seg_idx, (s0, s1) in enumerate(segments):
            seg_out = seg_dir / f"seg_{seg_idx + 1:03d}.mp4"
            seg_paths.append(seg_out)
            seg_cmd, local_starts, seg_dur = _build_video_timeline_cmd(
                shots=shots[s0:s1],
                clip_paths=clip_paths[s0:s1],
                shot_durations=probed_durations[s0:s1],
                video_timeline=seg_out,
                fps=fps,
                style_pack=style_pack,
                avail_filters=avail_filters,
            )
            planned_cmds.append(seg_cmd)
            if not dry_run:
                res = _run(seg_cmd, timeout=600)
                if res.returncode != 0:
                    raise RuntimeError(f"Video timeline segment {seg_idx + 1} (shots {s0}..{s1}) failed:\n{res.stderr}")
                seg_dur = _probe_clip_duration(seg_out, fallback=seg_dur)
            shot_starts.extend(seg_offset + t for t in local_starts)
            seg_offset += seg_dur
            segment_reports.append({"segment": seg_idx + 1, "shots": [s0, s1], "duration_sec": round(seg_dur, 3)})

        concat_list = seg_dir / "concat_list.txt"
        concat_lines = [f"file '{str(sp.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for sp in seg_paths]
        if not dry_run:
            concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy", "-movflags", "+faststart",
            str(video_timeline),
        ]
        planned_cmds.append(concat_cmd)
        if not dry_run:
            res = _run(concat_cmd, timeout=300)
            if res.returncode != 0:
                raise RuntimeError(f"Lossless segment concat failed:\n{res.stderr}")
            total_dur = _probe_clip_duration(video_timeline, fallback=seg_offset)
        else:
            total_dur = seg_offset

    # Step 2: Frame-locked 4-track audio mix (NAT_A / NAT_B / BGM w/ sonic_postcard ducking / SFX)
    mixed_av = work_dir / "timeline_mixed.mp4"
    audio_cmd = _build_4track_audio_mix_cmd(
        edl=edl,
        shots=shots,
        shot_starts=shot_starts,
        shot_durations=probed_durations,
        total_dur=total_dur,
        video_timeline=video_timeline,
        mixed_output=mixed_av,
        style_pack=style_pack,
        dry_run=dry_run,
    )
    planned_cmds.append(audio_cmd)
    if not dry_run:
        res = _run(audio_cmd, timeout=600)
        if res.returncode != 0:
            raise RuntimeError(f"4-track audio mix failed:\n{res.stderr}")

    # Step 3: Two-pass EBU R128 mastering (-14 LUFS, LRA=9, linear=true)
    output_master.parent.mkdir(parents=True, exist_ok=True)
    loudnorm_cmds = _two_pass_loudnorm(mixed_av, output_master, dry_run=dry_run)
    planned_cmds.extend(loudnorm_cmds)

    report = {
        "status": "dry_run" if dry_run else "rendered",
        "renderer_version": RENDERER_VERSION,
        "output_master": str(output_master),
        "shot_count": len(shots),
        "style_id": style_id,
        "look_id": look_id,
        "workers": workers,
        "reuse_cache": reuse_cache,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "segments": segment_reports,
        "estimated_duration_sec": round(total_dur, 3),
        "shot_starts_sec": [round(t, 3) for t in shot_starts],
        "probed_durations": [round(d, 4) for d in probed_durations],
        "command_count": len(planned_cmds),
        "commands": planned_cmds if dry_run else None,
    }
    if not dry_run:
        (work_dir / "render_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic FFmpeg master renderer and QA extractor (v0.2.1).")
    parser.add_argument("--edl", help="Path to validated EDL JSON file (used for rendering and EDL-aware --extract-qa).")
    parser.add_argument("--output", "-o", default="./work/trip_master_1080p.mp4", help="Output master MP4 path.")
    parser.add_argument("--work-dir", default="./work", help="Working directory for intermediate clips.")
    parser.add_argument("--width", type=int, default=None, help="Output width (defaults to EDL resolution or 1920).")
    parser.add_argument("--height", type=int, default=None, help="Output height (defaults to EDL resolution or 1080).")
    parser.add_argument("--fps", type=int, default=None, help="Output frame rate (defaults to EDL fps or 30).")
    parser.add_argument("--stabilize", action="store_true", help="Enable 2-pass vidstab stabilization on video clips.")
    parser.add_argument("--workers", type=int, default=1, help="Parallel shot rendering worker count (default: 1).")
    parser.add_argument("--reuse-cache", action="store_true", help="Reuse unchanged rendered shot_NNN.mp4 clips via content hash.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned FFmpeg commands without rendering.")
    parser.add_argument("--extract-qa", help="Path to rendered MP4 to extract QA sheets, transitions, collages, telemetry, and <=60s chunks.")
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    if args.extract_qa:
        edl_obj = json.loads(Path(args.edl).expanduser().resolve().read_text(encoding="utf-8")) if args.edl else None
        rep_path = work_dir / "render_report.json"
        render_rep = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.is_file() else None
        qa_report = extract_qa_artifacts(
            Path(args.extract_qa).expanduser().resolve(),
            work_dir / "qa",
            edl=edl_obj,
            render_report=render_rep,
        )
        print(json.dumps(qa_report, indent=2))
        return 0

    if not args.edl:
        raise SystemExit("Error: Provide --edl to render or --extract-qa to inspect a rendered MP4.")

    edl = json.loads(Path(args.edl).expanduser().resolve().read_text(encoding="utf-8"))

    # Resolve canvas dimensions from CLI -> EDL resolution -> aspect_profile -> 1920x1080
    width = args.width
    height = args.height
    if width is None or height is None:
        res_str = str(edl.get("resolution") or "")
        m = re.match(r"^(\d+)x(\d+)$", res_str)
        if m:
            width = width or int(m.group(1))
            height = height or int(m.group(2))
        elif edl.get("aspect_profile") == "9:16":
            width = width or 1080
            height = height or 1920
        else:
            width = width or 1920
            height = height or 1080

    fps = int(round(float(args.fps or edl.get("fps") or 30)))

    report = render_trip(
        edl=edl,
        output_master=Path(args.output).expanduser().resolve(),
        work_dir=work_dir,
        width=width,
        height=height,
        fps=fps,
        stabilize=args.stabilize,
        dry_run=args.dry_run,
        workers=args.workers,
        reuse_cache=args.reuse_cache,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
