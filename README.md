# Video-creator-skills

Antigravity skills for planning, writing, animating, and assembling short-form vertical videos. Use `viral-animated-shorts/` to produce `1080x1920` animated Shorts from research to final export, and use `clear-natural-english/` to keep spoken narration, YouTube metadata, and everyday docs plain and free of AI filler.

## Skills

### `viral-animated-shorts/` (v3.2)

Seven-stage pipeline (Stage 0 to Stage 6) for 30-55s `1080x1920` (9:16) animated Shorts in 2D stickman-in-3D or stylized 3D. Pick the production mode that fits your setup:

- **Mode A (`omni_video`):** Generates full character animation with Gemini Omni 1.2 Flash (`360p` draft to `1080p` final). Use this when you have `GEMINI_API_KEY`.
- **Mode B (`keyframe_motion`):** Runs end-to-end with zero paid video API keys. It builds 9:16 keyframes from verified 3-panel turnaround sheets, records `edge-tts` (`WordBoundary`) voiceover first, adds procedural SFX and BGM, and animates each frame with 2x-supersampled FFmpeg camera moves.

What v3.2 covers:

- **Character and mascot verification (`research_characters.py`):** Checks public figures (CEOs, executives, commentators) and official product mascots against a local registry, your photos (`--user-ref`), and the Wikipedia REST API (with opensearch fallback). Run this in Stage 0 before generating sheets or scenes to prevent `OFF_MODEL_CHARACTER` and `LIKENESS_DRIFT`.
- **Natural spoken scripts and display captions (`tts_and_captions_v2.py`):** Draws on `clear-natural-english/` and `viral-animated-shorts/references/spoken_english_guide.md` for spoken Shorts writing. Running `--lint-plan` blocks `TTS_MISREAD` glitches and emits soft `AI_SLOP_SCRIPT` warnings (use `--strict-slop` to fail on slop or `--no-slop-check` to turn slop checks off). Optional `tts_pronunciations` and per-shot `tts_text` fields let TTS speak phonetic spellings while captions render display English (`AWS`, `GTA 6`, `911`, `$75,000`, `Nov 24`).
- **Kinetic neon captions (`tts_and_captions_v2.py`, `srt_to_ass.py`):** Renders `86px` lower-third ASS captions (`--margin-v 420`, `Outline=8`, `Shadow=4.5`, `#30000000`) with entry bounce (`84%` to `122%` to `100%`), keyword pop (`126%` to `108%`), and alternating `+/-2.5 deg` tilt. With `--keyword-color auto`, colors match meaning: Neon Lime for money and numbers, Fiery Coral-Orange for drama and danger, Electric Cyan for brands and AI, and Bright Yellow for the active word. Lines cap at `--max-chars 18`, reset at every `shot_id` cut, and fail fast on `CAPTION_OVERLAP`.
- **Music retrieval and procedural BGM (`retrieve_music.py`, `make_bgm.py`):** Matches instrumental music to script tone. Uses `dramatic_news` and `cinematic_tension` for news and commentary, and procedural moods (`miami_pop`, `retro_drive`, `tech_pulse`, `comedy_quirk`) for comedy and upbeat Shorts.
- **3-stem audio mixing (`assemble_short.sh`):** Keeps voiceover clear over music using vocal EQ (`SHORTS_VO_CLARITY=1`), a `-16 dB` BGM bed (`SHORTS_BGM_GAIN_DB=-16`) carved at `2-4 kHz` and sidechain-ducked under speech, non-ducked SFX, and 2-pass `-14 LUFS` `loudnorm`.
- **Multi-frame QA, solo cast isolation, and replay loops:** Checks likeness, hook strength, pacing, and final audio watch-throughs. Isolates single-character shots to stop `SOLO_CHAR_BLEED`, anchors the final shot to Shot 1 for replay loops, and documents parallel batch series workflows.

### `clear-natural-english/`

Writes, rewrites, reviews, and edits English prose so it reads easily on the first pass and stays free of AI slop. Use it standalone for emails, docs, updates, proposals, and READMEs, or alongside `viral-animated-shorts/` to write natural voiceover lines, YouTube titles, descriptions, and pinned comments. Includes concrete before-and-after examples in `clear-natural-english/references/patterns-and-examples.md`.

## Production scripts (`viral-animated-shorts/scripts/`)

| Script | Stage | Purpose |
| :--- | :--- | :--- |
| `research_characters.py` | Stage 0 | Pure-stdlib character and mascot verifier (local registry, `--user-ref`, and Wikipedia REST API portrait cache to `character_references.json`). |
| `tts_and_captions_v2.py` | Stage 1 & 2 | Lints narration (`--lint-plan`, `--strict-slop`, `--no-slop-check`), synthesizes `edge-tts` voiceover (with `tts_pronunciations` and `tts_text` support), writes `shot_timings.json`, and builds kinetic ASS captions (`--font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto`). |
| `srt_to_ass.py` | Stage 6 | Converts `.srt` files into zero-overlap kinetic ASS captions (`--font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto`). |
| `sfx_engine.py` | Stage 2 | Synthesizes synced procedural SFX (`sfx_track.wav`) for comedy and news beats from `shot_timings.json`. |
| `retrieve_music.py` | Stage 2 | Fetches instrumental BGM tracks (for example `--mood dramatic_news` or `cinematic_tension`) conformed to target duration. |
| `make_bgm.py` | Stage 2 | Generates procedural BGM stems (`dramatic_news`, `cinematic_tension`, `miami_pop`, `retro_drive`, `tech_pulse`, `comedy_quirk`). |
| `animate_shot_v2.py` | Stage 3 | Animates Mode B 9:16 keyframes to exact spoken durations with 2x-supersampled FFmpeg camera presets. |
| `extract_qa_frames.sh` | Stage 4 | Extracts `f_start`, `f_mid`, `f_peak`, and `f_end` (plus the previous shot's end frame) using video-stream duration and `-sseof` for frame QA. |
| `assemble_short.sh` | Stage 6 | Verifies zero `CAPTION_OVERLAP`, conforms clips to `1080x1920`, mixes voiceover (`SHORTS_VO_CLARITY=1`), non-ducked SFX, and carved/ducked BGM (`SHORTS_BGM_GAIN_DB=-16`) at `-14 LUFS`, and burns ASS captions. |
