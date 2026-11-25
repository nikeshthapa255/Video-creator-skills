# Trip Cinema Studio

An Antigravity plugin that turns a local folder, explicit file list, or Google Drive folder of raw trip photos and video clips into a standalone **16:9 horizontal cinematic MP4** (`-14 LUFS`, `LRA=9`, no voiceover).

## Key Features

- **Flexible Input**: Local directory, explicit file list, or Google Drive folder URL (via the `gdrive` plugin).
- **4-Gate Interactive `/grill-me` Workflow**: Approve chapters & VIP faces, color looks & vertical layouts, BGM & pacing, and the shot-by-shot storyboard + `$` cost cap before final rendering.
- **Safety & Authenticity**:
  - Read-only raw media (`PreToolUse` hook blocks destructive `rm`/`mv`/`ffmpeg -y` overwrites on original files).
  - Zero generative Image-to-Video (I2V) on real human faces — people get deterministic `2x`–`4x` supersampled FFmpeg Ken Burns or 2.5D parallax.
  - `$0` local-only toggle, or Hybrid mode (`gemini-omni-1.2-flash` I2V strictly on user-approved non-human scenic hero stills).
- **Zero-API-Key Graphics**: Remotion `TripRouteMap` (pure SVG/MapLibre-style GPS path) and visual editorial `ChapterTitle` cards (serif/gold typography over a blurred, vignetted, slow-zooming chapter hero photo — never plain black slides).
- **Hardware-Friendly Batched macOS Rendering**: Runs `macos_render_trip.jxa` sequentially in small ~7–10 shot batches (`--start-shot` / `--end-shot --video-only`) under `nice -n 15`, then `--mux-segments`, so Apple Silicon laptops never freeze or spike GPU/RAM.
- **Chunked Multimodal QA & 3-Persona `/swarm` Review Panel**: Automated `-sseof -0.05` end-frame extraction (`qa_sheet_*.jpg`), `<=60s` review chunks, 100% video mid-frame alignment (`qa_video_alignment.jpg`), and 100% chapter/route card (`qa_chapter_cards.jpg`) inspection, followed by a 3-persona `/swarm` panel (Movie Director, Cinematographer, Family Member) before final delivery or Drive upload.

## Installation

Install directly from this repository using the Antigravity CLI:

```bash
agy plugin install https://github.com/<owner>/trip-cinema-studio
```

Or install locally from a cloned copy:

```bash
agy plugin install ./trip-cinema-studio
```

## Prerequisites

- **Standard Stack**: `python3`, `ffmpeg` & `ffprobe` (with `zscale`/`tonemap`; `vidstab` recommended), `exiftool` (for `QuickTimeUTC` + `Keys:CreationDate`), `node` & `npx` (for Remotion route maps and chapter titles).
- **Zero-Dependency macOS Native Engine**: On locked-down or clean macOS systems without `ffmpeg`, `python3`, or `node`, Trip Cinema Studio includes an Apple-signed `osascript -l JavaScript` (JXA + `AVFoundation`/`CoreImage`/`ImageIO`/`AppKit`) fallback engine under `skills/trip-cinema/scripts/macos_native/`.
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
├── plugin.json
├── hooks.json
├── hooks/
│   ├── raw_media_guard.sh       # PreToolUse: protects raw media from destructive commands
│   └── qa_stop_guard.sh         # Stop: ensures QA verification before final handoff
├── rules/
│   └── AGENTS.md                # Always-on safety, 4-gate HITL, and disk-state rules
├── agents/
│   ├── trip-ingestor.md         # Metadata, ST-DBSCAN chaptering, Gate 1 contact sheets
│   ├── trip-director.md         # Narrative arc, Gates 2-4 artifacts, beat-locked EDL
│   ├── trip-qa-critic.md        # End-frame & <=60s chunk multimodal QA critic
│   ├── review-director.md       # Stage 4 /swarm persona: story arc, pacing & bar sync
│   ├── review-cinematographer.md# Stage 4 /swarm persona: color, framing, cards & orientation
│   └── review-family-member.md  # Stage 4 /swarm persona: warmth, inclusivity & breathing room
└── skills/trip-cinema/
    ├── SKILL.md                 # 5-phase production runbook
    ├── scripts/                 # Deterministic Python/FFmpeg pipeline
    │   └── macos_native/        # Zero-dependency macOS JXA (AVFoundation/CoreImage) engine
    ├── remotion_template/       # 16:9 SVG GPS route map & EXIF chapter title compositions
    └── references/              # EDL JSON schema, FFmpeg recipes, Remotion & BGM guides
```

## The 4-Gate Interactive Workflow

1. **Gate 1 — Chapters & VIP People**: Review `3x3` contact sheets, detected chapters, recurring faces, and borderline rejects; pin must-keep shots (`locked_by_user: true`).
2. **Gate 2 — Color Look, Title & Layout**: Compare 4 color grades rendered from your actual trip photos (`Warm Film`, `Moody Teal-Orange`, `Clean Natural`, `Soft Atmospheric`), title/map styling, and `9:16` vertical layouts (`split_diptych_pair` vs. `blurred_bg_pillarbox`).
3. **Gate 3 — BGM & Pacing**: Audition open-source/CC0 tracks, optional `lyria-3.5` previews (`$0.08`/track), or supply a local track with BPM & drop analysis.
4. **Gate 4 — Visual Storyboard & I2V Cost Gate**: Inspect the beat-locked storyboard, exact shot durations, motion types, and exact `$` cost cap (`$0` in local mode) before final 16:9 `-14 LUFS` MP4 rendering.

## Stage 4 `/swarm` Film Review Panel

Before final delivery or Google Drive upload, run a 3-persona `/swarm` review panel over the Stage 4 QA sheets (`./work/qa/qa_sheet_*.jpg`, `./work/qa/qa_video_alignment.jpg`, `./work/qa/qa_chapter_cards.jpg`):

- **Movie Director** (`agents/review-director.md`): Story arc, emotional pacing, opening hook, chapter flow, musical bar sync, shot variety (video vs. still balance), and closing emotional landing.
- **Cinematographer** (`agents/review-cinematographer.md`): Color-grade cohesion across mixed cameras, lighting/contrast, framing/focal anchors, diptych pairing, upright video orientation (`qa_video_alignment.jpg`), and chapter/route card typography & legibility (`qa_chapter_cards.jpg`).
- **Family Member** (`agents/review-family-member.md`): Watches as a loved one on a living-room TV—checking sentimental warmth, inclusive moments for every traveler/elder/child, emotional breathing room, and naturalness.
