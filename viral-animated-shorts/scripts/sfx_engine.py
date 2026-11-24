#!/usr/bin/env python3
"""Procedural Shorts & Long-Form cinematic SFX synthesizer and timeline renderer (48 kHz 16-bit stereo WAV).

Uses vectorized NumPy synthesis & timeline mixing when NumPy is installed, with a fast
pure-stdlib interval-sparse fallback when NumPy is unavailable. Synthesized cues are
cached per cue name so rendering 55+ cues across a 5-15 minute (300-900s) timeline runs
in well under 2 seconds.

Supported cues (20 total):
  Shorts comedic/punch cues (9):
    record_scratch, vine_boom, siren_chirp, bonk_boing,
    cash_ching, ding_levelup, laser_zap, whoosh, pop
  Palace / historical drama cues (9):
    palace_gong, war_drum_hit, silk_whoosh, jade_clink, porcelain_shatter,
    blade_unsheathe, guzheng_gliss, fire_crackle_burst, court_gasp_swell
  General long-form cinematic cues (2):
    cinematic_riser, heartbeat_pulse

Examples:
  python3 sfx_engine.py --list
  python3 sfx_engine.py --cue vine_boom --out boom.wav
  python3 sfx_engine.py --cue palace_gong --out gong.wav
  python3 sfx_engine.py --timings audio/shot_timings.json --out audio/sfx_track.wav
"""

import argparse
from array import array
import json
import math
import random
import struct
import sys
import warnings
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

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop  # type: ignore
    HAS_AUDIOOP = True
except ImportError:
    audioop = None  # type: ignore
    HAS_AUDIOOP = False

SR = 48000
TWO_PI = 2.0 * math.pi


def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


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
    out = [0.0] * n
    phase = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    for i in range(n):
        t = i * inv_sr
        freq = 32.0 + 108.0 * exp(-t * 9.5)
        phase += TWO_PI * freq * inv_sr
        sub = sin(phase) + 0.35 * sin(phase * 2.0)
        click = uni(-1.0, 1.0) * exp(-t * 140.0) * 0.4
        env = exp(-t * 3.8) * _adsr_scalar(t, dur, a=0.003, d=0.08)
        out[i] = tanh((sub * 1.45 + click) * env) * 0.92
    return out, list(out)


def _py_record_scratch(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.42
    n = int(SR * dur)
    out = [0.0] * n
    phase = 0.0
    prev_noise = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    pi_dur = math.pi / dur
    for i in range(n):
        t = i * inv_sr
        if t < 0.14:
            freq = 420.0 + 1600.0 * (t / 0.14)
        else:
            freq = 2020.0 * exp(-(t - 0.14) * 9.0) + 180.0
        phase += TWO_PI * freq * inv_sr
        raw_n = uni(-1.0, 1.0)
        hp_noise = raw_n - 0.7 * prev_noise
        prev_noise = raw_n
        harmonic = sin(phase) + 0.5 * sin(phase * 2.7)
        env = _adsr_scalar(t, dur, a=0.004, d=0.06) * (0.45 + 0.55 * sin(pi_dur * t))
        out[i] = tanh((harmonic * 0.65 + hp_noise * 0.45) * env) * 0.85
    return out, list(out)


def _py_siren_chirp(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.48
    n = int(SR * dur)
    out = [0.0] * n
    phase = 0.0
    sin = math.sin
    cos = math.cos
    tanh = math.tanh
    inv_sr = 1.0 / SR
    for i in range(n):
        t = i * inv_sr
        cycle = (t * 4.2) % 1.0
        freq = 680.0 + 720.0 * (0.5 - 0.5 * cos(TWO_PI * cycle))
        phase += TWO_PI * freq * inv_sr
        sig = sin(phase) + 0.25 * sin(3.0 * phase)
        env = _adsr_scalar(t, dur, a=0.008, d=0.05)
        out[i] = tanh(sig * 0.9) * env * 0.75
    return out, list(out)


def _py_bonk_boing(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.65
    n = int(SR * dur)
    out = [0.0] * n
    phase = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    for i in range(n):
        t = i * inv_sr
        bonk = sin(TWO_PI * (340.0 * exp(-t * 25.0) + 110.0) * t) * exp(-t * 28.0)
        wobble_freq = 210.0 + 110.0 * sin(TWO_PI * 13.5 * t) * exp(-t * 3.0) + 90.0 * t
        phase += TWO_PI * wobble_freq * inv_sr
        spring = (sin(phase) + 0.3 * sin(2.0 * phase)) * exp(-t * 4.2)
        env = _adsr_scalar(t, dur, a=0.002, d=0.06)
        out[i] = tanh((bonk * 0.9 + spring * 0.7) * env) * 0.85
    return out, list(out)


def _py_cash_ching(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.72
    n = int(SR * dur)
    out = [0.0] * n
    freqs = (2093.0, 3136.0, 4186.0, 5274.0)
    amps = (0.50, 0.35, 0.25, 0.15)
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    uni = rng.uniform
    inv_sr = 1.0 / SR
    for i in range(n):
        t = i * inv_sr
        clack = uni(-1.0, 1.0) * exp(-t * 75.0) * 0.55
        bell = 0.0
        if t >= 0.045:
            tb = t - 0.045
            bell_env = exp(-tb * 5.2) * _adsr_scalar(tb, dur - 0.045, a=0.002, d=0.08)
            for f, a in zip(freqs, amps):
                bell += a * sin(TWO_PI * f * tb)
            bell *= bell_env
        out[i] = tanh(clack + bell * 1.1) * 0.82
    return out, list(out)


def _py_ding_levelup(rng: random.Random) -> Tuple[List[float], List[float]]:
    notes = (1046.5, 1318.5, 1568.0, 2093.0)
    step = 0.075
    dur = step * len(notes) + 0.38
    n = int(SR * dur)
    out = [0.0] * n
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    for idx, freq in enumerate(notes):
        start_i = int(idx * step * SR)
        note_dur = 0.42 if idx == len(notes) - 1 else 0.18
        note_n = min(int(note_dur * SR), n - start_i)
        dec = 5.5 if idx == len(notes) - 1 else 14.0
        w = TWO_PI * freq
        for j in range(note_n):
            t = j * inv_sr
            env = exp(-t * dec) * _adsr_scalar(t, note_dur, a=0.003, d=0.03)
            sig = sin(w * t) + 0.25 * sin(2.0 * w * t)
            out[start_i + j] += sig * env * 0.48
    out = [tanh(x) * 0.82 for x in out]
    return out, list(out)


def _py_laser_zap(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.28
    n = int(SR * dur)
    out = [0.0] * n
    phase = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    for i in range(n):
        t = i * inv_sr
        freq = 2600.0 * exp(-t * 11.5) + 140.0
        phase += TWO_PI * freq * inv_sr
        sig = sin(phase) + 0.45 * sin(2.0 * phase) + 0.2 * sin(3.0 * phase)
        env = _adsr_scalar(t, dur, a=0.002, d=0.04) * exp(-t * 4.0)
        out[i] = tanh(sig * env) * 0.8
    return out, list(out)


def _py_whoosh(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.34
    n = int(SR * dur)
    out = [0.0] * n
    lp = 0.0
    lp2 = 0.0
    sin = math.sin
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
        out[i] = tanh(bp * 2.8) * bell * 0.78
    return out, list(out)


def _py_pop(rng: random.Random) -> Tuple[List[float], List[float]]:
    dur = 0.095
    n = int(SR * dur)
    out = [0.0] * n
    phase = 0.0
    sin = math.sin
    exp = math.exp
    tanh = math.tanh
    inv_sr = 1.0 / SR
    for i in range(n):
        t = i * inv_sr
        freq = 210.0 + 520.0 * (t / dur)
        phase += TWO_PI * freq * inv_sr
        env = exp(-t * 32.0) * _adsr_scalar(t, dur, a=0.002, d=0.015)
        out[i] = tanh(sin(phase) * 1.4) * env * 0.85
    return out, list(out)


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
        freq = 32.0 + 108.0 * np.exp(-t * 9.5)
        phase = TWO_PI * np.cumsum(freq) / SR
        sub = np.sin(phase) + 0.35 * np.sin(phase * 2.0)
        click = rng.uniform(-1.0, 1.0, size=n) * np.exp(-t * 140.0) * 0.4
        env = np.exp(-t * 3.8) * _np_adsr(t, dur, a=0.003, d=0.08)
        out = np.tanh((sub * 1.45 + click) * env) * 0.92
        return out, out.copy()

    def _np_record_scratch(rng: Any) -> Tuple[Any, Any]:
        dur = 0.42
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        freq = np.where(t < 0.14, 420.0 + 1600.0 * (t / 0.14), 2020.0 * np.exp(-(t - 0.14) * 9.0) + 180.0)
        phase = TWO_PI * np.cumsum(freq) / SR
        raw = rng.uniform(-1.0, 1.0, size=n)
        hp = raw - 0.7 * np.concatenate(([0.0], raw[:-1]))
        harmonic = np.sin(phase) + 0.5 * np.sin(phase * 2.7)
        env = _np_adsr(t, dur, a=0.004, d=0.06) * (0.45 + 0.55 * np.sin(math.pi * t / dur))
        out = np.tanh((harmonic * 0.65 + hp * 0.45) * env) * 0.85
        return out, out.copy()

    def _np_siren_chirp(rng: Any) -> Tuple[Any, Any]:
        dur = 0.48
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        cycle = (t * 4.2) % 1.0
        freq = 680.0 + 720.0 * (0.5 - 0.5 * np.cos(TWO_PI * cycle))
        phase = TWO_PI * np.cumsum(freq) / SR
        sig = np.sin(phase) + 0.25 * np.sin(3.0 * phase)
        env = _np_adsr(t, dur, a=0.008, d=0.05)
        out = np.tanh(sig * 0.9) * env * 0.75
        return out, out.copy()

    def _np_bonk_boing(rng: Any) -> Tuple[Any, Any]:
        dur = 0.65
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        bonk = np.sin(TWO_PI * (340.0 * np.exp(-t * 25.0) + 110.0) * t) * np.exp(-t * 28.0)
        wobble_freq = 210.0 + 110.0 * np.sin(TWO_PI * 13.5 * t) * np.exp(-t * 3.0) + 90.0 * t
        phase = TWO_PI * np.cumsum(wobble_freq) / SR
        spring = (np.sin(phase) + 0.3 * np.sin(2.0 * phase)) * np.exp(-t * 4.2)
        env = _np_adsr(t, dur, a=0.002, d=0.06)
        out = np.tanh((bonk * 0.9 + spring * 0.7) * env) * 0.85
        return out, out.copy()

    def _np_cash_ching(rng: Any) -> Tuple[Any, Any]:
        dur = 0.72
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        clack = rng.uniform(-1.0, 1.0, size=n) * np.exp(-t * 75.0) * 0.55
        tb = np.maximum(0.0, t - 0.045)
        active = (t >= 0.045).astype(np.float64)
        bell_env = np.exp(-tb * 5.2) * _np_adsr(tb, dur - 0.045, a=0.002, d=0.08) * active
        bell = (
            0.50 * np.sin(TWO_PI * 2093.0 * tb)
            + 0.35 * np.sin(TWO_PI * 3136.0 * tb)
            + 0.25 * np.sin(TWO_PI * 4186.0 * tb)
            + 0.15 * np.sin(TWO_PI * 5274.0 * tb)
        ) * bell_env
        out = np.tanh(clack + bell * 1.1) * 0.82
        return out, out.copy()

    def _np_ding_levelup(rng: Any) -> Tuple[Any, Any]:
        notes = (1046.5, 1318.5, 1568.0, 2093.0)
        step = 0.075
        dur = step * len(notes) + 0.38
        n = int(SR * dur)
        out = np.zeros(n, dtype=np.float64)
        for idx, freq in enumerate(notes):
            start_i = int(idx * step * SR)
            note_dur = 0.42 if idx == len(notes) - 1 else 0.18
            note_n = min(int(note_dur * SR), n - start_i)
            tn = np.arange(note_n, dtype=np.float64) / SR
            dec = 5.5 if idx == len(notes) - 1 else 14.0
            env = np.exp(-tn * dec) * _np_adsr(tn, note_dur, a=0.003, d=0.03)
            sig = np.sin(TWO_PI * freq * tn) + 0.25 * np.sin(4.0 * math.pi * freq * tn)
            out[start_i : start_i + note_n] += sig * env * 0.48
        out = np.tanh(out) * 0.82
        return out, out.copy()

    def _np_laser_zap(rng: Any) -> Tuple[Any, Any]:
        dur = 0.28
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        freq = 2600.0 * np.exp(-t * 11.5) + 140.0
        phase = TWO_PI * np.cumsum(freq) / SR
        sig = np.sin(phase) + 0.45 * np.sin(2.0 * phase) + 0.2 * np.sin(3.0 * phase)
        env = _np_adsr(t, dur, a=0.002, d=0.04) * np.exp(-t * 4.0)
        out = np.tanh(sig * env) * 0.8
        return out, out.copy()

    def _np_whoosh(rng: Any) -> Tuple[Any, Any]:
        dur = 0.34
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        bell = np.sin(math.pi * t / dur) ** 1.6
        raw = rng.uniform(-1.0, 1.0, size=n)
        bp = _np_fft_bandpass(raw, 260.0, 2600.0)
        out = np.tanh(bp * 2.4) * bell * 0.78
        return out, out.copy()

    def _np_pop(rng: Any) -> Tuple[Any, Any]:
        dur = 0.095
        n = int(SR * dur)
        t = np.arange(n, dtype=np.float64) / SR
        freq = 210.0 + 520.0 * (t / dur)
        phase = TWO_PI * np.cumsum(freq) / SR
        env = np.exp(-t * 32.0) * _np_adsr(t, dur, a=0.002, d=0.015)
        out = np.tanh(np.sin(phase) * 1.4) * env * 0.85
        return out, out.copy()

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
    }
else:
    NUMPY_GENERATORS = {}

# Public registry of all supported cue names
GENERATORS = NUMPY_GENERATORS if HAS_NUMPY else STDLIB_GENERATORS


def synthesize_cue(name: str, seed: Optional[int] = None) -> Tuple[Any, Any]:
    """Synthesize a stereo cue pair (left, right) using NumPy when installed or stdlib fallback."""
    if seed is None:
        seed = zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF
    if HAS_NUMPY:
        return NUMPY_GENERATORS[name](np.random.default_rng(seed))
    return STDLIB_GENERATORS[name](random.Random(seed))


def _pack_stereo_pcm_stdlib(left: List[float], right: List[float], gain: float = 1.0) -> bytes:
    n = len(left)
    if n == 0:
        return b""
    peak = max(max(left), -min(left), max(right), -min(right)) * abs(gain)
    if peak <= 0.99:
        scale = 32767.0 * gain
        pcm = array("h", (int(v * scale) for pair in zip(left, right) for v in pair))
    else:
        tanh = math.tanh
        if gain == 1.0:
            pcm = array("h", (int(tanh(v) * 32767.0) for pair in zip(left, right) for v in pair))
        else:
            pcm = array("h", (int(tanh(v * gain) * 32767.0) for pair in zip(left, right) for v in pair))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def write_stereo_wav(samples: Any, out_path: Path, right: Optional[Any] = None) -> None:
    """Write 48 kHz 16-bit stereo WAV from mono or stereo (left, right) buffers."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if HAS_NUMPY and isinstance(samples, np.ndarray):
        l_arr = samples
        r_arr = samples if right is None else right
        interleaved = np.empty((l_arr.shape[0], 2), dtype=np.int16)
        interleaved[:, 0] = np.clip(np.round(np.tanh(l_arr) * 32767.0), -32768, 32767).astype(np.int16)
        interleaved[:, 1] = np.clip(np.round(np.tanh(r_arr) * 32767.0), -32768, 32767).astype(np.int16)
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


def _render_timeline_numpy(cues: List[Dict], total_dur: float, out_path: Path) -> int:
    final_len = int(math.ceil(total_dur * SR))
    mix_l = np.zeros(final_len, dtype=np.float64)
    mix_r = np.zeros(final_len, dtype=np.float64)
    cache: Dict[str, Tuple[Any, Any]] = {}
    placed = 0

    for cue in cues:
        name = cue["sfx"]
        if not name or name == "none":
            continue
        if name not in NUMPY_GENERATORS:
            print(f"Warning: skipping unknown SFX cue '{name}' at {cue['time_sec']:.2f}s", file=sys.stderr)
            continue
        if name not in cache:
            cache[name] = synthesize_cue(name)
        cl, cr = cache[name]
        start_i = int(cue["time_sec"] * SR)
        if start_i >= final_len:
            continue
        gain = db_to_lin(cue["gain_db"])
        n = min(cl.shape[0], final_len - start_i)
        mix_l[start_i : start_i + n] += cl[:n] * gain
        mix_r[start_i : start_i + n] += cr[:n] * gain
        placed += 1

    write_stereo_wav(mix_l, out_path, right=mix_r)
    return placed


def _render_timeline_stdlib(cues: List[Dict], total_dur: float, out_path: Path) -> int:
    """Fast pure-stdlib interval-sparse timeline mixer & chunked-zero WAV streamer."""
    final_len = int(math.ceil(total_dur * SR))
    cache: Dict[str, Tuple[List[float], List[float]]] = {}
    base_pcm_cache: Dict[str, bytes] = {}
    pcm_cache: Dict[Tuple[str, float], bytes] = {}
    placements: List[Tuple[int, int, str, float]] = []

    for cue in cues:
        name = cue["sfx"]
        if not name or name == "none":
            continue
        if name not in STDLIB_GENERATORS:
            print(f"Warning: skipping unknown SFX cue '{name}' at {cue['time_sec']:.2f}s", file=sys.stderr)
            continue
        if name not in cache:
            cache[name] = synthesize_cue(name)
        start_i = int(cue["time_sec"] * SR)
        if start_i >= final_len:
            continue
        cl = cache[name][0]
        end_i = min(final_len, start_i + len(cl))
        if end_i <= start_i:
            continue
        placements.append((start_i, end_i, name, float(cue["gain_db"])))

    placements.sort(key=lambda x: x[0])

    # Merge overlapping placements into disjoint active spans
    spans: List[Tuple[int, int, List[Tuple[int, int, str, float]]]] = []
    for item in placements:
        s, e, _, _ = item
        if spans and s < spans[-1][1]:
            prev_s, prev_e, group = spans[-1]
            group.append(item)
            spans[-1] = (prev_s, max(prev_e, e), group)
        else:
            spans.append((s, e, [item]))

    def _get_base_pcm(name: str) -> bytes:
        if name not in base_pcm_cache:
            cl, cr = cache[name]
            base_pcm_cache[name] = _pack_stereo_pcm_stdlib(cl, cr, gain=1.0)
        return base_pcm_cache[name]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    zero_block_frames = 65536
    zero_block = bytes(zero_block_frames * 4)

    def _write_zeros(wf: wave.Wave_write, frames: int) -> None:
        rem = frames
        while rem > 0:
            chunk = min(rem, zero_block_frames)
            wf.writeframes(zero_block if chunk == zero_block_frames else bytes(chunk * 4))
            rem -= chunk

    cursor = 0
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)

        for span_s, span_e, group in spans:
            if span_s > cursor:
                _write_zeros(wf, span_s - cursor)

            span_len = span_e - span_s
            if len(group) == 1:
                s_i, e_i, name, gain_db = group[0]
                needed_bytes = (e_i - s_i) * 4
                gain = db_to_lin(gain_db)
                if HAS_AUDIOOP and gain <= 1.0:
                    raw = _get_base_pcm(name)[:needed_bytes]
                    wf.writeframes(audioop.mul(raw, 2, gain) if gain != 1.0 else raw)
                else:
                    g_key = round(gain_db, 2)
                    cache_key = (name, g_key)
                    if cache_key not in pcm_cache:
                        cl, cr = cache[name]
                        pcm_cache[cache_key] = _pack_stereo_pcm_stdlib(cl, cr, gain=db_to_lin(g_key))
                    wf.writeframes(pcm_cache[cache_key][:needed_bytes])
            elif HAS_AUDIOOP and all(db_to_lin(g[3]) <= 1.0 for g in group):
                span_buf = bytearray(span_len * 4)
                for s_i, e_i, name, gain_db in group:
                    n_bytes = (e_i - s_i) * 4
                    off_b = (s_i - span_s) * 4
                    seg = _get_base_pcm(name)[:n_bytes]
                    gain = db_to_lin(gain_db)
                    if gain != 1.0:
                        seg = audioop.mul(seg, 2, gain)
                    mixed = audioop.add(bytes(span_buf[off_b : off_b + n_bytes]), seg, 2)
                    span_buf[off_b : off_b + n_bytes] = mixed
                wf.writeframes(bytes(span_buf))
            else:
                buf_l = [0.0] * span_len
                buf_r = [0.0] * span_len
                for s_i, e_i, name, gain_db in group:
                    cl, cr = cache[name]
                    gain = db_to_lin(gain_db)
                    off = s_i - span_s
                    n = e_i - s_i
                    for j in range(n):
                        buf_l[off + j] += cl[j] * gain
                        buf_r[off + j] += cr[j] * gain
                wf.writeframes(_pack_stereo_pcm_stdlib(buf_l, buf_r))

            cursor = span_e

        if cursor < final_len:
            _write_zeros(wf, final_len - cursor)

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
        gain_db = float(item.get("sfx_gain_db", item.get("gain_db", -2.5)))
        cues.append({"time_sec": max(0.0, base_t + offset), "sfx": sfx, "gain_db": gain_db})
        end_t = float(item.get("end_sec", base_t + float(item.get("duration_sec", 1.5))))
        total_dur = max(total_dur, end_t)

    return cues, total_dur


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Procedural Shorts & Long-Form cinematic SFX engine (48 kHz stereo WAV)."
    )
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
        cues, inferred_dur = load_cues(Path(args.timings))
        total_dur = max(args.duration, inferred_dur, 1.0)
        if HAS_NUMPY:
            placed = _render_timeline_numpy(cues, total_dur, out_path)
        else:
            placed = _render_timeline_stdlib(cues, total_dur, out_path)
        engine_tag = "numpy" if HAS_NUMPY else "stdlib"
        print(f"Rendered {placed} SFX cues ({total_dur:.2f}s, {engine_tag}) to {out_path}")
        return 0

    parser.error("Provide either --cue or --timings")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
