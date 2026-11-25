---
name: trip-cinema
description: >-
  Turn a local folder, file list, or Google Drive folder of raw trip photos and
  video clips into a finished 16:9 cinematic MP4 (-14 LUFS, no voiceover) with
  a mandatory 4-gate interactive artifact review, Remotion GPS route maps, and
  chunked multimodal QA.
---

# Trip Cinema Studio Runbook

Produce a standalone **16:9 horizontal cinematic MP4** (1080p or 4K, `-14 LUFS`, `LRA=9`, **no voiceover**) from raw trip media. Never modify raw media; keep all state in `./work/`.

## Phase 0: Preflight & Input Resolution

1. Run tool discovery:
   ```bash
   python3 scripts/preflight.py
   ```
2. Resolve the media source:
   - **Local folder / file list**: pass directly to `ingest_trip.py`.
   - **Google Drive folder URL**: download into `./work/raw_drive/` using the `gdrive` plugin first.
3. Ask the user for their **budget mode**: `$0` local-only (deterministic FFmpeg Ken Burns & 2.5D parallax) or **Hybrid** (adds optional `gemini-omni-1.2-flash` I2V on non-human scenic hero stills only).

## Phase 1: Ingest & Gate 1 (Chapters & VIP People)

1. Run ingestion and chapter clustering:
   ```bash
   python3 scripts/ingest_trip.py --input-dir <path> --work-dir ./work
   ```
   (Use `--file-list <file>` when given explicit paths.)
2. Present **Gate 1 Artifact** (`UserFacing: true`, `RequestFeedback: true`) containing:
   - Detected chronological/GPS chapters and dates from `./work/manifest.json`.
   - Embedded `3x3` contact sheets (`./work/contact_sheets/chapter_*.jpg`).
   - Recurring faces for VIP selection and borderline rejects.
3. Wait for user approval. Record pinned must-keep shots as `locked_by_user: true` in `./work/manifest.json` and `./work/gates_state.json`.

## Phase 2: Look, Title & Layout — Gate 2

1. Generate color-grade and vertical-layout previews from actual trip hero stills:
   ```bash
   python3 scripts/generate_look_previews.py --manifest ./work/manifest.json --out-dir ./work/look_previews
   # Zero-dependency macOS fallback (when ffmpeg/python3 are unavailable):
   osascript -l JavaScript scripts/macos_native/macos_look_previews.jxa --manifest ./work/manifest.json --out-dir ./work/look_previews
   ```
2. Present **Gate 2 Artifact** (`UserFacing: true`, `RequestFeedback: true`) with:
   - Side-by-side graded stills (Warm Film, Moody Teal-Orange, Clean Natural, Soft Atmospheric).
   - Minimalist EXIF chapter title + Remotion GPS route map mockup (`references/remotion_templates.md`).
   - Vertical `9:16` handling (`split_diptych_pair` vs. `blurred_bg_pillarbox`).
3. Wait for user selection and save to `./work/gates_state.json`.

## Phase 3: BGM & Pacing — Gate 3

1. Gather candidate music (see `references/bgm_sources.md` or run `python3 scripts/bgm_analyzer.py catalog`):
   - Open-source/CC0 suggestions,
   - Optional 2-3 `lyria-3.5` previews (`$0.08`/track via `gemini-api music`), or
   - A user-supplied local audio file.
2. Analyze BPM, downbeats, and energy drops:
   ```bash
   python3 scripts/bgm_analyzer.py analyze --input <track_path> --output ./work/beat_map.json
   ```
3. Present **Gate 3 BGM Picker Artifact** (`UserFacing: true`, `RequestFeedback: true`) with auditionable previews, BPM, drops, and pacing curve. **No voiceover.** Wait for approval.

## Phase 4: Storyboard, Cost Gate 4 & Lint

1. Author `./work/edl.json` following `references/edl_schema.json`:
   - Beat-lock cuts to `./work/beat_map.json`.
   - Never apply generative I2V to `has_human_closeup: true` shots.
   - **Visual Editorial Chapter Cards**: Every chapter/day card MUST pair serif/gold editorial typography (`eyebrow`, `title`) and animated dividers over a softly blurred, vignetted, slow-zooming hero photo (`backdrop_path`) from that chapter — never plain black slides. Set `fade_in` and `fade_out` durations.
   - If Hybrid I2V is approved, draft at `360p` before `1080p`/`4K` final.
2. Validate deterministically:
   ```bash
   python3 scripts/lint_edl.py --edl ./work/edl.json --manifest ./work/manifest.json --beat-map ./work/beat_map.json
   ```
3. Present **Gate 4 Visual Storyboard & I2V Cost Artifact** (`UserFacing: true`, `RequestFeedback: true`) with shot-by-shot visual strip, exact durations, motion types, and exact `$` cost cap. Wait for explicit approval.

## Phase 5: Render & Chunked Multimodal QA

1. Render Remotion GPS route map & chapter title overlays (`references/remotion_templates.md`), or use the macOS native CoreImage/AVFoundation engine (`scripts/macos_native/`) when `ffmpeg`/`node` are unavailable.
2. Render the 16:9 master MP4 (`references/ffmpeg_recipes.md`):
   ```bash
   python3 scripts/render_trip.py --edl ./work/edl.json --output ./output/trip_cinema.mp4 --work-dir ./work
   ```
   **Hardware-Friendly Batched Rendering on macOS**: When using `macos_render_trip.jxa`, render sequentially in ~7–10 shot batches under `nice -n 15` (`--start-shot` / `--end-shot --video-only`), then `--mux-segments` so Apple Silicon laptops never freeze or spike GPU/RAM:
   ```bash
   nice -n 15 osascript -l JavaScript scripts/macos_native/macos_render_trip.jxa --edl ./work/edl.json --start-shot 0 --end-shot 10 --video-only --output ./work/seg_01.mp4
   # ...repeat sequentially per ~7–10 shot batch, then mux:
   nice -n 15 osascript -l JavaScript scripts/macos_native/macos_render_trip.jxa --edl ./work/edl.json --mux-segments ./work/seg_01.mp4,./work/seg_02.mp4 --output ./output/trip_cinema.mp4
   ```
3. Extract end-frame contact sheets (`-sseof -0.05`), `<=60s` QA chunks, and 100% alignment/card sheets (`--edl`):
   ```bash
   python3 scripts/render_trip.py --extract-qa ./output/trip_cinema.mp4 --edl ./work/edl.json --work-dir ./work
   # Zero-dependency macOS fallback:
   osascript -l JavaScript scripts/macos_native/macos_extract_qa.jxa --input ./output/trip_cinema.mp4 --edl ./work/edl.json --out-dir ./work/qa
   ```
4. **Mandatory Video Alignment & Orientation QA Gate**: Inspect **100% of video clip mid-frames** (`./work/qa/qa_video_alignment.jpg`), **100% of chapter/route cards** (`./work/qa/qa_chapter_cards.jpg`), end-frame contact sheets (`./work/qa/qa_sheet_*.jpg`), and every `<=60s` chunk with `read_file`. Explicitly check for upside-down or 90°-rotated portrait videos (top-left `preferredTransform` vs bottom-left Quartz Y-axis inversion on macOS) and verify chapter cards use blurred photo backdrops (`backdrop_path`, never plain black).
5. **3-Persona `/swarm` Film Review Panel (Before Delivery / Drive Upload)**: Spawn a `/swarm` review panel with all three personas—`agents/review-director.md` (Movie Director), `agents/review-cinematographer.md` (Cinematographer), and `agents/review-family-member.md` (Family Member)—to independently inspect `./work/qa/qa_sheet_*.jpg`, `./work/qa/qa_video_alignment.jpg`, and `./work/qa/qa_chapter_cards.jpg` (plus `<=60s` chunks as needed). Resolve any `REVISE` notes, re-render and re-extract QA sheets if edits were made, and only write `./work/qa_passed.flag` and proceed to final delivery or Drive upload once all three personas return `PASS`.
