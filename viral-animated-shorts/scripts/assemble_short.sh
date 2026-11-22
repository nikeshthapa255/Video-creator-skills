#!/usr/bin/env bash
set -euo pipefail

# Deterministic v2 Shorts assembler:
#   ASS overlap gate -> probe & conform (1080x1920, 48kHz stereo) -> concat ->
#   3-stem audio mix (clean VO/native + non-ducked SFX + sidechain-ducked BGM) ->
#   safe-zone ASS caption burn -> 2-pass -14 LUFS loudnorm -> ffprobe verify.
#
# Usage:
#   assemble_short.sh <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav|none] [bgm.wav|none] [sfx.wav|none]
#
# Environment:
#   SHORTS_FPS  Output framerate (default: 30)

if [[ $# -lt 3 || $# -gt 6 ]]; then
  echo "Usage: $0 <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav|none] [bgm.wav|none] [sfx.wav|none]" >&2
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "Error: ffmpeg and ffprobe must be installed and on PATH." >&2
  exit 1
fi

CLIPS_DIR="$1"
CAPTIONS="$2"
OUT_MP4="$3"
VO_WAV="${4:-none}"
BGM_WAV="${5:-none}"
SFX_WAV="${6:-none}"
SHORTS_FPS="${SHORTS_FPS:-30}"

# Fail loudly if ASS captions have overlapping Dialogue lines (CAPTION_OVERLAP gate)
check_ass_overlaps() {
  local ass_file="$1"
  awk -F',' '
    function to_cs(ts,   a, b) {
      split(ts, a, ":");
      split(a[3], b, ".");
      return (a[1] * 3600 + a[2] * 60 + b[1]) * 100 + b[2];
    }
    /^Dialogue:/ {
      s = to_cs($2);
      e = to_cs($3);
      if (e < s) {
        printf "CAPTION_OVERLAP: invalid event (end < start) at line %d: %s -> %s\n", NR, $2, $3 > "/dev/stderr";
        bad = 1;
      }
      if (has_prev && s < prev_e) {
        printf "CAPTION_OVERLAP: line %d (%s -> %s) overlaps previous end (%s)\n", NR, $2, $3, prev_ts > "/dev/stderr";
        bad = 1;
      }
      prev_e = e;
      prev_ts = $3;
      has_prev = 1;
    }
    END { exit bad ? 1 : 0 }
  ' "$ass_file"
}

if [[ "$CAPTIONS" != "none" ]]; then
  if [[ ! -f "$CAPTIONS" ]]; then
    echo "Error: captions file not found: $CAPTIONS" >&2
    exit 1
  fi
  check_ass_overlaps "$CAPTIONS"
fi

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

CONCAT_LIST="$WORK_DIR/concat.txt"
: > "$CONCAT_LIST"

idx=0
for clip in "$CLIPS_DIR"/*.mp4; do
  [[ -e "$clip" ]] || continue
  idx=$((idx + 1))
  norm="$WORK_DIR/norm_$(printf '%03d' "$idx").mp4"
  has_audio=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$clip" | head -n 1 || true)
  # Conform every clip to 1080x1920, uniform FPS, 48kHz stereo (inject silent track if clip has no audio)
  if [[ -n "$has_audio" ]]; then
    ffmpeg -y -v error -i "$clip" \
      -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=${SHORTS_FPS}" \
      -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
      -c:a aac -ar 48000 -ac 2 -b:a 192k "$norm"
  else
    ffmpeg -y -v error -i "$clip" -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
      -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=${SHORTS_FPS}" \
      -map 0:v:0 -map 1:a:0 -shortest \
      -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
      -c:a aac -ar 48000 -ac 2 -b:a 192k "$norm"
  fi
  printf "file '%s'\n" "$norm" >> "$CONCAT_LIST"
done

if [[ "$idx" -eq 0 ]]; then
  echo "Error: No .mp4 clips found in $CLIPS_DIR" >&2
  exit 1
fi

STITCHED="$WORK_DIR/stitched.mp4"
ffmpeg -y -v error -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$STITCHED"

# 3-stem audio mix:
#   Primary (VO_WAV if provided, else stitched native audio) — clean on top
#   SFX_WAV (if provided) — non-ducked comedic punch on top
#   BGM_WAV (if provided) — looped & sidechain-ducked under primary audio
HAS_VO=0;  [[ -n "$VO_WAV"  && "$VO_WAV"  != "none" ]] && HAS_VO=1
HAS_BGM=0; [[ -n "$BGM_WAV" && "$BGM_WAV" != "none" ]] && HAS_BGM=1
HAS_SFX=0; [[ -n "$SFX_WAV" && "$SFX_WAV" != "none" ]] && HAS_SFX=1

MIXED="$STITCHED"
if [[ "$HAS_VO" -eq 1 || "$HAS_BGM" -eq 1 || "$HAS_SFX" -eq 1 ]]; then
  MIXED="$WORK_DIR/mixed.mp4"
  FF_IN=(-i "$STITCHED")
  NEXT_IDX=1

  if [[ "$HAS_VO" -eq 1 ]]; then
    FF_IN+=(-i "$VO_WAV")
    PRI_LABEL="${NEXT_IDX}:a"
    NEXT_IDX=$((NEXT_IDX + 1))
    BGM_ATTEN="-16dB"
  else
    PRI_LABEL="0:a"
    BGM_ATTEN="-18dB"
  fi

  BGM_IDX=""
  if [[ "$HAS_BGM" -eq 1 ]]; then
    FF_IN+=(-stream_loop -1 -i "$BGM_WAV")
    BGM_IDX="$NEXT_IDX"
    NEXT_IDX=$((NEXT_IDX + 1))
  fi

  SFX_IDX=""
  if [[ "$HAS_SFX" -eq 1 ]]; then
    FF_IN+=(-i "$SFX_WAV")
    SFX_IDX="$NEXT_IDX"
    NEXT_IDX=$((NEXT_IDX + 1))
  fi

  if [[ "$HAS_BGM" -eq 1 && "$HAS_SFX" -eq 1 ]]; then
    FILTER="[${PRI_LABEL}]asplit=2[sc][pri];[${BGM_IDX}:a]volume=${BGM_ATTEN}[bgm];[bgm][sc]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=250[duck];[pri][${SFX_IDX}:a][duck]amix=inputs=3:duration=first:normalize=0[aout]"
  elif [[ "$HAS_BGM" -eq 1 ]]; then
    FILTER="[${PRI_LABEL}]asplit=2[sc][pri];[${BGM_IDX}:a]volume=${BGM_ATTEN}[bgm];[bgm][sc]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=250[duck];[pri][duck]amix=inputs=2:duration=first:normalize=0[aout]"
  elif [[ "$HAS_SFX" -eq 1 ]]; then
    FILTER="[${PRI_LABEL}][${SFX_IDX}:a]amix=inputs=2:duration=first:normalize=0[aout]"
  else
    FILTER="[${PRI_LABEL}]acopy[aout]"
  fi

  ffmpeg -y -v error "${FF_IN[@]}" \
    -filter_complex "$FILTER" \
    -map 0:v:0 -map "[aout]" -c:v copy -c:a aac -ar 48000 -ac 2 -b:a 192k -shortest "$MIXED"
fi

CAPTIONED="$MIXED"
if [[ "$CAPTIONS" != "none" && -f "$CAPTIONS" ]]; then
  CAPTIONED="$WORK_DIR/captioned.mp4"
  cp "$CAPTIONS" "$WORK_DIR/captions.ass"
  ffmpeg -y -v error -i "$MIXED" -vf "ass=$WORK_DIR/captions.ass" \
    -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -c:a copy "$CAPTIONED"
fi

# Pass 1: measure loudness
STATS=$(ffmpeg -i "$CAPTIONED" -af loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json -f null - 2>&1)
M_I=$(echo "$STATS" | awk -F'"' '/"input_i"/ {print $4}')
M_TP=$(echo "$STATS" | awk -F'"' '/"input_tp"/ {print $4}')
M_LRA=$(echo "$STATS" | awk -F'"' '/"input_lra"/ {print $4}')
M_THRESH=$(echo "$STATS" | awk -F'"' '/"input_thresh"/ {print $4}')
M_OFFSET=$(echo "$STATS" | awk -F'"' '/"target_offset"/ {print $4}')

mkdir -p "$(dirname "$OUT_MP4")"
# Pass 2: linear loudness normalization (skip if input is pure silence)
if [[ "$M_I" == "-inf" || -z "$M_I" ]]; then
  cp "$CAPTIONED" "$OUT_MP4"
else
  ffmpeg -y -v error -i "$CAPTIONED" \
    -af "loudnorm=I=-14:TP=-1.0:LRA=11:measured_I=${M_I}:measured_TP=${M_TP}:measured_LRA=${M_LRA}:measured_thresh=${M_THRESH}:offset=${M_OFFSET}:linear=true" \
    -c:v copy -c:a aac -b:a 192k -ar 48000 "$OUT_MP4"
fi

echo "Assembled $OUT_MP4 (3-stem: vo=${HAS_VO}, bgm=${HAS_BGM}, sfx=${HAS_SFX})"
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate:format=duration -of compact "$OUT_MP4"
