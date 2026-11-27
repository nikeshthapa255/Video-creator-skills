---
name: trip-cinema
description: >-
  Turn a local folder, file list, or Google Drive folder of raw trip photos and
  80%-video 4K clips into a finished 16:9 (or 9:16 teaser) styled cinematic MP4
  (-14 LUFS, no voiceover) with 7 declarative Style Packs, Video-First Timeline
  Strips, 4-gate interactive review, speed ramps, multi-video grids, 4-track
  J/L-cut audio, and chunked multimodal QA.
---

# Trip Cinema Studio runbook (v0.2.1)

Produce a standalone 16:9 horizontal cinematic MP4 (1080p or 4K, `-14 LUFS`, `LRA=9`, no voiceover) or an optional 9:16 vertical teaser (`1080x1920`, `aspect_profile: "9:16"`) from raw trip media. Never modify raw media; keep all working files in `./work/`.

## Platform and reference routing (read before running)

Check the host OS (`uname -s`) and read **only the matching platform guide** before Phase 0. Load the other reference files only when you reach their phase.

| Condition or phase | Reference file |
| :--- | :--- |
| Linux host or cross-platform Python + FFmpeg stack | [references/platform_linux.md](./references/platform_linux.md) |
| macOS host using zero-dependency Apple-signed JXA | [references/platform_macos.md](./references/platform_macos.md) |
| Gate 3 BGM sourcing (CC0, Lyria 3.5, local track) | [references/bgm_sources.md](./references/bgm_sources.md) |
| Authoring or checking `./work/edl.json` (EDL v2.0) | [references/edl_schema.json](./references/edl_schema.json) |
| Custom FFmpeg filters (HLG vs. PQ HDR, vidstab, loudnorm) | [references/ffmpeg_recipes.md](./references/ffmpeg_recipes.md) |

## Declarative style packs (`styles/*.json`)

Every film uses one preset under `styles/<style_id>.json` (validated by `styles/style_schema.json`) controlling palette, typography, color grade, film textures, portrait/multi-video layouts, graphic cards, transition grammar, `max_consecutive_photos`, and audio ducking:

| `style_id` | Look and layouts | Cadence and transitions | Best fit |
| :--- | :--- | :--- | :--- |
| `cinema_doc` | Natural cinema grade, gold serif chapter cards over blurred backdrops, dark topo route map with elevation sparkline. | 70-95 BPM; 2.0-5.5s holds, max 2 consecutive photos; J/L bridges, soft dissolves, sonic postcards. | Cultural journeys, mountain treks, heritage trips. |
| `analog_scrapbook` | Warm kraft canvas, tilted Polaroids (`polaroid_stack`), 12fps stepped overlays, typewriter EXIF stamps. | 95-115 BPM; 1.5-3.5s holds, max 6 consecutive photos; paper wipes and shutter flashes. | City food crawls, friend getaways, photo-heavy trips. |
| `wes_postcard` | Symmetrical pastel postcard frames, Futura Bold Roman-numeral cards, locked frames. | 110-130 BPM; 1.2-3.0s staccato cuts, max 4 consecutive photos; 90-degree whip-pans. | Rail journeys, hill stations, boutique hotels, architecture. |
| `kinetic_montage` | High-contrast filmic grade, optional 2.39:1 matte, condensed `text_behind_subject` titles, 3-up vertical triptychs. | 120-145 BPM; 0.4-2.4s wave bursts, cubic speed ramps; whip-pans and zoom punches. | 80%-video treks, road trips, festivals, 30s `9:16` teasers. |
| `editorial_bento` | Quiet-luxury linen canvas, `1px` hairlines, living 2/3/4-up `bento_living_grid`, micro EXIF badges. | 80-105 BPM; calm 2.8-4.8s holds, max 5 consecutive photos; architectural dissolves. | Design and culinary cities, honeymoons, photography trips. |
| `super8_nostalgia` | Kodak warm highlights, lifted blacks, halation, rounded `super8_frame` overscan, gate weave, date stamp. | 85-110 BPM; 2.0-3.8s holds, 18fps overlay step; film burns and warm light leaks. | Beach holidays, family reunions, candid kids trips. |
| `sitcom_cast` | Bright daylight tones, freeze-frame `cast_intro` nameplates, preserved laughter, post-credits `blooper_title` stinger. | 115-135 BPM; 1.1-2.6s lively montage; flash cuts and blooper outtakes. | Family vacations, friend-group holidays, road trips. |

## Phase 0: Preflight and input resolution

1. Run tool discovery per your platform reference (`platform_linux.md` or `platform_macos.md`).
2. Resolve the media source: local folder, file list, Google Drive folder (`gdrive` plugin into `./work/raw_drive/`), or Google Photos shared album (`=d`/`=dv` originals into `./work/raw_gphotos/` with `./work/album_index.json` timestamps).
3. Ask the user for their budget mode: `$0` local-only (deterministic motion, grids, and speed ramps) or Hybrid (adds optional `gemini-omni-1.2-flash` I2V only on user-approved non-human scenic hero stills).

## Phase 1: Video timeline strips and Gate 1

1. Generate `./work/manifest.json` (or `manifest_v2.json`), `1800x360` Multi-Frame Video Timeline Strips with aligned 8 kHz mono RMS audio waveforms (`./work/clip_strips/`), and upright `3x3` photo contact sheets using the 7-Tier Creation Date Waterfall. Never upload raw 4K clips to a VLM.
2. Triage strips in parallel (15 to 20 strips per worker via `agents/media-triage-worker.md`) to propose golden `[in_sec, out_sec]` brackets (`2.0-5.0s`), `[60fps SLOW-MO]` (`fps >= 50`), `[SONIC POSTCARD]` peaks, `[SHAKE]` salvage cuts (`1.2-1.6s`), `[WIND]` mutes, `has_human_closeup`, `focal_anchor`, and optional `blooper: true` clips.
3. Present the **Gate 1 artifact** (`UserFacing: true`, `RequestFeedback: true`) with chronological/GPS chapters, unverified dates, shortlisted video strips and photo sheets, recurring faces for VIP selection (with optional `cast_intro` nicknames/roles), borderline rejects, and privacy exclusion prompts.
4. Wait for user approval. Persist `locked_by_user: true`, shifted `[in, out]` brackets, VIP metadata, `blooper: true`, and all **Gate 1 privacy exclusions** (`excluded_by_user: true` in manifest, plus `excluded_ids`, `excluded_paths`, and `forbidden_video_ranges` in `./work/gates_state.json`).

## Phase 2: Gate 2 style-pack carousel

1. Select the 4 best-fit Style Packs for the trip's video/photo ratio, setting, and cast size. Render four `1280x720` composite Style Preview Cards from real hero and portrait media (decoded upright in Rec.709 per your platform guide):
   - Left 60%: graded hero with that pack's title styling and matte.
   - Right 40%: real portraits rendered in the pack's `default_portrait_layout`.
   - Footer 56px: palette swatches, style name, target BPM range, and transition signature.
2. Present the **Gate 2 artifact** (`UserFacing: true`, `RequestFeedback: true`) with the 4 cards and toggles for `trip_wrapped`, `cast_intro`, post-credits `blooper_title`, and an optional 30s `9:16` teaser.
3. Wait for selection and save `style_id`, optional `look_id` override, portrait layout, and story toggles to `./work/gates_state.json`.

## Phase 3: Music selection and Gate 3

1. Gather 3 to 4 candidate tracks matching the active Style Pack's `target_bpm_label` and `music_genre_hint` (`references/bgm_sources.md` or `agents/bgm-scout.md`): CC0/open-source, optional `lyria-3.5` previews (`$0.08`/track), or a user-supplied local audio file.
2. Analyze the selected track into `./work/beat_map.json` (BPM, downbeats, RMS energy drops).
3. Present the **Gate 3 BGM picker artifact** (`UserFacing: true`, `RequestFeedback: true`) with auditionable previews, BPM, drops, and pacing curve. No voiceover. Wait for approval.

## Phase 4: EDL v2.0 storyboard, lint, and Gate 4

1. Author `./work/edl.json` conforming to `references/edl_schema.json` (`"version": "2.0"` and `"style_id"`):
   - **Wave pacing and beat sync**: match shot velocity to music energy; alternate fast bursts (max 4 to 6 cuts of `0.4-1.5s`) with breathing holds (`3.0-5.5s`) and 3-shot micro-stories (`Wide -> Medium -> Close-Up`). Snap cuts to bar/half-bar beats.
   - **Speed ramps (`speed_profile`)**: apply `cubic_ramp` or slow-mo (`rate < 0.85`) only to `>=50fps` footage with real motion, capped at `<=10-20%` of video shots. Never speed-ramp dialogue, laughter (`sonic_postcard: true`), emotional faces, or 24/30fps clips. Mute audio above `1.3x`. Required source span: `S_req = v_hi*d1 + ((v_hi + v_lo)/2)*d2 + v_lo*d3 + ((v_lo + v_hi)/2)*d4 + v_hi*d5`.
   - **Multi-video grids and framing (`layout`, `tiles[]`, `focal_anchor`)**: match source aspect ratio to slot geometry (`<=1.85x` mismatch; never drop portrait clips into 4-up landscape Bento slots or landscape clips into narrow triptychs) and set `focal_anchor` on every `has_human_closeup: true` tile.
   - **Transitions (`transition_out`)**: pick from the Style Pack's allowed transitions (`cut`, `dissolve`, `whip_pan_*`, `light_leak`, `film_burn`, `flash_cut`, `zoom_punch`, `paper_wipe`). Keep `>=0.4s` outgoing tail and incoming head handles on adjacent videos.
   - **4-track audio (`audio`)**: add `j_cut_sec` / `l_cut_sec` (`0.4-1.5s`, respecting handles and `forbidden_video_ranges`), include 2 to 4 `sonic_postcard: true` pockets (`duck_db: -13` to `-15`), and mute wind-hazard clips (`mute: true`).
   - **Graphic cards (`media_type: "graphic"`)**: use `chapter_title` (blurred `backdrop_path`, never flat black), `route_map` (with `elevation_profile`), `trip_wrapped` (verified EXIF only, omit `<30%` coverage), `cast_intro`, `text_behind_subject`, and `blooper_title`. Set optional `rendered_card_path` to pass through any bespoke pre-rendered PNG/MP4 card directly.
   - **Human face safety**: never use generative I2V on `has_human_closeup: true` shots or tiles.
2. Run deterministic EDL validation with `--gates-state ./work/gates_state.json` (per your platform guide) and fix every error before presenting Gate 4.
3. Present the **Gate 4 storyboard and cost artifact** (`UserFacing: true`, `RequestFeedback: true`) with the shot-by-shot summary and exact cost cap (`$0` in local mode). Wait for explicit approval.

## Phase 5: Render, multimodal QA, and 3-persona review

1. Render the master MP4 using your platform guide (`platform_linux.md` with `--workers` and `--reuse-cache`, or `platform_macos.md` with 5 to 6 shot cut-aligned batches and `--mux-segments`).
2. Extract QA artifacts with `--edl ./work/edl.json` (per your platform guide).
3. Inspect 100% of `./work/qa/qa_video_alignment.jpg`, `./work/qa/qa_chapter_cards.jpg`, `./work/qa/qa_collages.jpg`, `./work/qa/qa_transitions.jpg`, `./work/qa/qa_metrics.json`, `./work/qa/qa_sheet_*.jpg`, `./work/qa/end_frame_check.jpg`, and every `<=60s` chunk with `read_file`. Check upright orientation, HLG/PQ Rec.709 skin tones and black levels (`YMIN < 34`), collage framing, smooth transitions without whiteouts or dark dips, card legibility, duration drift (`<=250ms`), and A/V sync.
4. Spawn the 3-persona review panel (`agents/review-director.md`, `agents/review-cinematographer.md`, and `agents/review-family-member.md`) to audit story pacing, visual craft/telemetry, and Gate 1 privacy exclusions (`./work/gates_state.json`).
5. Resolve every `REVISE` note, re-render and re-extract QA media if you edited the cut, and write `./work/qa_passed.flag` only after all three personas return `PASS` (the stop hook blocks delivery whenever `qa_passed.flag` is missing or older than the newest rendered MP4).
