#!/usr/bin/env python3
"""Gate 2 visual preview generator: 4 cinematic looks + vertical layout comparison.

Renders 4 color-grade variations (Warm Film, Moody Teal-Orange, Clean Natural,
Soft Atmospheric) from a trip hero photo plus a 2-up comparison strip of
`split_diptych_pair` vs `blurred_bg_pillarbox` for vertical 9:16 assets in 16:9.
Uses FFmpeg when available, with a graceful Pillow fallback.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


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
        # Left half (960x540): 2-up diptych; Right half (960x540): blurred pillarbox
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
            "ffmpeg",
            "-y",
            "-i",
            str(portrait_a),
            "-i",
            str(portrait_b),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(out_path),
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
        # Left 960x540: diptych
        strip.paste(ImageOps.fit(a, (478, 540)), (0, 0))
        strip.paste(ImageOps.fit(b, (478, 540)), (482, 0))

        # Right 960x540: blurred pillarbox
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


def _pick_from_manifest(manifest_path: Path) -> tuple[Path | None, Path | None, Path | None]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    photos = [a for a in data.get("assets", []) if a.get("media_type") == "photo"]
    if not photos:
        return None, None, None
    landscapes = [Path(a["source_path"]) for a in photos if a.get("orientation") == "landscape"]
    portraits = [Path(a["source_path"]) for a in photos if a.get("orientation") == "portrait"]
    hero = landscapes[0] if landscapes else Path(photos[0]["source_path"])
    pa = portraits[0] if portraits else hero
    pb = portraits[1] if len(portraits) > 1 else pa
    return hero, pa, pb


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate 4 cinematic color-grade previews + vertical layout comparison for Gate 2."
    )
    parser.add_argument("--manifest", help="Path to ./work/manifest.json to auto-pick hero and portrait photos.")
    parser.add_argument("--hero-image", help="Explicit hero photo path.")
    parser.add_argument("--portrait-a", help="First portrait photo for vertical layout comparison.")
    parser.add_argument("--portrait-b", help="Second portrait photo for vertical diptych comparison.")
    parser.add_argument("--out-dir", default="./work/look_previews", help="Output directory (default: ./work/look_previews).")
    args = parser.parse_args()

    hero = Path(args.hero_image).expanduser().resolve() if args.hero_image else None
    pa = Path(args.portrait_a).expanduser().resolve() if args.portrait_a else None
    pb = Path(args.portrait_b).expanduser().resolve() if args.portrait_b else None

    if args.manifest:
        m_hero, m_pa, m_pb = _pick_from_manifest(Path(args.manifest).expanduser().resolve())
        hero = hero or m_hero
        pa = pa or m_pa
        pb = pb or m_pb

    if hero is None or not hero.exists():
        raise SystemExit("Error: Provide --hero-image or a valid --manifest with at least one photo.")

    pa = pa if (pa and pa.exists()) else hero
    pb = pb if (pb and pb.exists()) else pa

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    use_ffmpeg = _has_ffmpeg()

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
