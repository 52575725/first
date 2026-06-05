#!/usr/bin/env python3
"""Unified CLI entry point for micro-course video generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline import PipelineConfig, UnifiedVideoPipeline


REPO_ROOT = Path(__file__).resolve().parent


CONFIG_ARG_MAP = {
    "style": ("--style",),
    "execute_assets": ("--execute-assets",),
    "skip_assets": ("--skip-assets",),
    "asset_limit": ("--asset-limit",),
    "asset_timeout": ("--asset-timeout",),
    "voice": ("--voice",),
    "rate": ("--rate",),
    "pitch": ("--pitch",),
    "tts_provider": ("--tts-provider",),
    "sapi_rate": ("--sapi-rate",),
    "force_audio": ("--force-audio",),
    "generate_audio": ("--no-generate-audio",),
    "ensure_subtitles": ("--no-subtitles",),
    "qa_frames": ("--qa-frames",),
    "render_timeout": ("--render-timeout",),
    "audio_timeout": ("--audio-timeout",),
    "jobs": ("--jobs",),
    "qa_each_slide": ("--qa-each-slide",),
}


def load_json_config(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def provided_options(argv: list[str]) -> set[str]:
    provided = set()
    for item in argv:
        if item.startswith("--"):
            provided.add(item.split("=", 1)[0])
    return provided


def apply_project_config(args: argparse.Namespace, argv: list[str]) -> None:
    config_path = args.config_file
    if config_path is None and args.project:
        candidate = args.project / "project_config.json"
        if candidate.exists():
            config_path = candidate
    config = load_json_config(config_path)
    if not config:
        return

    supplied = provided_options(argv)
    for key, option_names in CONFIG_ARG_MAP.items():
        if key not in config:
            continue
        if any(option in supplied for option in option_names):
            continue
        value = config[key]
        if key == "generate_audio":
            args.no_generate_audio = not bool(value)
        elif key == "ensure_subtitles":
            args.no_subtitles = not bool(value)
        else:
            setattr(args, key, value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="一键运行 PPT/教案到微课视频的统一流水线",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", nargs="?", type=Path, help="输入 PPTX/PDF/DOCX/Markdown 等源文件")
    parser.add_argument("--project", type=Path, help="复用已有微课项目目录")
    parser.add_argument("--project-name", help="新建项目名，默认使用输入文件名")
    parser.add_argument("--config-file", type=Path, help="读取项目配置 JSON；默认使用项目目录 project_config.json")
    parser.add_argument("--projects-dir", type=Path, default=REPO_ROOT / "projects", help="项目输出根目录")
    parser.add_argument("--format", default="ppt169", help="画布格式")
    parser.add_argument("--style", default="micro-course", help="视频渲染风格")
    parser.add_argument("--no-render", action="store_true", help="只生成规划文件，不渲染 MP4")
    parser.add_argument("--execute-assets", action="store_true", help="允许执行图片搜索/生成")
    parser.add_argument("--skip-assets", action="store_true", help="跳过视觉素材规划")
    parser.add_argument("--asset-limit", type=int, default=8, help="限制素材搜索/生成数量")
    parser.add_argument("--asset-timeout", type=int, default=300, help="素材规划/搜索超时时间，秒")
    parser.add_argument("--voice", default="zh-CN-YunyangNeural", help="edge-tts 音色")
    parser.add_argument("--rate", default="-8%", help='edge-tts 语速，例如 "+0%%" 或 "-8%%"')
    parser.add_argument("--pitch", default="+0Hz", help='edge-tts 音调，例如 "+0Hz" 或 "-3Hz"')
    parser.add_argument("--tts-provider", choices=["edge", "sapi"], default="edge", help="TTS 提供方")
    parser.add_argument("--sapi-rate", type=int, default=1, help="Windows SAPI 语速，-10 到 10")
    parser.add_argument("--force-audio", action="store_true", help="强制重新生成逐页音频")
    parser.add_argument("--no-generate-audio", action="store_true", help="不自动生成缺失音频")
    parser.add_argument("--no-subtitles", action="store_true", help="不主动补生成字幕")
    parser.add_argument("--preview", type=int, help="只处理前 N 页，用于快速预览")
    parser.add_argument("--force-render", action="store_true", help="忽略渲染缓存，强制重绘所有片段")
    parser.add_argument("--qa-each-slide", action="store_true", help="为每页抽取 QA 截图")
    parser.add_argument("--qa-frames", type=int, default=3, help="渲染后抽取的 QA 截图数量")
    parser.add_argument("--render-timeout", type=int, default=3600, help="渲染超时时间，秒")
    parser.add_argument("--audio-timeout", type=int, default=900, help="音频生成超时时间，秒")
    parser.add_argument("--jobs", type=int, default=3, help="并行渲染片段数")
    parser.add_argument("--move-source", action="store_true", help="导入源文件时移动而不是复制")
    parser.add_argument("--output", type=Path, help="额外复制一份最终 MP4 到指定路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    apply_project_config(args, raw_argv)

    if args.project is None and args.input is None:
        parser.error("需要提供输入文件，或使用 --project 指定已有项目目录")

    config = PipelineConfig(
        input_path=args.input,
        project_path=args.project,
        project_name=args.project_name,
        projects_dir=args.projects_dir,
        canvas_format=args.format,
        render=not args.no_render,
        style=args.style,
        execute_assets=args.execute_assets,
        skip_assets=args.skip_assets,
        asset_limit=args.asset_limit,
        asset_timeout=args.asset_timeout,
        preview_slides=args.preview,
        force_render=args.force_render,
        force_audio=args.force_audio,
        generate_audio=not args.no_generate_audio,
        tts_provider=args.tts_provider,
        tts_voice=args.voice,
        tts_edge_rate=args.rate,
        tts_edge_pitch=args.pitch,
        tts_rate=args.sapi_rate,
        ensure_subtitles=not args.no_subtitles,
        qa_each_slide=args.qa_each_slide,
        qa_frames=args.qa_frames,
        render_timeout=args.render_timeout,
        audio_timeout=args.audio_timeout,
        jobs=args.jobs,
        copy_source=not args.move_source,
        output_path=args.output,
    )

    print("智能微课视频生成系统")
    if config.input_path:
        print(f"  输入: {config.input_path}")
    if config.project_path:
        print(f"  项目: {config.project_path}")
    print(f"  渲染: {'是' if config.render else '否'}")
    print(f"  风格: {config.style}")

    result = UnifiedVideoPipeline(config).run()

    print("\n完成")
    print(f"  项目目录: {result.project_path}")
    print(f"  流水线状态: {result.state_path}")
    if result.qa_report_path:
        print(f"  QA 报告: {result.qa_report_path}")
    if result.final_video_path:
        print(f"  最终视频: {result.final_video_path}")
    elif config.render:
        print("  最终视频: 未生成，请检查 pipeline_state.json / video_agent_state.json")
    if result.copied_output_path:
        print(f"  输出副本: {result.copied_output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
