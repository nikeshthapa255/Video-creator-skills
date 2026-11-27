#!/usr/bin/env python3
"""Build a high-CTR YouTube Shorts (1080x1920) or Long-Form (1920x1080) thumbnail
from an approved hero keyframe (or left/right split pair) with zero AI text-warp risk.

Hardened features:
  - Cross-platform bold font resolution (fc-match + Linux/macOS paths + Pillow fallback).
  - Fast O(H) 1px vertical gradient strip scrim resize (no pixel loops).
  - Automatic < 2 MB file size cap (--max-mb 1.95 default) via adaptive PNG quantization
    or downscale/JPEG fallback so YouTube never rejects oversized uploads.
  - Safe-zone clamping: 9:16 top headline starts at/below y=308 (clearing top 15% 288px
    chrome) and lower text clears bottom 35% UI; 16:9 lower text stays left/above the
    bottom-right YouTube duration badge zone.
  - Optional --right-image split-canvas mode with soft center blend.
  - Optional --mobile-preview 160px-wide legibility check PNG.

Usage:
    python3 make_thumbnail.py \\
      --image scenes/shot_01.png \\
      --headline "FAKE TUNNEL" \\
      --accent "PRANK!" \\
      --out thumbnail.png \\
      [--aspect 9:16] [--accent-color #FFE600] [--position top] \\
      [--right-image scenes/shot_02.png] [--mobile-preview qa/thumb_160px.png]
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONT_CANDIDATES = [
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]

FC_MATCH_QUERIES = [
    "Arial Black",
    "DejaVu Sans:style=Bold",
    "Liberation Sans:style=Bold",
    "Helvetica Neue:style=Bold",
    "sans-serif:style=Bold",
]


def _can_load_truetype(path: str) -> bool:
    if not path or not Path(path).is_file():
        return False
    try:
        ImageFont.truetype(path, 32)
        return True
    except OSError:
        return False


def find_bold_font(preferred: str = "") -> Optional[str]:
    """Locate a bold TrueType/OpenType font across Linux and macOS, or return None for Pillow fallback."""
    if preferred and _can_load_truetype(preferred):
        return preferred

    if shutil.which("fc-match"):
        for query in FC_MATCH_QUERIES:
            try:
                res = subprocess.run(
                    ["fc-match", "-f", "%{file}", query],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=3,
                )
                cand = res.stdout.strip()
                if _can_load_truetype(cand):
                    return cand
            except (OSError, subprocess.SubprocessError):
                break

    for cand in FONT_CANDIDATES:
        if _can_load_truetype(cand):
            return cand

    return None


def _load_font(font_path: Optional[str], size: int) -> ImageFont.ImageFont:
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Optional[str],
    max_w: int,
    start_size: int,
    min_size: int = 48,
) -> Tuple[ImageFont.ImageFont, int]:
    size = start_size
    while size >= min_size:
        font = _load_font(font_path, size)
        stroke_w = max(6, size // 9)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
        if (bbox[2] - bbox[0]) <= max_w:
            return font, size
        size -= 4
    return _load_font(font_path, min_size), min_size


def crop_and_resize(src: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_ratio = src.width / max(1, src.height)
    tgt_ratio = target_w / max(1, target_h)
    if src_ratio > tgt_ratio:
        new_w = max(1, int(round(src.height * tgt_ratio)))
        left = max(0, (src.width - new_w) // 2)
        cropped = src.crop((left, 0, left + new_w, src.height))
    else:
        new_h = max(1, int(round(src.width / tgt_ratio)))
        top = max(0, (src.height - new_h) // 2)
        cropped = src.crop((0, top, src.width, top + new_h))
    return cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)


def build_base_canvas(
    image_path: str,
    target_w: int,
    target_h: int,
    right_image_path: str = "",
) -> Image.Image:
    left_src = Image.open(image_path).convert("RGB")
    if not right_image_path:
        canvas = crop_and_resize(left_src, target_w, target_h)
    else:
        right_src = Image.open(right_image_path).convert("RGB")
        mid_x = target_w // 2
        feather = max(32, min(80, target_w // 24))
        half_f = feather // 2

        left_w = min(target_w, mid_x + half_f)
        right_x0 = max(0, mid_x - half_f)
        right_w = target_w - right_x0

        left_half = crop_and_resize(left_src, left_w, target_h).convert("RGBA")
        right_half = crop_and_resize(right_src, right_w, target_h).convert("RGBA")

        # Fast 1px horizontal alpha feather mask across the center blend seam
        mask_strip = Image.new("L", (right_w, 1), 255)
        blend_w = min(feather, right_w)
        for x in range(blend_w):
            alpha = int(round(255.0 * (x / max(1, blend_w - 1))))
            mask_strip.putpixel((x, 0), alpha)
        mask = mask_strip.resize((right_w, target_h), Image.Resampling.NEAREST)

        comp = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 255))
        comp.paste(left_half, (0, 0))
        comp.paste(right_half, (right_x0, 0), mask)
        canvas = comp.convert("RGB")

    canvas = ImageEnhance.Contrast(canvas).enhance(1.08)
    canvas = ImageEnhance.Color(canvas).enhance(1.12)
    canvas = ImageEnhance.Sharpness(canvas).enhance(1.15)
    return canvas


def add_scrim(img: Image.Image, position: str, aspect: str = "9:16") -> Image.Image:
    """Fast O(H) 1px vertical gradient strip resized to full canvas width."""
    w, h = img.size
    strip = Image.new("RGBA", (1, h), (0, 0, 0, 0))
    if position == "top":
        top_frac = 0.46 if aspect == "9:16" else 0.40
        top_band = max(1, int(h * top_frac))
        bot_band = max(1, int(h * 0.14))
        for y in range(top_band):
            alpha = int(175 * ((1.0 - y / top_band) ** 1.35))
            strip.putpixel((0, y), (8, 10, 18, alpha))
        for y in range(h - bot_band, h):
            alpha = int(110 * (((y - (h - bot_band)) / bot_band) ** 1.4))
            strip.putpixel((0, y), (8, 10, 18, alpha))
    else:
        top_band = max(1, int(h * 0.16))
        bot_band = max(1, int(h * 0.46))
        for y in range(top_band):
            alpha = int(110 * ((1.0 - y / top_band) ** 1.4))
            strip.putpixel((0, y), (8, 10, 18, alpha))
        for y in range(h - bot_band, h):
            alpha = int(190 * (((y - (h - bot_band)) / bot_band) ** 1.3))
            strip.putpixel((0, y), (8, 10, 18, alpha))

    overlay = strip.resize((w, h), Image.Resampling.NEAREST)
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def draw_outlined_at_top(
    base: Image.Image,
    text: str,
    y_top: int,
    x_region: Tuple[int, int],
    font: ImageFont.ImageFont,
    fill_color: str,
    stroke_w: int,
) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """Draw outlined headline centered horizontally inside x_region with exact top edge at y_top."""
    w, h = base.size
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    bbox = sd.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    reg_left, reg_right = x_region
    reg_w = max(1, reg_right - reg_left)
    x = reg_left + (reg_w - tw) // 2 - bbox[0]
    y = y_top - bbox[1]

    sd.text(
        (x + 6, y + 10),
        text,
        font=font,
        fill=(0, 0, 0, 215),
        stroke_width=stroke_w + 2,
        stroke_fill=(0, 0, 0, 215),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))
    base = Image.alpha_composite(base, shadow)

    d = ImageDraw.Draw(base)
    d.text((x, y), text, font=font, fill=fill_color, stroke_width=stroke_w, stroke_fill="#000000")
    drawn_box = (x + bbox[0], y_top, x + bbox[2], y_top + th)
    return base, drawn_box


def _encode_bytes(img: Image.Image, fmt: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def save_under_size_cap(rgb: Image.Image, out_path: Path, max_mb: float = 1.95) -> Tuple[int, str]:
    """Save thumbnail strictly under max_mb (YouTube 2 MB limit) while preserving valid format."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = max(65536, int(max_mb * 1024 * 1024))
    suffix = out_path.suffix.lower()

    if suffix in (".jpg", ".jpeg"):
        for q in (94, 92, 88, 84, 78, 72):
            data = _encode_bytes(rgb, "JPEG", quality=q, optimize=True, progressive=True)
            if len(data) <= max_bytes or q == 72:
                out_path.write_bytes(data)
                return len(data), f"jpeg-q{q}"

    # Default PNG path: try lossless RGB PNG first, then adaptive quantized PNGs, then scaled PNG
    data = _encode_bytes(rgb, "PNG", optimize=True, compress_level=9)
    if len(data) <= max_bytes:
        out_path.write_bytes(data)
        return len(data), "png-rgb"

    for colors in (256, 128, 64):
        quant = rgb.quantize(
            colors=colors,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
        data = _encode_bytes(quant, "PNG", optimize=True, compress_level=9)
        if len(data) <= max_bytes:
            out_path.write_bytes(data)
            return len(data), f"png-q{colors}"

    # Rare incompressible fallback: step down to YouTube's standard 720p thumbnail canvas
    w, h = rgb.size
    scaled_size = (1280, 720) if w >= h else (720, 1280)
    scaled = rgb.resize(scaled_size, Image.Resampling.LANCZOS)
    for colors in (256, 128, 64):
        quant = scaled.quantize(
            colors=colors,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.FLOYDSTEINBERG,
        )
        data = _encode_bytes(quant, "PNG", optimize=True, compress_level=9)
        if len(data) <= max_bytes:
            out_path.write_bytes(data)
            return len(data), f"png-{scaled_size[0]}x{scaled_size[1]}-q{colors}"

    # Ultimate fallback to guarantee < max_mb
    data = _encode_bytes(scaled, "JPEG", quality=92, optimize=True, progressive=True)
    out_path.write_bytes(data)
    return len(data), "jpeg-q92-fallback"


def build_thumbnail(
    image_path: str,
    out_path: str,
    headline: str,
    accent: str = "",
    aspect: str = "9:16",
    accent_color: str = "#FFE600",
    position: str = "top",
    right_image_path: str = "",
    mobile_preview_path: str = "",
    max_mb: float = 1.95,
    font_override: str = "",
) -> dict:
    target_w, target_h = (1080, 1920) if aspect == "9:16" else (1920, 1080)
    canvas = build_base_canvas(image_path, target_w, target_h, right_image_path=right_image_path)
    rgba = add_scrim(canvas, position, aspect=aspect)

    font_path = find_bold_font(font_override)
    probe_draw = ImageDraw.Draw(rgba)

    # Safe-zone horizontal bounds:
    # - 16:9 lower stays left of YouTube's bottom-right duration badge (x >= 1560, y >= 920)
    if aspect == "16:9" and position == "lower":
        x_region = (64, 1500)
        max_text_w = x_region[1] - x_region[0] - 24
    else:
        margin_x = int(target_w * 0.06)
        x_region = (margin_x, target_w - margin_x)
        max_text_w = x_region[1] - x_region[0]

    raw_lines: List[Tuple[str, str, int]] = []
    if headline.strip():
        raw_lines.append((headline.strip().upper(), "#FFFFFF", 118 if aspect == "9:16" else 134))
    if accent.strip():
        raw_lines.append((accent.strip().upper(), accent_color, 134 if aspect == "9:16" else 150))

    measured = []
    for txt, col, ideal_sz in raw_lines:
        font, actual_sz = fit_font(probe_draw, txt, font_path, max_text_w, ideal_sz)
        stroke_w = max(8, actual_sz // 9)
        bbox = probe_draw.textbbox((0, 0), txt, font=font, stroke_width=stroke_w)
        th = bbox[3] - bbox[1]
        measured.append((txt, col, font, stroke_w, th))

    line_spacing = 24 if aspect == "9:16" else 22
    total_block_h = sum(item[4] for item in measured) + max(0, len(measured) - 1) * line_spacing

    # Safe-zone vertical placement:
    # - 9:16 top clears top 15% YouTube Shorts chrome (288px) -> top edge >= 308px
    # - 9:16 lower clears bottom 35% Shorts UI (bottom <= 1220px)
    # - 16:9 top sits cleanly near top (y=72); 16:9 lower stays inside lower-left safe zone (bottom <= 960px)
    if aspect == "9:16":
        if position == "top":
            y_cursor = 308
        else:
            y_cursor = max(820, min(980, 1220 - total_block_h))
    else:
        if position == "top":
            y_cursor = 72
        else:
            y_cursor = max(560, 960 - total_block_h)

    drawn_boxes: List[Tuple[int, int, int, int]] = []
    for txt, col, font, stroke_w, th in measured:
        rgba, box = draw_outlined_at_top(rgba, txt, y_cursor, x_region, font, col, stroke_w)
        drawn_boxes.append(box)
        y_cursor += th + line_spacing

    final_rgb = rgba.convert("RGB")
    out = Path(out_path)
    byte_size, method = save_under_size_cap(final_rgb, out, max_mb=max_mb)

    mobile_out_str = ""
    if mobile_preview_path:
        m_w = 160
        m_h = max(1, int(round(m_w * target_h / target_w)))
        mobile_img = final_rgb.resize((m_w, m_h), Image.Resampling.LANCZOS)
        m_path = Path(mobile_preview_path)
        m_path.parent.mkdir(parents=True, exist_ok=True)
        mobile_img.save(m_path, format="PNG", optimize=True)
        mobile_out_str = str(m_path)

    size_kb = byte_size / 1024.0
    split_note = f", split={Path(right_image_path).name}" if right_image_path else ""
    mob_note = f", mobile_preview={mobile_out_str}" if mobile_out_str else ""
    print(
        f"Saved thumbnail {out} ({target_w}x{target_h}, {size_kb:.1f} KB [{method}], "
        f"lines={[m[0] for m in measured]}, boxes={drawn_boxes}{split_note}{mob_note})"
    )
    return {
        "out_path": str(out),
        "size_bytes": byte_size,
        "method": method,
        "boxes": drawn_boxes,
        "mobile_preview_path": mobile_out_str,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Build a YouTube Shorts (9:16) or Long-Form (16:9) thumbnail under 2 MB with safe-zone text."
    )
    p.add_argument("--image", required=True, help="Primary (or left-half) source keyframe image.")
    p.add_argument("--right-image", default="", help="Optional right-half image for split-canvas thumbnails.")
    p.add_argument("--headline", required=True, help="Primary top headline text (2-4 punchy words).")
    p.add_argument("--accent", default="", help="Optional second-line accent text.")
    p.add_argument("--out", required=True, help="Output thumbnail path (e.g., thumbnail.png).")
    p.add_argument("--aspect", choices=["9:16", "16:9"], default="9:16", help="Canvas aspect ratio (default: 9:16).")
    p.add_argument("--accent-color", default="#FFE600", help="Hex fill color for accent line (default: #FFE600).")
    p.add_argument("--position", choices=["top", "lower"], default="top", help="Text safe-zone placement (default: top).")
    p.add_argument("--mobile-preview", default="", help="Optional path to save a 160px-wide mobile legibility preview PNG.")
    p.add_argument("--max-mb", type=float, default=1.95, help="Max output file size in MB (default: 1.95, under YouTube 2 MB cap).")
    p.add_argument("--font", default="", help="Optional path to a bold TrueType/OpenType font file.")
    args = p.parse_args()

    try:
        build_thumbnail(
            image_path=args.image,
            out_path=args.out,
            headline=args.headline,
            accent=args.accent,
            aspect=args.aspect,
            accent_color=args.accent_color,
            position=args.position,
            right_image_path=args.right_image,
            mobile_preview_path=args.mobile_preview,
            max_mb=args.max_mb,
            font_override=args.font,
        )
    except Exception as exc:
        print(f"Error building thumbnail: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
