#!/usr/bin/env python3
"""2x-supersampled FFmpeg keyframe animator for Mode B (Keyframe + Motion) Shorts.

Renders crisp 1080x1920 MP4 clips at exact spoken durations (from shot_timings.json)
by applying zoompan on a 2160x3840 (2x) working canvas and Lanczos downscaling to
eliminate 1-pixel integer jitter.

Motion presets:
  whip_zoom, bounce_pop, shake_zoom, zoom_in, zoom_out, pan_left, pan_right, static_drift

Examples:
  python3 animate_shot_v2.py --image scenes/shot_01.png --duration 3.2 --motion whip_zoom --flash-in --out clips/shot_01.mp4
  python3 animate_shot_v2.py --timings audio/shot_timings.json --scenes-dir scenes --out-dir clips
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

WORK_W, WORK_H = 2160, 3840
OUT_W, OUT_H = 1080, 1920

PRESETS = (
    "whip_zoom",
    "bounce_pop",
    "shake_zoom",
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "static_drift",
)


def build_zoompan_expr(motion: str, frames: int) -> tuple:
    denom = max(frames - 1, 1)
    cut = max(int(frames * 0.20), 4)
    rest = max(frames - cut, 1)

    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"

    if motion == "whip_zoom":
        # Fast punch-in settle (1.28 -> 1.04) over first 20%, then subtle forward drift
        z = f"if(lt(on,{cut}),1.28-0.24*(on/{cut}),1.04+0.05*((on-{cut})/{rest}))"
        return z, cx, cy
    if motion == "bounce_pop":
        # Elastic scale-pop overshoot on shot entry (1.02 -> 1.15 -> 1.05 -> 1.09)
        p1 = max(int(frames * 0.10), 3)
        p2 = max(int(frames * 0.22), p1 + 3)
        r2 = max(frames - p2, 1)
        z = (
            f"if(lt(on,{p1}),1.02+0.13*(on/{p1}),"
            f"if(lt(on,{p2}),1.15-0.10*((on-{p1})/{p2 - p1}),"
            f"1.05+0.04*((on-{p2})/{r2})))"
        )
        return z, cx, cy
    if motion == "shake_zoom":
        # High-energy damped trauma shake + tight push-in
        z = f"1.10+0.08*(on/{denom})"
        x = f"iw/2-(iw/zoom/2)+sin(on*2.3)*34*exp(-on/18)"
        y = f"ih/2-(ih/zoom/2)+cos(on*3.1)*26*exp(-on/18)"
        return z, x, y
    if motion == "zoom_in":
        z = f"1.00+0.12*(on/{denom})"
        return z, cx, cy
    if motion == "zoom_out":
        z = f"1.14-0.12*(on/{denom})"
        return z, cx, cy
    if motion == "pan_left":
        z = "1.14"
        x = f"(iw-iw/zoom)*(1-on/{denom})"
        return z, x, cy
    if motion == "pan_right":
        z = "1.14"
        x = f"(iw-iw/zoom)*(on/{denom})"
        return z, x, cy
    # static_drift
    z = f"1.02+0.03*(on/{denom})"
    return z, cx, cy


def animate_single(
    image_path: Path,
    duration: float,
    motion: str,
    out_path: Path,
    fps: int = 30,
    flash_in: bool = False,
) -> None:
    if not image_path.exists():
        raise FileNotFoundError(f"Keyframe not found: {image_path}")
    frames = max(1, int(round(duration * fps)))
    z_expr, x_expr, y_expr = build_zoompan_expr(motion, frames)

    filters = [
        f"scale={WORK_W}:{WORK_H}:force_original_aspect_ratio=increase",
        f"crop={WORK_W}:{WORK_H}",
        "setsar=1",
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={WORK_W}x{WORK_H}:fps={fps}",
        f"scale={OUT_W}:{OUT_H}:flags=lanczos",
    ]
    if flash_in:
        filters.append("eq=brightness='if(lt(t,0.07),0.55*(1-t/0.07),0)':eval=frame")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(image_path),
        "-vf", ",".join(filters),
        "-t", f"{duration:.4f}",
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def find_scene_image(scenes_dir: Path, shot_id: str) -> Path:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        cand = scenes_dir / f"{shot_id}{ext}"
        if cand.exists():
            return cand
    raise FileNotFoundError(f"No keyframe found for {shot_id} in {scenes_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description="2x-supersampled FFmpeg keyframe animator.")
    parser.add_argument("--image", help="Single keyframe image path.")
    parser.add_argument("--duration", type=float, help="Clip duration in seconds.")
    parser.add_argument("--motion", choices=PRESETS, default="zoom_in")
    parser.add_argument("--flash-in", action="store_true", help="Add 2-frame white exposure flash on entry.")
    parser.add_argument("--fps", type=int, default=30, help="Output FPS (default: 30).")
    parser.add_argument("--out", help="Output MP4 path (single mode).")
    parser.add_argument("--timings", help="Path to shot_timings.json (batch mode).")
    parser.add_argument("--scenes-dir", help="Directory containing <shot_id>.png/.jpg keyframes (batch mode).")
    parser.add_argument("--out-dir", help="Output directory for rendered MP4 clips (batch mode).")
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg must be installed and on PATH.", file=sys.stderr)
        return 1

    if args.timings:
        if not args.scenes_dir or not args.out_dir:
            parser.error("--scenes-dir and --out-dir are required with --timings")
        data = json.loads(Path(args.timings).read_text(encoding="utf-8"))
        shots = data.get("shots", data) if isinstance(data, dict) else data
        scenes_dir = Path(args.scenes_dir)
        out_dir = Path(args.out_dir)
        for idx, shot in enumerate(shots, start=1):
            sid = shot["shot_id"]
            dur = float(shot["duration_sec"])
            motion = shot.get("motion_preset", "zoom_in")
            if motion not in PRESETS:
                motion = "zoom_in"
            flash = bool(shot.get("flash_in", False))
            img = find_scene_image(scenes_dir, sid)
            out_mp4 = out_dir / f"{idx:02d}_{sid}.mp4"
            animate_single(img, dur, motion, out_mp4, fps=args.fps, flash_in=flash)
            print(f"Animated {sid} ({dur:.2f}s, {motion}, flash={flash}) -> {out_mp4}")
        return 0

    if args.image and args.duration and args.out:
        animate_single(
            Path(args.image),
            args.duration,
            args.motion,
            Path(args.out),
            fps=args.fps,
            flash_in=args.flash_in,
        )
        print(f"Animated {args.image} ({args.duration:.2f}s, {args.motion}) -> {args.out}")
        return 0

    parser.error("Use either (--image, --duration, --out) or (--timings, --scenes-dir, --out-dir)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
