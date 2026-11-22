#!/usr/bin/env bash
set -euo pipefail

# Deterministic probe -> conform -> concat -> optional VO/BGM duck -> ASS captions -> 2-pass -14 LUFS assembly.
# Usage: assemble_short.sh <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav] [bgm.wav]

if [[ $# -lt 3 || $# -gt 5 ]]; then
  echo "Usage: $0 <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav] [bgm.wav]" >&2
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "Error: ffmpeg and ffprobe must be installed and on PATH." >&2
  exit 1
fi

CLIPS_DIR="$1"
CAPTIONS="$2"
OUT_MP4="$3"
VO_WAV="${4:-}"
BGM_WAV="${5:-}"

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
  # Conform every clip to 1080x1920, 24fps, 48kHz stereo (inject silent track if clip has no audio)
  if [[ -n "$has_audio" ]]; then
    ffmpeg -y -v error -i "$clip" \
      -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=24" \
      -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
      -c:a aac -ar 48000 -ac 2 -b:a 192k "$norm"
  else
    ffmpeg -y -v error -i "$clip" -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
      -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=24" \
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

MIXED="$STITCHED"
if [[ -n "$VO_WAV" && "$VO_WAV" != "none" && -n "$BGM_WAV" && "$BGM_WAV" != "none" ]]; then
  MIXED="$WORK_DIR/mixed.mp4"
  ffmpeg -y -v error -i "$STITCHED" -stream_loop -1 -i "$BGM_WAV" -i "$VO_WAV" \
    -filter_complex "[2:a]asplit=2[sc][vox];[1:a]volume=-16dB[bgm];[bgm][sc]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=250[duck];[vox][duck]amix=inputs=2:duration=first:normalize=0[aout]" \
    -map 0:v:0 -map "[aout]" -c:v copy -c:a aac -ar 48000 -b:a 192k -shortest "$MIXED"
elif [[ -n "$VO_WAV" && "$VO_WAV" != "none" ]]; then
  MIXED="$WORK_DIR/mixed.mp4"
  ffmpeg -y -v error -i "$STITCHED" -i "$VO_WAV" \
    -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -ar 48000 -b:a 192k -shortest "$MIXED"
elif [[ -n "$BGM_WAV" && "$BGM_WAV" != "none" ]]; then
  MIXED="$WORK_DIR/mixed.mp4"
  ffmpeg -y -v error -i "$STITCHED" -stream_loop -1 -i "$BGM_WAV" \
    -filter_complex "[0:a]asplit=2[sc][nat];[1:a]volume=-18dB[bgm];[bgm][sc]sidechaincompress=threshold=0.02:ratio=5:attack=15:release=250[duck];[nat][duck]amix=inputs=2:duration=first:normalize=0[aout]" \
    -map 0:v:0 -map "[aout]" -c:v copy -c:a aac -ar 48000 -b:a 192k -shortest "$MIXED"
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

echo "Assembled $OUT_MP4"
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate:format=duration -of compact "$OUT_MP4"
