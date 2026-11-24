#!/usr/bin/env bash
set -euo pipefail

# Slice a finished long-form MP4 (3-30+ min, 9:16 or 16:9) into lossless N-second review chunks
# (qa_min1.mp4, qa_min2.mp4, ...) well under 100 MB for multimodal view_file QA, extract evenly spaced
# keyframe screenshots, measure exact integrated LUFS, and optionally emit YouTube-ready chapters.txt.
#
# Usage:
#   split_qa_segments.sh <final_video.mp4> <out_dir> [--segment-sec 60] [--screenshots 10] [--timings shot_timings.json]

usage() {
  cat <<EOF
Slice a finished long-form video MP4 into lossless QA review segments, extract screenshots,
measure integrated LUFS, and optionally generate YouTube chapters from shot_timings.json.

Usage:
  $0 <final_video.mp4> <out_dir> [--segment-sec 60] [--screenshots 10] [--timings shot_timings.json]

Options:
  --segment-sec N    Duration of each lossless review chunk in seconds (default: 60)
  --screenshots N    Number of evenly spaced keyframe screenshots to extract (default: 10, 0 to skip)
  --timings FILE     Optional shot_timings.json; if it contains 'acts', writes <out_dir>/chapters.txt
  -h, --help         Show this help message
EOF
}

SEGMENT_SEC=60
SCREENSHOTS=10
TIMINGS_JSON=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --segment-sec)
      if [[ $# -lt 2 ]]; then
        echo "Error: --segment-sec requires a value." >&2
        exit 2
      fi
      SEGMENT_SEC="$2"
      shift 2
      ;;
    --segment-sec=*)
      SEGMENT_SEC="${1#*=}"
      shift
      ;;
    --screenshots)
      if [[ $# -lt 2 ]]; then
        echo "Error: --screenshots requires a value." >&2
        exit 2
      fi
      SCREENSHOTS="$2"
      shift 2
      ;;
    --screenshots=*)
      SCREENSHOTS="${1#*=}"
      shift
      ;;
    --timings)
      if [[ $# -lt 2 ]]; then
        echo "Error: --timings requires a file path." >&2
        exit 2
      fi
      TIMINGS_JSON="$2"
      shift 2
      ;;
    --timings=*)
      TIMINGS_JSON="${1#*=}"
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL+=("$1")
        shift
      done
      ;;
    -*)
      echo "Error: unknown option '$1'" >&2
      usage >&2
      exit 2
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ "${#POSITIONAL[@]}" -ne 2 ]]; then
  usage >&2
  exit 2
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "Error: ffmpeg and ffprobe must be installed and on PATH." >&2
  exit 1
fi

IN_MP4="${POSITIONAL[0]}"
OUT_DIR="${POSITIONAL[1]}"

if [[ ! -f "$IN_MP4" ]]; then
  echo "Error: input video not found: $IN_MP4" >&2
  exit 1
fi

if ! [[ "$SEGMENT_SEC" =~ ^[0-9]+$ ]] || (( SEGMENT_SEC < 1 )); then
  echo "Error: --segment-sec must be a positive integer (got '$SEGMENT_SEC')." >&2
  exit 2
fi

if ! [[ "$SCREENSHOTS" =~ ^[0-9]+$ ]]; then
  echo "Error: --screenshots must be a non-negative integer (got '$SCREENSHOTS')." >&2
  exit 2
fi

if [[ -n "$TIMINGS_JSON" && ! -f "$TIMINGS_JSON" ]]; then
  echo "Error: timings file not found: $TIMINGS_JSON" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

# Probe video dimensions, FPS, codec, and duration
DURATION=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$IN_MP4")
if [[ -z "$DURATION" ]]; then
  echo "Error: failed to probe duration of $IN_MP4" >&2
  exit 1
fi

V_META=$(ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,r_frame_rate \
  -of csv=p=0 "$IN_MP4" | head -n 1 || true)
IFS=',' read -r V_CODEC V_WIDTH V_HEIGHT V_FPS <<< "$V_META"
echo "Video Info: $IN_MP4 (codec=${V_CODEC:-unknown}, size=${V_WIDTH:-?}x${V_HEIGHT:-?}, fps=${V_FPS:-?}, duration=${DURATION}s)"

# Slice into lossless N-second segments with -c copy (qa_min1.mp4 .. qa_minK.mp4)
SEG_INDEX=1
START_SEC=0
while awk -v s="$START_SEC" -v d="$DURATION" 'BEGIN { exit !(s < d - 0.05) }'; do
  SEG_PATH="$OUT_DIR/qa_min${SEG_INDEX}.mp4"
  ffmpeg -y -v error -ss "$START_SEC" -t "$SEGMENT_SEC" -i "$IN_MP4" \
    -c copy -avoid_negative_ts make_zero "$SEG_PATH"
  SEG_DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$SEG_PATH")
  SEG_BYTES=$(wc -c < "$SEG_PATH")
  SEG_MB=$(awk -v b="$SEG_BYTES" 'BEGIN { printf "%.2f", b / 1048576.0 }')
  echo "Segment $SEG_INDEX: $SEG_PATH (start=${START_SEC}s, dur=${SEG_DUR}s, size=${SEG_MB} MB)"
  if awk -v b="$SEG_BYTES" 'BEGIN { exit !(b >= 95 * 1048576) }'; then
    echo "WARNING: $SEG_PATH is ${SEG_MB} MB (near/over 100 MB view_file limit); re-run with a smaller --segment-sec." >&2
  fi
  SEG_INDEX=$((SEG_INDEX + 1))
  START_SEC=$((START_SEC + SEGMENT_SEC))
done

# Extract evenly spaced keyframe screenshots across the full duration
if (( SCREENSHOTS > 0 )); then
  FRAMES_DIR="$OUT_DIR/screenshots"
  mkdir -p "$FRAMES_DIR"
  PAD_WIDTH=2
  if (( SCREENSHOTS >= 100 )); then
    PAD_WIDTH="${#SCREENSHOTS}"
  fi
  for i in $(seq 1 "$SCREENSHOTS"); do
    TS=$(awk -v d="$DURATION" -v idx="$i" -v n="$SCREENSHOTS" 'BEGIN { printf "%.3f", d * (idx - 0.5) / n }')
    SHOT_PNG="$FRAMES_DIR/frame_$(printf "%0${PAD_WIDTH}d" "$i").png"
    ffmpeg -y -v error -ss "$TS" -i "$IN_MP4" -frames:v 1 -q:v 2 "$SHOT_PNG"
  done
  echo "Extracted $SCREENSHOTS screenshots into $FRAMES_DIR"
fi

# Measure exact integrated loudness
HAS_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$IN_MP4" | head -n 1 || true)
if [[ -n "$HAS_AUDIO" ]]; then
  STATS=$(ffmpeg -i "$IN_MP4" -af loudnorm=I=-14:TP=-1.0:LRA=7:print_format=json -f null - 2>&1)
  M_I=$(echo "$STATS" | awk -F'"' '/"input_i"/ {print $4}')
  M_TP=$(echo "$STATS" | awk -F'"' '/"input_tp"/ {print $4}')
  M_LRA=$(echo "$STATS" | awk -F'"' '/"input_lra"/ {print $4}')
  M_THRESH=$(echo "$STATS" | awk -F'"' '/"input_thresh"/ {print $4}')
  echo "QA Loudness Summary for $IN_MP4 (${V_WIDTH:-?}x${V_HEIGHT:-?} @ ${V_FPS:-?} fps, duration=${DURATION}s):"
  echo "  input_i=${M_I} LUFS, input_tp=${M_TP} dBTP, input_lra=${M_LRA} LU, input_thresh=${M_THRESH} LUFS"
else
  echo "QA Loudness Summary for $IN_MP4 (${V_WIDTH:-?}x${V_HEIGHT:-?} @ ${V_FPS:-?} fps, duration=${DURATION}s): no audio stream"
fi

# Emit YouTube-ready chapters.txt if --timings is provided and contains 'acts'
if [[ -n "$TIMINGS_JSON" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Warning: python3 not found; skipping chapters.txt generation." >&2
  else
    python3 - "$TIMINGS_JSON" "$OUT_DIR/chapters.txt" <<'PY'
import json
import sys
from pathlib import Path

timings_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])

data = json.loads(timings_path.read_text(encoding="utf-8"))
acts = data.get("acts") if isinstance(data, dict) else None
if not isinstance(acts, list) or not acts:
    print(f"Note: no 'acts' found in {timings_path}; skipping chapters.txt.")
    raise SystemExit(0)

def fmt_ts(sec: float, use_hours: bool) -> str:
    total = max(0, int(round(sec)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if use_hours or h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

max_sec = max(float(a.get("start_sec", 0.0)) for a in acts if isinstance(a, dict))
use_hours = max_sec >= 3600.0

lines = []
for idx, entry in enumerate(acts, start=1):
    if not isinstance(entry, dict):
        continue
    start_sec = 0.0 if idx == 1 else float(entry.get("start_sec", 0.0))
    act_num = entry.get("act", entry.get("act_number", entry.get("chapter", idx)))
    raw_title = entry.get("title") or entry.get("name") or entry.get("label") or ""
    raw_title = str(raw_title).strip()
    if raw_title:
        if raw_title.lower().startswith(("act ", "chapter ")):
            title = raw_title
        else:
            title = f"Act {act_num}: {raw_title}"
    else:
        title = f"Act {act_num}"
    lines.append(f"{fmt_ts(start_sec, use_hours)} {title}")

if lines:
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} YouTube chapter(s) to {out_path}:")
    for ln in lines:
        print(f"  {ln}")
PY
  fi
fi
