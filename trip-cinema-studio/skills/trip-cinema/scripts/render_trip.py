#!/usr/bin/env python3
"""Deterministic 16:9 FFmpeg master renderer and QA extractor for trip-cinema-studio.

Implements late-2026 best practices:
  - HDR-to-SDR Rec.709 Mobius tone-mapping via zscale/tonemap
  - Optional 2-pass vidstab stabilization
  - 2x supersampled zoompan Ken Burns anchored to `focal_anchor`
  - 16:9 vertical handling (`split_diptych_pair` or `blurred_bg_pillarbox`)
  - Subtle temporal film grain (`noise=c0s=8:c0f=t+u`)
  - Clean cuts / xfades and J/L audio overlaps (`acrossfade`)
  - Two-pass `-14 LUFS` `LRA=9` `linear=true` EBU R128 mastering
  - `--extract-qa` mode for `-sseof -0.05` end-frame sheets and <=60s review chunks
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


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _probe_filters() -> set[str]:
    proc = _run(["ffmpeg", "-hide_banner", "-filters"], timeout=10)
    return set(re.findall(r"\b(zscale|vidstabdetect|vidstabtransform|acrossfade|loudnorm|xfade)\b", proc.stdout or ""))


def _probe_video_info(path: Path) -> dict[str, Any]:
    proc = _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        timeout=15,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"has_audio": False, "is_hdr": False}
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    transfer = (v.get("color_transfer") or "").lower()
    return {
        "has_audio": has_audio,
        "is_hdr": transfer in {"arib-std-b67", "smpte2084"},
    }


def _render_ken_burns_shot(
    shot: dict[str, Any],
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    look_vf: str,
) -> list[str]:
    """Build 2x-supersampled zoompan Ken Burns command anchored to `focal_anchor`."""
    dur = max(0.5, float(shot["out_sec"]) - float(shot.get("in_sec", 0.0)))
    frames = max(1, int(round(dur * fps)))
    anchor = shot.get("focal_anchor") or [0.5, 0.5]
    fx = min(0.95, max(0.05, float(anchor[0])))
    fy = min(0.95, max(0.05, float(anchor[1])))

    super_w = width * 2
    super_h = height * 2
    direction = shot.get("ken_burns_dir", "in")
    if direction == "out":
        z_expr = f"max(1.0,1.15-0.15*on/{frames})"
    else:
        z_expr = f"min(1.15,1.0+0.15*on/{frames})"

    x_expr = f"max(0,min(iw-iw/zoom,(iw-iw/zoom)*{fx:.3f}))"
    y_expr = f"max(0,min(ih-ih/zoom,(ih-ih/zoom)*{fy:.3f}))"

    vf = (
        f"scale={super_w}:{super_h}:force_original_aspect_ratio=increase,"
        f"crop={super_w}:{super_h},setsar=1,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={width}x{height}:fps={fps},"
        f"{look_vf},noise=c0s=8:c0f=t+u,format=yuv420p"
    )
    return [
        "ffmpeg", "-y", "-loop", "1", "-i", shot["source_path"],
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
        "-t", f"{dur:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out_clip),
    ]


def _render_portrait_shot(
    shot: dict[str, Any],
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    look_vf: str,
) -> list[str]:
    """Render portrait photo/video in 16:9 via diptych or blurred pillarbox."""
    dur = max(0.5, float(shot["out_sec"]) - float(shot.get("in_sec", 0.0)))
    handling = shot.get("aspect_handling", "blurred_bg_pillarbox")
    half_w = width // 2

    if handling == "split_diptych_pair" and shot.get("pair_source_path"):
        fc = (
            f"[0:v]scale={half_w}:{height}:force_original_aspect_ratio=increase,crop={half_w}:{height},setsar=1[a];"
            f"[1:v]scale={half_w}:{height}:force_original_aspect_ratio=increase,crop={half_w}:{height},setsar=1[b];"
            f"[a][b]hstack=inputs=2,{look_vf},noise=c0s=8:c0f=t+u,format=yuv420p[v]"
        )
        return [
            "ffmpeg", "-y",
            "-loop", "1", "-t", f"{dur:.3f}", "-i", shot["source_path"],
            "-loop", "1", "-t", f"{dur:.3f}", "-i", shot["pair_source_path"],
            "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", fc,
            "-map", "[v]", "-map", "2:a",
            "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(out_clip),
        ]

    # Default: blurred_bg_pillarbox
    fc = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        f"gblur=sigma=28,eq=brightness=-0.12[bg];"
        f"[0:v]scale=-2:{height}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,{look_vf},noise=c0s=8:c0f=t+u,format=yuv420p[v]"
    )
    is_photo = shot.get("media_type", "photo") == "photo"
    in_args = ["-loop", "1", "-t", f"{dur:.3f}", "-i", shot["source_path"]] if is_photo else [
        "-ss", f"{float(shot.get('in_sec', 0.0)):.3f}", "-t", f"{dur:.3f}", "-i", shot["source_path"]
    ]
    return [
        "ffmpeg", "-y",
        *in_args,
        "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", fc,
        "-map", "[v]", "-map", "1:a",
        "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
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
    avail_filters: set[str],
    stabilize: bool,
    work_dir: Path,
) -> list[list[str]]:
    """Build command(s) for a video shot, including HDR tone-mapping and optional vidstab."""
    in_sec = float(shot.get("in_sec", 0.0))
    dur = max(0.2, float(shot["out_sec"]) - in_sec)
    src = Path(shot["source_path"])
    info = _probe_video_info(src)
    is_hdr = bool(shot.get("is_hdr", False)) or info["is_hdr"]

    vf_parts: list[str] = []
    if is_hdr and "zscale" in avail_filters:
        vf_parts.append(HDR_TONEMAP_CHAIN)

    cmds: list[list[str]] = []
    if stabilize and "vidstabdetect" in avail_filters and "vidstabtransform" in avail_filters:
        trf_path = work_dir / f"{out_clip.stem}.trf"
        cmds.append([
            "ffmpeg", "-y", "-ss", f"{in_sec:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-vf", f"vidstabdetect=shakiness=5:accuracy=15:result={trf_path}",
            "-f", "null", "-",
        ])
        vf_parts.append(f"vidstabtransform=input={trf_path}:smoothing=15:zoom=0:optzoom=1")

    vf_parts.append(f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1")
    vf_parts.append(look_vf)
    vf_parts.append("noise=c0s=8:c0f=t+u,format=yuv420p")
    vf = ",".join(vf_parts)

    if info["has_audio"]:
        cmds.append([
            "ffmpeg", "-y", "-ss", f"{in_sec:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-vf", vf, "-r", str(fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
            str(out_clip),
        ])
    else:
        cmds.append([
            "ffmpeg", "-y", "-ss", f"{in_sec:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-vf", vf, "-map", "0:v", "-map", "1:a", "-r", str(fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(out_clip),
        ])
    return cmds


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

    # Probe duration for <=60s chunks
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
    """Render all EDL shots, assemble timeline with J/L acrossfade & BGM, and master."""
    clips_dir = work_dir / "rendered_shots"
    clips_dir.mkdir(parents=True, exist_ok=True)
    avail_filters = _probe_filters() if not dry_run else {"zscale", "vidstabdetect", "vidstabtransform", "acrossfade", "loudnorm"}

    look_id = edl.get("look_id", "clean_natural")
    look_vf = LOOK_FILTERS.get(look_id, LOOK_FILTERS["clean_natural"])
    shots = edl.get("shots", [])
    if not shots:
        raise ValueError("EDL contains no shots.")

    planned_cmds: list[list[str]] = []
    clip_paths: list[Path] = []

    for idx, shot in enumerate(shots):
        clip_out = clips_dir / f"shot_{idx + 1:03d}.mp4"
        clip_paths.append(clip_out)
        mtype = shot.get("media_type", "photo")
        orient = shot.get("orientation", "landscape")

        if orient == "portrait":
            cmds = [_render_portrait_shot(shot, clip_out, width, height, fps, look_vf)]
        elif mtype == "photo":
            cmds = [_render_ken_burns_shot(shot, clip_out, width, height, fps, look_vf)]
        else:
            use_stab = stabilize or bool(shot.get("stabilize", False))
            cmds = _render_video_shot(shot, clip_out, width, height, fps, look_vf, avail_filters, use_stab, clips_dir)

        for cmd in cmds:
            planned_cmds.append(cmd)
            if not dry_run:
                res = _run(cmd, timeout=300)
                if res.returncode != 0:
                    raise RuntimeError(f"Shot {idx + 1} render failed:\n{res.stderr}")

    # Concatenate video & apply J/L audio acrossfade when multiple shots exist
    concat_txt = clips_dir / "concat.txt"
    concat_txt.write_text("".join(f"file '{p.resolve()}'\n" for p in clip_paths), encoding="utf-8")
    raw_timeline = work_dir / "timeline_raw.mp4"

    if len(clip_paths) == 1:
        assemble_cmd = ["ffmpeg", "-y", "-i", str(clip_paths[0]), "-c", "copy", str(raw_timeline)]
    else:
        # Video concat + J/L audio acrossfade chain (0.25s smooth audio overlap)
        inputs: list[str] = []
        for p in clip_paths:
            inputs.extend(["-i", str(p)])
        v_in = "".join(f"[{i}:v]" for i in range(len(clip_paths)))
        fc_parts = [f"{v_in}concat=n={len(clip_paths)}:v=1:a=0[vout]"]
        prev_a = "[0:a]"
        for i in range(1, len(clip_paths)):
            nxt = f"[a{i}]" if i < len(clip_paths) - 1 else "[aout]"
            fc_parts.append(f"{prev_a}[{i}:a]acrossfade=d=0.25:c1=tri:c2=tri{nxt}")
            prev_a = nxt
        assemble_cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(fc_parts),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "256k",
            str(raw_timeline),
        ]
    planned_cmds.append(assemble_cmd)
    if not dry_run:
        res = _run(assemble_cmd, timeout=300)
        if res.returncode != 0:
            raise RuntimeError(f"Timeline assembly failed:\n{res.stderr}")

    # Optional BGM mix
    bgm_path = edl.get("bgm_path")
    pre_master = raw_timeline
    if bgm_path and Path(bgm_path).expanduser().exists():
        mixed_av = work_dir / "timeline_bgm_mixed.mp4"
        mix_cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_timeline),
            "-stream_loop", "-1", "-i", str(Path(bgm_path).expanduser().resolve()),
            "-filter_complex", "[0:a]volume=0.85[amb];[1:a]volume=0.65[bgm];[amb][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
            str(mixed_av),
        ]
        planned_cmds.append(mix_cmd)
        if not dry_run:
            res = _run(mix_cmd, timeout=300)
            if res.returncode != 0:
                raise RuntimeError(f"BGM mix failed:\n{res.stderr}")
        pre_master = mixed_av

    output_master.parent.mkdir(parents=True, exist_ok=True)
    loudnorm_cmds = _two_pass_loudnorm(pre_master, output_master, dry_run=dry_run)
    planned_cmds.extend(loudnorm_cmds)

    return {
        "status": "dry_run" if dry_run else "rendered",
        "output_master": str(output_master),
        "shot_count": len(shots),
        "look_id": look_id,
        "command_count": len(planned_cmds),
        "commands": planned_cmds if dry_run else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic 16:9 FFmpeg master renderer and QA extractor.")
    parser.add_argument("--edl", help="Path to validated EDL JSON file.")
    parser.add_argument("--output", "-o", default="./work/trip_master_1080p.mp4", help="Output master MP4 path.")
    parser.add_argument("--work-dir", default="./work", help="Working directory for intermediate clips.")
    parser.add_argument("--width", type=int, default=1920, help="Output width (default: 1920; use 3840 for 4K).")
    parser.add_argument("--height", type=int, default=1080, help="Output height (default: 1080; use 2160 for 4K).")
    parser.add_argument("--fps", type=int, default=30, help="Output frame rate (default: 30).")
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
    report = render_trip(
        edl=edl,
        output_master=Path(args.output).expanduser().resolve(),
        work_dir=work_dir,
        width=args.width,
        height=args.height,
        fps=args.fps,
        stabilize=args.stabilize,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
