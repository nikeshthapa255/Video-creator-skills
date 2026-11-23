#!/usr/bin/env python3
"""Convert an SRT file into a YouTube Shorts safe-zone ASS subtitle file.

Prefer tts_and_captions_v2.py (exact edge-tts WordBoundary timestamps) when synthesizing VO;
use this script when converting Whisper/Gemini SRT transcripts.

Features:
  - Auto-detects bold sans font via fc-match (Arial Black -> DejaVu Sans -> Liberation Sans)
    instead of hardcoding a font missing on Ubuntu.
  - Kinetic word-by-word reveals with automatic or explicit keyword highlighting
    (--keywords, --keyword-color) and horizontal safe-width chunking (--max-chars 24).
  - Strict clamping to next_event_start - 0.01s (1 cs) + overlap assertion to eliminate
    CAPTION_OVERLAP stacking.
  - Positioned above the bottom 35% Shorts UI danger zone (MarginV=680 on 1080x1920).

Usage:
  python3 srt_to_ass.py <input.srt> <output.ass> [--font "DejaVu Sans"] [--max-chars 24] [--keywords "OpenAI,leaked"]
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
    ACTIVE_WORD_COLOR,
    DEFAULT_KEYWORD_COLOR,
    NEUTRAL_WORD_COLOR,
    assert_no_overlaps,
    build_ass_header,
    chunk_words,
    cs_to_ass_time,
    derive_subtle_tint,
    detect_bold_font,
    mark_keywords,
    normalize_ass_color,
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
    max_chars: int = 24,
) -> List[Tuple[int, int, str]]:
    all_words: List[Dict] = []

    for entry_idx, (start_cs, end_cs, text) in enumerate(entries):
        # Clamp cue end strictly to next cue start - 1cs (0.01s)
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

    kw_color = normalize_ass_color(keyword_color)
    subtle_kw_color = derive_subtle_tint(kw_color)
    annotated = (
        mark_keywords(all_words, global_keywords=keywords)
        if caption_style == "kinetic_keyword"
        else all_words
    )

    # Group back by cue_idx so chunks never bridge across separate SRT cues/pauses
    cues_grouped: Dict[int, List[Dict]] = {}
    for w in annotated:
        cues_grouped.setdefault(int(w["cue_idx"]), []).append(w)

    raw: List[Tuple[int, int, str]] = []
    for cue_idx in sorted(cues_grouped):
        chunks = chunk_words(cues_grouped[cue_idx], max_words=3, max_chars=max_chars)
        for chunk in chunks:
            for active_idx, active_word in enumerate(chunk):
                w_start = max(0, int(round(active_word["start_sec"] * 100)))
                w_end = max(w_start + 1, int(round(active_word["end_sec"] * 100)))
                styled: List[str] = []

                if caption_style == "kinetic_keyword":
                    line_prefix = r"{\t(0,70,\fscx114\fscy114)\t(70,150,\fscx100\fscy100)}"
                    for idx, w in enumerate(chunk):
                        clean = w["text"].replace("{", "").replace("}", "")
                        is_kw = bool(w.get("is_keyword", False))
                        if idx == active_idx:
                            if is_kw:
                                styled.append(
                                    rf"{{\c{kw_color}\t(0,70,\fscx120\fscy120)\t(70,150,\fscx104\fscy104)}}"
                                    + clean
                                    + rf"{{\c{NEUTRAL_WORD_COLOR}\fscx100\fscy100}}"
                                )
                            else:
                                styled.append(
                                    rf"{{\c{ACTIVE_WORD_COLOR}}}" + clean + rf"{{\c{NEUTRAL_WORD_COLOR}}}"
                                )
                        else:
                            if is_kw:
                                styled.append(
                                    rf"{{\c{subtle_kw_color}}}" + clean + rf"{{\c{NEUTRAL_WORD_COLOR}}}"
                                )
                            else:
                                styled.append(clean)
                    raw.append((w_start, w_end, line_prefix + " ".join(styled)))
                else:
                    for idx, w in enumerate(chunk):
                        clean = w["text"].replace("{", "").replace("}", "")
                        if idx == active_idx:
                            styled.append(rf"{{\c{ACTIVE_WORD_COLOR}}}" + clean + rf"{{\c{NEUTRAL_WORD_COLOR}}}")
                        else:
                            styled.append(clean)
                    prefix = r"{\fscx109\fscy109\t(0,75,\fscx100\fscy100)}" if active_idx == 0 else ""
                    raw.append((w_start, w_end, prefix + " ".join(styled)))

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert SRT into zero-overlap Shorts safe-zone ASS captions.")
    parser.add_argument("input_srt", help="Input .srt path")
    parser.add_argument("output_ass", help="Output .ass path")
    parser.add_argument("--font", default="", help="Override bold font family (auto-detected via fc-match).")
    parser.add_argument(
        "--caption-style",
        choices=("kinetic_keyword", "karaoke_chunk"),
        default="kinetic_keyword",
        help="Caption animation style (default: kinetic_keyword).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=24,
        help="Maximum visible characters per caption line for 1080px horizontal safety (default: 24).",
    )
    parser.add_argument(
        "--keywords",
        default="",
        help="Optional comma-separated keywords to highlight (overrides auto-detection).",
    )
    parser.add_argument(
        "--keyword-color",
        default=DEFAULT_KEYWORD_COLOR,
        help="ASS BGR highlight color for keywords (default: &H008CFF& electric orange-gold).",
    )
    args = parser.parse_args()

    if args.max_chars < 1:
        parser.error("--max-chars must be >= 1")

    src = Path(args.input_srt)
    dst = Path(args.output_ass)
    font_name = detect_bold_font(args.font)
    entries = parse_srt(src.read_text(encoding="utf-8"))
    events = build_word_events(
        entries,
        caption_style=args.caption_style,
        keywords=parse_keyword_list(args.keywords),
        keyword_color=args.keyword_color,
        max_chars=args.max_chars,
    )

    lines = [build_ass_header(font_name).rstrip()]
    for start_cs, end_cs, text in events:
        lines.append(f"Dialogue: 0,{cs_to_ass_time(start_cs)},{cs_to_ass_time(end_cs)},ShortsHook,,0,0,0,,{text}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(events)} zero-overlap caption events "
        f"(font='{font_name}', style={args.caption_style}, max_chars={args.max_chars}) to {dst}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
