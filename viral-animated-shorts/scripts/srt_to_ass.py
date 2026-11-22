#!/usr/bin/env python3
"""Convert an SRT file into a YouTube Shorts safe-zone ASS subtitle file.

Prefer tts_and_captions_v2.py (exact edge-tts WordBoundary timestamps) when synthesizing VO;
use this script when converting Whisper/Gemini SRT transcripts.

Features:
  - Auto-detects bold sans font via fc-match (Arial Black -> DejaVu Sans -> Liberation Sans)
    instead of hardcoding a font missing on Ubuntu.
  - Phrase-entry bounce pop: {\\fscx109\\fscy109\\t(0,75,\\fscx100\\fscy100)}.
  - Strict clamping to next_event_start - 0.01s (1 cs) + overlap assertion to eliminate
    CAPTION_OVERLAP stacking.
  - Positioned above the bottom 35% Shorts UI danger zone (MarginV=680 on 1080x1920).

Usage:
  python3 srt_to_ass.py <input.srt> <output.ass> [--font "DejaVu Sans"]
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


def detect_bold_font(preferred: str = "") -> str:
    if preferred:
        return preferred
    candidates = ["Arial Black", "DejaVu Sans", "Liberation Sans", "Helvetica Neue"]
    if shutil.which("fc-match"):
        for cand in candidates:
            try:
                res = subprocess.run(
                    ["fc-match", "-f", "%{family}", f"{cand}:style=Bold"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                matched = res.stdout.strip().split(",")[0].strip()
                if matched and matched.lower() == cand.lower():
                    return matched
            except OSError:
                break
        try:
            res = subprocess.run(
                ["fc-match", "-f", "%{family}", "sans-serif:style=Bold"],
                capture_output=True,
                text=True,
                check=False,
            )
            matched = res.stdout.strip().split(",")[0].strip()
            if matched:
                return matched
        except OSError:
            pass
    return "DejaVu Sans"


def build_ass_header(font_name: str) -> str:
    return f"""[Script Info]
Title: Viral Animated Short Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShortsHook,{font_name},74,&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,680,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def srt_time_to_cs(ts: str) -> int:
    h, m, rest = ts.strip().split(":")
    s, ms = rest.replace(".", ",").split(",")
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 100) + round(int(ms) / 10)


def cs_to_ass_time(cs: int) -> str:
    cs = max(0, cs)
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    c = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def parse_srt(content: str) -> List[Tuple[int, int, str]]:
    blocks = re.split(r"\r?\n\r?\n+", content.strip())
    entries: List[Tuple[int, int, str]] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        time_idx = 1 if "-->" in lines[1] else 0
        if "-->" not in lines[time_idx]:
            continue
        start_s, end_s = [p.strip() for p in lines[time_idx].split("-->")]
        text = " ".join(lines[time_idx + 1 :])
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            entries.append((srt_time_to_cs(start_s), srt_time_to_cs(end_s), text))
    entries.sort(key=lambda x: x[0])
    return entries


def build_word_events(entries: List[Tuple[int, int, str]]) -> List[Tuple[int, int, str]]:
    raw: List[Tuple[int, int, str]] = []
    for entry_idx, (start_cs, end_cs, text) in enumerate(entries):
        # Clamp cue end strictly to next cue start - 1cs (0.01s)
        if entry_idx + 1 < len(entries):
            next_cue_start = entries[entry_idx + 1][0]
            if next_cue_start > start_cs + 1:
                end_cs = min(end_cs, next_cue_start - 1)
            elif next_cue_start > start_cs:
                end_cs = next_cue_start

        words = text.split()
        if not words:
            continue
        chunks = [words[i : i + 3] for i in range(0, len(words), 3)]
        n = len(words)
        total_dur = max(end_cs - start_cs, n)

        word_idx = 0
        for chunk in chunks:
            for active_idx in range(len(chunk)):
                w_start = start_cs + (word_idx * total_dur) // n
                w_end = end_cs if word_idx == n - 1 else start_cs + ((word_idx + 1) * total_dur) // n
                if w_end <= w_start:
                    w_end = w_start + 1
                styled = []
                for idx, word in enumerate(chunk):
                    clean = word.replace("{", "").replace("}", "")
                    if idx == active_idx:
                        styled.append(r"{\c&H00FFFF&}" + clean + r"{\c&H00FFFFFF&}")
                    else:
                        styled.append(clean)
                prefix = r"{\fscx109\fscy109\t(0,75,\fscx100\fscy100)}" if active_idx == 0 else ""
                raw.append((w_start, w_end, prefix + " ".join(styled)))
                word_idx += 1

    # Global monotonic pass: guarantee zero overlap across every adjacent Dialogue line
    clamped: List[Tuple[int, int, str]] = []
    for i, (s, e, text) in enumerate(raw):
        if clamped and s < clamped[-1][1]:
            s = clamped[-1][1]
        if i + 1 < len(raw):
            next_s = raw[i + 1][0]
            if next_s > s + 1:
                e = min(e, next_s - 1)
            elif next_s > s:
                e = next_s
        if e <= s:
            e = s + 1
        clamped.append((s, e, text))

    for i in range(len(clamped) - 1):
        s, e, text = clamped[i]
        ns = clamped[i + 1][0]
        if e > ns:
            clamped[i] = (s, ns, text)

    assert_no_overlaps(clamped)
    return clamped


def assert_no_overlaps(events: List[Tuple[int, int, str]]) -> None:
    for i in range(1, len(events)):
        prev_s, prev_e, _ = events[i - 1]
        cur_s, cur_e, _ = events[i]
        if cur_s < prev_e:
            raise AssertionError(
                f"CAPTION_OVERLAP at event {i}: prev [{prev_s}, {prev_e}] overlaps cur [{cur_s}, {cur_e}]"
            )
        if cur_e < cur_s:
            raise AssertionError(f"CAPTION_ORDER at event {i}: end {cur_e} < start {cur_s}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert SRT into zero-overlap Shorts safe-zone ASS captions.")
    parser.add_argument("input_srt", help="Input .srt path")
    parser.add_argument("output_ass", help="Output .ass path")
    parser.add_argument("--font", default="", help="Override bold font family (auto-detected via fc-match).")
    args = parser.parse_args()

    src = Path(args.input_srt)
    dst = Path(args.output_ass)
    font_name = detect_bold_font(args.font)
    entries = parse_srt(src.read_text(encoding="utf-8"))
    events = build_word_events(entries)

    lines = [build_ass_header(font_name).rstrip()]
    for start_cs, end_cs, text in events:
        lines.append(f"Dialogue: 0,{cs_to_ass_time(start_cs)},{cs_to_ass_time(end_cs)},ShortsHook,,0,0,0,,{text}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(events)} zero-overlap caption events (font='{font_name}') to {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
