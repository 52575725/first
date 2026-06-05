# 智能微课视频生成系统

将 PPT、PDF、DOCX、Markdown 等教学资料生成带配音、字幕和导出视频的微课项目。

## 功能概览

- 导入 PPT、PDF、DOCX、Markdown 等课程资料
- 解析页面结构与内容分段
- 生成讲稿、分页音频和字幕
- 规划视觉素材并完成视频渲染
- 通过命令行或 HTTP API 执行完整流程

## 环境要求

- Python 3.10+
- `ffmpeg` / `ffprobe`
- Windows 下如需本地语音可使用 SAPI
- 可选：
  - LibreOffice 或 Pandoc，用于部分旧格式转换
  - 图片生成或图片搜索相关 API Key

安装依赖：

```bash
pip install -r requirements.txt
```

## 快速开始

直接输入源文件创建新项目：

```bash
python run.py path/to/course.pptx
```

常见用法：

```bash
python run.py path/to/course.pptx ^
  --style micro-course ^
  --voice zh-CN-YunyangNeural ^
  --preview 5 ^
  --execute-assets
```

复用已有项目目录：

```bash
python run.py --project projects/friction_micro_course_demo
```

只生成规划文件，不渲染视频：

```bash
python run.py path/to/course.docx --no-render
```

## HTTP API

启动服务：

```bash
python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
```

接口入口：

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/health`

接口说明见 [api/README.md](./api/README.md) 和 [api/API_CONTRACT.md](./api/API_CONTRACT.md)。

## 仓库结构

```text
api/                                FastAPI 接口
pipeline/                           流程编排层
projects/                           项目与公开示例
run.py                              CLI 入口
requirements.txt                    Python 依赖
```

## 公开示例

仓库附带一个完整示例项目：

- [projects/friction_micro_course_demo](./projects/friction_micro_course_demo)

示例项目包含：

- 源文件
- 结构化中间产物
- 生成音频
- 字幕文件
- 最终视频

## 常见输出

执行过程中通常会生成以下内容：

- `audio/`：分页配音音频
- `exports/*.mp4`：最终视频
- `exports/*.srt`：字幕文件
- `slide_structure.json`：页面结构
- `component_plan.json`：组件与素材规划
- `preflight_report.json`：预检查结果

## 适用场景

- 课程讲解视频
- 教学微课制作
- 培训材料视频化
- 文档到配音视频的快速转换
