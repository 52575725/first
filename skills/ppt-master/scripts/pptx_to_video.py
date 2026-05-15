#!/usr/bin/env python3
"""PPT Master - PPTX/SVG project to video converter.

Converts a PPT Master project to MP4 with synchronized narration audio.
The video renderer keeps slide artwork proportional, can reserve a subtitle
safe area, and can burn generated SRT subtitles into the final MP4.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


RESOLUTIONS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}


def load_pillow():
    """Import Pillow only when slide raster composition is needed."""
    try:
        from PIL import Image, ImageColor, ImageDraw

        return Image, ImageColor, ImageDraw
    except ImportError as e:
        raise RuntimeError("Pillow not installed. Run: pip install Pillow") from e


def check_ffmpeg() -> bool:
    """Check if ffmpeg and ffprobe are available."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def probe_media_duration(media_path: Path) -> float:
    """Get media duration in seconds using ffprobe."""
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
                str(media_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as e:
        print(f"warning: failed to probe {media_path.name}: {e}", file=sys.stderr)
        return 0.0


def _parse_length(value: str | None) -> Optional[float]:
    """Parse an SVG length value, ignoring unit suffixes."""
    if not value:
        return None

    cleaned = value.strip()
    for suffix in ("px", "pt", "in", "cm", "mm"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    try:
        return float(cleaned)
    except ValueError:
        return None


def svg_aspect_ratio(svg_path: Path) -> float:
    """Read the SVG aspect ratio from viewBox or width/height."""
    try:
        root = ET.parse(svg_path).getroot()
    except ET.ParseError:
        return 16 / 9

    view_box = root.attrib.get("viewBox")
    if view_box:
        parts = view_box.replace(",", " ").split()
        if len(parts) == 4:
            try:
                width = float(parts[2])
                height = float(parts[3])
                if width > 0 and height > 0:
                    return width / height
            except ValueError:
                pass

    width = _parse_length(root.attrib.get("width"))
    height = _parse_length(root.attrib.get("height"))
    if width and height and height > 0:
        return width / height

    return 16 / 9


def svg_to_png(svg_path: Path, output_path: Path, width: int, height: int) -> None:
    """Convert SVG to PNG using cairosvg or Inkscape."""
    try:
        import cairosvg

        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(output_path),
            output_width=width,
            output_height=height,
        )
        return
    except ImportError:
        pass

    try:
        subprocess.run(
            [
                "inkscape",
                str(svg_path),
                "--export-type=png",
                f"--export-filename={output_path}",
                f"--export-width={width}",
                f"--export-height={height}",
            ],
            check=True,
            capture_output=True,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    raise RuntimeError(
        "No SVG converter available. Install cairosvg or Inkscape."
    )


def render_raster_slide(
    image_path: Path,
    output_path: Path,
    width: int,
    height: int,
    *,
    fit: str,
    margin: int,
    subtitle_band: int,
    background: str,
) -> None:
    """Render an existing PNG/JPG slide into the video canvas with ffmpeg."""
    margin = max(0, margin)
    subtitle_band = max(0, subtitle_band)
    content_w = max(1, width - margin * 2)
    content_h = max(1, height - margin * 2 - subtitle_band)

    if fit == "stretch" and margin == 0 and subtitle_band == 0:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(image_path), "-frames:v", "1", str(output_path)],
            check=True,
            capture_output=True,
        )
        return

    if fit == "cover":
        scale_mode = "increase"
        crop_or_pad = (
            f"crop={content_w}:{content_h},"
            f"pad={width}:{height}:{margin}:{margin}:color={background}"
        )
    elif fit == "stretch":
        scale_mode = "disable"
        crop_or_pad = f"pad={width}:{height}:{margin}:{margin}:color={background}"
    else:
        scale_mode = "decrease"
        crop_or_pad = (
            f"pad={content_w}:{content_h}:(ow-iw)/2:(oh-ih)/2:color={background},"
            f"pad={width}:{height}:{margin}:{margin}:color={background}"
        )

    if fit == "stretch":
        vf = f"scale={content_w}:{content_h},{crop_or_pad}"
    else:
        vf = (
            f"scale={content_w}:{content_h}:force_original_aspect_ratio={scale_mode},"
            f"{crop_or_pad}"
        )

    if subtitle_band:
        vf += f",drawbox=x=0:y={height - subtitle_band}:w={width}:h={subtitle_band}:color=#101820:t=fill"

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(image_path),
            "-vf",
            vf,
            "-frames:v",
            "1",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )


def render_slide_png(
    svg_path: Path,
    output_path: Path,
    width: int,
    height: int,
    *,
    fit: str,
    margin: int,
    subtitle_band: int,
    background: str,
) -> None:
    """Render one SVG slide into a video canvas with safe-area layout."""
    Image, ImageColor, ImageDraw = load_pillow()

    margin = max(0, margin)
    subtitle_band = max(0, subtitle_band)
    content_w = max(1, width - margin * 2)
    content_h = max(1, height - margin * 2 - subtitle_band)

    if fit == "stretch" and margin == 0 and subtitle_band == 0:
        svg_to_png(svg_path, output_path, width, height)
        return

    canvas = Image.new("RGB", (width, height), ImageColor.getrgb(background))
    if subtitle_band:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(
            [0, height - subtitle_band, width, height],
            fill=(16, 24, 32),
        )

    raw_path = output_path.with_name(f"{output_path.stem}.render.png")

    if fit == "stretch":
        render_w, render_h = content_w, content_h
        svg_to_png(svg_path, raw_path, render_w, render_h)
        image = Image.open(raw_path).convert("RGB")
    else:
        aspect = svg_aspect_ratio(svg_path)
        target_aspect = content_w / content_h

        if fit == "cover":
            if aspect >= target_aspect:
                render_h = content_h
                render_w = max(content_w, round(render_h * aspect))
            else:
                render_w = content_w
                render_h = max(content_h, round(render_w / aspect))
        else:
            if aspect >= target_aspect:
                render_w = content_w
                render_h = max(1, round(render_w / aspect))
            else:
                render_h = content_h
                render_w = max(1, round(render_h * aspect))

        svg_to_png(svg_path, raw_path, render_w, render_h)
        image = Image.open(raw_path).convert("RGB")

        if fit == "cover":
            left = max(0, (image.width - content_w) // 2)
            top = max(0, (image.height - content_h) // 2)
            image = image.crop((left, top, left + content_w, top + content_h))

    paste_x = margin + max(0, (content_w - image.width) // 2)
    paste_y = margin + max(0, (content_h - image.height) // 2)
    canvas.paste(image, (paste_x, paste_y))
    canvas.save(output_path)

    try:
        raw_path.unlink()
    except FileNotFoundError:
        pass


def generate_slide_video(
    png_path: Path,
    audio_path: Optional[Path],
    output_path: Path,
    duration: float,
    fps: int = 30,
    codec: str = "libx264",
) -> None:
    """Generate video for a single slide."""
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(png_path),
    ]

    if audio_path and audio_path.exists():
        cmd.extend(["-i", str(audio_path)])
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    cmd.extend(
        [
            "-c:v",
            codec,
            "-tune",
            "stillimage",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-t",
            str(duration),
            "-shortest",
            str(output_path),
        ]
    )

    subprocess.run(cmd, check=True, capture_output=True)


def concat_videos(video_list: list[Path], output_path: Path) -> None:
    """Concatenate multiple videos into one."""
    concat_file = output_path.parent / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for video in video_list:
            f.write(f"file '{video.absolute()}'\n")

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )

    concat_file.unlink()


def add_bgm(video_path: Path, bgm_path: Path, output_path: Path, volume: float = 0.2) -> None:
    """Add background music to video."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(bgm_path),
            "-filter_complex",
            f"[1:a]volume={volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )


def ffmpeg_filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")


def generate_subtitles(project: Path, audio_dir: Path, output_path: Path) -> bool:
    """Generate subtitles from notes when possible."""
    notes_dir = project / "notes"
    note_files = [f for f in notes_dir.glob("*.md") if f.name != "total.md"] if notes_dir.exists() else []
    if not note_files:
        print("warning: no per-slide notes found; skipping subtitles", file=sys.stderr)
        return False

    try:
        from subtitle_generator import generate_subtitles_from_notes

        generate_subtitles_from_notes(project, audio_dir, output_path)
        return output_path.exists()
    except Exception as e:
        print(f"warning: failed to generate subtitles: {e}", file=sys.stderr)
        return False


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    *,
    font_size: int,
    margin_v: int,
) -> None:
    """Burn SRT subtitles into the final video with a readable style."""
    style = ",".join(
        [
            "FontName=Microsoft YaHei",
            f"FontSize={font_size}",
            "PrimaryColour=&H00FFFFFF",
            "OutlineColour=&H00101820",
            "BackColour=&H70101820",
            "BorderStyle=4",
            "Outline=1",
            "Shadow=0",
            "Alignment=2",
            f"MarginV={margin_v}",
        ]
    )
    subtitle_filter = (
        f"subtitles='{ffmpeg_filter_path(subtitle_path)}':force_style='{style}'"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            subtitle_filter,
            "-c:a",
            "copy",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )


def find_audio(audio_dir: Path, stem: str, index: int) -> Optional[Path]:
    """Find matching audio for a slide."""
    candidates = [
        audio_dir / f"{stem}.mp3",
        audio_dir / f"{stem}.m4a",
        audio_dir / f"page_{index:02d}.mp3",
        audio_dir / f"page_{index:02d}.m4a",
        audio_dir / f"slide_{index:02d}.mp3",
        audio_dir / f"slide_{index:02d}.m4a",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_path", type=Path, help="PPT Master project directory")
    parser.add_argument("-o", "--output", type=Path, help="Output video path")
    parser.add_argument(
        "--resolution",
        choices=list(RESOLUTIONS.keys()),
        default="1080p",
        help="Video resolution (default: 1080p)",
    )
    parser.add_argument("--fps", type=int, default=30, help="Frames per second (default: 30)")
    parser.add_argument(
        "--codec",
        choices=["libx264", "libx265"],
        default="libx264",
        help="Video codec (default: libx264)",
    )
    parser.add_argument("--padding", type=float, default=0.5, help="Padding after each slide in seconds")
    parser.add_argument("--bgm", type=Path, help="Background music file")
    parser.add_argument("--bgm-volume", type=float, default=0.2, help="BGM volume (0.0-1.0)")
    parser.add_argument("--subtitle", action="store_true", help="Generate and burn subtitles from notes")
    parser.add_argument("-s", "--source", default="final", help="SVG source directory suffix (default: final)")
    parser.add_argument(
        "--fit",
        choices=["contain", "cover", "stretch"],
        default="contain",
        help="Slide fitting mode in the video canvas (default: contain)",
    )
    parser.add_argument(
        "--margin",
        type=positive_int,
        default=0,
        help="Canvas margin around the slide in pixels",
    )
    parser.add_argument(
        "--background",
        default="#f7f8fa",
        help="Background color for letterbox/safe areas (default: #f7f8fa)",
    )
    parser.add_argument(
        "--subtitle-layout",
        choices=["reserve", "overlay"],
        default="reserve",
        help="Reserve bottom safe area or overlay subtitles on slide (default: reserve)",
    )
    parser.add_argument(
        "--subtitle-band-ratio",
        type=float,
        default=0.16,
        help="Reserved subtitle band height as a ratio of video height (default: 0.16)",
    )
    parser.add_argument(
        "--subtitle-font-size",
        type=positive_int,
        help="Subtitle font size in pixels (default scales with resolution)",
    )

    args = parser.parse_args()

    if not check_ffmpeg():
        print("error: ffmpeg/ffprobe not found. Please install ffmpeg.", file=sys.stderr)
        return 1

    project = args.project_path
    if not project.exists():
        print(f"error: project not found: {project}", file=sys.stderr)
        return 1

    audio_dir = project / "audio"

    svg_dir = project / f"svg_{args.source}"
    svg_files = sorted(svg_dir.glob("*.svg")) if svg_dir.exists() else []
    if not svg_files:
        fallback_svg_dir = project / "svg_output"
        svg_files = sorted(fallback_svg_dir.glob("*.svg")) if fallback_svg_dir.exists() else []
        if svg_files:
            svg_dir = fallback_svg_dir

    raster_dir = project / "slides"
    raster_files = []
    if not svg_files and raster_dir.exists():
        raster_files = sorted(raster_dir.glob("slide_*.png"))

    slide_files = svg_files or raster_files
    source_kind = "svg" if svg_files else "raster"

    if not slide_files:
        print(f"error: no SVG files or slides/slide_*.png found in {project}", file=sys.stderr)
        return 1

    width, height = RESOLUTIONS[args.resolution]
    subtitle_band = 0
    if args.subtitle and args.subtitle_layout == "reserve":
        subtitle_band = round(height * max(0.0, min(args.subtitle_band_ratio, 0.35)))

    temp_dir = project / "temp_video"
    temp_dir.mkdir(exist_ok=True)

    print(f"Converting {len(slide_files)} slides to video...")
    print(f"Resolution: {args.resolution} ({width}x{height})")
    print(f"Codec: {args.codec}, FPS: {args.fps}")
    print(f"Source: {source_kind}")
    print(f"Layout: fit={args.fit}, margin={args.margin}px, subtitle_band={subtitle_band}px")

    video_segments: list[Path] = []

    for i, slide_file in enumerate(slide_files, 1):
        print(f"[{i}/{len(slide_files)}] Processing {slide_file.name}...", end=" ")

        png_path = temp_dir / f"{slide_file.stem}.png"
        try:
            if source_kind == "svg":
                render_slide_png(
                    slide_file,
                    png_path,
                    width,
                    height,
                    fit=args.fit,
                    margin=args.margin,
                    subtitle_band=subtitle_band,
                    background=args.background,
                )
            else:
                render_raster_slide(
                    slide_file,
                    png_path,
                    width,
                    height,
                    fit=args.fit,
                    margin=args.margin,
                    subtitle_band=subtitle_band,
                    background=args.background,
                )
        except Exception as e:
            print(f"\nerror: failed to render {slide_file.name}: {e}", file=sys.stderr)
            return 1

        audio_path = find_audio(audio_dir, slide_file.stem, i)

        if audio_path:
            duration = probe_media_duration(audio_path) + args.padding
        else:
            duration = 3.0

        segment_path = temp_dir / f"segment_{i:03d}.mp4"
        try:
            generate_slide_video(
                png_path,
                audio_path,
                segment_path,
                duration,
                args.fps,
                args.codec,
            )
            video_segments.append(segment_path)
            print(f"OK ({duration:.1f}s)")
        except Exception as e:
            print(f"\nerror: failed to generate video: {e}", file=sys.stderr)
            return 1

    print("\nConcatenating video segments...")
    output_path = args.output or (project / "exports" / f"{project.name}.mp4")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    working_output = temp_dir / "output_concatenated.mp4"
    try:
        concat_videos(video_segments, working_output)
    except Exception as e:
        print(f"error: failed to concatenate videos: {e}", file=sys.stderr)
        return 1

    if args.bgm and args.bgm.exists():
        print(f"Adding background music ({args.bgm.name})...")
        mixed_output = temp_dir / "output_with_bgm.mp4"
        try:
            add_bgm(working_output, args.bgm, mixed_output, args.bgm_volume)
            working_output = mixed_output
        except Exception as e:
            print(f"warning: failed to add BGM: {e}", file=sys.stderr)

    if args.subtitle:
        print("Generating subtitles...")
        subtitle_path = output_path.with_name(f"{output_path.stem}_subtitles.srt")
        has_subtitles = generate_subtitles(project, audio_dir, subtitle_path)
        if has_subtitles:
            print("Burning subtitles...")
            font_size = args.subtitle_font_size or max(12, round(height * 0.013))
            margin_v = max(24, round(height * 0.035))
            try:
                burn_subtitles(
                    working_output,
                    subtitle_path,
                    output_path,
                    font_size=font_size,
                    margin_v=margin_v,
                )
            except Exception as e:
                print(f"warning: failed to burn subtitles: {e}", file=sys.stderr)
                working_output.replace(output_path)
        else:
            working_output.replace(output_path)
    else:
        working_output.replace(output_path)

    print(f"\nVideo generated: {output_path}")
    print(f"  Duration: {probe_media_duration(output_path):.1f}s")
    print(f"  Size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")

    return 0


if __name__ == "__main__":
    sys.exit(main())
