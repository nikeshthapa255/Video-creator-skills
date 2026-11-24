# Video-creator-skills

Internal Antigravity skills for AI-assisted video and animated Shorts production.

## Skills

- `viral-animated-shorts/` (v3.1): End-to-end **Seven-Stage (`Stage 0 -> Stage 6`)** production pipeline for viral 2D stickman-in-3D and stylized 3D animated Shorts (`1080x1920`, 30-55s) with two official modes:
  - **Mode A (`omni_video`):** Gemini Omni 1.2 Flash generative video (`360p` draft -> `1080p` final).
  - **Mode B (`keyframe_motion`):** Zero-API-key path using Stage 0 verified references, 9:16 keyframes locked to 3-panel turnaround sheets, audio-first `edge-tts` (`WordBoundary`) timing, procedural SFX, dramatic instrumental music retrieval / procedural BGM, and 2x-supersampled FFmpeg camera motion.

### Key v3.1 Capabilities
- **Stage 0 Web & Visual Character/Mascot Research (`research_characters.py`):** Verifies real public figures (CEOs, executives, commentators) and official product mascots against a local registry, user-supplied photos (`--user-ref`), and the Wikipedia REST API (with opensearch fallback) before generating turnaround sheets or scenes — preventing `OFF_MODEL_CHARACTER` and `LIKENESS_DRIFT`.
- **v3.1 Fun Kinetic Semantic-Neon Captions (`tts_and_captions_v2.py`, `srt_to_ass.py`):** Bold `86px` lower-third ASS subtitles (`--margin-v 420`, `Outline=8`, `Shadow=4.5`, `#30000000`) with elastic entry bounce (`84% -> 122% -> 100%`), `126% -> 108%` keyword pop, alternating `+/-2.5 deg` micro-tilt, semantic neon palette (`--keyword-color auto`: Neon Lime for money/numbers, Fiery Coral-Orange for drama/danger, Electric Cyan for brands/AI, Bright Yellow for active spoken word), strict `--max-chars 18` horizontal safe width, clean `shot_id` scene-cut flushing, and zero-overlap assertion (`CAPTION_OVERLAP`).
- **Dramatic News Music Retrieval & Procedural BGM (`retrieve_music.py`, `make_bgm.py`):** Genre-matched dramatic instrumental tracks (`dramatic_news`, `cinematic_tension`) for breaking news and commentary, alongside upbeat/comedy procedural moods (`miami_pop`, `retro_drive`, `tech_pulse`, `comedy_quirk`).
- **Dialogue-Prioritized Balanced 3-Stem Audio Mixing (`assemble_short.sh`):** Upfront vocal clarity EQ (`SHORTS_VO_CLARITY=1`), subtle speech-band (`2-4 kHz`) frequency-carved and sidechain-ducked BGM bed (`SHORTS_BGM_GAIN_DB=-16`), crisp non-ducked SFX, and 2-pass `-14 LUFS` `loudnorm`.
- **Forensic Multi-Frame + Audio QA & Cast Isolation:** Includes pre-TTS narration linting (`TTS_MISREAD`), Stage 0 Likeness + Hook + Fun/Pacing gates, explicit solo-character isolation (`SOLO_CHAR_BLEED` prevention), dual-anchor seamless replay loops, and multi-Short batch conventions.

## Scripts (`viral-animated-shorts/scripts/`)

| Script | Stage | Purpose |
| :--- | :--- | :--- |
| `research_characters.py` | Stage 0 | Pure-stdlib entity/mascot researcher (local registry + `--user-ref` + Wikipedia REST API portrait download & cache -> `character_references.json`). |
| `tts_and_captions_v2.py` | Stage 1 & 2 | Pre-TTS narration linter (`--lint-plan`), audio-first `edge-tts` VO synthesis, `shot_timings.json`, and v3.1 Fun Kinetic ASS captions (`--font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto`). |
| `srt_to_ass.py` | Stage 6 | Converts `.srt` files into v3.1 Fun Kinetic horizontal-safe, zero-overlap ASS captions (`--font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto`). |
| `sfx_engine.py` | Stage 2 | Synthesizes synced comedic & news-beat procedural SFX stem (`sfx_track.wav`) from `shot_timings.json`. |
| `retrieve_music.py` | Stage 2 | Retrieves genre-matched instrumental BGM tracks (e.g. `--mood dramatic_news`, `cinematic_tension`) conformed to target duration. |
| `make_bgm.py` | Stage 2 | Synthesizes procedural BGM stems (`dramatic_news`, `cinematic_tension`, `miami_pop`, `retro_drive`, `tech_pulse`, `comedy_quirk`). |
| `animate_shot_v2.py` | Stage 3 | Animates Mode B 9:16 keyframes to exact spoken durations with 2x-supersampled FFmpeg camera presets. |
| `extract_qa_frames.sh` | Stage 4 | Extracts `f_start`, `f_mid`, `f_peak`, `f_end` (+ previous end-frame) using video-stream duration + `-sseof` for forensic QA. |
| `assemble_short.sh` | Stage 6 | Verifies zero `CAPTION_OVERLAP`, conforms to `1080x1920`, mixes dialogue-prioritized VO + non-ducked SFX + carved/ducked BGM (`-14 LUFS`), and burns ASS captions. |
