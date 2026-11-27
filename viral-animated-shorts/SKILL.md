---
name: viral-animated-shorts
description: >-
  End-to-end production pipeline for viral 2D stickman-in-3D and stylized 3D
  animated videos in both Short-Form Mode (9:16, 30-55s) and Long-Form Video
  Mode (3-15+ min multi-act episodes in 9:16 vertical micro-drama or 16:9
  horizontal YouTube format). Supports Mode A (Gemini Omni 1.2 Flash generative
  video), Mode B (zero-API-key keyframes, 2x-supersampled FFmpeg motion,
  multi-voice edge-tts, and procedural Act-aware BGM/SFX), and Hybrid mode.
  Includes multi-state 3-panel turnaround sheets, audio-first frame-locked
  timing, natural spoken English linting, chunked 60-second multimodal QA,
  fast-path assembly, and series bible continuity handoff. Use when the user
  wants to plan, storyboard, generate character sheets, animate, QA, upgrade
  from 2D to 3D, batch-produce, or assemble animated YouTube Shorts, Reels,
  TikToks, serialized vertical micro-dramas, or long-form YouTube episodes.
---

# Viral animated Shorts and long-form video pipeline (v4.1)

Produce upload-ready animated videos (2D stickman-in-3D or stylized 3D) in two video formats and three production modes:

- **Short-Form Mode (`video_format: "short"`, default):** `1080x1920` (9:16), 30-55s (target 34-42s, hard cap 59.0s), 10-14 rapid shots, single-voice narration (`+10%` default rate), dual-anchor replay loop (`loop_bridge`).
- **Long-Form Video Mode (`video_format: "long_form"`):** 3-15+ min, 3-6 Acts/chapters, 45-180+ shots in `9:16` vertical micro-drama or `16:9` horizontal YouTube commentary/story format.
- **Production modes:** **Mode A (`omni_video`)** renders video with Gemini Omni 1.2 Flash (`GEMINI_API_KEY`); **Mode B (`keyframe_motion`, default when no video API key exists)** pairs per-shot keyframes with audio-first `edge-tts` and 2x-supersampled FFmpeg camera moves; **Hybrid (`hybrid`)** combines Mode A hero/hook shots with Mode B narrative shots via `shots[].production_mode`.

## Reference routing (read only what applies)

Read only the reference files needed for your current stage or format so you keep context focused without missing format-specific rules:

| When you are about to... | Read this file | What it gives you |
| :--- | :--- | :--- |
| Draft Stage 1 script, dialogue, or YouTube metadata | `references/spoken_english_guide.md`, `../clear-natural-english/SKILL.md`, `references/shot_plan_schema.json` | Spoken English rules, display-vs-TTS separation, complete schema fields |
| Produce `video_format: "long_form"` (`9:16` drama or `16:9` commentary) | `references/long_form_guide.md` | Multi-act casting, **16:9 Commentary Playbook**, Stage 1.5 Pre-Render LGTM Gate, `episode_context.md` |
| Generate Stage 2/3 sheets, location plates, or keyframes | `references/sheet_prompt_templates.md` | 3-panel templates (1A to 1E, 2), cast isolation, **Text-Magnet Prop Substitution Table** |
| Run Stage 4/6 QA or retry a failed shot | `references/qa_rubric.md`, `references/failure_fix_playbook.md` | Pass gates, failure codes (including `SFX_TONE_MISMATCH`, `IMAGE_QUOTA_EXHAUSTED`) |
| Run multi-video batches, recurring series, Drive delivery, or logs | `references/batch_and_delivery.md` | Coordinator/flash hierarchy, quota wave cap, `<slug>/` Drive rules, 4D dedup, 24-column log |

## Prerequisites check

Verify tools with `--version` rather than `which`, because macOS `/usr/bin/python3` can be an uninstalled Xcode CLT stub:

- **All modes:** `ffmpeg -version`, `ffprobe -version`, `python3 --version`, `python3 -c "import PIL"` (Pillow, for `scripts/make_thumbnail.py`)
- **Mode A:** `gemini-api whoami` (or `google-genai` SDK with `GEMINI_API_KEY` set)
- **Mode B / Long-Form:** `python3 -c "import edge_tts"` (`pip install edge-tts`), `python3 -c "import numpy"` (fast vectorized BGM/SFX; stdlib fallback works for Shorts), plus `image-generator` or `gemini-api image`

If `gemini-api` or `GEMINI_API_KEY` is unavailable, default to **Mode B (`keyframe_motion`)** instead of stopping at Stage 2.

## Seven-stage workflow (Stage 0 to Stage 6)

Store working files inside `<project_dir>/` (`character_references.json`, `references/`, `acts/`, `shot_plan.json`, `sheets/`, `scenes/`, `drafts_360p/`, `clips/`, `qa/`, `qa_segments/`, `audio/`, `README_series_bible.md` or `episode_context.md`, and `final_short.mp4` or `final_episode.mp4`).

### Stage 0: Research real people and official mascots (required when used)

Verify every public figure and official brand mascot before generating turnaround sheets:

```sh
python3 scripts/research_characters.py \
  --entities "Jensen Huang,Colette Kress,Meta Muse" \
  --registry <shared_or_project_dir>/references/character_registry.json \
  --ref-dir <shared_or_project_dir>/references \
  --user-ref meta_muse=<path/to/official_mascot.png> \
  --out <project_dir>/character_references.json
```

`research_characters.py` splits `reference_downloaded` from `likeness_verified` and lists newly downloaded portraits under `uninspected_entities`. Open every downloaded or user-supplied image with `read_file` or `view_file`, then lock observable traits (hair, face structure, eyewear or `"NO glasses"`, signature attire, or official mascot silhouette/materials) before Stage 2:

```sh
python3 scripts/research_characters.py \
  --verify <entity_id> \
  --traits "<observed hairline, hair, eyewear/NO glasses, face, build>" \
  --attire "<observed signature outfit>" \
  --registry <shared_or_project_dir>/references/character_registry.json \
  --out <project_dir>/character_references.json
```

### Stage 1: Write the script, build the shot plan, and pass preflight lint

1. **Short-Form:** Draft 95-115 words (34-42s) across 10-14 rapid shots (`<= 3.5s` avg) with a visual gag or tonal shift every 2-3 shots, a cold hook at `0:00`, and a natural `loop_bridge`.
   - **Long-Form:** Author modular Acts (`acts/act_01.json`..`act_0N.json`), merge with 3-digit shot IDs (`shot_001`..`shot_NNN`), set `ending_style: "cliffhanger"` or `"outro"`, and omit `loop_bridge`. For `16:9` commentary, follow the **16:9 Commentary Playbook** in `references/long_form_guide.md` (`100-115 WPM`, `+6%..+10%` TTS rate, Anchor vs. Skeptical Analyst dynamic, 3-tier visual hierarchy, and optional **Stage 1.5 Pre-Render LGTM Gate** before spending image quota).
2. Compile `<project_dir>/shot_plan.json` matching `references/shot_plan_schema.json`:
   - Keep `dialogue_or_vo` in clean display English (`AWS`, `NPCs`, `1080p`, `911`, `$75,000`, `Pei Xuan`); put phonetic overrides in top-level `tts_pronunciations` (plural acronyms like `NPCs` auto-match from `NPC`) or per-shot `tts_text`. Never leak spaced phonetics (`N P C`) into display text.
   - Lock one verbatim `style_bible` ending with `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"`. Never write `"blank signs"` (`LITERAL_BLANK_SIGN`), and swap text-magnet props using `references/sheet_prompt_templates.md`.
   - Configure captions (`9:16`: `kinetic_keyword`, `86px`, `max_caption_chars: 18`, `caption_margin_v: 420`; `16:9`: `caption_style: "none"` + `captions.srt` CC recommended, or `clean_subtitle` at `62px`, `max_caption_chars: 36`, `caption_margin_v: 116`, `caption_tilt: 0`), `bgm_mood`, `ambience_bed`, and tone-matched `sfx_type` (use Tech/UI cues `ui_tick`, `ui_click`, `data_chime`, `sub_whoosh`, `alert_ping`, `soft_thud` on `16:9` commentary; never cartoon cues).
3. Run the preflight linter (blocks `TTS_MISREAD` and `LITERAL_BLANK_SIGN`; checks `AI_SLOP_SCRIPT` on dialogue, named dual-hosts, and `packaging` metadata; warns on `TEXT_MAGNET_PROP`, `SFX_TONE_MISMATCH`, and Short/16:9 WPM pacing):
   ```sh
   python3 scripts/tts_and_captions_v2.py --lint-plan <project_dir>/shot_plan.json --strict-slop
   ```

### Stage 2: Generate sheets, voiceover, captions, and audio stems

1. Generate 16:9 3-panel turnaround sheets (`Front | 3/4 Side | Back`, light-gray studio background, zero held props) using `references/sheet_prompt_templates.md` (Templates 1A to 1E). If an image model blocks a public figure's photo, use the Template 1C trait fallback and inspect side by side against Stage 0.
2. Synthesize voiceover and captions before animating so clips lock to spoken duration (`tts_and_captions_v2.py` reads top-level `voice`, `rate` [default `+10%` Shorts], `pitch`, `max_caption_chars`, `voice_cast`, `acts`, `post_gap_sec`, and forwards `ambience_bed` into `shot_timings.json`; Shorts fail with `SHORT_OVER_60S` if runtime exceeds `59.0s` unless `--allow-over-60s` is passed):
   ```sh
   # Short-Form (9:16; reads plan.voice, plan.rate, plan.pitch, plan.max_caption_chars; pass --voice to override):
   python3 scripts/tts_and_captions_v2.py \
     --shot-plan <project_dir>/shot_plan.json \
     --out-dir <project_dir>/audio \
     --caption-style kinetic_keyword \
     --font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto

   # Long-Form (multi-voice cast, 16:9 defaults MarginV=116, auto captions.srt + chapters.txt):
   python3 scripts/tts_and_captions_v2.py \
     --shot-plan <project_dir>/shot_plan.json \
     --out-dir <project_dir>/audio \
     --gap 0.08 --concurrency 4 --max-retries 4 --export-srt
   ```
3. Generate SFX (auto-reads `ambience_bed` from `shot_timings.json` when `--ambience` is omitted) and BGM stems:
   ```sh
   python3 scripts/sfx_engine.py \
     --timings <project_dir>/audio/shot_timings.json \
     --out <project_dir>/audio/sfx_track.wav

   # Short-Form BGM (retrieve_music.py or procedural make_bgm.py):
   python3 scripts/make_bgm.py --mood dramatic_news --duration <total_sec> --out <project_dir>/audio/bgm.wav

   # Long-Form Act-aware procedural score (palace_drama or epic_story):
   python3 scripts/make_bgm.py --mood epic_story --timings <project_dir>/audio/shot_timings.json --out <project_dir>/audio/bgm.wav
   ```

### Stage 3: Generate visuals and animate frame-locked clips

- Attach only the locked 3-panel sheet(s) (or active `state_sheets` variant) plus optional location plate in natural language order (never `<IMAGE_REF_N>`), append verbatim `style_bible`, and enforce cast-isolation clauses from `references/sheet_prompt_templates.md`.
- **Short-Form replay loop:** Attach both the character sheet and `scenes/shot_01.png` on the final shot (`dual_anchor_loop: true`). **Long-Form:** Set `dual_anchor_loop: false` and end on a cliffhanger or outro/End-Screen slate.
- **Mode A (`omni_video`):** Render `360p` drafts with `gemini-api video -m gemini-omni-1.2-flash`. In `hybrid` mode, strip audio (`-an`), trim to exact `shot_timings.json` duration, and save as `clips/<idx:03d>_<shot_id>.mp4`.
- **Mode B (`keyframe_motion`):** Generate keyframes (`scenes/shot_001.png`..`shot_NNN.png`), inspect with `read_file`, then animate to exact spoken duration with 2x-supersampled presets (`whip_zoom`, `bounce_pop`, `shake_zoom`, `zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `static_drift`, `static_hold` [zero crop for charts and End-Screen slates], `slow_dolly_in`, `tilt_up`, `tilt_down`):
  ```sh
  python3 scripts/animate_shot_v2.py \
    --timings <project_dir>/audio/shot_timings.json \
    --scenes-dir <project_dir>/scenes \
    --out-dir <project_dir>/clips \
    --workers 4 --skip-existing
  ```
  For parallel long-form Act workers, add `--act <N> --skip-omni` (and `--fade-out-last` on the final Act when `ending_style == "outro"`).

### Stage 4: Run multi-frame QA (max 3 retries per shot)

```sh
bash scripts/extract_qa_frames.sh <clip.mp4> <project_dir>/qa/<shot_id> [prev_clip.mp4]
```

Audit sampled frames (or Mode B keyframes before animating) alongside Stage 0 portraits, turnaround/state sheets, location plates, and the prior shot's end frame against `references/qa_rubric.md`. Apply fixes from `references/failure_fix_playbook.md`: `HOOK_WEAK`, `EXTRA_LIMB`, `STYLE_DRIFT`, `OFF_MODEL_CHARACTER`, `LIKENESS_DRIFT`, `STATE_CONTINUITY_DRIFT`, `LOCATION_CONTINUITY_DRIFT`, `EYELINE_CROSS`, `SOLO_CHAR_BLEED`, `MULTI_CHAR_BLEED`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `VOICE_CAST_DRIFT`, `AI_SLOP_SCRIPT`, `CAPTION_OVERLAP`, `CAPTION_CLIP_HORIZONTAL`, `AUDIO_DESYNC`, `FRAME_TIMELINE_DRIFT`, `BGM_OVERPOWERS_VO`, `BGM_TONE_MISMATCH`, `SFX_TONE_MISMATCH`, `ACT_BGM_FATIGUE`, `IMAGE_QUOTA_EXHAUSTED`, `QA_FILE_TOO_LARGE`.

### Stage 5: Render at `1080p` and optional 2D-to-3D upgrade

- **Mode A:** Re-render approved shots at `1080p` (`--resolution 1080p`). Mode B clips already render at `1080x1920` or `1920x1080`.
- **Optional 2D-to-3D upgrade:** Feed `<char>_2d.png` into Template 2 in `references/sheet_prompt_templates.md`, lock `<char>_3d.png`, update `style_bible`, and re-run the proven `shot_plan.json` and `audio/shot_timings.json`.

### Stage 6: Mix audio, assemble, build thumbnails, watch with sound, and deliver

1. Optional `.srt` to `.ass` conversion (for human mic or ElevenLabs VO; `16:9` defaults to `MarginV=116`):
   ```sh
   python3 scripts/srt_to_ass.py <input.srt> <project_dir>/audio/captions.ass --aspect-ratio 9:16
   ```
2. Assemble and loudness-normalize (`-14 LUFS`, 24-bit PCM 3-stem mix, single-point BGM speech carve, zero-overlap check; pass `none` for unused stems such as `captions.ass` on `16:9` CC uploads):
   ```sh
   SHORTS_VO_CLARITY=1 SHORTS_BGM_GAIN_DB=-15 \
   bash scripts/assemble_short.sh \
     <project_dir>/clips \
     <project_dir>/audio/captions.ass \
     <project_dir>/final_episode.mp4 \
     <project_dir>/audio/voiceover.wav \
     <project_dir>/audio/bgm.wav \
     <project_dir>/audio/sfx_track.wav
   ```
3. **Final watch-through with audio (mandatory):**
   - **Shorts (`< 90s` and `< 80 MB`):** Open `final_short.mp4` directly with `view_file` or `read_file`.
   - **Long-Form (`> 90s` or `> 80 MB`):** Slice into 60-second chunks (`bash scripts/split_qa_segments.sh <project_dir>/final_episode.mp4 <project_dir>/qa_segments --timings <project_dir>/audio/shot_timings.json`) and watch every `qa_min*.mp4` chunk with audio.
4. **Thumbnail and metadata packaging:** Lint `packaging.md` (`python3 scripts/tts_and_captions_v2.py --lint-metadata <project_dir>/packaging.md --strict-slop`) and render a `< 2 MB` safe-zone thumbnail (supports `--right-image` split canvas and `--mobile-preview` 160px legibility check):
   ```sh
   python3 scripts/make_thumbnail.py \
     --image <project_dir>/scenes/shot_01.png \
     --headline "HEADLINE" --accent "ACCENT" \
     --aspect 9:16 --mobile-preview <project_dir>/qa/thumb_160px.png \
     --out <deliverables_root>/<slug>/thumbnail.png
   ```
5. **Minimal `<slug>/` delivery folder (mandatory):** Package every finished video locally and on Google Drive inside its own `<slug>/` folder containing only `<slug>-short.mp4` (or `<slug>-episode.mp4`), `thumbnail.png`, `packaging.md`, and optional `episode_context.md`. Never upload raw intermediates (`clips/`, `scenes/`, `audio/`, `qa_segments/`). For batch wave caps, quota resume, 4D story dedup, Drive moves (`gdrive mutate mv`), and the 24-column production log, read `references/batch_and_delivery.md`.
