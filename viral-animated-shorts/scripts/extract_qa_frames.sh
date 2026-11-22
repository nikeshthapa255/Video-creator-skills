#!/usr/bin/env bash
set -euo pipefail

# Extract sampled QA frames (start, mid, action peak ~70%, end) plus optional previous shot end-frame.
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

DUR=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$CLIP")
T_START="0.25"
T_MID=$(awk -v d="$DUR" 'BEGIN { printf "%.2f", d * 0.45 }')
T_PEAK=$(awk -v d="$DUR" 'BEGIN { printf "%.2f", d * 0.72 }')
T_END=$(awk -v d="$DUR" 'BEGIN { v = d - 0.2; if (v < 0.1) v = 0.1; printf "%.2f", v }')

ffmpeg -y -v error -ss "$T_START" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_start.png"
ffmpeg -y -v error -ss "$T_MID" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_mid.png"
ffmpeg -y -v error -ss "$T_PEAK" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_peak.png"
ffmpeg -y -v error -ss "$T_END" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_end.png"

if [[ -n "$PREV_CLIP" ]]; then
  PREV_DUR=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$PREV_CLIP")
  PREV_END=$(awk -v d="$PREV_DUR" 'BEGIN { v = d - 0.15; if (v < 0.05) v = 0.05; printf "%.2f", v }')
  ffmpeg -y -v error -ss "$PREV_END" -i "$PREV_CLIP" -frames:v 1 "$OUT_DIR/prev_end.png"
fi

echo "Extracted QA frames to $OUT_DIR (duration=${DUR}s)"
