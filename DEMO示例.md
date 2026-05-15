# PPT Master 微课视频生成 - Demo示例

## 演示场景

将一份教学PPT自动转换为带讲解旁白的微课视频。

## 演示步骤

### 1. 准备工作

```bash
# 确保环境已安装
pip install edge-tts Pillow cairosvg
# 确保 ffmpeg 已安装
ffmpeg -version
```

### 2. 创建演示项目

```bash
# 创建项目
python3 skills/ppt-master/scripts/project_manager.py init demo_course --format ppt169

# 导入示例PPT（替换为你的文件）
python3 skills/ppt-master/scripts/project_manager.py import-sources demo_course your_lesson.pptx
```

### 3. 生成PPT内容

按照 `skills/ppt-master/SKILL.md` 执行完整流程，生成SVG幻灯片和讲解脚本。

### 4. 一键生成视频

```bash
# 方式1：分步执行
python3 skills/ppt-master/scripts/notes_to_audio.py demo_course \
  --voice zh-CN-XiaoxiaoNeural

python3 skills/ppt-master/scripts/pptx_to_video.py demo_course \
  --resolution 1080p \
  --subtitle

# 方式2：一键生成（推荐）
python3 skills/ppt-master/scripts/svg_to_pptx.py demo_course \
  --recorded-narration audio \
  --export-video \
  --video-resolution 1080p
```

### 5. 查看结果

```bash
# 视频位置
demo_course/exports/demo_course.mp4

# 字幕位置
demo_course/exports/demo_course.srt

# 在播放器中打开
# Windows: start demo_course/exports/demo_course.mp4
# macOS: open demo_course/exports/demo_course.mp4
# Linux: xdg-open demo_course/exports/demo_course.mp4
```

## 高级Demo：带背景音乐

```bash
# 准备背景音乐文件 bgm.mp3

python3 skills/ppt-master/scripts/pptx_to_video.py demo_course \
  --resolution 1080p \
  --bgm bgm.mp3 \
  --bgm-volume 0.15 \
  --subtitle \
  --fps 30
```

## 预期输出

- **视频时长**：根据讲解内容自动计算
- **视频分辨率**：1920x1080 (1080p)
- **音频质量**：AAC 192kbps
- **文件大小**：约 5-10MB/分钟（H.264编码）

## 性能基准

| 幻灯片数 | 音频生成 | 视频渲染 | 总耗时 |
|---------|---------|---------|--------|
| 10页    | ~30秒   | ~45秒   | ~1分15秒|
| 30页    | ~1分30秒| ~2分钟  | ~3分30秒|

*测试环境：Intel i7, 16GB RAM*
