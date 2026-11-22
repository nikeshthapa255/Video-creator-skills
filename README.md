# Video-creator-skills

Internal Antigravity skills for AI-assisted video and animated Shorts production.

## Skills

- `viral-animated-shorts/` (v2): End-to-end production pipeline for viral 2D stickman-in-3D and stylized 3D animated Shorts (`1080x1920`, 30-55s) with two official modes:
  - **Mode A (`omni_video`):** Gemini Omni 1.2 Flash generative video (`360p` draft -> `1080p` final).
  - **Mode B (`keyframe_motion`):** Zero-API-key path using 9:16 keyframes locked to 3-panel turnaround sheets, audio-first `edge-tts` (`WordBoundary`) timing, procedural comedic SFX (`sfx_engine.py`), procedural BGM (`make_bgm.py`), and 2x-supersampled FFmpeg camera motion (`animate_shot_v2.py`).
  - Includes pre-TTS narration linting (`TTS_MISREAD`), Fun/Pacing + Hook Gates, dual-anchor seamless replay loops, zero-overlap safe-zone ASS captions (`CAPTION_OVERLAP` assertion), 3-stem FFmpeg assembly (`-14 LUFS`), and multi-Short series/batch conventions.
