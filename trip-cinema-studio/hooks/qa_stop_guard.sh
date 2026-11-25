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

if [[ -f "$QA_FLAG" ]]; then
  echo '{}'
  exit 0
fi

if [[ -f "${CWD}/work/final_trip.mp4" || -f "${CWD}/work/trip_master_1080p.mp4" || -f "${CWD}/work/trip_master_4k.mp4" || -f "${CWD}/output/trip_cinema.mp4" || -f "${CWD}/output/final_trip.mp4" ]]; then
  jq -nc '{decision:"continue",reason:"A rendered MP4 exists, but ./work/qa_passed.flag is missing. Run render_trip.py --extract-qa, inspect the end-frame contact sheets and <=60s chunks with read_file, and write ./work/qa_passed.flag once verified."}'
  exit 0
fi

echo '{}'
