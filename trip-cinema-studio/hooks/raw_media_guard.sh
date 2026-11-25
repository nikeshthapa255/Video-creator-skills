#!/usr/bin/env bash
set -euo pipefail

# PreToolUse guard: blocks destructive rm/mv/shred/unlink or ffmpeg -y overwrites
# targeting raw user media outside ./work/, ./output/, or /tmp/.

INPUT="$(cat)"
if [[ -z "${INPUT// }" ]]; then
  echo '{"decision":"allow"}'
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  echo '{"decision":"allow"}'
  exit 0
fi

CMD="$(printf '%s' "$INPUT" | jq -r '.toolCall.args.CommandLine // ""' 2>/dev/null || true)"
if [[ -z "${CMD// }" ]]; then
  echo '{"decision":"allow"}'
  exit 0
fi

# Check destructive file commands (rm, mv, unlink, shred, srm)
if printf '%s' "$CMD" | grep -qiE '(^|[;&|][[:space:]]*)(rm|mv|unlink|shred|srm)[[:space:]]+'; then
  # Block if targeting raw media directory names
  if printf '%s' "$CMD" | grep -qiE '(^|[[:space:]/])(raw|raw_media|originals|dcim|camera)(/|[[:space:]]|$)'; then
    jq -nc '{decision:"deny",reason:"Blocked destructive command targeting raw media directory. Raw user media is read-only; work only inside ./work/ or ./output/."}'
    exit 0
  fi
  # Block if targeting media extensions without referencing ./work/, ./output/, or /tmp/
  if printf '%s' "$CMD" | grep -qiE '\.(heic|heif|dng|cr2|cr3|nef|arw|orf|raf|rw2|jpg|jpeg|png|webp|mov|mp4|m4v|avi|mkv|wav|mp3|aac|m4a)\b'; then
    if ! printf '%s' "$CMD" | grep -qE '(^|[[:space:]/])(\./)?(work|output|/tmp)(/|[[:space:]]|$)'; then
      jq -nc '{decision:"deny",reason:"Blocked destructive command on media files outside ./work/ or ./output/. Copy or process into ./work/ instead."}'
      exit 0
    fi
  fi
fi

# Check ffmpeg -y overwriting media outside ./work/, ./output/, or /tmp/
if printf '%s' "$CMD" | grep -qE '(^|[[:space:];&|])ffmpeg[[:space:]]' && printf '%s' "$CMD" | grep -qE '(^|[[:space:]])-y([[:space:]]|$)'; then
  # Extract last non-empty token of the command
  LAST_TOKEN="$(printf '%s' "$CMD" | awk '{print $NF}' | tr -d "\"'")"
  if printf '%s' "$LAST_TOKEN" | grep -qiE '\.(heic|heif|dng|cr2|cr3|nef|arw|orf|raf|rw2|jpg|jpeg|png|webp|mov|mp4|m4v|avi|mkv|wav|mp3|aac|m4a)$'; then
    if ! printf '%s' "$LAST_TOKEN" | grep -qE '^(\./)?(work|output|/tmp)/'; then
      jq -nc --arg out "$LAST_TOKEN" '{decision:"deny",reason:("Blocked ffmpeg -y overwrite targeting " + $out + " outside ./work/, ./output/, or /tmp/. Write renders to ./work/ or ./output/.")}'
      exit 0
    fi
  fi
fi

echo '{"decision":"allow"}'
