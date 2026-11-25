#!/usr/bin/env python3
"""Environment preflight checker for trip-cinema-studio.

Verifies CLI tools via `--version` (never `which`) and optional Python
packages, outputting a structured JSON report with macOS/Linux install hints.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import subprocess
import sys
from typing import Any


CLI_CHECKS: list[dict[str, Any]] = [
    {
        "name": "ffmpeg",
        "args": ["ffmpeg", "-version"],
        "required": True,
        "macos_hint": "brew install ffmpeg",
        "linux_hint": "sudo apt-get install -y ffmpeg",
        "purpose": "Video transcoding, tone-mapping, Ken Burns, and audio mastering",
    },
    {
        "name": "ffprobe",
        "args": ["ffprobe", "-version"],
        "required": True,
        "macos_hint": "brew install ffmpeg",
        "linux_hint": "sudo apt-get install -y ffmpeg",
        "purpose": "Media stream, duration, FPS, and HDR metadata inspection",
    },
    {
        "name": "exiftool",
        "args": ["exiftool", "-ver"],
        "required": False,
        "macos_hint": "brew install exiftool",
        "linux_hint": "sudo apt-get install -y libimage-exiftool-perl",
        "purpose": "QuickTimeUTC / Keys:CreationDate and full EXIF/GPS extraction",
    },
    {
        "name": "node",
        "args": ["node", "--version"],
        "required": False,
        "macos_hint": "brew install node",
        "linux_hint": "sudo apt-get install -y nodejs npm",
        "purpose": "Remotion route map and chapter title rendering",
    },
    {
        "name": "npx",
        "args": ["npx", "--version"],
        "required": False,
        "macos_hint": "brew install node",
        "linux_hint": "sudo apt-get install -y nodejs npm",
        "purpose": "Running local Remotion CLI without global install",
    },
    {
        "name": "python3",
        "args": ["python3", "--version"],
        "required": True,
        "macos_hint": "brew install python@3.12",
        "linux_hint": "sudo apt-get install -y python3 python3-pip",
        "purpose": "Running deterministic pipeline scripts",
    },
    {
        "name": "sips",
        "args": ["sips", "--version"],
        "required": False,
        "macos_hint": "Built into macOS (no install needed)",
        "linux_hint": "N/A (macOS-only fallback; install Pillow or ffmpeg on Linux)",
        "purpose": "Zero-dependency macOS image dimension & contact-sheet fallback",
    },
    {
        "name": "mdls",
        "args": ["mdls", "--version"],
        "required": False,
        "macos_hint": "Built into macOS (no install needed)",
        "linux_hint": "N/A (macOS-only fallback; install exiftool on Linux)",
        "purpose": "Zero-dependency macOS Spotlight metadata fallback",
    },
    {
        "name": "gemini-api",
        "args": ["gemini-api", "--version"],
        "required": False,
        "macos_hint": "Install the Antigravity Gemini API plugin",
        "linux_hint": "Install the Antigravity Gemini API plugin",
        "purpose": "Optional Lyria 3.5 BGM previews and Veo/Omni scenic I2V",
    },
]

PYTHON_PACKAGES: list[dict[str, str]] = [
    {
        "name": "PIL",
        "pip_name": "Pillow",
        "purpose": "Contact sheets, look preview fallback, and perceptual hashing",
    },
    {
        "name": "cv2",
        "pip_name": "opencv-python-headless",
        "purpose": "Optional blur/sharpness scoring and optical flow",
    },
    {
        "name": "numpy",
        "pip_name": "numpy",
        "purpose": "Fast array math for beat detection and image analysis",
    },
    {
        "name": "librosa",
        "pip_name": "librosa",
        "purpose": "High-accuracy BPM, beat, and downbeat extraction",
    },
    {
        "name": "insightface",
        "pip_name": "insightface",
        "purpose": "Optional local VIP face clustering (never sent to cloud)",
    },
]


def _run_version_check(args: list[str]) -> dict[str, Any]:
    """Run a version command safely without using `which`."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        combined = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        first_line = combined.splitlines()[0].strip() if combined else ""
        # Handle macOS xcode-select shim for python3 when CLT is absent.
        if "xcode-select: error:" in combined:
            return {
                "available": False,
                "version": None,
                "error": "xcode-select shim without Command Line Tools",
            }
        # `mdls --version` exits 0 and prints usage text on macOS.
        if args[0] == "mdls" and "usage: mdls" in combined:
            return {"available": True, "version": "macos-builtin", "error": None}
        if proc.returncode == 0 and first_line:
            return {"available": True, "version": first_line, "error": None}
        return {
            "available": False,
            "version": None,
            "error": first_line or f"exit code {proc.returncode}",
        }
    except FileNotFoundError:
        return {"available": False, "version": None, "error": "not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"available": False, "version": None, "error": "timed out"}
    except OSError as exc:
        return {"available": False, "version": None, "error": str(exc)}


def _check_ffmpeg_filters() -> dict[str, bool]:
    """Check whether ffmpeg has libzimg (zscale) and libvidstab filters."""
    result = {"zscale": False, "vidstabdetect": False, "vidstabtransform": False}
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = proc.stdout or ""
        for key in result:
            if key in output:
                result[key] = True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return result


def _check_python_package(module_name: str) -> dict[str, Any]:
    """Check if a Python package is importable and read its __version__."""
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return {"available": False, "version": None}
    try:
        mod = __import__(module_name)
        version = getattr(mod, "__version__", "installed")
        return {"available": True, "version": str(version)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "version": None, "error": str(exc)}


def run_preflight() -> dict[str, Any]:
    """Collect CLI tool, FFmpeg filter, and Python package availability."""
    os_name = platform.system().lower()
    is_macos = os_name == "darwin"

    cli_results: dict[str, Any] = {}
    install_hints: list[str] = []

    for item in CLI_CHECKS:
        res = _run_version_check(item["args"])
        res["required"] = item["required"]
        res["purpose"] = item["purpose"]
        hint = item["macos_hint"] if is_macos else item["linux_hint"]
        res["install_hint"] = hint
        cli_results[item["name"]] = res
        if not res["available"] and not hint.startswith("N/A"):
            install_hints.append(f"{item['name']}: {hint}")

    ffmpeg_filters = (
        _check_ffmpeg_filters()
        if cli_results["ffmpeg"]["available"]
        else {"zscale": False, "vidstabdetect": False, "vidstabtransform": False}
    )

    py_results: dict[str, Any] = {}
    missing_pip: list[str] = []
    for pkg in PYTHON_PACKAGES:
        res = _check_python_package(pkg["name"])
        res["pip_name"] = pkg["pip_name"]
        res["purpose"] = pkg["purpose"]
        py_results[pkg["name"]] = res
        if not res["available"]:
            missing_pip.append(pkg["pip_name"])

    if missing_pip:
        install_hints.append(f"python packages: pip install {' '.join(missing_pip)}")

    ready_for_zero_cost = (
        cli_results["ffmpeg"]["available"] and cli_results["ffprobe"]["available"]
    )
    ready_for_ingest_fallback = is_macos and (
        cli_results["sips"]["available"] or cli_results["mdls"]["available"]
    )

    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
        },
        "cli_tools": cli_results,
        "ffmpeg_filters": ffmpeg_filters,
        "python_packages": py_results,
        "capabilities": {
            "ready_for_render": ready_for_zero_cost,
            "hdr_tonemap_zscale": ffmpeg_filters["zscale"],
            "vidstab_stabilization": (
                ffmpeg_filters["vidstabdetect"] and ffmpeg_filters["vidstabtransform"]
            ),
            "exiftool_available": cli_results["exiftool"]["available"],
            "macos_builtin_fallback": ready_for_ingest_fallback,
            "remotion_ready": (
                cli_results["node"]["available"] and cli_results["npx"]["available"]
            ),
            "gemini_api_ready": cli_results["gemini-api"]["available"],
        },
        "install_hints": install_hints,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check CLI tools, FFmpeg filters, and Python packages for trip-cinema-studio."
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Optional path to write the JSON preflight report.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact single-line JSON instead of pretty-printed JSON.",
    )
    args = parser.parse_args()

    report = run_preflight()
    indent = None if args.compact else 2
    payload = json.dumps(report, indent=indent)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
