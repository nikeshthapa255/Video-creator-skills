# Long-form animated video production guide (v4.0)

Use this guide when scaling the pipeline from 30-55s Shorts to **3-15+ minute multi-act animated episodes** (45-180+ shots). Every workflow below builds on real multi-minute episode production (including 5-minute, 5-act, 92-shot serialized dramas at `-14.0 LUFS` with `<= 1` frame drift and horizontal 16:9 news explainers) and covers both vertical micro-dramas (`9:16`) and horizontal YouTube episodes (`16:9`).

## 1. Choose the long-form sub-format

| Dimension | Vertical micro-drama (`9:16`, 3-8 min) | Horizontal YouTube episode (`16:9`, 3-15+ min) |
| :--- | :--- | :--- |
| **Canvas** | `1080x1920` (2x zoompan `2160x3840`) | `1920x1080` (2x zoompan `3840x2160`) |
| **Structure** | 4-5 Acts (`~55-66s`/Act, `~17-20` shots/Act) | 3-6 Chapters (`~60-180s`/Chapter) |
| **Word count & shots** | `~750-1,100` words, `80-120` shots (`~3.2-3.6s` avg) | Commentary: `~300-525` words (3-5m, `100-115 WPM`); Story/doc: `~500-2,200+` words, `45-180+` shots (`~3.5-6.5s` avg) |
| **Framing & focal lock** | Middle 50% safe zone (eyes at 32-42% height, torso/hands above 65%) | Rule-of-thirds widescreen (`focal_anchor: "left_third" | "right_third"`), OTS, establishing wides, bottom 16% clear |
| **Captions** | Kinetic `86px` (`MarginV=420-480`, `max_chars=18`) or calm `clean_subtitle` | **`caption_style: "none"` + `captions.srt` CC recommended**; or burned `clean_subtitle` (`62px`, `MarginV=116`, `max_chars=36`, `caption_tilt: 0`, `Outline=6`, `Shadow=3.5`). Never enable both burned-in English ASS and default-on English CC. |
| **Opening & ending** | Cold open at `0:00` + re-hooks every `25-45s`; serialized `cliffhanger` (`dual_anchor_loop: false`) | `0-5s` title/thumbnail payoff + brisk `0-20s` hook (`1.5-2.5s` cuts) + re-hooks every `45-90s`; `outro` (8-12s End-Screen slate with `static_hold`) or `cliffhanger` |
| **Production mode** | `keyframe_motion` (Mode B) or `hybrid` (Mode A hero hooks/climaxes + Mode B dialogue) | `keyframe_motion` (Mode B) or `hybrid` |

Set these top-level fields in `shot_plan.json`:
- `"video_format": "long_form"`, `"script_genre": "drama"` (or `"commentary"`)
- `"aspect_ratio": "9:16"` or `"16:9"`
- `"ending_style": "cliffhanger"` or `"outro"` (omit `loop_bridge`, set `dual_anchor_loop: false`)
- `"acts": [...]` with `title` (non-spoiler, `<= 35` chars), `bgm_intensity`, `lighting_mood`, `rehook_question`, and optional `act_file`

## 2. 16:9 commentary, news, and explainer playbook

Horizontal `16:9` commentary episodes break down when directed with fast `9:16` micro-drama speeds (`+16%` to `+18%`), cartoon SFX, or random cuts between 3D art and 2D hosts. Follow five rules for `16:9` `script_genre: "commentary"` runs:

1. **Stage 1.5 pre-render review and LGTM gate (optional for solo runs, recommended for flagship 16:9):** Before generating Stage 2/3 sheets or keyframes, run two fast text-only passes over the merged `shot_plan.json`:
   - *Domain and technical accuracy pass:* Check every metric, date, quote, company claim, and `tts_pronunciations` entry against primary sources.
   - *Visual Flow and Retention Director pass:* Check hook payoff (`0-5s`), visual tier consistency, chart density, and Act re-hooks. Fixing a shot plan here costs zero image quota.
2. **Anti-firehose pacing budget:** Keep display narration between **100 and 115 WPM** (about `300` words for a 3-minute episode, `525` words for a 5-minute episode). Calibrate `voice_cast` TTS `rate` to **`+6%` to `+10%`** (`tts_and_captions_v2.py --lint-plan` warns above `+12%` or `130 WPM`). Insert `post_gap_sec: 0.5` to `0.8` after headline numbers and Act turns so key metrics sink in. Move deep technical caveats, disclaimers, and source URLs into `packaging.md` instead of cramming them into narration.
3. **Anchor vs. Skeptical Analyst dual-host dynamic:** Give two hosts distinct jobs instead of splitting one monologue across alternating clones:
   - *Host A (Anchor):* Delivers the setup, concrete numbers, and timeline cleanly.
   - *Host B (Skeptical Analyst):* Lands **2 to 3 organic skepticism beats** per episode (questioning vendor claims, checking unit economics, highlighting trade-offs or failure modes).
   - Because `tts_and_captions_v2.py --strict-slop` enforces commentary slop rules on named dual-hosts whenever `script_genre == "commentary"`, keep both voices in plain spoken English.
4. **Three-tier visual hierarchy (stops 3D/2D ping-pong and cramped slides):**
   - **Tier A (3D hero art, 5 to 6 shots max):** Reserve stylized 3D scenes strictly for the `0-10s` opening hook and Act openers.
   - **Tier B (cohesive 2D host/studio body):** Keep regular dialogue exchanges in one consistent 2D host/studio style anchored to locked turnaround sheets and a master studio location plate (Template 1E).
   - **Tier C (dark-mode big-type data slides):** Limit each data slide to **10 to 15 words** inside a `scale(0.89)` inner safe box with the bottom 16% completely clear for `MarginV=116` subtitles. Animate dense data charts and End-Screen slates with `motion_preset: "static_hold"` (`z=1.00`, zero edge crop) instead of `static_drift`.
5. **Editorial Tech/UI audio palette:** Use soft-calibrated Tech/UI SFX cues (`ui_tick`, `ui_click`, `data_chime`, `sub_whoosh`, `alert_ping`, `soft_thud`, `-17` to `-19 dBFS`) and set `"ambience_bed": "newsroom_air"` in `shot_plan.json`. Never place cartoon cues (`bonk_boing`, `pop`, `record_scratch`, `siren_chirp`) in `16:9` commentary (`--lint-plan` flags `SFX_TONE_MISMATCH`).

## 3. Pre-draft Writers' Packet and modular Act authoring

Do not draft 90+ shots in one monolithic pass. Before fanning out to parallel Act writers (`acts/act_01.json` .. `acts/act_0N.json`), lock a compact **Writers' Packet** so isolated subagents never repeat facts or flatten voices:

1. **Episode outline and Act turns:** Lock Act boundaries, `rehook_question` per Act, exact state-change shot numbers (for example, veil removal on `shot_061`), and exact closing/opening handoff beats at every Act seam.
2. **Character voice cards:** Define each speaker's diction, sentence length, honorifics/address forms, and verbal taboos alongside `voice_cast`. Keep `narrator` share `<= 30-35%` in scripted dramas (max 3 consecutive narrator shots mid-scene) and never narrate physical actions already shown in `beats[].action`.
3. **Per-Act knowledge gate:** List what was revealed in Act `N-1` so Act `N` treats it as established subtext and never re-explains it.
4. **Setup / payoff prop and line ledger:** Track which Act/shot plants each hero prop, wound, or clue and which exact Act/shot pays it off.
5. **Merge and lint once:** Use 3-digit shot IDs (`shot_001` .. `shot_NNN`), merge into `<project_dir>/shot_plan.json`, verify every `shot.speaker` exists in `voice_cast`, and run:
   ```sh
   python3 scripts/tts_and_captions_v2.py --lint-plan <project_dir>/shot_plan.json --strict-slop
   ```

## 4. Multi-voice casting, silent beats, and runtime calibration

### Verified `WordBoundary`-capable `edge-tts` cast

Pair accents across roles (`en-GB` vs. `en-US`) so listeners distinguish speakers immediately without looking:

| Role example | Verified `edge-tts` voice | Micro-drama `rate` (`9:16`) | Commentary/explainer `rate` (`16:9`) | Safe `pitch` (`-6..+6Hz`) |
| :--- | :--- | :--- | :--- | :--- |
| Narrator / Storyteller | `en-GB-SoniaNeural` | `+12%` to `+16%` | `+6%` to `+10%` | `-1Hz` |
| Lead / Anchor | `en-US-AvaNeural` | `+10%` to `+14%` | `+6%` to `+10%` | `-2Hz` |
| Antagonist / Rival | `en-US-EmmaNeural` | `+14%` to `+18%` | `+6%` to `+10%` | `+4Hz` to `+5Hz` |
| Ally / Skeptical Analyst (male) | `en-GB-RyanNeural` | `+10%` to `+14%` | `+6%` to `+10%` | `-4Hz` |
| Supporting / Guard (male) | `en-GB-ThomasNeural` | `+12%` to `+15%` | `+6%` to `+10%` | `-3Hz` |
| Secondary host (female) | `en-US-JennyNeural` | `+8%` to `+14%` | `+6%` to `+10%` | `+0Hz` |

### Runtime calibration and silent reaction beats

- **Avoid runtime bloat (`9:16` drama) and firehose speed (`16:9` commentary):** On an ~800-word / 92-shot 5-minute vertical drama, use `+13%..+18%` rates, `--gap 0.08`, and `post_gap_sec: 0.25` at Act breaks (`0.0` on final shot). On `16:9` commentary, stay at `+6%..+10%` rates with `0.5s` to `0.8s` `post_gap_sec` after headline stats and Act turns.
- **Directing silent reaction beats:** Budget 10-15% of dramatic shots as **silent holds** (`dialogue_or_vo: ""`, `shot_role: "reaction"`, `duration_sec: 0.9-1.8`, `sfx_type: "court_gasp_swell" | "heartbeat_pulse" | "blade_unsheathe"`). `tts_and_captions_v2.py` holds exact `duration_sec` silence without extra default `--gap` padding so scenes breathe.
- **Synthesize concurrently with retry backoff:**
  ```sh
  python3 scripts/tts_and_captions_v2.py \
    --shot-plan <project_dir>/shot_plan.json \
    --out-dir <project_dir>/audio \
    --gap 0.08 --concurrency 4 --max-retries 4 --export-srt
  ```
  This outputs `audio/voiceover.wav`, `audio/shot_timings.json` (with `acts[]`, `bgm_intensity`, and `ambience_bed`), `audio/chapters.txt` (validated for YouTube's `>= 3` chapters and `>= 10s` rule), `audio/captions.ass` (skipped if `caption_style: "none"`), and `audio/captions.srt`.
- **Bring your own voiceover (Human mic / ElevenLabs):** For flagship `16:9` channels, record or generate `audio/voiceover.wav`, transcribe to `.srt` + `shot_timings.json`, run `srt_to_ass.py --aspect-ratio 16:9 --caption-style clean_subtitle` (defaults to `MarginV=116`, or pass `none` to `assemble_short.sh`), and feed the same downstream pipeline.

## 5. Visual continuity: multi-state sheets, location plates, and 180-degree blocking

1. **Multi-state character sheets (Template 1D):** Anchor state variants (`sheets/<char>_veiled.png`) to the base sheet (`sheets/<char>.png`), map them in `characters[].state_sheets`, set `shots[].character_states`, and flip `negative_constraints` at the exact state-transition cut.
2. **Recurring set / location anchor plates (Template 1E):** For any recurring set (Throne Hall, Study, Newsroom, Courtyard), generate 1-4 empty-set master plates (`sheets/loc_<id>.png`), register them in `locations[]` with locked key-light direction, and attach `Image 2 = sheets/loc_<id>.png` alongside the character sheet so parallel Act workers share identical architecture and lighting.
3. **Hero prop registry (`props[]`):** Lock visual traits (and clean/damaged states) for recurring plot props (poisoned cup, jade seal, forged ledger) so they never morph across Acts.
4. **Shot-scale rhythm and 180-degree eye-line blocking:**
   - Vary `shot_scale` across every beat (`WS/EWS` establish -> `MS/OTS` exchange -> `CU/ECU` turn; max 2 consecutive shots at the same scale).
   - Pin opposing `eyeline` directions in shot-reverse-shot (`Char A: screen_right` <-> `Char B: screen_left`) and set `focal_anchor` (`left_third` / `right_third` / `upper_center`) on off-center `16:9` frames so `animate_shot_v2.py` zooms toward the subject instead of dead center.
   - Keep `>= 85%` of shots to 0-1 lead character (`~70%` solo, `~15-20%` inserts/wides, `<= 12%` left/right two-shots; use blurred OTS shoulder silhouettes in `16:9` to keep intimacy without triggering `MULTI_CHAR_BLEED`).
   - Add local script bans to `style_bible` in historical/cultural settings (`"plain silk banners, plain scrolls, zero letters, zero Chinese characters, zero digits, zero signage"`).

## 6. Act-aware BGM, spotted silence, calibrated SFX, and ambience

1. **Act-aware procedural BGM (`make_bgm.py --timings`):** Honors `acts[].bgm_intensity` (`sparse | tense | escalating | climax | epilogue`), holds dramatic tension instead of resolving to a peaceful fade when `ending_style == "cliffhanger"`, and supports **spotted silence** via per-shot `bgm_cue` (`"drop_out"`, `"drop_before_end"`, `"swell_in"` with `bgm_drop_sec`):
   ```sh
   python3 scripts/make_bgm.py \
     --mood palace_drama \
     --timings <project_dir>/audio/shot_timings.json \
     --out <project_dir>/audio/bgm.wav
   ```
   Use `--mood epic_story` for documentaries, thrillers, and general multi-act stories, or `dramatic_news` / `tech_pulse` for `16:9` news commentary. Speech-pocket EQ runs once inside `assemble_short.sh` so there is zero double-carving.
2. **Calibrated SFX and automatic room-tone ambience (`sfx_engine.py`):** Renders 26 RMS-calibrated cues (including 6 soft `-17` to `-19 dBFS` Tech/UI cues: `ui_tick`, `ui_click`, `data_chime`, `sub_whoosh`, `alert_ping`, `soft_thud`) with 3-variant caching and per-shot `sfx_pan` (`0.0..1.0`). When `--ambience` is omitted, `sfx_engine.py` automatically reads `ambience_bed` (`palace_courtyard`, `throne_hall`, `newsroom_air`, `hearth_embers`, or `none`) forwarded into `shot_timings.json`:
   ```sh
   python3 scripts/sfx_engine.py \
     --timings <project_dir>/audio/shot_timings.json \
     --out <project_dir>/audio/sfx_track.wav
   ```

## 7. Parallel swarm animation and Hybrid Mode A/B handoffs

### Mode A vs. Mode B directing matrix (for `hybrid` mode)

Spend Mode A (`omni_video`) on the **6-10 highest-ROI shots** per episode, and keep Mode B (`keyframe_motion`) on the remaining 85-90%:
- **Use Mode A (`omni_video`) for:** Frame 0 cold-open hook (`0-10s`), physical in-shot transformations (unveiling, shattering porcelain, pouring wine, collapsing/running), and climax action bursts.
- **Use Mode B (`keyframe_motion`) for:** Multi-character dialogue exchanges, emotional slow pushes (`slow_dolly_in`), prop/document inserts (`tilt_up` / `tilt_down`), dense data charts and End-Screen slates (`static_hold`, zero crop), and rapid montage cuts.
- **Hybrid visual and technical seam bridge:**
  1. Generate Stage 3 keyframes (`scenes/shot_XXX.png`) using the same sheets/style bible even for Mode A shots, and pass `scenes/shot_XXX.png` as Mode A's start-frame reference so set geometry and palette match Mode B neighbors.
  2. Cut out of a Mode A clip into a Mode B cutaway/insert or motion-matched preset (`whip_zoom`/`bounce_pop`) rather than a frozen `static_drift`.
  3. Strip Mode A audio (`-an`), trim to the exact fractional `duration_sec` in `shot_timings.json`, save as `clips/<idx:03d>_<shot_id>.mp4`, and run `animate_shot_v2.py --skip-omni` so Mode B renders only non-Omni shots while preserving global cumulative frame numbers.

### Parallel Act animation workers

```sh
python3 scripts/animate_shot_v2.py \
  --timings <project_dir>/audio/shot_timings.json \
  --scenes-dir <project_dir>/scenes \
  --out-dir <project_dir>/clips \
  --act 1 --workers 4 --skip-existing --skip-omni
```
Pass `--fade-out-last` on the final Act when `ending_style == "outro"`; omit `--fade-in-first` to keep Frame 0 sharp. Use per-shot `fade_in`/`fade_out` on Act transitions for clean dip-to-black breathers.

## 8. Fast-path assembly, packaging, and chunked multimodal QA

1. **Creator packaging (`packaging.md` + thumbnails/covers):**
   - **16:9 YouTube thumbnails:** Render `thumbnail.png` (`1920x1080`, strictly `< 2 MB`, bottom-right duration badge zone clear, optional `--right-image` split canvas and `--mobile-preview` 160px legibility check) with `scripts/make_thumbnail.py --aspect 16:9`. For `ending_style: "outro"`, reserve an **8-12s End-Screen shot** (`shot_role: "end_screen"`, `motion_preset: "static_hold"`) with subject framed left/top and clean space for clickable end cards.
   - **9:16 Vertical cover:** Render `thumbnail.png` (`1080x1920`, top text clamped below `y=288px` chrome) via `scripts/make_thumbnail.py --aspect 9:16`.
   - **Metadata (`packaging.md`):** Front-load YouTube titles (`<Hook 1>, <Hook 2> & <Hook 3> | Series #N`, zero `<`/`>`, zero em dashes), add 2-line hook description (`< 125` chars), non-spoiler `chapters.txt`, hashtags, tags, synthetic-content disclosure, and pinned comment. Lint with `python3 scripts/tts_and_captions_v2.py --lint-metadata <project_dir>/packaging.md --strict-slop`. Deliver inside a clean `<slug>/` folder per `references/batch_and_delivery.md`.
2. **Fast-path assembly (`assemble_short.sh`):** Probes `codec`, resolution, FPS, `time_base`, `SAR`, and stereo signature before using `-c copy` fast-path (falling back to per-clip conform on Hybrid timescale mismatches), mixes 3 stems in lossless 24-bit PCM with single-point BGM carving (`10.5 kHz` LP, `-3.0 dB @ 2.9 kHz`, smooth musical ducking), burns captions with 2-second keyframe cadence (`-g 60`), normalizes to `-14 LUFS` (`LRA=7` on `9:16`, `LRA=9` on `16:9`), and verifies post-norm LUFS:
   ```sh
   SHORTS_VO_CLARITY=1 SHORTS_BGM_GAIN_DB=-15 \
   bash scripts/assemble_short.sh \
     <project_dir>/clips \
     <project_dir>/audio/captions.ass \
     <project_dir>/final_episode.mp4 \
     <project_dir>/audio/voiceover.wav \
     <project_dir>/audio/bgm.wav \
     <project_dir>/audio/sfx_track.wav
   ```
   Pass `none` for `captions.ass` on `16:9` YouTube uploads using `audio/captions.srt` CC.
3. **Chunked 60-second multimodal QA (`split_qa_segments.sh`, mandatory for `> 90s` or `> 80 MB`):**
   ```sh
   bash scripts/split_qa_segments.sh \
     <project_dir>/final_episode.mp4 \
     <project_dir>/qa_segments \
     --timings <project_dir>/audio/shot_timings.json
   ```
   Inspect **every** `qa_min*.mp4` chunk (`< 100 MB` each) **with sound** via `view_file` (or parallel QA subagents), review `screenshots/frame_*.png`, and verify full + per-segment LUFS and `chapters.txt`.

## 9. Series continuity handoff (fiction and recurring non-fiction)

Choose the handoff artifact that matches your show format:

### Fictional serialized drama (`README_series_bible.md`)

Archive `<project_dir>/README_series_bible.md` alongside `sheets/`, `shot_plan.json`, `shot_timings.json`, and `captions.ass` containing:
- Act-by-Act plot summary and the exact cliffhanger beat + **Ep 2 Cold-Open Entry Note** (how Ep 2 picks up without a stale recap dump)
- **Character continuity and voice table:** turnaround/state sheet paths (active vs. retired), exact end-of-episode physical/wardrobe/prop state, locked `edge-tts` voice + `rate` + `pitch`, and **Voice & Address Card** (diction, honorifics, relationship shift)
- **Information / secrets matrix:** who knows what at Episode end (Public / Hero / Rival / Audience-Only)
- **Unresolved setup / payoff ledger:** planted props, clues, wounds, and promises carried into Ep 2+
- Locked verbatim `style_bible`, recurring `locations[]` plates, and complete `tts_pronunciations` dictionary

### Recurring non-fiction, news, and commentary series (`episode_context.md`, 40 to 60 lines)

Save a compact `<slug>/episode_context.md` inside each delivered episode folder (and feed the previous episode's file to the next coordinator) so recurring news/commentary series stay consistent without dragging full transcripts into context:

```markdown
# Episode context: <episode_id> (<slug>)
- **Date & runtime:** YYYY-MM-DD | MM:SS (16:9 commentary)
- **Headline angle:** One sentence summarizing the core thesis covered.
- **Covered claims & numbers (do not repeat verbatim):**
  - <Entity>: <metric / ruling / date already explained>
- **Open follow-up triggers (watch for FOLLOW_UP episodes):**
  - <Upcoming court ruling, earnings date, release window, or counter-move>
- **Locked cast, sheets & visual traits:**
  - Host A (`anchor`, `en-US-AvaNeural`, `+8%`): `shared/sheets/host_a.png`
  - Host B (`analyst`, `en-GB-RyanNeural`, `+8%`): `shared/sheets/host_b.png`
  - <Public figure>: `shared/sheets/<id>.png` (verified traits: glasses/hair/attire)
- **Locked `tts_pronunciations`:** `{"MCP": "M C P", "NPC": "N P C", ...}`
- **Next-episode bridge / viewer question:** Exact open thread left in the outro or pinned comment.
```

