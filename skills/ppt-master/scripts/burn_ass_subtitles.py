#!/usr/bin/env python3
"""Burn ASS subtitles into video."""
import subprocess
import sys
from pathlib import Path

video = Path(sys.argv[1])
ass_file = video.parent / f"{video.stem}.ass"
output = video.parent / f"{video.stem}_styled.mp4"

if not ass_file.exists():
    print(f"ASS file not found: {ass_file}")
    print("Run: python3 srt_to_ass.py <project_path> first")
    sys.exit(1)

# Use ASS subtitle with relative path
subprocess.run([
    "ffmpeg", "-i", video.name, "-vf",
    f"ass={ass_file.name}",
    "-c:a", "copy", output.name
], cwd=str(video.parent), check=True)

print(f"Done: {output}")
