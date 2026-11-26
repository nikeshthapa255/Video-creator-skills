---
name: trip-cinema
description: >-
  Turn a local folder, file list, or Google Drive folder of raw trip photos and
  80%-video 4K clips into a finished 16:9 (or 9:16 teaser) styled cinematic MP4
  (-14 LUFS, no voiceover) with 7 declarative Style Packs, Video-First Timeline
  Strips, 4-gate interactive review, speed ramps, multi-video grids, 4-track
  J/L-cut audio, and chunked multimodal QA.
---

# Trip Cinema Studio Runbook (v0.2.0)

Produce a standalone **16:9 horizontal cinematic MP4** (1080p or 4K, `-14 LUFS`, `LRA=9`, **no voiceover**) or an optional **9:16 vertical teaser** (`1080x1920`, `aspect_profile: "9:16"`) from raw trip media. Never modify raw media; keep all working state in `./work/`.

## Declarative Style Packs (`styles/*.json`)

Every film selects one declarative Style Pack preset under `styles/<style_id>.json` (validated against `styles/style_schema.json`) controlling palette, verified macOS/CSS typography, CoreImage + FFmpeg color grade, film grain/vignette/gate-weave textures, portrait & multi-video layouts, chapter/map/wrapped cards, transition grammar, per-style `max_consecutive_photos` pacing rules, and audio ducking:

| `style_id` | Signature Look & Layouts | Cadence & Transitions | Best Fit |
| :--- | :--- | :--- | :--- |
| **`cinema_doc`** | Natural raw-premium cinema, editorial gold serif chapter cards over blurred backdrops, dark topo route map with elevation sparkline. | 70-95 BPM; 2.0-5.5s holds, max 2 consecutive photos; J/L bridges + soft dissolves + sonic postcards. | Cultural journeys, mountain treks, heritage trips. |
| **`analog_scrapbook`** | Warm kraft canvas, tilted Polaroids (`polaroid_stack`) with masking tape, 12fps stepped overlays, typewriter EXIF stamps. | 95-115 BPM; 1.5-3.5s holds, max 6 consecutive photos; paper wipes + shutter flashes. | City food crawls, friends getaways, photo-heavy trips. |
| **`wes_postcard`** | Symmetrical pastel postcard frames (`Budapest Pastel` / `Darjeeling 70s`), Futura Bold Roman-numeral cards, locked frames. | 110-130 BPM; 1.2-3.0s staccato cuts, max 4 consecutive photos; 90-deg whip-pans. | Rail journeys, hill stations, boutique hotels, architecture. |
| **`kinetic_montage`** | High-contrast filmic grade, optional 2.39:1 matte, bold condensed `text_behind_subject` titles, 3-up vertical triptychs. | 120-145 BPM; 0.4-2.4s wave bursts, cubic speed ramps; whip-pans + zoom punches. | 80%-video treks, road trips, festivals, 30s `9:16` teasers. |
| **`editorial_bento`** | Quiet-luxury linen canvas, `1px` hairlines, living 2/3/4-up `bento_living_grid` mixing video + stills, micro EXIF badges. | 80-105 BPM; calm 2.8-4.8s holds, max 5 consecutive photos; architectural dissolves. | Design/culinary cities, honeymoons, photography trips. |
| **`super8_nostalgia`** | Kodak warm highlights, lifted blacks, halation, rounded `super8_frame` overscan, gate weave, retro date stamp. | 85-110 BPM; 2.0-3.8s holds, 18fps overlay step; film burns + warm light leaks. | Beach holidays, family reunions, candid kids trips. |
| **`sitcom_cast`** | Bright cheerful daylight tones, freeze-frame `cast_intro` nameplates, preserved laughter, post-credits `blooper_title` stinger. | 115-135 BPM; 1.1-2.6s lively montage; flash cuts + quick blooper outtakes. | Big family vacations, friend-group holidays, road trips. |

## Phase 0: Preflight & Input Resolution

1. Run tool discovery:
   ```bash
   python3 scripts/preflight.py
   ```
   (On clean macOS hosts without `python3`, `ffmpeg`, or `node`, run the zero-dependency Apple-signed JXA scripts under `scripts/macos_native/` directly.)
2. Resolve the media source:
   - **Local folder / file list**: pass directly to Stage 0 ingestion.
   - **Google Drive folder URL**: download into `./work/raw_drive/` using the `gdrive` plugin first.
3. Ask the user for their **budget mode**: `$0` local-only (deterministic motion/grids/ramps) or **Hybrid** (adds optional `gemini-omni-1.2-flash` I2V strictly on user-approved non-human scenic hero stills).

## Phase 1: Video-First Stage 0 Timeline Strips & Gate 1

For video-heavy trips (especially 60-150 raw 4K clips), never rely on single mid-frame thumbnails or upload raw 4K clips to a VLM. Generate compact **`1800x360` Multi-Frame Video Timeline Strips** (4/6/8 upright frames + aligned `/usr/bin/afconvert` or FFmpeg 8 kHz mono audio RMS bar flagging `speech_laughter` `[SONIC POSTCARD]` peaks and `wind_hazard` mute zones) using the **7-Tier Creation Date Waterfall** (`Keys:CreationDate` -> EXIF `DateTimeOriginal` -> QuickTime `mvhd` with GoPro quirk guard -> filename `PXL_`/`VID_`/`DJI_` -> `mdls` -> camera sequence interpolation -> unverified `mtime` flag).

1. **Standard stack (Python/FFmpeg)**:
   ```bash
   python3 scripts/ingest_trip.py --input-dir <path> --work-dir ./work --video-strips
   ```
   (Use `--file-list <file>` when given explicit paths.)
2. **Zero-dependency macOS Native stack (`nice -n 15`, batched 12-15 clips per exiting subprocess)**:
   ```bash
   # Step 1: Fast metadata index (7-tier date waterfall, 60fps slow-mo, HDR, upright size, GPS)
   nice -n 15 osascript -l JavaScript scripts/macos_native/macos_video_strips.jxa \
     --input-dir <path> --work-dir ./work --index-only

   # Step 2: Generate 1800x360 video timeline strips in batches of 12-15 clips
   nice -n 15 osascript -l JavaScript scripts/macos_native/macos_video_strips.jxa \
     --manifest ./work/manifest_v2.json --out-dir ./work/clip_strips --batch-start 0 --batch-end 15

   # Step 3: Generate upright 3x3 numbered photo contact sheets (#P001...)
   nice -n 15 osascript -l JavaScript scripts/macos_native/macos_video_strips.jxa \
     --photos-only --manifest ./work/manifest_v2.json --out-dir ./work/photo_sheets
   ```
3. **Sub-Clip Saliency Mining**: Triage `./work/clip_strips/strip_*.jpg` in parallel (15-20 strips per worker) to pick the golden `[in_sec, out_sec]` brackets (`2.0-5.0s`), flag `[60fps SLOW-MO]` (`fps >= 50`), `[SONIC POSTCARD]` laughter/ambient windows, `[SHAKE]` salvage cuts (`1.2-1.6s`), `[WIND]` auto-mute clips, and playful `blooper: true` outtakes.
4. Present **Gate 1 Artifact** (`UserFacing: true`, `RequestFeedback: true`) containing:
   - Detected chronological/GPS chapters and dates (plus any unverified-date clips needing confirmation).
   - Shortlisted `1800x360` video timeline strips with proposed `[in -> out]` brackets and badges, plus `3x3` photo contact sheets.
   - Recurring faces for VIP selection (with optional nickname/role for `cast_intro`) and borderline rejects / optional `blooper: true` tags.
5. Wait for user approval. Persist pins (`locked_by_user: true`), shifted `[in, out]` brackets, and blooper flags to `./work/manifest.json` (or `manifest_v2.json`) and `./work/gates_state.json`.

## Phase 2: Gate 2 — 4-Card Visual Style-Pack Carousel

1. Pick the 4 best-fit Style Packs from `styles/*.json` based on the trip's video/photo ratio, setting, and cast size, then render **4 composite `1280x720` Style Preview Cards** using the user's real hero landscape and portrait photos/video frames:
   ```bash
   # Zero-dependency macOS Native 4-Card Style-Pack Carousel:
   osascript -l JavaScript scripts/macos_native/macos_style_previews.jxa \
     --styles-dir styles \
     --style-ids cinema_doc,kinetic_montage,analog_scrapbook,super8_nostalgia \
     --hero <hero_img_or_video> --portrait-a <portrait_a> --portrait-b <portrait_b> \
     --trip-title "<Trip Title>" --out-dir ./work/style_previews

   # Cross-platform look preview fallback:
   python3 scripts/generate_look_previews.py --manifest ./work/manifest.json --out-dir ./work/look_previews
   ```
   Each `1280x720` card shows:
   - **Left 60%**: Real hero graded and textured with that pack's Chapter Title font, accent color, and matte/grain.
   - **Right 40%**: Real portraits rendered in that pack's native layout (diptych, tilted Polaroids, Bento tiles, Super 8 frame, or multi-video split).
   - **Footer 56px**: Palette swatches, style name, target BPM range, and transition signature.
2. Present **Gate 2 Artifact** (`UserFacing: true`, `RequestFeedback: true`) with the 4 Style Preview Cards plus toggles for optional story devices (`trip_wrapped` EXIF stats card, `cast_intro` cards, post-credits `blooper_title` stinger, and optional 30s `9:16` teaser).
3. Wait for user selection and save `style_id`, optional `look_id` override, portrait layout, and story toggles to `./work/gates_state.json`.

## Phase 3: BGM & Pacing — Gate 3

1. Gather candidate music matching the chosen Style Pack's `pacing_rules.target_bpm_label` and `audio.music_genre_hint` (see `references/bgm_sources.md` or run `python3 scripts/bgm_analyzer.py catalog`):
   - Open-source/CC0 suggestions,
   - Optional 2-3 `lyria-3.5` previews (`$0.08`/track via `gemini-api music`), or
   - A user-supplied local audio file.
2. Analyze BPM, downbeats, and energy drops:
   ```bash
   python3 scripts/bgm_analyzer.py analyze --input <track_path> --output ./work/beat_map.json
   ```
3. Present **Gate 3 BGM Picker Artifact** (`UserFacing: true`, `RequestFeedback: true`) with auditionable previews, BPM, drops, and pacing curve. **No voiceover.** Wait for approval.

## Phase 4: Video-First Storyboard, EDL v2.0 & Lint — Gate 4

1. Author `./work/edl.json` conforming to **EDL v2.0** (`references/edl_schema.json`) with `"version": "2.0"` and `"style_id"`:
   - **Energy-Sync & Wave Pacing**: Match shot velocity to the music energy curve; alternate high-energy montage bursts (max 4-6 fast `0.4-1.5s` cuts) with breathing holds (`3.0-5.5s`) and 3-shot micro-stories (`Wide -> Medium -> Close-Up`).
   - **Beat-Locked Cuts**: Snap cuts to bar/half-bar downbeats (`<= 60ms` ideal) and cut on action 3-5 frames before movement completes.
   - **Closed-Form Speed Ramps (`speed_profile`)**: Only use `mode: "cubic_ramp"` or slow-mo (`rate < 0.85`) on `>= 50fps` footage with real camera/subject motion, capped at `<= 10-20%` of video shots. Never speed-ramp dialogue, laughter (`sonic_postcard: true`), emotional faces, or 24/30fps footage. Mute raw audio (`audio.mute: true`) when speed exceeds `1.3x`. Exact required source span:
     `S_req = v_hi*d1 + ((v_hi + v_lo)/2)*d2 + v_lo*d3 + ((v_lo + v_hi)/2)*d4 + v_hi*d5`.
   - **Multi-Video & Hybrid Layouts (`layout` + `tiles[]`)**: Use `split_2up_video`, `triptych_3up_video`, `bento_living_grid` (mixing live video + crisp stills), `polaroid_stack`, `super8_frame`, `split_diptych_pair`, or `blurred_bg_pillarbox` so vertical clips become kinetic features.
   - **Live Moving Overlap Transitions (`transition_out`)**: Choose from `cut`, `dissolve`, `whip_pan_left/right/up/down`, `light_leak`, `film_burn`, `flash_cut`, `zoom_punch`, `paper_wipe`. Leave `>= 0.4s` outgoing tail headroom and `>= 0.4s` incoming head headroom (`in_sec >= 0.4`) on every non-cut transition between videos.
   - **4-Track J/L-Cut & Sonic Postcard Audio (`audio`)**: Set `j_cut_sec` / `l_cut_sec` (`0.4-1.5s`, respecting source head/tail headroom) for seamless scene bridges, flag 2-4 authentic `sonic_postcard: true` pockets (`duck_db: -13` to `-15`), and mute wind-hazard clips (`mute: true`).
   - **Style-Aware Graphic Cards (`media_type: "graphic"`)**: Use `chapter_title` (blurred slow-zooming `backdrop_path`, never plain black), `route_map` (with optional `waypoints[].elevation_m` + `elevation_profile`), `trip_wrapped` (verified EXIF `stats[]` only; hide any metric with `< 30%` coverage), `cast_intro`, `text_behind_subject` (with optional `mask_path`), and `blooper_title`.
   - **Human Face Safety**: Never apply generative I2V to `has_human_closeup: true` shots or tiles.
2. Validate deterministically with EDL v2.0 linter:
   ```bash
   python3 scripts/lint_edl.py --edl ./work/edl.json --manifest ./work/manifest.json --beat-map ./work/beat_map.json
   ```
   Verifies Style Pack `max_consecutive_photos`, cubic speed-ramp `S_req`, `>= 0.4s` transition handles, J/L-cut handles, multi-video `tiles[]`, graphic cards, `locked_by_user` pins, and overlap-adjusted beat sync.
3. Present **Gate 4 Visual Storyboard & Cost Artifact** (`UserFacing: true`, `RequestFeedback: true`) with shot-by-shot layout/speed/transition/audio summary and exact `$` cost cap (`$0` in local mode). Wait for explicit approval.

## Phase 5: Batched Video Render, Lossless 4-Track Mux & Chunked QA

1. **Optional Vision Foreground Masks (macOS 14+, for 2-6 hero `text_behind_subject` / `cast_intro` stills)**:
   ```bash
   osascript -l JavaScript scripts/macos_native/macos_extract_masks.jxa --edl ./work/edl.json --out-dir ./work/masks
   ```
   (Skips cleanly if unused; renderer falls back gracefully to magazine overlay / tilted Polaroid when no `mask_path` exists.)
2. **Render Master MP4**:
   - **Cross-platform Python/FFmpeg + optional Remotion**:
     ```bash
     python3 scripts/render_trip.py --edl ./work/edl.json --output ./output/trip_cinema.mp4 --work-dir ./work
     ```
   - **Zero-Dependency macOS Native Video-First Batched Render (`5-6` video shots per exiting subprocess under `nice -n 15`)**:
     Keep every batch to **5-6 video shots** (snapping batch boundaries to `transition_out: "cut"` hard cuts, with a 1-second cooldown between batches) so peak RSS stays `< 1.2 GB` and Apple Silicon stays responsive while decoding 4K HDR/Dolby Vision via hardware Rec.709 `AVAssetReaderVideoCompositionOutput`:
     ```bash
     nice -n 15 osascript -l JavaScript scripts/macos_native/macos_render_trip.jxa \
       --edl ./work/edl.json --start-shot 0 --end-shot 6 --video-only --output ./work/seg_01.mp4
     nice -n 15 osascript -l JavaScript scripts/macos_native/macos_render_trip.jxa \
       --edl ./work/edl.json --start-shot 6 --end-shot 12 --video-only --output ./work/seg_02.mp4
     # ...repeat sequentially per 5-6 shot batch, then run 2-step lossless 4-track audio mux:
     nice -n 15 osascript -l JavaScript scripts/macos_native/macos_render_trip.jxa \
       --edl ./work/edl.json --mux-segments ./work/seg_01.mp4,./work/seg_02.mp4 --output ./output/trip_cinema.mp4
     ```
     (`--mux-segments` exports the 4-track `NAT_A`/`NAT_B`/`BGM`/`SFX` mix via `AVAssetExportPresetAppleM4A`, then muxes video segments + `.m4a` via `AVAssetExportPresetPassthrough` with zero video re-encode.)
3. **Extract QA Media (`--edl`)**:
   ```bash
   python3 scripts/render_trip.py --extract-qa ./output/trip_cinema.mp4 --edl ./work/edl.json --work-dir ./work
   # Zero-dependency macOS fallback:
   osascript -l JavaScript scripts/macos_native/macos_extract_qa.jxa \
     --input ./output/trip_cinema.mp4 --edl ./work/edl.json --out-dir ./work/qa
   ```
4. **Mandatory Video Alignment, Style & Orientation QA Gate**: Inspect **100% of video clip mid-frames** (`./work/qa/qa_video_alignment.jpg`), **100% of chapter/route/wrapped/cast cards** (`./work/qa/qa_chapter_cards.jpg`), end-frame contact sheets (`./work/qa/qa_sheet_*.jpg`), and every `<= 60s` chunk with `read_file`. Check upright orientation, HDR-to-SDR Rec.709 skin tones, multi-video grid alignment, speed-ramp smoothness, transition continuity, card legibility, and A/V sync.
5. **3-Persona `/swarm` Film Review Panel (Before Delivery / Drive Upload)**: Spawn `agents/review-director.md`, `agents/review-cinematographer.md`, and `agents/review-family-member.md` to independently review the Stage 4 QA sheets and `<= 60s` chunks. Resolve all `REVISE` notes, re-render and re-extract QA sheets if edits were made, and only write `./work/qa_passed.flag` and deliver/upload once all three personas return `PASS`.
