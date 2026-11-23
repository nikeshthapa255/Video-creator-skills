#!/usr/bin/env python3
"""Pure-Python procedural BGM generator for Shorts series mood/BPM rotation.

Outputs a 48 kHz 16-bit stereo WAV sized to the requested duration with smooth fades.
Supported moods:
  dramatic_news     (default 104 BPM) — warm newsroom D-minor sub-bass pulse, string/synth swells, soft rim-tick
  cinematic_tension (default  96 BPM) — dark investigative cello/drone bed, slow tension swells, heartbeat pulse
  miami_pop         (default 122 BPM) — upbeat synth-pop chords, bouncy bass, crisp hats
  retro_drive       (default 112 BPM) — synthwave arpeggio, punchy octave bass, snare pulse
  tech_pulse        (default 128 BPM) — plucky minimal electronic arp, tight sub pulse
  comedy_quirk      (default 116 BPM) — playful staccato marimba plucks, bouncy bass

Example:
  python3 make_bgm.py --mood dramatic_news --duration 40 --out audio/bgm.wav
"""

import argparse
import math
import random
import struct
import wave
from pathlib import Path

SR = 48000

MOOD_CONFIGS = {
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
}


def midi_to_hz(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


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


def _synthesize_dramatic_news(duration: float, bpm: int) -> tuple:
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
    two_pi = 2.0 * math.pi

    for step in range(n_steps):
        bar = (step // 16) % len(chords)
        sub = step % 16
        start_i = int(step * step_sec * SR)
        if start_i >= n_samples:
            break

        chord = chords[bar]
        bass_midi = bass_notes[bar]

        # 1. Deep muted sub-bass pulse (warm, round, broadcast weight on 8ths with downbeat emphasis)
        if sub in (0, 2, 4, 6, 8, 10, 12, 14):
            bf = midi_to_hz(bass_midi)
            accent = 1.0 if sub in (0, 8) else (0.72 if sub in (4, 12) else 0.48)
            b_len = min(int(step_sec * (2.6 if sub in (0, 8) else 1.7) * SR), n_samples - start_i)
            w1 = two_pi * bf / SR
            w2 = 2.0 * w1
            decay = 4.2 if sub in (0, 8) else 7.2
            att_n = max(1, int(0.012 * SR))
            for j in range(b_len):
                env = (j / att_n if j < att_n else 1.0) * math.exp(-(j / SR) * decay)
                v = (math.sin(w1 * j) + 0.22 * math.sin(w2 * j)) * env * 0.30 * accent
                left[start_i + j] += v
                right[start_i + j] += v

        # 2. Warm cinematic string / synth pad swells across each bar (wide stereo, speech-friendly low-mids)
        if sub == 0:
            p_len = min(int(step_sec * 16.0 * SR), n_samples - start_i)
            p_dur = max(0.2, p_len / SR)
            # 4-voice warm chord + octave-down warmth on root
            voices = [midi_to_hz(m) for m in chord]
            w_l = [two_pi * f * (1.0016 if idx % 2 == 0 else 0.9986) / SR for idx, f in enumerate(voices)]
            w_r = [two_pi * f * (0.9984 if idx % 2 == 0 else 1.0014) / SR for idx, f in enumerate(voices)]
            att_s = min(0.45, p_dur * 0.28)
            rel_s = min(0.50, p_dur * 0.30)
            for j in range(p_len):
                t = j / SR
                env = min(1.0, t / att_s) * min(1.0, max(0.0, (p_dur - t) / rel_s))
                swell = 0.72 + 0.28 * math.sin(math.pi * t / p_dur)
                sl = 0.0
                sr_v = 0.0
                for wl, wr in zip(w_l, w_r):
                    sl += math.sin(wl * j) + 0.16 * math.sin(2.0 * wl * j)
                    sr_v += math.sin(wr * j) + 0.16 * math.sin(2.0 * wr * j)
                g = (env * swell) * 0.072
                left[start_i + j] += sl * g
                right[start_i + j] += sr_v * g

        # 3. Subtle broadcast newsroom rhythmic pulse (soft low-mid muted chord-tone motion, zero arcade beeps)
        if sub in (0, 3, 6, 8, 11, 14):
            note_idx = (sub // 3) % 3
            pf = midi_to_hz(chord[note_idx])
            p_len = min(int(step_sec * 1.45 * SR), n_samples - start_i)
            wp = two_pi * pf / SR
            pan_l = 0.62 if (sub % 6 == 0) else 0.42
            pan_r = 1.04 - pan_l
            att_n = max(1, int(0.008 * SR))
            for j in range(p_len):
                env = (j / att_n if j < att_n else 1.0) * math.exp(-(j / SR) * 9.5)
                v = (math.sin(wp * j) + 0.12 * math.sin(2.0 * wp * j)) * env * 0.095
                left[start_i + j] += v * pan_l
                right[start_i + j] += v * pan_r

        # 4. Soft muted broadcast thump on beats 1 and 3
        if sub in (0, 8):
            k_len = min(int(0.15 * SR), n_samples - start_i)
            phase = 0.0
            gain = 0.24 if sub == 0 else 0.18
            for j in range(k_len):
                t = j / SR
                f = 42.0 + 48.0 * math.exp(-t * 28.0)
                phase += two_pi * f / SR
                env = min(1.0, t / 0.006) * math.exp(-t * 15.0)
                v = math.sin(phase) * env * gain
                left[start_i + j] += v
                right[start_i + j] += v

        # 5. Understated broadcast rim-tick (warm woody click on 2 & 4 + soft news-ticker ghost offbeats)
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
                wood = math.sin(two_pi * rim_freq * t) * 0.65 + lp * 0.45
                v = wood * math.exp(-t * (68.0 if is_main else 95.0)) * gain
                left[start_i + j] += v * pan_l
                right[start_i + j] += v * pan_r

    _apply_fades(left, right, fade_in_sec=0.35, fade_out_sec=0.70)
    return left, right


def _synthesize_cinematic_tension(duration: float, bpm: int) -> tuple:
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
    two_pi = 2.0 * math.pi

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
                bow = 0.84 + 0.16 * math.sin(two_pi * 0.38 * t)
                c_l = math.sin(w0_l * j) + 0.32 * math.sin(2.0 * w0_l * j) + 0.12 * math.sin(3.0 * w0_l * j)
                c_r = math.sin(w0_r * j) + 0.32 * math.sin(2.0 * w0_r * j) + 0.12 * math.sin(3.0 * w0_r * j)
                fifth = math.sin(w5 * j) * 0.18
                g = env * bow * 0.17
                left[start_i + j] += (c_l + fifth) * g
                right[start_i + j] += (c_r - fifth * 0.5) * g

            # 2. Slow dark tension string/pad swell (crescendos toward the second half of the bar)
            voices = [midi_to_hz(m) for m in chord]
            w_l = [two_pi * f * (1.0020 if idx % 2 == 0 else 0.9980) / SR for idx, f in enumerate(voices)]
            w_r = [two_pi * f * (0.9980 if idx % 2 == 0 else 1.0020) / SR for idx, f in enumerate(voices)]
            for j in range(d_len):
                t = j / SR
                phase_pos = t / d_dur
                swell = (math.sin(math.pi * phase_pos) ** 1.35) * (0.55 + 0.45 * phase_pos)
                sl = sum(math.sin(wl * j) + 0.14 * math.sin(2.0 * wl * j) for wl in w_l)
                sr_v = sum(math.sin(wr * j) + 0.14 * math.sin(2.0 * wr * j) for wr in w_r)
                g = swell * 0.062
                left[start_i + j] += sl * g
                right[start_i + j] += sr_v * g

        # 3. Understated cinematic heartbeat kick ("lub-dub" on sub 0 & 3, subtle pulse on 8)
        if sub in (0, 3, 8):
            k_len = min(int(0.19 * SR), n_samples - start_i)
            phase = 0.0
            gain = 0.30 if sub == 0 else (0.19 if sub == 3 else 0.15)
            for j in range(k_len):
                t = j / SR
                f = 36.0 + 34.0 * math.exp(-t * 22.0)
                phase += two_pi * f / SR
                env = min(1.0, t / 0.010) * math.exp(-t * 12.0)
                v = math.sin(phase) * env * gain
                left[start_i + j] += v
                right[start_i + j] += v

        # 4. Muted suspense pulse (low dark tick on quarter beats, alternating gentle stereo)
        if sub in (0, 4, 8, 12):
            t_len = min(int(0.065 * SR), n_samples - start_i)
            tf = midi_to_hz(chord[1 if sub in (4, 12) else 0])
            wt = two_pi * tf / SR
            pan_l = 0.45 if sub in (0, 8) else 0.65
            pan_r = 1.10 - pan_l
            for j in range(t_len):
                t = j / SR
                env = min(1.0, t / 0.005) * math.exp(-t * 32.0)
                v = math.sin(wt * j) * env * 0.075
                left[start_i + j] += v * pan_l
                right[start_i + j] += v * pan_r

    _apply_fades(left, right, fade_in_sec=0.45, fade_out_sec=0.85)
    return left, right


def synthesize_bgm(mood: str, duration: float, bpm: int) -> tuple:
    cfg = MOOD_CONFIGS[mood]
    engine = cfg.get("engine")
    if engine == "dramatic_news":
        return _synthesize_dramatic_news(duration, bpm)
    if engine == "cinematic_tension":
        return _synthesize_cinematic_tension(duration, bpm)

    n_samples = int(math.ceil(duration * SR))
    left = [0.0] * n_samples
    right = [0.0] * n_samples

    beat_sec = 60.0 / bpm
    step_sec = beat_sec / 4.0  # 16th-note grid
    rng = random.Random(101 + bpm)

    n_steps = int(math.ceil(duration / step_sec))
    chords = cfg["chords"]
    bass_notes = cfg["bass"]

    for step in range(n_steps):
        bar = (step // 16) % len(chords)
        sub = step % 16
        t0 = step * step_sec
        start_i = int(t0 * SR)
        if start_i >= n_samples:
            break

        # 1. Kick drum (4-on-the-floor or syncopated)
        if sub in (0, 4, 8, 12) or (mood == "comedy_quirk" and sub == 10):
            k_len = min(int(0.16 * SR), n_samples - start_i)
            phase = 0.0
            for j in range(k_len):
                t = j / SR
                f = 46.0 + 115.0 * math.exp(-t * 36.0)
                phase += 2.0 * math.pi * f / SR
                v = math.sin(phase) * math.exp(-t * 16.0) * cfg["kick_gain"]
                left[start_i + j] += v
                right[start_i + j] += v

        # 2. Snare / clap on beats 2 and 4
        if sub in (4, 12):
            s_len = min(int(0.11 * SR), n_samples - start_i)
            for j in range(s_len):
                t = j / SR
                noise = rng.uniform(-1.0, 1.0)
                body = math.sin(2.0 * math.pi * 190.0 * t) * 0.45
                v = (noise * 0.6 + body) * math.exp(-t * 28.0) * cfg["snare_gain"]
                left[start_i + j] += v * 0.95
                right[start_i + j] += v * 1.05

        # 3. Crisp closed hi-hat on 8th/16th offbeats
        if sub % 2 == 1 or (mood == "tech_pulse" and sub % 2 == 0):
            h_len = min(int(0.035 * SR), n_samples - start_i)
            prev = 0.0
            for j in range(h_len):
                t = j / SR
                raw = rng.uniform(-1.0, 1.0)
                hp = raw - 0.85 * prev
                prev = raw
                accent = 1.0 if sub % 4 == 2 else 0.55
                v = hp * math.exp(-t * 85.0) * cfg["hat_gain"] * accent
                left[start_i + j] += v * 1.1
                right[start_i + j] += v * 0.85

        # 4. Octave bass pulse (8th notes)
        if sub % 2 == 0:
            b_note = bass_notes[bar] + (12 if (sub % 4 == 2 and mood != "comedy_quirk") else 0)
            bf = midi_to_hz(b_note)
            b_len = min(int(step_sec * 1.85 * SR), n_samples - start_i)
            for j in range(b_len):
                t = j / SR
                env = min(1.0, t / 0.006) * math.exp(-t * 7.5)
                v = (math.sin(2.0 * math.pi * bf * t) + 0.32 * math.sin(4.0 * math.pi * bf * t)) * env * 0.28
                left[start_i + j] += v
                right[start_i + j] += v

        # 5. Melodic pluck / arpeggio
        chord = chords[bar]
        if (mood == "comedy_quirk" and sub in (0, 3, 6, 8, 11, 14)) or (mood != "comedy_quirk" and sub % 2 == 0):
            a_note = chord[(sub // 2) % len(chord)]
            af = midi_to_hz(a_note)
            a_len = min(int(step_sec * 1.9 * SR), n_samples - start_i)
            pan = 0.35 if (sub // 2) % 2 == 0 else 0.65
            for j in range(a_len):
                t = j / SR
                decay = 14.0 if mood == "comedy_quirk" else 8.5
                env = min(1.0, t / 0.004) * math.exp(-t * decay)
                v = (math.sin(2.0 * math.pi * af * t) + 0.25 * math.sin(6.0 * math.pi * af * t)) * env * cfg["arp_gain"]
                left[start_i + j] += v * (1.0 - pan + 0.2)
                right[start_i + j] += v * (pan + 0.2)

        # 6. Warm stereo pad on bar downbeat
        if sub == 0 and cfg["pad_gain"] > 0:
            p_len = min(int(step_sec * 16.0 * SR), n_samples - start_i)
            freqs = [midi_to_hz(m) for m in chord[:3]]
            for j in range(p_len):
                t = j / SR
                p_dur = p_len / SR
                env = min(1.0, t / 0.12) * min(1.0, max(0.0, (p_dur - t) / 0.18))
                sig_l = sum(math.sin(2.0 * math.pi * f * 1.001 * t) for f in freqs) / 3.0
                sig_r = sum(math.sin(2.0 * math.pi * f * 0.999 * t) for f in freqs) / 3.0
                left[start_i + j] += sig_l * env * cfg["pad_gain"]
                right[start_i + j] += sig_r * env * cfg["pad_gain"]

    # Smooth start/end fades + gentle headroom scaling
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Procedural Shorts BGM generator (48 kHz stereo WAV).")
    parser.add_argument("--mood", choices=sorted(MOOD_CONFIGS), default="miami_pop")
    parser.add_argument("--duration", type=float, required=True, help="Target duration in seconds.")
    parser.add_argument("--bpm", type=int, default=0, help="Override mood default BPM.")
    parser.add_argument("--out", required=True, help="Output WAV path.")
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be > 0")

    bpm = args.bpm if args.bpm > 0 else MOOD_CONFIGS[args.mood]["bpm"]
    left, right = synthesize_bgm(args.mood, args.duration, bpm)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        buf = bytearray()
        for l_val, r_val in zip(left, right):
            lv = int(max(-1.0, min(1.0, math.tanh(l_val * 0.85))) * 32767.0)
            rv = int(max(-1.0, min(1.0, math.tanh(r_val * 0.85))) * 32767.0)
            buf.extend(struct.pack("<hh", lv, rv))
        wf.writeframes(bytes(buf))

    print(f"Generated {args.mood} BGM ({bpm} BPM, {args.duration:.2f}s) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
