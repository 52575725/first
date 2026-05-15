#!/usr/bin/env python3
"""Smart layout video generator with AI-driven layout selection."""

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
    """Split text into lines."""
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


def split_into_sentences(text):
    """Split Chinese text by sentence delimiters."""
    import re
    sentences = re.split(r'([。！？])', text)
    result = []
    for i in range(0, len(sentences)-1, 2):
        if sentences[i].strip():
            result.append(sentences[i] + (sentences[i+1] if i+1 < len(sentences) else ''))
    if len(sentences) % 2 == 1 and sentences[-1].strip():
        result.append(sentences[-1])
    return [s.strip() for s in result if s.strip()]


def choose_layout(slide_data):
    """Choose layout based on content."""
    bullets = slide_data.get('bullets', [])
    paragraphs = slide_data.get('paragraphs', [])
    title = slide_data.get('title', '')

    # Three-column: 3+ bullets or long paragraph
    if len(bullets) >= 3:
        return 'three_column'
    elif len(paragraphs) > 0 and len(paragraphs[0]) > 100:
        return 'three_column'

    # Two-column: 2 bullets or 2 paragraphs
    elif len(bullets) == 2 or len(paragraphs) == 2:
        return 'two_column'

    # Single-column: short content or single paragraph
    else:
        return 'single_column'


def generate_base_layout(duration, slide_num):
    """Generate base layout elements."""
    filters = []

    # Background
    filters.append(f"color=c=#f5f5f5:s=1920x1080:d={duration}[bg]")

    # Top label
    filters.append("[bg]drawbox=x=100:y=80:w=180:h=50:color=#ff9800:t=fill[bg1]")
    filters.append(
        "[bg1]drawtext=text='知识要点':"
        "fontfile=/Windows/Fonts/msyh.ttc:fontsize=40:fontcolor=white:"
        "x=140:y=92[v0]"
    )

    return filters, "v0", 1


def layout_three_column(filters, current, layer_num, slide_data, slide_num):
    """Three-column card layout."""
    title = slide_data.get('title', f'第{slide_num}页')
    bullets = slide_data.get('bullets', [])
    paragraphs = slide_data.get('paragraphs', [])

    # Title
    title_esc = escape_text(title)
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
        f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=32:fontcolor=#444444:"
        f"x=100:y=250[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # Prepare content
    if bullets and len(bullets) >= 3:
        contents = bullets[:3]
    elif paragraphs:
        sentences = split_into_sentences(paragraphs[0])
        if len(sentences) >= 3:
            # Distribute sentences across 3 cards
            per_card = len(sentences) // 3
            contents = [
                ''.join(sentences[:per_card]),
                ''.join(sentences[per_card:per_card*2]),
                ''.join(sentences[per_card*2:])
            ]
        elif len(sentences) == 2:
            contents = [sentences[0], sentences[1], ""]
        elif len(sentences) == 1:
            contents = [sentences[0], "", ""]
        else:
            contents = ["", "", ""]
    else:
        contents = ["", "", ""]

    # Three cards
    for i in range(3):
        card_x = 100 + i * 580
        card_y = 350

        # Card background + border
        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=520:h=600:color=white:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=520:h=600:color=#e0e0e0:t=2[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # Number
        filters.append(
            f"[{current}]drawtext=text='0{i+1}':"
            f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=48:fontcolor=#2196f3:"
            f"x={card_x+30}:y={card_y+30}[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # Content
        if i < len(contents) and contents[i]:
            lines = split_text(contents[i][:150], 12)
            y = card_y + 110
            for line in lines[:12]:
                line_esc = escape_text(line)
                filters.append(
                    f"[{current}]drawtext=text='{line_esc}':"
                    f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=32:fontcolor=#444444:"
                    f"x={card_x+30}:y={y}[v{layer_num}]"
                )
                current = f"v{layer_num}"
                layer_num += 1
                y += 45

    return filters, current, layer_num


def layout_two_column(filters, current, layer_num, slide_data, slide_num):
    """Two-column layout."""
    title = slide_data.get('title', f'第{slide_num}页')
    bullets = slide_data.get('bullets', [])
    paragraphs = slide_data.get('paragraphs', [])

    # Title
    title_esc = escape_text(title)
    filters.append(
        f"[{current}]drawtext=text='{title_esc}':"
        f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=64:fontcolor=#1a1a1a:"
        f"x=100:y=160[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # Prepare content
    if bullets and len(bullets) >= 2:
        contents = bullets[:2]
    elif paragraphs and len(paragraphs) >= 2:
        contents = paragraphs[:2]
    elif paragraphs:
        para = paragraphs[0]
        mid = len(para) // 2
        contents = [para[:mid], para[mid:]]
    else:
        contents = ["", ""]

    # Two large cards
    for i in range(2):
        card_x = 150 + i * 900
        card_y = 300

        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=820:h=650:color=white:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=820:h=650:color=#e0e0e0:t=2[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # Content
        if i < len(contents) and contents[i]:
            lines = split_text(contents[i][:200], 18)
            y = card_y + 50
            for line in lines[:14]:
                line_esc = escape_text(line)
                filters.append(
                    f"[{current}]drawtext=text='{line_esc}':"
                    f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=28:fontcolor=#444444:"
                    f"x={card_x+40}:y={y}[v{layer_num}]"
                )
                current = f"v{layer_num}"
                layer_num += 1
                y += 45

    return filters, current, layer_num


def layout_single_column(filters, current, layer_num, slide_data, slide_num):
    """Single-column full-width layout."""
    title = slide_data.get('title', f'第{slide_num}页')
    paragraphs = slide_data.get('paragraphs', [])

    # Title
    title_esc = escape_text(title)
    filters.append(
        f"[{current}]drawtext=text='{title_esc}':"
        f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=72:fontcolor=#1a1a1a:"
        f"x=(w-text_w)/2:y=200[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # Large content box
    filters.append(
        f"[{current}]drawbox=x=200:y=350:w=1520:h=600:color=white:t=fill[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    filters.append(
        f"[{current}]drawbox=x=200:y=350:w=1520:h=600:color=#e0e0e0:t=2[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # Content
    if paragraphs:
        lines = split_text(paragraphs[0][:300], 24)
        y = 400
        for line in lines[:12]:
            line_esc = escape_text(line)
            filters.append(
                f"[{current}]drawtext=text='{line_esc}':"
                f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=32:fontcolor=#444444:"
                f"x=250:y={y}[v{layer_num}]"
            )
            current = f"v{layer_num}"
            layer_num += 1
            y += 50

    return filters, current, layer_num

def generate_slide(slide_data, audio_path, output_path, duration, slide_num):
    """Generate slide with smart layout selection."""
    layout_type = choose_layout(slide_data)
    filters, current, layer_num = generate_base_layout(duration, slide_num)

    if layout_type == 'three_column':
        filters, current, layer_num = layout_three_column(filters, current, layer_num, slide_data, slide_num)
    elif layout_type == 'two_column':
        filters, current, layer_num = layout_two_column(filters, current, layer_num, slide_data, slide_num)
    else:
        filters, current, layer_num = layout_single_column(filters, current, layer_num, slide_data, slide_num)

    filter_complex = ";".join(filters)
    cmd = ["ffmpeg", "-y"]
    if audio_path and audio_path.exists():
        cmd.extend(["-i", str(audio_path)])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", f"[{current}]", "-map", "0:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration), "-shortest", str(output_path)
    ])

    subprocess.run(cmd, check=True, capture_output=True)
    return layout_type


project = Path("projects/旧衣闭环再生产业_ppt169_20260506")
structure_file = project / "slide_structure.json"
audio_dir = project / "audio"
temp_dir = project / "temp_smart_layout"
temp_dir.mkdir(exist_ok=True)

with open(structure_file, 'r', encoding='utf-8') as f:
    slides = json.load(f)

print(f"Generating smart layout video for {len(slides)} slides...")

segments = []
for i, slide in enumerate(slides, 1):
    audio = audio_dir / f"page_{i:02d}.mp3"
    if not audio.exists():
        audio = None

    duration = probe_duration(audio) + 0.5 if audio else 5.0
    segment = temp_dir / f"seg_{i:03d}.mp4"

    layout = generate_slide(slide, audio, segment, duration, i)
    segments.append(segment)
    print(f"[{i}/{len(slides)}] {layout} layout... OK ({duration:.1f}s)")

# Concat
print("\nConcatenating...")
concat_file = temp_dir / "concat.txt"
with open(concat_file, "w") as f:
    for seg in segments:
        f.write(f"file '{seg.name}'\n")

output = project / "exports" / f"{project.name}_smart.mp4"
subprocess.run([
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", "concat.txt", "-c", "copy", str(output.absolute())
], cwd=str(temp_dir), check=True, capture_output=True)

print(f"\nDone! Smart layout video: {output}")
print(f"Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
