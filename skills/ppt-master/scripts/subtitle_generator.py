#!/usr/bin/env python3
"""Subtitle generator for PPT Master videos."""

from __future__ import annotations

import re
import json
import subprocess
from pathlib import Path
from typing import List


BREAK_CHARS = set("\u3001\uff0c\u3002\uff01\uff1f\uff1b\uff1a,.!?;: ")
MAX_SUBTITLE_LINES = 2


def format_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_into_sentences(text: str) -> List[str]:
    """Split text into sentences."""
    # Split by Chinese and English sentence endings
    sentences = re.split(r"([\u3002\uff01\uff1f.!?]+)", text)

    result = []
    for i in range(0, len(sentences) - 1, 2):
        sentence = sentences[i].strip()
        punctuation = sentences[i + 1] if i + 1 < len(sentences) else ''
        if sentence:
            result.append(sentence + punctuation)

    # Handle last sentence if no punctuation
    if len(sentences) % 2 == 1 and sentences[-1].strip():
        result.append(sentences[-1].strip())

    return [s for s in result if s.strip()]


def wrap_text(text: str, max_chars: int = 22) -> str:
    """Wrap text for subtitle display."""
    if len(text) <= max_chars:
        return text

    lines = []
    current = ""

    for char in text:
        current += char
        should_break = len(current) >= max_chars and (
            char in BREAK_CHARS or len(current) >= max_chars + 6
        )
        if should_break:
            lines.append(current)
            current = ""

    if current:
        lines.append(current)

    return '\n'.join(lines) if lines else text


def split_caption_chunks(
    text: str,
    max_chars: int = 22,
    max_lines: int = MAX_SUBTITLE_LINES,
) -> List[str]:
    """Split one long subtitle sentence into readable caption chunks."""
    chunk_limit = max_chars * max_lines
    if len(text) <= chunk_limit:
        return [wrap_text(text, max_chars)]

    chunks = []
    current = ""
    last_break = -1

    for char in text:
        current += char
        if char in BREAK_CHARS:
            last_break = len(current)

        if len(current) >= chunk_limit:
            if max_chars <= last_break < len(current):
                chunks.append(current[:last_break].strip())
                current = current[last_break:].strip()
            else:
                chunks.append(current.strip())
                current = ""
            last_break = -1

    if current.strip():
        chunks.append(current.strip())

    return [wrap_text(chunk, max_chars) for chunk in chunks if chunk]


def generate_srt(
    texts: List[str],
    durations: List[float],
    output_path: Path
) -> None:
    """Generate SRT subtitle file with line wrapping."""
    lines = []
    current_time = 0.0
    subtitle_index = 1

    for text, duration in zip(texts, durations):
        if not text.strip():
            current_time += duration
            continue

        chunks = split_caption_chunks(text.strip())
        total_chars = sum(len(chunk.replace("\n", "")) for chunk in chunks) or 1
        chunk_start = current_time

        for chunk in chunks:
            chunk_chars = len(chunk.replace("\n", "")) or 1
            chunk_duration = duration * (chunk_chars / total_chars)
            chunk_end = chunk_start + chunk_duration

            lines.append(f"{subtitle_index}")
            lines.append(f"{format_timestamp(chunk_start)} --> {format_timestamp(chunk_end)}")
            lines.append(chunk)
            lines.append("")

            subtitle_index += 1
            chunk_start = chunk_end

        current_time += duration

    output_path.write_text("\n".join(lines), encoding="utf-8")


def generate_subtitles_from_notes(
    project_path: Path,
    audio_dir: Path,
    output_path: Path
) -> None:
    """Generate subtitles from notes and audio durations (sentence by sentence)."""
    notes_dir = project_path / "notes"
    note_files = sorted([f for f in notes_dir.glob("*.md") if f.name != "total.md"])

    all_texts = []
    all_durations = []

    for note_file in note_files:
        text = note_file.read_text(encoding="utf-8")
        # Remove markdown headers
        text = "\n".join(line for line in text.split("\n") if not line.startswith("#"))

        # Split into sentences
        sentences = split_into_sentences(text.strip())

        if not sentences:
            continue

        # Get total audio duration for this page
        audio_file = audio_dir / f"page_{note_file.stem.split('_')[-1]}.mp3"
        if not audio_file.exists():
            audio_file = audio_dir / f"{note_file.stem}.mp3"
        if not audio_file.exists():
            audio_file = audio_dir / f"{note_file.stem}.m4a"

        if audio_file.exists():
            total_duration = probe_audio_duration(audio_file) or 3.0
        else:
            total_duration = 3.0

        # Calculate duration per sentence based on character count
        total_chars = sum(len(s) for s in sentences)
        for sentence in sentences:
            char_ratio = len(sentence) / total_chars if total_chars > 0 else 1.0 / len(sentences)
            sentence_duration = total_duration * char_ratio
            all_texts.append(sentence)
            all_durations.append(sentence_duration)

    generate_srt(all_texts, all_durations, output_path)


def probe_audio_duration(audio_path: Path) -> float:
    """Probe audio duration without importing PPTX export dependencies."""
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
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return 0.0


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 subtitle_generator.py <project_path>")
        sys.exit(1)

    project = Path(sys.argv[1])
    audio_dir = project / "audio"
    output = project / "exports" / f"{project.name}.srt"
    output.parent.mkdir(parents=True, exist_ok=True)

    generate_subtitles_from_notes(project, audio_dir, output)
    print(f"Subtitle generated: {output}")
