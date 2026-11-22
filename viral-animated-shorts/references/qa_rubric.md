# Forensic QA Rubric & Hook Gate

## Part A: Beat 1 Hook Gate (Run Before Any Video Render)

Every Short must pass all 4 checks on Beat 1 (`0-3s`):
1. **Visual Pattern Interrupt:** Unmistakable contrast in frame 0 (e.g., historical 2D stickman inside a hyper-modern 3D space).
2. **Immediate Motion (0-0.5s):** Character or camera is already moving in the first half-second; zero static establishing shots or slow fade-ins.
3. **Verbal Open Loop:** Opens mid-crisis or mid-premise with zero greeting ("Hey guys", "Today we...").
4. **Safe-Zone Framing & Post-Production TOS:** Focal subject sits in the middle 50% of the 9:16 frame (top 15% and bottom 35% clear). `hook_gate.tos_2_3_words` is rendered in post via FFmpeg/ASS captions, never by the video model.

## Part B: Context-Aware Multi-Frame Clip QA

Feed the QA model (or inspect via `read_file`):
- The canonical 3-panel turnaround sheet(s)
- The previous shot's end-frame (if Shot > 1)
- Sampled frames from the current clip (`f_start`, `f_mid`, `f_peak`, `f_end`)

Return a JSON verdict per shot:

```json
{
  "shot_id": "shot_01",
  "pass": false,
  "scores": {
    "hook_motion": 5,
    "anatomy_integrity": 2,
    "style_and_wardrobe_match": 4,
    "cross_cut_continuity": 5,
    "text_and_artifact_free": 5
  },
  "failures": [
    {
      "code": "EXTRA_LIMB",
      "frame": "f_mid",
      "detail": "Third hand appears near the recruiter's shoulder."
    }
  ],
  "recommended_action": "in_place_edit | prompt_rewrite | reroll_simpler_motion"
}
```

### Failure Codes & Pass Threshold
- `HOOK_WEAK`: Clip 1 is static in `f_start` or setting is unclear.
- `EXTRA_LIMB`: Extra hands, fingers, limbs, or warped face/eyes on any sampled frame.
- `STYLE_DRIFT`: 2D line weight shifts, 2D bleeds into 3D (or vice versa), or outfit/colors diverge from the turnaround sheet or previous shot.
- `MULTI_CHAR_BLEED`: Attributes swap or merge between two characters in the same shot.
- `TEXT_WARP`: Garbled AI-rendered text, logos, or watermarks appear.
- `AUDIO_DESYNC`: Dialogue clips off at boundary or mouth/action timing drifts badly.

**Pass rule:** Zero `EXTRA_LIMB`, `TEXT_WARP`, or `HOOK_WEAK` failures, and all scores `>= 4/5`. Cap retries at 3 per shot.
