#!/usr/bin/env python3
"""Convert an SRT file into a YouTube Shorts safe-zone ASS subtitle file.

Places bold, high-contrast captions above the bottom 35% Shorts UI danger zone
(MarginV=680 on a 1080x1920 PlayRes canvas) with per-word highlight timing.
"""

import re
import sys
from pathlib import Path

ASS_HEADER = """[Script Info]
Title: Viral Animated Short Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShortsHook,Arial Black,76,&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,680,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def srt_time_to_cs(ts: str) -> int:
    h, m, rest = ts.strip().split(":")
    s, ms = rest.replace(".", ",").split(",")
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 100) + round(int(ms) / 10)


def cs_to_ass_time(cs: int) -> str:
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    c = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def parse_srt(content: str):
    blocks = re.split(r"\r?\n\r?\n+", content.strip())
    entries = []
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
    return entries


def build_word_events(start_cs: int, end_cs: int, text: str):
    words = text.split()
    if not words:
        return []
    # Chunk into 3-word phrases for punchy Shorts readability
    chunks = [words[i : i + 3] for i in range(0, len(words), 3)]
    total_dur = max(end_cs - start_cs, len(words))
    n = len(words)

    events = []
    word_idx = 0
    for chunk in chunks:
        for active_idx in range(len(chunk)):
            w_start = start_cs + (word_idx * total_dur) // n
            w_end = end_cs if word_idx == n - 1 else start_cs + ((word_idx + 1) * total_dur) // n
            if w_end <= w_start:
                w_end = min(w_start + 1, end_cs) if end_cs > w_start else w_start + 1
            styled = []
            for idx, word in enumerate(chunk):
                clean = word.replace("{", "").replace("}", "")
                if idx == active_idx:
                    styled.append(r"{\c&H00FFFF&}" + clean + r"{\c&H00FFFFFF&}")
                else:
                    styled.append(clean)
            events.append(
                f"Dialogue: 0,{cs_to_ass_time(w_start)},{cs_to_ass_time(w_end)},ShortsHook,,0,0,0,,{' '.join(styled)}"
            )
            word_idx += 1
    return events


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: srt_to_ass.py <input.srt> <output.ass>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    entries = parse_srt(src.read_text(encoding="utf-8"))
    lines = [ASS_HEADER.rstrip()]
    for start_cs, end_cs, text in entries:
        lines.extend(build_word_events(start_cs, end_cs, text))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines) - 1} caption events to {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
