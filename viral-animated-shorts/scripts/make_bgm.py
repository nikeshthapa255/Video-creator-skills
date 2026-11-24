#!/usr/bin/env python3
"""Generate procedural background music for Shorts & Long-Form videos (48 kHz 16-bit stereo WAV).

Supports both Shorts (30-55s) and Act-aware Long-Form episodes (3-15+ min) with
vectorized NumPy synthesis when NumPy is installed and a fast pure-stdlib fallback when
NumPy is unavailable.

Supported moods (8 total):
  Shorts & general moods (6):
    dramatic_news     (default 104 BPM): warm newsroom D-minor sub-bass pulse, string/synth swells, soft rim-tick
    cinematic_tension (default  96 BPM): dark investigative cello/drone bed, slow tension swells, heartbeat pulse
    miami_pop         (default 122 BPM): upbeat synth-pop chords, bouncy bass, crisp hats
    retro_drive       (default 112 BPM): synthwave arpeggio, punchy octave bass, snare pulse
    tech_pulse        (default 128 BPM): plucky minimal electronic arp, tight sub pulse
    comedy_quirk      (default 116 BPM): playful staccato marimba plucks, bouncy bass
  Long-Form Act-aware moods (2):
    palace_drama      (default  84 BPM): 5-Act D Yu Pentatonic guzheng, bowed erhu, palace drone, dagu drums & gong
    epic_story        (default  92 BPM): cinematic Act-aware narrative score (dark drone, rhythmic pulse, soaring strings, epilogue resolve)

Examples:
  python3 make_bgm.py --mood dramatic_news --duration 40 --out audio/bgm.wav
  python3 make_bgm.py --mood palace_drama --timings audio/shot_timings.json --out audio/bgm.wav
  python3 make_bgm.py --mood epic_story --duration 30 --out audio/bgm.wav
  python3 make_bgm.py --mood epic_story --timings audio/shot_timings.json --out audio/bgm.wav
"""

import argparse
from array import array
import json
import math
import random
import re
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np  # type: ignore

    HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore
    HAS_NUMPY = False

SR = 48000
TWO_PI = 2.0 * math.pi

MOOD_CONFIGS: Dict[str, Dict[str, Any]] = {
    "dramatic_news": {
        "bpm": 104,
        # Dm9 -> Bbmaj7 -> Gm7 -> Asus4 (serious, warm newsroom progression)
        "chords": [(50, 57, 60, 64), (46, 53, 57, 62), (43, 50, 53, 58), (45, 52, 57, 61)],
        "bass": [38, 34, 43, 45],
        "engine": "dramatic_news",
    },
    "cinematic_tension": {
        "bpm": 96,
        # Cm(add9) -> Abmaj7 -> Fm7 -> Gsus4 (dark investigative suspense)
        "chords": [(48, 55, 58, 62), (44, 51, 55, 60), (41, 48, 51, 56), (43, 50, 55, 56)],
        "bass": [36, 32, 41, 43],
        "engine": "cinematic_tension",
    },
    "miami_pop": {
        "bpm": 122,
        # Fmaj7 -> G6 -> Em7 -> Am7 (MIDI notes)
        "chords": [(65, 69, 72, 76), (67, 71, 74, 76), (64, 67, 71, 74), (69, 72, 76, 79)],
        "bass": [41, 43, 40, 45],
        "hat_gain": 0.12,
        "kick_gain": 0.42,
        "snare_gain": 0.20,
        "arp_gain": 0.16,
        "pad_gain": 0.14,
    },
    "retro_drive": {
        "bpm": 112,
        # Am -> F -> C -> G
        "chords": [(57, 60, 64, 69), (53, 57, 60, 65), (60, 64, 67, 72), (55, 59, 62, 67)],
        "bass": [45, 41, 48, 43],
        "hat_gain": 0.10,
        "kick_gain": 0.46,
        "snare_gain": 0.28,
        "arp_gain": 0.22,
        "pad_gain": 0.16,
    },
    "tech_pulse": {
        "bpm": 128,
        # Dm -> Bb -> C -> Am
        "chords": [(62, 65, 69, 72), (58, 62, 65, 69), (60, 64, 67, 72), (57, 60, 64, 69)],
        "bass": [38, 34, 36, 45],
        "hat_gain": 0.14,
        "kick_gain": 0.40,
        "snare_gain": 0.14,
        "arp_gain": 0.24,
        "pad_gain": 0.10,
    },
    "comedy_quirk": {
        "bpm": 116,
        # C -> A7 -> Dm -> G7
        "chords": [(60, 64, 67, 72), (57, 61, 64, 67), (62, 65, 69, 74), (55, 59, 62, 65)],
        "bass": [48, 45, 50, 43],
        "hat_gain": 0.08,
        "kick_gain": 0.34,
        "snare_gain": 0.16,
        "arp_gain": 0.28,
        "pad_gain": 0.06,
    },
    "palace_drama": {
        "bpm": 84,
        "engine": "palace_drama",
    },
    "epic_story": {
        "bpm": 92,
        "engine": "epic_story",
    },
}


def midi_to_hz(note: float) -> float:
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))


# =============================================================================
# Timings & General Act-Window Parsing (scales smoothly to any Act count N >= 1)
# =============================================================================

BGM_INTENSITY_TO_STAGE: Dict[str, int] = {
    "sparse": 1,
    "tense": 2,
    "escalating": 3,
    "climax": 4,
    "epilogue": 5,
}


def parse_act_value(raw: Any) -> Optional[int]:
    """Parse flexible act/chapter labels ('Act 1', 'chapter_2', 'act-1', 'act_01', 1, '1') to int."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    if isinstance(raw, float):
        iv = int(raw)
        return iv if iv >= 1 and iv == raw else None
    s = str(raw).strip()
    if not s:
        return None
    if s.isdigit():
        iv = int(s)
        return iv if iv >= 1 else None
    m = re.match(r"^(?:act|chapter|ch|part)[\s_-]*0*(\d+)$", s, flags=re.IGNORECASE)
    if m:
        iv = int(m.group(1))
        return iv if iv >= 1 else None
    m2 = re.search(r"0*(\d+)", s)
    if m2:
        iv = int(m2.group(1))
        return iv if iv >= 1 else None
    return None


def load_timings_metadata(
    timings_path: Optional[str],
    duration: float,
) -> Dict[str, Any]:
    """Read target duration, Act windows, bgm_intensity, ending_style, and bgm_cue drops from shot_timings.json."""
    if not timings_path:
        dur = max(1.0, float(duration))
        step = dur / 5.0
        acts = [(i + 1, i * step, (i + 1) * step) for i in range(5)]
        return {
            "duration": dur,
            "acts": acts,
            "act_intensities": [None] * len(acts),
            "ending_style": "",
            "bgm_cues": [],
        }

    p = Path(timings_path)
    if not p.exists():
        raise FileNotFoundError(f"Timings file not found: {timings_path}")
    data = json.loads(p.read_text(encoding="utf-8"))

    inferred_dur = 0.0
    ending_style = ""
    shots_list: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        inferred_dur = float(data.get("total_duration_sec", 0.0) or 0.0)
        ending_style = str(data.get("ending_style") or "").strip().lower()
        shots_list = [sh for sh in (data.get("shots", []) or []) if isinstance(sh, dict)]
        for sh in shots_list:
            e = float(sh.get("end_sec", float(sh.get("start_sec", 0.0)) + float(sh.get("duration_sec", 0.0))))
            inferred_dur = max(inferred_dur, e)
    elif isinstance(data, list):
        shots_list = [sh for sh in data if isinstance(sh, dict)]
        for sh in shots_list:
            e = float(sh.get("end_sec", float(sh.get("start_sec", 0.0)) + float(sh.get("duration_sec", 0.0))))
            inferred_dur = max(inferred_dur, e)

    dur = float(duration) if duration and duration > 0 else max(1.0, inferred_dur)

    # Extract per-shot bgm_cue automation directives (spotted silence / drops / swells)
    bgm_cues: List[Dict[str, Any]] = []
    for sh in shots_list:
        raw_cue = str(sh.get("bgm_cue") or "").strip().lower()
        if not raw_cue or raw_cue == "none":
            continue
        s_t = max(0.0, float(sh.get("start_sec", sh.get("time_sec", 0.0))))
        if s_t >= dur:
            continue
        e_t = float(sh.get("end_sec", s_t + float(sh.get("duration_sec", 0.0))))
        e_t = min(dur, max(s_t + 0.05, e_t))
        raw_drop = sh.get("bgm_drop_sec")
        drop_sec = float(raw_drop) if raw_drop is not None else None
        bgm_cues.append({
            "cue": raw_cue,
            "start_sec": s_t,
            "end_sec": e_t,
            "drop_sec": drop_sec,
        })

    # Parse Act windows + optional bgm_intensity
    parsed: List[Tuple[int, float, float, Optional[str]]] = []
    if isinstance(data, dict) and data.get("acts"):
        acts_raw = data["acts"]
        if isinstance(acts_raw, list):
            for idx, item in enumerate(acts_raw):
                if not isinstance(item, dict):
                    continue
                raw_a = item.get("act", item.get("act_number", idx + 1))
                a_num = parse_act_value(raw_a)
                if a_num is None:
                    a_num = idx + 1
                s = float(item.get("start_sec", item.get("start", 0.0)))
                e = float(item.get("end_sec", item.get("end", s + float(item.get("duration_sec", 0.0)))))
                inten = str(item.get("bgm_intensity") or "").strip().lower() or None
                parsed.append((a_num, s, e, inten))
        elif isinstance(acts_raw, dict):
            for idx, (k, item) in enumerate(acts_raw.items()):
                if not isinstance(item, dict):
                    continue
                a_num = parse_act_value(k)
                if a_num is None:
                    a_num = idx + 1
                s = float(item.get("start_sec", item.get("start", 0.0)))
                e = float(item.get("end_sec", item.get("end", 0.0)))
                inten = str(item.get("bgm_intensity") or "").strip().lower() or None
                parsed.append((a_num, s, e, inten))

    if not parsed:
        act_bounds: Dict[int, List[float]] = {}
        for sh in shots_list:
            act_val = sh.get("act", sh.get("act_number", sh.get("act_id")))
            a_num = parse_act_value(act_val)
            if a_num is None:
                continue
            s = float(sh.get("start_sec", sh.get("time_sec", 0.0)))
            e = float(sh.get("end_sec", s + float(sh.get("duration_sec", 0.0))))
            if a_num not in act_bounds:
                act_bounds[a_num] = [s, e]
            else:
                act_bounds[a_num][0] = min(act_bounds[a_num][0], s)
                act_bounds[a_num][1] = max(act_bounds[a_num][1], e)
        for a_num in sorted(act_bounds):
            parsed.append((a_num, act_bounds[a_num][0], act_bounds[a_num][1], None))

    if not parsed:
        step = dur / 5.0
        acts = [(i + 1, i * step, (i + 1) * step) for i in range(5)]
        return {
            "duration": dur,
            "acts": acts,
            "act_intensities": [None] * len(acts),
            "ending_style": ending_style,
            "bgm_cues": bgm_cues,
        }

    parsed.sort(key=lambda x: (x[1], x[0]))

    # Filter & clamp cleanly to [0.0, dur] without inverted windows
    filtered = [(a_num, max(0.0, s), min(dur, e), inten) for (a_num, s, e, inten) in parsed if s < dur - 1e-4]
    if not filtered:
        a_num, _, _, inten = parsed[0]
        filtered = [(a_num, 0.0, dur, inten)]

    cleaned_acts: List[Tuple[int, float, float]] = []
    cleaned_intensities: List[Optional[str]] = []
    for idx, (a_num, s, e, inten) in enumerate(filtered):
        s_clamped = 0.0 if idx == 0 else max(cleaned_acts[-1][2], min(dur, s))
        if s_clamped >= dur - 1e-4:
            break
        e_clamped = dur if idx == len(filtered) - 1 else min(dur, max(s_clamped, e))
        if e_clamped <= s_clamped + 1e-4:
            continue
        cleaned_acts.append((a_num, s_clamped, e_clamped))
        cleaned_intensities.append(inten)

    if not cleaned_acts:
        cleaned_acts = [(filtered[0][0], 0.0, dur)]
        cleaned_intensities = [filtered[0][3]]
    else:
        # Ensure contiguous coverage [0.0 .. dur] (close inter-act post_gap_sec holes)
        for idx in range(len(cleaned_acts) - 1):
            a_num, s_c, _ = cleaned_acts[idx]
            next_s = cleaned_acts[idx + 1][1]
            cleaned_acts[idx] = (a_num, s_c, next_s)
        last_a, last_s, _ = cleaned_acts[-1]
        cleaned_acts[-1] = (last_a, last_s, dur)

    return {
        "duration": dur,
        "acts": cleaned_acts,
        "act_intensities": cleaned_intensities,
        "ending_style": ending_style,
        "bgm_cues": bgm_cues,
    }


def load_timings_duration_and_acts(
    timings_path: Optional[str],
    duration: float,
) -> Tuple[float, List[Tuple[int, float, float]]]:
    """Read target duration and Act (act_num, start_sec, end_sec) windows from shot_timings.json."""
    meta = load_timings_metadata(timings_path, duration)
    return meta["duration"], meta["acts"]


def act_index_to_stage(act_idx: int, total_acts: int, t: float, duration: float) -> int:
    """Map arbitrary act index (out of N >= 1 acts) smoothly onto dramatic stages 1..5."""
    if total_acts <= 1:
        frac = min(0.9999, max(0.0, t / max(1.0, duration)))
        return 1 + int(frac * 5.0)
    if total_acts == 5:
        return act_idx + 1
    return 1 + int(round(4.0 * (act_idx / max(1, total_acts - 1))))


def resolve_act_stage(
    act_idx: int,
    total_acts: int,
    t: float,
    duration: float,
    act_intensities: Optional[List[Optional[str]]] = None,
) -> int:
    """Resolve dramatic stage 1..5 honoring explicit acts[].bgm_intensity when present."""
    if act_intensities and 0 <= act_idx < len(act_intensities):
        inten = (act_intensities[act_idx] or "").strip().lower()
        if inten in BGM_INTENSITY_TO_STAGE:
            return BGM_INTENSITY_TO_STAGE[inten]
    return act_index_to_stage(act_idx, total_acts, t, duration)


def _compute_bgm_cue_events(
    bgm_cues: Optional[List[Dict[str, Any]]],
    duration: float,
) -> List[Tuple[str, float, float, float]]:
    """Normalize bgm_cues into (kind, start_sec, end_sec, ramp_sec) tuples."""
    if not bgm_cues:
        return []
    out: List[Tuple[str, float, float, float]] = []
    for item in bgm_cues:
        cue = str(item.get("cue") or "").strip().lower()
        s_t = max(0.0, float(item.get("start_sec", 0.0)))
        e_t = min(duration, float(item.get("end_sec", duration)))
        if e_t <= s_t + 0.01:
            continue
        raw_drop = item.get("drop_sec")
        drop_s = float(raw_drop) if raw_drop is not None and float(raw_drop) > 0.0 else None
        if cue == "drop_out":
            hold_end = min(e_t, s_t + drop_s) if drop_s is not None else e_t
            out.append(("drop", s_t, max(s_t + 0.08, hold_end), 0.08))
        elif cue == "drop_before_end":
            eff_drop = drop_s if drop_s is not None else min(1.5, (e_t - s_t) * 0.5)
            d_start = max(s_t, e_t - eff_drop)
            out.append(("drop", d_start, e_t, 0.08))
        elif cue == "swell_in":
            swell_dur = drop_s if drop_s is not None else min(0.6, max(0.12, (e_t - s_t) * 0.5))
            out.append(("swell_in", s_t, min(duration, s_t + swell_dur), swell_dur))
    return out


def _apply_bgm_cues_pcm_inplace(
    pcm_bytes: bytearray,
    bgm_cues: Optional[List[Dict[str, Any]]],
    duration: float,
) -> None:
    """Apply smooth cosine music-drop (-36 dB) / swell-in automation directly to stereo int16 PCM."""
    events = _compute_bgm_cue_events(bgm_cues, duration)
    if not events:
        return
    total_frames = len(pcm_bytes) // 4
    floor_lin = 10.0 ** (-36.0 / 20.0)
    duck_n = max(1, int(0.080 * SR))
    swell_n = max(1, int(0.120 * SR))
    cos = math.cos
    pi = math.pi

    for kind, s_t, e_t, _ in events:
        if kind == "drop":
            sf = max(0, int(round(s_t * SR)))
            ef = min(total_frames, int(round(e_t * SR)))
            if ef <= sf:
                continue
            rec_end = min(total_frames, ef + swell_n)
            seg = array("h")
            seg.frombytes(pcm_bytes[sf * 4 : rec_end * 4])
            if sys.byteorder != "little":
                seg.byteswap()
            seg_len = rec_end - sf
            hold_end = ef - sf
            eff_duck = min(duck_n, hold_end)
            for i in range(seg_len):
                if i < eff_duck:
                    u = i / max(1, eff_duck)
                    g = 1.0 - (1.0 - floor_lin) * (0.5 - 0.5 * cos(pi * u))
                elif i < hold_end:
                    g = floor_lin
                else:
                    u = (i - hold_end) / max(1, seg_len - hold_end)
                    g = floor_lin + (1.0 - floor_lin) * (0.5 - 0.5 * cos(pi * u))
                seg[i * 2] = int(seg[i * 2] * g)
                seg[i * 2 + 1] = int(seg[i * 2 + 1] * g)
            if sys.byteorder != "little":
                seg.byteswap()
            pcm_bytes[sf * 4 : rec_end * 4] = seg.tobytes()
        elif kind == "swell_in":
            sf = max(0, int(round(s_t * SR)))
            ef = min(total_frames, int(round(e_t * SR)))
            if ef <= sf + 1:
                continue
            seg = array("h")
            seg.frombytes(pcm_bytes[sf * 4 : ef * 4])
            if sys.byteorder != "little":
                seg.byteswap()
            seg_len = ef - sf
            for i in range(seg_len):
                u = i / max(1, seg_len - 1)
                g = floor_lin + (1.0 - floor_lin) * (0.5 - 0.5 * cos(pi * u))
                seg[i * 2] = int(seg[i * 2] * g)
                seg[i * 2 + 1] = int(seg[i * 2 + 1] * g)
            if sys.byteorder != "little":
                seg.byteswap()
            pcm_bytes[sf * 4 : ef * 4] = seg.tobytes()


def _apply_smooth_act_gains_pcm_inplace(
    pcm_bytes: bytearray,
    acts: List[Tuple[int, float, float]],
    duration: float,
    act_intensities: Optional[List[Optional[str]]] = None,
    ramp_sec: float = 0.8,
) -> None:
    """Apply Act-level intensity gains with smooth 0.8s cosine ramps using pure Python array (no audioop)."""
    if not acts or len(acts) < 2:
        return
    total_frames = len(pcm_bytes) // 4
    stage_gains = {1: 0.86, 2: 0.93, 3: 1.00, 4: 1.00, 5: 0.84}
    gains_per_act = [
        stage_gains.get(resolve_act_stage(idx, len(acts), a_s, duration, act_intensities), 1.0)
        for idx, (_, a_s, _) in enumerate(acts)
    ]
    if all(abs(g - 1.0) <= 0.005 for g in gains_per_act):
        return

    ramp_n = max(1, int(ramp_sec * SR))
    half_ramp = ramp_n // 2
    cos = math.cos
    pi = math.pi

    # Build piecewise constant + cosine transition regions
    seg = array("h")
    seg.frombytes(pcm_bytes)
    if sys.byteorder != "little":
        seg.byteswap()

    for idx, (_, a_s, a_e) in enumerate(acts):
        g_cur = gains_per_act[idx]
        sf = max(0, int(round(a_s * SR)))
        ef = min(total_frames, int(round(a_e * SR)))
        if ef <= sf:
            continue
        # Reserve ramp margins at boundaries where gain changes
        left_margin = half_ramp if (idx > 0 and abs(gains_per_act[idx - 1] - g_cur) > 0.005) else 0
        right_margin = half_ramp if (idx + 1 < len(acts) and abs(gains_per_act[idx + 1] - g_cur) > 0.005) else 0
        body_s = min(ef, sf + left_margin)
        body_e = max(body_s, ef - right_margin)
        if abs(g_cur - 1.0) > 0.005:
            for i in range(body_s, body_e):
                seg[i * 2] = int(seg[i * 2] * g_cur)
                seg[i * 2 + 1] = int(seg[i * 2 + 1] * g_cur)

    # Apply smooth cosine ramps across boundaries between consecutive acts
    for idx in range(len(acts) - 1):
        g0 = gains_per_act[idx]
        g1 = gains_per_act[idx + 1]
        if abs(g1 - g0) <= 0.005:
            continue
        bf = int(round(acts[idx][2] * SR))
        rs = max(0, bf - half_ramp)
        re_f = min(total_frames, bf + half_ramp)
        rlen = re_f - rs
        if rlen <= 1:
            continue
        for k in range(rlen):
            u = k / max(1, rlen - 1)
            g = g0 + (g1 - g0) * (0.5 - 0.5 * cos(pi * u))
            i = rs + k
            seg[i * 2] = int(seg[i * 2] * g)
            seg[i * 2 + 1] = int(seg[i * 2 + 1] * g)

    if sys.byteorder != "little":
        seg.byteswap()
    pcm_bytes[:] = seg.tobytes()


# =============================================================================
# WAV & Fade Utilities (fast for both NumPy and pure-stdlib)
# =============================================================================

def _apply_fades(left: list, right: list, fade_in_sec: float, fade_out_sec: float) -> None:
    n_samples = len(left)
    fade_in = min(int(fade_in_sec * SR), n_samples // 2 if n_samples > 1 else n_samples)
    fade_out = min(int(fade_out_sec * SR), n_samples // 2 if n_samples > 1 else n_samples)
    for i in range(fade_in):
        g = 0.5 - 0.5 * math.cos(math.pi * i / max(1, fade_in))
        left[i] *= g
        right[i] *= g
    for i in range(fade_out):
        g = 0.5 - 0.5 * math.cos(math.pi * i / max(1, fade_out))
        idx = n_samples - 1 - i
        left[idx] *= g
        right[idx] *= g


def _pack_stereo_pcm_stdlib(left: List[float], right: List[float], drive: float = 0.85) -> bytes:
    n = len(left)
    if n == 0:
        return b""
    tanh = math.tanh
    if drive == 1.0:
        pcm = array("h", (int(tanh(v) * 32767.0) for pair in zip(left, right) for v in pair))
    else:
        pcm = array("h", (int(tanh(v * drive) * 32767.0) for pair in zip(left, right) for v in pair))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def write_stereo_wav_arrays(left: Any, right: Any, out_path: Path, drive: float = 0.85, already_limited: bool = False) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if HAS_NUMPY and isinstance(left, np.ndarray):
        l_arr = left if already_limited else np.tanh(left * drive)
        r_arr = right if already_limited else np.tanh(right * drive)
        interleaved = np.empty((l_arr.shape[0], 2), dtype=np.int16)
        interleaved[:, 0] = np.clip(np.round(l_arr * 32767.0), -32768, 32767).astype(np.int16)
        interleaved[:, 1] = np.clip(np.round(r_arr * 32767.0), -32768, 32767).astype(np.int16)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            wf.writeframes(interleaved.tobytes())
        return

    pcm_bytes = _pack_stereo_pcm_stdlib(left, right, drive=1.0 if already_limited else drive)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm_bytes)


def _apply_pcm_fades_inplace(pcm_bytes: bytearray, fade_in_frames: int, fade_out_frames: int) -> None:
    """Apply smooth raised-cosine fade-in and fade-out directly on stereo int16 PCM bytes."""
    total_frames = len(pcm_bytes) // 4
    fi = min(fade_in_frames, total_frames // 2)
    fo = min(fade_out_frames, total_frames // 2)
    if fi > 1:
        seg = array("h")
        seg.frombytes(pcm_bytes[: fi * 4])
        if sys.byteorder != "little":
            seg.byteswap()
        cos = math.cos
        inv = math.pi / fi
        for i in range(fi):
            g = 0.5 - 0.5 * cos(inv * i)
            seg[i * 2] = int(seg[i * 2] * g)
            seg[i * 2 + 1] = int(seg[i * 2 + 1] * g)
        if sys.byteorder != "little":
            seg.byteswap()
        pcm_bytes[: fi * 4] = seg.tobytes()
    if fo > 1:
        start_b = (total_frames - fo) * 4
        seg = array("h")
        seg.frombytes(pcm_bytes[start_b:])
        if sys.byteorder != "little":
            seg.byteswap()
        cos = math.cos
        inv = math.pi / fo
        for i in range(fo):
            g = 0.5 - 0.5 * cos(inv * (fo - 1 - i))
            seg[i * 2] = int(seg[i * 2] * g)
            seg[i * 2 + 1] = int(seg[i * 2 + 1] * g)
        if sys.byteorder != "little":
            seg.byteswap()
        pcm_bytes[start_b:] = seg.tobytes()


def _crossfade_pcm_seam(buf_a: bytes, buf_b: bytes, xfade_frames: int) -> bytes:
    """Return crossfaded stereo int16 overlap segment blending tail of buf_a into head of buf_b."""
    n = min(xfade_frames, len(buf_a) // 4, len(buf_b) // 4)
    if n <= 1:
        return b""
    a = array("h")
    b = array("h")
    a.frombytes(buf_a[-n * 4 :])
    b.frombytes(buf_b[: n * 4])
    if sys.byteorder != "little":
        a.byteswap()
        b.byteswap()
    out = array("h", [0]) * (n * 2)
    cos = math.cos
    inv = math.pi / n
    for i in range(n):
        wb = 0.5 - 0.5 * cos(inv * i)
        wa = 1.0 - wb
        out[i * 2] = max(-32767, min(32767, int(a[i * 2] * wa + b[i * 2] * wb)))
        out[i * 2 + 1] = max(-32767, min(32767, int(a[i * 2 + 1] * wa + b[i * 2 + 1] * wb)))
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()


def _upsample_2x(buf: List[float], target_n: int) -> List[float]:
    m = len(buf)
    if m == 0 or target_n <= 0:
        return [0.0] * max(0, target_n)
    out = [0.0] * target_n
    n_even = min(m, (target_n + 1) // 2)
    out[0 : n_even * 2 : 2] = buf[:n_even]
    n_odd = min(m - 1, target_n // 2)
    if n_odd > 0:
        out[1 : n_odd * 2 : 2] = [0.5 * (a + b) for a, b in zip(buf[:n_odd], buf[1 : n_odd + 1])]
    if n_odd * 2 + 1 < target_n:
        out[n_odd * 2 + 1 :] = [buf[m - 1]] * (target_n - (n_odd * 2 + 1))
    return out


# =============================================================================
# Existing 6 Shorts Moods (100% backward-compatible + fast loop tiling for >90s)
# =============================================================================

def _synthesize_dramatic_news(duration: float, bpm: int, apply_fades: bool = True) -> tuple:
    """Warm dramatic news-commentary underscore: deep muted sub-bass pulse, string/synth swells, soft rim-tick."""
    cfg = MOOD_CONFIGS["dramatic_news"]
    n_samples = int(math.ceil(duration * SR))
    left = [0.0] * n_samples
    right = [0.0] * n_samples

    beat_sec = 60.0 / bpm
    step_sec = beat_sec / 4.0  # 16th-note grid
    n_steps = int(math.ceil(duration / step_sec))
    chords = cfg["chords"]
    bass_notes = cfg["bass"]
    rng = random.Random(204 + bpm)
    two_pi = TWO_PI
    sin = math.sin
    exp = math.exp

    for step in range(n_steps):
        bar = (step // 16) % len(chords)
        sub = step % 16
        start_i = int(step * step_sec * SR)
        if start_i >= n_samples:
            break

        chord = chords[bar]
        bass_midi = bass_notes[bar]

        # 1. Deep muted sub-bass pulse
        if sub in (0, 2, 4, 6, 8, 10, 12, 14):
            bf = midi_to_hz(bass_midi)
            accent = 1.0 if sub in (0, 8) else (0.72 if sub in (4, 12) else 0.48)
            b_len = min(int(step_sec * (2.6 if sub in (0, 8) else 1.7) * SR), n_samples - start_i)
            w1 = two_pi * bf / SR
            w2 = 2.0 * w1
            decay = 4.2 if sub in (0, 8) else 7.2
            att_n = max(1, int(0.012 * SR))
            for j in range(b_len):
                env = (j / att_n if j < att_n else 1.0) * exp(-(j / SR) * decay)
                v = (sin(w1 * j) + 0.22 * sin(w2 * j)) * env * 0.30 * accent
                left[start_i + j] += v
                right[start_i + j] += v

        # 2. Warm cinematic string / synth pad swells across each bar
        if sub == 0:
            p_len = min(int(step_sec * 16.0 * SR), n_samples - start_i)
            p_dur = max(0.2, p_len / SR)
            voices = [midi_to_hz(m) for m in chord]
            w_l = [two_pi * f * (1.0016 if idx % 2 == 0 else 0.9986) / SR for idx, f in enumerate(voices)]
            w_r = [two_pi * f * (0.9984 if idx % 2 == 0 else 1.0014) / SR for idx, f in enumerate(voices)]
            att_s = min(0.45, p_dur * 0.28)
            rel_s = min(0.50, p_dur * 0.30)
            for j in range(p_len):
                t = j / SR
                env = min(1.0, t / att_s) * min(1.0, max(0.0, (p_dur - t) / rel_s))
                swell = 0.72 + 0.28 * sin(math.pi * t / p_dur)
                sl = 0.0
                sr_v = 0.0
                for wl, wr in zip(w_l, w_r):
                    sl += sin(wl * j) + 0.16 * sin(2.0 * wl * j)
                    sr_v += sin(wr * j) + 0.16 * sin(2.0 * wr * j)
                g = (env * swell) * 0.072
                left[start_i + j] += sl * g
                right[start_i + j] += sr_v * g

        # 3. Subtle broadcast newsroom rhythmic pulse
        if sub in (0, 3, 6, 8, 11, 14):
            note_idx = (sub // 3) % 3
            pf = midi_to_hz(chord[note_idx])
            p_len = min(int(step_sec * 1.45 * SR), n_samples - start_i)
            wp = two_pi * pf / SR
            pan_l = 0.62 if (sub % 6 == 0) else 0.42
            pan_r = 1.04 - pan_l
            att_n = max(1, int(0.008 * SR))
            for j in range(p_len):
                env = (j / att_n if j < att_n else 1.0) * exp(-(j / SR) * 9.5)
                v = (sin(wp * j) + 0.12 * sin(2.0 * wp * j)) * env * 0.095
                left[start_i + j] += v * pan_l
                right[start_i + j] += v * pan_r

        # 4. Soft muted broadcast thump on beats 1 and 3
        if sub in (0, 8):
            k_len = min(int(0.15 * SR), n_samples - start_i)
            phase = 0.0
            gain = 0.24 if sub == 0 else 0.18
            for j in range(k_len):
                t = j / SR
                f = 42.0 + 48.0 * exp(-t * 28.0)
                phase += two_pi * f / SR
                env = min(1.0, t / 0.006) * exp(-t * 15.0)
                v = sin(phase) * env * gain
                left[start_i + j] += v
                right[start_i + j] += v

        # 5. Understated broadcast rim-tick
        if sub in (4, 12, 2, 10, 14):
            is_main = sub in (4, 12)
            r_len = min(int(0.042 * SR), n_samples - start_i)
            lp = 0.0
            rim_freq = 410.0 if is_main else 520.0
            gain = 0.095 if is_main else 0.036
            pan_l = 0.92 if is_main else 1.08
            pan_r = 1.08 if is_main else 0.92
            for j in range(r_len):
                t = j / SR
                raw = rng.uniform(-1.0, 1.0)
                lp += 0.22 * (raw - lp)
                wood = sin(two_pi * rim_freq * t) * 0.65 + lp * 0.45
                v = wood * exp(-t * (68.0 if is_main else 95.0)) * gain
                left[start_i + j] += v * pan_l
                right[start_i + j] += v * pan_r

    if apply_fades:
        _apply_fades(left, right, fade_in_sec=0.35, fade_out_sec=0.70)
    return left, right


def _synthesize_cinematic_tension(duration: float, bpm: int, apply_fades: bool = True) -> tuple:
    """Darker suspenseful investigative underscore: low cello/drone bed, slow tension swells, heartbeat kick."""
    cfg = MOOD_CONFIGS["cinematic_tension"]
    n_samples = int(math.ceil(duration * SR))
    left = [0.0] * n_samples
    right = [0.0] * n_samples

    beat_sec = 60.0 / bpm
    step_sec = beat_sec / 4.0
    n_steps = int(math.ceil(duration / step_sec))
    chords = cfg["chords"]
    bass_notes = cfg["bass"]
    two_pi = TWO_PI
    sin = math.sin
    exp = math.exp

    for step in range(n_steps):
        bar = (step // 16) % len(chords)
        sub = step % 16
        start_i = int(step * step_sec * SR)
        if start_i >= n_samples:
            break

        chord = chords[bar]
        bass_midi = bass_notes[bar]

        # 1. Low bowed cello / sub-drone bed sustained across each bar
        if sub == 0:
            d_len = min(int(step_sec * 16.0 * SR), n_samples - start_i)
            d_dur = max(0.2, d_len / SR)
            f0 = midi_to_hz(bass_midi)
            f5 = midi_to_hz(bass_midi + 7)
            w0_l = two_pi * f0 * 1.0012 / SR
            w0_r = two_pi * f0 * 0.9988 / SR
            w5 = two_pi * f5 / SR
            att_s = min(0.55, d_dur * 0.30)
            rel_s = min(0.60, d_dur * 0.32)
            for j in range(d_len):
                t = j / SR
                env = min(1.0, t / att_s) * min(1.0, max(0.0, (d_dur - t) / rel_s))
                bow = 0.84 + 0.16 * sin(two_pi * 0.38 * t)
                c_l = sin(w0_l * j) + 0.32 * sin(2.0 * w0_l * j) + 0.12 * sin(3.0 * w0_l * j)
                c_r = sin(w0_r * j) + 0.32 * sin(2.0 * w0_r * j) + 0.12 * sin(3.0 * w0_r * j)
                fifth = sin(w5 * j) * 0.18
                g = env * bow * 0.17
                left[start_i + j] += (c_l + fifth) * g
                right[start_i + j] += (c_r - fifth * 0.5) * g

            # 2. Slow dark tension string/pad swell
            voices = [midi_to_hz(m) for m in chord]
            w_l = [two_pi * f * (1.0020 if idx % 2 == 0 else 0.9980) / SR for idx, f in enumerate(voices)]
            w_r = [two_pi * f * (0.9980 if idx % 2 == 0 else 1.0020) / SR for idx, f in enumerate(voices)]
            for j in range(d_len):
                t = j / SR
                phase_pos = t / d_dur
                swell = (sin(math.pi * phase_pos) ** 1.35) * (0.55 + 0.45 * phase_pos)
                sl = sum(sin(wl * j) + 0.14 * sin(2.0 * wl * j) for wl in w_l)
                sr_v = sum(sin(wr * j) + 0.14 * sin(2.0 * wr * j) for wr in w_r)
                g = swell * 0.062
                left[start_i + j] += sl * g
                right[start_i + j] += sr_v * g

        # 3. Heartbeat kick
        if sub in (0, 3, 8):
            k_len = min(int(0.19 * SR), n_samples - start_i)
            phase = 0.0
            gain = 0.30 if sub == 0 else (0.19 if sub == 3 else 0.15)
            for j in range(k_len):
                t = j / SR
                f = 36.0 + 34.0 * exp(-t * 22.0)
                phase += two_pi * f / SR
                env = min(1.0, t / 0.010) * exp(-t * 12.0)
                v = sin(phase) * env * gain
                left[start_i + j] += v
                right[start_i + j] += v

        # 4. Muted suspense pulse
        if sub in (0, 4, 8, 12):
            t_len = min(int(0.065 * SR), n_samples - start_i)
            tf = midi_to_hz(chord[1 if sub in (4, 12) else 0])
            wt = two_pi * tf / SR
            pan_l = 0.45 if sub in (0, 8) else 0.65
            pan_r = 1.10 - pan_l
            for j in range(t_len):
                t = j / SR
                env = min(1.0, t / 0.005) * exp(-t * 32.0)
                v = sin(wt * j) * env * 0.075
                left[start_i + j] += v * pan_l
                right[start_i + j] += v * pan_r

    if apply_fades:
        _apply_fades(left, right, fade_in_sec=0.45, fade_out_sec=0.85)
    return left, right


def _synthesize_pop_grid(mood: str, duration: float, bpm: int, apply_fades: bool = True) -> tuple:
    cfg = MOOD_CONFIGS[mood]
    n_samples = int(math.ceil(duration * SR))
    left = [0.0] * n_samples
    right = [0.0] * n_samples

    beat_sec = 60.0 / bpm
    step_sec = beat_sec / 4.0
    rng = random.Random(101 + bpm)
    n_steps = int(math.ceil(duration / step_sec))
    chords = cfg["chords"]
    bass_notes = cfg["bass"]
    sin = math.sin
    exp = math.exp

    for step in range(n_steps):
        bar = (step // 16) % len(chords)
        sub = step % 16
        t0 = step * step_sec
        start_i = int(t0 * SR)
        if start_i >= n_samples:
            break

        if sub in (0, 4, 8, 12) or (mood == "comedy_quirk" and sub == 10):
            k_len = min(int(0.16 * SR), n_samples - start_i)
            phase = 0.0
            for j in range(k_len):
                t = j / SR
                f = 46.0 + 115.0 * exp(-t * 36.0)
                phase += TWO_PI * f / SR
                v = sin(phase) * exp(-t * 16.0) * cfg["kick_gain"]
                left[start_i + j] += v
                right[start_i + j] += v

        if sub in (4, 12):
            s_len = min(int(0.11 * SR), n_samples - start_i)
            for j in range(s_len):
                t = j / SR
                noise = rng.uniform(-1.0, 1.0)
                body = sin(TWO_PI * 190.0 * t) * 0.45
                v = (noise * 0.6 + body) * exp(-t * 28.0) * cfg["snare_gain"]
                left[start_i + j] += v * 0.95
                right[start_i + j] += v * 1.05

        if sub % 2 == 1 or (mood == "tech_pulse" and sub % 2 == 0):
            h_len = min(int(0.035 * SR), n_samples - start_i)
            prev = 0.0
            for j in range(h_len):
                t = j / SR
                raw = rng.uniform(-1.0, 1.0)
                hp = raw - 0.85 * prev
                prev = raw
                accent = 1.0 if sub % 4 == 2 else 0.55
                v = hp * exp(-t * 85.0) * cfg["hat_gain"] * accent
                left[start_i + j] += v * 1.1
                right[start_i + j] += v * 0.85

        if sub % 2 == 0:
            b_note = bass_notes[bar] + (12 if (sub % 4 == 2 and mood != "comedy_quirk") else 0)
            bf = midi_to_hz(b_note)
            b_len = min(int(step_sec * 1.85 * SR), n_samples - start_i)
            for j in range(b_len):
                t = j / SR
                env = min(1.0, t / 0.006) * exp(-t * 7.5)
                v = (sin(TWO_PI * bf * t) + 0.32 * sin(4.0 * math.pi * bf * t)) * env * 0.28
                left[start_i + j] += v
                right[start_i + j] += v

        chord = chords[bar]
        if (mood == "comedy_quirk" and sub in (0, 3, 6, 8, 11, 14)) or (mood != "comedy_quirk" and sub % 2 == 0):
            a_note = chord[(sub // 2) % len(chord)]
            af = midi_to_hz(a_note)
            a_len = min(int(step_sec * 1.9 * SR), n_samples - start_i)
            pan = 0.35 if (sub // 2) % 2 == 0 else 0.65
            for j in range(a_len):
                t = j / SR
                decay = 14.0 if mood == "comedy_quirk" else 8.5
                env = min(1.0, t / 0.004) * exp(-t * decay)
                v = (sin(TWO_PI * af * t) + 0.25 * sin(6.0 * math.pi * af * t)) * env * cfg["arp_gain"]
                left[start_i + j] += v * (1.0 - pan + 0.2)
                right[start_i + j] += v * (pan + 0.2)

        if sub == 0 and cfg["pad_gain"] > 0:
            p_len = min(int(step_sec * 16.0 * SR), n_samples - start_i)
            freqs = [midi_to_hz(m) for m in chord[:3]]
            p_dur = p_len / SR
            for j in range(p_len):
                t = j / SR
                env = min(1.0, t / 0.12) * min(1.0, max(0.0, (p_dur - t) / 0.18))
                sig_l = sum(sin(TWO_PI * f * 1.001 * t) for f in freqs) / 3.0
                sig_r = sum(sin(TWO_PI * f * 0.999 * t) for f in freqs) / 3.0
                left[start_i + j] += sig_l * env * cfg["pad_gain"]
                right[start_i + j] += sig_r * env * cfg["pad_gain"]

    if apply_fades:
        fade_in = min(int(0.05 * SR), n_samples)
        fade_out = min(int(0.25 * SR), n_samples)
        for i in range(fade_in):
            g = i / fade_in
            left[i] *= g
            right[i] *= g
        for i in range(fade_out):
            g = i / fade_out
            idx = n_samples - 1 - i
            left[idx] *= g
            right[idx] *= g
    return left, right


def synthesize_bgm(mood: str, duration: float, bpm: int) -> tuple:
    """Short-form compatible synthesis API for the 6 classic Shorts moods."""
    cfg = MOOD_CONFIGS[mood]
    engine = cfg.get("engine")
    if engine == "dramatic_news":
        return _synthesize_dramatic_news(duration, bpm)
    if engine == "cinematic_tension":
        return _synthesize_cinematic_tension(duration, bpm)
    return _synthesize_pop_grid(mood, duration, bpm)


def _render_classic_long_tiled(
    mood: str,
    duration: float,
    bpm: int,
    out_path: Path,
    acts: Optional[List[Tuple[int, float, float]]] = None,
    act_intensities: Optional[List[Optional[str]]] = None,
    bgm_cues: Optional[List[Dict[str, Any]]] = None,
    ending_style: str = "",
) -> None:
    """Fast seamless 4-bar cycle renderer for >90s durations of the 6 classic moods."""
    bar_sec = (60.0 / bpm) * 4.0
    cycle_sec = bar_sec * 4.0
    seam_sec = 0.016
    # Render 5 bars + seam overflow so [bar_n .. bar_n + cycle_n + seam_n] wraps cleanly with zero click
    raw_dur = bar_sec * 5.0 + seam_sec + 0.04
    engine = MOOD_CONFIGS[mood].get("engine")
    if engine == "dramatic_news":
        l5, r5 = _synthesize_dramatic_news(raw_dur, bpm, apply_fades=False)
        fi_s, fo_s = 0.35, 0.70
    elif engine == "cinematic_tension":
        l5, r5 = _synthesize_cinematic_tension(raw_dur, bpm, apply_fades=False)
        fi_s, fo_s = 0.45, 0.85
    else:
        l5, r5 = _synthesize_pop_grid(mood, raw_dur, bpm, apply_fades=False)
        fi_s, fo_s = 0.15, 0.50

    bar_n = int(round(bar_sec * SR))
    cycle_n = int(round(cycle_sec * SR))
    seam_n = min(int(round(seam_sec * SR)), cycle_n // 8, max(0, len(l5) - (bar_n + cycle_n)))
    cyc_l = l5[bar_n : bar_n + cycle_n]
    cyc_r = r5[bar_n : bar_n + cycle_n]
    over_l = l5[bar_n + cycle_n : bar_n + cycle_n + seam_n]
    over_r = r5[bar_n + cycle_n : bar_n + cycle_n + seam_n]

    # Crossfade overflow [cycle_n .. cycle_n + seam_n] into head [0 .. seam_n] for C1-smooth tiling
    for i in range(seam_n):
        w = 0.5 - 0.5 * math.cos(math.pi * i / max(1, seam_n))
        cyc_l[i] = cyc_l[i] * w + over_l[i] * (1.0 - w)
        cyc_r[i] = cyc_r[i] * w + over_r[i] * (1.0 - w)

    cycle_pcm = _pack_stereo_pcm_stdlib(cyc_l, cyc_r, drive=0.85)
    total_frames = int(math.ceil(duration * SR))
    cycle_frames = len(cycle_pcm) // 4

    out_buf = bytearray()
    rem = total_frames
    while rem > 0:
        take = min(rem, cycle_frames)
        out_buf.extend(cycle_pcm[: take * 4])
        rem -= take

    # Smooth 0.8s cosine Act-level intensity ramps (pure Python array, Python 3.13 safe)
    if acts and len(acts) >= 2:
        _apply_smooth_act_gains_pcm_inplace(
            out_buf, acts, duration, act_intensities=act_intensities, ramp_sec=0.8
        )

    if bgm_cues:
        _apply_bgm_cues_pcm_inplace(out_buf, bgm_cues, duration)

    if ending_style == "cliffhanger":
        fo_s = 0.08
    _apply_pcm_fades_inplace(out_buf, int(fi_s * SR), int(fo_s * SR))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(bytes(out_buf))


# =============================================================================
# Vectorized NumPy Long-Form Composers (palace_drama + epic_story)
# =============================================================================

D_YU_SCALE = [
    38, 41, 43, 45, 48,
    50, 53, 55, 57, 60,
    62, 65, 67, 69, 72,
    74, 77, 79, 81, 84, 86,
]
D_YU_WITH_PASSING = sorted(set(D_YU_SCALE + [52, 64, 76]))

PALACE_PROGRESSION = [
    (38, 45, [50, 57, 62, 65, 69, 74]),
    (41, 48, [53, 60, 65, 69, 72, 77]),
    (43, 50, [55, 62, 67, 72, 74, 79]),
    (45, 52, [57, 64, 67, 69, 72, 76]),
    (38, 45, [50, 57, 64, 65, 69, 74]),
    (48, 55, [60, 67, 72, 74, 79, 81]),
    (43, 50, [55, 60, 65, 67, 72, 74]),
    (45, 52, [57, 64, 69, 72, 76, 81]),
]

# Cinematic D-minor / F-major / Bb-major narrative progression for epic_story
EPIC_PROGRESSION = [
    (38, [50, 57, 62, 64, 65, 69]),  # Dm9
    (34, [46, 53, 57, 60, 62, 65]),  # Bbmaj7
    (41, [53, 60, 64, 65, 69, 72]),  # Fmaj7
    (45, [52, 57, 60, 62, 64, 69]),  # Asus4 / Am7
    (38, [50, 57, 62, 65, 69, 74]),  # Dm(add9)
    (43, [50, 55, 58, 62, 65, 67]),  # Gm7
    (36, [48, 55, 60, 62, 64, 67]),  # C(add9)
    (45, [52, 57, 61, 64, 69, 72]),  # A7sus4 -> resolve
]


if HAS_NUMPY:

    def _np_mix_mono(left: Any, right: Any, mono: Any, start_idx: int, pan: float = 0.5, gain: float = 1.0) -> None:
        if start_idx >= left.shape[0] or mono.size == 0:
            return
        if start_idx < 0:
            mono = mono[-start_idx:]
            start_idx = 0
        avail = left.shape[0] - start_idx
        if avail <= 0 or mono.size == 0:
            return
        n = min(avail, mono.shape[0])
        p = float(np.clip(pan, 0.0, 1.0))
        gl = math.cos(p * math.pi * 0.5) * gain
        gr = math.sin(p * math.pi * 0.5) * gain
        seg = mono[:n]
        left[start_idx : start_idx + n] += seg * gl
        right[start_idx : start_idx + n] += seg * gr

    def _np_mix_stereo(left: Any, right: Any, buf_l: Any, buf_r: Any, start_idx: int, gain: float = 1.0) -> None:
        if start_idx >= left.shape[0] or buf_l.size == 0:
            return
        if start_idx < 0:
            buf_l = buf_l[-start_idx:]
            buf_r = buf_r[-start_idx:]
            start_idx = 0
        avail = left.shape[0] - start_idx
        if avail <= 0 or buf_l.size == 0:
            return
        n = min(avail, buf_l.shape[0])
        left[start_idx : start_idx + n] += buf_l[:n] * gain
        right[start_idx : start_idx + n] += buf_r[:n] * gain

    def _np_apply_speech_eq(left: Any, right: Any, speech_carve: bool = True) -> Tuple[Any, Any]:
        """Batched 2D COLA-unity overlap-add FFT filter (28 Hz highpass + optional 2.5-4 kHz speech carve)."""
        n = left.shape[0]
        fft_n = 16384
        hop = fft_n // 2
        # Periodic sqrt(Hann) analysis/synthesis pair sums to exact 1.0000 at 50% overlap (zero tremolo)
        win = np.sqrt(np.hanning(fft_n + 1)[:-1]).astype(np.float32)
        freqs = np.fft.rfftfreq(fft_n, d=1.0 / SR).astype(np.float32)

        hp_db = -18.0 / (1.0 + (freqs / 28.0) ** 4.0)
        if speech_carve:
            dip_db = -4.5 * np.exp(-0.5 * ((freqs - 3150.0) / 850.0) ** 2)
            shelf_db = -3.5 / (1.0 + (6200.0 / np.maximum(freqs, 1.0)) ** 3.0)
            eq_db = dip_db + shelf_db + hp_db
        else:
            eq_db = hp_db
        eq_curve = np.power(10.0, eq_db / 20.0).astype(np.float32)

        pad_tail = (hop - (n % hop)) % hop + hop
        total_padded = hop + n + pad_tail

        def _proc(x: Any) -> Any:
            xp = np.pad(x.astype(np.float32), (hop, pad_tail))
            frames = np.lib.stride_tricks.sliding_window_view(xp, fft_n)[::hop]
            y_frames = np.fft.irfft(np.fft.rfft(frames * win, axis=-1) * eq_curve, n=fft_n, axis=-1).astype(np.float32) * win
            out = np.zeros(total_padded, dtype=np.float32)
            even = y_frames[0::2]
            odd = y_frames[1::2]
            out[: even.shape[0] * fft_n] += even.reshape(-1)
            out[hop : hop + odd.shape[0] * fft_n] += odd.reshape(-1)
            return out[hop : hop + n]

        return _proc(left), _proc(right)

    def _np_apply_bgm_cues(
        left: Any,
        right: Any,
        bgm_cues: Optional[List[Dict[str, Any]]],
        duration: float,
    ) -> Tuple[Any, Any]:
        """Apply smooth cosine music-drop (-36 dB, 80ms duck, 120ms swell) automation envelope."""
        events = _compute_bgm_cue_events(bgm_cues, duration)
        if not events:
            return left, right
        n = left.shape[0]
        env = np.ones(n, dtype=np.float32)
        floor_lin = np.float32(10.0 ** (-36.0 / 20.0))
        duck_n = max(1, int(0.080 * SR))
        swell_n = max(1, int(0.120 * SR))

        for kind, s_t, e_t, _ in events:
            if kind == "drop":
                sf = max(0, int(round(s_t * SR)))
                ef = min(n, int(round(e_t * SR)))
                if ef <= sf:
                    continue
                eff_duck = min(duck_n, ef - sf)
                if eff_duck > 1:
                    u = np.linspace(0.0, 1.0, eff_duck, dtype=np.float32)
                    ramp = np.float32(1.0) - (np.float32(1.0) - floor_lin) * (np.float32(0.5) - np.float32(0.5) * np.cos(np.float32(math.pi) * u))
                    env[sf : sf + eff_duck] = np.minimum(env[sf : sf + eff_duck], ramp)
                if sf + eff_duck < ef:
                    env[sf + eff_duck : ef] = np.minimum(env[sf + eff_duck : ef], floor_lin)
                rec_end = min(n, ef + swell_n)
                if rec_end > ef:
                    u = np.linspace(0.0, 1.0, rec_end - ef, dtype=np.float32)
                    ramp_up = floor_lin + (np.float32(1.0) - floor_lin) * (np.float32(0.5) - np.float32(0.5) * np.cos(np.float32(math.pi) * u))
                    env[ef:rec_end] = np.minimum(env[ef:rec_end], ramp_up)
            elif kind == "swell_in":
                sf = max(0, int(round(s_t * SR)))
                ef = min(n, int(round(e_t * SR)))
                if ef <= sf + 1:
                    continue
                u = np.linspace(0.0, 1.0, ef - sf, dtype=np.float32)
                ramp_up = floor_lin + (np.float32(1.0) - floor_lin) * (np.float32(0.5) - np.float32(0.5) * np.cos(np.float32(math.pi) * u))
                env[sf:ef] = np.minimum(env[sf:ef], ramp_up)

        return left * env, right * env

    class _NpGuzhengBank:
        """Cached Karplus-Strong silk-string synthesizer for D Yu pitches."""

        def __init__(self, rng: Any, max_note_sec: float = 2.6):
            self.rng = rng
            self.cache: Dict[int, Any] = {m: self._synth_string(m, max_note_sec) for m in D_YU_WITH_PASSING}

        def _synth_string(self, midi: int, dur_sec: float) -> Any:
            freq = midi_to_hz(midi)
            n_total = int(dur_sec * SR)
            period_f = SR / freq
            delay_n = max(12, int(math.floor(period_f)))
            frac = period_f - delay_n

            noise = self.rng.uniform(-1.0, 1.0, size=delay_n).astype(np.float64)
            for _ in range(2):
                noise = 0.5 * (noise + np.roll(noise, 1))
            pluck_k = max(2, int(delay_n * 0.16))
            exc = noise - 0.68 * np.roll(noise, pluck_k)
            phase_init = np.linspace(0.0, TWO_PI, delay_n, endpoint=False)
            exc = exc * 0.72 + np.sin(phase_init) * 0.34 + 0.14 * np.sin(2.0 * phase_init)
            exc /= max(1e-6, float(np.max(np.abs(exc))))

            out = np.zeros(n_total + delay_n + 4, dtype=np.float64)
            out[:delay_n] = exc
            rt60 = 2.8 if midi < 55 else (2.2 if midi < 69 else 1.65)
            g = float(np.clip(10.0 ** (-3.0 * (delay_n / SR) / rt60), 0.965, 0.9965))
            bright = 0.56 if midi < 62 else 0.50

            n_blocks = int(math.ceil(n_total / delay_n))
            prev_tail = exc[-1]
            for b in range(1, n_blocks):
                src_s = (b - 1) * delay_n
                dst_s = b * delay_n
                blk = out[src_s : src_s + delay_n]
                shifted = np.empty_like(blk)
                shifted[0] = prev_tail
                shifted[1:] = blk[:-1]
                prev_tail = blk[-1]
                lp = bright * blk + (1.0 - bright) * shifted
                if frac > 1e-4:
                    lp_prev = np.empty_like(lp)
                    lp_prev[0] = lp[0]
                    lp_prev[1:] = lp[:-1]
                    lp = (1.0 - frac) * lp + frac * lp_prev
                out[dst_s : dst_s + delay_n] = lp * g

            sig = out[:n_total]
            pick_len = min(n_total, int(0.018 * SR))
            if pick_len > 4:
                tp = np.arange(pick_len, dtype=np.float64) / SR
                sig[:pick_len] += (
                    self.rng.uniform(-1.0, 1.0, size=pick_len) * 0.16
                    + np.sin(TWO_PI * min(2400.0, freq * 3.0) * tp) * 0.22
                ) * np.exp(-tp * 260.0)
            att = min(n_total, int(0.0025 * SR))
            if att > 1:
                sig[:att] *= np.linspace(0.0, 1.0, att)
            tail = min(n_total, int(0.12 * SR))
            if tail > 1:
                sig[-tail:] *= np.linspace(1.0, 0.0, tail)
            peak = float(np.max(np.abs(sig)))
            if peak > 1e-6:
                sig /= peak
            return sig.astype(np.float32)

        def trigger(self, left: Any, right: Any, midi: int, time_sec: float, dur_sec: float = 1.6, gain: float = 0.22, pan: Optional[float] = None) -> None:
            if midi not in self.cache:
                midi = min(self.cache.keys(), key=lambda k: abs(k - midi))
            base = self.cache[midi]
            n = min(base.shape[0], max(int(0.08 * SR), int(dur_sec * SR)))
            note = base[:n].copy()
            fade_n = min(n // 2, int(0.08 * SR))
            if fade_n > 1:
                note[-fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
            if pan is None:
                pan = float(np.clip(0.26 + 0.50 * ((midi - 45.0) / 36.0), 0.22, 0.78))
            _np_mix_mono(left, right, note, int(time_sec * SR), pan=pan, gain=gain)

        def trigger_glissando(self, left: Any, right: Any, start_t: float, sweep_dur: float = 0.75, ascending: bool = True, low_midi: int = 50, high_midi: int = 84, gain: float = 0.24) -> None:
            notes = [m for m in D_YU_SCALE if low_midi <= m <= high_midi]
            if not ascending:
                notes = list(reversed(notes))
            if len(notes) < 2:
                return
            k = len(notes)
            for i, m in enumerate(notes):
                frac = i / max(1, k - 1)
                t_off = (frac ** 0.88) * sweep_dur
                pan = 0.20 + 0.62 * frac if ascending else 0.82 - 0.62 * frac
                swell = 0.65 + 0.45 * math.sin(math.pi * frac)
                self.trigger(left, right, m, start_t + t_off, dur_sec=1.35 + 0.45 * frac, gain=gain * swell, pan=pan)

        def trigger_tremolo(self, left: Any, right: Any, midi: int, start_t: float, dur_sec: float, gain: float = 0.16, rate_hz: float = 11.5) -> None:
            step = 1.0 / rate_hz
            n_hits = max(2, int(dur_sec * rate_hz))
            base_pan = float(np.clip(0.28 + 0.48 * ((midi - 45.0) / 36.0), 0.24, 0.76))
            for i in range(n_hits):
                frac = i / max(1, n_hits - 1)
                env = math.sin(math.pi * min(1.0, max(0.05, frac))) ** 0.65
                pan_jit = 0.04 * (1.0 if i % 2 == 0 else -1.0)
                self.trigger(left, right, midi, start_t + i * step, dur_sec=0.35, gain=gain * env * 0.72, pan=base_pan + pan_jit)

    def _np_render_erhu_phrase(
        left: Any, right: Any, phrase_start: float, phrase_dur: float,
        notes: List[Tuple[float, float, int, float, float]], rng: Any,
        gain: float = 0.22, pan: float = 0.53, vibrato_depth: float = 0.19,
    ) -> None:
        n_samples = int(phrase_dur * SR)
        if n_samples < 64 or not notes:
            return
        freq_curve = np.full(n_samples, midi_to_hz(notes[0][2]), dtype=np.float32)
        bow_env = np.zeros(n_samples, dtype=np.float32)
        vib_env = np.zeros(n_samples, dtype=np.float32)
        for idx, (rel_t, dur, midi, slide_semi, weight) in enumerate(notes):
            s_i = max(0, int(rel_t * SR))
            e_i = min(n_samples, int((rel_t + dur) * SR))
            if e_i <= s_i + 16:
                continue
            m_len = e_i - s_i
            t_local = np.arange(m_len, dtype=np.float32) * np.float32(1.0 / SR)
            target_hz = midi_to_hz(midi)
            start_hz = midi_to_hz(midi + slide_semi) if abs(slide_semi) > 0.01 else (midi_to_hz(notes[idx - 1][2]) if idx > 0 else target_hz)
            slide_sec = min(0.11, dur * 0.24)
            if slide_sec > 0.008 and abs(start_hz - target_hz) > 0.1:
                alpha = np.clip(t_local / np.float32(slide_sec), 0.0, 1.0)
                f_seg = start_hz + (target_hz - start_hz) * (0.5 - 0.5 * np.cos(np.float32(math.pi) * alpha))
            else:
                f_seg = np.full(m_len, target_hz, dtype=np.float32)
            freq_curve[s_i:e_i] = f_seg
            att_s = max(0.005, min(0.085, dur * 0.22))
            rel_s = max(0.005, min(0.13, dur * 0.26))
            att = np.sin(np.clip(t_local / np.float32(att_s), 0.0, 1.0) * np.float32(math.pi * 0.5))
            rel = np.sin(np.clip((np.float32(dur) - t_local) / np.float32(rel_s), 0.0, 1.0) * np.float32(math.pi * 0.5))
            swell = 0.78 + 0.26 * np.sin(np.float32(math.pi) * np.clip(t_local / np.float32(max(0.05, dur)), 0.0, 1.0))
            bow_env[s_i:e_i] = np.maximum(bow_env[s_i:e_i], att * rel * swell * np.float32(weight))
            vib_delay = min(0.16, dur * 0.28)
            vib_env[s_i:e_i] = np.maximum(vib_env[s_i:e_i], np.clip((t_local - np.float32(vib_delay)) / np.float32(max(0.08, dur * 0.28)), 0.0, 1.0))

        t_all = np.arange(n_samples, dtype=np.float32) * np.float32(1.0 / SR)
        vib_semi = np.float32(vibrato_depth) * vib_env * np.sin(np.float32(TWO_PI * 5.2) * t_all)
        inst_freq = freq_curve * np.exp2(vib_semi * np.float32(1.0 / 12.0))
        ph = ((TWO_PI / SR) * np.cumsum(inst_freq.astype(np.float64))).astype(np.float32)

        s1 = np.sin(ph)
        two_c1 = np.float32(2.0) * np.cos(ph)
        s_prev = np.zeros_like(s1)
        s_curr = s1
        weights = [math.sin(k * math.pi * 0.14) / k * (1.0 if k % 2 == 1 else 0.85) for k in range(1, 10)]
        sig = s_curr * np.float32(weights[0])
        for k in range(2, 10):
            s_next = two_c1 * s_curr - s_prev
            s_prev = s_curr
            s_curr = s_next
            sig += s_curr * np.float32(weights[k - 1])

        noise = rng.uniform(-1.0, 1.0, size=n_samples).astype(np.float32)
        sig = sig * np.float32(0.55) + (noise - np.roll(noise, 1)) * np.float32(0.032)

        fft_n = 1 << (n_samples - 1).bit_length()
        freqs = np.fft.rfftfreq(fft_n, d=1.0 / SR).astype(np.float32)
        f = np.maximum(freqs, 20.0)
        body_resp = (
            0.42
            + 0.75 / (1.0 + ((f - 340.0) / 180.0) ** 2)
            + 1.55 / (1.0 + ((f - 950.0) / 240.0) ** 2)
            + 0.90 / (1.0 + ((f - 2100.0) / 380.0) ** 2)
        ) * (1.0 / (1.0 + (f / 2750.0) ** 4))
        sig = np.fft.irfft(np.fft.rfft(sig, n=fft_n) * body_resp, n=fft_n)[:n_samples].astype(np.float32) * bow_env
        peak = float(np.max(np.abs(sig)))
        if peak > 1e-6:
            sig *= np.float32(1.0 / max(1.0, peak * 0.72))

        start_idx = int(phrase_start * SR)
        _np_mix_mono(left, right, sig, start_idx, pan=pan, gain=gain)
        _np_mix_mono(left, right, sig * np.float32(0.22), start_idx + int(0.019 * SR), pan=0.22, gain=gain)
        _np_mix_mono(left, right, sig * np.float32(0.19), start_idx + int(0.031 * SR), pan=0.80, gain=gain)

    def _np_add_low_drone(left: Any, right: Any, start_sec: float, dur_sec: float, bass_midi: int, fifth_midi: int, gain: float = 0.20) -> None:
        n = int(dur_sec * SR)
        if n < 64:
            return
        t = np.arange(n, dtype=np.float32) * np.float32(1.0 / SR)
        f0 = midi_to_hz(bass_midi)
        f5 = midi_to_hz(fifth_midi)
        att = max(0.02, min(0.45, dur_sec * 0.28))
        rel = max(0.02, min(0.55, dur_sec * 0.30))
        env = np.minimum(np.float32(1.0), t / np.float32(att)) * np.minimum(np.float32(1.0), np.maximum(np.float32(0.0), (np.float32(dur_sec) - t) / np.float32(rel)))
        bow = np.float32(0.875) + np.float32(0.125) * np.sin(np.float32(math.pi / max(0.05, dur_sec)) * t)
        ph_l = np.float32(TWO_PI * f0 * 1.0011) * t
        ph_r = np.float32(TWO_PI * f0 * 0.9989) * t
        s1_l, c1_l = np.sin(ph_l), np.cos(ph_l)
        s2_l = np.float32(2.0) * s1_l * c1_l
        s3_l = np.float32(2.0) * c1_l * s2_l - s1_l
        s1_r, c1_r = np.sin(ph_r), np.cos(ph_r)
        s2_r = np.float32(2.0) * s1_r * c1_r
        s3_r = np.float32(2.0) * c1_r * s2_r - s1_r
        w5 = np.float32(TWO_PI * f5) * t
        s5, c5 = np.sin(w5), np.cos(w5)
        sig_l = s1_l + np.float32(0.48) * s2_l + np.float32(0.14) * s3_l + np.float32(0.22) * s5
        sig_r = s1_r + np.float32(0.48) * s2_r + np.float32(0.14) * s3_r + np.float32(0.18) * (s5 * np.float32(0.921) + c5 * np.float32(0.389))
        g = env * bow * np.float32(gain * 0.48)
        _np_mix_stereo(left, right, sig_l * g, sig_r * g, int(start_sec * SR))

    def _compose_palace_drama_numpy(
        duration: float,
        bpm: int,
        acts: List[Tuple[int, float, float]],
        seed: int = 42,
        act_intensities: Optional[List[Optional[str]]] = None,
        ending_style: str = "",
        bgm_cues: Optional[List[Dict[str, Any]]] = None,
        speech_eq: bool = False,
    ) -> Tuple[Any, Any]:
        rng = np.random.default_rng(seed)
        beat_sec = 60.0 / bpm
        bar_sec = beat_sec * 4.0
        n_samples = int(math.ceil(duration * SR))
        left = np.zeros(n_samples, dtype=np.float64)
        right = np.zeros(n_samples, dtype=np.float64)
        guzheng = _NpGuzhengBank(rng, max_note_sec=2.6)
        is_cliffhanger = (ending_style or "").strip().lower() == "cliffhanger"

        bell_cache: Dict[Tuple[int, float], Tuple[Any, Any]] = {}
        dagu_cache: Dict[bool, Any] = {}

        def _add_bell(t_s: float, base_hz: float, gain: float, pan: float) -> None:
            key = (round(base_hz * 10), round(pan, 2))
            if key not in bell_cache:
                n = int(5.4 * SR)
                t = np.arange(n, dtype=np.float32) * np.float32(1.0 / SR)
                att = np.minimum(np.float32(1.0), t / np.float32(0.022))
                sl = np.zeros(n, dtype=np.float32)
                sr_v = np.zeros(n, dtype=np.float32)
                for ratio, amp, dec in ((1.0, 1.0, 0.42), (2.0, 0.55, 0.62), (2.42, 0.34, 0.85), (3.01, 0.22, 1.15), (4.18, 0.12, 1.55)):
                    f = base_hz * ratio
                    env = np.exp(-t * np.float32(dec)) * np.float32(amp)
                    sl += np.sin(np.float32(TWO_PI * f) * t) * env
                    sr_v += np.sin(np.float32(TWO_PI * (f + 0.42)) * t + np.float32(0.3)) * env
                bell_cache[key] = (sl * att * np.float32(math.cos(pan * math.pi * 0.5) * 1.35), sr_v * att * np.float32(math.sin(pan * math.pi * 0.5) * 1.35))
            bl, br = bell_cache[key]
            _np_mix_stereo(left, right, bl, br, int(t_s * SR), gain=gain)

        def _add_dagu(t_s: float, gain: float, soft: bool = False) -> None:
            if soft not in dagu_cache:
                dur = 0.45 if soft else 0.95
                n = int(dur * SR)
                t = np.arange(n, dtype=np.float32) * np.float32(1.0 / SR)
                if soft:
                    f = 36.0 + 38.0 * np.exp(-t * np.float32(24.0))
                    ph = np.float32(TWO_PI / SR) * np.cumsum(f)
                    sig = np.sin(ph) * np.minimum(np.float32(1.0), t / np.float32(0.010)) * np.exp(-t * np.float32(10.5))
                else:
                    f = 42.0 + 68.0 * np.exp(-t * np.float32(28.0))
                    ph = np.float32(TWO_PI / SR) * np.cumsum(f)
                    body = np.sin(ph) + np.float32(0.32) * np.sin(np.float32(2.0) * ph)
                    slap = rng.uniform(-1.0, 1.0, size=n).astype(np.float32)
                    slap = np.float32(0.5) * (slap + np.roll(slap, 1)) * np.exp(-t * np.float32(85.0)) * np.float32(0.25)
                    sig = np.tanh((body + slap) * np.float32(1.15)) * np.minimum(np.float32(1.0), t / np.float32(0.004)) * np.exp(-t * np.float32(4.8))
                dagu_cache[soft] = sig.astype(np.float32)
            _np_mix_mono(left, right, dagu_cache[soft], int(t_s * SR), pan=0.50, gain=gain)

        def _add_gong(t_s: float, gain: float = 0.35) -> None:
            n = int(4.6 * SR)
            t = np.arange(n, dtype=np.float32) * np.float32(1.0 / SR)
            bloom = 1.0 - 0.025 * np.exp(-t * np.float32(3.5)) + 0.012 * np.exp(-t * np.float32(0.9))
            ph = np.float32(TWO_PI / SR) * np.cumsum(np.float32(98.0) * bloom)
            sl = np.zeros(n, dtype=np.float32)
            sr_v = np.zeros(n, dtype=np.float32)
            for ratio, amp, dec in ((1.0, 1.0, 0.52), (1.48, 0.55, 0.72), (2.03, 0.42, 0.92), (2.74, 0.30, 1.25), (3.61, 0.20, 1.65)):
                env = np.exp(-t * np.float32(dec)) * np.float32(amp)
                sl += np.sin(ph * np.float32(ratio)) * env
                sr_v += np.sin(ph * np.float32(ratio * 1.004) + np.float32(0.5)) * env
            wash = rng.uniform(-1.0, 1.0, size=n).astype(np.float32)
            wash = (wash + np.roll(wash, 1) + np.roll(wash, 2)) * np.float32(1.0 / 3.0) * np.exp(-t * np.float32(3.2)) * np.float32(0.14)
            att = np.minimum(np.float32(1.0), t / np.float32(0.012)) * np.float32(gain)
            _np_mix_stereo(left, right, np.tanh((sl + wash) * np.float32(0.85)) * att, np.tanh((sr_v - wash * np.float32(0.8)) * np.float32(0.85)) * att, int(t_s * SR))

        def _add_clapper(t_s: float, gain: float = 0.15, pan: float = 0.62) -> None:
            n = int(0.11 * SR)
            t = np.arange(n, dtype=np.float32) * np.float32(1.0 / SR)
            wood = (np.sin(np.float32(TWO_PI * 780.0) * t) * np.float32(0.65) + np.sin(np.float32(TWO_PI * 1340.0) * t) * np.float32(0.45) + rng.uniform(-0.35, 0.35, size=n).astype(np.float32)) * np.exp(-t * np.float32(58.0))
            _np_mix_mono(left, right, wood * np.float32(gain), int(t_s * SR), pan=pan)

        def _stage_at(t_val: float) -> Tuple[int, float, float]:
            for idx, (_, a_s, a_e) in enumerate(acts):
                if t_val < a_e or idx == len(acts) - 1:
                    st = resolve_act_stage(idx, len(acts), t_val, duration, act_intensities)
                    if is_cliffhanger and idx == len(acts) - 1 and st == 5:
                        explicit_inten = (act_intensities[idx] or "").strip().lower() if (act_intensities and idx < len(act_intensities)) else ""
                        if explicit_inten != "epilogue":
                            st = 4
                    return st, a_s, a_e
            return (4 if is_cliffhanger else 5), acts[-1][1], acts[-1][2]

        final_s, final_e = acts[-1][1], acts[-1][2]
        final_dur = max(1.0, final_e - final_s)
        epilogue_len = min(25.0, final_dur * 0.55) if duration >= 20.0 else duration * 0.25
        epilogue_start = (duration + 10.0) if is_cliffhanger else max(final_s + min(2.0, final_dur * 0.2), final_e - epilogue_len)

        # Act transition glissandi & final act imperial gong
        for idx in range(1, len(acts)):
            t_bound = acts[idx][1]
            asc = idx != 1 and idx != len(acts) - 1
            guzheng.trigger_glissando(left, right, max(0.0, t_bound - 0.7), sweep_dur=0.72, ascending=asc, low_midi=48, high_midi=84, gain=0.20)
        if len(acts) >= 2:
            _add_gong(final_s, gain=0.35)
        if not is_cliffhanger:
            guzheng.trigger_glissando(left, right, epilogue_start, sweep_dur=1.0, ascending=True, low_midi=50, high_midi=81, gain=0.17)

        total_bars = int(math.ceil(duration / bar_sec))
        for bar_idx in range(total_bars):
            bar_t = bar_idx * bar_sec
            if bar_t >= duration:
                break
            stage, _, _ = _stage_at(bar_t)
            in_epilogue = (not is_cliffhanger) and (bar_t >= epilogue_start - 0.15)
            bass_midi, fifth_midi, arp_pool = PALACE_PROGRESSION[bar_idx % len(PALACE_PROGRESSION)]
            rem_bar = min(bar_sec, duration - bar_t)

            if bar_idx % 8 == 0 and not in_epilogue:
                _add_bell(bar_t, midi_to_hz(50 if bar_idx % 16 == 0 else 57), 0.14 if stage <= 2 else 0.17, 0.38 if bar_idx % 16 == 0 else 0.62)

            drone_gain = (0.11 * max(0.25, 1.0 - (bar_t - epilogue_start) / max(1.0, epilogue_len))) if in_epilogue else (0.14, 0.18, 0.22, 0.26, 0.22)[stage - 1]
            _np_add_low_drone(left, right, bar_t, rem_bar + 0.08, bass_midi, fifth_midi, gain=drone_gain)

            if stage == 1:
                for bo, pi, g in ((0.0, 2, 0.18), (0.5, 4, 0.14), (1.5, 1, 0.15), (2.0, 3, 0.17), (3.0, 5, 0.14), (3.5, 2, 0.13)):
                    nt = bar_t + bo * beat_sec
                    if nt < duration - 0.1:
                        guzheng.trigger(left, right, arp_pool[pi % len(arp_pool)], nt, dur_sec=1.9, gain=g)
            elif stage == 2:
                ost = (0, 1, 2, 1, 0, 2, 3, 1)
                for e8 in range(8):
                    nt = bar_t + e8 * (beat_sec * 0.5)
                    if nt < duration - 0.1:
                        guzheng.trigger(left, right, arp_pool[ost[e8] % 4], nt, dur_sec=1.1, gain=0.17 if e8 in (0, 3, 6) else 0.12)
                _add_dagu(bar_t, 0.22, soft=True)
                _add_dagu(bar_t + beat_sec * 0.72, 0.14, soft=True)
            elif stage == 3:
                for q in range(4):
                    qt = bar_t + q * beat_sec
                    if qt < duration - 0.1:
                        guzheng.trigger(left, right, arp_pool[q % 3], qt, dur_sec=1.2, gain=0.18)
                        guzheng.trigger(left, right, arp_pool[(q + 2) % len(arp_pool)], qt + beat_sec * 0.5, dur_sec=0.9, gain=0.13)
                if bar_idx % 2 == 0:
                    guzheng.trigger_tremolo(left, right, arp_pool[4], bar_t + beat_sec * 2.0, beat_sec * 1.6, gain=0.15)
                else:
                    guzheng.trigger_tremolo(left, right, arp_pool[5], bar_t + beat_sec * 1.0, beat_sec * 1.2, gain=0.15)
                    _add_clapper(bar_t + beat_sec * 3.0, gain=0.13)
                _add_dagu(bar_t, 0.30, soft=False)
                _add_dagu(bar_t + beat_sec * 2.0, 0.24, soft=False)
            elif stage == 4:
                arp_seq = (0, 2, 4, 5, 3, 4, 2, 5)
                for e8 in range(8):
                    nt = bar_t + e8 * (beat_sec * 0.5)
                    if nt < duration - 0.1:
                        guzheng.trigger(left, right, arp_pool[arp_seq[e8]], nt, dur_sec=1.4, gain=0.17 if e8 % 2 == 0 else 0.13)
                if bar_idx % 2 == 1:
                    guzheng.trigger_glissando(left, right, bar_t + beat_sec * 3.0, sweep_dur=0.55, ascending=True, low_midi=57, high_midi=84, gain=0.18)
                else:
                    guzheng.trigger_tremolo(left, right, arp_pool[5], bar_t + beat_sec * 2.0, beat_sec * 1.8, gain=0.16)
                for bo, g in ((0.0, 0.34), (1.5, 0.24), (2.5, 0.28), (3.5, 0.22)):
                    _add_dagu(bar_t + bo * beat_sec, g, soft=False)
            else:
                if not in_epilogue:
                    for q in range(4):
                        qt = bar_t + q * beat_sec
                        if qt < epilogue_start:
                            guzheng.trigger(left, right, arp_pool[q % len(arp_pool)], qt, dur_sec=1.2, gain=0.17)
                    if bar_t + beat_sec * 2.0 < epilogue_start:
                        _add_dagu(bar_t, 0.33, soft=False)
                        _add_dagu(bar_t + beat_sec * 2.0, 0.28, soft=False)
                        _add_clapper(bar_t + beat_sec * 1.0, 0.15, 0.35)
                        _add_clapper(bar_t + beat_sec * 3.0, 0.15, 0.65)
                else:
                    for bo, pi, g in ((0.0, 0, 0.19), (1.0, 2, 0.16), (1.5, 4, 0.18), (2.5, 5, 0.17), (3.5, 2, 0.13)):
                        nt = bar_t + bo * beat_sec
                        if nt < duration - 0.35:
                            fade_tail = min(1.0, max(0.25, (duration - nt) / 8.0))
                            guzheng.trigger(left, right, arp_pool[pi % len(arp_pool)], nt, dur_sec=2.4, gain=g * fade_tail)

        # Expressive Erhu phrases mapped to Act stages (Stage 3 transposed +12st to D4..D5 natural erhu tessitura)
        def b2s(beats: float) -> float:
            return beats * beat_sec

        stage_motifs = {
            1: ([(b2s(2.0), b2s(3.5), 69, -2.0, 0.85), (b2s(5.5), b2s(2.5), 67, 0.0, 0.78), (b2s(8.0), b2s(3.0), 65, 2.0, 0.82), (b2s(11.0), b2s(1.0), 64, 0.0, 0.68), (b2s(12.0), b2s(4.0), 62, 0.0, 0.90)], 17.0, 0.19, 0.54, 0.17),
            2: ([(b2s(0.0), b2s(3.5), 74, -2.0, 0.92), (b2s(3.5), b2s(1.5), 72, 0.0, 0.82), (b2s(5.0), b2s(3.0), 69, 0.0, 0.95), (b2s(8.0), b2s(2.5), 67, 0.0, 0.86), (b2s(10.5), b2s(1.5), 65, 0.0, 0.80), (b2s(12.0), b2s(4.0), 62, 0.0, 0.92)], 18.0, 0.25, 0.52, 0.21),
            3: ([(b2s(0.0), b2s(2.0), 62, -3.0, 0.95), (b2s(2.0), b2s(2.0), 69, 0.0, 0.92), (b2s(4.0), b2s(3.0), 67, 0.0, 0.88), (b2s(8.0), b2s(3.5), 69, -2.0, 0.96), (b2s(13.0), b2s(2.5), 72, -3.0, 0.92), (b2s(15.5), b2s(3.5), 74, 0.0, 0.98)], 20.0, 0.26, 0.47, 0.20),
            4: ([(b2s(0.0), b2s(3.0), 74, -5.0, 0.96), (b2s(3.0), b2s(2.0), 77, 0.0, 0.95), (b2s(5.0), b2s(3.0), 79, 0.0, 1.00), (b2s(8.0), b2s(4.0), 81, -2.0, 1.05), (b2s(12.0), b2s(2.0), 84, -3.0, 1.00), (b2s(14.0), b2s(3.0), 79, 0.0, 0.94), (b2s(17.0), b2s(4.0), 74, 0.0, 0.96)], 22.0, 0.27, 0.52, 0.23),
            5: ([(b2s(1.0), b2s(3.5), 69, -2.0, 0.82), (b2s(4.5), b2s(2.5), 72, 0.0, 0.80), (b2s(7.0), b2s(4.0), 74, 0.0, 0.88), (b2s(11.0), b2s(3.0), 69, 0.0, 0.76), (b2s(14.0), b2s(6.0), 62, 0.0, 0.74)], 21.0, 0.19, 0.53, 0.16),
        }
        for idx, (_, a_s, a_e) in enumerate(acts):
            stage = resolve_act_stage(idx, len(acts), a_s, duration, act_intensities)
            if is_cliffhanger and idx == len(acts) - 1 and stage == 5:
                stage = 4
            motif, beats_len, g, pan, vib = stage_motifs[stage]
            t_cur = a_s + min(2.0, max(0.3, (a_e - a_s) * 0.1))
            while t_cur + 5.0 < a_e:
                span = min(b2s(beats_len), a_e - t_cur - 0.4)
                _np_render_erhu_phrase(left, right, t_cur, span, motif, rng, gain=g, pan=pan, vibrato_depth=vib)
                if stage == 4:
                    oct_m = [(s, d, m - 12, sl, w * 0.75) for (s, d, m, sl, w) in motif]
                    _np_render_erhu_phrase(left, right, t_cur, span, oct_m, rng, gain=0.15, pan=0.44, vibrato_depth=0.14)
                t_cur += b2s(beats_len + 4.0)

        if is_cliffhanger:
            # Abrupt dramatic cliffhanger sting on the final beat (no moonlight resolve)
            sting_t = max(0.0, duration - min(0.85, duration * 0.12))
            _add_dagu(sting_t, 0.42, soft=False)
            _add_gong(sting_t, gain=0.34)
        else:
            # Final luminous moonlight D-A-D chord
            end_chord_t = max(0.0, duration - min(4.8, duration * 0.18))
            for idx_c, m_c in enumerate((50, 57, 62, 69, 74)):
                guzheng.trigger(left, right, m_c, end_chord_t + idx_c * 0.14, dur_sec=2.6, gain=0.15)

        left, right = _np_apply_speech_eq(left, right, speech_carve=speech_eq)
        fade_in_n = min(n_samples // 3, int(1.2 * SR))
        fade_out_n = min(n_samples // 2, int((0.08 if is_cliffhanger else 2.5) * SR))
        if fade_in_n > 1:
            fi = (0.5 - 0.5 * np.cos(math.pi * np.arange(fade_in_n, dtype=np.float32) / np.float32(fade_in_n))).astype(np.float32)
            left[:fade_in_n] *= fi
            right[:fade_in_n] *= fi
        if fade_out_n > 1:
            fo = (0.5 - 0.5 * np.cos(math.pi * np.arange(fade_out_n, dtype=np.float32) / np.float32(fade_out_n))).astype(np.float32)
            left[-fade_out_n:] *= fo[::-1]
            right[-fade_out_n:] *= fo[::-1]

        left = np.tanh(left * np.float32(1.15))
        right = np.tanh(right * np.float32(1.15))
        peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))))
        target_peak = 10.0 ** (-3.0 / 20.0)
        if peak > 1e-8:
            scale = np.float32(target_peak / peak)
            left *= scale
            right *= scale

        if bgm_cues:
            left, right = _np_apply_bgm_cues(left, right, bgm_cues, duration)
        return left, right

    def _compose_epic_story_numpy(
        duration: float,
        bpm: int,
        acts: List[Tuple[int, float, float]],
        seed: int = 42,
        act_intensities: Optional[List[Optional[str]]] = None,
        ending_style: str = "",
        bgm_cues: Optional[List[Dict[str, Any]]] = None,
        speech_eq: bool = False,
    ) -> Tuple[Any, Any]:
        """Vectorized cinematic Act-aware narrative score for documentaries, explainers & dramatic stories."""
        rng = np.random.default_rng(seed)
        beat_sec = 60.0 / bpm
        bar_sec = beat_sec * 4.0
        n_samples = int(math.ceil(duration * SR))
        left = np.zeros(n_samples, dtype=np.float64)
        right = np.zeros(n_samples, dtype=np.float64)
        is_cliffhanger = (ending_style or "").strip().lower() == "cliffhanger"

        def _stage_at(t_val: float) -> int:
            for idx, (_, a_s, a_e) in enumerate(acts):
                if t_val < a_e or idx == len(acts) - 1:
                    st = resolve_act_stage(idx, len(acts), t_val, duration, act_intensities)
                    if is_cliffhanger and idx == len(acts) - 1 and st == 5:
                        explicit_inten = (act_intensities[idx] or "").strip().lower() if (act_intensities and idx < len(act_intensities)) else ""
                        if explicit_inten != "epilogue":
                            st = 4
                    return st
            return 4 if is_cliffhanger else 5

        final_s, final_e = acts[-1][1], acts[-1][2]
        final_dur = max(1.0, final_e - final_s)
        epilogue_len = min(20.0, final_dur * 0.5) if duration >= 20.0 else duration * 0.25
        epilogue_start = (duration + 10.0) if is_cliffhanger else max(final_s + min(1.5, final_dur * 0.2), final_e - epilogue_len)

        # Cached warm muted pluck + cinematic tom/kick
        pluck_cache: Dict[int, Any] = {}
        tom_cache: Dict[str, Any] = {}

        def _add_pluck(midi: int, t_s: float, gain: float, pan: float) -> None:
            if midi not in pluck_cache:
                dur_p = 0.75
                np_len = int(dur_p * SR)
                tp = np.arange(np_len, dtype=np.float32) * np.float32(1.0 / SR)
                f = midi_to_hz(midi)
                sig = (
                    np.sin(np.float32(TWO_PI * f) * tp) * np.float32(0.70)
                    + np.sin(np.float32(TWO_PI * 2.0 * f) * tp) * np.float32(0.24) * np.exp(-tp * np.float32(10.0))
                    + np.sin(np.float32(TWO_PI * 3.0 * f) * tp) * np.float32(0.10) * np.exp(-tp * np.float32(18.0))
                ) * np.minimum(np.float32(1.0), tp / np.float32(0.008)) * np.exp(-tp * np.float32(5.2))
                pluck_cache[midi] = sig.astype(np.float32)
            _np_mix_mono(left, right, pluck_cache[midi], int(t_s * SR), pan=pan, gain=gain)

        def _add_perc(kind: str, t_s: float, gain: float, pan: float = 0.5) -> None:
            if kind not in tom_cache:
                if kind == "kick":
                    n = int(0.45 * SR)
                    t = np.arange(n, dtype=np.float32) * np.float32(1.0 / SR)
                    ph = np.float32(TWO_PI / SR) * np.cumsum(38.0 + 62.0 * np.exp(-t * np.float32(22.0)))
                    sig = np.tanh(np.sin(ph) * np.float32(1.2)) * np.minimum(np.float32(1.0), t / np.float32(0.006)) * np.exp(-t * np.float32(7.5))
                elif kind == "tom":
                    n = int(0.32 * SR)
                    t = np.arange(n, dtype=np.float32) * np.float32(1.0 / SR)
                    ph = np.float32(TWO_PI / SR) * np.cumsum(72.0 + 54.0 * np.exp(-t * np.float32(18.0)))
                    slap = rng.uniform(-0.2, 0.2, size=n).astype(np.float32) * np.exp(-t * np.float32(65.0))
                    sig = np.tanh(np.sin(ph) + slap) * np.minimum(np.float32(1.0), t / np.float32(0.004)) * np.exp(-t * np.float32(9.0))
                else:  # tick
                    n = int(0.055 * SR)
                    t = np.arange(n, dtype=np.float32) * np.float32(1.0 / SR)
                    sig = (np.sin(np.float32(TWO_PI * 520.0) * t) * np.float32(0.55) + rng.uniform(-0.35, 0.35, size=n).astype(np.float32)) * np.exp(-t * np.float32(72.0))
                tom_cache[kind] = sig.astype(np.float32)
            _np_mix_mono(left, right, tom_cache[kind], int(t_s * SR), pan=pan, gain=gain)

        total_bars = int(math.ceil(duration / bar_sec))
        for bar_idx in range(total_bars):
            bar_t = bar_idx * bar_sec
            if bar_t >= duration:
                break
            stage = _stage_at(bar_t)
            in_epilogue = (not is_cliffhanger) and (bar_t >= epilogue_start - 0.15)
            bass_midi, chord = EPIC_PROGRESSION[bar_idx % len(EPIC_PROGRESSION)]
            rem_bar = min(bar_sec, duration - bar_t)

            # 1. Warm dark bass drone
            drone_g = 0.13 if in_epilogue else (0.16, 0.20, 0.24, 0.28, 0.21)[stage - 1]
            _np_add_low_drone(left, right, bar_t, rem_bar + 0.10, bass_midi, bass_midi + 7, gain=drone_g)

            # 2. Evolving stereo melodic synth/string pad across the bar
            p_len = int((rem_bar + 0.12) * SR)
            if p_len > 64:
                tp = np.arange(p_len, dtype=np.float32) * np.float32(1.0 / SR)
                p_dur = p_len / SR
                env = np.minimum(np.float32(1.0), tp / np.float32(min(0.55, p_dur * 0.28))) * np.minimum(
                    np.float32(1.0), np.maximum(np.float32(0.0), (np.float32(p_dur) - tp) / np.float32(min(0.60, p_dur * 0.30)))
                )
                swell = np.float32(0.70) + np.float32(0.30) * np.sin(np.float32(math.pi / max(0.1, p_dur)) * tp)
                sl = np.zeros(p_len, dtype=np.float32)
                sr_v = np.zeros(p_len, dtype=np.float32)
                n_voices = 3 if stage <= 2 else (5 if stage == 4 else 4)
                for v_idx, m in enumerate(chord[:n_voices]):
                    f = midi_to_hz(m + (12 if (stage == 4 and v_idx >= 3) else 0))
                    fl = f * (1.0018 if v_idx % 2 == 0 else 0.9982)
                    fr = f * (0.9982 if v_idx % 2 == 0 else 1.0018)
                    sl += np.sin(np.float32(TWO_PI * fl) * tp) + np.float32(0.18) * np.sin(np.float32(TWO_PI * 2.0 * fl) * tp)
                    sr_v += np.sin(np.float32(TWO_PI * fr) * tp) + np.float32(0.18) * np.sin(np.float32(TWO_PI * 2.0 * fr) * tp)
                pad_g = np.float32((0.055 if in_epilogue else (0.048, 0.056, 0.065, 0.074, 0.062)[stage - 1])) * env * swell
                _np_mix_stereo(left, right, sl * pad_g, sr_v * pad_g, int(bar_t * SR))

            # 3. Rhythmic narrative pulse & Act 3/4 percussion lift
            if in_epilogue:
                for bo, ci in ((0.0, 0), (1.5, 2), (2.5, 4)):
                    nt = bar_t + bo * beat_sec
                    if nt < duration - 0.4:
                        _add_pluck(chord[ci % len(chord)], nt, 0.14 * min(1.0, max(0.25, (duration - nt) / 6.0)), 0.42 + 0.16 * (ci % 2))
            else:
                arp_steps = (0, 2, 4, 6) if stage == 1 else ((0, 1, 2, 4, 5, 6) if stage == 2 else range(8))
                for e8 in arp_steps:
                    nt = bar_t + e8 * (beat_sec * 0.5)
                    if nt < duration - 0.1:
                        m = chord[(e8 + bar_idx) % len(chord)]
                        g = (0.12 if stage == 1 else (0.14 if stage == 2 else 0.16)) * (1.15 if e8 % 2 == 0 else 0.82)
                        _add_pluck(m, nt, g, 0.34 if e8 % 2 == 0 else 0.66)

                if stage >= 2:
                    _add_perc("kick", bar_t, 0.22 if stage == 2 else (0.30 if stage == 3 else 0.36))
                    _add_perc("tick", bar_t + beat_sec * 1.0, 0.08, 0.38)
                    _add_perc("tick", bar_t + beat_sec * 3.0, 0.08, 0.62)
                if stage >= 3:
                    _add_perc("kick", bar_t + beat_sec * 2.0, 0.26 if stage == 3 else 0.30)
                    _add_perc("tom", bar_t + beat_sec * 1.5, 0.18, 0.35)
                    _add_perc("tom", bar_t + beat_sec * 3.5, 0.20, 0.65)
                if stage == 4:
                    _add_perc("kick", bar_t + beat_sec * 2.5, 0.24)
                    _add_perc("tom", bar_t + beat_sec * 3.0, 0.22, 0.55)

        if is_cliffhanger:
            sting_t = max(0.0, duration - min(0.70, duration * 0.12))
            _add_perc("kick", sting_t, 0.45)
            _add_perc("tom", sting_t, 0.32, 0.42)
            _add_perc("tom", sting_t + 0.04, 0.30, 0.58)

        left, right = _np_apply_speech_eq(left, right, speech_carve=speech_eq)
        fade_in_n = min(n_samples // 3, int(1.0 * SR))
        fade_out_n = min(n_samples // 2, int((0.08 if is_cliffhanger else 2.2) * SR))
        if fade_in_n > 1:
            fi = (0.5 - 0.5 * np.cos(math.pi * np.arange(fade_in_n, dtype=np.float32) / np.float32(fade_in_n))).astype(np.float32)
            left[:fade_in_n] *= fi
            right[:fade_in_n] *= fi
        if fade_out_n > 1:
            fo = (0.5 - 0.5 * np.cos(math.pi * np.arange(fade_out_n, dtype=np.float32) / np.float32(fade_out_n))).astype(np.float32)
            left[-fade_out_n:] *= fo[::-1]
            right[-fade_out_n:] *= fo[::-1]

        left = np.tanh(left * np.float32(1.10))
        right = np.tanh(right * np.float32(1.10))
        peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))))
        target_peak = 10.0 ** (-3.0 / 20.0)
        if peak > 1e-8:
            scale = np.float32(target_peak / peak)
            left *= scale
            right *= scale

        if bgm_cues:
            left, right = _np_apply_bgm_cues(left, right, bgm_cues, duration)
        return left, right


# =============================================================================
# Fast Pure-Stdlib Act-Aware Long-Form Composers (when NumPy is unavailable)
# =============================================================================

def _stdlib_synth_stage_loop(
    mood: str,
    stage: int,
    in_epilogue: bool,
    bpm: int,
    seed: int,
) -> bytes:
    """Synthesize a rich, seamlessly loopable 4-bar stereo 48kHz PCM block for a given Act stage."""
    sr_h = SR // 2  # 24 kHz synth + C-speed 2x upsample keeps 6 stages under 2.5s total
    beat_sec = 60.0 / bpm
    bar_sec = beat_sec * 4.0
    n_bars = 4
    dur_h = bar_sec * n_bars
    n = int(round(dur_h * sr_h))
    seam_n = min(int(0.020 * sr_h), n // 8)
    n_gen = n + seam_n
    n_out = int(round(dur_h * SR))
    l = [0.0] * n_gen
    r = [0.0] * n_gen
    rng = random.Random(seed + stage * 31 + (100 if in_epilogue else 0))
    sin = math.sin
    cos = math.cos
    exp = math.exp
    inv_sr = 1.0 / sr_h

    def _add_pluck_note(midi: int, t_start: float, note_dur: float, gain: float, pan: float, bright: float = 0.35) -> None:
        s_i = int(t_start * sr_h)
        m_len = min(int(note_dur * sr_h), n_gen - s_i)
        if m_len < 16 or s_i < 0:
            return
        freq = midi_to_hz(midi)
        step_w = TWO_PI * freq * inv_sr
        cs, sn = cos(step_w), sin(step_w)
        s1, c1 = 0.0, 1.0
        d1 = exp(-3.4 * inv_sr)
        d2 = exp(-6.8 * inv_sr)
        e1 = 0.72
        e2 = bright
        att_n = max(1, int(0.004 * sr_h))
        gl = cos(pan * math.pi * 0.5) * gain
        gr = sin(pan * math.pi * 0.5) * gain
        for j in range(m_len):
            s2 = 2.0 * s1 * c1
            v = s1 * e1 + s2 * e2
            if j < att_n:
                v *= j / att_n
            l[s_i + j] += v * gl
            r[s_i + j] += v * gr
            ns1 = s1 * cs + c1 * sn
            c1 = c1 * cs - s1 * sn
            s1 = ns1
            e1 *= d1
            e2 *= d2

    def _add_drone_bar(bar_t: float, bar_dur: float, bass_midi: int, fifth_midi: int, gain: float) -> None:
        s_i = int(bar_t * sr_h)
        m_len = min(int(bar_dur * sr_h), n_gen - s_i)
        if m_len < 32:
            return
        f0 = midi_to_hz(bass_midi)
        f5 = midi_to_hz(fifth_midi)
        wl = TWO_PI * f0 * 1.0012 * inv_sr
        wr = TWO_PI * f0 * 0.9988 * inv_sr
        w5 = TWO_PI * f5 * inv_sr
        cl_s, sl_s = cos(wl), sin(wl)
        cr_s, sr_s = cos(wr), sin(wr)
        c5_s, s5_s = cos(w5), sin(w5)
        sl1, cl1 = 0.0, 1.0
        sr1, cr1 = 0.0, 1.0
        s5, c5 = 0.0, 1.0
        att_n = max(1, int(min(0.40, bar_dur * 0.25) * sr_h))
        rel_n = max(1, int(min(0.45, bar_dur * 0.28) * sr_h))
        g_base = gain * 0.48
        for j in range(m_len):
            env = (j / att_n if j < att_n else 1.0) * ((m_len - j) / rel_n if j > m_len - rel_n else 1.0)
            g = env * g_base
            l[s_i + j] += (sl1 + 0.35 * (2.0 * sl1 * cl1) + 0.20 * s5) * g
            r[s_i + j] += (sr1 + 0.35 * (2.0 * sr1 * cr1) + 0.16 * c5) * g
            nsl = sl1 * cl_s + cl1 * sl_s
            cl1 = cl1 * cl_s - sl1 * sl_s
            sl1 = nsl
            nsr = sr1 * cr_s + cr1 * sr_s
            cr1 = cr1 * cr_s - sr1 * sr_s
            sr1 = nsr
            ns5 = s5 * c5_s + c5 * s5_s
            c5 = c5 * c5_s - s5 * s5_s
            s5 = ns5

    def _add_drum(t_s: float, gain: float, soft: bool = False) -> None:
        s_i = int(t_s * sr_h)
        d_dur = 0.35 if soft else 0.65
        m_len = min(int(d_dur * sr_h), n_gen - s_i)
        if m_len < 16 or s_i < 0:
            return
        ph = 0.0
        f_lo = 36.0 if soft else 42.0
        f_hi = 34.0 if soft else 64.0
        dec = 10.5 if soft else 5.2
        e = 1.0
        de = exp(-dec * inv_sr)
        for j in range(m_len):
            t = j * inv_sr
            f = f_lo + f_hi * exp(-t * 24.0)
            ph += TWO_PI * f * inv_sr
            v = sin(ph) * e * gain
            l[s_i + j] += v
            r[s_i + j] += v
            e *= de

    def _add_bowed_line(notes_seq: List[Tuple[float, float, int]], gain: float, pan: float) -> None:
        gl = cos(pan * math.pi * 0.5) * gain
        gr = sin(pan * math.pi * 0.5) * gain
        for rel_b, dur_b, midi in notes_seq:
            s_i = int(rel_b * beat_sec * sr_h)
            m_len = min(int(dur_b * beat_sec * sr_h), n_gen - s_i)
            if m_len < 32 or s_i < 0:
                continue
            freq = midi_to_hz(midi)
            att_n = max(1, int(min(0.10, dur_b * beat_sec * 0.25) * sr_h))
            rel_n = max(1, int(min(0.14, dur_b * beat_sec * 0.28) * sr_h))
            ph = 0.0
            for j in range(m_len):
                t = j * inv_sr
                vib = 1.0 + 0.008 * sin(TWO_PI * 5.1 * t)
                ph += TWO_PI * freq * vib * inv_sr
                env = (j / att_n if j < att_n else 1.0) * ((m_len - j) / rel_n if j > m_len - rel_n else 1.0)
                v = (sin(ph) * 0.62 + sin(2.0 * ph) * 0.28 + sin(3.0 * ph) * 0.12) * env
                l[s_i + j] += v * gl
                r[s_i + j] += v * gr

    seam_sec = seam_n * inv_sr
    if mood == "palace_drama":
        for b in range(n_bars):
            bt = b * bar_sec
            extra = seam_sec if b == n_bars - 1 else 0.0
            bass_m, fifth_m, arp_pool = PALACE_PROGRESSION[b % len(PALACE_PROGRESSION)]
            dg = 0.12 if in_epilogue else (0.14, 0.18, 0.22, 0.26, 0.21)[stage - 1]
            _add_drone_bar(bt, bar_sec + extra, bass_m, fifth_m, dg)

            if in_epilogue:
                for bo, pi, g in ((0.0, 0, 0.19), (1.0, 2, 0.16), (1.5, 4, 0.18), (2.5, 5, 0.17)):
                    _add_pluck_note(arp_pool[pi % len(arp_pool)], bt + bo * beat_sec, 1.5, g, 0.30 + 0.08 * pi)
            elif stage == 1:
                for bo, pi, g in ((0.0, 2, 0.18), (1.0, 4, 0.14), (2.0, 3, 0.17), (3.0, 5, 0.14)):
                    _add_pluck_note(arp_pool[pi % len(arp_pool)], bt + bo * beat_sec, 1.4, g, 0.28 + 0.08 * pi)
            elif stage == 2:
                ost = (0, 1, 2, 1, 0, 2, 3, 1)
                for e8 in range(8):
                    _add_pluck_note(arp_pool[ost[e8] % 4], bt + e8 * beat_sec * 0.5, 0.9, 0.16 if e8 in (0, 3, 6) else 0.11, 0.35 + 0.07 * (e8 % 4))
                _add_drum(bt, 0.22, soft=True)
                _add_drum(bt + beat_sec * 0.72, 0.14, soft=True)
            elif stage == 3:
                for q in range(4):
                    _add_pluck_note(arp_pool[q % 3], bt + q * beat_sec, 1.0, 0.18, 0.34 + 0.10 * (q % 3))
                    _add_pluck_note(arp_pool[(q + 2) % len(arp_pool)], bt + (q + 0.5) * beat_sec, 0.7, 0.13, 0.62)
                _add_drum(bt, 0.30, soft=False)
                _add_drum(bt + beat_sec * 2.0, 0.24, soft=False)
            elif stage == 4:
                seq = (0, 2, 4, 5, 3, 4, 2, 5)
                for e8 in range(8):
                    _add_pluck_note(arp_pool[seq[e8]], bt + e8 * beat_sec * 0.5, 1.0, 0.17 if e8 % 2 == 0 else 0.13, 0.25 + 0.07 * e8, bright=0.42)
                for bo, g in ((0.0, 0.34), (1.5, 0.24), (2.5, 0.28), (3.5, 0.22)):
                    _add_drum(bt + bo * beat_sec, g, soft=False)
            else:
                for q in range(4):
                    _add_pluck_note(arp_pool[q % len(arp_pool)], bt + q * beat_sec, 1.0, 0.17, 0.32 + 0.10 * q)
                _add_drum(bt, 0.32, soft=False)
                _add_drum(bt + beat_sec * 2.0, 0.26, soft=False)

        # Singing Erhu motif across the 4-bar phrase (Stage 3 transposed +12st to D4..D5)
        erhu_lines = {
            1: [(2.0, 3.5, 69), (5.5, 2.5, 67), (8.0, 3.0, 65), (11.5, 4.0, 62)],
            2: [(0.0, 3.5, 74), (3.5, 2.0, 72), (5.5, 3.0, 69), (9.0, 3.0, 67), (12.0, 3.8, 62)],
            3: [(0.0, 2.5, 62), (2.5, 2.5, 69), (5.0, 3.0, 67), (8.5, 3.5, 69), (12.0, 3.8, 74)],
            4: [(0.0, 3.0, 74), (3.0, 2.5, 77), (5.5, 3.0, 79), (8.5, 3.5, 81), (12.0, 3.8, 74)],
            5: [(1.0, 3.5, 69), (4.5, 2.5, 72), (7.5, 4.0, 74), (11.5, 4.0, 62)],
        }
        _add_bowed_line(erhu_lines[stage], 0.15 if in_epilogue else (0.16, 0.20, 0.22, 0.24, 0.18)[stage - 1], 0.53)

    else:
        # epic_story pure-stdlib Act-stage texture
        for b in range(n_bars):
            bt = b * bar_sec
            extra = seam_sec if b == n_bars - 1 else 0.0
            bass_m, chord = EPIC_PROGRESSION[b % len(EPIC_PROGRESSION)]
            dg = 0.13 if in_epilogue else (0.16, 0.20, 0.24, 0.28, 0.20)[stage - 1]
            _add_drone_bar(bt, bar_sec + extra, bass_m, bass_m + 7, dg)
            # Warm melodic pad chord tones
            for v_idx, cm in enumerate(chord[:3]):
                _add_drone_bar(bt, bar_sec + extra, cm, cm + 7, 0.08 if in_epilogue else 0.10)
            if in_epilogue:
                for bo, ci in ((0.0, 0), (1.5, 2), (2.5, 4)):
                    _add_pluck_note(chord[ci % len(chord)], bt + bo * beat_sec, 1.3, 0.13, 0.42 + 0.14 * (ci % 2), bright=0.22)
            else:
                steps = (0, 2, 4, 6) if stage == 1 else ((0, 1, 2, 4, 5, 6) if stage == 2 else range(8))
                for e8 in steps:
                    m = chord[(e8 + b) % len(chord)]
                    g = (0.12 if stage == 1 else 0.15) * (1.15 if e8 % 2 == 0 else 0.80)
                    _add_pluck_note(m, bt + e8 * beat_sec * 0.5, 0.75, g, 0.35 if e8 % 2 == 0 else 0.65, bright=0.25)
                if stage >= 2:
                    _add_drum(bt, 0.22 if stage == 2 else (0.29 if stage == 3 else 0.35), soft=(stage == 2))
                if stage >= 3:
                    _add_drum(bt + beat_sec * 2.0, 0.25, soft=False)
                if stage == 4:
                    _add_drum(bt + beat_sec * 3.0, 0.22, soft=False)
        if stage in (3, 4) and not in_epilogue:
            lead = [(0.0, 3.5, 69), (4.0, 3.5, 72), (8.0, 3.5, 74), (12.0, 3.8, 76 if stage == 4 else 69)]
            _add_bowed_line(lead, 0.16 if stage == 3 else 0.20, 0.50)

    # Proper loop-seam wrap: crossfade overflow [n .. n + seam_n] into head [0 .. seam_n], then trim to n
    for i in range(seam_n):
        w = 0.5 - 0.5 * cos(math.pi * i / max(1, seam_n))
        l[i] = l[i] * w + l[n + i] * (1.0 - w)
        r[i] = r[i] * w + r[n + i] * (1.0 - w)
    l = l[:n]
    r = r[:n]

    # Normalize gently to ~-3.5 dBFS peak
    peak = max(max(abs(x) for x in l), max(abs(x) for x in r), 1e-6)
    scale = min(1.35, 0.68 / peak)
    l = [math.tanh(x * scale) * 0.75 for x in l]
    r = [math.tanh(x * scale) * 0.75 for x in r]

    l48 = _upsample_2x(l, n_out)
    r48 = _upsample_2x(r, n_out)
    return _pack_stereo_pcm_stdlib(l48, r48, drive=1.0)


def _render_longform_stdlib(
    mood: str,
    duration: float,
    bpm: int,
    acts: List[Tuple[int, float, float]],
    out_path: Path,
    seed: int = 42,
    act_intensities: Optional[List[Optional[str]]] = None,
    ending_style: str = "",
    bgm_cues: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Fast Act-aware pure-stdlib composer for palace_drama and epic_story."""
    is_cliffhanger = (ending_style or "").strip().lower() == "cliffhanger"
    final_s, final_e = acts[-1][1], acts[-1][2]
    final_dur = max(1.0, final_e - final_s)
    epilogue_len = min(25.0, final_dur * 0.55) if duration >= 20.0 else duration * 0.25
    epilogue_start = (duration + 10.0) if is_cliffhanger else max(final_s + min(2.0, final_dur * 0.2), final_e - epilogue_len)

    # Build strictly contiguous chronological segments spanning exact [0 .. total_frames]
    total_frames = int(math.ceil(duration * SR))
    raw_segments: List[Tuple[int, bool, int, int]] = []
    for idx, (_, a_s, a_e) in enumerate(acts):
        stage = resolve_act_stage(idx, len(acts), a_s, duration, act_intensities)
        if is_cliffhanger and idx == len(acts) - 1 and stage == 5:
            explicit_inten = (act_intensities[idx] or "").strip().lower() if (act_intensities and idx < len(act_intensities)) else ""
            if explicit_inten != "epilogue":
                stage = 4
        sf = 0 if not raw_segments else raw_segments[-1][3]
        ef = total_frames if idx == len(acts) - 1 else min(total_frames, max(sf, int(round(a_e * SR))))
        if ef <= sf:
            continue
        if (not is_cliffhanger) and idx == len(acts) - 1 and epilogue_start > a_s + 0.5 and epilogue_start < a_e - 0.5:
            epi_f = max(sf + 1, min(ef - 1, int(round(epilogue_start * SR))))
            raw_segments.append((stage, False, sf, epi_f))
            raw_segments.append((stage, True, epi_f, ef))
        else:
            raw_segments.append((stage, (not is_cliffhanger) and idx == len(acts) - 1 and duration < 20.0, sf, ef))

    if not raw_segments:
        raw_segments = [(1, False, 0, total_frames)]
    elif raw_segments[-1][3] < total_frames:
        st, epi, sf, _ = raw_segments[-1]
        raw_segments[-1] = (st, epi, sf, total_frames)

    segments = raw_segments

    # Synthesize each needed (stage, in_epilogue) 4-bar texture ONCE
    loop_cache: Dict[Tuple[int, bool], bytes] = {}
    for stage, in_epi, _, _ in segments:
        key = (stage, in_epi)
        if key not in loop_cache:
            loop_cache[key] = _stdlib_synth_stage_loop(mood, stage, in_epi, bpm, seed)

    def _tile_segment(loop_pcm: bytes, frames: int) -> bytearray:
        loop_f = len(loop_pcm) // 4
        buf = bytearray()
        rem = frames
        while rem > 0:
            take = min(rem, loop_f)
            buf.extend(loop_pcm[: take * 4])
            rem -= take
        return buf

    # Tile seg_frames + actual_xf on subsequent segments so crossfades NEVER shrink total length
    out_buf = bytearray()
    xfade_frames = int(0.35 * SR)
    for seg_idx, (stage, in_epi, sf, ef) in enumerate(segments):
        seg_frames = ef - sf
        if seg_idx > 0 and len(out_buf) >= 8:
            actual_xf = min(xfade_frames, len(out_buf) // 4, seg_frames)
            if actual_xf > 1:
                seg_pcm = _tile_segment(loop_cache[(stage, in_epi)], seg_frames + actual_xf)
                xf = _crossfade_pcm_seam(bytes(out_buf[-actual_xf * 4 :]), bytes(seg_pcm[: actual_xf * 4]), actual_xf)
                out_buf[-actual_xf * 4 :] = xf
                out_buf.extend(seg_pcm[actual_xf * 4 :])
            else:
                out_buf.extend(_tile_segment(loop_cache[(stage, in_epi)], seg_frames))
        else:
            out_buf.extend(_tile_segment(loop_cache[(stage, in_epi)], seg_frames))

    # Exact target length check (no trailing silence gap!)
    target_bytes = total_frames * 4
    if len(out_buf) > target_bytes:
        del out_buf[target_bytes:]
    elif len(out_buf) < target_bytes:
        # Repeat tail cycle frames if ever short by rounding (never pad dead silence)
        last_loop = loop_cache[(segments[-1][0], segments[-1][1])]
        out_buf.extend(_tile_segment(last_loop, (target_bytes - len(out_buf)) // 4))

    if bgm_cues:
        _apply_bgm_cues_pcm_inplace(out_buf, bgm_cues, duration)

    fo_frames = min(total_frames // 2, int((0.08 if is_cliffhanger else 2.4) * SR))
    _apply_pcm_fades_inplace(out_buf, min(total_frames // 3, int(1.2 * SR)), fo_frames)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(bytes(out_buf))


# =============================================================================
# CLI Entrypoint
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate procedural background music for Shorts & Long-Form videos (48 kHz 16-bit stereo WAV)."
    )
    parser.add_argument(
        "--mood",
        choices=sorted(MOOD_CONFIGS),
        default="miami_pop",
        help="Music mood preset (default: miami_pop).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Target duration in seconds (optional when --timings is provided).",
    )
    parser.add_argument(
        "--timings",
        default=None,
        help="Optional shot_timings.json for duration inference and Act-aware long-form scoring.",
    )
    parser.add_argument("--bpm", type=int, default=0, help="Override mood default BPM.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic RNG seed (default: 42).")
    parser.add_argument(
        "--speech-eq",
        action="store_true",
        default=False,
        help="Opt-in 2.5-4 kHz speech-pocket carve in make_bgm.py (default: OFF; assemble_short.sh handles speech EQ).",
    )
    parser.add_argument("--out", required=True, help="Output WAV path.")
    args = parser.parse_args()

    if args.duration <= 0 and not args.timings:
        parser.error("Provide --duration > 0 or --timings <shot_timings.json>")
    if args.duration < 0:
        parser.error("--duration must be >= 0")

    meta = load_timings_metadata(args.timings, args.duration)
    duration = meta["duration"]
    acts = meta["acts"]
    act_intensities = meta["act_intensities"]
    ending_style = meta["ending_style"]
    bgm_cues = meta["bgm_cues"]
    bpm = args.bpm if args.bpm > 0 else int(MOOD_CONFIGS[args.mood]["bpm"])
    out_path = Path(args.out)

    if args.mood == "palace_drama":
        if HAS_NUMPY:
            left, right = _compose_palace_drama_numpy(
                duration,
                bpm,
                acts,
                seed=args.seed,
                act_intensities=act_intensities,
                ending_style=ending_style,
                bgm_cues=bgm_cues,
                speech_eq=args.speech_eq,
            )
            write_stereo_wav_arrays(left, right, out_path, already_limited=True)
        else:
            _render_longform_stdlib(
                "palace_drama",
                duration,
                bpm,
                acts,
                out_path,
                seed=args.seed,
                act_intensities=act_intensities,
                ending_style=ending_style,
                bgm_cues=bgm_cues,
            )
        act_summary = ", ".join(f"Act{a}:{s:.1f}-{e:.1f}s" for a, s, e in acts)
        engine = "numpy" if HAS_NUMPY else "stdlib"
        print(f"Generated palace_drama BGM ({bpm} BPM D Yu, {duration:.2f}s, {engine}, {act_summary}) -> {out_path}")
        return 0

    if args.mood == "epic_story":
        if HAS_NUMPY:
            left, right = _compose_epic_story_numpy(
                duration,
                bpm,
                acts,
                seed=args.seed,
                act_intensities=act_intensities,
                ending_style=ending_style,
                bgm_cues=bgm_cues,
                speech_eq=args.speech_eq,
            )
            write_stereo_wav_arrays(left, right, out_path, already_limited=True)
        else:
            _render_longform_stdlib(
                "epic_story",
                duration,
                bpm,
                acts,
                out_path,
                seed=args.seed,
                act_intensities=act_intensities,
                ending_style=ending_style,
                bgm_cues=bgm_cues,
            )
        act_summary = ", ".join(f"Act{a}:{s:.1f}-{e:.1f}s" for a, s, e in acts)
        engine = "numpy" if HAS_NUMPY else "stdlib"
        print(f"Generated epic_story BGM ({bpm} BPM, {duration:.2f}s, {engine}, {act_summary}) -> {out_path}")
        return 0

    # Classic 6 Shorts moods: fast seamless 4-bar tiling for long durations (> 90s)
    if duration > 90.0:
        _render_classic_long_tiled(
            args.mood,
            duration,
            bpm,
            out_path,
            acts=acts if args.timings else None,
            act_intensities=act_intensities if args.timings else None,
            bgm_cues=bgm_cues if args.timings else None,
            ending_style=ending_style,
        )
    else:
        left, right = synthesize_bgm(args.mood, duration, bpm)
        if bgm_cues:
            pcm = bytearray(_pack_stereo_pcm_stdlib(left, right, drive=0.85))
            _apply_bgm_cues_pcm_inplace(pcm, bgm_cues, duration)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(SR)
                wf.writeframes(bytes(pcm))
        else:
            write_stereo_wav_arrays(left, right, out_path, drive=0.85, already_limited=False)

    print(f"Generated {args.mood} BGM ({bpm} BPM, {duration:.2f}s) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
