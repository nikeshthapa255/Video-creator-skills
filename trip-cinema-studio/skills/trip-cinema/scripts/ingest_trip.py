#!/usr/bin/env python3
"""Deterministic media ingestion, chapter clustering, and contact-sheet generator.

Supports `--input-dir`, `--file-list`, or a pre-downloaded Drive folder.
Uses ExifTool (`-api QuickTimeUTC` + `Keys:CreationDate` UTC normalization)
when available, with a zero-dependency fallback (pure-Python JPEG EXIF +
macOS `mdls`/`sips` + optional `ffprobe`).

Clusters media into chronological/geographic chapters using Haversine +
temporal ST-DBSCAN (with temporal-only fallback for GPS-less DSLR photos),
flags duplicates and portrait vs landscape assets, and renders 3x3 numbered
contact sheets for Gate 1 visual review.
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
from pathlib import Path
from typing import Any

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
    ".tif", ".tiff", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".raf",
}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS


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


def _parse_iso_to_epoch(raw: str | None) -> float | None:
    """Parse ExifTool, EXIF, or mdls timestamp strings into UTC epoch seconds."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text.startswith("0000:00:00"):
        return None
    # Normalize EXIF "YYYY:MM:DD HH:MM:SS" -> "YYYY-MM-DD HH:MM:SS"
    if re.match(r"^\d{4}:\d{2}:\d{2}\s", text):
        text = text[:10].replace(":", "-") + text[10:]
    # Handle trailing " +0000" from mdls
    text = re.sub(r"\s+([+-]\d{2})(\d{2})$", r"\1:\2", text)
    text = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _read_jpeg_exif_minimal(path: Path) -> dict[str, Any]:
    """Zero-dependency minimal JPEG APP1 EXIF reader (DateTimeOriginal, Orientation, GPS)."""
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
    return out


def _extract_exiftool_batch(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Run ExifTool once in batch mode with QuickTimeUTC + Keys:CreationDate."""
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
        "-CreateDate",
        "-MediaCreateDate",
        "-GPSLatitude",
        "-GPSLongitude",
        "-ImageWidth",
        "-ImageHeight",
        "-Orientation",
        "-Duration",
        "-Rotation",
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
    """Extract video dimensions, duration, rotation, and HDR transfer info."""
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
    vstream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    fmt = data.get("format", {})
    out: dict[str, Any] = {}
    if fmt.get("duration"):
        try:
            out["duration_sec"] = round(float(fmt["duration"]), 3)
        except ValueError:
            pass
    tags = fmt.get("tags", {}) or {}
    if tags.get("creation_time"):
        out["creation_time"] = tags["creation_time"]
    if vstream:
        out["width"] = vstream.get("width")
        out["height"] = vstream.get("height")
        transfer = (vstream.get("color_transfer") or "").lower()
        out["is_hdr"] = transfer in {"arib-std-b67", "smpte2084"}
        for sd in vstream.get("side_data_list", []) or []:
            if "rotation" in sd:
                out["rotation"] = int(sd["rotation"])
    return out


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
                # Temporal fallback when either asset lacks GPS (e.g., DSLR RAW/JPG)
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

    # Assign any remaining noise points to the nearest chronological cluster, or own chapter
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
) -> list[str]:
    """Render 3x3 numbered contact sheets per chapter for Gate 1 review."""
    sheets_dir = out_dir / "contact_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    has_pil = False
    try:
        from PIL import Image, ImageDraw, ImageOps  # type: ignore

        has_pil = True
    except ImportError:
        pass

    for ch in chapters:
        ch_id = ch["chapter_id"]
        ch_items = [items_by_id[i] for i in ch["item_ids"]]
        # Chunk into groups of 9 (3x3)
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
        # Simpler tile filter via concat for partial pages
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
    # Deduplicate exact resolved paths while preserving order
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
) -> dict[str, Any]:
    """Extract metadata, cluster chapters, flag duplicates, and build contact sheets."""
    has_exiftool = _tool_exists(["exiftool", "-ver"])
    has_ffprobe = _tool_exists(["ffprobe", "-version"])
    has_ffmpeg = _tool_exists(["ffmpeg", "-version"])
    has_mdls = _tool_exists(["mdls", "--version"])
    has_sips = _tool_exists(["sips", "--version"])

    exif_map = _extract_exiftool_batch(paths) if has_exiftool else {}

    items: list[dict[str, Any]] = []
    seen_names: dict[str, str] = {}
    seen_sizes: dict[int, str] = {}
    seen_hashes: dict[str, str] = {}

    for p in paths:
        abs_p = str(p.resolve())
        ext = p.suffix.lower()
        media_type = "video" if ext in VIDEO_EXTS else "photo"
        stat = p.stat()
        et = exif_map.get(abs_p, {})

        # Timestamp resolution: ExifTool QuickTimeUTC/Keys:CreationDate -> JPEG EXIF -> ffprobe -> mdls -> mtime
        epoch: float | None = None
        for key in ("CreationDate", "DateTimeOriginal", "CreateDate", "MediaCreateDate"):
            epoch = _parse_iso_to_epoch(et.get(key))
            if epoch is not None:
                break

        jpeg_exif = _read_jpeg_exif_minimal(p) if (not et and ext in {".jpg", ".jpeg"}) else {}
        if epoch is None and jpeg_exif.get("datetime"):
            epoch = _parse_iso_to_epoch(jpeg_exif["datetime"])

        probe = _extract_ffprobe(p) if (media_type == "video" and has_ffprobe) else {}
        if epoch is None and probe.get("creation_time"):
            epoch = _parse_iso_to_epoch(probe["creation_time"])

        mdls_data = _extract_macos_mdls(p) if (not et and has_mdls) else {}
        if epoch is None and mdls_data.get("kMDItemContentCreationDate"):
            epoch = _parse_iso_to_epoch(mdls_data["kMDItemContentCreationDate"])

        if epoch is None:
            epoch = stat.st_mtime

        iso_ts = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc).isoformat()

        lat = et.get("GPSLatitude") or jpeg_exif.get("lat")
        lon = et.get("GPSLongitude") or jpeg_exif.get("lon")
        if lat is None and mdls_data.get("kMDItemLatitude"):
            try:
                lat = round(float(mdls_data["kMDItemLatitude"]), 6)
                lon = round(float(mdls_data["kMDItemLongitude"]), 6)
            except (TypeError, ValueError):
                lat, lon = None, None

        width = et.get("ImageWidth") or probe.get("width")
        height = et.get("ImageHeight") or probe.get("height")
        if (width is None or height is None) and mdls_data.get("kMDItemPixelWidth"):
            try:
                width = int(mdls_data["kMDItemPixelWidth"])
                height = int(mdls_data["kMDItemPixelHeight"])
            except (TypeError, ValueError):
                pass
        if (width is None or height is None) and media_type == "photo" and has_sips:
            width, height = _extract_sips_dims(p)

        exif_orient = et.get("Orientation") or jpeg_exif.get("exif_orientation")
        rotation = abs(int(et.get("Rotation") or probe.get("rotation") or 0))
        if width and height:
            if exif_orient in (5, 6, 7, 8) or rotation in (90, 270):
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

        dhash = _compute_dhash(p) if media_type == "photo" else None
        dup_reasons: list[str] = []
        if p.name in seen_names:
            dup_reasons.append(f"same_filename:{seen_names[p.name]}")
        if stat.st_size in seen_sizes:
            dup_reasons.append(f"same_size:{seen_sizes[stat.st_size]}")
        if dhash and dhash in seen_hashes:
            dup_reasons.append(f"perceptual_hash:{seen_hashes[dhash]}")

        item_id = f"media_{len(items) + 1:04d}"
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
                "timestamp_epoch": round(epoch, 3),
                "timestamp_iso": iso_ts,
                "lat": float(lat) if lat is not None else None,
                "lon": float(lon) if lon is not None else None,
                "width": int(width) if width else None,
                "height": int(height) if height else None,
                "orientation": orientation,
                "duration_sec": float(duration_sec) if duration_sec is not None else None,
                "is_hdr": bool(probe.get("is_hdr", False)),
                "camera_model": et.get("Model") or mdls_data.get("kMDItemAcquisitionModel"),
                "dhash": dhash,
                "duplicate_flags": dup_reasons,
                "locked_by_user": False,
                "has_human_closeup": False,
            }
        )

    items.sort(key=lambda x: (x["timestamp_epoch"], x["filename"]))
    for idx, item in enumerate(items, start=1):
        item["index"] = idx

    chapters = st_dbscan_chapters(items, eps_km=eps_km, eps_hours=eps_hours, min_samples=min_samples)
    items_by_id = {x["id"]: x for x in items}

    contact_sheets: list[str] = []
    if not skip_sheets and chapters:
        contact_sheets = _generate_contact_sheets(chapters, items_by_id, out_dir, has_ffmpeg)

    manifest = {
        "schema_version": "1.0",
        "extractor": "exiftool+QuickTimeUTC" if has_exiftool else "zero-dep-exif-mdls-sips",
        "summary": {
            "total_assets": len(items),
            "photos": sum(1 for x in items if x["media_type"] == "photo"),
            "videos": sum(1 for x in items if x["media_type"] == "video"),
            "portrait_count": sum(1 for x in items if x["orientation"] == "portrait"),
            "landscape_count": sum(1 for x in items if x["orientation"] == "landscape"),
            "duplicate_candidates": sum(1 for x in items if x["duplicate_flags"]),
            "chapter_count": len(chapters),
        },
        "chapters": chapters,
        "contact_sheets": contact_sheets,
        "assets": items,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest trip photos/videos, cluster ST-DBSCAN chapters, and build Gate 1 contact sheets."
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input-dir", help="Local folder or pre-downloaded Drive folder of trip media.")
    src.add_argument("--file-list", help="Text file with one media path per line.")
    parser.add_argument("--work-dir", default="./work", help="Output working directory (default: ./work).")
    parser.add_argument("--eps-km", type=float, default=15.0, help="ST-DBSCAN spatial radius in km (default: 15.0).")
    parser.add_argument("--eps-hours", type=float, default=8.0, help="ST-DBSCAN temporal window in hours (default: 8.0).")
    parser.add_argument("--min-samples", type=int, default=2, help="ST-DBSCAN min samples per chapter (default: 2).")
    parser.add_argument("--skip-sheets", action="store_true", help="Skip rendering 3x3 contact sheets.")
    args = parser.parse_args()

    paths = _collect_paths(args.input_dir, args.file_list)
    manifest = ingest_trip(
        paths=paths,
        out_dir=Path(args.work_dir).resolve(),
        eps_km=args.eps_km,
        eps_hours=args.eps_hours,
        min_samples=args.min_samples,
        skip_sheets=args.skip_sheets,
    )
    print(json.dumps(manifest["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
