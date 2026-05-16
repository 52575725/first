#!/usr/bin/env python3
"""Generate per-slide audio from video_script_plan.json using local Windows TTS."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import os
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
VENDOR_DIR = REPO_ROOT / "tools" / "python_libs"


def running_in_virtualenv() -> bool:
    return Path(sys.prefix).resolve() != Path(getattr(sys, "base_prefix", sys.prefix)).resolve()


def readable_vendor_dir(path: Path) -> bool:
    try:
        if not path.exists():
            return False
        probe = path / "typing_extensions.py"
        if probe.exists():
            with probe.open("rb"):
                pass
        return True
    except OSError:
        return False


if not running_in_virtualenv() and readable_vendor_dir(VENDOR_DIR) and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))


WORKER_PS1 = r"""
param(
  [Parameter(Mandatory=$true)][string]$TextPath,
  [Parameter(Mandatory=$true)][string]$WavPath,
  [string]$VoiceName = "",
  [int]$Rate = 0,
  [int]$Volume = 100
)

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($VoiceName -ne "") {
  $synth.SelectVoice($VoiceName)
} else {
  $zhVoice = $synth.GetInstalledVoices() |
    Where-Object { $_.VoiceInfo.Culture.Name -eq "zh-CN" } |
    Select-Object -First 1
  if ($zhVoice -ne $null) {
    $synth.SelectVoice($zhVoice.VoiceInfo.Name)
  }
}
$synth.Rate = $Rate
$synth.Volume = $Volume
$text = [System.IO.File]::ReadAllText($TextPath, [System.Text.Encoding]::UTF8)
$dir = [System.IO.Path]::GetDirectoryName($WavPath)
if (-not [System.IO.Directory]::Exists($dir)) {
  [System.IO.Directory]::CreateDirectory($dir) | Out-Null
}
$synth.SetOutputToWaveFile($WavPath)
$synth.Speak($text)
$synth.SetOutputToNull()
$synth.Dispose()
"""


def ensure_media_tools_on_path() -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        try:
            probe = subprocess.run(
                [ffmpeg, "-hide_banner", "-h", "filter=drawtext"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            probe = None
        if probe and probe.returncode == 0:
            output = f"{probe.stdout}\n{probe.stderr}".lower()
            if "unknown filter" not in output:
                return

    def candidate_dirs():
        repo_root = Path(__file__).resolve().parents[3]
        roots = [
            Path("D:/tools/ffmpeg/ffmpeg-8.1.1-essentials_build/bin"),
            Path("D:/tools/ffmpeg"),
            repo_root / "tools" / "ffmpeg" / "bin",
            repo_root / "tools" / "ffmpeg",
            Path("C:/tools/ffmpeg"),
        ]
        local_appdata = Path.home() / "AppData" / "Local"
        roots.extend(local_appdata.glob("WeMod/app-*/resources/app.asar.unpacked/static/unpacked/capture/release/bin/64bit"))
        roots.extend(Path("C:/Program Files (x86)/Lenovo/LegionZone").glob("*/SEGamingAI/services/editor"))
        if Path("D:/tools/ffmpeg").exists():
            roots.extend(Path("D:/tools/ffmpeg").glob("*/bin"))
        return roots

    def is_working_media_dir(path):
        try:
            ffmpeg_path = path / "ffmpeg.exe"
            ffprobe_path = path / "ffprobe.exe"
            if not ffmpeg_path.exists() or not ffprobe_path.exists():
                return False
            probe = subprocess.run(
                [str(ffmpeg_path), "-hide_banner", "-h", "filter=drawtext"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        output = f"{probe.stdout}\n{probe.stderr}".lower()
        return probe.returncode == 0 and "unknown filter" not in output

    existing = []
    for path in candidate_dirs():
        try:
            if path.exists() and is_working_media_dir(path):
                path_text = str(path)
                if path_text not in existing:
                    existing.append(path_text)
        except OSError:
            continue
    if existing:
        current_path = os.environ.get("PATH", "")
        parts = existing + ([current_path] if current_path else [])
        os.environ["PATH"] = os.pathsep.join(parts)


ensure_media_tools_on_path()


def compact_voiceover(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.replace("_No extractable text content._", "").strip()
    if len(text) <= max_chars:
        return text
    sentences = re.split(r"(?<=[。！？!?；;])\s*", text)
    selected: list[str] = []
    total = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if total + len(sentence) > max_chars and selected:
            break
        selected.append(sentence)
        total += len(sentence)
    if selected:
        return " ".join(selected)
    return text[:max_chars]


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)


def probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return 0.0


async def generate_edge_audio(text: str, output_path: Path, *, voice: str, rate: str, pitch: str) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("Missing edge-tts. Install with: python -m pip install edge-tts") from exc
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(output_path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_path", type=Path)
    parser.add_argument("--provider", choices=["sapi", "edge"], default="edge")
    parser.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    parser.add_argument("--edge-rate", default="-6%")
    parser.add_argument("--edge-pitch", default="-3Hz")
    parser.add_argument("--rate", type=int, default=1)
    parser.add_argument("--volume", type=int, default=100)
    parser.add_argument("--max-chars", type=int, default=900)
    parser.add_argument("--force", action="store_true", help="Overwrite existing page audio")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = args.project_path
    script_plan_path = project / "video_script_plan.json"
    if not script_plan_path.exists():
        raise FileNotFoundError(f"Missing script plan: {script_plan_path}")

    payload = json.loads(script_plan_path.read_text(encoding="utf-8"))
    slides = payload.get("slides", [])
    audio_dir = project / "audio"
    temp_dir = project / "temp_audio_tts"
    audio_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    worker = temp_dir / "sapi_tts_worker.ps1"
    worker.write_text(WORKER_PS1, encoding="utf-8-sig")

    outputs = []
    for slide in slides:
        slide_number = int(slide.get("slide_number", len(outputs) + 1))
        mp3_path = audio_dir / f"page_{slide_number:02d}.mp3"
        if mp3_path.exists() and mp3_path.stat().st_size > 1024 and not args.force:
            outputs.append({"slide_number": slide_number, "path": str(mp3_path), "status": "exists"})
            continue
        if mp3_path.exists() and mp3_path.stat().st_size <= 1024:
            mp3_path.unlink()

        voiceover = compact_voiceover(slide.get("voiceover", ""), args.max_chars)
        if not voiceover:
            outputs.append({"slide_number": slide_number, "path": "", "status": "skipped_empty"})
            continue

        if args.provider == "edge":
            try:
                asyncio.run(
                    generate_edge_audio(
                        voiceover,
                        mp3_path,
                        voice=args.voice,
                        rate=args.edge_rate,
                        pitch=args.edge_pitch,
                    )
                )
            except Exception as exc:
                if mp3_path.exists() and mp3_path.stat().st_size <= 1024:
                    mp3_path.unlink()
                outputs.append(
                    {
                        "slide_number": slide_number,
                        "path": "",
                        "status": "tts_failed",
                        "error": str(exc),
                    }
                )
                continue
            outputs.append(
                {
                    "slide_number": slide_number,
                    "path": str(mp3_path),
                    "status": "ready",
                    "duration": round(probe_duration(mp3_path), 2),
                    "chars": len(voiceover),
                }
            )
            continue

        text_path = temp_dir / f"page_{slide_number:02d}.txt"
        wav_path = temp_dir / f"page_{slide_number:02d}.wav"
        text_path.write_text(voiceover, encoding="utf-8")

        ps_result = run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(worker),
                "-TextPath",
                str(text_path),
                "-WavPath",
                str(wav_path),
                "-VoiceName",
                args.voice,
                "-Rate",
                str(args.rate),
                "-Volume",
                str(args.volume),
            ],
            project,
        )
        if ps_result.returncode != 0:
            outputs.append(
                {
                    "slide_number": slide_number,
                    "path": "",
                    "status": "tts_failed",
                    "error": ps_result.stderr.strip() or ps_result.stdout.strip(),
                }
            )
            continue

        ffmpeg_result = run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav_path),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(mp3_path),
            ],
            project,
        )
        if ffmpeg_result.returncode != 0:
            outputs.append(
                {
                    "slide_number": slide_number,
                    "path": "",
                    "status": "encode_failed",
                    "error": ffmpeg_result.stderr.strip() or ffmpeg_result.stdout.strip(),
                }
            )
            continue

        outputs.append(
            {
                "slide_number": slide_number,
                "path": str(mp3_path),
                "status": "ready",
                "duration": round(probe_duration(mp3_path), 2),
                "chars": len(voiceover),
            }
        )

    manifest = {
        "project": project.name,
        "provider": args.provider,
        "voice": args.voice,
        "edge_rate": args.edge_rate,
        "edge_pitch": args.edge_pitch,
        "rate": args.rate,
        "outputs": outputs,
    }
    manifest_path = project / "audio" / "script_audio_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ready = sum(1 for item in outputs if item["status"] in {"ready", "exists"})
    print(f"Audio generated: {ready}/{len(outputs)}")
    print(f"Manifest: {manifest_path}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
