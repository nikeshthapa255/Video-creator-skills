#!/usr/bin/env python3
"""Convert an SRT file into zero-overlap v4.0 Fun Kinetic ASS captions for 9:16 Shorts or 16:9 Long-Form videos.

Prefer tts_and_captions_v2.py (exact edge-tts WordBoundary timestamps) when synthesizing VO;
use this script when converting Whisper/Gemini SRT transcripts.

Features (v4.0 Fun Kinetic):
  - Auto-detects bold sans font via fc-match (Arial Black -> DejaVu Sans -> Liberation Sans).
  - Aspect-ratio-aware layout (--aspect-ratio 9:16|16:9, default 9:16):
      * 9:16 (PlayRes 1080x1920): 86px bold, Outline=8, Shadow=4.5, MarginV=420,
        max_chars=18, tilt=+/-2.5 deg (100% identical to v3.1/v3.2 Shorts).
      * 16:9 (PlayRes 1920x1080): 62px bold, Outline=6, Shadow=3.5, MarginV=116,
        max_chars=36, tilt=0.0 deg (horizontal subtitle framing).
  - Resets caption chunks cleanly on every SRT cue boundary.
  - Elastic entry overshoot (84% -> 122% -> 100%) + 126% -> 108% keyword pop + optional
    alternating micro-tilt on keyword punchlines.
  - Semantic keyword palettes (--keyword-color auto|palace|<BGR hex>).
  - Clamps each event to next_event_start - 0.01s (1 cs) and verifies zero overlaps.

Usage:
  python3 srt_to_ass.py <input.srt> <output.ass> [--aspect-ratio 9:16|16:9] [--font-size 86] [--max-chars 18] [--margin-v 420] [--keyword-color auto]
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_and_captions_v2 import (  # noqa: E402
    DEFAULT_FONT_SIZE,
    DEFAULT_KEYWORD_COLOR,
    DEFAULT_MARGIN_V,
    DEFAULT_MAX_CHARS,
    DEFAULT_TILT_DEG,
    build_ass_header,
    build_zero_overlap_events,
    cs_to_ass_time,
    detect_bold_font,
    get_aspect_preset,
    parse_keyword_list,
)


def srt_time_to_cs(ts: str) -> int:
    h, m, rest = ts.strip().split(":")
    s, ms = rest.replace(".", ",").split(",")
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 100) + round(int(ms) / 10)


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


def build_word_events(
    entries: List[Tuple[int, int, str]],
    caption_style: str = "kinetic_keyword",
    keywords: Optional[Sequence[str]] = None,
    keyword_color: str = DEFAULT_KEYWORD_COLOR,
    max_chars: int = DEFAULT_MAX_CHARS,
    tilt_deg: float = DEFAULT_TILT_DEG,
) -> List[Tuple[int, int, str]]:
    all_words: List[Dict] = []

    for entry_idx, (start_cs, end_cs, text) in enumerate(entries):
        # Clamp cue end to next cue start - 1cs (0.01s)
        if entry_idx + 1 < len(entries):
            next_cue_start = entries[entry_idx + 1][0]
            if next_cue_start > start_cs + 1:
                end_cs = min(end_cs, next_cue_start - 1)
            elif next_cue_start > start_cs:
                end_cs = next_cue_start

        tokens = text.split()
        if not tokens:
            continue
        total_dur = max(end_cs - start_cs, len(tokens))
        weights = [max(2, len(tok)) for tok in tokens]
        total_w = sum(weights)
        acc = 0

        for idx, tok in enumerate(tokens):
            w_start = start_cs + (acc * total_dur) // total_w
            acc += weights[idx]
            w_end = end_cs if idx == len(tokens) - 1 else start_cs + (acc * total_dur) // total_w
            if w_end <= w_start:
                w_end = w_start + 1
            all_words.append({
                "text": tok,
                "start_sec": w_start / 100.0,
                "end_sec": w_end / 100.0,
                "cue_idx": entry_idx,
            })

    # Delegate directly to v4.0 engine so SRT and edge-tts captions share identical physics,
    # semantic colors, micro-tilt, cue_idx boundary flush, and zero-overlap clamping.
    return build_zero_overlap_events(
        all_words,
        caption_style=caption_style,
        keywords=keywords,
        keyword_color=keyword_color,
        max_chars=max_chars,
        tilt_deg=tilt_deg,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert an SRT file into zero-overlap v4.0 Fun Kinetic ASS captions (9:16 or 16:9)."
    )
    parser.add_argument("input_srt", help="Input .srt path")
    parser.add_argument("output_ass", help="Output .ass path")
    parser.add_argument(
        "--aspect-ratio",
        choices=("9:16", "16:9"),
        default="9:16",
        help="Target aspect ratio canvas & defaults (default: 9:16).",
    )
    parser.add_argument("--font", default="", help="Override bold font family (auto-detected via fc-match).")
    parser.add_argument(
        "--font-size",
        type=int,
        default=None,
        help=f"Caption font size in px (default: {DEFAULT_FONT_SIZE} for 9:16, 62 for 16:9).",
    )
    parser.add_argument(
        "--margin-v",
        type=int,
        default=None,
        help=f"ASS MarginV vertical offset from bottom in px (default: {DEFAULT_MARGIN_V} for 9:16, 116 for 16:9).",
    )
    parser.add_argument(
        "--tilt",
        type=float,
        default=None,
        help=f"Alternating tilt in degrees on keyword lines; 0 disables (default: {DEFAULT_TILT_DEG} for 9:16, 0.0 for 16:9).",
    )
    parser.add_argument(
        "--caption-style",
        choices=("kinetic_keyword", "karaoke_chunk", "clean_subtitle"),
        default="kinetic_keyword",
        help="Caption animation style (default: kinetic_keyword).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help=f"Maximum visible characters per caption line (default: {DEFAULT_MAX_CHARS} for 9:16, 36 for 16:9).",
    )
    parser.add_argument(
        "--keywords",
        default="",
        help="Optional comma-separated keywords to highlight (overrides auto-detection).",
    )
    parser.add_argument(
        "--keyword-color",
        default=DEFAULT_KEYWORD_COLOR,
        help="Keyword color: 'auto' (v3.1 neon), 'palace' (gold/crimson/jade), or ASS BGR hex (default: auto).",
    )
    args = parser.parse_args()

    preset = get_aspect_preset(args.aspect_ratio)
    font_size = args.font_size if args.font_size is not None else int(preset["font_size"])
    margin_v = args.margin_v if args.margin_v is not None else int(preset["margin_v"])
    max_chars = args.max_chars if args.max_chars is not None else int(preset["max_chars"])
    if args.caption_style == "clean_subtitle":
        tilt_deg = 0.0
    else:
        tilt_deg = args.tilt if args.tilt is not None else float(preset["tilt"])

    if max_chars < 1:
        parser.error("--max-chars must be >= 1")
    if font_size < 12:
        parser.error("--font-size must be >= 12")
    if margin_v < 0:
        parser.error("--margin-v must be >= 0")
    if tilt_deg < 0:
        parser.error("--tilt must be >= 0")

    src = Path(args.input_srt)
    dst = Path(args.output_ass)
    font_name = detect_bold_font(args.font)
    entries = parse_srt(src.read_text(encoding="utf-8"))
    events = build_word_events(
        entries,
        caption_style=args.caption_style,
        keywords=parse_keyword_list(args.keywords),
        keyword_color=args.keyword_color,
        max_chars=max_chars,
        tilt_deg=tilt_deg,
    )

    lines = [
        build_ass_header(
            font_name,
            font_size=font_size,
            margin_v=margin_v,
            aspect_ratio=args.aspect_ratio,
        ).rstrip()
    ]
    for start_cs, end_cs, text in events:
        lines.append(f"Dialogue: 0,{cs_to_ass_time(start_cs)},{cs_to_ass_time(end_cs)},ShortsHook,,0,0,0,,{text}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(events)} zero-overlap caption events "
        f"({args.aspect_ratio}, font='{font_name}' {font_size}px, margin_v={margin_v}, style={args.caption_style}, "
        f"max_chars={max_chars}, kw_color={args.keyword_color}, tilt=+/-{tilt_deg:g}deg) to {dst}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
