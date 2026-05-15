# PPT Master API

This API is a thin HTTP wrapper around `pipeline.UnifiedVideoPipeline`.

For frontend integration details, see [API_CONTRACT.md](./API_CONTRACT.md).

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
```

Open:

- `http://127.0.0.1:8000/docs` for Swagger UI
- `http://127.0.0.1:8000/health` for health check

## Create a Job

Upload a source file:

```bash
curl -X POST http://127.0.0.1:8000/api/jobs ^
  -F "file=@inputs/example.pptx" ^
  -F "render=true" ^
  -F "style=adaptive"
```

Reuse an existing project:

```bash
curl -X POST http://127.0.0.1:8000/api/jobs ^
  -F "project_path=C:\Users\魏旭浩\ppt-master\projects\your_project" ^
  -F "render=true"
```

## Poll and Download

```bash
curl http://127.0.0.1:8000/api/jobs/<job_id>
curl http://127.0.0.1:8000/api/jobs/<job_id>/outputs
```

Download final video:

```bash
curl -L -o final.mp4 http://127.0.0.1:8000/api/jobs/<job_id>/download/final_video_path
```
