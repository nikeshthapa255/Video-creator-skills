# Batch production, quota waves, story dedup, and Drive delivery

Read this guide when producing multiple Shorts or episodes in parallel, running a recurring series, deduplicating incoming stories, or delivering finished videos to Google Drive.

## 1. Topic coordinator and flash worker hierarchy

Split multi-video runs into two layers so character sheets stay consistent and builds finish quickly:

1. **Topic coordinator (one per video topic):** Owns Stage 0 likeness inspection, Stage 1 script and `shot_plan.json`, pre-TTS linting, final multimodal watch-through, and production-log updates.
2. **Flash workers (subagents delegated per topic):** Handle bounded execution tasks: keyframe generation batches, `tts_and_captions_v2.py`, `sfx_engine.py`, `make_bgm.py`, `animate_shot_v2.py`, `make_thumbnail.py`, and `extract_qa_frames.sh`.
3. **Shared read-only assets:** Store one shared `shorts/shared/references/character_registry.json` (updated by Stage 0 `research_characters.py` and `--verify`) and master 3-panel turnaround sheets in `shorts/shared/sheets/<char>.png`. Parallel coordinators read these files without overwriting them.
4. **Voice and BGM rotation:** Alternate neural voices across back-to-back Shorts (`en-US-AvaNeural`, `en-US-EmmaNeural`, `en-US-JennyNeural`) and match BGM moods to tone (`dramatic_news` or `cinematic_tension` for news and commentary; `miami_pop`, `retro_drive`, `tech_pulse`, or `comedy_quirk` for comedy and upbeat Shorts).

## 2. Quota-aware wave batching and idempotent resume

Image generation quotas exhaust faster than audio or FFmpeg work. Prevent partial stalls across a batch by following three rules:

1. **Zero-Quota-First ordering:** Complete Stage 0 research, linted `shot_plan.json`, `audio/voiceover.wav`, `audio/shot_timings.json`, `audio/captions.ass` (and `.srt`), `audio/sfx_track.wav`, `audio/bgm.wav`, and `packaging.md` for every topic in the batch before making any image-generation call.
2. **Concurrent wave cap (3 to 4 Shorts per window):** Generate keyframes for at most 3 to 4 Shorts per image-quota window (about 36 to 56 keyframes). Queue remaining topics for Wave 2 once the quota window resets.
3. **Idempotent keyframe checkpointing:** Always check whether `scenes/<shot_id>.png` already exists and is non-empty before generating a frame. If an image call returns a quota error (`IMAGE_QUOTA_EXHAUSTED`), stop calling the image API for that wave, keep all completed keyframes on disk, and set the production log status to `quota_paused (N/M keyframes)`. In Wave 2, resume only the missing shots, then run `animate_shot_v2.py --skip-existing` and `assemble_short.sh`.

## 3. Per-video minimal delivery folder and Zero-Bloat Drive rule

Package every completed Short or long-form episode in its own `<slug>/` folder locally (`<deliverables_root>/<slug>/`) and on Google Drive (`<drive_folder>/<slug>/`). Include only final upload deliverables:

1. `<slug>-short.mp4` (or `<slug>-episode.mp4` for long-form)
2. `thumbnail.png` (`1080x1920` for `9:16` Shorts or `1920x1080` for `16:9` episodes, under `2 MB`, built via `scripts/make_thumbnail.py`)
3. `packaging.md` (YouTube title candidates, description, hashtags, tags, pinned comment, synthetic-content disclosure, and chapters for long-form)
4. Optional `episode_context.md` (for recurring non-fiction or commentary series; see `references/long_form_guide.md`)

**Zero-Bloat Drive rule:** Never upload `clips/`, `scenes/`, `audio/`, `sheets/`, `drafts_360p/`, `qa/`, or `qa_segments/` to the Drive delivery folder. Keep raw build intermediates in local project workspaces only. When moving or reorganizing files that already exist on Google Drive, run `gdrive mutate mv` instead of deleting and re-uploading so Drive File IDs and shared URLs stay intact.

**Front-loaded YouTube title pattern:** Put the concrete hook first and any series tag after a pipe (`<Hook 1>, <Hook 2> & <Hook 3> | Series #N`). Never put raw `<` or `>` in titles, avoid em dashes, and run `python3 scripts/tts_and_captions_v2.py --lint-metadata <slug>/packaging.md --strict-slop` before delivery.

## 4. Four-dimension story deduplication and follow-up rules

Before greenlighting a candidate topic in a recurring news or commentary series, check it against prior rows in `production_log.csv` (or Google Sheet) across four dimensions:

| Dimension | Check |
| :--- | :--- |
| 1. `slug` | Exact or normalized slug match |
| 2. Title overlap | High keyword overlap against prior `title` and `yt_title` values |
| 3. Source URL | Normalized canonical source URL (strip tracking parameters and trailing slashes) |
| 4. Entity + event | Same primary entity (person, company, product) and same core event |

Classify each candidate into one of three buckets:
- `DUPLICATE`: Same event with no material new development. Skip it.
- `FOLLOW_UP`: Same story arc with a concrete new event (new ruling, dollar figure, reversal, launch, or counter-move).
- `NET_NEW`: Fresh entity or distinct event.

When producing a `FOLLOW_UP` video, follow this 5-rule continuation checklist:
1. Verify a concrete new material fact exists (never ship a commentary rehash of old news).
2. Open Shot 1 cold on the new development instead of recapping the prior episode.
3. Keep prior-episode context to one 6 to 10 word anchor clause inside Shot 1 or Shot 2.
4. Reuse the locked Stage 0 character sheets and `tts_pronunciations` from the prior episode so likenesses and pronunciation match.
5. Link the prior episode inside `packaging.md` and record the parent `episode_id` and reason in `followup_notes`.

## 5. Production log schema (24 columns)

Maintain one `production_log.csv` (or Google Sheet) updated in place by the coordinator as each video moves through `planned`, `audio_ready`, `quota_paused (N/M keyframes)`, `qa_failed`, and `delivered`:

```csv
episode_id,slug,title,mode,voice,bgm_mood,duration_sec,shot_count,hook_line,qa_rerolls,final_path,status,drive_folder_url,drive_video_url,thumbnail_url,yt_title,yt_description,yt_hashtags,yt_tags,yt_pinned_comment,yt_category,followup_notes,backlog_notes,updated_at
```

Populate `drive_folder_url`, `drive_video_url`, `thumbnail_url`, and the `yt_*` fields as soon as the `<slug>/` folder uploads so the publishing queue can read one single source of truth.
