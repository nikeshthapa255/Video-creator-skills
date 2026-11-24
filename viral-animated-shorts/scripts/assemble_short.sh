#!/usr/bin/env bash
set -euo pipefail

# Deterministic v4.1 Shorts & Long-Form Episode Assembler (9:16 vertical + 16:9 widescreen):
#   deterministic LC_ALL=C symlink-aware clip discovery -> aspect-ratio auto-detection (9:16 1080x1920 or 16:9 1920x1080) ->
#   ASS overlap gate + horizontal width warning -> strict fast-path concat (-c copy only if codec/size/pix_fmt/fps/time_base/SAR
#   and stereo audio time_base all match) or automatic per-clip conform -> lossless 24-bit PCM 3-stem audio mix
#   (clarity-enhanced VO/native + acoustic-safe speech-carved BGM musically ducked under dialogue + non-ducked SFX + peak limiter) ->
#   safe-zone ASS caption burn (2s IDR keyframe cadence) -> 2-pass linear -14 LUFS (TP=-1.0, LRA=7 for 9:16 / 9 for 16:9) loudnorm
#   directly from lossless PCM WAV into single final stereo AAC mux -> mandatory post-norm loudness verification + ffprobe summary.
#
# Usage:
#   assemble_short.sh <clips_dir> <captions.ass|none> <out.mp4> [voiceover.wav|none] [bgm.wav|none] [sfx.wav|none]
#
# Environment:
#   SHORTS_FPS                Output framerate (default: 30)
#   SHORTS_ASPECT_RATIO       Target aspect ratio: 9:16, 16:9, or auto (default: auto)
#   SHORTS_VO_CLARITY         Enable dialogue clarity EQ/leveling chain: 1 or 0 (default: 1)
#   SHORTS_BGM_GAIN_DB        BGM gain in dB before ducking (default: -15)
#   SHORTS_BGM_LP_HZ          BGM low-pass cutoff Hz (default: 10500, preserves acoustic shimmer)
#   SHORTS_SFX_GAIN_DB        SFX track gain in dB (default: -3)
#   SHORTS_TARGET_LRA         Target Loudness Range in LU (default: 7 for 9:16, 9 for 16:9)
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
  SHORTS_BGM_GAIN_DB        BGM gain in dB before ducking (default: -15)
  SHORTS_BGM_LP_HZ          BGM low-pass cutoff Hz (default: 10500)
  SHORTS_SFX_GAIN_DB        SFX track gain in dB (default: -3)
  SHORTS_TARGET_LRA         Target Loudness Range in LU (default: 7 for 9:16, 9 for 16:9)
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
SHORTS_BGM_GAIN_DB="${SHORTS_BGM_GAIN_DB:--15}"
SHORTS_BGM_LP_HZ="${SHORTS_BGM_LP_HZ:-10500}"
SHORTS_SFX_GAIN_DB="${SHORTS_SFX_GAIN_DB:--3}"
USER_MAX_CAPTION_CHARS="${SHORTS_MAX_CAPTION_CHARS:-}"
USER_TARGET_LRA="${SHORTS_TARGET_LRA:-}"

BGM_GAIN_DB="${SHORTS_BGM_GAIN_DB%dB}"
SFX_GAIN_DB="${SHORTS_SFX_GAIN_DB%dB}"
GOP_SIZE=$((SHORTS_FPS * 2))

if [[ ! -d "$CLIPS_DIR" ]]; then
  echo "Error: clips directory not found: $CLIPS_DIR" >&2
  exit 1
fi

mapfile -t CLIPS < <(LC_ALL=C find -L "$CLIPS_DIR" -maxdepth 1 -type f -name '*.mp4' | LC_ALL=C sort)
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

if [[ -n "$USER_TARGET_LRA" ]]; then
  SHORTS_TARGET_LRA="$USER_TARGET_LRA"
elif [[ "$RESOLVED_ASPECT" == "16:9" ]]; then
  SHORTS_TARGET_LRA=9
else
  SHORTS_TARGET_LRA=7
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

# Strict fast-path probe:
# Every clip must match h264, TARGET_WxTARGET_H, yuv420p, ${SHORTS_FPS}/1 fps, SAR in (1:1, N/A),
# identical video time_base (V_TB_REF), and uniform audio signature (including stereo channels=2 and audio time_base).
TARGET_FPS_FRAC="${SHORTS_FPS}/1"
EXPECTED_V="h264,${TARGET_W},${TARGET_H},yuv420p,${TARGET_FPS_FRAC}"
FAST_PATH=1
V_TB_REF=""
AUDIO_SIG_REF=""
FAST_REASON=""
ANY_CLIP_AUDIO=0

for clip in "${CLIPS[@]}"; do
  v_codec="" v_w="" v_h="" v_pix="" v_fps="" v_tb="" v_sar=""
  while IFS='=' read -r k v; do
    case "$k" in
      codec_name) v_codec="$v" ;;
      width) v_w="$v" ;;
      height) v_h="$v" ;;
      pix_fmt) v_pix="$v" ;;
      r_frame_rate) v_fps="$v" ;;
      time_base) v_tb="$v" ;;
      sample_aspect_ratio) v_sar="$v" ;;
    esac
  done < <(ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,pix_fmt,r_frame_rate,time_base,sample_aspect_ratio \
    -of default=noprint_wrappers=1 "$clip" || true)

  a_codec="" a_sr="" a_ch="" a_tb=""
  while IFS='=' read -r k v; do
    case "$k" in
      codec_name) a_codec="$v" ;;
      sample_rate) a_sr="$v" ;;
      channels) a_ch="$v" ;;
      time_base) a_tb="$v" ;;
    esac
  done < <(ffprobe -v error -select_streams a:0 \
    -show_entries stream=codec_name,sample_rate,channels,time_base \
    -of default=noprint_wrappers=1 "$clip" || true)

  if [[ -n "$a_codec" ]]; then
    ANY_CLIP_AUDIO=1
    a_sig="${a_codec},${a_sr},${a_ch},${a_tb}"
  else
    a_sig="none"
  fi

  if [[ "$FAST_PATH" -eq 0 ]]; then
    continue
  fi

  v_core="${v_codec},${v_w},${v_h},${v_pix},${v_fps}"
  v_sig="${v_core},${v_tb},${v_sar:-N/A}"
  if [[ "$v_core" != "$EXPECTED_V" ]]; then
    FAST_PATH=0
    FAST_REASON="video signature mismatch on $(basename "$clip"): got '${v_sig}', expected '${EXPECTED_V}'"
    continue
  fi

  if [[ -n "$v_sar" && "$v_sar" != "1:1" && "$v_sar" != "N/A" ]]; then
    FAST_PATH=0
    FAST_REASON="non-square SAR on $(basename "$clip"): got '${v_sar}' (expected 1:1 or N/A)"
    continue
  fi

  if [[ -z "$v_tb" ]]; then
    FAST_PATH=0
    FAST_REASON="missing video time_base on $(basename "$clip")"
    continue
  elif [[ -z "$V_TB_REF" ]]; then
    V_TB_REF="$v_tb"
  elif [[ "$v_tb" != "$V_TB_REF" ]]; then
    FAST_PATH=0
    FAST_REASON="video time_base mismatch on $(basename "$clip"): got '${v_tb}', expected '${V_TB_REF}'"
    continue
  fi

  if [[ "$a_sig" != "none" && "$a_ch" != "2" ]]; then
    FAST_PATH=0
    FAST_REASON="non-stereo clip audio on $(basename "$clip"): channels='${a_ch}' (must conform to stereo)"
    continue
  fi

  if [[ -z "$AUDIO_SIG_REF" ]]; then
    AUDIO_SIG_REF="$a_sig"
  elif [[ "$a_sig" != "$AUDIO_SIG_REF" ]]; then
    FAST_PATH=0
    FAST_REASON="audio signature mismatch on $(basename "$clip"): got '${a_sig}', expected '${AUDIO_SIG_REF}'"
    continue
  fi
done

STITCHED="$WORK_DIR/stitched.mp4"
CONCAT_LIST="$WORK_DIR/concat.txt"
: > "$CONCAT_LIST"

if [[ "$FAST_PATH" -eq 1 ]]; then
  echo "Fast-path concat enabled: ${#CLIPS[@]} clips match ${EXPECTED_V} (tb=${V_TB_REF}, aspect=${RESOLVED_ASPECT}, audio=${AUDIO_SIG_REF})."
  for clip in "${CLIPS[@]}"; do
    abs_clip=$(readlink -f "$clip")
    escaped_clip=${abs_clip//\'/\'\\\'\'}
    printf "file '%s'\n" "$escaped_clip" >> "$CONCAT_LIST"
  done
  ffmpeg -y -v error -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$STITCHED"
else
  echo "Fast-path bypassed (${FAST_REASON}); conforming ${#CLIPS[@]} clips to ${TARGET_W}x${TARGET_H} (${RESOLVED_ASPECT})."
  idx=0
  for clip in "${CLIPS[@]}"; do
    idx=$((idx + 1))
    norm="$WORK_DIR/norm_$(printf '%04d' "$idx").mp4"
    has_audio=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$clip" | head -n 1 || true)
    # Conform every clip to TARGET_WxTARGET_H, uniform FPS, square SAR, uniform time_base, 2s IDR cadence
    if [[ "$ANY_CLIP_AUDIO" -eq 1 ]]; then
      if [[ -n "$has_audio" ]]; then
        ffmpeg -y -v error -i "$clip" \
          -vf "scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=increase,crop=${TARGET_W}:${TARGET_H},setsar=1,fps=${SHORTS_FPS}" \
          -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -g "$GOP_SIZE" \
          -c:a aac -ar 48000 -ac 2 -b:a 192k "$norm"
      else
        ffmpeg -y -v error -i "$clip" -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
          -vf "scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=increase,crop=${TARGET_W}:${TARGET_H},setsar=1,fps=${SHORTS_FPS}" \
          -map 0:v:0 -map 1:a:0 -shortest \
          -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -g "$GOP_SIZE" \
          -c:a aac -ar 48000 -ac 2 -b:a 192k "$norm"
      fi
    else
      ffmpeg -y -v error -i "$clip" \
        -vf "scale=${TARGET_W}:${TARGET_H}:force_original_aspect_ratio=increase,crop=${TARGET_W}:${TARGET_H},setsar=1,fps=${SHORTS_FPS}" \
        -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -g "$GOP_SIZE" \
        -an "$norm"
    fi
    printf "file '%s'\n" "$norm" >> "$CONCAT_LIST"
  done
  ffmpeg -y -v error -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$STITCHED"
fi

# Video caption burn (separate from audio so audio stays lossless 24-bit PCM until final single AAC mux)
VIDEO_FINAL="$STITCHED"
if [[ "$CAPTIONS" != "none" && -f "$CAPTIONS" ]]; then
  VIDEO_FINAL="$WORK_DIR/video_captioned.mp4"
  cp "$CAPTIONS" "$WORK_DIR/captions.ass"
  ffmpeg -y -v error -i "$STITCHED" -map 0:v:0 -vf "ass=$WORK_DIR/captions.ass" \
    -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -g "$GOP_SIZE" -an "$VIDEO_FINAL"
fi

# Probe stitched video duration & native audio presence
STITCHED_DUR=$(ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=nw=1:nk=1 "$STITCHED" | head -n 1 || true)
if [[ -z "$STITCHED_DUR" || "$STITCHED_DUR" == "N/A" ]]; then
  STITCHED_DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$STITCHED" | head -n 1 || true)
fi
STITCHED_HAS_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$STITCHED" | head -n 1 || true)

# Dialogue-prioritized single-carve 3-stem audio mix -> lossless 24-bit PCM WAV ($WORK_DIR/mixed_audio.wav)
#   Primary (VO_WAV if provided, else stitched native audio, else silent bed of video duration): clarity-enhanced lead dialogue
#   BGM_WAV (if provided): acoustic-safe speech-carved (HP 55 Hz, LP ${SHORTS_BGM_LP_HZ} Hz, soft HF shelf, -3.0 dB @ 2.9 kHz), musically ducked
#   SFX_WAV (if provided): non-ducked controlled punch with speech pocket & limiter
#   Mix bus: transparent peak catcher (alimiter) into 24-bit PCM WAV (zero intermediate lossy AAC clipping)
if [[ "$SHORTS_VO_CLARITY" == "1" || "$SHORTS_VO_CLARITY" == "true" ]]; then
  VO_CHAIN="aformat=sample_rates=48000:channel_layouts=stereo,highpass=f=75,equalizer=f=2800:t=q:w=1.2:g=1.5,equalizer=f=6800:t=q:w=1.8:g=-1.5,acompressor=threshold=-20dB:ratio=2.5:attack=6:release=140:makeup=1.2"
else
  VO_CHAIN="aformat=sample_rates=48000:channel_layouts=stereo"
fi

BGM_CHAIN="aformat=sample_rates=48000:channel_layouts=stereo,highpass=f=55,lowpass=f=${SHORTS_BGM_LP_HZ},treble=g=-2.5:f=7500,equalizer=f=2900:t=q:w=0.9:g=-3.0,volume=${BGM_GAIN_DB}dB"
DUCK_CHAIN="sidechaincompress=threshold=0.045:ratio=3.2:attack=10:release=600:knee=4.0:makeup=1"
SFX_CHAIN="aformat=sample_rates=48000:channel_layouts=stereo,highpass=f=32,equalizer=f=2900:t=q:w=1.1:g=-2.0,volume=${SFX_GAIN_DB}dB,alimiter=limit=0.89:attack=2:release=40:level=disabled"
BUS_LIMITER="alimiter=limit=0.891:attack=2:release=50:level=disabled"

MIXED_WAV=""
if [[ "$HAS_VO" -eq 1 || "$HAS_BGM" -eq 1 || "$HAS_SFX" -eq 1 ]]; then
  MIXED_WAV="$WORK_DIR/mixed_audio.wav"
  FF_IN=()
  NEXT_IDX=0

  if [[ "$HAS_VO" -eq 1 ]]; then
    FF_IN+=(-i "$VO_WAV")
    PRI_LABEL="${NEXT_IDX}:a"
    NEXT_IDX=$((NEXT_IDX + 1))
  elif [[ -n "$STITCHED_HAS_AUDIO" ]]; then
    FF_IN+=(-i "$STITCHED")
    PRI_LABEL="${NEXT_IDX}:a"
    NEXT_IDX=$((NEXT_IDX + 1))
  else
    FF_IN+=(-f lavfi -t "$STITCHED_DUR" -i "anullsrc=channel_layout=stereo:sample_rate=48000")
    PRI_LABEL="${NEXT_IDX}:a"
    NEXT_IDX=$((NEXT_IDX + 1))
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
    FILTER="[${PRI_LABEL}]${VO_CHAIN},asplit=2[sc][pri];[${BGM_IDX}:a]${BGM_CHAIN}[bgm];[bgm][sc]${DUCK_CHAIN}[duck];[${SFX_IDX}:a]${SFX_CHAIN}[sfx];[pri][sfx][duck]amix=inputs=3:duration=first:normalize=0,${BUS_LIMITER}[aout]"
  elif [[ "$HAS_BGM" -eq 1 ]]; then
    FILTER="[${PRI_LABEL}]${VO_CHAIN},asplit=2[sc][pri];[${BGM_IDX}:a]${BGM_CHAIN}[bgm];[bgm][sc]${DUCK_CHAIN}[duck];[pri][duck]amix=inputs=2:duration=first:normalize=0,${BUS_LIMITER}[aout]"
  elif [[ "$HAS_SFX" -eq 1 ]]; then
    FILTER="[${PRI_LABEL}]${VO_CHAIN}[pri];[${SFX_IDX}:a]${SFX_CHAIN}[sfx];[pri][sfx]amix=inputs=2:duration=first:normalize=0,${BUS_LIMITER}[aout]"
  else
    FILTER="[${PRI_LABEL}]${VO_CHAIN},${BUS_LIMITER}[aout]"
  fi

  ffmpeg -y -v error "${FF_IN[@]}" \
    -filter_complex "$FILTER" \
    -map "[aout]" -t "$STITCHED_DUR" \
    -c:a pcm_s24le -ar 48000 -ac 2 "$MIXED_WAV"
elif [[ -n "$STITCHED_HAS_AUDIO" ]]; then
  MIXED_WAV="$WORK_DIR/mixed_audio.wav"
  ffmpeg -y -v error -i "$STITCHED" -vn -c:a pcm_s24le -ar 48000 -ac 2 "$MIXED_WAV"
fi

mkdir -p "$(dirname "$OUT_MP4")"

# 2-pass linear loudness normalization (EBU R128 -14 LUFS, TP -1.0 dBTP, LRA=${SHORTS_TARGET_LRA})
# Runs directly on lossless 24-bit PCM WAV -> single final stereo AAC encode muxed with video
M_I=""
M_TP=""
M_LRA=""
M_THRESH=""
M_OFFSET=""

if [[ -n "$MIXED_WAV" && -s "$MIXED_WAV" ]]; then
  # Pass 1: measure pre-norm loudness directly on lossless PCM WAV
  STATS=$(ffmpeg -i "$MIXED_WAV" -af "loudnorm=I=-14:TP=-1.0:LRA=${SHORTS_TARGET_LRA}:print_format=json" -f null - 2>&1)
  M_I=$(echo "$STATS" | awk -F'"' '/"input_i"/ {print $4}')
  M_TP=$(echo "$STATS" | awk -F'"' '/"input_tp"/ {print $4}')
  M_LRA=$(echo "$STATS" | awk -F'"' '/"input_lra"/ {print $4}')
  M_THRESH=$(echo "$STATS" | awk -F'"' '/"input_thresh"/ {print $4}')
  M_OFFSET=$(echo "$STATS" | awk -F'"' '/"target_offset"/ {print $4}')

  if [[ "$M_I" == "-inf" || -z "$M_I" ]]; then
    # Pure silence audio: mux video copy + single stereo AAC encode without loudnorm
    ffmpeg -y -v error -i "$VIDEO_FINAL" -i "$MIXED_WAV" \
      -map 0:v:0 -map 1:a:0 \
      -c:v copy -c:a aac -b:a 192k -ar 48000 -ac 2 "$OUT_MP4"
  else
    # Pass 2: linear loudness normalization from lossless WAV + single final stereo AAC mux
    ffmpeg -y -v error -i "$VIDEO_FINAL" -i "$MIXED_WAV" \
      -map 0:v:0 -map 1:a:0 \
      -c:v copy \
      -filter:a:0 "loudnorm=I=-14:TP=-1.0:LRA=${SHORTS_TARGET_LRA}:measured_I=${M_I}:measured_TP=${M_TP}:measured_LRA=${M_LRA}:measured_thresh=${M_THRESH}:offset=${M_OFFSET}:linear=true" \
      -c:a aac -b:a 192k -ar 48000 -ac 2 "$OUT_MP4"
  fi
else
  # Video-only assembly (no audio stems and silent clips)
  ffmpeg -y -v error -i "$VIDEO_FINAL" -map 0:v:0 -c:v copy -an "$OUT_MP4"
fi

# Mandatory post-norm loudness verification
FINAL_I="none"
FINAL_TP="none"
FINAL_LRA="none"
OUT_HAS_AUDIO=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$OUT_MP4" | head -n 1 || true)
if [[ -n "$OUT_HAS_AUDIO" ]]; then
  POST_STATS=$(ffmpeg -i "$OUT_MP4" -af "loudnorm=I=-14:TP=-1.0:LRA=${SHORTS_TARGET_LRA}:print_format=json" -f null - 2>&1)
  FINAL_I=$(echo "$POST_STATS" | awk -F'"' '/"input_i"/ {print $4}')
  FINAL_TP=$(echo "$POST_STATS" | awk -F'"' '/"input_tp"/ {print $4}')
  FINAL_LRA=$(echo "$POST_STATS" | awk -F'"' '/"input_lra"/ {print $4}')
fi

echo "Assembled $OUT_MP4 (aspect=${RESOLVED_ASPECT} ${TARGET_W}x${TARGET_H}, 3-stem: vo=${HAS_VO}, bgm=${HAS_BGM}, sfx=${HAS_SFX}, clarity=${SHORTS_VO_CLARITY}, bgm_db=${BGM_GAIN_DB}, bgm_lp=${SHORTS_BGM_LP_HZ}, sfx_db=${SFX_GAIN_DB}, target_lra=${SHORTS_TARGET_LRA}, fast_path=${FAST_PATH})"
echo "Loudness check: pre_norm input_i=${M_I:-none} LUFS -> post_norm input_i=${FINAL_I} LUFS, TP=${FINAL_TP} dBTP, LRA=${FINAL_LRA} LU"
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels:format=duration -of compact "$OUT_MP4"
