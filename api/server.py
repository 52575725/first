#!/usr/bin/env python3
"""FastAPI wrapper for the unified PPT Master video pipeline."""

from __future__ import annotations

import json
import shutil
import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline import PipelineConfig, UnifiedVideoPipeline  # noqa: E402


API_JOBS_DIR = REPO_ROOT / "api_jobs"
MAX_WORKERS = 1
STAGE_DEFINITIONS = [
    {"stage": "queued", "stage_label": "排队中", "progress": 0},
    {"stage": "preparing", "stage_label": "准备任务", "progress": 3},
    {"stage": "create_project", "stage_label": "创建项目", "progress": 8},
    {"stage": "import_source", "stage_label": "导入源文件", "progress": 14},
    {"stage": "project_ready", "stage_label": "项目已准备", "progress": 18},
    {"stage": "director", "stage_label": "启动视频导演", "progress": 20},
    {"stage": "parser", "stage_label": "解析课件", "progress": 28},
    {"stage": "preflight", "stage_label": "课件预检", "progress": 34},
    {"stage": "preview", "stage_label": "预览截断", "progress": 36},
    {"stage": "script", "stage_label": "生成讲稿", "progress": 40},
    {"stage": "audio", "stage_label": "合成音频", "progress": 55},
    {"stage": "components", "stage_label": "选择视频组件", "progress": 65},
    {"stage": "assets", "stage_label": "规划视觉素材", "progress": 75},
    {"stage": "layout", "stage_label": "生成渲染计划", "progress": 82},
    {"stage": "render", "stage_label": "渲染视频", "progress": 92},
    {"stage": "qa", "stage_label": "质量检查", "progress": 97},
    {"stage": "completed", "stage_label": "已完成", "progress": 100},
    {"stage": "failed", "stage_label": "生成失败", "progress": 100},
]
ROLE_STAGE_MAP = {
    "ParserAgent": {"stage": "parser", "stage_label": "解析课件", "progress": 28},
    "PreflightAgent": {"stage": "preflight", "stage_label": "课件预检", "progress": 34},
    "PreviewLimiter": {"stage": "preview", "stage_label": "预览截断", "progress": 36},
    "ScriptWriterAgent": {"stage": "script", "stage_label": "生成讲稿", "progress": 40},
    "NarrationAudioAgent": {"stage": "audio", "stage_label": "合成音频", "progress": 55},
    "ComponentSelectorAgent": {"stage": "components", "stage_label": "选择视频组件", "progress": 65},
    "AssetCuratorAgent": {"stage": "assets", "stage_label": "规划视觉素材", "progress": 75},
    "LayoutComposerAgent": {"stage": "layout", "stage_label": "生成渲染计划", "progress": 82},
    "RendererAgent": {"stage": "render", "stage_label": "渲染视频", "progress": 92},
    "QAAgent": {"stage": "qa", "stage_label": "质量检查", "progress": 97},
}

app = FastAPI(
    title="PPT Master Micro-course API",
    description="Upload PPT/course material, run the unified video pipeline, and download outputs.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://127.0.0.1:40423",
        "http://localhost:40423",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = Lock()


class JobSummary(BaseModel):
    id: str
    status: str
    stage: str = "queued"
    stage_label: str = "排队中"
    progress: int = 0
    created_at: str
    updated_at: str
    input_path: str = ""
    project_path: str = ""
    error: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)


class JobList(BaseModel):
    jobs: list[JobSummary]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_name(value: str, fallback: str = "upload") -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value.strip())
    safe = safe.strip("._")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe[:120] or fallback


def new_job_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def job_dir(job_id: str) -> Path:
    return API_JOBS_DIR / job_id


def job_state_path(job_id: str) -> Path:
    return job_dir(job_id) / "job_state.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with _jobs_lock:
        state = _jobs.get(job_id) or read_json(job_state_path(job_id), {})
        state.update(patch)
        state["updated_at"] = now_iso()
        _jobs[job_id] = state
        write_json(job_state_path(job_id), state)
        return state


def load_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        if job_id in _jobs:
            return _jobs[job_id]
    state = read_json(job_state_path(job_id), {})
    if not state:
        raise HTTPException(status_code=404, detail="Job not found")
    with _jobs_lock:
        _jobs[job_id] = state
    return state


def list_job_states() -> list[dict[str, Any]]:
    API_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    states = []
    for path in sorted(API_JOBS_DIR.glob("*/job_state.json"), reverse=True):
        try:
            states.append(read_json(path))
        except Exception:
            continue
    with _jobs_lock:
        known_ids = {state.get("id") for state in states}
        for job_id, state in _jobs.items():
            if job_id not in known_ids:
                states.append(state)
    return states


def job_to_summary(state: dict[str, Any]) -> JobSummary:
    progress = derive_job_progress(state)
    return JobSummary(
        id=state.get("id", ""),
        status=state.get("status", "unknown"),
        stage=progress["stage"],
        stage_label=progress["stage_label"],
        progress=progress["progress"],
        created_at=state.get("created_at", ""),
        updated_at=state.get("updated_at", ""),
        input_path=state.get("input_path", ""),
        project_path=state.get("project_path", ""),
        error=state.get("error", ""),
        result=state.get("result", {}),
        steps=progress["steps"],
    )


def run_pipeline_job(job_id: str, config: PipelineConfig) -> None:
    def report_progress(patch: dict[str, Any]) -> None:
        save_job(job_id, patch)

    save_job(
        job_id,
        {
            "status": "running",
            "stage": "preparing",
            "stage_label": "准备任务",
            "progress": 3,
            "started_at": now_iso(),
            "error": "",
        },
    )
    try:
        result = UnifiedVideoPipeline(config, progress_callback=report_progress).run()
        save_job(
            job_id,
            {
                "status": "completed",
                "stage": "completed",
                "stage_label": "已完成",
                "progress": 100,
                "completed_at": now_iso(),
                "project_path": str(result.project_path),
                "result": result.to_dict(),
            },
        )
    except Exception as exc:
        save_job(
            job_id,
            {
                "status": "failed",
                "stage": "failed",
                "stage_label": "生成失败",
                "progress": 100,
                "completed_at": now_iso(),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/stages")
def stages() -> dict[str, Any]:
    return {"stages": STAGE_DEFINITIONS}


@app.get("/api/jobs", response_model=JobList)
def list_jobs() -> JobList:
    return JobList(jobs=[job_to_summary(state) for state in list_job_states()])


@app.post("/api/jobs", response_model=JobSummary, status_code=202)
async def create_job(
    file: UploadFile | None = File(default=None),
    project_path: str | None = Form(default=None),
    project_name: str | None = Form(default=None),
    render: bool = Form(default=True),
    style: str = Form(default="micro-course"),
    execute_assets: bool = Form(default=False),
    skip_assets: bool = Form(default=False),
    asset_limit: int | None = Form(default=None),
    voice: str = Form(default="zh-CN-XiaoxiaoNeural"),
    rate: str = Form(default="-6%"),
    pitch: str = Form(default="-3Hz"),
    tts_provider: str = Form(default="edge"),
    sapi_rate: int = Form(default=1),
    preview: int | None = Form(default=None),
    force_audio: bool = Form(default=False),
    force_render: bool = Form(default=False),
    generate_audio: bool = Form(default=True),
    ensure_subtitles: bool = Form(default=True),
    qa_each_slide: bool = Form(default=False),
    qa_frames: int = Form(default=3),
    render_timeout: int = Form(default=3600),
    audio_timeout: int = Form(default=900),
    jobs: int = Form(default=3),
) -> JobSummary:
    if file is None and not project_path:
        raise HTTPException(status_code=400, detail="Provide either an uploaded file or project_path")
    if file is not None and project_path:
        raise HTTPException(status_code=400, detail="Use either file upload or project_path, not both")
    if tts_provider not in {"edge", "sapi"}:
        raise HTTPException(status_code=400, detail="tts_provider must be edge or sapi")

    job_id = new_job_id()
    root = job_dir(job_id)
    input_path: Path | None = None
    project: Path | None = Path(project_path).resolve() if project_path else None

    if file is not None:
        filename = safe_name(file.filename or "upload.pptx", "upload.pptx")
        input_dir = root / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        input_path = input_dir / filename
        with input_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)

    created_at = now_iso()
    initial_state = {
        "id": job_id,
        "status": "queued",
        "stage": "queued",
        "stage_label": "排队中",
        "progress": 0,
        "created_at": created_at,
        "updated_at": created_at,
        "input_path": str(input_path) if input_path else "",
        "project_path": str(project) if project else "",
        "result": {},
        "error": "",
    }
    save_job(job_id, initial_state)

    config = PipelineConfig(
        input_path=input_path,
        project_path=project,
        project_name=project_name or (f"api_{job_id}" if input_path else None),
        projects_dir=root / "projects",
        render=render,
        style=style,
        execute_assets=execute_assets,
        skip_assets=skip_assets,
        asset_limit=asset_limit,
        preview_slides=preview,
        force_render=force_render,
        force_audio=force_audio,
        generate_audio=generate_audio,
        tts_provider=tts_provider,
        tts_voice=voice,
        tts_edge_rate=rate,
        tts_edge_pitch=pitch,
        tts_rate=sapi_rate,
        ensure_subtitles=ensure_subtitles,
        qa_each_slide=qa_each_slide,
        qa_frames=qa_frames,
        render_timeout=render_timeout,
        audio_timeout=audio_timeout,
        jobs=jobs,
        copy_source=True,
    )
    _executor.submit(run_pipeline_job, job_id, config)
    return job_to_summary(load_job(job_id))


@app.get("/api/jobs/{job_id}", response_model=JobSummary)
def get_job(job_id: str) -> JobSummary:
    return job_to_summary(load_job(job_id))


@app.get("/api/jobs/{job_id}/outputs")
def get_outputs(job_id: str) -> dict[str, Any]:
    state = load_job(job_id)
    result = state.get("result", {})
    project_path = result.get("project_path") or state.get("project_path", "")
    keys = ["final_video_path", "base_video_path", "qa_report_path", "preflight_report_path", "state_path"]
    outputs = {
        key: {
            "path": result.get(key, ""),
            "exists": bool(result.get(key) and Path(result[key]).exists()),
            "download_url": f"/api/jobs/{job_id}/download/{key}",
        }
        for key in keys
    }
    video_agent_state = Path(project_path) / "video_agent_state.json" if project_path else None
    if video_agent_state:
        outputs["video_agent_state_path"] = {
            "path": str(video_agent_state),
            "exists": video_agent_state.exists(),
            "download_url": f"/api/jobs/{job_id}/download/video_agent_state_path",
        }
        project_config = Path(project_path) / "project_config.json"
        outputs["project_config_path"] = {
            "path": str(project_config),
            "exists": project_config.exists(),
            "download_url": f"/api/jobs/{job_id}/download/project_config_path",
        }
        preview_manifest = Path(project_path) / "preview_manifest.json"
        outputs["preview_manifest_path"] = {
            "path": str(preview_manifest),
            "exists": preview_manifest.exists(),
            "download_url": f"/api/jobs/{job_id}/download/preview_manifest_path",
        }
    return {"job_id": job_id, "status": state.get("status"), "outputs": outputs}


@app.get("/api/jobs/{job_id}/download/{kind}")
def download_output(job_id: str, kind: str) -> FileResponse:
    state = load_job(job_id)
    result = state.get("result", {})
    allowed = {
        "final_video_path": result.get("final_video_path", ""),
        "base_video_path": result.get("base_video_path", ""),
        "qa_report_path": result.get("qa_report_path", ""),
        "preflight_report_path": result.get("preflight_report_path", ""),
        "state_path": result.get("state_path", ""),
    }
    if kind == "video_agent_state_path" and result.get("project_path"):
        allowed[kind] = str(Path(result["project_path"]) / "video_agent_state.json")
    if kind == "project_config_path" and result.get("project_path"):
        allowed[kind] = str(Path(result["project_path"]) / "project_config.json")
    if kind == "preview_manifest_path" and result.get("project_path"):
        allowed[kind] = str(Path(result["project_path"]) / "preview_manifest.json")
    if kind not in allowed:
        raise HTTPException(status_code=404, detail="Unknown output kind")
    path = Path(allowed[kind]) if allowed[kind] else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(path, filename=path.name)


def derive_job_progress(state: dict[str, Any]) -> dict[str, Any]:
    status = state.get("status", "unknown")
    if status == "failed":
        return {
            "stage": "failed",
            "stage_label": "生成失败",
            "progress": 100,
            "steps": read_role_steps(state),
        }
    if status == "completed":
        return {
            "stage": "completed",
            "stage_label": "已完成",
            "progress": 100,
            "steps": read_role_steps(state),
        }

    role_progress = progress_from_video_agent_state(state)
    if role_progress:
        return role_progress

    return {
        "stage": state.get("stage", "queued"),
        "stage_label": state.get("stage_label", "排队中"),
        "progress": int(state.get("progress", 0) or 0),
        "steps": [],
    }


def project_path_from_state(state: dict[str, Any]) -> Path | None:
    result = state.get("result", {})
    path = result.get("project_path") or state.get("project_path")
    if not path:
        return None
    return Path(path)


def read_role_steps(state: dict[str, Any]) -> list[dict[str, Any]]:
    project = project_path_from_state(state)
    if project is None:
        return []
    agent_state = project / "video_agent_state.json"
    if not agent_state.exists():
        return []
    try:
        payload = read_json(agent_state)
    except Exception:
        return []
    steps = []
    for item in payload.get("roles", []):
        role = item.get("role", "")
        stage_meta = ROLE_STAGE_MAP.get(role, {})
        steps.append(
            {
                "role": role,
                "stage": stage_meta.get("stage", role),
                "stage_label": stage_meta.get("stage_label", role),
                "status": item.get("status", ""),
                "outputs": item.get("outputs", {}),
                "metrics": item.get("metrics", {}),
                "notes": item.get("notes", []),
            }
        )
    return steps


def progress_from_video_agent_state(state: dict[str, Any]) -> dict[str, Any] | None:
    steps = read_role_steps(state)
    if not steps:
        return None

    completed = [
        step
        for step in steps
        if step.get("status") in {"completed", "skipped"}
    ]
    if not completed:
        return {
            "stage": state.get("stage", "director"),
            "stage_label": state.get("stage_label", "启动视频导演"),
            "progress": int(state.get("progress", 20) or 20),
            "steps": steps,
        }

    last = completed[-1]
    meta = ROLE_STAGE_MAP.get(last.get("role", ""), {})
    return {
        "stage": meta.get("stage", last.get("stage", "director")),
        "stage_label": meta.get("stage_label", last.get("stage_label", "处理中")),
        "progress": int(meta.get("progress", state.get("progress", 20)) or 20),
        "steps": steps,
    }
