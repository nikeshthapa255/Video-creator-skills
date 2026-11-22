---
name: viral-animated-shorts
description: End-to-end production pipeline for viral 2D stickman-in-3D and stylized 3D animated Shorts (9:16, 30-55s) using Gemini Omni 1.2 Flash, 3-panel turnaround character sheets, forensic multi-frame VLM QA, and deterministic FFmpeg assembly. Use when the user wants to plan, storyboard, generate character sheets, render, QA, upgrade from 2D to 3D, or assemble animated YouTube Shorts, Reels, or TikToks.
---

# Viral Animated Shorts Pipeline

Produce 9:16 animated Shorts (2D stickman-in-3D or stylized 3D) natively on the Gemini media stack and FFmpeg without paid third-party wrappers or Blender.

## Prerequisites Check

Before running live video generation or assembly, verify local dependencies (test `--version`, not just `which`, because macOS `/usr/bin/python3` may be an uninstalled Xcode CLT stub):
- **Video/Audio API:** `gemini-api whoami` (preferred) or Python `google-genai` SDK with `GEMINI_API_KEY` set (see the `gemini-omni-flash-api` and `gemini-api-cli` skills).
- **Character Sheets:** `gemini-api image` (`gemini-nano-banana-2.1`) or the built-in `image-generator` subagent.
- **Assembly & Frame Sampling:** `ffmpeg -version`, `ffprobe -version`, and `python3 --version` (for caption conversion).

If `gemini-api` or `ffmpeg` is missing, complete Stages 1-2 live (using `image-generator` for sheets), generate `jobs.json` and `.ass` captions, and report exact install commands for the remaining live render/assembly steps.

## Six-Stage Workflow

Store all project artifacts inside a dedicated `<project_dir>/` (e.g., `shorts/<slug>/`):
`shot_plan.json`, `sheets/`, `drafts_360p/`, `qa/`, `finals_1080p/`, `audio/`, `final_short.mp4`.

### Stage 1: Script, Style Bible, and Hook Gate
1. Draft a tight script: 30-45s (2-4 scenes) for comedic skits, or 130-150 words (45-55s) for explainers. Zero intro or greeting.
2. Compile `<project_dir>/shot_plan.json` matching `references/shot_plan_schema.json`:
   - Lock one verbatim `style_bible` string reused across every scene prompt.
   - Define every character with zero held props in their base design.
   - Break scenes into 3-15s Omni clips with `[0-3s]` beat timecodes and Average Shot Length of 1.5-2.5s.
   - Set `audio_mode` to `native_omni` (comedic dialogue/SFX) or `tts_plus_bgm` (narration explainers).
   - Write the final line to loop syntactically into Beat 1.
3. **Hook Gate (required before rendering):** Verify Beat 1 (`0-3s`) against `references/qa_rubric.md` (Hook Gate section). Do not generate video until Beat 1 has frame-0 visual contrast, immediate motion, and an open-loop verbal premise.

### Stage 2: Immutable 3-Panel Turnaround Sheets
1. Build one 16:9 3-panel turnaround sheet per character (`Front | 3/4 Side | Back`) using templates in `references/sheet_prompt_templates.md`.
   - Enforce neutral light-gray background, flat even lighting, neutral expression, and **no held props** (props cause hand-morphing across shots).
2. Generate via `gemini-api image "<prompt>" --aspect-ratio 16:9 --image-size 2K --out <project_dir>/sheets/<char>_2d.png` (or delegate to `image-generator` and copy/convert the output `.jpg`/`.png` into `<project_dir>/sheets/`).
3. Visually inspect every sheet with `read_file`. Re-roll if panels disagree on proportions, outfit, or line weight.
4. When using `gemini-api`, upload approved sheets once (`gemini-api files upload <sheet> --wait`) to reuse `files/<id>` URIs across all scenes.

### Stage 3: Cheap `360p` Draft Pass
1. Generate `9:16` drafts at `360p` with `gemini-omni-1.2-flash` (`--aspect-ratio 9:16 --resolution 360p`) via `gemini-api video` or `generate_video.py --batch jobs_360p.json`. Each item in `jobs_360p.json` uses:
   ```json
   {"prompt": "...", "image": ["sheets/char_2d.png"], "duration": 7, "aspect_ratio": "9:16", "resolution": "360p", "output": "drafts_360p/shot_01.mp4"}
   ```
2. Pass the immutable turnaround sheet(s) (`-i` / `--image`, up to 6 images) into every shot and bind each character in natural language by upload order (never use `<IMAGE_REF_N>` tags).
3. Include the verbatim `style_bible`, explicit negative constraint (`no on-screen text, no watermarks, blank signs`), and single-shot or timecode beats.
4. For 2-character shots, separate characters spatially (left/right) or use shot-reverse-shot to prevent attribute bleeding.
5. For direct continuous cuts, extract the last clean frame of Scene N as a secondary anchor, while keeping the canonical turnaround sheet as the primary anchor.

### Stage 4: Context-Aware Forensic VLM QA (Max 2-3 Retries)
1. Sample frames from each draft clip (`start`, `mid`, `action peak`, `end`) plus the previous shot's end-frame:
   ```sh
   bash scripts/extract_qa_frames.sh <clip.mp4> <project_dir>/qa/<shot_id> [prev_clip.mp4]
   ```
2. Run `gemini-api analyze` (or visual inspection via `read_file`) with `references/qa_rubric.md`, passing the turnaround sheet, previous end-frame, and sampled frames.
3. Diagnose failures by exact frame index and failure code (`HOOK_WEAK`, `EXTRA_LIMB`, `STYLE_DRIFT`, `TEXT_WARP`, `AUDIO_DESYNC`).
4. Apply the matching fix from `references/failure_fix_playbook.md`:
   - Small localized glitch: edit in place via `--previous-interaction-id <id>` (`"<fix>. Keep everything else the same."`).
   - Major anatomy/composition error: simplify action/camera and re-roll.
   - Cap retries at 3 per shot. Log any newly discovered fix in `references/failure_fix_playbook.md`.

### Stage 5: `1080p` Final Render & Optional 2D-to-3D Upgrade
1. Re-render approved shots at `1080p` (`--resolution 1080p`).
2. **Optional 2D-to-3D Upgrade:** Convert each approved 2D sheet into a stylized 3D turnaround sheet using the 3D conversion template in `references/sheet_prompt_templates.md` with `-i <char>_2d.png`. Inspect via `read_file`, update `style_bible` to stylized 3D, and re-run the proven `shot_plan.json`.

### Stage 6: Deterministic FFmpeg Assembly
1. Generate word-timed `.srt` (`gemini-api transcribe -i <audio_or_clip> --format srt`) or export from the script timing.
2. Convert `.srt` to safe-zone `.ass` karaoke subtitles (positioned above the bottom 35% Shorts UI danger zone):
   ```sh
   python3 scripts/srt_to_ass.py <input.srt> <project_dir>/audio/captions.ass
   ```
3. Run the probe -> conform -> concat -> duck -> burn-captions -> 2-pass `-14 LUFS` assembly script:
   ```sh
   bash scripts/assemble_short.sh <project_dir>/finals_1080p <project_dir>/audio/captions.ass <project_dir>/final_short.mp4 [voiceover.wav] [bgm.wav]
   ```
4. Verify `final_short.mp4` dimensions (`1080x1920`), duration, audio loudness (`-14 LUFS`), and visual frame checks before delivering.
