# Long-form animated video production guide (v4.0)

Use this guide when scaling the pipeline from 30-55s Shorts to **3-15+ minute multi-act animated episodes** (50-180+ shots). Every rule below comes from real multi-minute episode production (including 5-minute, 5-act, 92-shot serialized vertical dramas at `-14.0 LUFS` with `-6.7 ms` total A/V drift) and generalizes to both vertical micro-dramas (`9:16`) and horizontal YouTube episodes (`16:9`).

## 1. Choose the long-form sub-format

| Dimension | Vertical Micro-Drama (`9:16`, 3-8 min) | Horizontal YouTube Episode (`16:9`, 3-15+ min) |
| :--- | :--- | :--- |
| **Canvas** | `1080x1920` (2x zoompan `2160x3840`) | `1920x1080` (2x zoompan `3840x2160`) |
| **Structure** | 4-5 Acts (~55-66s per Act, ~17-20 shots/Act) | 3-6 Chapters (~60-180s per Chapter) |
| **Word count & shots** | ~750-1,100 words, 80-120 shots (`~3.2-3.6s` avg shot) | ~500-2,200 words, 45-180 shots (`~3.5-6.5s` avg shot) |
| **Framing** | Middle 50% vertical safe zone | Rule-of-thirds widescreen, establishing shots, two-shots |
| **Captions** | Kinetic `86px`, `MarginV=420`, `max_chars=18`, `tilt=2.5` | Clean lower-third `62px`, `MarginV=72`, `max_chars=36`, `tilt=0` (or `none` + `--export-srt` for YouTube CC) |
| **Opening & ending** | Frame 0 cold open (`whip_zoom` + `flash_in` + impact SFX); serialized `cliffhanger` (`dual_anchor_loop: false`) | Cold-open hook (`0-20s`) + retention re-hooks every 60-90s; natural `outro` or `cliffhanger` (`dual_anchor_loop: false`) |
| **Production mode** | `keyframe_motion` (Mode B) or `hybrid` (Mode A hero shots + Mode B narrative) | `keyframe_motion` (Mode B) or `hybrid` |

Set these top-level fields in `shot_plan.json`:
- `"video_format": "long_form"`
- `"aspect_ratio": "9:16"` or `"16:9"`
- `"ending_style": "cliffhanger"` or `"outro"` (omit `loop_bridge` and set `dual_anchor_loop: false` on all shots)
- `"acts": [...]` (ordered Act/chapter metadata with titles and optional `act_file` pointers)

## 2. Modular Act-by-Act screenplay authoring

Do not draft 90+ shots in one monolithic pass. Split the screenplay into per-Act JSON modules (`acts/act_01.json` .. `acts/act_0N.json`) so parallel writing subagents can draft Acts concurrently against one shared cast, `style_bible`, and continuity outline, then merge them into one master `shot_plan.json`:

1. **Lock the episode outline first:** Define Act boundaries, emotional turning points, exact state-change shot numbers (for example, when a character removes a veil, gets injured, or changes location), and the end-of-episode cliffhanger or payoff.
2. **Draft Acts in parallel:** Each Act targets ~150-220 words (~17-22 shots for vertical micro-drama, or ~10-20 shots for horizontal pacing). Use 3-digit shot IDs (`shot_001` .. `shot_092`) from the start so lexicographical sorting never breaks past shot 99.
3. **Merge and lint once:** Merge all Acts into `<project_dir>/shot_plan.json` and run:
   ```sh
   python3 scripts/tts_and_captions_v2.py --lint-plan <project_dir>/shot_plan.json
   ```
   Fix every `TTS_MISREAD` error and review `AI_SLOP_SCRIPT` warnings before synthesizing audio.

## 3. Multi-voice casting and runtime calibration

Long-form stories with multiple characters need distinct voices that listeners tell apart immediately without looking at the screen.

### Verified `WordBoundary`-capable `edge-tts` cast

Not every neural voice in `edge-tts` emits `WordBoundary` timestamps (required for word-level kinetic captions). Stick to verified voices and pair accents across character roles (for example, `en-GB` for narrator/authority and `en-US` for leads/rivals):

| Role example | Verified `edge-tts` voice | Suggested pacing (`rate`) | Suggested `pitch` |
| :--- | :--- | :--- | :--- |
| Narrator / Storyteller | `en-GB-SoniaNeural` | `+12%` to `+16%` | `-1Hz` |
| Lead / Protagonist | `en-US-AvaNeural` | `+10%` to `+14%` | `-2Hz` |
| Antagonist / Rival | `en-US-EmmaNeural` | `+14%` to `+18%` | `+4Hz` to `+5Hz` |
| Ally / Authority (male) | `en-GB-RyanNeural` | `+10%` to `+14%` | `-4Hz` |
| Supporting / Guard (male) | `en-GB-ThomasNeural` | `+12%` to `+15%` | `-3Hz` |
| Secondary host (female) | `en-US-JennyNeural` | `+8%` to `+14%` | `+0Hz` |

Define `voice_cast` at the top level of `shot_plan.json` (do not duplicate a separate `speaker_voices` block):

```json
{
  "voice_cast": {
    "narrator": { "voice": "en-GB-SoniaNeural", "rate": "+16%", "pitch": "-1Hz" },
    "hero": { "voice": "en-US-AvaNeural", "rate": "+14%", "pitch": "-2Hz", "keyword_color": "&HFFE5A0&" },
    "rival": { "voice": "en-US-EmmaNeural", "rate": "+18%", "pitch": "+5Hz", "keyword_color": "&H365AFF&" },
    "ally": { "voice": "en-GB-RyanNeural", "rate": "+13%", "pitch": "-4Hz", "keyword_color": "&H00D7FF&" }
  }
}
```

### Runtime calibration lesson (avoiding 15-20% runtime bloat)

- Neutral TTS rates (`+0%` to `+5%`) with `0.14s` inter-shot gaps run roughly **15-20% slower** than target pacing on an ~800-word / 90-shot drama (~355s instead of ~300s).
- For crisp 5-minute vertical drama pacing (~800 words, ~92 shots), set speaker rates to `+13%` to `+18%`, use `--gap 0.08`, and place `"post_gap_sec": 0.25` ONLY on Act-closing shots (plus `"post_gap_sec": 0.0` on the final shot).
- For calmer 16:9 YouTube documentaries or explainers, `+2%` to `+8%` with `--gap 0.12` and `"post_gap_sec": 0.35` on chapter breaks reads naturally.
- Synthesize with bounded concurrency and retry backoff so a transient WebSocket hiccup across 90+ shots never aborts the run:
  ```sh
  python3 scripts/tts_and_captions_v2.py \
    --shot-plan <project_dir>/shot_plan.json \
    --out-dir <project_dir>/audio \
    --gap 0.08 \
    --concurrency 4 \
    --max-retries 4
  ```
  For `16:9` episodes, pass `--aspect-ratio 16:9` (or set `"aspect_ratio": "16:9"` in `shot_plan.json`) and add `--export-srt` to emit a clean `audio/captions.srt` sidecar for YouTube closed captions.

## 4. Multi-state character sheets and 90-shot prompt discipline

Text prompts alone cannot hold a major visual state change steady across 60+ generated keyframes (such as keeping a veil ON for Acts 1-3 and OFF for Acts 4-5, or keeping a stained sleeve or battle injury consistent).

1. **Create dedicated state-variant turnaround sheets:**
   - Generate the base 16:9 3-panel sheet (`sheets/<char>.png`) first, then derive state variants (`sheets/<char>_veiled.png`, `sheets/<char>_damaged.png`) anchored to the base sheet (Template 1D in `references/sheet_prompt_templates.md`).
   - Map them explicitly under `characters[].state_sheets` and select the active state per shot in `shots[].character_states` (for example, `{"lin_wanyin": "veiled"}` on shots 1-60, flipping to `"unveiled"` on shots 61-92).
   - Flip each shot's `negative_constraints` at the exact transition cut (for example, `"do NOT remove the veil"` through shot 60 -> `"do NOT cover the face with a veil"` from shot 61 onward).
2. **Track progressive continuity explicitly:** Record minor wardrobe/prop damage (stained cuff, broken hairpin, disheveled hair) in `characters[].continuity_notes` and enforce it in every subsequent shot prompt.
3. **Keep ~85-90% of shots to zero or one lead character:**
   - **~70% solo shots:** Prefix with `SOLO character: ONLY <Name> (matching the attached turnaround sheet) appears in this frame; no other named characters appear in the foreground.` Attach ONLY that character's active state sheet.
   - **~15-20% zero-lead shots** (inserts, environments, flashbacks, crowds): Prefix with `No named lead characters appear in the foreground of this frame.` Attach zero lead sheets.
   - **<= 12% two-character shots (never 3+):** Prefix with `Two-character confrontation: <CharA> (matching the first attached turnaround sheet) positioned on the left and <CharB> (matching the second attached turnaround sheet) positioned on the right, with clear spatial separation to prevent costume or face bleed.`
4. **Extend zero-text guards for cultural or historical worlds:** In historical, fantasy, or international settings, image models hallucinate gibberish scripts on banners, scrolls, books, and pillars. Always list the exact local surfaces and scripts to suppress in `style_bible` (for example: `"unadorned palace pillars, plain silk banners, plain scrolls, zero letters, zero Chinese characters, zero digits, zero signage, no subtitles, no watermarks"`).

## 5. Act-aware BGM and fast vectorized SFX

Looping one 30-second music clip across a 5-to-15-minute video causes severe listener fatigue and flattens story turns.

1. **Act-aware procedural BGM (`make_bgm.py --timings`):** Pass `audio/shot_timings.json` (which contains exact `acts` timestamps) so the score evolves dynamically per Act (sparse opening -> tense pulse -> percussive escalation -> climax -> epilogue fade) with built-in speech-pocket EQ:
   ```sh
   # Historical / palace drama 5-act score (guzheng, erhu, dagu drums, bronze gong):
   python3 scripts/make_bgm.py \
     --mood palace_drama \
     --timings <project_dir>/audio/shot_timings.json \
     --out <project_dir>/audio/bgm.wav

   # General multi-act narrative / documentary / thriller score:
   python3 scripts/make_bgm.py \
     --mood epic_story \
     --timings <project_dir>/audio/shot_timings.json \
     --out <project_dir>/audio/bgm.wav
   ```
2. **Vectorized SFX at scale (`sfx_engine.py`):** Caches every synthesized cue waveform and mixes across 5-15 minute timelines in seconds via NumPy (with pure-stdlib fallback). Includes cinematic/drama cues (`palace_gong`, `war_drum_hit`, `silk_whoosh`, `jade_clink`, `porcelain_shatter`, `blade_unsheathe`, `guzheng_gliss`, `fire_crackle_burst`, `court_gasp_swell`, `cinematic_riser`, `heartbeat_pulse`) alongside all 9 Shorts cues.

## 6. Parallel subagent swarm workflow and frame-locked animation

Scale Stage 3 production across parallel subagents without timeline drift or file collisions:

1. **Coordinator locks shared truth first:** Stage 0 references, all base + state turnaround sheets, merged `shot_plan.json`, `audio/voiceover.wav`, `audio/shot_timings.json`, `audio/captions.ass`, `audio/bgm.wav`, and `audio/sfx_track.wav`.
2. **Parallel Act keyframe workers (one subagent per Act):** Each worker reads the shared sheets read-only and generates `scenes/shot_XXX.png` for its assigned Act only, running visual QA against the active state sheet before finishing.
3. **Parallel frame-locked animation workers (`animate_shot_v2.py`):**
   - `animate_shot_v2.py` computes global cumulative frame boundaries across **all** shots in `shot_timings.json` *before* applying `--act`, `--start-idx`, `--end-idx`, or `--shot-ids` filters. That locks total drift to `<= 1` frame (`~10 ms`) across 90-180 shots even when 5 workers render Acts concurrently!
   - Always pass `--workers 4 --skip-existing` (and `--fade-out-last` on the final Act or full run; omit `--fade-in-first` so Frame 0 stays punchy):
     ```sh
     python3 scripts/animate_shot_v2.py \
       --timings <project_dir>/audio/shot_timings.json \
       --scenes-dir <project_dir>/scenes \
       --out-dir <project_dir>/clips \
       --act 1 \
       --workers 4 \
       --skip-existing
     ```
   - Outputs 3-digit zero-padded clips (`001_shot_001.mp4` .. `092_shot_092.mp4`) and auto-detects `16:9` (`1920x1080`) vs `9:16` (`1080x1920`) from `shot_timings.json` (or `--aspect-ratio`).

## 7. Fast-path assembly and chunked multimodal QA

1. **Fast-path `-c copy` assembly (`assemble_short.sh`):** Probes every clip in `clips/` (sorted via `LC_ALL=C find | sort`). When all clips match the target H.264/FPS/resolution signature, `assemble_short.sh` concatenates them with `-c copy` in seconds instead of re-encoding 90+ clips, mixes 3 stems with acoustic-safe `8500 Hz` BGM lowpass and `-3.5 dB` speech-pocket carve, burns captions (or pass `none` for clean `16:9` with soft `.srt`), runs 2-pass `-14 LUFS` `loudnorm`, and prints a mandatory post-norm loudness verification check:
   ```sh
   SHORTS_VO_CLARITY=1 SHORTS_BGM_GAIN_DB=-16 \
   bash scripts/assemble_short.sh \
     <project_dir>/clips \
     <project_dir>/audio/captions.ass \
     <project_dir>/final_episode.mp4 \
     <project_dir>/audio/voiceover.wav \
     <project_dir>/audio/bgm.wav \
     <project_dir>/audio/sfx_track.wav
   ```
2. **Chunked 60-second multimodal QA (`split_qa_segments.sh` — mandatory for videos > 90s or > 80 MB):**
   - A 5-minute `1080p CRF 18` episode is ~250-300 MB, which exceeds the `100 MB` `view_file` binary limit.
   - Always run `split_qa_segments.sh` to slice lossless 60-second review chunks (`qa_min1.mp4` .. `qa_minN.mp4`), extract 10 evenly spaced keyframe screenshots, measure independent integrated LUFS, and (with `--timings`) export YouTube chapter timestamps (`chapters.txt`):
     ```sh
     bash scripts/split_qa_segments.sh \
       <project_dir>/final_episode.mp4 \
       <project_dir>/qa_segments \
       --timings <project_dir>/audio/shot_timings.json
     ```
   - Inspect **every** `qa_min*.mp4` chunk with sound via `view_file` (or assign parallel QA subagents to audit 1-2 minutes each) plus the extracted `screenshots/frame_*.png` images.

## 8. Series Bible continuity handoff (for Episode 2+)

When finishing Episode 1 of a multi-episode series, write `<project_dir>/README_series_bible.md` and archive a compact context bundle alongside the final `.mp4` (`README_series_bible.md`, `sheets/`, `shot_plan.json`, `shot_timings.json`, `captions.ass`) containing:
- Act-by-Act plot summary and the exact cliffhanger beat
- Character table: turnaround sheet paths (including which state sheets are now retired vs active), exact end-of-episode physical/costume/prop state, and locked `edge-tts` voice + `rate` + `pitch`
- Verbatim `style_bible`
- Complete `tts_pronunciations` dictionary
