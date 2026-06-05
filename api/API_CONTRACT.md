# 微课视频 API 契约

基础地址：

```text
http://127.0.0.1:8000
```

## 任务状态模型

`POST /api/jobs` 与 `GET /api/jobs/{job_id}` 返回同一类任务摘要：

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
| `progress` | number | 0-100 |
| `input_path` | string | 上传输入文件路径 |
| `project_path` | string | 项目目录 |
| `error` | string | 失败信息 |
| `result` | object | 完成后的输出摘要 |
| `steps` | array | 多角色执行步骤明细 |

阶段顺序：

```text
queued -> preparing -> create_project -> import_source -> project_ready
-> director -> parser -> preflight -> preview -> script -> audio
-> components -> assets -> layout -> render -> qa -> completed
```

## 创建任务

### `POST /api/jobs`

`Content-Type: multipart/form-data`

支持两种模式：

1. 上传源文件 `file`
2. 复用已有项目目录 `project_path`

二者只能二选一。

参数定义：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `file` | file | 否 | - | PPTX / PDF / DOCX / Markdown 等输入 |
| `project_path` | string | 否 | - | 已存在项目目录 |
| `project_name` | string | 否 | 自动生成 | 新项目名称 |
| `render` | boolean | 否 | `true` | 是否渲染最终视频 |
| `style` | string | 否 | `micro-course` | 渲染风格 |
| `execute_assets` | boolean | 否 | `false` | 是否执行联网素材搜索/生成 |
| `skip_assets` | boolean | 否 | `false` | 是否跳过素材规划 |
| `asset_limit` | number | 否 | `8` | 素材数量上限 |
| `asset_timeout` | number | 否 | `300` | 素材阶段超时秒数 |
| `voice` | string | 否 | `zh-CN-XiaoxiaoNeural` | edge-tts 音色 |
| `rate` | string | 否 | `-6%` | edge-tts 语速 |
| `pitch` | string | 否 | `-3Hz` | edge-tts 音调 |
| `tts_provider` | string | 否 | `edge` | `edge` 或 `sapi` |
| `sapi_rate` | number | 否 | `1` | Windows SAPI 语速 |
| `preview` | number | 否 | 空 | 只处理前 N 页 |
| `force_audio` | boolean | 否 | `false` | 强制重生音频 |
| `force_render` | boolean | 否 | `false` | 强制重绘视频片段 |
| `generate_audio` | boolean | 否 | `true` | 自动补生成缺失音频 |
| `ensure_subtitles` | boolean | 否 | `true` | 自动补生成字幕 |
| `qa_each_slide` | boolean | 否 | `false` | 每页抽帧 QA |
| `qa_frames` | number | 否 | `3` | QA 抽帧数量 |
| `render_timeout` | number | 否 | `3600` | 渲染超时秒数 |
| `audio_timeout` | number | 否 | `900` | 音频阶段超时秒数 |
| `jobs` | number | 否 | `3` | 并行渲染片段数 |

示例：

```bash
curl --noproxy 127.0.0.1 -X POST http://127.0.0.1:8000/api/jobs ^
  -F "file=@inputs/demo.pptx" ^
  -F "render=true" ^
  -F "style=micro-course" ^
  -F "preview=5"
```

## 查询输出

### `GET /api/jobs/{job_id}/outputs`

返回示例：

```json
{
  "job_id": "20260513185555_8bd952c7",
  "status": "completed",
  "outputs": {
    "final_video_path": {
      "path": "C:\\...\\micro_course_style_final.mp4",
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
| `base_video_path` | 基础视频 |
| `qa_report_path` | QA 报告 |
| `preflight_report_path` | 预检报告 |
| `state_path` | pipeline 状态文件 |

下载接口：

```http
GET /api/jobs/{job_id}/download/{kind}
```
