# Video-creator-skills

Antigravity skills for planning, writing, animating, and assembling both **short-form vertical videos** (`30-55s`, `9:16`) and **long-form multi-act animated episodes** (`3-15+ min`, `9:16` vertical micro-dramas or `16:9` horizontal YouTube videos). Use `viral-animated-shorts/` (v4.0) to produce upload-ready animated videos from research to final export, and use `clear-natural-english/` to keep spoken narration, dialogue, YouTube metadata, and everyday docs plain and free of AI filler.

## Skills

### `viral-animated-shorts/` (v4.0)

Seven-stage pipeline (Stage 0 to Stage 6) for 2D stickman-in-3D or stylized 3D animated videos in two **video formats** and three **production modes**:

- **Short-Form Mode (`video_format: "short"`):** `1080x1920` (9:16), 30-55s, 10-14 rapid shots, single-voice narration, and dual-anchor replay loops.
- **Long-Form Video Mode (`video_format: "long_form"`):** 3-15+ min, 3-6 Acts/chapters, 45-180+ shots in either **`9:16` vertical micro-drama** (`1080x1920`, serialized cliffhanger) or **`16:9` horizontal YouTube episode** (`1920x1080`, clean lower-third captions or `.srt` sidecar, YouTube chapters, outro/cliffhanger). See `viral-animated-shorts/references/long_form_guide.md`.
- **Production modes:** **Mode A (`omni_video`)** with Gemini Omni 1.2 Flash (`360p` draft to `1080p` final), **Mode B (`keyframe_motion`)** with zero paid video API keys (verified turnaround sheets, `edge-tts`, kinetic/widescreen captions, procedural SFX/BGM, 2x-supersampled FFmpeg motion), or **Hybrid (`hybrid`)** mixing Mode A hero clips with Mode B narrative shots.

What v4.0 covers:

- **Character research & multi-state sheets (`research_characters.py`, Template 1A-1D):** Verifies real public figures and official mascots in Stage 0 via local registry, `--user-ref`, and Wikipedia REST API. Adds multi-state character turnaround sheets (`characters[].state_sheets` + `shots[].character_states`, e.g., veiled vs. unveiled across Acts) to prevent `STATE_CONTINUITY_DRIFT` over 90+ shots.
- **Multi-voice concurrent TTS & natural spoken English (`tts_and_captions_v2.py`):** Supports top-level `voice_cast` (`speaker -> voice/rate/pitch/keyword_color`), bounded async concurrency (`--concurrency 4`) with exponential retry backoff, per-shot `post_gap_sec` Act pauses, Act boundary summaries in `shot_timings.json`, clean display English vs. phonetic `tts_pronunciations`/`tts_text`, `--lint-plan` (`TTS_MISREAD` + `AI_SLOP_SCRIPT`), and optional `--export-srt` for YouTube CC.
- **Aspect-ratio aware captions (`tts_and_captions_v2.py`, `srt_to_ass.py`):**
  - `9:16` (`1080x1920`): `86px` Fun Kinetic lower-third ASS (`--margin-v 420`, `--max-chars 18`, `+/-2.5 deg` tilt, semantic `--keyword-color auto|palace` or per-speaker hex).
  - `16:9` (`1920x1080`): `62px` widescreen lower-third ASS (`--margin-v 72`, `--max-chars 36`, `--tilt 0`) plus optional clean `.srt` sidecar.
- **Act-aware procedural BGM & vectorized SFX (`make_bgm.py`, `retrieve_music.py`, `sfx_engine.py`):** Passing `--timings audio/shot_timings.json` to `make_bgm.py` (`palace_drama`, `epic_story`) scores each Act dynamically (opening -> tension -> escalation -> climax -> epilogue fade) with speech-pocket FFT EQ. `sfx_engine.py` caches waveforms and mixes 20 comedy and cinematic/drama cues in seconds via NumPy (with pure-stdlib fallback).
- **Frame-locked parallel swarm animation (`animate_shot_v2.py`):** Computes global cumulative frame boundaries across all shots before applying `--act`, `--start-idx`, `--end-idx`, or `--shot-ids` filters (<= 1 frame total drift over 5+ min), supports `--workers`, `--skip-existing`, 3-digit filenames (`001_shot_001.mp4`), 10 camera presets (adding `slow_dolly_in` and `tilt_up`), `--fade-out-last`, and 2x supersampling for both `9:16` and `16:9`.
- **Fast-path `-c copy` assembly & chunked 60s multimodal QA (`assemble_short.sh`, `split_qa_segments.sh`):** Auto-detects `9:16` vs `16:9`, concatenates uniform clips with `-c copy` in seconds, mixes 3 audio stems (`SHORTS_VO_CLARITY=1`, `SHORTS_BGM_GAIN_DB=-16`, `8500 Hz` LP / `-3.5 dB` speech carve, sidechain ducking, non-ducked SFX), and verifies post-norm `-14 LUFS`. For long-form videos (> 90s or > 80 MB), `split_qa_segments.sh` slices lossless 60-second chunks (`qa_min1.mp4..qa_minN.mp4`, < 100 MB each), extracts 10 screenshots, checks LUFS, and exports `chapters.txt`.

### `clear-natural-english/`

Writes, rewrites, reviews, and edits English prose so it reads easily on the first pass and stays free of AI slop. Use it standalone for emails, docs, updates, proposals, and READMEs, or alongside `viral-animated-shorts/` to write natural voiceover lines, multi-character dialogue, YouTube titles, descriptions, and pinned comments. Includes concrete before-and-after examples in `clear-natural-english/references/patterns-and-examples.md`.

## Production scripts (`viral-animated-shorts/scripts/`)

| Script | Stage | Purpose |
| :--- | :--- | :--- |
| `research_characters.py` | Stage 0 | Pure-stdlib character and mascot verifier (local registry, `--user-ref`, and Wikipedia REST API portrait cache to `character_references.json`). |
| `tts_and_captions_v2.py` | Stage 1 & 2 | Lints narration (`--lint-plan`, `--strict-slop`, `--no-slop-check`), synthesizes single-voice or multi-voice `edge-tts` (`voice_cast`, `--concurrency`, `--max-retries`, `post_gap_sec`, `tts_pronunciations`), writes `shot_timings.json` (with `acts[]`), and builds `9:16` or `16:9` ASS captions (plus optional `--export-srt`). |
| `srt_to_ass.py` | Stage 6 | Converts `.srt` files into zero-overlap ASS captions for `9:16` (`86px`, `max-chars 18`, `margin-v 420`) or `16:9` (`62px`, `max-chars 36`, `margin-v 72`, `tilt 0`). |
| `sfx_engine.py` | Stage 2 | Fast vectorized (NumPy + stdlib fallback) procedural SFX renderer (`sfx_track.wav`) with 20 comedy and cinematic/drama cues from `shot_timings.json`. |
| `retrieve_music.py` | Stage 2 | Fetches instrumental BGM tracks (for example `--mood dramatic_news` or `cinematic_tension`) conformed to target duration. |
| `make_bgm.py` | Stage 2 | Generates procedural BGM stems (`dramatic_news`, `cinematic_tension`, `miami_pop`, `retro_drive`, `tech_pulse`, `comedy_quirk`) plus Act-aware long-form scores (`palace_drama`, `epic_story`) locked to `--timings shot_timings.json`. |
| `animate_shot_v2.py` | Stage 3 | Animates `9:16` or `16:9` keyframes with global cumulative frame lock, parallel `--act`/`--start-idx`/`--end-idx`/`--shot-ids` slicing, `--workers`, `--skip-existing`, 10 camera presets, and `--fade-out-last`. |
| `extract_qa_frames.sh` | Stage 4 | Extracts `f_start`, `f_mid`, `f_peak`, and `f_end` (plus the previous shot's end frame) using video-stream duration and `-sseof` for per-shot frame QA. |
| `assemble_short.sh` | Stage 6 | Verifies zero `CAPTION_OVERLAP`, fast-path `-c copy` concats matching `9:16` or `16:9` clips, mixes 3 stems (`SHORTS_VO_CLARITY=1`, carved/ducked BGM, non-ducked SFX), burns ASS captions, runs 2-pass `-14 LUFS` `loudnorm`, and verifies post-norm LUFS. |
| `split_qa_segments.sh` | Stage 6 | Slices long-form MP4s (> 90s / > 80 MB) into lossless 60-second `qa_min*.mp4` chunks (< 100 MB each), extracts 10 keyframe screenshots, measures independent LUFS, and exports `chapters.txt` from `--timings`. |
