#!/usr/bin/env python3
"""BGM beat/downbeat/energy analyzer and Gate 3 track catalog helper.

Subcommands:
  analyze  Extract BPM, beats, downbeats, and energy drops to `beat_map.json`
           using `beat_this` -> `librosa` -> pure-Python WAV/FFmpeg fallback.
  catalog  Emit open-source/CC0 track candidates + `gemini-api music`
           (`lyria-3.5`) preview metadata for the Gate 3 BGM Picker Artifact.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any


CC0_CATALOG: list[dict[str, Any]] = [
    {
        "id": "cc0_golden_horizon",
        "title": "Golden Horizon (Acoustic Indie Folk)",
        "license": "CC0 / Public Domain",
        "source_hint": "FreePD / Pixabay Audio / Musopen",
        "approx_bpm": 108,
        "mood": "warm, nostalgic, sunlit roadtrip",
        "cost_usd": 0.0,
    },
    {
        "id": "cc0_coastal_drift",
        "title": "Coastal Drift (Ambient Downtempo)",
        "license": "CC0 / Public Domain",
        "source_hint": "FreePD / Incompetech CC0",
        "approx_bpm": 92,
        "mood": "reflective, airy, scenic landscapes",
        "cost_usd": 0.0,
    },
    {
        "id": "cc0_city_pulse",
        "title": "City Pulse (Upbeat Electronic)",
        "license": "CC0 / Public Domain",
        "source_hint": "FreePD / YouTube Audio Library (No Attribution)",
        "approx_bpm": 120,
        "mood": "energetic, street markets, transitions",
        "cost_usd": 0.0,
    },
]

LYRIA_PRESETS: list[dict[str, Any]] = [
    {
        "id": "lyria_warm_acoustic",
        "model": "lyria-3.5",
        "cli_example": (
            'gemini-api music --model lyria-3.5 --prompt '
            '"Warm instrumental acoustic guitar and soft felt piano, nostalgic travel film score, 104 BPM, no vocals" '
            '--output ./work/bgm_lyria_warm.wav'
        ),
        "estimated_cost_usd": 0.08,
        "mood": "warm, human, acoustic",
    },
    {
        "id": "lyria_cinematic_ambient",
        "model": "lyria-3.5",
        "cli_example": (
            'gemini-api music --model lyria-3.5 --prompt '
            '"Cinematic indie travel score with swelling strings, analog synth pad, and crisp percussion drop at 30s, 112 BPM, instrumental" '
            '--output ./work/bgm_lyria_cinematic.wav'
        ),
        "estimated_cost_usd": 0.08,
        "mood": "epic vistas, dynamic build",
    },
]


def _get_audio_duration_ffprobe(audio_path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return round(float(proc.stdout.strip()), 3)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _analyze_with_beat_this(audio_path: Path) -> dict[str, Any] | None:
    try:
        from beat_this.inference import File2Beats  # type: ignore

        file2beats = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)
        beats, downbeats = file2beats(str(audio_path))
        beat_list = [round(float(b), 3) for b in beats]
        down_list = [round(float(d), 3) for d in downbeats]
        bpm = 0.0
        if len(beat_list) >= 2:
            diffs = [beat_list[i + 1] - beat_list[i] for i in range(len(beat_list) - 1)]
            avg_ibi = sum(diffs) / len(diffs)
            if avg_ibi > 0:
                bpm = round(60.0 / avg_ibi, 2)
        return {
            "backend": "beat_this",
            "bpm": bpm,
            "beats_sec": beat_list,
            "downbeats_sec": down_list,
            "energy_drops_sec": down_list[::4][:8],
        }
    except Exception:  # noqa: BLE001
        return None


def _analyze_with_librosa(audio_path: Path) -> dict[str, Any] | None:
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore

        y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
        duration = float(len(y) / sr) if sr else 0.0
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        bpm_val = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
        beats = [round(float(t), 3) for t in librosa.frames_to_time(beat_frames, sr=sr)]
        downbeats = beats[::4]

        rms = librosa.feature.rms(y=y)[0]
        rms_times = librosa.frames_to_time(range(len(rms)), sr=sr)
        # Detect sharp upward energy jumps (drops) across ~1.5s windows
        drops: list[float] = []
        win = max(1, int(1.5 * sr / 512))
        if len(rms) > win * 2:
            diffs = [float(np.mean(rms[i : i + win]) - np.mean(rms[i - win : i])) for i in range(win, len(rms) - win)]
            mean_d = float(np.mean(diffs))
            std_d = float(np.std(diffs))
            thresh = mean_d + 1.75 * std_d
            last_drop = -10.0
            for idx, val in enumerate(diffs):
                t = float(rms_times[idx + win])
                if val > thresh and (t - last_drop) >= 8.0:
                    drops.append(round(t, 3))
                    last_drop = t

        return {
            "backend": "librosa",
            "duration_sec": round(duration, 3),
            "bpm": round(bpm_val, 2),
            "beats_sec": beats,
            "downbeats_sec": downbeats,
            "energy_drops_sec": drops[:10],
        }
    except Exception:  # noqa: BLE001
        return None


def _read_wav_mono_samples(wav_path: Path) -> tuple[list[float], int]:
    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth != 2:
        raise ValueError("Only 16-bit PCM WAV supported in pure-Python fallback.")
    total_samples = len(raw) // 2
    ints = struct.unpack(f"<{total_samples}h", raw)
    mono: list[float] = []
    scale = 32768.0
    if n_channels == 1:
        mono = [x / scale for x in ints]
    else:
        for i in range(0, total_samples, n_channels):
            mono.append(sum(ints[i : i + n_channels]) / (n_channels * scale))
    return mono, sr


def _analyze_pure_python_fallback(audio_path: Path) -> dict[str, Any]:
    """Convert to 16-bit mono WAV via FFmpeg if needed, then estimate BPM & drops."""
    tmp_wav: Path | None = None
    wav_to_read = audio_path
    if audio_path.suffix.lower() != ".wav":
        fd, tmp_name = tempfile.mkstemp(suffix=".wav")
        Path(tmp_name).unlink(missing_ok=True)
        tmp_wav = Path(tmp_name)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-ac", "1", "-ar", "11025", "-sample_fmt", "s16", str(tmp_wav)],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0 or not tmp_wav.exists():
            raise RuntimeError("Pure-Python fallback requires a .wav file or working ffmpeg.")
        wav_to_read = tmp_wav

    try:
        samples, sr = _read_wav_mono_samples(wav_to_read)
    finally:
        if tmp_wav and tmp_wav.exists():
            tmp_wav.unlink(missing_ok=True)

    duration = len(samples) / sr if sr else 0.0
    hop = max(1, int(sr * 0.02))  # 50 Hz envelope
    env: list[float] = []
    for i in range(0, len(samples), hop):
        chunk = samples[i : i + hop]
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) if chunk else 0.0
        env.append(rms)

    # Half-wave rectified onset flux
    flux = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]
    env_sr = sr / hop

    # Autocorrelation over 70..160 BPM
    best_bpm = 110.0
    best_score = -1.0
    for bpm in range(70, 161):
        lag = int(round((60.0 / bpm) * env_sr))
        if lag <= 0 or lag >= len(flux):
            continue
        score = sum(flux[i] * flux[i + lag] for i in range(len(flux) - lag))
        if score > best_score:
            best_score = score
            best_bpm = float(bpm)

    ibi = 60.0 / best_bpm
    # Find first strong onset in first 2 seconds to anchor beat grid
    first_win = min(len(flux), int(2.0 * env_sr))
    anchor_idx = max(range(first_win), key=lambda i: flux[i]) if first_win > 0 else 0
    anchor_sec = anchor_idx / env_sr

    beats: list[float] = []
    t = anchor_sec
    while t < duration:
        beats.append(round(t, 3))
        t += ibi
    downbeats = beats[::4]

    # Energy drops over 1.5s windows
    win = max(1, int(1.5 * env_sr))
    drops: list[float] = []
    if len(env) > win * 2:
        diffs = [
            (sum(env[i : i + win]) / win) - (sum(env[i - win : i]) / win)
            for i in range(win, len(env) - win)
        ]
        mean_d = sum(diffs) / len(diffs)
        var_d = sum((d - mean_d) ** 2 for d in diffs) / len(diffs)
        thresh = mean_d + 1.8 * math.sqrt(var_d)
        last_t = -10.0
        for idx, val in enumerate(diffs):
            cur_t = (idx + win) / env_sr
            if val > thresh and (cur_t - last_t) >= 8.0:
                drops.append(round(cur_t, 3))
                last_t = cur_t

    return {
        "backend": "pure-python-spectral-flux",
        "duration_sec": round(duration, 3),
        "bpm": round(best_bpm, 2),
        "beats_sec": beats,
        "downbeats_sec": downbeats,
        "energy_drops_sec": drops[:10],
    }


def analyze_bgm(audio_path: Path, output_path: Path) -> dict[str, Any]:
    """Run tiered beat extraction and write `beat_map.json`."""
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    result = _analyze_with_beat_this(audio_path)
    if result is None:
        result = _analyze_with_librosa(audio_path)
    if result is None:
        result = _analyze_pure_python_fallback(audio_path)

    if "duration_sec" not in result:
        result["duration_sec"] = _get_audio_duration_ffprobe(audio_path)

    payload = {
        "schema_version": "1.0",
        "source_audio": str(audio_path.resolve()),
        **result,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze BGM beat/drop structure or emit Gate 3 CC0 + Lyria 3.5 catalog."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_an = sub.add_parser("analyze", help="Analyze a local audio file and write beat_map.json.")
    p_an.add_argument("--input", "-i", required=True, help="Path to MP3/WAV/M4A BGM file.")
    p_an.add_argument("--output", "-o", default="./work/beat_map.json", help="Output beat_map.json path.")

    p_cat = sub.add_parser("catalog", help="Output CC0 track candidates and Lyria 3.5 preview commands.")
    p_cat.add_argument("--output", "-o", help="Optional JSON file path to save catalog.")

    args = parser.parse_args()

    if args.command == "analyze":
        res = analyze_bgm(Path(args.input).expanduser().resolve(), Path(args.output).resolve())
        summary = {
            "backend": res["backend"],
            "bpm": res["bpm"],
            "duration_sec": res.get("duration_sec"),
            "beat_count": len(res["beats_sec"]),
            "downbeat_count": len(res["downbeats_sec"]),
            "energy_drops_sec": res["energy_drops_sec"],
            "output": str(Path(args.output).resolve()),
        }
        print(json.dumps(summary, indent=2))
        return 0

    catalog = {
        "cc0_candidates": CC0_CATALOG,
        "lyria_35_previews": LYRIA_PRESETS,
    }
    text = json.dumps(catalog, indent=2)
    if args.output:
        out_p = Path(args.output).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
