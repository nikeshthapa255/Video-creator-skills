#!/usr/bin/env python3
"""Synthesize voiceover with edge-tts, lint narration scripts, and build v3.1 Fun Kinetic ASS captions.

Features:
  1. Pre-TTS Narration Linter (--lint-plan): catches TTS_MISREAD errors such as
     "$700 PS5 Pro" (-> "700-dollar PS5 Pro") and "no 60 FPS" (-> "no sixty FPS"),
     plus soft AI_SLOP_SCRIPT warnings (--strict-slop escalates to exit 2;
     --no-slop-check disables slop checks).
  2. Phonetic-vs-Display Separation: supports top-level tts_pronunciations and
     per-shot tts_text so TTS speaks phonetic forms ("A W S", "GTA Six", "nine-one-one")
     while captions render clean display English ("AWS", "GTA 6", "911").
  3. Audio-First Synthesis (edge-tts with boundary="WordBoundary"): generates per-shot
     VO clips, concatenates voiceover.wav (48 kHz stereo), writes exact shot_timings.json.
  4. v3.1 Fun Kinetic Captions & Horizontal Safety:
     - 86px bold sans font (auto-detected via fc-match), Outline=8, Shadow=4.5, #30000000 shadow.
     - Lower-third safe placement (--margin-v 420: clear of character torsos and Shorts UI).
     - Punchy 18-char chunks (--max-chars 18) reset on every shot_id/cue_idx cut.
     - Elastic entry overshoot (84% -> 122% -> 100%) + 126% -> 108% keyword pop + alternating
       +/-2.5 deg micro-tilt on keyword punchlines.
     - Semantic neon keyword palette (--keyword-color auto): Neon Lime (money/numbers),
       Fiery Coral-Orange (drama/danger/action), Electric Cyan (brands/tech/AI), Bright Yellow
       (active spoken word).
     - Zero-overlap clamping (next_start - 0.01s) with overlap assertion.

Examples:
  python3 tts_and_captions_v2.py --lint-plan shot_plan.json
  python3 tts_and_captions_v2.py --lint-plan shot_plan.json --strict-slop
  python3 tts_and_captions_v2.py --shot-plan shot_plan.json --out-dir audio --voice en-US-AvaNeural
  python3 tts_and_captions_v2.py --shot-plan shot_plan.json --out-dir audio --caption-style kinetic_keyword --font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto
"""

import argparse
import asyncio
import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

# v3.1 Fun Kinetic defaults
DEFAULT_FONT_SIZE = 86
DEFAULT_MAX_CHARS = 18
DEFAULT_MARGIN_V = 420
DEFAULT_TILT_DEG = 2.5
DEFAULT_KEYWORD_COLOR = "auto"

# ASS BGR hex palette (v3.1 Semantic Neon)
NEON_LIME_COLOR = "&H33FF66&"      # Money / numbers / stats
CORAL_ORANGE_COLOR = "&H365AFF&"   # Drama / danger / action
ELECTRIC_CYAN_COLOR = "&HFFE500&"  # Brands / tech / AI
ACTIVE_WORD_COLOR = "&H00FFFF&"    # Bright yellow active spoken word
NEUTRAL_WORD_COLOR = "&H00FFFFFF&" # Crisp white neutral word

SEMANTIC_CATEGORY_COLORS = {
    "money_number": NEON_LIME_COLOR,
    "drama_action": CORAL_ORANGE_COLOR,
    "brand_tech": ELECTRIC_CYAN_COLOR,
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
    "urgent", "emergency", "stolen", "steal", "destroy", "destroyed", "wipe", "wiped",
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
}


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


SLOP_CLUSTER_TERMS = {
    "crucial", "pivotal", "paramount", "robust", "multifaceted",
    "meticulous", "realm", "garner", "bolster", "revolutionize",
    "groundbreaking", "synergy", "leverage",
}

# Tuple format: (category, compiled_regex, hint, last_shot_only)
# Every regex uses capturing group 1 for the reported match so leading sentence
# punctuation is excluded from warnings.
SLOP_PATTERNS: List[Tuple[str, re.Pattern, str, bool]] = [
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
    ),
    (
        "fake_contrast",
        re.compile(
            r"\b((?:this|that|it)\s+(?:doesn['’]t|does\s+not)\s+(?:just\s+)?mean\b[^.!?]{2,60}[.!?;]\s*it\s+means)\b",
            flags=re.IGNORECASE,
        ),
        "State what happened directly instead of a 'not just X, it's Y' contrast.",
        False,
    ),
    (
        "fake_contrast",
        re.compile(
            r"(?:^|[.!?]\s+)(forget\s+[\w\s'’-]{2,35}(?:[—–:]|\s+-{1,2}\s+))",
            flags=re.IGNORECASE,
        ),
        "State what happened directly instead of an ad-copy 'Forget X' contrast.",
        False,
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
    ),
    (
        "throat_clearing",
        re.compile(
            r"\b((?:here['’]s|here\s+is)\s+the\s+(?:thing|catch|kicker"
            r"|wild\s+part|crazy\s+part|scary\s+part|truth))\b",
            flags=re.IGNORECASE,
        ),
        "Cut the warm-up phrase and open directly on the fact or action.",
        False,
    ),
    (
        "colon_reveal",
        re.compile(
            r"\b((?:the\s+(?:best|worst|crazy|wild|scary|funny)\s+part"
            r"|the\s+(?:secret|catch|twist|truth|reality)"
            r"|one\s+thing\s+is\s+clear|plot\s+twist)\s*:)",
            flags=re.IGNORECASE,
        ),
        "Fold the point into a direct spoken sentence instead of a staged colon reveal.",
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
    ),
    (
        "dramatic_filler",
        re.compile(
            r"(?:^|[.!?]\s+)((?:total|complete|utter|pure)\s+(?:\w+\s+){0,2}(?:disaster|chaos|madness|nightmare)[.!]?)\s*$",
            flags=re.IGNORECASE,
        ),
        "Cut the dramatic echo fragment; let the concrete fact carry the punch.",
        False,
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
    ),
    (
        "wrapup_summary",
        re.compile(
            r"\b(in\s+summary|to\s+sum\s+up|in\s+conclusion|all\s+in\s+all"
            r"|at\s+the\s+end\s+of\s+the\s+day|only\s+time\s+will\s+tell)\b",
            flags=re.IGNORECASE,
        ),
        "Cut the wrap-up phrase; end on a concrete beat that loops into Shot 1.",
        False,
    ),
    (
        "wrapup_summary",
        re.compile(
            r"^\s*((?:overall|ultimately)\s*,)",
            flags=re.IGNORECASE,
        ),
        "Cut the wrap-up phrase; end on a concrete beat that loops into Shot 1.",
        True,
    ),
    (
        "wrapup_summary",
        re.compile(
            r"(?:^|[.!?]\s+)((?:which\s+is|and\s+that['’]s)\s+(?:the\s+exact\s+reason|why)\s*(?:\.{0,3}|…))\s*$",
            flags=re.IGNORECASE,
        ),
        "Avoid overusing flat 'Which is why...' loop bridges; end on a concrete beat or rotate loop callbacks.",
        True,
    ),
]


def _extract_slop_cluster_terms(text: str) -> List[str]:
    terms: List[str] = []
    seen = set()
    for tok in re.findall(r"[a-z]+", text.lower()):
        if tok in SLOP_CLUSTER_TERMS and tok not in seen:
            seen.add(tok)
            terms.append(tok)
    return terms


def lint_slop_text(text: str, is_last_shot: bool = False) -> List[Dict[str, str]]:
    """Detect low-false-positive AI-slop patterns in spoken Shorts narration."""
    if not text or not text.strip():
        return []

    issues: List[Dict[str, str]] = []
    seen_spans: List[Tuple[int, int]] = []

    for category, pattern, hint, last_shot_only in SLOP_PATTERNS:
        if last_shot_only and not is_last_shot:
            continue
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


def lint_plan_slop(plan: Dict) -> List[Dict[str, str]]:
    """Run AI_SLOP_SCRIPT checks across every shot plus a plan-wide buzzword cluster check."""
    shots = plan.get("shots", []) or []
    warnings: List[Dict[str, str]] = []
    plan_cluster_terms: List[str] = []
    seen_plan_terms = set()
    shot_cluster_warned = False

    for idx, shot in enumerate(shots):
        sid = str(shot.get("shot_id", f"shot_{idx + 1:02d}"))
        text = str(shot.get("dialogue_or_vo", "") or "")
        is_last = idx == len(shots) - 1
        for iss in lint_slop_text(text, is_last_shot=is_last):
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

    return warnings


def apply_tts_pronunciations(text: str, pronunciations: Optional[Dict[str, str]]) -> str:
    """Apply top-level display -> phonetic TTS overrides longest-key-first with safe boundaries."""
    if not text or not isinstance(pronunciations, dict) or not pronunciations:
        return text

    clean_map: Dict[str, str] = {}
    for raw_k, raw_v in pronunciations.items():
        if isinstance(raw_k, str) and isinstance(raw_v, str) and raw_k.strip():
            clean_map[raw_k] = raw_v
    if not clean_map:
        return text

    sorted_keys = sorted(clean_map.keys(), key=lambda k: (-len(k), k))
    pattern = re.compile(
        "|".join(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])" for k in sorted_keys)
    )
    return pattern.sub(lambda m: clean_map[m.group(0)], text)


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
    if not c or c.lower() == "auto":
        return "auto"
    if re.fullmatch(r"[0-9A-Fa-f]{6}", c):
        return f"&H{c.upper()}&"
    if c.upper().startswith("&H"):
        body = c[2:].rstrip("&").upper()
        return f"&H{body}&"
    return CORAL_ORANGE_COLOR


def derive_subtle_tint(keyword_color: str) -> str:
    """Blend keyword ASS BGR color with white (55/45) so inactive keywords stay subtly tinted."""
    norm = normalize_ass_color(keyword_color)
    if norm == "auto":
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


def classify_keyword_category(
    raw_text: str,
    is_sentence_start: bool = False,
    all_caps_doc: bool = False,
) -> Optional[str]:
    """Classify a token into v3.1 semantic neon categories, or None if not an auto-keyword."""
    stripped = raw_text.strip(".,!?;:\"'()[]{}<>…—–")
    if not stripped:
        return None

    # 1. Money / numbers / stats -> Neon Lime
    if any(ch.isdigit() for ch in stripped) or "$" in stripped or "%" in stripped:
        return "money_number"

    cleaned = _clean_token(stripped)
    lower = cleaned.lower()
    if not lower:
        return None

    parts = [p for p in re.split(r"[-/]", lower) if p]
    if lower in NUMBER_WORDS or any(p in NUMBER_WORDS for p in parts):
        return "money_number"

    # 2. Drama / danger / action -> Fiery Coral-Orange
    if lower in POWER_DRAMA_WORDS or any(p in POWER_DRAMA_WORDS for p in parts):
        return "drama_action"

    # 3. Brands / tech / AI -> Electric Cyan
    if lower in MAJOR_TECH_BRANDS or any(p in MAJOR_TECH_BRANDS for p in parts):
        return "brand_tech"

    # 4. Capitalized non-stopword proper nouns/brands/acronyms mid-sentence -> Electric Cyan
    if not is_sentence_start and not all_caps_doc and len(cleaned) >= 2:
        if cleaned[0].isupper() and lower not in PROPER_STOPWORDS:
            return "brand_tech"

    return None


def is_auto_keyword(raw_text: str, is_sentence_start: bool, all_caps_doc: bool = False) -> bool:
    """Auto-detect high-impact keywords (numbers/currency/%, drama/action, brands/tech)."""
    return classify_keyword_category(raw_text, is_sentence_start=is_sentence_start, all_caps_doc=all_caps_doc) is not None


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


def mark_keywords(words: List[Dict], global_keywords: Optional[Sequence[str]] = None) -> List[Dict]:
    """Return shallow copies of words annotated with 'is_keyword' and 'keyword_category'."""
    if not words:
        return []

    alpha_tokens = [_clean_token(w["text"]) for w in words]
    alpha_tokens = [t for t in alpha_tokens if len(t) >= 2 and any(c.isalpha() for c in t)]
    all_caps_doc = bool(alpha_tokens) and (sum(1 for t in alpha_tokens if t.isupper()) / len(alpha_tokens) >= 0.80)

    result = [dict(w) for w in words]
    g_kw = [k for k in (global_keywords or []) if k]

    # Identify shot spans so per-shot highlight_keywords override per shot
    spans: List[Tuple[int, int, List[str]]] = []
    i = 0
    n = len(result)
    while i < n:
        sid = result[i].get("shot_id")
        shot_kw = result[i].get("shot_keywords")
        j = i + 1
        if sid is not None:
            while j < n and result[j].get("shot_id") == sid:
                j += 1
        else:
            j = n
        spans.append((i, j, parse_keyword_list(shot_kw)))
        i = j

    for start_idx, end_idx, shot_kw in spans:
        override_kw = shot_kw if shot_kw else g_kw
        if override_kw:
            flags = _match_explicit_keywords(result, override_kw, start_idx, end_idx)
            is_sent_start = True
            for offset, is_kw in enumerate(flags):
                idx = start_idx + offset
                txt = result[idx]["text"].strip()
                result[idx]["is_keyword"] = is_kw
                if is_kw:
                    cat = classify_keyword_category(txt, is_sentence_start=is_sent_start, all_caps_doc=all_caps_doc)
                    result[idx]["keyword_category"] = cat or "drama_action"
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
                )
                result[idx]["is_keyword"] = cat is not None
                result[idx]["keyword_category"] = cat
                is_sent_start = bool(re.search(r"[.!?:][\"')\]”’]*$", txt))

    return result


def resolve_keyword_color(word: Dict, keyword_color: str = DEFAULT_KEYWORD_COLOR) -> str:
    norm = normalize_ass_color(keyword_color)
    if norm != "auto":
        return norm
    cat = word.get("keyword_category") or "drama_action"
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


def build_ass_header(
    font_name: str,
    font_size: int = DEFAULT_FONT_SIZE,
    margin_v: int = DEFAULT_MARGIN_V,
) -> str:
    """Build v3.1 Fun Kinetic ASS header (86px bold, Outline=8, Shadow=4.5, #30000000, MarginV=420)."""
    return f"""[Script Info]
Title: Viral Animated Short Captions (v3.1 Fun Kinetic)
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShortsHook,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H30000000,-1,0,0,0,100,100,0,0,1,8,4.5,2,50,50,{margin_v},1

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


def build_zero_overlap_events(
    words: List[Dict],
    caption_style: str = "kinetic_keyword",
    keywords: Optional[Sequence[str]] = None,
    keyword_color: str = DEFAULT_KEYWORD_COLOR,
    max_chars: int = DEFAULT_MAX_CHARS,
    tilt_deg: float = DEFAULT_TILT_DEG,
) -> List[Tuple[int, int, str]]:
    """Build v3.1 Fun Kinetic ASS events with elastic bounce, semantic neon colors, +/-2.5 deg micro-tilt, and zero overlaps."""
    annotated = mark_keywords(words, global_keywords=keywords) if caption_style == "kinetic_keyword" else words
    chunks = chunk_words(annotated, max_words=3, max_chars=max_chars)
    raw: List[Tuple[int, int, str]] = []
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
                # Classic karaoke_chunk mode (still respects v3.1 shot_id flush & elastic entry)
                for idx, w in enumerate(chunk):
                    clean = w["text"].replace("{", "").replace("}", "")
                    if idx == active_idx:
                        styled.append(rf"{{\c{ACTIVE_WORD_COLOR}}}" + clean + rf"{{\c{NEUTRAL_WORD_COLOR}}}")
                    else:
                        styled.append(clean)
                prefix = r"{\fscx84\fscy84\t(0,65,\fscx122\fscy122)\t(65,155,\fscx100\fscy100)}" if active_idx == 0 else ""
                raw.append((w_start, w_end, prefix + " ".join(styled)))

    # Monotonic clamp: every event ends <= next_start - 1cs (0.01s)
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


def lint_plan(
    plan: Dict,
    check_slop: bool = True,
    strict_slop: bool = False,
    check_tts_misread: bool = True,
) -> int:
    found = 0
    if check_tts_misread:
        pronunciations = plan.get("tts_pronunciations")
        if not isinstance(pronunciations, dict):
            pronunciations = None
        for shot in plan.get("shots", []):
            sid = shot.get("shot_id", "unknown")
            spoken_text = resolve_tts_text(shot, pronunciations=pronunciations, autofix=False)
            issues = lint_narration_text(spoken_text)
            for iss in issues:
                found += 1
                print(
                    f"[TTS_MISREAD] {sid}: '{iss['match']}' -> '{iss['suggestion']}' ({iss['reason']})",
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
    if check_tts_misread and found > 0:
        extra = f" and {slop_count} AI_SLOP_SCRIPT warning(s)" if slop_count > 0 else ""
        print(
            f"TTS lint failed: {found} misread issue(s){extra} found. Fix shot_plan.json or pass --autofix.",
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
        if slop_count > 0:
            print(f"TTS lint passed: 0 misread risks found ({slop_count} AI_SLOP_SCRIPT warning(s)).")
        else:
            print("TTS lint passed: 0 misread risks found.")
    return 0


def run_synthesis(args: argparse.Namespace, plan: Dict) -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("Error: ffmpeg and ffprobe must be installed and on PATH.", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = args.voice or plan.get("voice") or "en-US-AvaNeural"

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
    cursor = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        concat_lines: List[str] = []

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

        shots = plan.get("shots", [])
        for idx, shot in enumerate(shots):
            sid = shot["shot_id"]
            display_text = str(shot.get("dialogue_or_vo", "") or "").strip()
            tts_spoken_text = resolve_tts_text(shot, pronunciations=pronunciations, autofix=args.autofix)
            if not display_text and tts_spoken_text:
                display_text = tts_spoken_text

            mp3_path = tmp_dir / f"{sid}.mp3"
            wav_path = tmp_dir / f"{sid}.wav"

            if not tts_spoken_text:
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
                spoken_words = asyncio.run(
                    synth_shot_edge_tts(tts_spoken_text, voice, args.rate, args.pitch, mp3_path)
                )
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
                words = align_words_to_display(spoken_words, display_text, dur)
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
                if shot_kw:
                    cw["shot_keywords"] = shot_kw
                all_words.append(cw)

            timing_entry: Dict = {
                "shot_id": sid,
                "start_sec": round(shot_start, 4),
                "end_sec": round(shot_end, 4),
                "duration_sec": round(dur + (args.gap if idx < len(shots) - 1 else 0.0), 4),
                "spoken_duration_sec": round(dur, 4),
                "motion_preset": shot.get("motion_preset", "zoom_in"),
                "flash_in": bool(shot.get("flash_in", False)),
                "sfx_type": shot.get("sfx_type", "none"),
                "sfx_offset_sec": float(shot.get("sfx_offset_sec", 0.0)),
                "sfx_gain_db": float(shot.get("sfx_gain_db", -2.5)),
                "dialogue_or_vo": display_text,
            }
            if tts_spoken_text and tts_spoken_text != display_text:
                timing_entry["tts_text"] = tts_spoken_text
            timing_entry["words"] = shifted_words
            shot_timings.append(timing_entry)

            concat_lines.append(f"file '{wav_path}'")
            cursor = shot_end
            if args.gap > 0 and idx < len(shots) - 1:
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

    global_keywords = parse_keyword_list(args.keywords) or parse_keyword_list(plan.get("highlight_keywords"))
    caption_style = args.caption_style or plan.get("caption_style") or "kinetic_keyword"
    plan_max_chars = (plan.get("fun_pacing_gate") or {}).get("max_caption_chars")
    max_chars = args.max_chars if args.max_chars is not None else int(plan_max_chars or DEFAULT_MAX_CHARS)
    if max_chars < 1:
        max_chars = DEFAULT_MAX_CHARS
    font_size = args.font_size if args.font_size is not None else int(plan.get("caption_font_size") or DEFAULT_FONT_SIZE)
    margin_v = args.margin_v if args.margin_v is not None else int(plan.get("caption_margin_v") or DEFAULT_MARGIN_V)
    keyword_color = args.keyword_color or plan.get("caption_keyword_color") or DEFAULT_KEYWORD_COLOR

    font_name = detect_bold_font(args.font)
    events = build_zero_overlap_events(
        all_words,
        caption_style=caption_style,
        keywords=global_keywords,
        keyword_color=keyword_color,
        max_chars=max_chars,
        tilt_deg=args.tilt,
    )
    ass_lines = [build_ass_header(font_name, font_size=font_size, margin_v=margin_v).rstrip()]
    for s, e, text in events:
        ass_lines.append(f"Dialogue: 0,{cs_to_ass_time(s)},{cs_to_ass_time(e)},ShortsHook,,0,0,0,,{text}")
    ass_path = out_dir / "captions.ass"
    ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")

    print(
        f"Synthesized {len(shot_timings)} shots ({total_dur:.2f}s, font='{font_name}' {font_size}px, "
        f"margin_v={margin_v}, style={caption_style}, max_chars={max_chars}, kw_color={keyword_color}, "
        f"tilt=+/-{args.tilt:g}deg, {len(events)} caption events)"
    )
    print(f"  VO:      {out_dir / 'voiceover.wav'}")
    print(f"  Timings: {timings_path}")
    print(f"  ASS:     {ass_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synthesize voiceover with edge-tts, lint narration scripts, and build v3.1 Fun Kinetic ASS captions."
    )
    parser.add_argument("--lint-plan", help="Run Pre-TTS narration linter on shot_plan.json and exit.")
    parser.add_argument("--shot-plan", help="Path to shot_plan.json for full audio-first synthesis.")
    parser.add_argument("--out-dir", help="Output directory for voiceover.wav, shot_timings.json, captions.ass.")
    parser.add_argument("--voice", default="", help="Override neural voice (default: plan.voice or en-US-AvaNeural).")
    parser.add_argument("--rate", default="+6%", help="Speech rate adjustment (default: +6%% for Shorts energy).")
    parser.add_argument("--pitch", default="+0Hz", help="Pitch adjustment (default: +0Hz).")
    parser.add_argument("--gap", type=float, default=0.06, help="Inter-shot silence gap in seconds (default: 0.06).")
    parser.add_argument("--font", default="", help="Override caption font family (auto-detected via fc-match).")
    parser.add_argument(
        "--font-size",
        type=int,
        default=None,
        help=f"Caption font size in px on 1080x1920 canvas (default: plan.caption_font_size or {DEFAULT_FONT_SIZE}).",
    )
    parser.add_argument(
        "--margin-v",
        type=int,
        default=None,
        help=f"ASS MarginV vertical offset from bottom in px (default: plan.caption_margin_v or {DEFAULT_MARGIN_V}).",
    )
    parser.add_argument(
        "--tilt",
        type=float,
        default=DEFAULT_TILT_DEG,
        help=f"Alternating tilt in degrees on keyword lines; set 0 to disable (default: {DEFAULT_TILT_DEG}).",
    )
    parser.add_argument(
        "--caption-style",
        choices=("kinetic_keyword", "karaoke_chunk"),
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
        help="Keyword color: 'auto' for semantic neon palette (lime/coral/cyan) or ASS BGR hex (default: auto).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help=f"Maximum visible characters per caption line (default: plan max_caption_chars or {DEFAULT_MAX_CHARS}).",
    )
    parser.add_argument("--autofix", action="store_true", help="Auto-rewrite TTS_MISREAD patterns during synthesis.")
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
    if args.tilt < 0:
        parser.error("--tilt must be >= 0")

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

    parser.error("Provide --lint-plan or --shot-plan")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
