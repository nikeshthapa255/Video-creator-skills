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

# Viral animated Shorts & long-form video pipeline (v4.0)

Produce upload-ready animated videos (2D stickman-in-3D or stylized 3D) in two **video formats** and three **production modes**:

### Choose a video format

- **Short-Form Mode (`video_format: "short"`, default):** `1080x1920` (9:16), 30-55s, 10-14 rapid shots, single-voice narration, dual-anchor replay loop (`loop_bridge`).
- **Long-Form Video Mode (`video_format: "long_form"`):** 3-15+ min, 3-6 Acts/chapters, 45-180+ shots in either **`9:16` vertical micro-drama** (`1080x1920`, ~3.2-3.6s avg shot, kinetic lower-third captions, serialized cliffhanger) or **`16:9` horizontal YouTube episode** (`1920x1080`, ~3.5-6.5s avg shot, clean lower-third captions or `.srt` sidecar, YouTube chapters, outro/cliffhanger). See `references/long_form_guide.md` for full multi-act casting, swarm production, and continuity rules.

### Choose a production mode

- **Mode A (`omni_video`):** Generates full character animation with Gemini Omni 1.2 Flash (`360p` draft to `1080p` final) via the `gemini-api` CLI or `google-genai` SDK. Pick this when you have `GEMINI_API_KEY` and want fluid generated video.
- **Mode B (`keyframe_motion`, no video API key required):** Generates per-shot keyframes from verified 3-panel turnaround sheets (`image-generator` or `gemini-api image`) and records `edge-tts` voiceover first (single voice or multi-voice cast). Adds kinetic or widescreen captions, vectorized SFX, and Act-aware BGM, then animates each frame with 2x-supersampled FFmpeg camera moves. Runs end-to-end with zero paid video API keys.
- **Hybrid (`hybrid`, long-form):** Combines Mode A (`omni_video`) hero/hook shots with Mode B (`keyframe_motion`) narrative shots via per-shot `shots[].production_mode`.

## Prerequisites check

Verify tools with `--version` rather than `which`, because macOS `/usr/bin/python3` may be an uninstalled Xcode CLT stub:

- **All modes:** `ffmpeg -version`, `ffprobe -version`, `python3 --version`
- **Mode A:** `gemini-api whoami` (or the `google-genai` Python SDK with `GEMINI_API_KEY` set)
- **Mode B / Long-Form:** `python3 -c "import edge_tts"` (install via `pip install edge-tts`), `python3 -c "import numpy"` (recommended for fast vectorized long-form BGM/SFX; stdlib fallback works for Shorts), plus the `image-generator` subagent or `gemini-api image`

If `gemini-api` or `GEMINI_API_KEY` is unavailable, default to **Mode B (`keyframe_motion`)** instead of stopping at Stage 2.

## Seven-stage workflow (Stage 0 to Stage 6)

Store all project files inside `<project_dir>/` (for example, `shorts/<slug>/` or `episodes/<slug>/`): `character_references.json`, `references/`, `acts/` (long-form modular Act files), `shot_plan.json`, `sheets/`, `scenes/` (Mode B keyframes), `drafts_360p/` (Mode A), `clips/` or `finals_1080p/`, `qa/`, `qa_segments/` (long-form 60s chunks), `audio/`, `README_series_bible.md` (serialized episodes), and `final_short.mp4` (or `final_episode.mp4`).

### Stage 0: Research real people and official mascots (required when used)

Before writing character sheets or generating visuals, verify every public figure (CEOs, executives, journalists, politicians) and official product or brand mascot. This stops downstream stages from inventing off-model creatures or wrong likenesses.

1. Run `scripts/research_characters.py` to check your local registry, attach any photos you supply (`--user-ref`), and verify unknown entities through the Wikipedia REST API (with opensearch fallback):
   ```sh
   python3 scripts/research_characters.py \
     --entities "Jensen Huang,Colette Kress,Meta Muse" \
     --registry <shared_or_project_dir>/references/character_registry.json \
     --ref-dir <shared_or_project_dir>/references \
     --user-ref meta_muse=<path/to/official_mascot.png> \
     --out <project_dir>/character_references.json
   ```
   You can also pass `--story-json <brief_or_plan.json>` alone or alongside `--entities`. If any entity lacks a verified local image, the script flags it under `unverified_entities`. Run a web image search or supply `--user-ref <id>=<local_image_path>` before continuing.
2. Open every downloaded or user-supplied reference image with `read_file` or `view_file`. Record exact silhouette, hairstyle, eyewear, facial structure, official mascot materials and colors (matching official mascot proportions instead of inventing a cartoon creature), and signature attire in `<project_dir>/character_references.json` and the local registry.
3. Pass Part A0 (Stage 0 character and likeness gate) of `references/qa_rubric.md` before generating Stage 1 or Stage 2 assets.

### Stage 1: Write the script, build the shot plan, lint narration, and pass the double gate

1. **Short-Form Mode:** Write a tight script of **95-115 words (~34-42s)** across **10-14 rapid shots** (target **<= 3.5s average shot length**). Put a visual gag, prop reaction, or tonal shift every 2-3 shots instead of a single-pose lecture, and skip intros or greetings.
   - **Long-Form Mode (`video_format: "long_form"`):** Split the script into **3-6 Acts/chapters** authored modularly in `acts/act_01.json` .. `acts/act_0N.json` (so parallel subagents can draft Acts concurrently), then merge into `shot_plan.json` using 3-digit shot IDs (`shot_001`..`shot_NNN`). Target ~750-1,100 words / 80-120 shots (`~3.2-3.6s` avg) for `9:16` vertical micro-dramas, or ~500-2,200 words / 45-180 shots (`~3.5-6.5s` avg) for `16:9` horizontal episodes. See `references/long_form_guide.md`.

#### Natural spoken English and scriptwriting

Follow `references/spoken_english_guide.md` and the sibling `../clear-natural-english/SKILL.md` when drafting `dialogue_or_vo`:

- Never drop articles, verbs, or objects to fit a word-count box. Vary cadence across shots by mixing 4-7 word punchlines with 12-16 word setup lines instead of writing metronomic telegraphese.
- Keep `dialogue_or_vo` in display English (`AWS`, `GTA 6`, `911`, `$75,000`, `Nov 24`, `Pei Xuan`, `Hanfu`). Put phonetic spellings (`A W S`, `GTA Six`, `nine-one-one`, `Pay Shwen`, `Hahn-foo`) in top-level `tts_pronunciations` or per-shot `tts_text` so phonetic workarounds never leak into burned-in captions.
- Use concrete actors and active verbs. Replace trailing `"-ing"` participle riders (`catching...`, `firing...`, `slapping...`) with a second active verb or a short second sentence, and replace press-release legalese (`keep full liability`) with everyday speech (`on the hook`).
- Cut spoken AI slop: dead openers (`In today's world`, `Have you ever wondered`), fake contrasts (`not just X, it's Y`; `Forget X - Y`), and colon reveals (`Plot twist:`, `Here's the catch:`). Also cut dramatic fragments (`Total privacy disaster.`, `Let that sink in.`), stock buzzwords (`delve`, `landscape`, `crucial`, `leverage`), and fortune-cookie morals.
- Do not open every Short with `"Because..."` or close every Short with a flat `"Which is why..."` drop. Open Shot 1 with a standalone concrete hook that lands cold at `0:00`, and close Shorts with a natural loop bridge or visual callback (close long-form episodes with `ending_style: "cliffhanger"` or `"outro"`).
- Ground every proper name with a quick role on first mention (for example, `Education Minister Choi`), and keep one Short to one clear thread (or introduce both beats in Shot 1). Apply `../clear-natural-english/SKILL.md` to YouTube titles, descriptions, and pinned comments, and cut em-dash spam and recycled openers (`Be honest:`, `Imagine...`).

2. Compile `<project_dir>/shot_plan.json` matching `references/shot_plan_schema.json`:
   - Set `video_format` (`short` or `long_form`), `script_genre` (`commentary` or `drama`), `aspect_ratio` (`9:16` or `16:9`), `production_mode` (`omni_video`, `keyframe_motion`, or `hybrid`), `audio_mode` (`tts_plus_bgm` or `native_omni`), and `character_references_path` pointing to `<project_dir>/character_references.json`.
   - Lock one verbatim `style_bible` string and reuse it in every shot prompt. **Never write `"blank signs"`**, because models render the literal word `"BLANK"`. Always write `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"` (plus local script guards like `"plain silk banners, plain scrolls, zero Chinese characters"` in historical settings).
   - Define every character with `no_held_props_confirmed: true`, `reference_image_path` (from Stage 0), and `likeness_verified: true`. For long-form visual state changes (e.g., veiled vs. unveiled across Acts), map variant turnaround sheets in `characters[].state_sheets` and select them per shot via `shots[].character_states`; register recurring interior/exterior sets in `locations[]` (Template 1E).
   - For multi-voice long-form episodes, define top-level `voice_cast` (`speaker -> {voice, rate, pitch, keyword_color}`) using `WordBoundary`-verified `edge-tts` voices (`en-GB-SoniaNeural`, `en-US-AvaNeural`, `en-GB-RyanNeural`, `en-US-EmmaNeural`, `en-GB-ThomasNeural`) and tag each shot with `speaker`, `act`, and optional `post_gap_sec` (`0.25-0.35s` on Act-closing shots). Include 10-15% silent reaction shots (`dialogue_or_vo: ""`, `shot_role: "reaction"`) in scripted drama.
   - Set caption options (`9:16`: `caption_style: "kinetic_keyword"`, `max_caption_chars: 18`, `caption_font_size: 86`, `caption_margin_v: 420`, `caption_keyword_color: "auto"` or `"palace"`; `16:9`: `caption_style: "none"` + `captions.srt` recommended for YouTube CC, or burned `clean_subtitle` with `caption_font_size: 62`, `max_caption_chars: 36`, `caption_margin_v: 96`, `caption_tilt: 0`). Add `fun_pacing_gate.natural_speech_passed: true` and `tts_pronunciations`.
   - Set `bgm_source` (`"retrieved_track"`, `"procedural"`, or `"procedural_act_aware"`), `bgm_mood` (`dramatic_news`, `cinematic_tension`, `miami_pop`, `retro_drive`, `tech_pulse`, `comedy_quirk`, or Act-aware long-form moods `palace_drama` and `epic_story`), optional `ambience_bed` (`palace_courtyard`, `throne_hall`, `newsroom_air`, `hearth_embers`), and per-shot `bgm_cue` (`drop_out` / `drop_before_end` for spotted silence).
   - Assign a synced `sfx_type` cue (plus optional `sfx_pan` `0.0..1.0`) to punchlines, reveals, and transitions (Shorts cues: `record_scratch`, `vine_boom`, `siren_chirp`, `bonk_boing`, `cash_ching`, `ding_levelup`, `laser_zap`, `whoosh`, `pop`; cinematic/drama cues: `palace_gong`, `war_drum_hit`, `silk_whoosh`, `jade_clink`, `porcelain_shatter`, `blade_unsheathe`, `guzheng_gliss`, `fire_crackle_burst`, `court_gasp_swell`, `cinematic_riser`, `heartbeat_pulse`).
   - For Shorts, write `loop_bridge` so the closing line connects naturally into Shot 1. For Long-Form, set `ending_style: "cliffhanger"` or `"outro"` (reserving an 8-12s End-Screen slate for `16:9` outros), omit `loop_bridge`, populate `acts[]` (with `bgm_intensity`, `lighting_mood`, `rehook_question`), and plan `packaging` (thumbnails/cover + titles).
3. Run the pre-TTS linter (required) before synthesizing voiceover. It blocks `TTS_MISREAD` risks and prints soft `[AI_SLOP_SCRIPT]` warnings:
   ```sh
   python3 scripts/tts_and_captions_v2.py --lint-plan <project_dir>/shot_plan.json
   ```
   Pass `--strict-slop` to fail with exit code `2` on any `[AI_SLOP_SCRIPT]` warning, or `--no-slop-check` to silence slop warnings during intentional parody.
4. Confirm Beat 1 (`0-3s` Shorts or Part A1-LF `0-20s` Long-Form), pacing (Part A2 or Part A2-LF), and spoken naturalness pass `references/qa_rubric.md` before generating assets.

### Stage 2: Generate turnaround/location sheets, voiceover, captions, and audio stems

1. Generate one 16:9 3-panel turnaround sheet per character (`Front | 3/4 Side | Back`) using `references/sheet_prompt_templates.md` on a plain light-gray background, in a neutral pose, with zero held props. Use Template 1A for 2D stickmen, Template 1B for official product mascots anchored to Stage 0, Template 1C for stylized 3D real people, **Template 1D for long-form multi-state variants** (`sheets/<char>_veiled.png`), and **Template 1E for recurring empty-set location plates** (`sheets/loc_<id>.png`). Compare side by side with `read_file` or `view_file`.
   - **If an image model blocks a public figure's photo:** Open the Stage 0 portrait with `read_file`, enter observable traits directly into Template 1C without attaching the raw photo, inspect side by side, and lock that sheet as the sole Stage 3 reference.
   - For Mode A, upload approved turnaround sheets once with `gemini-api files upload <sheet> --wait`.
2. Synthesize voiceover and captions before animating so every clip matches its exact spoken duration and avoids `AUDIO_DESYNC`. `tts_and_captions_v2.py` supports single-voice Shorts, multi-voice long-form `voice_cast`, bounded concurrency (`--concurrency 4 --max-retries 4`), per-shot `post_gap_sec`, silent reaction beats, `acts[]` boundary summaries, validated `chapters.txt`, `9:16` or `16:9` ASS captions (`kinetic_keyword`, `karaoke_chunk`, `clean_subtitle`, or `none`), and `--export-srt` for YouTube CC:
   ```sh
   # Short-Form (9:16, single voice):
   python3 scripts/tts_and_captions_v2.py \
     --shot-plan <project_dir>/shot_plan.json \
     --out-dir <project_dir>/audio \
     --voice en-US-AvaNeural \
     --caption-style kinetic_keyword \
     --font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto

   # Long-Form (reads voice_cast, aspect_ratio, acts, post_gap_sec from shot_plan.json):
   python3 scripts/tts_and_captions_v2.py \
     --shot-plan <project_dir>/shot_plan.json \
     --out-dir <project_dir>/audio \
     --gap 0.08 --concurrency 4 --max-retries 4 --export-srt
   ```
   This outputs `audio/voiceover.wav`, `audio/shot_timings.json` (including `acts[]`), `audio/chapters.txt` (when `acts` present), zero-overlap `audio/captions.ass` (skipped if `caption_style == "none"`), and optional `audio/captions.srt`.
3. Generate SFX (with optional subtle room-tone `--ambience`) and BGM stems:
   ```sh
   python3 scripts/sfx_engine.py \
     --timings <project_dir>/audio/shot_timings.json \
     --ambience none \
     --out <project_dir>/audio/sfx_track.wav

   # Short-Form news/commentary BGM retrieval:
   python3 scripts/retrieve_music.py \
     --mood dramatic_news \
     --duration <total_sec> \
     --out <project_dir>/audio/bgm.wav

   # Short-Form procedural BGM (dramatic_news, cinematic_tension, miami_pop, retro_drive, tech_pulse, comedy_quirk):
   python3 scripts/make_bgm.py \
     --mood dramatic_news \
     --duration <total_sec> \
     --out <project_dir>/audio/bgm.wav

   # Long-Form Act-aware procedural score (palace_drama or epic_story honoring acts[].bgm_intensity, cliffhanger & bgm_cue):
   python3 scripts/make_bgm.py \
     --mood epic_story \
     --timings <project_dir>/audio/shot_timings.json \
     --out <project_dir>/audio/bgm.wav
   ```

### Stage 3: Generate visuals (Mode A, Mode B, or Hybrid) and animate frame-locked clips

- **Rules for all modes:**
  - Attach only the locked 3-panel turnaround sheet(s) (or active `state_sheets` variant) plus optional location anchor plate (Template 1E) for that shot. Bind references in natural language by upload order (never `<IMAGE_REF_N>`).
  - Append the verbatim `style_bible` (including `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"`).
  - Keep solo shots isolated (`SOLO character: ONLY <char>...`), enforce opposing 180-degree `eyeline` directions on shot-reverse-shot dialogue (or OTS silhouettes on `16:9`), tag inserts (`No named lead characters appear in the foreground`), and keep 2-character shots spatially separated left/right (`Two-character confrontation...`, max 2 leads) to prevent `SOLO_CHAR_BLEED`, `MULTI_CHAR_BLEED`, and `EYELINE_CROSS`.
  - **Short-Form replay loop:** On the final shot (`dual_anchor_loop: true`), attach both the character sheet and `scenes/shot_01.png` so the ending snaps cleanly back to Frame 0. **Long-Form:** Skip replay looping (`dual_anchor_loop: false`) and end on a cliffhanger or natural outro/End-Screen slate.
- **Mode A (`omni_video`):** Render `360p` drafts (`9:16` or `16:9`) via `gemini-api video -m gemini-omni-1.2-flash` (or the `google-genai` Python SDK) into `drafts_360p/shot_XXX.mp4`. In `hybrid` mode, anchor Mode A hero shots to `scenes/shot_XXX.png`, strip audio (`-an`), trim to the exact `shot_timings.json` duration, and save as `clips/<idx:03d>_<shot_id>.mp4`.
- **Mode B (`keyframe_motion`):** Generate keyframes (`scenes/shot_001.png` .. `shot_NNN.png`) in the target aspect ratio (`9:16` or `16:9`) and inspect them with `read_file`. Then animate every shot to its exact spoken duration from `shot_timings.json` using 2x-supersampled eased FFmpeg camera presets (`whip_zoom`, `bounce_pop`, `shake_zoom`, `zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `static_drift`, `slow_dolly_in`, `tilt_up`, `tilt_down`) with `focal_anchor` support so `16:9` rule-of-thirds subjects stay framed:
  ```sh
  # Full Short or full episode with multi-threaded workers:
  python3 scripts/animate_shot_v2.py \
    --timings <project_dir>/audio/shot_timings.json \
    --scenes-dir <project_dir>/scenes \
    --out-dir <project_dir>/clips \
    --workers 4 --skip-existing

  # Parallel Long-Form Act slice (auto-detects 9:16 or 16:9; add --skip-omni in hybrid mode):
  python3 scripts/animate_shot_v2.py \
    --timings <project_dir>/audio/shot_timings.json \
    --scenes-dir <project_dir>/scenes \
    --out-dir <project_dir>/clips \
    --act 5 --workers 4 --skip-existing --skip-omni --fade-out-last
  ```

### Stage 4: Run multi-frame QA (max 2-3 retries per shot)

1. Sample `f_frame0`, `f_start`, `f_mid`, `f_peak`, and `f_end` (`-sseof -0.05`, safe against AAC padding):
   ```sh
   bash scripts/extract_qa_frames.sh <clip.mp4> <project_dir>/qa/<shot_id> [prev_clip.mp4]
   ```
2. Audit sampled frames (or Mode B keyframes before animating) alongside the Stage 0 reference image, the active 3-panel turnaround/state sheet, any location anchor plate, and the previous shot's end frame against `references/qa_rubric.md`.
3. Diagnose failure codes and apply targeted fixes from `references/failure_fix_playbook.md`, capping retries at 3 per shot:
   `HOOK_WEAK`, `EXTRA_LIMB`, `STYLE_DRIFT`, `OFF_MODEL_CHARACTER`, `LIKENESS_DRIFT`, `STATE_CONTINUITY_DRIFT`, `LOCATION_CONTINUITY_DRIFT`, `EYELINE_CROSS`, `SOLO_CHAR_BLEED`, `MULTI_CHAR_BLEED`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `VOICE_CAST_DRIFT`, `AI_SLOP_SCRIPT`, `CAPTION_OVERLAP`, `CAPTION_CLIP_HORIZONTAL`, `AUDIO_DESYNC`, `FRAME_TIMELINE_DRIFT`, `BGM_OVERPOWERS_VO`, `BGM_TONE_MISMATCH`, `ACT_BGM_FATIGUE`, `QA_FILE_TOO_LARGE`.

### Stage 5: Render at `1080p` and optional 2D-to-3D upgrade

1. **Mode A:** Re-render approved shots at `1080p` (`--resolution 1080p`). Mode B clips already render at `1080x1920` (9:16) or `1920x1080` (16:9) via 2x supersampling.
2. **Optional 2D-to-3D upgrade:** Feed `<char>_2d.png` into Template 2 in `references/sheet_prompt_templates.md`, lock `<char>_3d.png`, update `style_bible`, and re-run the proven `shot_plan.json` and `audio/shot_timings.json`.

### Stage 6: Mix 3-stem audio, fast-path assemble, package, and watch with sound

1. If building captions from an `.srt` file (including human mic / ElevenLabs VO) instead of `tts_and_captions_v2.py`:
   ```sh
   python3 scripts/srt_to_ass.py <input.srt> <project_dir>/audio/captions.ass \
     --aspect-ratio 9:16 --font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto
   ```
2. Run `assemble_short.sh`. It verifies zero `CAPTION_OVERLAP`, auto-detects `1080x1920` vs `1920x1080`, checks `time_base`/SAR/stereo before fast-path `-c copy` concat (falling back to per-clip conform on mismatch), burns `.ass` captions with a 2-second keyframe cadence (`-g 60`), mixes 3 stems in lossless 24-bit PCM with peak limiting (`SHORTS_VO_CLARITY=1`, single-point `SHORTS_BGM_LP_HZ=10500` / `-3.0 dB @ 2.9 kHz` BGM carve, smooth musical sidechain ducking, non-ducked SFX), runs 2-pass `-14 LUFS` `loudnorm` (`LRA=7` on `9:16`, `LRA=9` on `16:9`), and prints a mandatory post-norm loudness verification check:
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
   Pass `none` for any unused stem (for example, `none` for `voiceover.wav` when keeping Mode A native Omni audio, or `none` for `captions.ass` on `16:9` YouTube uploads using `audio/captions.srt`).
3. **Final watch-through with audio (mandatory) & Packaging:**
   - **Short-Form (< 90s and < 80 MB):** Open `final_short.mp4` directly with `read_file` or `view_file` and verify dialogue clarity, zero `TTS_MISREAD`/`AI_SLOP_SCRIPT`, synced SFX, clean display captions, consistent likenesses, and smooth replay loop.
   - **Long-Form (> 90s or > 80 MB):** Slice into lossless 60-second review chunks, 10 keyframe screenshots, full + per-segment LUFS audit, and validated `chapters.txt`:
     ```sh
     bash scripts/split_qa_segments.sh \
       <project_dir>/final_episode.mp4 \
       <project_dir>/qa_segments \
       --timings <project_dir>/audio/shot_timings.json
     ```
     Watch **every** `qa_segments/qa_min*.mp4` chunk with audio via `view_file` (or in parallel across QA subagents) and audit `qa_segments/screenshots/frame_*.png`.
   - Export creator packaging (`packaging.md` titles/description/pinned comment/AI disclosure + 2-3 A/B `16:9` thumbnails or `9:16` cover frame).

## Long-Form Video Mode (3-15+ min parallel swarm summary)

For full long-form specifications, read `references/long_form_guide.md`. Key rules that prevent failures at 60-180+ shot scale:

1. **Coordinator locks truth before fanning out:** Stage 0 references, base + state turnaround sheets (`characters[].state_sheets`), recurring location plates (`locations[]`, Template 1E), Writers' Packet (voice cards, per-Act knowledge gate, setup/payoff ledger), merged `shot_plan.json`, calibrated `voice_cast`, `audio/voiceover.wav`, `audio/shot_timings.json`, `audio/chapters.txt`, `audio/bgm.wav` (`make_bgm.py --timings`), and `audio/sfx_track.wav` (with optional `--ambience`).
2. **Parallel Act workers (keyframes + animation):** Assign one subagent per Act to generate `scenes/shot_XXX.png` (keeping `>= 85%` of shots to 0-1 lead characters, flipping `negative_constraints` at state-change cuts, matching 180-degree `eyeline`s) and animate via `animate_shot_v2.py --act <N> --workers 4 --skip-existing` (add `--skip-omni` in `hybrid` mode; globally frame-locked within `<= 1` frame).
3. **Fast-path assembly + chunked multimodal QA:** Assemble with `assemble_short.sh`, slice into 60-second chunks (`split_qa_segments.sh`), and inspect every minute with audio in parallel.
4. **Series Bible handoff (`README_series_bible.md`):** For serialized multi-episode stories, archive `README_series_bible.md` alongside `sheets/`, `shot_plan.json`, `shot_timings.json`, and `captions.ass` documenting plot/cliffhanger + Ep 2 cold-open entry, character states + voice cards, secrets matrix, unresolved setup/payoff ledger, `style_bible`, `locations[]`, `voice_cast`, and `tts_pronunciations`.

## Series and batch production (subagent playbook)

When producing a multi-Short batch or faceless series in parallel:

- Keep one shared `shorts/shared/references/character_registry.json` (updated by Stage 0 `research_characters.py`) and master 3-panel turnaround sheets in `shorts/shared/sheets/<char>.png`. Reuse them read-only across parallel subagents so character likenesses and mascots stay consistent across episodes.
- Alternate voices (`en-US-AvaNeural`, `en-US-EmmaNeural`, `en-US-JennyNeural`) and match BGM moods to episode tone (`dramatic_news`, `cinematic_tension` for news and commentary; `miami_pop` 122 BPM, `retro_drive` 112 BPM, `tech_pulse` 128 BPM, `comedy_quirk` 116 BPM for comedy and upbeat Shorts).
- Maintain one `shorts/production_log.csv` (or Google Sheet) updated in place by the coordinator after each episode completes:
  `episode_id,slug,title,mode,voice,bgm_mood,duration_sec,shot_count,hook_line,qa_rerolls,final_path,status,backlog_notes`

