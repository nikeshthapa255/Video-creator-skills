#!/usr/bin/env python3
"""Audio-first neural TTS synthesizer, narration linter, and zero-overlap ASS caption builder.

Features:
  1. Pre-TTS Narration Linter (--lint-plan): catches TTS_MISREAD triggers such as
     "$700 PS5 Pro" (-> "700-dollar PS5 Pro") and "no 60 FPS" (-> "no sixty FPS").
  2. Audio-First Synthesis (edge-tts with boundary="WordBoundary"): generates per-shot
     VO clips, concatenates voiceover.wav (48 kHz stereo), writes exact shot_timings.json.
  3. Zero-Overlap Safe-Zone Captions: auto-detects bold sans font via fc-match, adds
     phrase-entry bounce pop, clamps every event strictly to next_chunk_start - 0.01s,
     and asserts zero overlaps before writing captions.ass.

Examples:
  python3 tts_and_captions_v2.py --lint-plan shot_plan.json
  python3 tts_and_captions_v2.py --shot-plan shot_plan.json --out-dir audio --voice en-US-AvaNeural
"""

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def int_to_words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return TENS[t] if o == 0 else f"{TENS[t]}-{ONES[o]}"
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{ONES[h]} hundred" if r == 0 else f"{ONES[h]} hundred {int_to_words(r)}"
    return str(n)


NON_NOUN_FOLLOWERS = {
    "a", "an", "and", "are", "as", "at", "because", "before", "but", "by",
    "each", "every", "for", "from", "if", "in", "into", "is", "just", "less",
    "more", "of", "on", "or", "over", "per", "so", "than", "that", "the",
    "then", "to", "under", "was", "were", "when", "while", "with", "without",
}


def lint_narration_text(text: str) -> List[Dict[str, str]]:
    """Detect common neural TTS mispronunciations before synthesis."""
    issues: List[Dict[str, str]] = []

    # 1. "$700 PS5 Pro" -> often misread as "seven hundred dollars PS5 Pro"
    for m in re.finditer(r"\$(\d+(?:,\d{3})*)\s+([A-Za-z][A-Za-z0-9-]*)", text):
        num = m.group(1).replace(",", "")
        noun = m.group(2)
        if noun.lower() in NON_NOUN_FOLLOWERS:
            continue
        issues.append({
            "code": "TTS_MISREAD",
            "match": m.group(0),
            "suggestion": f"{num}-dollar {noun}",
            "reason": "Currency before a noun can read as '<amount> dollars <noun>' instead of '<amount>-dollar <noun>'.",
        })

    # 2. "no 60 FPS" -> neural TTS often reads "no <digits>" as "number <digits>"
    for m in re.finditer(r"\b(no)\s+(\d{1,3})\b", text, flags=re.IGNORECASE):
        num = int(m.group(2))
        word_num = int_to_words(num)
        issues.append({
            "code": "TTS_MISREAD",
            "match": m.group(0),
            "suggestion": f"{m.group(1)} {word_num}",
            "reason": "'no <digits>' is frequently misread by neural TTS as 'number <digits>'. Spell out the number or use 'zero'.",
        })

    # 3. Shorthand symbols
    for pat, repl, desc in [
        (r"\bw/", "with ", "Shorthand 'w/' can be misread."),
        (r"\bvs\.(?=\s)", "versus", "Abbreviation 'vs.' can pause awkwardly."),
    ]:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            issues.append({
                "code": "TTS_MISREAD",
                "match": m.group(0),
                "suggestion": repl.strip(),
                "reason": desc,
            })

    return issues


def autofix_narration_text(text: str) -> str:
    def _dollar_repl(m: re.Match) -> str:
        if m.group(2).lower() in NON_NOUN_FOLLOWERS:
            return m.group(0)
        return f"{m.group(1).replace(',', '')}-dollar {m.group(2)}"

    fixed = re.sub(r"\$(\d+(?:,\d{3})*)\s+([A-Za-z][A-Za-z0-9-]*)", _dollar_repl, text)
    fixed = re.sub(
        r"\b(no)\s+(\d{1,3})\b",
        lambda m: f"{m.group(1)} {int_to_words(int(m.group(2)))}",
        fixed,
        flags=re.IGNORECASE,
    )
    fixed = re.sub(r"\bw/", "with ", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"\bvs\.(?=\s)", "versus", fixed, flags=re.IGNORECASE)
    return fixed


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
        # Fallback to whatever Bold sans fc-match resolves
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


def cs_to_ass_time(cs: int) -> str:
    cs = max(0, cs)
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    c = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


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


def chunk_words(words: List[Dict], max_words: int = 3) -> List[List[Dict]]:
    chunks: List[List[Dict]] = []
    cur: List[Dict] = []
    for w in words:
        cur.append(w)
        ends_sentence = bool(re.search(r"[.!?]$", w["text"].strip()))
        if len(cur) >= max_words or ends_sentence:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_zero_overlap_events(words: List[Dict]) -> List[Tuple[int, int, str]]:
    """Build per-word highlighted ASS events with phrase-entry bounce pop and zero overlaps."""
    chunks = chunk_words(words, max_words=3)
    raw: List[Tuple[int, int, str]] = []

    for chunk in chunks:
        for active_idx, active_word in enumerate(chunk):
            w_start = max(0, int(round(active_word["start_sec"] * 100)))
            w_end = max(w_start + 5, int(round(active_word["end_sec"] * 100)))
            styled = []
            for idx, w in enumerate(chunk):
                clean = w["text"].replace("{", "").replace("}", "")
                if idx == active_idx:
                    styled.append(r"{\c&H00FFFF&}" + clean + r"{\c&H00FFFFFF&}")
                else:
                    styled.append(clean)
            prefix = r"{\fscx109\fscy109\t(0,75,\fscx100\fscy100)}" if active_idx == 0 else ""
            raw.append((w_start, w_end, prefix + " ".join(styled)))

    # Strict monotonic clamp: every event ends <= next_start - 1cs (0.01s)
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

    # Final clamp pass in case a tight event bumped forward
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


def probe_audio_duration(path: Path) -> float:
    res = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(res.stdout.strip())


async def synth_shot_edge_tts(text: str, voice: str, rate: str, pitch: str, mp3_path: Path) -> List[Dict]:
    try:
        import edge_tts  # type: ignore
    except ImportError as exc:
        raise RuntimeError("edge-tts is required for synthesis. Install via: pip install edge-tts") from exc

    comm = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    words: List[Dict] = []
    with mp3_path.open("wb") as f:
        async for chunk in comm.stream():
            ctype = chunk.get("type")
            if ctype == "audio":
                f.write(chunk["data"])
            elif ctype == "WordBoundary":
                start_s = chunk["offset"] / 10_000_000.0
                dur_s = chunk["duration"] / 10_000_000.0
                words.append({
                    "text": chunk["text"],
                    "start_sec": round(start_s, 4),
                    "end_sec": round(start_s + dur_s, 4),
                })
    return words


def lint_plan(plan: Dict) -> int:
    found = 0
    for shot in plan.get("shots", []):
        sid = shot.get("shot_id", "unknown")
        text = shot.get("dialogue_or_vo", "")
        issues = lint_narration_text(text)
        for iss in issues:
            found += 1
            print(
                f"[TTS_MISREAD] {sid}: '{iss['match']}' -> '{iss['suggestion']}' ({iss['reason']})",
                file=sys.stderr,
            )
    if found == 0:
        print("TTS lint passed: 0 misread risks found.")
        return 0
    print(f"TTS lint failed: {found} issue(s) found. Fix shot_plan.json or pass --autofix.", file=sys.stderr)
    return 2


def run_synthesis(args: argparse.Namespace, plan: Dict) -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("Error: ffmpeg and ffprobe must be installed and on PATH.", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = args.voice or plan.get("voice") or "en-US-AvaNeural"

    if not args.autofix:
        rc = lint_plan(plan)
        if rc != 0:
            return rc

    all_words: List[Dict] = []
    shot_timings: List[Dict] = []
    cursor = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        concat_lines: List[str] = []

        # Reusable silence clip for inter-shot gaps
        gap_wav = tmp_dir / "gap.wav"
        if args.gap > 0:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-t", f"{args.gap:.3f}", "-c:a", "pcm_s16le", str(gap_wav),
                ],
                check=True,
            )

        for idx, shot in enumerate(plan.get("shots", [])):
            sid = shot["shot_id"]
            text = shot.get("dialogue_or_vo", "").strip()
            if args.autofix:
                text = autofix_narration_text(text)

            mp3_path = tmp_dir / f"{sid}.mp3"
            wav_path = tmp_dir / f"{sid}.wav"

            if not text:
                silent_dur = max(0.5, float(shot.get("duration_sec", 2.0)))
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-t", f"{silent_dur:.3f}", "-c:a", "pcm_s16le", str(wav_path),
                    ],
                    check=True,
                )
                words = []
                dur = silent_dur
            else:
                words = asyncio.run(synth_shot_edge_tts(text, voice, args.rate, args.pitch, mp3_path))
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-i", str(mp3_path),
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                        str(wav_path),
                    ],
                    check=True,
                )
                dur = probe_audio_duration(wav_path)
                if not words:
                    # Fallback if edge-tts emits no WordBoundary events for a very short interjection
                    tokens = text.split()
                    step = dur / max(1, len(tokens))
                    words = [
                        {"text": tok, "start_sec": round(i * step, 4), "end_sec": round((i + 1) * step, 4)}
                        for i, tok in enumerate(tokens)
                    ]
            shot_start = cursor
            shot_end = cursor + dur

            shifted_words = []
            for w in words:
                sw = {
                    "text": w["text"],
                    "start_sec": round(shot_start + w["start_sec"], 4),
                    "end_sec": round(shot_start + w["end_sec"], 4),
                }
                shifted_words.append(sw)
                all_words.append(sw)

            shot_timings.append({
                "shot_id": sid,
                "start_sec": round(shot_start, 4),
                "end_sec": round(shot_end, 4),
                "duration_sec": round(dur + (args.gap if idx < len(plan["shots"]) - 1 else 0.0), 4),
                "spoken_duration_sec": round(dur, 4),
                "motion_preset": shot.get("motion_preset", "zoom_in"),
                "flash_in": bool(shot.get("flash_in", False)),
                "sfx_type": shot.get("sfx_type", "none"),
                "sfx_offset_sec": float(shot.get("sfx_offset_sec", 0.0)),
                "sfx_gain_db": float(shot.get("sfx_gain_db", -2.5)),
                "dialogue_or_vo": text,
                "words": shifted_words,
            })

            concat_lines.append(f"file '{wav_path}'")
            cursor = shot_end
            if args.gap > 0 and idx < len(plan["shots"]) - 1:
                concat_lines.append(f"file '{gap_wav}'")
                cursor += args.gap

        concat_txt = tmp_dir / "concat.txt"
        concat_txt.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        vo_wav = out_dir / "voiceover.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(vo_wav)],
            check=True,
        )

    total_dur = round(cursor, 4)
    timings_payload = {
        "slug": plan.get("slug", ""),
        "voice": voice,
        "total_duration_sec": total_dur,
        "shot_count": len(shot_timings),
        "avg_shot_length_sec": round(total_dur / max(1, len(shot_timings)), 3),
        "shots": shot_timings,
    }
    timings_path = out_dir / "shot_timings.json"
    timings_path.write_text(json.dumps(timings_payload, indent=2) + "\n", encoding="utf-8")

    font_name = detect_bold_font(args.font)
    events = build_zero_overlap_events(all_words)
    ass_lines = [build_ass_header(font_name).rstrip()]
    for s, e, text in events:
        ass_lines.append(f"Dialogue: 0,{cs_to_ass_time(s)},{cs_to_ass_time(e)},ShortsHook,,0,0,0,,{text}")
    ass_path = out_dir / "captions.ass"
    ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")

    print(f"Synthesized {len(shot_timings)} shots ({total_dur:.2f}s, font='{font_name}', {len(events)} caption events)")
    print(f"  VO:      {out_dir / 'voiceover.wav'}")
    print(f"  Timings: {timings_path}")
    print(f"  ASS:     {ass_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audio-first TTS synthesizer, linter, and ASS caption builder.")
    parser.add_argument("--lint-plan", help="Run Pre-TTS narration linter on shot_plan.json and exit.")
    parser.add_argument("--shot-plan", help="Path to shot_plan.json for full audio-first synthesis.")
    parser.add_argument("--out-dir", help="Output directory for voiceover.wav, shot_timings.json, captions.ass.")
    parser.add_argument("--voice", default="", help="Override neural voice (default: plan.voice or en-US-AvaNeural).")
    parser.add_argument("--rate", default="+6%", help="Speech rate adjustment (default: +6% for Shorts energy).")
    parser.add_argument("--pitch", default="+0Hz", help="Pitch adjustment.")
    parser.add_argument("--gap", type=float, default=0.06, help="Inter-shot silence gap in seconds (default: 0.06).")
    parser.add_argument("--font", default="", help="Override caption font family (auto-detected via fc-match).")
    parser.add_argument("--autofix", action="store_true", help="Auto-rewrite TTS_MISREAD patterns during synthesis.")
    args = parser.parse_args()

    if args.lint_plan:
        plan = json.loads(Path(args.lint_plan).read_text(encoding="utf-8"))
        return lint_plan(plan)

    if args.shot_plan:
        if not args.out_dir:
            parser.error("--out-dir is required with --shot-plan")
        plan = json.loads(Path(args.shot_plan).read_text(encoding="utf-8"))
        return run_synthesis(args, plan)

    parser.error("Provide --lint-plan or --shot-plan")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
