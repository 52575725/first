#!/usr/bin/env python3
"""Unified orchestration layer for PPT Master micro-course video generation."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "ppt-master" / "scripts"
PROJECTS_DIR = REPO_ROOT / "projects"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value.strip())
    safe = safe.strip("._")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe[:90] or "micro_course"


@dataclass
class PipelineConfig:
    input_path: Path | None = None
    project_path: Path | None = None
    project_name: str | None = None
    projects_dir: Path = PROJECTS_DIR
    canvas_format: str = "ppt169"
    render: bool = True
    style: str = "micro-course"
    execute_assets: bool = False
    skip_assets: bool = False
    asset_limit: int | None = None
    preview_slides: int | None = None
    force_render: bool = False
    force_audio: bool = False
    generate_audio: bool = True
    tts_provider: str = "edge"
    tts_voice: str = "zh-CN-YunyangNeural"
    tts_edge_rate: str = "-8%"
    tts_edge_pitch: str = "+0Hz"
    tts_rate: int = 1
    ensure_subtitles: bool = True
    qa_each_slide: bool = False
    qa_frames: int = 3
    render_timeout: int = 3600
    audio_timeout: int = 900
    jobs: int = 3
    copy_source: bool = True
    output_path: Path | None = None
    extra_director_args: list[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    project_path: Path
    state_path: Path
    qa_report_path: Path | None
    preflight_report_path: Path | None
    final_video_path: Path | None
    base_video_path: Path | None
    copied_output_path: Path | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_path": str(self.project_path),
            "state_path": str(self.state_path),
            "qa_report_path": str(self.qa_report_path) if self.qa_report_path else "",
            "preflight_report_path": str(self.preflight_report_path) if self.preflight_report_path else "",
            "final_video_path": str(self.final_video_path) if self.final_video_path else "",
            "base_video_path": str(self.base_video_path) if self.base_video_path else "",
            "copied_output_path": str(self.copied_output_path) if self.copied_output_path else "",
            "status": self.status,
        }


class UnifiedVideoPipeline:
    """Create or reuse a project, then run the deterministic video director."""

    def __init__(
        self,
        config: PipelineConfig,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.progress_callback = progress_callback
        self.run_log: list[dict[str, Any]] = []

    def run(self) -> PipelineResult:
        self._report_progress("preparing", "准备任务", 3)
        project_path = self._resolve_project()
        self._report_progress("project_ready", "项目已准备", 18, project_path=project_path)
        self._write_project_config(project_path)
        self._write_pipeline_state(project_path, status="running")
        try:
            self._report_progress("director", "启动视频导演", 20, project_path=project_path)
            self._run_director(project_path)
            result = self._collect_result(project_path, status="completed")
            self._write_pipeline_state(project_path, status="completed", result=result.to_dict())
            self._report_progress("completed", "已完成", 100, project_path=project_path)
            return result
        except Exception as exc:
            self._write_pipeline_state(project_path, status="failed", error=str(exc))
            self._report_progress("failed", "生成失败", 100, project_path=project_path, error=str(exc))
            raise

    def _resolve_project(self) -> Path:
        if self.config.project_path:
            project = self.config.project_path.resolve()
            if not project.exists():
                raise FileNotFoundError(f"Project directory not found: {project}")
            return project

        if not self.config.input_path:
            raise ValueError("Either input_path or project_path is required")

        input_path = self.config.input_path.resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        project_name = sanitize_name(self.config.project_name or input_path.stem)
        projects_dir = self.config.projects_dir.resolve()
        projects_dir.mkdir(parents=True, exist_ok=True)

        project = self._create_project(project_name, projects_dir)
        self._import_source(project, input_path)
        return project.resolve()

    def _create_project(self, project_name: str, projects_dir: Path) -> Path:
        self._report_progress("create_project", "创建项目", 8)
        date_token = datetime.now().strftime("%Y%m%d")
        last_error: Exception | None = None
        for attempt in range(1, 100):
            effective_name = project_name if attempt == 1 else f"{project_name}_{attempt}"
            project_path = projects_dir / f"{effective_name}_{self.config.canvas_format}_{date_token}"
            command = [
                sys.executable,
                str(SCRIPTS_DIR / "project_manager.py"),
                "init",
                effective_name,
                "--format",
                self.config.canvas_format,
                "--dir",
                str(projects_dir),
            ]
            try:
                self._run_command(command, cwd=REPO_ROOT)
            except RuntimeError as exc:
                last_error = exc
                if "already exists" in str(exc):
                    continue
                raise
            if not project_path.exists():
                raise RuntimeError(f"Project init did not create expected directory: {project_path}")
            self._report_progress("create_project", "项目创建完成", 12, project_path=project_path)
            return project_path
        raise RuntimeError(f"Could not create unique project directory: {last_error}")

    def _import_source(self, project: Path, input_path: Path) -> None:
        self._report_progress("import_source", "导入源文件", 14, project_path=project)
        command = [
            sys.executable,
            str(SCRIPTS_DIR / "project_manager.py"),
            "import-sources",
            str(project),
            str(input_path),
            "--copy" if self.config.copy_source else "--move",
        ]
        self._run_command(command, cwd=REPO_ROOT)
        self._report_progress("import_source", "源文件导入完成", 17, project_path=project)

    def _run_director(self, project: Path) -> None:
        command = [
            sys.executable,
            str(SCRIPTS_DIR / "video_director_agent.py"),
            str(project),
            "--style",
            self.config.style,
            "--tts-provider",
            self.config.tts_provider,
            "--tts-voice",
            self.config.tts_voice,
            "--tts-edge-rate",
            self.config.tts_edge_rate,
            "--tts-edge-pitch",
            self.config.tts_edge_pitch,
            "--tts-rate",
            str(self.config.tts_rate),
            "--audio-timeout",
            str(self.config.audio_timeout),
            "--render-timeout",
            str(self.config.render_timeout),
            "--qa-frames",
            str(self.config.qa_frames),
        ]
        if self.config.preview_slides:
            command.extend(["--preview-slides", str(self.config.preview_slides)])
        if self.config.render:
            command.append("--render")
        if self.config.execute_assets:
            command.append("--execute-assets")
        if self.config.skip_assets:
            command.append("--skip-assets")
        if self.config.asset_limit is not None:
            command.extend(["--asset-limit", str(self.config.asset_limit)])
        if self.config.jobs:
            command.extend(["--jobs", str(self.config.jobs)])
        if self.config.force_audio:
            command.append("--force-audio")
        if self.config.force_render:
            command.append("--force-render")
        if self.config.qa_each_slide:
            command.append("--qa-each-slide")
        if not self.config.generate_audio:
            command.append("--no-generate-audio")
        if self.config.ensure_subtitles:
            command.append("--ensure-subtitles")
        command.extend(self.config.extra_director_args)
        self._run_command(command, cwd=REPO_ROOT, timeout=self.config.render_timeout + self.config.audio_timeout + 120)

    def _collect_result(self, project: Path, status: str) -> PipelineResult:
        style_slug = self.config.style.replace("-", "_") if self.config.style else ""
        final_video = project / "exports" / (f"{style_slug}_style_final.mp4" if style_slug else f"{project.name}_final.mp4")
        base_video = project / "exports" / (f"{style_slug}_style_video.mp4" if style_slug else f"{project.name}_video.mp4")
        qa_report = project / "video_qa_report.json"
        preflight_report = project / "preflight_report.json"
        copied_output: Path | None = None

        final_video_path = final_video if final_video.exists() else None
        base_video_path = base_video if base_video.exists() else None
        if self.config.output_path and final_video_path:
            copied_output = self.config.output_path.resolve()
            copied_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_video_path, copied_output)

        return PipelineResult(
            project_path=project,
            state_path=project / "pipeline_state.json",
            qa_report_path=qa_report if qa_report.exists() else None,
            preflight_report_path=preflight_report if preflight_report.exists() else None,
            final_video_path=final_video_path,
            base_video_path=base_video_path,
            copied_output_path=copied_output,
            status=status,
        )

    def _run_command(self, command: list[str], *, cwd: Path, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
        started_at = now_iso()
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        self.run_log.append(
            {
                "command": [str(item) for item in command],
                "cwd": str(cwd),
                "started_at": started_at,
                "completed_at": now_iso(),
                "returncode": result.returncode,
                "stdout_tail": "\n".join(result.stdout.splitlines()[-20:]),
                "stderr_tail": "\n".join(result.stderr.splitlines()[-20:]),
            }
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "command failed"
            raise RuntimeError(detail)
        return result

    def _write_project_config(self, project: Path) -> None:
        payload = {
            "style": self.config.style,
            "execute_assets": self.config.execute_assets,
            "skip_assets": self.config.skip_assets,
            "asset_limit": self.config.asset_limit,
            "voice": self.config.tts_voice,
            "rate": self.config.tts_edge_rate,
            "pitch": self.config.tts_edge_pitch,
            "tts_provider": self.config.tts_provider,
            "sapi_rate": self.config.tts_rate,
            "generate_audio": self.config.generate_audio,
            "ensure_subtitles": self.config.ensure_subtitles,
            "qa_frames": self.config.qa_frames,
            "qa_each_slide": self.config.qa_each_slide,
            "render_timeout": self.config.render_timeout,
            "audio_timeout": self.config.audio_timeout,
            "jobs": self.config.jobs,
            "updated_at": now_iso(),
        }
        project.mkdir(parents=True, exist_ok=True)
        (project / "project_config.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_pipeline_state(
        self,
        project: Path,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "project": str(project),
            "generated_at": now_iso(),
            "status": status,
            "config": {
                "input_path": str(self.config.input_path) if self.config.input_path else "",
                "project_path": str(self.config.project_path) if self.config.project_path else "",
                "canvas_format": self.config.canvas_format,
                "render": self.config.render,
                "style": self.config.style,
                "execute_assets": self.config.execute_assets,
                "skip_assets": self.config.skip_assets,
                "preview_slides": self.config.preview_slides,
                "force_render": self.config.force_render,
                "tts_provider": self.config.tts_provider,
                "tts_voice": self.config.tts_voice,
                "tts_edge_rate": self.config.tts_edge_rate,
                "tts_edge_pitch": self.config.tts_edge_pitch,
                "ensure_subtitles": self.config.ensure_subtitles,
                "qa_each_slide": self.config.qa_each_slide,
                "qa_frames": self.config.qa_frames,
                "jobs": self.config.jobs,
            },
            "commands": self.run_log,
        }
        if result is not None:
            payload["result"] = result
        if error:
            payload["error"] = error
        project.mkdir(parents=True, exist_ok=True)
        (project / "pipeline_state.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _report_progress(
        self,
        stage: str,
        stage_label: str,
        progress: int,
        *,
        project_path: Path | None = None,
        error: str | None = None,
    ) -> None:
        if self.progress_callback is None:
            return
        payload: dict[str, Any] = {
            "stage": stage,
            "stage_label": stage_label,
            "progress": max(0, min(100, int(progress))),
        }
        if project_path is not None:
            payload["project_path"] = str(project_path)
        if error:
            payload["error"] = error
        self.progress_callback(payload)
