from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from .models import (
    AnalysisCreateRequest,
    AnalysisJob,
    JobStatus,
    ProcessingError,
    ProcessingStage,
    VideoAnalysisResult,
    VideoSource,
)


class JobNotFoundError(KeyError):
    pass


class SQLiteJobStore:
    """Small durable job store with atomic whole-document updates."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS analysis_jobs_status_idx ON analysis_jobs(status)"
            )

    def _save(self, job: AnalysisJob) -> AnalysisJob:
        status_value = job.status.value if isinstance(job.status, Enum) else job.status
        stage_value = job.stage.value if isinstance(job.stage, Enum) else job.stage
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_jobs(job_id, status, stage, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    stage = excluded.stage,
                    created_at = excluded.created_at,
                    payload_json = excluded.payload_json
                """,
                (
                    str(job.job_id),
                    status_value,
                    stage_value,
                    job.created_at.isoformat(),
                    job.model_dump_json(),
                ),
            )
        return job

    def create(self, request: AnalysisCreateRequest) -> AnalysisJob:
        now = datetime.now(timezone.utc)
        return self._save(
            AnalysisJob(
                job_id=uuid4(),
                request=request,
                status=JobStatus.QUEUED,
                stage=ProcessingStage.QUEUED,
                created_at=now,
            )
        )

    def get(self, job_id: UUID | str) -> AnalysisJob:
        try:
            normalized = str(UUID(str(job_id)))
        except ValueError as exc:
            raise JobNotFoundError(str(job_id)) from exc
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM analysis_jobs WHERE job_id = ?", (normalized,)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(normalized)
        return AnalysisJob.model_validate_json(row[0])

    def update_stage(
        self,
        job_id: UUID | str,
        stage: ProcessingStage,
        *,
        source: VideoSource | None = None,
    ) -> AnalysisJob:
        job = self.get(job_id)
        now = datetime.now(timezone.utc)
        updates: dict = {
            "status": JobStatus.RUNNING,
            "stage": stage,
            "started_at": job.started_at or now,
        }
        if source is not None:
            updates["source"] = source
        return self._save(job.model_copy(update=updates))

    def succeed(self, job_id: UUID | str, result: VideoAnalysisResult) -> AnalysisJob:
        job = self.get(job_id)
        return self._save(
            job.model_copy(
                update={
                    "status": JobStatus.SUCCEEDED,
                    "stage": ProcessingStage.FINALIZING,
                    "completed_at": datetime.now(timezone.utc),
                    "source": result.source,
                    "result": result,
                    "error": None,
                }
            )
        )

    def fail(self, job_id: UUID | str, error: ProcessingError) -> AnalysisJob:
        job = self.get(job_id)
        return self._save(
            job.model_copy(
                update={
                    "status": JobStatus.FAILED,
                    "stage": error.stage,
                    "completed_at": datetime.now(timezone.utc),
                    "error": error,
                }
            )
        )

    def recover_interrupted(self) -> int:
        """Fail jobs that cannot safely resume after a process restart."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM analysis_jobs WHERE status IN (?, ?)",
                (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
            ).fetchall()
        for (payload,) in rows:
            job = AnalysisJob.model_validate_json(payload)
            self.fail(
                job.job_id,
                ProcessingError(
                    code="PROCESS_RESTARTED",
                    stage=job.stage,
                    message="The local process stopped before this analysis completed; submit it again.",
                    retryable=True,
                ),
            )
        return len(rows)


class JobRunner:
    def __init__(self, worker: Callable[[UUID], None], max_workers: int = 1):
        self._worker = worker
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mlbb-draft")
        self._lock = threading.Lock()
        self._futures: dict[UUID, Future[None]] = {}

    def submit(self, job_id: UUID) -> None:
        with self._lock:
            current = self._futures.get(job_id)
            if current is not None and not current.done():
                return
            future = self._executor.submit(self._worker, job_id)
            self._futures[job_id] = future
            future.add_done_callback(lambda _: self._forget(job_id))

    def _forget(self, job_id: UUID) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)
