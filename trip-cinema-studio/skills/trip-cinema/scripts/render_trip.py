#!/usr/bin/env python3
"""Deterministic FFmpeg master renderer and QA extractor for trip-cinema-studio (v0.2.0).

Implements v0.2.0 parity with the macOS native engine:
  - Declarative Style Pack loading from `styles/<style_id>.json`
    (`color_grade.ffmpeg_vf`, `textures.grain_amount`, `audio.duck_bgm_db`, palette/typography)
  - Explicit `timeline_cursor` video assembly with frame-locked `xfade` offsets
    (`dissolve`, `slideleft`/`slideright`/`slideup`/`slidedown` + `boxblur` whip-pan,
    `fadewhite` flash cut, `wipeleft` paper wipe, warm `light_leak`/`film_burn`, `zoom_punch`)
    — eliminating the v0.1.0 cumulative `-0.25s` A/V desync bug
  - `media_type == "graphic"` support (compositing pre-rendered Remotion PNG/MP4 cards
    or rendering styled Pillow/FFmpeg cards for `chapter_title`, `route_map`,
    `trip_wrapped`, `cast_intro`, `text_behind_subject`, `blooper_title`)
  - `speed_profile` support (`constant` slow-mo `setpts=2.0*PTS`/`2.5*PTS` and
    5-phase analytical micro-slice `cubic_ramp` smoothstep ramps)
  - Multi-source video/photo layouts: `split_2up_video`, `triptych_3up_video`,
    `split_diptych_pair`, `bento_living_grid`, `polaroid_stack`, `super8_frame`,
    and `blurred_bg_pillarbox`
  - 4-track (`NAT_A`, `NAT_B`, `BGM`, `SFX`) `adelay` + `amix` frame-locked audio
    with J/L-cut overlaps and smooth `sonic_postcard` BGM ducking
  - HDR-to-SDR Rec.709 Mobius tone-mapping (`zscale`/`tonemap`), optional 2-pass
    `vidstab`, 2x supersampled `zoompan` Ken Burns, two-pass `-14 LUFS` `LRA=9`
    `linear=true` EBU R128 mastering, and `--extract-qa` review extraction
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any


HDR_TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=mobius:desat=0,"
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
    "whip_pan_left": ("slideleft", 0.36, "whip"),
    "whip_pan_right": ("slideright", 0.36, "whip"),
    "whip_pan_up": ("slideup", 0.36, "whip"),
    "whip_pan_down": ("slidedown", 0.36, "whip"),
    "flash_cut": ("fadewhite", 0.20, None),
    "paper_wipe": ("wipeleft", 0.38, None),
    "light_leak": ("dissolve", 0.45, "warm_burn"),
    "film_burn": ("fadewhite", 0.45, "warm_burn"),
    "zoom_punch": ("zoomin", 0.32, None),
}

VIDEO_CARD_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
IMAGE_CARD_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _probe_filters() -> set[str]:
    proc = _run(["ffmpeg", "-hide_banner", "-filters"], timeout=10)
    return set(
        re.findall(
            r"\b(zscale|vidstabdetect|vidstabtransform|acrossfade|loudnorm|xfade|boxblur|adelay|amix)\b",
            proc.stdout or "",
        )
    )


def _probe_video_info(path: Path, dry_run: bool = False) -> dict[str, Any]:
    if dry_run or not path.exists():
        return {"has_audio": True, "is_hdr": False, "duration_sec": None}
    proc = _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=15,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"has_audio": False, "is_hdr": False, "duration_sec": None}
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    transfer = (v.get("color_transfer") or "").lower()
    dur_raw = (data.get("format") or {}).get("duration")
    dur = float(dur_raw) if dur_raw is not None else None
    return {
        "has_audio": has_audio,
        "is_hdr": transfer in {"arib-std-b67", "smpte2084"},
        "duration_sec": dur,
    }


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


def _parse_focal_anchor(raw: Any) -> tuple[float, float]:
    """Accept both EDL v1.0 `{x, y}` and v2.0 `[x, y]` focal anchor shapes."""
    fx, fy = 0.5, 0.5
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            fx, fy = float(raw[0]), float(raw[1])
        except (TypeError, ValueError):
            fx, fy = 0.5, 0.5
    elif isinstance(raw, dict):
        try:
            fx = float(raw.get("x", 0.5))
            fy = float(raw.get("y", 0.5))
        except (TypeError, ValueError):
            fx, fy = 0.5, 0.5
    return min(0.95, max(0.05, fx)), min(0.95, max(0.05, fy))


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
    """Build 2x-supersampled zoompan Ken Burns command anchored to `focal_anchor`."""
    dur = _shot_output_duration(shot)
    frames = max(1, int(round(dur * fps)))
    fx, fy = _parse_focal_anchor(shot.get("focal_anchor"))

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
        f"crop={super_w}:{super_h},setsar=1,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={fps},"
        f"{tail_vf}"
    )
    return [
        "ffmpeg", "-y", "-loop", "1", "-i", shot["source_path"],
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{dur:.4f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
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
    """Render synchronized 2-up, 3-up triptych, Living Bento, or Polaroid grids via hstack/vstack."""
    dur = _shot_output_duration(shot)
    tiles_raw = shot.get("tiles")
    tiles: list[dict[str, Any]] = []
    if isinstance(tiles_raw, list) and len(tiles_raw) >= 2:
        tiles = list(tiles_raw)
    elif shot.get("source_path") and shot.get("pair_source_path"):
        default_mt = shot.get("media_type", "photo")
        tiles = [
            {"source_path": shot["source_path"], "media_type": default_mt, "in_sec": float(shot.get("in_sec", 0.0))},
            {"source_path": shot["pair_source_path"], "media_type": default_mt, "in_sec": float(shot.get("in_sec", 0.0))},
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
            inputs.extend(["-loop", "1", "-t", f"{dur:.4f}", "-i", str(t_src)])
            hdr_prefix = ""
        else:
            inputs.extend(["-ss", f"{t_in:.4f}", "-t", f"{dur:.4f}", "-i", str(t_src)])
            info = _probe_video_info(t_src, dry_run=dry_run)
            hdr_prefix = f"{HDR_TONEMAP_CHAIN}," if (info["is_hdr"] and "zscale" in avail_filters) else ""

        lbl = f"[t{idx}]"
        chain = (
            f"[{idx}:v]{hdr_prefix}"
            f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},setsar=1,fps={fps},setpts=PTS-STARTPTS"
        )
        if extra_pad:
            chain += f",{extra_pad}"
        chain += lbl
        tile_filters.append(chain)
        return lbl

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
                inner_w = max(64, w0 - 44) & ~1
                inner_h = max(64, height - 150) & ~1
                pad_g0 = f",pad={w0 + gutter}:{height}:0:0:color={bg_hex}" if gutter > 0 else ""
                pad_g1 = f",pad={w1 + gutter}:{height}:0:0:color={bg_hex}" if gutter > 0 else ""
                l0 = _tile_input_and_pre(0, tiles[0], inner_w, inner_h, f"pad={w0}:{height}:(ow-iw)/2:36:color=0xFAF7F2{pad_g0}")
                l1 = _tile_input_and_pre(1, tiles[1], inner_w, inner_h, f"pad={w1}:{height}:(ow-iw)/2:36:color=0xFAF7F2{pad_g1}")
                w2_inner = max(64, w2 - 44) & ~1
                l2 = _tile_input_and_pre(2, tiles[2], w2_inner, inner_h, f"pad={w2}:{height}:(ow-iw)/2:36:color=0xFAF7F2")
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
        in_w = max(64, w0 - 64) & ~1
        in_h = max(64, height - 160) & ~1
        pad0 = f"pad={w0}:{height}:(ow-iw)/2:40:color=0xFAF7F2" + (f",pad={w0 + gutter}:{height}:0:0:color={bg_hex}" if gutter > 0 else "")
        pad1 = f"pad={w1}:{height}:(ow-iw)/2:40:color=0xFAF7F2"
        l0 = _tile_input_and_pre(0, tiles[0], in_w, in_h, pad0)
        l1 = _tile_input_and_pre(1, tiles[1], in_w, in_h, pad1)
        combine = f"{l0}{l1}hstack=inputs=2,{tail_vf}[v]"

    fc = ";".join(tile_filters) + ";" + combine
    anull_idx = len(tiles)
    return [
        "ffmpeg", "-y",
        *inputs,
        "-f", "lavfi", "-t", f"{dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", fc,
        "-map", "[v]", "-map", f"{anull_idx}:a",
        "-t", f"{dur:.4f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out_clip),
    ]


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
    """Render single portrait (`blurred_bg_pillarbox`) or `super8_frame` clip."""
    dur = _shot_output_duration(shot)
    mtype = str(shot.get("media_type", "photo")).lower()
    src = Path(shot["source_path"])
    lay_cfg = style_pack.get("layouts") or {}
    blur_sigma = float(lay_cfg.get("pillarbox_blur_sigma", 28.0))
    dim_alpha = float(lay_cfg.get("pillarbox_dim_alpha", 0.22))
    dim_brightness = -min(0.32, max(0.08, dim_alpha * 0.65))
    tail_vf = _compose_tail_vf(look_vf, grain_vf, shot, dur, style_pack, width, height)

    hdr_prefix = ""
    if mtype == "video":
        info = _probe_video_info(src, dry_run=dry_run)
        if (bool(shot.get("is_hdr", False)) or info["is_hdr"]) and "zscale" in avail_filters:
            hdr_prefix = f"{HDR_TONEMAP_CHAIN},"

    if layout == "super8_frame":
        gate_w = min(width, int(round(height * 4.0 / 3.0))) & ~1
        gate_h = (height - 48) & ~1
        fc = (
            f"[0:v]{hdr_prefix}scale={gate_w}:{gate_h}:force_original_aspect_ratio=increase,"
            f"crop={gate_w}:{gate_h},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x0A0908,"
            f"setsar=1,fps={fps},{tail_vf}[v]"
        )
    else:
        fc = (
            f"[0:v]{hdr_prefix}split=2[bg0][fg0];"
            f"[bg0]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"gblur=sigma={blur_sigma:.1f},eq=brightness={dim_brightness:.3f}[bg];"
            f"[fg0]scale=-2:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},{tail_vf}[v]"
        )

    in_args = (
        ["-loop", "1", "-t", f"{dur:.4f}", "-i", str(src)]
        if mtype == "photo"
        else ["-ss", f"{float(shot.get('in_sec', 0.0)):.4f}", "-t", f"{dur:.4f}", "-i", str(src)]
    )
    return [
        "ffmpeg", "-y",
        *in_args,
        "-f", "lavfi", "-t", f"{dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", fc,
        "-map", "[v]", "-map", "1:a",
        "-t", f"{dur:.4f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
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
) -> list[list[str]]:
    """Build command(s) for a video shot: HDR tone-map, vidstab, constant slow-mo, or cubic speed ramp."""
    in_sec = float(shot.get("in_sec", 0.0))
    dur = _shot_output_duration(shot)
    src = Path(shot["source_path"])
    info = _probe_video_info(src, dry_run=dry_run)
    is_hdr = bool(shot.get("is_hdr", False)) or info["is_hdr"]
    tail_vf = _compose_tail_vf(look_vf, grain_vf, shot, dur, style_pack, width, height)

    sp = shot.get("speed_profile") if isinstance(shot.get("speed_profile"), dict) else None
    sp_mode = str(sp.get("mode") or sp.get("type") or "").lower() if sp else ""

    # Analytical micro-slice piecewise cubic speed ramp
    if sp and sp_mode == "cubic_ramp":
        slices, total_out, total_src_req = _build_cubic_ramp_slices(sp)
        if slices:
            target_out = dur if shot.get("duration_sec") else total_out
            pre_parts: list[str] = []
            if is_hdr and "zscale" in avail_filters:
                pre_parts.append(HDR_TONEMAP_CHAIN)
            pre_parts.append(f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,setpts=PTS-STARTPTS")
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
                f"{concat_in}concat=n={k}:v=1:a=0,fps={fps},trim=duration={target_out:.4f},setpts=PTS-STARTPTS,{tail_vf}[v]"
            )
            return [[
                "ffmpeg", "-y",
                "-ss", f"{in_sec:.4f}", "-t", f"{total_src_req + 0.50:.4f}", "-i", str(src),
                "-f", "lavfi", "-t", f"{target_out:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-filter_complex", ";".join(fc_parts),
                "-map", "[v]", "-map", "1:a",
                "-t", f"{target_out:.4f}", "-r", str(fps),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", "-shortest",
                str(out_clip),
            ]]

    # Constant speed (e.g. 0.5x -> setpts=2.0*PTS, 0.4x -> setpts=2.5*PTS) or 1.0x normal
    rate = 1.0
    if sp and sp_mode == "constant":
        rate = max(0.2, min(8.0, float(sp.get("rate", 1.0))))
    src_span = dur * rate

    vf_parts: list[str] = []
    if is_hdr and "zscale" in avail_filters:
        vf_parts.append(HDR_TONEMAP_CHAIN)

    cmds: list[list[str]] = []
    if stabilize and "vidstabdetect" in avail_filters and "vidstabtransform" in avail_filters:
        trf_path = work_dir / f"{out_clip.stem}.trf"
        cmds.append([
            "ffmpeg", "-y", "-ss", f"{in_sec:.4f}", "-t", f"{src_span:.4f}", "-i", str(src),
            "-vf", f"vidstabdetect=shakiness=5:accuracy=15:result={trf_path}",
            "-f", "null", "-",
        ])
        vf_parts.append(f"vidstabtransform=input={trf_path}:smoothing=15:zoom=0:optzoom=1")

    vf_parts.append(f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1")
    if abs(rate - 1.0) > 1e-3:
        pts_mult = 1.0 / rate
        vf_parts.append(f"setpts={pts_mult:.4f}*(PTS-STARTPTS)")
    else:
        vf_parts.append("setpts=PTS-STARTPTS")
    vf_parts.append(f"fps={fps}")
    vf_parts.append(tail_vf)
    vf = ",".join(vf_parts)

    cmds.append([
        "ffmpeg", "-y",
        "-ss", f"{in_sec:.4f}", "-t", f"{src_span + 0.10:.4f}", "-i", str(src),
        "-f", "lavfi", "-t", f"{dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-vf", vf,
        "-map", "0:v", "-map", "1:a",
        "-t", f"{dur:.4f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
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
) -> list[str]:
    """Render `media_type == 'graphic'` via pre-rendered Remotion PNG/MP4 or styled Pillow/FFmpeg card."""
    dur = _shot_output_duration(shot)
    gtype = str(shot.get("graphic_type") or shot.get("overlay") or "chapter_title").lower()
    fade_parts = _fade_vf_parts(shot, dur)
    if not fade_parts:
        fd = min(0.45, dur * 0.25)
        fade_parts = [f"fade=t=in:st=0:d={fd:.3f}", f"fade=t=out:st={max(0.0, dur - fd):.3f}:d={fd:.3f}"]

    # Path A: Pre-rendered Remotion MP4 / PNG card
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
                "-f", "lavfi", "-t", f"{dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-vf", vf,
                "-map", "0:v", "-map", "1:a",
                "-t", f"{dur:.4f}", "-r", str(fps),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", "-shortest",
                str(out_clip),
            ]
        return [
            "ffmpeg", "-y",
            "-loop", "1", "-t", f"{dur:.4f}", "-i", str(pre_path),
            "-f", "lavfi", "-t", f"{dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", vf,
            "-map", "0:v", "-map", "1:a",
            "-t", f"{dur:.4f}", "-r", str(fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(out_clip),
        ]

    # Path B1: Pillow-rendered card PNG -> smooth subtle slow-zoom FFmpeg clip
    card_png = cards_dir / f"{out_clip.stem}_card.png"
    rendered_png = False
    if not dry_run:
        rendered_png = _render_card_png_pillow(shot, card_png, width, height, style_pack)

    if rendered_png:
        frames = max(1, int(round(dur * fps)))
        zoom_pct = float((style_pack.get("cards") or {}).get("backdrop_zoom_pct", 0.04))
        z_expr = f"1.0+{zoom_pct:.4f}*on/{frames}"
        vf_parts = [
            f"zoompan=z='{z_expr}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={frames}:s={width}x{height}:fps={fps}",
        ]
        if grain_vf:
            vf_parts.append(grain_vf)
        vf_parts.extend(fade_parts)
        vf_parts.append("format=yuv420p")
        return [
            "ffmpeg", "-y",
            "-loop", "1", "-t", f"{dur:.4f}", "-i", str(card_png),
            "-f", "lavfi", "-t", f"{dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", ",".join(vf_parts),
            "-map", "0:v", "-map", "1:a",
            "-t", f"{dur:.4f}", "-r", str(fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
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
        in_args = ["-loop", "1", "-t", f"{dur:.4f}", "-i", str(Path(str(backdrop_raw)).expanduser())]
        base_vf = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height},setsar=1,gblur=sigma=20,eq=brightness=-0.22",
        ]
        text_hex = "0xF7F4EE"
        muted_hex = "0xDFD9CE"
    else:
        in_args = ["-f", "lavfi", "-t", f"{dur:.4f}", "-i", f"color=c={bg_hex}:s={width}x{height}:r={fps}"]
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
        "-f", "lavfi", "-t", f"{dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-vf", ",".join(vf_parts),
        "-map", "0:v", "-map", "1:a",
        "-t", f"{dur:.4f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out_clip),
    ]


def _build_video_timeline_cmd(
    shots: list[dict[str, Any]],
    clip_paths: list[Path],
    shot_durations: list[float],
    video_timeline: Path,
    fps: int,
    style_pack: dict[str, Any],
    avail_filters: set[str],
) -> tuple[list[str], list[float], float]:
    """Assemble frame-locked video timeline using explicit `timeline_cursor` + `xfade` offsets.

    Returns `(ffmpeg_cmd, shot_starts, total_timeline_dur)`.
    """
    if len(clip_paths) == 1:
        cmd = ["ffmpeg", "-y", "-i", str(clip_paths[0]), "-an", "-c:v", "copy", str(video_timeline)]
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
    has_boxblur = "boxblur" in avail_filters

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

            needs_post = (post_fx == "whip" and has_boxblur) or (post_fx == "warm_burn")
            mid_lbl = f"[vxf{idx}]" if needs_post else final_lbl
            fc_parts.append(
                f"{prev_lbl}[vin{idx}]xfade=transition={xf_name}:duration={ov:.6f}:offset={offset:.6f}{mid_lbl}"
            )
            if post_fx == "whip" and has_boxblur:
                fc_parts.append(
                    f"{mid_lbl}boxblur=luma_radius=18:luma_power=1:chroma_radius=9:chroma_power=1:"
                    f"enable='between(t,{offset:.4f},{offset + ov:.4f})'{final_lbl}"
                )
            elif post_fx == "warm_burn":
                fc_parts.append(
                    f"{mid_lbl}colorbalance=rs=0.28:gs=0.10:bs=-0.22:rh=0.30:gh=0.12:bh=-0.18:"
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
        "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
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
    if match:
        stats = json.loads(match.group(0))
        af = (
            f"loudnorm=I=-14:LRA=9:TP=-1.0:"
            f"measured_I={stats['input_i']}:measured_LRA={stats['input_lra']}:"
            f"measured_TP={stats['input_tp']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true"
        )
    else:
        af = "loudnorm=I=-14:LRA=9:TP=-1.0:linear=true"

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


def extract_qa_artifacts(master_path: Path, qa_dir: Path, chunk_sec: int = 60) -> dict[str, Any]:
    """Extract `-sseof -0.05` end-frame check + <=60s review chunks for `read_file` QA."""
    qa_dir.mkdir(parents=True, exist_ok=True)
    end_frame = qa_dir / "end_frame_check.jpg"
    _run(
        ["ffmpeg", "-y", "-sseof", "-0.05", "-i", str(master_path), "-frames:v", "1", "-q:v", "2", str(end_frame)],
        timeout=30,
    )

    proc = _run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(master_path)],
        timeout=15,
    )
    dur = float(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else 60.0
    n_chunks = max(1, math.ceil(dur / chunk_sec))
    chunks: list[str] = []
    for idx in range(n_chunks):
        start = idx * chunk_sec
        chunk_out = qa_dir / f"qa_chunk_{idx + 1:02d}.mp4"
        _run(
            [
                "ffmpeg", "-y", "-ss", str(start), "-t", str(chunk_sec),
                "-i", str(master_path),
                "-vf", "scale=960:540", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                str(chunk_out),
            ],
            timeout=120,
        )
        if chunk_out.exists():
            chunks.append(str(chunk_out))

    return {
        "master": str(master_path),
        "duration_sec": round(dur, 2),
        "end_frame_check": str(end_frame) if end_frame.exists() else None,
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
) -> dict[str, Any]:
    """Render all EDL shots, assemble frame-locked xfade video + 4-track J/L-cut audio, and master."""
    clips_dir = work_dir / "rendered_shots"
    cards_dir = work_dir / "graphic_cards"
    clips_dir.mkdir(parents=True, exist_ok=True)

    avail_filters = (
        _probe_filters()
        if not dry_run
        else {"zscale", "vidstabdetect", "vidstabtransform", "acrossfade", "loudnorm", "xfade", "boxblur", "adelay", "amix"}
    )

    style_pack = _load_style_pack(edl)
    style_id = str(style_pack.get("style_id") or edl.get("style_id") or "cinema_doc")
    look_id, look_vf, grain_vf = _resolve_grade_and_grain(edl, style_pack)

    shots = edl.get("shots", [])
    if not shots:
        raise ValueError("EDL contains no shots.")

    # Ensure even output dimensions
    width = width & ~1
    height = height & ~1

    planned_cmds: list[list[str]] = []
    clip_paths: list[Path] = []
    shot_durations: list[float] = []

    default_portrait_layout = str(
        edl.get("vertical_layout")
        or (style_pack.get("layouts") or {}).get("default_portrait_layout")
        or "blurred_bg_pillarbox"
    )

    for idx, shot in enumerate(shots):
        clip_out = clips_dir / f"shot_{idx + 1:03d}.mp4"
        clip_paths.append(clip_out)
        # Quantize shot duration to exact integer frame count for zero A/V drift
        raw_dur = _shot_output_duration(shot)
        shot_dur = max(2, int(round(raw_dur * fps))) / float(fps)
        shot = dict(shot)
        shot["duration_sec"] = shot_dur
        shot_durations.append(shot_dur)

        mtype = str(shot.get("media_type", "photo")).lower()
        orient = str(shot.get("orientation", "landscape")).lower()
        layout = str(shot.get("layout") or shot.get("aspect_handling") or "single").lower()
        has_tiles = isinstance(shot.get("tiles"), list) and len(shot["tiles"]) >= 2

        if mtype == "graphic":
            cmds = [_render_graphic_shot(shot, clip_out, width, height, fps, grain_vf, style_pack, cards_dir, dry_run)]
        elif layout in {"split_2up_video", "triptych_3up_video", "bento_living_grid", "polaroid_stack"} or (
            layout == "split_diptych_pair" and (has_tiles or shot.get("pair_source_path"))
        ):
            cmds = [
                _render_multi_tile_shot(
                    shot, layout, clip_out, width, height, fps, look_vf, grain_vf, style_pack, avail_filters, dry_run
                )
            ]
        elif layout == "super8_frame" or (orient == "portrait" and width > height):
            eff_layout = layout if layout != "single" else default_portrait_layout
            if eff_layout == "split_diptych_pair" and (has_tiles or shot.get("pair_source_path")):
                cmds = [
                    _render_multi_tile_shot(
                        shot, eff_layout, clip_out, width, height, fps, look_vf, grain_vf, style_pack, avail_filters, dry_run
                    )
                ]
            else:
                cmds = [
                    _render_portrait_or_framed_shot(
                        shot, eff_layout, clip_out, width, height, fps, look_vf, grain_vf, style_pack, avail_filters, dry_run
                    )
                ]
        elif mtype == "photo":
            cmds = [_render_ken_burns_shot(shot, clip_out, width, height, fps, look_vf, grain_vf, style_pack)]
        else:
            use_stab = stabilize or bool(shot.get("stabilize", False))
            cmds = _render_video_shot(
                shot, clip_out, width, height, fps, look_vf, grain_vf, style_pack, avail_filters, use_stab, clips_dir, dry_run
            )

        for cmd in cmds:
            planned_cmds.append(cmd)
            if not dry_run:
                res = _run(cmd, timeout=300)
                if res.returncode != 0:
                    raise RuntimeError(f"Shot {idx + 1} ({shot.get('shot_id', '')}) render failed:\n{res.stderr}")

    # Step 1: Frame-locked video timeline via explicit timeline_cursor + xfade offsets
    video_timeline = work_dir / "timeline_video.mp4"
    video_cmd, shot_starts, total_dur = _build_video_timeline_cmd(
        shots=shots,
        clip_paths=clip_paths,
        shot_durations=shot_durations,
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

    # Step 2: Frame-locked 4-track audio mix (NAT_A / NAT_B / BGM w/ sonic_postcard ducking / SFX)
    mixed_av = work_dir / "timeline_mixed.mp4"
    audio_cmd = _build_4track_audio_mix_cmd(
        edl=edl,
        shots=shots,
        shot_starts=shot_starts,
        shot_durations=shot_durations,
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

    return {
        "status": "dry_run" if dry_run else "rendered",
        "output_master": str(output_master),
        "shot_count": len(shots),
        "style_id": style_id,
        "look_id": look_id,
        "estimated_duration_sec": round(total_dur, 3),
        "shot_starts_sec": [round(t, 3) for t in shot_starts],
        "command_count": len(planned_cmds),
        "commands": planned_cmds if dry_run else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic FFmpeg master renderer and QA extractor (v0.2.0).")
    parser.add_argument("--edl", help="Path to validated EDL JSON file.")
    parser.add_argument("--output", "-o", default="./work/trip_master_1080p.mp4", help="Output master MP4 path.")
    parser.add_argument("--work-dir", default="./work", help="Working directory for intermediate clips.")
    parser.add_argument("--width", type=int, default=None, help="Output width (defaults to EDL resolution or 1920).")
    parser.add_argument("--height", type=int, default=None, help="Output height (defaults to EDL resolution or 1080).")
    parser.add_argument("--fps", type=int, default=None, help="Output frame rate (defaults to EDL fps or 30).")
    parser.add_argument("--stabilize", action="store_true", help="Enable 2-pass vidstab stabilization on video clips.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned FFmpeg commands without rendering.")
    parser.add_argument("--extract-qa", help="Path to rendered MP4 to extract -sseof -0.05 frame + <=60s QA chunks.")
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    if args.extract_qa:
        qa_report = extract_qa_artifacts(Path(args.extract_qa).expanduser().resolve(), work_dir / "qa")
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
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
