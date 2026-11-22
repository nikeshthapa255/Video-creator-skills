# Forensic QA Rubric & Pre-Render Gates

## Part A1: Beat 1 Hook Gate (Run Before Any Asset Render)

Every Short must pass all 4 checks on Beat 1 (`0-3s`):
1. **Visual Pattern Interrupt:** Unmistakable contrast in frame 0 (e.g., historical or 2D stickman inside a vivid 3D space).
2. **Immediate Motion (0-0.5s):** Character or camera is already moving in the first half-second (or opens with `whip_zoom`/`bounce_pop` + `--flash-in` in Mode B); zero static establishing shots or slow fade-ins.
3. **Verbal Open Loop:** Opens mid-crisis or mid-premise with zero greeting ("Hey guys", "Today we...").
4. **Safe-Zone Framing & Post-Production TOS:** Focal subject sits in the middle 50% of the 9:16 frame (top 15% and bottom 35% clear). `hook_gate.tos_2_3_words` is rendered in post via FFmpeg/ASS captions, never by the image or video model.

## Part A2: Fun / Pacing Gate (Run Before Rendering)

Avoid slow 50-second "explainer slideshow" pacing. Target:
1. **Script Density:** `95-115 words` total (`34-42s` runtime).
2. **Rapid Cuts:** `10-14 shots` with **Average Shot Length `<= 3.5s`** (sweet spot: `2.5-3.5s` per shot in Mode B; `<= 2.5s` per beat in Mode A).
3. **Comedic SFX Density:** Synced SFX cues (`vine_boom`, `record_scratch`, `bonk_boing`, `cash_ching`, `siren_chirp`, `ding_levelup`, `laser_zap`, `whoosh`, `pop`) across punchlines and transitions — never voice + flat music alone.
4. **Visual Gag Cadence:** A visual gag, prop reaction, extreme pose change, or tonal shift every **2-3 shots** (never a single-pose lecture).
5. **Pre-TTS Linter Pass:** `tts_and_captions_v2.py --lint-plan shot_plan.json` exits 0 with zero `TTS_MISREAD` risks.

## Part B: Context-Aware Multi-Frame Clip / Keyframe QA

Feed the QA model (or inspect via `read_file`):
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
- `MULTI_CHAR_BLEED`: Attributes swap or merge between two characters in the same shot.
- `TEXT_WARP`: Garbled AI-rendered text, logos, or watermarks appear.
- `LITERAL_BLANK_SIGN`: Model literally prints `"BLANK"`, `"EMPTY"`, or `"ADSPACE AVAILABLE"` on signs/trucks because the prompt said `"blank signs"`.
- `TTS_MISREAD`: Neural TTS mispronounces currency, numbers, or phrases (e.g., `"$700 PS5 Pro"` -> *"seven hundred dollars PS5 Pro"*, `"no 60 FPS"` -> *"number sixty FPS"*).
- `CAPTION_OVERLAP`: Two ASS `Dialogue:` events overlap in time, stacking two subtitle lines vertically.
- `AUDIO_DESYNC`: Dialogue clips off at boundary, drifts out of sync with visual cuts, or SFX fires off-beat.

**Pass rule:** Zero blocking failures (`HOOK_WEAK`, `EXTRA_LIMB`, `TEXT_WARP`, `LITERAL_BLANK_SIGN`, `TTS_MISREAD`, `CAPTION_OVERLAP`), and all scores `>= 4/5`. Cap retries at 3 per shot.

## Part C: Final Multimodal Delivery Gate (Mandatory)

Before marking a Short complete:
1. Confirm `assemble_short.sh` passed its automated `CAPTION_OVERLAP` assertion and `ffprobe` shows `1080x1920`, stereo `48 kHz` audio, and `-14 LUFS` loudness.
2. **Watch `final_short.mp4` with audio** via `read_file` (do not rely solely on still QA frames) to verify narration pronunciation (`TTS_MISREAD`), SFX punch, BGM ducking, caption readability, and loop flow from final line back to Shot 1.
