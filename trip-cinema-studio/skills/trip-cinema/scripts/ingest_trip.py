#!/usr/bin/env python3
"""Deterministic media ingestion, chapter clustering, contact sheets, and Video Timeline Strips (v0.2.0).

Supports `--input-dir`, `--file-list`, or a pre-downloaded Drive folder.
Implements the v0.2.0 Video-First Stage 0 spec:
  - 7-Tier Creation Date Waterfall with GoPro `mvhd` quirk guard & sequence interpolation
  - FPS extraction (`slow_mo_eligible: fps >= 50`), HDR detection, and GPS altitude parsing
  - `--video-strips` mode rendering `1800x360` Multi-Frame Video Timeline Strips via
    FFmpeg keyframe seek + 8 kHz mono PCM pipe for RMS audio peaks & wind classification
  - Haversine + temporal ST-DBSCAN chapter clustering (with temporal fallback for DSLR stills)
  - 3x3 numbered photo contact sheets for Gate 1 review
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
    ".tif", ".tiff", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".raf",
}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

GOPRO_NAME_RE = re.compile(r"^(?:GX|GH|GP|GOPR)\d+", re.IGNORECASE)
SEQUENCE_STEM_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*?)[_-]?(\d{3,6})$")
ISO6709_RE = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")


def _tool_exists(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        out = (proc.stdout or "") + (proc.stderr or "")
        if "xcode-select: error:" in out:
            return False
        if cmd[0] == "mdls" and "usage: mdls" in out:
            return True
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _parse_iso_to_epoch(raw: str | None, assume_utc_if_naive: bool = True, strip_tz_wallclock: bool = False) -> float | None:
    """Parse ExifTool, EXIF, ffprobe, or mdls timestamp strings into epoch seconds."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text.startswith("0000:00:00") or text.startswith("1904-01-01"):
        return None
    # Normalize EXIF "YYYY:MM:DD HH:MM:SS" -> "YYYY-MM-DD HH:MM:SS"
    if re.match(r"^\d{4}:\d{2}:\d{2}\s", text):
        text = text[:10].replace(":", "-") + text[10:]
    # Handle trailing " +0000" from mdls
    text = re.sub(r"\s+([+-]\d{2})(\d{2})$", r"\1:\2", text)
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    text = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.year < 1995 or parsed.year > 2100:
        return None

    if strip_tz_wallclock:
        # Preserve wall-clock date/time components without re-applying host UTC shift
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()

    if parsed.tzinfo is None:
        tz = dt.timezone.utc if assume_utc_if_naive else dt.datetime.now().astimezone().tzinfo
        parsed = parsed.replace(tzinfo=tz or dt.timezone.utc)
    return parsed.timestamp()


def _parse_iso6709(raw: str | None) -> tuple[float | None, float | None, float | None]:
    """Parse Apple QuickTime ISO6709 location string (`+28.2096+083.9856+822.4/`)."""
    if not raw or not isinstance(raw, str):
        return None, None, None
    m = ISO6709_RE.match(raw.strip())
    if not m:
        return None, None, None
    try:
        lat = round(float(m.group(1)), 6)
        lon = round(float(m.group(2)), 6)
        alt = round(float(m.group(3)), 1) if m.group(3) is not None else None
        return lat, lon, alt
    except ValueError:
        return None, None, None


def _parse_fps_fraction(raw: str | float | int | None) -> float | None:
    """Safely parse ffprobe rational frame rate (`60000/1001`) or numeric FPS."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return round(val, 2) if 1.0 <= val <= 1000.0 else None
    text = str(raw).strip()
    if not text or text in {"0/0", "0/1", "N/A"}:
        return None
    try:
        if "/" in text:
            num_s, den_s = text.split("/", 1)
            num, den = float(num_s), float(den_s)
            if den <= 0:
                return None
            val = num / den
        else:
            val = float(text)
        return round(val, 2) if 1.0 <= val <= 1000.0 else None
    except ValueError:
        return None


def _parse_filename_timestamp(path: Path) -> tuple[float | None, str | None]:
    """Tier 4: Parse capture timestamp from Pixel (`PXL_` UTC), Android (`VID_`), or DJI filenames."""
    stem = path.stem
    # Google Pixel filenames are always UTC: PXL_20260704_091522123
    m_pxl = re.search(r"PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", stem)
    if m_pxl:
        y, mo, d, h, mi, s = (int(g) for g in m_pxl.groups())
        try:
            ts = dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc).timestamp()
            return ts, "tier4_filename_pxl_utc"
        except ValueError:
            pass

    # Common local-wallclock naming patterns: VID_, IMG_, DJI_, MOV_, generic YYYYMMDD_HHMMSS
    patterns = [
        r"(?:VID|IMG|DJI|MOV|MVI|WP|SIGNAL)[_-]?(\d{4})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})",
        r"(?<!\d)(20\d{2}|199\d)[_-]?(0[1-9]|1[0-2])[_-]?(0[1-9]|[12]\d|3[01])[T_ -]?([01]\d|2[0-3])[_-]?([0-5]\d)[_-]?([0-5]\d)(?!\d)",
    ]
    for pat in patterns:
        m = re.search(pat, stem, re.IGNORECASE)
        if m:
            y, mo, d, h, mi, s = (int(g) for g in m.groups())
            try:
                ts = dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc).timestamp()
                return ts, "tier4_filename"
            except ValueError:
                continue
    return None, None


def _is_gopro_asset(path: Path, et: dict[str, Any], probe: dict[str, Any]) -> bool:
    """Detect GoPro videos affected by the local-wallclock-in-UTC-mvhd quirk."""
    if GOPRO_NAME_RE.match(path.name):
        return True
    model = str(et.get("Model") or et.get("Make") or probe.get("camera_model") or "").lower().strip()
    if "gopro" in model or model.startswith("hero"):
        return True
    return bool(probe.get("has_gpmf", False))


def _read_jpeg_exif_minimal(path: Path) -> dict[str, Any]:
    """Zero-dependency minimal JPEG APP1 EXIF reader (DateTimeOriginal, Orientation, GPS + Altitude)."""
    result: dict[str, Any] = {}
    try:
        with path.open("rb") as fh:
            if fh.read(2) != b"\xff\xd8":
                return result
            while True:
                marker = fh.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    break
                if marker[1] == 0xDA:  # Start of Scan
                    break
                if marker[1] in (0xD8, 0xD9):
                    continue
                seg_len_bytes = fh.read(2)
                if len(seg_len_bytes) < 2:
                    break
                seg_len = struct.unpack(">H", seg_len_bytes)[0]
                seg_data = fh.read(max(0, seg_len - 2))
                if marker[1] == 0xE1 and seg_data.startswith(b"Exif\x00\x00"):
                    tiff = seg_data[6:]
                    return _parse_tiff_exif(tiff)
    except OSError:
        return result
    return result


def _parse_tiff_exif(tiff: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if len(tiff) < 8:
        return out
    endian = "<" if tiff[:2] == b"II" else ">" if tiff[:2] == b"MM" else ""
    if not endian:
        return out

    def u16(offset: int) -> int:
        return struct.unpack(endian + "H", tiff[offset : offset + 2])[0]

    def u32(offset: int) -> int:
        return struct.unpack(endian + "I", tiff[offset : offset + 4])[0]

    def read_str(val_offset: int, count: int) -> str:
        raw = tiff[val_offset : val_offset + count]
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()

    def read_rationals(val_offset: int, count: int) -> list[float]:
        vals: list[float] = []
        for idx in range(count):
            base = val_offset + idx * 8
            if base + 8 > len(tiff):
                break
            num = u32(base)
            den = u32(base + 4)
            vals.append(num / den if den else 0.0)
        return vals

    def parse_ifd(ifd_offset: int) -> dict[int, tuple[int, int, bytes, int]]:
        entries: dict[int, tuple[int, int, bytes, int]] = {}
        if ifd_offset + 2 > len(tiff):
            return entries
        count = u16(ifd_offset)
        for i in range(count):
            entry_off = ifd_offset + 2 + i * 12
            if entry_off + 12 > len(tiff):
                break
            tag = u16(entry_off)
            typ = u16(entry_off + 2)
            cnt = u32(entry_off + 4)
            val_bytes = tiff[entry_off + 8 : entry_off + 12]
            val_off = u32(entry_off + 8)
            entries[tag] = (typ, cnt, val_bytes, val_off)
        return entries

    ifd0_off = u32(4)
    ifd0 = parse_ifd(ifd0_off)
    if 0x0112 in ifd0:
        _, _, vb, _ = ifd0[0x0112]
        out["exif_orientation"] = struct.unpack(endian + "H", vb[:2])[0]

    if 0x8769 in ifd0:
        exif_off = struct.unpack(endian + "I", ifd0[0x8769][2])[0]
        exif_ifd = parse_ifd(exif_off)
        for tag in (0x9003, 0x9004):  # DateTimeOriginal, DateTimeDigitized
            if tag in exif_ifd:
                _, cnt, vb, vo = exif_ifd[tag]
                if cnt > 4 and vo + cnt <= len(tiff):
                    out["datetime"] = read_str(vo, cnt)
                    break
        if 0x9011 in exif_ifd:  # OffsetTimeOriginal
            _, cnt, vb, vo = exif_ifd[0x9011]
            if cnt > 4 and vo + cnt <= len(tiff):
                out["offset_time"] = read_str(vo, cnt)

    if 0x8825 in ifd0:
        gps_off = struct.unpack(endian + "I", ifd0[0x8825][2])[0]
        gps = parse_ifd(gps_off)
        if 0x0002 in gps and 0x0004 in gps:
            lat_dms = read_rationals(gps[0x0002][3], 3)
            lon_dms = read_rationals(gps[0x0004][3], 3)
            if len(lat_dms) == 3 and len(lon_dms) == 3:
                lat = lat_dms[0] + lat_dms[1] / 60.0 + lat_dms[2] / 3600.0
                lon = lon_dms[0] + lon_dms[1] / 60.0 + lon_dms[2] / 3600.0
                lat_ref = gps[0x0001][2][:1].decode("ascii", errors="ignore") if 0x0001 in gps else "N"
                lon_ref = gps[0x0003][2][:1].decode("ascii", errors="ignore") if 0x0003 in gps else "E"
                if lat_ref.upper() == "S":
                    lat = -lat
                if lon_ref.upper() == "W":
                    lon = -lon
                out["lat"] = round(lat, 6)
                out["lon"] = round(lon, 6)
        if 0x0006 in gps:
            alt_vals = read_rationals(gps[0x0006][3], 1)
            if alt_vals:
                alt = alt_vals[0]
                if 0x0005 in gps and gps[0x0005][2][:1] == b"\x01":
                    alt = -alt
                out["altitude_m"] = round(alt, 1)
    return out


def _extract_exiftool_batch(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Run ExifTool once in batch mode with QuickTimeUTC + Keys:CreationDate + FPS + Altitude."""
    if not paths:
        return {}
    cmd = [
        "exiftool",
        "-j",
        "-n",
        "-api",
        "QuickTimeUTC=1",
        "-Keys:CreationDate",
        "-DateTimeOriginal",
        "-OffsetTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-GPSLatitude",
        "-GPSLongitude",
        "-GPSAltitude",
        "-GPSAltitudeRef",
        "-ImageWidth",
        "-ImageHeight",
        "-Orientation",
        "-Duration",
        "-Rotation",
        "-VideoFrameRate",
        "-Make",
        "-Model",
        *[str(p) for p in paths],
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        src = row.get("SourceFile")
        if src:
            mapped[os.path.abspath(src)] = row
    return mapped


def _extract_macos_mdls(path: Path) -> dict[str, Any]:
    """Fallback metadata using macOS Spotlight `mdls`."""
    attrs = [
        "kMDItemContentCreationDate",
        "kMDItemLatitude",
        "kMDItemLongitude",
        "kMDItemAltitude",
        "kMDItemPixelWidth",
        "kMDItemPixelHeight",
        "kMDItemDurationSeconds",
        "kMDItemAcquisitionModel",
    ]
    cmd = ["mdls"]
    for attr in attrs:
        cmd.extend(["-name", attr])
    cmd.append(str(path))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    out: dict[str, Any] = {}
    for line in (proc.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, val = [part.strip() for part in line.split("=", 1)]
        if val == "(null)":
            continue
        out[key] = val.strip('"')
    return out


def _extract_sips_dims(path: Path) -> tuple[int | None, int | None]:
    """Fallback image dimensions using macOS `sips`."""
    try:
        proc = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        w = h = None
        for line in (proc.stdout or "").splitlines():
            if "pixelWidth:" in line:
                w = int(line.split(":", 1)[1].strip())
            elif "pixelHeight:" in line:
                h = int(line.split(":", 1)[1].strip())
        return w, h
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        return None, None


def _extract_ffprobe(path: Path) -> dict[str, Any]:
    """Extract video dimensions, duration, FPS, rotation, HDR, GPMF/GoPro flag, and QuickTime tags."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}
        data = json.loads(proc.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return {}

    streams = data.get("streams", []) or []
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_gpmf = any(
        (s.get("codec_tag_string") or "").lower() == "gpmd"
        or "gopro" in str((s.get("tags") or {}).get("handler_name") or "").lower()
        for s in streams
    )

    fmt = data.get("format", {}) or {}
    out: dict[str, Any] = {"has_audio": has_audio, "has_gpmf": has_gpmf}
    if fmt.get("duration"):
        try:
            out["duration_sec"] = round(float(fmt["duration"]), 3)
        except ValueError:
            pass

    tags = fmt.get("tags", {}) or {}
    # Case-insensitive tag lookup
    tags_lower = {k.lower(): v for k, v in tags.items()}
    if tags_lower.get("com.apple.quicktime.creationdate"):
        out["qt_keys_creation_date"] = tags_lower["com.apple.quicktime.creationdate"]
    if tags_lower.get("creation_time"):
        out["creation_time"] = tags_lower["creation_time"]
    if tags_lower.get("com.apple.quicktime.model"):
        out["camera_model"] = tags_lower["com.apple.quicktime.model"]
    if tags_lower.get("com.apple.quicktime.location.iso6709"):
        lat, lon, alt = _parse_iso6709(tags_lower["com.apple.quicktime.location.iso6709"])
        if lat is not None and lon is not None:
            out["lat"] = lat
            out["lon"] = lon
        if alt is not None:
            out["altitude_m"] = alt

    if vstream:
        out["width"] = vstream.get("width")
        out["height"] = vstream.get("height")
        fps_val = _parse_fps_fraction(vstream.get("avg_frame_rate")) or _parse_fps_fraction(vstream.get("r_frame_rate"))
        if fps_val is not None:
            out["fps"] = fps_val
        transfer = (vstream.get("color_transfer") or "").lower()
        out["is_hdr"] = transfer in {"arib-std-b67", "smpte2084"}
        out["color_profile"] = (
            "HDR_HLG" if transfer == "arib-std-b67"
            else ("HDR_PQ" if transfer == "smpte2084" else "SDR_Rec709")
        )
        v_tags = {k.lower(): v for k, v in (vstream.get("tags") or {}).items()}
        if "rotate" in v_tags:
            try:
                out["rotation"] = int(v_tags["rotate"])
            except ValueError:
                pass
        for sd in vstream.get("side_data_list", []) or []:
            if "rotation" in sd:
                try:
                    out["rotation"] = int(sd["rotation"])
                except (TypeError, ValueError):
                    pass
    return out


def _resolve_timestamp_tiers_1_to_5(
    path: Path,
    et: dict[str, Any],
    jpeg_exif: dict[str, Any],
    probe: dict[str, Any],
    mdls_data: dict[str, Any],
    mtime: float,
    batch_mtime_suspect: bool = False,
) -> tuple[float | None, str | None, bool]:
    """Execute Tiers 1-5 of the 7-Tier Creation Date Waterfall with GoPro `mvhd` quirk guard.

    Returns `(epoch_or_none, tier_source_label, gopro_mvhd_guard_triggered)`.
    """
    is_gopro = _is_gopro_asset(path, et, probe)

    # Tier 1: QuickTime Keys:CreationDate (carries exact iOS timezone offset)
    for raw in (et.get("CreationDate"), probe.get("qt_keys_creation_date")):
        ep = _parse_iso_to_epoch(raw)
        if ep is not None:
            return ep, "tier1_qt_keys", False

    # Tier 2: EXIF DateTimeOriginal (+ OffsetTimeOriginal when present)
    dto_raw = et.get("DateTimeOriginal")
    if dto_raw:
        off = et.get("OffsetTimeOriginal") or ""
        combined = f"{dto_raw}{off}" if (off and "+" not in str(dto_raw) and "-" not in str(dto_raw)[10:]) else str(dto_raw)
        ep = _parse_iso_to_epoch(combined)
        if ep is not None:
            return ep, "tier2_exif_dto", False
    if jpeg_exif.get("datetime"):
        off = jpeg_exif.get("offset_time") or ""
        ep = _parse_iso_to_epoch(f"{jpeg_exif['datetime']}{off}")
        if ep is not None:
            return ep, "tier2_exif_dto", False

    # Tier 3: QuickTime mvhd CreateDate with GoPro quirk guard & batch-download mtime rejection
    if is_gopro:
        # GoPro writes local wall-clock time into UTC mvhd; never double-shift it
        raw_mvhd = probe.get("creation_time") or et.get("CreateDate") or et.get("MediaCreateDate")
        ep = _parse_iso_to_epoch(raw_mvhd, strip_tz_wallclock=True)
        if ep is not None and not (batch_mtime_suspect and abs(ep - mtime) <= 2.0):
            return ep, "tier3_qt_mvhd_gopro", True
    else:
        for raw in (et.get("CreateDate"), et.get("MediaCreateDate"), probe.get("creation_time")):
            ep = _parse_iso_to_epoch(raw)
            if ep is not None and not (batch_mtime_suspect and abs(ep - mtime) <= 2.0):
                return ep, "tier3_qt_mvhd", False

    # Tier 4: Filename regex parser (PXL_ UTC / VID_ / DJI_)
    fn_ep, fn_tier = _parse_filename_timestamp(path)
    if fn_ep is not None and fn_tier is not None:
        return fn_ep, fn_tier, False

    # Tier 5: macOS mdls kMDItemContentCreationDate (only if |mdls - mtime| > 60s)
    if mdls_data.get("kMDItemContentCreationDate"):
        ep = _parse_iso_to_epoch(mdls_data["kMDItemContentCreationDate"])
        if ep is not None and abs(ep - mtime) > 60.0:
            return ep, "tier5_mdls", False

    return None, None, False


def _apply_tiers_6_and_7(items: list[dict[str, Any]], mtime_by_id: dict[str, float]) -> None:
    """Tier 6: Same-camera sequence interpolation; Tier 7: Unverified mtime fallback."""
    # Group dated items (tiers 1-5) by (parent_dir, sequence_prefix)
    dated_seqs: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for it in items:
        if it["timestamp_epoch"] is None:
            continue
        p = Path(it["source_path"])
        m = SEQUENCE_STEM_RE.match(p.stem)
        if m:
            key = (str(p.parent), m.group(1).upper())
            dated_seqs.setdefault(key, []).append((int(m.group(2)), float(it["timestamp_epoch"])))
    for seq_list in dated_seqs.values():
        seq_list.sort(key=lambda pair: pair[0])

    for it in items:
        if it["timestamp_epoch"] is not None:
            continue
        p = Path(it["source_path"])
        interp_epoch: float | None = None
        m = SEQUENCE_STEM_RE.match(p.stem)
        if m:
            key = (str(p.parent), m.group(1).upper())
            num = int(m.group(2))
            peers = dated_seqs.get(key, [])
            prev_peer = max((pair for pair in peers if pair[0] < num), key=lambda pair: pair[0], default=None)
            next_peer = min((pair for pair in peers if pair[0] > num), key=lambda pair: pair[0], default=None)
            if prev_peer and next_peer and (next_peer[0] - prev_peer[0]) <= 25:
                frac = (num - prev_peer[0]) / float(next_peer[0] - prev_peer[0])
                interp_epoch = prev_peer[1] + (next_peer[1] - prev_peer[1]) * frac
            elif prev_peer and (num - prev_peer[0]) <= 5:
                interp_epoch = prev_peer[1] + 15.0 * (num - prev_peer[0])
            elif next_peer and (next_peer[0] - num) <= 5:
                interp_epoch = next_peer[1] - 15.0 * (next_peer[0] - num)

        if interp_epoch is not None:
            it["timestamp_epoch"] = round(interp_epoch, 3)
            it["timestamp_iso"] = dt.datetime.fromtimestamp(interp_epoch, tz=dt.timezone.utc).isoformat()
            it["timestamp_source"] = "tier6_sequence_interp"
            it["date_verified"] = True
            it["needs_date_confirmation"] = False
        else:
            fallback_ep = mtime_by_id[it["id"]]
            it["timestamp_epoch"] = round(fallback_ep, 3)
            it["timestamp_iso"] = dt.datetime.fromtimestamp(fallback_ep, tz=dt.timezone.utc).isoformat()
            it["timestamp_source"] = "tier7_mtime_unverified"
            it["date_verified"] = False
            it["needs_date_confirmation"] = True


def _analyze_audio_pcm_8khz(path: Path, duration_sec: float, num_bins: int = 120) -> dict[str, Any]:
    """Decode audio via FFmpeg 8 kHz mono 16-bit PCM pipe for RMS waveform, peaks & wind classification."""
    empty_result = {
        "audio_profile": "silent",
        "wind_hazard": False,
        "rms_bins": [0.0] * num_bins,
        "bin_classes": ["silent"] * num_bins,
        "audio_peak_timestamps": [],
        "sonic_postcard_candidates": [],
        "max_rms": 0.0,
    }
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", str(path),
        "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        pcm = proc.stdout or b""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return empty_result

    n_samples = len(pcm) // 2
    if n_samples < 64:
        return empty_result

    # Unpack 16-bit signed LE PCM samples efficiently
    samples = struct.unpack(f"<{n_samples}h", pcm[: n_samples * 2])
    dur = max(0.2, duration_sec if duration_sec > 0 else n_samples / 8000.0)

    rms_bins: list[float] = []
    peak_bins: list[float] = []
    clip_bins: list[float] = []
    zcr_bins: list[float] = []

    for b in range(num_bins):
        i0 = int(b * n_samples / num_bins)
        i1 = max(i0 + 1, int((b + 1) * n_samples / num_bins))
        seg = samples[i0:i1]
        m = len(seg)
        sq_sum = 0.0
        max_abs = 0
        clips = 0
        zcross = 0
        prev_s = seg[0]
        for s in seg:
            a = abs(s)
            sq_sum += float(s * s)
            if a > max_abs:
                max_abs = a
            if a >= 31000:
                clips += 1
            if (s >= 0 > prev_s) or (s < 0 <= prev_s):
                zcross += 1
            prev_s = s
        rms = math.sqrt(sq_sum / m) / 32768.0
        peak = max_abs / 32768.0
        rms_bins.append(round(rms, 4))
        peak_bins.append(peak)
        clip_bins.append(clips / m)
        zcr_bins.append(zcross / max(1, m - 1))

    max_rms = max(rms_bins) if rms_bins else 0.0
    if max_rms < 0.008:
        return empty_result

    sorted_rms = sorted(rms_bins)
    p25 = sorted_rms[int(0.25 * (num_bins - 1))]
    p50 = sorted_rms[int(0.50 * (num_bins - 1))]
    p90 = sorted_rms[int(0.90 * (num_bins - 1))]

    # Wind / clipping classification
    wind_bin_flags: list[bool] = []
    for rms, pk, cr, zcr in zip(rms_bins, peak_bins, clip_bins, zcr_bins):
        crest = pk / max(rms, 1e-5)
        is_wind_bin = (cr > 0.03) or (rms > 0.34 and (crest < 2.25 or zcr < 0.055))
        wind_bin_flags.append(is_wind_bin)

    wind_frac = sum(1 for f in wind_bin_flags if f) / num_bins
    overall_clip = sum(clip_bins) / num_bins
    wind_hazard = (wind_frac >= 0.38) or (overall_clip > 0.025)

    # Dynamic modulation ratio distinguishes speech/laughter bursts from steady ambient noise
    dyn_ratio = p90 / max(p25, 0.004)
    crest_ratio = max_rms / max(p50, 0.004)
    is_speech = (not wind_hazard) and (max_rms >= 0.045) and (dyn_ratio >= 1.85 or crest_ratio >= 2.15)

    if wind_hazard:
        audio_profile = "wind_hazard"
    elif is_speech:
        audio_profile = "speech_laughter"
    else:
        audio_profile = "ambient_music"

    # Per-bin color classes
    peak_thresh = max(0.05, p50 * 1.65)
    bin_classes: list[str] = []
    for rms, is_w in zip(rms_bins, wind_bin_flags):
        if rms < 0.008:
            bin_classes.append("silent")
        elif is_w:
            bin_classes.append("wind_hazard")
        elif is_speech and rms >= peak_thresh:
            bin_classes.append("speech_laughter")
        else:
            bin_classes.append("ambient_music")

    # Find top 2 non-wind audio peak timestamps (separated by >= max(1.5s, 12% dur))
    min_sep_sec = max(1.5, dur * 0.12)
    ranked_bins = sorted(
        [(rms_bins[i], i) for i in range(num_bins) if not wind_bin_flags[i] and rms_bins[i] >= 0.04],
        reverse=True,
    )
    peak_times: list[float] = []
    for _, b_idx in ranked_bins:
        t_sec = round((b_idx + 0.5) * dur / num_bins, 2)
        if all(abs(t_sec - prev_t) >= min_sep_sec for prev_t in peak_times):
            peak_times.append(t_sec)
            if len(peak_times) >= 2:
                break
    peak_times.sort()

    sonic_candidates: list[dict[str, float]] = []
    if audio_profile == "speech_laughter":
        for pt in peak_times:
            sonic_candidates.append(
                {
                    "peak_sec": pt,
                    "in_sec": round(max(0.0, pt - 1.5), 2),
                    "out_sec": round(min(dur, pt + 2.0), 2),
                }
            )

    return {
        "audio_profile": audio_profile,
        "wind_hazard": wind_hazard,
        "rms_bins": rms_bins,
        "bin_classes": bin_classes,
        "audio_peak_timestamps": peak_times,
        "sonic_postcard_candidates": sonic_candidates,
        "max_rms": round(max_rms, 4),
    }


def _render_video_strip_1800x360(
    item: dict[str, Any],
    video_code: str,
    strip_path: Path,
    audio_info: dict[str, Any],
) -> bool:
    """Render a `1800x360` Multi-Frame Video Timeline Strip JPEG via FFmpeg keyframe seek."""
    dur = max(0.5, float(item.get("duration_sec") or 4.0))
    if dur <= 6.0:
        n_frames = 4
    elif dur <= 20.0:
        n_frames = 6
    else:
        n_frames = 8

    sample_times = [
        round(dur * (0.05 + 0.90 * k / max(1, n_frames - 1)), 2)
        for k in range(n_frames)
    ]

    strip_w, strip_h = 1800, 360
    header_h = 36
    rail_h = 240
    wave_y = header_h + rail_h  # 276
    ribbon_h = 8
    bars_y = wave_y + ribbon_h  # 284
    bars_h = strip_h - bars_y   # 76

    src_path = Path(item["source_path"])
    with tempfile.TemporaryDirectory(prefix="trip_strip_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        frame_paths: list[tuple[float, Path]] = []
        for idx, t_sec in enumerate(sample_times):
            out_jpg = tmp_dir / f"f_{idx:02d}.jpg"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", f"{t_sec:.3f}",
                    "-i", str(src_path),
                    "-frames:v", "1",
                    "-vf", "scale=450:450:force_original_aspect_ratio=decrease",
                    "-q:v", "4",
                    str(out_jpg),
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if out_jpg.is_file():
                frame_paths.append((t_sec, out_jpg))

        if not frame_paths:
            return False

        # Build header text
        w_px = item.get("width") or "?"
        h_px = item.get("height") or "?"
        orient_lbl = str(item.get("orientation") or "unknown").capitalize()
        fps_val = item.get("fps")
        fps_str = f"{fps_val:.0f}fps" if isinstance(fps_val, (int, float)) else "?fps"
        if item.get("slow_mo_eligible"):
            fps_str += " [SLOW-MO]"
        hdr_tag = " | HDR" if item.get("is_hdr") else ""
        date_short = str(item.get("timestamp_iso") or "")[:16].replace("T", " ")
        if not item.get("date_verified", True):
            date_short += " [DATE?]"
        aud_lbl = audio_info.get("audio_profile", "silent")
        if audio_info.get("wind_hazard"):
            aud_lbl += " [WIND]"
        elif audio_info.get("sonic_postcard_candidates"):
            peaks_str = ",".join(f"{t:.1f}s" for t in audio_info.get("audio_peak_timestamps", []))
            aud_lbl += f" [SONIC {peaks_str}]"

        header_text = (
            f"#{video_code} | {item['filename'][:34]} | {dur:.1f}s | "
            f"{w_px}x{h_px} {orient_lbl} | {fps_str}{hdr_tag} | {date_short} | {aud_lbl}"
        )

        # Primary: Pillow exact 1800x360 composite
        try:
            from PIL import Image, ImageDraw  # type: ignore

            canvas = Image.new("RGB", (strip_w, strip_h), (16, 18, 24))
            draw = ImageDraw.Draw(canvas)

            # Top 36px dark header
            draw.rectangle([0, 0, strip_w, header_h], fill=(20, 22, 30))
            draw.line([(0, header_h - 1), (strip_w, header_h - 1)], fill=(48, 54, 68), width=1)
            draw.text((14, 11), header_text, fill=(238, 236, 230))

            # Middle 240px upright frame rail
            n_actual = len(frame_paths)
            cell_w = strip_w // n_actual
            for idx, (t_sec, fp) in enumerate(frame_paths):
                x0 = idx * cell_w
                x1 = strip_w if idx == n_actual - 1 else (idx + 1) * cell_w
                cw = x1 - x0
                draw.rectangle([x0, header_h, x1, wave_y], fill=(24, 26, 34))
                try:
                    with Image.open(fp) as im:
                        im = im.convert("RGB")
                        im.thumbnail((cw - 6, rail_h - 6))
                        px = x0 + (cw - im.width) // 2
                        py = header_h + (rail_h - im.height) // 2
                        canvas.paste(im, (px, py))
                except Exception:  # noqa: BLE001
                    pass
                # Crisp vertical divider + bottom-left timestamp pill
                draw.line([(x0, header_h), (x0, wave_y)], fill=(42, 46, 58), width=1)
                pill = f"{t_sec:.1f}s"
                draw.rectangle([x0 + 6, wave_y - 24, x0 + 58, wave_y - 4], fill=(10, 12, 16))
                draw.text((x0 + 11, wave_y - 20), pill, fill=(242, 206, 112))

            # Bottom 84px Audio RMS Waveform Bar
            draw.rectangle([0, wave_y, strip_w, strip_h], fill=(14, 16, 22))
            draw.line([(0, wave_y), (strip_w, wave_y)], fill=(52, 58, 72), width=1)

            rms_bins: list[float] = audio_info.get("rms_bins") or []
            bin_classes: list[str] = audio_info.get("bin_classes") or []
            n_bins = max(1, len(rms_bins))
            scale_max = max(0.25, max(rms_bins) if rms_bins else 0.25)

            palette = {
                "silent": (96, 102, 114),
                "ambient_music": (56, 178, 172),
                "speech_laughter": (242, 198, 92),
                "wind_hazard": (224, 72, 72),
            }

            for b in range(len(rms_bins)):
                bx0 = int(b * strip_w / n_bins)
                bx1 = max(bx0 + 2, int((b + 1) * strip_w / n_bins) - 1)
                cls = bin_classes[b] if b < len(bin_classes) else "silent"
                col = palette.get(cls, (96, 102, 114))

                # Top 8px status ribbon
                draw.rectangle([bx0, wave_y + 1, bx1, wave_y + ribbon_h - 1], fill=col)

                # RMS bar
                norm_h = min(1.0, rms_bins[b] / scale_max)
                bar_px = max(2, int(round(norm_h * (bars_h - 12))))
                by0 = strip_h - 4 - bar_px
                draw.rectangle([bx0, by0, bx1, strip_h - 4], fill=col)

                # Red hatch overlay for wind hazard bins
                if cls == "wind_hazard":
                    draw.line([(bx0, by0), (bx1, strip_h - 4)], fill=(255, 220, 220), width=1)

            # Gold peak tick markers for top audio peaks
            for pt in audio_info.get("audio_peak_timestamps", []):
                px = int(max(4, min(strip_w - 50, (pt / dur) * strip_w)))
                draw.line([(px, wave_y), (px, strip_h)], fill=(255, 224, 120), width=2)
                draw.rectangle([px + 3, bars_y + 2, px + 48, bars_y + 18], fill=(28, 24, 12))
                draw.text((px + 6, bars_y + 4), f"{pt:.1f}s", fill=(255, 224, 120))

            strip_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(strip_path, quality=86)
            return True
        except ImportError:
            # Pure-FFmpeg 1800x360 strip fallback when Pillow is absent
            n_actual = len(frame_paths)
            cell_w = strip_w // n_actual
            inputs: list[str] = []
            fc_parts: list[str] = []
            for idx, (_, fp) in enumerate(frame_paths):
                inputs.extend(["-i", str(fp)])
                cw = strip_w - cell_w * (n_actual - 1) if idx == n_actual - 1 else cell_w
                fc_parts.append(
                    f"[{idx}:v]scale={cw}:{rail_h}:force_original_aspect_ratio=decrease,"
                    f"pad={cw}:{rail_h}:(ow-iw)/2:(oh-ih)/2:color=0x181A22[f{idx}]"
                )
            h_in = "".join(f"[f{i}]" for i in range(n_actual))
            safe_hdr = header_text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
            if n_actual == 1:
                stack_expr = f"[f0]pad={strip_w}:{strip_h}:0:{header_h}:color=0x101218"
            else:
                stack_expr = f"{h_in}hstack=inputs={n_actual},pad={strip_w}:{strip_h}:0:{header_h}:color=0x101218"
            fc = ";".join(fc_parts) + f";{stack_expr},drawtext=text='{safe_hdr}':fontcolor=0xEEEDE6:fontsize=16:x=14:y=10[out]"
            strip_path.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-q:v", "4", str(strip_path)],
                capture_output=True,
                timeout=30,
                check=False,
            )
            return proc.returncode == 0 and strip_path.is_file()


def _generate_video_strips(
    items: list[dict[str, Any]],
    out_dir: Path,
    has_ffmpeg: bool,
    batch_start: int | None = None,
    batch_end: int | None = None,
) -> list[str]:
    """Generate `1800x360` Timeline Strips and audio RMS metadata for video assets."""
    if not has_ffmpeg:
        return []
    strips_dir = out_dir / "clip_strips"
    strips_dir.mkdir(parents=True, exist_ok=True)

    videos = [it for it in items if it["media_type"] == "video"]
    start_idx = max(0, batch_start or 0)
    end_idx = len(videos) if batch_end is None else min(len(videos), batch_end)

    generated: list[str] = []
    for v_pos in range(start_idx, end_idx):
        item = videos[v_pos]
        v_code = f"V{v_pos + 1:03d}"
        item["video_code"] = v_code
        dur = float(item.get("duration_sec") or 0.0)
        audio_info = _analyze_audio_pcm_8khz(Path(item["source_path"]), dur)
        item["audio_profile"] = audio_info["audio_profile"]
        item["wind_hazard"] = audio_info["wind_hazard"]
        item["audio_peak_timestamps"] = audio_info["audio_peak_timestamps"]
        item["sonic_postcard_candidates"] = audio_info["sonic_postcard_candidates"]

        strip_path = strips_dir / f"strip_{v_code}.jpg"
        if _render_video_strip_1800x360(item, v_code, strip_path, audio_info):
            item["video_strip_path"] = str(strip_path)
            generated.append(str(strip_path))
    return generated


def _compute_dhash(path: Path) -> str | None:
    """Compute a 64-bit difference hash when Pillow is available."""
    try:
        from PIL import Image, ImageOps  # type: ignore

        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("L").resize((9, 8))
            pixels = list(img.getdata())
        bits = 0
        for row in range(8):
            row_start = row * 9
            for col in range(8):
                bits = (bits << 1) | (1 if pixels[row_start + col] > pixels[row_start + col + 1] else 0)
        return f"{bits:016x}"
    except Exception:  # noqa: BLE001
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def st_dbscan_chapters(
    items: list[dict[str, Any]],
    eps_km: float = 15.0,
    eps_hours: float = 8.0,
    min_samples: int = 2,
) -> list[dict[str, Any]]:
    """Cluster sorted media items into chapters via Haversine + temporal ST-DBSCAN.

    Items without GPS fall back to temporal proximity against the nearest
    chronological neighbors so GPS-less DSLR photos merge into the right chapter.
    """
    n = len(items)
    if n == 0:
        return []

    eps_sec = eps_hours * 3600.0
    labels = [-2] * n  # -2 unvisited, -1 noise, >=0 cluster id

    def neighbors(idx: int) -> list[int]:
        a = items[idx]
        t_a = a["timestamp_epoch"]
        lat_a, lon_a = a.get("lat"), a.get("lon")
        out: list[int] = []
        for j, b in enumerate(items):
            if abs(b["timestamp_epoch"] - t_a) > eps_sec:
                continue
            lat_b, lon_b = b.get("lat"), b.get("lon")
            if lat_a is not None and lon_a is not None and lat_b is not None and lon_b is not None:
                if _haversine_km(lat_a, lon_a, lat_b, lon_b) <= eps_km:
                    out.append(j)
            else:
                out.append(j)
        return out

    cluster_id = 0
    for i in range(n):
        if labels[i] != -2:
            continue
        nbrs = neighbors(i)
        if len(nbrs) < min_samples:
            labels[i] = -1
            continue
        labels[i] = cluster_id
        queue = list(nbrs)
        seen = set(nbrs)
        while queue:
            curr = queue.pop(0)
            if labels[curr] == -1:
                labels[curr] = cluster_id
            if labels[curr] != -2:
                continue
            labels[curr] = cluster_id
            curr_nbrs = neighbors(curr)
            if len(curr_nbrs) >= min_samples:
                for nxt in curr_nbrs:
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
        cluster_id += 1

    for i in range(n):
        if labels[i] == -1:
            assigned = None
            best_dt = float("inf")
            for j in range(n):
                if labels[j] >= 0:
                    delta = abs(items[j]["timestamp_epoch"] - items[i]["timestamp_epoch"])
                    if delta < best_dt and delta <= eps_sec:
                        best_dt = delta
                        assigned = labels[j]
            if assigned is not None:
                labels[i] = assigned
            else:
                labels[i] = cluster_id
                cluster_id += 1

    chapters_map: dict[int, list[dict[str, Any]]] = {}
    for item, lbl in zip(items, labels):
        item["chapter_raw_id"] = lbl
        chapters_map.setdefault(lbl, []).append(item)

    ordered = sorted(
        chapters_map.values(),
        key=lambda grp: min(x["timestamp_epoch"] for x in grp),
    )
    chapters: list[dict[str, Any]] = []
    for idx, grp in enumerate(ordered, start=1):
        ch_id = f"chapter_{idx:02d}"
        lats = [x["lat"] for x in grp if x.get("lat") is not None]
        lons = [x["lon"] for x in grp if x.get("lon") is not None]
        alts = [x["altitude_m"] for x in grp if x.get("altitude_m") is not None]
        for x in grp:
            x["chapter_id"] = ch_id
        chapters.append(
            {
                "chapter_id": ch_id,
                "start_iso": grp[0]["timestamp_iso"],
                "end_iso": grp[-1]["timestamp_iso"],
                "item_count": len(grp),
                "photo_count": sum(1 for x in grp if x["media_type"] == "photo"),
                "video_count": sum(1 for x in grp if x["media_type"] == "video"),
                "centroid": {
                    "lat": round(sum(lats) / len(lats), 6) if lats else None,
                    "lon": round(sum(lons) / len(lons), 6) if lons else None,
                    "elevation_m": round(sum(alts) / len(alts), 1) if alts else None,
                },
                "item_ids": [x["id"] for x in grp],
            }
        )
    return chapters


def _generate_contact_sheets(
    chapters: list[dict[str, Any]],
    items_by_id: dict[str, dict[str, Any]],
    out_dir: Path,
    has_ffmpeg: bool,
    photos_only: bool = False,
) -> list[str]:
    """Render 3x3 numbered contact sheets per chapter for Gate 1 review."""
    sheets_dir = out_dir / "contact_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    has_pil = False
    try:
        from PIL import Image, ImageDraw  # type: ignore

        has_pil = True
    except ImportError:
        pass

    for ch in chapters:
        ch_id = ch["chapter_id"]
        ch_items = [items_by_id[i] for i in ch["item_ids"]]
        if photos_only:
            ch_items = [x for x in ch_items if x["media_type"] == "photo"]
        if not ch_items:
            continue
        for page_idx in range(0, len(ch_items), 9):
            chunk = ch_items[page_idx : page_idx + 9]
            page_num = page_idx // 9 + 1
            suffix = f"_p{page_num}" if len(ch_items) > 9 else ""
            sheet_path = sheets_dir / f"{ch_id}{suffix}.jpg"

            if has_pil:
                cell_w, cell_h = 426, 240
                canvas = Image.new("RGB", (cell_w * 3, cell_h * 3), (18, 18, 22))
                draw = ImageDraw.Draw(canvas)
                for slot, item in enumerate(chunk):
                    row, col = divmod(slot, 3)
                    x0, y0 = col * cell_w, row * cell_h
                    thumb = _load_thumb_pil(Path(item["source_path"]), item["media_type"], (cell_w - 8, cell_h - 28), has_ffmpeg)
                    if thumb is not None:
                        canvas.paste(thumb, (x0 + 4, y0 + 4))
                    label = f"#{item['index']:03d} [{item['orientation'][0].upper()}] {item['filename'][:22]}"
                    draw.rectangle([x0 + 4, y0 + cell_h - 24, x0 + cell_w - 4, y0 + cell_h - 4], fill=(10, 10, 14))
                    draw.text((x0 + 8, y0 + cell_h - 20), label, fill=(235, 235, 240))
                canvas.save(sheet_path, quality=88)
                generated.append(str(sheet_path))
            elif has_ffmpeg:
                if _render_sheet_ffmpeg(chunk, sheet_path):
                    generated.append(str(sheet_path))
    return generated


def _load_thumb_pil(path: Path, media_type: str, size: tuple[int, int], has_ffmpeg: bool):
    from PIL import Image, ImageOps  # type: ignore

    if media_type == "photo":
        try:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                img.thumbnail(size)
                bg = Image.new("RGB", size, (28, 28, 32))
                bg.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
                return bg
        except Exception:  # noqa: BLE001
            return None
    if media_type == "video" and has_ffmpeg:
        tmp = path.parent / f".tmp_thumb_{hashlib.md5(str(path).encode()).hexdigest()[:8]}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "0.5", "-i", str(path), "-frames:v", "1", "-q:v", "4", str(tmp)],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if tmp.exists():
                with Image.open(tmp) as img:
                    img = img.convert("RGB")
                    img.thumbnail(size)
                    bg = Image.new("RGB", size, (28, 28, 32))
                    bg.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
                    return bg
        except Exception:  # noqa: BLE001
            pass
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
    return None


def _render_sheet_ffmpeg(chunk: list[dict[str, Any]], sheet_path: Path) -> bool:
    """FFmpeg-only 3x3 contact sheet fallback when Pillow is absent."""
    inputs: list[str] = []
    filter_parts: list[str] = []
    for idx, item in enumerate(chunk):
        if item["media_type"] == "video":
            inputs.extend(["-ss", "0.5", "-i", item["source_path"]])
        else:
            inputs.extend(["-i", item["source_path"]])
        filter_parts.append(
            f"[{idx}:v]scale=426:240:force_original_aspect_ratio=decrease,"
            f"pad=426:240:(ow-iw)/2:(oh-ih)/2:color=121216[v{idx}]"
        )
    concat_inputs = "".join(f"[v{i}]" for i in range(len(chunk)))
    filter_complex = ";".join(filter_parts) + f";{concat_inputs}xstack=inputs={len(chunk)}:layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0|0_h0+h1|w0_h0+h1|w0+w1_h0+h1[out]"
    if len(chunk) == 1:
        filter_complex = filter_parts[0].replace("[v0]", "[out]")
    elif len(chunk) < 9:
        filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={len(chunk)}:v=1:a=0,tile=3x3[out]"
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]", "-frames:v", "1", str(sheet_path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
    return proc.returncode == 0 and sheet_path.exists()


def _collect_paths(input_dir: str | None, file_list: str | None) -> list[Path]:
    paths: list[Path] = []
    if input_dir:
        root = Path(input_dir).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Input directory not found: {root}")
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS and not p.name.startswith("."):
                paths.append(p)
    if file_list:
        list_path = Path(file_list).expanduser().resolve()
        for line in list_path.read_text(encoding="utf-8").splitlines():
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            p = Path(cleaned).expanduser().resolve()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                paths.append(p)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def ingest_trip(
    paths: list[Path],
    out_dir: Path,
    eps_km: float = 15.0,
    eps_hours: float = 8.0,
    min_samples: int = 2,
    skip_sheets: bool = False,
    video_strips: bool = False,
    batch_start: int | None = None,
    batch_end: int | None = None,
) -> dict[str, Any]:
    """Extract metadata (7-tier dates, FPS/slow-mo, altitude), cluster chapters, and build sheets/strips."""
    has_exiftool = _tool_exists(["exiftool", "-ver"])
    has_ffprobe = _tool_exists(["ffprobe", "-version"])
    has_ffmpeg = _tool_exists(["ffmpeg", "-version"])
    has_mdls = _tool_exists(["mdls", "--version"])
    has_sips = _tool_exists(["sips", "--version"])

    exif_map = _extract_exiftool_batch(paths) if has_exiftool else {}

    # Detect batch-download mtime clusters (>=3 files sharing the same 60-second mtime bucket)
    mtime_buckets: dict[int, int] = {}
    stats_by_path: dict[Path, os.stat_result] = {}
    for p in paths:
        st = p.stat()
        stats_by_path[p] = st
        bucket = int(st.st_mtime // 60)
        mtime_buckets[bucket] = mtime_buckets.get(bucket, 0) + 1

    items: list[dict[str, Any]] = []
    mtime_by_id: dict[str, float] = {}
    seen_names: dict[str, str] = {}
    seen_sizes: dict[int, str] = {}
    seen_hashes: dict[str, str] = {}

    for p in paths:
        abs_p = str(p.resolve())
        ext = p.suffix.lower()
        media_type = "video" if ext in VIDEO_EXTS else "photo"
        stat = stats_by_path[p]
        et = exif_map.get(abs_p, {})
        batch_mtime_suspect = mtime_buckets.get(int(stat.st_mtime // 60), 0) >= 3

        jpeg_exif = _read_jpeg_exif_minimal(p) if (ext in {".jpg", ".jpeg"}) else {}
        probe = _extract_ffprobe(p) if (media_type == "video" and has_ffprobe) else {}
        mdls_data = _extract_macos_mdls(p) if (not et and has_mdls) else {}

        epoch, ts_source, gopro_guard = _resolve_timestamp_tiers_1_to_5(
            path=p,
            et=et,
            jpeg_exif=jpeg_exif,
            probe=probe,
            mdls_data=mdls_data,
            mtime=stat.st_mtime,
            batch_mtime_suspect=batch_mtime_suspect,
        )
        iso_ts = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).isoformat() if epoch is not None else None

        lat = et.get("GPSLatitude")
        if lat is None:
            lat = probe.get("lat") if probe.get("lat") is not None else jpeg_exif.get("lat")
        lon = et.get("GPSLongitude")
        if lon is None:
            lon = probe.get("lon") if probe.get("lon") is not None else jpeg_exif.get("lon")
        if lat is None and mdls_data.get("kMDItemLatitude"):
            try:
                lat = round(float(mdls_data["kMDItemLatitude"]), 6)
                lon = round(float(mdls_data["kMDItemLongitude"]), 6)
            except (TypeError, ValueError):
                lat, lon = None, None

        altitude_m: float | None = None
        if et.get("GPSAltitude") is not None:
            try:
                alt_val = float(et["GPSAltitude"])
                if str(et.get("GPSAltitudeRef", "0")).strip() == "1":
                    alt_val = -abs(alt_val)
                altitude_m = round(alt_val, 1)
            except (TypeError, ValueError):
                pass
        if altitude_m is None and probe.get("altitude_m") is not None:
            altitude_m = float(probe["altitude_m"])
        if altitude_m is None and jpeg_exif.get("altitude_m") is not None:
            altitude_m = float(jpeg_exif["altitude_m"])
        if altitude_m is None and mdls_data.get("kMDItemAltitude"):
            try:
                altitude_m = round(float(mdls_data["kMDItemAltitude"]), 1)
            except (TypeError, ValueError):
                pass

        width = et.get("ImageWidth") or probe.get("width")
        height = et.get("ImageHeight") or probe.get("height")
        dims_from_mdls = False
        if (width is None or height is None) and mdls_data.get("kMDItemPixelWidth"):
            try:
                width = int(mdls_data["kMDItemPixelWidth"])
                height = int(mdls_data["kMDItemPixelHeight"])
                dims_from_mdls = True
            except (TypeError, ValueError):
                pass
        if (width is None or height is None) and media_type == "photo" and has_sips:
            width, height = _extract_sips_dims(p)

        exif_orient = et.get("Orientation") or jpeg_exif.get("exif_orientation")
        rotation = abs(int(et.get("Rotation") or probe.get("rotation") or 0))
        if width and height:
            if not dims_from_mdls and (exif_orient in (5, 6, 7, 8) or rotation in (90, 270)):
                width, height = height, width
            orientation = "portrait" if height > width else ("landscape" if width > height else "square")
        else:
            orientation = "unknown"

        duration_sec = et.get("Duration") or probe.get("duration_sec")
        if duration_sec is None and mdls_data.get("kMDItemDurationSeconds"):
            try:
                duration_sec = round(float(mdls_data["kMDItemDurationSeconds"]), 3)
            except ValueError:
                pass

        fps_val: float | None = None
        if media_type == "video":
            fps_val = _parse_fps_fraction(probe.get("fps")) or _parse_fps_fraction(et.get("VideoFrameRate"))
        slow_mo_eligible = bool(fps_val is not None and fps_val >= 50.0)

        dhash = _compute_dhash(p) if media_type == "photo" else None
        dup_reasons: list[str] = []
        if p.name in seen_names:
            dup_reasons.append(f"same_filename:{seen_names[p.name]}")
        if stat.st_size in seen_sizes:
            dup_reasons.append(f"same_size:{seen_sizes[stat.st_size]}")
        if dhash and dhash in seen_hashes:
            dup_reasons.append(f"perceptual_hash:{seen_hashes[dhash]}")

        item_id = f"media_{len(items) + 1:04d}"
        mtime_by_id[item_id] = stat.st_mtime
        seen_names.setdefault(p.name, item_id)
        seen_sizes.setdefault(stat.st_size, item_id)
        if dhash:
            seen_hashes.setdefault(dhash, item_id)

        items.append(
            {
                "id": item_id,
                "source_path": abs_p,
                "filename": p.name,
                "media_type": media_type,
                "file_size_bytes": stat.st_size,
                "timestamp_epoch": round(epoch, 3) if epoch is not None else None,
                "timestamp_iso": iso_ts,
                "timestamp_source": ts_source,
                "date_verified": epoch is not None,
                "needs_date_confirmation": epoch is None,
                "gopro_mvhd_guard": gopro_guard,
                "lat": float(lat) if lat is not None else None,
                "lon": float(lon) if lon is not None else None,
                "altitude_m": altitude_m,
                "width": int(width) if width else None,
                "height": int(height) if height else None,
                "orientation": orientation,
                "duration_sec": float(duration_sec) if duration_sec is not None else None,
                "fps": fps_val,
                "slow_mo_eligible": slow_mo_eligible,
                "is_hdr": bool(probe.get("is_hdr", False)),
                "color_profile": probe.get("color_profile") if media_type == "video" else "SDR_Photo",
                "camera_model": et.get("Model") or probe.get("camera_model") or mdls_data.get("kMDItemAcquisitionModel"),
                "dhash": dhash,
                "duplicate_flags": dup_reasons,
                "locked_by_user": False,
                "has_human_closeup": False,
            }
        )

    # Complete 7-Tier Date Waterfall (Tier 6 sequence interpolation + Tier 7 mtime fallback)
    _apply_tiers_6_and_7(items, mtime_by_id)

    items.sort(key=lambda x: (x["timestamp_epoch"], x["filename"]))
    for idx, item in enumerate(items, start=1):
        item["index"] = idx

    chapters = st_dbscan_chapters(items, eps_km=eps_km, eps_hours=eps_hours, min_samples=min_samples)
    items_by_id = {x["id"]: x for x in items}

    generated_strips: list[str] = []
    if video_strips:
        generated_strips = _generate_video_strips(
            items=items,
            out_dir=out_dir,
            has_ffmpeg=has_ffmpeg,
            batch_start=batch_start,
            batch_end=batch_end,
        )

    contact_sheets: list[str] = []
    if not skip_sheets and chapters:
        contact_sheets = _generate_contact_sheets(
            chapters=chapters,
            items_by_id=items_by_id,
            out_dir=out_dir,
            has_ffmpeg=has_ffmpeg,
            photos_only=video_strips,
        )

    manifest = {
        "schema_version": "2.0",
        "extractor": "exiftool+QuickTimeUTC+ffprobe" if has_exiftool else "zero-dep-exif-ffprobe-mdls-sips",
        "summary": {
            "total_assets": len(items),
            "photos": sum(1 for x in items if x["media_type"] == "photo"),
            "videos": sum(1 for x in items if x["media_type"] == "video"),
            "portrait_count": sum(1 for x in items if x["orientation"] == "portrait"),
            "landscape_count": sum(1 for x in items if x["orientation"] == "landscape"),
            "slow_mo_eligible_count": sum(1 for x in items if x.get("slow_mo_eligible")),
            "hdr_count": sum(1 for x in items if x.get("is_hdr")),
            "wind_hazard_count": sum(1 for x in items if x.get("wind_hazard")),
            "sonic_postcard_candidates": sum(1 for x in items if x.get("sonic_postcard_candidates")),
            "needs_date_confirmation_count": sum(1 for x in items if x.get("needs_date_confirmation")),
            "duplicate_candidates": sum(1 for x in items if x["duplicate_flags"]),
            "chapter_count": len(chapters),
            "video_strips_generated": len(generated_strips),
        },
        "chapters": chapters,
        "contact_sheets": contact_sheets,
        "video_strips": generated_strips,
        "needs_date_confirmation_ids": [x["id"] for x in items if x.get("needs_date_confirmation")],
        "assets": items,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest trip photos/videos with 7-tier date waterfall, ST-DBSCAN chapters, contact sheets, and 1800x360 Video Timeline Strips."
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input-dir", help="Local folder or pre-downloaded Drive folder of trip media.")
    src.add_argument("--file-list", help="Text file with one media path per line.")
    parser.add_argument("--work-dir", default="./work", help="Output working directory (default: ./work).")
    parser.add_argument("--eps-km", type=float, default=15.0, help="ST-DBSCAN spatial radius in km (default: 15.0).")
    parser.add_argument("--eps-hours", type=float, default=8.0, help="ST-DBSCAN temporal window in hours (default: 8.0).")
    parser.add_argument("--min-samples", type=int, default=2, help="ST-DBSCAN min samples per chapter (default: 2).")
    parser.add_argument("--skip-sheets", action="store_true", help="Skip rendering 3x3 contact sheets.")
    parser.add_argument(
        "--video-strips",
        action="store_true",
        help="Generate 1800x360 Multi-Frame Video Timeline Strips with 8 kHz PCM audio RMS waveform & wind classification.",
    )
    parser.add_argument("--batch-start", type=int, default=None, help="Optional 0-based start video index for batched --video-strips.")
    parser.add_argument("--batch-end", type=int, default=None, help="Optional exclusive end video index for batched --video-strips.")
    args = parser.parse_args()

    paths = _collect_paths(args.input_dir, args.file_list)
    manifest = ingest_trip(
        paths=paths,
        out_dir=Path(args.work_dir).resolve(),
        eps_km=args.eps_km,
        eps_hours=args.eps_hours,
        min_samples=args.min_samples,
        skip_sheets=args.skip_sheets,
        video_strips=args.video_strips,
        batch_start=args.batch_start,
        batch_end=args.batch_end,
    )
    print(json.dumps(manifest["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
