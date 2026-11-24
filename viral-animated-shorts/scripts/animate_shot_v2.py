#!/usr/bin/env python3
"""Animate still keyframes into 9:16 (1080x1920) or 16:9 (1920x1080) MP4 clips via 2x-supersampled FFmpeg zoompan.

Supports both Shorts (30-55s) and Long-Form Episodes (3-15+ min, 90-150+ shots) with parallel swarm slicing:
  - Computes cumulative shot_frames across the ENTIRE shot_timings.json FIRST (compute_global_shot_plans)
    so parallel slice workers stay frame-locked within +/-1 frame with zero drift.
  - Renders on a 2x working canvas (2160x3840 for 9:16, 3840x2160 for 16:9) -> Lanczos downscale
    to 1080x1920 or 1920x1080, 30 fps, libx264 (-preset fast -crf 18 -pix_fmt yuv420p), silent (-an).
  - Outputs 3-digit zero-padded filenames: <idx:03d>_<shot_id>.mp4 for deterministic LC_ALL=C sort past 99 shots.
  - Supports slicing via --start-idx / --end-idx (1-based inclusive), --shot-ids, or --act <N>,
    plus --workers <N> for ThreadPoolExecutor parallelism and --skip-existing for rerolls.

Motion presets (10):
  whip_zoom, bounce_pop, shake_zoom, zoom_in, zoom_out,
  pan_left, pan_right, static_drift, slow_dolly_in, tilt_up
  (Any unrecognized preset emits a warning and safely falls back to zoom_in.)

Examples:
  python3 animate_shot_v2.py --image scenes/shot_01.png --duration 3.2 --motion whip_zoom --flash-in --out clips/shot_01.mp4
  python3 animate_shot_v2.py --timings audio/shot_timings.json --scenes-dir scenes --out-dir clips
  python3 animate_shot_v2.py --timings audio/shot_timings.json --scenes-dir scenes --out-dir clips --aspect-ratio 16:9 --act 1 --workers 4 --fade-out-last
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Any, Optional

# Default 9:16 dimensions retained as module-level constants for backward compatibility
WORK_W, WORK_H = 2160, 3840
OUT_W, OUT_H = 1080, 1920

ASPECT_DIMS: dict[str, tuple[int, int, int, int]] = {
    "9:16": (2160, 3840, 1080, 1920),
    "16:9": (3840, 2160, 1920, 1080),
}

PRESETS = (
    "whip_zoom",
    "bounce_pop",
    "shake_zoom",
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "static_drift",
    "slow_dolly_in",
    "tilt_up",
)

_PRINT_LOCK = threading.Lock()


@dataclass(frozen=True)
class ShotPlan:
    idx: int
    shot_id: str
    duration_sec: float
    exact_frames: int
    cum_start_frame: int
    cum_end_frame: int
    motion: str
    flash_in: bool
    act: Optional[int]
    is_first: bool
    is_last: bool


def resolve_dimensions(aspect_ratio: str) -> tuple[int, int, int, int]:
    """Return (work_w, work_h, out_w, out_h) for the given aspect ratio."""
    if aspect_ratio not in ASPECT_DIMS:
        raise ValueError(f"Unsupported aspect ratio '{aspect_ratio}'. Expected one of: {', '.join(ASPECT_DIMS)}")
    return ASPECT_DIMS[aspect_ratio]


def resolve_motion(motion: str) -> str:
    """Validate motion preset; warn and fall back to zoom_in on unrecognized values."""
    if motion in PRESETS:
        return motion
    with _PRINT_LOCK:
        print(
            f"Warning: unrecognized motion preset '{motion}', falling back to 'zoom_in'.",
            file=sys.stderr,
        )
    return "zoom_in"


def build_zoompan_expr(
    motion: str,
    frames: int,
    work_w: int = WORK_W,
    work_h: int = WORK_H,
) -> tuple[str, str, str]:
    if motion not in PRESETS:
        motion = "zoom_in"

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
        # High-energy damped trauma shake + tight push-in, scaled proportionally to working canvas
        amp_x = max(1, int(round(34 * work_w / WORK_W)))
        amp_y = max(1, int(round(26 * work_h / WORK_H)))
        z = f"1.10+0.08*(on/{denom})"
        x = f"iw/2-(iw/zoom/2)+sin(on*2.3)*{amp_x}*exp(-on/18)"
        y = f"ih/2-(ih/zoom/2)+cos(on*3.1)*{amp_y}*exp(-on/18)"
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
    if motion == "slow_dolly_in":
        # Smooth dramatic 1.00 -> 1.10 push-in for emotional dialogue and reveals
        z = f"1.00+0.10*(on/{denom})"
        return z, cx, cy
    if motion == "tilt_up":
        # 1.14 zoom panning gently upward for throne/heroic reveals
        z = "1.14"
        y = f"(ih-ih/zoom)*(1-on/{denom})"
        return z, cx, y
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
    fade_in: bool = False,
    fade_out: bool = False,
    exact_frames: int = 0,
    aspect_ratio: str = "9:16",
) -> int:
    if not image_path.exists():
        raise FileNotFoundError(f"Keyframe not found: {image_path}")
    motion = resolve_motion(motion)
    work_w, work_h, out_w, out_h = resolve_dimensions(aspect_ratio)

    frames = exact_frames if exact_frames > 0 else max(1, int(round(duration * fps)))
    z_expr, x_expr, y_expr = build_zoompan_expr(motion, frames, work_w=work_w, work_h=work_h)

    filters = [
        f"scale={work_w}:{work_h}:force_original_aspect_ratio=increase",
        f"crop={work_w}:{work_h}",
        "setsar=1",
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={work_w}x{work_h}:fps={fps}",
        f"scale={out_w}:{out_h}:flags=lanczos",
    ]
    if flash_in or fade_in or fade_out:
        filters.append("setpts=PTS-STARTPTS")
    if flash_in:
        filters.append("eq=brightness='if(lt(t,0.07),0.55*(1-t/0.07),0)':eval=frame")
    if fade_in and frames > 1:
        fade_in_frames = max(1, min(frames // 2, int(round(0.4 * fps))))
        filters.append(f"fade=t=in:s=0:n={fade_in_frames}")
    if fade_out and frames > 1:
        fade_out_frames = max(1, min(frames - 1, int(round(0.6 * fps))))
        fade_start_frame = max(0, frames - fade_out_frames)
        filters.append(f"fade=t=out:s={fade_start_frame}:n={fade_out_frames}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Feed single still image (no -loop 1) so zoompan emits exactly `frames` frames with zero frame-1 reset jump
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(image_path),
        "-vf", ",".join(filters),
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return frames


def find_scene_image(scenes_dir: Path, shot_id: str) -> Path:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        cand = scenes_dir / f"{shot_id}{ext}"
        if cand.exists():
            return cand
    raise FileNotFoundError(f"No keyframe found for {shot_id} in {scenes_dir}")


def parse_act_value(val: Any) -> Optional[int]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        for prefix in ("act_", "act-", "act ", "chapter_", "chapter-", "chapter "):
            if s.startswith(prefix):
                s = s[len(prefix):].strip()
        if s.startswith("act") and s[3:].isdigit():
            s = s[3:]
        if s.startswith("chapter") and s[7:].isdigit():
            s = s[7:]
        if s.isdigit():
            return int(s)
    return None


def compute_global_shot_plans(
    shots: list[dict[str, Any]],
    fps: int,
    force_flash_in: bool = False,
    acts_meta: Optional[list[dict[str, Any]]] = None,
) -> list[ShotPlan]:
    """Compute exact cumulative frame boundaries across ALL shots before any slicing."""
    act_by_shot_id: dict[str, int] = {}
    if isinstance(acts_meta, list):
        for entry in acts_meta:
            if not isinstance(entry, dict):
                continue
            a_num = parse_act_value(
                entry.get("act", entry.get("act_number", entry.get("act_num", entry.get("chapter"))))
            )
            if a_num is None:
                continue
            for sid in entry.get("shot_ids", []) or []:
                if isinstance(sid, str):
                    act_by_shot_id[sid] = a_num

    plans: list[ShotPlan] = []
    cum_sec = 0.0
    cum_frames = 0
    total = len(shots)

    for idx, shot in enumerate(shots, start=1):
        sid = shot["shot_id"]
        dur = float(shot["duration_sec"])
        cum_sec += dur
        target_cum_frames = max(cum_frames + 1, int(round(cum_sec * fps)))
        shot_frames = target_cum_frames - cum_frames

        raw_motion = str(shot.get("motion_preset") or "zoom_in")
        flash = bool(shot.get("flash_in", False)) or force_flash_in
        act_val = parse_act_value(
            shot.get(
                "act",
                shot.get("act_number", shot.get("act_num", shot.get("act_id", shot.get("chapter")))),
            )
        )
        if act_val is None:
            act_val = act_by_shot_id.get(sid)

        plans.append(
            ShotPlan(
                idx=idx,
                shot_id=sid,
                duration_sec=dur,
                exact_frames=shot_frames,
                cum_start_frame=cum_frames,
                cum_end_frame=target_cum_frames,
                motion=raw_motion,
                flash_in=flash,
                act=act_val,
                is_first=(idx == 1),
                is_last=(idx == total),
            )
        )
        cum_frames = target_cum_frames

    return plans


def filter_plans(
    plans: list[ShotPlan],
    start_idx: Optional[int],
    end_idx: Optional[int],
    shot_ids_arg: Optional[str],
    act_arg: Optional[int],
) -> list[ShotPlan]:
    selected = plans
    if start_idx is not None:
        selected = [p for p in selected if p.idx >= start_idx]
    if end_idx is not None:
        selected = [p for p in selected if p.idx <= end_idx]
    if shot_ids_arg:
        wanted = {s.strip() for s in shot_ids_arg.split(",") if s.strip()}
        selected = [p for p in selected if p.shot_id in wanted]
    if act_arg is not None:
        selected = [p for p in selected if p.act == act_arg]
    return selected


def render_plan(
    plan: ShotPlan,
    scenes_dir: Path,
    out_dir: Path,
    fps: int,
    fade_in_first: bool,
    fade_out_last: bool,
    skip_existing: bool,
    aspect_ratio: str = "9:16",
) -> str:
    out_mp4 = out_dir / f"{plan.idx:03d}_{plan.shot_id}.mp4"
    if skip_existing and out_mp4.exists() and out_mp4.stat().st_size > 0:
        return (
            f"Skipped  [{plan.idx:03d}] {plan.shot_id} "
            f"({plan.exact_frames}f @ {plan.cum_start_frame}..{plan.cum_end_frame}) -> {out_mp4}"
        )

    img = find_scene_image(scenes_dir, plan.shot_id)
    resolved_motion = resolve_motion(plan.motion)
    do_fade_in = fade_in_first and plan.is_first
    do_fade_out = fade_out_last and plan.is_last

    animate_single(
        image_path=img,
        duration=plan.duration_sec,
        motion=resolved_motion,
        out_path=out_mp4,
        fps=fps,
        flash_in=plan.flash_in,
        fade_in=do_fade_in,
        fade_out=do_fade_out,
        exact_frames=plan.exact_frames,
        aspect_ratio=aspect_ratio,
    )
    return (
        f"Animated [{plan.idx:03d}] {plan.shot_id} "
        f"({plan.exact_frames}f / {plan.exact_frames / fps:.2f}s, cum={plan.cum_start_frame}..{plan.cum_end_frame}, "
        f"{aspect_ratio}, {resolved_motion}, flash={plan.flash_in}, fade_in={do_fade_in}, fade_out={do_fade_out}) -> {out_mp4}"
    )


def resolve_batch_aspect_ratio(cli_aspect: Optional[str], data: Any) -> str:
    if cli_aspect is not None:
        return cli_aspect
    if isinstance(data, dict):
        json_aspect = data.get("aspect_ratio")
        if isinstance(json_aspect, str) and json_aspect.strip():
            cand = json_aspect.strip()
            if cand in ASPECT_DIMS:
                return cand
            print(
                f"Warning: unrecognized aspect_ratio '{cand}' in shot_timings.json, defaulting to '9:16'.",
                file=sys.stderr,
            )
    return "9:16"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Animate still keyframes into 9:16 (1080x1920) or 16:9 (1920x1080) MP4 clips "
            "using 2x-supersampled FFmpeg zoompan."
        )
    )
    parser.add_argument("--image", help="Single keyframe image path.")
    parser.add_argument("--duration", type=float, help="Clip duration in seconds.")
    parser.add_argument(
        "--motion",
        default="zoom_in",
        help=f"Camera motion preset ({', '.join(PRESETS)}; unrecognized falls back to zoom_in).",
    )
    parser.add_argument("--flash-in", action="store_true", help="Add 2-frame white exposure flash on entry.")
    parser.add_argument(
        "--fade-in-first",
        action="store_true",
        help="Add subtle 0.4s fade-in on shot 1 (disabled by default for Frame 0 cold-open energy).",
    )
    parser.add_argument(
        "--fade-out-last",
        action="store_true",
        help="Add subtle 0.6s fade-to-black on the final shot.",
    )
    parser.add_argument(
        "--aspect-ratio",
        choices=list(ASPECT_DIMS.keys()),
        default=None,
        help="Target aspect ratio (9:16 or 16:9). Auto-detects from shot_timings.json if omitted, else defaults to 9:16.",
    )
    parser.add_argument("--fps", type=int, default=30, help="Output FPS (default: 30).")
    parser.add_argument("--out", help="Output MP4 path (single mode).")

    parser.add_argument("--timings", help="Path to shot_timings.json (batch/episode mode).")
    parser.add_argument("--scenes-dir", help="Directory containing <shot_id>.png/.jpg keyframes.")
    parser.add_argument("--out-dir", help="Output directory for rendered MP4 clips.")
    parser.add_argument("--start-idx", type=int, help="1-based inclusive start shot index.")
    parser.add_argument("--end-idx", type=int, help="1-based inclusive end shot index.")
    parser.add_argument("--shot-ids", help="Comma-separated list of shot_id values to render.")
    parser.add_argument("--act", type=int, help="Render only shots in the given positive act/chapter number.")
    parser.add_argument("--workers", type=int, default=1, help="ThreadPoolExecutor worker count (default: 1).")
    parser.add_argument("--skip-existing", action="store_true", help="Skip clips that already exist and are non-empty.")

    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg must be installed and on PATH.", file=sys.stderr)
        return 1

    if args.timings:
        if not args.scenes_dir or not args.out_dir:
            parser.error("--scenes-dir and --out-dir are required with --timings")
        if args.start_idx is not None and args.start_idx < 1:
            parser.error("--start-idx must be >= 1")
        if args.end_idx is not None and args.end_idx < 1:
            parser.error("--end-idx must be >= 1")
        if args.start_idx is not None and args.end_idx is not None and args.start_idx > args.end_idx:
            parser.error("--start-idx cannot exceed --end-idx")
        if args.act is not None and args.act < 1:
            parser.error("--act must be >= 1")
        if args.workers < 1:
            parser.error("--workers must be >= 1")

        data = json.loads(Path(args.timings).read_text(encoding="utf-8"))
        shots = data.get("shots", data) if isinstance(data, dict) else data
        acts_meta = data.get("acts") if isinstance(data, dict) else None
        if not isinstance(shots, list) or not shots:
            print(f"Error: no shots found in {args.timings}", file=sys.stderr)
            return 1

        aspect_ratio = resolve_batch_aspect_ratio(args.aspect_ratio, data)
        work_w, work_h, out_w, out_h = resolve_dimensions(aspect_ratio)

        # Global cumulative frame lock computed across ALL shots first
        plans = compute_global_shot_plans(
            shots,
            args.fps,
            force_flash_in=args.flash_in,
            acts_meta=acts_meta,
        )
        total_frames = plans[-1].cum_end_frame
        selected = filter_plans(plans, args.start_idx, args.end_idx, args.shot_ids, args.act)

        if not selected:
            print(
                f"Error: slice filters matched 0 of {len(plans)} shots in {args.timings}.",
                file=sys.stderr,
            )
            return 1

        scenes_dir = Path(args.scenes_dir)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        print(
            f"Global timeline locked: {len(plans)} shots, {total_frames} frames "
            f"({total_frames / args.fps:.2f}s @ {args.fps}fps, {aspect_ratio} {work_w}x{work_h}->{out_w}x{out_h}). "
            f"Rendering {len(selected)} shot(s) with workers={args.workers}."
        )

        if args.workers == 1:
            for plan in selected:
                msg = render_plan(
                    plan,
                    scenes_dir,
                    out_dir,
                    args.fps,
                    args.fade_in_first,
                    args.fade_out_last,
                    args.skip_existing,
                    aspect_ratio=aspect_ratio,
                )
                print(msg)
        else:
            errors: list[tuple[ShotPlan, Exception]] = []
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                future_map = {
                    pool.submit(
                        render_plan,
                        plan,
                        scenes_dir,
                        out_dir,
                        args.fps,
                        args.fade_in_first,
                        args.fade_out_last,
                        args.skip_existing,
                        aspect_ratio,
                    ): plan
                    for plan in selected
                }
                for fut in as_completed(future_map):
                    plan = future_map[fut]
                    try:
                        msg = fut.result()
                        with _PRINT_LOCK:
                            print(msg)
                    except Exception as exc:
                        errors.append((plan, exc))
            if errors:
                for plan, exc in errors:
                    print(f"Error rendering [{plan.idx:03d}] {plan.shot_id}: {exc}", file=sys.stderr)
                return 1
        return 0

    if args.image and args.duration and args.out:
        aspect_ratio = args.aspect_ratio or "9:16"
        out_path = Path(args.out)
        if args.skip_existing and out_path.exists() and out_path.stat().st_size > 0:
            print(f"Skipped {args.image} -> {args.out}")
            return 0
        resolved_motion = resolve_motion(args.motion)
        frames = animate_single(
            Path(args.image),
            args.duration,
            resolved_motion,
            out_path,
            fps=args.fps,
            flash_in=args.flash_in,
            fade_in=args.fade_in_first,
            fade_out=args.fade_out_last,
            aspect_ratio=aspect_ratio,
        )
        print(f"Animated {args.image} ({frames}f / {args.duration:.2f}s, {aspect_ratio}, {resolved_motion}) -> {args.out}")
        return 0

    parser.error("Use either (--image, --duration, --out) or (--timings, --scenes-dir, --out-dir)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
