---
name: viral-animated-shorts
description: End-to-end production pipeline for viral 2D stickman-in-3D and stylized 3D animated Shorts (9:16, 30-55s) with two production modes: Mode A (Gemini Omni 1.2 Flash generative video) and Mode B (zero-API-key keyframe + 2x-supersampled FFmpeg motion + edge-tts + procedural SFX/BGM). Includes 3-panel turnaround character sheets, audio-first timing, forensic multi-frame + audio QA, dual-anchor seamless loops, and deterministic 3-stem FFmpeg assembly. Use when the user wants to plan, storyboard, generate character sheets, animate, QA, upgrade from 2D to 3D, batch-produce, or assemble animated YouTube Shorts, Reels, or TikToks.
---

# Viral Animated Shorts Pipeline (v2)

Produce upload-ready `1080x1920` (9:16) animated Shorts (2D stickman-in-3D or stylized 3D) in two official production modes:

- **Mode A (`omni_video`):** Generative video with **Gemini Omni 1.2 Flash** (`360p` draft -> `1080p` final) via `gemini-api` CLI or `google-genai` SDK. Best when you have `GEMINI_API_KEY` and want full fluid character animation.
- **Mode B (`keyframe_motion`, Zero API-Key Fallback):** Per-shot 9:16 keyframes locked to the 3-panel turnaround sheet (via `image-generator` or `gemini-api image`) + **audio-first** `edge-tts` neural voiceover + procedural comedic SFX (`sfx_engine.py`) + procedural BGM (`make_bgm.py`) + 2x-supersampled FFmpeg camera motion (`animate_shot_v2.py`) + 3-stem assembly (`assemble_short.sh`). Runs end-to-end with zero paid video API keys.

## Prerequisites Check

Verify tools with `--version` (not just `which`, because macOS `/usr/bin/python3` may be an uninstalled Xcode CLT stub):
- **Both Modes:** `ffmpeg -version`, `ffprobe -version`, `python3 --version`
- **Mode A:** `gemini-api whoami` (or `google-genai` Python SDK with `GEMINI_API_KEY` set)
- **Mode B:** `python3 -c "import edge_tts"` (install via `pip install edge-tts`) + `image-generator` subagent (or `gemini-api image`)

If `gemini-api` / `GEMINI_API_KEY` is unavailable, default to **Mode B (`keyframe_motion`)** instead of stopping at Stage 2.

## Six-Stage Workflow

Store all artifacts inside `<project_dir>/` (e.g., `shorts/<slug>/`):
`shot_plan.json`, `sheets/`, `scenes/` (Mode B keyframes), `drafts_360p/` (Mode A), `clips/` or `finals_1080p/`, `qa/`, `audio/`, `final_short.mp4`.

### Stage 1: Script, Audio-First Plan, and Double Gate (Hook + Fun/Pacing)
1. Write a tight, high-energy script: **95-115 words (~34-42s)** across **10-14 rapid shots** (target **<= 3.5s average shot length**, with a visual gag or tonal shift every 2-3 shots — never a single-pose lecture). Zero intro or greeting.
2. Compile `<project_dir>/shot_plan.json` matching `references/shot_plan_schema.json`:
   - Set `production_mode` (`omni_video` or `keyframe_motion`) and `audio_mode` (`tts_plus_bgm` recommended for audio-first pacing, or `native_omni` for Mode A comedic dialogue).
   - Lock one verbatim `style_bible` string reused across every shot prompt. **Never write `"blank signs"`** (models render the literal word `"BLANK"`). Always use `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"`.
   - Define every character with `no_held_props_confirmed: true` on their turnaround sheet.
   - Assign a comedic `sfx_type` cue to punchline/transition beats (`record_scratch`, `vine_boom`, `siren_chirp`, `bonk_boing`, `cash_ching`, `ding_levelup`, `laser_zap`, `whoosh`, `pop`).
   - Write `loop_bridge` so the final line flows syntactically into Shot 1's opening line.
3. **Pre-TTS Narration Lint (Mandatory):** Run the linter before synthesizing VO to block `TTS_MISREAD` glitches (`$700 PS5 Pro` -> `700-dollar PS5 Pro`, `no 60 FPS` -> `no sixty FPS`):
   ```sh
   python3 scripts/tts_and_captions_v2.py --lint-plan <project_dir>/shot_plan.json
   ```
4. **Hook Gate + Fun/Pacing Gate:** Confirm Beat 1 (`0-3s`) and overall pacing pass Parts A1 and A2 of `references/qa_rubric.md` before generating assets.

### Stage 2: Immutable 3-Panel Turnaround Sheets + Audio-First Timing
1. **Turnaround Sheet:** Generate one 16:9 3-panel sheet per character (`Front | 3/4 Side | Back`) using `references/sheet_prompt_templates.md` (plain light-gray background, neutral pose, zero held props). Generate via `gemini-api image` or `image-generator`, copy/convert to `<project_dir>/sheets/<char>_2d.png`, and inspect with `read_file`. For Mode A, upload once with `gemini-api files upload <sheet> --wait`.
2. **Audio-First Synthesis (Standard for Mode B & `tts_plus_bgm` Mode A):** Synthesize per-shot VO first so every clip is animated to its exact spoken duration (eliminates `AUDIO_DESYNC`):
   ```sh
   python3 scripts/tts_and_captions_v2.py \
     --shot-plan <project_dir>/shot_plan.json \
     --out-dir <project_dir>/audio \
     --voice en-US-AvaNeural
   ```
   This outputs `audio/voiceover.wav`, exact `audio/shot_timings.json`, and zero-overlap bounce-pop `audio/captions.ass` built from exact `edge-tts` `WordBoundary` timestamps.
3. **Procedural SFX + BGM Stems:**
   ```sh
   python3 scripts/sfx_engine.py --timings <project_dir>/audio/shot_timings.json --out <project_dir>/audio/sfx_track.wav
   python3 scripts/make_bgm.py --mood miami_pop --duration <total_sec> --out <project_dir>/audio/bgm.wav
   ```

### Stage 3: Visual Generation (Mode A or Mode B) + Dual-Anchor Loop
- **Common Rules (Both Modes):**
  - Attach the immutable 3-panel turnaround sheet to every shot prompt; bind characters in natural language by upload order (never `<IMAGE_REF_N>`).
  - Append the verbatim `style_bible` (including `"unadorned facades, plain empty surfaces, zero letters, zero digits, zero signage"`).
  - Keep 2-character shots spatially separated (left/right) or use shot-reverse-shot to prevent `MULTI_CHAR_BLEED`.
  - **Dual-Anchor Loop (Final Shot):** When generating the final shot (e.g., `shot_12`), pass **both** the character turnaround sheet **and** `scenes/shot_01.png` (or Shot 1's first frame) as references so the final frame snaps back onto Shot 1's exact setting, lighting, and staging for seamless replay loops.
- **Mode A (`omni_video`):** Render `9:16` `360p` drafts via `gemini-api video -m gemini-omni-1.2-flash` or `generate_video.py --batch jobs_360p.json`. Each item in `jobs_360p.json` uses:
  ```json
  {"prompt": "...", "image": ["sheets/char_2d.png"], "duration": 4, "aspect_ratio": "9:16", "resolution": "360p", "output": "drafts_360p/shot_01.mp4"}
  ```
- **Mode B (`keyframe_motion`):** Generate 9:16 keyframes (`scenes/shot_01.png` .. `shot_NN.png`) anchored to the turnaround sheet, inspect them via `read_file`, then animate every shot to its exact spoken duration from `shot_timings.json` using 2x-supersampled FFmpeg camera moves (`whip_zoom`, `bounce_pop`, `shake_zoom`, `zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `static_drift`, optional `--flash-in`):
  ```sh
  python3 scripts/animate_shot_v2.py \
    --timings <project_dir>/audio/shot_timings.json \
    --scenes-dir <project_dir>/scenes \
    --out-dir <project_dir>/clips
  ```

### Stage 4: Context-Aware Forensic QA (Max 2-3 Retries)
1. Sample frames using video-stream duration + `-sseof` (safe against AAC padding):
   ```sh
   bash scripts/extract_qa_frames.sh <clip.mp4> <project_dir>/qa/<shot_id> [prev_clip.mp4]
   ```
2. Audit sampled frames (or Mode B keyframes before animating) alongside the turnaround sheet and previous shot end-frame against `references/qa_rubric.md`.
3. Diagnose exact failure codes (`HOOK_WEAK`, `EXTRA_LIMB`, `STYLE_DRIFT`, `MULTI_CHAR_BLEED`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `CAPTION_OVERLAP`, `AUDIO_DESYNC`) and apply fixes from `references/failure_fix_playbook.md`. Cap retries at 3 per shot.

### Stage 5: `1080p` Final Render & Optional 2D-to-3D Upgrade
1. **Mode A:** Re-render approved shots at `1080p` (`--resolution 1080p`). (Mode B clips already render at `1080x1920` via 2x supersampling.)
2. **Optional 2D-to-3D Upgrade:** Feed `<char>_2d.png` into the 3D conversion template in `references/sheet_prompt_templates.md`, lock `<char>_3d.png`, update `style_bible`, and re-run the proven `shot_plan.json` + `audio/shot_timings.json`.

### Stage 6: Deterministic 3-Stem FFmpeg Assembly & Audio Watch-Through
1. If building captions from an `.srt` instead of `tts_and_captions_v2.py`:
   ```sh
   python3 scripts/srt_to_ass.py <input.srt> <project_dir>/audio/captions.ass
   ```
2. Run the 3-stem assembler (verifies zero `CAPTION_OVERLAP`, conforms to `1080x1920`, mixes clean VO + non-ducked SFX + sidechain-ducked BGM, burns safe-zone `.ass` captions, applies 2-pass `-14 LUFS` `loudnorm`, and probes output):
   ```sh
   bash scripts/assemble_short.sh \
     <project_dir>/clips \
     <project_dir>/audio/captions.ass \
     <project_dir>/final_short.mp4 \
     <project_dir>/audio/voiceover.wav \
     <project_dir>/audio/bgm.wav \
     <project_dir>/audio/sfx_track.wav
   ```
   Pass `none` for any unused stem (e.g., `none` for `voiceover.wav` when keeping Mode A native Omni audio).
3. **Final Multimodal Watch-Through (Mandatory):** Inspect `final_short.mp4` **with audio** via `read_file` (not just still QA frames) to confirm zero `TTS_MISREAD`, crisp SFX alignment, zero caption overlap, and seamless loop feel.

## Series & Batch Production (Subagent Playbook)

When producing a multi-Short batch or faceless series in parallel:
- **Shared Master Sheet:** Generate and approve one master 3-panel turnaround sheet in `shorts/shared/sheets/<mascot>.png`, then reuse it read-only across parallel subagents for instant channel recognition.
- **Voice & BGM Rotation:** Rotate voices (`en-US-AvaNeural`, `en-US-EmmaNeural`, `en-US-JennyNeural`) and BGM moods (`miami_pop` 122 BPM, `retro_drive` 112 BPM, `tech_pulse` 128 BPM, `comedy_quirk` 116 BPM) across episodes so back-to-back Shorts feel distinct.
- **Single Production Log:** Maintain one `shorts/production_log.csv` (or Google Sheet) updated in-place by the coordinator after each episode completes:
  `episode_id,slug,title,mode,voice,bgm_mood,duration_sec,shot_count,hook_line,qa_rerolls,final_path,status,backlog_notes`
