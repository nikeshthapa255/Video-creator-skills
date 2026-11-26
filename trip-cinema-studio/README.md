# Trip Cinema Studio (v0.2.0)

An Antigravity plugin that turns a local folder, explicit file list, or Google Drive folder of raw trip photos and **80%-video 4K clips** into a styled standalone **16:9 horizontal cinematic MP4** (or optional **9:16 vertical teaser**, `-14 LUFS`, `LRA=9`, no voiceover).

## What Is New in v0.2.0

- **7 Declarative Style Packs (`styles/*.json`)**: Swap the entire visual language—palette, verified macOS/CSS typography, CoreImage + FFmpeg color grades, film grain/vignette/gate-weave textures, portrait layouts, graphic cards, transition grammar, pacing limits, and audio ducking—across `cinema_doc`, `analog_scrapbook`, `wes_postcard`, `kinetic_montage`, `editorial_bento`, `super8_nostalgia`, and `sitcom_cast`.
- **Video-First Stage 0 Timeline Strips (`1800x360`)**: Replaces 1-frame video thumbnails with multi-frame upright strips (4/6/8 frames + aligned 8 kHz mono RMS audio bar flagging `[SONIC POSTCARD]` laughter/ambient peaks and `[WIND]` mute hazards) plus a **7-Tier Creation Date Waterfall** (`Keys:CreationDate` -> EXIF -> QuickTime `mvhd` with GoPro quirk guard -> filename -> `mdls` -> sequence interpolation). Cuts VLM triage tokens by ~80% with zero raw 4K uploads.
- **Gate 2 4-Card Visual Style-Pack Carousel**: Renders 4 composite `1280x720` preview cards from the user's real hero and portrait footage showing actual graded chapter titles, native portrait layouts, palette swatches, target BPM, and transition vibe.
- **EDL v2.0 & Dynamic Video Engine**: Adds closed-form 5-phase cubic speed ramps (`speed_profile`), synchronized `split_2up_video` / `triptych_3up_video` / `bento_living_grid` / `polaroid_stack` / `super8_frame` layouts (`tiles[]`), live moving overlap transitions (`dissolve`, `whip_pan_*`, `light_leak`, `film_burn`, `flash_cut`, `zoom_punch`, `paper_wipe`), 4-track J/L-cut + Sonic Postcard audio muxing, and style-aware `graphic` cards (`chapter_title`, `route_map` with elevation sparkline, `trip_wrapped`, `cast_intro`, `text_behind_subject`, `blooper_title`).
- **M5 / Apple Silicon Video-First Safeguards**: Hardware 4K-to-1080p HDR/Dolby Vision-to-Rec.709 decoding (`AVAssetReaderVideoCompositionOutput`), **5-6 video shots per exiting subprocess** under `nice -n 15`, 2-step lossless audio mux (`AVAssetExportPresetAppleM4A` + `AVAssetExportPresetPassthrough`), and frame-locked FFmpeg `xfade` offsets eliminating cumulative A/V drift.

## Key Features

- **Flexible Input**: Local directory, explicit file list, or Google Drive folder URL (via the `gdrive` plugin).
- **4-Gate Interactive `/grill-me` Workflow**: Approve video timeline strips & VIP faces, choose from the 4-Card Style-Pack Carousel, audition BPM-matched BGM, and verify the linted EDL v2.0 storyboard + `$` cost cap before final rendering.
- **Safety & Authenticity**:
  - Read-only raw media (`PreToolUse` hook blocks destructive `rm`/`mv`/`ffmpeg -y` overwrites on original files).
  - Zero generative Image-to-Video (I2V) on real human faces (`has_human_closeup: true`) — people always get deterministic supersampled motion or parallax.
  - Verified EXIF stats only on `trip_wrapped` cards (metrics with `< 30%` coverage are hidden; nothing is invented).
  - `$0` local-only mode by default, or Hybrid mode (`gemini-omni-1.2-flash` I2V strictly on user-approved non-human scenic hero stills).
- **Chunked Multimodal QA & 3-Persona `/swarm` Review Panel**: Automated `-sseof -0.05` end-frame sheets (`qa_sheet_*.jpg`), `<= 60s` review chunks, 100% video mid-frame alignment (`qa_video_alignment.jpg`), and 100% graphic card (`qa_chapter_cards.jpg`) inspection, followed by a 3-persona `/swarm` panel (Movie Director, Cinematographer, Family Member) before delivery or Drive upload.

## Installation

Install directly from this repository using the Antigravity CLI:

```bash
agy plugin install https://github.com/nikeshthapa255/Video-creator-skills
```

Or install locally from a cloned copy:

```bash
agy plugin install ./trip-cinema-studio
```

## Prerequisites

- **Standard Stack**: `python3`, `ffmpeg` & `ffprobe` (with `zscale`/`tonemap`; `vidstab` recommended), `exiftool` (for `QuickTimeUTC` + `Keys:CreationDate`), `node` & `npx` (for Remotion route maps, elevation sparklines, and Trip Wrapped cards).
- **Zero-Dependency macOS Native Engine**: On clean or locked-down macOS hosts without `ffmpeg`, `python3`, or `node`, Trip Cinema Studio ships an Apple-signed `osascript -l JavaScript` (JXA + `AVFoundation`/`CoreImage`/`ImageIO`/`AppKit`/`Vision` + `/usr/bin/afconvert`) engine under `skills/trip-cinema/scripts/macos_native/`.
- **Optional Antigravity Plugins**:
  - `gdrive` — for downloading trip folders directly from a Google Drive URL
  - `gemini-api` — for `lyria-3.5` BGM previews (`$0.08`/track) and `gemini-omni-1.2-flash` scenic I2V

Run the built-in preflight check at any time:

```bash
python3 skills/trip-cinema/scripts/preflight.py
```

## Architecture

```text
trip-cinema-studio/
├── plugin.json                      # v0.2.0 manifest
├── hooks.json
├── hooks/
│   ├── raw_media_guard.sh           # PreToolUse: protects raw media from destructive commands
│   └── qa_stop_guard.sh             # Stop: ensures QA verification before final handoff
├── rules/
│   └── AGENTS.md                    # Always-on safety, 4-gate HITL, and disk-state rules
├── agents/
│   ├── trip-ingestor.md             # Stage 0 video timeline strips, 7-tier dates, ST-DBSCAN chapters
│   ├── trip-director.md             # Gate 2 Style Carousel, BGM sync, EDL v2.0 & lint_edl.py
│   ├── trip-qa-critic.md            # Video alignment, cards, transitions, audio & <=60s QA critic
│   ├── review-director.md           # Stage 4 /swarm persona: story arc, pacing & bar sync
│   ├── review-cinematographer.md    # Stage 4 /swarm persona: color, framing, cards & orientation
│   └── review-family-member.md      # Stage 4 /swarm persona: warmth, inclusivity & breathing room
└── skills/trip-cinema/
    ├── SKILL.md                     # 5-phase Video-First production runbook
    ├── styles/                      # style_schema.json + 7 declarative Style Pack presets
    │   ├── style_schema.json
    │   ├── cinema_doc.json
    │   ├── analog_scrapbook.json
    │   ├── wes_postcard.json
    │   ├── kinetic_montage.json
    │   ├── editorial_bento.json
    │   ├── super8_nostalgia.json
    │   └── sitcom_cast.json
    ├── scripts/                     # Deterministic Python/FFmpeg pipeline + EDL v2.0 linter
    │   └── macos_native/            # Zero-dependency macOS JXA engine + modular lib/*.js
    ├── remotion_template/           # Style-aware ChapterTitle, TripRouteMap (+elevation), TripWrappedCard
    └── references/                  # EDL v2.0 JSON schema, FFmpeg recipes, Remotion & BGM guides
```

## The 4-Gate Interactive Workflow

1. **Gate 1 — Video Timeline Strips, Chapters & VIP People**: Review bracketed `1800x360` video timeline strips (with `[60fps SLOW-MO]`, `[SONIC POSTCARD]`, `[SHAKE]`, `[WIND]` badges), `3x3` photo contact sheets, detected chapters, VIP faces/nicknames, and optional `blooper` tags; pin must-keep shots (`locked_by_user: true`).
2. **Gate 2 — 4-Card Visual Style-Pack Carousel**: Compare 4 composite `1280x720` Style Pack previews rendered from your real footage and toggle optional storytelling extras (`Trip Wrapped`, `Cast Intros`, `Post-Credits Bloopers`, `30s 9:16 Teaser`).
3. **Gate 3 — BGM & Pacing**: Audition BPM-matched CC0 tracks, optional `lyria-3.5` previews (`$0.08`/track), or a local track with downbeat and RMS energy analysis. **No voiceover.**
4. **Gate 4 — EDL v2.0 Storyboard & Cost Gate**: Inspect the linted shot-by-shot storyboard (layouts, speed ramps, transitions, J/L cuts, sonic postcards) and exact `$` cost cap (`$0` in local mode) before batched rendering.

## Stage 4 `/swarm` Film Review Panel

Before final delivery or Google Drive upload, run a 3-persona `/swarm` review panel over `./work/qa/qa_sheet_*.jpg`, `./work/qa/qa_video_alignment.jpg`, and `./work/qa/qa_chapter_cards.jpg`:

- **Movie Director** (`agents/review-director.md`): Story arc, wave pacing, opening pre-hook, chapter flow, musical bar sync, speed-ramp restraint, and closing emotional landing.
- **Cinematographer** (`agents/review-cinematographer.md`): Style Pack color/texture cohesion across mixed cameras, HDR-to-SDR Rec.709 tones, framing, multi-video grid balance, upright video orientation (`qa_video_alignment.jpg`), and graphic card typography & legibility (`qa_chapter_cards.jpg`).
- **Family Member** (`agents/review-family-member.md`): Watches as a loved one on a living-room TV—checking sentimental warmth, inclusive moments for every traveler/elder/child, sonic postcard realism, and emotional breathing room.
