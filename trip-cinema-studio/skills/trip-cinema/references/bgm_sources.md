# BGM Sources & Gate 3 Picker Guide

Trip Cinema Studio uses **no voiceover**. Audio is built from natural camera ambience/SFX plus a user-approved background music (BGM) track mastered to `-14 LUFS`, `LRA=9`.

## Option A: Open-Source / CC0 Music (`$0`)
Curated royalty-free / CC0 libraries suitable for travel films:
- **FreePD** (`freepd.com`) — Public domain (CC0) acoustic, ambient, and upbeat travel cues.
- **Incompetech / Kevin MacLeod** (`incompetech.com`) — CC-BY 4.0 instrumental stems with clear tempo/mood tags.
- **Musopen** (`musopen.org`) — Public domain classical, piano, and string recordings.
- **Freesound CC0** (`freesound.org`) — CC0 ambient beds, field recordings, and transition SFX (whooshes, waves, street ambience).
- **OpenGameArt** (`opengameart.org`) — CC0 / CC-BY instrumental loops and acoustic tracks.

## Option B: Custom `lyria-3.5` Previews (`$0.08`/track)
When the `gemini-api` plugin is enabled and the user opts into paid previews, generate 2–3 short instrumental auditions via `gemini-api music` (`lyria-3.5`) matched to the trip's mood and target BPM:
- Example prompt dimensions: tempo (e.g., `95 BPM` acoustic indie travel, `115 BPM` uplifting synth-folk, `72 BPM` warm Rhodes & cello).
- Present each generated preview in the Gate 3 BGM Picker Artifact with its exact cost (`$0.08`/track) before generating a full-length bed.

## Option C: User-Supplied Local Track (`$0`)
Accept any local `.mp3`, `.wav`, `.m4a`, or `.aac` file provided by the user. Never modify the original audio file; analyze it read-only with `scripts/bgm_analyzer.py` to generate `./work/beat_map.json`.
