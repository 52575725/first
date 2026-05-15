#!/usr/bin/env python3
"""Enhanced video generator with animations and transitions."""

import json
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


def probe_duration(audio_path: Path) -> float:
    """Get audio duration."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(audio_path)],
            capture_output=True, text=True, check=True
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except:
        return 3.0


def generate_animated_segment(
    png: Path,
    audio: Path,
    output: Path,
    duration: float,
    transition_type: str = "fade"
) -> None:
    """Generate video segment with Ken Burns effect and transitions."""

    # Ken Burns effect: slow zoom and pan
    zoom_filter = (
        f"scale=2200:-1,"  # Scale up for zoom room
        f"zoompan=z='min(zoom+0.0005,1.1)':d={int(duration*30)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080"
    )

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(png)]

    if audio and audio.exists():
        cmd.extend(["-i", str(audio)])
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    cmd.extend([
        "-vf", zoom_filter,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-t", str(duration),
        "-shortest",
        str(output)
    ])

    subprocess.run(cmd, check=True, capture_output=True)


def add_transitions(segments: List[Path], output: Path) -> None:
    """Concatenate segments with crossfade transitions."""

    if len(segments) == 1:
        segments[0].rename(output)
        return

    # Build complex filter for crossfade
    filter_parts = []
    inputs = []

    for i, seg in enumerate(segments):
        inputs.extend(["-i", str(seg)])

    # Crossfade between segments (0.5s overlap)
    fade_duration = 0.5
    current = "[0:v]"

    for i in range(1, len(segments)):
        next_input = f"[{i}:v]"
        output_label = f"[v{i}]" if i < len(segments) - 1 else "[outv]"
        filter_parts.append(
            f"{current}{next_input}xfade=transition=fade:duration={fade_duration}:offset=0{output_label}"
        )
        current = output_label

    # Audio concat
    audio_filter = "".join([f"[{i}:a]" for i in range(len(segments))]) + f"concat=n={len(segments)}:v=0:a=1[outa]"

    filter_complex = ";".join(filter_parts + [audio_filter])

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-c:a", "aac",
        str(output)
    ]

    subprocess.run(cmd, check=True, capture_output=True)


project = Path(sys.argv[1])
slides_dir = project / "slides"
audio_dir = project / "audio"
temp_dir = project / "temp_video_animated"
temp_dir.mkdir(exist_ok=True)

pngs = sorted(slides_dir.glob("slide_*.png"))
print(f"Found {len(pngs)} slides")
print("Generating animated segments...")

segments = []
for i, png in enumerate(pngs, 1):
    print(f"[{i}/{len(pngs)}] {png.name}...", end=" ")

    audio = audio_dir / f"page_{i:02d}.mp3"
    if not audio.exists():
        audio = None

    duration = probe_duration(audio) + 0.5 if audio else 3.0

    segment = temp_dir / f"seg_{i:03d}.mp4"
    generate_animated_segment(png, audio, segment, duration)
    segments.append(segment)
    print(f"OK ({duration:.1f}s)")

print("\nAdding transitions...")
output = project / "exports" / f"{project.name}_animated.mp4"
output.parent.mkdir(exist_ok=True)

add_transitions(segments, output)

print(f"\nDone! Video: {output}")
print(f"Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
