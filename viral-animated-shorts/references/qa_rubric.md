# Forensic QA rubric and pre-render gates

## Part A0: Stage 0 character and likeness gate (real people and official mascots)

Run **before** generating any turnaround sheet or scene keyframe whenever the video features public figures (CEOs, executives, journalists, politicians) or official brand mascots:
1. **Verify real references:** Check every real person and official mascot with `scripts/research_characters.py` (local registry + Wikipedia REST API), web search, or user-supplied photos (`--user-ref <id>=<local_image_path>`). Leave no entries in `unverified_entities` or `uninspected_entities`.
2. **Inspect every downloaded portrait:** Open each image in `uninspected_entities` with `read_file` or `view_file`. Record exact silhouette, materials and colors (matching the official mascot instead of inventing a creature), hairstyle, face shape, eyewear (including explicit `"NO glasses"` when absent), and signature outfit, then lock them via `python3 scripts/research_characters.py --verify <id> --traits "..."`.
3. **Compare sheets side by side:** Check each 3-panel turnaround sheet (`Front | 3/4 Side | Back`) against its Stage 0 reference image, especially after a celebrity-filter text fallback. Confirm `likeness_verified: true` before Stage 3.
4. **Isolate solo shots:** Add an explicit solo-character override (`SOLO character: ONLY <char> appears...`) to every single-character shot prompt, and pass only that character's turnaround sheet so `style_bible` never leaks a second character (`SOLO_CHAR_BLEED`).

## Part A1: Beat 1 hook gate (run before any asset render)

Every Short must pass all 4 checks on Beat 1 (`0-3s`):
1. **Visual pattern interrupt:** Show clear visual contrast in frame 0 (for example, a 2D stickman inside a vivid 3D space, or an immediate stylized 3D breaking-news scene).
2. **Immediate motion (0-0.5s):** Start with the character or camera already moving in the first half-second (or open with `whip_zoom`/`bounce_pop` plus `--flash-in` in Mode B). Never use static establishing shots or slow fade-ins.
3. **Verbal open loop:** Open mid-crisis or with a standalone concrete hook that hits cold at `0:00`. Cut greetings (`"Hey guys"`, `"Today we..."`), AI-slop openers (`"In today's world"`, `"Have you ever wondered"`, `"Here's the catch:"`, `"Buckle up"`), and repetitive `"Because..."` cold opens.
4. **Safe-zone framing and post-production TOS:** Keep the focal subject in the middle 50% of the 9:16 frame (top 15% and bottom 35% clear). Render `hook_gate.tos_2_3_words` in post with FFmpeg/ASS captions, never inside the image or video model.

## Part A2: Fun and pacing gate (run before rendering)

Avoid slow 50-second "explainer slideshow" pacing. Target:
1. **Script density:** `95-115 words` total (`34-42s` runtime; `--lint-plan` warns above `125` spoken words or `11` words/shot, and synthesis fails above `59.0s` with `SHORT_OVER_60S`).
2. **Rapid cuts:** `10-14 shots` with **average shot length `<= 3.5s`** (`2.5-3.5s` per shot in Mode B; `<= 2.5s` per beat in Mode A).
3. **SFX on beats:** Place synced SFX cues (`vine_boom`, `record_scratch`, `bonk_boing`, `cash_ching`, `siren_chirp`, `ding_levelup`, `laser_zap`, `whoosh`, `pop`) on punchlines and transitions instead of relying on voice and flat music alone.
4. **Visual gag cadence:** Add a visual gag, prop reaction, extreme pose change, or tonal shift every **2-3 shots** (never a single-pose lecture).
5. **Pre-TTS linter and v3.1 caption width cap:** Run `tts_and_captions_v2.py --lint-plan shot_plan.json --strict-slop` so it exits 0 with zero `TTS_MISREAD`, `LITERAL_BLANK_SIGN`, or `AI_SLOP_SCRIPT` issues. Keep clean display text in `dialogue_or_vo` (move phonetics to `tts_pronunciations` or `tts_text`), and cap `max_caption_chars` at `<= 18` (`--font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto`).
6. **Natural spoken English (zero `AI_SLOP_SCRIPT`):** Follow [spoken_english_guide.md](./spoken_english_guide.md) and `clear-natural-english`. Keep full grammar (never drop articles, verbs, or objects to fit a word-count box), vary shot cadence (mix 4-7 word punchlines with 12-16 word setups), and ground unfamiliar names with a role. Cut dead openers, fake contrasts (`"not just X, it's Y"`, `"Forget X - Y"`), colon reveals (`"Plot twist:"`, `"Here's the catch:"`, `"Lesson learned:"`, `"Bottom line:"`), dramatic fragments (`"Total privacy disaster."`, `"Let that sink in."`), trailing `"-ing"` riders, stock buzzwords, fortune-cookie morals, and repetitive `"Because..."` / `"Which is why..."` loops. Lint YouTube metadata with `--lint-metadata packaging.md --strict-slop`.

### Part A1-LF: Long-form hook and packaging gate (when `video_format == "long_form"`)

1. **0-5s click confirmation:** Visually and verbally pay off the Title and Thumbnail/Cover promise immediately (zero channel intros, zero logos, zero throat-clearing).
2. **0-20s velocity and Tier-A hook:** On `16:9` episodes, front-load the opening 20s with brisk `1.5-2.5s` cuts (or Tier-A 3D hero art / Mode A animation in `hybrid` mode) before settling into `3.5-6.5s` narrative beats.
3. **Retention re-hooks:** Populate `acts[].rehook_question` on every Act (every `25-45s` in `9:16` micro-dramas, every `45-90s` in `16:9` episodes) so Act boundaries open curiosity instead of triggering drop-off.
4. **Stage 1.5 optional LGTM gate (`16:9` commentary):** Before generating Stage 2/3 image assets, run a domain/technical fact check and a Visual Flow & Retention Director pass over the merged `shot_plan.json`.
5. **Packaging and End-Screen readiness:** Confirm non-spoiler chapter titles (`<= 35` chars, `>= 3` chapters `>= 10s`), front-loaded YouTube titles (zero `<`/`>`, zero em dashes), `< 2 MB` thumbnails (`scripts/make_thumbnail.py` with `--mobile-preview`), and an `8-12s` End-Screen safe slate (`motion_preset: "static_hold"`) when `ending_style == "outro"`.

### Part A2-LF: Long-form pacing, cast, visual tiering, and continuity gate (when `video_format == "long_form"`)

Instead of the 34-42s Short cap, verify these long-form targets (see [long_form_guide.md](./long_form_guide.md)):
1. **Multi-act structure and pacing budget:** 3-6 Acts/chapters (`acts[]` with `title`, `bgm_intensity`, `lighting_mood`, `rehook_question`) using 3-digit shot IDs (`shot_001..shot_NNN`).
   - `9:16` vertical micro-drama (3-8 min): `~750-1,100` words, `80-120` shots, `<= 3.6s` avg, `+13%..+18%` rates, `post_gap_sec: 0.25` at Act breaks.
   - `16:9` commentary/news/explainer (3-15+ min): **`100-115 WPM`** (`~300` words/3m, `~525` words/5m), **`+6%..+10%` TTS rates** (`--lint-plan` warns above `+12%` or `130 WPM`), `0.5s-0.8s` `post_gap_sec` after headline stats and Act turns, and deep caveats moved to `packaging.md`.
2. **Multi-voice cast and dynamic:** Every `speaker` resolves in `voice_cast` to a `WordBoundary`-verified `edge-tts` voice (or BYO WAV). Keep narrator share `<= 35%` and add 10-15% silent reaction beats (`dialogue_or_vo: ""`) in scripted drama. For `16:9` dual-host commentary, maintain distinct Anchor vs. Skeptical Analyst roles (2-3 organic skepticism beats; never alternating clones).
3. **Three-tier visual hierarchy (`16:9` commentary):** Cap Tier-A 3D hero art to `0-10s` hook and Act openers (5-6 shots max), keep Tier-B dialogue in cohesive 2D host/studio framing, and keep Tier-C dark-mode data slides to `10-15 words` max inside a `scale(0.89)` safe box (bottom 16% clear for `MarginV=116`, animated with zero-crop `static_hold`).
4. **State-sheet, location plate, and eye-line audit:** Lock `characters[].state_sheets` + flipped `negative_constraints` at every costume/prop transition, `locations[]` master plates (Template 1E) for recurring sets, `>= 85%` 0-1 lead shots, varied `shot_scale`, and opposing 180-degree `eyeline` directions across shot-reverse-shot cuts.
5. **Editorial audio fit and ending style:** Confirm `dual_anchor_loop: false` and `ending_style: "cliffhanger"` or `"outro"`. In `16:9` commentary, use Tech/UI SFX cues (`ui_tick`, `ui_click`, `data_chime`, `sub_whoosh`, `alert_ping`, `soft_thud`) and `ambience_bed: "newsroom_air"` with zero cartoon cues (`SFX_TONE_MISMATCH`).
6. **Pre-TTS linter and natural speech:** Run `tts_and_captions_v2.py --lint-plan shot_plan.json --strict-slop` (with `"script_genre": "drama"` for fiction, `"commentary"` for non-fiction) for zero blocking errors and zero `AI_SLOP_SCRIPT` warnings across all Acts and `packaging` fields.

## Part B: Multi-frame clip and keyframe QA

Feed the QA model (or inspect with `read_file` or `view_file`):
- The Stage 0 verified portrait or official mascot photo (for real people and official mascots)
- The canonical 3-panel turnaround sheet(s), active `state_sheets` variant, and recurring `locations[]` anchor plate
- The previous shot's end frame (for Shot > 1)
- Sampled frames from the current clip (`f_frame0`, `f_start`, `f_mid`, `f_peak`, `f_end`) or the Mode B keyframe PNG

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
- `HOOK_WEAK`: Clip 1 is static in `f_frame0`/`f_start` or the setting is unclear.
- `EXTRA_LIMB`: Extra hands, fingers, limbs, or warped face/eyes appear on any sampled frame.
- `STYLE_DRIFT`: 2D line weight shifts, 2D bleeds into 3D (or vice versa), 16:9 commentary ping-pongs randomly between 3D and 2D, or outfit/colors diverge from the turnaround sheet.
- `OFF_MODEL_CHARACTER`: Official product mascot or recurring character diverges from its Stage 0 silhouette, materials, colors, or proportions.
- `LIKENESS_DRIFT`: Real public figure does not match their verified Stage 0 reference portrait or drifts across cuts.
- `STATE_CONTINUITY_DRIFT`: Long-form costume, veil, injury, or prop state reverts or leaks across an Act boundary.
- `LOCATION_CONTINUITY_DRIFT`: Recurring room architecture, props, or key-light direction morphs across cuts because no master location plate (Template 1E) was anchored.
- `EYELINE_CROSS`: Shot-reverse-shot dialogue flips screen direction or breaks the 180-degree axis across adjacent cuts.
- `SOLO_CHAR_BLEED`: A secondary character, mascot, or co-host leaks into a single-character shot.
- `MULTI_CHAR_BLEED`: Attributes swap or merge between two characters in the same shot.
- `TEXT_WARP`: Garbled AI-rendered text, local scripts/calligraphy (such as gibberish Hanzi on banners/scrolls), logos, or watermarks appear.
- `LITERAL_BLANK_SIGN`: Model prints `"BLANK"`, `"EMPTY"`, or `"ADSPACE AVAILABLE"` on signs or trucks because the prompt said `"blank signs"`.
- `TTS_MISREAD`: Neural TTS mispronounces currency (`$1.5T`, `$700 PS5`), numbers, plural/possessive acronyms (`NPCs`, `MCP's`), resolutions/grades (`1080p SDR`, `AA-`), ALL-CAPS words (`US`, `IT`, `IN`, `OR`, `AM`), or proper names, or spaced phonetic hacks (`N P C`) leak into `dialogue_or_vo`.
- `VOICE_CAST_DRIFT`: Wrong speaker voice fires on a dialogue line, a voice lacks `WordBoundary` support, or uncalibrated rates cause runtime bloat or firehose pacing.
- `AI_SLOP_SCRIPT`: Narration, drama dialogue, or YouTube metadata uses AI-slop templates, em-dash spam, illegal `<`/`>` title chars, or clipped telegraphese.
- `CAPTION_OVERLAP`: Two ASS `Dialogue:` events overlap in time, stacking two subtitle lines vertically.
- `CAPTION_CLIP_HORIZONTAL`: A caption line exceeds safe horizontal width (`> 18-20` chars on `9:16` at `86px`, or `> 36-40` chars on `16:9` at `62px`) and crowds or clips the frame edge.
- `AUDIO_DESYNC`: Dialogue clips off at a boundary, drifts out of sync with visual cuts, or SFX fires off-beat.
- `FRAME_TIMELINE_DRIFT`: Parallel Act animation workers compute frame counts locally instead of globally, accumulating visible A/V drift by later Acts.
- `BGM_OVERPOWERS_VO`: Background music is too loud or clashes in the `2-4 kHz` speech band and masks the narration.
- `BGM_TONE_MISMATCH`: Background music mood clashes with script tone.
- `SFX_TONE_MISMATCH`: Cartoon SFX cues (`bonk_boing`, `pop`, `record_scratch`, `siren_chirp`) appear in a `16:9` commentary/news episode instead of soft-calibrated Tech/UI cues.
- `ACT_BGM_FATIGUE`: A single short BGM loop repeats unchanged across a multi-minute episode, causing listener fatigue and missing Act emotional shifts.
- `IMAGE_QUOTA_EXHAUSTED`: Image generation quota runs out mid-batch; requires saving completed keyframes and logging `quota_paused (N/M keyframes)` for Wave 2 resume.
- `QA_FILE_TOO_LARGE`: A long-form final MP4 exceeds the `100 MB` `view_file` limit and cannot be inspected end-to-end without slicing via `split_qa_segments.sh`.

**Pass rule:** Zero blocking failures (`HOOK_WEAK`, `EXTRA_LIMB`, `OFF_MODEL_CHARACTER`, `LIKENESS_DRIFT`, `STATE_CONTINUITY_DRIFT`, `LOCATION_CONTINUITY_DRIFT`, `EYELINE_CROSS`, `SOLO_CHAR_BLEED`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `VOICE_CAST_DRIFT`, `AI_SLOP_SCRIPT`, `CAPTION_OVERLAP`, `CAPTION_CLIP_HORIZONTAL`, `FRAME_TIMELINE_DRIFT`, `BGM_OVERPOWERS_VO`, `BGM_TONE_MISMATCH`, `SFX_TONE_MISMATCH`, `ACT_BGM_FATIGUE`, `QA_FILE_TOO_LARGE`), and all scores `>= 4/5`. Cap retries at 3 per shot.

## Part C: Captions, audio balance, and final delivery gate (mandatory)

### Part C1: Kinetic and widescreen captions safe-zone gate
1. **Horizontal safe width:** On `9:16` (`1080x1920`), cap every chunk at `<= 18` chars (`--font-size 86 --max-chars 18`, warning ceiling `20`). On `16:9` (`1920x1080`), cap at `<= 36` chars (`--font-size 62 --max-chars 36 --margin-v 116 --tilt 0`, warning ceiling `40`), or use `caption_style: "none"` + `captions.srt` CC (recommended for standard YouTube uploads; never pair burned-in English subtitles with default-on English CC).
2. **Placement and contrast:** On `9:16`, use `MarginV=420` (up to `480` for dense Shorts/TikTok bottom UI) with `Outline=8`, `Shadow=4.5`, and `#30000000` drop shadow. On `16:9`, use `MarginV=116` (clears YouTube scrub bar inside the bottom 16% safe zone) with `Outline=6`, `Shadow=3.5`.
3. **Animation and fatigue control:** Use `kinetic_keyword` (`84% -> 122% -> 100%` pop, `+/-2.5 deg` tilt) for punchy `9:16` Shorts; use `clean_subtitle` (zero bounce, zero tilt) on calm 5-15 min dramas or `16:9` burned-in captions. Flush chunks at every `shot_id` cut so `CAPTION_OVERLAP` passes.

### Part C2: Audio balance and music fit gate
1. **Clear lead dialogue:** Keep `voiceover.wav` (or native dialogue) upfront with `SHORTS_VO_CLARITY=1` (gentle `+1.5 dB @ 2.8 kHz` presence + `-1.5 dB @ 6.8 kHz` de-harsh + smooth leveling), mixed in lossless 24-bit PCM with peak limiting and normalized to `-14 LUFS` (`48 kHz` stereo, `LRA=7` on `9:16`, `LRA=9` on `16:9`), verified by `assemble_short.sh`'s post-norm loudness check.
2. **Single-carve BGM and musical ducking:** Keep `make_bgm.py` full-bandwidth (`--speech-eq` off by default) and let `assemble_short.sh` perform the single speech-pocket carve (`SHORTS_BGM_GAIN_DB=-15`, `SHORTS_BGM_LP_HZ=10500`, `-2.5 dB` high shelf @ `7.5 kHz`, `-3.0 dB @ 2.9 kHz`, smooth musical sidechain ducking `ratio=3.2`, `release=600ms`) so dialogue stays clear without hollow double-carving or frantic pumping.
3. **Match music and SFX to tone, Acts, and silence:** For Shorts, match mood to tone (`dramatic_news` / `cinematic_tension` vs. comedy). For long-form episodes, pass `--timings audio/shot_timings.json` to `make_bgm.py` (`palace_drama` or `epic_story`), wire `acts[].bgm_intensity`, use `ending_style: "cliffhanger"` for unresolved tension endings, use `bgm_cue: "drop_out" | "drop_before_end"` for spotted silence, set `ambience_bed` in `shot_plan.json` (auto-loaded by `sfx_engine.py`), and use Tech/UI SFX cues on `16:9` commentary.

### Part C3: Final multimodal watch-through (mandatory)
Before marking a Short or long-form episode complete:
1. Confirm `assemble_short.sh` passed its automated `CAPTION_OVERLAP` assertion, verified post-norm `-14 LUFS` loudness, and `ffprobe` reports the target resolution (`1080x1920` or `1920x1080`) and stereo `48 kHz` audio.
2. **For Shorts (< 90s and < 80 MB):** Watch `final_short.mp4` directly **with audio** via `read_file` or `view_file`.
3. **For Long-Form episodes (> 90s or > 80 MB):** Run `bash scripts/split_qa_segments.sh <final_episode.mp4> <project_dir>/qa_segments --timings <project_dir>/audio/shot_timings.json`. Inspect **every** 60-second chunk (`qa_min1.mp4` .. `qa_minN.mp4`) **with audio** via `view_file` (or in parallel across QA subagents), audit `screenshots/frame_*.png`, verify full + per-segment LUFS and `chapters.txt`, and confirm zero `STATE_CONTINUITY_DRIFT`, `LOCATION_CONTINUITY_DRIFT`, `EYELINE_CROSS`, `VOICE_CAST_DRIFT`, `FRAME_TIMELINE_DRIFT`, `SFX_TONE_MISMATCH`, `TTS_MISREAD`, or `AI_SLOP_SCRIPT`.
4. **For serialized or recurring episodes:** Confirm `README_series_bible.md` (serialized fiction) or `episode_context.md` (recurring non-fiction/commentary) records continuity state, locked `style_bible`, verified sheets/traits, `voice_cast`, and `tts_pronunciations`.
5. **Minimal `<slug>/` delivery check:** Verify only `<slug>-short.mp4` (or `<slug>-episode.mp4`), `thumbnail.png` (`< 2 MB`), `packaging.md` (passing `--lint-metadata --strict-slop`), and optional `episode_context.md` are placed in the delivery folder (zero raw intermediates).

