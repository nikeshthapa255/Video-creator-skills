---
name: viral-animated-shorts
description: >-
  End-to-end production pipeline for viral 2D stickman-in-3D and stylized 3D
  animated Shorts (9:16, 30-55s) in two production modes, Mode A (Gemini Omni
  1.2 Flash generative video) and Mode B (zero-API-key keyframe, 2x-supersampled
  FFmpeg motion, edge-tts, and procedural SFX/BGM). Includes 3-panel turnaround
  character sheets, audio-first timing, natural spoken English linting,
  multi-frame and audio QA, dual-anchor replay loops, and 3-stem FFmpeg
  assembly. Use when the user wants to plan, storyboard, generate character
  sheets, animate, QA, upgrade from 2D to 3D, batch-produce, or assemble
  animated YouTube Shorts, Reels, or TikToks.
---

# Viral animated Shorts pipeline (v3.2)

Produce upload-ready `1080x1920` (9:16) animated Shorts (2D stickman-in-3D or stylized 3D) in two production modes:

- **Mode A (`omni_video`):** Generates full character animation with Gemini Omni 1.2 Flash (`360p` draft to `1080p` final) via the `gemini-api` CLI or `google-genai` SDK. Pick this when you have `GEMINI_API_KEY` and want fluid generated video.
- **Mode B (`keyframe_motion`, no video API key required):** Generates per-shot 9:16 keyframes from verified 3-panel turnaround sheets (`image-generator` or `gemini-api image`) and records `edge-tts` voiceover first. Adds kinetic captions, SFX, and BGM, then animates each frame with 2x-supersampled FFmpeg camera moves. Runs end-to-end with zero paid video API keys.

## Prerequisites check

Verify tools with `--version` rather than `which`, because macOS `/usr/bin/python3` may be an uninstalled Xcode CLT stub:

- **Both modes:** `ffmpeg -version`, `ffprobe -version`, `python3 --version`
- **Mode A:** `gemini-api whoami` (or the `google-genai` Python SDK with `GEMINI_API_KEY` set)
- **Mode B:** `python3 -c "import edge_tts"` (install via `pip install edge-tts`), plus the `image-generator` subagent or `gemini-api image`

If `gemini-api` or `GEMINI_API_KEY` is unavailable, default to **Mode B (`keyframe_motion`)** instead of stopping at Stage 2.

## Seven-stage workflow (Stage 0 to Stage 6)

Store all project files inside `<project_dir>/` (for example, `shorts/<slug>/`): `character_references.json`, `references/`, `shot_plan.json`, `sheets/`, `scenes/` (Mode B keyframes), `drafts_360p/` (Mode A), `clips/` or `finals_1080p/`, `qa/`, `audio/`, and `final_short.mp4`.

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

1. Write a tight script of **95-115 words (~34-42s)** across **10-14 rapid shots** (target **<= 3.5s average shot length**). Put a visual gag, prop reaction, or tonal shift every 2-3 shots instead of a single-pose lecture, and skip intros or greetings.

#### Natural spoken English and scriptwriting

Follow `references/spoken_english_guide.md` and the sibling `../clear-natural-english/SKILL.md` when drafting `dialogue_or_vo`:

- Never drop articles, verbs, or objects to fit a word-count box. Vary cadence across shots by mixing 4-7 word punchlines with 12-16 word setup lines instead of writing metronomic telegraphese.
- Keep `dialogue_or_vo` in display English (`AWS`, `GTA 6`, `911`, `$75,000`, `Nov 24`). Put phonetic spellings (`A W S`, `GTA Six`, `nine-one-one`) in top-level `tts_pronunciations` or per-shot `tts_text` so phonetic workarounds never leak into burned-in captions.
- Use concrete actors and active verbs. Replace trailing `"-ing"` participle riders (`catching...`, `firing...`, `slapping...`) with a second active verb or a short second sentence, and replace press-release legalese (`keep full liability`) with everyday speech (`on the hook`).
- Cut spoken AI slop: dead openers (`In today's world`, `Have you ever wondered`), fake contrasts (`not just X, it's Y`; `Forget X - Y`), and colon reveals (`Plot twist:`, `Here's the catch:`). Also cut dramatic fragments (`Total privacy disaster.`, `Let that sink in.`), stock buzzwords (`delve`, `landscape`, `crucial`, `leverage`), and fortune-cookie morals.
- Do not open every Short with `"Because..."` or close every Short with a flat `"Which is why..."` drop. Open Shot 1 with a standalone concrete hook that lands cold at `0:00`, and rotate natural loop bridges or visual callbacks.
- Ground every proper name with a quick role on first mention (for example, `Education Minister Choi`), and keep one Short to one clear thread (or introduce both beats in Shot 1). Apply `../clear-natural-english/SKILL.md` to YouTube titles, descriptions, and pinned comments, and cut em-dash spam and recycled openers (`Be honest:`, `Imagine...`).

2. Compile `<project_dir>/shot_plan.json` matching `references/shot_plan_schema.json`:
   - Set `production_mode` (`omni_video` or `keyframe_motion`), `audio_mode` (`tts_plus_bgm` for audio-first pacing, or `native_omni` for Mode A comedic dialogue), and `character_references_path` pointing to `<project_dir>/character_references.json`.
   - Lock one verbatim `style_bible` string and reuse it in every shot prompt. **Never write `"blank signs"`**, because models render the literal word `"BLANK"`. Always write `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"`.
   - Define every character with `no_held_props_confirmed: true`, `reference_image_path` (from Stage 0), and `likeness_verified: true` (after inspecting references and sheets).
   - Set caption options (`caption_style: "kinetic_keyword"`, `fun_pacing_gate.max_caption_chars: 18`, `caption_font_size: 86`, `caption_margin_v: 420`, `caption_keyword_color: "auto"`). Add optional `highlight_keywords` (top-level or per-shot words, dollar amounts, or punchline nouns), `fun_pacing_gate.natural_speech_passed: true`, and optional `tts_pronunciations` or per-shot `tts_text` overrides.
   - Set `bgm_source` (`"retrieved_track"` or `"procedural"`) and `bgm_mood`. Use `"dramatic_news"` or `"cinematic_tension"` for news and commentary, or `"miami_pop"`, `"retro_drive"`, `"tech_pulse"`, or `"comedy_quirk"` for comedy and upbeat Shorts (plus optional `bgm_query`).
   - Assign a synced `sfx_type` cue to punchlines and transitions (`record_scratch`, `vine_boom`, `siren_chirp`, `bonk_boing`, `cash_ching`, `ding_levelup`, `laser_zap`, `whoosh`, `pop`).
   - Write `loop_bridge` so the closing line connects naturally into Shot 1's opening line.
3. Run the pre-TTS linter (required) before synthesizing voiceover. It blocks `TTS_MISREAD` risks (`$700 PS5 Pro` to `700-dollar PS5 Pro`, `no 60 FPS` to `no sixty FPS`; move phonetic fixes into `tts_pronunciations` or `tts_text` to protect captions) and prints soft `[AI_SLOP_SCRIPT]` warnings:
   ```sh
   python3 scripts/tts_and_captions_v2.py --lint-plan <project_dir>/shot_plan.json
   ```
   Pass `--strict-slop` to fail with exit code `2` on any `[AI_SLOP_SCRIPT]` warning, or `--no-slop-check` to silence slop warnings during intentional parody.
4. Confirm Beat 1 (`0-3s`), pacing, and spoken naturalness (zero `AI_SLOP_SCRIPT`) pass Parts A1 and A2 of `references/qa_rubric.md` before generating assets.

### Stage 2: Generate 3-panel turnaround sheets, voiceover, captions, and audio stems

1. Generate one 16:9 3-panel turnaround sheet per character (`Front | 3/4 Side | Back`) using `references/sheet_prompt_templates.md` on a plain light-gray background, in a neutral pose, with zero held props. Use Template 1A for 2D stickmen, Template 1B for official product mascots anchored to the Stage 0 image, and Template 1C for stylized 3D real people. Save to `<project_dir>/sheets/<char>_2d.png` (or `<char>_3d.png`) and compare side by side against the Stage 0 reference image with `read_file` or `view_file`.
   - **If an image model blocks a public figure's photo:** Open the Stage 0 portrait with `read_file`. Enter observable traits (hairline and color, facial structure, eyewear, age, signature jacket or attire) directly into Template 1C without attaching the raw photo. Check the generated 3-panel sheet side by side against the Stage 0 portrait, then use that locked sheet as the sole reference for every Stage 3 scene.
   - For Mode A, upload approved turnaround sheets once with `gemini-api files upload <sheet> --wait`.
2. Synthesize voiceover and kinetic captions before animating (standard for Mode B and `tts_plus_bgm` Mode A) so every clip matches its exact spoken duration and avoids `AUDIO_DESYNC`. The same command renders `86px` lower-third ASS captions (`Outline=8`, `Shadow=4.5`, `#30000000`, `--keyword-color auto`) and resets chunks on every `shot_id` cut. Use `--margin-v 420` to stay above the Shorts UI and off character torsos, and cap lines at `--max-chars 18` so `126%` keyword pop and `+/-2.5 deg` tilt never clip:
   ```sh
   python3 scripts/tts_and_captions_v2.py \
     --shot-plan <project_dir>/shot_plan.json \
     --out-dir <project_dir>/audio \
     --voice en-US-AvaNeural \
     --caption-style kinetic_keyword \
     --font-size 86 \
     --max-chars 18 \
     --margin-v 420 \
     --keyword-color auto
   ```
   This outputs `audio/voiceover.wav`, `audio/shot_timings.json`, and zero-overlap `audio/captions.ass` synced to `edge-tts` `WordBoundary` timestamps. When `tts_pronunciations` or `tts_text` is set, TTS speaks the phonetic text while captions display `dialogue_or_vo`.
3. Generate SFX and BGM stems:
   ```sh
   python3 scripts/sfx_engine.py \
     --timings <project_dir>/audio/shot_timings.json \
     --out <project_dir>/audio/sfx_track.wav

   # Recommended for breaking news and commentary (fetches a dramatic instrumental track):
   python3 scripts/retrieve_music.py \
     --mood dramatic_news \
     --duration <total_sec> \
     --out <project_dir>/audio/bgm.wav

   # Procedural fallback or upbeat tracks (dramatic_news, cinematic_tension, miami_pop, retro_drive, tech_pulse, comedy_quirk):
   python3 scripts/make_bgm.py \
     --mood dramatic_news \
     --duration <total_sec> \
     --out <project_dir>/audio/bgm.wav
   ```

### Stage 3: Generate visuals (Mode A or Mode B), isolate solo characters, and anchor the loop

- **Rules for both modes:**
  - Attach only the locked 3-panel turnaround sheet(s) for characters appearing in that shot. Bind characters in natural language by upload order (never `<IMAGE_REF_N>`).
  - Append the verbatim `style_bible` (including `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"`).
  - When a shot features one character, include this exact isolation line so `style_bible` or cast descriptions never leak secondary characters into the frame (prevents `SOLO_CHAR_BLEED`):
    `SOLO character: ONLY <char> appears in this frame; no other characters, mascots, or co-hosts appear anywhere in the scene.`
  - Keep 2-character shots spatially separated (left and right) or use shot-reverse-shot to prevent `MULTI_CHAR_BLEED`.
  - When generating the final shot (for example, `shot_12`), attach both the character turnaround sheet and `scenes/shot_01.png` (or Shot 1's first frame) as references. This snaps the ending back onto Shot 1's exact setting, lighting, and staging for replay loops.
- **Mode A (`omni_video`):** Render `9:16` `360p` drafts via `gemini-api video -m gemini-omni-1.2-flash` or `generate_video.py --batch jobs_360p.json`. Each item in `jobs_360p.json` uses:
  ```json
  {"prompt": "...", "image": ["sheets/char_2d.png"], "duration": 4, "aspect_ratio": "9:16", "resolution": "360p", "output": "drafts_360p/shot_01.mp4"}
  ```
- **Mode B (`keyframe_motion`):** Generate 9:16 keyframes (`scenes/shot_01.png` to `shot_NN.png`) anchored to the turnaround sheet and inspect them with `read_file`. Then animate every shot to its exact spoken duration from `shot_timings.json` using 2x-supersampled FFmpeg camera presets (`whip_zoom`, `bounce_pop`, `shake_zoom`, `zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `static_drift`, optional `--flash-in`):
  ```sh
  python3 scripts/animate_shot_v2.py \
    --timings <project_dir>/audio/shot_timings.json \
    --scenes-dir <project_dir>/scenes \
    --out-dir <project_dir>/clips
  ```

### Stage 4: Run multi-frame QA (max 2-3 retries per shot)

1. Sample frames using video-stream duration and `-sseof` (safe against AAC padding):
   ```sh
   bash scripts/extract_qa_frames.sh <clip.mp4> <project_dir>/qa/<shot_id> [prev_clip.mp4]
   ```
2. Audit sampled frames (or Mode B keyframes before animating) alongside the Stage 0 reference image, the 3-panel turnaround sheet, and the previous shot's end frame against `references/qa_rubric.md`.
3. Diagnose failure codes and apply targeted fixes from `references/failure_fix_playbook.md`, capping retries at 3 per shot:
   `HOOK_WEAK`, `EXTRA_LIMB`, `STYLE_DRIFT`, `OFF_MODEL_CHARACTER`, `LIKENESS_DRIFT`, `SOLO_CHAR_BLEED`, `MULTI_CHAR_BLEED`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `AI_SLOP_SCRIPT`, `CAPTION_OVERLAP`, `CAPTION_CLIP_HORIZONTAL`, `AUDIO_DESYNC`, `BGM_OVERPOWERS_VO`, `BGM_TONE_MISMATCH`.

### Stage 5: Render at `1080p` and optional 2D-to-3D upgrade

1. **Mode A:** Re-render approved shots at `1080p` (`--resolution 1080p`). Mode B clips already render at `1080x1920` via 2x supersampling.
2. **Optional 2D-to-3D upgrade:** Feed `<char>_2d.png` into the 3D conversion template in `references/sheet_prompt_templates.md`, lock `<char>_3d.png`, update `style_bible`, and re-run the proven `shot_plan.json` and `audio/shot_timings.json`.

### Stage 6: Mix 3-stem audio, assemble, and watch with sound

1. If building captions from an `.srt` file instead of `tts_and_captions_v2.py`:
   ```sh
   python3 scripts/srt_to_ass.py <input.srt> <project_dir>/audio/captions.ass \
     --font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto
   ```
2. Run `assemble_short.sh`. It verifies zero `CAPTION_OVERLAP`, conforms clips to `1080x1920`, burns `.ass` captions, and probes the final file. For audio, it applies vocal EQ (`SHORTS_VO_CLARITY=1`), mixes BGM at `-16 dB` (`SHORTS_BGM_GAIN_DB=-16`) with `2-4 kHz` speech-band carving and sidechain ducking, layers non-ducked SFX, and normalizes to `-14 LUFS` with 2-pass `loudnorm`:
   ```sh
   SHORTS_VO_CLARITY=1 SHORTS_BGM_GAIN_DB=-16 \
   bash scripts/assemble_short.sh \
     <project_dir>/clips \
     <project_dir>/audio/captions.ass \
     <project_dir>/final_short.mp4 \
     <project_dir>/audio/voiceover.wav \
     <project_dir>/audio/bgm.wav \
     <project_dir>/audio/sfx_track.wav
   ```
   Pass `none` for any unused stem (for example, `none` for `voiceover.wav` when keeping Mode A native Omni audio).
3. Open `final_short.mp4` **with audio** via `read_file` or `view_file` (do not rely on still QA frames alone) and verify:
   - Clear dialogue with zero `TTS_MISREAD` or `AI_SLOP_SCRIPT` over genre-matched BGM (no `BGM_OVERPOWERS_VO` or `BGM_TONE_MISMATCH`)
   - SFX aligned with visual beats and steady sidechain ducking
   - Zero `CAPTION_OVERLAP` and zero `CAPTION_CLIP_HORIZONTAL` at `1080px` width, with display English captions (no leaked phonetic TTS spellings)
   - Consistent character likeness or official mascot design, plus a replay loop that cuts back to Shot 1 without a visual jump

## Series and batch production (subagent playbook)

When producing a multi-Short batch or faceless series in parallel:

- Keep one shared `shorts/shared/references/character_registry.json` (updated by Stage 0 `research_characters.py`) and master 3-panel turnaround sheets in `shorts/shared/sheets/<char>.png`. Reuse them read-only across parallel subagents so character likenesses and mascots stay consistent across episodes.
- Alternate voices (`en-US-AvaNeural`, `en-US-EmmaNeural`, `en-US-JennyNeural`) and match BGM moods to episode tone (`dramatic_news`, `cinematic_tension` for news and commentary; `miami_pop` 122 BPM, `retro_drive` 112 BPM, `tech_pulse` 128 BPM, `comedy_quirk` 116 BPM for comedy and upbeat Shorts).
- Maintain one `shorts/production_log.csv` (or Google Sheet) updated in place by the coordinator after each episode completes:
  `episode_id,slug,title,mode,voice,bgm_mood,duration_sec,shot_count,hook_line,qa_rerolls,final_path,status,backlog_notes`
