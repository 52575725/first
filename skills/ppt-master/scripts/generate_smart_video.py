#!/usr/bin/env python3
"""Smart video generator with AI-recommended components."""

import json
import subprocess
from pathlib import Path


def probe_duration(audio_path):
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


def generate_smart_segment(
    png_path, audio_path, output_path,
    duration, recommendation
):
    """Generate video segment with AI-recommended effects."""

    # Simplified Ken Burns: scale to fit, then gentle zoom
    zoom_filter = (
        f"scale=1920:1080:force_original_aspect_ratio=increase,"
        f"crop=1920:1080,"
        f"zoompan=z='1+0.0005*on':d={int(duration*30)}:s=1920x1080:fps=30"
    )

    # Add fade for title slides
    if recommendation['title_animation'] in ['fly_in', 'fade_in']:
        zoom_filter += ",fade=in:0:30"

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(png_path)]

    if audio_path and audio_path.exists():
        cmd.extend(["-i", str(audio_path)])
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    cmd.extend([
        "-vf", zoom_filter,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-t", str(duration), "-shortest", str(output_path)
    ])

    subprocess.run(cmd, check=True, capture_output=True)


project = Path("projects/旧衣闭环再生产业_ppt169_20260506")
slides_dir = project / "slides"
audio_dir = project / "audio"
temp_dir = project / "temp_smart_video"
temp_dir.mkdir(exist_ok=True)

# Load recommendations
with open(project / "component_recommendations.json", 'r') as f:
    recommendations = json.load(f)

pngs = sorted(slides_dir.glob("slide_*.png"))
print(f"Generating smart video with AI recommendations...")

segments = []
for i, (png, rec) in enumerate(zip(pngs, recommendations), 1):
    print(f"[{i}/{len(pngs)}] {png.name}...", end=" ")

    audio = audio_dir / f"page_{i:02d}.mp3"
    if not audio.exists():
        audio = None

    base_duration = probe_duration(audio) if audio else 3.0
    duration = base_duration * rec['duration_multiplier'] + 0.5

    segment = temp_dir / f"seg_{i:03d}.mp4"
    generate_smart_segment(png, audio, segment, duration, rec)
    segments.append(segment)
    print(f"OK ({duration:.1f}s)")

# Concat with transitions
print("\nConcatenating with transitions...")
concat_file = temp_dir / "concat.txt"
with open(concat_file, "w") as f:
    for seg in segments:
        f.write(f"file '{seg.name}'\n")

output = project / "exports" / f"{project.name}_smart.mp4"
subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", "concat.txt", "-c", "copy", str(output.absolute())
], cwd=str(temp_dir), check=True, capture_output=True)

print(f"\nDone! Smart video: {output}")
print(f"Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
