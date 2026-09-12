from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .artifacts import ArtifactNotFoundError, LocalArtifactStore
from .config import ConfigRegistry
from .jobs import JobNotFoundError, JobRunner, SQLiteJobStore
from .models import AnalysisAccepted, AnalysisCreateRequest, AnalysisJob
from .pipeline import AnalysisPipeline
from .video import YouTubeProvider


def create_app(
    *,
    project_root: Path | None = None,
    data_root: Path | None = None,
    pipeline: AnalysisPipeline | None = None,
    run_jobs_inline: bool = False,
) -> FastAPI:
    project_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    data_root = (data_root or Path(os.getenv("MLBB_DRAFT_DATA_DIR", project_root / ".data"))).resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    store = pipeline.store if pipeline is not None else SQLiteJobStore(data_root / "jobs.sqlite3")
    artifacts = pipeline.artifacts if pipeline is not None else LocalArtifactStore(data_root / "artifacts")
    pipeline = pipeline or AnalysisPipeline(
        project_root=project_root,
        runtime_root=data_root / "work",
        store=store,
        artifacts=artifacts,
    )
    runner = None if run_jobs_inline else JobRunner(pipeline.run, max_workers=1)
    registry = ConfigRegistry(project_root / "configs")
    provider = YouTubeProvider()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        store.recover_interrupted()
        artifacts.sweep_expired()
        yield
        if runner is not None:
            runner.shutdown(wait=False)

    app = FastAPI(
        title="MLBB Draft Extractor",
        version="0.1.0",
        description=(
            "Layout-specific draft extraction. Coarse sampling is fixed at 0, 180, 360, ... "
            "seconds and can miss drafts wholly between samples."
        ),
        lifespan=lifespan,
    )
    app.state.job_store = store
    app.state.artifact_store = artifacts
    app.state.pipeline = pipeline
    app.state.runner = runner

    ui_root = project_root / "dist"
    if ui_root.is_dir():
        app.mount("/ui", StaticFiles(directory=ui_root, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        def open_ui() -> RedirectResponse:
            return RedirectResponse(url="/ui/")

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/analyses",
        response_model=AnalysisAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_analysis(request: AnalysisCreateRequest) -> AnalysisAccepted:
        try:
            registry.load_layout(request.layout_id)
            provider.resolve(request.youtube_url)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = store.create(request)
        if run_jobs_inline:
            pipeline.run(job.job_id)
        else:
            runner.submit(job.job_id)
        return AnalysisAccepted(job_id=job.job_id)

    @app.get("/analyses/{job_id}", response_model=AnalysisJob)
    def read_analysis(job_id: UUID) -> AnalysisJob:
        try:
            return store.get(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="analysis not found") from exc

    @app.get("/analyses/{job_id}/artifacts/{artifact_id}")
    def read_artifact(job_id: UUID, artifact_id: str) -> FileResponse:
        try:
            path, ref = artifacts.resolve(job_id, artifact_id)
        except (ArtifactNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        return FileResponse(path, media_type=ref.mime_type, filename=path.name)

    return app


app = create_app()
