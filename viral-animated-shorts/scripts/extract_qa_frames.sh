#!/usr/bin/env bash
set -euo pipefail

# Extract sampled QA frames (start, mid, action peak ~72%, end) plus optional previous shot end-frame.
# Uses video-stream duration + -sseof to avoid empty f_end.png caused by AAC audio padding.
# Usage: extract_qa_frames.sh <clip.mp4> <out_dir> [prev_clip.mp4]

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 <clip.mp4> <out_dir> [prev_clip.mp4]" >&2
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "Error: ffmpeg and ffprobe must be installed and on PATH." >&2
  exit 1
fi

CLIP="$1"
OUT_DIR="$2"
PREV_CLIP="${3:-}"

mkdir -p "$OUT_DIR"

get_video_duration() {
  local file="$1"
  local vdur
  vdur=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=noprint_wrappers=1:nokey=1 "$file" | head -n 1 || true)
  if [[ -z "$vdur" || "$vdur" == "N/A" ]]; then
    vdur=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$file" | head -n 1 || true)
  fi
  echo "$vdur"
}

DUR=$(get_video_duration "$CLIP")
if [[ -z "$DUR" || "$DUR" == "N/A" ]]; then
  echo "Error: could not determine video duration for $CLIP" >&2
  exit 1
fi

T_START=$(awk -v d="$DUR" 'BEGIN { v = (d > 0.6 ? 0.25 : d * 0.15); printf "%.2f", v }')
T_MID=$(awk -v d="$DUR" 'BEGIN { printf "%.2f", d * 0.45 }')
T_PEAK=$(awk -v d="$DUR" 'BEGIN { printf "%.2f", d * 0.72 }')

ffmpeg -y -v error -ss "$T_START" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_start.png"
ffmpeg -y -v error -ss "$T_MID" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_mid.png"
ffmpeg -y -v error -ss "$T_PEAK" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_peak.png"

# Use -sseof -0.4 so AAC audio padding never causes an empty EOF seek on f_end.png
if awk -v d="$DUR" 'BEGIN { exit !(d > 0.5) }'; then
  ffmpeg -y -v error -sseof -0.4 -i "$CLIP" -frames:v 1 "$OUT_DIR/f_end.png"
else
  T_END=$(awk -v d="$DUR" 'BEGIN { v = d * 0.85; printf "%.2f", v }')
  ffmpeg -y -v error -ss "$T_END" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_end.png"
fi

# Fallback if -sseof still missed on an unusual container
if [[ ! -s "$OUT_DIR/f_end.png" ]]; then
  T_END_FALLBACK=$(awk -v d="$DUR" 'BEGIN { v = d - 0.5; if (v < 0.05) v = 0.05; printf "%.2f", v }')
  ffmpeg -y -v error -ss "$T_END_FALLBACK" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_end.png"
fi

for f in "$OUT_DIR/f_start.png" "$OUT_DIR/f_mid.png" "$OUT_DIR/f_peak.png" "$OUT_DIR/f_end.png"; do
  if [[ ! -s "$f" ]]; then
    echo "Error: extracted frame is missing or empty: $f" >&2
    exit 1
  fi
done

if [[ -n "$PREV_CLIP" ]]; then
  PREV_DUR=$(get_video_duration "$PREV_CLIP")
  if awk -v d="$PREV_DUR" 'BEGIN { exit !(d > 0.45) }'; then
    ffmpeg -y -v error -sseof -0.35 -i "$PREV_CLIP" -frames:v 1 "$OUT_DIR/prev_end.png"
  else
    ffmpeg -y -v error -ss 0.05 -i "$PREV_CLIP" -frames:v 1 "$OUT_DIR/prev_end.png"
  fi
  if [[ ! -s "$OUT_DIR/prev_end.png" ]]; then
    echo "Error: extracted previous end frame is missing or empty: $OUT_DIR/prev_end.png" >&2
    exit 1
  fi
fi

echo "Extracted QA frames to $OUT_DIR (video_duration=${DUR}s)"
