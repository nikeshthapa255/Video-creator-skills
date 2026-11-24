# Forensic QA rubric and pre-render gates

## Part A0: Stage 0 character and likeness gate (real people and official mascots)

Run **before** generating any turnaround sheet or scene keyframe whenever the Short features public figures (CEOs, executives, journalists, politicians) or official brand mascots:
1. **Verify real references:** Check every real person and official mascot with `scripts/research_characters.py` (local registry + Wikipedia REST API), web search, or user-supplied photos (`--user-ref <id>=<local_image_path>`). Leave no unverified entries in `character_references.json`.
2. **Inspect every reference image:** Open each image with `read_file` or `view_file`. Record the exact silhouette, materials and colors (matching the official mascot instead of inventing a creature), hairstyle, face shape, eyewear, and signature outfit.
3. **Compare sheets side by side:** Check each 3-panel turnaround sheet (`Front | 3/4 Side | Back`) against its Stage 0 reference image, especially after a celebrity-filter text fallback. Mark `likeness_verified: true` before Stage 3.
4. **Isolate solo shots:** Add an explicit solo-character override (`SOLO character: ONLY <char> appears...`) to every single-character shot prompt, and pass only that character's turnaround sheet so `style_bible` never leaks a second character (`SOLO_CHAR_BLEED`).

## Part A1: Beat 1 hook gate (run before any asset render)

Every Short must pass all 4 checks on Beat 1 (`0-3s`):
1. **Visual pattern interrupt:** Show clear visual contrast in frame 0 (for example, a 2D stickman inside a vivid 3D space, or an immediate stylized 3D breaking-news scene).
2. **Immediate motion (0-0.5s):** Start with the character or camera already moving in the first half-second (or open with `whip_zoom`/`bounce_pop` plus `--flash-in` in Mode B). Never use static establishing shots or slow fade-ins.
3. **Verbal open loop:** Open mid-crisis or with a standalone concrete hook that hits cold at `0:00`. Cut greetings (`"Hey guys"`, `"Today we..."`), AI-slop openers (`"In today's world"`, `"Have you ever wondered"`, `"Here's the catch:"`, `"Buckle up"`), and repetitive `"Because..."` cold opens.
4. **Safe-zone framing and post-production TOS:** Keep the focal subject in the middle 50% of the 9:16 frame (top 15% and bottom 35% clear). Render `hook_gate.tos_2_3_words` in post with FFmpeg/ASS captions, never inside the image or video model.

## Part A2: Fun and pacing gate (run before rendering)

Avoid slow 50-second "explainer slideshow" pacing. Target:
1. **Script density:** `95-115 words` total (`34-42s` runtime).
2. **Rapid cuts:** `10-14 shots` with **average shot length `<= 3.5s`** (`2.5-3.5s` per shot in Mode B; `<= 2.5s` per beat in Mode A).
3. **SFX on beats:** Place synced SFX cues (`vine_boom`, `record_scratch`, `bonk_boing`, `cash_ching`, `siren_chirp`, `ding_levelup`, `laser_zap`, `whoosh`, `pop`) on punchlines and transitions instead of relying on voice and flat music alone.
4. **Visual gag cadence:** Add a visual gag, prop reaction, extreme pose change, or tonal shift every **2-3 shots** (never a single-pose lecture).
5. **Pre-TTS linter and v3.1 caption width cap:** Run `tts_and_captions_v2.py --lint-plan shot_plan.json` so it exits 0 with zero `TTS_MISREAD` errors (use `--strict-slop` to fail on `AI_SLOP_SCRIPT` warnings). Keep clean display text in `dialogue_or_vo` (move phonetics to `tts_pronunciations` or `tts_text`), and cap `max_caption_chars` at `<= 18` (`--font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto`).
6. **Natural spoken English (zero `AI_SLOP_SCRIPT`):** Follow [spoken_english_guide.md](./spoken_english_guide.md) and `clear-natural-english`. Keep full grammar (never drop articles, verbs, or objects to fit a word-count box), vary shot cadence (mix 4-7 word punchlines with 12-16 word setups), and ground unfamiliar names with a role. Cut dead openers, fake contrasts (`"not just X, it's Y"`, `"Forget X — Y"`), colon reveals (`"Plot twist:"`, `"Here's the catch:"`), dramatic fragments (`"Total privacy disaster."`, `"Let that sink in."`), trailing `"-ing"` riders, stock buzzwords, fortune-cookie morals, and repetitive `"Because..."` / `"Which is why..."` loops. Apply the same checks to YouTube titles, descriptions, and pinned comments.

### Part A2-LF: Long-form pacing and cast gate (when `video_format == "long_form"`)

Instead of the 34-42s Short cap, verify these long-form targets (see [long_form_guide.md](./long_form_guide.md)):
1. **Multi-act structure:** 3-6 Acts/chapters (`acts[]` populated with titles) using 3-digit shot IDs (`shot_001..shot_NNN`). For vertical micro-dramas (`9:16`, 3-8 min): ~750-1,100 words, 80-120 shots, `<= 3.6s` average shot length. For horizontal YouTube episodes (`16:9`, 3-15+ min): ~500-2,200 words, 45-180 shots, `3.5-6.5s` average shot length with visual beats or re-hooks every 45-90s.
2. **Multi-voice cast & runtime calibration:** Every `speaker` resolves cleanly in top-level `voice_cast` to a `WordBoundary`-verified `edge-tts` voice (no duplicate legacy `speaker_voices` block). Set rates (`+13%` to `+18%` for brisk 5-min drama; `+2%` to `+8%` for calmer 16:9 narration), inter-shot `--gap` (`0.08-0.12s`), and `post_gap_sec: 0.25-0.35` on Act-closing shots.
3. **State-sheet & cast isolation audit:** Every major wardrobe/prop state change (such as veil ON -> veil OFF) has a dedicated `state_sheets` turnaround image, matching `character_states`, and flipped `negative_constraints` at the exact transition cut. Keep >= 85% of shots to zero or one lead character, and max 2 characters per confrontation frame.
4. **Ending style:** Confirm `dual_anchor_loop: false` and `ending_style: "cliffhanger"` or `"outro"` (no forced Shorts replay loop).
5. **Pre-TTS linter & natural speech:** Run `tts_and_captions_v2.py --lint-plan shot_plan.json` for zero `TTS_MISREAD` errors and zero `AI_SLOP_SCRIPT` warnings across all Acts.

## Part B: Multi-frame clip and keyframe QA

Feed the QA model (or inspect with `read_file` or `view_file`):
- The Stage 0 verified portrait or official mascot photo (for real people and official mascots)
- The canonical 3-panel turnaround sheet(s) (and active `state_sheets` variant for long-form state changes)
- The previous shot's end frame (for Shot > 1)
- Sampled frames from the current clip (`f_start`, `f_mid`, `f_peak`, `f_end`) or the Mode B keyframe PNG

Return a JSON verdict per shot:

```json
{
  "shot_id": "shot_04",
  "pass": false,
  "scores": {
    "hook_motion": 5,
    "anatomy_integrity": 5,
    "likeness_and_cast_isolation": 5,
    "style_and_wardrobe_match": 4,
    "cross_cut_continuity": 5,
    "text_and_artifact_free": 1
  },
  "failures": [
    {
      "code": "LITERAL_BLANK_SIGN",
      "frame": "f_mid",
      "detail": "Storefront awning literally reads 'BLANK'."
    }
  ],
  "recommended_action": "prompt_rewrite"
}
```

### Failure codes and pass threshold
- `HOOK_WEAK`: Clip 1 is static in `f_start` or the setting is unclear.
- `EXTRA_LIMB`: Extra hands, fingers, limbs, or warped face/eyes appear on any sampled frame.
- `STYLE_DRIFT`: 2D line weight shifts, 2D bleeds into 3D (or vice versa), or outfit/colors diverge from the turnaround sheet or previous shot.
- `OFF_MODEL_CHARACTER`: Official product mascot or recurring character diverges from its Stage 0 silhouette, materials, colors, or proportions (for example, an invented creature instead of the official mascot).
- `LIKENESS_DRIFT`: Real public figure does not match their verified Stage 0 reference portrait (wrong hairstyle, face shape, eyewear, age, or signature outfit) or drifts across cuts.
- `STATE_CONTINUITY_DRIFT`: Long-form costume, veil, injury, or prop state reverts or leaks across an Act boundary (for example, a veil disappears before the unveil shot or reappears after it, or progressive damage vanishes).
- `SOLO_CHAR_BLEED`: A secondary character, mascot, or channel co-host leaks into a single-character shot because `style_bible` or prior context lacked a solo-character override.
- `MULTI_CHAR_BLEED`: Attributes swap or merge between two characters in the same shot.
- `TEXT_WARP`: Garbled AI-rendered text, local scripts/calligraphy (such as gibberish Hanzi on banners/scrolls), logos, or watermarks appear.
- `LITERAL_BLANK_SIGN`: Model prints `"BLANK"`, `"EMPTY"`, or `"ADSPACE AVAILABLE"` on signs or trucks because the prompt said `"blank signs"`.
- `TTS_MISREAD`: Neural TTS mispronounces currency, numbers, acronyms, proper names, or phrases, or phonetic hacks leak into `dialogue_or_vo` captions instead of living in `tts_pronunciations` or `tts_text`.
- `VOICE_CAST_DRIFT`: Wrong speaker voice fires on a dialogue line, a voice lacks `WordBoundary` support (causing missing captions), or uncalibrated neutral rates bloat a 5-minute episode by 15-20%.
- `AI_SLOP_SCRIPT`: Narration or YouTube metadata uses AI-slop templates (dead openers, fake contrasts, colon reveals, dramatic fragments, stock buzzwords, fortune-cookie morals, trailing `"-ing"` riders, or repetitive `"Because..."` / `"Which is why..."` loops) or drops grammar into metronomic telegraphese. Flagged as soft warnings by `tts_and_captions_v2.py --lint-plan` (blocking with `--strict-slop`) and enforced in Stage 1 / Part A2 before TTS.
- `CAPTION_OVERLAP`: Two ASS `Dialogue:` events overlap in time, stacking two subtitle lines vertically.
- `CAPTION_CLIP_HORIZONTAL`: A caption line exceeds safe horizontal width (`> 18-20` chars on `9:16` at `86px`, or `> 36-40` chars on `16:9` at `62px`) and crowds or clips the frame edge.
- `AUDIO_DESYNC`: Dialogue clips off at a boundary, drifts out of sync with visual cuts, or SFX fires off-beat.
- `FRAME_TIMELINE_DRIFT`: Parallel Act animation workers compute frame counts locally instead of globally, accumulating visible A/V drift by later Acts.
- `BGM_OVERPOWERS_VO`: Background music is too loud or clashes in the `2-4 kHz` speech band and masks the narration.
- `BGM_TONE_MISMATCH`: Background music mood clashes with script tone (for example, a quirky arcade/chiptune track under serious breaking news or investigative commentary).
- `ACT_BGM_FATIGUE`: A single short BGM loop repeats unchanged across a multi-minute episode, causing listener fatigue and missing Act emotional shifts.
- `QA_FILE_TOO_LARGE`: A long-form final MP4 exceeds the `100 MB` `view_file` limit and cannot be inspected end-to-end without slicing via `split_qa_segments.sh`.

**Pass rule:** Zero blocking failures (`HOOK_WEAK`, `EXTRA_LIMB`, `OFF_MODEL_CHARACTER`, `LIKENESS_DRIFT`, `STATE_CONTINUITY_DRIFT`, `SOLO_CHAR_BLEED`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `VOICE_CAST_DRIFT`, `AI_SLOP_SCRIPT`, `CAPTION_OVERLAP`, `CAPTION_CLIP_HORIZONTAL`, `FRAME_TIMELINE_DRIFT`, `BGM_OVERPOWERS_VO`, `BGM_TONE_MISMATCH`, `ACT_BGM_FATIGUE`, `QA_FILE_TOO_LARGE`), and all scores `>= 4/5`. Cap retries at 3 per shot.

## Part C: Captions, audio balance, and final delivery gate (mandatory)

### Part C1: Kinetic and widescreen captions safe-zone gate
1. **Horizontal safe width:** On `9:16` (`1080x1920`), cap every chunk at `<= 18` chars (`--font-size 86 --max-chars 18`, warning ceiling `20`) so `126%` pop and `+/-2.5 deg` tilt leave ~45-50px side margins (`CAPTION_CLIP_HORIZONTAL`). On `16:9` (`1920x1080`), cap at `<= 36` chars (`--font-size 62 --max-chars 36 --margin-v 72 --tilt 0`, warning ceiling `40`) or export a clean `.srt` sidecar (`--export-srt`).
2. **Placement and contrast:** On `9:16`, use `MarginV=420` (lower third, above Shorts UI and off character torsos). On `16:9`, use `MarginV=72`. Keep `Outline=8`, `Shadow=4.5`, and `#30000000` drop shadow for readability over bright frames.
3. **Kinetic animation and cut flush:** Use elastic entry (`84% -> 122% -> 100%`), keyword pop (`126% -> 108%`), and alternating `+/-2.5 deg` tilt on `9:16` punchlines (disable tilt on `16:9` with `--tilt 0`), with `--keyword-color auto`, `palace`, or per-speaker `voice_cast.<speaker>.keyword_color`. Flush caption chunks at every `shot_id` cut so lines never stack (`CAPTION_OVERLAP` assertion passes).

### Part C2: Audio balance and music fit gate
1. **Clear lead dialogue:** Keep `voiceover.wav` (or native dialogue) upfront with `SHORTS_VO_CLARITY=1` vocal presence EQ, normalized to `-14 LUFS` integrated loudness (`48 kHz` stereo), verified by `assemble_short.sh`'s post-norm loudness check.
2. **Duck and carve the BGM:** Set instrumental BGM to `SHORTS_BGM_GAIN_DB=-16` to `-20 dB`, carve `-3.5 dB` at `2.8 kHz` (`SHORTS_BGM_LP_HZ=8500` to preserve acoustic instrument shimmer), and sidechain-duck under VO to avoid `BGM_OVERPOWERS_VO`.
3. **Match music to tone and Acts:** For Shorts, pick an instrumental mood that fits the script (`dramatic_news` or `cinematic_tension` for news/commentary; upbeat moods for comedy). For long-form episodes, pass `--timings audio/shot_timings.json` to `make_bgm.py` (`palace_drama` or `epic_story`) so orchestration evolves across every Act and resolves in the epilogue (`ACT_BGM_FATIGUE`).

### Part C3: Final multimodal watch-through (mandatory)
Before marking a Short or long-form episode complete:
1. Confirm `assemble_short.sh` passed its automated `CAPTION_OVERLAP` assertion, verified post-norm `-14 LUFS` loudness, and `ffprobe` reports the target resolution (`1080x1920` or `1920x1080`) and stereo `48 kHz` audio.
2. **For Shorts (< 90s and < 80 MB):** Watch `final_short.mp4` directly **with audio** via `read_file` or `view_file`.
3. **For Long-Form episodes (> 90s or > 80 MB):** Run `bash scripts/split_qa_segments.sh <final_episode.mp4> <project_dir>/qa_segments --timings <project_dir>/audio/shot_timings.json`. Inspect **every** 60-second chunk (`qa_min1.mp4` .. `qa_minN.mp4`) **with audio** via `view_file` (or in parallel across QA subagents), audit `screenshots/frame_*.png`, verify exact LUFS, and confirm zero `STATE_CONTINUITY_DRIFT`, `VOICE_CAST_DRIFT`, `FRAME_TIMELINE_DRIFT`, `TTS_MISREAD`, or `AI_SLOP_SCRIPT`.
4. **For serialized episodes:** Confirm `README_series_bible.md` records the exact end-of-episode plot, wardrobe/prop states, locked `style_bible`, `voice_cast`, and `tts_pronunciations` for Episode 2+.

