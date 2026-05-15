# PPT Master API 契约

基础地址：

```text
http://127.0.0.1:8000
```

启动后可访问：

- Swagger UI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`
- 阶段列表：`GET /api/stages`

## 任务状态

`POST /api/jobs`、`GET /api/jobs/{job_id}` 返回同一种任务摘要：

```json
{
  "id": "20260513185555_8bd952c7",
  "status": "running",
  "stage": "script",
  "stage_label": "生成讲稿",
  "progress": 40,
  "created_at": "2026-05-13T10:55:55.923483Z",
  "updated_at": "2026-05-13T10:55:56.507416Z",
  "input_path": "C:\\...\\input\\demo.pptx",
  "project_path": "C:\\...\\projects\\api_xxx_ppt169_20260513",
  "error": "",
  "result": {},
  "steps": []
}
```

核心字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 任务 ID |
| `status` | string | `queued` / `running` / `completed` / `failed` |
| `stage` | string | 当前阶段机器码 |
| `stage_label` | string | 当前阶段中文名 |
| `progress` | number | 0-100 进度 |
| `project_path` | string | 后端生成或复用的项目目录 |
| `result` | object | 完成后的输出路径摘要 |
| `steps` | array | Director 多角色步骤，适合详情页展示 |

主要阶段顺序：

```text
queued -> preparing -> create_project -> import_source -> project_ready
-> director -> parser -> preflight -> preview -> script -> audio
-> components -> assets -> layout -> render -> qa -> completed
```

## 创建任务

### 上传源文件

`POST /api/jobs`

Content-Type：`multipart/form-data`

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `file` | file | 是 | - | PPTX/PDF/DOCX/Markdown 等源文件 |
| `project_name` | string | 否 | 自动生成 | 项目名 |
| `render` | boolean | 否 | `true` | 是否渲染 MP4 |
| `style` | string | 否 | `adaptive` | 视频风格 |
| `execute_assets` | boolean | 否 | `false` | 是否允许联网搜索/生成素材 |
| `skip_assets` | boolean | 否 | `false` | 是否跳过视觉素材规划 |
| `asset_limit` | number | 否 | 空 | 限制素材执行数量 |
| `preview` | number | 否 | 空 | 只处理前 N 页，用于快速预览 |
| `force_render` | boolean | 否 | `false` | 忽略片段缓存，强制重绘 |
| `voice` | string | 否 | `zh-CN-XiaoxiaoNeural` | edge-tts 音色 |
| `rate` | string | 否 | `-6%` | edge-tts 语速 |
| `pitch` | string | 否 | `-3Hz` | edge-tts 音调 |
| `tts_provider` | string | 否 | `edge` | `edge` 或 `sapi` |
| `sapi_rate` | number | 否 | `1` | Windows SAPI 语速 |
| `force_audio` | boolean | 否 | `false` | 强制重生逐页音频 |
| `generate_audio` | boolean | 否 | `true` | 自动生成缺失音频 |
| `ensure_subtitles` | boolean | 否 | `true` | 自动补生成字幕 |
| `qa_each_slide` | boolean | 否 | `false` | 每页抽帧做 QA |
| `qa_frames` | number | 否 | `3` | QA 抽帧数量 |
| `render_timeout` | number | 否 | `1200` | 渲染超时秒数 |
| `audio_timeout` | number | 否 | `900` | 音频阶段超时秒数 |

示例：

```bash
curl --noproxy 127.0.0.1 -X POST http://127.0.0.1:8000/api/jobs \
  -F "file=@inputs/demo.pptx" \
  -F "render=true" \
  -F "style=adaptive" \
  -F "preview=5"
```

### 复用已有项目

`POST /api/jobs`

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_path` | string | 是 | 已存在的 PPT Master 项目目录 |
| `render` | boolean | 否 | 是否渲染 MP4 |

注意：`file` 和 `project_path` 二选一，不能同时传。

## 查询输出

```http
GET /api/jobs/{job_id}/outputs
```

返回：

```json
{
  "job_id": "20260513185555_8bd952c7",
  "status": "completed",
  "outputs": {
    "final_video_path": {
      "path": "C:\\...\\adaptive_style_final.mp4",
      "exists": true,
      "download_url": "/api/jobs/20260513185555_8bd952c7/download/final_video_path"
    },
    "preflight_report_path": {
      "path": "C:\\...\\preflight_report.json",
      "exists": true,
      "download_url": "/api/jobs/20260513185555_8bd952c7/download/preflight_report_path"
    }
  }
}
```

可下载类型：

| kind | 说明 |
| --- | --- |
| `final_video_path` | 最终带字幕视频 |
| `base_video_path` | 未烧录字幕或基础视频 |
| `qa_report_path` | QA 报告 |
| `preflight_report_path` | 课件预检报告 |
| `state_path` | pipeline 状态 |
| `video_agent_state_path` | Director 多角色状态 |
| `project_config_path` | 项目默认配置 |
| `preview_manifest_path` | 预览截断清单，仅预览任务存在 |

下载：

```http
GET /api/jobs/{job_id}/download/{kind}
```

## 项目配置

流水线会在项目目录写入 `project_config.json`，保存稳定默认值，例如音色、语速、音调、素材策略、字幕和 QA 设置。

`preview`、`force_render` 这类一次性运行模式不会写入默认配置，避免后续完整渲染被意外截断或强制重绘；需要时通过 CLI/API 参数显式传入。
