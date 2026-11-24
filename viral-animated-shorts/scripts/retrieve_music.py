#!/usr/bin/env python3
"""Retrieve or synthesize speech-friendly background music for animated Shorts.

Zero required API keys. Uses the Python standard library and ffmpeg/ffprobe.

Retrieval cascade:
  1. Explicit --track-file (local instrumental file)
  2. Explicit --url (downloads and caches locally)
  3. Local royalty-free library match in --library-dir / $SHORTS_MUSIC_DIR
     (.wav, .mp3, .m4a, .ogg, .flac matched against mood + --query tokens)
  4. Optional Pixabay / Freesound royalty-free search ONLY when PIXABAY_API_KEY
     or FREESOUND_API_KEY is set in the environment (falls back to step 5 on error)
  5. Deterministic zero-API-key fallback: calls sibling make_bgm.py

Post-processing (applied to EVERY track source):
  - Loops shorter tracks or trims longer ones to exact --duration
  - Converts to 48 kHz 16-bit stereo PCM WAV
  - Applies gentle speech-friendly warmth (highpass=f=45, lowpass=f=7000)
  - Normalizes to consistent subtle loudness (--target-lufs, default -22.0 LUFS)
  - Applies smooth fade-in and fade-out after normalization

Examples:
  python3 retrieve_music.py --mood dramatic_news --duration 42 --out audio/bgm.wav
  python3 retrieve_music.py --mood cinematic_tension --query "cyber leak" --library-dir ~/music --duration 38 --out audio/bgm.wav
  python3 retrieve_music.py --track-file ~/music/news_bed.mp3 --duration 40 --out audio/bgm.wav
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}

MOODS = (
    "cinematic_tension",
    "comedy_quirk",
    "dramatic_news",
    "miami_pop",
    "retro_drive",
    "tech_pulse",
)

MOOD_KEYWORDS: Dict[str, List[str]] = {
    "dramatic_news": ["dramatic", "news", "newsroom", "broadcast", "commentary", "breaking", "journalism", "headline", "report"],
    "cinematic_tension": ["cinematic", "tension", "suspense", "investigative", "dark", "thriller", "mystery", "drone", "cyber"],
    "miami_pop": ["miami", "pop", "upbeat", "synthpop", "summer", "funk", "groove"],
    "retro_drive": ["retro", "synthwave", "drive", "80s", "outrun", "vaporwave"],
    "tech_pulse": ["tech", "pulse", "electronic", "minimal", "modern", "startup", "ai"],
    "comedy_quirk": ["comedy", "quirk", "quirky", "playful", "funny", "marimba", "cartoon"],
}

USER_AGENT = "viral-animated-shorts/2.1 (+https://github.com/)"


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
    val = res.stdout.strip()
    if not val or val == "N/A":
        raise RuntimeError(f"Could not determine audio duration for {path}")
    return float(val)


def download_cached(url: str, cache_dir: Path, timeout: int = 25) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if ext not in AUDIO_EXTS:
        ext = ".audio"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    cached_path = cache_dir / f"url_{digest}{ext}"
    if cached_path.exists() and cached_path.stat().st_size > 1024:
        return cached_path

    tmp_path = cache_dir / f"url_{digest}.part"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, tmp_path.open("wb") as out_f:
        shutil.copyfileobj(resp, out_f)
    if tmp_path.stat().st_size < 1024:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file from {url} is under 1 KB and may not be valid audio.")
    tmp_path.replace(cached_path)
    return cached_path


def find_local_library_track(dirs: List[Path], mood: str, query: str) -> Optional[Path]:
    mood_tokens = set(re.findall(r"[a-z0-9]+", mood.lower()))
    mood_synonyms = set(MOOD_KEYWORDS.get(mood, []))
    query_tokens = set(re.findall(r"[a-z0-9]+", (query or "").lower()))

    candidates: List[Tuple[int, str, Path]] = []
    seen: set = set()

    for base_dir in dirs:
        if not base_dir.exists() or not base_dir.is_dir():
            continue
        for path in sorted(base_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)

            stem_lower = path.stem.lower()
            name_tokens = set(re.findall(r"[a-z0-9]+", stem_lower))
            score = 0
            if mood.lower() in stem_lower:
                score += 10
            score += 4 * len(name_tokens & query_tokens)
            score += 3 * len(name_tokens & mood_tokens)
            score += 2 * len(name_tokens & mood_synonyms)
            if score > 0:
                candidates.append((score, resolved, path))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _search_freesound(query: str, api_key: str, cache_dir: Path) -> Optional[Tuple[Path, str]]:
    params = urllib.parse.urlencode({
        "query": query,
        "filter": "duration:[15 TO 300]",
        "fields": "id,name,previews,license",
        "page_size": 5,
        "token": api_key,
    })
    url = f"https://freesound.org/apiv2/search/text/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for item in data.get("results", []):
        previews = item.get("previews") or {}
        dl_url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3")
        if dl_url:
            path = download_cached(dl_url, cache_dir)
            return path, f"freesound:{item.get('id')}:{item.get('name', '')}"
    return None


def _search_pixabay(query: str, api_key: str, cache_dir: Path) -> Optional[Tuple[Path, str]]:
    params = urllib.parse.urlencode({
        "key": api_key,
        "q": query,
        "per_page": 5,
    })
    url = f"https://pixabay.com/api/music/?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for hit in data.get("hits", []):
        dl_url = hit.get("audio") or hit.get("previewURL") or hit.get("url")
        if dl_url and str(dl_url).startswith(("http://", "https://")):
            path = download_cached(str(dl_url), cache_dir)
            return path, f"pixabay:{hit.get('id')}:{hit.get('tags', '')}"
    return None


def try_optional_api_search(mood: str, query: str, cache_dir: Path) -> Optional[Tuple[Path, str, str]]:
    search_terms = " ".join(
        part for part in [query.strip(), mood.replace("_", " "), "instrumental"] if part
    ).strip()

    pixabay_key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if pixabay_key:
        try:
            res = _search_pixabay(search_terms, pixabay_key, cache_dir)
            if res:
                return res[0], "pixabay_api", res[1]
        except Exception as exc:
            print(f"Warning: Pixabay search failed ({exc}); falling back to the next music source.", file=sys.stderr)

    freesound_key = os.environ.get("FREESOUND_API_KEY", "").strip()
    if freesound_key:
        try:
            res = _search_freesound(search_terms, freesound_key, cache_dir)
            if res:
                return res[0], "freesound_api", res[1]
        except Exception as exc:
            print(f"Warning: Freesound search failed ({exc}); falling back to the next music source.", file=sys.stderr)

    return None


def run_make_bgm_fallback(mood: str, duration: float, raw_wav: Path) -> Path:
    make_bgm = SCRIPT_DIR / "make_bgm.py"
    if not make_bgm.exists():
        raise FileNotFoundError(f"Fallback generator not found: {make_bgm}")
    subprocess.run(
        [
            sys.executable,
            str(make_bgm),
            "--mood", mood,
            "--duration", f"{duration:.3f}",
            "--out", str(raw_wav),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return raw_wav


def _parse_loudnorm_stats(stderr_text: str) -> Optional[Dict[str, str]]:
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr_text, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not data.get("input_i") or str(data.get("input_i")) == "-inf":
        return None
    return {k: str(v) for k, v in data.items()}


def postprocess_bgm(
    raw_path: Path,
    out_path: Path,
    duration: float,
    target_lufs: float,
    fade_in: float,
    fade_out: float,
) -> Dict[str, object]:
    src_dur = probe_audio_duration(raw_path)
    needs_loop = src_dur + 0.05 < duration

    eff_fade_in = max(0.0, min(fade_in, duration * 0.45))
    eff_fade_out = max(0.0, min(fade_out, duration * 0.45))
    fade_out_start = max(0.0, duration - eff_fade_out)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage1 = Path(tmp) / "stage1.wav"
        in_args = ["-stream_loop", "-1", "-i", str(raw_path)] if needs_loop else ["-i", str(raw_path)]

        # Stage 1: exact duration (loop or trim), 48 kHz stereo PCM, warm speech-friendly EQ
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                *in_args,
                "-t", f"{duration:.3f}",
                "-af", "highpass=f=45,lowpass=f=7000",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                str(stage1),
            ],
            check=True,
        )

        # Stage 2: measure loudness for 2-pass linear loudnorm
        measure_cmd = [
            "ffmpeg", "-hide_banner", "-i", str(stage1),
            "-af", f"loudnorm=I={target_lufs:.1f}:TP=-2.0:LRA=9:print_format=json",
            "-f", "null", "-",
        ]
        m_res = subprocess.run(measure_cmd, capture_output=True, text=True, check=False)
        stats = _parse_loudnorm_stats(m_res.stderr)

        af_parts: List[str] = []
        if stats:
            af_parts.append(
                f"loudnorm=I={target_lufs:.1f}:TP=-2.0:LRA=9"
                f":measured_I={stats['input_i']}"
                f":measured_TP={stats['input_tp']}"
                f":measured_LRA={stats['input_lra']}"
                f":measured_thresh={stats['input_thresh']}"
                f":offset={stats['target_offset']}"
                f":linear=true"
            )
        else:
            af_parts.append(f"loudnorm=I={target_lufs:.1f}:TP=-2.0:LRA=9")

        # Apply fades AFTER loudnorm so smooth fades are never pumped back up
        if eff_fade_in > 0:
            af_parts.append(f"afade=t=in:st=0:d={eff_fade_in:.3f}:curve=qsin")
        if eff_fade_out > 0:
            af_parts.append(f"afade=t=out:st={fade_out_start:.3f}:d={eff_fade_out:.3f}:curve=qsin")

        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(stage1),
                "-af", ",".join(af_parts),
                "-t", f"{duration:.3f}",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                str(out_path),
            ],
            check=True,
        )

    final_dur = probe_audio_duration(out_path)
    return {
        "source_duration_sec": round(src_dur, 3),
        "looped": needs_loop,
        "output_duration_sec": round(final_dur, 3),
        "fade_in_sec": round(eff_fade_in, 3),
        "fade_out_sec": round(eff_fade_out, 3),
        "target_lufs": target_lufs,
        "sample_rate": 48000,
        "channels": 2,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retrieve or synthesize speech-friendly Shorts BGM (zero required API keys)."
    )
    parser.add_argument("--mood", choices=MOODS, default="dramatic_news", help="Target BGM mood (default: dramatic_news).")
    parser.add_argument("--query", default="", help="Optional search/match keywords (e.g. 'ai leak breaking news').")
    parser.add_argument("--duration", type=float, required=True, help="Target output duration in seconds (> 0).")
    parser.add_argument("--out", required=True, help="Output stereo 48 kHz WAV path.")
    parser.add_argument("--track-file", default="", help="Explicit local instrumental audio file path.")
    parser.add_argument("--url", default="", help="Explicit HTTP/HTTPS URL to an instrumental audio file (cached).")
    parser.add_argument(
        "--library-dir",
        default="",
        help="Local royalty-free music directory to search (also checks $SHORTS_MUSIC_DIR).",
    )
    parser.add_argument("--target-lufs", type=float, default=-22.0, help="Target integrated loudness in LUFS (default: -22.0).")
    parser.add_argument("--fade-in", type=float, default=0.4, help="Fade-in duration in seconds (default: 0.4).")
    parser.add_argument("--fade-out", type=float, default=0.9, help="Fade-out duration in seconds (default: 0.9).")
    parser.add_argument(
        "--cache-dir",
        default=str(Path(tempfile.gettempdir()) / "shorts_music_cache"),
        help="Cache directory for downloaded URL/API tracks.",
    )
    args = parser.parse_args()

    if args.duration <= 0:
        parser.error("--duration must be > 0")
    if args.fade_in < 0 or args.fade_out < 0:
        parser.error("--fade-in and --fade-out must be >= 0")

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("Error: ffmpeg and ffprobe must be installed and on PATH.", file=sys.stderr)
        return 1

    cache_dir = Path(args.cache_dir)
    out_path = Path(args.out)

    selected_source = ""
    selected_detail = ""
    raw_path: Optional[Path] = None

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # 1. Explicit --track-file
        if args.track_file:
            tf = Path(args.track_file)
            if not tf.is_file():
                print(f"Error: --track-file not found: {tf}", file=sys.stderr)
                return 1
            raw_path = tf
            selected_source = "track_file"
            selected_detail = str(tf.resolve())

        # 2. Explicit --url
        if raw_path is None and args.url:
            try:
                raw_path = download_cached(args.url, cache_dir)
                selected_source = "url"
                selected_detail = args.url
            except Exception as exc:
                print(f"Warning: failed to download --url ({exc}); falling back to the next music source.", file=sys.stderr)

        # 3. Local royalty-free library match (--library-dir and/or $SHORTS_MUSIC_DIR)
        if raw_path is None:
            lib_dirs: List[Path] = []
            if args.library_dir:
                lib_dirs.append(Path(args.library_dir))
            env_dir = os.environ.get("SHORTS_MUSIC_DIR", "").strip()
            if env_dir:
                for p in env_dir.split(os.pathsep):
                    if p.strip():
                        lib_dirs.append(Path(p.strip()))
            matched = find_local_library_track(lib_dirs, args.mood, args.query)
            if matched is not None:
                raw_path = matched
                selected_source = "local_library"
                selected_detail = str(matched.resolve())

        # 4. Optional Pixabay / Freesound royalty-free API search
        if raw_path is None:
            api_hit = try_optional_api_search(args.mood, args.query, cache_dir)
            if api_hit is not None:
                raw_path, selected_source, selected_detail = api_hit

        # 5. Deterministic zero-API-key fallback via make_bgm.py
        if raw_path is None:
            fallback_wav = tmp_dir / f"fallback_{args.mood}.wav"
            raw_path = run_make_bgm_fallback(args.mood, args.duration, fallback_wav)
            selected_source = "procedural_make_bgm"
            selected_detail = f"make_bgm.py --mood {args.mood}"

        pp_meta = postprocess_bgm(
            raw_path=raw_path,
            out_path=out_path,
            duration=args.duration,
            target_lufs=args.target_lufs,
            fade_in=args.fade_in,
            fade_out=args.fade_out,
        )

    summary = {
        "source": selected_source,
        "detail": selected_detail,
        "mood": args.mood,
        "query": args.query,
        "out": str(out_path),
        **pp_meta,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
