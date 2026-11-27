#!/usr/bin/env python3
"""Procedural Shorts & Long-Form cinematic SFX synthesizer and timeline renderer (48 kHz 16-bit stereo WAV).

Uses vectorized NumPy synthesis & timeline mixing when NumPy is installed, with a fast
pure-stdlib interval-sparse fallback when NumPy is unavailable. Synthesized cues are
cached per cue name so rendering 55+ cues across a 5-15 minute (300-900s) timeline runs
in well under 2 seconds.

Supported cues (26 total):
  Shorts comedic/punch cues (9):
    record_scratch, vine_boom, siren_chirp, bonk_boing,
    cash_ching, ding_levelup, laser_zap, whoosh, pop
  Palace / historical drama cues (9):
    palace_gong, war_drum_hit, silk_whoosh, jade_clink, porcelain_shatter,
    blade_unsheathe, guzheng_gliss, fire_crackle_burst, court_gasp_swell
  General long-form cinematic cues (2):
    cinematic_riser, heartbeat_pulse
  Tech / UI editorial cues (6):
    ui_tick, ui_click, data_chime, sub_whoosh, alert_ping, soft_thud

Examples:
  python3 sfx_engine.py --list
  python3 sfx_engine.py --cue vine_boom --out boom.wav
  python3 sfx_engine.py --cue data_chime --out chime.wav
  python3 sfx_engine.py --timings audio/shot_timings.json --out audio/sfx_track.wav
"""

import argparse
from array import array
import json
import math
import random
import sys
import wave
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import numpy as np  # type: ignore

    HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore
    HAS_NUMPY = False

SR = 48000
TWO_PI = 2.0 * math.pi

AMBIENCE_PRESETS = (
    "none",
    "palace_courtyard",
    "throne_hall",
    "newsroom_air",
    "hearth_embers",
)

# Per-cue loudness calibration: cue -> (target_rms_dbfs, max_peak_dbfs)
CUE_CALIBRATION: Dict[str, Tuple[float, float]] = {
    # Impacts & deep cinematic hits (-13 dBFS RMS, -2.0 dBFS peak)
    "vine_boom": (-13.0, -2.0),
    "war_drum_hit": (-13.0, -2.0),
    "palace_gong": (-13.0, -2.0),
    "heartbeat_pulse": (-13.0, -2.0),
    "cinematic_riser": (-13.0, -2.0),
    # Sharp comedic / UI / metallic tonal cues (-14 dBFS RMS, -2.5 dBFS peak)
    "siren_chirp": (-14.0, -2.5),
    "record_scratch": (-14.0, -2.5),
    "laser_zap": (-14.0, -2.5),
    "bonk_boing": (-14.0, -2.5),
    "cash_ching": (-14.0, -2.5),
    "ding_levelup": (-14.0, -2.5),
    "pop": (-14.0, -2.5),
    "jade_clink": (-14.0, -2.5),
    "blade_unsheathe": (-14.0, -2.5),
    "guzheng_gliss": (-14.0, -2.5),
    # Broadband textures & swells (-16 dBFS RMS, -2.5 dBFS peak)
    "porcelain_shatter": (-16.0, -2.5),
    "fire_crackle_burst": (-16.0, -2.5),
    "whoosh": (-16.0, -2.5),
    "silk_whoosh": (-16.0, -2.5),
    "court_gasp_swell": (-16.0, -2.5),
    # Tech / UI editorial cues (-17 to -19 dBFS RMS, -4.0 dBFS peak)
    "ui_tick": (-18.0, -4.0),
    "ui_click": (-17.5, -4.0),
    "data_chime": (-17.5, -4.0),
    "sub_whoosh": (-18.5, -4.0),
    "alert_ping": (-17.0, -4.0),
    "soft_thud": (-18.0, -4.0),
}


def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _pan_gains(pan: float) -> Tuple[float, float]:
    """Equal-power stereo balance gains where pan=0.5 yields (1.0, 1.0)."""
    p = max(0.0, min(1.0, float(pan)))
    if abs(p - 0.5) < 1e-6:
        return 1.0, 1.0
    angle = p * math.pi * 0.5
    s2 = math.sqrt(2.0)
    return s2 * math.cos(angle), s2 * math.sin(angle)


def _upsample_2x(buf: List[float], target_n: int) -> List[float]:
    """Linear 2x upsample from 24 kHz to 48 kHz for fast pure-stdlib long-cue synthesis."""
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
# Shared Pure-Stdlib Helpers
# =============================================================================

def _adsr_scalar(t: float, dur: float, a: float = 0.005, d: float = 0.05) -> float:
    if t < 0.0 or t > dur:
        return 0.0
    if a > 0.0 and t < a:
        return t / a
    if d > 0.0 and t > dur - d:
        return max(0.0, (dur - t) / d)
    return 1.0


def _stdlib_bandpass(samples: List[float], low_hz: float, high_hz: float, sr: int = SR) -> List[float]:
    """Fast cascaded 1-pole HP + LP bandpass filter in pure Python."""
    n = len(samples)
    out = [0.0] * n
    if n == 0:
        return out
    dt = 1.0 / sr
    alpha_lp = (TWO_PI * high_hz * dt) / (1.0 + TWO_PI * high_hz * dt) if high_hz > 0 else 1.0
    rc_hp = 1.0 / (TWO_PI * low_hz) if low_hz > 0 else 0.0
    alpha_hp = rc_hp / (rc_hp + dt) if low_hz > 0 else 1.0

    lp = 0.0
    hp = 0.0
    prev_in = 0.0
    for i in range(n):
        x = samples[i]
        lp += alpha_lp * (x - lp)
        if low_hz > 0:
            hp = alpha_hp * (hp + lp - prev_in)
            prev_in = lp
            out[i] = hp
        else:
            out[i] = lp
    return out


# =============================================================================
# Pure-Stdlib Cue Generators (all 20 cues, returning (left, right) float lists)
# =============================================================================

def _py_vine_boom(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.95
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    phase = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    p_jit = uni(0.985, 1.015)
    for i in range(n):
        t = i * inv_sr
        freq = (32.0 + 108.0 * exp(-t * 9.5)) * p_jit
        phase += TWO_PI * freq * inv_sr
        sub = sin(phase) + 0.35 * sin(phase * 2.0)
        click_l = uni(-1.0, 1.0) * exp(-t * 140.0) * 0.42
        click_r = uni(-1.0, 1.0) * exp(-t * 140.0) * 0.42
        air = sin(phase * 3.0 + 0.4) * exp(-t * 18.0) * 0.12
        env = exp(-t * 3.8) * _adsr_scalar(t, dur, a=0.003, d=0.08)
        l[i] = tanh((sub * 1.45 + click_l + air) * env) * 0.92
        r[i] = tanh((sub * 1.45 + click_r - air) * env) * 0.92
    return l, r


def _py_record_scratch(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.42
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    phase = 0.0
    prev_noise_l = 0.0
    prev_noise_r = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    pi_dur = math.pi / dur
    p_jit = uni(0.98, 1.02)
    for i in range(n):
        t = i * inv_sr
        if t < 0.14:
            freq = (420.0 + 1600.0 * (t / 0.14)) * p_jit
        else:
            freq = (2020.0 * exp(-(t - 0.14) * 9.0) + 180.0) * p_jit
        phase += TWO_PI * freq * inv_sr
        raw_l = uni(-1.0, 1.0)
        raw_r = uni(-1.0, 1.0)
        hp_l = raw_l - 0.7 * prev_noise_l
        hp_r = raw_r - 0.7 * prev_noise_r
        prev_noise_l = raw_l
        prev_noise_r = raw_r
        harm_l = sin(phase) + 0.5 * sin(phase * 2.7)
        harm_r = sin(phase + 0.28) + 0.5 * sin(phase * 2.72 - 0.2)
        env = _adsr_scalar(t, dur, a=0.004, d=0.06) * (0.45 + 0.55 * sin(pi_dur * t))
        l[i] = tanh((harm_l * 0.65 + hp_l * 0.45) * env) * 0.85
        r[i] = tanh((harm_r * 0.65 + hp_r * 0.45) * env) * 0.85
    return l, r


def _py_siren_chirp(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.48
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    phase = 0.0
    sin = math.sin
    cos = math.cos
    tanh = math.tanh
    inv_sr = 1.0 / SR
    p_jit = rng.uniform(0.985, 1.015)
    for i in range(n):
        t = i * inv_sr
        cycle = (t * 4.2) % 1.0
        freq = (680.0 + 720.0 * (0.5 - 0.5 * cos(TWO_PI * cycle))) * p_jit
        phase += TWO_PI * freq * inv_sr
        sig_l = sin(phase) + 0.25 * sin(3.0 * phase)
        sig_r = sin(phase + 0.22) + 0.25 * sin(3.0 * phase - 0.35)
        sway = 0.42 + 0.16 * sin(TWO_PI * 2.2 * t)
        env = _adsr_scalar(t, dur, a=0.008, d=0.05) * 0.75
        l[i] = tanh(sig_l * 0.9) * env * math.cos(sway * math.pi * 0.5) * 1.414
        r[i] = tanh(sig_r * 0.9) * env * math.sin(sway * math.pi * 0.5) * 1.414
    return l, r


def _py_bonk_boing(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.65
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    phase_l = 0.0
    phase_r = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    p_jit = rng.uniform(0.98, 1.02)
    for i in range(n):
        t = i * inv_sr
        bonk = sin(TWO_PI * (340.0 * exp(-t * 25.0) + 110.0) * p_jit * t) * exp(-t * 28.0)
        wobble_freq = (210.0 + 110.0 * sin(TWO_PI * 13.5 * t) * exp(-t * 3.0) + 90.0 * t) * p_jit
        phase_l += TWO_PI * wobble_freq * inv_sr
        phase_r += TWO_PI * (wobble_freq + 2.4) * inv_sr
        spring_l = (sin(phase_l) + 0.3 * sin(2.0 * phase_l)) * exp(-t * 4.2)
        spring_r = (sin(phase_r + 0.2) + 0.3 * sin(2.0 * phase_r)) * exp(-t * 4.2)
        env = _adsr_scalar(t, dur, a=0.002, d=0.06)
        l[i] = tanh((bonk * 0.9 + spring_l * 0.7) * env) * 0.85
        r[i] = tanh((bonk * 0.9 + spring_r * 0.7) * env) * 0.85
    return l, r


def _py_cash_ching(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.72
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    p_jit = rng.uniform(0.99, 1.01)
    freqs = tuple(f * p_jit for f in (2093.0, 3136.0, 4186.0, 5274.0))
    amps = (0.50, 0.35, 0.25, 0.15)
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    for i in range(n):
        t = i * inv_sr
        clack_l = uni(-1.0, 1.0) * exp(-t * 75.0) * 0.55
        clack_r = uni(-1.0, 1.0) * exp(-t * 75.0) * 0.55
        bell_l = 0.0
        bell_r = 0.0
        if t >= 0.045:
            tb = t - 0.045
            bell_env = exp(-tb * 5.2) * _adsr_scalar(tb, dur - 0.045, a=0.002, d=0.08)
            for f, a in zip(freqs, amps):
                bell_l += a * sin(TWO_PI * f * tb)
                bell_r += a * sin(TWO_PI * (f + 3.2) * tb + 0.25)
            bell_l *= bell_env
            bell_r *= bell_env
        l[i] = tanh(clack_l + bell_l * 1.1) * 0.82
        r[i] = tanh(clack_r + bell_r * 1.1) * 0.82
    return l, r


def _py_ding_levelup(rng: random.Random) -> Tuple[List[float], List[float]]:
    p_jit = rng.uniform(0.992, 1.008)
    notes = tuple(f * p_jit for f in (1046.5, 1318.5, 1568.0, 2093.0))
    step = 0.075
    dur = step * len(notes) + 0.38
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    cos = math.cos
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    for idx, freq in enumerate(notes):
        start_i = int(idx * step * SR)
        note_dur = 0.42 if idx == len(notes) - 1 else 0.18
        note_n = min(int(note_dur * SR), n - start_i)
        dec = 5.5 if idx == len(notes) - 1 else 14.0
        w = TWO_PI * freq
        pan = (0.32 + 0.12 * idx) * math.pi * 0.5
        gl = cos(pan) * 1.414 * 0.48
        gr = sin(pan) * 1.414 * 0.48
        for j in range(note_n):
            t = j * inv_sr
            env = exp(-t * dec) * _adsr_scalar(t, note_dur, a=0.003, d=0.03)
            sig = (sin(w * t) + 0.25 * sin(2.0 * w * t)) * env
            l[start_i + j] += sig * gl
            r[start_i + j] += sig * gr
    l = [tanh(x) * 0.82 for x in l]
    r = [tanh(x) * 0.82 for x in r]
    return l, r


def _py_laser_zap(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.28
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    phase = 0.0
    sin = math.sin
    cos = math.cos
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    p_jit = rng.uniform(0.975, 1.025)
    for i in range(n):
        t = i * inv_sr
        freq = (2600.0 * exp(-t * 11.5) + 140.0) * p_jit
        phase += TWO_PI * freq * inv_sr
        sig_l = sin(phase) + 0.45 * sin(2.0 * phase) + 0.2 * sin(3.0 * phase)
        sig_r = sin(phase + 0.18) + 0.45 * sin(2.0 * phase - 0.2) + 0.2 * sin(3.0 * phase)
        env = _adsr_scalar(t, dur, a=0.002, d=0.04) * exp(-t * 4.0)
        pan = (0.34 + 0.32 * (t / dur)) * math.pi * 0.5
        l[i] = tanh(sig_l * env) * 0.8 * cos(pan) * 1.414
        r[i] = tanh(sig_r * env) * 0.8 * sin(pan) * 1.414
    return l, r


def _py_whoosh(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.34
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    lp = 0.0
    lp2 = 0.0
    sin = math.sin
    cos = math.cos
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    pi_dur = math.pi / dur
    for i in range(n):
        t = i * inv_sr
        bell = sin(pi_dur * t) ** 1.6
        alpha = 0.05 + 0.35 * bell
        raw = uni(-1.0, 1.0)
        lp += alpha * (raw - lp)
        lp2 += (alpha * 0.6) * (lp - lp2)
        bp = lp - lp2 * 0.65
        mono = tanh(bp * 2.8) * bell * 0.78
        # Smooth L-to-R stereo pan sweep across whoosh
        pan = (0.20 + 0.60 * (t / dur)) * math.pi * 0.5
        l[i] = mono * cos(pan) * 1.414
        r[i] = mono * sin(pan) * 1.414
    return l, r


def _py_pop(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.095
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    phase = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    p_jit = rng.uniform(0.97, 1.03)
    for i in range(n):
        t = i * inv_sr
        freq = (210.0 + 520.0 * (t / dur)) * p_jit
        phase += TWO_PI * freq * inv_sr
        env = exp(-t * 32.0) * _adsr_scalar(t, dur, a=0.002, d=0.015)
        l[i] = tanh(sin(phase) * 1.4) * env * 0.85
        r[i] = tanh(sin(phase + 0.24) * 1.4) * env * 0.85
    return l, r


def _py_palace_gong(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 2.4
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    raw = [uni(-1.0, 1.0) for _ in range(n)]
    # 1-pole bandpass at sr_h
    dt = inv_sr
    a_lp = (TWO_PI * 5200.0 * dt) / (1.0 + TWO_PI * 5200.0 * dt)
    rc_hp = 1.0 / (TWO_PI * 600.0)
    a_hp = rc_hp / (rc_hp + dt)
    lp = 0.0
    hp = 0.0
    prev_in = 0.0
    wash_bp = [0.0] * n
    for i in range(n):
        lp += a_lp * (raw[i] - lp)
        hp = a_hp * (hp + lp - prev_in)
        prev_in = lp
        wash_bp[i] = hp

    partials = (
        (1.00, 1.00, exp(-0.95 * inv_sr)),
        (1.47, 0.62, exp(-1.20 * inv_sr)),
        (2.02, 0.48, exp(-1.45 * inv_sr)),
        (2.73, 0.34, exp(-1.85 * inv_sr)),
        (3.58, 0.25, exp(-2.30 * inv_sr)),
        (4.62, 0.18, exp(-2.85 * inv_sr)),
    )
    envs = [amp for _, amp, _ in partials]
    ratios = [r0 for r0, _, _ in partials]
    ratios_r = [r0 * 1.0055 for r0, _, _ in partials]
    decs = [d0 for _, _, d0 in partials]
    e_wash = 0.28
    d_wash = exp(-3.8 * inv_sr)
    e_thump = 0.45
    d_thump = exp(-24.0 * inv_sr)
    phase = 0.0
    for i in range(n):
        t = i * inv_sr
        bloom = 1.0 - 0.032 * exp(-t * 4.2) + 0.014 * exp(-t * 1.1)
        phase += TWO_PI * (108.0 * bloom) * inv_sr
        sl = 0.0
        sr_v = 0.0
        for k in range(6):
            ek = envs[k]
            sl += sin(phase * ratios[k]) * ek
            sr_v += sin(phase * ratios_r[k] + 0.45) * ek
            envs[k] = ek * decs[k]
        w = wash_bp[i] * e_wash
        e_wash *= d_wash
        thump = sin(TWO_PI * 72.0 * t) * e_thump
        e_thump *= d_thump
        att = _adsr_scalar(t, dur, a=0.008, d=0.22) * 1.15
        l[i] = tanh((sl * 0.55 + w + thump) * att) * 0.88
        r[i] = tanh((sr_v * 0.55 - w * 0.85 + thump) * att) * 0.88
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_war_drum_hit(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 1.1
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    slap_bp = _stdlib_bandpass([uni(-1.0, 1.0) for _ in range(n)], 180.0, 2600.0, sr=sr_h)
    room_bp = _stdlib_bandpass([uni(-1.0, 1.0) for _ in range(n)], 80.0, 900.0, sr=sr_h)
    phase = 0.0
    for i in range(n):
        t = i * inv_sr
        freq = 38.0 + 96.0 * exp(-t * 16.0)
        phase += TWO_PI * freq * inv_sr
        sub = sin(phase) + 0.40 * sin(2.0 * phase) + 0.16 * sin(3.0 * phase)
        slap = slap_bp[i] * exp(-t * 68.0) * 0.48
        rim = sin(TWO_PI * 245.0 * t) * exp(-t * 42.0) * 0.25
        env = exp(-t * 3.6) * _adsr_scalar(t, dur, a=0.0025, d=0.14)
        body = tanh((sub * 1.25 + slap + rim) * env) * 0.90
        rm = room_bp[i] * exp(-t * 5.2) * 0.08
        l[i] = tanh(body + rm) * 0.92
        r[i] = tanh(body - rm) * 0.92
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_silk_whoosh(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.55
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    cos = math.cos
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    raw = [uni(-1.0, 1.0) for _ in range(n)]
    low_s = _stdlib_bandpass(raw, 420.0, 1900.0, sr=sr_h)
    hi_s = _stdlib_bandpass(raw, 1800.0, 5400.0, sr=sr_h)
    pi_dur = math.pi / dur
    for i in range(n):
        t = i * inv_sr
        bell = sin(pi_dur * t) ** 1.45
        p_hi = min(1.0, max(0.0, (t - 0.06) / (dur - 0.10)))
        sweep_hi = sin(math.pi * p_hi) ** 1.8
        mono = (low_s[i] * 0.95 + hi_s[i] * 0.72 * sweep_hi) * bell * 1.95
        pan = (0.25 + 0.50 * (t / dur)) * math.pi * 0.5
        l[i] = tanh(mono * cos(pan)) * 0.85
        r[i] = tanh(mono * sin(pan)) * 0.85
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_jade_clink(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.65
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    click_bp = _stdlib_bandpass([uni(-1.0, 1.0) for _ in range(n)], 2800.0, 9000.0, sr=sr_h)
    partials = (
        (1950.0, 0.18, 12.0),
        (3210.0, 0.65, 7.8),
        (5120.0, 0.45, 9.4),
        (7810.0, 0.28, 12.8),
    )
    for i in range(n):
        t = i * inv_sr
        sl = 0.0
        sr_v = 0.0
        for freq, amp, dec in partials:
            e = exp(-t * dec) * amp
            sl += sin(TWO_PI * freq * t) * e
            sr_v += sin(TWO_PI * (freq + 3.8) * t + 0.25) * e
        ck = click_bp[i] * exp(-t * 220.0) * 0.35
        att = _adsr_scalar(t, dur, a=0.001, d=0.08)
        l[i] = tanh((sl + ck) * att * 0.95) * 0.82
        r[i] = tanh((sr_v - ck * 0.6) * att * 0.95) * 0.82
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_porcelain_shatter(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 1.15
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    sin = math.sin
    cos = math.cos
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    crack_bp = _stdlib_bandpass([uni(-1.0, 1.0) for _ in range(n)], 1400.0, 9500.0, sr=sr_h)
    l = [0.0] * n
    r = [0.0] * n
    for i in range(n):
        t = i * inv_sr
        cr = crack_bp[i] * exp(-t * 65.0) * 0.95
        body = sin(TWO_PI * 1680.0 * t) * exp(-t * 36.0) * 0.40 + sin(TWO_PI * 2940.0 * t) * exp(-t * 42.0) * 0.35
        l[i] = cr + body
        r[i] = cr * 0.92 + body

    shard_times = sorted(uni(0.035, 0.96) ** 1.35 for _ in range(26))
    for st in shard_times:
        s_idx = int(st * sr_h)
        sn = min(int(0.09 * sr_h), n - s_idx)
        if sn < 16:
            continue
        f1 = uni(2400.0, 6200.0)
        f2 = f1 * uni(1.42, 1.88)
        amp = uni(0.12, 0.34) * exp(-st * 1.45)
        dec = uni(45.0, 95.0)
        pan = uni(0.15, 0.85) * math.pi * 0.5
        gl = cos(pan) * amp
        gr = sin(pan) * amp
        w1 = TWO_PI * f1
        w2 = TWO_PI * f2
        for j in range(sn):
            ts = j * inv_sr
            ping = (sin(w1 * ts) * 0.65 + sin(w2 * ts) * 0.45 + uni(-0.25, 0.25) * exp(-ts * 180.0)) * exp(-ts * dec)
            l[s_idx + j] += ping * gl
            r[s_idx + j] += ping * gr

    for i in range(n):
        att = _adsr_scalar(i * inv_sr, dur, a=0.001, d=0.10) * 1.05
        l[i] = tanh(l[i] * att) * 0.88
        r[i] = tanh(r[i] * att) * 0.88
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_blade_unsheathe(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.75
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    scrape_bp = _stdlib_bandpass([uni(-1.0, 1.0) for _ in range(n)], 2200.0, 8800.0, sr=sr_h)
    ph1 = 0.0
    ph2 = 0.0
    ph3 = 0.0
    for i in range(n):
        t = i * inv_sr
        sp = min(1.0, t / 0.48)
        scrape_env = (sin(math.pi * sp) ** 1.3) * exp(-max(0.0, t - 0.25) * 7.0)
        prog = min(1.0, t / 0.42)
        fb = 1150.0 + 1850.0 * (prog ** 0.75)
        ph1 += TWO_PI * fb * inv_sr
        ph2 += TWO_PI * (fb * 1.51) * inv_sr
        ph3 += TWO_PI * (fb * 2.27) * inv_sr
        ring_env = min(1.0, t / 0.06) * exp(-max(0.0, t - 0.14) * 5.8)
        rl = (sin(ph1) * 0.55 + sin(ph2) * 0.35 + sin(ph3) * 0.22) * ring_env
        rr = (sin(ph1 * 1.003) * 0.55 + sin(ph2 * 0.997) * 0.35 + sin(ph3) * 0.22) * ring_env
        flick = exp(-((t - 0.36) / 0.015) ** 2) * sin(TWO_PI * 4600.0 * t) * 0.28
        att = _adsr_scalar(t, dur, a=0.008, d=0.12) * 1.15
        sc = scrape_bp[i] * scrape_env
        l[i] = tanh((sc * 0.52 + rl * 0.65 + flick) * att) * 0.85
        r[i] = tanh((sc * 0.46 + rr * 0.65 + flick * 0.85) * att) * 0.85
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_guzheng_gliss(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 1.0
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    cos = math.cos
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    midis = (62, 65, 67, 69, 72, 74, 77, 79, 81, 84, 86)
    sweep_time = 0.36
    d1 = exp(-4.5 * inv_sr)
    d2 = exp(-7.5 * inv_sr)
    d3 = exp(-12.0 * inv_sr)
    dn = exp(-190.0 * inv_sr)
    att_n = max(1, int(0.002 * sr_h))
    for idx, m in enumerate(midis):
        frac = idx / max(1, len(midis) - 1)
        s_idx = int(((frac ** 0.82) * sweep_time) * sr_h)
        avail = n - s_idx
        if avail < 32:
            continue
        freq = 440.0 * (2.0 ** ((m - 69) / 12.0))
        step_w = TWO_PI * freq * inv_sr
        cs = cos(step_w)
        sn = sin(step_w)
        s1 = 0.0
        c1 = 1.0
        e1 = 0.68
        e2 = 0.36
        e3 = 0.20
        en = 0.18
        pan = (0.22 + 0.56 * frac) * math.pi * 0.5
        amp = 0.32 + 0.18 * frac
        gl = cos(pan) * amp
        gr = sin(pan) * amp
        for j in range(avail):
            s2 = 2.0 * s1 * c1
            s3 = 2.0 * c1 * s2 - s1
            sig = s1 * e1 + s2 * e2 + s3 * e3
            if en > 0.001:
                sig += uni(-en, en)
                en *= dn
            if j < att_n:
                sig *= j / att_n
            l[s_idx + j] += sig * gl
            r[s_idx + j] += sig * gr
            ns1 = s1 * cs + c1 * sn
            c1 = c1 * cs - s1 * sn
            s1 = ns1
            e1 *= d1
            e2 *= d2
            e3 *= d3
    for i in range(n):
        att = _adsr_scalar(i * inv_sr, dur, a=0.002, d=0.16) * 0.95
        l[i] = tanh(l[i] * att) * 0.86
        r[i] = tanh(r[i] * att) * 0.86
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_fire_crackle_burst(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 1.3
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    sin = math.sin
    cos = math.cos
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    raw = [uni(-1.0, 1.0) for _ in range(n)]
    roar_bp = _stdlib_bandpass(raw, 95.0, 820.0, sr=sr_h)
    hi_bp = _stdlib_bandpass(raw, 900.0, 3400.0, sr=sr_h)
    l = [0.0] * n
    r = [0.0] * n
    for i in range(n):
        t = i * inv_sr
        re_v = min(1.0, t / 0.08) * exp(-max(0.0, t - 0.18) * 2.6)
        fh = hi_bp[i] * re_v * 0.28
        rr = roar_bp[i] * re_v
        l[i] = rr * 0.95 + fh
        r[i] = rr * 0.92 - fh * 0.8

    for _ in range(34):
        s_idx = int(uni(0.03, 1.12) * sr_h)
        pn = min(int(0.022 * sr_h), n - s_idx)
        if pn < 8:
            continue
        cf = uni(1800.0, 5600.0)
        dec = uni(180.0, 320.0)
        amp = uni(0.22, 0.55)
        pan = uni(0.15, 0.85) * math.pi * 0.5
        gl = cos(pan) * amp
        gr = sin(pan) * amp
        wc = TWO_PI * cf
        for j in range(pn):
            tp = j * inv_sr
            snap = (uni(-1.0, 1.0) * 0.55 + sin(wc * tp) * 0.65) * exp(-tp * dec)
            l[s_idx + j] += snap * gl
            r[s_idx + j] += snap * gr

    for i in range(n):
        att = _adsr_scalar(i * inv_sr, dur, a=0.01, d=0.18) * 1.1
        l[i] = tanh(l[i] * att) * 0.88
        r[i] = tanh(r[i] * att) * 0.88
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_court_gasp_swell(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 1.2
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    raw = [uni(-1.0, 1.0) for _ in range(n)]
    air_l = _stdlib_bandpass(raw, 450.0, 3200.0, sr=sr_h)
    air_r = air_l[159:] + air_l[:159]
    riser_end = 0.92
    ph = 0.0
    drop_ph = 0.0
    for i in range(n):
        t = i * inv_sr
        prog = min(1.0, t / riser_end)
        r_env = (prog ** 2.4) if t <= riser_end else exp(-(t - riser_end) * 28.0)
        ph += TWO_PI * (73.42 * (1.0 + 0.16 * prog)) * inv_sr
        tonal = sin(ph) * 0.55 + sin(ph * 1.5) * 0.35 + sin(ph * 2.12) * 0.25
        drop = 0.0
        if t >= riser_end:
            td = t - riser_end
            drop_ph += TWO_PI * (55.0 * exp(-td * 6.0) + 32.0) * inv_sr
            drop = sin(drop_ph) * exp(-td * 10.0) * 0.45
        att = _adsr_scalar(t, dur, a=0.04, d=0.08) * 1.1
        l[i] = tanh(((tonal * 0.65 + air_l[i] * 0.95) * r_env + drop) * att) * 0.85
        r[i] = tanh(((tonal * 0.65 + air_r[i] * 0.95) * r_env + drop) * att) * 0.85
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_cinematic_riser(rng: random.Random) -> Tuple[List[float], List[float]]:
    """2.0s dramatic trailer-style sub + shimmer tension riser ending on a crisp cutoff hit."""
    dur = 2.0
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    raw = [uni(-1.0, 1.0) for _ in range(n)]
    shimmer_l = _stdlib_bandpass(raw, 700.0, 4800.0, sr=sr_h)
    shimmer_r = shimmer_l[127:] + shimmer_l[:127]
    peak_t = 1.72
    ph = 0.0
    hit_ph = 0.0
    for i in range(n):
        t = i * inv_sr
        prog = min(1.0, t / peak_t)
        env = (prog ** 2.2) if t <= peak_t else exp(-(t - peak_t) * 18.0)
        f_sub = 48.0 + 78.0 * (prog ** 1.6)
        ph += TWO_PI * f_sub * inv_sr
        sub = sin(ph) * 0.65 + sin(ph * 1.498) * 0.35 + sin(ph * 2.0) * 0.25
        trem = 0.82 + 0.18 * sin(TWO_PI * (3.0 + 11.0 * prog) * t)
        hit = 0.0
        if t >= peak_t:
            th = t - peak_t
            hit_ph += TWO_PI * (36.0 + 82.0 * exp(-th * 22.0)) * inv_sr
            hit = sin(hit_ph) * exp(-th * 9.5) * 0.72
        att = _adsr_scalar(t, dur, a=0.05, d=0.06)
        l[i] = tanh(((sub * 0.78 + shimmer_l[i] * 0.62 * prog) * env * trem + hit) * att) * 0.88
        r[i] = tanh(((sub * 0.78 + shimmer_r[i] * 0.62 * prog) * env * trem + hit) * att) * 0.88
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_heartbeat_pulse(rng: random.Random) -> Tuple[List[float], List[float]]:
    """1.1s deep cinematic dual-beat 'lub-dub' heartbeat with warm chest sub-thump."""
    dur = 1.1
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    room = _stdlib_bandpass([uni(-1.0, 1.0) for _ in range(n)], 45.0, 260.0)
    beats = ((0.0, 1.00, 44.0, 42.0, 10.5), (0.28, 0.72, 38.0, 32.0, 12.0))
    for b_start, b_amp, f_lo, f_hi, dec in beats:
        s_idx = int(b_start * SR)
        bn = min(int(0.45 * SR), n - s_idx)
        ph = 0.0
        for j in range(bn):
            tb = j * inv_sr
            freq = f_lo + f_hi * exp(-tb * 24.0)
            ph += TWO_PI * freq * inv_sr
            env = min(1.0, tb / 0.012) * exp(-tb * dec)
            val = (sin(ph) + 0.25 * sin(2.0 * ph)) * env * b_amp
            rm = room[s_idx + j] * env * 0.08 * b_amp
            l[s_idx + j] += val + rm
            r[s_idx + j] += val - rm
    for i in range(n):
        att = _adsr_scalar(i * inv_sr, dur, a=0.004, d=0.08)
        l[i] = tanh(l[i] * 1.25 * att) * 0.90
        r[i] = tanh(r[i] * 1.25 * att) * 0.90
    return l, r


def _py_ui_tick(rng: random.Random) -> Tuple[List[float], List[float]]:
    """0.045s crisp micro-tick for data bullets and subtle UI highlights."""
    dur = 0.045
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    p_jit = uni(0.985, 1.015)
    click_bp = _stdlib_bandpass([uni(-1.0, 1.0) for _ in range(n)], 2200.0, 6800.0)
    f1 = 1850.0 * p_jit
    f2 = 3720.0 * p_jit
    for i in range(n):
        t = i * inv_sr
        env = exp(-t * 135.0) * _adsr_scalar(t, dur, a=0.001, d=0.006)
        tone_l = (sin(TWO_PI * f1 * t) * 0.65 + sin(TWO_PI * f2 * t) * 0.30) * env
        tone_r = (sin(TWO_PI * (f1 + 4.0) * t + 0.12) * 0.65 + sin(TWO_PI * f2 * t) * 0.30) * env
        ck = click_bp[i] * exp(-t * 280.0) * 0.35
        l[i] = tanh(tone_l + ck) * 0.85
        r[i] = tanh(tone_r - ck * 0.6) * 0.85
    return l, r


def _py_ui_click(rng: random.Random) -> Tuple[List[float], List[float]]:
    """0.070s clean tactile editorial switch click for card/slide switches."""
    dur = 0.070
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    p_jit = uni(0.985, 1.015)
    raw = [uni(-1.0, 1.0) for _ in range(n)]
    snap_bp = _stdlib_bandpass(raw, 1600.0, 7200.0)
    f_body = 920.0 * p_jit
    f_top = 3150.0 * p_jit
    for i in range(n):
        t = i * inv_sr
        e1 = exp(-t * 145.0)
        body = sin(TWO_PI * f_body * t) * e1 * 0.68
        top = sin(TWO_PI * f_top * t) * exp(-t * 240.0) * 0.34
        snap = snap_bp[i] * exp(-t * 260.0) * 0.42
        rel = 0.0
        if t >= 0.022:
            tr = t - 0.022
            rel = (sin(TWO_PI * (f_body * 1.28) * tr) * 0.30 + snap_bp[i] * 0.16) * exp(-tr * 190.0)
        att = _adsr_scalar(t, dur, a=0.0008, d=0.008)
        l[i] = tanh((body + top + snap + rel) * att) * 0.86
        r[i] = tanh((body + top * 0.94 - snap * 0.5 + rel) * att) * 0.86
    return l, r


def _py_data_chime(rng: random.Random) -> Tuple[List[float], List[float]]:
    """0.52s warm two-note minimalist fifth chime for verified stats and charts."""
    dur = 0.52
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    cos = math.cos
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    p_jit = rng.uniform(0.994, 1.006)
    notes = (
        (0.000, 1046.5 * p_jit, 0.44, 0.45),
        (0.065, 1568.0 * p_jit, 0.56, 0.55),
    )
    for start_t, freq, pan_pos, amp in notes:
        s_idx = int(start_t * SR)
        avail = n - s_idx
        if avail < 16:
            continue
        pan = pan_pos * math.pi * 0.5
        gl = cos(pan) * 1.414 * amp
        gr = sin(pan) * 1.414 * amp
        w = TWO_PI * freq
        for j in range(avail):
            tn = j * inv_sr
            env = min(1.0, tn / 0.006) * exp(-tn * 7.2)
            sl = (sin(w * tn) + 0.18 * sin(2.0 * w * tn)) * env
            sr_v = (sin((w + TWO_PI * 1.6) * tn + 0.15) + 0.18 * sin(2.0 * w * tn)) * env
            l[s_idx + j] += sl * gl
            r[s_idx + j] += sr_v * gr
    for i in range(n):
        att = _adsr_scalar(i * inv_sr, dur, a=0.004, d=0.06)
        l[i] = tanh(l[i] * att) * 0.85
        r[i] = tanh(r[i] * att) * 0.85
    return l, r


def _py_sub_whoosh(rng: random.Random) -> Tuple[List[float], List[float]]:
    """0.42s restrained airy low-mid editorial sweep for slide and Act transitions."""
    dur = 0.42
    n_out = int(SR * dur)
    sr_h = SR // 2
    n = int(sr_h * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    cos = math.cos
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / sr_h
    raw = [uni(-1.0, 1.0) for _ in range(n)]
    air = _stdlib_bandpass(raw, 140.0, 1150.0, sr=sr_h)
    pi_dur = math.pi / dur
    ph = 0.0
    p_jit = uni(0.97, 1.03)
    for i in range(n):
        t = i * inv_sr
        bell = sin(pi_dur * t) ** 1.7
        freq = (54.0 + 36.0 * bell) * p_jit
        ph += TWO_PI * freq * inv_sr
        sub = sin(ph) * bell * 0.62
        breath = air[i] * bell * 1.55
        mono = tanh(sub + breath) * 0.84
        pan = (0.32 + 0.36 * (t / dur)) * math.pi * 0.5
        l[i] = mono * cos(pan) * 1.414
        r[i] = mono * sin(pan) * 1.414
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _py_alert_ping(rng: random.Random) -> Tuple[List[float], List[float]]:
    """0.38s clean broadcast notification ping for breaking facts and caveats."""
    dur = 0.38
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    p_jit = rng.uniform(0.992, 1.008)
    f0 = 1318.5 * p_jit
    for i in range(n):
        t = i * inv_sr
        env = min(1.0, t / 0.0025) * exp(-t * 9.0) * _adsr_scalar(t, dur, a=0.002, d=0.05)
        shimmer = exp(-t * 18.0) * 0.24
        sl = (sin(TWO_PI * f0 * t) * 0.72 + sin(TWO_PI * (f0 * 2.0) * t) * shimmer + sin(TWO_PI * (f0 * 1.5) * t) * 0.14) * env
        sr_v = (sin(TWO_PI * (f0 + 2.2) * t + 0.18) * 0.72 + sin(TWO_PI * (f0 * 2.0) * t) * shimmer + sin(TWO_PI * (f0 * 1.5) * t) * 0.14) * env
        l[i] = tanh(sl) * 0.85
        r[i] = tanh(sr_v) * 0.85
    return l, r


def _py_soft_thud(rng: random.Random) -> Tuple[List[float], List[float]]:
    """0.32s muted warm low-end weight for editorial headlines and skeptic beats."""
    dur = 0.32
    n = int(SR * dur)
    l = [0.0] * n
    r = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    p_jit = uni(0.98, 1.02)
    felt = _stdlib_bandpass([uni(-1.0, 1.0) for _ in range(n)], 90.0, 420.0)
    ph = 0.0
    for i in range(n):
        t = i * inv_sr
        freq = (48.0 + 68.0 * exp(-t * 24.0)) * p_jit
        ph += TWO_PI * freq * inv_sr
        body = (sin(ph) + 0.22 * sin(2.0 * ph)) * exp(-t * 11.0)
        tap = felt[i] * exp(-t * 48.0) * 0.40
        att = _adsr_scalar(t, dur, a=0.003, d=0.04)
        l[i] = tanh((body + tap) * att) * 0.88
        r[i] = tanh((body - tap * 0.5) * att) * 0.88
    return l, r


STDLIB_GENERATORS: Dict[str, Callable[[random.Random], Tuple[List[float], List[float]]]] = {
    # Shorts cues (9)
    "vine_boom": _py_vine_boom,
    "record_scratch": _py_record_scratch,
    "siren_chirp": _py_siren_chirp,
    "bonk_boing": _py_bonk_boing,
    "cash_ching": _py_cash_ching,
    "ding_levelup": _py_ding_levelup,
    "laser_zap": _py_laser_zap,
    "whoosh": _py_whoosh,
    "pop": _py_pop,
    # Palace / drama cues (9)
    "palace_gong": _py_palace_gong,
    "war_drum_hit": _py_war_drum_hit,
    "silk_whoosh": _py_silk_whoosh,
    "jade_clink": _py_jade_clink,
    "porcelain_shatter": _py_porcelain_shatter,
    "blade_unsheathe": _py_blade_unsheathe,
    "guzheng_gliss": _py_guzheng_gliss,
    "fire_crackle_burst": _py_fire_crackle_burst,
    "court_gasp_swell": _py_court_gasp_swell,
    # General long-form cues (2)
    "cinematic_riser": _py_cinematic_riser,
    "heartbeat_pulse": _py_heartbeat_pulse,
    # Tech / UI editorial cues (6)
    "ui_tick": _py_ui_tick,
    "ui_click": _py_ui_click,
    "data_chime": _py_data_chime,
    "sub_whoosh": _py_sub_whoosh,
    "alert_ping": _py_alert_ping,
    "soft_thud": _py_soft_thud,
}


# =============================================================================
# Vectorized NumPy Cue Generators (enabled automatically when NumPy is installed)
# =============================================================================

if HAS_NUMPY:

    def _np_adsr(t: Any, dur: float, a: float = 0.005, d: float = 0.05) -> Any:
        env = np.ones_like(t, dtype=np.float64)
        if a > 0.0:
            env = np.minimum(env, np.clip(t / a, 0.0, 1.0))
        if d > 0.0:
            env = np.minimum(env, np.clip((dur - t) / d, 0.0, 1.0))
        return env

    def _np_fft_bandpass(x: Any, low_hz: float, high_hz: float) -> Any:
        n = x.shape[0]
        fft_n = 1 << (n - 1).bit_length()
        freqs = np.fft.rfftfreq(fft_n, d=1.0 / SR)
        spec = np.fft.rfft(x, n=fft_n)
        hp = 1.0 / (1.0 + (low_hz / np.maximum(freqs, 1.0)) ** 4.0) if low_hz > 0 else 1.0
        lp = 1.0 / (1.0 + (freqs / high_hz) ** 4.0) if high_hz > 0 else 1.0
        return np.fft.irfft(spec * hp * lp, n=fft_n)[:n]

    def _np_vine_boom(rng: Any) -> Tuple[Any, Any]:
        dur = 0.95
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.985, 1.015))
        freq = (32.0 + 108.0 * np.exp(-t * 9.5)) * p_jit
        phase = TWO_PI * np.cumsum(freq) / SR
        sub = np.sin(phase) + 0.35 * np.sin(phase * 2.0)
        click_l = rng.uniform(-1.0, 1.0, size=n) * np.exp(-t * 140.0) * 0.42
        click_r = rng.uniform(-1.0, 1.0, size=n) * np.exp(-t * 140.0) * 0.42
        air = np.sin(phase * 3.0 + 0.4) * np.exp(-t * 18.0) * 0.12
        env = np.exp(-t * 3.8) * _np_adsr(t, dur, a=0.003, d=0.08)
        return np.tanh((sub * 1.45 + click_l + air) * env) * 0.92, np.tanh((sub * 1.45 + click_r - air) * env) * 0.92

    def _np_record_scratch(rng: Any) -> Tuple[Any, Any]:
        dur = 0.42
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.98, 1.02))
        freq = np.where(t < 0.14, 420.0 + 1600.0 * (t / 0.14), 2020.0 * np.exp(-(t - 0.14) * 9.0) + 180.0) * p_jit
        phase = TWO_PI * np.cumsum(freq) / SR
        raw_l = rng.uniform(-1.0, 1.0, size=n)
        raw_r = rng.uniform(-1.0, 1.0, size=n)
        hp_l = raw_l - 0.7 * np.concatenate(([0.0], raw_l[:-1]))
        hp_r = raw_r - 0.7 * np.concatenate(([0.0], raw_r[:-1]))
        harm_l = np.sin(phase) + 0.5 * np.sin(phase * 2.7)
        harm_r = np.sin(phase + 0.28) + 0.5 * np.sin(phase * 2.72 - 0.2)
        env = _np_adsr(t, dur, a=0.004, d=0.06) * (0.45 + 0.55 * np.sin(math.pi * t / dur))
        return np.tanh((harm_l * 0.65 + hp_l * 0.45) * env) * 0.85, np.tanh((harm_r * 0.65 + hp_r * 0.45) * env) * 0.85

    def _np_siren_chirp(rng: Any) -> Tuple[Any, Any]:
        dur = 0.48
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.985, 1.015))
        cycle = (t * 4.2) % 1.0
        freq = (680.0 + 720.0 * (0.5 - 0.5 * np.cos(TWO_PI * cycle))) * p_jit
        phase = TWO_PI * np.cumsum(freq) / SR
        sig_l = np.sin(phase) + 0.25 * np.sin(3.0 * phase)
        sig_r = np.sin(phase + 0.22) + 0.25 * np.sin(3.0 * phase - 0.35)
        sway = (0.42 + 0.16 * np.sin(TWO_PI * 2.2 * t)) * math.pi * 0.5
        env = _np_adsr(t, dur, a=0.008, d=0.05) * 0.75
        return np.tanh(sig_l * 0.9) * env * np.cos(sway) * 1.414, np.tanh(sig_r * 0.9) * env * np.sin(sway) * 1.414

    def _np_bonk_boing(rng: Any) -> Tuple[Any, Any]:
        dur = 0.65
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.98, 1.02))
        bonk = np.sin(TWO_PI * (340.0 * np.exp(-t * 25.0) + 110.0) * p_jit * t) * np.exp(-t * 28.0)
        wobble_freq = (210.0 + 110.0 * np.sin(TWO_PI * 13.5 * t) * np.exp(-t * 3.0) + 90.0 * t) * p_jit
        phase_l = TWO_PI * np.cumsum(wobble_freq) / SR
        phase_r = TWO_PI * np.cumsum(wobble_freq + 2.4) / SR
        spring_l = (np.sin(phase_l) + 0.3 * np.sin(2.0 * phase_l)) * np.exp(-t * 4.2)
        spring_r = (np.sin(phase_r + 0.2) + 0.3 * np.sin(2.0 * phase_r)) * np.exp(-t * 4.2)
        env = _np_adsr(t, dur, a=0.002, d=0.06)
        return np.tanh((bonk * 0.9 + spring_l * 0.7) * env) * 0.85, np.tanh((bonk * 0.9 + spring_r * 0.7) * env) * 0.85

    def _np_cash_ching(rng: Any) -> Tuple[Any, Any]:
        dur = 0.72
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.99, 1.01))
        clack_l = rng.uniform(-1.0, 1.0, size=n) * np.exp(-t * 75.0) * 0.55
        clack_r = rng.uniform(-1.0, 1.0, size=n) * np.exp(-t * 75.0) * 0.55
        tb = np.maximum(0.0, t - 0.045)
        active = (t >= 0.045).astype(np.float64)
        bell_env = np.exp(-tb * 5.2) * _np_adsr(tb, dur - 0.045, a=0.002, d=0.08) * active
        bell_l = np.zeros(n, dtype=np.float64)
        bell_r = np.zeros(n, dtype=np.float64)
        for f_base, amp in ((2093.0, 0.50), (3136.0, 0.35), (4186.0, 0.25), (5274.0, 0.15)):
            f = f_base * p_jit
            bell_l += amp * np.sin(TWO_PI * f * tb)
            bell_r += amp * np.sin(TWO_PI * (f + 3.2) * tb + 0.25)
        bell_l *= bell_env
        bell_r *= bell_env
        return np.tanh(clack_l + bell_l * 1.1) * 0.82, np.tanh(clack_r + bell_r * 1.1) * 0.82

    def _np_ding_levelup(rng: Any) -> Tuple[Any, Any]:
        p_jit = float(rng.uniform(0.992, 1.008))
        notes = tuple(f * p_jit for f in (1046.5, 1318.5, 1568.0, 2093.0))
        step = 0.075
        dur = step * len(notes) + 0.38
        n = int(SR * dur)
        l = np.zeros(n, dtype=np.float64)
        r = np.zeros(n, dtype=np.float64)
        for idx, freq in enumerate(notes):
            start_i = int(idx * step * SR)
            note_dur = 0.42 if idx == len(notes) - 1 else 0.18
            note_n = min(int(note_dur * SR), n - start_i)
            tn = np.arange(note_n, dtype=np.float64) / SR
            dec = 5.5 if idx == len(notes) - 1 else 14.0
            env = np.exp(-tn * dec) * _np_adsr(tn, note_dur, a=0.003, d=0.03)
            sig = (np.sin(TWO_PI * freq * tn) + 0.25 * np.sin(4.0 * math.pi * freq * tn)) * env
            pan = (0.32 + 0.12 * idx) * math.pi * 0.5
            l[start_i : start_i + note_n] += sig * (math.cos(pan) * 1.414 * 0.48)
            r[start_i : start_i + note_n] += sig * (math.sin(pan) * 1.414 * 0.48)
        return np.tanh(l) * 0.82, np.tanh(r) * 0.82

    def _np_laser_zap(rng: Any) -> Tuple[Any, Any]:
        dur = 0.28
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.975, 1.025))
        freq = (2600.0 * np.exp(-t * 11.5) + 140.0) * p_jit
        phase = TWO_PI * np.cumsum(freq) / SR
        sig_l = np.sin(phase) + 0.45 * np.sin(2.0 * phase) + 0.2 * np.sin(3.0 * phase)
        sig_r = np.sin(phase + 0.18) + 0.45 * np.sin(2.0 * phase - 0.2) + 0.2 * np.sin(3.0 * phase)
        env = _np_adsr(t, dur, a=0.002, d=0.04) * np.exp(-t * 4.0)
        pan = (0.34 + 0.32 * (t / dur)) * math.pi * 0.5
        return np.tanh(sig_l * env) * 0.8 * np.cos(pan) * 1.414, np.tanh(sig_r * env) * 0.8 * np.sin(pan) * 1.414

    def _np_whoosh(rng: Any) -> Tuple[Any, Any]:
        dur = 0.34
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        bell = np.sin(math.pi * t / dur) ** 1.6
        raw = rng.uniform(-1.0, 1.0, size=n)
        bp = _np_fft_bandpass(raw, 260.0, 2600.0)
        mono = np.tanh(bp * 2.4) * bell * 0.78
        # Smooth L-to-R stereo pan sweep across whoosh
        pan = (0.20 + 0.60 * (t / dur)) * math.pi * 0.5
        return mono * np.cos(pan) * 1.414, mono * np.sin(pan) * 1.414

    def _np_pop(rng: Any) -> Tuple[Any, Any]:
        dur = 0.095
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.97, 1.03))
        freq = (210.0 + 520.0 * (t / dur)) * p_jit
        phase = TWO_PI * np.cumsum(freq) / SR
        env = np.exp(-t * 32.0) * _np_adsr(t, dur, a=0.002, d=0.015)
        return np.tanh(np.sin(phase) * 1.4) * env * 0.85, np.tanh(np.sin(phase + 0.24) * 1.4) * env * 0.85

    def _np_palace_gong(rng: Any) -> Tuple[Any, Any]:
        dur = 2.4
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        bloom = 1.0 - 0.032 * np.exp(-t * 4.2) + 0.014 * np.exp(-t * 1.1)
        phase = TWO_PI * np.cumsum(108.0 * bloom) / SR
        partials = (
            (1.00, 1.00, 0.95),
            (1.47, 0.62, 1.20),
            (2.02, 0.48, 1.45),
            (2.73, 0.34, 1.85),
            (3.58, 0.25, 2.30),
            (4.62, 0.18, 2.85),
            (6.10, 0.10, 3.60),
        )
        l = np.zeros(n, dtype=np.float64)
        r = np.zeros(n, dtype=np.float64)
        for ratio, amp, dec in partials:
            env = np.exp(-t * dec) * amp
            l += np.sin(phase * ratio) * env
            r += np.sin(phase * (ratio * 1.0055) + 0.45) * env
        wash = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 600.0, 5200.0) * np.exp(-t * 3.8) * 0.28
        thump = np.sin(TWO_PI * 72.0 * t) * np.exp(-t * 24.0) * 0.45
        att = _np_adsr(t, dur, a=0.008, d=0.22)
        return np.tanh((l * 0.55 + wash + thump) * att * 1.15) * 0.88, np.tanh((r * 0.55 - wash * 0.85 + thump) * att * 1.15) * 0.88

    def _np_war_drum_hit(rng: Any) -> Tuple[Any, Any]:
        dur = 1.1
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        freq = 38.0 + 96.0 * np.exp(-t * 16.0)
        phase = TWO_PI * np.cumsum(freq) / SR
        sub = np.sin(phase) + 0.40 * np.sin(phase * 2.0) + 0.16 * np.sin(phase * 3.0)
        slap = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 180.0, 2600.0) * np.exp(-t * 68.0) * 0.48
        rim = np.sin(TWO_PI * 245.0 * t) * np.exp(-t * 42.0) * 0.25
        env = np.exp(-t * 3.6) * _np_adsr(t, dur, a=0.0025, d=0.14)
        body = np.tanh((sub * 1.25 + slap + rim) * env) * 0.90
        room = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 80.0, 900.0) * np.exp(-t * 5.2) * 0.08
        return np.tanh(body + room) * 0.92, np.tanh(body - room) * 0.92

    def _np_silk_whoosh(rng: Any) -> Tuple[Any, Any]:
        dur = 0.55
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        bell = np.sin(math.pi * t / dur) ** 1.45
        raw = rng.uniform(-1.0, 1.0, size=n)
        low_silk = _np_fft_bandpass(raw, 420.0, 1900.0)
        high_silk = _np_fft_bandpass(raw, 1800.0, 5400.0)
        sweep_hi = np.sin(math.pi * np.clip((t - 0.06) / (dur - 0.10), 0.0, 1.0)) ** 1.8
        mono = (low_silk * 0.95 + high_silk * 0.72 * sweep_hi) * bell * 1.95
        pan = 0.25 + 0.50 * (t / dur)
        return np.tanh(mono * np.cos(pan * math.pi * 0.5)) * 0.85, np.tanh(mono * np.sin(pan * math.pi * 0.5)) * 0.85

    def _np_jade_clink(rng: Any) -> Tuple[Any, Any]:
        dur = 0.65
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        partials = ((1950.0, 0.18, 12.0), (3210.0, 0.65, 7.8), (5120.0, 0.45, 9.4), (7810.0, 0.28, 12.8))
        l = np.zeros(n, dtype=np.float64)
        r = np.zeros(n, dtype=np.float64)
        for freq, amp, dec in partials:
            env = np.exp(-t * dec) * amp
            l += np.sin(TWO_PI * freq * t) * env
            r += np.sin(TWO_PI * (freq + 3.8) * t + 0.25) * env
        click = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 2800.0, 9000.0) * np.exp(-t * 220.0) * 0.35
        att = _np_adsr(t, dur, a=0.001, d=0.08)
        return np.tanh((l + click) * att * 0.95) * 0.82, np.tanh((r - click * 0.6) * att * 0.95) * 0.82

    def _np_porcelain_shatter(rng: Any) -> Tuple[Any, Any]:
        dur = 1.15
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        crack = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 1400.0, 9500.0) * np.exp(-t * 65.0) * 0.95
        crack_body = (
            np.sin(TWO_PI * 1680.0 * t) * np.exp(-t * 36.0) * 0.40
            + np.sin(TWO_PI * 2940.0 * t) * np.exp(-t * 42.0) * 0.35
        )
        l = crack + crack_body
        r = crack * 0.92 + crack_body
        shard_times = np.sort(rng.uniform(0.035, 0.96, size=26) ** 1.35)
        for st in shard_times:
            s_idx = int(st * SR)
            sn = min(int(0.09 * SR), n - s_idx)
            if sn < 32:
                continue
            ts = np.arange(sn, dtype=np.float64) / SR
            f1 = rng.uniform(2400.0, 6200.0)
            f2 = f1 * rng.uniform(1.42, 1.88)
            amp = rng.uniform(0.12, 0.34) * math.exp(-st * 1.45)
            ping = (
                np.sin(TWO_PI * f1 * ts) * 0.65
                + np.sin(TWO_PI * f2 * ts) * 0.45
                + rng.uniform(-0.25, 0.25, size=sn) * np.exp(-ts * 180.0)
            ) * np.exp(-ts * rng.uniform(45.0, 95.0)) * amp
            pan = rng.uniform(0.15, 0.85)
            l[s_idx : s_idx + sn] += ping * math.cos(pan * math.pi * 0.5)
            r[s_idx : s_idx + sn] += ping * math.sin(pan * math.pi * 0.5)
        att = _np_adsr(t, dur, a=0.001, d=0.10)
        return np.tanh(l * att * 1.05) * 0.88, np.tanh(r * att * 1.05) * 0.88

    def _np_blade_unsheathe(rng: Any) -> Tuple[Any, Any]:
        dur = 0.75
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        scrape = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 2200.0, 8800.0)
        scrape_env = np.sin(math.pi * np.clip(t / 0.48, 0.0, 1.0)) ** 1.3 * np.exp(-np.maximum(0.0, t - 0.25) * 7.0)
        sweep_prog = np.clip(t / 0.42, 0.0, 1.0)
        f_base = 1150.0 + 1850.0 * (sweep_prog ** 0.75)
        ph1 = TWO_PI * np.cumsum(f_base) / SR
        ph2 = TWO_PI * np.cumsum(f_base * 1.51) / SR
        ph3 = TWO_PI * np.cumsum(f_base * 2.27) / SR
        ring_env = np.minimum(1.0, t / 0.06) * np.exp(-np.maximum(0.0, t - 0.14) * 5.8)
        ring_l = (np.sin(ph1) * 0.55 + np.sin(ph2) * 0.35 + np.sin(ph3) * 0.22) * ring_env
        ring_r = (np.sin(ph1 * 1.003) * 0.55 + np.sin(ph2 * 0.997) * 0.35 + np.sin(ph3) * 0.22) * ring_env
        flick = np.exp(-((t - 0.36) / 0.015) ** 2) * np.sin(TWO_PI * 4600.0 * t) * 0.28
        att = _np_adsr(t, dur, a=0.008, d=0.12)
        return (
            np.tanh((scrape * scrape_env * 0.52 + ring_l * 0.65 + flick) * att * 1.15) * 0.85,
            np.tanh((scrape * scrape_env * 0.46 + ring_r * 0.65 + flick * 0.85) * att * 1.15) * 0.85,
        )

    def _np_guzheng_gliss(rng: Any) -> Tuple[Any, Any]:
        dur = 1.0
        n = int(SR * dur)
        l = np.zeros(n, dtype=np.float64)
        r = np.zeros(n, dtype=np.float64)
        midis = (62, 65, 67, 69, 72, 74, 77, 79, 81, 84, 86)
        sweep_time = 0.36
        for idx, m in enumerate(midis):
            frac = idx / max(1, len(midis) - 1)
            s_idx = int(((frac ** 0.82) * sweep_time) * SR)
            avail = n - s_idx
            if avail < 64:
                continue
            tn = np.arange(avail, dtype=np.float64) / SR
            freq = 440.0 * (2.0 ** ((m - 69) / 12.0))
            sig = (
                np.sin(TWO_PI * freq * tn) * 0.68 * np.exp(-tn * 4.5)
                + np.sin(4.0 * math.pi * freq * tn) * 0.36 * np.exp(-tn * 7.5)
                + np.sin(6.0 * math.pi * freq * tn) * 0.20 * np.exp(-tn * 12.0)
                + rng.uniform(-0.18, 0.18, size=avail) * np.exp(-tn * 190.0)
            ) * np.minimum(1.0, tn / 0.002)
            pan = 0.22 + 0.56 * frac
            amp = 0.32 + 0.18 * frac
            l[s_idx:] += sig * math.cos(pan * math.pi * 0.5) * amp
            r[s_idx:] += sig * math.sin(pan * math.pi * 0.5) * amp
        t_all = np.arange(n, dtype=np.float64) / SR
        att = _np_adsr(t_all, dur, a=0.002, d=0.16)
        return np.tanh(l * att * 0.95) * 0.86, np.tanh(r * att * 0.95) * 0.86

    def _np_fire_crackle_burst(rng: Any) -> Tuple[Any, Any]:
        dur = 1.3
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        raw = rng.uniform(-1.0, 1.0, size=n)
        roar = _np_fft_bandpass(raw, 95.0, 820.0)
        roar_env = np.minimum(1.0, t / 0.08) * np.exp(-np.maximum(0.0, t - 0.18) * 2.6)
        flame_hi = _np_fft_bandpass(raw, 900.0, 3400.0) * roar_env * 0.28
        l = roar * roar_env * 0.95 + flame_hi
        r = roar * roar_env * 0.92 - flame_hi * 0.8
        for pt in rng.uniform(0.03, 1.12, size=34):
            s_idx = int(pt * SR)
            pn = min(int(0.022 * SR), n - s_idx)
            if pn < 16:
                continue
            tp = np.arange(pn, dtype=np.float64) / SR
            cf = rng.uniform(1800.0, 5600.0)
            snap = (
                rng.uniform(-1.0, 1.0, size=pn) * 0.55
                + np.sin(TWO_PI * cf * tp) * 0.65
            ) * np.exp(-tp * rng.uniform(180.0, 320.0)) * rng.uniform(0.22, 0.55)
            pan = rng.uniform(0.15, 0.85)
            l[s_idx : s_idx + pn] += snap * math.cos(pan * math.pi * 0.5)
            r[s_idx : s_idx + pn] += snap * math.sin(pan * math.pi * 0.5)
        att = _np_adsr(t, dur, a=0.01, d=0.18)
        return np.tanh(l * att * 1.1) * 0.88, np.tanh(r * att * 1.1) * 0.88

    def _np_court_gasp_swell(rng: Any) -> Tuple[Any, Any]:
        dur = 1.2
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        riser_end = 0.92
        riser_prog = np.clip(t / riser_end, 0.0, 1.0)
        riser_env = np.where(t <= riser_end, riser_prog ** 2.4, np.exp(-(t - riser_end) * 28.0))
        ph = TWO_PI * np.cumsum(73.42 * (1.0 + 0.16 * riser_prog)) / SR
        tonal = np.sin(ph) * 0.55 + np.sin(ph * 1.5) * 0.35 + np.sin(ph * 2.12) * 0.25
        raw = rng.uniform(-1.0, 1.0, size=n)
        air_l = _np_fft_bandpass(raw, 450.0, 3200.0)
        air_r = _np_fft_bandpass(np.roll(raw, 317), 450.0, 3200.0)
        td = np.maximum(0.0, t - riser_end)
        drop_active = (t >= riser_end).astype(np.float64)
        drop_ph = TWO_PI * np.cumsum(55.0 * np.exp(-td * 6.0) + 32.0) / SR
        drop = np.sin(drop_ph) * np.exp(-td * 10.0) * drop_active * 0.45
        att = _np_adsr(t, dur, a=0.04, d=0.08)
        return (
            np.tanh(((tonal * 0.65 + air_l * 0.95) * riser_env + drop) * att * 1.1) * 0.85,
            np.tanh(((tonal * 0.65 + air_r * 0.95) * riser_env + drop) * att * 1.1) * 0.85,
        )

    def _np_cinematic_riser(rng: Any) -> Tuple[Any, Any]:
        dur = 2.0
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        peak_t = 1.72
        prog = np.clip(t / peak_t, 0.0, 1.0)
        env = np.where(t <= peak_t, prog ** 2.2, np.exp(-(t - peak_t) * 18.0))
        f_sub = 48.0 + 78.0 * (prog ** 1.6)
        ph = TWO_PI * np.cumsum(f_sub) / SR
        sub = np.sin(ph) * 0.65 + np.sin(ph * 1.498) * 0.35 + np.sin(ph * 2.0) * 0.25
        trem = 0.82 + 0.18 * np.sin(TWO_PI * (3.0 + 11.0 * prog) * t)
        raw = rng.uniform(-1.0, 1.0, size=n)
        shimmer_l = _np_fft_bandpass(raw, 700.0, 4800.0)
        shimmer_r = _np_fft_bandpass(np.roll(raw, 251), 700.0, 4800.0)
        th = np.maximum(0.0, t - peak_t)
        hit_active = (t >= peak_t).astype(np.float64)
        hit_ph = TWO_PI * np.cumsum(36.0 + 82.0 * np.exp(-th * 22.0)) / SR
        hit = np.sin(hit_ph) * np.exp(-th * 9.5) * hit_active * 0.72
        att = _np_adsr(t, dur, a=0.05, d=0.06)
        return (
            np.tanh(((sub * 0.78 + shimmer_l * 0.62 * prog) * env * trem + hit) * att) * 0.88,
            np.tanh(((sub * 0.78 + shimmer_r * 0.62 * prog) * env * trem + hit) * att) * 0.88,
        )

    def _np_heartbeat_pulse(rng: Any) -> Tuple[Any, Any]:
        dur = 1.1
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        l = np.zeros(n, dtype=np.float64)
        r = np.zeros(n, dtype=np.float64)
        room = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 45.0, 260.0)
        for b_start, b_amp, f_lo, f_hi, dec in ((0.0, 1.00, 44.0, 42.0, 10.5), (0.28, 0.72, 38.0, 32.0, 12.0)):
            s_idx = int(b_start * SR)
            bn = min(int(0.45 * SR), n - s_idx)
            tb = np.arange(bn, dtype=np.float64) / SR
            ph = TWO_PI * np.cumsum(f_lo + f_hi * np.exp(-tb * 24.0)) / SR
            env = np.minimum(1.0, tb / 0.012) * np.exp(-tb * dec)
            val = (np.sin(ph) + 0.25 * np.sin(2.0 * ph)) * env * b_amp
            rm = room[s_idx : s_idx + bn] * env * 0.08 * b_amp
            l[s_idx : s_idx + bn] += val + rm
            r[s_idx : s_idx + bn] += val - rm
        att = _np_adsr(t, dur, a=0.004, d=0.08)
        return np.tanh(l * 1.25 * att) * 0.90, np.tanh(r * 1.25 * att) * 0.90

    def _np_ui_tick(rng: Any) -> Tuple[Any, Any]:
        dur = 0.045
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.985, 1.015))
        f1 = 1850.0 * p_jit
        f2 = 3720.0 * p_jit
        env = np.exp(-t * 135.0) * _np_adsr(t, dur, a=0.001, d=0.006)
        tone_l = (np.sin(TWO_PI * f1 * t) * 0.65 + np.sin(TWO_PI * f2 * t) * 0.30) * env
        tone_r = (np.sin(TWO_PI * (f1 + 4.0) * t + 0.12) * 0.65 + np.sin(TWO_PI * f2 * t) * 0.30) * env
        ck = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 2200.0, 6800.0) * np.exp(-t * 280.0) * 0.35
        return np.tanh(tone_l + ck) * 0.85, np.tanh(tone_r - ck * 0.6) * 0.85

    def _np_ui_click(rng: Any) -> Tuple[Any, Any]:
        dur = 0.070
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.985, 1.015))
        f_body = 920.0 * p_jit
        f_top = 3150.0 * p_jit
        snap_bp = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 1600.0, 7200.0)
        body = np.sin(TWO_PI * f_body * t) * np.exp(-t * 145.0) * 0.68
        top = np.sin(TWO_PI * f_top * t) * np.exp(-t * 240.0) * 0.34
        snap = snap_bp * np.exp(-t * 260.0) * 0.42
        tr = np.maximum(0.0, t - 0.022)
        active = (t >= 0.022).astype(np.float64)
        rel = (np.sin(TWO_PI * (f_body * 1.28) * tr) * 0.30 + snap_bp * 0.16) * np.exp(-tr * 190.0) * active
        att = _np_adsr(t, dur, a=0.0008, d=0.008)
        return (
            np.tanh((body + top + snap + rel) * att) * 0.86,
            np.tanh((body + top * 0.94 - snap * 0.5 + rel) * att) * 0.86,
        )

    def _np_data_chime(rng: Any) -> Tuple[Any, Any]:
        dur = 0.52
        n = int(SR * dur)
        l = np.zeros(n, dtype=np.float64)
        r = np.zeros(n, dtype=np.float64)
        p_jit = float(rng.uniform(0.994, 1.006))
        for start_t, freq, pan_pos, amp in (
            (0.000, 1046.5 * p_jit, 0.44, 0.45),
            (0.065, 1568.0 * p_jit, 0.56, 0.55),
        ):
            s_idx = int(start_t * SR)
            avail = n - s_idx
            if avail < 16:
                continue
            tn = np.arange(avail, dtype=np.float64) / SR
            env = np.minimum(1.0, tn / 0.006) * np.exp(-tn * 7.2)
            sl = (np.sin(TWO_PI * freq * tn) + 0.18 * np.sin(4.0 * math.pi * freq * tn)) * env
            sr_v = (np.sin(TWO_PI * (freq + 1.6) * tn + 0.15) + 0.18 * np.sin(4.0 * math.pi * freq * tn)) * env
            pan = pan_pos * math.pi * 0.5
            l[s_idx:] += sl * (math.cos(pan) * 1.414 * amp)
            r[s_idx:] += sr_v * (math.sin(pan) * 1.414 * amp)
        t_all = np.arange(n, dtype=np.float64) / SR
        att = _np_adsr(t_all, dur, a=0.004, d=0.06)
        return np.tanh(l * att) * 0.85, np.tanh(r * att) * 0.85

    def _np_sub_whoosh(rng: Any) -> Tuple[Any, Any]:
        dur = 0.42
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.97, 1.03))
        bell = np.sin(math.pi * t / dur) ** 1.7
        freq = (54.0 + 36.0 * bell) * p_jit
        ph = TWO_PI * np.cumsum(freq) / SR
        sub = np.sin(ph) * bell * 0.62
        air = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 140.0, 1150.0) * bell * 1.55
        mono = np.tanh(sub + air) * 0.84
        pan = (0.32 + 0.36 * (t / dur)) * math.pi * 0.5
        return mono * np.cos(pan) * 1.414, mono * np.sin(pan) * 1.414

    def _np_alert_ping(rng: Any) -> Tuple[Any, Any]:
        dur = 0.38
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.992, 1.008))
        f0 = 1318.5 * p_jit
        env = np.minimum(1.0, t / 0.0025) * np.exp(-t * 9.0) * _np_adsr(t, dur, a=0.002, d=0.05)
        shimmer = np.exp(-t * 18.0) * 0.24
        sl = (np.sin(TWO_PI * f0 * t) * 0.72 + np.sin(TWO_PI * (f0 * 2.0) * t) * shimmer + np.sin(TWO_PI * (f0 * 1.5) * t) * 0.14) * env
        sr_v = (np.sin(TWO_PI * (f0 + 2.2) * t + 0.18) * 0.72 + np.sin(TWO_PI * (f0 * 2.0) * t) * shimmer + np.sin(TWO_PI * (f0 * 1.5) * t) * 0.14) * env
        return np.tanh(sl) * 0.85, np.tanh(sr_v) * 0.85

    def _np_soft_thud(rng: Any) -> Tuple[Any, Any]:
        dur = 0.32
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        p_jit = float(rng.uniform(0.98, 1.02))
        freq = (48.0 + 68.0 * np.exp(-t * 24.0)) * p_jit
        ph = TWO_PI * np.cumsum(freq) / SR
        body = (np.sin(ph) + 0.22 * np.sin(2.0 * ph)) * np.exp(-t * 11.0)
        tap = _np_fft_bandpass(rng.uniform(-1.0, 1.0, size=n), 90.0, 420.0) * np.exp(-t * 48.0) * 0.40
        att = _np_adsr(t, dur, a=0.003, d=0.04)
        return np.tanh((body + tap) * att) * 0.88, np.tanh((body - tap * 0.5) * att) * 0.88

    NUMPY_GENERATORS: Dict[str, Callable[[Any], Tuple[Any, Any]]] = {
        "vine_boom": _np_vine_boom,
        "record_scratch": _np_record_scratch,
        "siren_chirp": _np_siren_chirp,
        "bonk_boing": _np_bonk_boing,
        "cash_ching": _np_cash_ching,
        "ding_levelup": _np_ding_levelup,
        "laser_zap": _np_laser_zap,
        "whoosh": _np_whoosh,
        "pop": _np_pop,
        "palace_gong": _np_palace_gong,
        "war_drum_hit": _np_war_drum_hit,
        "silk_whoosh": _np_silk_whoosh,
        "jade_clink": _np_jade_clink,
        "porcelain_shatter": _np_porcelain_shatter,
        "blade_unsheathe": _np_blade_unsheathe,
        "guzheng_gliss": _np_guzheng_gliss,
        "fire_crackle_burst": _np_fire_crackle_burst,
        "court_gasp_swell": _np_court_gasp_swell,
        "cinematic_riser": _np_cinematic_riser,
        "heartbeat_pulse": _np_heartbeat_pulse,
        "ui_tick": _np_ui_tick,
        "ui_click": _np_ui_click,
        "data_chime": _np_data_chime,
        "sub_whoosh": _np_sub_whoosh,
        "alert_ping": _np_alert_ping,
        "soft_thud": _np_soft_thud,
    }
else:
    NUMPY_GENERATORS = {}

# Public registry of all supported cue names
GENERATORS = NUMPY_GENERATORS if HAS_NUMPY else STDLIB_GENERATORS


def _calibrate_cue_numpy(left: Any, right: Any, target_rms_db: float, max_peak_db: float) -> Tuple[Any, Any]:
    """Calibrate stereo NumPy cue buffer to target RMS with gentle peak ceiling."""
    target_rms = db_to_lin(target_rms_db)
    max_peak = db_to_lin(max_peak_db)
    l_out = left.astype(np.float64, copy=True)
    r_out = right.astype(np.float64, copy=True)
    knee = max_peak * 0.72
    headroom = max(1e-6, max_peak - knee)

    for _ in range(3):
        rms = float(np.sqrt(0.5 * (np.mean(l_out * l_out) + np.mean(r_out * r_out))))
        if rms < 1e-8:
            return l_out, r_out
        if abs(rms - target_rms) / target_rms < 0.015:
            break
        scale = target_rms / rms
        l_out *= scale
        r_out *= scale
        peak = max(float(np.max(np.abs(l_out))), float(np.max(np.abs(r_out))))
        if peak > max_peak:
            for arr in (l_out, r_out):
                mag = np.abs(arr)
                over = np.maximum(0.0, mag - knee)
                shaped = np.where(mag > knee, knee + headroom * np.tanh(over / headroom), mag)
                arr[:] = np.sign(arr) * shaped
    peak_final = max(float(np.max(np.abs(l_out))), float(np.max(np.abs(r_out))))
    if peak_final > max_peak:
        g_cap = max_peak / peak_final
        l_out *= g_cap
        r_out *= g_cap
    return l_out, r_out


def _calibrate_cue_stdlib(
    left: List[float],
    right: List[float],
    target_rms_db: float,
    max_peak_db: float,
) -> Tuple[List[float], List[float]]:
    """Calibrate stereo pure-Python cue buffer to target RMS with gentle peak ceiling."""
    n = len(left)
    if n == 0:
        return left, right
    target_rms = db_to_lin(target_rms_db)
    max_peak = db_to_lin(max_peak_db)
    knee = max_peak * 0.72
    headroom = max(1e-6, max_peak - knee)
    tanh = math.tanh
    l_out = list(left)
    r_out = list(right)

    for _ in range(3):
        energy = 0.0
        for a, b in zip(l_out, r_out):
            energy += a * a + b * b
        rms = math.sqrt(energy / (2.0 * n))
        if rms < 1e-8:
            return l_out, r_out
        if abs(rms - target_rms) / target_rms < 0.015:
            break
        g = target_rms / rms
        peak = 0.0
        for i in range(n):
            lv = l_out[i] * g
            rv = r_out[i] * g
            l_out[i] = lv
            r_out[i] = rv
            al = abs(lv)
            ar = abs(rv)
            if al > peak:
                peak = al
            if ar > peak:
                peak = ar
        if peak > max_peak:
            for vals in (l_out, r_out):
                for i, x in enumerate(vals):
                    ax = abs(x)
                    if ax > knee:
                        shaped = knee + headroom * tanh((ax - knee) / headroom)
                        vals[i] = shaped if x >= 0.0 else -shaped
    peak_final = max(max(abs(a) for a in l_out), max(abs(b) for b in r_out))
    if peak_final > max_peak:
        g_cap = max_peak / peak_final
        l_out = [a * g_cap for a in l_out]
        r_out = [b * g_cap for b in r_out]
    return l_out, r_out


def synthesize_cue(name: str, seed: Optional[int] = None) -> Tuple[Any, Any]:
    """Synthesize a calibrated stereo cue pair (left, right) using NumPy when installed or stdlib fallback."""
    if seed is None:
        seed = zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF
    target_rms_db, max_peak_db = CUE_CALIBRATION.get(name, (-14.0, -2.5))
    if HAS_NUMPY:
        cl, cr = NUMPY_GENERATORS[name](np.random.default_rng(seed))
        return _calibrate_cue_numpy(cl, cr, target_rms_db, max_peak_db)
    cl, cr = STDLIB_GENERATORS[name](random.Random(seed))
    return _calibrate_cue_stdlib(cl, cr, target_rms_db, max_peak_db)


# =============================================================================
# Continuous Stereo Room-Tone Ambience Bed (-36 dBFS RMS)
# =============================================================================

def _synth_ambience_loop_numpy(preset: str, loop_sec: float = 8.0, target_rms_db: float = -36.0) -> Tuple[Any, Any]:
    """Synthesize a seamlessly loopable stereo room-tone bed calibrated to target_rms_db."""
    n = int(round(loop_sec * SR))
    seam_n = min(int(0.08 * SR), n // 8)
    n_gen = n + seam_n
    rng = np.random.default_rng(zlib.crc32(f"amb_{preset}".encode("utf-8")) & 0xFFFFFFFF)
    t = np.arange(n_gen, dtype=np.float64) / SR
    raw_l = rng.uniform(-1.0, 1.0, size=n_gen)
    raw_r = rng.uniform(-1.0, 1.0, size=n_gen)

    if preset == "palace_courtyard":
        # Night breeze + gentle outdoor air shimmer
        breeze_l = _np_fft_bandpass(raw_l, 110.0, 760.0) * (0.78 + 0.22 * np.sin(TWO_PI * (2.0 / loop_sec) * t))
        breeze_r = _np_fft_bandpass(raw_r, 110.0, 760.0) * (0.78 + 0.22 * np.sin(TWO_PI * (3.0 / loop_sec) * t + 1.1))
        air_l = _np_fft_bandpass(raw_l, 1200.0, 3600.0) * 0.14
        air_r = _np_fft_bandpass(raw_r, 1200.0, 3600.0) * 0.14
        l, r = breeze_l + air_l, breeze_r + air_r
    elif preset == "throne_hall":
        # Deep cavernous stone hall resonance
        low_l = _np_fft_bandpass(raw_l, 52.0, 320.0)
        low_r = _np_fft_bandpass(raw_r, 52.0, 320.0)
        mode_l = (np.sin(TWO_PI * 110.0 * t) * 0.08 + np.sin(TWO_PI * 165.0 * t + 0.5) * 0.05)
        mode_r = (np.sin(TWO_PI * 110.0 * t + 0.7) * 0.08 + np.sin(TWO_PI * 165.0 * t + 1.4) * 0.05)
        l, r = low_l + mode_l, low_r + mode_r
    elif preset == "newsroom_air":
        # Smooth studio HVAC / broadcast room tone
        l = _np_fft_bandpass(raw_l, 75.0, 1650.0) * 0.85 + _np_fft_bandpass(raw_l, 70.0, 260.0) * 0.45
        r = _np_fft_bandpass(raw_r, 75.0, 1650.0) * 0.85 + _np_fft_bandpass(raw_r, 70.0, 260.0) * 0.45
    elif preset == "hearth_embers":
        # Warm fire bed + sparse soft tiny ember crackles
        l = _np_fft_bandpass(raw_l, 85.0, 580.0) * (0.82 + 0.18 * np.sin(TWO_PI * (2.0 / loop_sec) * t))
        r = _np_fft_bandpass(raw_r, 85.0, 580.0) * (0.82 + 0.18 * np.sin(TWO_PI * (3.0 / loop_sec) * t + 0.8))
        for _ in range(int(loop_sec * 5)):
            s_i = int(rng.uniform(0.05, loop_sec - 0.05) * SR)
            pn = min(int(0.014 * SR), n_gen - s_i)
            if pn < 16:
                continue
            tp = np.arange(pn, dtype=np.float64) / SR
            cf = rng.uniform(2000.0, 4600.0)
            pop = (np.sin(TWO_PI * cf * tp) * 0.5 + rng.uniform(-0.5, 0.5, size=pn)) * np.exp(-tp * 260.0) * rng.uniform(0.08, 0.22)
            pan = rng.uniform(0.20, 0.80) * math.pi * 0.5
            l[s_i : s_i + pn] += pop * math.cos(pan)
            r[s_i : s_i + pn] += pop * math.sin(pan)
    else:
        return np.zeros(n, dtype=np.float64), np.zeros(n, dtype=np.float64)

    # Seamless overflow loop-seam crossfade
    w = 0.5 - 0.5 * np.cos(math.pi * np.arange(seam_n, dtype=np.float64) / max(1, seam_n))
    l[:seam_n] = l[:seam_n] * w + l[n : n + seam_n] * (1.0 - w)
    r[:seam_n] = r[:seam_n] * w + r[n : n + seam_n] * (1.0 - w)
    l = l[:n]
    r = r[:n]

    rms = float(np.sqrt(0.5 * (np.mean(l * l) + np.mean(r * r))))
    target = db_to_lin(target_rms_db)
    if rms > 1e-9:
        scale = target / rms
        l *= scale
        r *= scale
    return l, r


def _synth_ambience_loop_stdlib(preset: str, loop_sec: float = 6.0, target_rms_db: float = -36.0) -> Tuple[List[float], List[float]]:
    """Fast 24kHz->48kHz seamlessly loopable stereo room-tone bed in pure Python."""
    sr_h = SR // 2
    n = int(round(loop_sec * sr_h))
    seam_n = min(int(0.06 * sr_h), n // 8)
    n_gen = n + seam_n
    n_out = int(round(loop_sec * SR))
    rng = random.Random(zlib.crc32(f"amb_{preset}".encode("utf-8")) & 0xFFFFFFFF)
    raw_l = [rng.uniform(-1.0, 1.0) for _ in range(n_gen)]
    raw_r = [rng.uniform(-1.0, 1.0) for _ in range(n_gen)]
    sin = math.sin
    cos = math.cos
    exp = math.exp
    inv_sr = 1.0 / sr_h

    if preset == "palace_courtyard":
        bl = _stdlib_bandpass(raw_l, 110.0, 760.0, sr=sr_h)
        br = _stdlib_bandpass(raw_r, 110.0, 760.0, sr=sr_h)
        l = [bl[i] * (0.78 + 0.22 * sin(TWO_PI * (2.0 / loop_sec) * (i * inv_sr))) for i in range(n_gen)]
        r = [br[i] * (0.78 + 0.22 * sin(TWO_PI * (3.0 / loop_sec) * (i * inv_sr) + 1.1)) for i in range(n_gen)]
    elif preset == "throne_hall":
        ll = _stdlib_bandpass(raw_l, 52.0, 320.0, sr=sr_h)
        lr = _stdlib_bandpass(raw_r, 52.0, 320.0, sr=sr_h)
        l = [ll[i] + 0.06 * sin(TWO_PI * 110.0 * (i * inv_sr)) for i in range(n_gen)]
        r = [lr[i] + 0.06 * sin(TWO_PI * 110.0 * (i * inv_sr) + 0.7) for i in range(n_gen)]
    elif preset == "newsroom_air":
        l = _stdlib_bandpass(raw_l, 75.0, 1650.0, sr=sr_h)
        r = _stdlib_bandpass(raw_r, 75.0, 1650.0, sr=sr_h)
    elif preset == "hearth_embers":
        l = _stdlib_bandpass(raw_l, 85.0, 580.0, sr=sr_h)
        r = _stdlib_bandpass(raw_r, 85.0, 580.0, sr=sr_h)
        for _ in range(int(loop_sec * 5)):
            s_i = int(rng.uniform(0.05, loop_sec - 0.05) * sr_h)
            pn = min(int(0.014 * sr_h), n_gen - s_i)
            if pn < 8:
                continue
            cf = rng.uniform(2000.0, 4600.0)
            amp = rng.uniform(0.08, 0.22)
            pan = rng.uniform(0.20, 0.80) * math.pi * 0.5
            gl, gr = cos(pan) * amp, sin(pan) * amp
            for j in range(pn):
                tp = j * inv_sr
                pop = (sin(TWO_PI * cf * tp) * 0.5 + rng.uniform(-0.5, 0.5)) * exp(-tp * 260.0)
                l[s_i + j] += pop * gl
                r[s_i + j] += pop * gr
    else:
        return [0.0] * n_out, [0.0] * n_out

    for i in range(seam_n):
        w = 0.5 - 0.5 * cos(math.pi * i / max(1, seam_n))
        l[i] = l[i] * w + l[n + i] * (1.0 - w)
        r[i] = r[i] * w + r[n + i] * (1.0 - w)
    l = l[:n]
    r = r[:n]

    energy = sum(a * a + b * b for a, b in zip(l, r))
    rms = math.sqrt(energy / max(1, 2 * n))
    target = db_to_lin(target_rms_db)
    if rms > 1e-9:
        sc = target / rms
        l = [a * sc for a in l]
        r = [b * sc for b in r]
    return _upsample_2x(l, n_out), _upsample_2x(r, n_out)


def _pack_stereo_pcm_stdlib(left: List[float], right: List[float], gain: float = 1.0) -> bytes:
    """Pack stereo float lists to int16 PCM; soft-limit with tanh ONLY if peak > 0.98."""
    n = len(left)
    if n == 0:
        return b""
    peak = max(max(left), -min(left), max(right), -min(right)) * abs(gain)
    if peak <= 0.98:
        scale = 32767.0 * gain
        pcm = array("h", (max(-32767, min(32767, int(round(v * scale)))) for pair in zip(left, right) for v in pair))
    else:
        tanh = math.tanh
        if gain == 1.0:
            pcm = array("h", (int(round(tanh(v) * 32767.0)) for pair in zip(left, right) for v in pair))
        else:
            pcm = array("h", (int(round(tanh(v * gain) * 32767.0)) for pair in zip(left, right) for v in pair))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def write_stereo_wav(samples: Any, out_path: Path, right: Optional[Any] = None) -> None:
    """Write 48 kHz 16-bit stereo WAV; soft-limit ONLY if peak > 0.98 (no double-tanh)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if HAS_NUMPY and isinstance(samples, np.ndarray):
        l_arr = samples
        r_arr = samples if right is None else right
        peak = max(
            float(np.max(np.abs(l_arr))) if l_arr.size else 0.0,
            float(np.max(np.abs(r_arr))) if r_arr.size else 0.0,
        )
        if peak > 0.98:
            l_out = np.tanh(l_arr)
            r_out = np.tanh(r_arr)
        else:
            l_out = l_arr
            r_out = r_arr
        interleaved = np.empty((l_out.shape[0], 2), dtype=np.int16)
        interleaved[:, 0] = np.clip(np.round(l_out * 32767.0), -32768, 32767).astype(np.int16)
        interleaved[:, 1] = np.clip(np.round(r_out * 32767.0), -32768, 32767).astype(np.int16)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            wf.writeframes(interleaved.tobytes())
        return

    l_list = list(samples)
    r_list = l_list if right is None else list(right)
    pcm_bytes = _pack_stereo_pcm_stdlib(l_list, r_list)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm_bytes)


def _render_timeline_numpy(
    cues: List[Dict],
    total_dur: float,
    out_path: Path,
    ambience: str = "none",
) -> int:
    final_len = int(math.ceil(total_dur * SR))
    mix_l = np.zeros(final_len, dtype=np.float64)
    mix_r = np.zeros(final_len, dtype=np.float64)

    if ambience and ambience != "none":
        amb_l, amb_r = _synth_ambience_loop_numpy(ambience)
        loop_n = amb_l.shape[0]
        if loop_n > 0:
            reps = (final_len + loop_n - 1) // loop_n
            mix_l += np.tile(amb_l, reps)[:final_len]
            mix_r += np.tile(amb_r, reps)[:final_len]
            fade_n = min(final_len // 4, int(0.25 * SR))
            if fade_n > 1:
                ramp = 0.5 - 0.5 * np.cos(math.pi * np.arange(fade_n, dtype=np.float64) / fade_n)
                mix_l[:fade_n] *= ramp
                mix_r[:fade_n] *= ramp
                mix_l[-fade_n:] *= ramp[::-1]
                mix_r[-fade_n:] *= ramp[::-1]

    cache: Dict[Tuple[str, int], Tuple[Any, Any]] = {}
    occ_counts: Dict[str, int] = {}
    placed = 0

    for cue in cues:
        name = cue["sfx"]
        if not name or name == "none":
            continue
        if name not in NUMPY_GENERATORS:
            print(f"Warning: skipping unknown SFX cue '{name}' at {cue['time_sec']:.2f}s", file=sys.stderr)
            continue
        variant = occ_counts.get(name, 0) % 3
        occ_counts[name] = occ_counts.get(name, 0) + 1
        key = (name, variant)
        if key not in cache:
            seed = (zlib.crc32(name.encode("utf-8")) + variant * 7919) & 0xFFFFFFFF
            cache[key] = synthesize_cue(name, seed=seed)
        cl, cr = cache[key]
        start_i = int(cue["time_sec"] * SR)
        if start_i >= final_len:
            continue
        gain = db_to_lin(cue["gain_db"])
        gl, gr = _pan_gains(cue.get("pan", 0.5))
        n = min(cl.shape[0], final_len - start_i)
        mix_l[start_i : start_i + n] += cl[:n] * (gain * gl)
        mix_r[start_i : start_i + n] += cr[:n] * (gain * gr)
        placed += 1

    write_stereo_wav(mix_l, out_path, right=mix_r)
    return placed


def _render_timeline_stdlib(
    cues: List[Dict],
    total_dur: float,
    out_path: Path,
    ambience: str = "none",
) -> int:
    """Python 3.13-safe pure-stdlib float timeline mixer with 3-variant cache, panning, and optional ambience."""
    final_len = int(math.ceil(total_dur * SR))
    cache: Dict[Tuple[str, int], Tuple[List[float], List[float]]] = {}
    occ_counts: Dict[str, int] = {}
    placements: List[Tuple[int, int, str, int, float, float]] = []

    for cue in cues:
        name = cue["sfx"]
        if not name or name == "none":
            continue
        if name not in STDLIB_GENERATORS:
            print(f"Warning: skipping unknown SFX cue '{name}' at {cue['time_sec']:.2f}s", file=sys.stderr)
            continue
        variant = occ_counts.get(name, 0) % 3
        occ_counts[name] = occ_counts.get(name, 0) + 1
        key = (name, variant)
        if key not in cache:
            seed = (zlib.crc32(name.encode("utf-8")) + variant * 7919) & 0xFFFFFFFF
            cache[key] = synthesize_cue(name, seed=seed)
        start_i = int(cue["time_sec"] * SR)
        if start_i >= final_len:
            continue
        cl = cache[key][0]
        end_i = min(final_len, start_i + len(cl))
        if end_i <= start_i:
            continue
        placements.append((start_i, end_i, name, variant, float(cue["gain_db"]), float(cue.get("pan", 0.5))))

    placements.sort(key=lambda x: x[0])

    # Merge overlapping placements into disjoint active spans
    spans: List[Tuple[int, int, List[Tuple[int, int, str, int, float, float]]]] = []
    for item in placements:
        s, e = item[0], item[1]
        if spans and s < spans[-1][1]:
            prev_s, prev_e, group = spans[-1]
            group.append(item)
            spans[-1] = (prev_s, max(prev_e, e), group)
        else:
            spans.append((s, e, [item]))

    has_amb = bool(ambience and ambience != "none")
    amb_l: List[float] = []
    amb_r: List[float] = []
    amb_len = 0
    fade_n = min(final_len // 4, int(0.25 * SR))
    if has_amb:
        amb_l, amb_r = _synth_ambience_loop_stdlib(ambience)
        amb_len = len(amb_l)

    def _amb_sample_slice(start_f: int, length: int) -> Tuple[List[float], List[float]]:
        if not has_amb or amb_len == 0:
            return [0.0] * length, [0.0] * length
        bl = [0.0] * length
        br = [0.0] * length
        cos = math.cos
        pi = math.pi
        for j in range(length):
            g_idx = start_f + j
            k = g_idx % amb_len
            fg = 1.0
            if fade_n > 1:
                if g_idx < fade_n:
                    fg = 0.5 - 0.5 * cos(pi * g_idx / fade_n)
                elif g_idx >= final_len - fade_n:
                    rem = final_len - 1 - g_idx
                    fg = 0.5 - 0.5 * cos(pi * rem / fade_n)
            bl[j] = amb_l[k] * fg
            br[j] = amb_r[k] * fg
        return bl, br

    out_path.parent.mkdir(parents=True, exist_ok=True)
    zero_block_frames = 65536
    zero_block = bytes(zero_block_frames * 4)

    def _write_gap(wf: wave.Wave_write, start_f: int, frames: int) -> None:
        if not has_amb:
            rem = frames
            while rem > 0:
                chunk = min(rem, zero_block_frames)
                wf.writeframes(zero_block if chunk == zero_block_frames else bytes(chunk * 4))
                rem -= chunk
            return
        pos = start_f
        rem = frames
        while rem > 0:
            chunk = min(rem, zero_block_frames)
            bl, br = _amb_sample_slice(pos, chunk)
            wf.writeframes(_pack_stereo_pcm_stdlib(bl, br))
            pos += chunk
            rem -= chunk

    cursor = 0
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)

        for span_s, span_e, group in spans:
            if span_s > cursor:
                _write_gap(wf, cursor, span_s - cursor)

            span_len = span_e - span_s
            buf_l, buf_r = _amb_sample_slice(span_s, span_len)
            for s_i, e_i, name, variant, gain_db, pan in group:
                cl, cr = cache[(name, variant)]
                gain = db_to_lin(gain_db)
                gl, gr = _pan_gains(pan)
                g_l = gain * gl
                g_r = gain * gr
                off = s_i - span_s
                n = e_i - s_i
                for j in range(n):
                    buf_l[off + j] += cl[j] * g_l
                    buf_r[off + j] += cr[j] * g_r
            wf.writeframes(_pack_stereo_pcm_stdlib(buf_l, buf_r))
            cursor = span_e

        if cursor < final_len:
            _write_gap(wf, cursor, final_len - cursor)

    return len(placements)


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
        gain_db = float(item.get("sfx_gain_db", item.get("gain_db", -3.0)))
        pan = max(0.0, min(1.0, float(item.get("sfx_pan", item.get("pan", 0.5)))))
        cues.append({"time_sec": max(0.0, base_t + offset), "sfx": sfx, "gain_db": gain_db, "pan": pan})
        end_t = float(item.get("end_sec", base_t + float(item.get("duration_sec", 1.5))))
        total_dur = max(total_dur, end_t)

    return cues, total_dur


def load_ambience_from_timings(timings_path: Path) -> str:
    """Read optional ambience_bed from shot_timings.json, falling back to 'none'."""
    try:
        data = json.loads(timings_path.read_text(encoding="utf-8"))
    except Exception:
        return "none"
    if not isinstance(data, dict):
        return "none"
    raw = str(data.get("ambience_bed") or "").strip().lower()
    if not raw or raw == "none":
        return "none"
    if raw in AMBIENCE_PRESETS:
        return raw
    print(
        f"Warning: unknown ambience_bed '{raw}' in {timings_path.name}; falling back to 'none'.",
        file=sys.stderr,
    )
    return "none"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Procedural Shorts & Long-Form cinematic SFX engine (48 kHz stereo WAV)."
    )
    parser.add_argument("--list", action="store_true", help="List available SFX cues.")
    parser.add_argument("--cue", help="Render a single SFX cue to --out.")
    parser.add_argument("--timings", help="Path to shot_timings.json or cue list JSON.")
    parser.add_argument("--duration", type=float, default=0.0, help="Override timeline duration in seconds.")
    parser.add_argument("--gain-db", type=float, default=-3.0, help="Single-cue gain in dB (default: -3.0).")
    parser.add_argument(
        "--ambience",
        choices=AMBIENCE_PRESETS,
        default=None,
        help="Optional continuous -36 dBFS stereo room-tone bed mixed into timeline WAV (default: shot_timings.json ambience_bed or none).",
    )
    parser.add_argument("--out", help="Output WAV path.")
    args = parser.parse_args()

    if args.list:
        for name in sorted(GENERATORS):
            print(name)
        return 0

    if not args.out:
        parser.error("--out is required unless --list is passed")

    out_path = Path(args.out)

    if args.cue:
        if args.cue == "none":
            n_sil = int(0.1 * SR)
            if HAS_NUMPY:
                z = np.zeros(n_sil, dtype=np.float64)
                write_stereo_wav(z, out_path, right=z)
            else:
                write_stereo_wav([0.0] * n_sil, out_path)
            print(f"Wrote silent 'none' cue (0.10s) to {out_path}")
            return 0
        if args.cue not in GENERATORS:
            print(f"Error: unknown cue '{args.cue}'. Available: {', '.join(sorted(GENERATORS))}", file=sys.stderr)
            return 2
        gain = db_to_lin(args.gain_db)
        cl, cr = synthesize_cue(args.cue, seed=42)
        if HAS_NUMPY:
            write_stereo_wav(cl * gain, out_path, right=cr * gain)
            dur_s = cl.shape[0] / SR
        else:
            write_stereo_wav([x * gain for x in cl], out_path, right=[x * gain for x in cr])
            dur_s = len(cl) / SR
        print(f"Wrote cue '{args.cue}' ({dur_s:.2f}s) to {out_path}")
        return 0

    if args.timings:
        timings_path = Path(args.timings)
        cues, inferred_dur = load_cues(timings_path)
        total_dur = max(args.duration, inferred_dur, 1.0)
        ambience = args.ambience if args.ambience is not None else load_ambience_from_timings(timings_path)
        if HAS_NUMPY:
            placed = _render_timeline_numpy(cues, total_dur, out_path, ambience=ambience)
        else:
            placed = _render_timeline_stdlib(cues, total_dur, out_path, ambience=ambience)
        engine_tag = "numpy" if HAS_NUMPY else "stdlib"
        amb_tag = f", ambience={ambience}" if ambience != "none" else ""
        print(f"Rendered {placed} SFX cues ({total_dur:.2f}s, {engine_tag}{amb_tag}) to {out_path}")
        return 0

    parser.error("Provide either --cue or --timings")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
