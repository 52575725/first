#!/usr/bin/env python3
"""Intelligent content analyzer for PPT Master.

Analyzes slide content to extract:
- Knowledge point structure
- Content density
- Recommended pause duration
- Key emphasis areas
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class SlideAnalysis:
    """Analysis result for a single slide."""
    slide_num: int
    title: str
    content_density: float  # 0.0-1.0
    has_chart: bool
    has_image: bool
    key_points: List[str]
    recommended_pause: float  # seconds


def analyze_markdown_content(md_text: str) -> dict:
    """Analyze markdown content structure."""
    lines = md_text.strip().split('\n')

    # Extract title
    title = ""
    for line in lines:
        if line.startswith('#'):
            title = line.lstrip('#').strip()
            break

    # Count content elements
    text_length = len(md_text)
    bullet_points = len(re.findall(r'^\s*[-*]\s', md_text, re.MULTILINE))
    has_chart = bool(re.search(r'图表|chart|数据|data', md_text, re.IGNORECASE))
    has_image = bool(re.search(r'!\[.*?\]\(.*?\)', md_text))

    # Calculate content density (0.0-1.0)
    density = min(1.0, (text_length / 500 + bullet_points / 10) / 2)

    # Extract key points (lines starting with -, *, or numbers)
    key_points = []
    for line in lines:
        if re.match(r'^\s*[-*\d]+[.)]\s+(.+)', line):
            point = re.sub(r'^\s*[-*\d]+[.)]\s+', '', line).strip()
            if point:
                key_points.append(point)

    return {
        'title': title,
        'content_density': density,
        'has_chart': has_chart,
        'has_image': has_image,
        'key_points': key_points[:5],  # Top 5 key points
    }


def calculate_pause_duration(
    content_density: float,
    has_chart: bool,
    has_image: bool,
    base_pause: float = 0.5
) -> float:
    """Calculate recommended pause duration after slide."""
    pause = base_pause

    # Add time for charts (need more time to digest)
    if has_chart:
        pause += 1.5

    # Add time for dense content
    if content_density > 0.7:
        pause += 0.5
    elif content_density > 0.5:
        pause += 0.3

    # Add time for images
    if has_image:
        pause += 0.3

    return round(pause, 1)


def analyze_slide(slide_md_path: Path, slide_num: int) -> SlideAnalysis:
    """Analyze a single slide markdown file."""
    md_text = slide_md_path.read_text(encoding='utf-8')
    analysis = analyze_markdown_content(md_text)

    pause = calculate_pause_duration(
        analysis['content_density'],
        analysis['has_chart'],
        analysis['has_image']
    )

    return SlideAnalysis(
        slide_num=slide_num,
        title=analysis['title'],
        content_density=analysis['content_density'],
        has_chart=analysis['has_chart'],
        has_image=analysis['has_image'],
        key_points=analysis['key_points'],
        recommended_pause=pause
    )


def analyze_project(project_path: Path) -> List[SlideAnalysis]:
    """Analyze all slides in a project."""
    notes_dir = project_path / "notes"
    if not notes_dir.exists():
        return []

    results = []
    note_files = sorted(
        [f for f in notes_dir.glob("*.md") if f.name != "total.md"]
    )

    for i, note_file in enumerate(note_files, 1):
        analysis = analyze_slide(note_file, i)
        results.append(analysis)

    return results


def generate_analysis_report(analyses: List[SlideAnalysis]) -> str:
    """Generate a text report of the analysis."""
    lines = ["# Content Analysis Report\n"]

    total_pause = sum(a.recommended_pause for a in analyses)
    avg_density = sum(a.content_density for a in analyses) / len(analyses) if analyses else 0

    lines.append(f"Total slides: {len(analyses)}")
    lines.append(f"Average content density: {avg_density:.2f}")
    lines.append(f"Total recommended pause time: {total_pause:.1f}s\n")

    lines.append("## Slide Details\n")
    for a in analyses:
        lines.append(f"### Slide {a.slide_num}: {a.title}")
        lines.append(f"- Density: {a.content_density:.2f}")
        lines.append(f"- Has chart: {'Yes' if a.has_chart else 'No'}")
        lines.append(f"- Recommended pause: {a.recommended_pause}s")
        if a.key_points:
            lines.append(f"- Key points: {len(a.key_points)}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 content_analyzer.py <project_path>")
        sys.exit(1)

    project = Path(sys.argv[1])
    analyses = analyze_project(project)

    if not analyses:
        print("No slides found to analyze.")
        sys.exit(1)

    report = generate_analysis_report(analyses)
    print(report)

    # Save report
    report_path = project / "content_analysis.md"
    report_path.write_text(report, encoding='utf-8')
    print(f"\nReport saved to: {report_path}")
