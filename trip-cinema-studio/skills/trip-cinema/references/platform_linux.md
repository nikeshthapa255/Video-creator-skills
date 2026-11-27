# Linux and cross-platform commands (Python + FFmpeg)

Read this guide when `uname -s` is `Linux` or when running the Python + FFmpeg stack on any host. Run all commands from the skill directory (or prefix `scripts/` with the installed skill path) while keeping working files in `./work/`.

## Phase 0: Preflight and media ingestion

Check installed binaries (`ffmpeg`, `ffprobe`, `exiftool`, Pillow, optional `remotion`):

```bash
python3 scripts/preflight.py
```

Resolve the input media source before running Stage 0:

- **Local folder or file list**: pass directly via `--input-dir <path>` or `--file-list <file>`.
- **Google Drive folder URL**: download read-only copies into `./work/raw_drive/` with the `gdrive` plugin.
- **Google Photos shared album URL**:
  1. Fetch the shared album HTML and extract each media item's base URL, item ID, and album capture timestamp into `./work/album_index.json` (preserving exact capture dates when downloads strip EXIF/QuickTime tags).
  2. Download full-resolution originals into `./work/raw_gphotos/` by appending `=d` to photo base URLs and `=dv` to video base URLs (never download low-res thumbnails).
  3. Pass `./work/raw_gphotos/` to `ingest_trip.py`, and reconcile any `tier7_mtime_unverified` timestamps against `./work/album_index.json` before Gate 1.

## Phase 1: Stage 0 timeline strips and parallel triage

Generate `./work/manifest.json`, `1800x360` video timeline strips (`./work/clip_strips/strip_*.jpg`), and `3x3` upright photo contact sheets (`./work/contact_sheets/`):

```bash
python3 scripts/ingest_trip.py --input-dir <path> --work-dir ./work --video-strips
```

Optional flags:

- `--file-list <paths.txt>` instead of `--input-dir`
- `--batch-start <idx> --batch-end <idx>` to split large video trips (60 to 150 clips) across batches
- `--eps-km 15 --eps-hours 8` to tune ST-DBSCAN chapter clustering

For large albums, spawn parallel triage workers using [agents/media-triage-worker.md](../../../agents/media-triage-worker.md) (15 to 20 strips or 3 to 5 photo sheets per worker) to mine golden `[in_sec, out_sec]` brackets, `[60fps SLOW-MO]`, `[SONIC POSTCARD]`, `[SHAKE]`, `[WIND]`, `has_human_closeup`, `focal_anchor`, and candidate `blooper: true` clips.

After Gate 1 approval, persist:

- `locked_by_user: true`, `excluded_by_user: true`, brackets, and face flags in `./work/manifest.json`.
- Privacy bans in `./work/gates_state.json` using `excluded_ids`, `excluded_paths`, and `forbidden_video_ranges` (for example, `{"V067": [[12.0, 18.5]]}`).

## Phase 2: Gate 2 style-pack carousel (`1280x720` PNG cards)

Render four `1280x720` composite Style Preview Cards using real trip hero and portrait media (photo or video; HLG and PQ HDR frames decode upright in Rec.709):

```bash
python3 scripts/generate_look_previews.py \
  --style-ids cinema_doc,kinetic_montage,analog_scrapbook,super8_nostalgia \
  --styles-dir styles \
  --hero <hero_photo_or_video> \
  --portrait-a <portrait_a_photo_or_video> \
  --portrait-b <portrait_b_photo_or_video> \
  --trip-title "<Trip Title>" \
  --out-dir ./work/style_previews
```

If `--hero`, `--portrait-a`, or `--portrait-b` are omitted, pass `--manifest ./work/manifest.json` to auto-pick upright hero and portrait media while skipping `excluded_by_user` assets.

## Phase 3: Music scouting and beat analysis

Spawn [agents/bgm-scout.md](../../../agents/bgm-scout.md) (or check `references/bgm_sources.md`) to collect candidate tracks matching the active Style Pack's BPM and genre hint. Analyze each candidate track into `./work/beat_map.json`:

```bash
python3 scripts/bgm_analyzer.py analyze --input <track_path> --output ./work/beat_map.json
```

## Phase 4: EDL v2.0 deterministic lint

Validate `./work/edl.json` before presenting Gate 4. Always pass `--gates-state` so Gate 1 privacy bans and forbidden video ranges fail lint hard:

```bash
python3 scripts/lint_edl.py \
  --edl ./work/edl.json \
  --manifest ./work/manifest.json \
  --beat-map ./work/beat_map.json \
  --gates-state ./work/gates_state.json
```

`lint_edl.py` enforces Style Pack `max_consecutive_photos`, cubic speed-ramp `S_req` and `>=50fps`, `>=0.4s` transition handles, J/L-cut handles, multi-tile slot aspect ratio mismatches (`>1.85x`), `has_human_closeup` tile `focal_anchor` warnings, `rendered_card_path` existence, `locked_by_user` pins, `excluded_ids`/`excluded_paths`, and `forbidden_video_ranges` overlaps (including J/L-cut and transition handles).

## Phase 5: Parallel cached render and EDL-aware QA extraction

Render the master MP4 with parallel workers and content-hash shot caching (`--reuse-cache` skips unchanged `shot_NNN.mp4` clips across revision passes and assembles cut-aligned segments with lossless `-c copy` concat):

```bash
python3 scripts/render_trip.py \
  --edl ./work/edl.json \
  --output ./output/trip_cinema.mp4 \
  --work-dir ./work \
  --workers 4 \
  --reuse-cache
```

Extract all QA inspection media immediately after rendering (automatically reads `./work/render_report.json` when present):

```bash
python3 scripts/render_trip.py \
  --extract-qa ./output/trip_cinema.mp4 \
  --edl ./work/edl.json \
  --work-dir ./work
```

Outputs generated under `./work/qa/`:

1. `qa_video_alignment.jpg`: 100% of video clip mid-frames.
2. `qa_chapter_cards.jpg`: 100% of graphic cards (`route_map` sampled at 86%).
3. `qa_collages.jpg`: large `960x540` cells for every multi-tile layout (`split_2up_video`, `triptych_3up_video`, `bento_living_grid`, `polaroid_stack`, `split_diptych_pair`).
4. `qa_transitions.jpg`: 3-frame `[PRE | MID | POST]` strips for every non-cut transition (`offset - 0.05s`, `offset + ov/2`, `offset + ov + 0.05s`).
5. `qa_metrics.json`: per-shot FFmpeg `signalstats` (`YAVG`, `YMIN`, `YMAX`, `SATAVG`), lifted-black/washed HDR flags compared against SDR photos, and expected-vs-probed `drift_ms`.
6. Plus `qa_sheet_01.jpg`, `end_frame_check.jpg`, and `<=60s` `qa_chunk_*.mp4` review clips.
