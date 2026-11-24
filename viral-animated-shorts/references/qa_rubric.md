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

## Part B: Multi-frame clip and keyframe QA

Feed the QA model (or inspect with `read_file` or `view_file`):
- The Stage 0 verified portrait or official mascot photo (for real people and official mascots)
- The canonical 3-panel turnaround sheet(s)
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
- `SOLO_CHAR_BLEED`: A secondary character, mascot, or channel co-host leaks into a single-character shot because `style_bible` or prior context lacked a solo-character override.
- `MULTI_CHAR_BLEED`: Attributes swap or merge between two characters in the same shot.
- `TEXT_WARP`: Garbled AI-rendered text, logos, or watermarks appear.
- `LITERAL_BLANK_SIGN`: Model prints `"BLANK"`, `"EMPTY"`, or `"ADSPACE AVAILABLE"` on signs or trucks because the prompt said `"blank signs"`.
- `TTS_MISREAD`: Neural TTS mispronounces currency, numbers, acronyms, or phrases (for example, `"$700 PS5 Pro"` -> *"seven hundred dollars PS5 Pro"*, `"no 60 FPS"` -> *"number sixty FPS"*), or phonetic hacks leak into `dialogue_or_vo` captions instead of living in `tts_pronunciations` or `tts_text`.
- `AI_SLOP_SCRIPT`: Narration or YouTube metadata uses AI-slop templates (dead openers, fake contrasts, colon reveals, dramatic fragments, stock buzzwords, fortune-cookie morals, trailing `"-ing"` riders, or repetitive `"Because..."` / `"Which is why..."` loops) or drops grammar into metronomic telegraphese. Flagged as soft warnings by `tts_and_captions_v2.py --lint-plan` (blocking with `--strict-slop`) and enforced in Stage 1 / Part A2 before TTS.
- `CAPTION_OVERLAP`: Two ASS `Dialogue:` events overlap in time, stacking two subtitle lines vertically.
- `CAPTION_CLIP_HORIZONTAL`: A caption line exceeds safe horizontal width (`> 18-20` chars at `86px` v3.1 scale with `126%` pop and `+/-2.5 deg` tilt) and crowds or clips the left or right edge of the `1080px` frame.
- `AUDIO_DESYNC`: Dialogue clips off at a boundary, drifts out of sync with visual cuts, or SFX fires off-beat.
- `BGM_OVERPOWERS_VO`: Background music is too loud or clashes in the `2-4 kHz` speech band and masks the narration.
- `BGM_TONE_MISMATCH`: Background music mood clashes with script tone (for example, a quirky arcade/chiptune track under serious breaking news or investigative commentary).

**Pass rule:** Zero blocking failures (`HOOK_WEAK`, `EXTRA_LIMB`, `OFF_MODEL_CHARACTER`, `LIKENESS_DRIFT`, `SOLO_CHAR_BLEED`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `AI_SLOP_SCRIPT`, `CAPTION_OVERLAP`, `CAPTION_CLIP_HORIZONTAL`, `BGM_OVERPOWERS_VO`, `BGM_TONE_MISMATCH`), and all scores `>= 4/5`. Cap retries at 3 per shot.

## Part C: Captions, audio balance, and final delivery gate (mandatory)

### Part C1: v3.1 kinetic captions and safe-zone gate
1. **Horizontal safe width (`<= 18` chars at `86px`):** Keep every caption chunk at or below `18` characters (`--font-size 86 --max-chars 18`, warning ceiling `20`). This leaves ~45-50px side margins at peak `126%` pop and `+/-2.5 deg` tilt to prevent edge clipping (`CAPTION_CLIP_HORIZONTAL`).
2. **Lower-third placement and contrast (`MarginV=420`):** Place captions in the lower third (`--margin-v 420`) so they clear character torsos and stay above the Shorts UI. Use `Outline=8`, `Shadow=4.5`, and a `#30000000` drop shadow to keep text readable over bright frames.
3. **Kinetic animation and cut flush:** Use elastic entry (`84% -> 122% -> 100%`), keyword pop (`126% -> 108%`), and alternating `+/-2.5 deg` tilt on punchlines, with `--keyword-color auto` (Neon Lime for money and numbers, Fiery Coral-Orange for drama and danger, Electric Cyan for brands and AI, Bright Yellow for the active word). Flush caption chunks at every `shot_id` cut so lines never stack (`CAPTION_OVERLAP` assertion passes).

### Part C2: Audio balance and music fit gate
1. **Clear lead dialogue:** Keep `voiceover.wav` (or native dialogue) upfront with `SHORTS_VO_CLARITY=1` vocal presence EQ, normalized to `-14 LUFS` integrated loudness (`48 kHz` stereo).
2. **Duck and carve the BGM:** Set instrumental BGM to `SHORTS_BGM_GAIN_DB=-16` to `-20 dB`, carve the `2-4 kHz` speech band, and sidechain-duck under VO to avoid `BGM_OVERPOWERS_VO`.
3. **Match music to tone:** Pick an instrumental mood that fits the script (`dramatic_news` or `cinematic_tension` via `retrieve_music.py` or `make_bgm.py` for news and commentary; upbeat or quirky moods only for comedy) to prevent `BGM_TONE_MISMATCH`.

### Part C3: Final multimodal watch-through (mandatory)
Before marking a Short complete:
1. Confirm `assemble_short.sh` passed its automated `CAPTION_OVERLAP` assertion and `ffprobe` reports `1080x1920`, stereo `48 kHz` audio, and `-14 LUFS` loudness.
2. **Watch `final_short.mp4` with audio** via `read_file` or `view_file` (still QA frames are not enough). Check pronunciation (`TTS_MISREAD`), spoken naturalness and grammar (`AI_SLOP_SCRIPT`), clean display captions without leaked phonetics, dialogue clarity over BGM, SFX timing, horizontal caption fit, character likenesses, and whether the final line loops back naturally to Shot 1.
