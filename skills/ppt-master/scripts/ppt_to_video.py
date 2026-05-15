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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from functools import lru_cache


VIDEO_W = 1920
VIDEO_H = 1080
MIN_SEGMENT_BYTES = 32 * 1024


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


def normalize_video_text(text):
    """Light cleanup for OCR/PPT text before recomposed video rendering."""
    text = str(text or "").strip()
    text = re.sub(r"([\u4e00-\u9fff])\1{2,}", r"\1", text)
    text = text.replace(": :", "：").replace("：：", "：")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_no_content_placeholder(text):
    cleaned = normalize_video_text(text).strip("_ ").lower()
    cleaned = cleaned.rstrip(".")
    return "no extractable text content" in cleaned


def visual_text_len(text):
    """Approximate rendered text width for Chinese/Latin mixed strings."""
    total = 0.0
    for ch in str(text):
        total += 1.0 if "\u4e00" <= ch <= "\u9fff" else 0.55
    return total


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
    fontfile = "/Windows/Fonts/msyhbd.ttc" if bold else "/Windows/Fonts/msyh.ttc"
    text_esc = escape_text(text)
    filters.append(
        f"[{current}]drawtext=text='{text_esc}':fontfile={fontfile}:"
        f"fontsize={font_size}:fontcolor={color}:expansion=none:x={x}:y={y}"
        f"{_enable_after(start)}[v{layer_num}]"
    )
    return f"v{layer_num}", layer_num + 1


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
        area = width * height
        ratio = width / max(1, height)
        ratio_score = max(0.15, 1.0 - min(abs(ratio - 16 / 9), 1.2))
        score = area * ratio_score
        if score > fallback_score:
            fallback = path
            fallback_score = score
        if width >= 1200 and height >= 650:
            score *= 1.4
        else:
            score *= 0.25
        if score > best_score:
            best = path
            best_score = score
    return best or fallback


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
    if selected in {"cover_hero", "section_title", "statement_focus", "image_hero", "photo_story", "product_showcase", "image_pan_zoom"}:
        return "hero"
    if selected in {"problem_stack"}:
        return "problem"
    if selected in {"capability_matrix", "two_column_compare", "before_after"}:
        return "matrix"
    if selected in {"revenue_model", "financial_snapshot"}:
        return "business"
    if selected in {"solution_flow", "process_flow", "timeline", "roadmap_timeline", "lifecycle_loop", "flywheel"}:
        return "process"
    if selected in {"market_dashboard", "metric_dashboard", "kpi_cards", "chart_focus"}:
        return "metrics"
    if selected in {"team_roster", "role_grid", "org_chart"}:
        return "team"
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
        current, layer_num = add_filter_drawtext(
            filters, current, layer_num, line, x=x, y=y + idx * line_height,
            font_size=font_size, color=color, bold=bold, start=start
        )
    return current, layer_num


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
        return ordered_cards[:limit]

    for count in (4, 3):
        if len(clean) >= count * 3 and all(is_card_heading(item) for item in clean[:count]):
            middle = clean[count: count * 2]
            bodies = clean[count * 2: count * 3]
            if all(is_card_subtitle(item) for item in middle):
                for idx in range(count):
                    add_card(clean[idx], bodies[idx], middle[idx])
                return cards[:limit]

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
        return cards[:limit]

    for card in build_alternating_cards(clean, limit=limit):
        if len(cards) >= limit:
            break
        if not any(card["title"] == existing["title"] for existing in cards):
            cards.append(card)

    if not cards and clean:
        titles = ["核心要点", "组织方式", "关键价值", "补充说明"]
        for idx, body in enumerate(fallback_body_chunks(clean, limit=limit)):
            add_card(titles[idx] if idx < len(titles) else f"要点{idx + 1}", body)

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


def layout_diverse(slide_data, slide_num, duration, project=None, recommendation=None):
    component = selected_visual_component(recommendation)
    kind = adaptive_layout_kind(slide_data, slide_num, project, recommendation)
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
        "fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=36:fontcolor=white:"
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
        f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=56:fontcolor=#1a1a1a:"
        f"x=100:y=170[v{layer_num}]"
    )
    current = f"v{layer_num}"
    layer_num += 1

    # 副标题
    filters.append(
        f"[{current}]drawtext=text='重点看这几个维度':"
        f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=28:fontcolor=#666666:"
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
            f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=42:fontcolor=white:"
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
                    f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=28:fontcolor=#333333:"
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
        f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=56:fontcolor=#1a1a1a:"
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
                    f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=30:fontcolor=#333333:"
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
        f"fontfile=/Windows/Fonts/msyhbd.ttc:fontsize=64:fontcolor=#1a1a1a:"
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
                f"fontfile=/Windows/Fonts/msyh.ttc:fontsize=32:fontcolor=#333333:"
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
    visual_asset = slide_visual_asset_for_layout(project, slide_num, slide_data)
    if style == "adaptive":
        layout_type = "adaptive"
    elif style == "diverse":
        layout_type = "diverse"
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
    elif layout_type == "clean_cards":
        filters, current, layer_num = layout_clean_cards(slide_data, slide_num, duration, project)
    else:
        filters, current, layer_num = generate_base_layout(duration, slide_num)

    if layout_type == 'three_column':
        filters, current, layer_num = layout_three_column(filters, current, layer_num, slide_data, slide_num)
    elif layout_type == 'two_column':
        filters, current, layer_num = layout_two_column(filters, current, layer_num, slide_data, slide_num)
    elif layout_type not in {"clean_cards", "adaptive", "diverse"}:
        filters, current, layer_num = layout_single_column(filters, current, layer_num, slide_data, slide_num)

    filter_complex = ";".join(filters)
    filter_script_path = None
    cmd = ["ffmpeg", "-y"]
    audio_input_index = 0
    if visual_asset:
        cmd.extend(["-loop", "1", "-i", str(visual_asset)])
        audio_input_index = 1
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
    for item in rendered_slides:
        slide_num, duration = item[:2]
        plan = plans.get(slide_num, {})
        chunks = plan.get("subtitle_chunks") or []
        for chunk in chunks:
            text = normalize_video_text(chunk.get("text", ""))
            if is_noise_line(text):
                continue
            start = offset + max(0.0, min(float(chunk.get("start", 0.0) or 0.0), duration))
            end = offset + max(0.0, min(float(chunk.get("end", duration) or duration), duration))
            if end <= start:
                continue
            subtitle_parts = split_subtitle_text(text)
            span = end - start
            part_count = max(1, len(subtitle_parts))
            for idx, part in enumerate(subtitle_parts):
                part_start = start + span * idx / part_count
                part_end = start + span * (idx + 1) / part_count
                blocks.append(
                    f"{seq}\n{format_srt_timestamp(part_start)} --> {format_srt_timestamp(part_end)}\n{part}\n"
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
    visual = slide_visual_asset_for_layout(project, slide_num, slide_data)
    payload = {
        "version": 22,
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
