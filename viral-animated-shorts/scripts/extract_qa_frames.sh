#!/usr/bin/env bash
set -euo pipefail

# Extract sampled QA frames (exact frame0, start, mid, action peak ~72%, true end) plus optional previous shot end-frame.
# Uses exact frame-0 select + -sseof -0.05 (with -0.2 fallback) so Frame 0 hooks and final-frame loop/fade continuity are genuinely audited.
# Usage: extract_qa_frames.sh <clip.mp4> <out_dir> [prev_clip.mp4]

usage() {
  cat <<EOF
Extract sampled QA frames (frame0, start, mid, action peak, end) from a video clip, plus an optional previous clip end frame.

Usage: $0 <clip.mp4> <out_dir> [prev_clip.mp4]
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage >&2
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "Error: ffmpeg and ffprobe must be installed and on PATH." >&2
  exit 1
fi

CLIP="$1"
OUT_DIR="$2"
PREV_CLIP="${3:-}"

if [[ ! -f "$CLIP" ]]; then
  echo "Error: clip not found: $CLIP" >&2
  exit 1
fi
if [[ -n "$PREV_CLIP" && ! -f "$PREV_CLIP" ]]; then
  echo "Error: previous clip not found: $PREV_CLIP" >&2
  exit 1
fi

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

# Exact Frame 0 for Frame-0 hook / crop / cold-open verification
ffmpeg -y -v error -i "$CLIP" -vf "select=eq(n\,0)" -frames:v 1 "$OUT_DIR/f_frame0.png"

extract_at_ts() {
  local ts="$1"
  local out_png="$2"
  rm -f "$out_png"
  ffmpeg -y -v error -ss "$ts" -i "$CLIP" -frames:v 1 "$out_png" 2>/dev/null || true
  if [[ ! -s "$out_png" ]]; then
    ffmpeg -y -v error -i "$CLIP" -ss "$ts" -frames:v 1 "$out_png" 2>/dev/null || true
  fi
  if [[ ! -s "$out_png" && -s "$OUT_DIR/f_frame0.png" ]]; then
    cp "$OUT_DIR/f_frame0.png" "$out_png"
  fi
}

extract_at_ts "$T_START" "$OUT_DIR/f_start.png"
extract_at_ts "$T_MID" "$OUT_DIR/f_mid.png"
extract_at_ts "$T_PEAK" "$OUT_DIR/f_peak.png"

# Sample true end frame via -sseof -0.05 (with -sseof -0.2 and -ss fallbacks for tiny/unusual clips)
rm -f "$OUT_DIR/f_end.png"
ffmpeg -y -v error -sseof -0.05 -i "$CLIP" -frames:v 1 "$OUT_DIR/f_end.png" 2>/dev/null || true
if [[ ! -s "$OUT_DIR/f_end.png" ]]; then
  ffmpeg -y -v error -sseof -0.2 -i "$CLIP" -frames:v 1 "$OUT_DIR/f_end.png" 2>/dev/null || true
fi
if [[ ! -s "$OUT_DIR/f_end.png" ]]; then
  T_END_FALLBACK=$(awk -v d="$DUR" 'BEGIN { v = d - 0.5; if (v < 0.05) v = (d > 0.05 ? d * 0.85 : 0.0); printf "%.2f", v }')
  ffmpeg -y -v error -ss "$T_END_FALLBACK" -i "$CLIP" -frames:v 1 "$OUT_DIR/f_end.png"
fi

for f in "$OUT_DIR/f_frame0.png" "$OUT_DIR/f_start.png" "$OUT_DIR/f_mid.png" "$OUT_DIR/f_peak.png" "$OUT_DIR/f_end.png"; do
  if [[ ! -s "$f" ]]; then
    echo "Error: extracted frame is missing or empty: $f" >&2
    exit 1
  fi
done

if [[ -n "$PREV_CLIP" ]]; then
  rm -f "$OUT_DIR/prev_end.png"
  ffmpeg -y -v error -sseof -0.05 -i "$PREV_CLIP" -frames:v 1 "$OUT_DIR/prev_end.png" 2>/dev/null || true
  if [[ ! -s "$OUT_DIR/prev_end.png" ]]; then
    ffmpeg -y -v error -sseof -0.2 -i "$PREV_CLIP" -frames:v 1 "$OUT_DIR/prev_end.png" 2>/dev/null || true
  fi
  if [[ ! -s "$OUT_DIR/prev_end.png" ]]; then
    ffmpeg -y -v error -ss 0.05 -i "$PREV_CLIP" -frames:v 1 "$OUT_DIR/prev_end.png"
  fi
  if [[ ! -s "$OUT_DIR/prev_end.png" ]]; then
    echo "Error: extracted previous end frame is missing or empty: $OUT_DIR/prev_end.png" >&2
    exit 1
  fi
fi

echo "Extracted QA frames to $OUT_DIR (video_duration=${DUR}s)"
