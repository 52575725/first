#!/usr/bin/env python3
"""Extract slide text and hierarchy for component selection.

Preferred input is rendered slide images under ``slides/slide_*.png``. The
script runs OCR when a Tesseract backend is available, groups OCR tokens into
visual lines, infers hierarchy from position/size, and writes
``slide_structure.json``. If OCR is unavailable, ``--method auto`` falls back to
the existing notes markdown so downstream recommendation can still run.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


CN_SENTENCE_RE = re.compile(r"([\u3002\uff01\uff1f.!?]+)")
BULLET_RE = re.compile(
    r"^\s*(?:[\u2022\-*]|\d+[\.)]|[A-Za-z][\.)]|[一二三四五六七八九十]+[\u3001.])\s*"
)
DATA_RE = re.compile(
    r"(\d+(?:\.\d+)?%?|\bdata\b|\bchart\b|\bkpi\b|\broi\b|"
    r"\u6570\u636e|\u589e\u957f|\u6bd4\u4f8b|\u8d8b\u52bf|\u6307\u6807)",
    re.IGNORECASE,
)
PROCESS_RE = re.compile(
    r"(\u6b65\u9aa4|\u6d41\u7a0b|\u9636\u6bb5|\u4ece.+\u5230|process|flow|step)",
    re.IGNORECASE,
)
SOURCE_ASSET_RE = re.compile(
    r"(^|[/\\])slide_\d+_image_\d+\.(?:png|jpe?g|webp|gif|wmf|emf|svg)\)?$",
    re.IGNORECASE,
)
COMPARE_RE = re.compile(
    r"(\u5bf9\u6bd4|\u4f18\u52bf|\u52a3\u52bf|\u4e0d\u540c|\bvs\b|compare)",
    re.IGNORECASE,
)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def join_token_text(tokens: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for token in tokens:
        text = token["text"]
        if not parts:
            parts.append(text)
            continue
        previous = parts[-1][-1] if parts[-1] else ""
        if contains_cjk(previous + text[:1]):
            parts.append(text)
        else:
            parts.append(" " + text)
    return clean_text("".join(parts))


def image_size(path: Path) -> Tuple[int, int]:
    """Read PNG/JPEG dimensions without Pillow."""
    with path.open("rb") as f:
        header = f.read(32)

    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        return struct.unpack(">II", header[16:24])

    if header.startswith(b"\xff\xd8"):
        with path.open("rb") as f:
            f.seek(2)
            while True:
                marker = f.read(2)
                if len(marker) < 2:
                    break
                while marker[0] != 0xFF:
                    marker = marker[1:] + f.read(1)
                    if len(marker) < 2:
                        break
                code = marker[1]
                size_bytes = f.read(2)
                if len(size_bytes) < 2:
                    break
                size = struct.unpack(">H", size_bytes)[0]
                if 0xC0 <= code <= 0xC3:
                    data = f.read(5)
                    return struct.unpack(">HH", data[1:5])[::-1]
                f.seek(size - 2, 1)

    return (1920, 1080)


def extract_from_notes(notes_file: Path) -> Dict[str, Any]:
    """Extract structured content from notes markdown."""
    content = notes_file.read_text(encoding="utf-8")

    structure: Dict[str, Any] = {
        "title": "",
        "subtitle": "",
        "bullets": [],
        "paragraphs": [],
        "text_blocks": [],
        "source": "notes",
    }

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("# "):
            structure["title"] = line[2:].strip()
        elif line.startswith("## "):
            structure["subtitle"] = line[3:].strip()
        elif line.startswith(("- ", "* ")):
            structure["bullets"].append(line[2:].strip())
        elif not line.startswith("#"):
            structure["paragraphs"].append(line)

    if not structure["title"] and structure["paragraphs"]:
        structure["title"] = structure["paragraphs"][0][:40]

    return structure


def parse_tesseract_tsv(tsv_text: str, min_conf: float) -> List[Dict[str, Any]]:
    tokens: List[Dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    for row in reader:
        text = clean_text(row.get("text", ""))
        if not text:
            continue
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            conf = -1.0
        if conf >= 0 and conf < min_conf:
            continue
        try:
            x = int(float(row["left"]))
            y = int(float(row["top"]))
            w = int(float(row["width"]))
            h = int(float(row["height"]))
        except (KeyError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        tokens.append({"text": text, "conf": conf, "x": x, "y": y, "w": w, "h": h})
    return tokens


def ocr_with_pytesseract(image_path: Path, lang: str, min_conf: float) -> Optional[List[Dict[str, Any]]]:
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return None

    tsv = pytesseract.image_to_data(str(image_path), lang=lang)
    return parse_tesseract_tsv(tsv, min_conf)


def find_tesseract_exe() -> Optional[str]:
    """Find Tesseract even when installer did not add it to PATH."""
    path_hit = shutil.which("tesseract")
    if path_hit:
        return path_hit

    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def find_tessdata_dir() -> Optional[Path]:
    """Prefer project-local tessdata, then the standard Windows install dir."""
    script_root = Path(__file__).resolve().parents[3]
    candidates = [
        script_root / "tools" / "tessdata",
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tessdata"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def ocr_with_tesseract_cli(image_path: Path, lang: str, min_conf: float) -> Optional[List[Dict[str, Any]]]:
    tesseract = find_tesseract_exe()
    if not tesseract:
        return None

    with tempfile.TemporaryDirectory(prefix="ppt_master_ocr_") as temp_dir:
        output_base = Path(temp_dir) / "ocr"
        cmd = [
            tesseract,
            str(image_path),
            str(output_base),
            "-l",
            lang,
            "--psm",
            "6",
            "-c",
            "tessedit_create_tsv=1",
        ]
        tessdata_dir = find_tessdata_dir()
        if tessdata_dir:
            cmd.extend(["--tessdata-dir", str(tessdata_dir)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "tesseract failed")

        tsv_path = output_base.with_suffix(".tsv")
        if not tsv_path.exists():
            raise RuntimeError("tesseract did not produce TSV output")
        return parse_tesseract_tsv(tsv_path.read_text(encoding="utf-8", errors="replace"), min_conf)


def run_ocr(image_path: Path, lang: str, min_conf: float) -> Optional[List[Dict[str, Any]]]:
    tokens = ocr_with_pytesseract(image_path, lang, min_conf)
    if tokens is not None:
        return tokens
    return ocr_with_tesseract_cli(image_path, lang, min_conf)


def group_tokens_into_lines(tokens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not tokens:
        return []

    heights = sorted(token["h"] for token in tokens)
    median_h = heights[len(heights) // 2]
    y_threshold = max(8, median_h * 0.65)
    lines: List[List[Dict[str, Any]]] = []

    for token in sorted(tokens, key=lambda item: (item["y"] + item["h"] / 2, item["x"])):
        center_y = token["y"] + token["h"] / 2
        target: Optional[List[Dict[str, Any]]] = None
        for line in lines:
            line_center = sum(t["y"] + t["h"] / 2 for t in line) / len(line)
            if abs(center_y - line_center) <= y_threshold:
                target = line
                break
        if target is None:
            lines.append([token])
        else:
            target.append(token)

    grouped: List[Dict[str, Any]] = []
    for line_tokens in lines:
        ordered = sorted(line_tokens, key=lambda item: item["x"])
        x1 = min(token["x"] for token in ordered)
        y1 = min(token["y"] for token in ordered)
        x2 = max(token["x"] + token["w"] for token in ordered)
        y2 = max(token["y"] + token["h"] for token in ordered)
        grouped.append(
            {
                "text": join_token_text(ordered),
                "x": x1,
                "y": y1,
                "w": x2 - x1,
                "h": y2 - y1,
                "conf": round(sum(token["conf"] for token in ordered) / len(ordered), 1),
            }
        )

    return [line for line in sorted(grouped, key=lambda item: (item["y"], item["x"])) if line["text"]]


def cluster_columns(lines: List[Dict[str, Any]], width: int) -> int:
    body_lines = [line for line in lines if line.get("role") not in {"title", "subtitle"}]
    if len(body_lines) < 3:
        return 1

    centers = sorted(line["x"] + line["w"] / 2 for line in body_lines)
    threshold = max(120, width * 0.12)
    clusters: List[List[float]] = []
    for center in centers:
        if not clusters or abs(center - (sum(clusters[-1]) / len(clusters[-1]))) > threshold:
            clusters.append([center])
        else:
            clusters[-1].append(center)
    return max(1, min(4, len(clusters)))


def assign_roles(lines: List[Dict[str, Any]], width: int, height: int) -> List[Dict[str, Any]]:
    if not lines:
        return []

    heights = sorted(line["h"] for line in lines)
    p75_height = heights[min(len(heights) - 1, math.floor(len(heights) * 0.75))]
    top_limit = height * 0.32
    title_assigned = False

    for index, line in enumerate(lines):
        text = line["text"]
        role = "body"
        if not title_assigned and line["y"] < top_limit and (line["h"] >= p75_height or index == 0):
            role = "title"
            title_assigned = True
        elif title_assigned and line["y"] < top_limit and line["h"] >= p75_height * 0.85:
            role = "subtitle"
        elif BULLET_RE.search(text):
            role = "bullet"
        elif line["w"] < width * 0.45 and line["h"] >= p75_height * 0.9:
            role = "label"

        line["role"] = role
        line["bbox_norm"] = {
            "x": round(line["x"] / width, 4),
            "y": round(line["y"] / height, 4),
            "w": round(line["w"] / width, 4),
            "h": round(line["h"] / height, 4),
        }
    return lines


def infer_page_type(text: str, lines: List[Dict[str, Any]], columns: int) -> str:
    lowered = text.lower()
    if not text.strip():
        return "image_only"
    if PROCESS_RE.search(text):
        return "process"
    if COMPARE_RE.search(text):
        return "comparison"
    if DATA_RE.search(text) and len(re.findall(r"\d", text)) >= 4:
        return "data"
    if columns >= 3:
        return "multi_card"
    if len(lines) <= 3:
        return "section"
    if sum(1 for line in lines if line.get("role") == "bullet") >= 3:
        return "bullet_list"
    if "timeline" in lowered or "\u65f6\u95f4" in text:
        return "timeline"
    return "content"


def build_structure_from_ocr(
    image_path: Path,
    slide_number: int,
    lang: str,
    min_conf: float,
) -> Dict[str, Any]:
    width, height = image_size(image_path)
    tokens = run_ocr(image_path, lang, min_conf)
    if tokens is None:
        raise RuntimeError("OCR backend not available. Install Tesseract or pytesseract.")

    lines = group_tokens_into_lines(tokens)
    lines = assign_roles(lines, width, height)
    columns = cluster_columns(lines, width)

    title_lines = [line["text"] for line in lines if line["role"] == "title"]
    subtitle_lines = [line["text"] for line in lines if line["role"] == "subtitle"]
    bullet_lines = [BULLET_RE.sub("", line["text"]).strip() for line in lines if line["role"] == "bullet"]
    paragraph_lines = [
        line["text"]
        for line in lines
        if line["role"] in {"body", "label"} and line["text"] not in title_lines + subtitle_lines
    ]

    full_text = "\n".join(line["text"] for line in lines)
    return {
        "slide_number": slide_number,
        "filename": image_path.stem,
        "source": "ocr",
        "ocr_backend": "pytesseract" if "pytesseract" in sys.modules else "tesseract_cli",
        "canvas": {"width": width, "height": height},
        "title": title_lines[0] if title_lines else "",
        "subtitle": subtitle_lines[0] if subtitle_lines else "",
        "bullets": bullet_lines,
        "paragraphs": paragraph_lines,
        "text_blocks": lines,
        "layout": {
            "columns": columns,
            "line_count": len(lines),
            "text_density": round(min(1.0, len(full_text) / 900 + len(lines) / 35), 3),
            "page_type": infer_page_type(full_text, lines, columns),
        },
        "signals": {
            "has_data": bool(DATA_RE.search(full_text)),
            "has_process": bool(PROCESS_RE.search(full_text)),
            "has_comparison": bool(COMPARE_RE.search(full_text)),
        },
    }


def enrich_notes_structure(structure: Dict[str, Any], slide_number: int, filename: str) -> Dict[str, Any]:
    all_text = "\n".join(
        [structure.get("title", ""), structure.get("subtitle", "")]
        + structure.get("bullets", [])
        + structure.get("paragraphs", [])
    )
    line_count = len(structure.get("bullets", [])) + len(structure.get("paragraphs", []))
    structure.update(
        {
            "slide_number": slide_number,
            "filename": filename,
            "layout": {
                "columns": 1,
                "line_count": line_count,
                "text_density": round(min(1.0, len(all_text) / 900 + line_count / 35), 3),
                "page_type": infer_page_type(all_text, [], 1),
            },
            "signals": {
                "has_data": bool(DATA_RE.search(all_text)),
                "has_process": bool(PROCESS_RE.search(all_text)),
                "has_comparison": bool(COMPARE_RE.search(all_text)),
            },
        }
    )
    return structure


def slide_images(project_path: Path) -> List[Path]:
    slides_dir = project_path / "slides"
    return sorted(slides_dir.glob("slide_*.png"))


def note_files(project_path: Path) -> List[Path]:
    notes_dir = project_path / "notes"
    if not notes_dir.exists():
        return []
    return sorted(file for file in notes_dir.glob("*.md") if file.name != "total.md")


def source_markdown_files(project_path: Path) -> List[Path]:
    sources_dir = project_path / "sources"
    if not sources_dir.exists():
        return []
    return sorted(sources_dir.glob("*.md"))


def clean_source_markdown_line(line: str) -> str:
    line = str(line or "").strip()
    if not line:
        return ""
    if line.startswith("![") and "](" in line:
        return ""
    line = re.sub(r"!\[[^\]]*\]\(.*\)", "", line).strip()
    if SOURCE_ASSET_RE.search(line):
        return ""
    return line


def structure_from_source_lines(lines: List[str]) -> Dict[str, Any]:
    cleaned: List[str] = []
    for line in lines:
        line = clean_source_markdown_line(line)
        if not line or line.startswith("### Speaker Notes"):
            continue
        cleaned.append(line)

    title = ""
    subtitle = ""
    bullets: List[str] = []
    paragraphs: List[str] = []

    for line in cleaned:
        plain = line.lstrip("#").strip()
        bullet_match = re.match(r"^[-*]\s+(.+)", line)
        if not title:
            title = bullet_match.group(1).strip() if bullet_match else plain
            continue
        if bullet_match:
            bullets.append(bullet_match.group(1).strip())
            continue
        if not subtitle and len(plain) <= 48:
            subtitle = plain
        else:
            paragraphs.append(plain)

    if not title and paragraphs:
        title = paragraphs.pop(0)

    return {
        "title": title,
        "subtitle": subtitle,
        "bullets": bullets,
        "paragraphs": paragraphs,
        "text_blocks": [],
        "source": "source_markdown",
    }


def split_source_markdown_slides(markdown_path: Path) -> List[Dict[str, Any]]:
    slides: List[Dict[str, Any]] = []
    current_number: Optional[int] = None
    current_lines: List[str] = []

    def flush() -> None:
        nonlocal current_number, current_lines
        if current_number is None:
            return
        structure = structure_from_source_lines(current_lines)
        slides.append(enrich_notes_structure(structure, current_number, f"slide_{current_number:02d}"))
        current_number = None
        current_lines = []

    for raw in markdown_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        match = re.match(r"^##\s+Slide\s+(\d+)", line, re.IGNORECASE)
        if match:
            flush()
            current_number = int(match.group(1))
            current_lines = []
            continue
        if current_number is None:
            continue
        if line.startswith("## "):
            flush()
            continue
        if not line or line.startswith(("Source:", "Total slides:")):
            continue
        current_lines.append(line)
    flush()
    return slides


def analyze_project(
    project_path: Path,
    *,
    method: str = "auto",
    ocr_lang: str = "chi_sim+eng",
    min_conf: float = 35,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Extract structure from all slides."""
    warnings: List[str] = []
    slides: List[Dict[str, Any]] = []

    images = slide_images(project_path)
    if method in {"auto", "ocr"} and images:
        try:
            for index, image_path in enumerate(images, 1):
                slides.append(build_structure_from_ocr(image_path, index, ocr_lang, min_conf))
            return slides, {"method": "ocr", "warnings": warnings}
        except RuntimeError as exc:
            if method == "ocr":
                raise
            warnings.append(str(exc))

    notes = note_files(project_path)
    for index, note_file in enumerate(notes, 1):
        slides.append(enrich_notes_structure(extract_from_notes(note_file), index, note_file.stem))
    if slides:
        return slides, {"method": "notes", "warnings": warnings}

    for markdown_path in source_markdown_files(project_path):
        slides.extend(split_source_markdown_slides(markdown_path))

    return slides, {"method": "source_markdown", "warnings": warnings}


def build_component_input(project_path: Path, slides: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project": project_path.name,
        "extraction": meta,
        "slides": slides,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_path", type=Path, help="PPT Master project directory")
    parser.add_argument(
        "--method",
        choices=["auto", "ocr", "notes"],
        default="auto",
        help="Extraction method. auto tries OCR first, then notes fallback.",
    )
    parser.add_argument("--ocr-lang", default="chi_sim+eng", help="Tesseract language list")
    parser.add_argument("--min-conf", type=float, default=35, help="Minimum OCR confidence")
    parser.add_argument("--output", type=Path, help="Output slide structure JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = args.project_path
    output = args.output or (project / "slide_structure.json")

    slides, meta = analyze_project(
        project,
        method=args.method,
        ocr_lang=args.ocr_lang,
        min_conf=args.min_conf,
    )
    if not slides:
        print("error: no slides found to analyze", file=sys.stderr)
        return 1

    payload = build_component_input(project, slides, meta)
    with output.open("w", encoding="utf-8") as f:
        json.dump(slides, f, ensure_ascii=False, indent=2)

    component_input = project / "component_selection_input.json"
    with component_input.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Extracted {len(slides)} slides via {meta['method']}")
    for warning in meta.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Structure saved to: {output}")
    print(f"Component input saved to: {component_input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
