# Trip Cinema Studio Rules

Follow these rules whenever `trip-cinema-studio` is active:

## 1. Raw Media Immutability
- Never delete, overwrite, rename, or move the user's original raw photos, videos, or audio files.
- Write all proxies, contact sheets, intermediate renders, manifests, and final exports inside `./work/` or `./output/`.

## 2. Human Face & Authenticity Safety
- Never run generative Image-to-Video (I2V) on photos or shots containing real human faces or portraits (`has_human_closeup: true`).
- Use deterministic 2x-4x supersampled FFmpeg Ken Burns motion or 2.5D depth parallax for human subjects.
- Reserve generative I2V (e.g., `gemini-omni-1.2-flash`) strictly for user-approved, non-human scenic hero stills, and never exceed the user's approved `$` cost cap.
- Never invent dates, GPS coordinates, camera settings, or place names; read them directly from EXIF metadata or `manifest.json`.

## 3. Mandatory 4-Gate Interactive `/grill-me` Loop
Do not start final rendering until the user has explicitly approved all four interactive gates via artifacts:
1. **Gate 1 — Chapters & VIP People Artifact**: Present numbered `3x3` contact sheets, detected chapters, recurring faces, and borderline rejects; record user-pinned must-keep shots as `locked_by_user: true`.
2. **Gate 2 — Color Look, Title & Layout Artifact**: Present side-by-side graded preview stills from actual trip photos, chapter title/map styling, and vertical `9:16` layout options (`split_diptych_pair` vs `blurred_bg_pillarbox`).
3. **Gate 3 — BGM & Pacing Artifact**: Present the BGM Picker (open-source/CC0 suggestions, optional `lyria-3.5` previews at `$0.08`/track, or a user-supplied local track) with BPM, energy drops, and pacing curve. **No voiceover.**
4. **Gate 4 — Visual Storyboard & I2V Cost Gate**: Present the shot-by-shot visual strip, exact durations, motion types, and exact `$` cost cap (`$0` when local-only).

## 4. Visual Editorial Chapter Cards (Never Plain Black Slides)
- Every chapter/day transition card MUST pair refined serif/gold editorial typography (`eyebrow`, `title`) and animated dividers over a softly blurred, vignetted, slow-zooming hero photo (`backdrop_path`) from that chapter — never flat black text slides.

## 5. Disk-Backed State, QA Gates & Output Contract
- Persist all state on disk in `./work/` (`manifest.json`, `gates_state.json`, `beat_map.json`, `edl.json`) so `/compact` or `/swarm` handoffs never lose user decisions.
- Deliver a standalone **16:9 horizontal cinematic MP4** (1080p or 4K, `-14 LUFS`, `LRA=9`). Do not export NLE timelines (`.otio`, `.fcpxml`).
- Always run `lint_edl.py` before rendering.
- **Mandatory Video Alignment & Orientation QA Gate**: Every final QA run MUST extract and visually inspect **100% of video clip mid-frames** (`qa_video_alignment.jpg` via `--edl`) AND **100% of chapter/route graphic cards** (`qa_chapter_cards.jpg`), plus `<=60s` output chunks, via `read_file`. Explicitly check for upside-down or 90°-rotated portrait videos (caused by top-left `preferredTransform` vs bottom-left Quartz Y-axis inversion on macOS) before writing `qa_passed.flag`.
- **3-Persona `/swarm` Film Review Panel**: Before final delivery or Google Drive upload, run a `/swarm` review panel on the Stage 4 QA sheets (`./work/qa/qa_sheet_*.jpg`, `./work/qa/qa_video_alignment.jpg`, `./work/qa/qa_chapter_cards.jpg`) using `agents/review-director.md` (story arc, pacing, hook/outro, bar sync, shot variety), `agents/review-cinematographer.md` (color cohesion, lighting, framing, diptychs, upright orientation, card legibility), and `agents/review-family-member.md` (sentimental warmth, representation of every traveler/elder/child, emotional breathing room, naturalness). Resolve all `REVISE` notes before writing `qa_passed.flag` or delivering/uploading.
