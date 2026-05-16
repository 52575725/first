#!/usr/bin/env python3
"""PPT视频生成工具 - 一键生成带字幕的智能布局视频"""

import json
import subprocess
import sys
import re
import argparse
import hashlib
import os
import shutil
import threading
import time
import math
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from functools import lru_cache


VIDEO_W = 1920
VIDEO_H = 1080
MIN_SEGMENT_BYTES = 32 * 1024
RENDER_CONTEXT = threading.local()


def ensure_media_tools_on_path():
    """Make a working ffmpeg/ffprobe pair discoverable after moving the repo."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        try:
            probe = subprocess.run(
                [ffmpeg, "-hide_banner", "-h", "filter=drawtext"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            probe = None
        if probe and probe.returncode == 0:
            output = f"{probe.stdout}\n{probe.stderr}".lower()
            if "unknown filter" not in output:
                return

    def candidate_dirs():
        repo_root = Path(__file__).resolve().parents[3]
        roots = [
            Path("D:/tools/ffmpeg/ffmpeg-8.1.1-essentials_build/bin"),
            Path("D:/tools/ffmpeg"),
            repo_root / "tools" / "ffmpeg" / "bin",
            repo_root / "tools" / "ffmpeg",
            Path("C:/tools/ffmpeg"),
        ]
        local_appdata = Path.home() / "AppData" / "Local"
        roots.extend(local_appdata.glob("WeMod/app-*/resources/app.asar.unpacked/static/unpacked/capture/release/bin/64bit"))
        roots.extend(Path("C:/Program Files (x86)/Lenovo/LegionZone").glob("*/SEGamingAI/services/editor"))
        if Path("D:/tools/ffmpeg").exists():
            roots.extend(Path("D:/tools/ffmpeg").glob("*/bin"))
        return roots

    def is_working_media_dir(path):
        try:
            ffmpeg_path = path / "ffmpeg.exe"
            ffprobe_path = path / "ffprobe.exe"
            if not ffmpeg_path.exists() or not ffprobe_path.exists():
                return False
            probe = subprocess.run(
                [str(ffmpeg_path), "-hide_banner", "-h", "filter=drawtext"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        output = f"{probe.stdout}\n{probe.stderr}".lower()
        return probe.returncode == 0 and "unknown filter" not in output

    existing = []
    for path in candidate_dirs():
        try:
            if path.exists() and is_working_media_dir(path):
                path_text = str(path)
                if path_text not in existing:
                    existing.append(path_text)
        except OSError:
            continue
    if existing:
        current_path = os.environ.get("PATH", "")
        parts = existing + ([current_path] if current_path else [])
        os.environ["PATH"] = os.pathsep.join(parts)


ensure_media_tools_on_path()


class VideoConfig:
    """视频配置"""
    def __init__(self):
        self.subtitle_font_size = 38
        self.subtitle_margin_lr = 100
        self.subtitle_margin_v = 52
        self.video_font_size_title = 28
        self.video_font_size_content = 32
        self.style = None


COMPONENT_LAYOUT_MAP = {
    "section_title": "single_column",
    "bullet_reveal": "single_column",
    "three_card_summary": "three_column",
    "process_flow": "three_column",
    "timeline": "three_column",
    "kpi_cards": "three_column",
    "chart_focus": "three_column",
    "two_column_compare": "two_column",
    "preserve_slide": "preserve_slide",
    "callout_overlay": "preserve_slide",
    "image_pan_zoom": "preserve_slide",
    "cover_hero": "single_column",
    "statement_focus": "single_column",
    "quote_focus": "single_column",
    "insight_cards": "three_column",
    "problem_stack": "three_column",
    "solution_flow": "three_column",
    "capability_matrix": "three_column",
    "before_after": "two_column",
    "dense_grid": "three_column",
    "split_text_visual": "two_column",
    "roadmap_timeline": "three_column",
    "lifecycle_loop": "three_column",
    "flywheel": "three_column",
    "metric_dashboard": "three_column",
    "market_dashboard": "three_column",
    "revenue_model": "three_column",
    "financial_snapshot": "three_column",
    "image_hero": "preserve_slide",
    "photo_story": "preserve_slide",
    "image_mosaic": "preserve_slide",
    "product_showcase": "preserve_slide",
    "map_focus": "preserve_slide",
    "team_roster": "three_column",
    "role_grid": "three_column",
    "org_chart": "three_column",
    "blackboard_derivation": "diverse",
    "formula_walkthrough": "diverse",
    "checkpoint_ladder": "diverse",
    "radial_concept_map": "diverse",
    "magazine_spread": "diverse",
    "rounded_step_cards": "diverse",
    "misconception_compare": "diverse",
    "application_storyboard": "diverse",
}


def load_component_recommendations(project):
    """Load component recommendations keyed by slide number."""
    path = project / "component_recommendations.json"
    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "recommendations" in data:
        data = data["recommendations"]

    if not isinstance(data, list):
        raise ValueError(f"Invalid component recommendations: {path}")

    recommendations = {}
    for item in data:
        slide_number = item.get("slide_number")
        if slide_number is not None:
            recommendations[int(slide_number)] = item
    return recommendations


def load_render_plan(project):
    """Load director render-plan metadata keyed by slide number."""
    path = project / "render_plan.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    slides = data.get("slides", []) if isinstance(data, dict) else []
    result = {}
    for item in slides:
        try:
            slide_number = int(item.get("slide_number"))
        except Exception:
            continue
        result[slide_number] = item
    return result


def recommended_component(recommendation):
    """Return the primary component id from one recommendation object."""
    if not recommendation:
        return None
    return recommendation.get("primary_component") or recommendation.get("component", {}).get("id")


def recommended_visual_effect(slide_data, recommendation):
    """Resolve the component-like visual effect used over preserved slide art."""
    strategy = recommendation.get("render_strategy", {}) if recommendation else {}
    effect = strategy.get("visual_effect") or strategy.get("effect_component")
    if effect:
        return effect

    component_id = recommended_component(recommendation)
    if component_id and component_id != "preserve_slide":
        return component_id

    layout = slide_data.get("layout", {})
    signals = slide_data.get("signals", {})
    page_type = recommendation.get("page_type") if recommendation else None
    page_type = page_type or layout.get("page_type", "")

    if page_type == "comparison" or signals.get("has_comparison"):
        return "two_column_compare"
    if page_type == "process" or signals.get("has_process"):
        return "process_flow"
    if page_type == "data" or signals.get("has_data"):
        return "chart_focus"
    if int(layout.get("columns", 1) or 1) >= 3:
        return "three_card_summary"
    return "callout_overlay"


def _clamp(value, low, high):
    return max(low, min(high, value))


def _block_box(block, pad=18):
    x = int(round(float(block.get("x", 0)))) - pad
    y = int(round(float(block.get("y", 0)))) - pad
    w = int(round(float(block.get("w", 0)))) + pad * 2
    h = int(round(float(block.get("h", 0)))) + pad * 2
    x = _clamp(x, 0, VIDEO_W - 1)
    y = _clamp(y, 0, VIDEO_H - 1)
    w = _clamp(w, 1, VIDEO_W - x)
    h = _clamp(h, 1, VIDEO_H - y)
    return x, y, w, h


def _box_union(boxes, pad=24):
    if not boxes:
        return 140, 240, 1640, 660
    left = min(x for x, _, _, _ in boxes) - pad
    top = min(y for _, y, _, _ in boxes) - pad
    right = max(x + w for x, _, w, _ in boxes) + pad
    bottom = max(y + h for _, y, _, h in boxes) + pad
    left = _clamp(left, 0, VIDEO_W - 1)
    top = _clamp(top, 0, VIDEO_H - 1)
    right = _clamp(right, left + 1, VIDEO_W)
    bottom = _clamp(bottom, top + 1, VIDEO_H)
    return left, top, right - left, bottom - top


def _useful_text_blocks(slide_data, limit=8, include_title=False):
    blocks = []
    for block in slide_data.get("text_blocks", []):
        text = str(block.get("text", "")).strip()
        if not text:
            continue
        conf = float(block.get("conf", 0) or 0)
        role = block.get("role", "")
        x, y, w, h = _block_box(block, pad=0)
        area = w * h
        if conf < 62 and area < 70000:
            continue
        if w < 55 or h < 14:
            continue
        if not include_title and role == "title" and y < 230:
            continue
        if len(text) <= 1 and area < 50000:
            continue
        score = area + conf * 1200
        if role == "subtitle":
            score += 55000
        elif role == "body":
            score += 25000
        blocks.append((score, block))

    blocks.sort(key=lambda item: item[0], reverse=True)
    selected = [block for _, block in blocks[: max(1, limit * 2)]]
    selected.sort(key=lambda item: (int(item.get("y", 0)), int(item.get("x", 0))))
    return selected[:limit]


def _enable(start, end):
    start = max(0.0, float(start))
    end = max(start + 0.05, float(end))
    return f":enable='between(t,{start:.2f},{end:.2f})'"


def _drawbox(vf_parts, box, color, thickness, start=None, end=None):
    x, y, w, h = box
    enabled = _enable(start, end) if start is not None and end is not None else ""
    vf_parts.append(
        f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t={thickness}{enabled}"
    )


def _hex_to_rgb(color):
    color = str(color or "#2375ff").lstrip("#")
    if len(color) != 6:
        return 35, 117, 255
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    r, g, b = [int(_clamp(v, 0, 255)) for v in rgb]
    return f"#{r:02x}{g:02x}{b:02x}"


def _relative_luminance(color):
    r, g, b = _hex_to_rgb(color)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _adjust_color(color, factor):
    r, g, b = _hex_to_rgb(color)
    if factor >= 1:
        rgb = [v + (255 - v) * (factor - 1) for v in (r, g, b)]
    else:
        rgb = [v * factor for v in (r, g, b)]
    return _rgb_to_hex(rgb)


def _ffmpeg_hex(color):
    return "0x" + str(color or "#2375ff").lstrip("#")


def _theme_accent(theme):
    return (theme or {}).get("accent", "#2375ff")


def _theme_soft(theme, alpha=0.12):
    return f"{_ffmpeg_hex(_theme_accent(theme))}@{alpha:.2f}"


def _enable_after(start):
    return f":enable='gte(t,{max(0.0, float(start)):.2f})'"


def _add_focus_mask(vf_parts, box, start, end, alpha="black@0.36"):
    x, y, w, h = box
    if y > 0:
        _drawbox(vf_parts, (0, 0, VIDEO_W, y), alpha, "fill", start, end)
    if y + h < VIDEO_H:
        _drawbox(vf_parts, (0, y + h, VIDEO_W, VIDEO_H - y - h), alpha, "fill", start, end)
    if x > 0:
        _drawbox(vf_parts, (0, y, x, h), alpha, "fill", start, end)
    if x + w < VIDEO_W:
        _drawbox(vf_parts, (x + w, y, VIDEO_W - x - w, h), alpha, "fill", start, end)


def _timeline_windows(duration, count, start_pad=0.55, end_pad=0.35):
    count = max(1, count)
    usable = max(0.8, duration - start_pad - end_pad)
    step = usable / count
    windows = []
    for idx in range(count):
        start = start_pad + idx * step
        end = min(duration - 0.15, start + max(0.75, step * 0.88))
        windows.append((start, end))
    return windows


def _apply_callout_effect(vf_parts, slide_data, duration, theme=None):
    blocks = _useful_text_blocks(slide_data, limit=4, include_title=True)
    if not blocks:
        _drawbox(vf_parts, (120, 120, 1680, 820), _theme_soft(theme, 0.10), "fill", 0.5, duration - 0.25)
        return "callout_overlay"

    for block, (start, end) in zip(blocks, _timeline_windows(duration, len(blocks))):
        box = _block_box(block, pad=24)
        _drawbox(vf_parts, box, _theme_soft(theme, 0.12), "fill", start, end)
    return "callout_overlay"


def _apply_chart_focus(vf_parts, slide_data, duration, theme=None):
    blocks = _useful_text_blocks(slide_data, limit=7)
    boxes = [_block_box(block, pad=14) for block in blocks]
    focus = _box_union(boxes, pad=34)
    start = 0.45
    end = max(start + 0.2, duration - 0.25)
    _add_focus_mask(vf_parts, focus, start, end)
    _drawbox(vf_parts, focus, "white@0.10", "fill", start, end)

    for block, (s, e) in zip(blocks[:3], _timeline_windows(duration, min(3, len(blocks)))):
        box = _block_box(block, pad=18)
        _drawbox(vf_parts, box, _theme_soft(theme, 0.13), "fill", s, e)
    return "chart_focus"


def _fallback_content_area(slide_data):
    blocks = _useful_text_blocks(slide_data, limit=8)
    if not blocks:
        return 160, 260, 1600, 650
    return _box_union([_block_box(block, pad=0) for block in blocks], pad=38)


def _split_area(area, count, horizontal=True):
    x, y, w, h = area
    boxes = []
    if horizontal:
        gap = 22
        item_w = max(1, int((w - gap * (count - 1)) / count))
        for idx in range(count):
            boxes.append((x + idx * (item_w + gap), y, item_w, h))
    else:
        gap = 18
        item_h = max(1, int((h - gap * (count - 1)) / count))
        for idx in range(count):
            boxes.append((x, y + idx * (item_h + gap), w, item_h))
    return boxes


def _apply_process_effect(vf_parts, slide_data, duration, count=4, theme=None):
    area = _fallback_content_area(slide_data)
    x, y, w, h = area
    horizontal = w >= h * 1.25
    boxes = _split_area(area, count, horizontal=horizontal)
    windows = _timeline_windows(duration, len(boxes), start_pad=0.45)
    accent = _ffmpeg_hex(_theme_accent(theme))
    for idx, (box, (start, end)) in enumerate(zip(boxes, windows)):
        _drawbox(vf_parts, box, f"{accent}@0.10", "fill", start, end)
        progress_w = int((idx + 1) * VIDEO_W / len(boxes))
        _drawbox(vf_parts, (0, VIDEO_H - 8, progress_w, 8), f"{accent}@0.55", "fill", start, duration)
    return "process_flow"


def _apply_two_column_effect(vf_parts, slide_data, duration, theme=None):
    blocks = _useful_text_blocks(slide_data, limit=10)
    left = []
    right = []
    for block in blocks:
        box = _block_box(block, pad=10)
        center = box[0] + box[2] / 2
        (left if center < VIDEO_W / 2 else right).append(box)

    left_box = _box_union(left, pad=34) if left else (100, 250, 820, 640)
    right_box = _box_union(right, pad=34) if right else (1000, 250, 820, 640)
    mid = max(1.2, duration * 0.52)
    accent = _theme_accent(theme)
    _drawbox(vf_parts, left_box, f"{_ffmpeg_hex(accent)}@0.08", "fill", 0.45, mid)
    _drawbox(vf_parts, right_box, f"{_ffmpeg_hex(_adjust_color(accent, 1.45))}@0.09", "fill", mid - 0.25, duration - 0.2)
    _drawbox(vf_parts, (VIDEO_W // 2 - 1, 220, 2, 710), "white@0.22", "fill", 0.45, duration - 0.2)
    return "two_column_compare"


def _apply_kpi_effect(vf_parts, slide_data, duration, theme=None):
    number_re = re.compile(r"\d")
    blocks = [
        block for block in _useful_text_blocks(slide_data, limit=10, include_title=True)
        if number_re.search(str(block.get("text", "")))
    ]
    if not blocks:
        return _apply_chart_focus(vf_parts, slide_data, duration, theme)
    for block, (start, end) in zip(blocks[:4], _timeline_windows(duration, min(4, len(blocks)))):
        box = _block_box(block, pad=26)
        _drawbox(vf_parts, box, _theme_soft(theme, 0.14), "fill", start, end)
    return "kpi_cards"


def build_preserved_slide_filter(slide_data, recommendation, duration, theme=None):
    vf_parts = [
        f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease",
        f"pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2:color=#f7f8fa",
        "setsar=1",
        "format=rgba",
    ]
    effect = recommended_visual_effect(slide_data, recommendation)

    if effect in {"process_flow", "timeline", "solution_flow", "roadmap_timeline", "lifecycle_loop", "flywheel"}:
        applied = _apply_process_effect(vf_parts, slide_data, duration, theme=theme)
    elif effect in {"two_column_compare", "before_after", "comparison_columns"}:
        applied = _apply_two_column_effect(vf_parts, slide_data, duration, theme=theme)
    elif effect in {"chart_focus", "metric_dashboard", "market_dashboard", "revenue_model", "financial_snapshot"}:
        applied = _apply_chart_focus(vf_parts, slide_data, duration, theme=theme)
    elif effect in {"kpi_cards", "gauge_chart", "progress_bar_chart"}:
        applied = _apply_kpi_effect(vf_parts, slide_data, duration, theme=theme)
    elif effect in {"three_card_summary", "bullet_reveal", "insight_cards", "problem_stack", "capability_matrix", "dense_grid", "team_roster", "role_grid"}:
        applied = _apply_process_effect(vf_parts, slide_data, duration, count=3, theme=theme)
    else:
        applied = _apply_callout_effect(vf_parts, slide_data, duration, theme=theme)

    vf_parts.append("fade=t=in:st=0:d=0.22")
    vf_parts.append(f"fade=t=out:st={max(0.0, duration - 0.25):.2f}:d=0.22")
    vf_parts.append("format=yuv420p")
    return ",".join(vf_parts), applied


def source_slide_lines(project, slide_num):
    """Read clean per-slide text from imported Markdown sources when available."""
    if not project:
        return []
    sources_dir = project / "sources"
    if not sources_dir.exists():
        return []

    for source in sorted(sources_dir.glob("*.md")):
        lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
        in_slide = False
        collected = []
        for line in lines:
            match = re.match(r"^##\s+Slide\s+(\d+)\s*$", line.strip(), re.I)
            if match:
                if in_slide:
                    break
                in_slide = int(match.group(1)) == slide_num
                continue
            if not in_slide:
                continue

            text = line.strip()
            if not text:
                continue
            if text.startswith("## "):
                break
            if text.startswith("### Speaker Notes"):
                break
            if text.startswith("![") or text.startswith("<") or text.startswith("‹#›"):
                continue
            text = re.sub(r"!\[[^\]]*\]\(.*\)", "", text).strip()
            if not text:
                continue
            text = re.sub(r"^[-*+]\s+", "", text).strip()
            if is_noise_line(text):
                continue
            if text:
                collected.append(text)

        if collected:
            return collected
    return []


def is_catalog_marker(text):
    return normalize_video_text(text).replace(" ", "") in {
        "目录", "目錄", "本章主要内容", "主要内容", "内容提要", "contents", "Contents", "outline", "agenda"
    }


def is_catalog_number(text):
    return bool(re.fullmatch(r"\d{1,2}", normalize_video_text(text)))


def catalog_items_from_lines(lines):
    clean = [normalize_video_text(line) for line in lines if normalize_video_text(line)]
    # Numbered process slides often contain "01/02/03" labels; only treat a
    # slide as a catalog when an explicit catalog marker is present.
    has_catalog_signal = any(is_catalog_marker(line) for line in clean)
    if not has_catalog_signal:
        return []

    candidates = [
        line
        for line in clean
        if not is_catalog_marker(line)
        and not is_catalog_number(line)
        and visual_text_len(line) >= 5
    ]
    seen = set()
    candidates = [line for line in candidates if not (line in seen or seen.add(line))]

    if len(candidates) > 3:
        for keyword in ("创新创业大赛", "创新创业", "大赛"):
            related = [line for line in candidates if keyword in line]
            if len(related) >= 3:
                candidates = related
                break

    return candidates[:4]


def source_slide_has_image(project, slide_num):
    """Return whether imported Markdown has a real image on this slide."""
    if not project:
        return False
    sources_dir = project / "sources"
    if not sources_dir.exists():
        return False

    for source in sorted(sources_dir.glob("*.md")):
        lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
        in_slide = False
        for line in lines:
            match = re.match(r"^##\s+Slide\s+(\d+)\s*$", line.strip(), re.I)
            if match:
                if in_slide:
                    break
                in_slide = int(match.group(1)) == slide_num
                continue
            if not in_slide:
                continue

            text = line.strip()
            if text.startswith("## "):
                break
            if text.startswith("!["):
                return True
    return False


def normalize_math_for_screen(text):
    """Keep display math as professional notation while cleaning common variants."""
    text = str(text or "")
    text = re.sub(r"(?<![A-Za-z])V\s*(?=[(（0-9A-Za-zπ])", "√", text)
    text = re.sub(r"(?<![A-Za-z])v\s*(?=[(（0-9π])", "√", text)
    text = re.sub(r"正负\s*根号\s*([A-Za-z0-9π]+)", r"±√\1", text)
    text = re.sub(r"正负\s*([A-Za-z0-9π]+)", r"±\1", text)
    text = re.sub(r"(\d+)\s*[倍借]\s*根号\s*([A-Za-z0-9π]+)", r"\1√\2", text)
    text = re.sub(r"根号\s*\(([^)）]+)\)", r"√(\1)", text)
    text = re.sub(r"根号\s*（([^)）]+)）", r"√(\1)", text)
    text = re.sub(r"根号\s*([A-Za-z0-9π]+)", r"√\1", text)
    text = re.sub(r"(?<=[A-Za-z0-9π)）√])\s*[#＃]\s*(?=√|[A-Za-z0-9π(（])", " ÷ ", text)
    radical_piece = r"(?:[A-Za-z0-9π]+)?\s*√\s*(?:[A-Za-z0-9π²³]+|\([^()（）]+\)|（[^()（）]+）)"
    text = re.sub(
        rf"({radical_piece})\s*(?:变成|化成|化为|化简为|写成|得到)\s*({radical_piece})",
        r"\1 → \2",
        text,
    )
    text = re.sub(r"([A-Za-z0-9)\]）π]+)\s*的平方(?!根)", r"\1²", text)
    text = re.sub(r"([A-Za-z0-9)\]）π]+)\s*的立方(?!根)", r"\1³", text)
    text = re.sub(r"\^\s*2\b", "²", text)
    text = re.sub(r"\^\s*3\b", "³", text)
    return text


def restore_math_notation_for_subtitles(text):
    """Recover concise math notation from voice-friendly subtitle text."""
    text = normalize_video_text(text)
    replacements = [
        (r"正负\s*根号\s*([A-Za-z0-9π]+)", r"±√\1"),
        (r"正负\s*([A-Za-z0-9π]+)", r"±\1"),
        (r"根号\s*\(([^)）]+)\)", r"√(\1)"),
        (r"根号\s*（([^)）]+)）", r"√(\1)"),
        (r"根号\s*([A-Za-z0-9π]+)", r"√\1"),
        (r"([A-Za-z0-9)\]）π]+)\s*的平方(?!根)", r"\1²"),
        (r"([A-Za-z0-9)\]）π]+)\s*的立方(?!根)", r"\1³"),
        (r"\s*乘以\s*", " × "),
        (r"\s*除以\s*", " ÷ "),
        (r"\s*大于等于\s*", " ≥ "),
        (r"\s*小于等于\s*", " ≤ "),
        (r"\s*不等于\s*", " ≠ "),
        (r"\s*等于\s*", " = "),
        (r"\s*大于\s*", " > "),
        (r"\s*小于\s*", " < "),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s+([，。；：！？])", r"\1", text)
    text = re.sub(r"([（(])\s+", r"\1", text)
    text = re.sub(r"\s+([）)])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_video_text(text):
    """Light cleanup for OCR/PPT text before recomposed video rendering."""
    text = str(text or "").strip()
    text = normalize_math_for_screen(text)
    text = re.sub(r"([\u4e00-\u9fff])\1{2,}", r"\1", text)
    text = text.replace(": :", "：").replace("：：", "：")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_no_content_placeholder(text):
    cleaned = normalize_video_text(text).strip("_ ").lower()
    cleaned = cleaned.rstrip(".")
    if "no extractable text content" in cleaned:
        return True
    return bool(re.fullmatch(r"(?:>\s*)?\[image\]\s*image\s*\d+", cleaned))


def visual_text_len(text):
    """Approximate rendered text width for Chinese/Latin mixed strings."""
    total = 0.0
    for ch in str(text):
        total += 1.0 if "\u4e00" <= ch <= "\u9fff" else 0.55
    return total


MATH_FONT = "'C\\:/Windows/Fonts/times.ttf'"
CHINESE_FONT = "'C\\:/Windows/Fonts/msyh.ttc'"
CHINESE_FONT_BOLD = "'C\\:/Windows/Fonts/msyhbd.ttc'"
MATH_TEXT_RE = re.compile(r"[A-Za-z0-9√±×÷=<>≤≥≠²³πμ→+\-*/^().（）\[\]\s,，、;；:：|]+")
MATH_SIGNAL_RE = re.compile(r"[A-Za-z√±×÷=<>≤≥≠²³πμ→^]")
STRONG_MATH_SIGNAL_RE = re.compile(r"(√|±|×|÷|≤|≥|≠|²|³|→|\^|=|[A-Za-z0-9]\s*[+\-*/<>]\s*[A-Za-z0-9√])")
RADICAL_BODY_RE = re.compile(r"\s*([A-Za-z0-9π²³]+)")


def has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text))


def is_math_segment(text):
    text = str(text or "")
    return bool(text.strip() and not has_cjk(text) and MATH_SIGNAL_RE.search(text))


def contains_math_notation(text):
    return any(is_math_segment(match.group(0)) for match in MATH_TEXT_RE.finditer(str(text or "")))


def contains_display_formula(text):
    text = normalize_math_for_screen(str(text or ""))
    return bool(STRONG_MATH_SIGNAL_RE.search(text))


def is_pure_math_text(text):
    text = normalize_math_for_screen(str(text or "")).strip()
    if not text or has_cjk(text):
        return False
    if not contains_display_formula(text):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9√±×÷=<>≤≥≠²³πμ→+\-*/^().（）\[\]\s,，、;；:：|]+", text))


def split_radical_tokens(segment):
    """Split math text so radicals can be rendered with a vinculum."""
    text = str(segment or "")
    tokens = []
    idx = 0
    while idx < len(text):
        root_idx = text.find("√", idx)
        if root_idx < 0:
            if idx < len(text):
                tokens.append(("text", text[idx:]))
            break
        if root_idx > idx:
            tokens.append(("text", text[idx:root_idx]))
        body_start = root_idx + 1
        if body_start >= len(text):
            tokens.append(("text", "√"))
            idx = body_start
            continue
        if text[body_start] in "(（":
            close = ")" if text[body_start] == "(" else "）"
            depth = 1
            pos = body_start + 1
            while pos < len(text):
                if text[pos] == text[body_start]:
                    depth += 1
                elif text[pos] == close:
                    depth -= 1
                    if depth == 0:
                        break
                pos += 1
            if pos < len(text) and depth == 0:
                body = text[body_start + 1:pos].strip()
                tokens.append(("radical", body or " "))
                idx = pos + 1
            else:
                body = text[body_start:].strip()
                tokens.append(("radical", body or " "))
                break
            continue
        match = RADICAL_BODY_RE.match(text, body_start)
        if match:
            body = match.group(1).strip()
            if body:
                tokens.append(("radical", body))
                idx = match.end()
                continue
        tokens.append(("text", "√"))
        idx = body_start
    return [(kind, value) for kind, value in tokens if value]


def radical_token_width(body, font_size):
    return int(max(font_size * 1.05, font_size * 0.62 + estimate_text_px(body, font_size, True) + font_size * 0.16))


def contains_radical_notation(text):
    return "√" in str(text or "")


def formula_asset_dir(project):
    if not project:
        return None
    path = Path(project) / "temp_video" / "formula_assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def active_formula_assets():
    assets = getattr(RENDER_CONTEXT, "formula_assets", None)
    if assets is None:
        assets = []
        RENDER_CONTEXT.formula_assets = assets
    return assets


def math_font_path(bold=False):
    candidates = [
        "C:/Windows/Fonts/cambriab.ttf" if bold else "C:/Windows/Fonts/cambria.ttc",
        "C:/Windows/Fonts/cambria.ttc",
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return candidates[-1]


def parse_radical_body(text, start):
    text = str(text or "")
    if start >= len(text):
        return "", start
    if text[start] in "(（[":
        pairs = {"(": ")", "（": "）", "[": "]"}
        close = pairs[text[start]]
        depth = 1
        pos = start + 1
        while pos < len(text):
            if text[pos] == text[start]:
                depth += 1
            elif text[pos] == close:
                depth -= 1
                if depth == 0:
                    return text[start + 1:pos].strip(), pos + 1
            pos += 1
        return text[start + 1:].strip(), len(text)
    match = re.match(r"\s*([A-Za-z0-9π²³]+)", text[start:])
    if match:
        return match.group(1).strip(), start + match.end()
    return "", start


def formula_to_mathml(text):
    """Convert compact formula text into small MathML fragments for browser-quality radical layout."""
    text = normalize_math_for_screen(str(text or "")).strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", " ", text)

    def mi(token):
        if not token:
            return ""
        escaped = html.escape(token)
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return f"<mn>{escaped}</mn>"
        return f"<mi>{escaped}</mi>"

    def render_plain(segment):
        out = []
        for part in re.findall(r"[A-Za-z]+|[πμ]|\d+(?:\.\d+)?|[²³]|[±×÷=<>≤≥≠+\-*/(), ]|.", segment):
            if not part or part.isspace():
                out.append("<mspace width='0.28em'/>")
            elif part in {"²", "³"}:
                exp = "2" if part == "²" else "3"
                base = out.pop() if out else "<mi></mi>"
                out.append(f"<msup>{base}<mn>{exp}</mn></msup>")
            elif re.fullmatch(r"[A-Za-z]+|[πμ]|\d+(?:\.\d+)?", part):
                out.append(mi(part))
            elif part in {"±", "×", "÷", "=", "<", ">", "≤", "≥", "≠", "+", "-", "*", "/"}:
                op = {"*": "×", "/": "÷"}.get(part, part)
                out.append(f"<mo>{html.escape(op)}</mo>")
            elif part in {"(", ")", ","}:
                out.append(f"<mo>{html.escape(part)}</mo>")
            else:
                out.append(f"<mtext>{html.escape(part)}</mtext>")
        return "".join(out)

    def render_expr(expr):
        expr = str(expr or "").strip()
        out = []
        idx = 0
        while idx < len(expr):
            root_idx = expr.find("√", idx)
            if root_idx < 0:
                out.append(render_plain(expr[idx:]))
                break
            if root_idx > idx:
                out.append(render_plain(expr[idx:root_idx]))
            body, next_idx = parse_radical_body(expr, root_idx + 1)
            if body:
                out.append(f"<msqrt>{render_expr(body)}</msqrt>")
                idx = next_idx
            else:
                out.append("<mo>√</mo>")
                idx = root_idx + 1
        return "".join(out)

    return (
        "<math xmlns='http://www.w3.org/1998/Math/MathML' display='block'>"
        f"<mrow>{render_expr(text)}</mrow>"
        "</math>"
    )


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _ensure_repo_python_packages():
    py_tag = f"{sys.version_info.major}{sys.version_info.minor}"
    site_packages = _repo_root() / f".venv{py_tag}" / "Lib" / "site-packages"
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))


def _ensure_mathtext_renderer(config_dir):
    """Load matplotlib mathtext, adding the project venv path when the script is
    launched with the system Python."""
    if config_dir:
        try:
            Path(config_dir).mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("MPLCONFIGDIR", str(config_dir))
        except Exception:
            pass

    try:
        from matplotlib import mathtext
        from matplotlib.font_manager import FontProperties
        return mathtext, FontProperties
    except Exception:
        pass

    _ensure_repo_python_packages()

    from matplotlib import mathtext
    from matplotlib.font_manager import FontProperties
    return mathtext, FontProperties


def formula_to_mathtext(text):
    """Convert compact screen math into matplotlib mathtext syntax."""
    text = normalize_math_for_screen(str(text or "")).strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", " ", text)
    compact = re.sub(r"\s+", "", text)
    if compact in {"F=μN", "f=μN"}:
        return r"$F=\mu N$"
    if compact in {"0≤fs≤μsN", "0≤f_s≤μ_sN", "0≤f静≤μsN"}:
        return r"$0\leq f_s\leq \mu_s N$"
    if compact in {"√a÷√b=√(a÷b)", "√a/√b=√(a/b)"}:
        return r"$\frac{\sqrt{a}}{\sqrt{b}}=\sqrt{\frac{a}{b}}$"
    if compact in {"√a×√b=√(a×b)", "√a*√b=√(a*b)"}:
        return r"$\sqrt{a}\times\sqrt{b}=\sqrt{a\times b}$"
    if compact in {"√a;a≥0", "√a；a≥0"}:
        return r"$\sqrt{a},\quad a\geq0$"
    if compact == "x²=a→x=√a":
        return r"$x^2=a,\quad x\geq0\Rightarrow x=\sqrt{a}$"
    if compact == "√9=3;x²=9→x=±3":
        return r"$\sqrt{9}=3,\quad x^2=9\Rightarrow x=\pm3$"
    if compact == "√(x+2)=x→x=2":
        return r"$\sqrt{x+2}=x\Rightarrow x=2$"

    op_map = {
        "±": r"\pm",
        "×": r"\times",
        "*": r"\times",
        "÷": r"\div",
        "/": r"\div",
        "≤": r"\leq",
        "≥": r"\geq",
        "≠": r"\neq",
        "→": r"\rightarrow",
        "π": r"\pi",
        "μ": r"\mu",
    }

    def render_plain(segment):
        out = []
        token_re = r"[A-Za-z]+|[πμ]|\d+(?:\.\d+)?|[²³]|[±×÷=<>≤≥≠→+\-*/^(),\[\]\s,，、;；:：|]|."
        for part in re.findall(token_re, str(segment or "")):
            if not part:
                continue
            if part.isspace():
                out.append(r"\,")
            elif part == "²":
                out.append("^2")
            elif part == "³":
                out.append("^3")
            elif re.fullmatch(r"[A-Za-z]+|\d+(?:\.\d+)?", part):
                out.append(part)
            elif part in op_map:
                out.append(op_map[part])
            elif part in {"=", "<", ">", "+", "-", "^", "(", ")", ",", "[", "]"}:
                out.append(part)
            elif part in {"，", "、", ";", "；"}:
                out.append(r",\quad ")
            elif part in {":", "："}:
                out.append(r":\quad ")
            elif part == "|":
                out.append(r"|")
            else:
                out.append(r"\mathrm{" + re.sub(r"[^A-Za-z0-9]+", "", part) + "}")
        return "".join(out)

    def render_expr(expr):
        expr = str(expr or "").strip()
        out = []
        idx = 0
        while idx < len(expr):
            root_idx = expr.find("√", idx)
            if root_idx < 0:
                out.append(render_plain(expr[idx:]))
                break
            if root_idx > idx:
                out.append(render_plain(expr[idx:root_idx]))
            body, next_idx = parse_radical_body(expr, root_idx + 1)
            if body:
                out.append(r"\sqrt{" + render_expr(body) + "}")
                idx = next_idx
            else:
                out.append(r"\sqrt{}")
                idx = root_idx + 1
        return "".join(out)

    return "$" + render_expr(text) + "$"


def _image_to_formula_alpha(image, color):
    """Turn mathtext's white background into an antialiased transparent mask."""
    from PIL import Image

    fg = _hex_to_rgb(color)
    bg = (255, 255, 255)
    source = image.convert("RGBA")
    result = Image.new("RGBA", source.size, (fg[0], fg[1], fg[2], 0))
    src = source.load()
    dst = result.load()
    for y in range(source.height):
        for x in range(source.width):
            r, g, b, _ = src[x, y]
            estimates = []
            for pixel, fore, back in ((r, fg[0], bg[0]), (g, fg[1], bg[1]), (b, fg[2], bg[2])):
                denom = back - fore
                if abs(denom) > 1:
                    estimates.append((back - pixel) / denom)
            alpha = max(estimates) if estimates else 0.0
            if alpha > 0.01:
                alpha_i = int(_clamp(alpha * 255, 0, 255))
                dst[x, y] = (fg[0], fg[1], fg[2], alpha_i)
    return result


def render_formula_asset_mathtext(out_dir, png_path, meta_path, formula, *, font_size, color, bold):
    mathtext, FontProperties = _ensure_mathtext_renderer(out_dir.parent / "mplconfig")
    from PIL import Image

    raw_path = out_dir / f"{png_path.stem}.{os.getpid()}.{threading.get_ident()}.raw.png"
    try:
        expr = formula_to_mathtext(formula)
        prop = FontProperties(
            family=["DejaVu Sans", "Cambria Math", "Cambria", "Times New Roman"],
            size=max(12, int(font_size)),
            weight="bold" if bold else "normal",
        )
        mathtext.math_to_image(expr, raw_path, prop=prop, dpi=180, format="png", color=color)
        image = Image.open(raw_path)
        transparent = _image_to_formula_alpha(image, color)
        bbox_alpha = transparent.getchannel("A").getbbox()
        if not bbox_alpha:
            return None
        crop_pad = max(3, int(font_size * 0.08))
        left = max(0, bbox_alpha[0] - crop_pad)
        top = max(0, bbox_alpha[1] - crop_pad)
        right = min(transparent.width, bbox_alpha[2] + crop_pad)
        bottom = min(transparent.height, bbox_alpha[3] + crop_pad)
        cropped = transparent.crop((left, top, right, bottom))
        cropped.save(png_path)
        meta = {
            "width": cropped.width,
            "height": cropped.height,
            "formula": formula,
            "renderer": "matplotlib_mathtext_v4",
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": png_path, "width": meta["width"], "height": meta["height"]}
    finally:
        try:
            raw_path.unlink(missing_ok=True)
        except Exception:
            pass


def render_formula_asset(project, text, *, font_size=48, color="#0f172a", bold=False):
    """Render a formula to transparent PNG as one reusable visual layer."""
    out_dir = formula_asset_dir(project)
    if not out_dir:
        return None
    formula = normalize_math_for_screen(text)
    if not formula or not contains_math_notation(formula):
        return None
    renderer_id = "pil_radical_v3" if contains_radical_notation(formula) else "matplotlib_mathtext_v4"
    key_payload = json.dumps(
        {
            "formula": formula,
            "font_size": int(font_size),
            "color": color,
            "bold": bool(bold),
            "renderer": renderer_id,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(key_payload).hexdigest()[:20]
    png_path = out_dir / f"formula_{digest}.png"
    meta_path = out_dir / f"formula_{digest}.json"
    if png_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if int(meta.get("width", 0)) > 0 and int(meta.get("height", 0)) > 0:
                return {"path": png_path, "width": int(meta["width"]), "height": int(meta["height"])}
        except Exception:
            pass

    if not contains_radical_notation(formula):
        try:
            asset = render_formula_asset_mathtext(
                out_dir, png_path, meta_path, formula,
                font_size=font_size, color=color, bold=bold
            )
            if asset:
                return asset
        except Exception:
            pass

    try:
        _ensure_repo_python_packages()
        from PIL import Image, ImageDraw, ImageFont

        rgba = _hex_to_rgb(color) + (255,)
        base_font = ImageFont.truetype(math_font_path(bold), int(font_size))
        small_font = ImageFont.truetype(math_font_path(bold), max(12, int(font_size * 0.58)))

        def bbox(font, value):
            box = font.getbbox(str(value or " "))
            return box[2] - box[0], box[3] - box[1], box

        def radical_metrics(body_w):
            root_w = max(18, int(font_size * 0.46))
            gap = max(3, int(font_size * 0.06))
            overhang = max(6, int(font_size * 0.14))
            return root_w, gap, overhang

        def measure_plain(segment):
            w = 0
            h = int(font_size * 1.18)
            for part in re.findall(r"[A-Za-zπ]+|\d+(?:\.\d+)?|[²³]|[±×÷=<>≤≥≠→+\-*/(),，、;； ]|.", segment):
                if not part:
                    continue
                if part.isspace():
                    w += int(font_size * 0.28)
                elif part in {"²", "³"}:
                    tw, th, _ = bbox(small_font, "2" if part == "²" else "3")
                    w += tw + 1
                    h = max(h, int(font_size * 1.25))
                else:
                    glyph = {"*": "×", "/": "÷", "，": ",", "、": ",", "；": ";", "→": "→"}.get(part, part)
                    tw, th, _ = bbox(base_font, glyph)
                    w += tw + 2
                    h = max(h, th + int(font_size * 0.36))
            return max(1, w), max(1, h)

        def measure_expr(expr):
            w = 0
            h = int(font_size * 1.20)
            idx = 0
            while idx < len(expr):
                root_idx = expr.find("√", idx)
                if root_idx < 0:
                    tw, th = measure_plain(expr[idx:])
                    w += tw
                    h = max(h, th)
                    break
                if root_idx > idx:
                    tw, th = measure_plain(expr[idx:root_idx])
                    w += tw
                    h = max(h, th)
                body, next_idx = parse_radical_body(expr, root_idx + 1)
                if body:
                    bw, bh = measure_expr(body)
                    root_w, gap, overhang = radical_metrics(bw)
                    w += root_w + gap + bw + overhang
                    h = max(h, bh + int(font_size * 0.42), int(font_size * 1.42))
                    idx = next_idx
                else:
                    rw, rh, _ = bbox(base_font, "√")
                    w += rw
                    h = max(h, rh)
                    idx = root_idx + 1
            return max(1, w), max(1, h)

        def draw_plain(draw, xy, segment):
            x0, y0 = xy
            cursor = x0
            for part in re.findall(r"[A-Za-zπ]+|\d+(?:\.\d+)?|[²³]|[±×÷=<>≤≥≠→+\-*/(),，、;； ]|.", segment):
                if not part:
                    continue
                if part.isspace():
                    cursor += int(font_size * 0.28)
                    continue
                if part in {"²", "³"}:
                    glyph = "2" if part == "²" else "3"
                    draw.text((cursor, y0 - int(font_size * 0.28)), glyph, font=small_font, fill=rgba)
                    cursor += bbox(small_font, glyph)[0] + 1
                    continue
                glyph = {"*": "×", "/": "÷", "，": ",", "、": ",", "；": ";", "→": "→"}.get(part, part)
                draw.text((cursor, y0), glyph, font=base_font, fill=rgba)
                cursor += bbox(base_font, glyph)[0] + 2
            return cursor

        def draw_expr(draw, xy, expr):
            x0, y0 = xy
            cursor = x0
            idx = 0
            while idx < len(expr):
                root_idx = expr.find("√", idx)
                if root_idx < 0:
                    cursor = draw_plain(draw, (cursor, y0), expr[idx:])
                    break
                if root_idx > idx:
                    cursor = draw_plain(draw, (cursor, y0), expr[idx:root_idx])
                body, next_idx = parse_radical_body(expr, root_idx + 1)
                if body:
                    bw, bh = measure_expr(body)
                    root_w, gap, overhang = radical_metrics(bw)
                    stroke = max(2, int(font_size * 0.055))
                    line_y = y0 + max(2, int(font_size * 0.06))
                    body_x = cursor + root_w + gap
                    body_y = y0 + int(font_size * 0.20)
                    end_x = body_x + bw + overhang
                    points = [
                        (cursor, y0 + int(font_size * 0.62)),
                        (cursor + int(root_w * 0.20), y0 + int(font_size * 0.56)),
                        (cursor + int(root_w * 0.40), y0 + int(font_size * 0.94)),
                        (cursor + int(root_w * 0.72), line_y),
                        (end_x, line_y),
                    ]
                    draw.line(points, fill=rgba, width=stroke)
                    draw_expr(draw, (body_x, body_y), body)
                    cursor = end_x + gap
                    idx = next_idx
                else:
                    cursor = draw_plain(draw, (cursor, y0), "√")
                    idx = root_idx + 1
            return cursor

        content_w, content_h = measure_expr(formula)
        pad_x = max(10, int(font_size * 0.22))
        pad_y = max(8, int(font_size * 0.18))
        image = Image.new("RGBA", (content_w + pad_x * 2, content_h + pad_y * 2), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        draw_expr(draw, (pad_x, pad_y + int(font_size * 0.10)), formula)
        alpha = image.getchannel("A")
        bbox_alpha = alpha.getbbox()
        if not bbox_alpha:
            return None
        crop_pad = max(3, int(font_size * 0.07))
        left = max(0, bbox_alpha[0] - crop_pad)
        top = max(0, bbox_alpha[1] - crop_pad)
        right = min(image.width, bbox_alpha[2] + crop_pad)
        bottom = min(image.height, bbox_alpha[3] + crop_pad)
        cropped = image.crop((left, top, right, bottom))
        cropped.save(png_path)
        meta = {"width": cropped.width, "height": cropped.height, "formula": formula, "renderer": renderer_id}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": png_path, "width": meta["width"], "height": meta["height"]}
    except Exception as exc:
        if os.environ.get("PPT_MASTER_DEBUG_FORMULA"):
            import traceback
            traceback.print_exc()
        return None


def add_formula_overlay(filters, current, layer_num, text, *, x, y, width, height, font_size, color, bold=False, start=0.0, project=None):
    project = project or getattr(RENDER_CONTEXT, "project", None)
    asset = render_formula_asset(project, text, font_size=font_size, color=color, bold=bold)
    if not asset:
        text_esc = escape_text(text)
        fontfile = math_font_path(bold)
        filters.append(
            f"[{current}]drawtext=text='{text_esc}':fontfile={fontfile}:"
            f"fontsize={font_size}:fontcolor={color}:expansion=none:x={x}:y={y}"
            f"{_enable_after(start)}[v{layer_num}]"
        )
        return f"v{layer_num}", layer_num + 1
    assets = active_formula_assets()
    input_index = len(assets)
    assets.append(Path(asset["path"]))
    iw = max(1, int(asset["width"]))
    ih = max(1, int(asset["height"]))
    scale = min(1.0, float(width) / iw, float(height) / ih)
    out_w = max(1, int(iw * scale))
    out_h = max(1, int(ih * scale))
    overlay_x = int(x + (width - out_w) / 2)
    overlay_y = int(y + (height - out_h) / 2)
    label = f"formula{layer_num}"
    filters.append(
        f"[formula_in{input_index}]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,format=rgba[{label}]"
    )
    filters.append(
        f"[{current}][{label}]overlay=x={overlay_x}:y={overlay_y}:shortest=1"
        f"{_enable_after(start)}[v{layer_num}]"
    )
    return f"v{layer_num}", layer_num + 1


def split_rich_text_segments(text):
    text = str(text or "")
    segments = []
    pos = 0
    for match in MATH_TEXT_RE.finditer(text):
        start, end = match.span()
        if start > pos:
            segments.append((text[pos:start], False))
        segment = match.group(0)
        segments.append((segment, is_math_segment(segment)))
        pos = end
    if pos < len(text):
        segments.append((text[pos:], False))
    return [(segment, math_like) for segment, math_like in segments if segment]


def estimate_text_px(text, font_size, math_like=False):
    if math_like and contains_radical_notation(text):
        total = 0
        for kind, value in split_radical_tokens(text):
            if kind == "radical":
                total += radical_token_width(value, font_size)
            else:
                total += estimate_text_px(value, font_size, False)
        return int(max(1, total))
    total = 0.0
    for ch in str(text):
        if "\u4e00" <= ch <= "\u9fff":
            total += font_size * 0.96
        elif ch.isspace():
            total += font_size * 0.35
        elif ch in "²³":
            total += font_size * 0.36
        elif ch in "√∑∫±×÷≤≥≠":
            total += font_size * 0.70
        elif math_like:
            total += font_size * 0.54
        else:
            total += font_size * 0.50
    return int(max(1, total))


def is_noise_line(text):
    text = normalize_video_text(text)
    return (
        not text
        or is_no_content_placeholder(text)
        or text.startswith("此处建议")
        or text.startswith("—")
        or text in {"-", "–", "—"}
        or text.startswith("▎")
        or text in {"01", "02", "03", "04", "05"}
    )


def is_card_heading(text):
    text = normalize_video_text(text)
    if is_noise_line(text):
        return False
    if "：" in text or ":" in text:
        return visual_text_len(text) <= 24
    return visual_text_len(text) <= 16


def is_card_subtitle(text):
    text = normalize_video_text(text)
    return bool(text and visual_text_len(text) <= 18 and ("·" in text or "/" in text or " / " in text))


def split_heading_subtitle(heading, subtitle=""):
    heading = normalize_video_text(heading)
    subtitle = normalize_video_text(subtitle)
    for sep in ("：", ":"):
        if sep in heading:
            left, right = heading.split(sep, 1)
            left = left.strip()
            right = right.strip()
            if left and right and visual_text_len(left) <= 12:
                return left, subtitle or right
    return heading, subtitle


EXAMPLE_HEADING_RE = re.compile(r"^(举例|例题|例子|示例|案例|例题提示|代入检查|演示)(?:\s*[：:].*)?$")


def is_example_heading(text):
    text = normalize_video_text(text).strip()
    if not text:
        return False
    head = re.split(r"[：:]", text, 1)[0].strip()
    return bool(EXAMPLE_HEADING_RE.match(text) or head in {"举例", "例题", "例子", "示例", "案例", "例题提示", "代入检查", "演示"})


def is_formula_rule_text(text):
    text = normalize_video_text(text)
    formula_symbols = ("√", "±", "²", "³", "×", "÷", "≤", "≥", "≠", "=", "^")
    return any(symbol in text for symbol in formula_symbols) or any(keyword in text for keyword in ("公式", "法则", "运算", "根号", "平方根", "化简"))


def is_radical_division_rule(title, rest):
    title = normalize_video_text(title)
    text = title + " " + " ".join(normalize_video_text(line) for line in rest)
    return "根号除法" in title or ("除法" in title and "√" in text)


def merge_example_cards(cards, limit=4):
    """Keep examples attached to a rule/formula card instead of floating alone."""
    merged = []
    examples = []
    for card in cards:
        title = normalize_video_text(card.get("title", ""))
        subtitle = normalize_video_text(card.get("subtitle", ""))
        body = normalize_video_text(card.get("body", ""))
        if is_example_heading(title):
            example_text = normalize_video_text(" ".join(part for part in (subtitle, body) if part))
            if example_text:
                examples.append(example_text)
            continue
        merged.append({**card, "title": title, "subtitle": subtitle, "body": body})

    for example in examples:
        if not merged:
            merged.append({"title": "例题演示", "subtitle": "", "body": example})
            continue
        target_idx = next(
            (
                idx for idx, item in reversed(list(enumerate(merged)))
                if is_formula_rule_text(item.get("title", "") + " " + item.get("body", ""))
            ),
            len(merged) - 1,
        )
        old_body = normalize_video_text(merged[target_idx].get("body", ""))
        merged[target_idx]["body"] = normalize_video_text(" ".join(part for part in (old_body, f"例：{example}") if part))
    return merged[:limit]


def separate_example_cards(cards, rest=None, limit_examples=2):
    main_cards = []
    examples = []
    for card in cards:
        title = normalize_video_text(card.get("title", ""))
        subtitle = normalize_video_text(card.get("subtitle", ""))
        body = normalize_video_text(card.get("body", ""))
        if is_example_heading(title):
            example_text = normalize_video_text(" ".join(part for part in (subtitle, body) if part))
            if example_text:
                examples.append(example_text)
            continue
        main_cards.append({**card, "title": title, "subtitle": subtitle, "body": body})
    for line in rest or []:
        line = normalize_video_text(line)
        if is_example_heading(line) and contains_math_notation(line):
            examples.append(re.sub(r"^[^：:]{1,8}[：:]\s*", "", line))
    deduped = []
    seen = set()
    for example in examples:
        key = enrichment_fingerprint(example)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(example)
        if len(deduped) >= limit_examples:
            break
    return main_cards, deduped


def cards_are_sparse_or_unstable(cards):
    if not cards:
        return True
    sparse = 0
    unstable = 0
    for card in cards:
        title = normalize_video_text(card.get("title", ""))
        subtitle = normalize_video_text(card.get("subtitle", ""))
        body = normalize_video_text(card.get("body", ""))
        if is_example_heading(title):
            unstable += 1
        if not body and not subtitle:
            sparse += 1
    return sparse >= max(2, len(cards) // 2 + 1) or unstable >= 1


ORDERED_POINT_RE = re.compile(
    r"^(一是|二是|三是|四是|五是|六是|七是|八是|九是|十是|"
    r"第一|第二|第三|第四|第五|第六|第七|第八|第九|第十)"
    r"[，,、：:\s]*(.+)$"
)
ORDERED_POINT_LABEL_RE = re.compile(
    r"^(一是|二是|三是|四是|五是|六是|七是|八是|九是|十是|"
    r"第一|第二|第三|第四|第五|第六|第七|第八|第九|第十)$"
)


def parse_ordered_point(text):
    text = normalize_video_text(text).strip()
    match = ORDERED_POINT_RE.match(text)
    if not match:
        return None
    label = match.group(1)
    body = match.group(2).strip().rstrip("。；;，,")
    return label, body


def is_ordered_point_label(text):
    return bool(ORDERED_POINT_LABEL_RE.match(normalize_video_text(text)))


def ordered_point_cards(clean, limit=4):
    first_ordered_idx = None
    ordered = []
    for idx, line in enumerate(clean):
        parsed = parse_ordered_point(line)
        if not parsed:
            continue
        if first_ordered_idx is None:
            first_ordered_idx = idx
        ordered.append(parsed)

    if len(ordered) < 2:
        return []

    cards = []
    intro_lines = clean[:first_ordered_idx] if first_ordered_idx is not None else []
    intro = next((line for line in intro_lines if not parse_ordered_point(line)), "")
    if intro and len(cards) < limit:
        metric_match = re.search(r"(\d{4}年|\d+(?:\.\d+)?%|\d+(?:\.\d+)?万|\d+(?:\.\d+)?亿)", intro)
        title = "背景"
        metric = metric_match.group(1) if metric_match else ""
        if "举办" in intro or "首届" in intro or "大赛" in intro:
            title = "赛事起源"
        cards.append({
            "title": title,
            "subtitle": metric,
            "body": intro,
        })

    for label, body in ordered:
        if len(cards) >= limit:
            break
        cards.append({
            "title": body or label,
            "subtitle": label,
            "body": "",
        })
    return cards[:limit]


def split_long_body_chunks(text, limit=3, target_units=42):
    text = normalize_video_text(text)
    if not text:
        return []

    parts = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。！？；;，," and visual_text_len(buf) >= 16:
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    if not parts:
        parts = [text]
    expanded_parts = []
    for part in parts:
        if visual_text_len(part) > target_units * 1.35:
            expanded_parts.extend(wrapped_text_lines_full(part, target_units))
        else:
            expanded_parts.append(part)
    parts = expanded_parts

    chunks = []
    current = ""
    for part in parts:
        if not current:
            current = part
            continue
        if visual_text_len(current + part) <= target_units or len(chunks) >= limit - 1:
            current += part
        else:
            chunks.append(current.strip())
            current = part
    if current.strip():
        chunks.append(current.strip())

    if len(chunks) == 1 and visual_text_len(chunks[0]) > target_units * 1.35:
        lines = wrapped_text_lines_full(chunks[0], target_units)
        chunks = []
        current = ""
        for line in lines:
            if not current:
                current = line
            elif visual_text_len(current + line) <= target_units or len(chunks) >= limit - 1:
                current += line
            else:
                chunks.append(current.strip())
                current = line
        if current.strip():
            chunks.append(current.strip())

    return chunks[:limit]


def fallback_body_chunks(texts, limit=3):
    chunks = []
    for text in texts:
        for chunk in split_long_body_chunks(text, limit=limit - len(chunks)):
            if chunk and chunk not in chunks:
                chunks.append(chunk)
            if len(chunks) >= limit:
                return chunks
    if 0 < len(chunks) < limit:
        combined = "".join(normalize_video_text(text) for text in texts)
        tighter = split_long_body_chunks(combined, limit=limit, target_units=26)
        if len(tighter) > len(chunks):
            chunks = tighter
    return chunks


def compact_title_from_body(text):
    title = normalize_video_text(text)
    chunks = split_long_body_chunks(title, limit=3, target_units=24)
    title = chunks[0] if chunks else title
    if visual_text_len(title) > 30:
        title = wrapped_text_lines_full(title, 24)[0]
    title = title.rstrip("，,。；;：:")
    return title or "核心内容"


def project_display_title(project):
    if not project:
        return "课程内容"
    name = re.sub(r"_ppt\d+_\d{8}$", "", project.name)
    name = name.replace("_", " ").strip()
    match = re.match(r"^\s*(?:\d+(?:\.\d+)?|[一二三四五六七八九十]+[、.．])\s+(.+)$", name)
    if match and any(keyword in name for keyword in ("数据", "进制", "编码", "表示", "计算机")):
        name = match.group(1).strip()
    return name or "课程内容"


def hero_mark_from_title(title):
    text = normalize_video_text(title)
    if any(keyword in text for keyword in ("数据", "进制", "编码", "计算机", "二进制")):
        return "01"
    if any(keyword in text for keyword in ("流程", "转换", "步骤")):
        return "STEP"
    return "KEY"


def build_alternating_cards(rest, limit=3):
    cards = []
    idx = 0
    while idx < len(rest) and len(cards) < limit:
        heading = rest[idx]
        if not is_card_heading(heading):
            idx += 1
            continue

        idx += 1
        body_parts = []
        while idx < len(rest):
            candidate = rest[idx]
            if is_card_heading(candidate) and body_parts:
                break
            if not is_noise_line(candidate):
                body_parts.append(candidate)
            idx += 1
            if len("".join(body_parts)) >= 48:
                break

        title, subtitle = split_heading_subtitle(heading)
        cards.append({"title": title, "subtitle": subtitle, "body": " ".join(body_parts)})

    return cards


def clean_card_data(slide_data, slide_num, project=None):
    raw_lines = [normalize_video_text(line) for line in source_slide_lines(project, slide_num)]
    catalog_items = catalog_items_from_lines(raw_lines)
    if catalog_items:
        return {
            "label": "课程目录",
            "title": "目录",
            "subtitle": "本章将围绕以下内容展开。",
            "cards": [
                {
                    "number": f"{idx + 1:02d}",
                    "title": item,
                    "subtitle": "",
                    "body": "",
                }
                for idx, item in enumerate(catalog_items[:4])
            ],
        }

    lines = [line for line in raw_lines if not is_noise_line(line)]

    if lines:
        if len(lines) == 1 and visual_text_len(lines[0]) > 34:
            title = compact_title_from_body(lines[0])
            rest = [lines[0]]
        else:
            title = lines[0]
            rest = lines[1:]
    else:
        title = normalize_video_text(slide_data.get("title") or "")
        if is_noise_line(title):
            title = ""
        rest = [
            normalize_video_text(item)
            for item in [*slide_data.get("paragraphs", []), *slide_data.get("bullets", [])]
        ]
        rest = [line for line in rest if not is_noise_line(line)]
        if not rest and visual_text_len(title) > 34:
            rest = [title]
            title = compact_title_from_body(title)

    if (
        len(rest) >= 9
        and all(is_card_heading(item) for item in rest[:3])
        and all(is_card_subtitle(item) for item in rest[3:6])
    ):
        card_titles = rest[0:3]
        card_subtitles = rest[3:6]
        card_bodies = rest[6:9]
        cards = []
        for idx in range(3):
            card_title, card_subtitle = split_heading_subtitle(card_titles[idx], card_subtitles[idx])
            cards.append({"title": card_title, "subtitle": card_subtitle, "body": card_bodies[idx]})
    else:
        cards = build_alternating_cards(rest)
        if len(cards) < 3 and len(rest) >= 6 and all(is_card_heading(item) for item in rest[:3]):
            cards = [
                {
                    "title": split_heading_subtitle(rest[idx])[0],
                    "subtitle": split_heading_subtitle(rest[idx])[1],
                    "body": rest[idx + 3] if idx + 3 < len(rest) else "",
                }
                for idx in range(3)
            ]
        if len(cards) < 3:
            body_source = rest or slide_data.get("paragraphs", [])
            if len(body_source) < 3 and body_source:
                body_source = fallback_body_chunks(body_source, limit=3) or body_source
            fallback_titles = [
                line for line in rest
                if is_card_heading(line) and not any(line == body for body in body_source)
            ]
            fallback_titles = (fallback_titles + ["核心要点", "组织方式", "关键价值"])[:3]
            cards = []
            for idx, body in enumerate(body_source[:3]):
                card_title, card_subtitle = split_heading_subtitle(fallback_titles[idx])
                cards.append({"title": card_title, "subtitle": card_subtitle, "body": body})

    if not cards:
        cards = [{"title": title or "核心内容", "subtitle": "", "body": ""}]
    cards = augment_teaching_cards(title, rest, cards, limit=3)

    return {
        "label": "知识要点",
        "title": title,
        "subtitle": "重点看这3个维度。",
        "cards": [
            {
                "number": f"{idx + 1:02d}",
                "title": normalize_video_text(card["title"]),
                "subtitle": normalize_video_text(card.get("subtitle", "")),
                "body": normalize_video_text(card.get("body", "")),
            }
            for idx, card in enumerate(cards[:3])
        ],
    }


def add_filter_drawbox(filters, current, layer_num, box, color, thickness="fill", start=0.0):
    x, y, w, h = box
    filters.append(
        f"[{current}]drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t={thickness}"
        f"{_enable_after(start)}[v{layer_num}]"
    )
    return f"v{layer_num}", layer_num + 1


def add_filter_segment(filters, current, layer_num, x1, y1, x2, y2, color, thickness=5, start=0.0):
    dx = float(x2) - float(x1)
    dy = float(y2) - float(y1)
    length = max(1.0, (dx * dx + dy * dy) ** 0.5)
    angle = math.atan2(dy, dx)
    text = " " * max(1, int(length / 6))
    text_esc = escape_text(text)
    filters.append(
        f"[{current}]drawtext=text='{text_esc}':fontfile={CHINESE_FONT_BOLD}:"
        f"fontsize={int(thickness)}:fontcolor={color}:expansion=none:"
        f"x={int(x1)}:y={int(y1 - thickness / 2)}:box=1:boxcolor={color}:boxborderw=0:"
        f"rotate={angle:.5f}{_enable_after(start)}[v{layer_num}]"
    )
    return f"v{layer_num}", layer_num + 1


def radial_connector_points(cx, cy, radius, box, gap=12):
    x, y, w, h = [float(v) for v in box]
    tx = x + w / 2
    ty = y + h / 2
    dx = tx - float(cx)
    dy = ty - float(cy)
    distance = max(1.0, (dx * dx + dy * dy) ** 0.5)
    ux = dx / distance
    uy = dy / distance
    start_x = float(cx) + ux * (radius + gap)
    start_y = float(cy) + uy * (radius + gap)

    candidates = []
    if abs(ux) > 1e-6:
        edge_x = x if ux > 0 else x + w
        t = (edge_x - float(cx)) / ux
        iy = float(cy) + uy * t
        if y <= iy <= y + h and t > 0:
            candidates.append((t, edge_x, iy))
    if abs(uy) > 1e-6:
        edge_y = y if uy > 0 else y + h
        t = (edge_y - float(cy)) / uy
        ix = float(cx) + ux * t
        if x <= ix <= x + w and t > 0:
            candidates.append((t, ix, edge_y))
    if candidates:
        _, end_x, end_y = min(candidates, key=lambda item: item[0])
    else:
        end_x, end_y = tx, ty
    end_x -= ux * gap
    end_y -= uy * gap
    return int(start_x), int(start_y), int(end_x), int(end_y)


def add_filter_axis_line(filters, current, layer_num, x1, y1, x2, y2, color, thickness=6, start=0.0):
    x1, y1, x2, y2 = [int(round(v)) for v in (x1, y1, x2, y2)]
    thickness = max(1, int(thickness))
    if abs(x2 - x1) >= abs(y2 - y1):
        x = min(x1, x2)
        w = max(thickness, abs(x2 - x1))
        y = int(round((y1 + y2) / 2 - thickness / 2))
        return add_filter_drawbox(filters, current, layer_num, (x, y, w, thickness), color, "fill", start)
    y = min(y1, y2)
    h = max(thickness, abs(y2 - y1))
    x = int(round((x1 + x2) / 2 - thickness / 2))
    return add_filter_drawbox(filters, current, layer_num, (x, y, thickness, h), color, "fill", start)


def add_filter_elbow_connector(filters, current, layer_num, x1, y1, x2, y2, color, thickness=6, start=0.0):
    if abs(x2 - x1) < thickness * 1.5 or abs(y2 - y1) < thickness * 1.5:
        return add_filter_axis_line(filters, current, layer_num, x1, y1, x2, y2, color, thickness, start)
    horizontal_first = abs(x2 - x1) >= abs(y2 - y1)
    bend_x = x2 if horizontal_first else x1
    bend_y = y1 if horizontal_first else y2
    current, layer_num = add_filter_axis_line(filters, current, layer_num, x1, y1, bend_x, bend_y, color, thickness, start)
    current, layer_num = add_filter_axis_line(filters, current, layer_num, bend_x, bend_y, x2, y2, color, thickness, start)
    return current, layer_num


def add_filter_roundrect(filters, current, layer_num, box, color, radius=34, start=0.0):
    x, y, w, h = [int(v) for v in box]
    r = max(0, min(int(radius), w // 2, h // 2))
    if r <= 2:
        return add_filter_drawbox(filters, current, layer_num, box, color, "fill", start)

    if w - 2 * r > 0:
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + r, y, w - 2 * r, h), color, "fill", start)
    if h - 2 * r > 0:
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y + r, w, h - 2 * r), color, "fill", start)

    band = 2
    for top in range(0, r, band):
        bh = min(band, r - top)
        mid = top + bh / 2
        inset = int(max(0, min(r, r - ((r * r - (r - mid) * (r - mid)) ** 0.5))))
        strip_w = max(1, w - 2 * inset)
        current, layer_num = add_filter_drawbox(
            filters, current, layer_num, (x + inset, y + top, strip_w, bh), color, "fill", start
        )
        current, layer_num = add_filter_drawbox(
            filters, current, layer_num, (x + inset, y + h - top - bh, strip_w, bh), color, "fill", start
        )
    return current, layer_num


def add_round_panel(filters, current, layer_num, box, accent="#10b981", fill="white@0.94", start=0.0, radius=34, shadow=True, border=True):
    x, y, w, h = [int(v) for v in box]
    if shadow:
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 12, y + 16, w, h), "black@0.045", radius, start)
    if border:
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, w, h), f"{accent}@0.50", radius, start + 0.02)
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 3, y + 3, w - 6, h - 6), fill, max(2, radius - 3), start + 0.04)
    else:
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, w, h), fill, radius, start + 0.02)
    return current, layer_num


def add_filter_circle(filters, current, layer_num, cx, cy, radius, color, start=0.0):
    r = int(radius)
    band = 2
    for top in range(-r, r, band):
        bh = min(band, r - top)
        mid = top + bh / 2
        half = int(max(1, (r * r - mid * mid) ** 0.5))
        current, layer_num = add_filter_drawbox(
            filters, current, layer_num, (int(cx) - half, int(cy) + top, half * 2, bh), color, "fill", start
        )
    return current, layer_num


def add_soft_circle_token(filters, current, layer_num, cx, cy, radius, accent, label, start=0.0, fill="white@0.92", text_color=None):
    text_color = text_color or accent
    current, layer_num = add_filter_circle(filters, current, layer_num, cx + 8, cy + 10, radius, "black@0.040", start)
    current, layer_num = add_filter_circle(filters, current, layer_num, cx, cy, radius, f"{accent}@0.28", start + 0.02)
    current, layer_num = add_filter_circle(filters, current, layer_num, cx, cy, max(1, radius - 8), fill, start + 0.04)
    label = str(label)
    font_size = 28 if visual_text_len(label) <= 3 else 22
    text_x = int(cx - min(radius - 10, visual_text_len(label) * font_size * 0.30))
    current, layer_num = add_filter_drawtext(
        filters, current, layer_num, label, x=text_x, y=int(cy - font_size * 0.48),
        font_size=font_size, color=text_color, bold=True, start=start + 0.08
    )
    return current, layer_num


def add_filter_drawtext(
    filters,
    current,
    layer_num,
    text,
    *,
    x,
    y,
    font_size,
    color,
    bold=False,
    start=0.0,
):
    if contains_display_formula(text):
        return add_filter_rich_text(
            filters,
            current,
            layer_num,
            text,
            x=x,
            y=y,
            font_size=font_size,
            color=color,
            bold=bold,
            start=start,
        )
    fontfile = CHINESE_FONT_BOLD if bold else CHINESE_FONT
    text_esc = escape_text(text)
    filters.append(
        f"[{current}]drawtext=text='{text_esc}':fontfile={fontfile}:"
        f"fontsize={font_size}:fontcolor={color}:expansion=none:x={x}:y={y}"
        f"{_enable_after(start)}[v{layer_num}]"
    )
    return f"v{layer_num}", layer_num + 1


def add_filter_radical_token(
    filters,
    current,
    layer_num,
    body,
    *,
    x,
    y,
    font_size,
    color,
    bold=False,
    start=0.0,
):
    body = str(body or " ").strip() or " "
    root_font = int(font_size * 1.22)
    body_font = int(font_size)
    root_w = int(root_font * 0.52)
    body_x = int(x + root_w + font_size * 0.02)
    root_y = int(y - font_size * 0.15)
    body_y = int(y)
    line_y = int(y + font_size * 0.04)
    body_w = int(max(font_size * 0.45, estimate_text_px(body, body_font, True)))
    line_h = max(2, int(font_size * 0.055))
    root_esc = escape_text("√")
    body_esc = escape_text(body)
    filters.append(
        f"[{current}]drawtext=text='{root_esc}':fontfile={MATH_FONT}:"
        f"fontsize={root_font}:fontcolor={color}:expansion=none:x={int(x)}:y={root_y}"
        f"{_enable_after(start)}[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1
    current, layer_num = add_filter_drawbox(
        filters, current, layer_num,
        (body_x - max(1, int(font_size * 0.04)), line_y, body_w + max(3, int(font_size * 0.12)), line_h),
        color, "fill", start
    )
    filters.append(
        f"[{current}]drawtext=text='{body_esc}':fontfile={MATH_FONT}:"
        f"fontsize={body_font}:fontcolor={color}:expansion=none:x={body_x}:y={body_y}"
        f"{_enable_after(start)}[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1
    width = max(radical_token_width(body, font_size), body_x - int(x) + body_w)
    return current, layer_num, int(width)


def add_filter_rich_text(
    filters,
    current,
    layer_num,
    text,
    *,
    x,
    y,
    font_size,
    color,
    bold=False,
    start=0.0,
):
    cursor_x = int(x)
    for segment, math_like in split_rich_text_segments(text):
        segment = str(segment)
        if not segment:
            continue
        if math_like and contains_display_formula(segment):
            leading_spaces = len(segment) - len(segment.lstrip())
            trailing_spaces = len(segment) - len(segment.rstrip())
            if leading_spaces:
                cursor_x += int(font_size * 0.30 * leading_spaces)
            formula_text = segment.strip()
            if formula_text:
                token_w = max(
                    estimate_text_px(formula_text, font_size, True),
                    int(font_size * 1.30),
                )
                current, layer_num = add_formula_overlay(
                    filters, current, layer_num, formula_text,
                    x=cursor_x, y=y - int(font_size * 0.30),
                    width=token_w + int(font_size * 0.45),
                    height=max(36, int(font_size * 1.70)),
                    font_size=font_size,
                    color=color,
                    bold=bold,
                    start=start,
                )
                cursor_x += token_w + int(font_size * 0.16)
            if trailing_spaces:
                cursor_x += int(font_size * 0.30 * trailing_spaces)
            continue
        if math_like and contains_radical_notation(segment):
            for token_type, token_value in split_radical_tokens(segment):
                if token_type == "radical":
                    formula_text = f"√{token_value}"
                    token_w = max(
                        radical_token_width(token_value, font_size),
                        int(estimate_text_px(formula_text, font_size, True) * 1.25),
                    )
                    current, layer_num = add_formula_overlay(
                        filters, current, layer_num, formula_text,
                        x=cursor_x, y=y - int(font_size * 0.22),
                        width=token_w + int(font_size * 0.36),
                        height=max(36, int(font_size * 1.55)),
                        font_size=font_size,
                        color=color,
                        bold=bold,
                        start=start,
                    )
                    cursor_x += token_w + int(font_size * 0.12)
                    continue
                if not token_value:
                    continue
                text_esc = escape_text(token_value)
                filters.append(
                    f"[{current}]drawtext=text='{text_esc}':fontfile={MATH_FONT}:"
                    f"fontsize={font_size}:fontcolor={color}:expansion=none:x={cursor_x}:y={y}"
                    f"{_enable_after(start)}[v{layer_num}]"
                )
                current = f"v{layer_num}"
                layer_num += 1
                cursor_x += estimate_text_px(token_value, font_size, True)
            continue
        if math_like:
            fontfile = MATH_FONT
        else:
            fontfile = CHINESE_FONT_BOLD if bold else CHINESE_FONT
        text_esc = escape_text(segment)
        filters.append(
            f"[{current}]drawtext=text='{text_esc}':fontfile={fontfile}:"
            f"fontsize={font_size}:fontcolor={color}:expansion=none:x={cursor_x}:y={y}"
            f"{_enable_after(start)}[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1
        cursor_x += estimate_text_px(segment, font_size, math_like)
    return current, layer_num


def slide_visual_asset(project, slide_num):
    """Return the selected visual asset for a slide, if the image module produced one."""
    if not project:
        return None
    visual_dir = project / "images" / "visual_assets"
    for suffix in ("visual", "existing", "generated"):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            path = visual_dir / f"slide_{slide_num:02d}_{suffix}{ext}"
            if path.exists():
                return path
    return None


def should_use_visual_asset(slide_data, slide_num, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    layout = slide_data.get("layout", {}) if isinstance(slide_data, dict) else {}
    page_type = layout.get("page_type", "")
    if slide_num != 1 and (page_type == "section" or len(rest) <= 1):
        return False
    if title == "目录":
        return False
    return True


def slide_visual_asset_for_layout(project, slide_num, slide_data=None):
    if slide_data is not None and not should_use_visual_asset(slide_data, slide_num, project):
        return None
    return slide_visual_asset(project, slide_num)


def source_slide_images(project, slide_num):
    if not project:
        return []
    sources_dir = project / "sources"
    if not sources_dir.exists():
        return []
    candidates = []
    for folder in sources_dir.iterdir():
        if not folder.is_dir() or not folder.name.endswith("_files"):
            continue
        for path in folder.glob(f"slide_{slide_num:02d}_image_*"):
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                candidates.append(path)
    return sorted(candidates)


@lru_cache(maxsize=512)
def image_dimensions(path_str):
    try:
        from PIL import Image
        with Image.open(path_str) as image:
            return image.size
    except Exception:
        pass
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0",
                str(path_str),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
        )
        if result.returncode == 0:
            parts = [int(part) for part in result.stdout.strip().split(",")[:2]]
            if len(parts) == 2 and parts[0] > 0 and parts[1] > 0:
                return parts[0], parts[1]
    except Exception:
        pass
    return 0, 0


def source_slide_art(project, slide_num):
    best = None
    best_score = 0
    fallback = None
    fallback_score = 0
    for path in source_slide_images(project, slide_num):
        width, height = image_dimensions(str(path))
        if width <= 0 or height <= 0:
            continue
        if width < 180 or height < 120:
            continue
        if max(width, height) < 420 and path.stat().st_size < 18 * 1024:
            continue
        if width == height and slide_num in {1, 3, 8, 13, 18}:
            continue
        if height > width * 1.35 and path.stat().st_size < 12 * 1024:
            continue
        area = width * height
        ratio = width / max(1, height)
        ratio_score = max(0.15, 1.0 - min(abs(ratio - 16 / 9), 1.2))
        score = area * ratio_score
        if score > fallback_score:
            fallback = path
            fallback_score = score
        if (width >= 900 and height >= 260) or (width >= 650 and height >= 360):
            score *= 1.4
        else:
            score *= 0.25
        if score > best_score:
            best = path
            best_score = score
    return best or fallback


def micro_course_should_use_image(title="", rest=None, slide_num=0, path=None):
    return False


def micro_course_visual_asset(project, slide_num, slide_data=None, title=None, rest=None):
    return None


def slide_art_asset(project, slide_num):
    if not project:
        return None
    rendered = project / "slides" / f"slide_{slide_num:02d}.png"
    if rendered.exists():
        return rendered
    return None


def slide_theme_asset(project, slide_num):
    """Return a slide-level image usable for theme sampling, not full-slide reuse."""
    rendered = slide_art_asset(project, slide_num)
    if rendered:
        return rendered
    return slide_visual_asset(project, slide_num) or source_slide_art(project, slide_num)


@lru_cache(maxsize=512)
def image_theme(path_str):
    try:
        from PIL import Image
        with Image.open(path_str) as image:
            image = image.convert("RGB").resize((80, 45))
            pixels = list(image.getdata())
    except Exception:
        return {"accent": "#2375ff", "background": "#f7f8fa", "text": "#101828"}

    buckets = {}
    light = []
    dark = []
    for r, g, b in pixels:
        lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
        if lum > 0.92 or lum < 0.08:
            if lum > 0.92:
                light.append((r, g, b))
            else:
                dark.append((r, g, b))
            continue
        sat = (max(r, g, b) - min(r, g, b)) / 255
        if sat < 0.10:
            continue
        key = (round(r / 32) * 32, round(g / 32) * 32, round(b / 32) * 32)
        buckets[key] = buckets.get(key, 0) + 1 + sat

    accent = max(buckets.items(), key=lambda item: item[1])[0] if buckets else (35, 117, 255)
    if _relative_luminance(_rgb_to_hex(accent)) > 0.72:
        accent = tuple(int(v * 0.72) for v in accent)
    bg = tuple(sum(channel) // len(light) for channel in zip(*light)) if light else (247, 248, 250)
    text = "#101828" if not dark else _rgb_to_hex(tuple(sum(channel) // len(dark) for channel in zip(*dark)))
    return {"accent": _rgb_to_hex(accent), "background": _rgb_to_hex(bg), "text": text}


def project_theme(project):
    if not project:
        return {"accent": "#2375ff", "background": "#f7f8fa", "text": "#101828"}
    for slide_num in range(1, 8):
        art = slide_theme_asset(project, slide_num)
        if art:
            return image_theme(str(art))
    return {"accent": "#2375ff", "background": "#f7f8fa", "text": "#101828"}


def has_slide_visual(project, slide_num, slide_data=None):
    return slide_visual_asset_for_layout(project, slide_num, slide_data) is not None


def add_filter_visual_cover(
    filters,
    current,
    layer_num,
    *,
    box,
    opacity=1.0,
    start=0.0,
):
    """Overlay the slide visual input into a fixed box using cover-fit cropping."""
    x, y, w, h = box
    image_label = f"img{layer_num}"
    filters.append(
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},format=rgba,colorchannelmixer=aa={opacity:.3f}[{image_label}]"
    )
    filters.append(
        f"[{current}][{image_label}]overlay=x={x}:y={y}:shortest=1"
        f"{_enable_after(start)}[v{layer_num}]"
    )
    return f"v{layer_num}", layer_num + 1


def add_filter_visual_contain(
    filters,
    current,
    layer_num,
    *,
    box,
    opacity=1.0,
    start=0.0,
):
    """Overlay the slide visual input into a fixed box without cropping."""
    x, y, w, h = box
    image_label = f"img{layer_num}"
    filters.append(
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white@0.0,"
        f"format=rgba,colorchannelmixer=aa={opacity:.3f}[{image_label}]"
    )
    filters.append(
        f"[{current}][{image_label}]overlay=x={x}:y={y}:shortest=1"
        f"{_enable_after(start)}[v{layer_num}]"
    )
    return f"v{layer_num}", layer_num + 1


def add_filter_visual_backdrop(filters, current, layer_num, project, slide_num, opacity=0.20, slide_data=None):
    if not has_slide_visual(project, slide_num, slide_data):
        return current, layer_num
    current, layer_num = add_filter_visual_cover(
        filters,
        current,
        layer_num,
        box=(0, 0, VIDEO_W, VIDEO_H),
        opacity=opacity,
        start=0.0,
    )
    current, layer_num = add_filter_drawbox(
        filters,
        current,
        layer_num,
        (0, 0, VIDEO_W, VIDEO_H),
        "white@0.54",
        "fill",
        0.0,
    )
    return current, layer_num


SOFT_WRAP_CHARS = set(" \t,，、;；:：.。!！?？)）]】”\"'")
TRAILING_WRAP_CHARS = " \t,，、;；:：.。"


def split_soft_wrap_candidate(candidate):
    for idx in range(len(candidate) - 2, 0, -1):
        if candidate[idx].isspace() or candidate[idx] in SOFT_WRAP_CHARS:
            left = candidate[:idx + 1].rstrip()
            right = candidate[idx + 1:].lstrip()
            if left and right:
                return left, right
    return None, None


def mark_truncated_line(line):
    clean = str(line or "").rstrip(TRAILING_WRAP_CHARS)
    return f"{clean}..." if clean else "..."


def wrapped_text_lines(text, max_units, max_lines):
    lines = []
    current = ""
    current_units = 0.0
    for ch in str(text or ""):
        unit = 1.0 if "\u4e00" <= ch <= "\u9fff" else 0.55
        if current and current_units + unit > max_units:
            soft_left, soft_right = split_soft_wrap_candidate(current + ch)
            if soft_left and visual_text_len(soft_left) <= max_units + 0.55:
                lines.append(soft_left)
                current = soft_right
                current_units = visual_text_len(current)
            else:
                lines.append(current)
                current = ch
                current_units = unit
            if len(lines) >= max_lines:
                lines[-1] = mark_truncated_line(lines[-1])
                break
        else:
            current += ch
            current_units += unit
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


def wrapped_text_lines_full(text, max_units):
    lines = []
    current = ""
    current_units = 0.0
    for ch in str(text or ""):
        unit = 1.0 if "\u4e00" <= ch <= "\u9fff" else 0.55
        if current and current_units + unit > max_units:
            soft_left, soft_right = split_soft_wrap_candidate(current + ch)
            if soft_left and visual_text_len(soft_left) <= max_units + 0.55:
                lines.append(soft_left)
                current = soft_right
                current_units = visual_text_len(current)
            else:
                lines.append(current)
                current = ch
                current_units = unit
        else:
            current += ch
            current_units += unit
    if current:
        lines.append(current)
    return lines


def text_units_for_width(width, font_size, safety=0.88):
    return max(4, int(width / max(1, font_size) * safety + 0.5))


def fit_wrapped_text(text, width, height, max_font, min_font, safety=0.88):
    min_font = max(16, min_font)
    for font_size in range(max_font, min_font - 1, -2):
        max_units = text_units_for_width(width, font_size, safety)
        lines = wrapped_text_lines_full(text, max_units)
        line_height = max(font_size + 5, int(font_size * 1.18))
        if len(lines) * line_height <= height:
            return lines, font_size, line_height

    for font_size in range(min_font - 2, 15, -2):
        max_units = text_units_for_width(width, font_size, safety)
        lines = wrapped_text_lines_full(text, max_units)
        line_height = max(font_size + 4, int(font_size * 1.15))
        if len(lines) * line_height <= height:
            return lines, font_size, line_height

    font_size = 16
    line_height = 21
    max_units = text_units_for_width(width, font_size, safety)
    all_lines = wrapped_text_lines_full(text, max_units)
    max_lines = max(1, int(height // line_height))
    lines = all_lines[:max_lines]
    if lines and len(all_lines) > max_lines:
        lines[-1] = ellipsize_visual_text(lines[-1], max_units)
    return lines, font_size, line_height


def add_fitting_wrapped_text_in_box(
    filters,
    current,
    layer_num,
    text,
    *,
    x,
    y,
    width,
    height,
    max_font,
    min_font,
    color,
    bold=False,
    start=0.0,
    safety=0.88,
):
    lines, font_size, line_height = fit_wrapped_text(text, width, height, max_font, min_font, safety)
    for idx, line in enumerate(lines):
        current, layer_num = add_filter_drawtext(
            filters,
            current,
            layer_num,
            line,
            x=x,
            y=y + idx * line_height,
            font_size=font_size,
            color=color,
            bold=bold,
            start=start,
        )
    return current, layer_num, len(lines), font_size, line_height


def add_wrapped_text_in_box(
    filters,
    current,
    layer_num,
    text,
    *,
    x,
    y,
    width,
    max_lines,
    line_height,
    font_size,
    color,
    bold=False,
    start=0.0,
    safety=0.88,
):
    max_units = text_units_for_width(width, font_size, safety)
    lines = wrapped_text_lines(text, max_units, max_lines)
    for idx, line in enumerate(lines):
        current, layer_num = add_filter_drawtext(
            filters,
            current,
            layer_num,
            line,
            x=x,
            y=y + idx * line_height,
            font_size=font_size,
            color=color,
            bold=bold,
            start=start,
        )
    return current, layer_num, len(lines)


def add_wrapped_text(
    filters,
    current,
    layer_num,
    text,
    *,
    x,
    y,
    max_chars,
    max_lines,
    line_height,
    font_size,
    color,
    bold=False,
    start=0.0,
):
    lines = wrapped_text_lines(text, max_chars, max_lines)
    for idx, line in enumerate(lines):
        current, layer_num = add_filter_drawtext(
            filters,
            current,
            layer_num,
            line,
            x=x,
            y=y + idx * line_height,
            font_size=font_size,
            color=color,
            bold=bold,
            start=start,
        )
    return current, layer_num


def layout_clean_cards(slide_data, slide_num, duration, project=None):
    """Recompose slide text into a clean white three-card video layout."""
    data = clean_card_data(slide_data, slide_num, project)
    is_catalog_layout = data.get("label") == "课程目录"
    theme = project_theme(project)
    accent = _theme_accent(theme)
    bg = _adjust_color(theme.get("background", "#fbfcfb"), 1.03)
    text_color = theme.get("text", "#070b1d")
    filters = [f"color=c={bg}:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_filter_visual_backdrop(filters, current, layer_num, project, slide_num, opacity=0.22, slide_data=slide_data)

    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, 10), accent, "fill")
    current, layer_num = add_filter_drawtext(
        filters,
        current,
        layer_num,
        data["label"],
        x=120,
        y=88,
        font_size=28,
        color=accent,
        bold=True,
    )
    current, layer_num = add_wrapped_text(
        filters,
        current,
        layer_num,
        data["title"],
        x=120,
        y=138,
        max_chars=22,
        max_lines=2,
        line_height=72,
        font_size=66,
        color=text_color,
        bold=True,
    )
    current, layer_num = add_filter_drawtext(
        filters,
        current,
        layer_num,
        data["subtitle"],
        x=120,
        y=260,
        font_size=30,
        color="#626b7a",
    )

    card_y = 430
    if is_catalog_layout:
        card_count = len(data["cards"])
        if card_count >= 4:
            card_x0, card_w, card_h, gap = 100, 405, 290, 35
        else:
            card_x0, card_w, card_h, gap = 110, 540, 290, 35
        starts = [0.35 + idx * 0.5 for idx in range(card_count)]
    else:
        card_x0, card_w, card_h, gap = 120, 500, 270, 55
        starts = [0.35, max(0.75, duration * 0.34), max(1.1, duration * 0.62)]

    for idx, card in enumerate(data["cards"]):
        x = card_x0 + idx * (card_w + gap)
        start = starts[idx] if idx < len(starts) else 0.35 + idx * 0.6
        current, layer_num = add_filter_drawbox(
            filters, current, layer_num, (x + 8, card_y + 8, card_w, card_h), "black@0.025", "fill", start
        )
        current, layer_num = add_filter_drawbox(
            filters, current, layer_num, (x, card_y, card_w, card_h), "white@0.98", "fill", start
        )
        current, layer_num = add_filter_drawbox(
            filters, current, layer_num, (x, card_y, card_w, 2), "#edf2f6@0.85", "fill", start
        )
        current, layer_num = add_filter_drawtext(
            filters,
            current,
            layer_num,
            card["number"],
            x=x + 28,
            y=card_y + 28,
            font_size=28,
        color=accent,
            bold=True,
            start=start,
        )
        title_y = card_y + 86
        if is_catalog_layout:
            current, layer_num, title_line_count, _, title_line_height = add_fitting_wrapped_text_in_box(
                filters,
                current,
                layer_num,
                card["title"],
                x=x + 28,
                y=title_y,
                width=card_w - 56,
                height=132,
                max_font=38,
                min_font=28,
                color=text_color,
                bold=True,
                start=start,
            )
        else:
            title_lines = wrapped_text_lines(card["title"], 15, 2)
            title_font = 36 if len(title_lines) > 1 or visual_text_len(card["title"]) > 14 else 40
            for line_idx, line in enumerate(title_lines):
                current, layer_num = add_filter_drawtext(
                    filters,
                    current,
                    layer_num,
                    line,
                    x=x + 28,
                    y=title_y + line_idx * 40,
                    font_size=title_font,
                    color=text_color,
                    bold=True,
                    start=start,
                )
            title_line_count = len(title_lines)
            title_line_height = 40
        subtitle_y = title_y + max(1, title_line_count) * title_line_height + 12
        if card["subtitle"]:
            current, layer_num = add_wrapped_text(
                filters,
                current,
                layer_num,
                card["subtitle"],
                x=x + 28,
                y=subtitle_y,
                max_chars=17,
                max_lines=1,
                line_height=34,
                font_size=24,
                color=_adjust_color(accent, 0.85),
                bold=True,
                start=start,
            )
            body_y = subtitle_y + 40
        else:
            body_y = subtitle_y + 4
        current, layer_num = add_wrapped_text(
            filters,
            current,
            layer_num,
            card["body"],
            x=x + 28,
            y=body_y,
            max_chars=18,
            max_lines=4,
            line_height=30,
            font_size=23,
            color="#5d6878",
            start=start,
        )

    return filters, current, layer_num


def slide_context(slide_data, slide_num, project=None):
    raw_lines = [normalize_video_text(line) for line in source_slide_lines(project, slide_num)]
    catalog_items = catalog_items_from_lines(raw_lines)
    if catalog_items:
        return "目录", catalog_items

    lines = [line for line in raw_lines if not is_noise_line(line)]
    if lines:
        if len(lines) == 1 and visual_text_len(lines[0]) > 34:
            return compact_title_from_body(lines[0]), [lines[0]]
        return lines[0], lines[1:]

    title = normalize_video_text(slide_data.get("title") or "")
    if is_noise_line(title):
        title = ""
    rest = [
        normalize_video_text(item)
        for item in [*slide_data.get("paragraphs", []), *slide_data.get("bullets", [])]
    ]
    rest = [line for line in rest if not is_noise_line(line)]
    if not rest and visual_text_len(title) > 34:
        return compact_title_from_body(title), [title]
    if not title and not rest and slide_num == 1:
        return project_display_title(project), []
    return title, rest


def has_meaningful_slide_text(slide_data, slide_num, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    return bool(title or rest)


def should_skip_slide(slide_data, slide_num, project=None, audio_path=None):
    if audio_path and audio_path.exists():
        return False
    if has_meaningful_slide_text(slide_data, slide_num, project):
        return False
    if source_slide_has_image(project, slide_num):
        return False
    return True


def adaptive_kind_from_component(recommendation):
    selected = selected_visual_component(recommendation)
    if selected in {"cover_hero", "section_title", "statement_focus", "image_hero", "photo_story", "product_showcase", "image_pan_zoom", "magazine_spread"}:
        return "hero"
    if selected in {"problem_stack"}:
        return "problem"
    if selected in {"capability_matrix", "two_column_compare", "before_after", "misconception_compare"}:
        return "matrix"
    if selected in {"revenue_model", "financial_snapshot"}:
        return "business"
    if selected in {"solution_flow", "process_flow", "timeline", "roadmap_timeline", "lifecycle_loop", "flywheel", "formula_walkthrough", "checkpoint_ladder"}:
        return "process"
    if selected in {"market_dashboard", "metric_dashboard", "kpi_cards", "chart_focus"}:
        return "metrics"
    if selected in {"team_roster", "role_grid", "org_chart"}:
        return "team"
    if selected in {"blackboard_derivation", "radial_concept_map", "rounded_step_cards", "application_storyboard"}:
        return "cards"
    return None


def selected_visual_component(recommendation):
    component_id = recommended_component(recommendation)
    strategy = recommendation.get("render_strategy", {}) if recommendation else {}
    return strategy.get("visual_effect") or strategy.get("effect_component") or component_id or ""


def adaptive_layout_kind(slide_data, slide_num, project=None, recommendation=None):
    title, rest = slide_context(slide_data, slide_num, project)
    if title == "目录" and len(rest) >= 2:
        return "cards"

    text = title + " " + " ".join(rest)
    layout = slide_data.get("layout", {})
    page_type = layout.get("page_type", "")

    if is_formula_rule_text(text) and any(keyword in text for keyword in ("公式", "法则", "运算", "根号", "平方根", "化简", "例")):
        return "process"
    if any(keyword in text for keyword in ("进制", "编码", "二进制", "十进制")) and any(keyword in text for keyword in ("不同", "对比", "特点", "比较")):
        return "matrix"
    if any(keyword in text for keyword in ("转换", "步骤", "从小数点", "补零", "分组")):
        return "process"

    component_kind = adaptive_kind_from_component(recommendation)
    if component_kind:
        return component_kind

    if slide_num == 1 or len(rest) <= 3:
        return "hero"
    if any(keyword in title for keyword in ("挑战", "痛点", "问题")):
        return "problem"
    if any(keyword in title for keyword in ("盈利", "收入", "商业模式", "营收")):
        return "business"
    if any(keyword in title for keyword in ("优势", "亮点", "壁垒", "竞争力", "与众不同")):
        return "matrix"
    if any(keyword in title for keyword in ("解决方案", "流程", "生态", "发展规划", "规划")):
        return "process"
    if any(keyword in title for keyword in ("市场", "数据", "测算", "规模", "潜力")) or page_type == "data":
        return "metrics"
    if any(keyword in title for keyword in ("团队", "成员", "组织")):
        return "team"
    if any(mark in text for mark in ("第1年", "第2", "第3", "第4", "步骤")):
        return "process"
    return "cards"


def add_adaptive_header(filters, current, layer_num, label, title, subtitle=""):
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, 10), "#e5e7eb", "fill")
    current, layer_num = add_filter_drawtext(
        filters, current, layer_num, label, x=120, y=86, font_size=28, color="#f4a000", bold=True
    )
    title_units = visual_text_len(title)
    if title_units <= 18:
        title_font = 60
    elif title_units <= 34:
        title_font = 52
    else:
        title_font = 46
    title_line_height = max(52, title_font + 8)
    current, layer_num, title_line_count = add_wrapped_text_in_box(
        filters,
        current,
        layer_num,
        title,
        x=120,
        y=136,
        width=1600,
        max_lines=2,
        line_height=title_line_height,
        font_size=title_font,
        color="#070b1d",
        bold=True,
    )
    if subtitle:
        current, layer_num, _ = add_wrapped_text_in_box(
            filters,
            current,
            layer_num,
            subtitle,
            x=120,
            y=136 + title_line_count * title_line_height + 22,
            width=1460,
            max_lines=2,
            line_height=36,
            font_size=30,
            color="#626b7a",
        )
    return current, layer_num


def add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data=None, opacity=0.14):
    if has_slide_visual(project, slide_num, slide_data):
        current, layer_num = add_filter_visual_cover(
            filters, current, layer_num, box=(0, 0, VIDEO_W, VIDEO_H), opacity=opacity, start=0.0
        )
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, VIDEO_H), "white@0.78", "fill", 0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, 10), "#62ad6a", "fill", 0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 908, VIDEO_W, 2), "#d9e7dc", "fill", 0.0)
    for idx, x in enumerate((118, 420, 722, 1024, 1326, 1628)):
        current, layer_num = add_filter_drawbox(
            filters, current, layer_num, (x, 915, 96, 4), "#dfe9e2", "fill", 0.15 + idx * 0.05
        )
    return current, layer_num


def add_premium_header(filters, current, layer_num, label, title, subtitle="", *, title_width=1260):
    pill_w = int(max(210, min(360, visual_text_len(label) * 18 + 62)))
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (120, 74, pill_w, 48), "#0f172a", "fill", 0.05)
    current, layer_num = add_filter_drawtext(
        filters, current, layer_num, label, x=146, y=86, font_size=24, color="white", bold=True, start=0.10
    )
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (120 + pill_w + 18, 98, 132, 4), "#62ad6a", "fill", 0.18)
    current, layer_num, title_lines, _, title_line_h = add_fitting_wrapped_text_in_box(
        filters, current, layer_num, title, x=120, y=145, width=title_width, height=145,
        max_font=60, min_font=42, color="#07111f", bold=True, start=0.22, safety=0.92
    )
    if subtitle:
        current, layer_num, _ = add_wrapped_text_in_box(
            filters, current, layer_num, subtitle, x=120, y=145 + title_lines * title_line_h + 12,
            width=min(1300, title_width + 100), max_lines=1, line_height=32, font_size=26,
            color="#506070", start=0.35
        )
    return current, layer_num


def add_premium_panel(filters, current, layer_num, box, accent="#62ad6a", start=0.25, fill="white@0.97"):
    x, y, w, h = box
    current, layer_num = add_round_panel(filters, current, layer_num, (x, y, w, h), accent, fill, start, radius=28, shadow=True, border=False)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 24, y + 20, 92, 8), f"{accent}@0.92", 4, start + 0.05)
    return current, layer_num


def add_visual_panel(filters, current, layer_num, project, slide_num, slide_data, box, label="", start=0.25):
    x, y, w, h = box
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 12, y + 14, w, h), "black@0.05", "fill", start)
    if has_slide_visual(project, slide_num, slide_data):
        current, layer_num = add_filter_visual_cover(filters, current, layer_num, box=box, opacity=0.98, start=start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, box, "black@0.08", 4, start + 0.05)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y + h - 88, w, 88), "black@0.38", "fill", start + 0.10)
        if label:
            current, layer_num = add_filter_drawtext(
                filters, current, layer_num, label, x=x + 30, y=y + h - 58,
                font_size=25, color="white", bold=True, start=start + 0.15
            )
    else:
        current, layer_num = add_filter_drawbox(filters, current, layer_num, box, "#eef8f1", "fill", start)
        for idx in range(5):
            bar_w = int(w * (0.68 - idx * 0.07))
            current, layer_num = add_filter_drawbox(
                filters, current, layer_num, (x + 42, y + 70 + idx * 62, bar_w, 18),
                "#62ad6a@0.20", "fill", start + idx * 0.08
            )
        current, layer_num = add_filter_drawtext(
            filters, current, layer_num, label or "VISUAL SYSTEM", x=x + 42, y=y + h - 72,
            font_size=28, color="#2f8b4b", bold=True, start=start + 0.2
        )
    return current, layer_num


def add_metric_bar(filters, current, layer_num, x, y, w, label, value, accent, start=0.35, ratio=0.72):
    ratio = _clamp(ratio, 0.12, 1.0)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, label, x=x, y=y, font_size=22, color="#667085", start=start)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y + 38, w, 10), "#e4ece7", "fill", start + 0.05)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y + 38, int(w * ratio), 10), f"{accent}@0.92", "fill", start + 0.16)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, value, x=x + w + 24, y=y + 24, font_size=28, color=accent, bold=True, start=start + 0.12)
    return current, layer_num


def add_metric_capsule_bar(filters, current, layer_num, x, y, w, label, value, accent, start=0.35, ratio=0.72):
    ratio = _clamp(ratio, 0.12, 1.0)
    row_h = 52
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, w, row_h), "white@0.74", 26, start)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 2, y + 2, w - 4, row_h - 4), f"{accent}@0.045", 24, start + 0.02)
    current, layer_num = add_bounded_text(
        filters, current, layer_num, label, x=x + 24, y=y + 13,
        width=150, height=26, max_font=20, min_font=16, color="#465160", bold=True, start=start + 0.05
    )
    bar_x = x + 190
    bar_w = max(120, w - 320)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (bar_x, y + 22, bar_w, 10), "#dcebe2", 5, start + 0.07)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (bar_x, y + 22, max(10, int(bar_w * ratio)), 10), f"{accent}@0.95", 5, start + 0.15)
    chip_w = int(max(58, min(96, visual_text_len(value) * 18 + 36)))
    chip_x = x + w - chip_w - 18
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (chip_x, y + 8, chip_w, 36), f"{accent}@0.16", 18, start + 0.11)
    text_x = int(chip_x + max(12, (chip_w - visual_text_len(value) * 16) / 2))
    current, layer_num = add_filter_drawtext(
        filters, current, layer_num, value, x=text_x, y=y + 15,
        font_size=20, color=accent, bold=True, start=start + 0.16
    )
    return current, layer_num


def concise_body(text, limit=58):
    chunks = split_long_body_chunks(text, limit=1, target_units=limit)
    return chunks[0] if chunks else normalize_video_text(text)


def concise_card_body(text, limit=38):
    return compact_sentence_without_ellipsis(text, limit)


def compact_sentence_without_ellipsis(text, limit=42):
    """Return a short complete-looking sentence without adding ellipsis."""
    text = normalize_video_text(text)
    if not text:
        return ""
    text = text.replace("……", "。").replace("...", "。").replace("…", "")
    if visual_text_len(text) <= limit:
        return text

    clauses = [part.strip() for part in re.split(r"[。！？；;]", text) if part.strip()]
    if not clauses:
        clauses = [text]

    picked = []
    for clause in clauses:
        clause = clause.strip(" ，,。；;：:")
        candidate = "；".join([*picked, clause])
        if visual_text_len(candidate) <= limit:
            picked.append(clause)
            continue
        if picked:
            break

        fragments = [part.strip() for part in re.split(r"[，,、]", clause) if part.strip()]
        subparts = []
        for fragment in fragments:
            candidate = "，".join([*subparts, fragment])
            if visual_text_len(candidate) <= limit:
                subparts.append(fragment)
            elif subparts:
                break
            else:
                first_line = wrapped_text_lines_full(fragment, limit)[0]
                subparts.append(first_line.strip(" ，,。；;：:"))
                break
        if subparts:
            picked.append("，".join(subparts))
        break

    summary = "；".join(picked).strip(" ，,。；;：:")
    return summary or text.strip(" ，,。；;：:")


def metric_token(text):
    text = normalize_video_text(text)
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:%|万吨|吨|亿\+?|亿|万|年|元)?)", text)
    return match.group(1).replace(" ", "") if match else ""


def start_step(duration, idx, base=0.35, ratio=0.075, minimum=0.42):
    cadence = min(1.05, max(minimum, duration * ratio))
    return base + idx * cadence


def add_micro_label(filters, current, layer_num, text, x, y, accent, start=0.0):
    w = int(max(130, min(330, visual_text_len(text) * 15 + 54)))
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, w, 42), f"{accent}@0.14", 21, start)
    current, layer_num = add_filter_circle(filters, current, layer_num, x + 22, y + 21, 5, accent, start + 0.02)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, text, x=x + 40, y=y + 10, font_size=20, color=accent, bold=True, start=start + 0.04)
    return current, layer_num


def add_glass_box(filters, current, layer_num, box, accent="#10b981", fill="white@0.88", start=0.0, border=True):
    x, y, w, h = box
    current, layer_num = add_round_panel(
        filters, current, layer_num, box, accent, fill, start,
        radius=34 if min(w, h) > 120 else 26, shadow=True, border=border
    )
    return current, layer_num


def add_large_title(filters, current, layer_num, title, *, x, y, width, height, color="#f8fafc", start=0.2, max_font=72, min_font=42):
    current, layer_num, _, _, _ = add_fitting_wrapped_text_in_box(
        filters, current, layer_num, title, x=x, y=y, width=width, height=height,
        max_font=max_font, min_font=min_font, color=color, bold=True, start=start, safety=0.92
    )
    return current, layer_num


def add_body_line(filters, current, layer_num, text, *, x, y, width, max_lines=2, font_size=24, color="#475569", start=0.3):
    return add_bounded_text(
        filters, current, layer_num, text, x=x, y=y, width=width,
        height=max_lines * max(font_size + 8, 30), max_font=font_size,
        min_font=max(16, font_size - 6), color=color, bold=False, start=start
    )


def ellipsize_visual_text(text, max_units):
    text = normalize_video_text(text)
    if visual_text_len(text) <= max_units:
        return text
    suffix = "..."
    while text and visual_text_len(text + suffix) > max_units:
        text = text[:-1]
    return (text + suffix) if text else suffix


def fit_bounded_text(text, width, height, max_font, min_font, safety=0.86):
    text = normalize_video_text(text)
    if not text:
        return [], max_font, max_font + 6
    min_font = max(14, min_font)
    for font_size in range(max_font, min_font - 1, -2):
        max_units = text_units_for_width(width, font_size, safety)
        lines = wrapped_text_lines_full(text, max_units)
        line_height = max(font_size + 5, int(font_size * 1.16))
        max_lines = max(1, int(height // line_height))
        if len(lines) <= max_lines:
            return lines, font_size, line_height
    font_size = min_font
    max_units = text_units_for_width(width, font_size, safety)
    lines = wrapped_text_lines_full(text, max_units)
    line_height = max(font_size + 4, int(font_size * 1.14))
    max_lines = max(1, int(height // line_height))
    lines = lines[:max_lines]
    if lines and len(wrapped_text_lines_full(text, max_units)) > max_lines:
        lines[-1] = ellipsize_visual_text(lines[-1], max_units)
    return lines, font_size, line_height


def add_bounded_text(
    filters,
    current,
    layer_num,
    text,
    *,
    x,
    y,
    width,
    height,
    max_font,
    min_font,
    color,
    bold=False,
    start=0.0,
    safety=0.86,
):
    lines, font_size, line_height = fit_bounded_text(text, width, height, max_font, min_font, safety=safety)
    for idx, line in enumerate(lines):
        if is_pure_math_text(line):
            current, layer_num = add_formula_overlay(
                filters, current, layer_num, line, x=x, y=y + idx * line_height,
                width=width, height=line_height, font_size=font_size,
                color=color, bold=bold, start=start
            )
        elif contains_display_formula(line):
            caption, formula = split_formula_caption(line)
            line_y = y + idx * line_height
            if formula and caption:
                formula_w = min(
                    int(width * 0.42),
                    max(int(width * 0.22), estimate_text_px(formula, font_size, True) + int(font_size * 1.20)),
                )
                text_w = max(1, width - formula_w - 14)
                caption = ellipsize_visual_text(caption, text_units_for_width(text_w, font_size, 0.94))
                current, layer_num = add_filter_drawtext(
                    filters, current, layer_num, caption, x=x, y=line_y,
                    font_size=font_size, color=color, bold=bold, start=start
                )
                current, layer_num = add_formula_overlay(
                    filters, current, layer_num, formula,
                    x=x + text_w + 14, y=line_y - int(font_size * 0.18),
                    width=formula_w, height=max(line_height, int(font_size * 1.50)),
                    font_size=font_size, color=color, bold=bold, start=start
                )
            elif formula:
                current, layer_num = add_formula_overlay(
                    filters, current, layer_num, formula, x=x, y=line_y,
                    width=width, height=line_height, font_size=font_size,
                    color=color, bold=bold, start=start
                )
            else:
                current, layer_num = add_filter_rich_text(
                    filters, current, layer_num, line, x=x, y=line_y,
                    font_size=font_size, color=color, bold=bold, start=start
                )
        else:
            current, layer_num = add_filter_drawtext(
                filters, current, layer_num, line, x=x, y=y + idx * line_height,
                font_size=font_size, color=color, bold=bold, start=start
            )
    return current, layer_num


def split_formula_caption(text):
    text = normalize_video_text(text)
    if not text:
        return "", ""
    snippets = []
    for snippet in extract_formula_snippets(text):
        if not snippet:
            continue
        if any(snippet != existing and snippet in existing for existing in snippets):
            continue
        snippets = [existing for existing in snippets if not (existing != snippet and existing in snippet)]
        snippets.append(snippet)
    formula = ", ".join(snippets[:2]) if snippets else ""
    caption = text
    for snippet in sorted(snippets, key=len, reverse=True):
        caption = caption.replace(snippet, " ")
    caption = re.sub(r"±?√\s*(?:[A-Za-z0-9π²³]+|\([^()（）]+\)|（[^()（）]+）)(?:[²³]|\^\s*[23])?", " ", caption)
    caption = re.sub(r"\s*[:：]\s*$", "", caption)
    caption = re.sub(r"[，,：:；;。\s]*、+[，,：:；;。\s]*", "，", caption)
    caption = re.sub(r"[：:；;，,。]\s*[：:；;，,。]*", "，", caption)
    caption = re.sub(r"(?:像|如)\s*等", "", caption)
    caption = re.sub(r"\s+", " ", caption).strip(" ，、。；;:")
    caption = re.sub(r"(?:例如|比如|如|像)$", "", caption).strip(" ，、。；;:")
    if formula and visual_text_len(caption) > 26:
        caption = re.split(r"[，。；;]", caption, 1)[0].strip()
    return caption, formula


def add_micro_course_safe_text(
    filters,
    current,
    layer_num,
    text,
    *,
    x,
    y,
    width,
    height,
    max_font,
    min_font,
    color,
    bold=False,
    start=0.0,
    formula_color=None,
):
    """Avoid inline formula/text overlaps in compact micro-course cards."""
    if not contains_display_formula(text):
        return add_bounded_text(
            filters, current, layer_num, text,
            x=x, y=y, width=width, height=height,
            max_font=max_font, min_font=min_font,
            color=color, bold=bold, start=start
        )
    if is_pure_math_text(text):
        return add_formula_overlay(
            filters, current, layer_num, text,
            x=x, y=y, width=width, height=height,
            font_size=min(max_font, max(min_font, int(height * 0.70))),
            color=formula_color or color, bold=bold, start=start
        )
    caption, formula = split_formula_caption(text)
    if not formula:
        return add_bounded_text(
            filters, current, layer_num, caption or text,
            x=x, y=y, width=width, height=height,
            max_font=max_font, min_font=min_font,
            color=color, bold=bold, start=start
        )
    if caption and height >= 54:
        caption_h = min(max(22, int(height * 0.44)), height - 30)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, caption,
            x=x, y=y, width=width, height=caption_h,
            max_font=max_font, min_font=min_font,
            color=color, bold=bold, start=start
        )
        current, layer_num = add_formula_overlay(
            filters, current, layer_num, formula,
            x=x, y=y + caption_h + 4, width=width, height=max(24, height - caption_h - 4),
            font_size=min(max_font, max(min_font, int((height - caption_h) * 0.72))),
            color=formula_color or color, bold=bold, start=start + 0.02
        )
        return current, layer_num
    if caption:
        formula_w = min(
            int(width * 0.38),
            max(int(width * 0.20), estimate_text_px(formula, max_font, True) + int(max_font * 1.20)),
        )
        text_w = max(1, width - formula_w - 16)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, caption,
            x=x, y=y, width=text_w, height=height,
            max_font=max_font, min_font=min_font,
            color=color, bold=bold, start=start
        )
        current, layer_num = add_formula_overlay(
            filters, current, layer_num, formula,
            x=x + text_w + 16, y=y, width=formula_w, height=height,
            font_size=min(max_font, max(min_font, int(height * 0.58))),
            color=formula_color or color, bold=bold, start=start + 0.02
        )
        return current, layer_num
    return add_formula_overlay(
        filters, current, layer_num, formula,
        x=x, y=y, width=width, height=height,
        font_size=min(max_font, max(min_font, int(height * 0.68))),
        color=formula_color or color, bold=bold, start=start
    )


def add_dot_grid(filters, current, layer_num, color="#d1d5db", start=0.0):
    for row in range(0, VIDEO_H, 160):
        for col in range(0, VIDEO_W, 220):
            if (row // 160 + col // 220) % 2 == 0:
                current, layer_num = add_filter_drawbox(filters, current, layer_num, (col + 40, row + 45, 4, 4), f"{color}@0.38", "fill", start)
    return current, layer_num


def add_diverse_dark_canvas(filters, current, layer_num, project, slide_num, slide_data, accent="#10b981"):
    if has_slide_visual(project, slide_num, slide_data):
        current, layer_num = add_filter_visual_cover(filters, current, layer_num, box=(0, 0, VIDEO_W, VIDEO_H), opacity=0.22, start=0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, VIDEO_H), "#07111f@0.92", "fill", 0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, 80), f"{accent}@0.18", "fill", 0.0)
    current, layer_num = add_dot_grid(filters, current, layer_num, "#94a3b8", 0.0)
    return current, layer_num


def add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, accent="#10b981"):
    if has_slide_visual(project, slide_num, slide_data):
        current, layer_num = add_filter_visual_cover(filters, current, layer_num, box=(0, 0, VIDEO_W, VIDEO_H), opacity=0.12, start=0.0)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, VIDEO_H), "white@0.82", "fill", 0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, 10), accent, "fill", 0.0)
    current, layer_num = add_dot_grid(filters, current, layer_num, "#cbd5e1", 0.0)
    return current, layer_num


def diverse_cards(slide_data, slide_num, project, limit=4):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=limit)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:limit]
    cards = merge_example_cards(cards, limit=limit)
    cards = augment_teaching_cards(title, rest, cards, limit=limit)
    return title, cards


def layout_diverse_editorial(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    display_title = title or project_display_title(project)
    subtitle = concise_body(" ".join(rest), 44) if rest else "核心概念 / 方法拆解 / 场景应用"
    filters = [f"color=c=#07111f:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    if has_slide_visual(project, slide_num, slide_data):
        current, layer_num = add_filter_visual_cover(filters, current, layer_num, box=(880, 0, 1040, VIDEO_H), opacity=0.96, start=0.0)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (760, 0, 1180, VIDEO_H), "black@0.30", "fill", 0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, 930, VIDEO_H), "#07111f@0.98", "fill", 0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (120, 118, 112, 8), "#10b981", "fill", 0.15)
    current, layer_num = add_micro_label(filters, current, layer_num, "KNOWLEDGE MAP", 120, 168, "#10b981", 0.2)
    current, layer_num = add_large_title(filters, current, layer_num, display_title, x=120, y=275, width=760, height=230, color="#f8fafc", start=0.32, max_font=76, min_font=52)
    current, layer_num = add_body_line(filters, current, layer_num, subtitle, x=125, y=555, width=680, max_lines=2, font_size=32, color="#a7f3d0", start=0.62)
    chip_source = [
        concise_body(line, 10)
        for line in rest
        if not is_noise_line(line) and 2 <= visual_text_len(line) <= 18
    ]
    if len(chip_source) < 4:
        chip_source.extend(["核心概念", "关键方法", "应用场景", "进阶理解"])
    chips = chip_source[:4]
    for idx, chip in enumerate(chips):
        x = 120 + (idx % 2) * 260
        y = 720 + (idx // 2) * 72
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 220, 48), "#ffffff@0.08", "fill", 0.82 + idx * 0.08)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, chip, x=x + 24, y=y + 12, font_size=22, color="#f8fafc", bold=True, start=0.86 + idx * 0.08)
    return filters, current, layer_num


def layout_adaptive_hero(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    if slide_num == 1:
        display_title = title or project_display_title(project)
        subtitle = concise_body(" ".join(rest), 36) if rest else "核心概念 / 方法拆解 / 场景应用"
        tagline = "结构化理解每一页内容"
        filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
        current = "bg"
        layer_num = 0
        current, layer_num = add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data, opacity=0.10)
        current, layer_num = add_visual_panel(
            filters, current, layer_num, project, slide_num, slide_data,
            (1120, 120, 650, 760), concise_body(display_title, 12), start=0.18
        )
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (120, 138, 92, 8), "#62ad6a", "fill", 0.22)
        current, layer_num, _, _, _ = add_fitting_wrapped_text_in_box(
            filters, current, layer_num, display_title, x=120, y=220, width=900, height=210,
            max_font=72, min_font=54, color="#07111f", bold=True, start=0.28, safety=0.94
        )
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, subtitle, x=120, y=482, max_chars=20, max_lines=1,
            line_height=44, font_size=38, color="#2f8b4b", bold=True, start=0.56
        )
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, tagline, x=120, y=560, max_chars=34, max_lines=2,
            line_height=34, font_size=26, color="#5b6778", start=0.78
        )
        chip_source = [
            concise_body(line, 8)
            for line in rest
            if not is_noise_line(line) and 2 <= visual_text_len(line) <= 16
        ]
        if len(chip_source) < 4:
            chip_source.extend(["概念", "方法", "应用", "进阶"])
        chips = [(chip_source[0], "#2375ff"), (chip_source[1], "#62ad6a"), (chip_source[2], "#f4a000"), (chip_source[3], "#16a3a3")]
        for idx, (chip, accent) in enumerate(chips):
            x = 120 + idx * 206
            y = 715
            start = 0.92 + idx * 0.12
            current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 168, 52), f"{accent}@0.12", "fill", start)
            current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 6, 52), f"{accent}@0.92", "fill", start)
            current, layer_num = add_filter_drawtext(filters, current, layer_num, chip, x=x + 22, y=y + 13, font_size=24, color="#07111f", bold=True, start=start)
        return filters, current, layer_num

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    visual = has_slide_visual(project, slide_num, slide_data)
    subtitle = rest[0] if rest else ""
    tagline = rest[1] if len(rest) > 1 else ""
    body_text = subtitle
    if tagline and visual_text_len(subtitle) < 70:
        body_text = f"{subtitle} {tagline}".strip()
        tagline = ""
    dense_text_mode = bool(visual and visual_text_len(body_text) > 78)
    text_x = 120
    text_w = 1540 if dense_text_mode else (870 if visual else 1160)

    if dense_text_mode:
        current, layer_num = add_filter_visual_backdrop(filters, current, layer_num, project, slide_num, opacity=0.13, slide_data=slide_data)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, VIDEO_H), "#fbfcfb@0.70", "fill", 0.0)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (80, 120, 1660, 790), "white@0.72", "fill", 0.0)
    elif visual:
        visual_box = (1055, 135, 745, 760)
        current, layer_num = add_filter_visual_cover(
            filters,
            current,
            layer_num,
            box=visual_box,
            opacity=0.98,
            start=0.15,
        )
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, 1010, VIDEO_H), "#fbfcfb@0.97", "fill", 0.0)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (1010, 0, 46, VIDEO_H), "#fbfcfb@0.76", "fill", 0.0)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, visual_box, "black@0.07", 4, 0.25)

    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, 10), "#e5e7eb", "fill")
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (text_x, 160, 92, 8), "#62ad6a", "fill", 0.2)

    title_units = visual_text_len(title)
    if dense_text_mode:
        title_font = 62 if title_units <= 24 else 54
        title_max_lines = 3
        title_box_h = 220
    elif not visual:
        title_font = 76 if title_units <= 24 else 64
        title_max_lines = 2
        title_box_h = 190
    elif title_units <= 14:
        title_font = 72
        title_max_lines = 2
        title_box_h = 190
    elif title_units <= 26:
        title_font = 62
        title_max_lines = 3
        title_box_h = 230
    else:
        title_font = 54
        title_max_lines = 3
        title_box_h = 240
    title_line_height = max(62, title_font + 10)
    current, layer_num, title_line_count, _, title_line_height = add_fitting_wrapped_text_in_box(
        filters, current, layer_num, title, x=text_x, y=230, width=text_w, height=title_box_h,
        max_font=title_font, min_font=42 if dense_text_mode else 46,
        color="#070b1d", bold=True, start=0.25, safety=0.94,
    )
    if body_text:
        subtitle_font = 34 if dense_text_mode else (32 if visual else 36)
        subtitle_y = 230 + title_line_count * title_line_height + (34 if dense_text_mode else 42)
        subtitle_h = max(180, (850 if dense_text_mode else 800) - subtitle_y)
        current, layer_num, subtitle_line_count, _, subtitle_line_height = add_fitting_wrapped_text_in_box(
            filters, current, layer_num, body_text, x=text_x, y=subtitle_y, width=text_w, height=subtitle_h,
            max_font=subtitle_font, min_font=22 if dense_text_mode else 24,
            color="#465160", bold=True, start=0.6, safety=0.92 if dense_text_mode else 0.88,
        )
    else:
        subtitle_y = 430
        subtitle_line_count = 0
        subtitle_line_height = 40
    if tagline:
        tagline_y = subtitle_y + subtitle_line_count * subtitle_line_height + 46
        if tagline_y < 820:
            current, layer_num, _ = add_wrapped_text_in_box(
                filters, current, layer_num, tagline, x=text_x, y=tagline_y, width=text_w,
                max_lines=2 if visual else 1, line_height=32, font_size=24, color="#8b95a1", start=0.9
            )
    if not visual:
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (1330, 180, 360, 360), "#eaf7ec", "fill", 0.45)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (1400, 250, 220, 220), "#62ad6a@0.90", "fill", 0.65)
        mark = hero_mark_from_title(title)
        current, layer_num = add_filter_drawtext(
            filters, current, layer_num, mark, x=1450 if len(mark) <= 2 else 1432, y=330,
            font_size=62 if len(mark) <= 2 else 48, color="white", bold=True, start=0.8
        )
    return filters, current, layer_num


def process_items_from_lines(title, rest):
    clean = [line for line in rest if not is_noise_line(line)]
    year_items = [line for line in clean if "年" in line and visual_text_len(line) <= 12]
    if len(year_items) >= 3:
        first_idx = clean.index(year_items[0])
        after_years = clean[first_idx + len(year_items):]
        phases = [line for line in after_years if is_card_heading(line) and "年" not in line][:len(year_items)]
        if len(phases) < len(year_items):
            phases = phases + after_years[: len(year_items) - len(phases)]
        return [
            {
                "title": year_items[idx],
                "body": phases[idx] if idx < len(phases) else "",
            }
            for idx in range(min(5, len(year_items)))
        ]

    if len(clean) >= 10 and all(re.fullmatch(r"\d{2}", item) for item in clean[:5]):
        return [
            {"title": clean[5 + idx], "body": clean[10 + idx] if 10 + idx < len(clean) else ""}
            for idx in range(5)
        ]

    year_line = next((line for line in clean if "第1年" in line or "第2" in line), "")
    if year_line:
        years = re.findall(r"第\d(?:-\d)?年\+?", year_line)
        phase_line = next((line for line in clean if "试点" in line or "布局" in line or "扩张" in line), "")
        phases = ["试点运营", "区域扩张", "全国布局", "产业链延伸"]
        if not any(phase in phase_line for phase in phases):
            phases = clean[1:1 + len(years)]
        return [
            {"title": years[idx] if idx < len(years) else f"阶段{idx + 1}", "body": phases[idx] if idx < len(phases) else ""}
            for idx in range(min(4, max(len(years), len(phases))))
        ]

    headings = [line for line in clean if is_card_heading(line)]
    if len(headings) >= 4:
        return [{"title": heading, "body": ""} for heading in headings[:5]]

    return [{"title": line, "body": ""} for line in clean[:5]]


def lifecycle_items_from_lines(rest):
    items = process_items_from_lines("", rest)
    if len(items) >= 4:
        return items[:5]
    clean = [line for line in rest if not is_noise_line(line)]
    headings = [line for line in clean if is_card_heading(line)]
    if len(headings) >= 4:
        return [{"title": heading, "body": ""} for heading in headings[:5]]
    fallback = clean[:5]
    return [{"title": line, "body": ""} for line in fallback]


def layout_adaptive_lifecycle(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = lifecycle_items_from_lines(rest)
    if len(items) < 3:
        return layout_adaptive_process(slide_data, slide_num, duration, project)

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data, opacity=0.12)
    current, layer_num = add_premium_header(filters, current, layer_num, "闭环系统", title, "从回收触点到再生面料，形成可追踪的产业闭环。")

    cx, cy = 980, 635
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (710, 365, 540, 540), "#eaf7ec", "fill", 0.25)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (805, 460, 350, 350), "white@0.96", "fill", 0.35)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (900, 555, 160, 160), "#62ad6a@0.92", "fill", 0.48)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "LOOP", x=913, y=594, font_size=42, color="white", bold=True, start=0.58)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "闭环再生", x=894, y=650, font_size=26, color="white", bold=True, start=0.68)

    positions = [(540, 380), (1085, 365), (1280, 635), (982, 805), (410, 650)]
    accents = ["#2375ff", "#62ad6a", "#f4a000", "#e1565a", "#16a3a3"]
    count = min(len(items), len(positions))
    prev = None
    for idx, item in enumerate(items[:count]):
        x, y = positions[idx]
        start = 0.45 + idx * max(0.50, duration * 0.07)
        accent = accents[idx % len(accents)]
        if prev:
            x1, y1 = prev
            current, layer_num = add_filter_drawbox(
                filters, current, layer_num,
                (min(x1, x) + 134, min(y1, y) + 70, abs(x - x1) + 64, 5),
                f"{accent}@0.30", "fill", start
            )
        prev = (x, y)
        current, layer_num = add_premium_panel(filters, current, layer_num, (x, y, 292, 142), accent, start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 24, y + 34, 54, 54), f"{accent}@0.14", "fill", start + 0.05)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1:02d}", x=x + 36, y=y + 48, font_size=26, color=accent, bold=True, start=start + 0.08)
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, item["title"], x=x + 94, y=y + 30, max_chars=10,
            max_lines=2, line_height=31, font_size=27, color="#07111f", bold=True, start=start + 0.10
        )
        if item.get("body"):
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, concise_body(item["body"], 24), x=x + 94, y=y + 94, max_chars=17,
                max_lines=1, line_height=24, font_size=20, color="#5d6878", start=start + 0.16
            )
    return filters, current, layer_num


def layout_adaptive_process(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = process_items_from_lines(title, rest)
    if not items:
        return layout_clean_cards(slide_data, slide_num, duration, project)

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_filter_visual_backdrop(filters, current, layer_num, project, slide_num, opacity=0.24, slide_data=slide_data)
    current, layer_num = add_adaptive_header(filters, current, layer_num, "流程拆解", title, "按步骤看清核心路径。")

    count = min(5, len(items))
    start_x = 145
    usable_w = 1630
    step_w = usable_w // count
    y = 560
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (start_x, y + 54, usable_w - 40, 6), "#d7eadb", "fill", 0.3)
    for idx, item in enumerate(items[:count]):
        x = start_x + idx * step_w
        start = 0.45 + idx * max(0.55, duration * 0.09)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 88, 88), "#62ad6a", "fill", start)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1:02d}", x=x + 24, y=y + 24, font_size=34, color="white", bold=True, start=start)
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, item["title"], x=x, y=y + 130, max_chars=9, max_lines=2,
            line_height=38, font_size=32, color="#070b1d", bold=True, start=start
        )
        if item.get("body"):
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, item["body"], x=x, y=y + 220, max_chars=13, max_lines=3,
                line_height=28, font_size=21, color="#5d6878", start=start
            )
    return filters, current, layer_num


def layout_adaptive_roadmap(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = process_items_from_lines(title, rest)
    if len(items) < 3:
        return layout_adaptive_process(slide_data, slide_num, duration, project)

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data, opacity=0.10)
    current, layer_num = add_premium_header(filters, current, layer_num, "增长路线图", title, "从试点运营到全国布局，展示阶段目标和推进节奏。")

    count = min(5, len(items))
    axis_y = 620
    left, width = 180, 1540
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (left, axis_y, width, 10), "#d7eadb", "fill", 0.25)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (left, axis_y, int(width * 0.72), 10), "#62ad6a@0.86", "fill", 0.45)

    accents = ["#2375ff", "#62ad6a", "#f4a000", "#e1565a", "#16a3a3"]
    step = width // max(1, count - 1) if count > 1 else width
    for idx, item in enumerate(items[:count]):
        node_x = left + idx * step if count > 1 else left + width // 2
        node_x = min(left + width - 80, max(left + 80, node_x))
        top_side = idx % 2 == 0
        card_w, card_h = 335, 155
        card_x = max(80, min(VIDEO_W - card_w - 80, node_x - card_w // 2))
        card_y = 392 if top_side else 712
        start = 0.4 + idx * max(0.48, duration * 0.075)
        accent = accents[idx % len(accents)]
        connector_y = card_y + card_h if top_side else axis_y + 8
        connector_h = axis_y - connector_y if top_side else card_y - connector_y
        current, layer_num = add_filter_drawbox(
            filters, current, layer_num,
            (node_x - 3, min(connector_y, axis_y), 6, abs(connector_h)),
            f"{accent}@0.45", "fill", start
        )
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (node_x - 28, axis_y - 24, 56, 56), f"{accent}@0.92", "fill", start)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1}", x=node_x - 10, y=axis_y - 8, font_size=25, color="white", bold=True, start=start + 0.04)
        current, layer_num = add_premium_panel(filters, current, layer_num, (card_x, card_y, card_w, card_h), accent, start)
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, item["title"], x=card_x + 24, y=card_y + 26, max_chars=11,
            max_lines=2, line_height=29, font_size=25, color="#07111f", bold=True, start=start + 0.08
        )
        if item.get("body"):
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, concise_body(item["body"], 26), x=card_x + 24, y=card_y + 88, max_chars=18,
                max_lines=1, line_height=24, font_size=20, color="#5d6878", start=start + 0.14
            )
    return filters, current, layer_num


def content_cards_from_lines(rest, limit=4):
    clean = [line for line in rest if not is_noise_line(line)]
    cards = []

    def add_card(heading, body="", subtitle=""):
        if len(cards) >= limit:
            return
        title, derived_subtitle = split_heading_subtitle(heading, subtitle)
        cards.append({
            "title": title,
            "subtitle": derived_subtitle,
            "body": normalize_video_text(body),
        })

    ordered_cards = ordered_point_cards(clean, limit=limit)
    if ordered_cards:
        return merge_example_cards(ordered_cards, limit=limit)

    for count in (4, 3):
        if len(clean) >= count * 3 and all(is_card_heading(item) for item in clean[:count]):
            middle = clean[count: count * 2]
            bodies = clean[count * 2: count * 3]
            if all(is_card_subtitle(item) for item in middle):
                for idx in range(count):
                    add_card(clean[idx], bodies[idx], middle[idx])
                return merge_example_cards(cards, limit=limit)

    idx = 0
    while idx + 3 < len(clean) and len(cards) < limit:
        if (
            is_card_heading(clean[idx])
            and is_card_heading(clean[idx + 1])
            and not is_card_heading(clean[idx + 2])
            and not is_card_heading(clean[idx + 3])
        ):
            add_card(clean[idx], clean[idx + 2])
            add_card(clean[idx + 1], clean[idx + 3])
            idx += 4
            continue
        break

    if len(cards) >= limit:
        return merge_example_cards(cards, limit=limit)

    for card in build_alternating_cards(clean, limit=limit):
        if len(cards) >= limit:
            break
        if not any(card["title"] == existing["title"] for existing in cards):
            cards.append(card)

    if not cards and clean:
        titles = ["核心要点", "组织方式", "关键价值", "补充说明"]
        for idx, body in enumerate(fallback_body_chunks(clean, limit=limit)):
            add_card(titles[idx] if idx < len(titles) else f"要点{idx + 1}", body)

    return merge_example_cards(cards, limit=limit)


def augment_teaching_cards(title, rest, cards, limit=4):
    """Fill sparse PPT pages with short teaching cards instead of leaving empty space."""
    cards = [
        {
            "title": normalize_video_text(card.get("title", "")),
            "subtitle": normalize_video_text(card.get("subtitle", "")),
            "body": normalize_video_text(card.get("body", "")),
            **({"number": card.get("number")} if card.get("number") else {}),
        }
        for card in cards
        if normalize_video_text(card.get("title", "")) or normalize_video_text(card.get("body", ""))
    ]
    if len(cards) >= limit and all(visual_text_len(card.get("body", "")) >= 10 for card in cards[:limit]):
        return cards[:limit]

    topic = " ".join([normalize_video_text(title), *[normalize_video_text(line) for line in rest]])
    supplements = []
    if "根号" in topic or "平方根" in topic or "√" in topic:
        supplements.extend([
            {"title": "先看条件", "subtitle": "定义范围", "body": "实数范围内，根号下的被开方数通常要非负。"},
            {"title": "例题提示", "subtitle": "代入检查", "body": "看到具体数字时，先判断能否开方，再看结果是否取非负值。"},
            {"title": "易错提醒", "subtitle": "符号区分", "body": "√a 默认表示算术平方根，求平方根时才写 ±√a。"},
            {"title": "应用场景", "subtitle": "回到问题", "body": "分类、化简和方程求解都要先确认根式是否有意义。"},
        ])
    elif any(key in topic for key in ("函数", "方程", "不等式")):
        supplements.extend([
            {"title": "先抓对象", "subtitle": "变量关系", "body": "先明确未知量、已知条件和目标结论，再选择运算方法。"},
            {"title": "例题提示", "subtitle": "逐步验证", "body": "每变形一步都要检查是否改变定义域或解集。"},
            {"title": "易错提醒", "subtitle": "条件不丢", "body": "含分母、根号或平方运算时，要额外检查限制条件。"},
        ])
    else:
        supplements.extend([
            {"title": "补充说明", "subtitle": "换句话说", "body": "把本页概念拆成定义、条件和用途三个层次来理解。"},
            {"title": "例子联想", "subtitle": "落到场景", "body": "遇到抽象概念时，先找一个具体数字、案例或操作步骤。"},
            {"title": "速记提示", "subtitle": "复盘顺序", "body": "先看关键词，再看限制条件，最后用一个例子检验。"},
        ])

    existing_keys = {enrichment_fingerprint(card.get("title", "") + card.get("body", "")) for card in cards}
    for item in supplements:
        if len(cards) >= limit:
            break
        key = enrichment_fingerprint(item["title"] + item["body"])
        if is_duplicate_enrichment(key, existing_keys):
            continue
        cards.append(item)
        existing_keys.add(key)

    for idx, card in enumerate(cards[:limit]):
        card.setdefault("number", f"{idx + 1:02d}")
    return cards[:limit]


def classify_enrichment_line(line):
    line = normalize_video_text(line)
    if not line or is_noise_line(line):
        return None
    if any(keyword in line for keyword in ("易错", "注意", "不能", "不要", "无意义", "区分", "区别")):
        return "易错"
    if any(keyword in line for keyword in ("例如", "比如", "举例")) and re.search(r"\d", line):
        return "例题"
    formula_marks = ("√", "±", "=", "x²", "²", "^2", "×", "÷", "<", ">", "≤", "≥")
    if any(mark in line for mark in formula_marks):
        return "公式"
    if "如" in line and re.search(r"\d", line):
        return "例题"
    if any(keyword in line for keyword in ("非负", "有意义", "条件", "范围", "必须", "须", "才")):
        return "条件"
    if any(keyword in line for keyword in ("定义", "表示", "代表", "指", "是")):
        return "定义"
    if re.search(r"\d", line):
        return "数据"
    return "速记"


def enrichment_fingerprint(line):
    text = normalize_video_text(line)
    text = re.sub(r"^[^：:]{1,18}[：:]\s*", "", text)
    text = re.sub(r"[，。；;、：:\s（）()《》“”\"'‘’]", "", text)
    return text.lower()


def is_duplicate_enrichment(candidate_key, existing_keys):
    if not candidate_key:
        return True
    for key in existing_keys:
        if candidate_key == key:
            return True
        if len(candidate_key) >= 14 and len(key) >= 14 and (candidate_key in key or key in candidate_key):
            return True
    return False


def left_enrichment_items(title, rest, cards, limit=3):
    title = normalize_video_text(title)
    topic_text = " ".join([title, *[normalize_video_text(line) for line in rest]])
    if title == "目录" and cards:
        path = " → ".join(
            concise_body(card.get("title", ""), 10)
            for card in cards[:4]
            if normalize_video_text(card.get("title", ""))
        )
        items = [{"label": "路径", "body": f"按 {path} 的顺序建立理解。"}] if path else []
        if len(cards) >= 2:
            items.append({"label": "方法", "body": "先看概念，再练运算，最后用进阶题检验。"})
        return items[:limit]

    candidates = []
    for line in rest:
        candidates.append(normalize_video_text(line))
    for card in cards:
        heading = normalize_video_text(card.get("title", ""))
        subtitle = normalize_video_text(card.get("subtitle", ""))
        body = normalize_video_text(card.get("body", ""))
        if heading and body:
            candidates.append(f"{heading}：{body}")
        elif body:
            candidates.append(body)
        elif subtitle:
            candidates.append(f"{heading}：{subtitle}" if heading else subtitle)
        elif heading:
            candidates.append(heading)

    grouped = {"易错": [], "公式": [], "例题": [], "条件": [], "定义": [], "数据": [], "速记": []}
    seen = set()
    for line in candidates:
        line = normalize_video_text(line)
        if not line or is_noise_line(line):
            continue
        if line == title or visual_text_len(line) <= 2:
            continue
        key = enrichment_fingerprint(line)
        if is_duplicate_enrichment(key, seen):
            continue
        label = classify_enrichment_line(line)
        if label:
            seen.add(key)
            grouped.setdefault(label, []).append(line)

    items = []
    item_keys = set()
    ordered_labels = ("易错", "公式", "条件", "例题", "定义", "数据", "速记")
    for _ in range(limit):
        added_this_round = False
        for label in ordered_labels:
            if len(items) >= limit:
                break
            for line in grouped.get(label, []):
                key = enrichment_fingerprint(line)
                if is_duplicate_enrichment(key, item_keys):
                    continue
                items.append({"label": label, "body": concise_body(line, 52)})
                item_keys.add(key)
                added_this_round = True
                break
        if len(items) >= limit:
            break
        if not added_this_round:
            break

    if len(items) < limit and "根号" in topic_text:
        fallback = [
            {"label": "条件", "body": "实数范围内，根号下的被开方数通常要非负。"},
            {"label": "易错", "body": "√a 默认表示算术平方根；求平方根时才写 ±√a。"},
            {"label": "公式", "body": "若 a≥0，则 √a 是满足 x²=a 的非负数。"},
        ]
        for item in fallback:
            key = enrichment_fingerprint(item["body"])
            if len(items) >= limit:
                break
            if not is_duplicate_enrichment(key, item_keys):
                items.append(item)
                item_keys.add(key)

    if len(items) < limit and cards:
        card_titles = [
            concise_body(card.get("title", ""), 8)
            for card in cards
            if normalize_video_text(card.get("title", ""))
        ]
        if card_titles:
            items.append({
                "label": "速记",
                "body": f"先区分 {'、'.join(card_titles[:3])}，再对应选择规则。",
            })
    if not items and title:
        items.append({"label": "速记", "body": f"{title}：先看定义，再看条件，最后用例题验证。"})
    return items[:limit]


def add_left_enrichment_panel(filters, current, layer_num, title, rest, cards, *, x, y, w, h, accent, start=0.62):
    items = left_enrichment_items(title, rest, cards, limit=3)
    if not items:
        return current, layer_num

    current, layer_num = add_round_panel(
        filters, current, layer_num, (x, y, w, h), accent, "white@0.78", start,
        radius=42, shadow=True, border=True
    )
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 34, y + 30, 150, 42), f"{accent}@0.14", 21, start + 0.06)
    current, layer_num = add_bounded_text(
        filters, current, layer_num, "关键补充", x=x + 60, y=y + 40,
        width=105, height=22, max_font=20, min_font=16, color=accent, bold=True, start=start + 0.10
    )
    current, layer_num = add_bounded_text(
        filters, current, layer_num, "本页额外记忆点", x=x + 205, y=y + 41,
        width=w - 245, height=24, max_font=19, min_font=15, color="#64748b", start=start + 0.12
    )

    row_h = 82
    row_gap = 22
    row_y = y + 92
    palette = [accent, "#8b5cf6", "#10b981"]
    for idx, item in enumerate(items):
        accent_i = palette[idx % len(palette)]
        top = row_y + idx * (row_h + row_gap)
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 34, top, w - 68, row_h), f"{accent_i}@0.075", 28, start + 0.18 + idx * 0.08)
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 56, top + 22, 76, 38), f"{accent_i}@0.16", 19, start + 0.22 + idx * 0.08)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("label", "速记"), x=x + 74, y=top + 31,
            width=44, height=20, max_font=18, min_font=14, color=accent_i, bold=True, start=start + 0.26 + idx * 0.08
        )
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("body", ""), x=x + 154, y=top + 19,
            width=w - 228, height=48, max_font=21, min_font=16, color="#1e293b", bold=False, start=start + 0.30 + idx * 0.08
        )
    return current, layer_num


def add_compact_enrichment_panel(
    filters,
    current,
    layer_num,
    title,
    rest,
    cards,
    *,
    x,
    y,
    w,
    h,
    accent,
    start=0.70,
    dark=False,
    label="补充",
    limit=2,
):
    items = left_enrichment_items(title, rest, cards, limit=limit)
    if not items:
        return current, layer_num

    fill = "#ffffff@0.90" if not dark else "#ffffff@0.10"
    heading_color = "#0f172a" if not dark else "#f8fafc"
    body_color = "#475569" if not dark else "#cbd5e1"
    current, layer_num = add_round_panel(
        filters, current, layer_num, (x, y, w, h), accent, fill, start,
        radius=34, shadow=not dark, border=True
    )
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 24, y + 22, 92, 34), f"{accent}@0.18", 17, start + 0.04)
    current, layer_num = add_bounded_text(
        filters, current, layer_num, label, x=x + 44, y=y + 29,
        width=52, height=18, max_font=17, min_font=13, color=accent, bold=True, start=start + 0.08
    )
    row_y = y + 72
    usable_w = w - 52
    row_h = max(48, (h - 92) // max(1, len(items)))
    for idx, item in enumerate(items):
        top = row_y + idx * row_h
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 24, top + 1, 62, 30), f"{accent}@0.13", 15, start + 0.12 + idx * 0.07)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("label", "速记"), x=x + 38, y=top + 8,
            width=36, height=16, max_font=15, min_font=12, color=accent, bold=True, start=start + 0.16 + idx * 0.07
        )
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("body", ""), x=x + 98, y=top + 3,
            width=usable_w - 78, height=row_h - 6, max_font=18, min_font=14,
            color=heading_color if idx == 0 else body_color, bold=False, start=start + 0.20 + idx * 0.07
        )
    return current, layer_num


def cards_from_items(items):
    cards = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cards.append({
            "title": normalize_video_text(item.get("title", "")),
            "subtitle": normalize_video_text(item.get("metric", "")),
            "body": normalize_video_text(item.get("body", "")),
        })
    return cards


def formula_card_body_summary(title_text, body_text, limit=42):
    title_text = normalize_video_text(title_text)
    body_text = normalize_video_text(body_text)
    combined = f"{title_text} {body_text}"
    if not body_text:
        return ""

    if "平方根" in combined and ("算术平方根" in combined or "非负" in combined):
        if "±" in combined or "正负" in combined:
            return "平方根有正负两个结果，√a 表示非负的算术平方根。"
        return "√a 表示非负的算术平方根，结果要满足自乘还原。"
    if "符号" in combined and "√" in combined:
        if "√16" in combined:
            return "√16 = 4，表示 16 的算术平方根是 4。"
        if "√9" in combined:
            return "√9 = 3，表示 9 的算术平方根是 3。"
        return "√ 是算术平方根符号，结果默认取非负值。"
    if "解决" in combined or "自乘" in combined or "问题" in combined:
        return "先找自乘等于原数的数，再判断平方根是否取正负。"
    if "定义" in combined and "根号" in combined:
        return "按定义看被开方数、运算符号和结果范围。"
    return compact_sentence_without_ellipsis(body_text, limit)


def formula_walkthrough_cards(title, rest, limit=4):
    cards = content_cards_from_lines(rest, limit=limit)
    if not cards:
        cards = cards_from_items(process_items_from_lines(title, rest))[:limit]
    cleaned = []
    seen = set()
    for card in cards:
        title_text = normalize_video_text(card.get("title", ""))
        raw_body = normalize_video_text((card.get("subtitle") or "") + " " + (card.get("body") or ""))
        body_text = formula_card_body_summary(title_text, raw_body, 42)
        if not title_text and not body_text:
            continue
        key = enrichment_fingerprint(title_text + body_text)
        if is_duplicate_enrichment(key, seen):
            continue
        seen.add(key)
        cleaned.append({"title": title_text, "body": body_text})
        if len(cleaned) >= limit:
            break
    if len(cleaned) < 3:
        for item in process_items_from_lines(title, rest):
            title_text = normalize_video_text(item.get("title", ""))
            body_text = formula_card_body_summary(title_text, item.get("body", ""), 42)
            key = enrichment_fingerprint(title_text + body_text)
            if is_duplicate_enrichment(key, seen):
                continue
            seen.add(key)
            cleaned.append({"title": title_text, "body": body_text})
            if len(cleaned) >= limit:
                break
    if len(cleaned) < 3:
        cleaned = [
            {"title": card.get("title", ""), "body": (card.get("subtitle") or "") + " " + (card.get("body") or "")}
            for card in augment_teaching_cards(title, rest, cleaned, limit=limit)
        ]
    if len(cleaned) < limit:
        topic_text = " ".join([normalize_video_text(title), *[normalize_video_text(line) for line in rest]])
        supplemental = []
        if "√" in topic_text or "平方根" in topic_text:
            supplemental.append({
                "title": "例题检验",
                "body": "x² = 9 → x = ±3；而 √9 = 3。",
            })
        for card in supplemental:
            key = enrichment_fingerprint(card["title"] + card["body"])
            if is_duplicate_enrichment(key, seen):
                continue
            seen.add(key)
            cleaned.append(card)
            if len(cleaned) >= limit:
                break
    return cleaned[:limit]


def extract_formula_snippets(text):
    text = normalize_video_text(text)
    snippets = []
    radical_atom = r"±?√\s*(?:[A-Za-z0-9π²³]+|\([^()（）]+\)|（[^()（）]+）)"
    radical_expr = rf"(?:\(?\s*{radical_atom}\s*\)?(?:[²³]|\^\s*[23])?)"
    radical_list_re = re.compile(rf"{radical_atom}(?:\s*[、,，]\s*{radical_atom})+")

    relation_ops = set("=≥≤<>≠→")
    formula_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789π√±²³+-*/×÷=<>≤≥≠→^().（）[]|,，、;； ")

    def is_formula_char(ch):
        return ch in formula_chars or ch.isspace()

    def trim_formula_candidate(candidate):
        candidate = re.sub(r"\s+", " ", candidate).strip(" ，、。；;:")
        candidate = re.sub(r"^[,，、;；\s]+|[,，、;；\s]+$", "", candidate)
        return candidate

    def collect_relation_candidates(value):
        for idx, ch in enumerate(value):
            if ch not in relation_ops:
                continue
            left = idx - 1
            while left >= 0 and is_formula_char(value[left]):
                left -= 1
            right = idx + 1
            while right < len(value) and is_formula_char(value[right]):
                right += 1
            candidate = trim_formula_candidate(value[left + 1:right])
            if not candidate or not any(op in candidate for op in relation_ops):
                continue
            if contains_math_notation(candidate):
                snippets.append(candidate)

    if any(keyword in text for keyword in ("摩擦", "正压力", "动摩擦因数", "静摩擦")):
        if re.search(r"F\s*=\s*μ\s*N", text, re.IGNORECASE):
            snippets.append("F = μN")
        if re.search(r"f\s*=\s*μ\s*N", text, re.IGNORECASE):
            snippets.append("f = μN")
        if re.search(r"μs\s*N|μ_s\s*N", text, re.IGNORECASE):
            snippets.append("0 ≤ fs ≤ μsN")

    for marker in ("如", "可写作", "即"):
        if marker in text:
            tail = text.split(marker, 1)[1]
            tail = re.split(r"[，。；;]", tail, 1)[0].strip()
            if contains_math_notation(tail):
                list_match = radical_list_re.search(tail)
                snippets.append(list_match.group(0).strip() if list_match else tail)
    collect_relation_candidates(text)
    snippets.extend(match.group(0).strip() for match in radical_list_re.finditer(text))
    radical_re = re.compile(radical_expr)
    snippets.extend(match.group(0).strip() for match in radical_re.finditer(text))
    cleaned = []
    seen = set()
    for snippet in snippets:
        snippet = re.sub(r"\s+", " ", snippet).strip(" ，、。；;")
        if not snippet or not contains_math_notation(snippet):
            continue
        if any(snippet != existing and snippet in existing for existing in cleaned):
            continue
        key = enrichment_fingerprint(snippet)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(concise_body(snippet, 24))
    return cleaned


def formula_example_lines(rest, cards, fallback_title, limit=3):
    formula_candidates = []
    topic_text = " ".join([normalize_video_text(fallback_title), *[normalize_video_text(line) for line in rest]])
    for line in rest:
        text = normalize_video_text(line)
        if not text or is_noise_line(text):
            continue
        for snippet in extract_formula_snippets(text):
            formula_candidates.append(snippet)

    derived = []
    if "√9" in topic_text or "9的平方根" in topic_text or "9 的平方根" in topic_text:
        derived.extend([
            "平方根：x² = 9 → x = ±3",
            "算术平方根：√9 = 3",
            "完整写法：±√9 = ±3",
        ])
    if "√16" in topic_text or "16的算术平方根" in topic_text or "16 的算术平方根" in topic_text:
        derived.append("算术平方根：√16 = 4")

    lines = []
    seen = set()
    for item in [*derived, *formula_candidates]:
        item = compact_sentence_without_ellipsis(item, 28)
        if not item or not contains_math_notation(item):
            continue
        key = enrichment_fingerprint(item)
        if key in seen:
            continue
        seen.add(key)
        lines.append(item)
        if len(lines) >= limit:
            return lines

    fallback = compact_sentence_without_ellipsis(fallback_title, 18)
    return [fallback] if fallback else []


def formula_example_from_cards(rest, cards, fallback_title):
    lines = formula_example_lines(rest, cards, fallback_title, limit=3)
    return "\n".join(lines)


def layout_adaptive_problem(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=4)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project)["cards"]

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data, opacity=0.11)
    current, layer_num = add_premium_header(filters, current, layer_num, "问题雷达", title, "把资源浪费、环境污染、回收低效和价值缺失拆成四个风险信号。")
    current, layer_num = add_visual_panel(
        filters, current, layer_num, project, slide_num, slide_data,
        (1185, 205, 585, 620), "废旧纺织品处置压力", start=0.25
    )
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (1210, 245, 535, 112), "black@0.30", "fill", 0.40)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "回收断点", x=1240, y=274, font_size=28, color="white", bold=True, start=0.48)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "从投放到再生的链路没有闭合", x=1240, y=317, font_size=22, color="white", start=0.58)

    positions = [(120, 380), (640, 380), (120, 650), (640, 650)]
    accents = ["#e1565a", "#f4a000", "#2375ff", "#62ad6a"]
    for idx, card in enumerate(cards[:4]):
        x, y = positions[idx]
        accent = accents[idx % len(accents)]
        start = 0.35 + idx * max(0.45, duration * 0.07)
        current, layer_num = add_premium_panel(filters, current, layer_num, (x, y, 455, 210), accent, start)
        token = metric_token(card["title"] + " " + card.get("subtitle", "") + " " + card.get("body", ""))
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 28, y + 34, 74, 74), f"{accent}@0.14", "fill", start + 0.06)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, token or f"0{idx + 1}", x=x + 42, y=y + 53, font_size=28, color=accent, bold=True, start=start + 0.10)
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, card["title"], x=x + 126, y=y + 32, max_chars=14,
            max_lines=2, line_height=31, font_size=27, color="#07111f", bold=True, start=start + 0.12
        )
        if card.get("subtitle"):
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, card["subtitle"], x=x + 126, y=y + 95, max_chars=20,
                max_lines=1, line_height=28, font_size=22, color=accent, bold=True, start=start + 0.16
            )
            body_y = y + 130
        else:
            body_y = y + 100
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, concise_body(card.get("body", ""), 38), x=x + 30, y=body_y, max_chars=24,
            max_lines=2, line_height=27, font_size=21, color="#5d6878", start=start + 0.18
        )
    return filters, current, layer_num


def comparison_groups_from_lines(rest, limit=2):
    clean = [line for line in rest if not is_noise_line(line)]

    def is_group_heading(line):
        line = normalize_video_text(line)
        if visual_text_len(line) > 8:
            return False
        return (
            line in {"十进制", "二进制", "八进制", "十六进制", "原码", "反码", "补码"}
            or any(keyword in line for keyword in ("进制", "编码", "类型", "方式"))
        )

    groups = []
    idx = 0
    while idx < len(clean) and len(groups) < limit:
        heading = clean[idx]
        if not is_group_heading(heading):
            idx += 1
            continue
        details = []
        idx += 1
        while idx < len(clean):
            candidate = clean[idx]
            if is_group_heading(candidate) and details:
                break
            if not is_noise_line(candidate):
                details.append(candidate)
            idx += 1
            if len(details) >= 4:
                break
        groups.append({"title": heading, "details": details})
    return groups


def layout_adaptive_comparison(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    groups = comparison_groups_from_lines(rest, limit=2)
    if len(groups) < 2:
        return layout_adaptive_matrix(slide_data, slide_num, duration, project)

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_filter_visual_backdrop(filters, current, layer_num, project, slide_num, opacity=0.18, slide_data=slide_data)
    current, layer_num = add_adaptive_header(filters, current, layer_num, "对比说明", title, "把不同对象的特点放在一起比较。")

    positions = [(120, 430), (1000, 430)]
    accents = ["#2375ff", "#62ad6a"]
    for idx, group in enumerate(groups[:2]):
        x, y = positions[idx]
        accent = accents[idx]
        start = 0.35 + idx * max(0.55, duration * 0.08)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 10, y + 10, 800, 430), "black@0.025", "fill", start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 800, 430), "white@0.98", "fill", start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 800, 10), accent, "fill", start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 36, y + 42, 76, 76), f"{accent}@0.13", "fill", start)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1:02d}", x=x + 54, y=y + 63, font_size=30, color=accent, bold=True, start=start)
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, group["title"], x=x + 138, y=y + 46, max_chars=12,
            max_lines=1, line_height=42, font_size=38, color="#070b1d", bold=True, start=start
        )
        detail_y = y + 145
        for detail_idx, detail in enumerate(group["details"][:4]):
            current, layer_num = add_filter_drawbox(
                filters, current, layer_num, (x + 44, detail_y + detail_idx * 62 + 12, 12, 12),
                f"{accent}@0.88", "fill", start + detail_idx * 0.16
            )
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, detail, x=x + 74, y=detail_y + detail_idx * 62,
                max_chars=31, max_lines=2, line_height=28, font_size=23, color="#465160",
                start=start + detail_idx * 0.16
            )
    return filters, current, layer_num


def layout_adaptive_matrix(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=4)
    if len(cards) < 3:
        cards = clean_card_data(slide_data, slide_num, project)["cards"]

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data, opacity=0.10)
    text = title + " " + " ".join(rest)
    is_course_compare = any(keyword in text for keyword in ("进制", "编码", "数据表示", "数值", "二进制", "十进制"))
    label = "对比说明" if is_course_compare else "能力矩阵"
    subtitle = "把不同对象的特点放在一起比较。" if is_course_compare else "按不同维度看项目的核心支撑。"
    current, layer_num = add_premium_header(filters, current, layer_num, "能力矩阵" if not is_course_compare else "对比说明", title, subtitle)

    accents = ["#2375ff", "#62ad6a", "#f4a000", "#e1565a"]
    if len(cards) >= 4:
        positions = [(120, 382), (1020, 382), (120, 672), (1020, 672)]
        card_w, card_h = 760, 230
    else:
        positions = [(120, 455), (710, 455), (1300, 455)]
        card_w, card_h = 500, 365

    for idx, card in enumerate(cards[:4]):
        x, y = positions[idx]
        accent = accents[idx % len(accents)]
        start = 0.35 + idx * max(0.5, duration * 0.08)
        current, layer_num = add_premium_panel(filters, current, layer_num, (x, y, card_w, card_h), accent, start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 32, y + 36, 72, 72), f"{accent}@0.12", "fill", start + 0.06)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1:02d}", x=x + 45, y=y + 56, font_size=28, color=accent, bold=True, start=start + 0.10)
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, card["title"], x=x + 126, y=y + 34, max_chars=18,
            max_lines=2, line_height=34, font_size=30, color="#07111f", bold=True, start=start + 0.12
        )
        body_y = y + 118
        if card.get("subtitle"):
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, card["subtitle"], x=x + 126, y=y + 92, max_chars=24,
                max_lines=1, line_height=28, font_size=23, color=accent, bold=True, start=start + 0.14
            )
            body_y = y + 132
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, concise_body(card.get("body", ""), 58), x=x + 32, y=body_y, max_chars=34 if card_w > 600 else 23,
            max_lines=4 if card_h > 260 else 3, line_height=28, font_size=22, color="#5d6878", start=start
        )
        score = 0.88 - idx * 0.08
        current, layer_num = add_metric_bar(filters, current, layer_num, x + 32, y + card_h - 48, min(360, card_w - 190), "成熟度", "高", accent, start + 0.20, ratio=score)
    return filters, current, layer_num


def business_items_from_lines(rest):
    clean = [line for line in rest if not is_noise_line(line)]
    title_keywords = ("销售", "授权", "交易", "补贴", "来源")
    skip_keywords = ("预计", "测算", "净利润", "原料转化", "单价", "成本", "年收入")
    cards = []
    idx = 0
    while idx < len(clean) and len(cards) < 4:
        line = clean[idx]
        if is_card_heading(line) and any(key in line for key in title_keywords) and not any(key in line for key in skip_keywords):
            body_parts = []
            idx += 1
            while idx < len(clean):
                candidate = clean[idx]
                if is_card_heading(candidate) and any(key in candidate for key in title_keywords) and not any(key in candidate for key in skip_keywords):
                    break
                if visual_text_len(candidate) > 12 and not any(key in candidate for key in skip_keywords):
                    body_parts.append(candidate)
                idx += 1
                if len("".join(body_parts)) >= 58:
                    break
            title, subtitle = split_heading_subtitle(line)
            cards.append({"title": title, "subtitle": subtitle, "body": " ".join(body_parts)})
            continue
        idx += 1

    if len(cards) < 2:
        cards = content_cards_from_lines(clean, limit=4)

    metrics = [
        line for line in clean
        if re.search(r"\d", line) and visual_text_len(line) <= 18 and "1000" not in line
    ][:3]
    return cards[:4], metrics


def layout_adaptive_business(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards, metrics = business_items_from_lines(rest)
    if len(cards) < 2:
        return layout_adaptive_metrics(slide_data, slide_num, duration, project)

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data, opacity=0.12)
    current, layer_num = add_premium_header(filters, current, layer_num, "收入引擎", title, "用收入流向说明项目如何从回收规模转化为现金流。")

    hub_x, hub_y = 840, 560
    hub_title = concise_body(title, 6) or "核心"
    hub_subtitle = "结构拆解"
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (hub_x - 150, hub_y - 150, 300, 300), "#eaf7ec", "fill", 0.30)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (hub_x - 96, hub_y - 96, 192, 192), "#62ad6a@0.94", "fill", 0.44)
    current, layer_num = add_bounded_text(
        filters, current, layer_num, hub_title, x=hub_x - 72, y=hub_y - 50,
        width=144, height=42, max_font=32, min_font=22, color="white", bold=True, start=0.54
    )
    current, layer_num = add_bounded_text(
        filters, current, layer_num, hub_subtitle, x=hub_x - 76, y=hub_y + 8,
        width=152, height=34, max_font=26, min_font=18, color="white", bold=True, start=0.64
    )

    current, layer_num = add_premium_panel(filters, current, layer_num, (120, 390, 460, 430), "#62ad6a", 0.35, fill="#f4fbf6")
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "核心测算", x=160, y=444, font_size=28, color="#2f8b4b", bold=True, start=0.46)
    if metrics:
        for idx, metric in enumerate(metrics[:2]):
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, metric, x=160, y=520 + idx * 104, max_chars=12,
                max_lines=1, line_height=56, font_size=56 if visual_text_len(metric) <= 8 else 46,
                color="#07111f", bold=True, start=0.58 + idx * 0.25
            )
    else:
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, cards[0]["title"], x=160, y=520, max_chars=12,
            max_lines=2, line_height=54, font_size=48, color="#07111f", bold=True, start=0.55
        )
    current, layer_num = add_bounded_text(
        filters, current, layer_num, concise_body(cards[0].get("body", ""), 78), x=160, y=708,
        width=365, height=74, max_font=21, min_font=16, color="#5d6878", start=0.78
    )

    accents = ["#2375ff", "#62ad6a", "#f4a000", "#e1565a"]
    for idx, card in enumerate(cards[:4]):
        x, y = 1120, 350 + idx * 130
        start = 0.45 + idx * max(0.42, duration * 0.06)
        accent = accents[idx % len(accents)]
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (hub_x + 110, hub_y - 4, x - hub_x - 115, 5), f"{accent}@0.22", "fill", start + 0.05)
        current, layer_num = add_premium_panel(filters, current, layer_num, (x, y, 620, 118), accent, start)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1:02d}", x=x + 28, y=y + 30, font_size=26, color=accent, bold=True, start=start + 0.06)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card["title"], x=x + 84, y=y + 22,
            width=485, height=34, max_font=26, min_font=20, color="#07111f", bold=True, start=start + 0.08
        )
        current, layer_num = add_bounded_text(
            filters, current, layer_num, concise_body(card.get("body", ""), 72), x=x + 84, y=y + 60,
            width=500, height=44, max_font=19, min_font=15, color="#5d6878", start=start + 0.12
        )
    return filters, current, layer_num


def metric_items_from_lines(rest, limit=4):
    clean = [line for line in rest if not is_noise_line(line)]
    items = []

    def looks_metric(line):
        return bool(re.search(r"\d|%|亿|万|H&M|Patagonia|发改委|工信部|消费者", line))

    ordered_cards = ordered_point_cards(clean, limit=limit)
    if ordered_cards:
        return [
            {
                "title": card["title"],
                "metric": card.get("subtitle") or "",
                "body": card.get("body") or "",
            }
            for card in ordered_cards
        ][:limit]

    # Common exported-PPT pattern: heading, heading, metric, metric, body, body.
    for base in (0, 6):
        if len(clean) >= base + 6:
            if is_card_heading(clean[base]) and is_card_heading(clean[base + 1]) and (
                looks_metric(clean[base + 2]) or looks_metric(clean[base + 3])
            ):
                items.append({"title": clean[base], "metric": clean[base + 2], "body": clean[base + 4]})
                items.append({"title": clean[base + 1], "metric": clean[base + 3], "body": clean[base + 5]})

    if len(items) >= limit:
        return items[:limit]

    cards = build_alternating_cards(clean)
    items.extend(
        {"title": card["title"], "metric": card.get("subtitle") or "", "body": card.get("body") or ""}
        for card in cards
    )
    if len(items) >= limit:
        return items[:limit]

    headings = [line for line in clean if is_card_heading(line)]
    metrics = [line for line in clean if re.search(r"\d|%|亿|万|H&M|Patagonia", line)]
    bodies = [line for line in clean if visual_text_len(line) > 18]
    for idx, heading in enumerate(headings):
        if len(items) >= limit:
            break
        title, subtitle = split_heading_subtitle(heading)
        items.append({
            "title": title,
            "metric": metrics[idx] if idx < len(metrics) else subtitle,
            "body": bodies[idx] if idx < len(bodies) else "",
        })
    return items[:limit]


def layout_adaptive_metrics(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = metric_items_from_lines(rest, limit=4)
    if len(items) < 2:
        return layout_clean_cards(slide_data, slide_num, duration, project)

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_filter_visual_backdrop(filters, current, layer_num, project, slide_num, opacity=0.24, slide_data=slide_data)
    current, layer_num = add_adaptive_header(filters, current, layer_num, "数据看点", title, "把关键指标拆开看。")

    positions = [(120, 430), (1020, 430), (120, 720), (1020, 720)]
    for idx, item in enumerate(items[:4]):
        x, y = positions[idx]
        start = 0.35 + idx * max(0.5, duration * 0.08)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 8, y + 8, 760, 210), "black@0.025", "fill", start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 760, 210), "white@0.98", "fill", start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 8, 210), "#62ad6a", "fill", start)
        metric = normalize_video_text(item.get("metric", ""))
        if is_ordered_point_label(metric):
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, metric, x=x + 34, y=y + 28, max_chars=18,
                max_lines=1, line_height=36, font_size=34, color="#62ad6a", bold=True, start=start
            )
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, item["title"], x=x + 34, y=y + 82, max_chars=18,
                max_lines=2, line_height=34, font_size=30, color="#070b1d", bold=True, start=start
            )
            body_y = y + 154
        else:
            current, layer_num = add_wrapped_text(
                filters, current, layer_num, item["title"], x=x + 34, y=y + 28, max_chars=16,
                max_lines=1, line_height=34, font_size=30, color="#070b1d", bold=True, start=start
            )
            if metric:
                current, layer_num = add_wrapped_text(
                    filters, current, layer_num, metric, x=x + 34, y=y + 78, max_chars=18,
                    max_lines=1, line_height=36, font_size=34, color="#62ad6a", bold=True, start=start
                )
            body_y = y + 128
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, item.get("body", ""), x=x + 34, y=body_y, max_chars=32,
            max_lines=2, line_height=28, font_size=22, color="#5d6878", start=start
        )
    return filters, current, layer_num


def layout_adaptive_market(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = metric_items_from_lines(rest, limit=4)
    if len(items) < 2:
        return layout_adaptive_metrics(slide_data, slide_num, duration, project)

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data, opacity=0.12)
    current, layer_num = add_premium_header(filters, current, layer_num, "知识仪表盘", title, "用定义、规则、例题和应用线索梳理这一页。")

    hero = next((item for item in items if item.get("metric") and re.search(r"\d|%|H&M|Patagonia", item.get("metric", ""))), items[0])
    current, layer_num = add_round_panel(filters, current, layer_num, (120, 360, 690, 510), "#62ad6a", "#f4fbf6@0.98", 0.28, radius=46)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (166, 412, 160, 44), "#62ad6a@0.16", 22, 0.38)
    current, layer_num = add_filter_circle(filters, current, layer_num, 190, 434, 6, "#62ad6a", 0.40)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "关键要点", x=208, y=421, font_size=22, color="#2f8b4b", bold=True, start=0.42)
    hero_metric = normalize_video_text(hero.get("metric") or hero.get("title") or "")
    hero_title = normalize_video_text(hero.get("title") or "")
    current, layer_num = add_fitting_wrapped_text_in_box(
        filters, current, layer_num, hero_metric, x=166, y=485, width=585, height=112,
        max_font=62, min_font=38, color="#07111f", bold=True, start=0.55, safety=0.90
    )[:2]
    current, layer_num = add_bounded_text(
        filters, current, layer_num, hero_title, x=166, y=620,
        width=565, height=42, max_font=27, min_font=21, color="#465160", bold=True, start=0.70
    )
    bars = [("理解", "高", 0.86), ("方法", "强", 0.78), ("应用", "升", 0.66)]
    for idx, (label, value, ratio) in enumerate(bars):
        current, layer_num = add_metric_capsule_bar(
            filters, current, layer_num, 166, 695 + idx * 58, 565, label, value, "#62ad6a",
            start=0.82 + idx * 0.10, ratio=ratio
        )

    signal_items = [item for item in items if item is not hero] or items[1:]
    while len(signal_items) < 3 and len(signal_items) < len(items):
        signal_items.append(items[len(signal_items)])
    positions = [(880, 365), (880, 545), (880, 725)]
    accents = ["#2375ff", "#f4a000", "#e1565a"]
    for idx, item in enumerate(signal_items[:3]):
        x, y = positions[idx]
        accent = accents[idx]
        start = 0.55 + idx * max(0.5, duration * 0.08)
        current, layer_num = add_round_panel(filters, current, layer_num, (x, y, 840, 145), accent, "white@0.94", start, radius=36)
        current, layer_num = add_soft_circle_token(filters, current, layer_num, x + 65, y + 72, 38, accent, f"S{idx + 1}", start + 0.06, fill=f"{accent}@0.10")
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("title", ""), x=x + 125, y=y + 30,
            width=380, height=42, max_font=28, min_font=22, color="#07111f", bold=True, start=start + 0.10
        )
        metric = normalize_video_text(item.get("metric", ""))
        if metric:
            current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 520, y + 28, 270, 44), f"{accent}@0.12", 22, start + 0.10)
            current, layer_num = add_bounded_text(
                filters, current, layer_num, metric, x=x + 545, y=y + 37,
                width=220, height=25, max_font=23, min_font=15, color=accent, bold=True, start=start + 0.14
            )
        current, layer_num = add_bounded_text(
            filters, current, layer_num, concise_body(item.get("body", ""), 64), x=x + 125, y=y + 82,
            width=640, height=34, max_font=21, min_font=16, color="#5d6878", start=start + 0.16
        )
    return filters, current, layer_num


def layout_adaptive_team(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=4)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project)["cards"]

    filters = [f"color=c=#fbfcfb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_premium_canvas(filters, current, layer_num, project, slide_num, slide_data, opacity=0.10)
    current, layer_num = add_premium_header(filters, current, layer_num, "团队配置", title, "按角色、经验和执行能力展示团队组合。")
    current, layer_num = add_visual_panel(
        filters, current, layer_num, project, slide_num, slide_data, (1230, 330, 500, 500),
        "组织能力 · 产业协同", start=0.28
    )
    positions = [(120, 382), (640, 382), (120, 662), (640, 662)]
    accents = ["#2375ff", "#62ad6a", "#f4a000", "#e1565a"]
    for idx, card in enumerate(cards[:4]):
        x, y = positions[idx]
        accent = accents[idx % len(accents)]
        start = 0.35 + idx * max(0.5, duration * 0.08)
        current, layer_num = add_premium_panel(filters, current, layer_num, (x, y, 455, 205), accent, start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 30, y + 38, 82, 82), f"{accent}@0.14", "fill", start + 0.06)
        initials = "CEO" if "CEO" in card["title"] else ("CTO" if "CTO" in card["title"] else f"T{idx + 1}")
        current, layer_num = add_filter_drawtext(filters, current, layer_num, initials, x=x + 44, y=y + 64, font_size=26, color=accent, bold=True, start=start + 0.10)
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, card["title"], x=x + 132, y=y + 32, max_chars=15, max_lines=2,
            line_height=31, font_size=27, color="#07111f", bold=True, start=start + 0.12
        )
        current, layer_num = add_wrapped_text(
            filters, current, layer_num, concise_body(card.get("body", ""), 42), x=x + 32, y=y + 126, max_chars=24, max_lines=2,
            line_height=26, font_size=20, color="#5d6878", start=start + 0.16
        )
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 132, y + 100, 112, 26), f"{accent}@0.12", "fill", start + 0.18)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, "核心角色", x=x + 148, y=y + 104, font_size=18, color=accent, bold=True, start=start + 0.20)
    return filters, current, layer_num


def layout_adaptive(slide_data, slide_num, duration, project=None, recommendation=None):
    component = selected_visual_component(recommendation)
    if component == "lifecycle_loop":
        return layout_adaptive_lifecycle(slide_data, slide_num, duration, project)
    if component in {"roadmap_timeline", "timeline"}:
        return layout_adaptive_roadmap(slide_data, slide_num, duration, project)
    if component == "market_dashboard":
        return layout_adaptive_market(slide_data, slide_num, duration, project)
    kind = adaptive_layout_kind(slide_data, slide_num, project, recommendation)
    if kind == "hero":
        return layout_adaptive_hero(slide_data, slide_num, duration, project)
    if kind == "problem":
        return layout_adaptive_problem(slide_data, slide_num, duration, project)
    if kind == "matrix":
        title, rest = slide_context(slide_data, slide_num, project)
        text = title + " " + " ".join(rest)
        if any(keyword in text for keyword in ("进制", "编码", "二进制", "十进制")) and len(comparison_groups_from_lines(rest, limit=2)) >= 2:
            return layout_adaptive_comparison(slide_data, slide_num, duration, project)
        return layout_adaptive_matrix(slide_data, slide_num, duration, project)
    if kind == "business":
        return layout_adaptive_business(slide_data, slide_num, duration, project)
    if kind == "process":
        return layout_adaptive_process(slide_data, slide_num, duration, project)
    if kind == "metrics":
        return layout_adaptive_metrics(slide_data, slide_num, duration, project)
    if kind == "team":
        return layout_adaptive_team(slide_data, slide_num, duration, project)
    return layout_clean_cards(slide_data, slide_num, duration, project)


def layout_diverse_incident_board(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=4)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:4]
    cards = augment_teaching_cards(title, rest, cards, limit=4)
    filters = [f"color=c=#0f172a:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_dark_canvas(filters, current, layer_num, project, slide_num, slide_data, "#ef4444")
    current, layer_num = add_micro_label(filters, current, layer_num, "RISK MAP", 120, 92, "#ef4444", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=160, width=1040, height=130, color="#f8fafc", start=0.22, max_font=58, min_font=40)
    if has_slide_visual(project, slide_num, slide_data):
        current, layer_num = add_filter_visual_cover(filters, current, layer_num, box=(1230, 120, 520, 300), opacity=0.95, start=0.30)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (1230, 120, 520, 300), "#ef4444@0.55", 4, 0.36)
    positions = [(120, 410), (575, 365), (1030, 475), (1485, 430)]
    heights = [380, 300, 360, 320]
    accents = ["#ef4444", "#f97316", "#eab308", "#38bdf8"]
    for idx, card in enumerate(cards[:4]):
        x, y = positions[idx]
        h = heights[idx]
        accent = accents[idx]
        start = start_step(duration, idx, 0.42, 0.07)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 360, h), "#ffffff@0.08", "fill", start)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x, y, 360, 7), accent, "fill", start + 0.04)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x + 24, y + 30, 62, 62), f"{accent}@0.18", "fill", start + 0.08)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, metric_token(str(card)) or f"{idx + 1:02d}", x=x + 36, y=y + 48, font_size=25, color=accent, bold=True, start=start + 0.10)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card.get("title", ""), x=x + 24, y=y + 116,
            width=312, height=72, max_font=28, min_font=20, color="#f8fafc", bold=True, start=start + 0.14
        )
        body = concise_body((card.get("subtitle") or "") + " " + (card.get("body") or ""), 48)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, body, x=x + 24, y=y + 202,
            width=312, height=max(78, h - 228), max_font=21, min_font=16, color="#cbd5e1", start=start + 0.18
        )
    current, layer_num = add_compact_enrichment_panel(
        filters, current, layer_num, title, rest, cards,
        x=1215, y=810, w=560, h=150, accent="#38bdf8", start=0.86, dark=True, label="补充"
    )
    return filters, current, layer_num


def layout_diverse_orbit_loop(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = lifecycle_items_from_lines(rest)
    if len(items) < 3:
        items = process_items_from_lines(title, rest)
    filters = [f"color=c=#f8fafc:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#10b981")
    current, layer_num = add_micro_label(filters, current, layer_num, "ORBIT LOOP", 120, 88, "#10b981", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=150, width=1160, height=120, color="#07111f", start=0.22, max_font=58, min_font=40)
    cx, cy = 960, 620
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (cx - 250, cy - 250, 500, 500), "#d1fae5", "fill", 0.25)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (cx - 145, cy - 145, 290, 290), "white@0.94", "fill", 0.35)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (cx - 86, cy - 86, 172, 172), "#10b981", "fill", 0.48)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "LOOP", x=cx - 55, y=cy - 20, font_size=44, color="white", bold=True, start=0.56)
    positions = [(430, 390), (845, 330), (1290, 405), (1230, 785), (555, 790)]
    accents = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"]
    for idx, item in enumerate(items[:5]):
        x, y = positions[idx]
        accent = accents[idx]
        start = start_step(duration, idx, 0.48, 0.07)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (min(cx, x + 140), min(cy, y + 66), abs(cx - x - 140) + 8, 5), f"{accent}@0.28", "fill", start)
        current, layer_num = add_glass_box(filters, current, layer_num, (x, y, 300, 132), accent, "white@0.94", start)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1:02d}", x=x + 24, y=y + 32, font_size=26, color=accent, bold=True, start=start + 0.04)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("title", ""), x=x + 82, y=y + 26,
            width=190, height=76, max_font=25, min_font=18, color="#07111f", bold=True, start=start + 0.08
        )
    current, layer_num = add_compact_enrichment_panel(
        filters, current, layer_num, title, rest, cards_from_items(items),
        x=1335, y=138, w=420, h=178, accent="#10b981", start=0.82, label="速记"
    )
    return filters, current, layer_num


def layout_diverse_split_manifesto(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=3)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:3]
    filters = [f"color=c=#fff7ed:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#f59e0b")
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, 720, VIDEO_H), "#111827", "fill", 0.0)
    current, layer_num = add_micro_label(filters, current, layer_num, "WHY DIFFERENT", 120, 100, "#f59e0b", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=188, width=560, height=250, color="#f8fafc", start=0.24, max_font=48, min_font=34)
    current, layer_num = add_body_line(filters, current, layer_num, concise_body(" ".join([c.get("body", "") for c in cards]), 52), x=126, y=520, width=500, max_lines=3, font_size=25, color="#fed7aa", start=0.52)
    for idx, card in enumerate(cards[:3]):
        x = 820 + idx * 330
        y = 300 + (idx % 2) * 115
        h = 420 if idx == 1 else 350
        start = start_step(duration, idx, 0.42, 0.08)
        accent = ["#10b981", "#f59e0b", "#3b82f6"][idx]
        current, layer_num = add_glass_box(filters, current, layer_num, (x, y, 285, h), accent, "white@0.94", start)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"0{idx + 1}", x=x + 28, y=y + 36, font_size=32, color=accent, bold=True, start=start + 0.06)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card.get("title", ""), x=x + 28, y=y + 108,
            width=225, height=104, max_font=27, min_font=21, color="#111827", bold=True, start=start + 0.12
        )
        current, layer_num = add_bounded_text(
            filters, current, layer_num, concise_body(card.get("body", ""), 70), x=x + 28, y=y + 226,
            width=225, height=max(74, h - 252), max_font=20, min_font=16, color="#64748b", start=start + 0.18
        )
    current, layer_num = add_compact_enrichment_panel(
        filters, current, layer_num, title, rest, cards,
        x=110, y=675, w=520, h=220, accent="#f59e0b", start=0.84, dark=True, label="延展"
    )
    return filters, current, layer_num


def layout_diverse_dashboard(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = metric_items_from_lines(rest, limit=4)
    cards = cards_from_items(items)
    filters = [f"color=c=#f8fafc:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#3b82f6")
    current, layer_num = add_micro_label(filters, current, layer_num, "KNOWLEDGE DASHBOARD", 120, 82, "#3b82f6", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=145, width=1150, height=125, color="#07111f", start=0.22, max_font=56, min_font=38)
    hero = items[0] if items else {"title": "关键知识点", "metric": "", "body": ""}
    current, layer_num = add_round_panel(filters, current, layer_num, (120, 345, 660, 505), "#3b82f6", "white@0.94", 0.32, radius=46)
    metric = hero.get("metric") or metric_token(str(hero)) or "01"
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (165, 398, 155, 42), "#3b82f6@0.14", 21, 0.38)
    current, layer_num = add_filter_circle(filters, current, layer_num, 188, 419, 6, "#3b82f6", 0.40)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "关键要点", x=207, y=407, font_size=21, color="#3b82f6", bold=True, start=0.42)
    current, layer_num = add_fitting_wrapped_text_in_box(filters, current, layer_num, metric, x=165, y=470, width=545, height=105, max_font=62, min_font=38, color="#07111f", bold=True, start=0.55, safety=0.88)[:2]
    current, layer_num = add_body_line(filters, current, layer_num, hero.get("title", ""), x=165, y=600, width=535, max_lines=2, font_size=25, color="#475569", start=0.72)
    left_bars = [("理解", "高", 0.84), ("方法", "强", 0.76), ("应用", "升", 0.68)]
    for idx, (label, value, ratio) in enumerate(left_bars):
        current, layer_num = add_metric_capsule_bar(
            filters, current, layer_num, 165, 690 + idx * 56, 540, label, value, "#3b82f6",
            start=0.84 + idx * 0.08, ratio=ratio
        )
    bar_data = items[1:4] if len(items) > 1 else [{"title": "定义理解"}, {"title": "规则掌握"}, {"title": "题型应用"}]
    for idx, item in enumerate(bar_data[:3]):
        y = 375 + idx * 145
        ratio = 0.84 - idx * 0.14
        start = start_step(duration, idx, 0.48, 0.07)
        current, layer_num = add_round_panel(filters, current, layer_num, (850, y, 820, 108), "#10b981", "white@0.90", start, radius=34, shadow=True, border=False)
        current, layer_num = add_soft_circle_token(filters, current, layer_num, 904, y + 54, 31, "#10b981", f"{idx + 1}", start + 0.04, fill="#ecfdf5")
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("title", f"Signal {idx + 1}"), x=960, y=y + 24,
            width=300, height=40, max_font=25, min_font=18, color="#07111f", bold=True, start=start + 0.06
        )
        metric = normalize_video_text(item.get("metric", ""))
        if metric:
            current, layer_num = add_filter_roundrect(filters, current, layer_num, (1320, y + 22, 260, 42), "#3b82f6@0.12", 21, start + 0.08)
            current, layer_num = add_bounded_text(
                filters, current, layer_num, metric, x=1345, y=y + 31,
                width=210, height=24, max_font=22, min_font=16, color="#3b82f6", bold=True, start=start + 0.12
            )
        current, layer_num = add_metric_capsule_bar(
            filters, current, layer_num, 960, y + 70, 560, "掌握度", ["高", "强", "升"][idx], "#3b82f6",
            start=start + 0.14, ratio=ratio
        )
    current, layer_num = add_compact_enrichment_panel(
        filters, current, layer_num, title, rest, cards,
        x=1285, y=142, w=390, h=172, accent="#3b82f6", start=0.80, label="补充"
    )
    return filters, current, layer_num


def layout_diverse_flow_network(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=4)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:4]
    filters = [f"color=c=#ecfeff:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#14b8a6")
    current, layer_num = add_micro_label(filters, current, layer_num, "FLOW NETWORK", 120, 90, "#14b8a6", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=150, width=1050, height=120, color="#07111f", start=0.22, max_font=56, min_font=38)
    hub_x, hub_y = 930, 620
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (hub_x - 125, hub_y - 125, 250, 250), "#14b8a6", "fill", 0.35)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "VALUE", x=hub_x - 70, y=hub_y - 28, font_size=42, color="white", bold=True, start=0.48)
    nodes = [(220, 395), (1370, 370), (1370, 735), (220, 735)]
    accents = ["#3b82f6", "#f59e0b", "#ef4444", "#10b981"]
    for idx, card in enumerate(cards[:4]):
        x, y = nodes[idx]
        accent = accents[idx]
        start = start_step(duration, idx, 0.46, 0.07)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (min(hub_x, x + 170), min(hub_y, y + 70), abs(hub_x - x - 170) + 8, 6), f"{accent}@0.36", "fill", start)
        current, layer_num = add_glass_box(filters, current, layer_num, (x, y, 360, 150), accent, "white@0.95", start)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1:02d}", x=x + 28, y=y + 36, font_size=27, color=accent, bold=True, start=start + 0.04)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card.get("title", ""), x=x + 88, y=y + 28,
            width=240, height=62, max_font=25, min_font=18, color="#07111f", bold=True, start=start + 0.08
        )
        current, layer_num = add_bounded_text(
            filters, current, layer_num, concise_body(card.get("body", ""), 32), x=x + 28, y=y + 98,
            width=305, height=28, max_font=20, min_font=15, color="#475569", start=start + 0.14
        )
    current, layer_num = add_compact_enrichment_panel(
        filters, current, layer_num, title, rest, cards,
        x=1240, y=130, w=470, h=180, accent="#14b8a6", start=0.82, label="线索"
    )
    return filters, current, layer_num


def layout_diverse_soft_bubbles(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=5)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:5]
    filters = [f"color=c=#f8fafc:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#06b6d4")
    current, layer_num = add_micro_label(filters, current, layer_num, "SOFT CLUSTER", 120, 88, "#06b6d4", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=150, width=920, height=130, color="#07111f", start=0.22, max_font=58, min_font=38)
    current, layer_num = add_body_line(filters, current, layer_num, concise_body(" ".join([c.get("body", "") for c in cards]), 48), x=125, y=305, width=760, max_lines=2, font_size=25, color="#64748b", start=0.48)

    current, layer_num = add_left_enrichment_panel(
        filters, current, layer_num, title, rest, cards,
        x=120, y=425, w=690, h=382, accent="#06b6d4", start=0.68
    )

    accents = ["#06b6d4", "#8b5cf6", "#f59e0b", "#10b981", "#ef4444"]
    circles = [(1265, 500, 245), (1040, 670, 175), (1488, 710, 155), (1432, 330, 128), (1120, 350, 105)]
    for idx, (cx, cy, r) in enumerate(circles):
        current, layer_num = add_filter_circle(filters, current, layer_num, cx, cy, r, f"{accents[idx]}@0.10", 0.20 + idx * 0.05)

    positions = [(955, 310, 380, 160), (1230, 450, 430, 180), (900, 620, 390, 170), (1325, 710, 360, 150), (1115, 790, 330, 130)]
    for idx, card in enumerate(cards[:5]):
        x, y, w, h = positions[idx]
        accent = accents[idx % len(accents)]
        start = start_step(duration, idx, 0.42, 0.07)
        current, layer_num = add_round_panel(filters, current, layer_num, (x, y, w, h), accent, "white@0.82", start, radius=64, shadow=True, border=True)
        current, layer_num = add_soft_circle_token(filters, current, layer_num, x + 58, y + 58, 34, accent, f"{idx + 1}", start + 0.04, fill=f"{accent}@0.12")
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card.get("title", ""), x=x + 112, y=y + 36,
            width=w - 145, height=58, max_font=26, min_font=18, color="#07111f", bold=True, start=start + 0.10
        )
        body = concise_body((card.get("subtitle") or "") + " " + (card.get("body") or ""), 52)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, body, x=x + 42, y=y + 100,
            width=w - 80, height=h - 114, max_font=20, min_font=15, color="#475569", start=start + 0.16
        )
    return filters, current, layer_num


def layout_diverse_capsule_flow(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = process_items_from_lines(title, rest)
    if len(items) < 3:
        _, cards = diverse_cards(slide_data, slide_num, project, limit=4)
        items = [{"title": c.get("title", ""), "body": c.get("body", "")} for c in cards]
    filters = [f"color=c=#fff7ed:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#fb923c")
    current, layer_num = add_micro_label(filters, current, layer_num, "CAPSULE FLOW", 120, 88, "#fb923c", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=150, width=1200, height=130, color="#111827", start=0.22, max_font=58, min_font=38)

    current, layer_num = add_filter_roundrect(filters, current, layer_num, (240, 555, 1440, 22), "#fed7aa", 11, 0.32)
    count = min(5, max(1, len(items)))
    anchors = [(295, 500), (625, 620), (960, 500), (1290, 620), (1585, 500)]
    accents = ["#fb923c", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6"]
    for idx, item in enumerate(items[:count]):
        cx, cy = anchors[idx]
        accent = accents[idx]
        start = start_step(duration, idx, 0.42, 0.08)
        current, layer_num = add_soft_circle_token(filters, current, layer_num, cx, 566, 48, accent, f"{idx + 1}", start, fill=accent, text_color="#111827")
        pill_w = 300 if idx % 2 == 0 else 360
        pill_h = 128 if idx % 2 == 0 else 145
        x = max(80, min(VIDEO_W - pill_w - 80, cx - pill_w // 2))
        y = cy - pill_h // 2
        current, layer_num = add_round_panel(filters, current, layer_num, (x, y, pill_w, pill_h), accent, "white@0.92", start + 0.04, radius=60, shadow=True, border=True)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("title", ""), x=x + 34, y=y + 28,
            width=pill_w - 68, height=52, max_font=24, min_font=17, color="#111827", bold=True, start=start + 0.10
        )
        if item.get("body"):
            current, layer_num = add_bounded_text(
                filters, current, layer_num, concise_body(item.get("body", ""), 34), x=x + 34, y=y + 82,
                width=pill_w - 68, height=pill_h - 92, max_font=18, min_font=14, color="#64748b", start=start + 0.16
            )
    current, layer_num = add_compact_enrichment_panel(
        filters, current, layer_num, title, rest, cards_from_items(items),
        x=1325, y=140, w=430, h=166, accent="#fb923c", start=0.82, label="提示"
    )
    return filters, current, layer_num


def layout_diverse_roster(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=4)
    if not cards:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:4]
    if len(cards) < 4:
        cards.extend(
            {"title": f"要点 {idx + 1}", "body": ""}
            for idx in range(len(cards), 4)
        )
    filters = [f"color=c=#f8fafc:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#8b5cf6")
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, 500, VIDEO_H), "#312e81", "fill", 0.0)
    current, layer_num = add_micro_label(filters, current, layer_num, "CONTENT SYSTEM", 120, 95, "#c4b5fd", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=180, width=320, height=280, color="#f8fafc", start=0.24, max_font=44, min_font=32)
    current, layer_num = add_body_line(filters, current, layer_num, "把多个要点组织成可扫读的内容模块。", x=125, y=555, width=300, max_lines=4, font_size=24, color="#ddd6fe", start=0.52)
    positions = [(570, 225), (1020, 225), (570, 590), (1020, 590)]
    accents = ["#8b5cf6", "#10b981", "#f59e0b", "#3b82f6"]
    for idx, card in enumerate(cards[:4]):
        x, y = positions[idx]
        accent = accents[idx]
        start = start_step(duration, idx, 0.40, 0.08)
        current, layer_num = add_glass_box(filters, current, layer_num, (x, y, 390, 275), accent, "white@0.94", start)
        initials = metric_token(card.get("title", "")) or f"{idx + 1:02d}"
        current, layer_num = add_soft_circle_token(filters, current, layer_num, x + 71, y + 75, 46, accent, initials, start + 0.06, fill=f"{accent}@0.11")
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card.get("title", ""), x=x + 132, y=y + 36,
            width=225, height=72, max_font=25, min_font=19, color="#07111f", bold=True, start=start + 0.12
        )
        current, layer_num = add_bounded_text(
            filters, current, layer_num, concise_body(card.get("body", ""), 86), x=x + 32, y=y + 142,
            width=320, height=105, max_font=20, min_font=16, color="#475569", start=start + 0.18
        )
    current, layer_num = add_compact_enrichment_panel(
        filters, current, layer_num, title, rest, cards,
        x=88, y=690, w=350, h=185, accent="#c4b5fd", start=0.82, dark=True, label="补充"
    )
    return filters, current, layer_num


def layout_diverse_subway_roadmap(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = process_items_from_lines(title, rest)
    filters = [f"color=c=#0b1220:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_dark_canvas(filters, current, layer_num, project, slide_num, slide_data, "#84cc16")
    current, layer_num = add_micro_label(filters, current, layer_num, "EXPANSION MAP", 120, 92, "#84cc16", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=155, width=1180, height=120, color="#f8fafc", start=0.22, max_font=56, min_font=38)
    start_x, start_y = 220, 620
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (start_x, start_y, 1460, 12), "#334155", "fill", 0.28)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (start_x, start_y, 1080, 12), "#84cc16", "fill", 0.48)
    count = min(5, max(1, len(items)))
    step = 1460 // max(1, count - 1)
    colors = ["#84cc16", "#22c55e", "#3b82f6", "#f59e0b", "#ef4444"]
    for idx, item in enumerate(items[:count]):
        x = start_x + idx * step if count > 1 else start_x + 720
        x = min(1680, x)
        start = start_step(duration, idx, 0.45, 0.075)
        accent = colors[idx]
        current, layer_num = add_soft_circle_token(filters, current, layer_num, x, start_y + 6, 40, accent, f"{idx + 1}", start, fill=accent, text_color="#07111f")
        card_y = 365 if idx % 2 == 0 else 720
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (x - 2, min(card_y + 145, start_y), 4, abs(start_y - card_y - 145)), f"{accent}@0.50", "fill", start + 0.06)
        card_x = max(80, min(1580, x - 155))
        current, layer_num = add_glass_box(filters, current, layer_num, (card_x, card_y, 310, 145), accent, "#ffffff@0.10", start)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, item.get("title", ""), x=card_x + 25, y=card_y + 32,
            width=260, height=58, max_font=24, min_font=18, color="#f8fafc", bold=True, start=start + 0.10
        )
        if item.get("body"):
            current, layer_num = add_bounded_text(
                filters, current, layer_num, concise_body(item.get("body"), 34), x=card_x + 25, y=card_y + 92,
                width=260, height=30, max_font=18, min_font=15, color="#cbd5e1", start=start + 0.14
            )
    current, layer_num = add_compact_enrichment_panel(
        filters, current, layer_num, title, rest, cards_from_items(items),
        x=1335, y=116, w=430, h=172, accent="#84cc16", start=0.84, dark=True, label="提示"
    )
    return filters, current, layer_num


def layout_diverse_blackboard_derivation(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=4)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:4]
    filters = [f"color=c=#07140f:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, VIDEO_H), "#07140f", "fill", 0.0)
    current, layer_num = add_dot_grid(filters, current, layer_num, "#d1fae5", 0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (72, 72, 1776, 830), "#0f2a1e@0.92", "fill", 0.0)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (72, 72, 1776, 830), "#84cc16@0.55", 5, 0.0)
    current, layer_num = add_micro_label(filters, current, layer_num, "\u63a8\u5bfc\u9ed1\u677f", 128, 112, "#84cc16", 0.12)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=128, y=178, width=890, height=128, color="#f7fee7", start=0.24, max_font=58, min_font=38)

    focus = rest[0] if rest else title
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (1080, 145, 610, 190), "#ecfccb@0.10", 36, 0.34)
    current, layer_num = add_bounded_text(
        filters, current, layer_num, focus, x=1124, y=190,
        width=520, height=92, max_font=38, min_font=24, color="#ecfccb", bold=True, start=0.44
    )
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (1125, 303, 488, 4), "#84cc16@0.80", "fill", 0.58)

    positions = [(130, 390), (590, 355), (1050, 400), (1430, 610)]
    accents = ["#bef264", "#67e8f9", "#fde68a", "#fca5a5"]
    for idx, card in enumerate(cards[:4]):
        x, y = positions[idx]
        w = 390 if idx < 3 else 310
        h = 180 if idx < 3 else 170
        accent = accents[idx % len(accents)]
        start = start_step(duration, idx, 0.42, 0.08)
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, w, h), "#ffffff@0.06", 28, start)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"{idx + 1}", x=x + 28, y=y + 28, font_size=34, color=accent, bold=True, start=start + 0.04)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card.get("title", ""), x=x + 76, y=y + 28,
            width=w - 105, height=58, max_font=25, min_font=18, color="#f8fafc", bold=True, start=start + 0.08
        )
        body = concise_body((card.get("subtitle") or "") + " " + (card.get("body") or ""), 58)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, body, x=x + 28, y=y + 100,
            width=w - 58, height=h - 118, max_font=20, min_font=15, color="#d1fae5", start=start + 0.16
        )
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "\u63d0\u793a", x=128, y=828, font_size=24, color="#84cc16", bold=True, start=0.86)
    note = concise_body(" ".join(rest[1:]), 46) if len(rest) > 1 else "把符号、条件和结论分开看，推导会更清楚。"
    current, layer_num = add_bounded_text(filters, current, layer_num, note, x=220, y=826, width=1180, height=36, max_font=24, min_font=18, color="#d9f99d", start=0.9)
    return filters, current, layer_num


def layout_diverse_formula_walkthrough(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    items = formula_walkthrough_cards(title, rest, limit=4)
    filters = [f"color=c=#f8fafc:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#2563eb")
    current, layer_num = add_micro_label(filters, current, layer_num, "\u5206\u6b65\u8bb2\u89e3", 120, 88, "#2563eb", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=150, width=1120, height=126, color="#07111f", start=0.22, max_font=56, min_font=38)
    current, layer_num = add_round_panel(filters, current, layer_num, (1280, 128, 460, 260), "#2563eb", "white@0.92", 0.32, radius=42, shadow=True, border=True)
    example_lines = formula_example_lines(rest, items, title, limit=3)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "\u4f8b\u9898", x=1322, y=170, font_size=22, color="#2563eb", bold=True, start=0.38)
    for example_idx, example in enumerate(example_lines):
        current, layer_num = add_bounded_text(
            filters, current, layer_num, example, x=1322, y=216 + example_idx * 52,
            width=370, height=42, max_font=22, min_font=17, color="#111827",
            bold=example_idx == 0, start=0.48 + example_idx * 0.06
        )

    rail_x = 190
    rail_y = 420
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (rail_x, rail_y + 48, 1480, 18), "#dbeafe", 9, 0.30)
    count = min(4, max(1, len(items)))
    gap = 1480 // max(1, count - 1)
    accents = ["#2563eb", "#10b981", "#f59e0b", "#8b5cf6"]
    for idx, item in enumerate(items[:count]):
        cx = rail_x + idx * gap if count > 1 else rail_x + 720
        start = start_step(duration, idx, 0.44, 0.08)
        accent = accents[idx % len(accents)]
        current, layer_num = add_soft_circle_token(filters, current, layer_num, cx, rail_y + 57, 44, accent, f"{idx + 1}", start, fill=accent, text_color="white")
        card_w = 360 if count >= 4 else 390
        card_x = max(80, min(VIDEO_W - card_w - 80, cx - card_w // 2))
        card_y = 530 if idx % 2 == 0 else 665
        card_h = 188
        current, layer_num = add_round_panel(filters, current, layer_num, (card_x, card_y, card_w, card_h), accent, "white@0.94", start + 0.04, radius=36, shadow=True, border=True)
        formula_title = normalize_video_text(item.get("title", ""))
        formula_body = formula_card_body_summary(formula_title, item.get("body", ""), 42)
        title_h = 56 if formula_body else 100
        current, layer_num = add_bounded_text(
            filters, current, layer_num, formula_title, x=card_x + 28, y=card_y + 28,
            width=card_w - 56, height=title_h, max_font=20, min_font=17,
            color="#07111f", bold=True, start=start + 0.10
        )
        if formula_body:
            current, layer_num = add_bounded_text(
                filters, current, layer_num, formula_body, x=card_x + 28, y=card_y + 88,
                width=card_w - 56, height=78, max_font=16, min_font=15,
                color="#64748b", start=start + 0.16
            )
    return filters, current, layer_num


def layout_diverse_checkpoint_ladder(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=6)
    if len(cards) < 3:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:6]
    if not cards:
        cards = [{"title": title or "Checkpoint", "body": ""}]

    filters = [f"color=c=#f6f7fb:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#7c3aed")
    current, layer_num = add_micro_label(filters, current, layer_num, "\u68c0\u67e5\u9636\u68af", 120, 88, "#7c3aed", 0.1)
    current, layer_num = add_large_title(
        filters, current, layer_num, title, x=120, y=148, width=1180, height=130,
        color="#111827", start=0.22, max_font=56, min_font=38
    )

    summary = "\u5148\u770b\u5206\u7c7b\u6807\u51c6\uff0c\u518d\u628a\u5177\u4f53\u6839\u5f0f\u653e\u56de\u5bf9\u5e94\u7c7b\u578b\u3002"
    current, layer_num = add_round_panel(filters, current, layer_num, (1350, 112, 420, 198), "#7c3aed", "white@0.93", 0.28, radius=48, shadow=True, border=True)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "\u6293\u4f4f\u5173\u952e", x=1390, y=154, font_size=22, color="#7c3aed", bold=True, start=0.34)
    current, layer_num = add_bounded_text(filters, current, layer_num, summary, x=1390, y=200, width=340, height=70, max_font=24, min_font=17, color="#334155", start=0.42)

    ladder_x = 180
    rail_x = 315
    count = min(4, len(cards))
    top_y = 340
    row_gap = 138
    row_h = 118
    current, layer_num = add_filter_roundrect(
        filters, current, layer_num,
        (rail_x, top_y + 36, 18, row_gap * max(1, count - 1) + row_h - 14),
        "#ddd6fe", 9, 0.30
    )
    accents = ["#7c3aed", "#2563eb", "#0f766e", "#f59e0b", "#ef4444", "#0891b2"]
    for idx, card in enumerate(cards[:count]):
        y = top_y + idx * row_gap
        start = start_step(duration, idx, 0.42, 0.07)
        accent = accents[idx % len(accents)]
        current, layer_num = add_soft_circle_token(
            filters, current, layer_num, rail_x + 9, y + 54, 34,
            accent, f"{idx + 1}", start, fill="white@0.95"
        )
        box_x = ladder_x + (34 if idx % 2 else 0)
        box_w = 1360 if idx % 2 else 1410
        panel_x = box_x + 170
        current, layer_num = add_round_panel(
            filters, current, layer_num, (panel_x, y, box_w, row_h),
            accent, "white@0.92", start + 0.04, radius=40, shadow=True, border=True
        )
        card_title = normalize_video_text(card.get("title", ""))
        body = concise_body((card.get("subtitle") or "") + " " + (card.get("body") or ""), 112)
        text_y = y + 18
        if body:
            current, layer_num = add_bounded_text(
                filters, current, layer_num, card_title, x=panel_x + 44, y=text_y,
                width=box_w - 88, height=34, max_font=23, min_font=17,
                color="#111827", bold=True, start=start + 0.10
            )
            current, layer_num = add_bounded_text(
                filters, current, layer_num, body, x=panel_x + 44, y=y + 56,
                width=box_w - 88, height=46, max_font=18,
                min_font=14, color="#64748b", start=start + 0.16, safety=0.82
            )
        else:
            current, layer_num = add_bounded_text(
                filters, current, layer_num, card_title, x=panel_x + 44, y=text_y,
                width=box_w - 88, height=row_h - 36, max_font=24, min_font=15,
                color="#111827", bold=True, start=start + 0.10
            )

    current, layer_num = add_filter_roundrect(filters, current, layer_num, (1390, 820, 330, 46), "#ede9fe", 23, 0.78)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "\u56de\u770b -> \u5e94\u7528", x=1460, y=831, font_size=22, color="#6d28d9", bold=True, start=0.84)
    return filters, current, layer_num


def layout_diverse_misconception_compare(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    groups = comparison_groups_from_lines(rest, limit=2)
    cards = content_cards_from_lines(rest, limit=4)
    cards = augment_teaching_cards(title, rest, cards, limit=4)
    left_title = groups[0]["title"] if groups else "常见误区"
    right_title = groups[1]["title"] if len(groups) > 1 else "正确理解"
    left_body = groups[0].get("body", "") if groups else (cards[0].get("body", "") if cards else "")
    right_body = groups[1].get("body", "") if len(groups) > 1 else (cards[1].get("body", "") if len(cards) > 1 else "")
    filters = [f"color=c=#fff7ed:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#f97316")
    current, layer_num = add_micro_label(filters, current, layer_num, "\u8bef\u533a\u5bf9\u6bd4", 120, 88, "#f97316", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=150, width=1260, height=126, color="#111827", start=0.22, max_font=56, min_font=38)
    panels = [((145, 365, 710, 440), "#ef4444", "×", left_title, left_body), ((1065, 365, 710, 440), "#10b981", "✓", right_title, right_body)]
    for idx, (box, accent, mark, heading, body) in enumerate(panels):
        x, y, w, h = box
        start = 0.38 + idx * 0.22
        current, layer_num = add_round_panel(filters, current, layer_num, box, accent, "white@0.95", start, radius=48, shadow=True, border=True)
        current, layer_num = add_soft_circle_token(filters, current, layer_num, x + 72, y + 78, 46, accent, mark, start + 0.04, fill=f"{accent}@0.14", text_color=accent)
        current, layer_num = add_bounded_text(filters, current, layer_num, heading, x=x + 140, y=y + 48, width=w - 190, height=80, max_font=34, min_font=22, color="#111827", bold=True, start=start + 0.10)
        current, layer_num = add_bounded_text(filters, current, layer_num, concise_body(body, 120), x=x + 54, y=y + 160, width=w - 108, height=190, max_font=25, min_font=18, color="#475569", start=start + 0.18)
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 54, y + 365, w - 108, 20), f"{accent}@0.18", 10, start + 0.26)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (890, 538, 140, 62), "#111827", 31, 0.72)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "修正", x=924, y=554, font_size=28, color="white", bold=True, start=0.78)
    return filters, current, layer_num


def layout_diverse_rounded_step_cards(slide_data, slide_num, duration, project=None):
    title, cards = diverse_cards(slide_data, slide_num, project, limit=4)
    filters = [f"color=c=#f8fafc:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#06b6d4")
    current, layer_num = add_micro_label(filters, current, layer_num, "\u5706\u89d2\u8981\u70b9", 120, 88, "#06b6d4", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=150, width=1160, height=126, color="#07111f", start=0.22, max_font=56, min_font=38)
    positions = [(125, 365, 395, 260), (560, 430, 395, 300), (995, 365, 395, 260), (1430, 430, 330, 300)]
    accents = ["#06b6d4", "#8b5cf6", "#f59e0b", "#10b981"]
    for idx, card in enumerate(cards[:4]):
        x, y, w, h = positions[idx]
        accent = accents[idx]
        start = start_step(duration, idx, 0.40, 0.08)
        current, layer_num = add_round_panel(filters, current, layer_num, (x, y, w, h), accent, "white@0.88", start, radius=70, shadow=True, border=True)
        current, layer_num = add_soft_circle_token(filters, current, layer_num, x + 68, y + 70, 42, accent, f"{idx + 1}", start + 0.04, fill=f"{accent}@0.14")
        current, layer_num = add_bounded_text(filters, current, layer_num, card.get("title", ""), x=x + 124, y=y + 44, width=w - 160, height=72, max_font=27, min_font=19, color="#07111f", bold=True, start=start + 0.10)
        body = concise_body((card.get("subtitle") or "") + " " + (card.get("body") or ""), 82)
        current, layer_num = add_bounded_text(filters, current, layer_num, body, x=x + 42, y=y + 145, width=w - 84, height=h - 178, max_font=21, min_font=16, color="#475569", start=start + 0.16)
    return filters, current, layer_num


def layout_diverse_radical_division_rule(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    lines = [normalize_video_text(line) for line in rest if normalize_video_text(line) and not is_noise_line(line)]
    formula = next((line for line in lines if "√a" in line and "√b" in line), "√a ÷ √b = √(a÷b)")
    examples = []
    for line in lines:
        if "举例" in line and contains_math_notation(line):
            examples.append(re.sub(r"^举例[:：]\s*", "", line))
        elif "√12" in line and "√3" in line:
            examples.append("√12 ÷ √3 = √(12÷3) = √4 = 2")
        elif "√8" in line and "√2" in line:
            examples.append("√8 ÷ √2 = √4 = 2")
    if not examples:
        examples = ["√8 ÷ √2 = √(8÷2) = √4 = 2", "√12 ÷ √3 = √(12÷3) = √4 = 2"]
    examples = list(dict.fromkeys(examples))[:2]

    filters = [f"color=c=#eef2ff:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#6366f1")
    current, layer_num = add_micro_label(filters, current, layer_num, "\u8fd0\u7b97\u6cd5\u5219", 120, 88, "#6366f1", 0.1)
    current, layer_num = add_large_title(
        filters, current, layer_num, title, x=120, y=145, width=780, height=118,
        color="#111827", start=0.22, max_font=56, min_font=38
    )

    current, layer_num = add_round_panel(filters, current, layer_num, (610, 138, 1120, 210), "#6366f1", "white@0.94", 0.30, radius=52, shadow=True, border=True)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "\u6838\u5fc3\u516c\u5f0f", x=660, y=178, font_size=24, color="#6366f1", bold=True, start=0.38)
    current, layer_num = add_bounded_text(
        filters, current, layer_num, formula, x=660, y=225, width=650, height=70,
        max_font=40, min_font=28, color="#111827", bold=True, start=0.48
    )
    current, layer_num = add_bounded_text(
        filters, current, layer_num, "适用前提：a ≥ 0，b > 0，分母不能为 0。",
        x=1320, y=222, width=360, height=78, max_font=24, min_font=18,
        color="#475569", start=0.58
    )

    cards = [
        {
            "box": (145, 390, 430, 390),
            "accent": "#3b82f6",
            "label": "01",
            "title": "分母有理化",
            "body": "分母含根号时，分子分母同乘对应根式，例如 1/√2 = √2/2。",
        },
        {
            "box": (640, 430, 500, 310),
            "accent": "#10b981",
            "label": "02",
            "title": "三步化简",
            "body": "先合并根号，再计算被开方数，最后开方或继续化简。",
        },
        {
            "box": (1205, 430, 500, 310),
            "accent": "#f59e0b",
            "label": "03",
            "title": "先看条件",
            "body": "被开方数要非负；除数对应的根式不能等于 0。",
        },
    ]
    for idx, card in enumerate(cards):
        x, y, w, h = card["box"]
        accent = card["accent"]
        start = start_step(duration, idx, 0.46, 0.08)
        current, layer_num = add_round_panel(filters, current, layer_num, card["box"], accent, "white@0.92", start, radius=48, shadow=True, border=True)
        current, layer_num = add_soft_circle_token(filters, current, layer_num, x + 66, y + 70, 40, accent, card["label"], start + 0.04, fill=f"{accent}@0.13", text_color=accent)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card["title"], x=x + 124, y=y + 42,
            width=w - 165, height=62, max_font=28, min_font=20,
            color="#111827", bold=True, start=start + 0.10
        )
        current, layer_num = add_bounded_text(
            filters, current, layer_num, card["body"], x=x + 42, y=y + 138,
            width=w - 84, height=h - 172, max_font=22, min_font=16,
            color="#475569", start=start + 0.16
        )

    current, layer_num = add_round_panel(filters, current, layer_num, (640, 790, 1065, 135), "#8b5cf6", "white@0.92", 0.78, radius=46, shadow=True, border=True)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "\u4f8b\u9898\u6f14\u793a", x=690, y=826, font_size=24, color="#7c3aed", bold=True, start=0.84)
    for idx, example in enumerate(examples):
        current, layer_num = add_bounded_text(
            filters, current, layer_num, example, x=855, y=814 + idx * 50,
            width=790, height=42, max_font=26, min_font=19,
            color="#111827", bold=idx == 0, start=0.90 + idx * 0.06
        )
    return filters, current, layer_num


def layout_diverse_radial_concept_map(slide_data, slide_num, duration, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    if is_radical_division_rule(title, rest):
        return layout_diverse_radical_division_rule(slide_data, slide_num, duration, project)
    cards = content_cards_from_lines(rest, limit=5)
    if len(cards) < 3:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:5]
    cards = augment_teaching_cards(title, rest, cards, limit=5)
    filters = [f"color=c=#eef2ff:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#6366f1")
    current, layer_num = add_micro_label(filters, current, layer_num, "\u77e5\u8bc6\u5730\u56fe", 120, 88, "#6366f1", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=145, width=1000, height=118, color="#111827", start=0.22, max_font=56, min_font=38)
    cx, cy = 960, 600
    positions = [(330, 405), (795, 205), (1245, 330), (1305, 735), (500, 760)]
    accents = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"]
    card_boxes = [(x, y, 330, 138) for x, y in positions[:len(cards[:5])]]
    for idx, box in enumerate(card_boxes):
        accent = accents[idx % len(accents)]
        start = start_step(duration, idx, 0.46, 0.07)
        x1, y1, x2, y2 = radial_connector_points(cx, cy, 154, box, gap=-8)
        current, layer_num = add_filter_elbow_connector(
            filters, current, layer_num, x1, y1, x2, y2, f"{accent}@0.32", thickness=7, start=start
        )
    current, layer_num = add_filter_circle(filters, current, layer_num, cx, cy, 158, "#6366f1@0.16", 0.24)
    current, layer_num = add_filter_circle(filters, current, layer_num, cx, cy, 106, "#6366f1", 0.34)
    current, layer_num = add_bounded_text(filters, current, layer_num, concise_body(title, 12), x=cx - 78, y=cy - 34, width=156, height=70, max_font=30, min_font=20, color="white", bold=True, start=0.46)
    for idx, card in enumerate(cards[:5]):
        x, y, w, h = card_boxes[idx]
        accent = accents[idx]
        start = start_step(duration, idx, 0.48, 0.07)
        current, layer_num = add_round_panel(filters, current, layer_num, (x, y, w, h), accent, "white@0.92", start, radius=54, shadow=True, border=True)
        current, layer_num = add_bounded_text(filters, current, layer_num, card.get("title", ""), x=x + 38, y=y + 34, width=w - 76, height=66, max_font=24, min_font=17, color="#111827", bold=True, start=start + 0.08)
    return filters, current, layer_num


def layout_diverse_application_storyboard(slide_data, slide_num, duration, project=None):
    title, cards = diverse_cards(slide_data, slide_num, project, limit=4)
    filters = [f"color=c=#f8fafc:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    current, layer_num = add_diverse_light_canvas(filters, current, layer_num, project, slide_num, slide_data, "#0f766e")
    current, layer_num = add_micro_label(filters, current, layer_num, "\u573a\u666f\u5206\u955c", 120, 88, "#0f766e", 0.1)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=120, y=150, width=1160, height=126, color="#07111f", start=0.22, max_font=56, min_font=38)
    positions = [(120, 360), (555, 360), (990, 360), (1425, 360)]
    accents = ["#0f766e", "#2563eb", "#f59e0b", "#8b5cf6"]
    for idx, card in enumerate(cards[:4]):
        x, y = positions[idx]
        start = start_step(duration, idx, 0.42, 0.08)
        accent = accents[idx]
        current, layer_num = add_round_panel(filters, current, layer_num, (x, y, 360, 420), accent, "white@0.94", start, radius=42, shadow=True, border=True)
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 34, y + 34, 292, 150), f"{accent}@0.12", 34, start + 0.04)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"\u573a\u666f {idx + 1}", x=x + 58, y=y + 80, font_size=24, color=accent, bold=True, start=start + 0.08)
        current, layer_num = add_bounded_text(filters, current, layer_num, card.get("title", ""), x=x + 36, y=y + 220, width=286, height=76, max_font=25, min_font=18, color="#07111f", bold=True, start=start + 0.14)
        current, layer_num = add_bounded_text(filters, current, layer_num, concise_body(card.get("body", ""), 58), x=x + 36, y=y + 312, width=286, height=62, max_font=19, min_font=15, color="#475569", start=start + 0.20)
    return filters, current, layer_num


def collect_micro_course_examples(title, rest, cards, limit=3):
    examples = []
    for line in rest:
        line = normalize_video_text(line)
        if not line:
            continue
        if is_example_heading(line):
            examples.append(re.sub(r"^[^：:]{1,8}[：:]\s*", "", line))
        elif ("例" in line or "如" in line) and contains_math_notation(line):
            snippets = extract_formula_snippets(line)
            examples.extend(snippets or [compact_sentence_without_ellipsis(line, 34)])
    for card in cards:
        body = normalize_video_text(card.get("body", ""))
        if "例：" in body:
            examples.append(body.split("例：", 1)[1])
        elif is_example_heading(card.get("title", "")):
            examples.append(normalize_video_text(" ".join(part for part in (card.get("subtitle", ""), body) if part)))
    examples.extend(formula_example_lines(rest, cards, title, limit=limit))

    deduped = []
    seen = set()
    for example in examples:
        example = compact_sentence_without_ellipsis(example, 42)
        key = enrichment_fingerprint(example)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(example)
        if len(deduped) >= limit:
            break
    return deduped


def micro_course_main_formula(title, rest, cards):
    combined = " ".join([normalize_video_text(title), *[normalize_video_text(line) for line in rest]])
    clean_title = normalize_video_text(title)
    if clean_title == "目录":
        return ""
    if any(keyword in combined for keyword in ("摩擦力", "摩擦", "滑动摩擦", "静摩擦", "正压力", "粗糙")):
        if any(keyword in combined for keyword in ("静摩擦", "最大静摩擦", "取值范围")):
            return "0 ≤ fs ≤ μsN"
        if any(keyword in combined for keyword in ("公式", "计算", "μ", "正压力", "滑动摩擦")):
            return "F = μN"
        if any(keyword in combined for keyword in ("方向", "相对运动趋势")):
            return "f 与相对运动趋势相反"
        if any(keyword in combined for keyword in ("条件", "产生")):
            return "接触 + 挤压 + 相对运动趋势"
    if "知识全解析" in combined:
        return "√a；a ≥ 0"
    if "基础概念" in combined:
        return "√a；a ≥ 0"
    if "根号的定义" in combined:
        return "x² = a → x = √a"
    if "根号的分类" in combined or ("算术平方根" in combined and "平方根" in combined):
        return "√9 = 3；x² = 9 → x = ±3"
    if "运算技巧" in combined:
        return "条件 → 化简 → 合并 → 验算"
    if "化简" in combined and "√72" in combined:
        return "√72 = √(36×2) = 6√2"
    if "乘法法则" in combined and "√a" in combined and "√b" in combined:
        return "√a × √b = √(a×b)"
    if "除法法则" in combined and "√a" in combined and "√b" in combined:
        return "√a ÷ √b = √(a÷b)"
    if "加减法" in combined and ("同类根式" in combined or "2√3" in combined):
        return "m√a ± n√a = (m±n)√a"
    if "方程" in combined:
        if "√(x + 2) = x" in combined or "√(x+2)=x" in combined:
            return "√(x+2)=x → x=2"
        if "√(2x" in combined:
            return "√(2x + 1) = 3"
        return "定义域 → 平方 → 求解 → 验根"
    if "不等式" in combined:
        if "√(x+1)" in combined or "√(x + 1)" in combined:
            return "√(x+1) > 2 → x > 3"
        return "定义域 ∩ 平方后的解集"
    if "函数" in combined:
        if "√(x-2)" in combined or "√(x - 2)" in combined or "y=√" in combined:
            return "y = √(x - 2), x ≥ 2"
        return "根式函数：根号内 ≥ 0，函数值 ≥ 0"
    if "无理数" in combined:
        return "非完全平方数开方：√2、√3 为无理数"
    if "进阶知识" in combined:
        return "定义域 → 变形 → 验根 / 取交集"
    if "根号的性质" in combined or "性质" in combined:
        return "a ≥ 0；√a ≥ 0；√(x²)=|x|"
    if "应用场景" in combined:
        return "已知 x² = a → 开方反求 x"
    if "平方根" in combined:
        return "x² = a → x = ±√a；√a ≥ 0"
    preferred = []
    fallback = []
    for line in rest:
        line = normalize_video_text(line)
        if not line or not contains_math_notation(line):
            continue
        snippets = extract_formula_snippets(line) or ([line] if visual_text_len(line) <= 48 else [])
        if any(keyword in line for keyword in ("公式", "法则", "定义", "规律", "性质", "写作", "表示")):
            preferred.extend(snippets)
        else:
            fallback.extend(snippets)
    for card in cards:
        heading = normalize_video_text(card.get("title", ""))
        body = normalize_video_text(card.get("body", ""))
        snippets = extract_formula_snippets(body)
        if contains_math_notation(heading) and any(keyword in heading for keyword in ("公式", "法则", "定义", "规律", "性质")):
            preferred.append(heading)
        if any(keyword in heading + body for keyword in ("公式", "法则", "定义", "规律", "性质", "写作", "表示")):
            preferred.extend(snippets)
        else:
            fallback.extend(snippets)
    seen = set()
    for item in [*preferred, *fallback]:
        item = compact_sentence_without_ellipsis(item, 34)
        if not item or not contains_math_notation(item):
            continue
        key = enrichment_fingerprint(item)
        if key in seen:
            continue
        seen.add(key)
        return item
    return ""


def micro_course_topic_kind(title, rest, cards, project=None, slide_num=0):
    combined = " ".join([normalize_video_text(title), *[normalize_video_text(line) for line in rest]])
    has_image = micro_course_visual_asset(project, slide_num, title=title, rest=rest) is not None
    formula = micro_course_main_formula(title, rest, cards)
    if slide_num == 1:
        return "opener"
    real_example = any(keyword in combined for keyword in ("举例", "例题", "计算", "求解", "化简", "方程", "不等式"))
    if any(keyword in combined for keyword in ("目录", "感谢观看")):
        return "concept"
    if any(keyword in combined for keyword in ("性质", "分类", "概念", "进阶知识", "无理数")):
        return "formula"
    if any(keyword in combined for keyword in ("增大有益摩擦", "减小有害摩擦", "鞋底花纹", "轮胎花纹", "润滑油", "轴承", "刹车", "抓地力", "防滑")):
        return "case"
    if any(keyword in combined for keyword in ("情境", "应用", "观察", "探究", "生活", "案例", "场景", "几何", "物理", "金融")):
        return "case"
    if real_example and any(keyword in combined for keyword in ("计算", "求解", "化简", "方程", "不等式")):
        return "example"
    if formula or any(keyword in combined for keyword in ("公式", "法则", "定义", "性质", "规律", "根号", "平方根")):
        return "formula"
    if has_image:
        return "case"
    return "concept"


def add_micro_course_header(filters, current, layer_num, title, subtitle, start=0.12):
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, 10), "#1d4ed8", "fill", 0.0)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (42, 34, 212, 66), "#1d4ed8", 16, start)
    current, layer_num = add_soft_circle_token(filters, current, layer_num, 82, 67, 22, "#ffffff", ">", start + 0.03, fill="white@0.96", text_color="#1d4ed8")
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "微课", x=126, y=50, font_size=30, color="white", bold=True, start=start + 0.06)
    current, layer_num = add_micro_course_bulb(filters, current, layer_num, 1680, 32, start + 0.04)
    current, layer_num = add_large_title(filters, current, layer_num, title, x=365, y=30, width=1180, height=82, color="#08215c", start=start + 0.06, max_font=58, min_font=34)
    if subtitle:
        current, layer_num = add_bounded_text(filters, current, layer_num, subtitle, x=520, y=120, width=880, height=36, max_font=26, min_font=18, color="#172554", start=start + 0.14)
    return current, layer_num


def micro_course_directory_items(rest, limit=3):
    items = []
    seen = set()
    for raw in rest or []:
        text = normalize_video_text(raw)
        if not text or is_noise_line(text) or text == "目录":
            continue
        text = re.sub(r"^[0-9]+(?:\.[0-9]+)?[)\.、．\- ]*", "", text).strip()
        text = re.sub(r"^[一二三四五六七八九十]+[、\.．\- ]*", "", text).strip()
        if not text or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
        if len(items) >= limit:
            break
    return items


def micro_course_directory_label(text, limit=10):
    text = normalize_video_text(text)
    for sep in ("：", ":", "，", ",", "。", "·", "/", "—", "-", "、"):
        if sep in text:
            text = text.split(sep, 1)[0].strip()
    if visual_text_len(text) > limit:
        text = text[:limit].rstrip("，。；;：: ")
    return text


def micro_course_directory_labels(rest, limit=3):
    return [micro_course_directory_label(item) for item in micro_course_directory_items(rest, limit=limit)]


def micro_course_specific_points(title, rest, limit=4):
    combined = " ".join([normalize_video_text(title), *[normalize_video_text(line) for line in rest]])
    clean_title = normalize_video_text(title)
    if clean_title == "目录":
        labels = [label for label in micro_course_directory_labels(rest, limit=3) if label]
        points = [
            f"第一层：{labels[0]}，先把核心概念说清楚" if len(labels) >= 1 else "第一层：先把核心概念说清楚",
            f"第二层：{labels[1]}，把方法和规则连起来" if len(labels) >= 2 else "第二层：把方法和规则连起来",
            f"第三层：{labels[2]}，再落到应用和检查" if len(labels) >= 3 else "第三层：再落到应用和检查",
            "学习顺序：概念 → 方法 → 应用",
        ]
    elif "知识全解析" in combined:
        points = [
            "先懂符号：根号表示算术平方根",
            "再懂条件：被开方数必须非负",
            "会算例题：乘除加减都先化简",
            "能迁移：函数、方程、不等式都要保留定义域",
        ]
    elif "基础概念" in combined:
        points = [
            "含义：表示算术平方根",
            "条件：实数范围内被开方数非负",
            "结果：根号结果只取非负值",
            "区别：平方根才包含正负两类",
        ]
    elif "根号的定义" in combined:
        points = [
            "找数：先设一个平方关系",
            "取值：根号默认取非负的那个数",
            "例子：根号九等于三，不是正负三",
            "完整平方根：九的平方根是正负三",
        ]
    elif "根号的性质" in combined:
        points = [
            "有意义：被开方数 a ≥ 0",
            "结果范围：√a ≥ 0",
            "平方还原：(√a)² = a",
            "开平方：√(x²)=|x|",
        ]
    elif "根号的分类" in combined:
        points = [
            "算术平方根：只取非负结果",
            "平方根：通常对应正负两个结果",
            "立方根：结果可以为负",
            "判断：先看根指数，再看结果个数",
        ]
    elif "运算技巧" in combined:
        points = [
            "先看条件：根号内必须有意义",
            "再拆因子：优先找完全平方数",
            "合并同类：根号内相同才能加减",
            "最后验算：平方或代回原式检查",
        ]
    elif "乘法法则" in combined:
        points = [
            "条件：a ≥ 0，b ≥ 0",
            "合并：√a × √b = √(a×b)",
            "例子：√2 × √3 = √6",
            "检验：√4×√9 与 √36 都等于 6",
        ]
    elif "除法法则" in combined:
        points = [
            "条件：a ≥ 0，b > 0",
            "合并：√a ÷ √b = √(a÷b)",
            "例子：√8 ÷ √2 = √4 = 2",
            "分母有根号时要有理化",
        ]
    elif "加减法" in combined:
        points = [
            "先化简：把 √8 化成 2√2",
            "再判断：根号内相同才是同类根式",
            "合并：只合并系数，根式不变",
            "例子：2√3 + 5√3 = 7√3",
        ]
    elif "化简" in combined:
        points = [
            "分解：把被开方数拆出完全平方因子",
            "提取：√36 可以提出为 6",
            "保留：剩下不能开尽方的因子留在根号内",
            "例子：√72 = 6√2",
        ]
    elif "进阶知识" in combined:
        points = [
            "定义域：根号内表达式必须 ≥ 0",
            "变形：平方前先确认两边条件",
            "验根：方程平方后必须代回",
            "取交集：不等式结果要和定义域合并",
        ]
    elif "无理数" in combined:
        points = [
            "完全平方数：开方后可得到整数",
            "非完全平方数：如 √2、√3",
            "无理数特征：无限不循环小数",
            "判断：不能化成两个整数之比",
        ]
    elif "应用场景" in combined:
        points = [
            "几何：由面积或勾股关系反求边长",
            "物理：由平方关系反求速度或距离",
            "金融：由增长倍数反推平均变化率",
            "关键：先识别哪个量被平方了",
        ]
    elif "分类及特点" in combined or (
        "静摩擦力" in combined and "滑动摩擦力" in combined and "滚动摩擦力" in combined
    ):
        points = [
            "静摩擦：物体没相对滑动，但有相对运动趋势，方向与趋势相反。",
            "滑动摩擦：已经发生相对滑动，如推箱子时箱子和地面互相阻碍。",
            "滚动摩擦：轮胎或滚轮滚动时产生，通常比滑动摩擦小。",
            "判断顺序：先看是否滑动，再看是否滚动，最后判断受力方向。",
        ]
    elif "摩擦力的计算与应用" in combined:
        points = [
            "先分清类型：滑动摩擦可用 F = μN，静摩擦不能直接套等号。",
            "μ 表示接触面的粗糙程度，材料和表面状态变了，μ 才会变。",
            "N 是垂直接触面的正压力，水平面无额外竖直力时常等于重力。",
            "应用时一次只改一个因素，才能判断摩擦力为什么变大或变小。",
        ]
    elif "影响因素分析" in combined:
        points = [
            "适用条件：比较摩擦大小时，尽量只改变一个变量，其他条件保持相同。",
            "粗糙程度看 μ：砂纸比玻璃更粗糙，μ 更大，阻碍作用通常更强。",
            "正压力看 N：压得越紧，接触面微小凸起咬合越明显。",
            "判断方法：先说清楚 μ 变了还是 N 变了，再解释摩擦力变化。",
        ]
    elif any(keyword in combined for keyword in ("增大有益摩擦", "鞋底花纹", "轮胎花纹", "抓地力", "防滑")):
        points = [
            "鞋底花纹：凹凸纹路让接触面更粗糙，增加微小凸起之间的咬合。",
            "轮胎沟槽：排走积水，让橡胶更稳定地贴住路面，保持抓地力。",
            "本质关系：在正压力相近时，接触面越粗糙，有益摩擦通常越大。",
            "应用价值：行走、跑步和刹车都依赖这种防滑能力。",
        ]
    elif any(keyword in combined for keyword in ("减小有害摩擦", "润滑油", "滚动代替滑动", "轴承")):
        points = [
            "润滑油：在接触面之间形成油膜，减少固体表面的直接刮擦。",
            "滚动代替滑动：滚珠或滚轮让接触方式改变，阻力明显变小。",
            "本质目标：减少能量损耗和零件磨损，而不是让摩擦完全消失。",
            "应用场景：发动机、轴承、传动部件都需要控制有害摩擦。",
        ]
    elif "函数" in combined:
        points = [
            "定义域：根号内表达式 ≥ 0",
            "值域：算术平方根结果 ≥ 0",
            "图像：从端点开始向右上延伸",
            "例子：y=√(x-2) 要求 x≥2",
        ]
    elif "方程" in combined:
        points = [
            "先定条件：根号内 ≥ 0",
            "再平方：消去根号",
            "后求解：得到候选解",
            "必须验根：防止平方引入增根",
        ]
    elif "不等式" in combined:
        points = [
            "先写定义域：根号内 ≥ 0",
            "两边非负时再平方",
            "解出范围后与定义域取交集",
            "例子：√(x+1)>2 → x>3",
        ]
    else:
        points = []
    return points[:limit]


def micro_course_core_points(title, rest, cards, limit=3):
    specific = micro_course_specific_points(title, rest, limit)
    if specific:
        return specific
    points = []
    for card in cards:
        item = compact_sentence_without_ellipsis(card.get("title") or card.get("body") or "", 24)
        if item:
            points.append(item)
    if len(points) < limit:
        for line in rest:
            line = compact_sentence_without_ellipsis(line, 28)
            if line and not any(enrichment_fingerprint(line) == enrichment_fingerprint(item) for item in points):
                points.append(line)
            if len(points) >= limit:
                break
    if not points:
        points = ["先抓关键词", "再看限制条件", "最后用例子检验"]
    return points[:limit]


def micro_course_specific_lead(title, rest):
    combined = " ".join([normalize_video_text(title), *[normalize_video_text(line) for line in rest]])
    clean_title = normalize_video_text(title)
    if clean_title == "目录":
        labels = [label for label in micro_course_directory_labels(rest, limit=3) if label]
        if len(labels) >= 3:
            return f"这节课按一条线讲：先看{labels[0]}，再看{labels[1]}，最后看{labels[2]}。"
        if len(labels) == 2:
            return f"这节课按一条线讲：先看{labels[0]}，再看{labels[1]}。"
        if len(labels) == 1:
            return f"这节课按一条线讲：先看{labels[0]}。"
        return "这节课按一条线讲：先看概念，再看方法，最后看应用。"
    if "知识全解析" in combined:
        return "根号的学习主线是：先理解定义与条件，再掌握运算规则，最后迁移到函数、方程和不等式。"
    if "基础概念" in combined:
        return "这一页先讲清楚三件事：根号表示什么，什么时候有意义，以及它和“平方根”有什么区别。"
    if "根号的定义" in combined:
        return "根号不是随便套符号，它来自“哪个非负数平方后等于 a”这个问题。"
    if "根号的性质" in combined:
        return "性质页重点看两个方向：根号下能不能成立，以及开方后的结果范围。"
    if "根号的分类" in combined:
        return "分类不是背名字，而是区分结果个数：算术平方根一个，平方根通常两个。"
    if "运算技巧" in combined:
        return "运算题不要急着算，先检查条件，再化简，再合并，最后回到原式验算。"
    if "进阶知识" in combined:
        return "进阶题的重点不是公式更多，而是每次变形都不能丢掉定义域和验根。"
    if "无理数" in combined:
        return "不是所有根号都能开成整数；开不尽的根式往往对应无限不循环小数。"
    if "乘法法则" in combined:
        return "两个根式相乘时，可以合并进一个根号，但前提是被开方数都非负。"
    if "除法法则" in combined:
        return "除法多一个限制：分母里的根式不能为 0，也就是 b > 0。"
    if "加减法" in combined:
        return "根式加减的核心不是把根号内相加，而是先化成同类根式再合并系数。"
    if "化简" in combined:
        return "化简根式就是把根号里能开尽方的部分提出来，让结果更短、更标准。"
    if "应用场景" in combined:
        return "应用题里出现根号，通常是因为题目已知平方关系，需要反求原来的量。"
    if "分类及特点" in combined or (
        "静摩擦力" in combined and "滑动摩擦力" in combined and "滚动摩擦力" in combined
    ):
        return "这一页要把三种摩擦分开看：静摩擦看趋势，滑动摩擦看实际滑动，滚动摩擦看轮胎和滚轮。"
    if "摩擦力的计算与应用" in combined:
        return "这一章要把计算和应用连起来：先确认摩擦类型，再读懂 μ 和 N，最后用生活例子检验。"
    if "影响因素分析" in combined:
        return "影响因素页不要只记公式，要把条件说清楚：到底是接触面粗糙程度变了，还是正压力变了。"
    if any(keyword in combined for keyword in ("增大有益摩擦", "鞋底花纹", "轮胎花纹", "抓地力", "防滑")):
        return "这一页重点解释：为什么鞋底和轮胎要做花纹，以及这些花纹怎样把摩擦变成安全优势。"
    if any(keyword in combined for keyword in ("减小有害摩擦", "润滑油", "滚动代替滑动", "轴承")):
        return "这一页重点看减小摩擦的两条路：隔开粗糙接触面，或者把滑动接触改成滚动接触。"
    if "不等式" in combined:
        return "根号不等式要先写定义域，再判断两边能否平方，最后把解集和定义域取交集。"
    if "函数" in combined:
        return "根式函数先由根号内确定定义域，再由算术平方根确定值域和图像起点。"
    if "方程" in combined:
        return "先平方得到候选解，再把 -1 和 2 代回原方程验根。"
    return ""


def micro_course_formula_lead_text(ctx):
    lead = normalize_video_text(ctx.get("lead", ""))
    title = normalize_video_text(ctx.get("title", ""))
    combined = " ".join([title, lead, *[normalize_video_text(line) for line in ctx.get("rest", [])]])
    if "影响因素分析" in combined:
        return "先看清控制变量：接触面粗糙程度影响 μ，正压力大小影响 N，两者都会改变摩擦力。"
    if "摩擦" in combined and ("F = μN" in combined or "公式" in combined or "正压力" in combined):
        return "先判断是不是滑动摩擦，再把 μ 和 N 分开读，最后用控制变量法解释变化。"
    if "不等式" in combined:
        return "先确定定义域和可平方条件，再变形求范围，最后与定义域合并。"
    if "函数" in combined:
        return "先看根号内表达式的非负条件，再判断函数值范围和图像起点。"
    if "方程" in combined:
        return "平方只得到候选解，必须代回原方程检验，排除增根。"
    if contains_math_notation(lead) or contains_display_formula(lead):
        return "先把符号、条件和结论分开，再选择对应的变形方法。"
    return lead


def micro_course_formula_card_details(ctx):
    title = normalize_video_text(ctx.get("title", ""))
    formula = normalize_video_text(ctx.get("formula", ""))
    combined = " ".join([title, formula, *[normalize_video_text(line) for line in ctx.get("rest", [])]])
    if "分类及特点" in combined or (
        "静摩擦力" in combined and "滑动摩擦力" in combined and "滚动摩擦力" in combined
    ):
        return [
            "静摩擦：没滑动，但有阻止滑动的趋势，方向和趋势相反。",
            "滑动摩擦：已经滑动，常见于推箱子、拖动物体。",
            "滚动摩擦：轮子或球滚动时产生，通常比滑动摩擦小。",
        ]
    if "影响因素分析" in combined:
        return [
            "控制变量：只比较一个因素；相同物体和接触方式下，分别改变 μ 或 N。",
            "F = μN 中，μ 看接触面粗糙程度，N 看垂直接触面的正压力。",
            "砂纸让 μ 变大；加重物让 N 变大，二者都可能让摩擦力增大。",
        ]
    if "摩擦" in combined and ("F = μN" in combined or "公式" in combined or "正压力" in combined):
        return [
            "滑动摩擦且已经发生相对滑动时，常用 F = μN；静止时先看静摩擦范围。",
            "μ 由接触面材料和粗糙程度决定，N 是垂直接触面的正压力。",
            "只改变 μ 或 N 中一个量，再比较摩擦力变化，结论才清楚。",
        ]
    if "乘法法则" in combined:
        return [
            "两个被开方数都要非负，即 a ≥ 0、b ≥ 0。",
            "条件成立时，√a × √b 可以合并成 √(a×b)。",
            "代入 4 和 9 检查：2×3 与 √36 都等于 6。",
        ]
    if "除法法则" in combined:
        return [
            "a ≥ 0，且分母里的 b 必须大于 0，不能让分母为 0。",
            "条件成立时，√a ÷ √b 可以写成 √(a÷b)。",
            "用 √8÷√2=√4=2 检查，同时记住 b > 0。",
        ]
    if "方程" in combined:
        return [
            "先写定义域，保证根号内表达式 ≥ 0。",
            "两边平方只得到候选解，不能直接当最终答案。",
            "把候选解代回原方程，排除平方带来的增根。",
        ]
    if "不等式" in combined:
        return [
            "先写定义域，再判断两边是否都非负。",
            "能平方时再变形，解出普通不等式范围。",
            "最终结果要和定义域取交集，不能漏条件。",
        ]
    if "函数" in combined:
        return [
            "根号内表达式必须 ≥ 0，这一步决定定义域。",
            "算术平方根的输出一定 ≥ 0，这一步决定值域方向。",
            "图像从定义域起点开始，再看单调性和交点。",
        ]
    cleaned = []
    seen = set()
    for point in ctx.get("points", []):
        point = normalize_video_text(point)
        if is_noise_line(point):
            continue
        key = enrichment_fingerprint(point)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(compact_sentence_without_ellipsis(point, 46))
        if len(cleaned) >= 3:
            break
    fallbacks = [
        "先确认公式适用范围，条件不成立就不能直接套用。",
        "把文字量翻译成符号，分清已知量、未知量和要比较的量。",
        "代入简单数或回到原题检查，防止把限制条件漏掉。",
    ]
    cleaned.extend(fallbacks[len(cleaned):])
    return cleaned[:3]


def micro_course_explain_line(title, rest, cards, limit=68):
    specific = micro_course_specific_lead(title, rest)
    if specific:
        return compact_sentence_without_ellipsis(specific, limit)
    candidates = []
    for card in cards:
        text = normalize_video_text(card.get("body", "") or card.get("subtitle", ""))
        if text and visual_text_len(text) >= 8:
            candidates.append(text)
    candidates.extend(normalize_video_text(line) for line in rest if normalize_video_text(line))
    if not candidates:
        return "把本页内容拆成概念、条件和应用三个层次来看。"
    return compact_sentence_without_ellipsis(candidates[0], limit)


def micro_course_teaching_steps(title, rest, cards, formula="", limit=3):
    combined = " ".join([normalize_video_text(title), *[normalize_video_text(line) for line in rest]])
    clean_title = normalize_video_text(title)
    if clean_title == "目录":
        labels = [label for label in micro_course_directory_labels(rest, limit=3) if label]
        steps = [
            f"先讲{labels[0]}，把最基础的概念说透。" if len(labels) >= 1 else "先讲概念，把最基础的内容说透。",
            f"再讲{labels[1]}，把方法和规则连起来。" if len(labels) >= 2 else "再讲方法，把规则连起来。",
            f"最后讲{labels[2]}，用案例或应用收尾。" if len(labels) >= 3 else "最后讲应用，用案例收尾。",
        ]
    elif any(keyword in combined for keyword in ("摩擦", "静摩擦", "滑动摩擦", "滚动摩擦", "正压力", "粗糙")):
        if any(keyword in combined for keyword in ("公式", "计算", "μ", "正压力")):
            steps = [
                "确定类型：滑动摩擦用 F = μN。",
                "找准参数：μ 看接触面，N 看正压力。",
                "检查静摩擦：静止时先看 0 ≤ fs ≤ μsN。",
            ]
        elif any(keyword in combined for keyword in ("方向", "相对运动趋势")):
            steps = [
                "先判断物体相对接触面的运动趋势。",
                "摩擦力方向与相对运动或趋势相反。",
                "再结合受力平衡或运动状态验证。",
            ]
        elif any(keyword in combined for keyword in ("实验", "测力计", "数据")):
            steps = [
                "匀速拉动木块，让拉力与滑动摩擦力平衡。",
                "改变正压力或粗糙程度，只变一个条件。",
                "记录多组数据，再比较摩擦力变化。",
            ]
        else:
            steps = [
                "产生条件：接触、挤压、接触面不够光滑。",
                "判断方向：阻碍相对运动或相对运动趋势。",
                "实际应用：按需要增大有益摩擦或减小有害摩擦。",
            ]
    elif "知识全解析" in combined:
        steps = [
            "问题 1：√a 到底表示哪个数？",
            "问题 2：根号下为什么不能随便放负数？",
            "问题 3：遇到题目时怎样列式、化简、验算？",
        ]
    elif "根号的定义" in combined:
        steps = [
            "先设问题：求九的平方根，就是找平方后等于九的数。",
            "再分符号：根号九只表示算术平方根，不含负根。",
            "写完整答案：九的平方根是正负三。",
        ]
    elif "基础概念" in combined:
        steps = [
            "先看对象：根号表示对被开方数开平方。",
            "再看条件：在实数范围内，被开方数必须非负。",
            "最后区分：算术平方根只取非负，平方根才写正负。",
        ]
    elif "根号的性质" in combined:
        steps = [
            "定义域：先保证根号内表达式非负。",
            "结果范围：算术平方根不能取负值。",
            "逆运算：平方和开方要注意绝对值。",
        ]
    elif "根号的分类" in combined:
        steps = [
            "算术平方根：只取非负结果。",
            "平方根：解平方关系时通常有正负两个结果。",
            "更高次根：立方根可以为负。",
        ]
    elif "运算技巧" in combined:
        steps = [
            "先判条件：根号内的数或式子要有意义。",
            "再做化简：优先拆出完全平方因子。",
            "最后运算：同类根式合并，并代回检验。",
        ]
    elif "乘法法则" in combined:
        steps = [
            "确认条件：两个被开方数都非负。",
            "合并根号：先合成一个根式。",
            "代入检验：用具体数字检查等式。",
        ]
    elif "除法法则" in combined:
        steps = [
            "确认条件：分母根式不能为 0。",
            "合并根号：先写成一个商的根式。",
            "遇到分母根号：再做有理化。",
        ]
    elif "加减法" in combined:
        steps = [
            "先化简：√8 变成 2√2。",
            "找同类：根号内相同才能加减。",
            "合并系数：2√3+5√3=7√3。",
        ]
    elif "化简" in combined:
        steps = [
            "分解被开方数：72=36×2。",
            "提出完全平方因子：√36=6。",
            "得到最简根式：√72=6√2。",
        ]
    elif "进阶知识" in combined:
        steps = [
            "先写定义域，明确根号内必须非负。",
            "再做等价变形，平方时看两边是否非负。",
            "最后回到原题验根或与定义域取交集。",
        ]
    elif "无理数" in combined:
        steps = [
            "先看被开方数是不是完全平方数。",
            "开不尽时，如 √2、√3，通常是无理数。",
            "无理数的小数展开无限且不循环。",
        ]
    elif "方程" in combined:
        steps = [
            "先写限制：根号内表达式 ≥ 0。",
            "两边平方：把根式方程转为普通方程。",
            "代回验根：排除平方带来的增根。",
        ]
    elif "不等式" in combined:
        steps = [
            "先定定义域：x+1 ≥ 0。",
            "两边非负时平方：得到 x+1>4。",
            "合并范围：x>3 已满足定义域。",
        ]
    elif "函数" in combined:
        steps = [
            "先看根号内：x-2 ≥ 0。",
            "得到定义域：x ≥ 2。",
            "再看输出：y=√(x-2) 一定 ≥ 0。",
        ]
    else:
        steps = []

    if not steps:
        for card in cards:
            text = normalize_video_text(card.get("body") or card.get("title") or "")
            if text and visual_text_len(text) >= 6:
                steps.append(compact_sentence_without_ellipsis(text, 38))
            if len(steps) >= limit:
                break
    if len(steps) < limit:
        steps.extend(["先看条件是否成立", "再把文字翻译成符号", "最后代入数字检验"][: limit - len(steps)])
    return steps[:limit]


def micro_course_check_example(title, rest, formula="", examples=None):
    combined = " ".join([normalize_video_text(title), *[normalize_video_text(line) for line in rest]])
    clean_title = normalize_video_text(title)
    if clean_title == "目录":
        labels = [label for label in micro_course_directory_labels(rest, limit=3) if label]
        if len(labels) >= 3:
            return f"概念先行，方法跟上，最后回到{labels[2]}检验理解。"
        return "概念先行，方法跟上，最后回到应用场景检验理解。"
    if "基础概念" in combined:
        return "算术平方根只取非负；平方根要考虑正负"
    if "根号的定义" in combined:
        return "三的平方是九，所以根号九等于三"
    if "根号的性质" in combined:
        return "正数可以开平方；负数在实数范围内不能直接开平方"
    if "根号的分类" in combined:
        return "算术平方根只取一个；平方根要写正负两个"
    if "运算技巧" in combined:
        return "√72=√(36×2)=6√2，先拆完全平方因子"
    if "应用场景" in combined:
        return "面积 9 → 边长 √9=3；斜边 c=√(a²+b²)"
    if "分类及特点" in combined or (
        "静摩擦力" in combined and "滑动摩擦力" in combined and "滚动摩擦力" in combined
    ):
        return "静摩擦看相对运动趋势，滑动摩擦看实际滑动，滚动摩擦通常更小。"
    if "摩擦力的计算与应用" in combined:
        return "先判类型，再看 μ 和 N，最后代入或比较。"
    if "影响因素分析" in combined:
        return "砂纸让 μ 变大，加重物让 N 变大，二者都会影响摩擦力。"
    if any(keyword in combined for keyword in ("增大有益摩擦", "鞋底花纹", "轮胎花纹", "抓地力", "防滑")):
        return "粗糙程度提高 → 抓地力增强 → 更不容易打滑。"
    if any(keyword in combined for keyword in ("减小有害摩擦", "润滑油", "滚动代替滑动", "轴承")):
        return "润滑或滚动接触能降低阻力，但关键部位仍要保留必要摩擦。"
    if "乘法法则" in combined:
        return "√4×√9=2×3=6，√(4×9)=√36=6"
    if "除法法则" in combined:
        return "√8÷√2=√4=2，条件 b>0"
    if "加减法" in combined:
        return "√8+√2=2√2+√2=3√2"
    if "化简" in combined:
        return "72=36×2，所以 √72=6√2"
    if "进阶知识" in combined:
        return "含根号题先写定义域，变形后必须验根或取交集"
    if "无理数" in combined:
        return "√2、√3 开不尽，结果是无限不循环小数"
    if "函数" in combined:
        return "y=√(x-2) 要求 x≥2"
    if "方程" in combined:
        if "√(x + 2) = x" in combined or "√(x+2)=x" in combined:
            return "二代回成立，负一舍去"
        return "先求候选解，再代回原方程验根"
    if "不等式" in combined:
        return "√(x+1)>2 → x+1>4 → x>3"
    if examples:
        for example in examples:
            example = compact_sentence_without_ellipsis(example, 42)
            if example:
                return example
    return compact_sentence_without_ellipsis(formula or "代入具体数字检查条件和结果。", 42)


def micro_course_build_context(slide_data, slide_num, project=None):
    title, rest = slide_context(slide_data, slide_num, project)
    cards = content_cards_from_lines(rest, limit=5)
    if len(cards) < 2:
        cards = clean_card_data(slide_data, slide_num, project).get("cards", [])[:5]
    cards = merge_example_cards(cards, limit=5)
    cards = augment_teaching_cards(title, rest, cards, limit=5)
    examples = collect_micro_course_examples(title, rest, cards, limit=3)
    formula = micro_course_main_formula(title, rest, cards)
    points = micro_course_core_points(title, rest, cards, limit=4)
    steps = micro_course_teaching_steps(title, rest, cards, formula, limit=3)
    check = micro_course_check_example(title, rest, formula, examples)
    lead = micro_course_explain_line(title, rest, cards, limit=74)
    kind = micro_course_topic_kind(title, rest, cards, project, slide_num)
    visual_asset = micro_course_visual_asset(project, slide_num, slide_data, title, rest)
    return {
        "title": title or project_display_title(project),
        "rest": rest,
        "cards": cards,
        "examples": examples,
        "formula": formula,
        "points": points,
        "steps": steps,
        "check": check,
        "lead": lead,
        "kind": kind,
        "visual_asset": visual_asset,
    }


def micro_course_panel(filters, current, layer_num, box, accent, title, body_lines, start=0.34, fill="white@0.94", radius=28, icon_label=""):
    x, y, w, h = box
    current, layer_num = add_round_panel(filters, current, layer_num, box, accent, fill, start, radius=radius, shadow=True, border=False)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, 8, h), accent, 4, start + 0.03)
    if icon_label:
        current, layer_num = add_soft_circle_token(filters, current, layer_num, x + 52, y + 50, 26, accent, icon_label, start + 0.08, fill=f"{accent}@0.14", text_color=accent)
        title_x = x + 92
    else:
        title_x = x + 36
    current, layer_num = add_bounded_text(
        filters, current, layer_num, title, x=title_x, y=y + 28,
        width=w - (title_x - x) - 28, height=42, max_font=26, min_font=20,
        color=accent, bold=True, start=start + 0.10
    )
    text_y = y + 88
    for idx, line in enumerate(body_lines[:4]):
        line = normalize_video_text(line)
        if not line:
            continue
        current, layer_num = add_bounded_text(
            filters, current, layer_num, line, x=x + 38, y=text_y + idx * 44,
            width=w - 76, height=38, max_font=24, min_font=17,
            color="#111827", bold=False, start=start + 0.16 + idx * 0.05
        )
    return current, layer_num


def add_micro_course_bulb(filters, current, layer_num, x, y, start=0.28):
    current, layer_num = add_filter_circle(filters, current, layer_num, x + 48, y + 44, 38, "#facc15@0.92", start)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 31, y + 78, 34, 20), "#2563eb", 7, start + 0.06)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 34, y + 96, 28, 9), "#1d4ed8", 4, start + 0.08)
    for dx, dy, w, h in [(-18, 42, 18, 5), (96, 42, 18, 5), (47, -12, 5, 18), (7, 9, 15, 5), (78, 9, 15, 5)]:
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + dx, y + dy, w, h), "#facc15@0.80", 3, start + 0.10)
    return current, layer_num


def micro_course_canvas(duration, project=None, slide_num=0, slide_data=None):
    filters = [f"color=c=#eef7ff:s={VIDEO_W}x{VIDEO_H}:d={duration}[bg]"]
    current = "bg"
    layer_num = 0
    if micro_course_visual_asset(project, slide_num, slide_data):
        current, layer_num = add_filter_visual_cover(filters, current, layer_num, box=(0, 0, VIDEO_W, VIDEO_H), opacity=0.10, start=0.0)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (0, 0, VIDEO_W, VIDEO_H), "white@0.84", "fill", 0.0)
    current, layer_num = add_filter_circle(filters, current, layer_num, 1730, 200, 145, "#bfdbfe@0.30", 0.0)
    current, layer_num = add_filter_circle(filters, current, layer_num, 205, 910, 120, "#bbf7d0@0.22", 0.0)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (0, 0, VIDEO_W, 10), "#1d4ed8", 0, 0.0)
    return filters, current, layer_num


def add_micro_course_point_stack(filters, current, layer_num, points, *, x, y, width, accent="#1d70c9", start=0.58):
    for idx, point in enumerate(points[:4]):
        row_y = y + idx * 78
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, row_y, width, 58), "white@0.78", 22, start + idx * 0.08)
        current, layer_num = add_filter_circle(filters, current, layer_num, x + 32, row_y + 29, 18, f"{accent}@0.20", start + idx * 0.08)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, str(idx + 1), x=x + 24, y=row_y + 14, font_size=21, color=accent, bold=True, start=start + idx * 0.08)
        current, layer_num = add_bounded_text(
            filters, current, layer_num, point, x=x + 65, y=row_y + 15,
            width=width - 88, height=28, max_font=24, min_font=17,
            color="#0f172a", bold=True, start=start + idx * 0.08 + 0.03
        )
    return current, layer_num


def add_micro_course_formula_block(filters, current, layer_num, formula, *, x, y, width, height, start=0.62, accent="#2563eb"):
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 18, y + 18, width, height), "black@0.045", 34, start)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, width, height), "#ffffff@0.92", 34, start + 0.03)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 26, y + 24, 120, 38), f"{accent}@0.14", 19, start + 0.08)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "关键式", x=x + 50, y=y + 33, font_size=20, color=accent, bold=True, start=start + 0.10)
    display_formula = formula or "先看条件，再代入检验"
    if is_pure_math_text(display_formula):
        formula_area_h = max(40, height - 120)
        formula_font = min(38, max(22, int(formula_area_h * 0.28)))
        current, layer_num = add_formula_overlay(
            filters, current, layer_num, display_formula,
            x=x + 52, y=y + 78, width=width - 104, height=formula_area_h,
            font_size=formula_font,
            color="#0f172a", bold=True, start=start + 0.18
        )
    elif contains_display_formula(display_formula):
        current, layer_num = add_micro_course_safe_text(
            filters, current, layer_num, display_formula,
            x=x + 72, y=y + 88, width=width - 144, height=height - 128,
            max_font=42, min_font=22, color="#0f172a", bold=True, start=start + 0.18
        )
    else:
        current, layer_num = add_bounded_text(
            filters, current, layer_num, display_formula,
            x=x + 72, y=y + 88, width=width - 144, height=height - 128,
            max_font=58, min_font=28, color="#0f172a", bold=True, start=start + 0.18, safety=0.92
        )
    return current, layer_num


def add_micro_course_rule_formula(filters, current, layer_num, formula, *, x, y, width, height, start=0.62, accent="#2563eb"):
    lines = [part.strip() for part in re.split(r"[；;]", normalize_video_text(formula)) if part.strip()]
    if is_pure_math_text(formula) or len(lines) <= 1:
        return add_micro_course_formula_block(filters, current, layer_num, formula, x=x, y=y, width=width, height=height, start=start, accent=accent)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 18, y + 18, width, height), "black@0.045", 34, start)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, width, height), "#ffffff@0.92", 34, start + 0.03)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 26, y + 24, 120, 38), f"{accent}@0.14", 19, start + 0.08)
    current, layer_num = add_filter_drawtext(filters, current, layer_num, "关键式", x=x + 50, y=y + 33, font_size=20, color=accent, bold=True, start=start + 0.10)
    line_h = max(48, int((height - 108) / min(3, len(lines))))
    for idx, line in enumerate(lines[:3]):
        if is_pure_math_text(line):
            current, layer_num = add_formula_overlay(
                filters, current, layer_num, line,
                x=x + 72, y=y + 82 + idx * line_h, width=width - 144, height=line_h,
                font_size=min(44, max(24, int(line_h * 0.62))),
                color="#0f172a", bold=True, start=start + 0.18 + idx * 0.06
            )
        elif contains_display_formula(line):
            current, layer_num = add_micro_course_safe_text(
                filters, current, layer_num, line,
                x=x + 72, y=y + 86 + idx * line_h, width=width - 144, height=line_h - 8,
                max_font=32, min_font=18, color="#0f172a", bold=True, start=start + 0.18 + idx * 0.06
            )
        else:
            current, layer_num = add_bounded_text(
                filters, current, layer_num, line,
                x=x + 72, y=y + 86 + idx * line_h, width=width - 144, height=line_h - 8,
                max_font=38, min_font=22, color="#0f172a", bold=True, start=start + 0.18 + idx * 0.06, safety=0.92
            )
    return current, layer_num


def add_micro_course_image_scene(filters, current, layer_num, *, box, label, start=0.50, accent="#2563eb"):
    x, y, w, h = box
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 16, y + 18, w, h), "black@0.050", 34, start)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, w, h), "white@0.88", 34, start + 0.03)
    current, layer_num = add_filter_visual_contain(filters, current, layer_num, box=(x + 30, y + 34, w - 60, h - 82), opacity=1.0, start=start + 0.08)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 30, y + h - 42, w - 60, 30), f"{accent}@0.12", 15, start + 0.14)
    current, layer_num = add_bounded_text(filters, current, layer_num, label, x=x + 52, y=y + h - 36, width=w - 104, height=20, max_font=18, min_font=14, color=accent, bold=True, start=start + 0.16)
    return current, layer_num


def add_micro_course_diag_line(filters, current, layer_num, x1, y1, x2, y2, color, thickness=10, start=0.0, pieces=26):
    if abs(x2 - x1) < 2 or abs(y2 - y1) < 2:
        return add_filter_axis_line(filters, current, layer_num, x1, y1, x2, y2, color, thickness, start)
    pieces = max(8, int(pieces))
    for idx in range(pieces + 1):
        t = idx / pieces
        px = int(round(x1 + (x2 - x1) * t - thickness / 2))
        py = int(round(y1 + (y2 - y1) * t - thickness / 2))
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (px, py, thickness, thickness), color, "fill", start)
    return current, layer_num


def add_micro_course_area_model(filters, current, layer_num, *, box, start=0.48, accent="#2563eb"):
    x, y, w, h = [int(v) for v in box]
    size = int(min(w * 0.36, h * 0.66))
    sx = x + 44
    sy = y + 86
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (sx, sy, size, size), "#dbeafe@0.90", 24, start + 0.08)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (sx, sy, size, size), accent, 5, start + 0.10)
    step = max(1, size // 3)
    for i in range(1, 3):
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (sx + i * step, sy, 3, size), "#93c5fd@0.90", "fill", start + 0.12 + i * 0.03)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (sx, sy + i * step, size, 3), "#93c5fd@0.90", "fill", start + 0.12 + i * 0.03)
    current, layer_num = add_bounded_text(filters, current, layer_num, "面积 9", x=sx + 38, y=sy + size // 2 - 22, width=size - 76, height=44, max_font=31, min_font=20, color="#08215c", bold=True, start=start + 0.18)
    current, layer_num = add_bounded_text(filters, current, layer_num, "边长 = √9 = 3", x=sx - 12, y=sy + size + 22, width=size + 24, height=36, max_font=25, min_font=17, color="#0f172a", bold=True, start=start + 0.24)

    tx = sx + size + 86
    ty = sy + 24
    base = int(min(w * 0.26, h * 0.48))
    height = int(base * 0.78)
    x0, y0 = tx, ty + height
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (x0, y0, base, 6), "#334155", "fill", start + 0.12)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (x0, y0 - height, 6, height), "#334155", "fill", start + 0.12)
    current, layer_num = add_micro_course_diag_line(filters, current, layer_num, x0 + 3, y0 - height, x0 + base, y0, "#f59e0b", 9, start + 0.18, pieces=24)
    current, layer_num = add_bounded_text(filters, current, layer_num, "直角边 a、b", x=x0 + 16, y=y0 + 20, width=base, height=30, max_font=20, min_font=14, color="#334155", bold=True, start=start + 0.24)
    current, layer_num = add_bounded_text(filters, current, layer_num, "c = √(a²+b²)", x=x0 + base - 96, y=y0 - height // 2 - 36, width=185, height=42, max_font=24, min_font=16, color="#92400e", bold=True, start=start + 0.30)

    px = tx
    py = sy + size - 18
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (px, py, base + 88, 58), "#dcfce7@0.90", 18, start + 0.30)
    current, layer_num = add_bounded_text(filters, current, layer_num, "速度 v = √(2E/m)", x=px + 24, y=py + 15, width=base + 44, height=28, max_font=22, min_font=15, color="#166534", bold=True, start=start + 0.36)
    return current, layer_num


def add_micro_course_arrow(filters, current, layer_num, x1, y1, x2, y2, color, *, thickness=9, start=0.0, label="", label_box=None):
    current, layer_num = add_micro_course_diag_line(filters, current, layer_num, x1, y1, x2, y2, color, thickness, start, pieces=24)
    angle = math.atan2(y2 - y1, x2 - x1)
    head_len = max(24, int(thickness * 4.0))
    wing = 0.58
    for delta in (math.pi - wing, math.pi + wing):
        hx = int(round(x2 + math.cos(angle + delta) * head_len))
        hy = int(round(y2 + math.sin(angle + delta) * head_len))
        current, layer_num = add_micro_course_diag_line(filters, current, layer_num, hx, hy, x2, y2, color, thickness, start + 0.02, pieces=8)
    if label:
        if label_box:
            lx, ly, lw, lh = label_box
        else:
            lx = int((x1 + x2) / 2 - 70)
            ly = int((y1 + y2) / 2 - 42)
            lw, lh = 140, 32
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (lx, ly, lw, lh), "white@0.86", 14, start + 0.04)
        current, layer_num = add_bounded_text(filters, current, layer_num, label, x=lx + 10, y=ly + 6, width=lw - 20, height=lh - 10, max_font=20, min_font=14, color=color, bold=True, start=start + 0.06)
    return current, layer_num


def friction_focus_from_text(text):
    text = normalize_video_text(text)
    if any(keyword in text for keyword in ("公式", "计算", "正压力", "μ", "动摩擦因数")):
        return "formula"
    if any(keyword in text for keyword in ("静摩擦", "趋势", "方向", "未动")):
        return "static"
    if any(keyword in text for keyword in ("增大", "鞋底", "轮胎", "有益")):
        return "increase"
    if any(keyword in text for keyword in ("减小", "润滑", "滚动", "轴承", "有害")):
        return "reduce"
    if any(keyword in text for keyword in ("实验", "测力计", "木块", "数据", "探究")):
        return "experiment"
    return "default"


def add_micro_course_friction_model(filters, current, layer_num, *, box, formula="", focus="default", start=0.48, accent="#2563eb"):
    x, y, w, h = [int(v) for v in box]
    ground_y = y + int(h * 0.68)
    block_w = int(min(w * 0.34, 230))
    block_h = int(min(h * 0.27, 104))
    block_x = x + int(w * 0.34)
    block_y = ground_y - block_h

    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 46, ground_y, w - 92, 22), "#94a3b8@0.45", 11, start + 0.08)
    for idx in range(14):
        tick_x = x + 58 + idx * max(24, (w - 128) // 14)
        current, layer_num = add_micro_course_diag_line(filters, current, layer_num, tick_x, ground_y + 20, tick_x + 26, ground_y + 48, "#64748b@0.72", 4, start + 0.10 + idx * 0.004, pieces=5)

    current, layer_num = add_filter_roundrect(filters, current, layer_num, (block_x + 10, block_y + 12, block_w, block_h), "black@0.065", 24, start + 0.12)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (block_x, block_y, block_w, block_h), "#fef3c7@0.96", 24, start + 0.14)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (block_x + 18, block_y + 22, block_w - 36, 6), "#f59e0b@0.80", "fill", start + 0.18)
    current, layer_num = add_bounded_text(filters, current, layer_num, "木块", x=block_x + 58, y=block_y + 45, width=block_w - 116, height=34, max_font=26, min_font=18, color="#92400e", bold=True, start=start + 0.20)

    mid_x = block_x + block_w // 2
    mid_y = block_y + block_h // 2
    current, layer_num = add_micro_course_arrow(filters, current, layer_num, block_x + block_w + 18, mid_y, block_x + block_w + 176, mid_y, "#2563eb", thickness=10, start=start + 0.24, label="外力 F", label_box=(block_x + block_w + 70, mid_y - 54, 104, 32))
    current, layer_num = add_micro_course_arrow(filters, current, layer_num, block_x - 22, mid_y, block_x - 172, mid_y, "#ef4444", thickness=10, start=start + 0.30, label="摩擦力 f", label_box=(block_x - 172, mid_y - 54, 130, 32))
    current, layer_num = add_micro_course_arrow(filters, current, layer_num, mid_x, block_y - 12, mid_x, block_y - 126, "#16a34a", thickness=9, start=start + 0.36, label="N", label_box=(mid_x + 18, block_y - 112, 58, 30))
    current, layer_num = add_micro_course_arrow(filters, current, layer_num, mid_x, block_y + block_h + 10, mid_x, block_y + block_h + 120, "#475569", thickness=9, start=start + 0.42, label="G", label_box=(mid_x + 18, block_y + block_h + 66, 58, 30))

    formula_text = normalize_video_text(formula)
    if not formula_text:
        if focus == "static":
            formula_text = "0 ≤ fs ≤ μsN"
        elif focus == "formula":
            formula_text = "F = μN"
        else:
            formula_text = "f 阻碍相对运动"
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 72, y + 56, w - 144, 70), f"{accent}@0.12", 24, start + 0.18)
    if contains_math_notation(formula_text) or "μ" in formula_text:
        current, layer_num = add_formula_overlay(
            filters, current, layer_num, formula_text,
            x=x + 96, y=y + 70, width=w - 192, height=42,
            font_size=28, color="#0f172a", bold=True, start=start + 0.24
        )
    else:
        current, layer_num = add_bounded_text(filters, current, layer_num, formula_text, x=x + 96, y=y + 72, width=w - 192, height=36, max_font=26, min_font=17, color="#0f172a", bold=True, start=start + 0.24)

    tips = {
        "formula": "粗糙程度影响 μ，正压力 N 变大时滑动摩擦力也变大。",
        "static": "静摩擦会随外力变化，但不会超过最大静摩擦力。",
        "increase": "增大粗糙程度或压力，可以提升抓地、防滑效果。",
        "reduce": "润滑或改为滚动接触，可以减少能量损耗和磨损。",
        "experiment": "匀速拉动时，测力计示数近似等于滑动摩擦力。",
        "default": "方向判断先看相对运动或相对运动趋势，再取相反方向。",
    }
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 82, y + h - 82, w - 164, 46), "#ecfeff@0.88", 20, start + 0.48)
    current, layer_num = add_bounded_text(filters, current, layer_num, tips.get(focus, tips["default"]), x=x + 112, y=y + h - 70, width=w - 224, height=24, max_font=21, min_font=14, color="#0f766e", bold=True, start=start + 0.52)
    return current, layer_num


def add_micro_course_number_line(filters, current, layer_num, *, box, formula, start=0.48, accent="#7c3aed", mode="domain"):
    x, y, w, h = [int(v) for v in box]
    line_y = y + h // 2 + 42
    line_x = x + 70
    line_w = w - 140
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (line_x, line_y, line_w, 5), "#334155", "fill", start + 0.10)
    ticks = [("-2", 0.10), ("0", 0.30), ("2", 0.50), ("4", 0.70), ("6", 0.90)]
    for label, pos in ticks:
        tx = int(line_x + line_w * pos)
        current, layer_num = add_filter_drawbox(filters, current, layer_num, (tx, line_y - 14, 4, 28), "#334155", "fill", start + 0.14)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, label, x=tx - 12, y=line_y + 24, font_size=20, color="#475569", bold=True, start=start + 0.18)
    start_x = int(line_x + line_w * 0.30)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (start_x, line_y - 12, line_w - (start_x - line_x), 24), f"{accent}@0.20", 12, start + 0.22)
    current, layer_num = add_filter_circle(filters, current, layer_num, start_x, line_y, 12, accent, start + 0.26)
    if contains_math_notation(formula):
        current, layer_num = add_formula_overlay(
            filters, current, layer_num, formula,
            x=x + 72, y=y + 60, width=w - 144, height=54,
            font_size=30, color="#0f172a", bold=True, start=start + 0.30
        )
    else:
        current, layer_num = add_bounded_text(filters, current, layer_num, formula, x=x + 72, y=y + 64, width=w - 144, height=46, max_font=31, min_font=19, color="#0f172a", bold=True, start=start + 0.30)
    hint = "代回验根：负一舍去，二成立" if mode == "equation" else "根号内 ≥ 0，先把允许的 x 范围圈出来"
    current, layer_num = add_bounded_text(filters, current, layer_num, hint, x=x + 72, y=y + h - 68, width=w - 144, height=32, max_font=22, min_font=15, color="#5b21b6", bold=True, start=start + 0.36)
    return current, layer_num


def add_micro_course_root_curve(filters, current, layer_num, *, box, formula, start=0.48, accent="#2563eb"):
    x, y, w, h = [int(v) for v in box]
    axis_y = y + h - 82
    axis_x = x + 82
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (axis_x, axis_y, w - 145, 5), "#334155", "fill", start + 0.10)
    current, layer_num = add_filter_drawbox(filters, current, layer_num, (axis_x + 62, y + 70, 5, h - 145), "#334155", "fill", start + 0.10)
    points = [
        (axis_x + 66, axis_y - 8),
        (axis_x + 132, axis_y - 64),
        (axis_x + 236, axis_y - 112),
        (axis_x + 366, axis_y - 154),
        (axis_x + 516, axis_y - 188),
    ]
    for idx in range(len(points) - 1):
        current, layer_num = add_micro_course_diag_line(filters, current, layer_num, *points[idx], *points[idx + 1], accent, 9, start + 0.18 + idx * 0.04, pieces=12)
    current, layer_num = add_filter_circle(filters, current, layer_num, points[0][0], points[0][1], 11, "#16a34a", start + 0.24)
    if contains_math_notation(formula):
        current, layer_num = add_formula_overlay(
            filters, current, layer_num, formula,
            x=x + 100, y=y + 58, width=w - 200, height=50,
            font_size=28, color="#0f172a", bold=True, start=start + 0.30
        )
    else:
        current, layer_num = add_bounded_text(filters, current, layer_num, formula, x=x + 100, y=y + 64, width=w - 200, height=42, max_font=30, min_font=18, color="#0f172a", bold=True, start=start + 0.30)
    current, layer_num = add_bounded_text(filters, current, layer_num, "起点由定义域决定，曲线只向右延伸", x=x + 110, y=axis_y + 22, width=w - 220, height=30, max_font=21, min_font=14, color="#334155", bold=True, start=start + 0.38)
    return current, layer_num


def add_micro_course_math_visual(filters, current, layer_num, ctx, *, box, start=0.48, accent="#2563eb"):
    x, y, w, h = box
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 16, y + 18, w, h), "black@0.045", 34, start)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, w, h), "white@0.88", 34, start + 0.03)
    title = normalize_video_text(ctx.get("title", ""))
    combined = " ".join([title, *[normalize_video_text(line) for line in ctx.get("rest", [])], *[normalize_video_text(line) for line in ctx.get("points", [])]])
    formula = ctx.get("formula") or ""
    if any(keyword in combined for keyword in ("摩擦", "静摩擦", "滑动摩擦", "滚动摩擦", "粗糙", "正压力", "测力计", "μ")):
        focus = friction_focus_from_text(combined)
        return add_micro_course_friction_model(
            filters, current, layer_num, box=(x, y, w, h), formula=formula,
            focus=focus, start=start, accent=accent
        )
    if any(keyword in combined for keyword in ("应用", "场景", "几何", "物理", "距离", "速度", "面积")):
        return add_micro_course_area_model(filters, current, layer_num, box=(x, y, w, h), start=start, accent=accent)

    if "不等式" in title or ("方程" in title and "函数" not in title):
        expression = formula or ("√(x-2) ≥ 0" if "不等式" in title else "√(x+1) = 3")
        return add_micro_course_number_line(
            filters, current, layer_num, box=(x, y, w, h), formula=expression,
            start=start, accent=accent, mode="equation" if "方程" in title else "domain"
        )

    if "函数" in title:
        return add_micro_course_root_curve(filters, current, layer_num, box=(x, y, w, h), formula=formula or "y = √(x-2)", start=start, accent=accent)

    if "分类" in title:
        entries = [("算术平方根", "√9 = 3"), ("平方根", "x² = 9 → x = ±3"), ("立方根", "³√8 = 2")]
        for idx, (label, formula) in enumerate(entries):
            row_y = y + 70 + idx * 105
            color = ["#2563eb", "#16a34a", "#f59e0b"][idx]
            current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 50, row_y, w - 100, 72), f"{color}@0.12", 24, start + 0.10 + idx * 0.07)
            current, layer_num = add_filter_drawtext(filters, current, layer_num, label, x=x + 82, y=row_y + 21, font_size=24, color=color, bold=True, start=start + 0.14 + idx * 0.07)
            current, layer_num = add_micro_course_safe_text(filters, current, layer_num, formula, x=x + 315, y=row_y + 14, width=w - 390, height=46, max_font=26, min_font=18, color="#0f172a", bold=True, start=start + 0.16 + idx * 0.07)
        return current, layer_num

    if any(keyword in title for keyword in ("乘法", "除法", "加减", "化简")):
        if "乘法" in title:
            formula = "√a × √b = √(a×b)"
        elif "除法" in title:
            formula = "√a ÷ √b = √(a÷b)"
        elif "加减" in title:
            formula = "m√a ± n√a = (m±n)√a"
        elif "化简" in title:
            formula = ctx.get("formula") or "√72 = √(36×2) = 6√2"
        else:
            formula = ctx.get("formula") or "√a 运算先看条件"
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 78, y + 75, w - 156, 120), "#dbeafe@0.70", 30, start + 0.10)
        if is_pure_math_text(formula):
            current, layer_num = add_formula_overlay(
                filters, current, layer_num, formula,
                x=x + 120, y=y + 100, width=w - 240, height=54,
                font_size=34, color="#0f172a", bold=True, start=start + 0.16
            )
        else:
            current, layer_num = add_bounded_text(filters, current, layer_num, formula, x=x + 120, y=y + 105, width=w - 240, height=70, max_font=34, min_font=20, color="#0f172a", bold=True, start=start + 0.16)
        if "除法" in title:
            current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 120, y + 174, 320, 30), "#eff6ff@0.92", 14, start + 0.20)
            current, layer_num = add_bounded_text(
                filters, current, layer_num, "条件：a ≥ 0，b > 0",
                x=x + 136, y=y + 178, width=288, height=20,
                max_font=18, min_font=14, color="#475569", bold=True, start=start + 0.22
            )
        steps = ["确认条件", "变形计算", "回代检验"]
        for idx, step in enumerate(steps):
            step_x = x + 78 + idx * ((w - 156) // 3)
            current, layer_num = add_filter_circle(filters, current, layer_num, step_x + 38, y + 275, 24, ["#2563eb", "#16a34a", "#f59e0b"][idx], start + 0.26 + idx * 0.05)
            current, layer_num = add_filter_drawtext(filters, current, layer_num, str(idx + 1), x=step_x + 29, y=y + 257, font_size=24, color="white", bold=True, start=start + 0.30 + idx * 0.05)
            current, layer_num = add_bounded_text(filters, current, layer_num, step, x=step_x + 76, y=y + 256, width=(w - 156) // 3 - 88, height=36, max_font=21, min_font=15, color="#111827", bold=True, start=start + 0.34 + idx * 0.05)
        return current, layer_num

    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 92, y + 95, w - 184, 126), "#dbeafe@0.66", 34, start + 0.10)
    formula_lines = [part.strip() for part in re.split(r"[；;]", normalize_video_text(ctx.get("formula") or "核心关系：先明确条件，再代入检验")) if part.strip()]
    for idx, line in enumerate(formula_lines[:2]):
        current, layer_num = add_micro_course_safe_text(filters, current, layer_num, line, x=x + 140, y=y + 112 + idx * 56, width=w - 280, height=44, max_font=30, min_font=18, color="#0f172a", bold=True, start=start + 0.16 + idx * 0.05)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (x + 135, y + 270, w - 270, 54), "#dcfce7@0.78", 20, start + 0.26)
    current, layer_num = add_bounded_text(filters, current, layer_num, "先看适用条件，再确定表达和结果范围。", x=x + 170, y=y + 285, width=w - 340, height=26, max_font=23, min_font=17, color="#166534", bold=True, start=start + 0.32)
    return current, layer_num


def layout_micro_course_opener(ctx, slide_data, slide_num, duration, project=None):
    filters, current, layer_num = micro_course_canvas(duration, project, slide_num, slide_data)
    current, layer_num = add_micro_course_header(filters, current, layer_num, ctx["title"], "简明精讲 / 公式理解 / 例题应用", 0.12)
    if ctx["visual_asset"]:
        current, layer_num = add_micro_course_image_scene(filters, current, layer_num, box=(1040, 230, 710, 475), label="本节主题图", start=0.48, accent="#1d4ed8")
        left_w = 760
    else:
        left_w = 1100
    current, layer_num = add_bounded_text(filters, current, layer_num, ctx["lead"], x=155, y=255, width=left_w, height=88, max_font=34, min_font=22, color="#0f172a", bold=True, start=0.42)
    current, layer_num = add_micro_course_point_stack(filters, current, layer_num, ctx["points"][:3], x=155, y=410, width=left_w, accent="#1d70c9", start=0.62)
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (155, 790, 1220, 86), "#dbeafe@0.82", 28, 0.96)
    current, layer_num = add_bounded_text(filters, current, layer_num, "学习路径：符号含义 / 限制条件 / 运算方法 / 题目检验。", x=205, y=812, width=1120, height=42, max_font=28, min_font=20, color="#08215c", bold=True, start=1.02)
    return filters, current, layer_num


def layout_micro_course_formula_lens(ctx, slide_data, slide_num, duration, project=None):
    filters, current, layer_num = micro_course_canvas(duration, project, slide_num, slide_data)
    current, layer_num = add_micro_course_header(filters, current, layer_num, ctx["title"], "先抓条件，再看公式怎么用", 0.12)
    lead_text = micro_course_formula_lead_text(ctx)
    current, layer_num = add_bounded_text(filters, current, layer_num, lead_text, x=110, y=210, width=710, height=104, max_font=28, min_font=19, color="#111827", start=0.38)
    current, layer_num = add_micro_course_rule_formula(filters, current, layer_num, ctx["formula"], x=940, y=225, width=760, height=245, start=0.50, accent="#2563eb")
    labels = ["条件", "表达", "检验"]
    point_source = micro_course_formula_card_details(ctx)
    for idx, label in enumerate(labels):
        x = 175 + idx * 520
        y = 575
        accent = ["#2563eb", "#16a34a", "#f59e0b"][idx]
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, 430, 168), f"{accent}@0.11", 28, 0.72 + idx * 0.08)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, label, x=x + 32, y=y + 24, font_size=28, color=accent, bold=True, start=0.78 + idx * 0.08)
        current, layer_num = add_micro_course_safe_text(filters, current, layer_num, point_source[idx] if idx < len(point_source) else label, x=x + 32, y=y + 64, width=365, height=82, max_font=22, min_font=14, color="#0f172a", bold=True, start=0.84 + idx * 0.08)
    if ctx["examples"]:
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (340, 835, 1240, 70), "#f5f3ff@0.92", 22, 1.02)
        current, layer_num = add_micro_course_safe_text(filters, current, layer_num, f"快速检验：{ctx.get('check') or ctx['examples'][0]}", x=390, y=846, width=1140, height=48, max_font=26, min_font=18, color="#5b21b6", bold=True, start=1.08)
    return filters, current, layer_num


def layout_micro_course_case_scene(ctx, slide_data, slide_num, duration, project=None):
    filters, current, layer_num = micro_course_canvas(duration, project, slide_num, slide_data)
    current, layer_num = add_micro_course_header(filters, current, layer_num, ctx["title"], "用图像和案例降低理解成本", 0.12)
    current, layer_num = add_micro_course_math_visual(filters, current, layer_num, ctx, box=(110, 235, 820, 430), start=0.42, accent="#1d4ed8")
    text_x, text_w = 1015, 735
    current, layer_num = add_bounded_text(filters, current, layer_num, ctx["lead"], x=text_x, y=235, width=text_w, height=92, max_font=31, min_font=20, color="#111827", bold=True, start=0.52)
    for idx, point in enumerate(ctx["points"][:3]):
        y = 382 + idx * 124
        accent = ["#2563eb", "#16a34a", "#f59e0b"][idx]
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (text_x, y, text_w, 104), "white@0.84", 24, 0.68 + idx * 0.08)
        current, layer_num = add_filter_circle(filters, current, layer_num, text_x + 42, y + 52, 18, f"{accent}@0.22", 0.70 + idx * 0.08)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, str(idx + 1), x=text_x + 34, y=y + 37, font_size=22, color=accent, bold=True, start=0.72 + idx * 0.08)
        current, layer_num = add_micro_course_safe_text(filters, current, layer_num, point, x=text_x + 82, y=y + 18, width=text_w - 120, height=68, max_font=23, min_font=15, color="#0f172a", bold=True, start=0.76 + idx * 0.08)
    bottom = ctx.get("check") or (ctx["examples"][0] if ctx["examples"] else (ctx["formula"] or "用一个具体数检验结论是否成立。"))
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (265, 855, 1390, 64), "#ecfeff@0.90", 22, 1.02)
    current, layer_num = add_bounded_text(filters, current, layer_num, f"落地看：{bottom}", x=320, y=872, width=1280, height=30, max_font=24, min_font=17, color="#155e75", bold=True, start=1.08)
    return filters, current, layer_num


def layout_micro_course_example_workbench(ctx, slide_data, slide_num, duration, project=None):
    filters, current, layer_num = micro_course_canvas(duration, project, slide_num, slide_data)
    current, layer_num = add_micro_course_header(filters, current, layer_num, ctx["title"], "例题不单独成格，而是和规则一起演示", 0.12)
    formula = ctx["formula"] or (ctx["examples"][0] if ctx["examples"] else "先列式，再化简，最后验算")
    current, layer_num = add_micro_course_rule_formula(filters, current, layer_num, formula, x=120, y=220, width=700, height=210, start=0.42, accent="#16a34a")
    steps = ctx.get("steps", [])[:3]
    if len(steps) < 3:
        steps.extend(ctx["points"][: 3 - len(steps)])
    if len(steps) < 3:
        steps.extend(["先看条件是否成立", "再把文字翻译成符号", "最后代入数字检验"][: 3 - len(steps)])
    for idx, step in enumerate(steps[:3]):
        x = 910
        y = 235 + idx * 145
        accent = ["#2563eb", "#16a34a", "#f59e0b"][idx]
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (x, y, 780, 105), "white@0.86", 28, 0.52 + idx * 0.10)
        current, layer_num = add_filter_drawtext(filters, current, layer_num, f"Step {idx + 1}", x=x + 34, y=y + 24, font_size=24, color=accent, bold=True, start=0.58 + idx * 0.10)
        current, layer_num = add_micro_course_safe_text(filters, current, layer_num, step, x=x + 155, y=y + 14, width=585, height=70, max_font=24, min_font=15, color="#0f172a", bold=True, start=0.62 + idx * 0.10)
    current, layer_num = add_micro_course_math_visual(filters, current, layer_num, ctx, box=(135, 515, 650, 290), start=0.88, accent="#7c3aed")
    current, layer_num = add_filter_roundrect(filters, current, layer_num, (260, 860, 1400, 60), "#dbeafe@0.86", 20, 1.12)
    current, layer_num = add_bounded_text(filters, current, layer_num, "不要只看答案，重点看每一步用了哪个条件。", x=320, y=876, width=1280, height=28, max_font=24, min_font=18, color="#08215c", bold=True, start=1.18)
    return filters, current, layer_num


def layout_micro_course_concept_bites(ctx, slide_data, slide_num, duration, project=None):
    filters, current, layer_num = micro_course_canvas(duration, project, slide_num, slide_data)
    if normalize_video_text(ctx["title"]) == "目录":
        directory_labels = [label for label in micro_course_directory_labels(ctx.get("rest", []), limit=3) if label]
        subtitle = " / ".join(directory_labels) if directory_labels else "概念路径 / 方法 / 应用"
    else:
        subtitle = "重点信息分层讲清楚"
    current, layer_num = add_micro_course_header(filters, current, layer_num, ctx["title"], subtitle, 0.12)
    current, layer_num = add_bounded_text(filters, current, layer_num, ctx["lead"], x=170, y=235, width=780, height=80, max_font=32, min_font=21, color="#111827", bold=True, start=0.42)
    center_text = ctx["formula"] or ctx["points"][0]
    current, layer_num = add_micro_course_rule_formula(filters, current, layer_num, center_text, x=1060, y=235, width=570, height=225, start=0.50, accent="#7c3aed")
    positions = [(210, 505), (735, 575), (1260, 505)]
    for idx, point in enumerate(ctx["points"][:3]):
        x, y = positions[idx]
        accent = ["#2563eb", "#16a34a", "#f59e0b"][idx]
        current, layer_num = add_filter_circle(filters, current, layer_num, x + 165, y + 95, 112, f"{accent}@0.14", 0.68 + idx * 0.08)
        current, layer_num = add_filter_circle(filters, current, layer_num, x + 165, y + 95, 82, "white@0.86", 0.72 + idx * 0.08)
        current, layer_num = add_bounded_text(filters, current, layer_num, point, x=x + 55, y=y + 72, width=220, height=46, max_font=24, min_font=16, color="#0f172a", bold=True, start=0.78 + idx * 0.08)
    bottom_text = ctx.get("check") or (ctx["examples"][0] if ctx["examples"] else "")
    if bottom_text:
        current, layer_num = add_filter_roundrect(filters, current, layer_num, (420, 845, 1080, 64), "#f5f3ff@0.90", 22, 1.02)
        current, layer_num = add_micro_course_safe_text(filters, current, layer_num, f"讲解路径：{bottom_text}", x=475, y=854, width=970, height=46, max_font=24, min_font=17, color="#5b21b6", bold=True, start=1.08)
    return filters, current, layer_num


def layout_micro_course(slide_data, slide_num, duration, project=None, recommendation=None):
    ctx = micro_course_build_context(slide_data, slide_num, project)
    if ctx["kind"] == "opener":
        return layout_micro_course_opener(ctx, slide_data, slide_num, duration, project)
    if ctx["kind"] == "case":
        return layout_micro_course_case_scene(ctx, slide_data, slide_num, duration, project)
    if ctx["kind"] == "example":
        return layout_micro_course_example_workbench(ctx, slide_data, slide_num, duration, project)
    if ctx["kind"] == "formula":
        return layout_micro_course_formula_lens(ctx, slide_data, slide_num, duration, project)
    return layout_micro_course_concept_bites(ctx, slide_data, slide_num, duration, project)


def layout_diverse(slide_data, slide_num, duration, project=None, recommendation=None):
    component = selected_visual_component(recommendation)
    kind = adaptive_layout_kind(slide_data, slide_num, project, recommendation)
    title, rest = slide_context(slide_data, slide_num, project)
    probe_cards = content_cards_from_lines(rest, limit=5)
    formula_like = is_formula_rule_text(title + " " + " ".join(rest))
    preserve_special_radial = component == "radial_concept_map" and is_radical_division_rule(title, rest)
    if not preserve_special_radial and component in {"radial_concept_map", "application_storyboard"} and cards_are_sparse_or_unstable(probe_cards):
        component = "formula_walkthrough" if formula_like else "rounded_step_cards"
    if not preserve_special_radial and formula_like and component in {"radial_concept_map", "application_storyboard", "rounded_step_cards"}:
        component = "formula_walkthrough"
    if component == "blackboard_derivation":
        return layout_diverse_blackboard_derivation(slide_data, slide_num, duration, project)
    if component == "formula_walkthrough":
        return layout_diverse_formula_walkthrough(slide_data, slide_num, duration, project)
    if component == "checkpoint_ladder":
        return layout_diverse_checkpoint_ladder(slide_data, slide_num, duration, project)
    if component == "radial_concept_map":
        return layout_diverse_radial_concept_map(slide_data, slide_num, duration, project)
    if component == "magazine_spread":
        return layout_diverse_editorial(slide_data, slide_num, duration, project)
    if component == "rounded_step_cards":
        return layout_diverse_rounded_step_cards(slide_data, slide_num, duration, project)
    if component == "misconception_compare":
        return layout_diverse_misconception_compare(slide_data, slide_num, duration, project)
    if component == "application_storyboard":
        return layout_diverse_application_storyboard(slide_data, slide_num, duration, project)
    if slide_num == 2:
        return layout_diverse_soft_bubbles(slide_data, slide_num, duration, project)
    if slide_num == 4:
        return layout_diverse_soft_bubbles(slide_data, slide_num, duration, project)
    if slide_num == 5:
        return layout_diverse_capsule_flow(slide_data, slide_num, duration, project)
    if slide_num == 8:
        return layout_diverse_roster(slide_data, slide_num, duration, project)
    if slide_num == 9:
        return layout_diverse_subway_roadmap(slide_data, slide_num, duration, project)
    if slide_num == 1 or kind == "hero":
        return layout_diverse_editorial(slide_data, slide_num, duration, project)
    if component == "problem_stack" or kind == "problem":
        return layout_diverse_incident_board(slide_data, slide_num, duration, project)
    if component in {"lifecycle_loop", "solution_flow", "process_flow", "flywheel"} and slide_num != 9:
        return layout_diverse_orbit_loop(slide_data, slide_num, duration, project)
    if component in {"two_column_compare", "before_after"} or kind == "matrix":
        return layout_diverse_soft_bubbles(slide_data, slide_num, duration, project)
    if component == "market_dashboard" or kind == "metrics":
        return layout_diverse_dashboard(slide_data, slide_num, duration, project)
    if kind == "business":
        return layout_diverse_flow_network(slide_data, slide_num, duration, project)
    if kind == "team":
        return layout_diverse_roster(slide_data, slide_num, duration, project)
    if component in {"roadmap_timeline", "timeline"} or slide_num == 9 or kind == "process":
        return layout_diverse_capsule_flow(slide_data, slide_num, duration, project)
    return layout_diverse_soft_bubbles(slide_data, slide_num, duration, project)


def command_text(cmd):
    return " ".join(str(part) for part in cmd)


def process_tail(result, limit=3000):
    text = (result.stderr or result.stdout or "").strip()
    if len(text) > limit:
        return text[-limit:]
    return text


def write_process_log(log_path, cmd, result, cwd=None, context=""):
    if not log_path:
        return
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    content = [
        f"context: {context}" if context else "context:",
        f"cwd: {cwd or ''}",
        f"returncode: {result.returncode}",
        f"command: {command_text(cmd)}",
        "",
        "stdout:",
        result.stdout or "",
        "",
        "stderr:",
        result.stderr or "",
    ]
    log_path.write_text("\n".join(content), encoding="utf-8", errors="ignore")


def run_subprocess(cmd, cwd=None, log_path=None, context=""):
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if log_path or result.returncode != 0:
        write_process_log(log_path, cmd, result, cwd=cwd, context=context)
    if result.returncode != 0:
        tail = process_tail(result)
        log_note = f" Log: {log_path}" if log_path else ""
        raise RuntimeError(f"{context or 'Command failed'} (exit {result.returncode}).{log_note}\n{tail}")
    return result


def probe_duration_or_none(media_path):
    if not media_path:
        return None
    media_path = Path(media_path)
    if not media_path.exists():
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(media_path)],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", check=True
        )
        duration = float(json.loads(result.stdout)["format"]["duration"])
        if duration > 0:
            return duration
    except Exception:
        return None
    return None


def validate_media_file(path, expected_duration=None, min_size=MIN_SEGMENT_BYTES):
    path = Path(path)
    if not path.exists():
        return False, None, "missing file"
    size = path.stat().st_size
    if size < min_size:
        return False, None, f"too small ({size} bytes)"
    duration = probe_duration_or_none(path)
    if duration is None:
        return False, None, "ffprobe duration failed"
    if duration < 0.5:
        return False, duration, f"duration too short ({duration:.2f}s)"
    if expected_duration is not None:
        expected_duration = float(expected_duration)
        min_duration = max(0.5, expected_duration * 0.5)
        if duration < min_duration:
            return False, duration, f"duration {duration:.2f}s below expected {expected_duration:.2f}s"
    return True, duration, ""


def require_valid_segment(path, expected_duration):
    ok, duration, reason = validate_media_file(path, expected_duration=expected_duration)
    if not ok:
        raise RuntimeError(f"Invalid rendered segment: {path} ({reason})")
    return duration


def atomic_copy_file(src, dst, run_id):
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.{run_id}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        shutil.copy2(src, tmp)
        ok, _, reason = validate_media_file(tmp)
        if not ok:
            raise RuntimeError(f"Invalid copied cache file: {tmp} ({reason})")
        tmp.replace(dst)
    finally:
        if tmp.exists():
            tmp.unlink()


def probe_duration(audio_path):
    duration = probe_duration_or_none(audio_path)
    if duration is not None:
        return duration
    """获取音频时长"""
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
    """转义FFmpeg drawtext文本"""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
    )


def split_text(text, max_chars):
    """按字符数切分文本"""
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
    """按中文句子分隔符切分文本"""
    sentences = re.split(r'([。！？])', text)
    result = []
    for i in range(0, len(sentences)-1, 2):
        if sentences[i].strip():
            result.append(sentences[i] + (sentences[i+1] if i+1 < len(sentences) else ''))
    if len(sentences) % 2 == 1 and sentences[-1].strip():
        result.append(sentences[-1])
    return [s.strip() for s in result if s.strip()]


def choose_layout(slide_data, recommendation=None):
    """选择布局类型，优先使用大模型/组件推荐结果。"""
    component_id = recommended_component(recommendation)
    if component_id in COMPONENT_LAYOUT_MAP:
        return COMPONENT_LAYOUT_MAP[component_id]

    bullets = slide_data.get('bullets', [])
    paragraphs = slide_data.get('paragraphs', [])

    if len(bullets) >= 3:
        return 'three_column'
    elif len(paragraphs) > 0 and len(paragraphs[0]) > 100:
        return 'three_column'
    elif len(bullets) == 2 or len(paragraphs) == 2:
        return 'two_column'
    else:
        return 'single_column'


def generate_base_layout(duration, slide_num):
    """生成基础布局 - 渐变背景"""
    filters = []
    # 渐变背景（从浅蓝到白色）
    filters.append(f"color=c=#e8f4f8:s=1920x1080:d={duration}[bg]")
    # 顶部装饰条
    filters.append("[bg]drawbox=x=0:y=0:w=1920:h=60:color=#2196f3:t=fill[bg1]")
    # 标签背景（圆角效果用多个box模拟）
    filters.append("[bg1]drawbox=x=100:y=75:w=200:h=55:color=#ff9800:t=fill[bg2]")
    filters.append(
        "[bg2]drawtext=text='知识要点':"
        f"fontfile={CHINESE_FONT_BOLD}:fontsize=36:fontcolor=white:"
        "x=150:y=88[v0]"
    )
    return filters, "v0", 1


def layout_three_column(filters, current, layer_num, slide_data, slide_num):
    """三栏卡片布局 - 优化版"""
    title = slide_data.get('title', f'第{slide_num}页')
    bullets = slide_data.get('bullets', [])
    paragraphs = slide_data.get('paragraphs', [])

    # 标题
    title_esc = escape_text(title)
    filters.append(
        f"[{current}]drawtext=text='{title_esc}':"
        f"fontfile={CHINESE_FONT_BOLD}:fontsize=56:fontcolor=#1a1a1a:"
        f"x=100:y=170[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 副标题
    filters.append(
        f"[{current}]drawtext=text='重点看这几个维度':"
        f"fontfile={CHINESE_FONT}:fontsize=28:fontcolor=#666666:"
        f"x=100:y=240[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 内容分配
    if bullets and len(bullets) >= 3:
        contents = bullets[:3]
    elif paragraphs:
        sentences = split_into_sentences(paragraphs[0])
        if len(sentences) >= 3:
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

    # 三个卡片 - 优化间距和尺寸
    card_colors = ['#2196f3', '#4caf50', '#ff9800']  # 蓝、绿、橙
    for i in range(3):
        card_x = 80 + i * 600
        card_y = 320

        # 卡片阴影（模拟）
        filters.append(
            f"[{current}]drawbox=x={card_x+8}:y={card_y+8}:w=540:h=640:color=#00000040:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 卡片背景
        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=540:h=640:color=white:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 卡片边框
        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=540:h=640:color=#e0e0e0:t=3[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 彩色图标背景
        filters.append(
            f"[{current}]drawbox=x={card_x+30}:y={card_y+30}:w=70:h=70:color={card_colors[i]}:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 图标编号
        filters.append(
            f"[{current}]drawtext=text='0{i+1}':"
            f"fontfile={CHINESE_FONT_BOLD}:fontsize=42:fontcolor=white:"
            f"x={card_x+48}:y={card_y+44}[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 卡片内容
        if i < len(contents) and contents[i]:
            lines = split_text(contents[i][:180], 14)
            y = card_y + 130
            for line in lines[:10]:
                line_esc = escape_text(line)
                filters.append(
                    f"[{current}]drawtext=text='{line_esc}':"
                    f"fontfile={CHINESE_FONT}:fontsize=28:fontcolor=#333333:"
                    f"x={card_x+30}:y={y}[v{layer_num}]"
                )
                current = f"v{layer_num}"
                layer_num += 1
                y += 50

    return filters, current, layer_num


def layout_two_column(filters, current, layer_num, slide_data, slide_num):
    """双栏布局 - 优化版"""
    title = slide_data.get('title', f'第{slide_num}页')
    bullets = slide_data.get('bullets', [])
    paragraphs = slide_data.get('paragraphs', [])

    # 标题
    title_esc = escape_text(title)
    filters.append(
        f"[{current}]drawtext=text='{title_esc}':"
        f"fontfile={CHINESE_FONT_BOLD}:fontsize=56:fontcolor=#1a1a1a:"
        f"x=100:y=170[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 内容分配
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

    # 两个大卡片
    card_colors = ['#2196f3', '#4caf50']
    for i in range(2):
        card_x = 120 + i * 920
        card_y = 280

        # 卡片阴影
        filters.append(
            f"[{current}]drawbox=x={card_x+10}:y={card_y+10}:w=860:h=700:color=#00000040:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 卡片背景
        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=860:h=700:color=white:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 卡片边框
        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=860:h=700:color=#e0e0e0:t=3[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 顶部彩色条
        filters.append(
            f"[{current}]drawbox=x={card_x}:y={card_y}:w=860:h=8:color={card_colors[i]}:t=fill[v{layer_num}]"
        )
        current = f"v{layer_num}"
        layer_num += 1

        # 卡片内容
        if i < len(contents) and contents[i]:
            lines = split_text(contents[i][:250], 20)
            y = card_y + 50
            for line in lines[:14]:
                line_esc = escape_text(line)
                filters.append(
                    f"[{current}]drawtext=text='{line_esc}':"
                    f"fontfile={CHINESE_FONT}:fontsize=30:fontcolor=#333333:"
                    f"x={card_x+40}:y={y}[v{layer_num}]"
                )
                current = f"v{layer_num}"
                layer_num += 1
                y += 48

    return filters, current, layer_num


def layout_single_column(filters, current, layer_num, slide_data, slide_num):
    """单栏布局 - 优化版"""
    title = slide_data.get('title', f'第{slide_num}页')
    paragraphs = slide_data.get('paragraphs', [])

    # 标题（居中）
    title_esc = escape_text(title)
    filters.append(
        f"[{current}]drawtext=text='{title_esc}':"
        f"fontfile={CHINESE_FONT_BOLD}:fontsize=64:fontcolor=#1a1a1a:"
        f"x=(w-text_w)/2:y=180[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 装饰线
    filters.append(
        f"[{current}]drawbox=x=860:y=260:w=200:h=4:color=#2196f3:t=fill[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 大内容框阴影
    filters.append(
        f"[{current}]drawbox=x=210:y=330:w=1500:h=600:color=#00000040:t=fill[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 大内容框背景
    filters.append(
        f"[{current}]drawbox=x=200:y=320:w=1520:h=600:color=white:t=fill[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 内容框边框
    filters.append(
        f"[{current}]drawbox=x=200:y=320:w=1520:h=600:color=#e0e0e0:t=3[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 顶部彩色条
    filters.append(
        f"[{current}]drawbox=x=200:y=320:w=1520:h=10:color=#4caf50:t=fill[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 内容
    if paragraphs:
        lines = split_text(paragraphs[0][:350], 28)
        y = 380
        for line in lines[:11]:
            line_esc = escape_text(line)
            filters.append(
                f"[{current}]drawtext=text='{line_esc}':"
                f"fontfile={CHINESE_FONT}:fontsize=32:fontcolor=#333333:"
                f"x=250:y={y}[v{layer_num}]"
            )
            current = f"v{layer_num}"
            layer_num += 1
            y += 52

    return filters, current, layer_num


def generate_preserved_slide(project, slide_num, audio_path, output_path, duration, slide_data, recommendation=None, log_path=None):
    """Use the existing rendered slide image instead of redrawing it."""
    image_path = slide_art_asset(project, slide_num)
    if not image_path or not image_path.exists():
        return None

    theme = image_theme(str(image_path))
    vf, applied_effect = build_preserved_slide_filter(slide_data, recommendation, duration, theme)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
    ]
    if audio_path and audio_path.exists():
        cmd.extend(["-i", str(audio_path)])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    cmd.extend([
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration), "-shortest", str(output_path)
    ])
    run_subprocess(cmd, log_path=log_path, context=f"render preserved slide {slide_num}")
    return applied_effect


def generate_slide(slide_data, audio_path, output_path, duration, slide_num, recommendation=None, project=None, style=None, log_path=None):
    """生成单个幻灯片视频"""
    RENDER_CONTEXT.project = project
    RENDER_CONTEXT.formula_assets = []
    visual_asset = micro_course_visual_asset(project, slide_num, slide_data) if style == "micro-course" else slide_visual_asset_for_layout(project, slide_num, slide_data)
    if style == "adaptive":
        layout_type = "adaptive"
    elif style == "diverse":
        layout_type = "diverse"
    elif style == "micro-course":
        layout_type = "micro_course"
    elif style == "clean-cards":
        layout_type = "clean_cards"
    else:
        layout_type = choose_layout(slide_data, recommendation)
    if layout_type == "preserve_slide" and project:
        applied_effect = generate_preserved_slide(
            project, slide_num, audio_path, output_path, duration, slide_data, recommendation, log_path
        )
        if applied_effect:
            return f"{layout_type}+{applied_effect}"
        layout_type = "single_column"

    if layout_type == "diverse":
        filters, current, layer_num = layout_diverse(slide_data, slide_num, duration, project, recommendation)
    elif layout_type == "adaptive":
        filters, current, layer_num = layout_adaptive(slide_data, slide_num, duration, project, recommendation)
    elif layout_type == "micro_course":
        filters, current, layer_num = layout_micro_course(slide_data, slide_num, duration, project, recommendation)
    elif layout_type == "clean_cards":
        filters, current, layer_num = layout_clean_cards(slide_data, slide_num, duration, project)
    else:
        filters, current, layer_num = generate_base_layout(duration, slide_num)

    if layout_type == 'three_column':
        filters, current, layer_num = layout_three_column(filters, current, layer_num, slide_data, slide_num)
    elif layout_type == 'two_column':
        filters, current, layer_num = layout_two_column(filters, current, layer_num, slide_data, slide_num)
    elif layout_type not in {"clean_cards", "adaptive", "diverse", "micro_course"}:
        filters, current, layer_num = layout_single_column(filters, current, layer_num, slide_data, slide_num)

    formula_assets = list(getattr(RENDER_CONTEXT, "formula_assets", []))
    filter_complex = ";".join(filters)
    filter_script_path = None
    cmd = ["ffmpeg", "-y"]
    next_input_index = 0
    if visual_asset:
        cmd.extend(["-loop", "1", "-i", str(visual_asset)])
        next_input_index += 1
    for idx, formula_asset in enumerate(formula_assets):
        cmd.extend(["-loop", "1", "-i", str(formula_asset)])
        filter_complex = filter_complex.replace(f"[formula_in{idx}]", f"[{next_input_index}:v]")
        next_input_index += 1
    audio_input_index = next_input_index
    if audio_path and audio_path.exists():
        cmd.extend(["-i", str(audio_path)])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    if len(filter_complex) > 6000:
        filter_script_path = output_path.with_name(f"{output_path.stem}_filter_complex.txt")
        filter_script_path.write_text(filter_complex, encoding="utf-8")
        cmd.extend(["-filter_complex_script", str(filter_script_path)])
    else:
        cmd.extend(["-filter_complex", filter_complex])

    cmd.extend([
        "-map", f"[{current}]", "-map", f"{audio_input_index}:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration), "-shortest", str(output_path)
    ])

    run_subprocess(cmd, log_path=log_path, context=f"render slide {slide_num}")
    return layout_type


def escape_ass_text(text):
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    escaped = []
    for line in lines:
        escaped.append(
            line
            .replace("\\", "\\\\")
            .replace("{", "\\{")
            .replace("}", "\\}")
        )
    return "\\N".join(escaped)


def generate_ass_from_srt(srt_path, output_path, config):
    """SRT转ASS字幕"""
    ass_header = f"""[Script Info]
Title: Generated Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{config.subtitle_font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,{config.subtitle_margin_lr},{config.subtitle_margin_lr},{config.subtitle_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    srt_content = srt_path.read_text(encoding='utf-8')
    lines = srt_content.strip().split('\n\n')
    ass_events = []

    for block in lines:
        parts = block.split('\n', 2)
        if len(parts) < 3:
            continue

        timestamp_line = parts[1]
        if '-->' not in timestamp_line:
            continue

        start, end = timestamp_line.split('-->')
        start = start.strip().replace(',', '.')[:-1]
        end = end.strip().replace(',', '.')[:-1]
        text = escape_ass_text(parts[2])

        ass_events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    ass_content = ass_header + '\n'.join(ass_events)
    output_path.write_text(ass_content, encoding='utf-8')


def burn_subtitles(video_path, ass_path, output_path, log_path=None):
    """烧录字幕到视频"""
    run_subprocess([
        "ffmpeg", "-y", "-i", video_path.name,
        "-vf", f"ass={ass_path.name}",
        "-c:a", "copy", output_path.name
    ], cwd=video_path.parent, log_path=log_path, context="burn subtitles")


def standardize_audio(input_path, output_path, log_path=None):
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    same_output = input_path.resolve() == output_path.resolve()
    target_path = output_path
    if same_output:
        target_path = output_path.with_name(
            f"{output_path.stem}.standardizing.{os.getpid()}.{int(time.time())}{output_path.suffix}"
        )
    try:
        run_subprocess([
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "copy",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(target_path),
        ], log_path=log_path, context="standardize audio")
        if same_output:
            target_path.replace(output_path)
    finally:
        if same_output and target_path.exists():
            target_path.unlink()
    return output_path


def format_srt_timestamp(seconds):
    total_ms = max(0, int(round(float(seconds) * 1000)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_subtitle_text(text):
    text = normalize_video_text(text)
    if not text:
        return []

    clauses = []
    current = ""
    for ch in text:
        current += ch
        if ch in "。！？；;，,、":
            clean = current.strip()
            if clean:
                clauses.append(clean)
            current = ""
    if current.strip():
        clauses.append(current.strip())

    chunks = []
    target_units = 24
    for clause in clauses or [text]:
        if visual_text_len(clause) <= target_units + 4:
            chunks.append(clause)
            continue
        chunks.extend(split_long_body_chunks(clause, limit=4, target_units=target_units))

    merged = []
    for chunk in chunks:
        chunk = normalize_video_text(chunk)
        if not chunk:
            continue
        if merged and visual_text_len(merged[-1]) + visual_text_len(chunk) <= 20:
            merged[-1] = normalize_video_text(merged[-1] + chunk)
        else:
            merged.append(chunk)
    return merged[:12] or [text]


def ffmpeg_null_output():
    return "NUL" if sys.platform.startswith("win") else "/dev/null"


def detect_audio_silence_cutpoints(audio_path, duration):
    if not audio_path or not audio_path.exists() or duration <= 1.0:
        return []
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats",
                "-i", str(audio_path),
                "-af", "silencedetect=noise=-34dB:d=0.18",
                "-f", "null", ffmpeg_null_output(),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except Exception:
        return []

    log = f"{result.stderr or ''}\n{result.stdout or ''}"
    silences = []
    silence_start = None
    for line in log.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            silence_start = float(start_match.group(1))
        end_match = re.search(r"silence_end:\s*([0-9.]+)", line)
        if end_match and silence_start is not None:
            silence_end = float(end_match.group(1))
            if silence_end > silence_start:
                silences.append((silence_start, silence_end))
            silence_start = None

    cutpoints = []
    for start, end in silences:
        midpoint = (start + end) / 2
        if 0.45 <= midpoint <= duration - 0.45:
            cutpoints.append(midpoint)
    return cutpoints


def subtitle_cuts_from_audio(duration, weights, cutpoints):
    needed = len(weights) - 1
    if needed <= 0:
        return []
    total_weight = sum(weights) or float(len(weights))
    min_span = max(0.55, min(1.15, duration / max(1, len(weights) * 2.4)))
    cuts = []
    previous = 0.0

    for idx in range(needed):
        target = duration * sum(weights[:idx + 1]) / total_weight
        max_cut = duration - (needed - idx) * min_span
        valid = [
            cut for cut in cutpoints
            if previous + min_span <= cut <= max_cut
        ]
        if valid:
            cut = min(valid, key=lambda value: abs(value - target))
        else:
            cut = target
        cut = max(previous + min_span, min(max_cut, cut))
        cuts.append(cut)
        previous = cut
    return cuts


def read_note_text(project, slide_num):
    note_path = project / "notes" / f"page_{slide_num:02d}.md"
    if not note_path.exists():
        return ""
    text = note_path.read_text(encoding="utf-8", errors="ignore")
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("![") or line.startswith("<"):
            continue
        line = re.sub(r"^[-*+]\s+", "", line).strip()
        if line:
            lines.append(line)
    return normalize_video_text(" ".join(lines))


def build_srt_from_notes(project, rendered_slides, output_path):
    blocks = []
    seq = 1
    offset = 0.0
    audio_dir = project / "audio"
    for item in rendered_slides:
        if len(item) >= 3:
            slide_num, duration, audio_path = item[:3]
        else:
            slide_num, duration = item[:2]
            audio_path = audio_dir / f"page_{int(slide_num):02d}.mp3"
        text = read_note_text(project, slide_num)
        if not text:
            offset += duration
            continue

        parts = split_subtitle_text(text)
        parts = [part for part in parts if part and not is_noise_line(part)]
        if not parts:
            offset += duration
            continue

        weights = [max(1.0, visual_text_len(part)) for part in parts]
        cutpoints = detect_audio_silence_cutpoints(audio_path, duration)
        cuts = subtitle_cuts_from_audio(duration, weights, cutpoints)
        local_times = [0.0] + cuts + [duration]
        slide_end = offset + duration
        for idx, part in enumerate(parts):
            cursor = offset + local_times[idx]
            end = offset + local_times[idx + 1]
            if idx == len(parts) - 1:
                end = slide_end
            if end <= cursor:
                end = min(slide_end, cursor + 0.8)
            blocks.append(
                f"{seq}\n{format_srt_timestamp(cursor)} --> {format_srt_timestamp(end)}\n{part}\n"
            )
            seq += 1
            cursor = end
        offset += duration

    if not blocks:
        return None
    output_path.write_text("\n".join(blocks), encoding="utf-8")
    return output_path


def build_srt_from_script_plan(project, rendered_slides, output_path):
    plan_path = project / "video_script_plan.json"
    if not plan_path.exists():
        return None
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    plans = {
        int(item.get("slide_number", 0)): item
        for item in data.get("slides", [])
        if item.get("slide_number")
    }
    blocks = []
    seq = 1
    offset = 0.0
    audio_dir = project / "audio"
    for item in rendered_slides:
        if len(item) >= 3:
            slide_num, duration, audio_path = item[:3]
        else:
            slide_num, duration = item[:2]
            audio_path = audio_dir / f"page_{int(slide_num):02d}.mp3"
        plan = plans.get(slide_num, {})
        chunks = plan.get("subtitle_chunks") or []
        expanded_chunks = []
        for chunk in chunks:
            text = restore_math_notation_for_subtitles(chunk.get("text", ""))
            if is_noise_line(text):
                continue
            start = max(0.0, min(float(chunk.get("start", 0.0) or 0.0), duration))
            end = max(0.0, min(float(chunk.get("end", duration) or duration), duration))
            if end <= start:
                continue
            subtitle_parts = split_subtitle_text(text)
            span = end - start
            part_count = max(1, len(subtitle_parts))
            for idx, part in enumerate(subtitle_parts):
                expanded_chunks.append(
                    {
                        "start": start + span * idx / part_count,
                        "end": start + span * (idx + 1) / part_count,
                        "text": part,
                    }
                )
        if not expanded_chunks:
            offset += duration
            continue
        weights = [
            max(1.0, visual_text_len(chunk["text"]))
            for chunk in expanded_chunks
        ]
        cutpoints = detect_audio_silence_cutpoints(audio_path, duration)
        cuts = subtitle_cuts_from_audio(duration, weights, cutpoints)
        local_times = [0.0] + cuts + [duration]
        slide_end = offset + duration
        for idx, chunk in enumerate(expanded_chunks):
            part_start = offset + local_times[idx]
            part_end = offset + local_times[idx + 1]
            if idx == len(expanded_chunks) - 1:
                part_end = slide_end
            if part_end <= part_start:
                part_end = min(slide_end, part_start + 0.8)
            blocks.append(
                f"{seq}\n{format_srt_timestamp(part_start)} --> {format_srt_timestamp(part_end)}\n{chunk['text']}\n"
            )
            seq += 1
        offset += duration

    if not blocks:
        return None
    output_path.write_text("\n".join(blocks), encoding="utf-8")
    return output_path


def ffprobe_json(media_path):
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
        if result.returncode != 0:
            return {"error": process_tail(result)}
        return json.loads(result.stdout or "{}")
    except Exception as exc:
        return {"error": str(exc)}


def count_srt_blocks(srt_path):
    if not srt_path or not Path(srt_path).exists():
        return 0
    content = Path(srt_path).read_text(encoding="utf-8", errors="ignore").strip()
    if not content:
        return 0
    return len([block for block in re.split(r"\n\s*\n", content) if block.strip()])


def extract_video_qa_frames(project, video_path, log_dir=None, count=4):
    qa_dir = project / "qa_frames"
    qa_dir.mkdir(exist_ok=True)
    duration = probe_duration_or_none(video_path) or 0.0
    if duration <= 1.0:
        return []

    raw_offsets = [min(12.0, duration * 0.12), duration * 0.25, duration * 0.50, duration * 0.75]
    offsets = []
    for value in raw_offsets[:count]:
        sec = max(0.2, min(duration - 0.4, float(value)))
        if not any(abs(sec - existing) < 0.8 for existing in offsets):
            offsets.append(sec)

    frames = []
    for idx, sec in enumerate(offsets, 1):
        frame_path = qa_dir / f"{video_path.stem}_qa_{idx}_{int(sec)}s.png"
        try:
            run_subprocess(
                [
                    "ffmpeg", "-y", "-ss", f"{sec:.2f}",
                    "-i", str(video_path),
                    "-frames:v", "1", "-update", "1",
                    str(frame_path),
                ],
                log_path=(log_dir / f"qa_frame_{idx}.log") if log_dir else None,
                context=f"extract QA frame {idx}",
            )
            if frame_path.exists():
                frames.append(str(frame_path))
        except Exception:
            continue
    return frames


def write_video_quality_report(project, video_path, rendered_slides, srt_file=None, log_dir=None):
    exports_dir = project / "exports"
    frames = extract_video_qa_frames(project, video_path, log_dir=log_dir)
    probe = ffprobe_json(video_path)
    report = {
        "video": str(video_path),
        "size_bytes": video_path.stat().st_size if video_path.exists() else 0,
        "slide_count": len(rendered_slides),
        "rendered_slides": [
            {"slide": int(item[0]), "duration": round(float(item[1]), 3)}
            for item in rendered_slides
        ],
        "subtitle_file": str(srt_file) if srt_file and Path(srt_file).exists() else "",
        "subtitle_blocks": count_srt_blocks(srt_file),
        "qa_frames": frames,
        "probe": probe,
    }
    report_path = exports_dir / f"{video_path.stem}_qa.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report_path


def parse_args():
    parser = argparse.ArgumentParser(description="Render PPT Master project to video")
    parser.add_argument("project_path", type=Path)
    parser.add_argument("--font-size", type=int, default=None)
    parser.add_argument("--style", default=None)
    parser.add_argument("--preview-slides", type=int, default=None, help="Only render the first N slides")
    parser.add_argument("--force-render", action="store_true", help="Ignore cached per-slide segments")
    parser.add_argument("--jobs", type=int, default=None, help="Number of slide segments to render in parallel")
    parser.add_argument("--standardize-audio", action="store_true", help="Create a loudness-normalized 48kHz stereo final copy")
    parser.add_argument("--standardized-output", type=Path, default=None, help="Output path/name for --standardize-audio")
    parser.add_argument("--no-qa-frames", action="store_true", help="Skip automatic QA frame extraction and media report")
    return parser.parse_args()


def load_render_manifest(temp_dir, style_slug):
    path = temp_dir / f"render_manifest_{style_slug or 'default'}.json"
    if not path.exists():
        return path, {}
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return path, {}


def save_render_manifest(path, manifest):
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_fingerprint(path):
    if not path or not Path(path).exists():
        return None
    stat = Path(path).stat()
    return {"path": str(Path(path)), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def slide_render_key(project, slide_num, slide_data, audio, recommendation, duration, style):
    art = slide_art_asset(project, slide_num)
    visual = micro_course_visual_asset(project, slide_num, slide_data) if style == "micro-course" else slide_visual_asset_for_layout(project, slide_num, slide_data)
    payload = {
        "version": 61,
        "slide_number": slide_num,
        "slide": slide_data,
        "source_lines": source_slide_lines(project, slide_num),
        "slide_art": file_fingerprint(art),
        "visual_asset": file_fingerprint(visual),
        "audio": file_fingerprint(audio),
        "recommendation": recommendation,
        "duration": round(float(duration), 3),
        "style": style,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def render_slide_segment(task):
    slide_num = task["slide_num"]
    duration = task["duration"]
    segment = Path(task["segment"])
    run_id = task["run_id"]
    tmp_segment = segment.with_name(
        f"{segment.stem}.{run_id}.{os.getpid()}.{threading.get_ident()}.tmp{segment.suffix}"
    )
    try:
        layout = generate_slide(
            task["slide"],
            task["audio"],
            tmp_segment,
            duration,
            slide_num,
            task["recommendation"],
            task["project"],
            task["style"],
            task.get("log_path"),
        )
        actual_duration = require_valid_segment(tmp_segment, duration)
        tmp_segment.replace(segment)
        cache_segment = task.get("cache_segment")
        if cache_segment:
            atomic_copy_file(segment, cache_segment, run_id)
    finally:
        if tmp_segment.exists():
            tmp_segment.unlink()
    return {
        **task,
        "layout": layout,
        "actual_duration": actual_duration,
        "cache_note": "",
    }


def render_result_message(result, total):
    recommendation = result.get("recommendation")
    component = recommended_component(recommendation) or "rule_based"
    duration = result["duration"]
    actual_duration = result["actual_duration"]
    duration_note = f"{actual_duration:.1f}s"
    if abs(actual_duration - duration) > 0.2:
        duration_note += f", planned {duration:.1f}s"
    return (
        f"[{result['slide_num']}/{total}] {component} -> {result['layout']} 布局... "
        f"OK{result.get('cache_note', '')} ({duration_note})"
    )


def main():
    args = parse_args()
    project = args.project_path
    config = VideoConfig()
    if args.font_size:
        config.subtitle_font_size = args.font_size
    if args.style:
        config.style = args.style

    structure_file = project / "slide_structure.json"
    audio_dir = project / "audio"
    temp_dir = project / "temp_video"
    exports_dir = project / "exports"
    temp_dir.mkdir(exist_ok=True)
    exports_dir.mkdir(exist_ok=True)

    with open(structure_file, 'r', encoding='utf-8') as f:
        slides = json.load(f)
    if args.preview_slides and args.preview_slides > 0:
        slides = slides[:args.preview_slides]
    recommendations = load_component_recommendations(project)
    render_plan = load_render_plan(project)
    style_slug = (config.style or "").replace("-", "_")
    output_slug = f"{style_slug}_style" if style_slug else project.name
    if args.preview_slides and args.preview_slides > 0:
        output_slug = f"{output_slug}_preview{args.preview_slides}"
    manifest_path, render_manifest = load_render_manifest(temp_dir, style_slug)
    segment_cache = render_manifest.get("segments", {}) if isinstance(render_manifest, dict) else {}
    run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
    cache_dir = temp_dir / f"cache_{style_slug or 'default'}"
    run_dir = temp_dir / f"run_{style_slug or 'default'}_{run_id}"
    log_dir = temp_dir / "logs" / f"run_{style_slug or 'default'}_{run_id}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"生成智能布局视频 ({len(slides)} 页)...")
    if recommendations:
        print(f"已加载组件推荐: {len(recommendations)} 页")
    else:
        print("未找到 component_recommendations.json，使用旧规则选择布局")

    # 生成视频片段
    results_by_slide = {}
    pending_tasks = []
    auto_jobs = max(1, min(3, (os.cpu_count() or 2) // 2))
    render_jobs = max(1, args.jobs or auto_jobs)
    for i, slide in enumerate(slides, 1):
        audio = audio_dir / f"page_{i:02d}.mp3"
        if not audio.exists():
            audio = None

        if should_skip_slide(slide, i, project, audio):
            print(f"[{i}/{len(slides)}] skip empty slide (no text/audio/source image)")
            continue

        recommendation = recommendations.get(i)
        multiplier = float(recommendation.get("render_strategy", {}).get("duration_multiplier", 1.0)) if recommendation else 1.0
        planned_duration = float(render_plan.get(i, {}).get("duration", 5.0) or 5.0)
        duration = (probe_duration(audio) * multiplier + 0.5) if audio else planned_duration
        segment = run_dir / f"seg_{i:03d}.mp4"
        cache_segment = cache_dir / f"seg_{i:03d}.mp4"

        key = slide_render_key(project, i, slide, audio, recommendation, duration, config.style)
        cached = segment_cache.get(str(i), {})
        cached_path = Path(cached.get("path", cache_segment)) if isinstance(cached, dict) else cache_segment
        if not cached_path.exists() and cache_segment.exists():
            cached_path = cache_segment
        task = {
            "slide_num": i,
            "slide": slide,
            "audio": audio,
            "recommendation": recommendation,
            "duration": duration,
            "segment": segment,
            "cache_segment": cache_segment,
            "key": key,
            "project": project,
            "style": config.style,
            "run_id": run_id,
            "log_path": log_dir / f"slide_{i:03d}.log",
        }
        if (
            not args.force_render
            and cached_path.exists()
            and cached.get("key") == key
        ):
            ok, actual_duration, reason = validate_media_file(cached_path, expected_duration=duration)
            if ok:
                atomic_copy_file(cached_path, segment, run_id)
                layout = cached.get("layout", "cached")
                result = {
                    **task,
                    "layout": layout,
                    "actual_duration": actual_duration,
                    "cache_note": " cache",
                }
                results_by_slide[i] = result
                segment_cache[str(i)] = {
                    "key": key,
                    "path": str(cache_segment),
                    "layout": layout,
                    "duration": actual_duration,
                }
                if cached_path.resolve() != cache_segment.resolve():
                    atomic_copy_file(cached_path, cache_segment, run_id)
                print(render_result_message(result, len(slides)))
            else:
                print(f"[{i}/{len(slides)}] cached segment invalid ({reason}); re-rendering")
                pending_tasks.append(task)
        else:
            pending_tasks.append(task)

    if pending_tasks:
        worker_count = min(render_jobs, len(pending_tasks))
        if worker_count > 1:
            print(f"并行渲染: {worker_count} jobs")
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_task = {
                executor.submit(render_slide_segment, task): task
                for task in pending_tasks
            }
            for future in as_completed(future_to_task):
                result = future.result()
                slide_num = result["slide_num"]
                results_by_slide[slide_num] = result
                segment_cache[str(slide_num)] = {
                    "key": result["key"],
                    "path": str(result["cache_segment"]),
                    "layout": result["layout"],
                    "duration": result["actual_duration"],
                }
                print(render_result_message(result, len(slides)))

    ordered_results = [results_by_slide[i] for i in sorted(results_by_slide)]
    segments = [result["segment"] for result in ordered_results]
    rendered_slides = [
        (result["slide_num"], result["actual_duration"], result["audio"])
        for result in ordered_results
    ]

    if not segments:
        raise RuntimeError("No video segments were generated")

    # 合并视频
    print("\n合并视频...")
    concat_file = run_dir / "concat.txt"
    with open(concat_file, "w") as f:
        for seg in segments:
            f.write(f"file '{seg.name}'\n")

    video_output = exports_dir / f"{output_slug}_video.mp4"
    run_subprocess([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", "concat.txt",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-af", "aresample=async=1:first_pts=0",
        "-movflags", "+faststart",
        str(video_output.absolute())
    ], cwd=run_dir, log_path=log_dir / "concat.log", context="concat video segments")

    print(f"\n视频生成完成: {video_output}")
    print(f"大小: {video_output.stat().st_size / 1024 / 1024:.1f} MB")

    # 处理字幕
    adjusted_srt = exports_dir / f"{output_slug}_adjusted.srt"
    srt_file = build_srt_from_script_plan(project, rendered_slides, adjusted_srt)
    if srt_file is None:
        srt_file = build_srt_from_notes(project, rendered_slides, adjusted_srt)
    if srt_file is None:
        srt_file = exports_dir / f"{project.name}_new.srt"
    if srt_file.exists():
        print("\n生成字幕...")
        ass_file = exports_dir / f"{output_slug}.ass"
        generate_ass_from_srt(srt_file, ass_file, config)
        print(f"ASS字幕: {ass_file}")

        print("\n烧录字幕...")
        final_output = exports_dir / f"{output_slug}_final.mp4"
        burn_subtitles(video_output, ass_file, final_output, log_path=log_dir / "burn_subtitles.log")
        deliverable_output = final_output
        print(f"\n最终视频: {final_output}")
        print(f"大小: {final_output.stat().st_size / 1024 / 1024:.1f} MB")
        print(f"字幕字号: {config.subtitle_font_size}px")
    else:
        print(f"\n未找到字幕文件: {srt_file}")
        print("跳过字幕处理")

    if "deliverable_output" not in locals():
        deliverable_output = video_output

    if args.standardize_audio:
        if args.standardized_output:
            standardized_output = args.standardized_output
            if not standardized_output.is_absolute():
                standardized_output = exports_dir / standardized_output
        else:
            standardized_output = deliverable_output.with_name(
                f"{deliverable_output.stem}_standardized{deliverable_output.suffix}"
            )
        print("\nStandardizing audio (48kHz stereo / loudnorm)...")
        standardize_audio(deliverable_output, standardized_output, log_path=log_dir / "standardize_audio.log")
        print(f"Standardized audio output: {standardized_output}")
        print(f"Size: {standardized_output.stat().st_size / 1024 / 1024:.1f} MB")
        deliverable_output = standardized_output

    if not args.no_qa_frames:
        print("\n生成质检抽帧与媒体报告...")
        qa_report = write_video_quality_report(
            project, deliverable_output, rendered_slides, srt_file=srt_file, log_dir=log_dir
        )
        print(f"QA报告: {qa_report}")

    render_manifest = {
        "project": project.name,
        "style": config.style,
        "preview_slides": args.preview_slides,
        "segments": segment_cache,
    }
    save_render_manifest(manifest_path, render_manifest)


if __name__ == "__main__":
    main()
