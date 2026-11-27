# Trip Cinema Studio (v0.2.1)

An Antigravity plugin that turns a local folder, explicit file list, Google Drive folder, or Google Photos shared album of raw trip photos and **80%-video 4K clips** into a standalone **16:9 horizontal cinematic MP4** (or optional **9:16 vertical teaser**, `-14 LUFS`, `LRA=9`, no voiceover).

## What changed in v0.2.1

- **Progressive-disclosure platform routing**: `skills/trip-cinema/SKILL.md` now opens with a routing table pointing agents to either `references/platform_linux.md` (Python + FFmpeg) or `references/platform_macos.md` (zero-dependency Apple-signed JXA) based on `uname -s`, keeping OS-specific commands out of the main runbook.
- **HLG vs. PQ HDR color decode**: `render_trip.py` and `references/ffmpeg_recipes.md` separate 10-bit HLG smartphone footage (`arib-std-b67`) from PQ (`smpte2084`) so HLG clips decode into Rec.709 without PQ `npl=100` whitewash.
- **Clean transitions, cached segment assembly, and portrait speed ramps**: `render_trip.py` suppresses colliding graphic card fades next to overlaps, replaces `fadewhite` and 1-frame `boxblur` pops, emits video-only `-an -frames:v` intermediates with probed `xfade` offsets, assembles cut-aligned segments (`-c copy`) with `--workers` and `--reuse-cache`, runs `vidstab` and `speed_profile` before portrait framing, and honors `rendered_card_path` on bespoke graphic cards.
- **Collage `focal_anchor` framing and aspect-ratio lint**: `render_trip.py` applies `focal_anchor` (defaulting `fy=0.35` on human close-ups) across all multi-tile layouts and improves `polaroid_stack` framing; `lint_edl.py` flags slot-to-source aspect mismatches above `1.85x`.
- **EDL-aware Linux QA and timestamped stop guard**: `render_trip.py --extract-qa --edl` generates `qa_video_alignment.jpg`, `qa_chapter_cards.jpg`, `qa_collages.jpg` (`960x540` cells), `qa_transitions.jpg` (3-frame overlap strips), and `qa_metrics.json` (`signalstats` luma/saturation + duration drift). `hooks/qa_stop_guard.sh` scans `./output/*.mp4` and `./work/*master*.mp4` dynamically and blocks stale `qa_passed.flag` files.
- **Deterministic Gate 1 privacy exclusions**: `lint_edl.py --gates-state` enforces `excluded_ids`, `excluded_paths`, manifest `excluded_by_user`, and `forbidden_video_ranges` across shots, tiles, and J/L-cut/transition handles.
- **Linux v0.2.1 tooling parity**: `generate_look_previews.py --style-ids` renders `1280x720` 4-Card Style-Pack Carousel PNGs from photo or video inputs using `styles/*.json`, plus reusable `agents/media-triage-worker.md` and `agents/bgm-scout.md` templates.

## Core features

- **7 Declarative Style Packs (`styles/*.json`)**: `cinema_doc`, `analog_scrapbook`, `wes_postcard`, `kinetic_montage`, `editorial_bento`, `super8_nostalgia`, and `sitcom_cast`.
- **Video-First Stage 0 Timeline Strips (`1800x360`)**: Multi-frame upright strips with aligned 8 kHz mono RMS audio waveforms (`[SONIC POSTCARD]` peaks and `[WIND]` mute zones) and a 7-Tier Creation Date Waterfall (`Keys:CreationDate` -> EXIF -> QuickTime `mvhd` -> filename -> `mdls` -> sequence interpolation).
- **4-Gate interactive workflow**:
  1. **Gate 1 (Timeline strips, chapters, VIPs, privacy bans)**: Approve brackets, VIP faces, bloopers, `locked_by_user` pins, and `excluded_ids`/`forbidden_video_ranges`.
  2. **Gate 2 (4-Card Style-Pack Carousel)**: Compare 4 composite `1280x720` Style Pack cards rendered from your real footage and toggle `trip_wrapped`, `cast_intro`, `blooper_title`, and a 30s `9:16` teaser.
  3. **Gate 3 (BGM and pacing)**: Audition BPM-matched CC0 tracks, optional `lyria-3.5` previews (`$0.08`/track), or a local track with beat/energy analysis. No voiceover.
  4. **Gate 4 (EDL v2.0 storyboard and cost cap)**: Inspect the linted shot plan and `$` cost cap (`$0` in local mode) before rendering.
- **Safety and authenticity**:
  - Read-only raw media (`hooks/raw_media_guard.sh` blocks destructive writes against original files).
  - Zero generative I2V on `has_human_closeup: true` shots or tiles.
  - Verified EXIF stats only on `trip_wrapped` cards (metrics below 30% coverage are omitted).
- **Chunked multimodal QA and 3-persona `/swarm` review panel**: Audits 100% of video mid-frames, graphic cards, multi-tile collages, non-cut transitions, `qa_metrics.json`, end frames, and `<= 60s` chunks across `review-director`, `review-cinematographer`, and `review-family-member` before writing `qa_passed.flag`.

## Installation

Install directly from GitHub using the Antigravity CLI:

```bash
agy plugin install https://github.com/nikeshthapa255/Video-creator-skills
```

Or install from a local clone:

```bash
agy plugin install ./trip-cinema-studio
```

## Prerequisites

- **Linux / cross-platform stack (`references/platform_linux.md`)**: `python3`, `ffmpeg` and `ffprobe` (with `zscale`/`tonemap`; `vidstab` recommended), Pillow, `exiftool`, and optional `node`/`npx` (for Remotion cards).
- **Zero-dependency macOS native engine (`references/platform_macos.md`)**: Apple-signed `osascript -l JavaScript` (JXA + `AVFoundation`/`CoreImage`/`ImageIO`/`AppKit`/`Vision` + `/usr/bin/afconvert`) under `skills/trip-cinema/scripts/macos_native/`.
- **Optional plugins**: `gdrive` (Google Drive download/upload) and `gemini-api` (`lyria-3.5` BGM previews and scenic I2V).

Run preflight discovery at any time:

```bash
python3 skills/trip-cinema/scripts/preflight.py
```

## Architecture

```text
trip-cinema-studio/
├── plugin.json                      # v0.2.1 manifest
├── hooks.json
├── hooks/
│   ├── raw_media_guard.sh           # PreToolUse: protects raw media from destructive commands
│   └── qa_stop_guard.sh             # Stop: verifies fresh qa_passed.flag against newest MP4
├── rules/
│   └── AGENTS.md                    # Always-on safety, 4-gate HITL, and disk-state rules
├── agents/
│   ├── trip-ingestor.md             # Stage 0 video timeline strips, 7-tier dates, Google Photos
│   ├── media-triage-worker.md       # Parallel strip and photo-sheet saliency triage worker
│   ├── bgm-scout.md                 # Candidate BGM scouting and beat_map.json analysis
│   ├── trip-director.md             # Gate 2 Style Carousel, BGM sync, EDL v2.0 & lint_edl.py
│   ├── trip-qa-critic.md            # Alignment, cards, collages, transitions, metrics & <=60s QA
│   ├── review-director.md           # Stage 5 /swarm persona: story arc, pacing & bar sync
│   ├── review-cinematographer.md    # Stage 5 /swarm persona: HDR color, framing & transitions
│   └── review-family-member.md      # Stage 5 /swarm persona: warmth, inclusivity & privacy audit
└── skills/trip-cinema/
    ├── SKILL.md                     # Lean OS-agnostic runbook with platform routing table
    ├── styles/                      # style_schema.json + 7 declarative Style Pack presets
    ├── scripts/                     # Python/FFmpeg pipeline, EDL v2.0 linter, Style Carousel
    │   └── macos_native/            # Zero-dependency macOS JXA engine + modular lib/*.js
    ├── remotion_template/           # Style-aware ChapterTitle, TripRouteMap, TripWrappedCard
    └── references/                  # platform_linux.md, platform_macos.md, edl_schema.json,
                                     # ffmpeg_recipes.md, remotion_templates.md, bgm_sources.md
```

