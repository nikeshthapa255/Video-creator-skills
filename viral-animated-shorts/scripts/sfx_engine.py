#!/usr/bin/env python3
"""Procedural comedic SFX synthesizer and timeline renderer (48 kHz 16-bit stereo WAV).

Uses only the Python standard library (no third-party audio packages).
Supported cues:
  record_scratch, vine_boom, siren_chirp, bonk_boing,
  cash_ching, ding_levelup, laser_zap, whoosh, pop

Examples:
  python3 sfx_engine.py --list
  python3 sfx_engine.py --cue vine_boom --out boom.wav
  python3 sfx_engine.py --timings audio/shot_timings.json --out audio/sfx_track.wav
"""

import argparse
import json
import math
import random
import struct
import sys
import wave
import zlib
from pathlib import Path
from typing import Dict, List, Tuple

SR = 48000


def _adsr(t: float, dur: float, a: float = 0.005, d: float = 0.05) -> float:
    if t < 0.0 or t > dur:
        return 0.0
    if a > 0 and t < a:
        return t / a
    if d > 0 and t > dur - d:
        return max(0.0, (dur - t) / d)
    return 1.0


def _gen_vine_boom(rng: random.Random) -> List[float]:
    dur = 0.95
    n = int(SR * dur)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        freq = 32.0 + 108.0 * math.exp(-t * 9.5)
        phase += 2.0 * math.pi * freq / SR
        sub = math.sin(phase) + 0.35 * math.sin(phase * 2.0)
        click = (rng.uniform(-1.0, 1.0)) * math.exp(-t * 140.0) * 0.4
        env = math.exp(-t * 3.8) * _adsr(t, dur, a=0.003, d=0.08)
        out.append(math.tanh((sub * 1.45 + click) * env) * 0.92)
    return out


def _gen_record_scratch(rng: random.Random) -> List[float]:
    dur = 0.42
    n = int(SR * dur)
    out = []
    phase = 0.0
    prev_noise = 0.0
    for i in range(n):
        t = i / SR
        # Rapid upward then downward vinyl pitch scrub
        if t < 0.14:
            freq = 420.0 + 1600.0 * (t / 0.14)
        else:
            freq = 2020.0 * math.exp(-(t - 0.14) * 9.0) + 180.0
        phase += 2.0 * math.pi * freq / SR
        raw_n = rng.uniform(-1.0, 1.0)
        hp_noise = raw_n - 0.7 * prev_noise
        prev_noise = raw_n
        harmonic = math.sin(phase) + 0.5 * math.sin(phase * 2.7)
        env = _adsr(t, dur, a=0.004, d=0.06) * (0.45 + 0.55 * math.sin(math.pi * t / dur))
        out.append(math.tanh((harmonic * 0.65 + hp_noise * 0.45) * env) * 0.85)
    return out


def _gen_siren_chirp(rng: random.Random) -> List[float]:
    dur = 0.48
    n = int(SR * dur)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        # Two crisp alternating siren whoops
        cycle = (t * 4.2) % 1.0
        freq = 680.0 + 720.0 * (0.5 - 0.5 * math.cos(2.0 * math.pi * cycle))
        phase += 2.0 * math.pi * freq / SR
        sig = math.sin(phase) + 0.25 * math.sin(3.0 * phase)
        env = _adsr(t, dur, a=0.008, d=0.05)
        out.append(math.tanh(sig * 0.9) * env * 0.75)
    return out


def _gen_bonk_boing(rng: random.Random) -> List[float]:
    dur = 0.65
    n = int(SR * dur)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        # Initial hollow bonk + bouncy cartoon spring wobble
        bonk = math.sin(2.0 * math.pi * (340.0 * math.exp(-t * 25.0) + 110.0) * t) * math.exp(-t * 28.0)
        wobble_freq = 210.0 + 110.0 * math.sin(2.0 * math.pi * 13.5 * t) * math.exp(-t * 3.0) + 90.0 * t
        phase += 2.0 * math.pi * wobble_freq / SR
        spring = (math.sin(phase) + 0.3 * math.sin(2.0 * phase)) * math.exp(-t * 4.2)
        env = _adsr(t, dur, a=0.002, d=0.06)
        out.append(math.tanh((bonk * 0.9 + spring * 0.7) * env) * 0.85)
    return out


def _gen_cash_ching(rng: random.Random) -> List[float]:
    dur = 0.72
    n = int(SR * dur)
    out = []
    freqs = [2093.0, 3136.0, 4186.0, 5274.0]
    amps = [0.50, 0.35, 0.25, 0.15]
    for i in range(n):
        t = i / SR
        # Metallic drawer clack at t=0..0.06, bell ring at t>=0.05
        clack = rng.uniform(-1.0, 1.0) * math.exp(-t * 75.0) * 0.55
        bell = 0.0
        if t >= 0.045:
            tb = t - 0.045
            bell_env = math.exp(-tb * 5.2) * _adsr(tb, dur - 0.045, a=0.002, d=0.08)
            for f, a in zip(freqs, amps):
                bell += a * math.sin(2.0 * math.pi * f * tb)
            bell *= bell_env
        out.append(math.tanh(clack + bell * 1.1) * 0.82)
    return out


def _gen_ding_levelup(rng: random.Random) -> List[float]:
    notes = [1046.5, 1318.5, 1568.0, 2093.0]  # C6 E6 G6 C7
    step = 0.075
    dur = step * len(notes) + 0.38
    n = int(SR * dur)
    out = [0.0] * n
    for idx, freq in enumerate(notes):
        start_i = int(idx * step * SR)
        note_dur = 0.42 if idx == len(notes) - 1 else 0.18
        note_n = min(int(note_dur * SR), n - start_i)
        for j in range(note_n):
            t = j / SR
            env = math.exp(-t * (5.5 if idx == len(notes) - 1 else 14.0)) * _adsr(t, note_dur, a=0.003, d=0.03)
            sig = math.sin(2.0 * math.pi * freq * t) + 0.25 * math.sin(4.0 * math.pi * freq * t)
            out[start_i + j] += sig * env * 0.48
    return [math.tanh(x) * 0.82 for x in out]


def _gen_laser_zap(rng: random.Random) -> List[float]:
    dur = 0.28
    n = int(SR * dur)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        freq = 2600.0 * math.exp(-t * 11.5) + 140.0
        phase += 2.0 * math.pi * freq / SR
        # Band-limited-ish saw blend
        sig = math.sin(phase) + 0.45 * math.sin(2.0 * phase) + 0.2 * math.sin(3.0 * phase)
        env = _adsr(t, dur, a=0.002, d=0.04) * math.exp(-t * 4.0)
        out.append(math.tanh(sig * env) * 0.8)
    return out


def _gen_whoosh(rng: random.Random) -> List[float]:
    dur = 0.34
    n = int(SR * dur)
    out = []
    lp = 0.0
    lp2 = 0.0
    for i in range(n):
        t = i / SR
        # Bell-shaped energy swell with sweeping filter cutoff
        bell = math.sin(math.pi * t / dur) ** 1.6
        alpha = 0.05 + 0.35 * bell
        raw = rng.uniform(-1.0, 1.0)
        lp += alpha * (raw - lp)
        lp2 += (alpha * 0.6) * (lp - lp2)
        bp = lp - lp2 * 0.65
        out.append(math.tanh(bp * 2.8) * bell * 0.78)
    return out


def _gen_pop(rng: random.Random) -> List[float]:
    dur = 0.095
    n = int(SR * dur)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        freq = 210.0 + 520.0 * (t / dur)
        phase += 2.0 * math.pi * freq / SR
        env = math.exp(-t * 32.0) * _adsr(t, dur, a=0.002, d=0.015)
        out.append(math.tanh(math.sin(phase) * 1.4) * env * 0.85)
    return out


GENERATORS = {
    "vine_boom": _gen_vine_boom,
    "record_scratch": _gen_record_scratch,
    "siren_chirp": _gen_siren_chirp,
    "bonk_boing": _gen_bonk_boing,
    "cash_ching": _gen_cash_ching,
    "ding_levelup": _gen_ding_levelup,
    "laser_zap": _gen_laser_zap,
    "whoosh": _gen_whoosh,
    "pop": _gen_pop,
}


def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


def write_stereo_wav(samples: List[float], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        buf = bytearray()
        for s in samples:
            v = int(max(-1.0, min(1.0, math.tanh(s))) * 32767.0)
            buf.extend(struct.pack("<hh", v, v))
        wf.writeframes(bytes(buf))


def load_cues(timings_path: Path) -> Tuple[List[Dict], float]:
    data = json.loads(timings_path.read_text(encoding="utf-8"))
    cues: List[Dict] = []
    total_dur = 0.0

    if isinstance(data, dict):
        total_dur = float(data.get("total_duration_sec", 0.0))
        items = data.get("shots", data.get("cues", []))
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("Unsupported timings JSON format: expected an object with 'shots'/'cues' or a list of cues.")

    for item in items:
        sfx = item.get("sfx_type") or item.get("sfx") or item.get("sfx_cue") or "none"
        if not sfx or sfx == "none":
            continue
        base_t = float(item.get("time_sec", item.get("start_sec", 0.0)))
        offset = float(item.get("sfx_offset_sec", 0.0))
        gain_db = float(item.get("sfx_gain_db", item.get("gain_db", -2.5)))
        cues.append({"time_sec": max(0.0, base_t + offset), "sfx": sfx, "gain_db": gain_db})
        end_t = float(item.get("end_sec", base_t + float(item.get("duration_sec", 1.5))))
        total_dur = max(total_dur, end_t)

    return cues, total_dur


def main() -> int:
    parser = argparse.ArgumentParser(description="Procedural comedic SFX engine (48 kHz stereo WAV).")
    parser.add_argument("--list", action="store_true", help="List available SFX cues.")
    parser.add_argument("--cue", help="Render a single SFX cue to --out.")
    parser.add_argument("--timings", help="Path to shot_timings.json or cue list JSON.")
    parser.add_argument("--duration", type=float, default=0.0, help="Override timeline duration in seconds.")
    parser.add_argument("--gain-db", type=float, default=-2.0, help="Single-cue gain in dB (default: -2.0).")
    parser.add_argument("--out", help="Output WAV path.")
    args = parser.parse_args()

    if args.list:
        for name in sorted(GENERATORS):
            print(name)
        return 0

    if not args.out:
        parser.error("--out is required unless --list is passed")

    out_path = Path(args.out)
    rng = random.Random(42)

    if args.cue:
        if args.cue not in GENERATORS:
            print(f"Error: unknown cue '{args.cue}'. Available: {', '.join(sorted(GENERATORS))}", file=sys.stderr)
            return 2
        gain = db_to_lin(args.gain_db)
        samples = [s * gain for s in GENERATORS[args.cue](rng)]
        write_stereo_wav(samples, out_path)
        print(f"Wrote cue '{args.cue}' ({len(samples)/SR:.2f}s) to {out_path}")
        return 0

    if args.timings:
        cues, inferred_dur = load_cues(Path(args.timings))
        total_dur = max(args.duration, inferred_dur, 1.0)
        total_samples = int(math.ceil((total_dur + 1.0) * SR))
        mix = [0.0] * total_samples
        cache: Dict[str, List[float]] = {}
        placed = 0

        for cue in cues:
            name = cue["sfx"]
            if name not in GENERATORS:
                print(f"Warning: skipping unknown SFX cue '{name}' at {cue['time_sec']:.2f}s", file=sys.stderr)
                continue
            if name not in cache:
                cache[name] = GENERATORS[name](random.Random(zlib.crc32(name.encode("utf-8"))))
            clip = cache[name]
            gain = db_to_lin(cue["gain_db"])
            start_i = int(cue["time_sec"] * SR)
            for j, val in enumerate(clip):
                idx = start_i + j
                if idx < total_samples:
                    mix[idx] += val * gain
            placed += 1

        # Trim back to exact target duration (keep at least total_dur)
        final_len = int(math.ceil(total_dur * SR))
        write_stereo_wav(mix[:final_len], out_path)
        print(f"Rendered {placed} SFX cues ({total_dur:.2f}s) to {out_path}")
        return 0

    parser.error("Provide either --cue or --timings")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
