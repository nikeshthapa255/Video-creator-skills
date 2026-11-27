#!/usr/bin/env python3
"""Synthesize single- or multi-voice voiceover with edge-tts, lint scripts, and build v4.0 ASS/SRT captions.

Supports both Shorts (9:16, 30-55s) and Long-Form Video Mode (9:16 vertical micro-dramas
and 16:9 horizontal episodes, 3-15+ min) with 100% backward compatibility for v3.2 Shorts CLI calls.

Features:
  1. Pre-TTS Narration Linter (--lint-plan): catches TTS_MISREAD errors such as
     "$700 PS5 Pro" (-> "700-dollar PS5 Pro") and "no 60 FPS" (-> "no sixty FPS"),
     plus format-aware AI_SLOP_SCRIPT warnings (--strict-slop escalates to exit 2;
     --no-slop-check disables slop checks; long-form/cliffhanger/outro plans skip
     Shorts replay-loop bridge warnings).
  2. Phonetic-vs-Display Separation: supports top-level tts_pronunciations and
     per-shot tts_text so TTS speaks phonetic forms ("A W S", "GTA Six", "Lin Wahn-yeen")
     while captions render clean display English ("AWS", "GTA 6", "Lin Wanyin").
  3. Multi-Voice Character Routing & Concurrent Synthesis:
     - Honors per-shot voice/rate/pitch, top-level voice_cast (with optional per-speaker
       keyword_color), CLI --voice/--rate/--pitch, and generic role defaults
       (narrator, host/protagonist, rival, cohost/ally, guard/supporting).
     - Bounded async concurrency (--concurrency, default 4), exponential retry backoff
       (--max-retries, default 4), atomic .tmp.mp3 -> .mp3 writes, and zero-byte /
       zero-WordBoundary validation.
     - Per-shot post_gap_sec with cached silence WAVs and Act/chapter summaries in
       shot_timings.json.
  4. Aspect-Ratio-Aware Fun Kinetic ASS Captions & Clean SRT Export (--export-srt):
     - 9:16 (PlayRes 1080x1920): 86px bold, Outline=8, Shadow=4.5, MarginV=420,
       max_chars=18, tilt=+/-2.5 deg (identical to v3.1/v3.2 Shorts).
     - 16:9 (PlayRes 1920x1080): 62px bold, Outline=6, Shadow=3.5, MarginV=116,
       max_chars=36, tilt=0.0 deg, plus automatic clean captions.srt export.
     - Semantic keyword palettes: --keyword-color auto (v3.1 neon), --keyword-color palace
       (Imperial Gold / Fiery Crimson-Coral / Pearl Cyan-Jade), custom ASS BGR hex, and
       per-speaker voice_cast.<speaker>.keyword_color overrides.
     - Zero-overlap clamping (next_start - 0.01s) with overlap assertion.

Examples:
  python3 tts_and_captions_v2.py --lint-plan shot_plan.json
  python3 tts_and_captions_v2.py --lint-plan shot_plan.json --strict-slop
  python3 tts_and_captions_v2.py --lint-metadata packaging.md --strict-slop
  python3 tts_and_captions_v2.py --shot-plan shot_plan.json --out-dir audio --voice en-US-AvaNeural
  python3 tts_and_captions_v2.py --shot-plan shot_plan.json --out-dir audio --aspect-ratio 16:9 --export-srt
  python3 tts_and_captions_v2.py --shot-plan shot_plan.json --out-dir audio --keyword-color palace --concurrency 4
"""

import argparse
import asyncio
import difflib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

# v3.1 / v3.2 Fun Kinetic 9:16 defaults (exported for srt_to_ass.py compatibility)
DEFAULT_FONT_SIZE = 86
DEFAULT_MAX_CHARS = 18
DEFAULT_MARGIN_V = 420
DEFAULT_TILT_DEG = 2.5
DEFAULT_KEYWORD_COLOR = "auto"
DEFAULT_SHORT_RATE = "+10%"
DEFAULT_LONG_RATE = "+0%"
DEFAULT_PITCH = "+0Hz"
DEFAULT_GAP_SEC = 0.06
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_RETRIES = 4

ASPECT_RATIO_PRESETS: Dict[str, Dict[str, Any]] = {
    "9:16": {
        "play_res_x": 1080,
        "play_res_y": 1920,
        "font_size": DEFAULT_FONT_SIZE,
        "max_chars": DEFAULT_MAX_CHARS,
        "margin_v": DEFAULT_MARGIN_V,
        "tilt": DEFAULT_TILT_DEG,
        "outline": 8,
        "shadow": 4.5,
        "margin_lr": 50,
    },
    "16:9": {
        "play_res_x": 1920,
        "play_res_y": 1080,
        "font_size": 62,
        "max_chars": 36,
        "margin_v": 116,
        "tilt": 0.0,
        "outline": 6,
        "shadow": 3.5,
        "margin_lr": 80,
    },
}

# Generic built-in multi-voice defaults (used ONLY when a shot names a speaker without
# a plan.voice_cast entry and without CLI --voice/--rate/--pitch overrides)
GENERIC_VOICE_CAST: Dict[str, Dict[str, str]] = {
    "narrator": {
        "voice": "en-GB-SoniaNeural",
        "rate": "+2%",
        "pitch": "-1Hz",
    },
    "host": {
        "voice": "en-US-AvaNeural",
        "rate": "+4%",
        "pitch": "+0Hz",
    },
    "protagonist": {
        "voice": "en-US-AvaNeural",
        "rate": "+0%",
        "pitch": "-2Hz",
    },
    "rival": {
        "voice": "en-US-EmmaNeural",
        "rate": "+4%",
        "pitch": "+3Hz",
    },
    "cohost": {
        "voice": "en-GB-RyanNeural",
        "rate": "+2%",
        "pitch": "-2Hz",
    },
    "ally": {
        "voice": "en-GB-RyanNeural",
        "rate": "-2%",
        "pitch": "-4Hz",
    },
    "guard": {
        "voice": "en-GB-ThomasNeural",
        "rate": "+2%",
        "pitch": "-3Hz",
    },
    "supporting": {
        "voice": "en-GB-ThomasNeural",
        "rate": "+2%",
        "pitch": "-2Hz",
    },
}

# ASS BGR hex palette (v3.1 Semantic Neon + Palace Drama)
NEON_LIME_COLOR = "&H33FF66&"      # Money / numbers / stats
CORAL_ORANGE_COLOR = "&H365AFF&"   # Drama / danger / action / Fiery Crimson-Coral
ELECTRIC_CYAN_COLOR = "&HFFE500&"  # Brands / tech / AI
IMPERIAL_GOLD_COLOR = "&H00D7FF&"  # Imperial / authority terms (BGR for #FFD700)
CRIMSON_CORAL_COLOR = "&H365AFF&"  # Danger / betrayal / rival
PEARL_JADE_COLOR = "&HFFE5A0&"     # Pearl / jade / veil / protagonist (BGR for #A0E5FF)
ACTIVE_WORD_COLOR = "&H00FFFF&"    # Bright yellow active spoken word
NEUTRAL_WORD_COLOR = "&H00FFFFFF&" # Crisp white neutral word

SEMANTIC_CATEGORY_COLORS = {
    "money_number": NEON_LIME_COLOR,
    "drama_action": CORAL_ORANGE_COLOR,
    "brand_tech": ELECTRIC_CYAN_COLOR,
    "palace_imperial": IMPERIAL_GOLD_COLOR,
    "palace_danger": CRIMSON_CORAL_COLOR,
    "palace_jade": PEARL_JADE_COLOR,
}

NUMBER_WORDS = {
    "zero", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety",
    "hundred", "hundreds", "thousand", "thousands", "million", "millions",
    "billion", "billions", "trillion", "trillions", "percent", "percentage",
    "dollar", "dollars", "bucks", "cash", "price", "cost", "costs", "paid",
    "worth", "revenue", "profit", "profits", "budget", "debt", "double", "triple",
    "half", "quarter", "dozen", "free", "cheap", "expensive", "buys", "bought",
}

PALACE_IMPERIAL_WORDS = {
    "phoenix", "crown", "empress", "imperial", "emperor", "throne", "palace",
    "majesty", "regent", "edict", "decree", "seal", "gold", "golden", "sovereign",
    "dynasty", "court", "prince", "royal", "command", "verdict", "highness",
}

PALACE_DANGER_WORDS = {
    "poison", "poisoned", "ashes", "ash", "treason", "guilty", "forged", "forge",
    "fire", "burn", "burned", "burning", "flames", "arrest", "arrested", "silence",
    "silenced", "blood", "bloody", "betrayed", "betray", "betrayal", "murder",
    "murdered", "seize", "seized", "frame", "framed", "execution", "execute",
    "dead", "death", "kill", "killed", "killer", "knife", "blade", "trap", "trapped",
    "venom", "curse", "cursed", "confess", "confession", "crime", "punish",
    "witness", "scream", "screamed", "lies", "lied", "lying", "liar", "war",
}

PALACE_JADE_WORDS = {
    "veil", "veiled", "jade", "cipher", "ciphers", "pearl", "silk", "ink",
    "scroll", "scrolls", "letter", "letters", "evidence", "truth", "clue", "clues",
    "proof", "secret", "secrets", "hidden", "whisper", "whispered", "moonlight",
    "lantern", "hairpin", "teacup", "tea", "cup", "sleeve", "mirror", "brocade",
}

POWER_DRAMA_WORDS = {
    "leaked", "leak", "leaks", "blabbed", "crashed", "crash", "faking", "fake", "faked",
    "caught", "ousted", "banned", "ban", "hacked", "hack", "spy", "spying", "panic",
    "secret", "secrets", "freeze", "frozen", "scandal", "scandals", "reckless",
    "unreadable", "rejected", "stolen", "cheating", "cheat", "quits", "quit",
    "disaster", "lawsuit", "lawsuits", "jailbreak", "jailbroken", "critical", "record",
    "massive", "shocking", "warning", "warnings", "privacy", "encrypted", "proof",
    "surveillance", "risk", "risks", "slammed", "slam", "vault", "vaults", "locked",
    "lockdown", "fired", "sued", "exploded", "exposed", "busted", "killed", "dead",
    "broke", "broken", "trapped", "collapse", "collapsed", "threat", "danger",
    "attack", "raid", "blocked", "shutdown", "fraud", "lied", "lying", "crisis",
    "urgent", "emergency", "steal", "destroy", "destroyed", "wipe", "wiped",
}

MAJOR_TECH_BRANDS = {
    "openai", "anthropic", "meta", "nvidia", "google", "apple", "signal", "claude",
    "nasdaq", "spacex", "congress", "muse", "jolly", "gpt", "airbnb", "reuters",
    "microsoft", "amazon", "tesla", "tiktok", "youtube", "gemini", "chatgpt",
    "grok", "deepseek", "xai", "ai", "gpu", "gpus", "chip", "chips", "bank", "banks",
    "robot", "robots", "model", "models", "cloud", "server", "servers", "tech",
    "crypto", "bitcoin", "fed", "sec", "ftc", "pentagon", "nasa", "silicon",
}

PROPER_STOPWORDS = {
    "i", "i'm", "i've", "i'll", "i'd", "a", "an", "the", "and", "or", "but", "if",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as", "is", "are",
    "was", "were", "be", "been", "being", "it", "its", "it's", "this", "that",
    "these", "those", "he", "she", "they", "we", "you", "his", "her", "their",
    "our", "your", "not", "no", "so", "just", "now", "here", "there", "what",
    "when", "where", "why", "how", "who", "all", "one", "new", "up", "out", "off",
    "over", "into", "about", "after", "before", "still", "even", "only", "very",
    "too", "also", "then", "than", "yes", "yeah", "well", "oh", "hey", "ok", "okay",
    "my", "mine", "yours", "hers", "him", "them", "us", "let", "do", "did",
    "does", "will", "would", "could", "should", "shall", "may", "might", "must",
    "have", "has", "had", "never", "ever", "more", "most", "some", "any", "each",
    "every", "such", "own", "same", "once", "twice", "until", "while",
}


def get_aspect_preset(aspect_ratio: str) -> Dict[str, Any]:
    ar = (aspect_ratio or "9:16").strip()
    if ar not in ASPECT_RATIO_PRESETS:
        raise ValueError(f"Unsupported aspect ratio '{aspect_ratio}'. Expected one of: {', '.join(ASPECT_RATIO_PRESETS)}")
    return ASPECT_RATIO_PRESETS[ar]


def infer_video_format(plan: Dict) -> str:
    explicit = str(plan.get("video_format") or "").strip().lower()
    if explicit:
        return explicit
    shots = plan.get("shots", []) or []
    acts = plan.get("acts", []) or []
    target_dur = float(plan.get("target_duration_sec") or 0.0)
    if acts or len(shots) > 25 or target_dur > 90.0:
        return "long_form"
    return "short"


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
    "a", "an", "and", "are", "as", "at", "because", "before", "billion", "bucks",
    "but", "by", "each", "every", "for", "from", "hundred", "if", "in", "into",
    "is", "just", "less", "million", "more", "of", "on", "or", "over", "per",
    "so", "than", "that", "the", "then", "thousand", "to", "total", "trillion",
    "under", "was", "were", "when", "while", "with", "without", "worth",
}

RISKY_ALLCAPS_WORDS = {"US", "IT", "IN", "ON", "OR", "AM"}

COMPACT_CURRENCY_SUFFIX = {
    "K": "thousand",
    "M": "million",
    "B": "billion",
    "T": "trillion",
}

COMPARISON_SYMBOL_WORDS = {
    "<=": "less than or equal to",
    ">=": "greater than or equal to",
    "!=": "not equal to",
    "->": "to",
    "<": "under",
    ">": "over",
    "ω": "omega",
}


def _expand_pronunciation_map(pronunciations: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Build display -> phonetic map, auto-deriving safe plural keys (e.g. NPC -> NPCs) when unmapped."""
    if not isinstance(pronunciations, dict) or not pronunciations:
        return {}
    clean_map: Dict[str, str] = {}
    for raw_k, raw_v in pronunciations.items():
        if isinstance(raw_k, str) and isinstance(raw_v, str) and raw_k.strip():
            clean_map[raw_k] = raw_v
    if not clean_map:
        return {}
    expanded = dict(clean_map)
    for k, v in clean_map.items():
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", k) and any(ch.isalpha() for ch in k):
            plural_k = f"{k}s"
            if plural_k not in expanded:
                v_clean = v.strip()
                if re.search(r"(?:^|\s)[A-Z]$", v_clean):
                    expanded[plural_k] = f"{v_clean}'s"
                else:
                    expanded[plural_k] = f"{v_clean}s"
    return expanded


def _covered_spans(text: str, pronunciations: Optional[Dict[str, str]]) -> List[Tuple[int, int]]:
    expanded = _expand_pronunciation_map(pronunciations)
    if not expanded or not text:
        return []
    sorted_keys = sorted(expanded.keys(), key=lambda k: (-len(k), k))
    pat = re.compile(
        "|".join(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])" for k in sorted_keys)
    )
    return [(m.start(), m.end()) for m in pat.finditer(text)]


def _span_overlaps(s: int, e: int, spans: List[Tuple[int, int]]) -> bool:
    return any(s < ce and cs < e for cs, ce in spans)


def _span_inside(s: int, e: int, spans: List[Tuple[int, int]]) -> bool:
    return any(cs <= s and e <= ce for cs, ce in spans)


def lint_display_phonetic_leaks(display_text: str) -> List[Dict[str, str]]:
    """Flag spaced phonetic spellings like 'N P C' or 'A W S' in raw display dialogue_or_vo."""
    issues: List[Dict[str, str]] = []
    if not display_text:
        return issues
    for m in re.finditer(r"\b((?:[A-Z]\s+){2,}[A-Z])\b", display_text):
        matched = m.group(1)
        collapsed = re.sub(r"\s+", "", matched)
        issues.append({
            "code": "TTS_MISREAD",
            "severity": "error",
            "match": matched,
            "suggestion": f'{collapsed} (map "{collapsed}": "{matched}" in tts_pronunciations)',
            "reason": "Spaced phonetic letters in display dialogue_or_vo leak into burned-in captions; keep clean acronyms in dialogue_or_vo and put phonetics in tts_pronunciations or tts_text.",
        })
    return issues


def lint_narration_text(
    text: str,
    pronunciations: Optional[Dict[str, str]] = None,
    *,
    is_tts_text: bool = False,
) -> List[Dict[str, str]]:
    """Detect neural TTS mispronunciations and soft pronunciation risks before synthesis."""
    issues: List[Dict[str, str]] = []
    if not text:
        return issues

    pron_keys = set(pronunciations.keys()) if isinstance(pronunciations, dict) else set()
    pron_keys_lower = {k.lower() for k in pron_keys}
    covered = _covered_spans(text, pronunciations)
    warned_spans: List[Tuple[int, int]] = []

    # 0. Spaced phonetic leaks in display text (always an error when linting raw display dialogue_or_vo)
    if not is_tts_text:
        issues.extend(lint_display_phonetic_leaks(text))

    # 1. "$700 PS5 Pro" -> often misread as "seven hundred dollars PS5 Pro"
    for m in re.finditer(r"\$(\d+(?:,\d{3})*)\s+([A-Za-z][A-Za-z0-9-]*)", text):
        if _span_inside(m.start(), m.end(), covered):
            continue
        num = m.group(1).replace(",", "")
        noun = m.group(2)
        if noun.lower() in NON_NOUN_FOLLOWERS:
            continue
        issues.append({
            "code": "TTS_MISREAD",
            "severity": "error",
            "match": m.group(0),
            "suggestion": f"{num}-dollar {noun}",
            "reason": "Currency before a noun can read as '<amount> dollars <noun>' instead of '<amount>-dollar <noun>'.",
        })

    # 1b. Compact currency like "$10M" or "$1.5T" -> neural TTS often reads "$1.5T" as "one point five T"
    for m in re.finditer(r"\$(\d+(?:\.\d+)?)([KMBT])\b", text):
        if m.group(0) in pron_keys or _span_inside(m.start(), m.end(), covered):
            continue
        unit_word = COMPACT_CURRENCY_SUFFIX[m.group(2)]
        issues.append({
            "code": "TTS_MISREAD",
            "severity": "error",
            "match": m.group(0),
            "suggestion": f"${m.group(1)} {unit_word}",
            "reason": "Compact currency suffix (K/M/B/T) is often read literally as a letter by neural TTS; write out the magnitude or map it in tts_pronunciations/tts_text.",
        })
        warned_spans.append((m.start(), m.end()))

    # 2. "no 60 FPS" -> neural TTS often reads "no <digits>" as "number <digits>"
    for m in re.finditer(r"\b(no)\s+(\d{1,3})\b", text, flags=re.IGNORECASE):
        if _span_inside(m.start(), m.end(), covered):
            continue
        num = int(m.group(2))
        word_num = int_to_words(num)
        issues.append({
            "code": "TTS_MISREAD",
            "severity": "error",
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
            if _span_inside(m.start(), m.end(), covered):
                continue
            issues.append({
                "code": "TTS_MISREAD",
                "severity": "error",
                "match": m.group(0),
                "suggestion": repl.strip(),
                "reason": desc,
            })

    # 4. Soft warning: risky 2-letter ALL-CAPS English words (with clock-time "3 AM" handling)
    for m in re.finditer(r"\b(US|IT|IN|ON|OR|AM)\b", text):
        word = m.group(1)
        if word in pron_keys or _span_inside(m.start(), m.end(), covered):
            continue
        if word == "AM":
            m_clock = re.search(r"\b(\d{1,2}(?::\d{2})?)\s+$", text[: m.start()])
            if m_clock:
                clock_str = f"{m_clock.group(1)} AM"
                if clock_str in pron_keys:
                    continue
                issues.append({
                    "code": "TTS_PRONUNCIATION_WARNING",
                    "severity": "warning",
                    "match": clock_str,
                    "suggestion": 'map "AM": "A M" in tts_pronunciations',
                    "reason": f"Clock time '{clock_str}' should not be lowercased to 'am'; map '\"AM\": \"A M\"' in tts_pronunciations if TTS reads it as 'am'.",
                })
                warned_spans.append((m.start(), m.end()))
                continue
        issues.append({
            "code": "TTS_ALLCAPS_WARNING",
            "severity": "warning",
            "match": word,
            "suggestion": word.lower(),
            "reason": f"2-letter ALL-CAPS '{word}' is often read by neural TTS as an acronym or state code; lowercase it unless intentional.",
        })
        warned_spans.append((m.start(), m.end()))

    # 5. Soft warning: unmapped plural acronyms (e.g. NPCs, GPUs, APIs)
    for m in re.finditer(r"\b([A-Z]{2,5}s)\b", text):
        tok = m.group(1)
        base = tok[:-1]
        if tok in pron_keys or base in pron_keys or _span_inside(m.start(), m.end(), covered):
            continue
        issues.append({
            "code": "TTS_PRONUNCIATION_WARNING",
            "severity": "warning",
            "match": tok,
            "suggestion": f'map "{base}" (or "{tok}") in tts_pronunciations',
            "reason": f"Plural acronym '{tok}' can be slurred or misread by neural TTS; add '{base}' to tts_pronunciations.",
        })
        warned_spans.append((m.start(), m.end()))

    # 6. Soft warning: possessive acronyms (e.g. MCP's, AWS's)
    for m in re.finditer(r"\b([A-Z]{2,6}['’]s)\b", text):
        tok = m.group(1)
        base = tok[:-2]
        if tok in pron_keys or base in pron_keys or _span_inside(m.start(), m.end(), covered) or _span_inside(m.start(), m.end() - 2, covered):
            continue
        issues.append({
            "code": "TTS_PRONUNCIATION_WARNING",
            "severity": "warning",
            "match": tok,
            "suggestion": f'map "{base}" in tts_pronunciations',
            "reason": f"Possessive acronym '{tok}' can be slurred by neural TTS; add '{base}' to tts_pronunciations.",
        })
        warned_spans.append((m.start(), m.end()))

    # 7. Soft warning: resolutions and error codes (1080p, 4K, CE-108255-1)
    for m in re.finditer(r"\b(\d{3,4}[pPkK]|[2468][kK]|[A-Z]{2,4}-\d[\w-]*)\b", text):
        tok = m.group(1)
        if tok in pron_keys or _span_inside(m.start(), m.end(), covered) or _span_overlaps(m.start(), m.end(), warned_spans):
            continue
        issues.append({
            "code": "TTS_PRONUNCIATION_WARNING",
            "severity": "warning",
            "match": tok,
            "suggestion": f'map "{tok}" in tts_pronunciations or spell out in tts_text',
            "reason": f"Resolution or code '{tok}' can be read unevenly by neural TTS; verify or map in tts_pronunciations.",
        })
        warned_spans.append((m.start(), m.end()))

    # 8. Soft warning: credit/grade signs (AA-, A+, B-)
    for m in re.finditer(r"(?<![A-Za-z0-9/+-])([A-F]{1,3}[+-])(?=\s|$|[.,;!?])", text):
        tok = m.group(1)
        if tok in pron_keys or _span_inside(m.start(), m.end(), covered) or _span_overlaps(m.start(), m.end(), warned_spans):
            continue
        sign_word = "plus" if tok.endswith("+") else "minus"
        issues.append({
            "code": "TTS_PRONUNCIATION_WARNING",
            "severity": "warning",
            "match": tok,
            "suggestion": f'map "{tok}" to "{tok[:-1]} {sign_word}" in tts_pronunciations',
            "reason": f"Credit or grade sign '{tok}' often drops the '{tok[-1]}' in neural TTS; spell out '{sign_word}' in tts_pronunciations or tts_text.",
        })
        warned_spans.append((m.start(), m.end()))

    # 9. Soft warning: comparison / math symbols (<=, >=, !=, ->, <, >, ω)
    for m in re.finditer(r"(<=|>=|!=|->|[<>ω])", text):
        sym = m.group(1)
        if sym in pron_keys or _span_inside(m.start(), m.end(), covered):
            continue
        issues.append({
            "code": "TTS_PRONUNCIATION_WARNING",
            "severity": "warning",
            "match": sym,
            "suggestion": COMPARISON_SYMBOL_WORDS.get(sym, "spell out in words"),
            "reason": f"Symbol '{sym}' is often skipped or misread by neural TTS; write it in words in tts_text.",
        })
        warned_spans.append((m.start(), m.end()))

    # 10. Soft warning: compact units (750B, 42.4pp, 120tok/s, tok/s)
    for m in re.finditer(r"(?<!\$)\b(\d+(?:\.\d+)?(?:pp|tok/s|[BMKT])|tok/s)\b", text):
        tok = m.group(1)
        if tok in pron_keys or _span_inside(m.start(), m.end(), covered) or _span_overlaps(m.start(), m.end(), warned_spans):
            continue
        issues.append({
            "code": "TTS_PRONUNCIATION_WARNING",
            "severity": "warning",
            "match": tok,
            "suggestion": f'map "{tok}" in tts_pronunciations or spell out units in tts_text',
            "reason": f"Compact unit '{tok}' is often read literally as letters by neural TTS; expand in tts_pronunciations or tts_text.",
        })
        warned_spans.append((m.start(), m.end()))

    # 11. Soft warning: unmapped consonant-cluster acronyms (SDR, HDR, NPC, PSN, FPS, DLC)
    for m in re.finditer(r"\b([BCDFGHJKLMNPQRSTVWXYZ]{3,5})\b", text):
        tok = m.group(1)
        if tok in pron_keys or _span_inside(m.start(), m.end(), covered) or _span_overlaps(m.start(), m.end(), warned_spans):
            continue
        spaced = " ".join(tok)
        issues.append({
            "code": "TTS_PRONUNCIATION_WARNING",
            "severity": "warning",
            "match": tok,
            "suggestion": f'map "{tok}": "{spaced}" in tts_pronunciations',
            "reason": f"Consonant-cluster acronym '{tok}' can be garbled by neural TTS; map it in tts_pronunciations.",
        })
        warned_spans.append((m.start(), m.end()))

    # 12. Soft warning: "eval" / "evals" -> often misread as "eh-val"
    for m in re.finditer(r"\b(evals?)\b", text, flags=re.IGNORECASE):
        tok = m.group(1)
        low = tok.lower()
        if low in pron_keys_lower or "eval" in pron_keys_lower or _span_inside(m.start(), m.end(), covered):
            continue
        repl = "evaluations" if low.endswith("s") else "evaluation"
        issues.append({
            "code": "TTS_PRONUNCIATION_WARNING",
            "severity": "warning",
            "match": tok,
            "suggestion": repl,
            "reason": f"Neural TTS often mispronounces '{tok}'; use '{repl}' or map it in tts_pronunciations.",
        })
        warned_spans.append((m.start(), m.end()))

    return issues


def autofix_narration_text(text: str) -> str:
    def _dollar_repl(m: re.Match) -> str:
        if m.group(2).lower() in NON_NOUN_FOLLOWERS:
            return m.group(0)
        return f"{m.group(1).replace(',', '')}-dollar {m.group(2)}"

    fixed = re.sub(r"\$(\d+(?:,\d{3})*)\s+([A-Za-z][A-Za-z0-9-]*)", _dollar_repl, text)
    fixed = re.sub(
        r"\$(\d+(?:\.\d+)?)([KMBT])\b",
        lambda m: f"${m.group(1)} {COMPACT_CURRENCY_SUFFIX[m.group(2)]}",
        fixed,
    )
    fixed = re.sub(
        r"\b(no)\s+(\d{1,3})\b",
        lambda m: f"{m.group(1)} {int_to_words(int(m.group(2)))}",
        fixed,
        flags=re.IGNORECASE,
    )
    fixed = re.sub(r"\bw/", "with ", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"\bvs\.(?=\s)", "versus", fixed, flags=re.IGNORECASE)
    return fixed


SLOP_CLUSTER_TERMS = {
    "crucial", "pivotal", "paramount", "robust", "multifaceted",
    "meticulous", "realm", "garner", "bolster", "revolutionize",
    "groundbreaking", "synergy", "leverage",
}

NARRATOR_SPEAKER_ROLES = {"narrator", "host", "cohost", "anchor", "vo", "voiceover"}

# Tuple format: (category, compiled_regex, hint, last_shot_only, loop_only, commentary_only)
# Every regex uses capturing group 1 for the reported match so leading sentence
# punctuation is excluded from warnings.
SLOP_PATTERNS: List[Tuple[str, re.Pattern, str, bool, bool, bool]] = [
    (
        "fake_contrast",
        re.compile(
            r"\b((?:(?:it|this|that|he|she)\s+isn['’]t|(?:they|we)\s+aren['’]t"
            r"|(?:it['’]s|this\s+is|that['’]s|they['’]re|we['’]re|he['’]s|she['’]s)\s+not|not)"
            r"\s+(?:just|merely|only)\s+(?:a|an|the|[\w$'’-]+)(?:\s+[\w$'’-]+){0,6}"
            r"\s*[,;:—–-]\s*(?:it['’]s|this\s+is|that['’]s|but\s+(?:also\s+)?(?:a|an|the|about|how|why|what)))\b",
            flags=re.IGNORECASE,
        ),
        "State what happened directly instead of a 'not just X, it's Y' contrast.",
        False,
        False,
        True,
    ),
    (
        "fake_contrast",
        re.compile(
            r"\b((?:this|that|it)\s+(?:doesn['’]t|does\s+not)\s+(?:just\s+)?mean\b[^.!?]{2,60}[.!?;]\s*it\s+means)\b",
            flags=re.IGNORECASE,
        ),
        "State what happened directly instead of a 'not just X, it's Y' contrast.",
        False,
        False,
        True,
    ),
    (
        "fake_contrast",
        re.compile(
            r"(?:^|[.!?]\s+)(forget\s+[\w\s'’-]{2,35}(?:[—–:]|\s+-{1,2}\s+))",
            flags=re.IGNORECASE,
        ),
        "State what happened directly instead of an ad-copy 'Forget X' contrast.",
        False,
        False,
        True,
    ),
    (
        "throat_clearing",
        re.compile(
            r"\b(in\s+today['’]s\s+(?:world|fast[- ]paced|ever[- ]evolving"
            r"|rapidly\s+(?:changing|evolving)|digital\s+(?:age|world|era)"
            r"|modern\s+(?:world|era)|ai[- ]driven|hyper[- ]connected))\b",
            flags=re.IGNORECASE,
        ),
        "Cut the warm-up phrase and open directly on the fact or action.",
        False,
        False,
        False,
    ),
    (
        "throat_clearing",
        re.compile(
            r"\b(let['’]s\s+dive\s+(?:right\s+)?in(?:to)?|without\s+further\s+ado"
            r"|buckle\s+up|strap\s+in|have\s+you\s+ever\s+wondered)\b",
            flags=re.IGNORECASE,
        ),
        "Cut the warm-up phrase and open directly on the fact or action.",
        False,
        False,
        False,
    ),
    (
        "throat_clearing",
        re.compile(
            r"\b((?:here['’]s|here\s+is)\s+the\s+(?:thing|catch|kicker"
            r"|wild\s+part|crazy\s+part|scary\s+part))\b",
            flags=re.IGNORECASE,
        ),
        "Cut the warm-up phrase and open directly on the fact or action.",
        False,
        False,
        False,
    ),
    (
        "throat_clearing",
        re.compile(
            r"\b((?:here['’]s|here\s+is)\s+the\s+truth)\b",
            flags=re.IGNORECASE,
        ),
        "Cut the warm-up phrase and open directly on the fact or action.",
        False,
        False,
        True,
    ),
    (
        "colon_reveal",
        re.compile(
            r"\b((?:the\s+(?:best|worst|crazy|wild|scary|funny)\s+part"
            r"|the\s+(?:secret|catch|twist|truth|reality|takeaway)"
            r"|lesson\s+learned|bottom\s+line"
            r"|one\s+thing\s+is\s+clear|plot\s+twist)\s*:)",
            flags=re.IGNORECASE,
        ),
        "Fold the point into a direct spoken sentence instead of a staged colon reveal.",
        False,
        False,
        False,
    ),
    (
        "dramatic_filler",
        re.compile(
            r"\b(let\s+that\s+sink\s+in|(?:and\s+)?that\s+changes\s+everything(?=[.!?]|\s*$)"
            r"|nothing\s+will\s+ever\s+be\s+the\s+same"
            r"|that(?:['’]s|\s+is)\s+the\s+real\s+(?:win|shift|game[- ]changer))\b",
            flags=re.IGNORECASE,
        ),
        "Cut the dramatic echo; let the concrete fact carry the punch.",
        False,
        False,
        True,
    ),
    (
        "dramatic_filler",
        re.compile(
            r"(?:^|[.!?]\s+)((?:total|complete|utter|pure)\s+(?:\w+\s+){0,2}(?:disaster|chaos|madness|nightmare)[.!]?)\s*$",
            flags=re.IGNORECASE,
        ),
        "Cut the dramatic echo fragment; let the concrete fact carry the punch.",
        False,
        False,
        True,
    ),
    (
        "fake_deep",
        re.compile(
            r"\b(at\s+its\s+core|a\s+paradigm\s+shift"
            r"|(?:marks?|represents?)\s+a\s+(?:pivotal|defining|watershed)\s+(?:moment|shift|milestone)"
            r"|(?:stands?|serves?)\s+as\s+a\s+(?:testament|beacon|reminder)"
            r"|(?:rich|intricate|complex)\s+tapestry)\b",
            flags=re.IGNORECASE,
        ),
        "Replace grand framing with the concrete fact or consequence.",
        False,
        False,
        False,
    ),
    (
        "ai_buzzword",
        re.compile(
            r"\b(delv(?:e|es|ed|ing)|game[- ]changer"
            r"|(?:ever[- ]evolving|rapidly\s+evolving)\s+landscape"
            r"|(?:seamlessly|effortlessly)\s+(?:integrat|blend|combin|connect|transform)\w*"
            r"|unlock(?:s|ed|ing)?\s+(?:the\s+)?(?:full\s+)?(?:power|potential|secrets?)\s+of"
            r"|underscor(?:e|es|ed|ing)\s+(?:the\s+)?(?:importance|significance|need|fact|commitment)"
            r"|navigat(?:e|es|ed|ing)\s+the\s+(?:complex|intricate|ever[- ]changing|shifting))\b",
            flags=re.IGNORECASE,
        ),
        "Replace AI-cliche wording with plain spoken English.",
        False,
        False,
        False,
    ),
    (
        "wrapup_summary",
        re.compile(
            r"\b(in\s+summary|to\s+sum\s+up|in\s+conclusion|all\s+in\s+all"
            r"|at\s+the\s+end\s+of\s+the\s+day)\b",
            flags=re.IGNORECASE,
        ),
        "Cut the wrap-up phrase; end on a concrete beat that loops into Shot 1.",
        False,
        False,
        False,
    ),
    (
        "wrapup_summary",
        re.compile(
            r"\b(only\s+time\s+will\s+tell)\b",
            flags=re.IGNORECASE,
        ),
        "Cut the wrap-up phrase; end on a concrete beat that loops into Shot 1.",
        False,
        False,
        True,
    ),
    (
        "wrapup_summary",
        re.compile(
            r"^\s*((?:overall|ultimately)\s*,)",
            flags=re.IGNORECASE,
        ),
        "Cut the wrap-up phrase; end on a concrete beat that loops into Shot 1.",
        True,
        False,
        False,
    ),
    (
        "wrapup_summary",
        re.compile(
            r"(?:^|[.!?]\s+)((?:which\s+is|and\s+that['’]s)\s+(?:the\s+exact\s+reason|why)\s*(?:\.{0,3}|…))\s*$",
            flags=re.IGNORECASE,
        ),
        "Avoid overusing flat 'Which is why...' loop bridges; end on a concrete beat or rotate loop callbacks.",
        True,
        True,
        False,
    ),
]

DRAMA_SLOP_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    (
        "drama_slop",
        re.compile(
            r"\b(mark\s+my\s+words|grave\s+mistake|you\s+have\s+no\s+idea\s+what\s+you['’]ve"
            r"|played\s+right\s+into\s+my\s+hands|little\s+did\s+(?:he|she|they)\s+know"
            r"|my\s+blood\s+ran\s+cold|this\s+ends\s+now)\b",
            flags=re.IGNORECASE,
        ),
        "Replace melodramatic stock phrasing with concrete subtext or specific character action.",
    ),
]

METADATA_SLOP_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    (
        "metadata_opener",
        re.compile(r"(?:^|[.!?\n]\s*|[-*]\s*|\*\*\s*)(imagine(?:\.{3}|…|\s+\w+))", flags=re.IGNORECASE),
        "Cut recycled 'Imagine...' metadata opener; lead with the concrete fact or hook.",
    ),
    (
        "metadata_opener",
        re.compile(r"\b(be\s+honest\s*:|honest\s+question\s*:)", flags=re.IGNORECASE),
        "Cut recycled 'Be honest:' / 'Honest question:' opener; ask the question directly.",
    ),
    (
        "metadata_opener",
        re.compile(r"\b(wait\s*[—–-]+\s*did\s+you)\b", flags=re.IGNORECASE),
        "Cut recycled 'Wait-did you...' clickbait opener; state the concrete hook directly.",
    ),
]

LITERAL_BLANK_SIGN_RE = re.compile(
    r"\b((?:blank|empty)\s+(?:signs?|billboards?|awnings?|screens?|posters?))\b",
    flags=re.IGNORECASE,
)

TEXT_MAGNET_PROP_RE = re.compile(
    r"\b(neon\s+signs?|billboards?|calendars?|bus\s+signs?|street\s+signs?|storefront\s+signs?|menu\s+boards?|news\s+tickers?)\b",
    flags=re.IGNORECASE,
)

CARTOON_SFX_CUES = {"bonk_boing", "pop", "record_scratch", "siren_chirp"}


def _extract_slop_cluster_terms(text: str) -> List[str]:
    terms: List[str] = []
    seen = set()
    for tok in re.findall(r"[a-z]+", text.lower()):
        if tok in SLOP_CLUSTER_TERMS and tok not in seen:
            seen.add(tok)
            terms.append(tok)
    return terms


def lint_slop_text(
    text: str,
    is_last_shot: bool = False,
    skip_loop_checks: bool = False,
    skip_commentary_patterns: bool = False,
    check_drama_slop: bool = False,
) -> List[Dict[str, str]]:
    """Detect low-false-positive AI-slop patterns in spoken narration and dialogue."""
    if not text or not text.strip():
        return []

    issues: List[Dict[str, str]] = []
    seen_spans: List[Tuple[int, int]] = []

    for category, pattern, hint, last_shot_only, loop_only, commentary_only in SLOP_PATTERNS:
        if last_shot_only and not is_last_shot:
            continue
        if loop_only and skip_loop_checks:
            continue
        if commentary_only and skip_commentary_patterns:
            continue
        effective_hint = hint
        if skip_loop_checks and category == "wrapup_summary":
            effective_hint = "Cut the wrap-up phrase; end on a concrete story beat, outro, or cliffhanger."
        for m in pattern.finditer(text):
            grp = 1 if m.lastindex else 0
            s, e = m.start(grp), m.end(grp)
            if any(s < es and ss < e for ss, es in seen_spans):
                continue
            matched = m.group(grp).strip()
            if not matched:
                continue
            seen_spans.append((s, e))
            issues.append({
                "code": "AI_SLOP_SCRIPT",
                "category": category,
                "match": matched,
                "hint": effective_hint,
            })

    if check_drama_slop:
        for category, pattern, hint in DRAMA_SLOP_PATTERNS:
            for m in pattern.finditer(text):
                grp = 1 if m.lastindex else 0
                s, e = m.start(grp), m.end(grp)
                if any(s < es and ss < e for ss, es in seen_spans):
                    continue
                matched = m.group(grp).strip()
                if not matched:
                    continue
                seen_spans.append((s, e))
                issues.append({
                    "code": "AI_SLOP_SCRIPT",
                    "category": category,
                    "match": matched,
                    "hint": hint,
                })

    cluster_terms = _extract_slop_cluster_terms(text)
    if len(cluster_terms) >= 2:
        issues.append({
            "code": "AI_SLOP_SCRIPT",
            "category": "ai_buzzword_cluster",
            "match": ", ".join(cluster_terms),
            "hint": "Replace clustered AI buzzwords with plain spoken English.",
        })

    return issues


def lint_metadata_text(
    text: str,
    field_name: str,
    *,
    is_title: bool = False,
) -> List[Dict[str, str]]:
    """Lint YouTube titles, descriptions, and pinned comments for AI slop, em-dash spam, and illegal title chars."""
    if not text or not text.strip():
        return []

    warnings: List[Dict[str, str]] = []
    for iss in lint_slop_text(
        text,
        is_last_shot=False,
        skip_loop_checks=True,
        skip_commentary_patterns=False,
        check_drama_slop=False,
    ):
        warnings.append({
            "code": "AI_SLOP_SCRIPT",
            "shot_id": field_name,
            "category": iss["category"],
            "match": iss["match"],
            "hint": iss["hint"],
        })

    seen_spans: List[Tuple[int, int]] = []
    for category, pattern, hint in METADATA_SLOP_PATTERNS:
        for m in pattern.finditer(text):
            grp = 1 if m.lastindex else 0
            s, e = m.start(grp), m.end(grp)
            if any(s < es and ss < e for ss, es in seen_spans):
                continue
            matched = m.group(grp).strip()
            if not matched:
                continue
            seen_spans.append((s, e))
            warnings.append({
                "code": "AI_SLOP_SCRIPT",
                "shot_id": field_name,
                "category": category,
                "match": matched,
                "hint": hint,
            })

    em_dash_count = text.count("—")
    if em_dash_count >= 1:
        label = f"em-dash spam ({em_dash_count}x '—')" if em_dash_count >= 2 else "em-dash '—'"
        warnings.append({
            "code": "AI_SLOP_SCRIPT",
            "shot_id": field_name,
            "category": "em_dash_spam",
            "match": label,
            "hint": "Replace em dashes ('—') with commas, periods, colons, or parentheses per clear-natural-english.",
        })

    if is_title:
        m_ang = re.search(r"[<>]", text)
        if m_ang:
            warnings.append({
                "code": "AI_SLOP_SCRIPT",
                "shot_id": field_name,
                "category": "youtube_illegal_title_char",
                "match": m_ang.group(0),
                "hint": "YouTube rejects raw '<' and '>' in titles; write 'under' or 'over' instead.",
            })

    return warnings


def lint_metadata_file(path: Path) -> List[Dict[str, str]]:
    """Lint a JSON plan/packaging file or a Markdown packaging.md file for metadata slop."""
    raw = path.read_text(encoding="utf-8")
    warnings: List[Dict[str, str]] = []

    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        if isinstance(data, dict):
            if isinstance(data.get("title"), str):
                warnings.extend(lint_metadata_text(data["title"], "title", is_title=True))
            pkg = data.get("packaging") if isinstance(data.get("packaging"), dict) else data
            if isinstance(pkg, dict):
                cands = pkg.get("title_candidates")
                if isinstance(cands, list):
                    for idx, cand in enumerate(cands):
                        if isinstance(cand, str):
                            warnings.extend(
                                lint_metadata_text(cand, f"packaging.title_candidates[{idx}]", is_title=True)
                            )
                for title_key in ("yt_title", "thumbnail_headline", "thumbnail_accent"):
                    if isinstance(pkg.get(title_key), str):
                        warnings.extend(lint_metadata_text(pkg[title_key], f"packaging.{title_key}", is_title=True))
                for body_key in ("description", "yt_description", "pinned_comment", "yt_pinned_comment"):
                    if isinstance(pkg.get(body_key), str):
                        warnings.extend(lint_metadata_text(pkg[body_key], f"packaging.{body_key}", is_title=False))
        return warnings

    # Markdown / plain-text packaging file
    in_code = False
    in_title_section = False
    body_lines: List[str] = []
    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            heading_low = stripped.lower()
            in_title_section = ("title" in heading_low) and ("chapter" not in heading_low)
            continue
        m_inline_title = re.match(r"^(?:\*\*)?(?:youtube\s+)?title(?:\s*\d+)?(?:\*\*)?\s*:\s*(.+)$", stripped, flags=re.IGNORECASE)
        if m_inline_title:
            title_val = m_inline_title.group(1).strip().strip("`\"'")
            warnings.extend(lint_metadata_text(title_val, f"{path.name}:L{line_no}(title)", is_title=True))
            continue
        if in_title_section and stripped:
            clean_t = re.sub(r"^(?:[-*]|\d+\.)\s+", "", stripped).strip().strip("`\"'")
            if clean_t:
                warnings.extend(lint_metadata_text(clean_t, f"{path.name}:L{line_no}(title)", is_title=True))
            continue
        body_lines.append(raw_line)

    body_text = "\n".join(body_lines)
    if body_text.strip():
        warnings.extend(lint_metadata_text(body_text, f"{path.name}(body)", is_title=False))
    return warnings


def lint_plan_slop(plan: Dict) -> List[Dict[str, str]]:
    """Run genre- and speaker-aware AI_SLOP_SCRIPT checks across every shot, metadata fields, and plan-wide buzzword clusters."""
    shots = plan.get("shots", []) or []
    video_format = infer_video_format(plan)
    script_genre = str(plan.get("script_genre") or "commentary").strip().lower() or "commentary"
    ending_style = str(plan.get("ending_style") or "").strip().lower()
    skip_loop_checks = (video_format == "long_form") or (ending_style in ("cliffhanger", "outro"))
    check_drama_slop = (script_genre == "drama") or (video_format == "long_form")

    warnings: List[Dict[str, str]] = []
    plan_cluster_terms: List[str] = []
    seen_plan_terms = set()
    shot_cluster_warned = False

    for idx, shot in enumerate(shots):
        sid = str(shot.get("shot_id", f"shot_{idx + 1:02d}"))
        text = str(shot.get("dialogue_or_vo", "") or "")
        is_last = idx == len(shots) - 1
        speaker = str(shot.get("speaker") or "").strip().lower()
        is_narrator = speaker in NARRATOR_SPEAKER_ROLES
        skip_commentary = (script_genre == "drama" and not is_narrator)

        if video_format != "long_form" and idx == 0:
            m_because = re.match(r"^\s*(because\b)", text, flags=re.IGNORECASE)
            if m_because:
                warnings.append({
                    "code": "AI_SLOP_SCRIPT",
                    "shot_id": sid,
                    "category": "because_cold_open",
                    "match": m_because.group(1),
                    "hint": "Avoid opening Shot 1 with 'Because ...'; start with a standalone concrete hook that lands cold at 0:00.",
                })

        for iss in lint_slop_text(
            text,
            is_last_shot=is_last,
            skip_loop_checks=skip_loop_checks,
            skip_commentary_patterns=skip_commentary,
            check_drama_slop=check_drama_slop,
        ):
            if iss["category"] == "ai_buzzword_cluster":
                shot_cluster_warned = True
            warnings.append({
                "code": iss["code"],
                "shot_id": sid,
                "category": iss["category"],
                "match": iss["match"],
                "hint": iss["hint"],
            })
        for term in _extract_slop_cluster_terms(text):
            if term not in seen_plan_terms:
                seen_plan_terms.add(term)
                plan_cluster_terms.append(term)

    if len(plan_cluster_terms) >= 3 and not shot_cluster_warned:
        warnings.append({
            "code": "AI_SLOP_SCRIPT",
            "shot_id": "plan",
            "category": "ai_buzzword_cluster",
            "match": ", ".join(plan_cluster_terms),
            "hint": "Replace clustered AI buzzwords across the script with plain spoken English.",
        })

    # Scan plan.title and plan.packaging metadata fields
    if isinstance(plan.get("title"), str) and plan["title"].strip():
        warnings.extend(lint_metadata_text(plan["title"], "title", is_title=True))
    pkg = plan.get("packaging")
    if isinstance(pkg, dict):
        cands = pkg.get("title_candidates")
        if isinstance(cands, list):
            for idx, cand in enumerate(cands):
                if isinstance(cand, str) and cand.strip():
                    warnings.extend(
                        lint_metadata_text(cand, f"packaging.title_candidates[{idx}]", is_title=True)
                    )
        for title_key in ("thumbnail_headline", "thumbnail_accent"):
            if isinstance(pkg.get(title_key), str) and pkg[title_key].strip():
                warnings.extend(lint_metadata_text(pkg[title_key], f"packaging.{title_key}", is_title=True))
        for body_key in ("description", "pinned_comment"):
            if isinstance(pkg.get(body_key), str) and pkg[body_key].strip():
                warnings.extend(lint_metadata_text(pkg[body_key], f"packaging.{body_key}", is_title=False))

    return warnings


def lint_plan_prompt_hygiene(plan: Dict) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Check style_bible, spatial_layout, beats[].action, and negative_constraints for text-warp triggers."""
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    style_bible = str(plan.get("style_bible") or "")
    if style_bible.strip():
        for m in LITERAL_BLANK_SIGN_RE.finditer(style_bible):
            errors.append({
                "code": "LITERAL_BLANK_SIGN",
                "location": "style_bible",
                "match": m.group(1),
                "hint": "Replace with 'unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage' so models do not print literal 'BLANK'.",
            })
        sb_low = style_bible.lower()
        if "zero letters" not in sb_low and "zero signage" not in sb_low:
            warnings.append({
                "code": "MISSING_NO_TEXT_GUARD",
                "location": "style_bible",
                "match": "style_bible",
                "hint": "Append 'unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage' to style_bible to prevent background TEXT_WARP.",
            })

    for idx, shot in enumerate(plan.get("shots", []) or []):
        sid = str(shot.get("shot_id", f"shot_{idx + 1:02d}"))
        fields_to_check: List[Tuple[str, str, bool]] = [
            (f"{sid}.spatial_layout", str(shot.get("spatial_layout") or ""), True),
            (f"{sid}.negative_constraints", str(shot.get("negative_constraints") or ""), False),
        ]
        for b_idx, beat in enumerate(shot.get("beats", []) or []):
            if isinstance(beat, dict):
                fields_to_check.append((f"{sid}.beats[{b_idx}].action", str(beat.get("action") or ""), True))

        for loc, text, check_magnet in fields_to_check:
            if not text.strip():
                continue
            for m in LITERAL_BLANK_SIGN_RE.finditer(text):
                errors.append({
                    "code": "LITERAL_BLANK_SIGN",
                    "location": loc,
                    "match": m.group(1),
                    "hint": "Remove literal 'blank/empty sign' phrasing; use 'unadorned facades, plain empty surfaces, zero letters, zero signage'.",
                })
            if check_magnet:
                for m in TEXT_MAGNET_PROP_RE.finditer(text):
                    warnings.append({
                        "code": "TEXT_MAGNET_PROP",
                        "location": loc,
                        "match": m.group(1),
                        "hint": "Text-magnet prop often causes TEXT_WARP; swap for unadorned facades, colored light tubes, striped awnings, hourglasses, or abstract color bars (see references/sheet_prompt_templates.md).",
                    })

    return errors, warnings


def _parse_rate_percent(raw_rate: Any) -> Optional[float]:
    if raw_rate is None:
        return None
    s = str(raw_rate).strip()
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)%", s)
    if not m:
        return None
    return float(m.group(1))


def lint_plan_pacing_and_sfx(plan: Dict) -> List[Dict[str, str]]:
    """Run format-aware word budget, 16:9 commentary WPM/rate, and SFX tone checks."""
    warnings: List[Dict[str, str]] = []
    shots = plan.get("shots", []) or []
    if not shots:
        return warnings

    video_format = infer_video_format(plan)
    script_genre = str(plan.get("script_genre") or "commentary").strip().lower() or "commentary"
    aspect_ratio = str(plan.get("aspect_ratio") or "9:16").strip()
    pronunciations = plan.get("tts_pronunciations") if isinstance(plan.get("tts_pronunciations"), dict) else None

    if video_format == "short":
        spoken_words = sum(
            len(resolve_tts_text(shot, pronunciations=pronunciations, autofix=False).split())
            for shot in shots
        )
        avg_words = spoken_words / max(1, len(shots))
        if spoken_words > 125:
            warnings.append({
                "code": "PACING_OVERRUN",
                "location": "plan",
                "match": f"{spoken_words} spoken words",
                "hint": "Short script exceeds 125 expanded spoken words (target 95-115 words / 34-42s to stay safely under 48s).",
            })
        if avg_words > 11.0:
            warnings.append({
                "code": "PACING_OVERRUN",
                "location": "plan",
                "match": f"{avg_words:.1f} words/shot",
                "hint": "Average spoken words per shot exceeds 11 (risk of >3.8s shots); split long beats into faster cuts.",
            })

    if video_format == "long_form" and script_genre == "commentary" and aspect_ratio == "16:9":
        # Check TTS rates > +12%
        rates_to_check: List[Tuple[str, Any]] = []
        if plan.get("rate") is not None:
            rates_to_check.append(("plan.rate", plan.get("rate")))
        vc = plan.get("voice_cast")
        if isinstance(vc, dict):
            for sp, cfg in vc.items():
                if isinstance(cfg, dict) and cfg.get("rate") is not None:
                    rates_to_check.append((f"voice_cast.{sp}.rate", cfg.get("rate")))
        for idx, shot in enumerate(shots):
            sid = str(shot.get("shot_id", f"shot_{idx + 1:03d}"))
            if shot.get("rate") is not None:
                rates_to_check.append((f"{sid}.rate", shot.get("rate")))
            sfx = str(shot.get("sfx_type") or "").strip().lower()
            if sfx in CARTOON_SFX_CUES:
                warnings.append({
                    "code": "SFX_TONE_MISMATCH",
                    "location": sid,
                    "match": sfx,
                    "hint": "Cartoon SFX cue clashes with 16:9 commentary tone; use Tech/UI cues (ui_tick, ui_click, data_chime, sub_whoosh, alert_ping, soft_thud).",
                })

        for loc, raw_r in rates_to_check:
            pct = _parse_rate_percent(raw_r)
            if pct is not None and pct > 12.0:
                warnings.append({
                    "code": "PACING_FIREHOSE",
                    "location": loc,
                    "match": str(raw_r).strip(),
                    "hint": "16:9 commentary TTS rate exceeds +12% (firehose risk); keep between +6% and +10%.",
                })

        target_dur = plan.get("target_duration_sec")
        if isinstance(target_dur, (int, float)) and target_dur > 0:
            display_words = sum(len(str(s.get("dialogue_or_vo", "") or "").split()) for s in shots)
            wpm = display_words / (float(target_dur) / 60.0)
            if wpm > 130.0:
                warnings.append({
                    "code": "PACING_FIREHOSE",
                    "location": "plan",
                    "match": f"{wpm:.0f} WPM ({display_words} words / {target_dur:.0f}s)",
                    "hint": "16:9 commentary display pace exceeds 130 WPM; target 100-115 WPM (~300 words/3m, ~525 words/5m) and move caveats to packaging.md.",
                })

    return warnings


def apply_tts_pronunciations(text: str, pronunciations: Optional[Dict[str, str]]) -> str:
    """Apply top-level display -> phonetic TTS overrides longest-key-first with safe boundaries and plural acronym support."""
    if not text or not isinstance(pronunciations, dict) or not pronunciations:
        return text

    expanded_map = _expand_pronunciation_map(pronunciations)
    if not expanded_map:
        return text

    sorted_keys = sorted(expanded_map.keys(), key=lambda k: (-len(k), k))
    pattern = re.compile(
        "|".join(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])" for k in sorted_keys)
    )
    return pattern.sub(lambda m: expanded_map[m.group(0)], text)


def resolve_tts_text(
    shot: Dict,
    pronunciations: Optional[Dict[str, str]] = None,
    autofix: bool = False,
) -> str:
    """Derive the spoken TTS string for a shot while leaving display dialogue_or_vo untouched."""
    override = shot.get("tts_text")
    if isinstance(override, str) and override.strip():
        spoken = override.strip()
    else:
        spoken = str(shot.get("dialogue_or_vo", "") or "").strip()
        if spoken and pronunciations:
            spoken = apply_tts_pronunciations(spoken, pronunciations)
    if autofix and spoken:
        spoken = autofix_narration_text(spoken)
    return spoken


def _align_norm(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def _distribute_tokens_in_span(tokens: List[str], start_sec: float, end_sec: float) -> List[Dict]:
    if not tokens:
        return []
    start_s = max(0.0, float(start_sec))
    min_span = 0.02 * len(tokens)
    end_s = max(start_s + min_span, float(end_sec))
    total_dur = end_s - start_s
    weights = [max(2, len(_clean_token(tok)) or len(tok)) for tok in tokens]
    total_w = max(1, sum(weights))
    out: List[Dict] = []
    cursor = start_s
    for idx, (tok, w) in enumerate(zip(tokens, weights)):
        t_end = end_s if idx == len(tokens) - 1 else cursor + total_dur * (w / total_w)
        out.append({
            "text": tok,
            "start_sec": round(cursor, 4),
            "end_sec": round(max(cursor + 0.01, t_end), 4),
        })
        cursor = t_end
    return out


def align_words_to_display(
    tts_words: List[Dict],
    display_text: str,
    total_dur: float,
) -> List[Dict]:
    """Map spoken WordBoundary timings onto clean display tokens via SequenceMatcher.

    Unchanged words keep their exact WordBoundary timestamps; replaced phonetic spans
    (e.g. ["A", "W", "S"] -> ["AWS"]) inherit [first.start_sec, last.end_sec] and split
    proportionally by character length when mapping to multiple display tokens.
    """
    display_tokens = display_text.split()
    if not display_tokens:
        return []
    if not tts_words:
        return _distribute_tokens_in_span(display_tokens, 0.0, max(0.05, total_dur))

    tts_norm = [_align_norm(str(w.get("text", ""))) for w in tts_words]
    disp_norm = [_align_norm(tok) for tok in display_tokens]

    matcher = difflib.SequenceMatcher(None, tts_norm, disp_norm, autojunk=False)
    aligned: List[Dict] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                src = tts_words[i1 + offset]
                s = float(src["start_sec"])
                e = max(s + 0.01, float(src["end_sec"]))
                aligned.append({
                    "text": display_tokens[j1 + offset],
                    "start_sec": round(s, 4),
                    "end_sec": round(e, 4),
                })
        elif tag == "replace":
            span_start = float(tts_words[i1]["start_sec"])
            raw_end = max(float(w["end_sec"]) for w in tts_words[i1:i2])
            span_end = max(span_start + 0.02, raw_end)
            aligned.extend(_distribute_tokens_in_span(display_tokens[j1:j2], span_start, span_end))
        elif tag == "insert":
            prev_end = float(tts_words[i1 - 1]["end_sec"]) if i1 > 0 else 0.0
            if i1 < len(tts_words):
                next_start = float(tts_words[i1]["start_sec"])
            else:
                next_start = max(float(total_dur), prev_end + 0.05 * (j2 - j1))
            if next_start <= prev_end + 0.01:
                span_start = prev_end
                span_end = prev_end + 0.02 * (j2 - j1)
            else:
                span_start = prev_end
                span_end = next_start
            aligned.extend(_distribute_tokens_in_span(display_tokens[j1:j2], span_start, span_end))
        # tag == "delete": extra spoken tokens with no display counterpart are omitted

    if not aligned:
        s0 = float(tts_words[0]["start_sec"])
        e0 = max(float(tts_words[-1]["end_sec"]), float(total_dur))
        return _distribute_tokens_in_span(display_tokens, s0, e0)

    return aligned


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


def normalize_ass_color(color: str) -> str:
    c = (color or "").strip()
    if not c or c.lower() in ("auto", "palace"):
        return c.lower() if c else "auto"
    if re.fullmatch(r"[0-9A-Fa-f]{6}", c):
        return f"&H{c.upper()}&"
    if c.upper().startswith("&H"):
        body = c[2:].rstrip("&").upper()
        return f"&H{body}&"
    return CORAL_ORANGE_COLOR


def derive_subtle_tint(keyword_color: str) -> str:
    """Blend keyword ASS BGR color with white (55/45) so inactive keywords stay subtly tinted."""
    norm = normalize_ass_color(keyword_color)
    if norm in ("auto", "palace"):
        norm = CORAL_ORANGE_COLOR
    m = re.fullmatch(r"&H([0-9A-Fa-f]{6,8})&", norm)
    if not m:
        return "&H9BB0FF&"
    hex_str = m.group(1)[-6:]
    b = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    r = int(hex_str[4:6], 16)
    tb = min(255, int(round(b * 0.55 + 255 * 0.45)))
    tg = min(255, int(round(g * 0.55 + 255 * 0.45)))
    tr = min(255, int(round(r * 0.55 + 255 * 0.45)))
    return f"&H{tb:02X}{tg:02X}{tr:02X}&"


def parse_keyword_list(raw: object) -> List[str]:
    if not raw:
        return []
    items: List[str] = []
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        for entry in raw:
            if isinstance(entry, str):
                items.extend(part.strip() for part in entry.split(","))
    return [it for it in items if it]


def _clean_token(token: str) -> str:
    stripped = token.strip(".,!?;:\"'()[]{}<>…—–-")
    stripped = re.sub(r"(?:'s|’s)$", "", stripped, flags=re.IGNORECASE)
    return stripped


def _norm_key(token: str) -> str:
    return _clean_token(token).lower()


def _speaker_palace_fallback(speaker: Optional[str]) -> str:
    sp = (speaker or "").strip().lower()
    if sp in ("emperor", "prince", "authority", "guard", "ally", "cohost", "mentor", "advisor", "royal"):
        return "palace_imperial"
    if sp in ("rival", "antagonist", "villain", "enemy", "nemesis", "traitor"):
        return "palace_danger"
    if sp in ("protagonist", "hero", "heroine", "host", "lead"):
        return "palace_jade"
    return "palace_imperial"


def classify_keyword_category(
    raw_text: str,
    is_sentence_start: bool = False,
    all_caps_doc: bool = False,
    speaker: Optional[str] = None,
    palette: str = "auto",
) -> Optional[str]:
    """Classify a token into v3.1 Neon or Palace Drama semantic categories, or None."""
    stripped = raw_text.strip(".,!?;:\"'()[]{}<>…—–")
    if not stripped:
        return None

    # 1. Money / numbers / stats -> Neon Lime (or Imperial Gold in palace mode)
    if any(ch.isdigit() for ch in stripped) or "$" in stripped or "%" in stripped:
        return "palace_imperial" if palette == "palace" else "money_number"

    cleaned = _clean_token(stripped)
    lower = cleaned.lower()
    if not lower:
        return None

    parts = [p for p in re.split(r"[-/]", lower) if p]
    if lower in NUMBER_WORDS or any(p in NUMBER_WORDS for p in parts):
        return "palace_imperial" if palette == "palace" else "money_number"

    # 2. Palace Drama semantic tiers when palette == "palace"
    if palette == "palace":
        if lower in PALACE_DANGER_WORDS or any(p in PALACE_DANGER_WORDS for p in parts):
            return "palace_danger"
        if lower in PALACE_IMPERIAL_WORDS or any(p in PALACE_IMPERIAL_WORDS for p in parts):
            return "palace_imperial"
        if lower in PALACE_JADE_WORDS or any(p in PALACE_JADE_WORDS for p in parts):
            return "palace_jade"
        if lower in POWER_DRAMA_WORDS or any(p in POWER_DRAMA_WORDS for p in parts):
            return "palace_danger"
        if lower in MAJOR_TECH_BRANDS or any(p in MAJOR_TECH_BRANDS for p in parts):
            return "palace_jade"
        if not is_sentence_start and not all_caps_doc and len(cleaned) >= 2:
            if cleaned[0].isupper() and lower not in PROPER_STOPWORDS:
                return _speaker_palace_fallback(speaker)
        return None

    # 3. General v3.1 Semantic Neon tiers (default "auto")
    if lower in POWER_DRAMA_WORDS or any(p in POWER_DRAMA_WORDS for p in parts):
        return "drama_action"

    if lower in MAJOR_TECH_BRANDS or any(p in MAJOR_TECH_BRANDS for p in parts):
        return "brand_tech"

    if not is_sentence_start and not all_caps_doc and len(cleaned) >= 2:
        if cleaned[0].isupper() and lower not in PROPER_STOPWORDS:
            return "brand_tech"

    return None


def is_auto_keyword(
    raw_text: str,
    is_sentence_start: bool,
    all_caps_doc: bool = False,
    speaker: Optional[str] = None,
    palette: str = "auto",
) -> bool:
    """Auto-detect high-impact keywords (numbers/currency/%, drama/action, brands/tech)."""
    return (
        classify_keyword_category(
            raw_text,
            is_sentence_start=is_sentence_start,
            all_caps_doc=all_caps_doc,
            speaker=speaker,
            palette=palette,
        )
        is not None
    )


def _match_explicit_keywords(words: Sequence[Dict], explicit_keywords: Sequence[str], start_idx: int, end_idx: int) -> List[bool]:
    """Match explicit single-word and multi-word keywords over words[start_idx:end_idx]."""
    length = end_idx - start_idx
    flags = [False] * length
    single_set = set()
    raw_single_set = set()
    phrases: List[List[str]] = []

    for kw in explicit_keywords:
        toks = [t for t in kw.strip().split() if t]
        if not toks:
            continue
        if len(toks) == 1:
            nk = _norm_key(toks[0])
            if nk:
                single_set.add(nk)
            raw_single_set.add(toks[0].strip(".,!?;:\"'()[]{}<>").lower())
        else:
            phrases.append([_norm_key(t) or t.lower() for t in toks])

    norm_tokens = [_norm_key(words[start_idx + i]["text"]) for i in range(length)]
    raw_tokens = [words[start_idx + i]["text"].strip(".,!?;:\"'()[]{}<>").lower() for i in range(length)]

    for i in range(length):
        if norm_tokens[i] in single_set or raw_tokens[i] in raw_single_set:
            flags[i] = True
        else:
            for sub in re.split(r"[-/]", norm_tokens[i]):
                if sub and sub in single_set:
                    flags[i] = True
                    break

    for phrase in phrases:
        plen = len(phrase)
        if plen > length:
            continue
        for i in range(length - plen + 1):
            if all(norm_tokens[i + j] == phrase[j] or raw_tokens[i + j] == phrase[j] for j in range(plen)):
                for j in range(plen):
                    flags[i + j] = True

    return flags


def mark_keywords(
    words: List[Dict],
    global_keywords: Optional[Sequence[str]] = None,
    palette: str = "auto",
) -> List[Dict]:
    """Return shallow copies of words annotated with 'is_keyword' and 'keyword_category'."""
    if not words:
        return []

    alpha_tokens = [_clean_token(w["text"]) for w in words]
    alpha_tokens = [t for t in alpha_tokens if len(t) >= 2 and any(c.isalpha() for c in t)]
    all_caps_doc = bool(alpha_tokens) and (sum(1 for t in alpha_tokens if t.isupper()) / len(alpha_tokens) >= 0.80)

    result = [dict(w) for w in words]
    g_kw = [k for k in (global_keywords or []) if k]
    norm_pal = normalize_ass_color(palette)
    cls_palette = "palace" if norm_pal == "palace" else "auto"

    # Identify shot spans so per-shot highlight_keywords override per shot
    spans: List[Tuple[int, int, List[str], Optional[str]]] = []
    i = 0
    n = len(result)
    while i < n:
        sid = result[i].get("shot_id")
        shot_kw = result[i].get("shot_keywords")
        speaker = result[i].get("speaker")
        j = i + 1
        if sid is not None:
            while j < n and result[j].get("shot_id") == sid:
                j += 1
        else:
            j = n
        spans.append((i, j, parse_keyword_list(shot_kw), speaker))
        i = j

    for start_idx, end_idx, shot_kw, speaker in spans:
        override_kw = shot_kw if shot_kw else g_kw
        if override_kw:
            flags = _match_explicit_keywords(result, override_kw, start_idx, end_idx)
            is_sent_start = True
            for offset, is_kw in enumerate(flags):
                idx = start_idx + offset
                txt = result[idx]["text"].strip()
                result[idx]["is_keyword"] = is_kw
                if is_kw:
                    cat = classify_keyword_category(
                        txt,
                        is_sentence_start=is_sent_start,
                        all_caps_doc=all_caps_doc,
                        speaker=speaker,
                        palette=cls_palette,
                    )
                    fallback_cat = _speaker_palace_fallback(speaker) if cls_palette == "palace" else "drama_action"
                    result[idx]["keyword_category"] = cat or fallback_cat
                else:
                    result[idx]["keyword_category"] = None
                is_sent_start = bool(re.search(r"[.!?:][\"')\]”’]*$", txt))
        else:
            is_sent_start = True
            for idx in range(start_idx, end_idx):
                txt = result[idx]["text"].strip()
                cat = classify_keyword_category(
                    txt,
                    is_sentence_start=is_sent_start,
                    all_caps_doc=all_caps_doc,
                    speaker=speaker,
                    palette=cls_palette,
                )
                result[idx]["is_keyword"] = cat is not None
                result[idx]["keyword_category"] = cat
                is_sent_start = bool(re.search(r"[.!?:][\"')\]”’]*$", txt))

    return result


def resolve_keyword_color(word: Dict, keyword_color: str = DEFAULT_KEYWORD_COLOR) -> str:
    norm = normalize_ass_color(keyword_color)
    # Explicit global ASS hex color overrides palette modes
    if norm not in ("auto", "palace"):
        return norm

    # Per-speaker voice_cast.<speaker>.keyword_color override
    sp_color = word.get("speaker_keyword_color")
    if isinstance(sp_color, str) and sp_color.strip():
        sp_norm = normalize_ass_color(sp_color)
        if sp_norm not in ("auto", "palace"):
            return sp_norm

    cat = word.get("keyword_category")
    if not cat:
        cat = _speaker_palace_fallback(word.get("speaker")) if norm == "palace" else "drama_action"

    if norm == "palace":
        if cat == "drama_action":
            return CRIMSON_CORAL_COLOR
        if cat == "brand_tech":
            return PEARL_JADE_COLOR
        if cat == "money_number":
            return IMPERIAL_GOLD_COLOR
    return SEMANTIC_CATEGORY_COLORS.get(cat, CORAL_ORANGE_COLOR)


def cs_to_ass_time(cs: int) -> str:
    cs = max(0, cs)
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    c = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def cs_to_srt_time(cs: int) -> str:
    cs = max(0, cs)
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    ms = (cs % 100) * 10
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_ass_header(
    font_name: str,
    font_size: int = DEFAULT_FONT_SIZE,
    margin_v: int = DEFAULT_MARGIN_V,
    aspect_ratio: str = "9:16",
) -> str:
    """Build aspect-ratio-aware Fun Kinetic ASS header (9:16 1080x1920 or 16:9 1920x1080)."""
    preset = get_aspect_preset(aspect_ratio)
    res_x = preset["play_res_x"]
    res_y = preset["play_res_y"]
    outline = preset["outline"]
    shadow = preset["shadow"]
    margin_lr = preset["margin_lr"]
    return f"""[Script Info]
Title: Viral Animated Captions (v4.0 Fun Kinetic {aspect_ratio})
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShortsHook,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H30000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def split_long_word(word: Dict, max_chars: int) -> List[Dict]:
    """Safely split any rare single token > max_chars (preferring hyphen boundaries)."""
    text = word["text"].strip()
    if len(text) <= max_chars or max_chars < 1:
        return [word]

    pieces: List[str] = []
    if "-" in text:
        raw_parts = text.split("-")
        hyph_parts = [p + "-" for p in raw_parts[:-1]] + [raw_parts[-1]]
        cur = ""
        for part in hyph_parts:
            if not part:
                continue
            if len(part) > max_chars:
                if cur:
                    pieces.append(cur)
                    cur = ""
                for k in range(0, len(part), max_chars):
                    pieces.append(part[k : k + max_chars])
            elif cur and len(cur) + len(part) > max_chars:
                pieces.append(cur)
                cur = part
            else:
                cur += part
        if cur:
            pieces.append(cur)
    else:
        pieces = [text[k : k + max_chars] for k in range(0, len(text), max_chars)]

    if len(pieces) <= 1:
        return [word]

    start_s = float(word["start_sec"])
    end_s = max(start_s + 0.02, float(word["end_sec"]))
    total_dur = end_s - start_s
    total_len = max(1, sum(len(p) for p in pieces))
    out: List[Dict] = []
    cursor = start_s
    for idx, piece in enumerate(pieces):
        frac = len(piece) / total_len
        p_end = end_s if idx == len(pieces) - 1 else cursor + total_dur * frac
        entry = dict(word)
        entry["text"] = piece
        entry["start_sec"] = round(cursor, 4)
        entry["end_sec"] = round(max(cursor + 0.01, p_end), 4)
        out.append(entry)
        cursor = p_end
    return out


def _max_words_for_chars(max_chars: int) -> int:
    """Keep 3-word punchy chunks on 9:16 (max_chars<=22), scale smoothly for 16:9 horizontal lines."""
    if max_chars <= 22:
        return 3
    return max(3, min(8, max_chars // 5))


def chunk_words(words: List[Dict], max_words: int = 3, max_chars: int = DEFAULT_MAX_CHARS) -> List[List[Dict]]:
    """Group words into bold punchy chunks within max_chars, resetting on every shot_id/cue_idx boundary."""
    expanded: List[Dict] = []
    for w in words:
        expanded.extend(split_long_word(w, max_chars))

    chunks: List[List[Dict]] = []
    cur: List[Dict] = []
    cur_len = 0
    cur_boundary = None

    for w in expanded:
        w_text = w["text"].strip()
        if not w_text:
            continue

        # Reset on every shot_id or SRT cue_idx transition (fixes cross-cut word merging)
        boundary = (w.get("shot_id"), w.get("cue_idx"))
        if cur and boundary != cur_boundary:
            chunks.append(cur)
            cur = []
            cur_len = 0
        cur_boundary = boundary

        added_len = len(w_text) if not cur else (cur_len + 1 + len(w_text))
        if cur and added_len > max_chars:
            chunks.append(cur)
            cur = [w]
            cur_len = len(w_text)
        else:
            cur.append(w)
            cur_len = added_len

        ends_sentence = bool(re.search(r"[.!?][\"')\]”’]*$", w_text))
        if len(cur) >= max_words or ends_sentence:
            chunks.append(cur)
            cur = []
            cur_len = 0
    if cur:
        chunks.append(cur)
    return chunks


def _clamp_monotonic_events(raw: List[Tuple[int, int, str]]) -> List[Tuple[int, int, str]]:
    """Monotonically clamp event timings so every event ends <= next_start - 1cs (0.01s)."""
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


def build_zero_overlap_events(
    words: List[Dict],
    caption_style: str = "kinetic_keyword",
    keywords: Optional[Sequence[str]] = None,
    keyword_color: str = DEFAULT_KEYWORD_COLOR,
    max_chars: int = DEFAULT_MAX_CHARS,
    tilt_deg: float = DEFAULT_TILT_DEG,
) -> List[Tuple[int, int, str]]:
    """Build v4.0 Fun Kinetic or Clean Subtitle ASS events with zero overlaps."""
    if caption_style == "none":
        return []

    annotated = (
        mark_keywords(words, global_keywords=keywords, palette=keyword_color)
        if caption_style == "kinetic_keyword"
        else words
    )
    chunks = chunk_words(annotated, max_words=_max_words_for_chars(max_chars), max_chars=max_chars)
    raw: List[Tuple[int, int, str]] = []

    if caption_style == "clean_subtitle":
        for chunk in chunks:
            if not chunk:
                continue
            c_start = max(0, int(round(chunk[0]["start_sec"] * 100)))
            c_end = max(c_start + 5, int(round(chunk[-1]["end_sec"] * 100)))
            clean_text = " ".join(
                w["text"].replace("{", "").replace("}", "").strip()
                for w in chunk
                if w["text"].strip()
            )
            if not clean_text:
                continue
            sp_color = chunk[0].get("speaker_keyword_color")
            if isinstance(sp_color, str) and sp_color.strip():
                sp_norm = normalize_ass_color(sp_color)
                if sp_norm not in ("auto", "palace"):
                    tint = derive_subtle_tint(sp_norm)
                    clean_text = rf"{{\c{tint}}}" + clean_text + rf"{{\c{NEUTRAL_WORD_COLOR}}}"
            raw.append((c_start, c_end, clean_text))
        return _clamp_monotonic_events(raw)

    tilt_sign = 1.0  # Alternates +tilt_deg / -tilt_deg across keyword punchline chunks

    for chunk in chunks:
        chunk_has_kw = caption_style == "kinetic_keyword" and any(bool(w.get("is_keyword", False)) for w in chunk)
        chunk_tilt = 0.0
        if chunk_has_kw and tilt_deg > 0:
            chunk_tilt = round(tilt_deg * tilt_sign, 2)
            tilt_sign = -tilt_sign

        for active_idx, active_word in enumerate(chunk):
            w_start = max(0, int(round(active_word["start_sec"] * 100)))
            w_end = max(w_start + 5, int(round(active_word["end_sec"] * 100)))
            styled: List[str] = []

            if caption_style == "kinetic_keyword":
                tilt_tag = rf"\frz{chunk_tilt:g}" if chunk_tilt != 0.0 else ""
                if active_idx == 0:
                    # Elastic entry overshoot: 84% -> 122% -> 100%
                    line_prefix = rf"{{{tilt_tag}\fscx84\fscy84\t(0,65,\fscx122\fscy122)\t(65,155,\fscx100\fscy100)}}"
                else:
                    # Crisp mid-chunk pulse without jarring shrink
                    line_prefix = rf"{{{tilt_tag}\t(0,55,\fscx106\fscy106)\t(55,130,\fscx100\fscy100)}}"

                for idx, w in enumerate(chunk):
                    clean = w["text"].replace("{", "").replace("}", "")
                    is_kw = bool(w.get("is_keyword", False))
                    w_kw_color = resolve_keyword_color(w, keyword_color) if is_kw else ""
                    if idx == active_idx:
                        if is_kw:
                            # Keyword pop: 126% -> 108%
                            styled.append(
                                rf"{{\c{w_kw_color}\fscx126\fscy126\t(0,85,\fscx108\fscy108)}}"
                                + clean
                                + rf"{{\c{NEUTRAL_WORD_COLOR}\fscx100\fscy100}}"
                            )
                        else:
                            styled.append(
                                rf"{{\c{ACTIVE_WORD_COLOR}}}" + clean + rf"{{\c{NEUTRAL_WORD_COLOR}}}"
                            )
                    else:
                        if is_kw:
                            subtle_color = derive_subtle_tint(w_kw_color)
                            styled.append(
                                rf"{{\c{subtle_color}}}" + clean + rf"{{\c{NEUTRAL_WORD_COLOR}}}"
                            )
                        else:
                            styled.append(clean)
                raw.append((w_start, w_end, line_prefix + " ".join(styled)))
            else:
                # Classic karaoke_chunk mode (still respects shot_id flush & elastic entry)
                for idx, w in enumerate(chunk):
                    clean = w["text"].replace("{", "").replace("}", "")
                    if idx == active_idx:
                        styled.append(rf"{{\c{ACTIVE_WORD_COLOR}}}" + clean + rf"{{\c{NEUTRAL_WORD_COLOR}}}")
                    else:
                        styled.append(clean)
                prefix = r"{\fscx84\fscy84\t(0,65,\fscx122\fscy122)\t(65,155,\fscx100\fscy100)}" if active_idx == 0 else ""
                raw.append((w_start, w_end, prefix + " ".join(styled)))

    return _clamp_monotonic_events(raw)


def build_srt_cues(
    words: List[Dict],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> List[Tuple[int, int, str]]:
    """Build clean zero-overlap SRT cues from the exact same chunks as captions.ass."""
    chunks = chunk_words(words, max_words=_max_words_for_chars(max_chars), max_chars=max_chars)
    raw: List[Tuple[int, int, str]] = []
    for chunk in chunks:
        if not chunk:
            continue
        c_start = max(0, int(round(chunk[0]["start_sec"] * 100)))
        c_end = max(c_start + 5, int(round(chunk[-1]["end_sec"] * 100)))
        line = " ".join(w["text"].strip() for w in chunk if w["text"].strip())
        if line:
            raw.append((c_start, c_end, line))
    return _clamp_monotonic_events(raw)


def write_srt_file(cues: List[Tuple[int, int, str]], srt_path: Path) -> None:
    blocks: List[str] = []
    for idx, (s_cs, e_cs, text) in enumerate(cues, start=1):
        blocks.append(f"{idx}\n{cs_to_srt_time(s_cs)} --> {cs_to_srt_time(e_cs)}\n{text}")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


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


def build_plan_voice_cast(plan: Dict) -> Dict[str, Dict[str, str]]:
    """Extract normalized speaker -> {voice, rate, pitch, keyword_color} map from plan."""
    cast: Dict[str, Dict[str, str]] = {}

    speaker_voices = plan.get("speaker_voices")
    if isinstance(speaker_voices, dict):
        for role, v in speaker_voices.items():
            key = str(role).strip()
            if key and isinstance(v, str) and v.strip():
                cast[key] = {"voice": v.strip()}

    plan_cast = plan.get("voice_cast")
    if isinstance(plan_cast, dict):
        for role, cfg in plan_cast.items():
            if not isinstance(cfg, dict):
                continue
            key = str(role).strip()
            if not key:
                continue
            entry = dict(cast.get(key, {}))
            if cfg.get("voice") and str(cfg["voice"]).strip():
                entry["voice"] = str(cfg["voice"]).strip()
            if cfg.get("rate") is not None and str(cfg["rate"]).strip():
                entry["rate"] = str(cfg["rate"]).strip()
            if cfg.get("pitch") is not None and str(cfg["pitch"]).strip():
                entry["pitch"] = str(cfg["pitch"]).strip()
            if cfg.get("keyword_color") and str(cfg["keyword_color"]).strip():
                entry["keyword_color"] = str(cfg["keyword_color"]).strip()
            cast[key] = entry

    return cast


def resolve_shot_voice_params(
    shot: Dict,
    plan_voice_cast: Dict[str, Dict[str, str]],
    cli_voice: str,
    cli_rate: Optional[str],
    cli_pitch: Optional[str],
    plan_default_voice: str,
    fallback_rate: str,
    fallback_pitch: str,
    plan_default_rate: str = "",
    plan_default_pitch: str = "",
) -> Tuple[str, str, str, str, Optional[str]]:
    """Resolve (speaker, voice, rate, pitch, speaker_keyword_color) for a shot.

    Priority per field:
      1. Per-shot explicit shot.voice / shot.rate / shot.pitch
      2. plan.voice_cast[speaker] (exact or case-insensitive fallback, plus plan.speaker_voices)
      3. Explicit CLI --voice / --rate / --pitch
      4. Explicit plan.voice / plan.rate / plan.pitch when voice_cast is absent (keeps single-voice plans intact)
      5. Generic built-in defaults (only when a speaker is named on the shot)
      6. Plan default voice / format fallback rate & pitch
    """
    raw_speaker = shot.get("speaker")
    has_speaker = isinstance(raw_speaker, str) and bool(raw_speaker.strip())
    speaker = raw_speaker.strip() if has_speaker else ""

    plan_voice_cast_lower = {k.lower(): v for k, v in plan_voice_cast.items()}
    cast_cfg = (
        plan_voice_cast.get(speaker) or plan_voice_cast_lower.get(speaker.lower()) or {}
        if has_speaker
        else {}
    )
    generic_cfg = GENERIC_VOICE_CAST.get(speaker.lower(), {}) if has_speaker else {}

    shot_voice = str(shot.get("voice") or "").strip()
    cli_v = cli_voice.strip() if cli_voice else ""
    plan_v = plan_default_voice.strip() if plan_default_voice else ""
    if not plan_voice_cast and plan_v:
        voice = (
            shot_voice
            or cast_cfg.get("voice")
            or cli_v
            or plan_v
            or generic_cfg.get("voice")
            or "en-US-AvaNeural"
        )
    else:
        voice = (
            shot_voice
            or cast_cfg.get("voice")
            or cli_v
            or generic_cfg.get("voice")
            or plan_v
            or "en-US-AvaNeural"
        )

    shot_rate = str(shot["rate"]).strip() if shot.get("rate") is not None and str(shot["rate"]).strip() else ""
    cli_r = cli_rate.strip() if cli_rate else ""
    plan_r = plan_default_rate.strip() if plan_default_rate else ""
    if not plan_voice_cast and plan_r:
        rate = (
            shot_rate
            or cast_cfg.get("rate")
            or cli_r
            or plan_r
            or generic_cfg.get("rate")
            or fallback_rate
        )
    else:
        rate = (
            shot_rate
            or cast_cfg.get("rate")
            or cli_r
            or generic_cfg.get("rate")
            or plan_r
            or fallback_rate
        )

    shot_pitch = str(shot["pitch"]).strip() if shot.get("pitch") is not None and str(shot["pitch"]).strip() else ""
    cli_p = cli_pitch.strip() if cli_pitch else ""
    plan_p = plan_default_pitch.strip() if plan_default_pitch else ""
    if not plan_voice_cast and plan_p:
        pitch = (
            shot_pitch
            or cast_cfg.get("pitch")
            or cli_p
            or plan_p
            or generic_cfg.get("pitch")
            or fallback_pitch
        )
    else:
        pitch = (
            shot_pitch
            or cast_cfg.get("pitch")
            or cli_p
            or generic_cfg.get("pitch")
            or plan_p
            or fallback_pitch
        )

    sp_kw_color = str(shot.get("keyword_color") or cast_cfg.get("keyword_color") or "").strip() or None
    return (speaker or "narrator"), voice, (rate or fallback_rate), (pitch or fallback_pitch), sp_kw_color


async def synth_shot_edge_tts(
    text: str,
    voice: str,
    rate: str,
    pitch: str,
    mp3_path: Path,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> List[Dict]:
    """Synthesize a single shot with edge-tts using exponential retry backoff + atomic write."""
    try:
        import edge_tts  # type: ignore
    except ImportError as exc:
        raise RuntimeError("edge-tts is required for synthesis. Install via: pip install edge-tts") from exc

    last_exc: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        words: List[Dict] = []
        audio_bytes = 0
        tmp_mp3 = mp3_path.with_suffix(".tmp.mp3")
        try:
            comm = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=rate,
                pitch=pitch,
                boundary="WordBoundary",
            )
            with tmp_mp3.open("wb") as f:
                async for chunk in comm.stream():
                    ctype = chunk.get("type")
                    if ctype == "audio":
                        data = chunk["data"]
                        f.write(data)
                        audio_bytes += len(data)
                    elif ctype == "WordBoundary":
                        start_s = chunk["offset"] / 10_000_000.0
                        dur_s = chunk["duration"] / 10_000_000.0
                        words.append({
                            "text": chunk["text"],
                            "start_sec": round(start_s, 4),
                            "end_sec": round(start_s + dur_s, 4),
                        })
            if audio_bytes == 0:
                raise RuntimeError(f"edge-tts returned 0 audio bytes for voice={voice}")
            if not words and text.strip():
                raise RuntimeError(f"edge-tts returned 0 WordBoundary events for voice={voice}")
            os.replace(tmp_mp3, mp3_path)
            return words
        except Exception as exc:
            last_exc = exc
            if tmp_mp3.exists():
                try:
                    tmp_mp3.unlink()
                except OSError:
                    pass
            if attempt >= max_retries:
                break
            backoff = (0.8 * (2 ** attempt)) + random.uniform(0.05, 0.35)
            print(
                f"[retry {attempt + 1}/{max_retries}] {mp3_path.stem} ({voice}): {exc} -> sleeping {backoff:.2f}s",
                file=sys.stderr,
            )
            await asyncio.sleep(backoff)

    raise RuntimeError(f"Failed to synthesize {mp3_path.name} after {max_retries + 1} attempts: {last_exc}") from last_exc


async def synthesize_all_shots_async(
    jobs: List[Dict[str, Any]],
    concurrency: int = DEFAULT_CONCURRENCY,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Dict[int, Dict[str, Any]]:
    """Synthesize all shots concurrently with bounded semaphore + ffmpeg 48kHz stereo WAV decode."""
    sem = asyncio.Semaphore(max(1, concurrency))
    results: Dict[int, Dict[str, Any]] = {}

    async def _worker(job: Dict[str, Any]) -> None:
        idx = job["idx"]
        mp3_path: Path = job["mp3_path"]
        wav_path: Path = job["wav_path"]
        tts_text: str = job["tts_text"]

        if not tts_text:
            silent_dur = max(0.5, float(job["duration_sec"]))
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t", f"{silent_dur:.3f}", "-c:a", "pcm_s16le", str(wav_path),
            )
            rc = await proc.wait()
            if rc != 0:
                raise RuntimeError(f"ffmpeg silent wav failed for {job['shot_id']} (rc={rc})")
            results[idx] = {"spoken_words": [], "dur": silent_dur}
            return

        async with sem:
            spoken_words = await synth_shot_edge_tts(
                text=tts_text,
                voice=job["voice"],
                rate=job["rate"],
                pitch=job["pitch"],
                mp3_path=mp3_path,
                max_retries=max_retries,
            )

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error",
            "-i", str(mp3_path),
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
            str(wav_path),
        )
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg decode failed for {job['shot_id']} (rc={rc})")
        dur = probe_audio_duration(wav_path)
        results[idx] = {"spoken_words": spoken_words, "dur": dur}

    await asyncio.gather(*(_worker(job) for job in jobs))
    return results


def parse_act_value(raw: Any) -> Optional[int]:
    """Normalize act/chapter identifiers (1, '1', 'Act 1', 'act_01', 'chapter_1', 'act-2') to int."""
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


def build_act_summaries(shot_timings: List[Dict], plan: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """Aggregate per-act timing boundaries and merge title/bgm_intensity/shot_ids from plan.acts."""
    plan_act_meta: Dict[int, Dict[str, Any]] = {}
    shot_id_to_act: Dict[str, int] = {}
    if isinstance(plan, dict) and isinstance(plan.get("acts"), list):
        for idx, item in enumerate(plan["acts"]):
            if not isinstance(item, dict):
                continue
            raw_key = item.get("act", item.get("act_number", idx + 1))
            act_num = parse_act_value(raw_key)
            if act_num is None:
                act_num = idx + 1
            title = str(item.get("title") or item.get("act_title") or "").strip()
            bgm_intensity = str(item.get("bgm_intensity") or "").strip().lower()
            meta = plan_act_meta.setdefault(act_num, {})
            if title:
                meta["title"] = title
            if bgm_intensity:
                meta["bgm_intensity"] = bgm_intensity
            raw_sids = item.get("shot_ids")
            if isinstance(raw_sids, list):
                for sid in raw_sids:
                    sid_s = str(sid).strip()
                    if sid_s:
                        shot_id_to_act[sid_s] = act_num

    acts_map: Dict[int, Dict[str, Any]] = {}
    acts_order: List[int] = []
    for entry in shot_timings:
        act_num = parse_act_value(entry.get("act"))
        if act_num is None:
            act_num = shot_id_to_act.get(str(entry.get("shot_id", "")))
        if act_num is None:
            continue
        if act_num not in acts_map:
            acts_order.append(act_num)
            meta = plan_act_meta.get(act_num, {})
            rec: Dict[str, Any] = {
                "act": act_num,
                "title": meta.get("title", ""),
                "start_sec": entry["start_sec"],
                "end_sec": entry["end_sec"],
                "duration_sec": 0.0,
                "shot_ids": [entry["shot_id"]],
            }
            if meta.get("bgm_intensity"):
                rec["bgm_intensity"] = meta["bgm_intensity"]
            acts_map[act_num] = rec
        else:
            rec = acts_map[act_num]
            rec["start_sec"] = min(rec["start_sec"], entry["start_sec"])
            rec["end_sec"] = max(rec["end_sec"], entry["end_sec"])
            rec["shot_ids"].append(entry["shot_id"])

    summaries: List[Dict[str, Any]] = []
    for act_num in acts_order:
        rec = acts_map[act_num]
        rec["start_sec"] = round(rec["start_sec"], 4)
        rec["end_sec"] = round(rec["end_sec"], 4)
        rec["duration_sec"] = round(rec["end_sec"] - rec["start_sec"], 4)
        summaries.append(rec)
    return summaries


def format_chapter_timestamp(sec: float) -> str:
    total = max(0, int(round(sec)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def write_chapters_file(
    acts: List[Dict[str, Any]],
    chapters_path: Path,
    aspect_ratio: str = "9:16",
) -> List[str]:
    """Write YouTube chapters.txt from act summaries and warn if YouTube chapter rules aren't met."""
    lines: List[str] = []
    if not acts:
        return lines

    if len(acts) < 3:
        print(
            f"Warning: chapters.txt has only {len(acts)} chapter(s); "
            "YouTube requires at least 3 chapters starting at 0:00.",
            file=sys.stderr,
        )

    for idx, act in enumerate(acts):
        act_num = act.get("act", idx + 1)
        start_s = 0.0 if idx == 0 else float(act.get("start_sec", 0.0))
        dur_s = float(act.get("duration_sec", max(0.0, float(act.get("end_sec", 0.0)) - start_s)))
        raw_title = str(act.get("title") or "").strip()

        if aspect_ratio == "16:9":
            label = raw_title if raw_title else f"Act {act_num}"
        else:
            if not raw_title:
                label = f"Act {act_num}"
            elif re.match(r"^(?:act|chapter)\s+\d+", raw_title, flags=re.IGNORECASE):
                label = raw_title
            else:
                label = f"Act {act_num}: {raw_title}"

        if dur_s < 10.0:
            print(
                f"Warning: chapter {idx + 1} ('{label}') is {dur_s:.1f}s; "
                "YouTube requires every chapter to be at least 10 seconds long.",
                file=sys.stderr,
            )

        lines.append(f"{format_chapter_timestamp(start_s)} {label}")

    chapters_path.parent.mkdir(parents=True, exist_ok=True)
    chapters_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def lint_plan(
    plan: Dict,
    check_slop: bool = True,
    strict_slop: bool = False,
    check_tts_misread: bool = True,
) -> int:
    found = 0
    prompt_errors = 0
    tts_warnings = 0
    hygiene_warnings = 0
    pacing_sfx_warnings = 0

    if check_tts_misread:
        pronunciations = plan.get("tts_pronunciations")
        if not isinstance(pronunciations, dict):
            pronunciations = None
        for shot in plan.get("shots", []):
            sid = shot.get("shot_id", "unknown")
            raw_display = str(shot.get("dialogue_or_vo", "") or "")
            for iss in lint_display_phonetic_leaks(raw_display):
                found += 1
                print(
                    f"[{iss['code']}] {sid}: '{iss['match']}' -> '{iss['suggestion']}' ({iss['reason']})",
                    file=sys.stderr,
                )
            spoken_text = resolve_tts_text(shot, pronunciations=pronunciations, autofix=False)
            issues = lint_narration_text(spoken_text, pronunciations=pronunciations, is_tts_text=True)
            for iss in issues:
                if iss.get("severity") == "warning":
                    tts_warnings += 1
                    print(
                        f"WARNING [{iss['code']}] {sid}: '{iss['match']}' -> '{iss['suggestion']}' ({iss['reason']})",
                        file=sys.stderr,
                    )
                else:
                    found += 1
                    print(
                        f"[{iss['code']}] {sid}: '{iss['match']}' -> '{iss['suggestion']}' ({iss['reason']})",
                        file=sys.stderr,
                    )

        p_errs, p_warns = lint_plan_prompt_hygiene(plan)
        for err in p_errs:
            prompt_errors += 1
            print(
                f"[{err['code']}] {err['location']}: '{err['match']}' -> {err['hint']}",
                file=sys.stderr,
            )
        for warn in p_warns:
            hygiene_warnings += 1
            print(
                f"WARNING [{warn['code']}] {warn['location']}: '{warn['match']}' -> {warn['hint']}",
                file=sys.stderr,
            )

        for warn in lint_plan_pacing_and_sfx(plan):
            pacing_sfx_warnings += 1
            print(
                f"WARNING [{warn['code']}] {warn['location']}: '{warn['match']}' -> {warn['hint']}",
                file=sys.stderr,
            )

    slop_warnings: List[Dict[str, str]] = []
    if check_slop:
        slop_warnings = lint_plan_slop(plan)
        for w in slop_warnings:
            print(
                f"WARNING [AI_SLOP_SCRIPT] {w['shot_id']} ({w['category']}): '{w['match']}' -> {w['hint']}",
                file=sys.stderr,
            )

    slop_count = len(slop_warnings)
    if check_tts_misread and (found > 0 or prompt_errors > 0):
        parts: List[str] = []
        if found > 0:
            parts.append(f"{found} TTS misread issue(s)")
        if prompt_errors > 0:
            parts.append(f"{prompt_errors} prompt-hygiene error(s)")
        if slop_count > 0:
            parts.append(f"{slop_count} AI_SLOP_SCRIPT warning(s)")
        print(
            f"Plan lint failed: {', '.join(parts)} found. Fix shot_plan.json (or pass --autofix for TTS rules).",
            file=sys.stderr,
        )
        return 2

    if strict_slop and slop_count > 0:
        print(
            f"Narration lint failed (--strict-slop): {slop_count} AI_SLOP_SCRIPT warning(s) found.",
            file=sys.stderr,
        )
        return 2

    if check_tts_misread:
        notes: List[str] = []
        if tts_warnings > 0:
            notes.append(f"{tts_warnings} TTS pronunciation warning(s)")
        if hygiene_warnings > 0:
            notes.append(f"{hygiene_warnings} prompt-hygiene warning(s)")
        if pacing_sfx_warnings > 0:
            notes.append(f"{pacing_sfx_warnings} pacing/SFX warning(s)")
        if slop_count > 0:
            notes.append(f"{slop_count} AI_SLOP_SCRIPT warning(s)")
        if notes:
            print(f"TTS lint passed: 0 blocking errors found ({', '.join(notes)}).")
        else:
            print("TTS lint passed: 0 blocking errors found.")
    return 0


def run_synthesis(args: argparse.Namespace, plan: Dict) -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("Error: ffmpeg and ffprobe must be installed and on PATH.", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    video_format = infer_video_format(plan)
    aspect_ratio = args.aspect_ratio or str(plan.get("aspect_ratio") or "9:16").strip()
    if aspect_ratio not in ASPECT_RATIO_PRESETS:
        print(f"Error: unsupported aspect ratio '{aspect_ratio}'. Expected 9:16 or 16:9.", file=sys.stderr)
        return 2
    preset = get_aspect_preset(aspect_ratio)

    raw_plan_voice = str(plan.get("voice") or "").strip()
    raw_plan_rate = str(plan.get("rate") or "").strip()
    raw_plan_pitch = str(plan.get("pitch") or "").strip()
    default_voice = args.voice.strip() if args.voice else (raw_plan_voice or "en-US-AvaNeural")
    fallback_rate = raw_plan_rate or (DEFAULT_SHORT_RATE if video_format != "long_form" else DEFAULT_LONG_RATE)
    fallback_pitch = raw_plan_pitch or DEFAULT_PITCH
    plan_voice_cast = build_plan_voice_cast(plan)

    check_slop = not getattr(args, "no_slop_check", False)
    strict_slop = bool(getattr(args, "strict_slop", False))
    if not args.autofix:
        rc = lint_plan(plan, check_slop=check_slop, strict_slop=strict_slop, check_tts_misread=True)
        if rc != 0:
            return rc
    elif check_slop:
        rc = lint_plan(plan, check_slop=True, strict_slop=strict_slop, check_tts_misread=False)
        if rc != 0:
            return rc

    pronunciations = plan.get("tts_pronunciations")
    if not isinstance(pronunciations, dict):
        pronunciations = None

    all_words: List[Dict] = []
    shot_timings: List[Dict] = []
    resolved_cast_used: Dict[str, Dict[str, str]] = {}
    cursor = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        shots = plan.get("shots", [])
        jobs: List[Dict[str, Any]] = []

        for idx, shot in enumerate(shots):
            sid = str(shot["shot_id"])
            display_text = str(shot.get("dialogue_or_vo", "") or "").strip()
            tts_spoken_text = resolve_tts_text(shot, pronunciations=pronunciations, autofix=args.autofix)
            if not display_text and tts_spoken_text:
                display_text = tts_spoken_text

            speaker, shot_voice, shot_rate, shot_pitch, sp_kw_color = resolve_shot_voice_params(
                shot=shot,
                plan_voice_cast=plan_voice_cast,
                cli_voice=args.voice,
                cli_rate=args.rate,
                cli_pitch=args.pitch,
                plan_default_voice=raw_plan_voice,
                fallback_rate=fallback_rate,
                fallback_pitch=fallback_pitch,
                plan_default_rate=raw_plan_rate,
                plan_default_pitch=raw_plan_pitch,
            )
            cast_rec: Dict[str, str] = {"voice": shot_voice, "rate": shot_rate, "pitch": shot_pitch}
            if sp_kw_color:
                cast_rec["keyword_color"] = sp_kw_color
            resolved_cast_used.setdefault(speaker, cast_rec)

            jobs.append({
                "idx": idx,
                "shot": shot,
                "shot_id": sid,
                "display_text": display_text,
                "tts_text": tts_spoken_text,
                "speaker": speaker,
                "voice": shot_voice,
                "rate": shot_rate,
                "pitch": shot_pitch,
                "speaker_keyword_color": sp_kw_color,
                "duration_sec": float(shot.get("duration_sec", 2.0)),
                "mp3_path": tmp_dir / f"{idx:04d}_{sid}.mp3",
                "wav_path": tmp_dir / f"{idx:04d}_{sid}.wav",
            })

        synth_results = asyncio.run(
            synthesize_all_shots_async(
                jobs,
                concurrency=args.concurrency,
                max_retries=args.max_retries,
            )
        )

        gap_cache: Dict[int, Path] = {}
        concat_lines: List[str] = []

        def _get_gap_wav(gap_sec: float) -> Path:
            gap_ms = int(round(gap_sec * 1000))
            if gap_ms not in gap_cache:
                gpath = tmp_dir / f"gap_{gap_ms}ms.wav"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-t", f"{gap_ms / 1000.0:.3f}", "-c:a", "pcm_s16le", str(gpath),
                    ],
                    check=True,
                )
                gap_cache[gap_ms] = gpath
            return gap_cache[gap_ms]

        for job in jobs:
            idx = job["idx"]
            shot = job["shot"]
            sid = job["shot_id"]
            display_text = job["display_text"]
            tts_spoken_text = job["tts_text"]
            speaker = job["speaker"]
            shot_voice = job["voice"]
            sp_kw_color = job["speaker_keyword_color"]
            wav_path = job["wav_path"]

            res = synth_results[idx]
            spoken_words = res["spoken_words"]
            dur = float(res["dur"])

            words = align_words_to_display(spoken_words, display_text, dur) if tts_spoken_text else []

            shot_start = cursor
            shot_end = cursor + dur
            shot_kw = parse_keyword_list(shot.get("highlight_keywords"))

            shifted_words = []
            for w in words:
                sw = {
                    "text": w["text"],
                    "start_sec": round(shot_start + w["start_sec"], 4),
                    "end_sec": round(shot_start + w["end_sec"], 4),
                }
                shifted_words.append(sw)
                cw = dict(sw)
                cw["shot_id"] = sid
                cw["speaker"] = speaker
                if sp_kw_color:
                    cw["speaker_keyword_color"] = sp_kw_color
                if shot_kw:
                    cw["shot_keywords"] = shot_kw
                all_words.append(cw)

            raw_post_gap = shot.get("post_gap_sec")
            is_silent_shot = not bool(tts_spoken_text)
            if raw_post_gap is not None:
                post_gap = max(0.0, float(raw_post_gap))
            elif is_silent_shot or idx >= len(jobs) - 1:
                post_gap = 0.0
            else:
                post_gap = max(0.0, float(args.gap))

            norm_act = parse_act_value(shot.get("act"))
            timing_entry: Dict[str, Any] = {
                "shot_id": sid,
                "act": norm_act if norm_act is not None else shot.get("act"),
                "speaker": speaker,
                "voice": shot_voice,
                "rate": job["rate"],
                "pitch": job["pitch"],
                "start_sec": round(shot_start, 4),
                "end_sec": round(shot_end, 4),
                "duration_sec": round(dur + post_gap, 4),
                "spoken_duration_sec": round(dur, 4),
                "post_gap_sec": round(post_gap, 4),
                "motion_preset": shot.get("motion_preset", "zoom_in"),
                "flash_in": bool(shot.get("flash_in", False)),
                "sfx_type": shot.get("sfx_type", "none"),
                "sfx_offset_sec": float(shot.get("sfx_offset_sec", 0.0)),
                "sfx_gain_db": float(shot.get("sfx_gain_db", -3.0)),
                "dialogue_or_vo": display_text,
            }
            for opt_key in ("production_mode", "bgm_cue", "bgm_drop_sec", "sfx_pan", "focal_anchor", "fade_in", "fade_out"):
                if opt_key in shot and shot[opt_key] is not None:
                    timing_entry[opt_key] = shot[opt_key]
            if tts_spoken_text and tts_spoken_text != display_text:
                timing_entry["tts_text"] = tts_spoken_text
            timing_entry["words"] = shifted_words
            shot_timings.append(timing_entry)

            concat_lines.append(f"file '{wav_path}'")
            cursor = shot_end
            if post_gap > 0.0005:
                gap_wav = _get_gap_wav(post_gap)
                concat_lines.append(f"file '{gap_wav}'")
                cursor += post_gap

        concat_txt = tmp_dir / "concat.txt"
        concat_txt.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        vo_wav = out_dir / "voiceover.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(vo_wav)],
            check=True,
        )

    total_dur = round(cursor, 4)
    avg_shot_len = round(total_dur / max(1, len(shot_timings)), 3)
    act_summaries = build_act_summaries(shot_timings, plan=plan)
    timings_payload: Dict[str, Any] = {
        "slug": plan.get("slug", ""),
        "video_format": video_format,
        "aspect_ratio": aspect_ratio,
        "voice": default_voice,
        "voice_cast": resolved_cast_used,
        "total_duration_sec": total_dur,
        "shot_count": len(shot_timings),
        "avg_shot_length_sec": avg_shot_len,
        "acts": act_summaries,
        "shots": shot_timings,
    }
    if plan.get("ending_style"):
        timings_payload["ending_style"] = str(plan["ending_style"]).strip()
    amb_bed = str(plan.get("ambience_bed") or "").strip()
    if amb_bed:
        timings_payload["ambience_bed"] = amb_bed
    timings_path = out_dir / "shot_timings.json"
    timings_path.write_text(json.dumps(timings_payload, indent=2) + "\n", encoding="utf-8")

    if video_format == "short":
        if total_dur > 59.0 and not bool(getattr(args, "allow_over_60s", False)):
            print(
                f"[SHORT_OVER_60S] Short runtime {total_dur:.2f}s exceeds 59.0s YouTube Shorts cap. "
                "Trim the script, raise plan.rate, or pass --allow-over-60s to override.",
                file=sys.stderr,
            )
            return 2
        if total_dur > 48.0 or avg_shot_len > 3.8:
            print(
                f"WARNING [PACING_OVERRUN] Short runtime {total_dur:.2f}s (avg {avg_shot_len:.2f}s/shot) "
                "exceeds 48.0s / 3.8s target ceiling.",
                file=sys.stderr,
            )

    chapters_path: Optional[Path] = None
    if act_summaries:
        chapters_path = out_dir / "chapters.txt"
        write_chapters_file(act_summaries, chapters_path, aspect_ratio=aspect_ratio)

    global_keywords = parse_keyword_list(args.keywords) or parse_keyword_list(plan.get("highlight_keywords"))
    caption_style = args.caption_style or plan.get("caption_style") or "kinetic_keyword"
    plan_max_chars = (plan.get("fun_pacing_gate") or {}).get("max_caption_chars")
    explicit_caption_max = (
        plan.get("max_caption_chars")
        if plan.get("max_caption_chars") is not None
        else plan.get("caption_max_chars")
    )
    if args.max_chars is not None:
        max_chars = args.max_chars
    elif explicit_caption_max is not None:
        max_chars = int(explicit_caption_max)
    elif aspect_ratio == "16:9":
        max_chars = (
            int(plan_max_chars)
            if (plan_max_chars is not None and int(plan_max_chars) > DEFAULT_MAX_CHARS)
            else int(preset["max_chars"])
        )
    else:
        max_chars = int(plan_max_chars) if plan_max_chars is not None else int(preset["max_chars"])
    if max_chars < 1:
        max_chars = preset["max_chars"]

    plan_font_size = plan.get("caption_font_size")
    font_size = args.font_size if args.font_size is not None else int(plan_font_size or preset["font_size"])
    plan_margin_v = plan.get("caption_margin_v")
    margin_v = args.margin_v if args.margin_v is not None else int(plan_margin_v if plan_margin_v is not None else preset["margin_v"])
    plan_tilt = plan.get("caption_tilt") if plan.get("caption_tilt") is not None else plan.get("tilt")
    if args.tilt is not None:
        tilt_deg = args.tilt
    elif caption_style == "clean_subtitle":
        tilt_deg = 0.0
    elif plan_tilt is not None:
        tilt_deg = float(plan_tilt)
    else:
        tilt_deg = float(preset["tilt"])
    keyword_color = args.keyword_color or plan.get("caption_keyword_color") or DEFAULT_KEYWORD_COLOR

    font_name = detect_bold_font(args.font)
    ass_path: Optional[Path] = None
    if caption_style == "none":
        events: List[Tuple[int, int, str]] = []
    else:
        events = build_zero_overlap_events(
            all_words,
            caption_style=caption_style,
            keywords=global_keywords,
            keyword_color=keyword_color,
            max_chars=max_chars,
            tilt_deg=tilt_deg,
        )
        ass_lines = [build_ass_header(font_name, font_size=font_size, margin_v=margin_v, aspect_ratio=aspect_ratio).rstrip()]
        for s, e, text in events:
            ass_lines.append(f"Dialogue: 0,{cs_to_ass_time(s)},{cs_to_ass_time(e)},ShortsHook,,0,0,0,,{text}")
        ass_path = out_dir / "captions.ass"
        ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")

    srt_path: Optional[Path] = None
    if args.export_srt or aspect_ratio == "16:9":
        srt_cues = build_srt_cues(all_words, max_chars=max_chars)
        srt_path = out_dir / "captions.srt"
        write_srt_file(srt_cues, srt_path)

    act_info = f" across {len(act_summaries)} act(s)" if act_summaries else ""
    print(
        f"Synthesized {len(shot_timings)} shots{act_info} ({total_dur:.2f}s, {video_format} {aspect_ratio}, "
        f"font='{font_name}' {font_size}px, margin_v={margin_v}, style={caption_style}, "
        f"max_chars={max_chars}, kw_color={keyword_color}, tilt=+/-{tilt_deg:g}deg, "
        f"{len(events)} caption events)"
    )
    print(f"  VO:       {out_dir / 'voiceover.wav'}")
    print(f"  Timings:  {timings_path}")
    if ass_path is not None:
        print(f"  ASS:      {ass_path}")
    else:
        print("  ASS:      (skipped; caption_style=none)")
    if srt_path is not None:
        print(f"  SRT:      {srt_path}")
    if chapters_path is not None:
        print(f"  Chapters: {chapters_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synthesize voiceover with edge-tts, lint narration scripts, and build v4.0 Fun Kinetic ASS/SRT captions."
    )
    parser.add_argument("--lint-plan", help="Run Pre-TTS narration, prompt-hygiene, pacing, and slop linter on shot_plan.json and exit.")
    parser.add_argument("--lint-metadata", help="Lint a JSON packaging/plan file or Markdown packaging.md for metadata AI slop and illegal title chars.")
    parser.add_argument("--shot-plan", help="Path to shot_plan.json for full audio-first synthesis.")
    parser.add_argument("--out-dir", help="Output directory for voiceover.wav, shot_timings.json, captions.ass.")
    parser.add_argument(
        "--aspect-ratio",
        choices=("9:16", "16:9"),
        default=None,
        help="Target aspect ratio for caption layout (default: plan.aspect_ratio or 9:16).",
    )
    parser.add_argument("--voice", default="", help="Override/fallback neural voice (default: plan.voice or en-US-AvaNeural).")
    parser.add_argument(
        "--rate",
        default=None,
        help=(
            f"Speech rate adjustment (default: plan.rate or {DEFAULT_SHORT_RATE.replace('%', '%%')} for Shorts, "
            f"{DEFAULT_LONG_RATE.replace('%', '%%')} for long-form unless set by voice_cast/shot)."
        ),
    )
    parser.add_argument(
        "--pitch",
        default=None,
        help=f"Pitch adjustment (default: plan.pitch or {DEFAULT_PITCH} unless set by voice_cast/shot).",
    )
    parser.add_argument(
        "--gap",
        type=float,
        default=DEFAULT_GAP_SEC,
        help=f"Default inter-shot silence gap in seconds when shot lacks post_gap_sec (default: {DEFAULT_GAP_SEC}).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max concurrent edge-tts synthesis requests (default: {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Max exponential-backoff retries per shot on transient edge-tts error (default: {DEFAULT_MAX_RETRIES}).",
    )
    parser.add_argument("--font", default="", help="Override caption font family (auto-detected via fc-match).")
    parser.add_argument(
        "--font-size",
        type=int,
        default=None,
        help="Caption font size in px (default: plan.caption_font_size or 86 for 9:16 / 62 for 16:9).",
    )
    parser.add_argument(
        "--margin-v",
        type=int,
        default=None,
        help="ASS MarginV vertical offset from bottom in px (default: plan.caption_margin_v or 420 for 9:16 / 116 for 16:9).",
    )
    parser.add_argument(
        "--tilt",
        type=float,
        default=None,
        help="Alternating tilt in degrees on keyword lines; 0 disables (default: 2.5 for 9:16 / 0.0 for 16:9).",
    )
    parser.add_argument(
        "--caption-style",
        choices=("kinetic_keyword", "karaoke_chunk", "clean_subtitle", "none"),
        default=None,
        help="Caption animation style (default: plan.caption_style or kinetic_keyword).",
    )
    parser.add_argument(
        "--keywords",
        default="",
        help="Optional comma-separated keywords to highlight (overrides auto-detection).",
    )
    parser.add_argument(
        "--keyword-color",
        default=None,
        help="Keyword color: 'auto' (v3.1 neon), 'palace' (Imperial Gold/Crimson/Pearl Jade), or ASS BGR hex.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="Maximum visible characters per caption line (default: plan max_caption_chars or 18 for 9:16 / 36 for 16:9).",
    )
    parser.add_argument(
        "--export-srt",
        action="store_true",
        help="Also write clean captions.srt alongside captions.ass (enabled automatically for 16:9).",
    )
    parser.add_argument("--autofix", action="store_true", help="Auto-rewrite TTS_MISREAD patterns during synthesis.")
    parser.add_argument(
        "--allow-over-60s",
        action="store_true",
        help="Allow Short-Form synthesis to exceed 59.0s instead of failing with SHORT_OVER_60S.",
    )
    parser.add_argument(
        "--strict-slop",
        action="store_true",
        help="Fail with exit code 2 if any AI_SLOP_SCRIPT warning is found.",
    )
    parser.add_argument(
        "--no-slop-check",
        action="store_true",
        help="Skip AI_SLOP_SCRIPT narration warnings.",
    )
    args = parser.parse_args()

    if args.max_chars is not None and args.max_chars < 1:
        parser.error("--max-chars must be >= 1")
    if args.font_size is not None and args.font_size < 12:
        parser.error("--font-size must be >= 12")
    if args.margin_v is not None and args.margin_v < 0:
        parser.error("--margin-v must be >= 0")
    if args.tilt is not None and args.tilt < 0:
        parser.error("--tilt must be >= 0")
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    if args.max_retries < 0:
        parser.error("--max-retries must be >= 0")

    if args.lint_metadata:
        meta_path = Path(args.lint_metadata)
        if not meta_path.exists():
            print(f"Error: --lint-metadata path not found: {meta_path}", file=sys.stderr)
            return 2
        warnings = lint_metadata_file(meta_path)
        for w in warnings:
            print(
                f"WARNING [AI_SLOP_SCRIPT] {w['shot_id']} ({w['category']}): '{w['match']}' -> {w['hint']}",
                file=sys.stderr,
            )
        if args.strict_slop and warnings:
            print(
                f"Metadata lint failed (--strict-slop): {len(warnings)} issue(s) found.",
                file=sys.stderr,
            )
            return 2
        if warnings:
            print(f"Metadata lint finished with {len(warnings)} warning(s).")
        else:
            print("Metadata lint passed: 0 issues found.")
        return 0

    if args.lint_plan:
        plan = json.loads(Path(args.lint_plan).read_text(encoding="utf-8"))
        return lint_plan(
            plan,
            check_slop=not args.no_slop_check,
            strict_slop=args.strict_slop,
            check_tts_misread=True,
        )

    if args.shot_plan:
        if not args.out_dir:
            parser.error("--out-dir is required with --shot-plan")
        plan = json.loads(Path(args.shot_plan).read_text(encoding="utf-8"))
        return run_synthesis(args, plan)

    parser.error("Provide --lint-plan, --lint-metadata, or --shot-plan")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
