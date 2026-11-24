#!/usr/bin/env bash
set -euo pipefail

# Deterministic v4.0 Shorts & Long-Form Episode Assembler (9:16 vertical + 16:9 widescreen):
#   deterministic LC_ALL=C clip discovery -> aspect-ratio auto-detection (9:16 1080x1920 or 16:9 1920x1080) ->
#   ASS overlap gate + horizontal width warning -> smart fast-path concat (-c copy if all clips match)
#   or automatic per-clip conform -> dialogue-prioritized 3-stem audio mix
#   (clarity-enhanced VO/native + acoustic-safe speech-carved BGM ducked under dialogue + non-ducked SFX) ->
#   safe-zone ASS caption burn -> 2-pass linear -14 LUFS (TP=-1.0, LRA=7) loudnorm ->
#   mandatory post-norm loudness verification + ffprobe summary.
#
# Usage:
#   assemble_short.sh <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav|none] [bgm.wav|none] [sfx.wav|none]
#
# Environment:
#   SHORTS_FPS                Output framerate (default: 30)
#   SHORTS_ASPECT_RATIO       Target aspect ratio: 9:16, 16:9, or auto (default: auto)
#   SHORTS_VO_CLARITY         Enable dialogue clarity EQ/leveling chain: 1 or 0 (default: 1)
#   SHORTS_BGM_GAIN_DB        BGM gain in dB before ducking (default: -16)
#   SHORTS_BGM_LP_HZ          BGM low-pass cutoff Hz (default: 8500, preserves acoustic shimmer)
#   SHORTS_SFX_GAIN_DB        SFX track gain in dB (default: -3)
#   SHORTS_MAX_CAPTION_CHARS  Warn if any caption line exceeds this character count
#                             (default: 20 for 9:16, 40 for 16:9)

usage() {
  cat <<EOF
Assemble 9:16 (1080x1920) or 16:9 (1920x1080) Shorts & Long-Form episodes from shot clips,
3-stem audio, and ASS captions, normalized to -14 LUFS.

Usage: $0 <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav|none] [bgm.wav|none] [sfx.wav|none]

Environment overrides:
  SHORTS_FPS                Output framerate (default: 30)
  SHORTS_ASPECT_RATIO       Target aspect ratio: 9:16, 16:9, or auto (default: auto)
  SHORTS_VO_CLARITY         Dialogue clarity chain (highpass/EQ/compressor): 1 or 0 (default: 1)
  SHORTS_BGM_GAIN_DB        BGM gain in dB before ducking (default: -16)
  SHORTS_BGM_LP_HZ          BGM low-pass cutoff Hz (default: 8500)
  SHORTS_SFX_GAIN_DB        SFX track gain in dB (default: -3)
  SHORTS_MAX_CAPTION_CHARS  Warn if any caption line exceeds this character count
                            (default: 20 for 9:16, 40 for 16:9)
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
SHORTS_ASPECT_RATIO="${SHORTS_ASPECT_RATIO:-auto}"
SHORTS_VO_CLARITY="${SHORTS_VO_CLARITY:-1}"
SHORTS_BGM_GAIN_DB="${SHORTS_BGM_GAIN_DB:--16}"
SHORTS_BGM_LP_HZ="${SHORTS_BGM_LP_HZ:-8500}"
SHORTS_SFX_GAIN_DB="${SHORTS_SFX_GAIN_DB:--3}"
USER_MAX_CAPTION_CHARS="${SHORTS_MAX_CAPTION_CHARS:-}"

BGM_GAIN_DB="${SHORTS_BGM_GAIN_DB%dB}"
SFX_GAIN_DB="${SHORTS_SFX_GAIN_DB%dB}"

if [[ ! -d "$CLIPS_DIR" ]]; then
  echo "Error: clips directory not found: $CLIPS_DIR" >&2
  exit 1
fi

mapfile -t CLIPS < <(LC_ALL=C find "$CLIPS_DIR" -maxdepth 1 -type f -name '*.mp4' | LC_ALL=C sort)
if [[ "${#CLIPS[@]}" -eq 0 ]]; then
  echo "Error: no .mp4 clips found in $CLIPS_DIR" >&2
  exit 1
fi

# Resolve target dimensions & aspect ratio
case "$SHORTS_ASPECT_RATIO" in
  9:16)
    RESOLVED_ASPECT="9:16"
    TARGET_W=1080
    TARGET_H=1920
    ;;
  16:9)
    RESOLVED_ASPECT="16:9"
    TARGET_W=1920
    TARGET_H=1080
    ;;
  auto)
    first_dims=$(ffprobe -v error -select_streams v:0 \
      -show_entries stream=width,height -of csv=p=0 "${CLIPS[0]}" | head -n 1 || true)
    first_w="${first_dims%%,*}"
    first_h="${first_dims#*,}"
    if [[ "$first_w" =~ ^[0-9]+$ && "$first_h" =~ ^[0-9]+$ ]] && (( first_w > first_h )); then
      RESOLVED_ASPECT="16:9"
      TARGET_W=1920
      TARGET_H=1080
    else
      RESOLVED_ASPECT="9:16"
      TARGET_W=1080
      TARGET_H=1920
    fi
    ;;
  *)
    echo "Error: invalid SHORTS_ASPECT_RATIO='${SHORTS_ASPECT_RATIO}' (expected 9:16, 16:9, or auto)." >&2
    exit 2
    ;;
esac

if [[ -n "$USER_MAX_CAPTION_CHARS" ]]; then
  SHORTS_MAX_CAPTION_CHARS="$USER_MAX_CAPTION_CHARS"
elif [[ "$RESOLVED_ASPECT" == "16:9" ]]; then
  SHORTS_MAX_CAPTION_CHARS=40
else
  SHORTS_MAX_CAPTION_CHARS=20
fi

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

# Warn if any stripped visual caption line exceeds SHORTS_MAX_CAPTION_CHARS
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

HAS_VO=0;  [[ -n "$VO_WAV"  && "$VO_WAV"  != "none" ]] && HAS_VO=1
HAS_BGM=0; [[ -n "$BGM_WAV" && "$BGM_WAV" != "none" ]] && HAS_BGM=1
HAS_SFX=0; [[ -n "$SFX_WAV" && "$SFX_WAV" != "none" ]] && HAS_SFX=1

if [[ "$HAS_VO" -eq 1 && ! -f "$VO_WAV" ]]; then
  echo "Error: voiceover file not found: $VO_WAV" >&2
  exit 1
fi
if [[ "$HAS_BGM" -eq 1 && ! -f "$BGM_WAV" ]]; then
  echo "Error: BGM file not found: $BGM_WAV" >&2
  exit 1
fi
if [[ "$HAS_SFX" -eq 1 && ! -f "$SFX_WAV" ]]; then
  echo "Error: SFX file not found: $SFX_WAV" >&2
  exit 1
fi

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

# Smart fast-path probe: check if all clips already match TARGET_WxTARGET_H, ${SHORTS_FPS}/1 fps, h264, yuv420p, and uniform audio signature
TARGET_FPS_FRAC="${SHORTS_FPS}/1"
EXPECTED_V="h264,${TARGET_W},${TARGET_H},yuv420p,${TARGET_FPS_FRAC}"
FAST_PATH=1
AUDIO_SIG_REF=""
FAST_REASON=""

for clip in "${CLIPS[@]}"; do
  v_sig=$(ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,pix_fmt,r_frame_rate \
    -of csv=p=0 "$clip" | head -n 1 || true)
  if [[ "$v_sig" != "$EXPECTED_V" ]]; then
    FAST_PATH=0
    FAST_REASON="video signature mismatch on $(basename "$clip"): got '${v_sig}', expected '${EXPECTED_V}'"
    break
  fi

  a_sig=$(ffprobe -v error -select_streams a:0 \
    -show_entries stream=codec_name,sample_rate,channels \
    -of csv=p=0 "$clip" | head -n 1 || true)
  [[ -z "$a_sig" ]] && a_sig="none"

  if [[ -z "$AUDIO_SIG_REF" ]]; then
    AUDIO_SIG_REF="$a_sig"
  elif [[ "$a_sig" != "$AUDIO_SIG_REF" ]]; then
    FAST_PATH=0
    FAST_REASON="audio signature mismatch on $(basename "$clip"): got '${a_sig}', expected '${AUDIO_SIG_REF}'"
    break
  fi
done

STITCHED="$WORK_DIR/stitched.mp4"
CONCAT_LIST="$WORK_DIR/concat.txt"
: > "$CONCAT_LIST"

if [[ "$FAST_PATH" -eq 1 ]]; then
  echo "Fast-path concat enabled: ${#CLIPS[@]} clips match ${EXPECTED_V} (aspect=${RESOLVED_ASPECT}, audio=${AUDIO_SIG_REF})."
  for clip in "${CLIPS[@]}"; do
    abs_clip=$(readlink -f "$clip")
    escaped_clip=${abs_clip//\'/\'\\\'\'}
    printf "file '%s'\n" "$escaped_clip" >> "$CONCAT_LIST"
  done
  if [[ "$AUDIO_SIG_REF" == "none" && "$HAS_VO" -eq 0 && ( "$HAS_BGM" -eq 1 || "$HAS_SFX" -eq 1 ) ]]; then
    # Preserve video copy while adding silent 48kHz stereo track so 0:a exists as primary reference
    STITCHED_SILENT="$WORK_DIR/stitched_video_only.mp4"
    ffmpeg -y -v error -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$STITCHED_SILENT"
    ffmpeg -y -v error -i "$STITCHED_SILENT" -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
      -map 0:v:0 -map 1:a:0 -shortest -c:v copy -c:a aac -ar 48000 -ac 2 -b:a 192k "$STITCHED"
  else
    ffmpeg -y -v error -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$STITCHED"
  fi
else
  echo "Fast-path bypassed (${FAST_REASON}); conforming ${#CLIPS[@]} clips to ${TARGET_W}x${TARGET_H} (${RESOLVED_ASPECT})."
  idx=0
  for clip in "${CLIPS[@]}"; do
    idx=$((idx + 1))
    norm="$WORK_DIR/norm_$(printf '%04d' "$idx").mp4"
    has_audio=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$clip" | head -n 1 || true)
    # Conform every clip to TARGET_WxTARGET_H, uniform FPS, 48kHz stereo (inject silent track if clip has no audio)
    if [[ -n "$has_audio" ]]; then
      ffmpeg -y -v error -i "$clip" \
        -vf "scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=increase,crop=${TARGET_W}:${TARGET_H},setsar=1,fps=${SHORTS_FPS}" \
        -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
        -c:a aac -ar 48000 -ac 2 -b:a 192k "$norm"
    else
      ffmpeg -y -v error -i "$clip" -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
        -vf "scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=increase,crop=${TARGET_W}:${TARGET_H},setsar=1,fps=${SHORTS_FPS}" \
        -map 0:v:0 -map 1:a:0 -shortest \
        -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
        -c:a aac -ar 48000 -ac 2 -b:a 192k "$norm"
    fi
    printf "file '%s'\n" "$norm" >> "$CONCAT_LIST"
  done
  ffmpeg -y -v error -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$STITCHED"
fi

# Dialogue-prioritized 3-stem audio mix:
#   Primary (VO_WAV if provided, else stitched native audio): clarity-enhanced lead dialogue
#   BGM_WAV (if provided): acoustic-safe speech-carved (HP 60 Hz, LP ${SHORTS_BGM_LP_HZ} Hz, -3.5 dB dip @ 2.8 kHz), ducked under dialogue
#   SFX_WAV (if provided): non-ducked controlled punch
if [[ "$SHORTS_VO_CLARITY" == "1" || "$SHORTS_VO_CLARITY" == "true" ]]; then
  VO_CHAIN="highpass=f=80,equalizer=f=3000:t=q:w=1.2:g=2.0,acompressor=threshold=-18dB:ratio=2.5:attack=8:release=120:makeup=1.5"
else
  VO_CHAIN="anull"
fi

BGM_CHAIN="highpass=f=60,lowpass=f=${SHORTS_BGM_LP_HZ},equalizer=f=2800:t=q:w=1.0:g=-3.5,volume=${BGM_GAIN_DB}dB"
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

mkdir -p "$(dirname "$OUT_MP4")"

# 2-pass linear loudness normalization (EBU R128 -14 LUFS, TP -1.0 dBTP, LRA 7)
M_I=""
M_TP=""
M_LRA=""
M_THRESH=""
M_OFFSET=""
HAS_OUT_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$CAPTIONED" | head -n 1 || true)

if [[ -n "$HAS_OUT_AUDIO" ]]; then
  # Pass 1: measure pre-norm loudness
  STATS=$(ffmpeg -i "$CAPTIONED" -af loudnorm=I=-14:TP=-1.0:LRA=7:print_format=json -f null - 2>&1)
  M_I=$(echo "$STATS" | awk -F'"' '/"input_i"/ {print $4}')
  M_TP=$(echo "$STATS" | awk -F'"' '/"input_tp"/ {print $4}')
  M_LRA=$(echo "$STATS" | awk -F'"' '/"input_lra"/ {print $4}')
  M_THRESH=$(echo "$STATS" | awk -F'"' '/"input_thresh"/ {print $4}')
  M_OFFSET=$(echo "$STATS" | awk -F'"' '/"target_offset"/ {print $4}')
fi

# Pass 2: linear loudness normalization (skip if no audio or pure silence)
if [[ -z "$HAS_OUT_AUDIO" || "$M_I" == "-inf" || -z "$M_I" ]]; then
  cp "$CAPTIONED" "$OUT_MP4"
else
  ffmpeg -y -v error -i "$CAPTIONED" \
    -af "loudnorm=I=-14:TP=-1.0:LRA=7:measured_I=${M_I}:measured_TP=${M_TP}:measured_LRA=${M_LRA}:measured_thresh=${M_THRESH}:offset=${M_OFFSET}:linear=true" \
    -c:v copy -c:a aac -b:a 192k -ar 48000 "$OUT_MP4"
fi

# Mandatory post-norm loudness verification
FINAL_I="none"
FINAL_TP="none"
FINAL_LRA="none"
OUT_HAS_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$OUT_MP4" | head -n 1 || true)
if [[ -n "$OUT_HAS_AUDIO" ]]; then
  POST_STATS=$(ffmpeg -i "$OUT_MP4" -af loudnorm=I=-14:TP=-1.0:LRA=7:print_format=json -f null - 2>&1)
  FINAL_I=$(echo "$POST_STATS" | awk -F'"' '/"input_i"/ {print $4}')
  FINAL_TP=$(echo "$POST_STATS" | awk -F'"' '/"input_tp"/ {print $4}')
  FINAL_LRA=$(echo "$POST_STATS" | awk -F'"' '/"input_lra"/ {print $4}')
fi

echo "Assembled $OUT_MP4 (aspect=${RESOLVED_ASPECT} ${TARGET_W}x${TARGET_H}, 3-stem: vo=${HAS_VO}, bgm=${HAS_BGM}, sfx=${HAS_SFX}, clarity=${SHORTS_VO_CLARITY}, bgm_db=${BGM_GAIN_DB}, bgm_lp=${SHORTS_BGM_LP_HZ}, sfx_db=${SFX_GAIN_DB}, fast_path=${FAST_PATH})"
echo "Loudness check: pre_norm input_i=${M_I:-none} LUFS -> post_norm input_i=${FINAL_I} LUFS, TP=${FINAL_TP} dBTP, LRA=${FINAL_LRA} LU"
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels:format=duration -of compact "$OUT_MP4"
