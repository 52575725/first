#!/usr/bin/env python3
"""Generate ASS subtitle with custom font styling."""

from pathlib import Path
import sys


def generate_ass_from_srt(srt_path: Path, output_path: Path):
    """Convert SRT to ASS with custom styling."""

    # ASS header with Microsoft YaHei font
    ass_header = """[Script Info]
Title: Generated Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,75,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,100,100,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Read SRT file
    srt_content = srt_path.read_text(encoding='utf-8')

    # Parse SRT and convert to ASS
    lines = srt_content.strip().split('\n\n')
    ass_events = []

    for block in lines:
        parts = block.split('\n', 2)
        if len(parts) < 3:
            continue

        # Parse timestamp
        timestamp_line = parts[1]
        if '-->' not in timestamp_line:
            continue

        start, end = timestamp_line.split('-->')
        start = start.strip().replace(',', '.')
        end = end.strip().replace(',', '.')

        # Convert HH:MM:SS,mmm to H:MM:SS.mm
        start = start[:-1]  # Remove last digit
        end = end[:-1]

        # Get text
        text = parts[2].replace('\n', '\\N')

        ass_events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    # Write ASS file
    ass_content = ass_header + '\n'.join(ass_events)
    output_path.write_text(ass_content, encoding='utf-8')


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 srt_to_ass.py <project_path>")
        sys.exit(1)

    project = Path(sys.argv[1])
    srt_file = project / "exports" / f"{project.name}_new.srt"
    ass_file = project / "exports" / f"{project.name}_new.ass"

    if not srt_file.exists():
        print(f"SRT file not found: {srt_file}")
        sys.exit(1)

    generate_ass_from_srt(srt_file, ass_file)
    print(f"ASS subtitle generated: {ass_file}")
