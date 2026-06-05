# friction_micro_course_demo

公开样例项目，主题为“摩擦力”。

## 包含内容

- `sources/摩擦力.pptx`：原始课件
- `sources/摩擦力.md`：规范化后的 Markdown
- `sources/摩擦力_files/`：从课件提取并被 Markdown 引用的图片
- `audio/`：逐页旁白音频
- `images/`：流程中保留的视觉素材
- `exports/micro_course_style_final.mp4`：最终视频
- `exports/micro_course_style_adjusted.srt`：字幕文件
- `preflight_report.json`：预检摘要
- `slide_structure.json`：结构化页面文本
- `component_plan.json`：页面组件选择结果

## 已移除

为适合公开仓库，本样例已移除以下内容：

- 临时渲染目录
- 预览模式输出
- QA 抽帧图片
- 重复导出文件
- 带本地绝对路径的状态清单

## 用途

这个样例主要用于：

- 演示一个完整微课项目目录应该包含什么
- 验证 `run.py --project projects/friction_micro_course_demo` 的复用流程
- 对外说明输入资料、音频、字幕、视频之间的对应关系
