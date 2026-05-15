# PPT-to-Video Director Agent

`video_director_agent.py` is the structured multi-role entry point for turning
PPT content into a component-based video plan and, optionally, a rendered MP4.

## Roles

The director runs these roles in order:

1. `ParserAgent`: reads or creates `slide_structure.json`, merges clean source
   Markdown text, and writes `slide_ir.json`.
2. `ScriptWriterAgent`: estimates per-slide narration duration and writes
   `video_script_plan.json`.
3. `NarrationAudioAgent`: generates missing per-slide narration audio from the
   script plan with local Windows TTS when rendering is requested.
4. `ComponentSelectorAgent`: chooses component blocks from the video component
   library and writes `component_plan.json` plus `component_recommendations.json`.
5. `AssetCuratorAgent`: reuses existing visual assets or runs
   `visual_asset_planner.py` when requested, then writes `asset_plan.json`.
6. `LayoutComposerAgent`: combines the previous role outputs into
   `render_plan.json`.
7. `RendererAgent`: calls `ppt_to_video.py --style adaptive` when `--render` is
   set.
8. `QAAgent`: checks output files, asset availability, component variety,
   duplicate subtitles, and optional QA frames.

The full role state is written to `video_agent_state.json`.

## Commands

Plan only:

```bash
python3 scripts/video_director_agent.py <project_path>
```

Plan and render using existing assets:

```bash
python3 scripts/video_director_agent.py <project_path> --render
```

Allow image search/generation, then render:

```bash
python3 scripts/video_director_agent.py <project_path> --execute-assets --render
```

Render without generating local narration audio:

```bash
python3 scripts/video_director_agent.py <project_path> --render --no-generate-audio
```

Inject a model-selected component JSON:

```bash
python3 scripts/video_director_agent.py <project_path> --llm-output model_components.json --render
```

## Output Files

- `slide_ir.json`: parsed PPT text, hierarchy, layout signals, and selected image references.
- `video_script_plan.json`: per-slide script/duration/subtitle chunks.
- `component_plan.json`: selected component blocks and alternatives.
- `asset_plan.json`: visual asset decision and selected image per slide.
- `render_plan.json`: final structured plan consumed by rendering.
- `video_qa_report.json`: automated checks and QA frame paths.
- `video_agent_state.json`: director state and role outputs.

## Design Rules

- Roles communicate through JSON, not free-form chat.
- The component selector must choose from the registered component library.
- Rendering is deterministic; it does not ask a model during ffmpeg generation.
- Missing or weak images are handled by the asset role before rendering.
- QA runs after planning and after rendering when `--render` is enabled.
