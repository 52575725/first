#!/usr/bin/env python3
"""Burn subtitles into video with custom font."""
import subprocess
import sys
from pathlib import Path

video = Path(sys.argv[1]).absolute()
subtitle = Path(sys.argv[2]).absolute()
output = video.parent / f"{video.stem}_with_subs.mp4"

# Use Microsoft YaHei font with better styling
subtitle_filter = (
    f"subtitles='{str(subtitle).replace('\\\\', '/')}':"
    "force_style='FontName=Microsoft YaHei,"
    "FontSize=20,"
    "PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,"
    "BackColour=&H80000000,"
    "Outline=2,"
    "Shadow=1,"
    "MarginV=30'"
)

subprocess.run([
    "ffmpeg", "-i", str(video), "-vf", subtitle_filter,
    "-c:a", "copy", str(output)
], check=True)

print(f"Done: {output}")
