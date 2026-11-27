#!/usr/bin/env python3
"""Gate 2 visual preview generator: v0.2.0 4-Card Style-Pack Carousel + v0.1.0 look fallback.

In v0.2.0 Style-Pack mode (`--style-ids`), reads `styles/<style_id>.json`, decodes photo
or video inputs upright in Rec.709 (using HLG-safe vs. PQ-safe HDR conversion), grades
each image with the pack's `color_grade.ffmpeg_vf`, and renders `1280x720` PNG preview cards:
  - Left 60% (`768x664`): Graded hero with styled title overlay and optional matte
  - Right 40% (`512x664`): Real portraits rendered in the pack's `default_portrait_layout`
  - Footer (`1280x56`): Palette swatches, style name, target BPM, and transition signature

When called without `--style-ids`, preserves legacy v0.1.0 4-look + vertical layout strip output.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm"}

HLG_TO_SDR_ZSCALE = (
    "zscale=pin=bt2020:min=bt2020nc:p=bt709:m=bt709:r=tv,"
    "format=yuv420p,eq=gamma=0.95:brightness=-0.02"
)
PQ_TO_SDR_MOBIUS = (
    "zscale=tin=smpte2084:pin=bt2020:min=bt2020nc:t=linear:npl=100,"
    "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)

LOOK_RECIPES: dict[str, dict[str, Any]] = {
    "warm_film": {
        "label": "Warm Film",
        "ffmpeg_vf": (
            "scale=960:540:force_original_aspect_ratio=increase,crop=960:540,"
            "eq=contrast=1.06:saturation=1.08:gamma=1.02,"
            "colorbalance=rs=0.06:gs=0.02:bs=-0.06:rm=0.04:bm=-0.04"
        ),
        "pil_matrix": (1.06, 1.01, 0.92),
        "pil_contrast": 1.08,
    },
    "moody_teal_orange": {
        "label": "Moody Teal-Orange",
        "ffmpeg_vf": (
            "scale=960:540:force_original_aspect_ratio=increase,crop=960:540,"
            "eq=contrast=1.12:saturation=1.04:brightness=-0.02,"
            "colorbalance=rs=-0.05:gs=0.01:bs=0.08:rh=0.07:gh=0.02:bh=-0.06"
        ),
        "pil_matrix": (0.98, 1.00, 1.04),
        "pil_contrast": 1.15,
    },
    "clean_natural": {
        "label": "Clean Natural",
        "ffmpeg_vf": (
            "scale=960:540:force_original_aspect_ratio=increase,crop=960:540,"
            "eq=contrast=1.03:saturation=1.03:gamma=1.0"
        ),
        "pil_matrix": (1.00, 1.00, 1.00),
        "pil_contrast": 1.03,
    },
    "soft_atmospheric": {
        "label": "Soft Atmospheric",
        "ffmpeg_vf": (
            "scale=960:540:force_original_aspect_ratio=increase,crop=960:540,"
            "eq=contrast=0.95:brightness=0.03:saturation=0.92:gamma=1.05,"
            "colorbalance=rs=0.03:gs=0.02:bs=0.04"
        ),
        "pil_matrix": (1.02, 1.01, 1.03),
        "pil_contrast": 0.94,
    },
}


def _has_ffmpeg() -> bool:
    try:
        proc = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5, check=False)
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _probe_filters() -> set[str]:
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=8, check=False)
        return set(re.findall(r"\b(zscale|tonemap)\b", proc.stdout or ""))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()


def _probe_video_transfer_and_dur(path: Path) -> tuple[str, float]:
    """Return `(color_transfer, duration_sec)` for a video clip via ffprobe."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return "", 2.0
        data = json.loads(proc.stdout)
        v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        transfer = (v.get("color_transfer") or "").lower()
        dur_raw = v.get("duration") or (data.get("format") or {}).get("duration")
        dur = float(dur_raw) if dur_raw is not None else 2.0
        return transfer, max(0.2, dur)
    except Exception:  # noqa: BLE001
        return "", 2.0


def _hdr_decode_prefix(transfer: str, avail_filters: set[str]) -> str:
    if transfer == "smpte2084":
        if "zscale" in avail_filters and "tonemap" in avail_filters:
            return PQ_TO_SDR_MOBIUS + ","
        if "zscale" in avail_filters:
            return "zscale=pin=bt2020:min=bt2020nc:p=bt709:m=bt709:r=tv,format=yuv420p,eq=gamma=0.92:brightness=-0.03,"
        return "format=yuv420p,eq=gamma=0.92:brightness=-0.03,"
    if transfer == "arib-std-b67":
        if "zscale" in avail_filters:
            return HLG_TO_SDR_ZSCALE + ","
        return "format=yuv420p,eq=gamma=0.95:brightness=-0.02,"
    return ""


def _decode_upright_frame_pil(src: Path, avail_filters: set[str], use_ffmpeg: bool) -> Any:
    """Decode any photo OR video file to an upright Rec.709 RGB Pillow Image."""
    from PIL import Image, ImageOps  # type: ignore

    if src.suffix.lower() in VIDEO_EXTS:
        if not use_ffmpeg:
            raise RuntimeError(f"FFmpeg is required to decode video input for Style Preview: {src}")
        transfer, dur = _probe_video_transfer_and_dur(src)
        sample_t = min(max(0.0, dur - 0.1), max(0.0, min(dur * 0.35, 1.5)))
        hdr_prefix = _hdr_decode_prefix(transfer, avail_filters)
        vf = f"{hdr_prefix}format=rgb24"
        with tempfile.TemporaryDirectory(prefix="trip_prev_dec_") as tmp_s:
            out_png = Path(tmp_s) / "frame.png"
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{sample_t:.3f}", "-i", str(src),
                    "-frames:v", "1", "-vf", vf, str(out_png),
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if proc.returncode != 0 or not out_png.is_file():
                raise RuntimeError(f"Failed to decode video frame from {src}: {proc.stderr.decode(errors='ignore')}")
            with Image.open(out_png) as im:
                return im.convert("RGB").copy()

    with Image.open(src) as img:
        return ImageOps.exif_transpose(img).convert("RGB").copy()


def _apply_style_grade_pil(img: Any, style_pack: dict[str, Any], use_ffmpeg: bool) -> Any:
    """Apply the Style Pack's FFmpeg color grade (with Pillow fallback) to an RGB Image."""
    from PIL import Image, ImageEnhance  # type: ignore

    grade = style_pack.get("color_grade") or {}
    ffmpeg_vf = str(grade.get("ffmpeg_vf") or "").strip()

    if use_ffmpeg and ffmpeg_vf:
        with tempfile.TemporaryDirectory(prefix="trip_prev_grade_") as tmp_s:
            tmp_dir = Path(tmp_s)
            in_png = tmp_dir / "in.png"
            out_png = tmp_dir / "out.png"
            img.save(in_png, format="PNG")
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(in_png),
                    "-vf", f"{ffmpeg_vf},format=rgb24",
                    "-frames:v", "1", str(out_png),
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if proc.returncode == 0 and out_png.is_file():
                with Image.open(out_png) as graded:
                    return graded.convert("RGB").copy()

    ci = grade.get("coreimage") or {}
    out = img.copy()
    contrast = float(ci.get("contrast", 1.04))
    sat = float(ci.get("saturation", 1.04))
    bright = 1.0 + float(ci.get("brightness", 0.0))
    out = ImageEnhance.Contrast(out).enhance(max(0.5, min(1.8, contrast)))
    out = ImageEnhance.Color(out).enhance(max(0.2, min(1.8, sat)))
    out = ImageEnhance.Brightness(out).enhance(max(0.5, min(1.5, bright)))
    return out


def _hex_rgb(hex_str: str, fallback: tuple[int, int, int] = (15, 17, 21)) -> tuple[int, int, int]:
    cleaned = str(hex_str or "").strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(c * 2 for c in cleaned)
    if len(cleaned) != 6:
        return fallback
    try:
        return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
    except ValueError:
        return fallback


def _is_light(rgb: tuple[int, int, int]) -> bool:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) > 145.0


def _load_font(size: int, kind: str = "sans") -> Any:
    """Resolve a clean system font across Linux and macOS."""
    from PIL import ImageFont  # type: ignore

    pools = {
        "serif": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
            "/System/Library/Fonts/Supplemental/Baskerville.ttc",
        ],
        "mono": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
            "/System/Library/Fonts/Menlo.ttc",
        ],
        "sans": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/SFNS.ttf",
        ],
        "sans_reg": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
        ],
    }
    for cand in pools.get(kind, pools["sans"]):
        if Path(cand).is_file():
            try:
                return ImageFont.truetype(cand, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fit_cover_anchor(img: Any, tw: int, th: int, fx: float = 0.5, fy: float = 0.4) -> Any:
    """Resize and crop RGB image to cover (tw, th) anchored at (fx, fy)."""
    from PIL import Image  # type: ignore

    resample = getattr(Image, "Resampling", Image).LANCZOS
    iw, ih = img.size
    scale = max(tw / max(1, iw), th / max(1, ih))
    nw, nh = max(tw, int(round(iw * scale))), max(th, int(round(ih * scale)))
    resized = img.resize((nw, nh), resample)
    cx = int(round(nw * fx - tw / 2.0))
    cy = int(round(nh * fy - th / 2.0))
    cx = max(0, min(nw - tw, cx))
    cy = max(0, min(nh - th, cy))
    return resized.crop((cx, cy, cx + tw, cy + th))


def _paste_rounded(canvas: Any, img: Any, pos: tuple[int, int], radius: int = 0) -> None:
    from PIL import Image, ImageDraw  # type: ignore

    if radius <= 1:
        canvas.paste(img, pos)
        return
    mask = Image.new("L", img.size, 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=radius, fill=255)
    canvas.paste(img, pos, mask)


def _render_style_preview_card(
    style_pack: dict[str, Any],
    hero_raw: Any,
    pa_raw: Any,
    pb_raw: Any,
    trip_title: str,
    out_path: Path,
    use_ffmpeg: bool,
) -> Path:
    """Render a 1280x720 Style-Pack Carousel PNG matching macos_style_previews.jxa layout."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter  # type: ignore

    w, h = 1280, 720
    footer_h = 56
    body_h = h - footer_h  # 664
    left_w = 768           # 60%
    right_w = w - left_w   # 512 (40%)

    pal = style_pack.get("palette") or {}
    typo = style_pack.get("typography") or {}
    tex = style_pack.get("textures") or {}
    lay = style_pack.get("layouts") or {}
    cards = style_pack.get("cards") or {}
    trans = style_pack.get("transitions") or {}
    pacing = style_pack.get("pacing_rules") or {}

    bg_rgb = _hex_rgb(pal.get("bg", "#0F1115"))
    surf_rgb = _hex_rgb(pal.get("surface", "#181B22"))
    txt_rgb = _hex_rgb(pal.get("text_primary", "#F5F2EB"))
    muted_rgb = _hex_rgb(pal.get("text_secondary", "#B9B2A3"))
    accent_rgb = _hex_rgb(pal.get("accent", "#D4AF37"))
    border_rgb = _hex_rgb(pal.get("border", "#2E333D"))

    hero_g = _apply_style_grade_pil(hero_raw, style_pack, use_ffmpeg)
    pa_g = _apply_style_grade_pil(pa_raw, style_pack, use_ffmpeg)
    pb_g = _apply_style_grade_pil(pb_raw, style_pack, use_ffmpeg)

    canvas = Image.new("RGBA", (w, h), (*bg_rgb, 255))

    # ---- Left 60% (768x664): Graded hero + styled title overlay ----
    frame_style = str(tex.get("frame_style") or "editorial_clean")
    letterbox_ratio = float(tex.get("letterbox_ratio") or 0.0)
    pad_l = 22 if frame_style == "postcard_matte" else 0
    hero_panel = _fit_cover_anchor(hero_g, left_w - 2 * pad_l, body_h - 2 * pad_l, fx=0.5, fy=0.45).convert("RGBA")

    # Subtle legibility gradient scrim over hero center/bottom
    scrim = Image.new("RGBA", hero_panel.size, (10, 12, 16, 78))
    hero_panel = Image.alpha_composite(hero_panel, scrim)
    canvas.alpha_composite(hero_panel, (pad_l, pad_l))

    draw = ImageDraw.Draw(canvas)

    if letterbox_ratio > 1.85:
        active_h = int(round(left_w / letterbox_ratio))
        bar_h = max(0, (body_h - active_h) // 2)
        if bar_h > 4:
            draw.rectangle([0, 0, left_w - 1, bar_h], fill=(8, 9, 12, 255))
            draw.rectangle([0, body_h - bar_h, left_w - 1, body_h - 1], fill=(8, 9, 12, 255))

    # Title typography based on Style Pack
    display_css = str(typo.get("display_css") or "").lower()
    disp_kind = "mono" if "mono" in display_css or "typewriter" in display_css else ("serif" if "serif" in display_css else "sans")
    f_eyebrow = _load_font(16, "mono" if disp_kind == "mono" else "sans")
    f_title = _load_font(48 if len(trip_title) <= 22 else 38, disp_kind)
    f_sub = _load_font(18, "sans_reg")

    eyebrow_txt = "CHAPTER I  ·  STYLE PREVIEW" if cards.get("use_roman_numerals") else "CHAPTER 01  ·  STYLE PREVIEW"
    title_txt = trip_title.upper() if typo.get("display_uppercase") else trip_title
    tagline_txt = str(style_pack.get("tagline") or "")[:64]

    # Subtle dark card backing when text_primary is light on bright photos, or light pill when style is light pop
    use_light_text = True
    title_fill = (250, 248, 243, 255) if use_light_text else (*txt_rgb, 255)
    sub_fill = (232, 227, 218, 235)

    def _center_left(y: int, text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = max(24, int((left_w - tw) / 2))
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 140))
        draw.text((x, y), text, font=font, fill=fill)

    draw.rectangle([24, 24, left_w - 24, body_h - 24], outline=(*accent_rgb, 110), width=2)
    _center_left(int(body_h * 0.36), eyebrow_txt, f_eyebrow, (*accent_rgb, 248))
    _center_left(int(body_h * 0.43), title_txt, f_title, title_fill)
    if cards.get("show_divider_rule", True):
        draw.line([(left_w // 2 - 84, int(body_h * 0.56)), (left_w // 2 + 84, int(body_h * 0.56))], fill=(*accent_rgb, 235), width=2)
    if tagline_txt:
        _center_left(int(body_h * 0.60), tagline_txt, f_sub, sub_fill)

    # Vertical separator between Left 60% and Right 40%
    draw.line([(left_w, 0), (left_w, body_h)], fill=(*border_rgb, 255), width=2)

    # ---- Right 40% (512x664): Portrait layout preview ----
    def_layout = str(lay.get("default_portrait_layout") or "split_diptych_pair")
    gutter = max(8, min(20, int(lay.get("gutter_px", 12))))
    radius = max(0, min(20, int(lay.get("corner_radius_px", 0))))
    rx0, ry0 = left_w + 18, 18
    rw, rh = right_w - 36, body_h - 36

    if def_layout == "polaroid_stack":
        # Two staggered tilted Polaroid prints on warm kraft/bg canvas
        for idx, (p_img, angle, dx, dy, cap) in enumerate(
            [(pa_g, -4.5, rx0 + 18, ry0 + 28, "DAY 01"), (pb_g, 4.0, rx0 + 178, ry0 + 205, "DAY 02")]
        ):
            cw, ch = 268, 356
            b = 16
            iw, ih = cw - 2 * b, 268
            card = Image.new("RGBA", (cw, ch), (250, 247, 242, 255))
            fit = _fit_cover_anchor(p_img, iw, ih, fx=0.5, fy=0.35).convert("RGBA")
            card.paste(fit, (b, b))
            cdraw = ImageDraw.Draw(card)
            cdraw.rectangle([0, 0, cw - 1, ch - 1], outline=(215, 206, 192, 255), width=2)
            cdraw.text((b + 4, b + ih + 22), cap, font=_load_font(16, "mono"), fill=(65, 58, 50, 255))
            rot = card.rotate(angle, resample=getattr(Image, "Resampling", Image).BICUBIC, expand=True)
            canvas.alpha_composite(rot, (dx, dy))
    elif def_layout == "bento_living_grid":
        # Asymmetric Bento: left tall portrait + right 2 stacked tiles with hairlines
        lw = int((rw - gutter) * 0.56)
        rw2 = rw - lw - gutter
        th = (rh - gutter) // 2
        bh = rh - th - gutter
        t0 = _fit_cover_anchor(pa_g, lw, rh, fx=0.5, fy=0.35)
        t1 = _fit_cover_anchor(pb_g, rw2, th, fx=0.5, fy=0.35)
        t2 = _fit_cover_anchor(hero_g, rw2, bh, fx=0.5, fy=0.5)
        _paste_rounded(canvas, t0, (rx0, ry0), radius=radius or 12)
        _paste_rounded(canvas, t1, (rx0 + lw + gutter, ry0), radius=radius or 12)
        _paste_rounded(canvas, t2, (rx0 + lw + gutter, ry0 + th + gutter), radius=radius or 12)
        draw.rounded_rectangle([rx0, ry0, rx0 + lw, ry0 + rh], radius=radius or 12, outline=(*border_rgb, 255), width=2)
        draw.rounded_rectangle([rx0 + lw + gutter, ry0, rx0 + rw, ry0 + th], radius=radius or 12, outline=(*border_rgb, 255), width=2)
        draw.rounded_rectangle([rx0 + lw + gutter, ry0 + th + gutter, rx0 + rw, ry0 + rh], radius=radius or 12, outline=(*border_rgb, 255), width=2)
    elif def_layout == "super8_frame":
        # Rounded Super 8 overscan matte + retro date stamp
        draw.rectangle([left_w + 2, 0, w, body_h], fill=(12, 10, 9, 255))
        gw, gh = rw - 24, int((rw - 24) * 1.12)
        gh = min(rh - 64, gh)
        gx, gy = rx0 + (rw - gw) // 2, ry0 + (rh - gh) // 2
        gate = _fit_cover_anchor(pa_g, gw, gh, fx=0.5, fy=0.35)
        _paste_rounded(canvas, gate, (gx, gy), radius=24)
        draw.rounded_rectangle([gx, gy, gx + gw, gy + gh], radius=24, outline=(*accent_rgb, 140), width=2)
        draw.text((gx + 18, gy + gh - 34), "SUPER 8  ·  18FPS", font=_load_font(15, "mono"), fill=(238, 152, 74, 240))
    elif def_layout == "triptych_3up_video":
        g3 = max(6, gutter // 2)
        cw = (rw - 2 * g3) // 3
        for idx, p_img in enumerate((pa_g, hero_g, pb_g)):
            tx = rx0 + idx * (cw + g3)
            tile = _fit_cover_anchor(p_img, cw, rh, fx=0.5, fy=0.35)
            _paste_rounded(canvas, tile, (tx, ry0), radius=radius)
    elif def_layout == "blurred_bg_pillarbox":
        bg = _fit_cover_anchor(pa_g, rw, rh, fx=0.5, fy=0.5).filter(ImageFilter.GaussianBlur(radius=20))
        bg = ImageEnhance.Brightness(bg).enhance(0.65)
        canvas.paste(bg, (rx0, ry0))
        fg_w = int(rh * 9 / 16)
        fg = _fit_cover_anchor(pa_g, min(rw - 24, fg_w), rh, fx=0.5, fy=0.35)
        canvas.paste(fg, (rx0 + (rw - fg.width) // 2, ry0))
    else:
        # split_2up_video / split_diptych_pair
        cw = (rw - gutter) // 2
        t0 = _fit_cover_anchor(pa_g, cw, rh, fx=0.5, fy=0.35)
        t1 = _fit_cover_anchor(pb_g, rw - cw - gutter, rh, fx=0.5, fy=0.35)
        _paste_rounded(canvas, t0, (rx0, ry0), radius=radius)
        _paste_rounded(canvas, t1, (rx0 + cw + gutter, ry0), radius=radius)
        if frame_style == "pop_sticker":
            draw.rounded_rectangle([rx0, ry0, rx0 + cw, ry0 + rh], radius=radius or 12, outline=(*accent_rgb, 255), width=4)
            draw.rounded_rectangle([rx0 + cw + gutter, ry0, rx0 + rw, ry0 + rh], radius=radius or 12, outline=(*border_rgb, 255), width=4)

    # Layout tag pill in top-right corner of right panel
    badge_txt = def_layout.replace("_", " ").upper()
    f_badge = _load_font(12, "mono")
    bb = draw.textbbox((0, 0), badge_txt, font=f_badge)
    bw, bh_t = (bb[2] - bb[0]) + 18, (bb[3] - bb[1]) + 10
    bx0, by0 = w - 26 - bw, 26
    draw.rounded_rectangle([bx0, by0, bx0 + bw, by0 + bh_t], radius=6, fill=(14, 16, 22, 215), outline=(*accent_rgb, 180), width=1)
    draw.text((bx0 + 9, by0 + 4), badge_txt, font=f_badge, fill=(246, 244, 238, 255))

    # ---- Footer (1280x56): Swatches, display name, BPM, and transition signature ----
    draw.rectangle([0, body_h, w, h], fill=(*surf_rgb, 255))
    draw.line([(0, body_h), (w, body_h)], fill=(*border_rgb, 255), width=2)

    footer_txt_rgb = (24, 24, 28) if _is_light(surf_rgb) else (246, 244, 238)
    footer_muted_rgb = (82, 78, 72) if _is_light(surf_rgb) else (182, 178, 170)

    swatches = pal.get("swatches") or [pal.get("bg", "#0F1115"), pal.get("accent", "#D4AF37")]
    sx = 22
    for sw in swatches[:5]:
        s_rgb = _hex_rgb(sw)
        draw.rounded_rectangle([sx, body_h + 14, sx + 28, body_h + 42], radius=5, fill=(*s_rgb, 255), outline=(*border_rgb, 255), width=1)
        sx += 34

    style_name = str(style_pack.get("display_name") or style_pack.get("style_id") or "Style Pack")
    style_id = str(style_pack.get("style_id") or "")
    f_name = _load_font(20, "sans")
    f_meta = _load_font(15, "mono")

    draw.text((sx + 14, body_h + 16), style_name, font=f_name, fill=(*footer_txt_rgb, 255))
    name_bb = draw.textbbox((sx + 14, body_h + 16), style_name, font=f_name)
    draw.text((name_bb[2] + 12, body_h + 20), f"[{style_id}]", font=f_meta, fill=(*accent_rgb, 255))

    bpm_lbl = str(pacing.get("target_bpm_label") or "80-110 BPM")
    trans_lbl = str(trans.get("signature_label") or "Cuts + Dissolves")
    right_meta = f"{bpm_lbl}  |  {trans_lbl}"
    rm_bb = draw.textbbox((0, 0), right_meta, font=f_meta)
    draw.text((w - 22 - (rm_bb[2] - rm_bb[0]), body_h + 20), right_meta, font=f_meta, fill=(*footer_muted_rgb, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, format="PNG")
    return out_path


# ---- Legacy v0.1.0 look helpers (preserved when --style-ids is omitted) ----

def _render_look_ffmpeg(src: Path, dst: Path, vf: str) -> bool:
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", "-q:v", "3", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, timeout=20, check=False)
    return proc.returncode == 0 and dst.exists()


def _render_look_pil(src: Path, dst: Path, recipe: dict[str, Any]) -> bool:
    try:
        from PIL import Image, ImageEnhance, ImageOps  # type: ignore

        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img = ImageOps.fit(img, (960, 540), method=Image.Resampling.LANCZOS)
            r_m, g_m, b_m = recipe["pil_matrix"]
            r, g, b = img.split()
            r = r.point(lambda v: min(255, int(v * r_m)))
            g = g.point(lambda v: min(255, int(v * g_m)))
            b = b.point(lambda v: min(255, int(v * b_m)))
            merged = Image.merge("RGB", (r, g, b))
            merged = ImageEnhance.Contrast(merged).enhance(recipe["pil_contrast"])
            merged.save(dst, quality=90)
        return True
    except Exception:  # noqa: BLE001
        return False


def _render_vertical_comparison(
    portrait_a: Path,
    portrait_b: Path,
    out_path: Path,
    use_ffmpeg: bool,
) -> bool:
    """Create a 2-up 16:9 strip comparing split_diptych_pair vs blurred_bg_pillarbox."""
    if use_ffmpeg:
        filter_complex = (
            "[0:v]scale=480:540:force_original_aspect_ratio=increase,crop=480:540[da];"
            "[1:v]scale=480:540:force_original_aspect_ratio=increase,crop=480:540[db];"
            "[da][db]hstack=inputs=2[diptych];"
            "[0:v]scale=960:540:force_original_aspect_ratio=increase,crop=960:540,gblur=sigma=24,eq=brightness=-0.12[bg];"
            "[0:v]scale=-2:540[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[pillar];"
            "[diptych][pillar]hstack=inputs=2[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", str(portrait_a), "-i", str(portrait_b),
            "-filter_complex", filter_complex, "-map", "[out]",
            "-frames:v", "1", "-q:v", "3", str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=25, check=False)
        if proc.returncode == 0 and out_path.exists():
            return True

    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps  # type: ignore

        with Image.open(portrait_a) as ia, Image.open(portrait_b) as ib:
            a = ImageOps.exif_transpose(ia).convert("RGB")
            b = ImageOps.exif_transpose(ib).convert("RGB")

        strip = Image.new("RGB", (1920, 540), (16, 16, 20))
        strip.paste(ImageOps.fit(a, (478, 540)), (0, 0))
        strip.paste(ImageOps.fit(b, (478, 540)), (482, 0))

        bg = ImageOps.fit(a, (960, 540)).filter(ImageFilter.GaussianBlur(radius=18))
        bg = ImageEnhance.Brightness(bg).enhance(0.65)
        fg = a.copy()
        fg.thumbnail((960, 540))
        bg.paste(fg, ((960 - fg.width) // 2, (540 - fg.height) // 2))
        strip.paste(bg, (960, 0))

        draw = ImageDraw.Draw(strip)
        draw.rectangle([16, 16, 300, 48], fill=(12, 12, 16))
        draw.text((24, 24), "A: Split Diptych Pair", fill=(240, 240, 245))
        draw.rectangle([976, 16, 1300, 48], fill=(12, 12, 16))
        draw.text((984, 24), "B: Blurred BG Pillarbox", fill=(240, 240, 245))
        strip.save(out_path, quality=90)
        return True
    except Exception:  # noqa: BLE001
        return False


def _pick_from_manifest(
    manifest_path: Path,
    allow_videos: bool = True,
) -> tuple[Path | None, Path | None, Path | None]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_assets = [
        a for a in data.get("assets", [])
        if not a.get("excluded_by_user")
        and (allow_videos or a.get("media_type") == "photo")
        and a.get("source_path")
        and Path(str(a["source_path"])).expanduser().is_file()
    ]
    if not raw_assets:
        return None, None, None
    # Prefer user-pinned assets first, then photos/videos cleanly
    ordered = sorted(raw_assets, key=lambda a: (0 if a.get("locked_by_user") else 1, 0 if a.get("media_type") == "photo" else 1))
    landscapes = [Path(a["source_path"]).expanduser().resolve() for a in ordered if a.get("orientation") == "landscape"]
    portraits = [Path(a["source_path"]).expanduser().resolve() for a in ordered if a.get("orientation") == "portrait"]
    hero = landscapes[0] if landscapes else Path(ordered[0]["source_path"]).expanduser().resolve()
    pa = portraits[0] if portraits else hero
    pb = portraits[1] if len(portraits) > 1 else pa
    return hero, pa, pb


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Gate 2 4-Card Style-Pack Carousel PNGs (--style-ids) or legacy color-look previews."
    )
    parser.add_argument("--style-ids", help="Comma-separated Style Pack IDs (e.g. cinema_doc,sitcom_cast,kinetic_montage,analog_scrapbook).")
    parser.add_argument("--styles-dir", help="Directory containing <style_id>.json presets (default: ../styles).")
    parser.add_argument("--manifest", help="Path to ./work/manifest.json to auto-pick hero and portrait media.")
    parser.add_argument("--hero", "--hero-image", dest="hero", help="Explicit hero photo or video path.")
    parser.add_argument("--portrait-a", help="First portrait photo or video path.")
    parser.add_argument("--portrait-b", help="Second portrait photo or video path.")
    parser.add_argument("--trip-title", default="Trip Cinema", help="Trip title printed on each Style Preview Card.")
    parser.add_argument("--out-dir", default=None, help="Output directory (default: ./work/style_previews with --style-ids, else ./work/look_previews).")
    args = parser.parse_args()

    hero = Path(args.hero).expanduser().resolve() if args.hero else None
    pa = Path(args.portrait_a).expanduser().resolve() if args.portrait_a else None
    pb = Path(args.portrait_b).expanduser().resolve() if args.portrait_b else None

    if args.manifest:
        m_hero, m_pa, m_pb = _pick_from_manifest(Path(args.manifest).expanduser().resolve(), allow_videos=bool(args.style_ids))
        hero = hero or m_hero
        pa = pa or m_pa
        pb = pb or m_pb

    if hero is None or not hero.exists():
        raise SystemExit("Error: Provide --hero / --hero-image or a valid --manifest with at least one media file.")

    pa = pa if (pa and pa.exists()) else hero
    pb = pb if (pb and pb.exists()) else pa

    use_ffmpeg = _has_ffmpeg()
    avail_filters = _probe_filters() if use_ffmpeg else set()

    # ---- v0.2.0 4-Card Style-Pack Carousel Mode ----
    if args.style_ids:
        style_ids = [s.strip() for s in args.style_ids.split(",") if s.strip()]
        if not style_ids:
            raise SystemExit("Error: --style-ids must specify at least one Style Pack ID.")
        styles_dir = (
            Path(args.styles_dir).expanduser().resolve()
            if args.styles_dir
            else (Path(__file__).resolve().parent.parent / "styles")
        )
        out_dir = Path(args.out_dir or "./work/style_previews").expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        hero_img = _decode_upright_frame_pil(hero, avail_filters, use_ffmpeg)
        pa_img = _decode_upright_frame_pil(pa, avail_filters, use_ffmpeg)
        pb_img = _decode_upright_frame_pil(pb, avail_filters, use_ffmpeg)

        cards_out: list[dict[str, Any]] = []
        for sid in style_ids:
            sp_path = styles_dir / f"{sid}.json"
            if not sp_path.is_file():
                raise SystemExit(f"Error: Style pack JSON not found: {sp_path}")
            style_pack = json.loads(sp_path.read_text(encoding="utf-8"))
            card_path = out_dir / f"style_{sid}.png"
            _render_style_preview_card(
                style_pack=style_pack,
                hero_raw=hero_img,
                pa_raw=pa_img,
                pb_raw=pb_img,
                trip_title=args.trip_title,
                out_path=card_path,
                use_ffmpeg=use_ffmpeg,
            )
            cards_out.append(
                {
                    "style_id": sid,
                    "display_name": style_pack.get("display_name", sid),
                    "default_portrait_layout": (style_pack.get("layouts") or {}).get("default_portrait_layout"),
                    "target_bpm": (style_pack.get("pacing_rules") or {}).get("target_bpm_label"),
                    "path": str(card_path),
                    "width": 1280,
                    "height": 720,
                    "status": "ok",
                }
            )

        report = {
            "mode": "style_pack_carousel",
            "trip_title": args.trip_title,
            "hero_source": str(hero),
            "portrait_a_source": str(pa),
            "portrait_b_source": str(pb),
            "renderer": "ffmpeg+pillow" if use_ffmpeg else "pillow-fallback",
            "cards": cards_out,
        }
        (out_dir / "style_previews.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0

    # ---- Legacy v0.1.0 Color-Look Fallback Mode ----
    out_dir = Path(args.out_dir or "./work/look_previews").expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rendered_looks: list[dict[str, str]] = []
    for key, recipe in LOOK_RECIPES.items():
        dst = out_dir / f"look_{key}.jpg"
        ok = _render_look_ffmpeg(hero, dst, recipe["ffmpeg_vf"]) if use_ffmpeg else _render_look_pil(hero, dst, recipe)
        if not ok and use_ffmpeg:
            ok = _render_look_pil(hero, dst, recipe)
        rendered_looks.append(
            {
                "look_id": key,
                "label": recipe["label"],
                "path": str(dst) if ok else "",
                "status": "ok" if ok else "failed",
            }
        )

    comp_path = out_dir / "vertical_layout_comparison.jpg"
    comp_ok = _render_vertical_comparison(pa, pb, comp_path, use_ffmpeg)

    report = {
        "mode": "legacy_looks",
        "hero_source": str(hero),
        "renderer": "ffmpeg" if use_ffmpeg else "pillow-fallback",
        "looks": rendered_looks,
        "vertical_comparison": str(comp_path) if comp_ok else None,
    }
    (out_dir / "look_previews.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
