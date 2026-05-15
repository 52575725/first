#!/usr/bin/env python3
"""Quick video generator using existing PNG slides and audio files."""

import json
import subprocess
import sys
from pathlib import Path


def probe_duration(audio_path):
    """Get audio duration in seconds."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(audio_path)],
            capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except:
        return 3.0


def generate_segment(png, audio, output, duration):
    """Generate video segment from PNG and audio."""
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(png)]

    if audio and audio.exists():
        cmd.extend(["-i", str(audio)])
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    cmd.extend([
        "-c:v", "libx264", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-t", str(duration), "-shortest", str(output)
    ])

    subprocess.run(cmd, check=True, capture_output=True)


project = Path(sys.argv[1])
slides_dir = project / "slides"
audio_dir = project / "audio"
temp_dir = project / "temp_video"
temp_dir.mkdir(exist_ok=True)

pngs = sorted(slides_dir.glob("slide_*.png"))
print(f"Found {len(pngs)} slides")

segments = []
for i, png in enumerate(pngs, 1):
    print(f"[{i}/{len(pngs)}] Processing {png.name}...", end=" ")

    # Find audio
    audio = audio_dir / f"page_{i:02d}.mp3"
    if not audio.exists():
        audio = audio_dir / f"slide_{i:02d}.mp3"
    if not audio.exists():
        audio = audio_dir / f"{i:02d}.mp3"
    if not audio.exists():
        audio = None

    duration = probe_duration(audio) + 0.5 if audio else 3.0

    segment = temp_dir / f"seg_{i:03d}.mp4"
    generate_segment(png, audio, segment, duration)
    segments.append(segment)
    print(f"OK ({duration:.1f}s)")

# Concat
print("\nConcatenating...")
concat_file = temp_dir / "concat.txt"
with open(concat_file, "w") as f:
    for seg in segments:
        f.write(f"file '{seg.name}'\n")

output = project / "exports" / f"{project.name}_new.mp4"
output.parent.mkdir(exist_ok=True)

subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", "concat.txt", "-c", "copy", str(output.absolute())
], cwd=str(temp_dir), check=True, capture_output=True)

print(f"\nDone! Video: {output}")
print(f"Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
