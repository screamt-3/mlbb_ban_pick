#!/usr/bin/env python3
"""Run the complete pipeline against the bounded MSC calibration fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mlbb_draft.artifacts import LocalArtifactStore
from mlbb_draft.jobs import SQLiteJobStore
from mlbb_draft.models import AnalysisCreateRequest
from mlbb_draft.pipeline import AnalysisPipeline
from mlbb_draft.video import LocalFileProvider


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "fixtures" / "msc-ewc-2026-group-stage-day1" / "draft-1.mp4"
DEFAULT_DATA = ROOT / ".data" / "fixture-validation"
LAYOUT_ID = "msc-ewc-2026-group-stage-en-v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    video = args.video.resolve()
    data_dir = args.data_dir.resolve()
    store = SQLiteJobStore(data_dir / "jobs.sqlite3")
    artifacts = LocalArtifactStore(data_dir / "artifacts")
    pipeline = AnalysisPipeline(
        project_root=ROOT,
        runtime_root=data_dir / "work",
        store=store,
        artifacts=artifacts,
        provider=LocalFileProvider(),
    )
    job = store.create(AnalysisCreateRequest(youtube_url=str(video), layout_id=LAYOUT_ID))
    pipeline.run(job.job_id)
    completed = store.get(job.job_id)
    payload = completed.model_dump_json(indent=2)
    if args.output:
        args.output.resolve().write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if completed.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
