# 微课视频 API

本目录提供一个基于 FastAPI 的本地接口层，用于提交微课视频生成任务、轮询进度和下载产物。

## 启动

```bash
pip install -r requirements.txt
python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
```

启动后可访问：

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/health`
- `GET /api/stages`

## 创建任务

上传源文件：

```bash
curl -X POST http://127.0.0.1:8000/api/jobs ^
  -F "file=@inputs/demo.pptx" ^
  -F "render=true" ^
  -F "style=micro-course"
```

复用已有项目：

```bash
curl -X POST http://127.0.0.1:8000/api/jobs ^
  -F "project_path=C:\path\to\projects\friction_micro_course_demo" ^
  -F "render=true"
```

## 查询任务

```bash
curl http://127.0.0.1:8000/api/jobs/<job_id>
curl http://127.0.0.1:8000/api/jobs/<job_id>/outputs
```

下载最终视频：

```bash
curl -L -o final.mp4 http://127.0.0.1:8000/api/jobs/<job_id>/download/final_video_path
```

完整字段定义见 [API_CONTRACT.md](./API_CONTRACT.md)。
