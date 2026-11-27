#!/usr/bin/env bash
set -euo pipefail

# Stop hook guard: prevents premature stop if a final render exists in ./work/ or ./output/
# without recorded QA verification, while respecting continuationActive to avoid loops.

INPUT="$(cat)"
if [[ -z "${INPUT// }" ]] || ! command -v jq >/dev/null 2>&1; then
  echo '{}'
  exit 0
fi

CONTINUATION_ACTIVE="$(printf '%s' "$INPUT" | jq -r '.continuationActive // false' 2>/dev/null || echo "false")"
if [[ "$CONTINUATION_ACTIVE" == "true" ]]; then
  echo '{}'
  exit 0
fi

TERM_REASON="$(printf '%s' "$INPUT" | jq -r '.terminationReason // "NO_TOOL_CALL"' 2>/dev/null || echo "NO_TOOL_CALL")"
if [[ "$TERM_REASON" != "NO_TOOL_CALL" ]]; then
  echo '{}'
  exit 0
fi

CWD="$(printf '%s' "$INPUT" | jq -r '.cwd // "."' 2>/dev/null || echo ".")"
QA_FLAG="${CWD}/work/qa_passed.flag"

shopt -s nullglob
CANDIDATES=(
  "${CWD}/output/"*.mp4
  "${CWD}/work/"*master*.mp4
  "${CWD}/work/final_trip.mp4"
  "${CWD}/work/trip_master_1080p.mp4"
  "${CWD}/work/trip_master_4k.mp4"
  "${CWD}/output/trip_cinema.mp4"
  "${CWD}/output/final_trip.mp4"
)
shopt -u nullglob

NEWEST_MP4=""
for mp4 in "${CANDIDATES[@]}"; do
  [[ -f "$mp4" ]] || continue
  if [[ -z "$NEWEST_MP4" || "$mp4" -nt "$NEWEST_MP4" ]]; then
    NEWEST_MP4="$mp4"
  fi
done

if [[ -z "$NEWEST_MP4" ]]; then
  echo '{}'
  exit 0
fi

if [[ ! -f "$QA_FLAG" ]]; then
  jq -nc --arg mp4 "$NEWEST_MP4" \
    '{decision:"continue",reason:("A rendered MP4 exists (" + $mp4 + "), but ./work/qa_passed.flag is missing. Run render_trip.py --extract-qa --edl (or macos_extract_qa.jxa), inspect qa_video_alignment.jpg, qa_chapter_cards.jpg, qa_collages.jpg, qa_transitions.jpg, qa_metrics.json, qa_sheet_*.jpg, and <=60s chunks, complete the 3-persona review panel, and write ./work/qa_passed.flag once verified.")}'
  exit 0
fi

if [[ "$NEWEST_MP4" -nt "$QA_FLAG" ]]; then
  jq -nc --arg mp4 "$NEWEST_MP4" \
    '{decision:"continue",reason:("Rendered MP4 (" + $mp4 + ") is newer than ./work/qa_passed.flag (stale QA sign-off). Re-run --extract-qa --edl, re-inspect all QA artifacts and <=60s chunks, and update ./work/qa_passed.flag once verified.")}'
  exit 0
fi

echo '{}'
