# 智能微课视频生成系统

将 PPT、PDF、DOCX、Markdown 等教学资料整理为可配音、可加字幕、可导出 MP4 的微课视频项目。

## 项目定位

这个仓库只保留微课视频生成相关能力：

- 课件/文档导入与结构解析
- 讲稿生成与分页音频合成
- 视觉素材规划与组件化编排
- 视频渲染、字幕生成与质量检查
- HTTP API 封装

仓库中已移除与微课项目无关的示例、比赛材料、展示页面和对外推广信息。

## 目录结构

```text
api/                                FastAPI 接口
pipeline/                           统一编排层
projects/friction_micro_course_demo 公开样例项目
skills/ppt-master/                  底层脚本、模板、参考规则（保留兼容路径）
run.py                              CLI 入口
requirements.txt                    Python 依赖入口
```

## 环境要求

- Python 3.10+
- `ffmpeg` / `ffprobe`
- Windows 下如需本地语音可使用 SAPI
- 可选：
  - LibreOffice 或 Pandoc，用于部分旧格式转换
  - 图片生成 / 图片搜索相关 API Key

安装依赖：

```bash
pip install -r requirements.txt
```

## 命令行使用

直接输入源文件创建新项目：

```bash
python run.py path/to/course.pptx
```

常见参数：

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

可访问：

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/health`

接口说明见 [api/README.md](./api/README.md) 和 [api/API_CONTRACT.md](./api/API_CONTRACT.md)。

## 公开样例

仓库保留了一个完整样例：

- [projects/friction_micro_course_demo](./projects/friction_micro_course_demo)

样例内容：

- 源 PPT：`sources/摩擦力.pptx`
- 规范化 Markdown：`sources/摩擦力.md`
- 素材图片：`sources/摩擦力_files/`
- 生成音频：`audio/`
- 最终视频：`exports/micro_course_style_final.mp4`
- 字幕文件：`exports/micro_course_style_adjusted.srt`

为了适合公开发布，样例中已移除：

- 本地绝对路径状态文件
- 临时渲染目录
- 预览产物
- QA 抽帧
- 与项目无关的历史输出

## 说明

- `skills/ppt-master/` 目录仍保留原有脚本和模板结构，因为运行链路依赖这些文件。
- 内部模块名中仍存在 `ppt-master` 路径，这是当前脚本依赖路径，不影响对外公开使用。
