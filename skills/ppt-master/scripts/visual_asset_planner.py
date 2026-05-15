#!/usr/bin/env python3
"""Plan, extract, search, and generate visual assets for a PPT Master project.

The planner answers four questions per slide/paragraph:
1. Does this content need an image?
2. Can an existing image from the source PPT be reused?
3. If not, should the image be searched from licensed web sources or generated?
4. Is the selected image actually usable (readable, large enough, sane aspect)?

Default mode is safe: it extracts and validates existing assets, then writes a
dry-run plan. Use ``--execute`` to run web search / image generation commands.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
VENDOR_DIR = REPO_ROOT / "tools" / "python_libs"
if VENDOR_DIR.exists() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
PPT_EXTENSIONS = {".pptx", ".pptm", ".ppsx", ".ppsm", ".potx", ".potm"}

try:
    from PIL import Image, ImageStat  # type: ignore
except ImportError:  # pragma: no cover - runtime fallback
    Image = None
    ImageStat = None


CONCRETE_KEYWORDS = (
    "旧衣", "衣物", "纺织", "面料", "服装", "垃圾", "填埋", "焚烧", "回收箱",
    "分拣", "清洗", "消毒", "纤维", "工厂", "设备", "门店", "城市", "社区",
    "团队", "消费者", "品牌", "产品", "材料", "fabric", "textile", "recycling",
    "factory", "team", "product", "store", "city", "landfill",
)

ABSTRACT_KEYWORDS = (
    "战略", "模式", "价值", "愿景", "使命", "算法", "平台", "体系", "模型",
    "利润", "收入", "市场规模", "政策", "优势", "壁垒", "规划", "roadmap",
    "strategy", "model", "policy", "revenue", "profit", "advantage",
)

NO_IMAGE_KEYWORDS = (
    "目录", "agenda", "数据", "指标", "测算", "KPI", "ROI", "财务", "表格",
)

SEARCH_QUERY_RULES = (
    (("填埋", "垃圾", "废料"), "textile waste landfill"),
    (("焚烧", "污染"), "clothing waste pollution"),
    (("智能回收", "回收箱"), "clothing recycling bin"),
    (("AI", "视觉", "分拣"), "textile sorting machine"),
    (("分拣",), "textile sorting facility"),
    (("清洗", "消毒"), "textile washing factory"),
    (("再生纤维", "纤维"), "recycled textile fiber"),
    (("环保面料", "面料"), "recycled fabric textile"),
    (("市场", "消费"), "sustainable fashion store"),
    (("品牌",), "sustainable fashion retail"),
    (("团队", "CEO", "CTO", "COO"), "business team meeting"),
    (("全国", "城市", "布局"), "China city skyline"),
    (("旧衣", "衣物", "纺织"), "textile recycling"),
)


@dataclass
class ImageQuality:
    usable: bool
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0
    reason: str = ""


@dataclass
class ExtractedAsset:
    filename: str
    path: str
    source_type: str
    source_file: str
    slide_number: int | None
    checksum: str
    quality: ImageQuality


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_token(value: str, fallback: str = "asset") -> str:
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", value.strip())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe[:80] or fallback


def is_no_content_placeholder(text: str) -> bool:
    cleaned = clean_line(text).strip("_ ").lower().rstrip(".")
    return "no extractable text content" in cleaned


def validate_image(path: Path, min_width: int, min_height: int) -> ImageQuality:
    if Image is None:
        probed = probe_image_with_ffprobe(path)
        if probed:
            width, height = probed
            aspect = width / height if height else 0.0
            if width < min_width or height < min_height:
                return ImageQuality(False, width, height, aspect, "too small")
            if aspect < 0.25 or aspect > 4.0:
                return ImageQuality(False, width, height, aspect, "extreme aspect ratio")
            return ImageQuality(True, width, height, aspect, "ok via ffprobe")
        if path.stat().st_size < 80_000:
            return ImageQuality(False, reason="Pillow unavailable and file too small to trust")
        return ImageQuality(True, reason="Pillow unavailable; accepted by file size only")
    try:
        with Image.open(path) as img:
            width, height = img.size
            aspect = width / height if height else 0.0
            if width < min_width or height < min_height:
                return ImageQuality(False, width, height, aspect, "too small")
            if aspect < 0.25 or aspect > 4.0:
                return ImageQuality(False, width, height, aspect, "extreme aspect ratio")
            if ImageStat is not None:
                stat = ImageStat.Stat(img.convert("L").resize((64, 64)))
                if stat.var and stat.var[0] < 3:
                    return ImageQuality(False, width, height, aspect, "near-blank image")
            return ImageQuality(True, width, height, aspect, "ok")
    except Exception as exc:
        return ImageQuality(False, reason=f"unreadable: {exc}")


def probe_image_with_ffprobe(path: Path) -> tuple[int, int] | None:
    """Return image dimensions using ffprobe when Pillow is unavailable."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        streams = json.loads(result.stdout).get("streams") or []
        if not streams:
            return None
        width = int(streams[0].get("width") or 0)
        height = int(streams[0].get("height") or 0)
        if width and height:
            return width, height
    except Exception:
        return None
    return None


def copy_unique_image(
    src: Path,
    dest_dir: Path,
    *,
    filename_hint: str,
    seen: dict[str, ExtractedAsset],
    source_type: str,
    source_file: Path,
    slide_number: int | None,
    min_width: int,
    min_height: int,
) -> ExtractedAsset | None:
    if not src.exists() or src.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    checksum = file_checksum(src)
    if checksum in seen:
        return None

    quality = validate_image(src, min_width, min_height)
    suffix = src.suffix.lower()
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = sanitize_token(filename_hint)
    dst = dest_dir / f"{base}{suffix}"
    if dst.exists() and file_checksum(dst) == checksum:
        asset = ExtractedAsset(
            filename=dst.name,
            path=str(dst),
            source_type=source_type,
            source_file=str(source_file),
            slide_number=slide_number,
            checksum=checksum,
            quality=quality,
        )
        seen[checksum] = asset
        return asset
    counter = 2
    while dst.exists():
        dst = dest_dir / f"{base}_{counter}{suffix}"
        counter += 1

    shutil.copy2(src, dst)
    asset = ExtractedAsset(
        filename=dst.name,
        path=str(dst),
        source_type=source_type,
        source_file=str(source_file),
        slide_number=slide_number,
        checksum=checksum,
        quality=quality,
    )
    seen[checksum] = asset
    return asset


def iter_markdown_image_refs(markdown_path: Path) -> Iterable[tuple[int | None, Path]]:
    slide_number: int | None = None
    image_re = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    for line in markdown_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"^##\s+Slide\s+(\d+)", line.strip(), re.I)
        if match:
            slide_number = int(match.group(1))
            continue
        for image_match in image_re.finditer(line):
            rel = image_match.group(1).strip()
            if rel.startswith(("http://", "https://")):
                continue
            yield slide_number, (markdown_path.parent / rel).resolve()


def extract_images_from_markdown_sources(
    project: Path,
    dest_dir: Path,
    seen: dict[str, ExtractedAsset],
    min_width: int,
    min_height: int,
) -> list[ExtractedAsset]:
    assets: list[ExtractedAsset] = []
    for md_path in sorted((project / "sources").glob("*.md")):
        for slide_number, image_path in iter_markdown_image_refs(md_path):
            hint = f"src_slide_{slide_number or 0:02d}_{image_path.stem}"
            asset = copy_unique_image(
                image_path,
                dest_dir,
                filename_hint=hint,
                seen=seen,
                source_type="source_markdown_image",
                source_file=md_path,
                slide_number=slide_number,
                min_width=min_width,
                min_height=min_height,
            )
            if asset:
                assets.append(asset)
    return assets


def extract_pptx_with_python_pptx(
    pptx_path: Path,
    dest_dir: Path,
    seen: dict[str, ExtractedAsset],
    min_width: int,
    min_height: int,
) -> list[ExtractedAsset]:
    try:
        from pptx import Presentation  # type: ignore
        from pptx.enum.shapes import MSO_SHAPE_TYPE  # type: ignore
    except Exception:
        return []

    assets: list[ExtractedAsset] = []
    prs = Presentation(str(pptx_path))

    def walk_shapes(shapes: Any) -> Iterable[Any]:
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from walk_shapes(shape.shapes)
            else:
                yield shape

    for slide_idx, slide in enumerate(prs.slides, 1):
        image_idx = 0
        for shape in walk_shapes(slide.shapes):
            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                continue
            try:
                image = shape.image
                image_idx += 1
                suffix = f".{(image.ext or 'png').lower()}"
                tmp = dest_dir / f"__tmp_slide_{slide_idx:02d}_{image_idx:02d}{suffix}"
                tmp.write_bytes(image.blob)
                asset = copy_unique_image(
                    tmp,
                    dest_dir,
                    filename_hint=f"ppt_slide_{slide_idx:02d}_image_{image_idx:02d}",
                    seen=seen,
                    source_type="source_pptx_picture",
                    source_file=pptx_path,
                    slide_number=slide_idx,
                    min_width=min_width,
                    min_height=min_height,
                )
                tmp.unlink(missing_ok=True)
                if asset:
                    assets.append(asset)
            except Exception:
                continue
    return assets


def extract_pptx_media_zip(
    pptx_path: Path,
    dest_dir: Path,
    seen: dict[str, ExtractedAsset],
    min_width: int,
    min_height: int,
) -> list[ExtractedAsset]:
    assets: list[ExtractedAsset] = []
    try:
        with zipfile.ZipFile(pptx_path) as zf:
            names = [name for name in zf.namelist() if name.startswith("ppt/media/")]
            for idx, name in enumerate(names, 1):
                suffix = Path(name).suffix.lower()
                if suffix not in IMAGE_EXTENSIONS:
                    continue
                tmp = dest_dir / f"__tmp_media_{idx:03d}{suffix}"
                tmp.write_bytes(zf.read(name))
                asset = copy_unique_image(
                    tmp,
                    dest_dir,
                    filename_hint=f"ppt_media_{idx:03d}",
                    seen=seen,
                    source_type="source_pptx_media",
                    source_file=pptx_path,
                    slide_number=None,
                    min_width=min_width,
                    min_height=min_height,
                )
                tmp.unlink(missing_ok=True)
                if asset:
                    assets.append(asset)
    except zipfile.BadZipFile:
        return []
    return assets


def extract_source_images(
    project: Path,
    min_width: int,
    min_height: int,
) -> list[ExtractedAsset]:
    dest_dir = project / "images" / "extracted"
    dest_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, ExtractedAsset] = {}
    assets: list[ExtractedAsset] = []
    assets.extend(extract_images_from_markdown_sources(project, dest_dir, seen, min_width, min_height))

    for pptx_path in sorted((project / "sources").iterdir() if (project / "sources").exists() else []):
        if pptx_path.suffix.lower() not in PPT_EXTENSIONS:
            continue
        extracted = extract_pptx_with_python_pptx(pptx_path, dest_dir, seen, min_width, min_height)
        if not extracted:
            extracted = extract_pptx_media_zip(pptx_path, dest_dir, seen, min_width, min_height)
        assets.extend(extracted)
    return assets


def clean_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line.strip())
    line = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", line).strip()
    return line


def load_slide_texts(project: Path) -> list[dict[str, Any]]:
    source_slides: dict[int, list[str]] = {}
    for md_path in sorted((project / "sources").glob("*.md")):
        current: int | None = None
        for raw in md_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = re.match(r"^##\s+Slide\s+(\d+)", raw.strip(), re.I)
            if match:
                current = int(match.group(1))
                source_slides.setdefault(current, [])
                continue
            if current is None:
                continue
            if raw.startswith("## ") or raw.startswith("### Speaker Notes"):
                current = None
                continue
            line = clean_line(raw)
            if not line or is_no_content_placeholder(line) or line.startswith(("#", "-", "Source:", "Total slides:")):
                continue
            source_slides[current].append(line)

    if source_slides:
        return [
            {
                "slide_number": slide_num,
                "title": lines[0] if lines else f"Slide {slide_num}",
                "segments": lines[1:] or lines[:1],
            }
            for slide_num, lines in sorted(source_slides.items())
        ]

    structure_file = project / "slide_structure.json"
    if not structure_file.exists():
        return []
    data = json.loads(structure_file.read_text(encoding="utf-8"))
    slides = data.get("slides", data) if isinstance(data, dict) else data
    result = []
    for slide in slides:
        title = slide.get("title") or f"Slide {slide.get('slide_number')}"
        segments = []
        segments.extend(slide.get("bullets") or [])
        segments.extend(slide.get("paragraphs") or [])
        result.append(
            {
                "slide_number": int(slide.get("slide_number") or len(result) + 1),
                "title": title,
                "segments": [clean_line(s) for s in segments if clean_line(s)],
            }
        )
    return result


def score_segment_need(text: str) -> float:
    if not text:
        return 0.0
    score = 0.0
    if any(key in text for key in CONCRETE_KEYWORDS):
        score += 0.45
    if any(key in text for key in ABSTRACT_KEYWORDS):
        score += 0.18
    if any(key in text for key in NO_IMAGE_KEYWORDS):
        score -= 0.28
    if re.search(r"\d", text) and len(text) < 50:
        score -= 0.15
    if len(text) >= 28:
        score += 0.12
    return max(0.0, min(1.0, score))


def choose_mode(text: str, slide_assets: list[ExtractedAsset]) -> str:
    usable_existing = [asset for asset in slide_assets if asset.quality.usable]
    if usable_existing:
        return "existing"
    if any(key in text for key in CONCRETE_KEYWORDS):
        return "search"
    if any(key in text for key in ABSTRACT_KEYWORDS):
        return "generate"
    return "none"


def build_search_query(text: str) -> str:
    for keys, query in SEARCH_QUERY_RULES:
        if any(key in text for key in keys):
            return query
    latin_words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", text)
    if latin_words:
        return " ".join(latin_words[:4])
    if any(key in text for key in ("环保", "可持续", "绿色")):
        return "sustainable fashion"
    if any(key in text for key in ("团队", "成员")):
        return "business team meeting"
    return "textile recycling"


def build_generation_prompt(text: str, title: str) -> str:
    compact = re.sub(r"\s+", " ", f"{title}. {text}").strip()
    return (
        "Create a clean, realistic editorial image for a business presentation. "
        "No text, no logos, no watermarks. Subject: "
        f"{compact[:260]}"
    )


def plan_visual_assets(slides: list[dict[str, Any]], assets: list[ExtractedAsset]) -> list[dict[str, Any]]:
    by_slide: dict[int, list[ExtractedAsset]] = {}
    for asset in assets:
        if asset.slide_number is not None:
            by_slide.setdefault(asset.slide_number, []).append(asset)

    decisions: list[dict[str, Any]] = []
    for slide in slides:
        slide_num = int(slide["slide_number"])
        title = slide.get("title", "")
        segments = slide.get("segments") or [title]
        slide_assets = by_slide.get(slide_num, [])

        segment_decisions = []
        for idx, segment in enumerate(segments[:8], 1):
            score = score_segment_need(f"{title} {segment}")
            mode = choose_mode(f"{title} {segment}", slide_assets) if score >= 0.34 else "none"
            segment_decisions.append(
                {
                    "segment_index": idx,
                    "text": segment,
                    "need_score": round(score, 2),
                    "mode": mode,
                    "query": build_search_query(f"{title} {segment}") if mode == "search" else "",
                    "prompt": build_generation_prompt(segment, title) if mode == "generate" else "",
                }
            )

        actionable = next((item for item in segment_decisions if item["mode"] != "none"), None)
        if actionable is None and any(asset.quality.usable for asset in slide_assets) and segment_decisions:
            segment_decisions[0]["mode"] = "existing"
            actionable = segment_decisions[0]
        decision = {
            "slide_number": slide_num,
            "title": title,
            "recommended_mode": actionable["mode"] if actionable else "none",
            "reason": "reuse usable source PPT image" if actionable and actionable["mode"] == "existing"
            else "concrete real-world subject, use licensed search" if actionable and actionable["mode"] == "search"
            else "abstract/custom concept, use generation" if actionable and actionable["mode"] == "generate"
            else "text/data/structure can be rendered without an image",
            "existing_assets": [asdict(asset) for asset in slide_assets],
            "segments": segment_decisions,
        }
        decisions.append(decision)
    return decisions


def run_command(args: list[str], cwd: Path) -> bool:
    env = None
    if VENDOR_DIR.exists():
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(VENDOR_DIR) + ((";" + existing) if existing else "")
    try:
        subprocess.run(args, cwd=str(cwd), check=True, env=env)
        return True
    except subprocess.CalledProcessError:
        return False


def execute_decisions(
    project: Path,
    decisions: list[dict[str, Any]],
    *,
    allow_search: bool,
    allow_generate: bool,
    limit: int | None,
) -> list[dict[str, Any]]:
    output_dir = project / "images" / "visual_assets"
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = []
    count = 0

    for decision in decisions:
        if limit is not None and count >= limit:
            break
        mode = decision["recommended_mode"]
        if mode == "none":
            continue

        slide_num = int(decision["slide_number"])
        filename = f"slide_{slide_num:02d}_visual.jpg"
        selected = {
            "slide_number": slide_num,
            "mode": mode,
            "status": "planned",
            "filename": filename,
        }

        if mode == "existing":
            usable = [
                asset for asset in decision.get("existing_assets", [])
                if asset.get("quality", {}).get("usable")
            ]
            if usable:
                src = Path(usable[0]["path"])
                suffix = src.suffix.lower() or ".jpg"
                dst = output_dir / f"slide_{slide_num:02d}_existing{suffix}"
                shutil.copy2(src, dst)
                selected.update({"status": "ready", "filename": dst.name, "path": str(dst)})
                completed.append(selected)
                count += 1
            continue

        segment = next((item for item in decision["segments"] if item["mode"] == mode), None)
        if not segment:
            continue

        if mode == "search" and allow_search:
            ok = run_command(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "image_search.py"),
                    segment["query"],
                    "--filename",
                    filename,
                    "--slide",
                    f"{slide_num:02d}",
                    "--purpose",
                    "visual_asset",
                    "--orientation",
                    "landscape",
                    "-o",
                    str(output_dir),
                ],
                cwd=SCRIPT_DIR.parent.parent,
            )
            selected.update({"status": "ready" if ok else "failed", "query": segment["query"]})
            if ok:
                selected["path"] = str(output_dir / filename)
            completed.append(selected)
            count += 1
            continue

        if mode == "generate" and allow_generate:
            stem = f"slide_{slide_num:02d}_generated"
            ok = run_command(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "image_gen.py"),
                    segment["prompt"],
                    "--aspect_ratio",
                    "16:9",
                    "--image_size",
                    "1K",
                    "--filename",
                    stem,
                    "-o",
                    str(output_dir),
                ],
                cwd=SCRIPT_DIR.parent.parent,
            )
            generated = next(output_dir.glob(f"{stem}.*"), None)
            selected.update({"status": "ready" if ok and generated else "failed", "prompt": segment["prompt"]})
            if generated:
                selected.update({"filename": generated.name, "path": str(generated)})
            completed.append(selected)
            count += 1

    return completed


def write_manifest(project: Path, payload: dict[str, Any]) -> Path:
    path = project / "images" / "visual_asset_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_path", type=Path, help="PPT Master project directory")
    parser.add_argument("--execute", action="store_true", help="Run search/generation for planned assets")
    parser.add_argument("--no-search", action="store_true", help="Do not call image_search.py in execute mode")
    parser.add_argument("--no-generate", action="store_true", help="Do not call image_gen.py in execute mode")
    parser.add_argument("--limit", type=int, default=None, help="Limit executed assets")
    parser.add_argument("--min-width", type=int, default=900, help="Minimum usable source image width")
    parser.add_argument("--min-height", type=int, default=500, help="Minimum usable source image height")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = args.project_path
    if not project.exists():
        raise FileNotFoundError(project)

    assets = extract_source_images(project, args.min_width, args.min_height)
    slides = load_slide_texts(project)
    decisions = plan_visual_assets(slides, assets)
    executed = []
    if args.execute:
        executed = execute_decisions(
            project,
            decisions,
            allow_search=not args.no_search,
            allow_generate=not args.no_generate,
            limit=args.limit,
        )

    payload = {
        "project": project.name,
        "generated_at": now_iso(),
        "mode": "execute" if args.execute else "dry_run",
        "quality_threshold": {
            "min_width": args.min_width,
            "min_height": args.min_height,
        },
        "extracted_assets": [asdict(asset) for asset in assets],
        "decisions": decisions,
        "executed_assets": executed,
    }
    manifest_path = write_manifest(project, payload)

    print(f"Extracted source images: {len(assets)}")
    print(f"Planned slide decisions: {len(decisions)}")
    print(f"Executed assets: {len(executed)}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
