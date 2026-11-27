# macOS native commands (zero-dependency Apple-signed JXA)

Read this guide when `uname -s` is `Darwin` and you want zero third-party binaries or Apple Silicon hardware Rec.709 decoding (`AVAssetReaderVideoCompositionOutput` for 4K HDR HLG and Dolby Vision). All scripts under `scripts/macos_native/` run via `osascript -l JavaScript` using Apple-signed `AVFoundation`, `CoreImage`, `Vision`, and `AppKit`.

## Phase 0: Preflight and media ingestion

- If Python 3 and FFmpeg are absent on the Mac, skip `scripts/preflight.py` and invoke the JXA scripts below directly.
- For Google Drive folder URLs, download read-only copies into `./work/raw_drive/` via the `gdrive` plugin first.
- For local folders, pass `--input-dir <path>` directly without modifying raw media.

## Phase 1: Stage 0 timeline strips and photo sheets (`nice -n 15`)

Run video strip generation in exiting batches of 12 to 15 clips under `nice -n 15` so memory stays bounded on Apple Silicon:

```bash
# Step 1: Fast metadata index (7-tier date waterfall, 60fps slow-mo, HDR, upright size, GPS)
nice -n 15 osascript -l JavaScript scripts/macos_native/macos_video_strips.jxa \
  --input-dir <path> --work-dir ./work --index-only

# Step 2: Render 1800x360 video timeline strips in batches of 12-15 clips (repeat per batch)
nice -n 15 osascript -l JavaScript scripts/macos_native/macos_video_strips.jxa \
  --manifest ./work/manifest_v2.json --out-dir ./work/clip_strips --batch-start 0 --batch-end 15

# Step 3: Render upright 3x3 numbered photo contact sheets (#P001...)
nice -n 15 osascript -l JavaScript scripts/macos_native/macos_video_strips.jxa \
  --photos-only --manifest ./work/manifest_v2.json --out-dir ./work/photo_sheets
```

Audio profiling uses `/usr/bin/afconvert` (8 kHz mono PCM RMS bar) to flag `speech_laughter` `[SONIC POSTCARD]` peaks and `wind_hazard` mute zones. After Gate 1 approval, persist pins (`locked_by_user: true`), shifted `[in, out]` brackets, and privacy bans (`excluded_ids`, `excluded_paths`, `forbidden_video_ranges`) to `./work/manifest_v2.json` and `./work/gates_state.json`.

## Phase 2: Gate 2 style-pack carousel (`1280x720` PNG cards)

Render four `1280x720` Style Preview Cards using real hero and portrait photos or video clips:

```bash
osascript -l JavaScript scripts/macos_native/macos_style_previews.jxa \
  --styles-dir styles \
  --style-ids cinema_doc,kinetic_montage,analog_scrapbook,super8_nostalgia \
  --hero <hero_photo_or_video> \
  --portrait-a <portrait_a_photo_or_video> \
  --portrait-b <portrait_b_photo_or_video> \
  --trip-title "<Trip Title>" \
  --out-dir ./work/style_previews
```

## Phase 3 and Phase 4: Beat analysis and EDL lint

If `python3` is installed on macOS, run beat analysis and deterministic EDL linting with Gate 1 privacy enforcement before Gate 4:

```bash
python3 scripts/bgm_analyzer.py analyze --input <track_path> --output ./work/beat_map.json
python3 scripts/lint_edl.py \
  --edl ./work/edl.json \
  --manifest ./work/manifest_v2.json \
  --beat-map ./work/beat_map.json \
  --gates-state ./work/gates_state.json
```

If `python3` is unavailable, verify all EDL v2.0 constraints manually against `references/edl_schema.json` and `./work/gates_state.json` (especially `excluded_ids`, `excluded_paths`, `forbidden_video_ranges`, `>=0.4s` transition handles, and `<=1.85x` tile aspect match) before rendering.

## Phase 5: Vision masks, cut-aligned batched render, lossless mux, and QA

### 1. Optional Vision foreground masks (macOS 14+)

Extract alpha masks for 2 to 6 hero `text_behind_subject` or `cast_intro` stills (skip if unused; the renderer falls back to clean editorial or Polaroid framing without a mask):

```bash
osascript -l JavaScript scripts/macos_native/macos_extract_masks.jxa \
  --edl ./work/edl.json --out-dir ./work/masks
```

### 2. Batched video render (5 to 6 shots per exiting subprocess)

Keep every batch to **5 to 6 video shots**, snapping batch boundaries to `transition_out: "cut"` hard cuts with a 1-second cooldown between batches so peak RSS stays below `1.2 GB`:

```bash
nice -n 15 osascript -l JavaScript scripts/macos_native/macos_render_trip.jxa \
  --edl ./work/edl.json --start-shot 0 --end-shot 6 --video-only --output ./work/seg_01.mp4
sleep 1
nice -n 15 osascript -l JavaScript scripts/macos_native/macos_render_trip.jxa \
  --edl ./work/edl.json --start-shot 6 --end-shot 12 --video-only --output ./work/seg_02.mp4
# Repeat sequentially per 5-6 shot cut-aligned batch
```

### 3. Two-step lossless 4-track audio mux

Export the 4-track `NAT_A`/`NAT_B`/`BGM`/`SFX` mix (`AVAssetExportPresetAppleM4A`) and passthrough-mux all video segments (`AVAssetExportPresetPassthrough`, zero video re-encode):

```bash
nice -n 15 osascript -l JavaScript scripts/macos_native/macos_render_trip.jxa \
  --edl ./work/edl.json \
  --mux-segments ./work/seg_01.mp4,./work/seg_02.mp4 \
  --output ./output/trip_cinema.mp4
```

### 4. Extract QA inspection media

```bash
osascript -l JavaScript scripts/macos_native/macos_extract_qa.jxa \
  --input ./output/trip_cinema.mp4 \
  --edl ./work/edl.json \
  --out-dir ./work/qa
```

If Python 3 and FFmpeg are also installed on macOS, run `python3 scripts/render_trip.py --extract-qa ./output/trip_cinema.mp4 --edl ./work/edl.json --work-dir ./work` instead to also generate `qa_collages.jpg`, `qa_transitions.jpg`, and `qa_metrics.json`.
