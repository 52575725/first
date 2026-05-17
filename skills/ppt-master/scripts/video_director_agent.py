#!/usr/bin/env python3
"""Multi-role PPT-to-video director for component-based video assembly.

This script is intentionally deterministic at the orchestration layer. Each
"agent" role receives structured input, writes structured JSON, and hands that
JSON to the next role. LLM output can be injected later for component selection,
but rendering always consumes a fixed render plan.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os
import shutil


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import recommend_components
except Exception as exc:  # pragma: no cover - import error is reported at runtime
    recommend_components = None  # type: ignore[assignment]
    RECOMMEND_IMPORT_ERROR = exc
else:
    RECOMMEND_IMPORT_ERROR = None

try:
    from subject_framework import framework_for_slide
except Exception:  # pragma: no cover - optional in legacy runs
    framework_for_slide = None  # type: ignore[assignment]

try:
    from visual_asset_planner import audit_downloaded_visual, audit_web_source_item
except Exception:  # pragma: no cover - visual audit remains fail-open
    audit_downloaded_visual = None  # type: ignore[assignment]
    audit_web_source_item = None  # type: ignore[assignment]


SOURCE_ASSET_RE = re.compile(
    r"(^|[/\\])slide_\d+_image_\d+\.(?:png|jpe?g|webp|gif|wmf|emf|svg)\)?$",
    re.IGNORECASE,
)
COURSE_SECTION_PREFIX_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]+[、.．]\s*|\d+(?:\.\d+)?\s+)(.+)$")


def ensure_media_tools_on_path():
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def styled_output_names(style: str) -> tuple[str, str]:
    slug = (style or "").replace("-", "_")
    if slug:
        return f"{slug}_style_video.mp4", f"{slug}_style_final.mp4"
    return "", ""


def rendered_output_names(style: str, preview_slides: int | None = None) -> tuple[str, str]:
    base_name, final_name = styled_output_names(style)
    if preview_slides and preview_slides > 0 and base_name and final_name:
        suffix = f"_preview{preview_slides}"
        return (
            base_name.replace("_video.mp4", f"{suffix}_video.mp4"),
            final_name.replace("_final.mp4", f"{suffix}_final.mp4"),
        )
    return base_name, final_name


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def run_command(args: list[str], cwd: Path, *, timeout: int | None = None) -> dict[str, Any]:
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": [str(item) for item in args],
    }


def ffprobe_duration(path: Path) -> float | None:
    if not path.exists():
        return None
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
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return None


def visual_text_len(text: str) -> float:
    units = 0.0
    for ch in str(text or ""):
        units += 1.0 if "\u4e00" <= ch <= "\u9fff" else 0.55
    return units


def compact_text(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def is_source_asset_reference(text: str) -> bool:
    text = str(text or "").strip()
    if not text:
        return False
    quoted = re.sub(r"^>\s*", "", text).strip()
    if re.fullmatch(r"\[image\]\s*image\s*\d+", quoted, re.IGNORECASE):
        return True
    if text.startswith("![") and "](" in text:
        return True
    return bool(SOURCE_ASSET_RE.search(text))


def normalize_source_text(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\(.*\)", "", str(text or "")).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if is_source_asset_reference(text):
        return ""
    return text


def normalize_math_for_voice(text: str) -> str:
    text = str(text or "")
    replacements = {
        "√": "根号",
        "×": "乘以",
        "÷": "除以",
        "±": "正负",
        "≤": "小于等于",
        "≥": "大于等于",
        "≠": "不等于",
        "²": "的平方",
        "³": "的立方",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\s*=\s*", " 等于 ", text)
    text = re.sub(r"\s*>\s*", " 大于 ", text)
    text = re.sub(r"\s*<\s*", " 小于 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def cleanup_voiceover_text(text: str) -> str:
    text = normalize_source_text(text)
    text = normalize_math_for_voice(text)
    text = text.replace("，，", "，").replace("。。", "。")
    text = re.sub(r"\s+([，。；：！？])", r"\1", text)
    text = re.sub(r"([，。；：！？])\s+", r"\1", text)
    text = re.sub(r"，([。；！？])", r"\1", text)
    text = re.sub(r"([。；！？]){2,}", r"\1", text)
    return text.strip(" ，。；;")


def clean_course_title(title: str) -> str:
    title = cleanup_voiceover_text(title)
    match = COURSE_SECTION_PREFIX_RE.match(title)
    if match and any(keyword in title for keyword in ("数据", "进制", "编码", "表示", "计算机")):
        title = match.group(1).strip()
    return title


def concise_detail(text: str) -> str:
    text = cleanup_voiceover_text(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([=＋+*/×^])\s*", r"\1", text)
    if visual_text_len(text) > 42:
        parts = re.split(r"[。；;，,]", text)
        parts = [part.strip() for part in parts if part.strip()]
        if parts:
            chosen: list[str] = []
            for part in parts:
                chosen.append(part)
                joined = "，".join(chosen)
                if len(chosen) >= 3:
                    break
                if len(chosen) >= 2 and not re.search(r"(时|如|若|当|根据.*规则)$", joined):
                    break
            text = "，".join(chosen)
    return text[:120].rstrip("，,；;。")


def clean_text_items(items: list[Any]) -> list[str]:
    cleaned: list[str] = []
    for item in items:
        text = normalize_source_text(str(item or ""))
        if text:
            cleaned.append(text)
    return cleaned


def project_display_name(project: Path) -> str:
    name = re.sub(r"_ppt\d+_\d{8}$", "", project.name)
    name = name.replace("_", " ").strip()
    return name or "课程内容"


def promote_slide_title(
    project: Path,
    slide_number: int,
    title: str,
    subtitle: str,
    paragraphs: list[Any],
    bullets: list[Any],
) -> tuple[str, str, list[str], list[str]]:
    title = normalize_source_text(title)
    subtitle = normalize_source_text(subtitle)
    paragraphs = clean_text_items(paragraphs)
    bullets = clean_text_items(bullets)
    if title:
        return title, subtitle, paragraphs, bullets

    for candidate in [subtitle, *paragraphs, *bullets]:
        if candidate:
            title = candidate
            break
    if title == subtitle:
        subtitle = ""
    elif title in paragraphs:
        paragraphs.remove(title)
    elif title in bullets:
        bullets.remove(title)
    if not title:
        title = project_display_name(project) if slide_number == 1 else f"第 {slide_number} 页"
    return title, subtitle, paragraphs, bullets


def source_slide_lines(project: Path, slide_num: int) -> list[str]:
    sources_dir = project / "sources"
    if not sources_dir.exists():
        return []
    for source in sorted(sources_dir.glob("*.md")):
        lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
        in_slide = False
        collected: list[str] = []
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
            if text.startswith("## ") or text.startswith("### Speaker Notes"):
                break
            if text.startswith("![") or text.startswith("<") or text.startswith("‹#›"):
                continue
            text = normalize_source_text(re.sub(r"^[-*+]\s+", "", text).strip())
            if text:
                collected.append(text)
        if collected:
            return collected
    return []


def source_title_and_body(lines: list[str]) -> tuple[str, list[str]]:
    clean = [normalize_source_text(line) for line in lines if normalize_source_text(line)]
    if not clean:
        return "", []
    title = clean[0]
    body = clean[1:]
    if is_catalog_number(title) and body:
        title = body[0]
        body = body[1:]
    body = [line for line in body if not is_catalog_number(line)]
    return title, body


def is_catalog_marker(text: str) -> bool:
    marker = normalize_source_text(text).replace(" ", "").lower()
    return marker in {"目录", "目錄", "本章主要内容", "主要内容", "内容提要", "contents", "outline", "agenda"}


def is_catalog_number(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}", normalize_source_text(text)))


def catalog_items_from_lines(lines: list[str]) -> list[str]:
    clean = [normalize_source_text(line) for line in lines if normalize_source_text(line)]
    has_catalog_signal = any(is_catalog_marker(line) for line in clean) or sum(1 for line in clean if is_catalog_number(line)) >= 2
    if not has_catalog_signal:
        return []

    candidates = [
        line
        for line in clean
        if not is_catalog_marker(line)
        and not is_catalog_number(line)
        and visual_text_len(line) >= 5
    ]
    seen: set[str] = set()
    candidates = [line for line in candidates if not (line in seen or seen.add(line))]

    if len(candidates) > 3:
        for keyword in ("创新创业大赛", "创新创业", "大赛"):
            related = [line for line in candidates if keyword in line]
            if len(related) >= 3:
                candidates = related
                break

    return candidates[:4]


def catalog_voiceover(items: list[str]) -> str:
    labels = ["第一", "第二", "第三", "第四"]
    parts = [f"{labels[idx]}，{item}" for idx, item in enumerate(items[:4])]
    count_label = ["零", "一", "二", "三", "四"][len(parts)] if len(parts) <= 4 else str(len(parts))
    return f"本章主要包括{count_label}部分：" + "；".join(parts) + "。"


def teaching_voiceover(title: str, subtitle: str, paragraphs: list[Any], bullets: list[Any]) -> str:
    title = clean_course_title(title)
    subtitle = cleanup_voiceover_text(subtitle)
    points = clean_text_items([*paragraphs, *bullets])
    points = [concise_detail(point) for point in points]
    points = [point for point in points if point]
    if not title and points:
        title = clean_course_title(points.pop(0))
    if not title:
        return ""

    if "根号的应用场景" in title:
        return cleanup_voiceover_text(
            "根号的应用，核心是从平方关系反求原来的量。"
            "几何里，已知正方形面积为9，边长就是根号9等于3；"
            "直角三角形里，斜边满足c等于根号下a的平方加b的平方。"
            "物理里，如果公式里先给出速度或距离的平方，也要通过开方还原。"
            "所以看到应用题，先问哪一个量被平方了，再决定怎样开根号。"
        )
    if "根号乘法法则" in title:
        return cleanup_voiceover_text(
            "根号乘法先看条件：a和b都要大于等于0。"
            "条件成立时，可以把根号a乘以根号b合并成根号下a乘b。"
            "例如根号4乘根号9，左边是2乘3等于6，右边是根号36，也等于6。"
            "这一步的重点不是背公式，而是知道合并前必须确认非负。"
        )
    if "根号除法法则" in title:
        return cleanup_voiceover_text(
            "根号除法比乘法多一个限制：分母不能为0，所以b必须大于0。"
            "条件成立时，根号a除以根号b，可以写成根号下a除以b。"
            "例如根号8除以根号2，合并后是根号4，结果等于2。"
            "如果根号出现在分母里，还要通过同乘根式完成有理化。"
        )
    if "根号在函数中的应用" in title:
        return cleanup_voiceover_text(
            "根号放进函数里，第一步永远是看定义域。"
            "比如y等于根号下x减2，必须先满足x减2大于等于0，所以x大于等于2。"
            "图像从定义域的起点出发，只向右延伸。"
            "后面讨论单调性、交点或取值范围，都不能离开这个定义域。"
        )

    if not points and not subtitle:
        return title

    if any(keyword in title for keyword in ("进制", "编码", "特点", "比较", "对比")):
        heading_indexes = [
            idx for idx, item in enumerate(points)
            if visual_text_len(item) <= 8
            and not re.search(r"\d{2,}|[=+*/]", item)
            and any(keyword in item for keyword in ("进制", "编码", "类型", "方式"))
        ]
    else:
        heading_indexes = [
            idx for idx, item in enumerate(points)
            if visual_text_len(item) <= 10 and not re.search(r"\d{3,}|[=+*/]", item)
        ]
    if len(heading_indexes) >= 2 and any(keyword in title for keyword in ("不同", "对比", "特点", "比较")):
        sections: list[str] = []
        heading_set = set(heading_indexes)
        for idx in heading_indexes[:2]:
            heading = points[idx]
            details: list[str] = []
            for next_idx, follow in enumerate(points[idx + 1:], idx + 1):
                if next_idx in heading_set:
                    break
                if visual_text_len(follow) >= 5:
                    details.append(concise_detail(follow))
                if len(details) >= 2:
                    break
            summary = "，".join(detail for detail in details if detail)
            sections.append(f"{heading}：{summary}" if summary else heading)
        joined = "；".join(sections)
        return cleanup_voiceover_text(f"把{title}放在一起比较。{joined}。")

    selected = points[:4]
    if subtitle and subtitle not in selected:
        selected.insert(0, subtitle)
    selected = selected[:4]

    def detail_sentence(item: str, idx: int) -> str:
        item = cleanup_voiceover_text(item)
        if not item:
            return ""
        if idx == 0:
            return f"先抓住{item}。"
        if item.startswith(("若", "如果", "像", "如")):
            return f"比如，{item}。"
        if item.startswith(("举例", "例如")):
            return f"{item}。"
        if item.startswith("计算"):
            return f"再用一个计算验证：{item}。"
        if item.startswith(("在", "当")):
            return f"放到具体条件里，{item}。"
        if visual_text_len(item) <= 18:
            return f"{'接着看' if idx == 1 else '还要注意'}{item}。"
        return f"再看{item}。"

    if len(selected) == 1:
        return cleanup_voiceover_text(f"{title}先抓一个核心：{selected[0]}。")
    if len(selected) == 2:
        return cleanup_voiceover_text(f"{title}可以分两步看。{detail_sentence(selected[0], 0)}{detail_sentence(selected[1], 1)}")
    if selected:
        sentences = [f"{title}{detail_sentence(selected[0], 0)}"]
        for idx, item in enumerate(selected[1:4], 1):
            if idx == 3 and visual_text_len(item) < 12:
                continue
            sentences.append(detail_sentence(item, idx))
        return cleanup_voiceover_text("".join(sentences))
    return title


def course_scene_voiceover(
    title: str,
    subtitle: str,
    paragraphs: list[Any],
    bullets: list[Any],
    *,
    framework: dict[str, Any] | None = None,
    slide_number: int = 0,
    previous_title: str = "",
) -> str:
    """Build a subject/scene-aware narration, then fall back to the legacy writer."""
    framework = framework or {}
    subject = framework.get("subject", "general")
    family = framework.get("family", "general")
    scene = framework.get("scene", "concept")
    title = clean_course_title(title)
    subtitle = cleanup_voiceover_text(subtitle)
    points = clean_text_items([*paragraphs, *bullets])
    points = [concise_detail(point) for point in points]
    points = [point for point in points if point and not is_source_asset_reference(point)]
    if not title and points:
        title = clean_course_title(points.pop(0))
    base = teaching_voiceover(title, subtitle, points[:3], points[3:])
    if not title:
        return base

    selected = []
    for point in ([subtitle] if subtitle else []) + points:
        clean = cleanup_voiceover_text(point)
        if clean and clean not in selected and clean != title:
            selected.append(clean)
        if len(selected) >= 4:
            break

    def join_points(limit: int = 3) -> str:
        if not selected:
            return ""
        labels = ["第一", "第二", "第三", "最后"]
        parts = []
        for idx, point in enumerate(selected[:limit]):
            parts.append(f"{labels[idx]}，{point}")
        return "；".join(parts)

    if family == "humanities":
        if scene in {"quote_analysis", "close_reading", "character_analysis"}:
            detail = join_points(3)
            if detail:
                return cleanup_voiceover_text(
                    f"{title}不要只停在结论上，先回到原文找证据。{detail}。"
                    "读文学类内容时，画面里的关键词要和文本细节互相对应，最后再落到人物、情感或主题。"
                )
        if scene == "reading_path":
            detail = join_points(4)
            return cleanup_voiceover_text(
                f"{title}先搭阅读路线。{detail or base}。"
                "这类页面适合用路径图把段落推进、人物关系和主题变化连起来。"
            )
        if scene == "emotion_curve":
            detail = join_points(4)
            return cleanup_voiceover_text(
                f"{title}重点看情感怎样变化。{detail or base}。"
                "讲解时按起点、转折和落点推进，避免把情感词孤立地背下来。"
            )
        if scene in {"comparison_reading", "discussion", "writing_task"}:
            detail = join_points(3)
            return cleanup_voiceover_text(
                f"{title}从文本理解过渡到迁移运用。{detail or base}。"
                "先说清依据，再比较差异，最后把方法用到表达或探究任务里。"
            )
        if selected:
            return cleanup_voiceover_text(
                f"{title}先抓住文本核心，再补充背景和细节。{join_points(3)}。"
            )

    if family == "stem":
        if scene in {"definition", "formula"}:
            detail = join_points(3)
            return cleanup_voiceover_text(
                f"{title}先把条件说清楚，再看表达式怎么成立。{detail or base}。"
                "遇到公式时不要急着代入，先检查适用范围和物理或数学含义。"
            )
        if scene in {"example", "exercise", "derivation"}:
            detail = join_points(4)
            return cleanup_voiceover_text(
                f"{title}按解题步骤展开。{detail or base}。"
                "每一步都要说明为什么这样做，最后回到条件检验结果是否合理。"
            )
        if scene in {"experiment", "variable_control"}:
            detail = join_points(4)
            return cleanup_voiceover_text(
                f"{title}先看实验目的，再看变量和现象。{detail or base}。"
                "讲实验页时要把操作、观察和结论分开，避免只念器材或步骤。"
            )
        if scene in {"application", "case"}:
            detail = join_points(3)
            return cleanup_voiceover_text(
                f"{title}把概念放回真实情境。{detail or base}。"
                "先指出对应模型，再解释它能解决什么问题。"
            )
        if scene == "misconception":
            detail = join_points(3)
            return cleanup_voiceover_text(
                f"{title}重点不是多算，而是区分容易混淆的条件。{detail or base}。"
                "先指出错因，再给出正确判断路径。"
            )

    return base


def script_style_for_framework(framework: dict[str, Any] | None) -> str:
    framework = framework or {}
    family = framework.get("family", "general")
    scene = framework.get("scene", "concept")
    if family == "humanities":
        return f"humanities_{scene}"
    if family == "stem":
        return f"stem_{scene}"
    return f"general_{scene}"


def pause_hints_for_framework(framework: dict[str, Any] | None) -> list[str]:
    framework = framework or {}
    scene = framework.get("scene", "concept")
    if scene in {"quote_analysis", "close_reading", "character_analysis"}:
        return ["after_evidence", "before_theme"]
    if scene in {"example", "exercise", "derivation", "formula"}:
        return ["after_condition", "before_result"]
    if scene in {"experiment", "variable_control"}:
        return ["after_variable", "before_conclusion"]
    return ["after_transition"]


def prepare_voiceover_for_timing(
    text: str,
    framework: dict[str, Any] | None = None,
    pause_hints: list[str] | None = None,
) -> str:
    """Keep subtitle chunking aligned with the exact text sent to TTS."""
    framework = framework or {}
    pause_hints = pause_hints or []
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    replacements = [
        ("先", "先，"),
        ("再", "再，"),
        ("最后", "最后，"),
        ("也就是说", "也就是说，"),
        ("换句话说", "换句话说，"),
        ("例如", "例如，"),
        ("注意", "注意，"),
    ]
    for before, after in replacements:
        text = text.replace(after, before)
        text = text.replace(before, after)

    scene = framework.get("scene", "")
    if scene in {"quote_analysis", "close_reading", "character_analysis"}:
        text = text.replace("原文", "原文，").replace("证据", "证据，")
    elif scene in {"formula", "definition", "example", "exercise", "derivation"}:
        text = text.replace("条件", "条件，").replace("结果", "结果，")
    elif scene in {"experiment", "variable_control"}:
        text = text.replace("变量", "变量，").replace("结论", "结论，")

    text = re.sub(r"，{2,}", "，", text)
    text = re.sub(r"。{2,}", "。", text)
    text = re.sub(r"，([。！？；])", r"\1", text)

    sentences = re.split(r"(?<=[。！？!?；;])", text)
    balanced: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > 70 and "，" in sentence:
            current = ""
            for part in [part for part in sentence.split("，") if part]:
                candidate = f"{current}，{part}" if current else part
                if len(candidate) > 44 and current:
                    balanced.append(current + "。")
                    current = part
                else:
                    current = candidate
            if current:
                balanced.append(current if current[-1] in "。！？；;" else current + "。")
        else:
            balanced.append(sentence if sentence[-1] in "。！？；;" else sentence + "。")

    if "after_transition" in pause_hints and len(balanced) >= 2:
        balanced[0] = balanced[0].rstrip("。") + "。"
    return "".join(balanced)


def strip_page_lead(text: str, title: str) -> str:
    text = cleanup_voiceover_text(text)
    title = cleanup_voiceover_text(title)
    if not text:
        return ""
    patterns = [
        rf"^这一页(?:主要)?讲{re.escape(title)}[，。]?",
        rf"^这一页(?:重点)?看{re.escape(title)}[，。]?",
        rf"^这一页围绕{re.escape(title)}展开[，。]?",
        rf"^这一页比较{re.escape(title)}[，。]?",
        rf"^本页(?:主要)?讲{re.escape(title)}[，。]?",
        rf"^本页(?:重点)?看{re.escape(title)}[，。]?",
        rf"^本页围绕{re.escape(title)}展开[，。]?",
        rf"^围绕{re.escape(title)}展开[，,]?重点包括[：:]?",
        rf"^{re.escape(title)}这里抓住几个关键点[：:]?",
        rf"^{re.escape(title)}(?:的)?核心是[：:]?",
        r"^这一页(?:主要)?讲",
        r"^这一页(?:重点)?看",
        r"^这一页围绕",
        r"^这一页比较",
        r"^本页(?:主要)?讲",
        r"^本页(?:重点)?看",
        r"^本页围绕",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text).strip(" ，。；;")
    if title and text.startswith(title + "。"):
        text = text[len(title) + 1 :].strip(" ，。；;")
    if title and text == title:
        return ""
    return cleanup_voiceover_text(text)


def is_section_transition_voiceover(text: str) -> bool:
    text = cleanup_voiceover_text(text)
    if "章节过渡" in text or "先建立章节位置" in text:
        return True
    return bool(re.match(r"^(接下来|下面|现在)进入.+这一部分", text))


def is_opening_or_catalog_voiceover(text: str) -> bool:
    text = cleanup_voiceover_text(text)
    return text.startswith(("本课主要", "本章主要", "大家好"))


def ensure_sentence_end(text: str) -> str:
    text = cleanup_voiceover_text(text)
    if text and text[-1] not in "。！？!?":
        return text + "。"
    return text


def is_closing_title(title: str) -> bool:
    title = cleanup_voiceover_text(title)
    return any(keyword in title for keyword in ("感谢观看", "谢谢", "结语", "总结", "回顾", "结束"))


def is_broad_section_title(title: str) -> bool:
    title = cleanup_voiceover_text(title)
    if not title or is_closing_title(title) or is_catalog_marker(title):
        return False
    return any(keyword in title for keyword in ("基础", "概念", "技巧", "方法", "进阶", "应用", "知识", "模块", "部分"))


def section_transition_voiceover(slide_number: int, title: str, previous_title: str) -> str:
    title = cleanup_voiceover_text(title)
    previous_title = cleanup_voiceover_text(previous_title)
    if any(keyword in title for keyword in ("基础", "概念", "入门")):
        return ensure_sentence_end(f"先从{title}开始。把符号、定义和适用条件讲清楚，后面的运算会更顺")
    if any(keyword in title for keyword in ("运算", "技巧", "方法", "化简")):
        return ensure_sentence_end(f"概念铺好以后，进入{title}。后面把规则、例题和化简步骤连起来看，重点是知道什么时候能用")
    if any(keyword in title for keyword in ("进阶", "应用", "综合", "方程", "函数")):
        return ensure_sentence_end(f"基础规则已经铺开，接着进入{title}。这里会把前面的概念放进更复杂的题型里，重点检查条件和变形是否合理")
    if previous_title and previous_title not in {"目录", title}:
        return ensure_sentence_end(f"从{previous_title}过渡过来，下面进入{title}。先抓住主线，再看具体细节")
    return ensure_sentence_end(f"下面进入{title}。先把主线搭起来，再展开具体内容")


def opening_voiceover(title: str) -> str:
    title = cleanup_voiceover_text(title) or "这节课"
    return ensure_sentence_end(
        f"大家好，这节课我们一起学习{title}。先把概念讲清楚，再把运算规则和常见题型连起来看"
    )


def closing_voiceover(voiceover: str, title: str, previous_title: str) -> str:
    text = strip_page_lead(voiceover, title)
    text = re.sub(
        r"^(?:下一步，把重点放到|再往下，来看|在这个基础上，继续看|有了.+?的铺垫，接着看|先来看)感谢观看[。；;，,]?",
        "",
        text,
    ).strip(" ，。；;")
    if not text or text == title:
        previous = cleanup_voiceover_text(previous_title)
        if previous:
            text = f"最后做个收束。前面从{previous}一路展开，核心仍然是先判断根号是否有意义，再按规则化简和求解"
        else:
            text = "最后做个收束。根号学习的主线，是先判断是否有意义，再按规则完成化简、运算和应用"
    elif not text.startswith(("最后", "回到", "总结")):
        text = f"最后做个收束。{text}"
    return ensure_sentence_end(text)


def continuity_connector(slide_number: int, title: str, previous_title: str) -> str:
    title = cleanup_voiceover_text(title)
    previous_title = cleanup_voiceover_text(previous_title)
    if slide_number <= 1 or is_closing_title(title):
        return ""
    if not previous_title or previous_title in {"目录", title}:
        return f"先来看{title}。"
    joined = f"{previous_title} {title}"
    if any(keyword in joined for keyword in ("文本", "课文", "赏析", "意象", "母亲", "情感", "史铁生", "地坛", "联读")):
        if any(keyword in title for keyword in ("母亲", "人物", "形象")):
            return f"前面抓住了文本线索，现在把镜头转向{title}。"
        if any(keyword in title for keyword in ("情感", "主题", "生命", "表达")):
            return f"有了细节依据，再往前推进到{title}。"
        if any(keyword in title for keyword in ("联读", "拓展", "迁移")):
            return f"理解了原文表达，再把方法迁移到{title}。"
        return f"顺着{previous_title}的细节，继续看{title}。"
    if any(keyword in joined for keyword in ("摩擦", "受力", "测力计", "正压力", "粗糙", "牛顿")):
        if any(keyword in title for keyword in ("条件", "产生", "定义")):
            return f"先把现象框住，接着看{title}。"
        if any(keyword in title for keyword in ("实验", "探究", "影响")):
            return f"概念说清楚后，用{title}来验证。"
        if any(keyword in title for keyword in ("应用", "增大", "减小")):
            return f"模型建立之后，再回到{title}。"
        return f"沿着受力分析的思路，进入{title}。"
    if any(keyword in title for keyword in ("定义", "性质", "分类")):
        return f"顺着{previous_title}，再看{title}。"
    if any(keyword in title for keyword in ("乘法", "除法", "加减", "化简")):
        return f"规则开始落到运算上，接着看{title}。"
    if any(keyword in title for keyword in ("方程", "不等式", "函数")):
        return f"方法继续往题型里推进，来看{title}。"
    if any(keyword in title for keyword in ("无理数", "应用", "场景")):
        return f"理解规则之后，再把它放到{title}里看。"
    variants = [
        f"有了{previous_title}的铺垫，{title}就更容易理解。",
        f"顺着刚才的思路，{title}主要解决下一个问题。",
        f"接下来把重点转到{title}。",
        f"再往下，{title}会把前面的规则用起来。",
        f"把前面的结论用起来，进入{title}。",
        f"现在把视角切到{title}。",
    ]
    return variants[slide_number % len(variants)]


def smooth_voiceover(voiceover: str, *, slide_number: int, title: str, previous_title: str) -> str:
    voiceover = cleanup_voiceover_text(voiceover)
    title = cleanup_voiceover_text(title)
    if not voiceover:
        return ""
    if is_closing_title(title):
        return closing_voiceover(voiceover, title, previous_title)
    if is_opening_or_catalog_voiceover(voiceover):
        return ensure_sentence_end(voiceover)
    if is_section_transition_voiceover(voiceover):
        return section_transition_voiceover(slide_number, title, previous_title)
    body = strip_page_lead(voiceover, title)
    if slide_number <= 1 and not body:
        return opening_voiceover(title)
    if not body and is_broad_section_title(title):
        return section_transition_voiceover(slide_number, title, previous_title)
    if not body:
        return ensure_sentence_end(continuity_connector(slide_number, title, previous_title) or voiceover)
    connector = continuity_connector(slide_number, title, previous_title)
    if connector and body.startswith(title):
        body = body[len(title) :].strip(" ，。；;：:")
        return ensure_sentence_end(f"{connector}{body}")
    if connector:
        return ensure_sentence_end(f"{connector}{body}")
    return ensure_sentence_end(body or voiceover)


def limit_slides(slides: list[dict[str, Any]], preview_slides: int | None) -> list[dict[str, Any]]:
    if not preview_slides or preview_slides <= 0:
        return slides
    return slides[:preview_slides]


def sentence_chunks(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*", text)
    chunks = [part.strip() for part in parts if part.strip()]
    if chunks:
        return chunks
    return [text]


def parse_srt(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore").strip())
    chunks: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        text = " ".join(lines[2:])
        chunks.append({"timecode": lines[1], "text": text})
    return chunks


def format_srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_srt_timestamp(value: str) -> float:
    match = re.match(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        return 0.0
    hours, minutes, seconds, millis = [int(part) for part in match.groups()]
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def wrap_subtitle_text(text: str, max_chars: int = 22) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    lines: list[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= max_chars:
            lines.append(current)
            current = ""
            if len(lines) >= 2:
                break
    if current and len(lines) < 2:
        lines.append(current)
    return "\n".join(lines)


def write_srt_from_script_plan(project: Path, output_path: Path) -> bool:
    payload = read_json(project / "video_script_plan.json", {})
    slides = payload.get("slides", []) if isinstance(payload, dict) else []
    if not slides:
        return False

    lines: list[str] = []
    index = 1
    cursor = 0.0
    for slide in slides:
        duration = float(slide.get("duration", 5.0) or 5.0)
        chunks = slide.get("subtitle_chunks") or []
        if not chunks:
            text = slide.get("voiceover", "")
            chunks = [{"start": 0.0, "end": duration, "text": text}]
        for chunk in chunks:
            text = compact_text(chunk.get("text", ""), 80)
            if "No extractable text content" in text:
                continue
            if not text:
                continue
            start = cursor + float(chunk.get("start", 0.0) or 0.0)
            end = cursor + float(chunk.get("end", duration) or duration)
            if end <= start:
                end = min(cursor + duration, start + 1.2)
            lines.extend(
                [
                    str(index),
                    f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}",
                    wrap_subtitle_text(text),
                    "",
                ]
            )
            index += 1
        cursor += duration

    if index == 1:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return True


def load_existing_visual_asset(project: Path, slide_number: int) -> Path | None:
    visual_dir = project / "images" / "visual_assets"
    for suffix in ("visual", "existing", "generated"):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            path = visual_dir / f"slide_{slide_number:02d}_{suffix}{ext}"
            if path.exists() and path.stat().st_size > 50_000:
                return path
    return None


def load_existing_background_decor(project: Path, slide_number: int) -> Path | None:
    decor_dir = project / "images" / "background_decor"
    for suffix in ("background", "decor", "texture"):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            path = decor_dir / f"slide_{slide_number:02d}_{suffix}{ext}"
            if path.exists() and path.stat().st_size > 40_000:
                return path
    return None


def load_image_sources(project: Path) -> dict[int, dict[str, Any]]:
    sources = read_json(project / "images" / "visual_assets" / "image_sources.json", {})
    items = sources.get("items", []) if isinstance(sources, dict) else []
    by_slide: dict[int, dict[str, Any]] = {}
    for item in items:
        raw_slide = item.get("slide") or item.get("slide_number")
        try:
            slide_number = int(raw_slide)
        except Exception:
            continue
        by_slide[slide_number] = item
    return by_slide


def load_background_decor_sources(project: Path) -> dict[int, dict[str, Any]]:
    sources = read_json(project / "images" / "background_decor" / "image_sources.json", {})
    items = sources.get("items", []) if isinstance(sources, dict) else []
    by_slide: dict[int, dict[str, Any]] = {}
    for item in items:
        raw_slide = item.get("slide") or item.get("slide_number")
        try:
            slide_number = int(raw_slide)
        except Exception:
            continue
        by_slide[slide_number] = item
    return by_slide


def load_visual_asset_executions(project: Path) -> dict[int, dict[str, Any]]:
    manifest = read_json(project / "images" / "visual_asset_manifest.json", {})
    executions = manifest.get("executed_assets", []) if isinstance(manifest, dict) else []
    by_slide: dict[int, dict[str, Any]] = {}
    for item in executions:
        if item.get("kind") not in (None, "", "visual_asset"):
            continue
        try:
            slide_number = int(item.get("slide_number"))
        except Exception:
            continue
        by_slide[slide_number] = item
    return by_slide


HUMANITIES_COMPONENT_FALLBACKS = [
    "magazine_spread",
    "quote_focus",
    "split_text_visual",
    "photo_story",
    "rounded_step_cards",
    "insight_cards",
]
STEM_ONLY_COMPONENTS = {
    "blackboard_derivation",
    "formula_walkthrough",
    "radial_concept_map",
    "checkpoint_ladder",
    "misconception_compare",
}
HUMANITIES_ONLY_COMPONENTS = {"quote_focus", "magazine_spread", "photo_story"}


def guard_component_for_framework(component: str, framework: dict[str, Any] | None) -> tuple[str, str]:
    framework = framework or {}
    family = framework.get("family", "general")
    pool = [item for item in framework.get("component_pool", []) if item]
    if family == "humanities" and component in STEM_ONLY_COMPONENTS:
        for candidate in pool + HUMANITIES_COMPONENT_FALLBACKS:
            if candidate not in STEM_ONLY_COMPONENTS:
                return candidate, f"replaced STEM component {component} for humanities slide"
    if family == "stem" and component in HUMANITIES_ONLY_COMPONENTS:
        for candidate in pool + ["formula_walkthrough", "process_flow", "rounded_step_cards"]:
            if candidate not in HUMANITIES_ONLY_COMPONENTS:
                return candidate, f"replaced humanities component {component} for STEM slide"
    return component, ""


def audit_visual_source_for_render(
    *,
    source_meta: dict[str, Any],
    decision: dict[str, Any],
    execution: dict[str, Any],
    asset_path: Path | None = None,
) -> dict[str, Any]:
    if execution.get("visual_audit"):
        return execution["visual_audit"]
    query = source_meta.get("search_query") or ""
    if not query:
        for segment in decision.get("segments", []):
            if segment.get("mode") == "search" and segment.get("query"):
                query = segment["query"]
                break
    framework = decision.get("subject_framework") or {}
    if asset_path and audit_downloaded_visual is not None and source_meta and asset_path.exists():
        return audit_downloaded_visual(asset_path, source_meta, query=query, framework=framework)
    if execution.get("web_relevance_audit"):
        audit = dict(execution["web_relevance_audit"])
        if execution.get("image_content_audit"):
            audit = {
                "score": audit.get("score", 0),
                "accepted": audit.get("accepted"),
                "reason": audit.get("reason", ""),
                "metadata": execution["web_relevance_audit"],
                "content": execution.get("image_content_audit", {}),
            }
        return audit
    if audit_web_source_item is None or not source_meta:
        return {}
    return audit_web_source_item(source_meta, query=query, framework=framework)


def framework_for_qa_slide(
    title: str,
    lines: list[str],
    slide_number: int,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if fallback and fallback.get("subject") not in {"", None, "general"}:
        return fallback
    if framework_for_slide is None:
        return fallback or {}
    try:
        return framework_for_slide(title, lines, slide_number)
    except Exception:
        return fallback or {}


@dataclass
class RoleResult:
    role: str
    status: str
    outputs: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "status": self.status,
            "outputs": self.outputs,
            "notes": self.notes,
            "metrics": self.metrics,
        }


class AgentContext:
    def __init__(self, project: Path, args: argparse.Namespace):
        self.project = project
        self.args = args
        self.state: dict[str, Any] = {
            "project": str(project),
            "generated_at": now_iso(),
            "director": {
                "goal": "Build a component-based video from PPT content through structured specialist roles.",
                "style": args.style,
                "render_requested": args.render,
                "asset_execution_requested": args.execute_assets,
            },
            "roles": [],
        }

    def add_result(self, result: RoleResult) -> None:
        self.state["roles"].append(result.to_dict())
        write_json(self.project / "video_agent_state.json", self.state)


class PPTParserAgent:
    name = "ParserAgent"

    def run(self, ctx: AgentContext) -> tuple[list[dict[str, Any]], RoleResult]:
        structure_path = ctx.project / "slide_structure.json"
        notes: list[str] = []
        if not structure_path.exists():
            command = [
                sys.executable,
                str(SCRIPT_DIR / "extract_structure.py"),
                str(ctx.project),
                "--method",
                "auto",
            ]
            result = run_command(command, SCRIPT_DIR)
            if not result["ok"]:
                raise RuntimeError(result["stderr"] or "extract_structure.py failed")
            notes.append("slide_structure.json was missing; extracted it with extract_structure.py.")

        if recommend_components is None:
            raise RuntimeError(f"recommend_components import failed: {RECOMMEND_IMPORT_ERROR}")

        slides = recommend_components.load_slides(ctx.project)
        image_sources = load_image_sources(ctx.project)
        slide_ir: list[dict[str, Any]] = []
        for slide in slides:
            slide_number = int(slide.get("slide_number") or len(slide_ir) + 1)
            source_title, source_paragraphs = source_title_and_body(
                [slide.get("source_title", ""), *slide.get("source_paragraphs", [])]
            )
            if source_title:
                title, subtitle, paragraphs, bullets = source_title, "", source_paragraphs, []
            else:
                title, subtitle, paragraphs, bullets = promote_slide_title(
                    ctx.project,
                    slide_number,
                    slide.get("title", ""),
                    slide.get("subtitle", ""),
                    slide.get("paragraphs", []),
                    slide.get("bullets", []),
                )
            text_blocks = slide.get("text_blocks", [])
            blocks = []
            for block in text_blocks:
                block_text = normalize_source_text(block.get("text", ""))
                if not block_text:
                    continue
                role = block.get("role", "body")
                level = 1 if role == "title" else 2 if role == "subtitle" else 3
                blocks.append(
                    {
                        "text": block_text,
                        "role": role,
                        "level": level,
                        "bbox": [
                            block.get("x", 0),
                            block.get("y", 0),
                            block.get("w", 0),
                            block.get("h", 0),
                        ],
                        "bbox_norm": block.get("bbox_norm", {}),
                        "confidence": block.get("conf"),
                    }
                )

            visual_asset = load_existing_visual_asset(ctx.project, slide_number)
            image_refs = []
            if visual_asset:
                source_meta = image_sources.get(slide_number, {})
                image_refs.append(
                    {
                        "path": str(visual_asset),
                        "source": source_meta.get("provider", "visual_assets"),
                        "title": source_meta.get("title", ""),
                        "license": source_meta.get("license_name", ""),
                        "usable": True,
                    }
                )

            content_text = "\n".join(
                [str(title), *[str(item) for item in paragraphs], *[str(item) for item in bullets]]
            ).strip()
            slide_ir.append(
                {
                    "slide_number": slide_number,
                    "title": title,
                    "subtitle": subtitle,
                    "paragraphs": paragraphs,
                    "bullets": bullets,
                    "text_blocks": blocks,
                    "layout": slide.get("layout", {}),
                    "signals": slide.get("signals", {}),
                    "images": image_refs,
                    "content_text": content_text,
                    "content_units": round(visual_text_len(content_text), 1),
                    "source": slide.get("source", "unknown"),
                    "parser_notes": {
                        "uses_source_markdown": bool(slide.get("source_title")),
                        "ocr_backend": slide.get("ocr_backend", ""),
                    },
                }
            )

        path = write_json(ctx.project / "slide_ir.json", slide_ir)
        return slide_ir, RoleResult(
            self.name,
            "completed",
            outputs={"slide_ir": str(path)},
            notes=notes,
            metrics={"slides": len(slide_ir)},
        )


class PreflightAgent:
    name = "PreflightAgent"

    def run(self, ctx: AgentContext, slide_ir: list[dict[str, Any]]) -> RoleResult:
        slides_report: list[dict[str, Any]] = []
        page_types: dict[str, int] = {}
        semantic_page_types: dict[str, int] = {}
        subject_counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}

        for slide in slide_ir:
            slide_number = int(slide["slide_number"])
            title = normalize_source_text(slide.get("title", ""))
            paragraphs = [normalize_source_text(item) for item in slide.get("paragraphs", []) if normalize_source_text(item)]
            bullets = [normalize_source_text(item) for item in slide.get("bullets", []) if normalize_source_text(item)]
            source_lines = source_slide_lines(ctx.project, slide_number)
            catalog_items = catalog_items_from_lines(source_lines or [title, *paragraphs, *bullets])
            layout = slide.get("layout", {}) or {}
            page_type = layout.get("page_type", "unknown")
            page_types[page_type] = page_types.get(page_type, 0) + 1
            framework = framework_for_qa_slide(title, source_lines + paragraphs + bullets, slide_number, {})
            subject = framework.get("subject", "general")
            semantic_page_type = framework.get("page_type") or framework.get("scene") or "concept"
            subject_counts[subject] = subject_counts.get(subject, 0) + 1
            semantic_page_types[semantic_page_type] = semantic_page_types.get(semantic_page_type, 0) + 1
            text_units = visual_text_len(" ".join([title, *paragraphs, *bullets]))
            image_count = len(slide.get("images", []))

            risks: list[str] = []
            suggestions: list[str] = []
            if framework.get("image_policy") in {"prefer", "required"} and image_count == 0 and text_units < 80:
                risks.append("visual_enrichment_needed")
                suggestions.append("This page should receive a searched/generated subject-relevant visual, not only text.")
            if text_units < 18 and image_count == 0 and semantic_page_type not in {"cover", "summary"}:
                risks.append("sparse_explanation_needed")
                suggestions.append("Sparse content should be expanded with explanation, examples, or context cards.")
            if catalog_items:
                risks.append("catalog_detected")
                suggestions.append("目录页将自动生成连贯导读旁白。")
            if text_units < 4 and image_count == 0:
                risks.append("empty_or_low_text")
                suggestions.append("该页文字很少，可能需要保留原图或补充讲稿。")
            if text_units < 12 and image_count > 0:
                risks.append("image_heavy")
                suggestions.append("该页可能依赖图片内容，建议抽检画面。")
            if visual_text_len(title) > 34:
                risks.append("long_title")
                suggestions.append("标题较长，渲染时会自动缩放/换行。")
            if text_units > 180:
                risks.append("dense_text")
                suggestions.append("正文较密，建议使用预览模式确认字幕和版式。")
            if sum(1 for line in source_lines if is_catalog_number(line)) >= 2 and not catalog_items:
                risks.append("numeric_noise")
                suggestions.append("检测到多个数字编号，可能存在 OCR/目录噪声。")
            if len(bullets) >= 6:
                risks.append("many_bullets")
                suggestions.append("列表项较多，建议拆成多卡片或流程页。")

            for risk in risks:
                risk_counts[risk] = risk_counts.get(risk, 0) + 1

            severity = "low"
            if any(
                risk in risks
                for risk in (
                    "empty_or_low_text",
                    "dense_text",
                    "numeric_noise",
                    "visual_enrichment_needed",
                    "sparse_explanation_needed",
                )
            ):
                severity = "medium"
            if "empty_or_low_text" in risks and image_count == 0:
                severity = "high"

            slides_report.append(
                {
                    "slide_number": slide_number,
                    "title": title,
                    "page_type": page_type,
                    "semantic_page_type": semantic_page_type,
                    "subject": subject,
                    "scene": framework.get("scene", "concept"),
                    "subject_framework": framework,
                    "text_units": round(text_units, 1),
                    "image_count": image_count,
                    "catalog_items": catalog_items,
                    "risks": risks,
                    "severity": severity,
                    "suggestions": suggestions,
                }
            )

        high = sum(1 for item in slides_report if item["severity"] == "high")
        medium = sum(1 for item in slides_report if item["severity"] == "medium")
        payload = {
            "project": ctx.project.name,
            "generated_at": now_iso(),
            "summary": {
                "slides": len(slide_ir),
                "high_risk_slides": high,
                "medium_risk_slides": medium,
                "page_types": page_types,
                "semantic_page_types": semantic_page_types,
                "subject_counts": subject_counts,
                "risk_counts": risk_counts,
                "preview_recommended": bool(high or medium),
                "preview_first_recommended": bool(
                    high
                    or medium
                    or any(key in risk_counts for key in ("visual_enrichment_needed", "sparse_explanation_needed"))
                ),
                "preview_slides": ctx.args.preview_slides or min(5, len(slide_ir)),
            },
            "slides": slides_report,
        }
        path = write_json(ctx.project / "preflight_report.json", payload)
        status = "completed"
        notes = ["Wrote preflight_report.json."]
        if high or medium:
            notes.append("Preflight found slides that should be checked with preview mode.")
        return RoleResult(
            self.name,
            status,
            outputs={"preflight_report": str(path)},
            notes=notes,
            metrics={"slides": len(slide_ir), "high_risk_slides": high, "medium_risk_slides": medium},
        )


class ScriptWriterAgent:
    name = "ScriptWriterAgent"

    def run(self, ctx: AgentContext, slide_ir: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], RoleResult]:
        srt_chunks = parse_srt(ctx.project / "exports" / f"{ctx.project.name}_new.srt")
        script_plan: list[dict[str, Any]] = []
        previous_title = ""
        for slide in slide_ir:
            slide_number = int(slide["slide_number"])
            audio = ctx.project / "audio" / f"page_{slide_number:02d}.mp3"
            duration = ffprobe_duration(audio)
            if duration is None:
                duration = max(5.0, min(28.0, 3.0 + slide.get("content_units", 0) / 18.0))
            source_lines = source_slide_lines(ctx.project, slide_number)
            catalog_items = catalog_items_from_lines(
                source_lines
                or [
                    slide.get("title", ""),
                    *slide.get("paragraphs", []),
                    *slide.get("bullets", []),
                ]
            )
            framework = framework_for_qa_slide(
                slide.get("title", ""),
                source_lines + [str(item) for item in slide.get("paragraphs", [])] + [str(item) for item in slide.get("bullets", [])],
                slide_number,
                {},
            )
            if catalog_items:
                voiceover = catalog_voiceover(catalog_items)
            else:
                voiceover = course_scene_voiceover(
                    slide.get("title", ""),
                    slide.get("subtitle", ""),
                    slide.get("paragraphs", []),
                    slide.get("bullets", []),
                    framework=framework,
                    slide_number=slide_number,
                    previous_title=previous_title,
                )
            if not voiceover:
                voiceover = f"第 {slide_number} 页为视觉内容页，请结合画面查看。"
            title = cleanup_voiceover_text(slide.get("title", ""))
            voiceover = smooth_voiceover(
                voiceover,
                slide_number=slide_number,
                title=title,
                previous_title=previous_title,
            )
            pause_hints = pause_hints_for_framework(framework)
            tts_voiceover = prepare_voiceover_for_timing(voiceover, framework, pause_hints)
            chunks = sentence_chunks(tts_voiceover)
            if not chunks:
                chunks = [slide.get("title", f"Slide {slide_number}")]
            chunks = chunks[:10]
            weights = [max(1.0, visual_text_len(chunk)) for chunk in chunks]
            total_weight = sum(weights) or float(len(chunks))
            min_span = min(1.4, max(0.75, duration / max(1, len(chunks) * 3.0)))
            timed_chunks = []
            cursor = 0.0
            for idx, (chunk, weight) in enumerate(zip(chunks, weights)):
                if idx == len(chunks) - 1:
                    end = duration
                else:
                    remaining_min = min_span * (len(chunks) - idx - 1)
                    proportional = duration * weight / total_weight
                    end = min(duration - remaining_min, cursor + max(min_span, proportional))
                timed_chunks.append(
                    {
                        "start": round(cursor, 2),
                        "end": round(end, 2),
                        "text": compact_text(chunk, 80),
                    }
                )
                cursor = end

            script_plan.append(
                {
                    "slide_number": slide_number,
                    "title": title,
                    "duration": round(duration, 2),
                    "voiceover": compact_text(voiceover, 900),
                    "tts_voiceover": compact_text(tts_voiceover, 900),
                    "subtitle_chunks": timed_chunks,
                    "audio_path": str(audio) if audio.exists() else "",
                    "script_style": script_style_for_framework(framework),
                    "scene": framework.get("scene", "concept"),
                    "subject_framework": framework,
                    "pause_hints": pause_hints,
                }
            )
            if title and not is_catalog_marker(title):
                previous_title = title

        path = write_json(ctx.project / "video_script_plan.json", {
            "project": ctx.project.name,
            "generated_at": now_iso(),
            "source_srt_chunks": len(srt_chunks),
            "slides": script_plan,
        })
        return script_plan, RoleResult(
            self.name,
            "completed",
            outputs={"video_script_plan": str(path)},
            metrics={"slides": len(script_plan), "source_srt_chunks": len(srt_chunks)},
        )


class ComponentSelectorAgent:
    name = "ComponentSelectorAgent"

    def run(self, ctx: AgentContext, slide_ir: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], RoleResult]:
        if recommend_components is None:
            raise RuntimeError(f"recommend_components import failed: {RECOMMEND_IMPORT_ERROR}")
        recommendations = recommend_components.generate_recommendations(ctx.project, ctx.args.llm_output)
        write_json(ctx.project / "component_recommendations.json", recommendations)

        component_plan: list[dict[str, Any]] = []
        for item in recommendations:
            strategy = item.get("render_strategy", {})
            selected = strategy.get("visual_effect") or item.get("primary_component")
            selected, guard_reason = guard_component_for_framework(
                selected,
                strategy.get("subject_framework") or {},
            )
            if guard_reason:
                strategy = {**strategy, "visual_effect": selected}
            component_plan.append(
                {
                    "slide_number": item.get("slide_number"),
                    "base_component": item.get("primary_component"),
                    "selected_component": selected,
                    "alternatives": item.get("alternatives", []),
                    "confidence": item.get("confidence"),
                    "selection_source": item.get("selection_source"),
                    "reason": "; ".join(part for part in (item.get("reason", ""), guard_reason) if part),
                    "render_strategy": strategy,
                }
            )

        path = write_json(ctx.project / "component_plan.json", {
            "project": ctx.project.name,
            "generated_at": now_iso(),
            "slides": component_plan,
        })
        unique_components = sorted({item["selected_component"] for item in component_plan if item.get("selected_component")})
        return component_plan, RoleResult(
            self.name,
            "completed",
            outputs={
                "component_plan": str(path),
                "component_recommendations": str(ctx.project / "component_recommendations.json"),
                "component_prompt": str(ctx.project / "component_selection_prompt.md"),
            },
            metrics={"slides": len(component_plan), "unique_selected_components": unique_components},
        )


class NarrationAudioAgent:
    name = "NarrationAudioAgent"

    def run(self, ctx: AgentContext) -> RoleResult:
        if not ctx.args.render or ctx.args.no_generate_audio:
            return RoleResult(self.name, "skipped", notes=["Audio generation disabled or render not requested."])

        plan = read_json(ctx.project / "video_script_plan.json", {})
        expected_slide_numbers = [
            int(item.get("slide_number", 0))
            for item in plan.get("slides", [])
            if item.get("slide_number")
        ]
        expected_audio = [ctx.project / "audio" / f"page_{slide_number:02d}.mp3" for slide_number in expected_slide_numbers]
        existing = [path for path in expected_audio if path.exists()]
        if expected_audio and len(existing) == len(expected_audio) and not ctx.args.force_audio:
            return RoleResult(
                self.name,
                "completed",
                notes=["Reused existing page audio for all planned slides."],
                metrics={"audio_files": len(existing)},
            )

        subject_counts: dict[str, int] = {}
        for slide in plan.get("slides", []):
            framework = slide.get("subject_framework") or {}
            subject = framework.get("subject") or "general"
            subject_counts[subject] = subject_counts.get(subject, 0) + 1
        dominant_subject = max(subject_counts, key=subject_counts.get) if subject_counts else "general"
        edge_rate = ctx.args.tts_edge_rate
        edge_pitch = ctx.args.tts_edge_pitch
        if edge_rate == "-6%":
            if dominant_subject in {"literature", "history", "politics"}:
                edge_rate = "-8%"
            elif dominant_subject in {"math", "physics", "chemistry", "biology"}:
                edge_rate = "-5%"

        command = [
            sys.executable,
            str(SCRIPT_DIR / "script_plan_to_audio.py"),
            str(ctx.project),
            "--provider",
            ctx.args.tts_provider,
            "--voice",
            ctx.args.tts_voice,
            "--edge-rate",
            edge_rate,
            "--edge-pitch",
            edge_pitch,
            "--rate",
            str(ctx.args.tts_rate),
        ]
        if ctx.args.force_audio:
            command.append("--force")
        result = run_command(command, SCRIPT_DIR, timeout=ctx.args.audio_timeout)
        if not result["ok"]:
            raise RuntimeError(result["stderr"] or result["stdout"] or "script_plan_to_audio.py failed")

        manifest_path = ctx.project / "audio" / "script_audio_manifest.json"
        manifest = read_json(manifest_path, {})
        outputs = manifest.get("outputs", []) if isinstance(manifest, dict) else []
        ready = sum(1 for item in outputs if item.get("status") in {"ready", "exists"})
        return RoleResult(
            self.name,
            "completed",
            outputs={"audio_manifest": str(manifest_path)},
            metrics={"ready_audio_files": ready, "slides": len(outputs), "dominant_subject": dominant_subject},
        )


class AssetCuratorAgent:
    name = "AssetCuratorAgent"

    def run(self, ctx: AgentContext, slide_ir: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], RoleResult]:
        manifest_path = ctx.project / "images" / "visual_asset_manifest.json"
        visual_dir = ctx.project / "images" / "visual_assets"
        decor_dir = ctx.project / "images" / "background_decor"
        existing_assets = {
            int(path.stem.split("_")[1]): path
            for path in visual_dir.glob("slide_*_visual.*")
            if path.exists() and path.stat().st_size > 50_000 and re.match(r"slide_\d+_visual", path.stem)
        } if visual_dir.exists() else {}
        existing_backgrounds = {
            int(path.stem.split("_")[1]): path
            for path in decor_dir.glob("slide_*_background.*")
            if path.exists() and path.stat().st_size > 40_000 and re.match(r"slide_\d+_background", path.stem)
        } if decor_dir.exists() else {}
        missing_background_count = max(0, len(slide_ir) - len(existing_backgrounds))

        notes: list[str] = []
        should_execute_assets = ctx.args.execute_assets or (
            ctx.args.render and missing_background_count > 0 and not ctx.args.skip_assets
        )
        if should_execute_assets:
            command = [sys.executable, str(SCRIPT_DIR / "visual_asset_planner.py"), str(ctx.project), "--execute"]
            if ctx.args.asset_limit:
                command.extend(["--limit", str(ctx.args.asset_limit)])
            result = run_command(command, SCRIPT_DIR.parent.parent, timeout=ctx.args.asset_timeout)
            if not result["ok"]:
                if ctx.args.execute_assets:
                    raise RuntimeError(result["stderr"] or "visual_asset_planner.py failed")
                notes.append("background decor search failed; continuing with local fallback.")
            else:
                notes.append("visual_asset_planner.py executed search/generation.")
        elif not existing_assets and not manifest_path.exists() and not ctx.args.skip_assets:
            command = [sys.executable, str(SCRIPT_DIR / "visual_asset_planner.py"), str(ctx.project)]
            result = run_command(command, SCRIPT_DIR.parent.parent, timeout=ctx.args.asset_timeout)
            if not result["ok"]:
                raise RuntimeError(result["stderr"] or "visual_asset_planner.py dry-run failed")
            notes.append("No selected visual assets found; wrote a dry-run visual asset plan.")
        elif existing_assets:
            notes.append("Reused existing selected visual assets.")

        manifest = read_json(manifest_path, {})
        image_sources = load_image_sources(ctx.project)
        background_sources = load_background_decor_sources(ctx.project)
        executions = load_visual_asset_executions(ctx.project)
        by_slide: dict[int, dict[str, Any]] = {}
        for slide in slide_ir:
            slide_number = int(slide["slide_number"])
            decision = next(
                (
                    item for item in manifest.get("decisions", [])
                    if int(item.get("slide_number", -1)) == slide_number
                ),
                {},
            ) if isinstance(manifest, dict) else {}
            source_meta = image_sources.get(slide_number, {})
            background_path = load_existing_background_decor(ctx.project, slide_number)
            background_source = background_sources.get(slide_number, {})
            execution = executions.get(slide_number, {})
            asset_path = load_existing_visual_asset(ctx.project, slide_number)
            relevance_audit = audit_visual_source_for_render(
                source_meta=source_meta,
                decision=decision,
                execution=execution,
                asset_path=asset_path,
            )
            if asset_path and relevance_audit and relevance_audit.get("accepted") is False:
                notes.append(
                    f"Slide {slide_number}: ignored visual asset after relevance audit "
                    f"({relevance_audit.get('reason', 'no reason')})."
                )
                asset_path = None
            by_slide[slide_number] = {
                "slide_number": slide_number,
                "mode": decision.get("recommended_mode", "existing" if asset_path else "none"),
                "reason": decision.get("reason", ""),
                "asset_path": str(asset_path) if asset_path else "",
                "asset_ready": bool(asset_path),
                "source": source_meta,
                "execution": execution,
                "visual_relevance": relevance_audit,
                "background_decor_path": str(background_path) if background_path else "",
                "background_decor_ready": bool(background_path),
                "background_decor_source": background_source,
            }

        path = write_json(ctx.project / "asset_plan.json", {
            "project": ctx.project.name,
            "generated_at": now_iso(),
            "slides": list(by_slide.values()),
        })
        return by_slide, RoleResult(
            self.name,
            "completed",
            outputs={"asset_plan": str(path), "visual_asset_manifest": str(manifest_path)},
            notes=notes,
            metrics={
                "slides_with_ready_assets": sum(1 for item in by_slide.values() if item["asset_ready"]),
                "slides_with_background_decor": sum(1 for item in by_slide.values() if item["background_decor_ready"]),
                "slides": len(by_slide),
            },
        )


class LayoutComposerAgent:
    name = "LayoutComposerAgent"

    def run(
        self,
        ctx: AgentContext,
        slide_ir: list[dict[str, Any]],
        script_plan: list[dict[str, Any]],
        component_plan: list[dict[str, Any]],
        asset_plan: dict[int, dict[str, Any]],
    ) -> tuple[dict[str, Any], RoleResult]:
        scripts = {int(item["slide_number"]): item for item in script_plan}
        components = {int(item["slide_number"]): item for item in component_plan}
        render_slides = []
        for slide in slide_ir:
            slide_number = int(slide["slide_number"])
            component = components.get(slide_number, {})
            script = scripts.get(slide_number, {})
            asset = asset_plan.get(slide_number, {})
            render_slides.append(
                {
                    "slide_number": slide_number,
                    "title": slide.get("title", ""),
                    "layout_style": ctx.args.style,
                    "component": component.get("selected_component", "callout_overlay"),
                    "base_component": component.get("base_component", "preserve_slide"),
                    "subject_framework": (component.get("render_strategy") or {}).get("subject_framework", {}),
                    "semantic_page_type": ((component.get("render_strategy") or {}).get("subject_framework", {}) or {}).get("page_type", ""),
                    "scene_tags": ((component.get("render_strategy") or {}).get("subject_framework", {}) or {}).get("scene_tags", []),
                    "duration": script.get("duration", 5.0),
                    "visual_asset": asset.get("asset_path", ""),
                    "background_decor_asset": asset.get("background_decor_path", ""),
                    "visual_mode": asset.get("mode", "none"),
                    "background_decor_source": asset.get("background_decor_source", {}),
                    "visual_relevance": asset.get("visual_relevance", {}),
                    "subtitle_safe_area": {
                        "x": 100,
                        "y": 850,
                        "w": 1720,
                        "h": 190,
                    },
                    "guards": {
                        "max_body_line_units": 27,
                        "avoid_subtitle_overlap": True,
                        "requires_asset_exists": bool(asset.get("asset_path")),
                    },
                }
            )

        render_plan = {
            "project": ctx.project.name,
            "generated_at": now_iso(),
            "director": "video_director_agent",
            "renderer": "ppt_to_video.py",
            "style": ctx.args.style,
            "slides": render_slides,
        }
        path = write_json(ctx.project / "render_plan.json", render_plan)
        return render_plan, RoleResult(
            self.name,
            "completed",
            outputs={"render_plan": str(path)},
            metrics={"slides": len(render_slides)},
        )


class RendererAgent:
    name = "RendererAgent"

    def run(self, ctx: AgentContext, render_plan: dict[str, Any]) -> RoleResult:
        if not ctx.args.render:
            return RoleResult(self.name, "skipped", notes=["Use --render to generate the MP4."])

        srt_path = ctx.project / "exports" / f"{ctx.project.name}_new.srt"
        write_srt_from_script_plan(ctx.project, srt_path)
        if not srt_path.exists() and ctx.args.ensure_subtitles:
            result = run_command([sys.executable, str(SCRIPT_DIR / "subtitle_generator.py"), str(ctx.project)], SCRIPT_DIR)
            if not result["ok"]:
                raise RuntimeError(result["stderr"] or "subtitle_generator.py failed")

        command = [
            sys.executable,
            str(SCRIPT_DIR / "ppt_to_video.py"),
            str(ctx.project),
            "--style",
            ctx.args.style,
        ]
        if ctx.args.preview_slides:
            command.extend(["--preview-slides", str(ctx.args.preview_slides)])
        if ctx.args.force_render:
            command.append("--force-render")
        result = run_command(command, SCRIPT_DIR, timeout=ctx.args.render_timeout)
        if not result["ok"]:
            raise RuntimeError(result["stderr"] or "ppt_to_video.py failed")
        base_name, final_name = rendered_output_names(ctx.args.style, ctx.args.preview_slides)
        final_video = ctx.project / "exports" / (final_name or f"{ctx.project.name}_final.mp4")
        base_video = ctx.project / "exports" / (base_name or f"{ctx.project.name}_video.mp4")
        return RoleResult(
            self.name,
            "completed",
            outputs={
                "final_video": str(final_video) if final_video.exists() else "",
                "base_video": str(base_video) if base_video.exists() else "",
            },
            metrics={
                "final_video_mb": round(final_video.stat().st_size / 1024 / 1024, 2) if final_video.exists() else 0,
                "renderer_stdout_tail": "\n".join(result["stdout"].splitlines()[-8:]),
            },
        )


class QAAgent:
    name = "QAAgent"

    def run(self, ctx: AgentContext, render_plan: dict[str, Any]) -> RoleResult:
        checks: list[dict[str, Any]] = []
        _, final_name = rendered_output_names(ctx.args.style, ctx.args.preview_slides)
        final_video = ctx.project / "exports" / (final_name or f"{ctx.project.name}_final.mp4")
        checks.append(
            {
                "id": "final_video_exists",
                "status": "pass" if final_video.exists() else ("skip" if not ctx.args.render else "fail"),
                "detail": str(final_video),
            }
        )

        missing_assets = [
            item["slide_number"]
            for item in render_plan.get("slides", [])
            if item.get("visual_mode") not in ("none", "") and item.get("visual_asset") and not Path(item["visual_asset"]).exists()
        ]
        checks.append(
            {
                "id": "visual_assets_exist",
                "status": "pass" if not missing_assets else "fail",
                "detail": {"missing_asset_slides": missing_assets},
            }
        )

        visual_relevance_check = self.check_visual_relevance(render_plan)
        checks.append(visual_relevance_check)
        checks.append(self.check_image_content_audit(render_plan))

        components = [item.get("component") for item in render_plan.get("slides", []) if item.get("component")]
        checks.append(
            {
                "id": "component_variety",
                "status": "pass" if len(set(components)) >= min(3, len(components)) else "warn",
                "detail": {"unique_components": sorted(set(components))},
            }
        )

        duplicate_subtitles = self.find_duplicate_subtitles(ctx.project)
        checks.append(
            {
                "id": "duplicate_subtitles",
                "status": "pass" if not duplicate_subtitles else "warn",
                "detail": duplicate_subtitles[:10],
            }
        )

        timing_check = self.check_subtitle_timing(ctx.project, final_video)
        checks.append(timing_check)
        checks.append(self.check_audio_slide_sync(ctx, render_plan))

        preflight_check = self.check_preflight_risks(ctx.project)
        checks.append(preflight_check)

        contamination_check = self.check_template_contamination(ctx, render_plan)
        checks.append(contamination_check)

        script_slide_count = len(read_json(ctx.project / "video_script_plan.json", {}).get("slides", []))
        render_slide_count = len(render_plan.get("slides", []))
        checks.append(
            {
                "id": "script_render_slide_count",
                "status": "pass" if script_slide_count == render_slide_count else "warn",
                "detail": {"script_slides": script_slide_count, "render_slides": render_slide_count},
            }
        )

        frame_paths: list[str] = []
        if ctx.args.render and final_video.exists() and ctx.args.qa_frames:
            if ctx.args.qa_each_slide or ctx.args.preview_slides:
                frame_paths = self.extract_slide_frames(ctx.project, final_video, render_plan)
            else:
                frame_paths = self.extract_frames(ctx.project, final_video, ctx.args.qa_frames)
            frame_quality = self.inspect_frame_quality(frame_paths)
            checks.append(
                {
                    "id": "qa_frames",
                    "status": "pass" if frame_paths else "warn",
                    "detail": frame_paths,
                }
            )
            checks.append(
                {
                    "id": "qa_frame_quality",
                    "status": "pass" if not frame_quality else "warn",
                    "detail": frame_quality,
                }
            )

        overall = "pass"
        if any(item["status"] == "fail" for item in checks):
            overall = "fail"
        elif any(item["status"] == "warn" for item in checks):
            overall = "warn"

        preview_quality_path = self.write_preview_quality_report(ctx, render_plan, checks)

        path = write_json(ctx.project / "video_qa_report.json", {
            "project": ctx.project.name,
            "generated_at": now_iso(),
            "overall": overall,
            "checks": checks,
        })
        return RoleResult(
            self.name,
            "completed",
            outputs={
                "qa_report": str(path),
                "preview_quality_report": str(preview_quality_path),
                "qa_frames": ", ".join(frame_paths),
            },
            metrics={"overall": overall},
        )

    @staticmethod
    def check_visual_relevance(render_plan: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        for item in render_plan.get("slides", []):
            if not item.get("visual_asset"):
                continue
            audit = item.get("visual_relevance") or {}
            if audit and audit.get("accepted") is False:
                issues.append(
                    {
                        "slide_number": item.get("slide_number"),
                        "score": audit.get("score"),
                        "reason": audit.get("reason"),
                        "query": audit.get("query"),
                    }
                )
            elif item.get("visual_mode") == "search" and not audit:
                issues.append(
                    {
                        "slide_number": item.get("slide_number"),
                        "reason": "searched visual has no relevance audit",
                    }
                )
        return {
            "id": "visual_relevance_audit",
            "status": "pass" if not issues else "warn",
            "detail": issues[:12],
        }

    @staticmethod
    def check_image_content_audit(render_plan: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        for item in render_plan.get("slides", []):
            if not item.get("visual_asset"):
                continue
            audit = item.get("visual_relevance") or {}
            content = audit.get("content") or audit.get("image_content_audit") or {}
            if content and content.get("accepted") is False:
                issues.append(
                    {
                        "slide_number": item.get("slide_number"),
                        "score": content.get("score"),
                        "reason": content.get("reason"),
                        "tags": content.get("tags"),
                    }
                )
            elif item.get("visual_mode") in {"search", "generate"} and not content:
                issues.append(
                    {
                        "slide_number": item.get("slide_number"),
                        "reason": "visual asset has no pixel-level content audit",
                    }
                )
        return {
            "id": "image_content_audit",
            "status": "pass" if not issues else "warn",
            "detail": issues[:12],
        }

    @staticmethod
    def check_template_contamination(ctx: AgentContext, render_plan: dict[str, Any]) -> dict[str, Any]:
        scripts = {
            int(item.get("slide_number", 0)): item
            for item in read_json(ctx.project / "video_script_plan.json", {}).get("slides", [])
            if item.get("slide_number")
        }
        humanities_leaks = ("根号", "平方根", "定义域", "验根", "代入", "公式", "计算", "F = μN", "条件是否满足")
        stem_leaks = ("文本细读", "意象", "母亲形象", "史铁生", "我与地坛", "地坛")
        issues: list[dict[str, Any]] = []
        for item in render_plan.get("slides", []):
            slide_number = int(item.get("slide_number", 0) or 0)
            title = str(item.get("title") or "")
            script = scripts.get(slide_number, {})
            script_lines = [script.get("voiceover", "")]
            for chunk in script.get("subtitle_chunks", []) or []:
                if isinstance(chunk, dict):
                    script_lines.append(str(chunk.get("text") or ""))
            source_lines = source_slide_lines(ctx.project, slide_number)
            framework = framework_for_qa_slide(
                title,
                source_lines + script_lines,
                slide_number,
                item.get("subject_framework") or {},
            )
            family = framework.get("family", "general")
            subject = framework.get("subject", "general")
            text = " ".join([title, *source_lines, *script_lines])
            component = item.get("component", "")
            flags: list[str] = []
            if family == "humanities":
                flags.extend([term for term in humanities_leaks if term and term in text])
                if component in STEM_ONLY_COMPONENTS:
                    flags.append(f"STEM-only component: {component}")
            elif family == "stem":
                flags.extend([term for term in stem_leaks if term and term in text])
                if component in HUMANITIES_ONLY_COMPONENTS and subject not in {"literature", "history"}:
                    flags.append(f"humanities-only component: {component}")
            if flags:
                issues.append(
                    {
                        "slide_number": slide_number,
                        "subject": subject,
                        "family": family,
                        "component": component,
                        "flags": sorted(set(flags))[:8],
                    }
                )
        return {
            "id": "template_contamination",
            "status": "pass" if not issues else "warn",
            "detail": issues[:12],
        }

    @staticmethod
    def write_preview_quality_report(
        ctx: AgentContext,
        render_plan: dict[str, Any],
        checks: list[dict[str, Any]],
    ) -> Path:
        subject_counts: dict[str, int] = {}
        semantic_page_counts: dict[str, int] = {}
        component_counts: dict[str, int] = {}
        for item in render_plan.get("slides", []):
            framework = item.get("subject_framework") or {}
            subject = framework.get("subject") or "general"
            semantic_page_type = framework.get("page_type") or framework.get("scene") or "concept"
            component = item.get("component") or "unknown"
            subject_counts[subject] = subject_counts.get(subject, 0) + 1
            semantic_page_counts[semantic_page_type] = semantic_page_counts.get(semantic_page_type, 0) + 1
            component_counts[component] = component_counts.get(component, 0) + 1
        preflight = read_json(ctx.project / "preflight_report.json", {})
        summary = preflight.get("summary", {}) if isinstance(preflight, dict) else {}
        payload = {
            "project": ctx.project.name,
            "generated_at": now_iso(),
            "style": ctx.args.style,
            "preview_slides": ctx.args.preview_slides,
            "subject_mix": subject_counts,
            "semantic_page_mix": semantic_page_counts,
            "component_mix": component_counts,
            "preflight_summary": summary,
            "preview_first_recommended": bool(summary.get("preview_first_recommended")),
            "image_content_audit": next((c for c in checks if c.get("id") == "image_content_audit"), {}),
            "visual_relevance": next((c for c in checks if c.get("id") == "visual_relevance_audit"), {}),
            "template_contamination": next((c for c in checks if c.get("id") == "template_contamination"), {}),
            "component_variety": next((c for c in checks if c.get("id") == "component_variety"), {}),
            "subtitle_timing": next((c for c in checks if c.get("id") == "subtitle_timing"), {}),
            "audio_slide_sync": next((c for c in checks if c.get("id") == "audio_slide_sync"), {}),
        }
        return write_json(ctx.project / "preview_quality_report.json", payload)

    @staticmethod
    def find_duplicate_subtitles(project: Path) -> list[dict[str, Any]]:
        srt = project / "exports" / f"{project.name}_new.srt"
        chunks = parse_srt(srt)
        duplicates: list[dict[str, Any]] = []
        previous = ""
        for index, chunk in enumerate(chunks, 1):
            text = re.sub(r"\s+", "", chunk.get("text", ""))
            if text and text == previous:
                duplicates.append({"index": index, "text": chunk.get("text", "")})
            previous = text
        return duplicates

    @staticmethod
    def check_subtitle_timing(project: Path, final_video: Path) -> dict[str, Any]:
        if not final_video.exists():
            return {"id": "subtitle_timing", "status": "skip", "detail": "final video missing"}
        duration = ffprobe_duration(final_video) or 0.0
        expected_srt = final_video.with_name(final_video.stem.replace("_final", "_adjusted") + ".srt")
        srt_candidates = [expected_srt] if expected_srt.exists() else []
        srt_candidates += [
            path
            for path in sorted((project / "exports").glob("*_style_adjusted.srt"))
            if path not in srt_candidates
        ]
        srt_candidates += [
            path
            for path in sorted((project / "exports").glob("*_adjusted.srt"))
            if path not in srt_candidates
        ]
        if not srt_candidates:
            return {"id": "subtitle_timing", "status": "warn", "detail": "adjusted SRT missing"}
        chunks = parse_srt(srt_candidates[0])
        if not chunks:
            return {"id": "subtitle_timing", "status": "warn", "detail": "no subtitle chunks"}
        starts = []
        ends = []
        overlaps = []
        previous_end = 0.0
        for index, chunk in enumerate(chunks, 1):
            timecode = chunk.get("timecode", "")
            if "-->" not in timecode:
                continue
            start_raw, end_raw = [part.strip() for part in timecode.split("-->", 1)]
            start = parse_srt_timestamp(start_raw)
            end = parse_srt_timestamp(end_raw)
            starts.append(start)
            ends.append(end)
            if start + 0.05 < previous_end:
                overlaps.append({"index": index, "start": start, "previous_end": previous_end})
            previous_end = max(previous_end, end)
        last_end = max(ends) if ends else 0.0
        drift = round(last_end - duration, 3)
        status = "pass"
        if overlaps or abs(drift) > 1.5:
            status = "warn"
        return {
            "id": "subtitle_timing",
            "status": status,
            "detail": {
                "video_duration": round(duration, 3),
                "last_subtitle_end": round(last_end, 3),
                "drift_seconds": drift,
                "overlaps": overlaps[:10],
                "subtitle_file": str(srt_candidates[0]),
            },
        }

    @staticmethod
    def check_audio_slide_sync(ctx: AgentContext, render_plan: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        _, final_name = rendered_output_names(ctx.args.style, ctx.args.preview_slides)
        qa_path = ctx.project / "exports" / ((final_name or "").replace(".mp4", "_qa.json") or f"{ctx.project.name}_final_qa.json")
        renderer_qa = read_json(qa_path, {}) if qa_path.exists() else {}
        rendered = {
            int(item.get("slide", 0)): float(item.get("duration", 0.0) or 0.0)
            for item in renderer_qa.get("rendered_slides", [])
            if item.get("slide")
        } if isinstance(renderer_qa, dict) else {}
        for item in render_plan.get("slides", []):
            slide_number = int(item.get("slide_number", 0) or 0)
            audio_path = ctx.project / "audio" / f"page_{slide_number:02d}.mp3"
            audio_duration = ffprobe_duration(audio_path) or 0.0
            planned_duration = float(item.get("duration", 0.0) or 0.0)
            rendered_duration = rendered.get(slide_number, 0.0)
            if audio_duration <= 0:
                issues.append({"slide_number": slide_number, "reason": "missing audio duration"})
                continue
            if planned_duration and abs(planned_duration - audio_duration) > 0.35:
                issues.append(
                    {
                        "slide_number": slide_number,
                        "planned_duration": round(planned_duration, 3),
                        "audio_duration": round(audio_duration, 3),
                        "delta": round(planned_duration - audio_duration, 3),
                    }
                )
            if rendered_duration and abs(rendered_duration - audio_duration) > 0.45:
                issues.append(
                    {
                        "slide_number": slide_number,
                        "rendered_duration": round(rendered_duration, 3),
                        "audio_duration": round(audio_duration, 3),
                        "delta": round(rendered_duration - audio_duration, 3),
                    }
                )
        return {
            "id": "audio_slide_sync",
            "status": "pass" if not issues else "warn",
            "detail": issues[:12],
        }

    @staticmethod
    def check_preflight_risks(project: Path) -> dict[str, Any]:
        report = read_json(project / "preflight_report.json", {})
        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        high = int(summary.get("high_risk_slides", 0) or 0)
        medium = int(summary.get("medium_risk_slides", 0) or 0)
        status = "pass" if not high else "warn"
        return {
            "id": "preflight_risks",
            "status": status,
            "detail": {
                "high_risk_slides": high,
                "medium_risk_slides": medium,
                "report": str(project / "preflight_report.json"),
            },
        }

    @staticmethod
    def extract_frames(project: Path, final_video: Path, count: int) -> list[str]:
        duration = ffprobe_duration(final_video) or 0
        if duration <= 0:
            offsets = [2, 10, 25]
        else:
            offsets = [max(1.0, duration * ratio) for ratio in (0.12, 0.36, 0.68, 0.88)][:count]
        frame_paths: list[str] = []
        for idx, offset in enumerate(offsets, 1):
            out = project / "exports" / f"video_agent_qa_frame_{idx:02d}.png"
            result = run_command(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{offset:.2f}",
                    "-i",
                    str(final_video),
                    "-frames:v",
                    "1",
                    str(out),
                ],
                project,
            )
            if result["ok"] and out.exists():
                frame_paths.append(str(out))
        return frame_paths

    @staticmethod
    def extract_slide_frames(project: Path, final_video: Path, render_plan: dict[str, Any]) -> list[str]:
        slides = render_plan.get("slides", [])
        cursor = 0.0
        frame_paths: list[str] = []
        for item in slides:
            slide_number = int(item.get("slide_number", len(frame_paths) + 1))
            duration = float(item.get("duration", 5.0) or 5.0)
            offset = cursor + max(0.5, duration * 0.55)
            out = project / "exports" / f"qa_slide_{slide_number:03d}.png"
            result = run_command(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{offset:.2f}",
                    "-i",
                    str(final_video),
                    "-frames:v",
                    "1",
                    str(out),
                ],
                project,
            )
            if result["ok"] and out.exists():
                frame_paths.append(str(out))
            cursor += duration
        return frame_paths

    @staticmethod
    def inspect_frame_quality(frame_paths: list[str]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        try:
            from PIL import Image, ImageStat
        except Exception:
            return issues
        for path_text in frame_paths:
            path = Path(path_text)
            try:
                image = Image.open(path).convert("L").resize((160, 90))
                stat = ImageStat.Stat(image)
                mean = stat.mean[0]
                stddev = stat.stddev[0]
                pixels = list(image.getdata())
                w, h = image.size
                edge_hits = 0
                comparisons = 0
                for y in range(h):
                    for x in range(w - 1):
                        if abs(pixels[y * w + x] - pixels[y * w + x + 1]) > 34:
                            edge_hits += 1
                        comparisons += 1
                bottom = image.crop((0, int(h * 0.78), w, h))
                bottom_stat = ImageStat.Stat(bottom)
                bottom_stddev = bottom_stat.stddev[0]
                edge_density = edge_hits / max(1, comparisons)
            except Exception:
                continue
            flags: list[str] = []
            if stddev < 4:
                flags.append("near_blank_or_low_contrast")
            if mean > 252:
                flags.append("over_bright")
            if mean < 8:
                flags.append("too_dark")
            if edge_density < 0.006:
                flags.append("too_little_visual_detail")
            if bottom_stddev > 72:
                flags.append("busy_bottom_subtitle_area")
            if flags:
                issues.append(
                    {
                        "path": str(path),
                        "mean": round(mean, 2),
                        "stddev": round(stddev, 2),
                        "edge_density": round(edge_density, 4),
                        "bottom_stddev": round(bottom_stddev, 2),
                        "flags": flags,
                    }
                )
        return issues


class DirectorAgent:
    name = "DirectorAgent"

    def run(self, ctx: AgentContext) -> int:
        ctx.project = ctx.project.resolve()
        if not ctx.project.exists():
            raise FileNotFoundError(ctx.project)

        parser = PPTParserAgent()
        preflight = PreflightAgent()
        script_writer = ScriptWriterAgent()
        audio_agent = NarrationAudioAgent()
        selector = ComponentSelectorAgent()
        asset_curator = AssetCuratorAgent()
        composer = LayoutComposerAgent()
        renderer = RendererAgent()
        qa = QAAgent()

        try:
            slide_ir, result = parser.run(ctx)
            ctx.add_result(result)
            result = preflight.run(ctx, slide_ir)
            ctx.add_result(result)
            if (
                ctx.args.auto_preview_first
                and ctx.args.render
                and not ctx.args.preview_slides
                and not ctx.args.confirm_full_render
            ):
                preflight_payload = read_json(ctx.project / "preflight_report.json", {})
                summary = preflight_payload.get("summary", {}) if isinstance(preflight_payload, dict) else {}
                if summary.get("preview_first_recommended"):
                    ctx.args.preview_slides = int(summary.get("preview_slides") or min(5, len(slide_ir)))
                    ctx.add_result(
                        RoleResult(
                            "PreviewFirstGate",
                            "completed",
                            notes=[
                                "Preflight recommended preview-first; rendering limited preview. "
                                "Use --confirm-full-render for the full video."
                            ],
                            metrics={"preview_slides": ctx.args.preview_slides, "total_slides": len(slide_ir)},
                        )
                    )
            working_slide_ir = limit_slides(slide_ir, ctx.args.preview_slides)
            if ctx.args.preview_slides:
                preview_path = write_json(
                    ctx.project / "preview_manifest.json",
                    {
                        "project": ctx.project.name,
                        "generated_at": now_iso(),
                        "preview_slides": ctx.args.preview_slides,
                        "rendered_slide_numbers": [int(item["slide_number"]) for item in working_slide_ir],
                        "total_slides": len(slide_ir),
                    },
                )
                ctx.add_result(
                    RoleResult(
                        "PreviewLimiter",
                        "completed",
                        outputs={"preview_manifest": str(preview_path)},
                        metrics={"preview_slides": len(working_slide_ir), "total_slides": len(slide_ir)},
                    )
                )
            script_plan, result = script_writer.run(ctx, working_slide_ir)
            ctx.add_result(result)
            result = audio_agent.run(ctx)
            ctx.add_result(result)
            if result.status == "completed" and result.metrics.get("ready_audio_files"):
                script_plan, result = script_writer.run(ctx, working_slide_ir)
                result.notes.append("Refreshed durations after narration audio generation.")
                ctx.add_result(result)
            component_plan, result = selector.run(ctx, working_slide_ir)
            ctx.add_result(result)
            asset_plan, result = asset_curator.run(ctx, working_slide_ir)
            ctx.add_result(result)
            render_plan, result = composer.run(ctx, working_slide_ir, script_plan, component_plan, asset_plan)
            ctx.add_result(result)
            result = renderer.run(ctx, render_plan)
            ctx.add_result(result)
            result = qa.run(ctx, render_plan)
            ctx.add_result(result)
        except Exception as exc:
            ctx.state["status"] = "failed"
            ctx.state["error"] = str(exc)
            write_json(ctx.project / "video_agent_state.json", ctx.state)
            raise

        ctx.state["status"] = "completed"
        ctx.state["completed_at"] = now_iso()
        write_json(ctx.project / "video_agent_state.json", ctx.state)
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_path", type=Path, help="PPT Master project directory")
    parser.add_argument("--render", action="store_true", help="Generate the video after planning")
    parser.add_argument("--style", default="adaptive", help="Renderer style passed to ppt_to_video.py")
    parser.add_argument("--preview-slides", type=int, default=None, help="Only plan/render the first N slides for fast preview")
    parser.add_argument("--auto-preview-first", action="store_true", help="Render a short preview first when preflight finds risky slides")
    parser.add_argument("--confirm-full-render", action="store_true", help="Allow full render even when --auto-preview-first recommends preview")
    parser.add_argument("--force-render", action="store_true", help="Ignore renderer segment cache")
    parser.add_argument("--execute-assets", action="store_true", help="Allow image search/generation during asset curation")
    parser.add_argument("--skip-assets", action="store_true", help="Do not run visual_asset_planner.py when assets are missing")
    parser.add_argument("--asset-limit", type=int, default=None, help="Limit executed asset downloads/generations")
    parser.add_argument("--asset-timeout", type=int, default=600, help="Asset planning timeout in seconds")
    parser.add_argument("--render-timeout", type=int, default=1200, help="Renderer timeout in seconds")
    parser.add_argument("--no-generate-audio", action="store_true", help="Do not generate local TTS audio before rendering")
    parser.add_argument("--force-audio", action="store_true", help="Regenerate page audio even if audio files already exist")
    parser.add_argument("--audio-timeout", type=int, default=900, help="Narration audio generation timeout in seconds")
    parser.add_argument("--tts-provider", choices=["edge", "sapi"], default="edge", help="TTS provider for generated narration")
    parser.add_argument("--tts-voice", default="zh-CN-XiaoxiaoNeural", help="TTS voice name")
    parser.add_argument("--tts-rate", type=int, default=1, help="Windows SAPI voice rate, -10 to 10")
    parser.add_argument("--tts-edge-rate", default="-6%", help='edge-tts rate, e.g. "+0%%" or "-8%%"')
    parser.add_argument("--tts-edge-pitch", default="-3Hz", help='edge-tts pitch, e.g. "+0Hz" or "-3Hz"')
    parser.add_argument("--llm-output", type=Path, default=None, help="Optional JSON from an LLM component selector")
    parser.add_argument("--ensure-subtitles", action="store_true", help="Run subtitle_generator.py if no subtitle file exists")
    parser.add_argument("--qa-each-slide", action="store_true", help="Extract one QA frame per rendered slide")
    parser.add_argument("--qa-frames", type=int, default=3, help="Number of QA frames to extract when rendering")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ctx = AgentContext(args.project_path, args)
    return DirectorAgent().run(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
