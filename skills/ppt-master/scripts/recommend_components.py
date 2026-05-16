#!/usr/bin/env python3
"""Recommend video components from extracted slide structure.

The intended flow is:
1. ``extract_structure.py`` creates ``slide_structure.json`` and
   ``component_selection_input.json`` from OCR/visual hierarchy.
2. This script creates a model-ready prompt and either consumes a model JSON
   answer or falls back to deterministic rules.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
COMPONENT_INDEX_PATH = SKILL_DIR / "templates" / "components" / "video_components_index.json"
ADVANCED_COMPONENT_INDEX_PATH = SKILL_DIR / "templates" / "components" / "advanced_video_components.json"
CHARTS_INDEX_PATH = SKILL_DIR / "templates" / "charts" / "charts_index.json"


BASE_COMPONENT_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "preserve_slide",
        "name": "Preserve original slide",
        "best_for": ["visually complete slides", "dense layouts", "imported PPT screenshots"],
        "video_treatment": "subtle hold, optional safe-area subtitles",
    },
    {
        "id": "section_title",
        "name": "Section title",
        "best_for": ["cover", "chapter divider", "low text density"],
        "video_treatment": "fade in title, slow background zoom",
    },
    {
        "id": "bullet_reveal",
        "name": "Bullet reveal",
        "best_for": ["lists", "talking points", "sequential explanation"],
        "video_treatment": "highlight one bullet at a time",
    },
    {
        "id": "three_card_summary",
        "name": "Three-card summary",
        "best_for": ["three to five parallel points", "feature cards", "benefit cards"],
        "video_treatment": "card-by-card emphasis",
    },
    {
        "id": "two_column_compare",
        "name": "Two-column comparison",
        "best_for": ["before/after", "pros/cons", "problem/solution", "VS"],
        "video_treatment": "left-right contrast and emphasis sweep",
    },
    {
        "id": "process_flow",
        "name": "Process flow",
        "best_for": ["steps", "from-to chain", "workflow", "pipeline"],
        "template": "process_flow.svg",
        "video_treatment": "advance along path by narration segment",
    },
    {
        "id": "timeline",
        "name": "Timeline",
        "best_for": ["milestones", "roadmap", "schedule"],
        "template": "timeline.svg",
        "video_treatment": "progressive marker reveal",
    },
    {
        "id": "kpi_cards",
        "name": "KPI cards",
        "best_for": ["metrics", "numbers", "financial highlights"],
        "template": "kpi_cards.svg",
        "video_treatment": "number emphasis and short pause",
    },
    {
        "id": "chart_focus",
        "name": "Chart focus",
        "best_for": ["bar chart", "line chart", "pie chart", "data trend"],
        "template": "bar_chart.svg",
        "video_treatment": "focus on axis/series then conclusion",
    },
    {
        "id": "image_pan_zoom",
        "name": "Image pan and zoom",
        "best_for": ["image-heavy slides", "product photos", "scene explanation"],
        "video_treatment": "slow pan with callout text",
    },
    {
        "id": "callout_overlay",
        "name": "Callout overlay",
        "best_for": ["existing slide needs emphasis", "highlight one phrase or region"],
        "video_treatment": "draw box or spotlight over original slide",
    },
]


def _component_index_to_catalog(index_path: Path) -> List[Dict[str, Any]]:
    """Load the modern component library index into the flat catalog shape."""
    if not index_path.exists():
        return []

    data = json.loads(index_path.read_text(encoding="utf-8"))
    components = data.get("components", {})
    catalog = []
    for component_id, item in components.items():
        catalog.append(
            {
                "id": component_id,
                "name": item.get("name", component_id),
                "best_for": item.get("best_for") or item.get("content_shape", []),
                "content_shape": item.get("content_shape", []),
                "template": item.get("template"),
                "visual_treatment": item.get("visual_treatment", ""),
                "video_treatment": item.get("video_treatment", ""),
                "needs_image": item.get("needs_image", False),
                "library": "video_components",
            }
        )
    return catalog


def _advanced_component_index_to_catalog(index_path: Path) -> List[Dict[str, Any]]:
    """Load high-variance video components into the flat catalog shape."""
    if not index_path.exists():
        return []

    data = json.loads(index_path.read_text(encoding="utf-8"))
    components = data.get("components", {})
    catalog = []
    for component_id, item in components.items():
        catalog.append(
            {
                "id": component_id,
                "name": item.get("name", component_id),
                "best_for": item.get("best_for") or item.get("content_shape", []),
                "content_shape": item.get("content_shape", []),
                "family": item.get("family", ""),
                "kind": item.get("kind", ""),
                "layout": item.get("layout", "diverse"),
                "visual_treatment": item.get("visual_treatment", ""),
                "video_treatment": item.get("video_treatment", ""),
                "needs_image": item.get("needs_image", False),
                "variety_weight": item.get("variety_weight", 0.8),
                "library": "advanced_video_components",
            }
        )
    return catalog


def _charts_index_to_catalog(index_path: Path, limit: int = 70) -> List[Dict[str, Any]]:
    """Expose existing chart SVG templates as selectable visualization components."""
    if not index_path.exists():
        return []

    data = json.loads(index_path.read_text(encoding="utf-8"))
    charts = data.get("charts", {})
    catalog = []
    for chart_id, item in list(charts.items())[:limit]:
        catalog.append(
            {
                "id": chart_id,
                "name": item.get("label", chart_id),
                "best_for": item.get("keywords", []),
                "content_shape": [item.get("summary", "")],
                "template": f"{chart_id}.svg",
                "visual_treatment": item.get("summary", ""),
                "video_treatment": "render as native SVG template, then reveal the key area in narration order",
                "needs_image": False,
                "library": "charts",
            }
        )
    return catalog


def load_component_catalog() -> List[Dict[str, Any]]:
    """Return a deduplicated catalog from JSON indexes plus compatibility defaults."""
    merged: dict[str, Dict[str, Any]] = {}
    for item in BASE_COMPONENT_CATALOG:
        merged[item["id"]] = {**item, "library": "legacy_compat"}
    for item in _component_index_to_catalog(COMPONENT_INDEX_PATH):
        merged[item["id"]] = {**merged.get(item["id"], {}), **item}
    for item in _advanced_component_index_to_catalog(ADVANCED_COMPONENT_INDEX_PATH):
        merged[item["id"]] = {**merged.get(item["id"], {}), **item}
    for item in _charts_index_to_catalog(CHARTS_INDEX_PATH):
        merged.setdefault(item["id"], item)
    return list(merged.values())


COMPONENT_CATALOG: List[Dict[str, Any]] = load_component_catalog()


DATA_RE = re.compile(r"(\d+(?:\.\d+)?%?|\bdata\b|\bkpi\b|ROI|\u6570\u636e|\u6307\u6807|\u589e\u957f)", re.I)
PROCESS_RE = re.compile(r"(\u6b65\u9aa4|\u6d41\u7a0b|\u9636\u6bb5|\u89e3\u51b3\u65b9\u6848|\u751f\u6001|\u4ece.+\u5230|process|flow|step)", re.I)
COMPARE_RE = re.compile(r"(\u5bf9\u6bd4|\u4f18\u52bf|\u52a3\u52bf|\u4e0d\u540c|\bvs\b|compare)", re.I)
TIME_RE = re.compile(r"(\u65f6\u95f4|\u9636\u6bb5|\u8def\u7ebf\u56fe|\u89c4\u5212|\u7b2c\s*\d+\s*\u5e74|timeline|roadmap|milestone)", re.I)
PROBLEM_RE = re.compile(r"(\u95ee\u9898|\u75db\u70b9|\u6311\u6218|\u98ce\u9669|\u963b\u788d|challenge|problem|risk)", re.I)
MARKET_RE = re.compile(r"(\u5e02\u573a|\u89c4\u6a21|\u84dd\u6d77|\u6d88\u8d39|\u653f\u7b56|market|consumer|policy)", re.I)
REVENUE_RE = re.compile(r"(\u76c8\u5229|\u6536\u5165|\u8425\u6536|\u5229\u6da6|\u5546\u4e1a\u6a21\u5f0f|revenue|profit|business model)", re.I)
TEAM_RE = re.compile(r"(\u56e2\u961f|\u6210\u5458|\u7ec4\u7ec7|CEO|CTO|COO|team|role)", re.I)
IMAGE_RE = re.compile(r"(\u56fe\u7247|\u7167\u7247|\u573a\u666f|\u4ea7\u54c1|\u8bbe\u5907|\u57ce\u5e02|\u5730\u56fe|photo|image|product|scene|map)", re.I)
MATH_RE = re.compile(r"(\u6839\u53f7|\u5e73\u65b9\u6839|\u7acb\u65b9\u6839|\u65b9\u7a0b|\u4e0d\u7b49\u5f0f|\u5316\u7b80|\u8fd0\u7b97|\u6709\u7406\u6570|\u65e0\u7406\u6570|\u51fd\u6570|鈭殀\\sqrt|radical|equation|formula)", re.I)
FORMULA_RE = re.compile(r"(鈭殀\\sqrt|[a-zA-Z]\s*[=<>+\-*/^]|\d+\s*[+\-*/^]\s*\d+|\u516c\u5f0f|\u6cd5\u5219|\u6027\u8d28|\u63a8\u5bfc|\u8ba1\u7b97|\u6b65\u9aa4)", re.I)
MISTAKE_RE = re.compile(r"(\u6613\u9519|\u9519\u8bef|\u6ce8\u610f|\u8bef\u533a|\u6b63\u786e|\u9519\u89e3|wrong|mistake|correct)", re.I)
SOURCE_ASSET_RE = re.compile(
    r"(^|[/\\])slide_\d+_image_\d+\.(?:png|jpe?g|webp|gif|wmf|emf|svg)\)?$",
    re.IGNORECASE,
)


def is_source_asset_reference(text: str) -> bool:
    text = str(text or "").strip()
    return bool(text.startswith("![") or SOURCE_ASSET_RE.search(text))


def visual_text_len(text: str) -> float:
    total = 0.0
    for ch in str(text or ""):
        total += 1.0 if "\u4e00" <= ch <= "\u9fff" else 0.55
    return total


def fallback_visual_effect(slide: Dict[str, Any]) -> str:
    """Choose a visual emphasis layer while preserving the original slide art."""
    layout = slide.get("layout", {})
    signals = slide.get("signals", {})
    page_type = layout.get("page_type", "")
    density = float(layout.get("text_density", 0) or 0)
    text = slide_text(slide)
    clean_text = "\n".join(line for line in text.splitlines() if not is_source_asset_reference(line))

    if any(marker in clean_text for marker in ("\u76ee\u5f55", "\u4e3b\u8981\u5185\u5bb9", "\u5185\u5bb9\u63d0\u8981", "\u7ae0\u8282", "\u77e5\u8bc6\u5730\u56fe")):
        return "radial_concept_map"
    if any(key in clean_text for key in ("\u6cd5\u5219", "\u89c4\u5219", "\u6ce8\u610f", "\u8981\u70b9", "\u68c0\u67e5")) and len(clean_text.splitlines()) >= 4:
        return "checkpoint_ladder"
    if MISTAKE_RE.search(clean_text) or (COMPARE_RE.search(clean_text) and MATH_RE.search(clean_text)):
        return "misconception_compare"
    if MATH_RE.search(clean_text) and FORMULA_RE.search(clean_text):
        if PROCESS_RE.search(clean_text) or len(re.findall(r"\d", clean_text)) >= 4:
            return "formula_walkthrough"
        return "blackboard_derivation"
    if MATH_RE.search(clean_text) and page_type == "section":
        return "magazine_spread"
    if any(key in clean_text for key in ("\u5e94\u7528", "\u573a\u666f", "\u4f8b\u5b50", "\u6848\u4f8b", "\u751f\u6d3b")):
        return "application_storyboard"

    if not clean_text.strip() and page_type in {"image_only", "section"}:
        return "section_title"
    if page_type == "section" or (density < 0.16 and visual_text_len(clean_text) <= 18):
        return "section_title"
    if any(marker in clean_text for marker in ("\u672c\u7ae0\u4e3b\u8981\u5185\u5bb9", "\u4e3b\u8981\u5185\u5bb9", "\u76ee\u5f55", "\u5185\u5bb9\u63d0\u8981")):
        return "three_card_summary"
    if len(re.findall(r"\b\d+\.\d+\b", clean_text)) >= 3:
        return "three_card_summary"
    if any(key in clean_text for key in ("\u8fdb\u5236", "\u7f16\u7801", "\u4e8c\u8fdb\u5236", "\u5341\u8fdb\u5236")) and any(key in clean_text for key in ("\u4e0d\u540c", "\u5bf9\u6bd4", "\u7279\u70b9", "\u6bd4\u8f83")):
        return "two_column_compare"
    if any(key in clean_text for key in ("\u8f6c\u6362", "\u6b65\u9aa4", "\u4ece\u5c0f\u6570\u70b9", "\u8865\u96f6", "\u5206\u7ec4")):
        return "process_flow"
    if PROBLEM_RE.search(clean_text):
        return "problem_stack"
    if TEAM_RE.search(clean_text):
        return "team_roster"
    if TIME_RE.search(clean_text):
        return "roadmap_timeline"
    if REVENUE_RE.search(clean_text):
        return "revenue_model"
    if COMPARE_RE.search(clean_text) and any(key in clean_text for key in ("\u4f18\u52bf", "\u58c1\u5792", "\u80fd\u529b", "\u7ade\u4e89\u529b")):
        return "capability_matrix"
    if MARKET_RE.search(clean_text) and DATA_RE.search(clean_text):
        return "market_dashboard"
    if page_type == "comparison" or signals.get("has_comparison") or COMPARE_RE.search(clean_text):
        return "two_column_compare"
    if page_type == "process" or signals.get("has_process") or PROCESS_RE.search(clean_text):
        if "\u95ed\u73af" in clean_text or "\u5faa\u73af" in clean_text:
            return "lifecycle_loop"
        return "formula_walkthrough" if MATH_RE.search(clean_text) else "solution_flow"
    if page_type == "data" or signals.get("has_data"):
        if DATA_RE.search(clean_text):
            return "chart_focus"
        return "kpi_cards"
    if int(layout.get("columns", 1) or 1) >= 3:
        return "rounded_step_cards"
    return "callout_overlay"


def slide_text(slide: Dict[str, Any]) -> str:
    parts = [slide.get("title", ""), slide.get("subtitle", "")]
    parts.extend(slide.get("bullets", []))
    parts.extend(slide.get("paragraphs", []))
    if not any(parts):
        parts.extend(block.get("text", "") for block in slide.get("text_blocks", []))
    return "\n".join(part for part in parts if part and not is_source_asset_reference(str(part)))


def component_score(slide: Dict[str, Any], component_id: str) -> float:
    text = slide_text(slide)
    layout = slide.get("layout", {})
    signals = slide.get("signals", {})
    density = float(layout.get("text_density", 0))
    columns = int(layout.get("columns", 1))
    bullets = slide.get("bullets", [])
    page_type = layout.get("page_type", "")
    digit_count = len(re.findall(r"\d", text))

    if component_id == "preserve_slide":
        return 0.55 + density * 0.25 + (0.15 if slide.get("source") == "ocr" else 0)
    if component_id == "cover_hero":
        return 0.88 if page_type == "section" or density < 0.16 else 0.18
    if component_id == "section_title":
        return 0.9 if page_type == "section" or density < 0.18 else 0.2
    if component_id == "statement_focus":
        return 0.78 if density < 0.25 and len(bullets) <= 2 else 0.22
    if component_id == "quote_focus":
        return 0.76 if any(mark in text for mark in ("\u201c", "\u201d", '"', "\u4f7f\u547d", "\u613f\u666f")) and density < 0.35 else 0.18
    if component_id == "blackboard_derivation":
        return 0.93 if MATH_RE.search(text) and FORMULA_RE.search(text) and not PROCESS_RE.search(text) else 0.16
    if component_id == "formula_walkthrough":
        return 0.92 if MATH_RE.search(text) and (PROCESS_RE.search(text) or digit_count >= 4) else 0.17
    if component_id == "checkpoint_ladder":
        return 0.82 if len(bullets) >= 5 or any(key in text for key in ("\u6cd5\u5219", "\u89c4\u5219", "\u6ce8\u610f", "\u8981\u70b9", "\u68c0\u67e5")) else 0.16
    if component_id == "radial_concept_map":
        return 0.84 if MATH_RE.search(text) and (page_type == "section" or 3 <= len(bullets) <= 6 or columns >= 3) else 0.18
    if component_id == "magazine_spread":
        return 0.8 if density < 0.22 or page_type == "section" else 0.18
    if component_id == "rounded_step_cards":
        return 0.78 if 3 <= len(bullets) <= 5 or columns >= 3 else 0.2
    if component_id == "misconception_compare":
        return 0.88 if MISTAKE_RE.search(text) or (COMPARE_RE.search(text) and MATH_RE.search(text)) else 0.18
    if component_id == "application_storyboard":
        return 0.82 if any(key in text for key in ("\u5e94\u7528", "\u573a\u666f", "\u4f8b\u5b50", "\u6848\u4f8b", "\u751f\u6d3b", "\u51fd\u6570")) or IMAGE_RE.search(text) else 0.18
    if component_id == "bullet_reveal":
        return 0.35 + min(0.45, len(bullets) * 0.12)
    if component_id in {"three_card_summary", "insight_cards"}:
        return 0.75 if columns >= 3 or 3 <= len(bullets) <= 5 or page_type == "multi_card" else 0.25
    if component_id == "problem_stack":
        return 0.88 if PROBLEM_RE.search(text) else 0.16
    if component_id == "solution_flow":
        return 0.87 if signals.get("has_process") or PROCESS_RE.search(text) else 0.2
    if component_id == "capability_matrix":
        return 0.84 if COMPARE_RE.search(text) and any(key in text for key in ("\u4f18\u52bf", "\u58c1\u5792", "\u80fd\u529b", "moat")) else 0.2
    if component_id == "before_after":
        return 0.82 if any(key in text.lower() for key in ("before", "after", "\u8fc7\u53bb", "\u73b0\u5728", "\u539f\u6765", "\u76ee\u6807", "\u8f6c\u578b")) else 0.17
    if component_id == "dense_grid":
        return 0.68 if len(bullets) >= 6 or columns >= 4 else 0.18
    if component_id == "split_text_visual":
        return 0.72 if IMAGE_RE.search(text) and 0.18 <= density <= 0.55 else 0.18
    if component_id == "two_column_compare":
        return 0.8 if signals.get("has_comparison") or COMPARE_RE.search(text) else 0.2
    if component_id == "process_flow":
        return 0.85 if signals.get("has_process") or PROCESS_RE.search(text) else 0.2
    if component_id == "timeline":
        return 0.8 if TIME_RE.search(text) else 0.15
    if component_id == "roadmap_timeline":
        return 0.88 if TIME_RE.search(text) and any(key in text for key in ("\u5e74", "\u89c4\u5212", "\u9636\u6bb5", "roadmap")) else 0.18
    if component_id == "lifecycle_loop":
        return 0.86 if any(key in text for key in ("\u95ed\u73af", "\u5faa\u73af", "\u518d\u751f", "cycle", "loop")) else 0.18
    if component_id == "flywheel":
        return 0.78 if any(key in text for key in ("\u589e\u957f", "\u53c2\u4e0e", "\u590d\u8d2d", "\u7f51\u7edc\u6548\u5e94", "flywheel")) else 0.16
    if component_id == "kpi_cards":
        return 0.72 if DATA_RE.search(text) and digit_count >= 3 else 0.2
    if component_id == "metric_dashboard":
        return 0.84 if DATA_RE.search(text) and digit_count >= 4 else 0.2
    if component_id == "market_dashboard":
        return 0.88 if MARKET_RE.search(text) and (DATA_RE.search(text) or digit_count >= 3) else 0.2
    if component_id == "revenue_model":
        return 0.9 if REVENUE_RE.search(text) else 0.18
    if component_id == "financial_snapshot":
        return 0.82 if REVENUE_RE.search(text) and digit_count >= 3 else 0.18
    if component_id == "chart_focus":
        return 0.82 if page_type == "data" or signals.get("has_data") else 0.2
    if component_id in {"image_pan_zoom", "image_hero", "photo_story", "image_mosaic", "product_showcase", "map_focus"}:
        if page_type == "image_only" or IMAGE_RE.search(text):
            return 0.72
        return 0.18
    if component_id in {"team_roster", "role_grid"}:
        return 0.86 if TEAM_RE.search(text) else 0.18
    if component_id == "org_chart":
        return 0.72 if TEAM_RE.search(text) and any(key in text for key in ("\u7ec4\u7ec7", "\u67b6\u6784", "\u6c47\u62a5", "\u90e8\u95e8")) else 0.18
    if component_id == "callout_overlay":
        return 0.45 + (0.2 if density > 0.45 else 0)
    if component_id in {"bar_chart", "horizontal_bar_chart", "line_chart", "donut_chart", "pie_chart", "waterfall_chart"}:
        return 0.66 if DATA_RE.search(text) and digit_count >= 3 else 0.12
    if component_id in {"comparison_columns", "comparison_table", "pros_cons_chart", "swot_analysis"}:
        return 0.64 if COMPARE_RE.search(text) else 0.12
    if component_id in {"cycle_diagram", "numbered_steps", "chevron_process", "pipeline_with_stages"}:
        return 0.64 if PROCESS_RE.search(text) else 0.12
    return 0.0


def top_components(slide: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
    scored = []
    for component in COMPONENT_CATALOG:
        score = component_score(slide, component["id"])
        scored.append({**component, "score": round(min(0.99, score), 2)})
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


def rule_based_recommendation(slide: Dict[str, Any]) -> Dict[str, Any]:
    primary = next(item for item in COMPONENT_CATALOG if item["id"] == "preserve_slide")
    visual_effect = fallback_visual_effect(slide)
    ranked = top_components(slide, limit=6)
    alternatives = [
        item["id"] for item in ranked
        if item["id"] not in {"preserve_slide", visual_effect}
    ][:4]
    effect_component = next(
        (item for item in COMPONENT_CATALOG if item["id"] == visual_effect),
        {"id": visual_effect, "name": visual_effect},
    )
    return {
        "slide_number": slide["slide_number"],
        "source": slide.get("source", "unknown"),
        "page_type": slide.get("layout", {}).get("page_type", "content"),
        "primary_component": "preserve_slide",
        "alternatives": [visual_effect] + alternatives,
        "confidence": 0.92,
        "reason": "Rules fallback chooses a content-aware recomposed component, while keeping original slide art available only as a supporting reference.",
        "render_strategy": {
            "base": "recompose_component",
            "visual_effect": visual_effect,
            "effect_component": visual_effect,
            "subtitle_layout": "reserve",
            "duration_multiplier": 1.0,
            "transition": "fade",
        },
        "component": {**primary, "score": 0.92},
        "effect_component": effect_component,
    }


def diversify_recommendations(recommendations: List[Dict[str, Any]], slides: List[Dict[str, Any]]) -> None:
    """Avoid long runs of the same recomposed component in generated videos."""
    slide_by_number = {int(slide.get("slide_number", idx + 1)): slide for idx, slide in enumerate(slides)}
    last_effect = ""
    run_length = 0
    math_cycle = [
        "blackboard_derivation",
        "formula_walkthrough",
        "checkpoint_ladder",
        "radial_concept_map",
        "rounded_step_cards",
        "misconception_compare",
        "application_storyboard",
    ]
    repeated_effects = {
        "formula_walkthrough",
        "blackboard_derivation",
        "checkpoint_ladder",
        "rounded_step_cards",
        "radial_concept_map",
        "three_card_summary",
    }
    application_keywords = (
        "\u5e94\u7528",
        "\u573a\u666f",
        "\u4f8b\u5b50",
        "\u6848\u4f8b",
        "\u51fd\u6570",
        "\u751f\u6d3b",
    )

    for item in recommendations:
        slide_number = int(item.get("slide_number", 0) or 0)
        slide = slide_by_number.get(slide_number, {})
        text = slide_text(slide)
        strategy = item.setdefault("render_strategy", {})
        effect = strategy.get("visual_effect") or strategy.get("effect_component") or ""

        if effect == last_effect:
            run_length += 1
        else:
            run_length = 1

        should_diversify = run_length >= 2 and effect in repeated_effects
        if should_diversify and MATH_RE.search(text):
            cycle_index = max(0, slide_number - 1) % len(math_cycle)
            candidate = math_cycle[cycle_index]
            if MISTAKE_RE.search(text) or COMPARE_RE.search(text):
                candidate = "misconception_compare"
            elif any(key in text for key in application_keywords):
                candidate = "application_storyboard"
            elif candidate == effect:
                candidate = math_cycle[(cycle_index + 1) % len(math_cycle)]

            strategy["visual_effect"] = candidate
            strategy["effect_component"] = candidate
            item["alternatives"] = [candidate] + [
                alt for alt in item.get("alternatives", []) if alt != candidate
            ][:4]
            item["reason"] = f"{item.get('reason', '')} Diversified adjacent math pages with {candidate}."
            effect_component = next(
                (component for component in COMPONENT_CATALOG if component["id"] == candidate),
                {"id": candidate, "name": candidate},
            )
            item["effect_component"] = effect_component
            effect = candidate
            run_length = 1

        last_effect = effect


def build_reason(slide: Dict[str, Any], component: Dict[str, Any]) -> str:
    layout = slide.get("layout", {})
    bullets = len(slide.get("bullets", []))
    return (
        f"Selected {component['id']} for page_type={layout.get('page_type')}, "
        f"columns={layout.get('columns')}, density={layout.get('text_density')}, "
        f"bullets={bullets}."
    )


def load_slides(project_path: Path) -> List[Dict[str, Any]]:
    structure_file = project_path / "slide_structure.json"
    if not structure_file.exists():
        raise FileNotFoundError(
            f"Structure file not found: {structure_file}. Run extract_structure.py first."
        )
    with structure_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "slides" in data:
        return merge_source_text(project_path, data["slides"])
    if isinstance(data, list):
        return merge_source_text(project_path, data)
    raise ValueError("Unsupported slide structure JSON format")


def load_source_slide_lines(project_path: Path) -> dict[int, list[str]]:
    """Read clean per-slide text from imported Markdown sources when present."""
    result: dict[int, list[str]] = {}
    sources_dir = project_path / "sources"
    if not sources_dir.exists():
        return result
    for source_path in sorted(sources_dir.glob("*.md")):
        current: int | None = None
        for raw in source_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = re.match(r"^##\s+Slide\s+(\d+)", raw.strip(), re.I)
            if match:
                current = int(match.group(1))
                result.setdefault(current, [])
                continue
            if current is None:
                continue
            if raw.startswith("## ") or raw.startswith("### Speaker Notes"):
                current = None
                continue
            stripped = raw.strip()
            if is_source_asset_reference(stripped):
                continue
            line = re.sub(r"!\[[^\]]*\]\(.*\)", "", stripped).strip()
            if is_source_asset_reference(line):
                continue
            if not line or line.startswith(("#", "Source:", "Total slides:")):
                continue
            line = re.sub(r"^[-*+]\s+", "", line).strip()
            result[current].append(re.sub(r"\s+", " ", line))
    return result


def merge_source_text(project_path: Path, slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    source_lines = load_source_slide_lines(project_path)
    if not source_lines:
        return slides
    merged = []
    for slide in slides:
        slide_number = int(slide.get("slide_number") or len(merged) + 1)
        lines = source_lines.get(slide_number, [])
        if lines:
            slide = dict(slide)
            slide["source_title"] = lines[0]
            slide["source_paragraphs"] = lines[1:]
            slide["title"] = lines[0]
            slide["paragraphs"] = lines[1:]
            slide["bullets"] = []
        merged.append(slide)
    return merged


def build_llm_payload(project_path: Path, slides: List[Dict[str, Any]]) -> Dict[str, Any]:
    compact_slides = []
    for slide in slides:
        compact_slides.append(
            {
                "slide_number": slide.get("slide_number"),
                "title": slide.get("title"),
                "subtitle": slide.get("subtitle"),
                "bullets": slide.get("bullets", [])[:8],
                "paragraphs": slide.get("paragraphs", [])[:5],
                "layout": slide.get("layout", {}),
                "signals": slide.get("signals", {}),
                "text_blocks": [
                    {
                        "text": block.get("text"),
                        "role": block.get("role"),
                        "bbox_norm": block.get("bbox_norm"),
                    }
                    for block in slide.get("text_blocks", [])[:30]
                ],
            }
        )

    return {
        "task": "Choose video layout/components for each PPT slide.",
        "project": project_path.name,
        "component_catalog": COMPONENT_CATALOG,
        "slides": compact_slides,
        "output_schema": {
            "slide_number": "number",
            "primary_component": "one component id",
            "alternatives": ["component id"],
            "confidence": "0-1",
            "reason": "short reason grounded in OCR hierarchy",
            "render_strategy": {
                "base": "preserve_original_slide or recompose_component",
                "visual_effect": "component id used as a visual emphasis layer, e.g. problem_stack/solution_flow/market_dashboard/revenue_model/team_roster",
                "subtitle_layout": "reserve or overlay",
                "duration_multiplier": "number",
                "transition": "fade/push/none",
            },
        },
    }


def write_prompt(project_path: Path, payload: Dict[str, Any]) -> Path:
    prompt_path = project_path / "component_selection_prompt.md"
    prompt = [
        "# Component Selection Prompt",
        "",
        "You are selecting video presentation components from OCR-extracted PPT structure.",
        "Use the provided component_catalog only. Preserve the original slide when OCR shows",
        "a dense or already well-designed layout. Choose recomposed components only when they",
        "clearly improve comprehension in video.",
        "",
        "Return strict JSON: an array of slide recommendation objects matching output_schema.",
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    prompt_path.write_text("\n".join(prompt), encoding="utf-8")
    return prompt_path


def load_llm_output(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "recommendations" in data:
        data = data["recommendations"]
    if not isinstance(data, list):
        raise ValueError("LLM output must be a JSON array or contain recommendations[]")
    return data


def generate_recommendations(project_path: Path, llm_output: Path | None = None) -> List[Dict[str, Any]]:
    slides = load_slides(project_path)
    payload = build_llm_payload(project_path, slides)
    prompt_path = write_prompt(project_path, payload)

    if llm_output:
        recommendations = load_llm_output(llm_output)
        source = "llm"
    else:
        recommendations = [rule_based_recommendation(slide) for slide in slides]
        source = "rules_fallback"

    diversify_recommendations(recommendations, slides)

    for item in recommendations:
        item.setdefault("selection_source", source)

    manifest = {
        "project": project_path.name,
        "selection_source": source,
        "prompt_path": str(prompt_path),
        "recommendations": recommendations,
    }
    manifest_path = project_path / "component_recommendations_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return recommendations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_path", type=Path, help="PPT Master project directory")
    parser.add_argument(
        "--llm-output",
        type=Path,
        help="Optional JSON returned by a large model. If omitted, rule fallback is used.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = args.project_path
    recommendations = generate_recommendations(project, args.llm_output)

    output = project / "component_recommendations.json"
    output.write_text(json.dumps(recommendations, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Generated recommendations for {len(recommendations)} slides")
    print(f"Saved to: {output}")
    print(f"Model prompt saved to: {project / 'component_selection_prompt.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

