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

HAS_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$IN_MP4" | head -n 1 || true)
QA_LRA=7
if [[ "${V_WIDTH:-0}" =~ ^[0-9]+$ && "${V_HEIGHT:-0}" =~ ^[0-9]+$ ]] && (( V_WIDTH > V_HEIGHT )); then
  QA_LRA=9
fi

is_finite_num() {
  awk -v v="$1" 'BEGIN { exit !(v ~ /^-?[0-9]+(\.[0-9]+)?$/) }'
}

# Slice into lossless N-second segments with -c copy (qa_min1.mp4 .. qa_minK.mp4)
SEG_INDEX=1
START_SEC=0
SEG_LUFS_COUNT=0
MIN_SEG_I=""
MIN_SEG_IDX=""
MAX_SEG_I=""
MAX_SEG_IDX=""
MAX_SEG_TP=""

while awk -v s="$START_SEC" -v d="$DURATION" 'BEGIN { exit !(s < d - 0.05) }'; do
  SEG_PATH="$OUT_DIR/qa_min${SEG_INDEX}.mp4"
  ffmpeg -y -v error -ss "$START_SEC" -t "$SEGMENT_SEC" -i "$IN_MP4" \
    -c copy -avoid_negative_ts make_zero "$SEG_PATH"
  SEG_DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$SEG_PATH")
  SEG_BYTES=$(wc -c < "$SEG_PATH")
  SEG_MB=$(awk -v b="$SEG_BYTES" 'BEGIN { printf "%.2f", b / 1048576.0 }')

  SEG_LOUD_INFO=""
  if [[ -n "$HAS_AUDIO" ]]; then
    SEG_STATS=$(ffmpeg -i "$SEG_PATH" -af "loudnorm=I=-14:TP=-1.0:LRA=${QA_LRA}:print_format=json" -f null - 2>&1)
    SEG_I=$(echo "$SEG_STATS" | awk -F'"' '/"input_i"/ {print $4}')
    SEG_TP=$(echo "$SEG_STATS" | awk -F'"' '/"input_tp"/ {print $4}')
    SEG_LOUD_INFO=", input_i=${SEG_I:-n/a} LUFS, input_tp=${SEG_TP:-n/a} dBTP"
    if is_finite_num "${SEG_I:-}"; then
      SEG_LUFS_COUNT=$((SEG_LUFS_COUNT + 1))
      if [[ -z "$MIN_SEG_I" ]] || awk -v a="$SEG_I" -v b="$MIN_SEG_I" 'BEGIN { exit !(a < b) }'; then
        MIN_SEG_I="$SEG_I"
        MIN_SEG_IDX="$SEG_INDEX"
      fi
      if [[ -z "$MAX_SEG_I" ]] || awk -v a="$SEG_I" -v b="$MAX_SEG_I" 'BEGIN { exit !(a > b) }'; then
        MAX_SEG_I="$SEG_I"
        MAX_SEG_IDX="$SEG_INDEX"
      fi
    fi
    if is_finite_num "${SEG_TP:-}"; then
      if [[ -z "$MAX_SEG_TP" ]] || awk -v a="$SEG_TP" -v b="$MAX_SEG_TP" 'BEGIN { exit !(a > b) }'; then
        MAX_SEG_TP="$SEG_TP"
      fi
    fi
  fi

  echo "Segment $SEG_INDEX: $SEG_PATH (start=${START_SEC}s, dur=${SEG_DUR}s, size=${SEG_MB} MB${SEG_LOUD_INFO})"
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

# Measure exact integrated loudness (full file + per-segment min/max)
if [[ -n "$HAS_AUDIO" ]]; then
  STATS=$(ffmpeg -i "$IN_MP4" -af "loudnorm=I=-14:TP=-1.0:LRA=${QA_LRA}:print_format=json" -f null - 2>&1)
  M_I=$(echo "$STATS" | awk -F'"' '/"input_i"/ {print $4}')
  M_TP=$(echo "$STATS" | awk -F'"' '/"input_tp"/ {print $4}')
  M_LRA=$(echo "$STATS" | awk -F'"' '/"input_lra"/ {print $4}')
  M_THRESH=$(echo "$STATS" | awk -F'"' '/"input_thresh"/ {print $4}')
  echo "QA Loudness Summary for $IN_MP4 (${V_WIDTH:-?}x${V_HEIGHT:-?} @ ${V_FPS:-?} fps, duration=${DURATION}s):"
  echo "  full_file:   input_i=${M_I} LUFS, input_tp=${M_TP} dBTP, input_lra=${M_LRA} LU, input_thresh=${M_THRESH} LUFS"
  if (( SEG_LUFS_COUNT > 0 )); then
    echo "  per_segment: min_i=${MIN_SEG_I} LUFS (seg ${MIN_SEG_IDX}), max_i=${MAX_SEG_I} LUFS (seg ${MAX_SEG_IDX}), max_tp=${MAX_SEG_TP:-n/a} dBTP (${SEG_LUFS_COUNT} segment(s))"
  else
    echo "  per_segment: no finite loudness readings (silent segments)"
  fi
else
  echo "QA Loudness Summary for $IN_MP4 (${V_WIDTH:-?}x${V_HEIGHT:-?} @ ${V_FPS:-?} fps, duration=${DURATION}s): no audio stream"
fi

# Emit YouTube-ready chapters.txt if --timings is provided and contains 'acts'
if [[ -n "$TIMINGS_JSON" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Warning: python3 not found; skipping chapters.txt generation." >&2
  else
    python3 - "$TIMINGS_JSON" "$OUT_DIR/chapters.txt" "${V_WIDTH:-0}" "${V_HEIGHT:-0}" "${DURATION:-0}" <<'PY'
import json
import sys
from pathlib import Path

timings_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
try:
    vid_w = int(sys.argv[3])
except ValueError:
    vid_w = 0
try:
    vid_h = int(sys.argv[4])
except ValueError:
    vid_h = 0
try:
    vid_dur = float(sys.argv[5])
except ValueError:
    vid_dur = 0.0

data = json.loads(timings_path.read_text(encoding="utf-8"))
acts = data.get("acts") if isinstance(data, dict) else None
if not isinstance(acts, list) or not acts:
    print(f"Note: no 'acts' found in {timings_path}; skipping chapters.txt.")
    raise SystemExit(0)

valid_acts = [a for a in acts if isinstance(a, dict)]
if not valid_acts:
    print(f"Note: no valid act objects found in {timings_path}; skipping chapters.txt.")
    raise SystemExit(0)

# Sort acts chronologically by start_sec (stable sort preserves order on ties)
valid_acts.sort(key=lambda a: float(a.get("start_sec", 0.0)))

def fmt_ts(sec: float, use_hours: bool) -> str:
    total = max(0, int(round(sec)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if use_hours or h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

max_sec = max((float(a.get("start_sec", 0.0)) for a in valid_acts), default=0.0)
use_hours = max_sec >= 3600.0

ar = str(data.get("aspect_ratio") or "").strip() if isinstance(data, dict) else ""
is_widescreen = (ar == "16:9") or (vid_w > vid_h and ar != "9:16")

chapters = []
for idx, entry in enumerate(valid_acts, start=1):
    start_sec = 0.0 if idx == 1 else float(entry.get("start_sec", 0.0))
    act_num = entry.get("act", entry.get("act_number", entry.get("chapter", idx)))
    raw_title = entry.get("title") or entry.get("name") or entry.get("label") or ""
    raw_title = str(raw_title).strip()
    if is_widescreen:
        title = raw_title if raw_title else f"Act {act_num}"
    else:
        if raw_title:
            if raw_title.lower().startswith(("act ", "chapter ")):
                title = raw_title
            else:
                title = f"Act {act_num}: {raw_title}"
        else:
            title = f"Act {act_num}"
    chapters.append((start_sec, title))

# Validate YouTube chapter requirements (>= 3 chapters, each >= 10.0s)
if len(chapters) < 3:
    print(
        f"WARNING [CHAPTERS]: YouTube requires at least 3 chapters (found {len(chapters)}).",
        file=sys.stderr,
    )

for i, (s_sec, title) in enumerate(chapters, start=1):
    if i < len(chapters):
        next_sec = chapters[i][0]
    elif vid_dur > 0:
        next_sec = vid_dur
    else:
        continue
    chap_dur = next_sec - s_sec
    if chap_dur < 10.0:
        print(
            f"WARNING [CHAPTERS]: Chapter {i} ('{title}') duration is {chap_dur:.2f}s (< 10.0s YouTube minimum).",
            file=sys.stderr,
        )

lines = [f"{fmt_ts(s_sec, use_hours)} {title}" for s_sec, title in chapters]
if lines:
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} YouTube chapter(s) to {out_path}:")
    for ln in lines:
        print(f"  {ln}")
PY
  fi
fi
