#!/usr/bin/env python3
"""Component-based video generator with card layout."""

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


def escape_text(text):
    """Escape text for ffmpeg drawtext."""
    return text.replace("'", "'\\\\\\''").replace(":", "\\:")


def split_text(text, max_chars):
    """Split text into lines with proper width."""
    if len(text) <= max_chars:
        return [text]

    lines = []
    current = ""
    for char in text:
        if len(current) >= max_chars:
            lines.append(current)
            current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


def generate_slide_component(
    slide_data, audio_path, output_path, duration, slide_num
):
    """Generate video with card layout."""

    title = slide_data.get('title', '')
    bullets = slide_data.get('bullets', [])
    paragraphs = slide_data.get('paragraphs', [])

    filters = []

    # Background
    filters.append(f"color=c=#f5f5f5:s=1920x1080:d={duration}[bg]")

    # Top label box
    filters.append("[bg]drawbox=x=100:y=80:w=180:h=50:color=#ff9800:t=fill[bg1]")

    # Label text
    filters.append(
        "[bg1]drawtext=text='知识要点':"
        "fontfile=/Windows/Fonts/msyh.ttc:fontsize=28:fontcolor=white:"
        "x=140:y=92[v0]"
    )

    current = "v0"
    layer_num = 1

    # Main title
    main_title = title if title else f"第{slide_num}页"
    title_esc = escape_text(main_title)
    filters.append(
        f"[{current}]drawtext=text='{title_esc}':"
        f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=64:fontcolor=#1a1a1a:"
        f"x=100:y=160[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # Subtitle
    filters.append(
        f"[{current}]drawtext=text='重点看这几个维度。':"
        f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=32:fontcolor=#666666:"
        f"x=100:y=250[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # Prepare content for three cards
    card_contents = []
    if bullets and len(bullets) >= 3:
        # Use first 3 bullets
        card_contents = bullets[:3]
    elif paragraphs:
        # Split first paragraph into 3 parts
        para = paragraphs[0]
        chunk_size = len(para) // 3
        card_contents = [
            para[:chunk_size],
            para[chunk_size:chunk_size*2],
            para[chunk_size*2:]
        ]
    else:
        card_contents = ["", "", ""]

    # Three cards
    card_width = 520
    card_height = 600
    card_y = 350
    card_spacing = 60

    for i in range(3):
        card_x = 100 + i * (card_width + card_spacing)

        # Card background
        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w={card_width}:h={card_height}:"
            f"color=white:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # Card border
        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w={card_width}:h={card_height}:"
            f"color=#e0e0e0:t=2[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # Card number
        filters.append(
            f"[{current}]drawtext=text='0{i+1}':"
            f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=48:fontcolor=#2196f3:"
            f"x={card_x+30}:y={card_y+30}[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # Card content
        content = card_contents[i] if i < len(card_contents) else ""
        if content:
            content_lines = split_text(content[:200], 18)
            content_y = card_y + 110
            for line in content_lines[:12]:
                line_esc = escape_text(line)
                filters.append(
                    f"[{current}]drawtext=text='{line_esc}':"
                    f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=24:fontcolor=#666666:"
                    f"x={card_x+30}:y={content_y}[v{layer_num}]"
                )
                current = f"v{layer_num}"
                layer_num += 1
                content_y += 45

    filter_complex = ";".join(filters)

    cmd = ["ffmpeg", "-y"]

    if audio_path and audio_path.exists():
        cmd.extend(["-i", str(audio_path)])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", f"[{current}]",
        "-map", "0:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration), "-shortest", str(output_path)
    ])

    subprocess.run(cmd, check=True, capture_output=True)


project = Path("projects/旧衣闭环再生产业_ppt169_20260506")
structure_file = project / "slide_structure.json"
audio_dir = project / "audio"
temp_dir = project / "temp_component_video"
temp_dir.mkdir(exist_ok=True)

with open(structure_file, 'r', encoding='utf-8') as f:
    slides = json.load(f)

print(f"Generating card layout video for {len(slides)} slides...")

segments = []
for i, slide in enumerate(slides, 1):
    print(f"[{i}/{len(slides)}] Slide {i}...", end=" ")

    audio = audio_dir / f"page_{i:02d}.mp3"
    if not audio.exists():
        audio = None

    duration = probe_duration(audio) + 0.5 if audio else 5.0

    segment = temp_dir / f"seg_{i:03d}.mp4"
    generate_slide_component(slide, audio, segment, duration, i)
    segments.append(segment)
    print(f"OK ({duration:.1f}s)")

# Concat
print("\nConcatenating...")
concat_file = temp_dir / "concat.txt"
with open(concat_file, "w") as f:
    for seg in segments:
        f.write(f"file '{seg.name}'\n")

output = project / "exports" / f"{project.name}_component.mp4"
subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", "concat.txt", "-c", "copy", str(output.absolute())
], cwd=str(temp_dir), check=True, capture_output=True)

print(f"\nDone! Component video: {output}")
print(f"Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
