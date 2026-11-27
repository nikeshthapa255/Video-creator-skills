#!/usr/bin/env python3
"""Verify real people and official mascots before Stage 2 turnaround sheets or Stage 3 keyframes.

Uses only the Python standard library to check a local character registry, user photos,
or Wikipedia so downstream stages stay on-model.

Capabilities:
  1. Looks up each entity (from --entities, --story-json, or --user-ref) in a
     local JSON character registry first.
  2. Accepts repeatable --user-ref <id>=<local_image_path> overrides for
     user-supplied official mascot art or verified reference portraits.
  3. For unknown entities, queries the Wikipedia REST API
     (https://en.wikipedia.org/api/rest_v1/page/summary/...) with an opensearch
     fallback to verify real-world identity, download their reference portrait
     into --ref-dir, and cache the verified record into --registry.
  4. Emits <character_references.json> locking verified visual traits, attire,
     official mascot specs, and reference_image_path for downstream stages.

Examples:
  python3 research_characters.py \\
    --entities "Jensen Huang,Colette Kress,Meta Muse" \\
    --registry ./references/character_registry.json \\
    --ref-dir ./references \\
    --user-ref meta_muse=./uploads/meta_muse_official.png \\
    --out ./character_references.json

  python3 research_characters.py \\
    --story-json ./story_brief.json \\
    --ref-dir ./references \\
    --out ./character_references.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

USER_AGENT = "ViralAnimatedShortsCharacterResearch/3.0 (stdlib; non-commercial-research)"

# Proper-noun phrases that represent organizations, locations, products, or generic
# headline terms rather than individual people or mascots during prose extraction.
NON_PERSON_PHRASES: Set[str] = {
    "wall street", "silicon valley", "white house", "new york", "san francisco",
    "united states", "united kingdom", "european union", "supreme court",
    "riemann hypothesis", "security institute", "ai security", "breaking news",
    "curated breaking", "tech crunch", "venture beat", "the verge", "hacker news",
    "open ai", "openai", "meta ai", "google deepmind", "microsoft copilot",
    "claude mythos", "claude code", "claude sonnet", "claude opus",
    "apple intelligence", "amazon web", "web services", "data center",
    "artificial intelligence", "machine learning", "large language", "language model",
    "news shorts", "shorts production", "first half", "second half", "third quarter",
    "fourth quarter", "last week", "next week", "exact same", "zero chance",
    "safety exam", "safety testers", "good behavior", "good friday",
    "sunday behavior", "monday morning", "tuesday morning", "deal book",
    "dealbook summit", "times square", "menlo park", "palo alto",
    "mountain view", "santa clara", "redmond washington", "los angeles",
    "hong kong", "south korea", "north america", "latin america", "middle east",
    "social media", "big tech", "big ai", "frontier models", "frontier releases",
    "formal proof", "nasdaq wipeout", "money printer", "signal foundation",
}

NON_PERSON_FIRST_WORDS: Set[str] = {
    "The", "A", "An", "In", "On", "At", "By", "For", "With", "From", "After",
    "Before", "Amid", "While", "When", "Why", "How", "What", "Who", "Where",
    "This", "That", "These", "Those", "Here", "There", "First", "Second",
    "Third", "Then", "Now", "Just", "Even", "Only", "Stop", "Wait", "Imagine",
    "Turns", "Instead", "Meanwhile", "Either", "Because", "Which", "Right",
    "Terrible", "Tiny", "Not", "No", "Yes", "New", "Top", "Big", "Best",
    "Breaking", "Curated", "Official", "Interim", "Former", "Chief", "Tech",
    "Wall", "Silicon", "OpenAI", "Anthropic", "Nvidia", "Google", "Meta",
    "Microsoft", "Apple", "Amazon", "DeepMind", "Claude", "ChatGPT", "Gemini",
    "SpaceX", "Nasdaq", "Reuters", "Bloomberg", "Fortune", "Forbes", "Wired",
    "TechCrunch", "Verge", "Guardian", "Show", "Ask", "Launch", "Update",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December", "Monday", "Tuesday",
    "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "UK", "US", "EU", "AI",
}

NON_PERSON_SECOND_WORDS: Set[str] = {
    "AI", "IPO", "CEO", "CFO", "CTO", "COO", "Labs", "Inc", "Corp", "LLC",
    "News", "Times", "Post", "Journal", "Review", "Press", "Media", "Group",
    "Foundation", "Institute", "University", "College", "Court", "Congress",
    "Senate", "House", "Department", "Commission", "Agency", "Board", "Team",
    "Cloud", "Model", "Models", "Agent", "Agents", "Server", "System", "Engine",
    "Hypothesis", "Theorem", "Proof", "Test", "Tests", "Exam", "Report", "Study",
    "Quarter", "Year", "Month", "Week", "Day", "Today", "Tomorrow", "Yesterday",
    "Street", "Valley", "Park", "City", "State", "County", "Square", "Bay",
    "Twitter", "Shorts", "Video", "Audio", "Safety", "Security", "Privacy",
    "Wipeout", "Crash", "Drama", "Scandal", "Lawsuit", "Freeze", "Launch",
    "Mythos", "Astra", "Sonnet", "Opus", "Haiku", "Flash", "Pro", "Ultra",
}

PERSON_SUMMARY_KEYWORDS = (
    "born ", "businessman", "businesswoman", "business executive", "entrepreneur",
    "computer scientist", "research scientist", "artificial intelligence researcher",
    "software engineer", "programmer", "chief executive", "ceo of", "co-founder",
    "cofounder", "president of", "chairman", "chairwoman", "investor", "billionaire",
    "politician", "senator", "representative", "judge", "attorney", "journalist",
    "professor", "physicist", "mathematician", "executive", "mascot", "fictional character",
    "virtual assistant", "cartoon character", "actor", "actress", "filmmaker",
    "singer", "musician", "athlete", "author", "designer", "inventor",
)

MASCOT_SUMMARY_KEYWORDS = (
    "mascot", "fictional character", "cartoon character", "virtual assistant",
    "plush", "brand character", "spokescharacter", "animated character",
)

DISQUALIFY_SUMMARY_KEYWORDS = (
    "may refer to:", "disambiguation", "company headquartered", "multinational corporation",
    "software company", "technology company", "is a city", "is a country",
    "is a mathematical", "is a programming language", "is a large language model",
    "is an american multinational", "is a financial",
)


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def slugify_id(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return re.sub(r"_{2,}", "_", s) or "unknown_entity"


def load_registry(registry_path: Path, ref_dir: Path) -> Dict[str, Any]:
    if registry_path.exists():
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("version", "3.0")
            data.setdefault("characters", {})
            return data
    return {
        "version": "3.0",
        "updated_utc": utc_now_iso(),
        "description": "Stage 0 verified character, public figure, and official mascot visual reference registry.",
        "ref_dir": str(ref_dir),
        "characters": {},
    }


def save_registry(registry: Dict[str, Any], registry_path: Path) -> None:
    registry["updated_utc"] = utc_now_iso()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def alias_matches_text(alias: str, text: str) -> bool:
    alias = alias.strip()
    if not alias:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def find_in_registry(query: str, reg_chars: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Return matching registry key for an entity name or id, or None."""
    q_clean = query.strip()
    if not q_clean:
        return None
    q_slug = slugify_id(q_clean)
    if q_slug in reg_chars:
        return q_slug
    q_low = q_clean.lower()
    for cid, entry in reg_chars.items():
        if str(entry.get("name", "")).strip().lower() == q_low:
            return cid
        for al in entry.get("aliases", []) or []:
            if str(al).strip().lower() == q_low:
                return cid
    return None


def collect_story_text(story: Dict[str, Any]) -> Tuple[str, str]:
    """Return (primary_text, full_text) extracted from a story brief or shot plan."""
    primary_parts = [
        str(story.get("title", "")),
        str(story.get("summary", "")),
        " ".join(str(f) for f in story.get("key_facts", []) if f),
    ]
    primary_text = " ".join(p for p in primary_parts if p).strip()

    extra_parts = [primary_text]
    for shot_key in ("shots", "curated_shots"):
        for sh in story.get(shot_key, []) or []:
            if isinstance(sh, dict):
                extra_parts.append(str(sh.get("dialogue_or_vo", "")))
                extra_parts.append(str(sh.get("spatial_layout", "")))
    full_text = " ".join(p for p in extra_parts if p).strip()
    return primary_text, full_text


def _is_plausible_person_phrase(phrase: str) -> bool:
    low = phrase.lower().strip()
    if low in NON_PERSON_PHRASES:
        return False
    parts = phrase.split()
    if len(parts) < 2 or len(parts) > 3:
        return False
    if parts[0] in NON_PERSON_FIRST_WORDS:
        return False
    if parts[-1] in NON_PERSON_SECOND_WORDS:
        return False
    if any(p.isupper() or any(ch.isdigit() for ch in p) for p in parts):
        return False
    return True


def extract_candidate_person_names(text: str, limit: int = 6) -> List[str]:
    """Extract plausible 2-word (or hyphenated) real-person / mascot proper names from text."""
    candidates: List[str] = []
    seen_lower: Set[str] = set()

    role_re = re.compile(
        r"\b(?:CEO|CFO|CTO|COO|President|Founder|Co-founder|Cofounder|Chair(?:man|woman|person)?|"
        r"Director|Chief|Researcher|Scientist|Judge|Senator|Mascot)\s+"
        r"([A-Z][a-zA-Z'-]{1,20}\s+[A-Z][a-zA-Z'-]{1,22})\b"
    )
    for m in role_re.finditer(text):
        cand = m.group(1).strip()
        if _is_plausible_person_phrase(cand) and cand.lower() not in seen_lower:
            seen_lower.add(cand.lower())
            candidates.append(cand)

    name_re = re.compile(
        r"\b([A-Z][a-z]{1,18}(?:-[A-Z][a-z]{1,14})?\s+[A-Z][a-z]{1,20}(?:-[A-Z][a-z]{1,14})?)\b"
    )
    for m in name_re.finditer(text):
        cand = m.group(1).strip()
        if _is_plausible_person_phrase(cand) and cand.lower() not in seen_lower:
            seen_lower.add(cand.lower())
            candidates.append(cand)

    return candidates[:limit]


def _http_get_json(url: str, timeout: int = 10) -> Optional[Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None


def _download_image(url: str, dest_base: Path, timeout: int = 15) -> Optional[Path]:
    if not url:
        return None
    low = url.lower().split("?", 1)[0]
    if low.endswith(".png"):
        ext = ".png"
    elif low.endswith(".webp"):
        ext = ".webp"
    elif low.endswith(".jpeg"):
        ext = ".jpeg"
    else:
        ext = ".jpg"
    dest = dest_base.with_suffix(ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 512:
            return None
        dest.write_bytes(data)
        return dest
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _classify_wikipedia_summary(
    summary: Dict[str, Any],
    strict_person_filter: bool = True,
) -> Tuple[bool, str]:
    """Classify a Wikipedia REST summary as (accepted, entity_type)."""
    if not isinstance(summary, dict) or summary.get("type") == "disambiguation":
        return False, ""
    desc = str(summary.get("description", "")).lower()
    extract = str(summary.get("extract", "")).lower()
    blob = f"{desc} {extract[:400]}"

    if any(kw in blob for kw in MASCOT_SUMMARY_KEYWORDS):
        return True, "official_mascot"

    if any(bad in blob for bad in DISQUALIFY_SUMMARY_KEYWORDS):
        has_birth = bool(re.search(r"\bborn\s+(?:[a-z]+\s+\d{1,2},\s+)?(?:19|20)\d{2}\b", blob))
        if not has_birth:
            return (False, "") if strict_person_filter else (False, "")

    if any(kw in blob for kw in PERSON_SUMMARY_KEYWORDS) or re.search(
        r"\((?:born\s+)?(?:19|20)\d{2}", extract[:160]
    ):
        return True, "real_person"

    if not strict_person_filter and extract:
        return True, "official_mascot"

    return False, ""


def query_wikipedia_entity(
    name: str,
    ref_dir: Path,
    strict_person_filter: bool = True,
) -> Optional[Dict[str, Any]]:
    """Query Wikipedia REST summary API (with opensearch fallback) and download reference portrait."""
    slug_title = urllib.parse.quote(name.strip().replace(" ", "_"), safe="_()")
    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug_title}"
    summary = _http_get_json(summary_url)

    if not isinstance(summary, dict) or summary.get("title") == "Not found." or summary.get("type") == "disambiguation":
        summary = None
        q = urllib.parse.quote(name.strip())
        search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={q}&limit=5&format=json"
        search_res = _http_get_json(search_url)
        titles = search_res[1] if isinstance(search_res, list) and len(search_res) > 1 and isinstance(search_res[1], list) else []
        tokens = [t.lower() for t in name.strip().split() if len(t) >= 3]
        for title in titles:
            if not isinstance(title, str):
                continue
            title_low = title.lower()
            if tokens and not any(tok in title_low for tok in tokens):
                continue
            t_slug = urllib.parse.quote(title.replace(" ", "_"), safe="_()")
            cand_summary = _http_get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{t_slug}")
            if isinstance(cand_summary, dict) and cand_summary.get("type") != "disambiguation":
                ok_cand, _ = _classify_wikipedia_summary(cand_summary, strict_person_filter=strict_person_filter)
                if ok_cand:
                    summary = cand_summary
                    break

    if not isinstance(summary, dict):
        return None

    ok, entity_type = _classify_wikipedia_summary(summary, strict_person_filter=strict_person_filter)
    if not ok:
        return None

    canonical_name = str(summary.get("title") or name).strip()
    char_id = slugify_id(canonical_name)
    desc = str(summary.get("description") or "").strip()
    extract = str(summary.get("extract") or "").strip()
    first_sentence = extract.split(". ")[0].strip() if extract else desc
    wiki_url = (
        summary.get("content_urls", {}).get("desktop", {}).get("page")
        or f"https://en.wikipedia.org/wiki/{urllib.parse.quote(canonical_name.replace(' ', '_'))}"
    )

    thumb_url = (
        summary.get("originalimage", {}).get("source")
        or summary.get("thumbnail", {}).get("source")
        or ""
    )
    # Prefer the lighter thumbnail when available to keep local downloads fast and reliable
    if summary.get("thumbnail", {}).get("source"):
        thumb_url = str(summary["thumbnail"]["source"])

    local_ref_path = ""
    if thumb_url:
        dl = _download_image(thumb_url, ref_dir / f"{char_id}_ref")
        if dl:
            local_ref_path = str(dl)

    role_text = desc or first_sentence[:120] or "Public Figure"
    is_mascot = entity_type == "official_mascot"

    if local_ref_path:
        visual_traits = (
            f"Official mascot design of {canonical_name} ({role_text}), matching exact silhouette, proportions, and colors in the verified reference image"
            if is_mascot
            else f"Stylized 3D/animated likeness of {canonical_name} ({role_text}), matching facial features, hairstyle, eyewear, and signature attire in the verified reference portrait"
        )
    else:
        visual_traits = (
            f"Official mascot design of {canonical_name} ({role_text}). Obtain and visually inspect an official reference image before Stage 2"
            if is_mascot
            else f"Stylized likeness of {canonical_name} ({role_text}). Obtain and visually inspect a verified reference portrait before Stage 2"
        )

    aliases = [canonical_name]
    if name.strip() and name.strip().lower() != canonical_name.lower():
        aliases.append(name.strip())
    parts = canonical_name.split()
    if not is_mascot and len(parts) >= 2 and len(parts[-1]) >= 4:
        aliases.append(parts[-1])

    return {
        "id": char_id,
        "name": canonical_name,
        "entity_type": entity_type,
        "role": role_text,
        "aliases": aliases,
        "org_triggers": [],
        "verified_source": wiki_url,
        "verified_utc": utc_now_iso(),
        "summary_extract": first_sentence[:260],
        "visual_traits": visual_traits,
        "attire": (
            "Exact official mascot design and color palette; zero invented accessories or text"
            if is_mascot
            else "Match verified reference portrait attire and grooming; zero logos or readable text"
        ),
        "official_mascot_specs": (
            {
                "silhouette": "Inspect downloaded reference image and lock exact silhouette",
                "primary_colors": [],
                "material_finish": "Match official artwork/plush/render finish",
            }
            if is_mascot
            else None
        ),
        "signature_features": [
            f"downloaded portrait pending visual inspection ({canonical_name})"
            if local_ref_path
            else f"identity verified via Wikipedia ({canonical_name}); reference portrait needed",
            role_text,
        ],
        "reference_image_path": local_ref_path,
        "reference_image_url": thumb_url,
        "reference_downloaded": bool(local_ref_path),
        "likeness_verified": False,
        "needs_visual_inspection": bool(local_ref_path),
        "needs_manual_reference": not bool(local_ref_path),
        "no_held_props_confirmed": True,
    }


BOILERPLATE_TRAIT_MARKERS = (
    "matching facial features, hairstyle, eyewear, and signature attire in the verified reference portrait",
    "matching exact silhouette, proportions, and colors in the verified reference image",
    "obtain and visually inspect",
    "match exact appearance, silhouette, colors, and details in user-supplied reference image",
    "pending stage 0 visual reference inspection",
)


def is_boilerplate_visual_traits(traits: str) -> bool:
    """Return True if visual_traits is empty or still auto-generated boilerplate instead of inspected traits."""
    cleaned = (traits or "").strip()
    if not cleaned:
        return True
    low = cleaned.lower()
    if low.startswith("unverified:"):
        return True
    return any(marker in low for marker in BOILERPLATE_TRAIT_MARKERS)


def parse_user_ref_arg(raw_spec: str) -> Tuple[str, str, str]:
    """Parse '<id>=<local_image_path>' -> (char_id, display_name, image_path)."""
    if "=" not in raw_spec:
        raise ValueError(f"Invalid --user-ref '{raw_spec}'. Expected format: <id>=<local_image_path>")
    raw_id, raw_path = raw_spec.split("=", 1)
    raw_id = raw_id.strip()
    raw_path = raw_path.strip()
    if not raw_id or not raw_path:
        raise ValueError(f"Invalid --user-ref '{raw_spec}'. Both <id> and <local_image_path> are required.")
    char_id = slugify_id(raw_id)
    display_name = raw_id if any(ch.isupper() or ch == " " for ch in raw_id) else raw_id.replace("_", " ").replace("-", " ").title()
    return char_id, display_name, raw_path


def make_unverified_placeholder(name: str) -> Dict[str, Any]:
    """Create an explicit unverified record so Stage 2 never invents an off-model character."""
    char_id = slugify_id(name)
    return {
        "id": char_id,
        "name": name.strip(),
        "entity_type": "unverified_entity",
        "role": "Unverified real person or official mascot",
        "aliases": [name.strip()],
        "org_triggers": [],
        "verified_source": "",
        "verified_utc": utc_now_iso(),
        "summary_extract": "",
        "visual_traits": (
            f"UNVERIFIED: Do NOT invent appearance for '{name.strip()}'. "
            f"Locate an official reference photo via web search or pass --user-ref {char_id}=<local_image_path> before Stage 2."
        ),
        "attire": "Pending Stage 0 visual reference inspection",
        "official_mascot_specs": None,
        "signature_features": [],
        "reference_image_path": "",
        "reference_image_url": "",
        "reference_downloaded": False,
        "likeness_verified": False,
        "needs_visual_inspection": False,
        "needs_manual_reference": True,
        "stage0_action_required": f"Search web or supply official image via --user-ref {char_id}=<path> and visually inspect before generating turnaround sheet.",
        "no_held_props_confirmed": True,
        "match_source": "unverified_needs_user_ref",
    }


def assign_characters_to_shots(
    story: Dict[str, Any],
    characters: List[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Map each shot_id in story shots/curated_shots to matched character IDs."""
    shots = story.get("shots") or story.get("curated_shots") or []
    assignments: Dict[str, List[str]] = {}
    if not isinstance(shots, list) or not shots:
        return assignments

    for idx, sh in enumerate(shots, start=1):
        if not isinstance(sh, dict):
            continue
        sid = str(sh.get("shot_id") or f"shot_{idx:02d}")
        explicit = [c for c in (sh.get("characters") or []) if isinstance(c, str) and c.strip()]
        if explicit:
            assignments[sid] = explicit
            continue

        shot_blob = " ".join([
            str(sh.get("dialogue_or_vo", "")),
            str(sh.get("spatial_layout", "")),
            str(sh.get("camera", "")),
        ])
        matched_ids: List[str] = []
        for ch in characters:
            cid = ch["id"]
            aliases = ch.get("aliases", []) or [ch.get("name", "")]
            if any(alias_matches_text(str(al), shot_blob) for al in aliases):
                matched_ids.append(cid)
            elif any(alias_matches_text(str(org), shot_blob) for org in (ch.get("org_triggers") or [])):
                matched_ids.append(cid)

        # Deduplicate preserving order
        dedup: List[str] = []
        for cid in matched_ids:
            if cid not in dedup:
                dedup.append(cid)
        assignments[sid] = dedup

    return assignments


def research_characters(
    entities: Optional[List[str]] = None,
    story: Optional[Dict[str, Any]] = None,
    registry_path: Path = Path("references/character_registry.json"),
    ref_dir: Path = Path("references"),
    user_refs: Optional[List[str]] = None,
    copy_user_refs: bool = False,
    out_path: Optional[Path] = None,
    allow_web: bool = True,
) -> Dict[str, Any]:
    """Run Stage 0 character & official mascot research and optionally write character_references.json."""
    ref_dir.mkdir(parents=True, exist_ok=True)
    registry = load_registry(registry_path, ref_dir)
    reg_chars: Dict[str, Dict[str, Any]] = registry.setdefault("characters", {})
    registry_dirty = False

    matched: Dict[str, Dict[str, Any]] = {}
    newly_discovered: List[str] = []

    def ensure_local_image(cid: str, entry: Dict[str, Any]) -> None:
        nonlocal registry_dirty
        ref_p = str(entry.get("reference_image_path", "")).strip()
        ref_url = str(entry.get("reference_image_url", "")).strip()
        has_local = bool(ref_p and Path(ref_p).exists())

        if not has_local and allow_web and ref_url:
            dl = _download_image(ref_url, ref_dir / f"{cid}_ref")
            if dl:
                ref_p = str(dl)
                entry["reference_image_path"] = ref_p
                has_local = True
                if cid in reg_chars:
                    reg_chars[cid]["reference_image_path"] = ref_p
                    registry_dirty = True

        has_real_traits = not is_boilerplate_visual_traits(str(entry.get("visual_traits") or ""))
        verified_ok = bool(entry.get("likeness_verified", False)) and has_local and has_real_traits
        needs_inspect = has_local and not verified_ok

        entry["reference_downloaded"] = has_local
        entry["likeness_verified"] = verified_ok
        entry["needs_visual_inspection"] = needs_inspect
        entry["needs_manual_reference"] = not has_local

        if cid in reg_chars:
            for k in ("reference_downloaded", "likeness_verified", "needs_visual_inspection", "needs_manual_reference"):
                if reg_chars[cid].get(k) != entry[k]:
                    reg_chars[cid][k] = entry[k]
                    registry_dirty = True

    def add_from_registry(cid: str, source: str) -> None:
        if cid in matched or cid not in reg_chars:
            return
        entry = dict(reg_chars[cid])
        entry["match_source"] = source
        entry.setdefault("attire", entry.get("clothing_and_accessories", ""))
        entry.setdefault("official_mascot_specs", None)
        entry.setdefault("no_held_props_confirmed", True)
        ensure_local_image(cid, entry)
        matched[cid] = entry

    # 1. Process --user-ref overrides first so user-supplied official photos are locked in registry
    parsed_user_refs: List[Tuple[str, str, str]] = []
    for raw_spec in user_refs or []:
        parsed_user_refs.append(parse_user_ref_arg(raw_spec))

    for char_id, display_name, raw_img_path in parsed_user_refs:
        src_path = Path(raw_img_path).expanduser()
        final_img_path = str(src_path)
        exists_on_disk = src_path.exists()
        if exists_on_disk:
            resolved = src_path.resolve()
            if copy_user_refs and resolved.parent != ref_dir.resolve():
                ext = resolved.suffix.lower() or ".png"
                dest = ref_dir / f"{char_id}_user_ref{ext}"
                shutil.copy2(resolved, dest)
                final_img_path = str(dest.resolve())
            else:
                final_img_path = str(resolved)
        else:
            print(
                f"[CharacterResearch] Warning: --user-ref image not found on disk: {raw_img_path}",
                file=sys.stderr,
            )

        existing_key = find_in_registry(char_id, reg_chars) or find_in_registry(display_name, reg_chars)
        target_id = existing_key or char_id
        entry = dict(reg_chars.get(target_id, {}))
        entry.setdefault("id", target_id)
        entry.setdefault("name", display_name)
        entry.setdefault("entity_type", "official_mascot_or_person")
        entry.setdefault("role", "User-supplied verified visual reference")
        aliases = list(entry.get("aliases") or [])
        for al in (entry["name"], display_name):
            if al and al not in aliases:
                aliases.append(al)
        entry["aliases"] = aliases
        entry.setdefault("org_triggers", [])
        entry["verified_source"] = f"user_supplied_reference:{final_img_path}"
        entry["verified_utc"] = utc_now_iso()
        entry.setdefault(
            "visual_traits",
            f"Match exact appearance, silhouette, colors, and details in user-supplied reference image ({entry['name']})",
        )
        entry.setdefault("attire", "Match user-supplied reference image exactly; zero invented props or text")
        entry.setdefault(
            "official_mascot_specs",
            {
                "reference_locked": True,
                "notes": "Inspect user-supplied image directly before generating Stage 2 turnaround sheet.",
            },
        )
        entry.setdefault("signature_features", [f"user-supplied official reference ({entry['name']})"])
        entry["reference_image_path"] = final_img_path
        entry.setdefault("reference_image_url", "")
        has_real_traits = not is_boilerplate_visual_traits(str(entry.get("visual_traits") or ""))
        verified_ok = exists_on_disk and has_real_traits and bool(entry.get("likeness_verified", False))
        entry["reference_downloaded"] = exists_on_disk
        entry["likeness_verified"] = verified_ok
        entry["needs_visual_inspection"] = exists_on_disk and not verified_ok
        entry["needs_manual_reference"] = not exists_on_disk
        entry["no_held_props_confirmed"] = True
        entry.pop("stage0_action_required", None)

        reg_chars[target_id] = entry
        registry_dirty = True
        entry_copy = dict(entry)
        entry_copy["match_source"] = "user_supplied_ref"
        matched[target_id] = entry_copy

    # 2. Collect explicit entity queries from --entities and --story-json
    explicit_queries: List[Tuple[str, str]] = []
    for ent in entities or []:
        ent_clean = ent.strip()
        if ent_clean:
            explicit_queries.append((ent_clean, "cli_entities"))

    if story:
        for cid in story.get("featured_characters", []) or []:
            if isinstance(cid, str) and cid.strip():
                explicit_queries.append((cid.strip(), "story_featured_cast"))
        for ch in story.get("characters", []) or []:
            if isinstance(ch, str) and ch.strip():
                explicit_queries.append((ch.strip(), "story_characters"))
            elif isinstance(ch, dict):
                qname = str(ch.get("name") or ch.get("id") or "").strip()
                if qname:
                    explicit_queries.append((qname, "story_characters"))

    for query_name, source_tag in explicit_queries:
        reg_key = find_in_registry(query_name, reg_chars)
        if reg_key:
            add_from_registry(reg_key, source_tag)
            continue

        discovered = None
        if allow_web:
            discovered = query_wikipedia_entity(query_name, ref_dir, strict_person_filter=False)

        if discovered:
            cid = discovered["id"]
            if cid not in reg_chars:
                reg_chars[cid] = discovered
                registry_dirty = True
                newly_discovered.append(cid)
                print(
                    f"[CharacterResearch] Downloaded via Wikipedia (pending visual inspection): {discovered['name']} "
                    f"({discovered['entity_type']}) -> ref={discovered.get('reference_image_path') or 'none'}",
                    file=sys.stderr,
                )
            add_from_registry(cid, "wikipedia_discovery")
        else:
            placeholder = make_unverified_placeholder(query_name)
            if placeholder["id"] not in matched:
                matched[placeholder["id"]] = placeholder
                print(
                    f"[CharacterResearch] Needs manual Stage 0 reference: '{query_name}' "
                    f"(not in registry or Wikipedia). Pass --user-ref {placeholder['id']}=<image_path>.",
                    file=sys.stderr,
                )

    # 3. If --story-json was supplied, also match registry entries & discover new people in story prose
    if story:
        primary_text, full_text = collect_story_text(story)

        for cid, entry in list(reg_chars.items()):
            if cid in matched:
                continue
            aliases = entry.get("aliases", []) or [entry.get("name", "")]
            if any(alias_matches_text(str(al), full_text) for al in aliases):
                add_from_registry(cid, "story_explicit_name")

        for cid, entry in list(reg_chars.items()):
            if cid in matched:
                continue
            triggers = entry.get("org_triggers", []) or []
            if any(alias_matches_text(str(tr), primary_text) for tr in triggers):
                add_from_registry(cid, "story_org_trigger")

        if allow_web and primary_text:
            known_lowers: Set[str] = set()
            for entry in reg_chars.values():
                known_lowers.add(str(entry.get("name", "")).lower())
                for al in entry.get("aliases", []) or []:
                    known_lowers.add(str(al).lower())
            for ent in matched.values():
                known_lowers.add(str(ent.get("name", "")).lower())

            candidates = extract_candidate_person_names(primary_text, limit=6)
            checked = 0
            for cand in candidates:
                if checked >= 5:
                    break
                low = cand.lower()
                if low in known_lowers or any(
                    (low in k or k in low) for k in known_lowers if len(k) > 4
                ):
                    continue
                checked += 1
                discovered = query_wikipedia_entity(cand, ref_dir, strict_person_filter=True)
                if discovered:
                    cid = discovered["id"]
                    if cid not in reg_chars:
                        reg_chars[cid] = discovered
                        registry_dirty = True
                        newly_discovered.append(cid)
                        print(
                            f"[CharacterResearch] Discovered from story prose via Wikipedia (pending inspection): "
                            f"{discovered['name']} ({discovered['role']}) -> ref={discovered.get('reference_image_path') or 'none'}",
                            file=sys.stderr,
                        )
                    add_from_registry(cid, "wikipedia_story_discovery")

    if registry_dirty:
        save_registry(registry, registry_path)
        print(f"[CharacterResearch] Updated registry: {registry_path}", file=sys.stderr)

    all_characters = list(matched.values())
    ref_paths: Dict[str, str] = {}
    unverified_entities: List[str] = []
    uninspected_entities: List[str] = []
    for ch in all_characters:
        rp = str(ch.get("reference_image_path", "")).strip()
        if rp and Path(rp).exists():
            ref_paths[ch["id"]] = rp
        if not ch.get("likeness_verified", False):
            unverified_entities.append(ch["id"])
        if ch.get("needs_visual_inspection", False):
            uninspected_entities.append(ch["id"])

    shot_assignments = assign_characters_to_shots(story, all_characters) if story else {}

    result: Dict[str, Any] = {
        "schema_version": "3.1",
        "researched_utc": utc_now_iso(),
        "slug": str(story.get("slug", "cli-entities")) if story else "cli-entities",
        "title": str(story.get("title", "")) if story else "",
        "registry_path": str(registry_path),
        "ref_dir": str(ref_dir),
        "character_ids": [c["id"] for c in all_characters],
        "characters": all_characters,
        "reference_image_paths": ref_paths,
        "unverified_entities": unverified_entities,
        "uninspected_entities": uninspected_entities,
        "newly_discovered_from_web": newly_discovered,
        "shot_character_assignments": shot_assignments,
    }

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(
            f"[CharacterResearch] Saved {out_path} "
            f"(characters={len(all_characters)}, downloaded_refs={len(ref_paths)}, "
            f"uninspected={len(uninspected_entities)}, unverified={len(unverified_entities)}, "
            f"new_web={len(newly_discovered)})",
            file=sys.stderr,
        )

    return result


def verify_character_in_registry(
    registry_path: Path,
    ref_dir: Path,
    entity_query: str,
    traits: str,
    attire: str = "",
    out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record visually inspected observable traits and lock likeness_verified=True in the registry."""
    cleaned_traits = (traits or "").strip()
    if is_boilerplate_visual_traits(cleaned_traits):
        raise ValueError(
            "--traits must describe concrete observable traits from inspecting the portrait (not empty or boilerplate)."
        )

    registry = load_registry(registry_path, ref_dir)
    reg_chars: Dict[str, Dict[str, Any]] = registry.setdefault("characters", {})
    target_id = find_in_registry(entity_query, reg_chars)
    if not target_id:
        raise ValueError(
            f"Entity '{entity_query}' not found in registry {registry_path}. Run Stage 0 research first."
        )

    entry = dict(reg_chars[target_id])
    ref_p = str(entry.get("reference_image_path", "")).strip()
    has_local = bool(ref_p and Path(ref_p).exists())

    entry["visual_traits"] = cleaned_traits
    if attire.strip():
        entry["attire"] = attire.strip()
    entry["reference_downloaded"] = has_local
    entry["likeness_verified"] = True
    entry["needs_visual_inspection"] = False
    entry["needs_manual_reference"] = not has_local
    entry["likeness_verified_utc"] = utc_now_iso()
    entry.pop("stage0_action_required", None)

    reg_chars[target_id] = entry
    save_registry(registry, registry_path)
    print(
        f"[CharacterResearch] Verified likeness for '{target_id}' in {registry_path} "
        f"(reference_downloaded={has_local}, likeness_verified=True)",
        file=sys.stderr,
    )

    # Also update an existing character_references.json in place when --out is provided
    if out_path is not None and out_path.exists():
        ref_doc = json.loads(out_path.read_text(encoding="utf-8"))
        if isinstance(ref_doc, dict) and isinstance(ref_doc.get("characters"), list):
            for idx, ch in enumerate(ref_doc["characters"]):
                if isinstance(ch, dict) and ch.get("id") == target_id:
                    merged = dict(ch)
                    merged.update({
                        "visual_traits": entry["visual_traits"],
                        "attire": entry.get("attire", merged.get("attire", "")),
                        "reference_downloaded": entry["reference_downloaded"],
                        "likeness_verified": True,
                        "needs_visual_inspection": False,
                        "needs_manual_reference": entry["needs_manual_reference"],
                        "likeness_verified_utc": entry["likeness_verified_utc"],
                    })
                    ref_doc["characters"][idx] = merged
            ref_doc["unverified_entities"] = [
                c["id"] for c in ref_doc["characters"] if not c.get("likeness_verified", False)
            ]
            ref_doc["uninspected_entities"] = [
                c["id"] for c in ref_doc["characters"] if c.get("needs_visual_inspection", False)
            ]
            out_path.write_text(json.dumps(ref_doc, indent=2) + "\n", encoding="utf-8")
            print(f"[CharacterResearch] Synced verified traits into {out_path}", file=sys.stderr)

    return entry


def parse_entities_csv(raw: str) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in re.split(r"[,;\n]+", raw) if part.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify real people and official mascots before Stage 2 turnaround sheets or Stage 3 "
            "keyframes, using a local registry, user photos, or Wikipedia."
        )
    )
    parser.add_argument(
        "--entities",
        default="",
        help='Comma-separated names of people or official mascots to research (e.g. "Jensen Huang,Colette Kress,Meta Muse")',
    )
    parser.add_argument(
        "--story-json",
        default="",
        help="Optional path to a story brief or shot_plan.json to scan for characters and public figures",
    )
    parser.add_argument(
        "--registry",
        default="",
        help="Path to character_registry.json (defaults to <ref-dir>/character_registry.json)",
    )
    parser.add_argument(
        "--ref-dir",
        "--refs-dir",
        dest="ref_dir",
        default="references",
        help="Directory for downloaded or cached reference portraits (default: ./references)",
    )
    parser.add_argument(
        "--user-ref",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="Supply a local reference image for a person or mascot; repeat for multiple (e.g. --user-ref meta_muse=./muse.png).",
    )
    parser.add_argument(
        "--copy-user-refs",
        action="store_true",
        help="Copy --user-ref images into --ref-dir so all references live in one directory",
    )
    parser.add_argument(
        "--verify",
        default="",
        metavar="ENTITY_ID",
        help="Lock likeness_verified=True in --registry for an inspected entity ID or name (requires --traits).",
    )
    parser.add_argument(
        "--traits",
        default="",
        help="Observable visual traits recorded after inspecting the Stage 0 portrait (used with --verify).",
    )
    parser.add_argument(
        "--attire",
        default="",
        help="Optional observed signature attire to record alongside --traits when running --verify.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output path for character_references.json (prints JSON to stdout if omitted; updated in place with --verify if it exists)",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable live Wikipedia REST API lookups (use local registry and --user-ref only)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    ref_dir = Path(args.ref_dir)
    registry_path = Path(args.registry) if args.registry else (ref_dir / "character_registry.json")
    out_path = Path(args.out) if args.out else None

    if args.verify:
        if not args.traits.strip():
            parser.error("--traits is required when using --verify.")
        try:
            entry = verify_character_in_registry(
                registry_path=registry_path,
                ref_dir=ref_dir,
                entity_query=args.verify,
                traits=args.traits,
                attire=args.attire,
                out_path=out_path,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(entry, indent=2))
        return 0

    entities = parse_entities_csv(args.entities)
    story: Optional[Dict[str, Any]] = None
    if args.story_json:
        story_path = Path(args.story_json)
        if not story_path.exists():
            print(f"Error: --story-json not found: {story_path}", file=sys.stderr)
            return 2
        raw = json.loads(story_path.read_text(encoding="utf-8"))
        story = raw.get("selected", raw) if isinstance(raw, dict) else None

    if not entities and story is None and not args.user_ref:
        parser.error("Provide at least one of --entities, --story-json, --user-ref, or --verify.")

    try:
        result = research_characters(
            entities=entities,
            story=story,
            registry_path=registry_path,
            ref_dir=ref_dir,
            user_refs=args.user_ref,
            copy_user_refs=args.copy_user_refs,
            out_path=out_path,
            allow_web=not args.no_web,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if out_path is None:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
