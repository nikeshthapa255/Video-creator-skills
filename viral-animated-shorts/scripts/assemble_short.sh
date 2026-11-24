#!/usr/bin/env bash
set -euo pipefail

# Deterministic v2.1 Shorts assembler:
#   ASS overlap gate + horizontal width warning -> probe & conform (1080x1920, 48kHz stereo) -> concat ->
#   dialogue-prioritized 3-stem audio mix (clarity-enhanced VO/native + subtle speech-carved sidechain-ducked BGM + controlled SFX) ->
#   safe-zone ASS caption burn -> 2-pass -14 LUFS (LRA=7) loudnorm -> ffprobe verify.
#
# Usage:
#   assemble_short.sh <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav|none] [bgm.wav|none] [sfx.wav|none]
#
# Environment:
#   SHORTS_FPS                Output framerate (default: 30)
#   SHORTS_VO_CLARITY         Enable dialogue clarity EQ/leveling chain: 1 or 0 (default: 1)
#   SHORTS_BGM_GAIN_DB        Background music gain in dB before ducking (default: -16)
#   SHORTS_SFX_GAIN_DB        SFX stem gain in dB (default: -3)
#   SHORTS_MAX_CAPTION_CHARS  Warn if any caption line exceeds this char count (default: 20)

usage() {
  cat <<EOF
Usage: $0 <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav|none] [bgm.wav|none] [sfx.wav|none]

Environment overrides:
  SHORTS_FPS                Output framerate (default: 30)
  SHORTS_VO_CLARITY         Dialogue clarity chain (highpass/EQ/compressor): 1 or 0 (default: 1)
  SHORTS_BGM_GAIN_DB        Subtle BGM gain in dB before sidechain ducking (default: -16)
  SHORTS_SFX_GAIN_DB        Non-intrusive SFX stem gain in dB (default: -3)
  SHORTS_MAX_CAPTION_CHARS  Horizontal width warning threshold in chars for 86px v3.1 captions (default: 20)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 3 || $# -gt 6 ]]; then
  usage >&2
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
SHORTS_VO_CLARITY="${SHORTS_VO_CLARITY:-1}"
SHORTS_BGM_GAIN_DB="${SHORTS_BGM_GAIN_DB:--16}"
SHORTS_SFX_GAIN_DB="${SHORTS_SFX_GAIN_DB:--3}"
SHORTS_MAX_CAPTION_CHARS="${SHORTS_MAX_CAPTION_CHARS:-20}"

BGM_GAIN_DB="${SHORTS_BGM_GAIN_DB%dB}"
SFX_GAIN_DB="${SHORTS_SFX_GAIN_DB%dB}"

# Fail loudly if ASS captions have overlapping Dialogue lines (CAPTION_OVERLAP gate)
check_ass_overlaps() {
  local ass_file="$1"
  awk -F',' '
    function to_cs(ts,   a, b) {
      gsub(/^[ \t\r]+|[ \t\r]+$/, "", ts);
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

# Warn clearly if any stripped visual caption line exceeds SHORTS_MAX_CAPTION_CHARS
check_ass_line_widths() {
  local ass_file="$1"
  local max_chars="$2"
  awk -F',' -v max_c="$max_chars" '
    /^Dialogue:/ {
      txt = "";
      for (i = 10; i <= NF; i++) {
        txt = (i == 10 ? $i : txt "," $i);
      }
      gsub(/\r/, "", txt);
      gsub(/\{[^}]*\}/, "", txt);
      gsub(/\\h/, " ", txt);
      gsub(/\\[Nn]/, "\n", txt);
      n = split(txt, lines, "\n");
      for (j = 1; j <= n; j++) {
        ln = lines[j];
        gsub(/^[ \t]+|[ \t]+$/, "", ln);
        if (length(ln) > max_c) {
          warns++;
          if (warns <= 10) {
            printf "WARNING [CAPTION_WIDTH]: ASS line %d has %d chars (> %d): \"%s\"\n", NR, length(ln), max_c, ln > "/dev/stderr";
          }
        }
      }
    }
    END {
      if (warns > 0) {
        printf "WARNING [CAPTION_WIDTH]: %d caption line(s) exceeded SHORTS_MAX_CAPTION_CHARS=%d in %s\n", warns, max_c, FILENAME > "/dev/stderr";
      }
    }
  ' "$ass_file"
}

if [[ "$CAPTIONS" != "none" ]]; then
  if [[ ! -f "$CAPTIONS" ]]; then
    echo "Error: captions file not found: $CAPTIONS" >&2
    exit 1
  fi
  check_ass_overlaps "$CAPTIONS"
  check_ass_line_widths "$CAPTIONS" "$SHORTS_MAX_CAPTION_CHARS"
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

# Dialogue-prioritized 3-stem audio mix:
#   Primary (VO_WAV if provided, else stitched native audio) — clarity-enhanced & front-and-center
#   BGM_WAV (if provided) — speech-carved (HP 60 Hz, LP 6.5 kHz, -3 dB dip @ 2.8 kHz), subtle gain, sidechain-ducked under dialogue
#   SFX_WAV (if provided) — controlled punch without overwhelming dialogue
HAS_VO=0;  [[ -n "$VO_WAV"  && "$VO_WAV"  != "none" ]] && HAS_VO=1
HAS_BGM=0; [[ -n "$BGM_WAV" && "$BGM_WAV" != "none" ]] && HAS_BGM=1
HAS_SFX=0; [[ -n "$SFX_WAV" && "$SFX_WAV" != "none" ]] && HAS_SFX=1

if [[ "$SHORTS_VO_CLARITY" == "1" || "$SHORTS_VO_CLARITY" == "true" ]]; then
  VO_CHAIN="highpass=f=80,equalizer=f=3000:t=q:w=1.2:g=2.0,acompressor=threshold=-18dB:ratio=2.5:attack=8:release=120:makeup=1.5"
else
  VO_CHAIN="anull"
fi

BGM_CHAIN="highpass=f=60,lowpass=f=6500,equalizer=f=2800:t=q:w=1.0:g=-3.0,volume=${BGM_GAIN_DB}dB"
DUCK_CHAIN="sidechaincompress=threshold=0.015:ratio=6:attack=15:release=320:makeup=1"

MIXED="$STITCHED"
if [[ "$HAS_VO" -eq 1 || "$HAS_BGM" -eq 1 || "$HAS_SFX" -eq 1 ]]; then
  MIXED="$WORK_DIR/mixed.mp4"
  FF_IN=(-i "$STITCHED")
  NEXT_IDX=1

  if [[ "$HAS_VO" -eq 1 ]]; then
    FF_IN+=(-i "$VO_WAV")
    PRI_LABEL="${NEXT_IDX}:a"
    NEXT_IDX=$((NEXT_IDX + 1))
  else
    PRI_LABEL="0:a"
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
    FILTER="[${PRI_LABEL}]${VO_CHAIN},asplit=2[sc][pri];[${BGM_IDX}:a]${BGM_CHAIN}[bgm];[bgm][sc]${DUCK_CHAIN}[duck];[${SFX_IDX}:a]volume=${SFX_GAIN_DB}dB[sfx];[pri][sfx][duck]amix=inputs=3:duration=first:normalize=0[aout]"
  elif [[ "$HAS_BGM" -eq 1 ]]; then
    FILTER="[${PRI_LABEL}]${VO_CHAIN},asplit=2[sc][pri];[${BGM_IDX}:a]${BGM_CHAIN}[bgm];[bgm][sc]${DUCK_CHAIN}[duck];[pri][duck]amix=inputs=2:duration=first:normalize=0[aout]"
  elif [[ "$HAS_SFX" -eq 1 ]]; then
    FILTER="[${PRI_LABEL}]${VO_CHAIN}[pri];[${SFX_IDX}:a]volume=${SFX_GAIN_DB}dB[sfx];[pri][sfx]amix=inputs=2:duration=first:normalize=0[aout]"
  else
    FILTER="[${PRI_LABEL}]${VO_CHAIN}[aout]"
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

# Pass 1: measure loudness (EBU R128 -14 LUFS, TP -1.0 dBTP, LRA 7)
STATS=$(ffmpeg -i "$CAPTIONED" -af loudnorm=I=-14:TP=-1.0:LRA=7:print_format=json -f null - 2>&1)
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
    -af "loudnorm=I=-14:TP=-1.0:LRA=7:measured_I=${M_I}:measured_TP=${M_TP}:measured_LRA=${M_LRA}:measured_thresh=${M_THRESH}:offset=${M_OFFSET}:linear=true" \
    -c:v copy -c:a aac -b:a 192k -ar 48000 "$OUT_MP4"
fi

echo "Assembled $OUT_MP4 (3-stem: vo=${HAS_VO}, bgm=${HAS_BGM}, sfx=${HAS_SFX}, clarity=${SHORTS_VO_CLARITY}, bgm_db=${BGM_GAIN_DB}, sfx_db=${SFX_GAIN_DB})"
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate:format=duration -of compact "$OUT_MP4"
