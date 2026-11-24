# Forensic QA Rubric & Pre-Render Gates

## Part A0: Stage 0 Character & Likeness Consistency Gate (Mandatory for Real People & Official Mascots)

Run **before** generating any turnaround sheet or scene keyframe whenever the Short features real-world public figures (CEOs, executives, journalists, politicians) or official product/brand mascots:
1. **Verified Real-World Reference:** Every real person and official mascot is verified via `scripts/research_characters.py` (local registry + Wikipedia REST API), targeted web search, or user-supplied official photos (`--user-ref <id>=<local_image_path>`). Zero entities remain unverified in `character_references.json`.
2. **Direct Visual Inspection:** Every reference image is inspected visually (`read_file` / `view_file`), locking exact official silhouette, materials/colors (e.g., official plush mascot geometry rather than an invented creature), hairstyle, facial structure, eyewear, and signature attire.
3. **Side-by-Side Turnaround Lock:** Each generated 3-panel turnaround sheet (`Front | 3/4 Side | Back`) is verified side-by-side against its Stage 0 reference image (especially when using the celebrity photo-to-image text-trait fallback) and marked `likeness_verified: true` before Stage 3 animation.
4. **Solo-Shot Cast Isolation:** Every single-character shot prompt includes an explicit solo-character override clause (`SOLO character: ONLY <char> appears...`) and passes only that character's turnaround sheet so `style_bible` never leaks secondary characters (`SOLO_CHAR_BLEED`).

## Part A1: Beat 1 Hook Gate (Run Before Any Asset Render)

Every Short must pass all 4 checks on Beat 1 (`0-3s`):
1. **Visual Pattern Interrupt:** Unmistakable contrast in frame 0 (e.g., historical or 2D stickman inside a vivid 3D space, or instant stylized 3D breaking-news hook).
2. **Immediate Motion (0-0.5s):** Character or camera is already moving in the first half-second (or opens with `whip_zoom`/`bounce_pop` + `--flash-in` in Mode B); zero static establishing shots or slow fade-ins.
3. **Verbal Open Loop:** Opens mid-crisis or mid-premise with zero greeting ("Hey guys", "Today we...").
4. **Safe-Zone Framing & Post-Production TOS:** Focal subject sits in the middle 50% of the 9:16 frame (top 15% and bottom 35% clear). `hook_gate.tos_2_3_words` is rendered in post via FFmpeg/ASS captions, never by the image or video model.

## Part A2: Fun / Pacing Gate (Run Before Rendering)

Avoid slow 50-second "explainer slideshow" pacing. Target:
1. **Script Density:** `95-115 words` total (`34-42s` runtime).
2. **Rapid Cuts:** `10-14 shots` with **Average Shot Length `<= 3.5s`** (sweet spot: `2.5-3.5s` per shot in Mode B; `<= 2.5s` per beat in Mode A).
3. **Comedic / News SFX Density:** Synced SFX cues (`vine_boom`, `record_scratch`, `bonk_boing`, `cash_ching`, `siren_chirp`, `ding_levelup`, `laser_zap`, `whoosh`, `pop`) across punchlines and transitions — never voice + flat music alone.
4. **Visual Gag Cadence:** A visual gag, prop reaction, extreme pose change, or tonal shift every **2-3 shots** (never a single-pose lecture).
5. **Pre-TTS Linter & v3.1 Caption Width Cap:** `tts_and_captions_v2.py --lint-plan shot_plan.json` exits 0 with zero `TTS_MISREAD` risks, and `max_caption_chars` is set to `<= 18` (`--font-size 86 --max-chars 18 --margin-v 420 --keyword-color auto`).

## Part B: Context-Aware Multi-Frame Clip / Keyframe QA

Feed the QA model (or inspect via `read_file` / `view_file`):
- The Stage 0 verified reference portrait / official mascot photo (for real people & official mascots)
- The canonical 3-panel turnaround sheet(s)
- The previous shot's end-frame (if Shot > 1)
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

### Failure Codes & Pass Threshold
- `HOOK_WEAK`: Clip 1 is static in `f_start` or setting is unclear.
- `EXTRA_LIMB`: Extra hands, fingers, limbs, or warped face/eyes on any sampled frame.
- `STYLE_DRIFT`: 2D line weight shifts, 2D bleeds into 3D (or vice versa), or outfit/colors diverge from the turnaround sheet or previous shot.
- `OFF_MODEL_CHARACTER`: Official product mascot or recurring character diverges from its Stage 0 official silhouette, materials, colors, or proportions (e.g., invented creature instead of official mascot design).
- `LIKENESS_DRIFT`: Real public figure does not match their verified Stage 0 reference portrait (wrong hairstyle, facial structure, eyewear, age, or signature attire) or drifts across cuts.
- `SOLO_CHAR_BLEED`: A secondary character, mascot, or channel co-host leaks into a single-character shot because `style_bible` or prior context lacked an explicit solo-character override.
- `MULTI_CHAR_BLEED`: Attributes swap or merge between two characters in the same shot.
- `TEXT_WARP`: Garbled AI-rendered text, logos, or watermarks appear.
- `LITERAL_BLANK_SIGN`: Model literally prints `"BLANK"`, `"EMPTY"`, or `"ADSPACE AVAILABLE"` on signs/trucks because the prompt said `"blank signs"`.
- `TTS_MISREAD`: Neural TTS mispronounces currency, numbers, or phrases (e.g., `"$700 PS5 Pro"` -> *"seven hundred dollars PS5 Pro"*, `"no 60 FPS"` -> *"number sixty FPS"*).
- `CAPTION_OVERLAP`: Two ASS `Dialogue:` events overlap in time, stacking two subtitle lines vertically.
- `CAPTION_CLIP_HORIZONTAL`: A caption line exceeds safe horizontal width (`> 18-20` chars at `86px` v3.1 scale with `126%` pop + `+/-2.5 deg` tilt), overflowing or crowding the left/right edges of the `1080px` frame.
- `AUDIO_DESYNC`: Dialogue clips off at boundary, drifts out of sync with visual cuts, or SFX fires off-beat.
- `BGM_OVERPOWERS_VO`: Background music is too loud or clashes in the `2-4 kHz` speech presence band, masking or muddying the narration.
- `BGM_TONE_MISMATCH`: Background music genre/mood clashes with script tone (e.g., quirky arcade/chiptune track under serious breaking news or high-stakes commentary).

**Pass rule:** Zero blocking failures (`HOOK_WEAK`, `EXTRA_LIMB`, `OFF_MODEL_CHARACTER`, `LIKENESS_DRIFT`, `SOLO_CHAR_BLEED`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `CAPTION_OVERLAP`, `CAPTION_CLIP_HORIZONTAL`, `BGM_OVERPOWERS_VO`, `BGM_TONE_MISMATCH`), and all scores `>= 4/5`. Cap retries at 3 per shot.

## Part C: Captions, Audio Balance & Final Multimodal Delivery Gate (Mandatory)

### Part C1: v3.1 Fun Kinetic Captions & Safe-Zone Gate
1. **Horizontal Safe Width (`<= 18` chars at `86px`):** Every rendered caption chunk stays at or below `18` characters (`--font-size 86 --max-chars 18`, warning ceiling `20`) with ~45-50px side margins at peak `126%` pop and `+/-2.5 deg` tilt — zero edge clipping (`CAPTION_CLIP_HORIZONTAL`).
2. **Lower-Third Safe Placement & High Contrast (`MarginV=420`):** Captions sit cleanly in the lower third (`--margin-v 420`), staying off character torsos/visual gags while remaining above Shorts UI chrome, with bold `Outline=8`, `Shadow=4.5`, and deeper `#30000000` drop shadow so text stays legible over bright backgrounds.
3. **Fun Kinetic Energy & Scene-Cut Flush:** Captions use elastic entry overshoot (`84% -> 122% -> 100%`), `126% -> 108%` keyword pop, alternating `+/-2.5 deg` micro-tilt on keyword punchlines, semantic neon colors (`--keyword-color auto`: Neon Lime for money/numbers, Fiery Coral-Orange for drama/danger, Electric Cyan for brands/AI, Bright Yellow for active spoken word), clean chunk flushing on every `shot_id` boundary, and zero vertical stacking (`CAPTION_OVERLAP` assertion passes).

### Part C2: Balanced Audio & Music Fit Gate
1. **Upfront Dialogue Clarity:** Original narration (`voiceover.wav` or native dialogue) sits crystal-clear and upfront (`SHORTS_VO_CLARITY=1` vocal presence EQ), normalized to `-14 LUFS` integrated loudness (`48 kHz` stereo).
2. **Subtle Speech-Carved BGM Bed:** Instrumental BGM sits cleanly underneath dialogue (`SHORTS_BGM_GAIN_DB=-16` to `-20 dB`) with a `2-4 kHz` speech-band EQ carve and sidechain ducking under VO — zero muddy frequency clash (`BGM_OVERPOWERS_VO`).
3. **Genre-Matched Music Fit:** Instrumental mood matches script tone (`dramatic_news` or `cinematic_tension` via `retrieve_music.py` / `make_bgm.py` for news and investigative commentary; upbeat/quirky moods only for comedic scripts) — zero `BGM_TONE_MISMATCH`.

### Part C3: Final Multimodal Watch-Through (Mandatory)
Before marking a Short complete:
1. Confirm `assemble_short.sh` passed its automated `CAPTION_OVERLAP` assertion and `ffprobe` shows `1080x1920`, stereo `48 kHz` audio, and `-14 LUFS` loudness.
2. **Watch `final_short.mp4` with audio** via `read_file` / `view_file` (do not rely solely on still QA frames) to verify narration pronunciation (`TTS_MISREAD`), upfront dialogue clarity vs. BGM level/fit, SFX punch, horizontal-safe kinetic captions, authentic character likenesses, and seamless loop flow from the final line back to Shot 1.
