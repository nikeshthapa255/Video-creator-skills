# Failure-to-Fix Playbook (Evolutionary Memory)

Map each QA failure code to the cheapest reliable fix on Gemini Omni 1.2 Flash. Append newly discovered fixes after production runs.

| Failure Code | First-Choice Fix | Fallback Fix |
| :--- | :--- | :--- |
| `HOOK_WEAK` | Trim static lead-in or use `--previous-interaction-id` to make the setting landmark unmistakable in frame 0 (`"Show [landmark] clearly behind the character from frame 1. Keep everything else the same."`). | Rewrite Shot 1 beat `[0-2s]` with an active camera push-in and immediate character action. |
| `EXTRA_LIMB` | Edit in place via `--previous-interaction-id`: `"Remove the extra hand on [character]; keep exactly two arms. Keep everything else the same."` | Re-roll with simpler arm motion, remove held objects from the beat, or avoid extreme side-profile turns. |
| `STYLE_DRIFT` | Re-anchor with the original 3-panel turnaround sheet (do not chain a drifted frame) and verify the verbatim `style_bible` block is present. | Shorten the clip (e.g., split a 15s clip into two 7s clips) and lock `"In a single unbroken scene, no scene cuts"`. |
| `MULTI_CHAR_BLEED` | Split into shot-reverse-shot (one character per cut) or explicitly pin positions (`"Character A on screen left, Character B on screen right, separated by a desk"`). | Pass distinct turnaround sheets for both characters in explicit upload order. |
| `TEXT_WARP` | Add `"blank signs, no readable text, no logos, no watermarks"` and overlay any needed text in FFmpeg post-processing. | Crop or cover small edge artifacts during 9:16 safe-zone framing. |
| `AUDIO_DESYNC` | Re-run edit with `--strip-audio` to regenerate clean audio, or switch the project to `tts_plus_bgm` mode with `gemini-3.8-flash-tts`. | Pad or trim clip boundaries by 0.1-0.2s during FFmpeg concat. |
