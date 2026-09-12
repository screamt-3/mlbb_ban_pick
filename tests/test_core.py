from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from mlbb_draft.api import create_app
from mlbb_draft.artifacts import ArtifactNotFoundError, LocalArtifactStore
from mlbb_draft.clustering import cluster_positive_detections, merge_overlapping_decode_intervals
from mlbb_draft.config import ConfigRegistry
from mlbb_draft.jobs import JobNotFoundError, SQLiteJobStore
from mlbb_draft.models import (
    AnalysisCreateRequest,
    ArtifactRef,
    ConfidenceClass,
    DraftDetection,
    DraftStage,
    EvidenceFrame,
    ExtractionStatus,
    JobStatus,
    LayoutReference,
    ProcessingStage,
    ScoreEvidence,
)
from mlbb_draft.sampling import bounded_timestamps, coarse_timestamps, refinement_timestamps
from mlbb_draft.video import LocalFileProvider, YouTubeProvider


ROOT = Path(__file__).resolve().parents[1]
LAYOUT_ID = "msc-ewc-2026-group-stage-en-v1"


def score(value: float = 0.9) -> ScoreEvidence:
    return ScoreEvidence(
        metric="test",
        value=value,
        threshold=0.8,
        classification=ConfidenceClass.HIGH,
        producer="test",
        algorithm_version="1",
    )


def artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid4().hex,
        uri="/test",
        mime_type="image/png",
        sha256="a" * 64,
        created_at=datetime.now(timezone.utc),
    )


def evidence(timestamp: float, possible: bool = True) -> EvidenceFrame:
    return EvidenceFrame(
        frame_id=f"f-{timestamp}",
        source_video_id="video",
        source_url="https://www.youtube.com/watch?v=Ew_-LE64e4w",
        requested_timestamp_seconds=timestamp,
        actual_timestamp_seconds=timestamp,
        extraction_status=ExtractionStatus.SUCCEEDED,
        artifact=artifact(),
        detection=DraftDetection(
            possible_draft=possible,
            score=score(),
            feature_scores={},
            required_anchors_passed=possible,
            geometry_passed=True,
            draft_stage=DraftStage.PICKING,
        ),
        layout=LayoutReference(layout_id=LAYOUT_ID, version="1", config_sha256="b" * 64),
    )


def test_coarse_timestamps_exact_grid_and_end_exclusive():
    assert coarse_timestamps(1) == [0]
    assert coarse_timestamps(180) == [0]
    assert coarse_timestamps(181) == [0, 180]
    assert coarse_timestamps(540) == [0, 180, 360]


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_coarse_timestamps_reject_invalid(value):
    with pytest.raises(ValueError):
        coarse_timestamps(value)


def test_bounded_timestamps_include_end():
    assert bounded_timestamps(1, 6, 2) == [1, 3, 5, 6]
    assert refinement_timestamps(5, 3, 2, lower_bound=4, upper_bound=7) == [4, 6, 7]


def test_cluster_positive_detections_and_merge():
    frames = [evidence(180), evidence(360), evidence(900), evidence(1080, possible=False)]
    regions = cluster_positive_detections(
        video_id="video",
        frames=frames,
        duration_seconds=1200,
        clustering_distance_seconds=360,
    )
    assert [r.coarse_detection_timestamps for r in regions] == [[180, 360], [900]]
    assert regions[0].search_start_seconds == 0
    assert regions[1].search_end_seconds == 1200
    merged = merge_overlapping_decode_intervals(regions)
    assert len(merged) == 1
    assert set(merged[0].candidate_ids) == {r.candidate_id for r in regions}


def test_config_registry_loads_and_validates_counts():
    registry = ConfigRegistry(ROOT / "configs")
    layout, layout_ref = registry.load_layout(LAYOUT_ID)
    rules, rules_ref = registry.load_rules("msc-ewc-2026-draft-sequence-v1")
    assert len(layout.slots) == 20
    assert sum(phase.count for phase in rules.phases) == 20
    assert layout_ref.config_sha256
    assert rules_ref.validation_status == "provisional"


def test_youtube_provider_normalizes_supported_urls():
    provider = YouTubeProvider()
    assert provider.resolve("https://youtu.be/Ew_-LE64e4w?t=12").video_id == "Ew_-LE64e4w"
    assert (
        provider.resolve("https://www.youtube.com/watch?v=Ew_-LE64e4w&t=14740s").canonical_url
        == "https://www.youtube.com/watch?v=Ew_-LE64e4w"
    )
    with pytest.raises(ValueError):
        provider.resolve("https://example.com/watch?v=Ew_-LE64e4w")


def test_artifact_store_round_trip_and_safe_lookup(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    job_id = uuid4()
    image = np.full((8, 9, 3), 127, dtype=np.uint8)
    ref = store.put_image(job_id, image)
    path, restored = store.resolve(job_id, ref.artifact_id)
    assert restored.sha256 == ref.sha256
    assert cv2.imread(str(path)).shape == image.shape
    with pytest.raises(ArtifactNotFoundError):
        store.resolve(job_id, "missing")


def test_artifact_store_prunes_intermediates(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    job_id = uuid4()
    image = np.full((8, 9, 3), 127, dtype=np.uint8)
    kept = store.put_image(job_id, image)
    removed = store.put_image(job_id, image)
    assert store.retain_only(job_id, {kept.artifact_id}) == 1
    assert store.resolve(job_id, kept.artifact_id)
    with pytest.raises(ArtifactNotFoundError):
        store.resolve(job_id, removed.artifact_id)


def test_local_file_provider_is_fixture_only_adapter(tmp_path: Path):
    path = tmp_path / "fixture.mp4"
    path.write_bytes(b"not-probed-in-this-test")
    locator = LocalFileProvider().resolve(str(path))
    assert locator.provider == "local"
    assert locator.canonical_url == path.as_uri()


def test_sqlite_job_lifecycle_and_recovery(tmp_path: Path):
    store = SQLiteJobStore(tmp_path / "jobs.sqlite3")
    request = AnalysisCreateRequest(
        youtube_url="https://www.youtube.com/watch?v=Ew_-LE64e4w", layout_id=LAYOUT_ID
    )
    queued = store.create(request)
    assert store.get(queued.job_id).status == JobStatus.QUEUED
    running = store.update_stage(queued.job_id, ProcessingStage.PROBING)
    assert running.status == JobStatus.RUNNING
    assert store.recover_interrupted() == 1
    failed = store.get(queued.job_id)
    assert failed.status == JobStatus.FAILED
    assert failed.error.code == "PROCESS_RESTARTED"
    with pytest.raises(JobNotFoundError):
        store.get(uuid4())


def test_api_rejects_bad_url_and_unknown_layout(tmp_path: Path):
    app = create_app(project_root=ROOT, data_root=tmp_path, run_jobs_inline=False)
    with TestClient(app) as client:
        bad_url = client.post("/analyses", json={"youtube_url": "no", "layout_id": LAYOUT_ID})
        assert bad_url.status_code == 422
        missing_layout = client.post(
            "/analyses",
            json={
                "youtube_url": "https://www.youtube.com/watch?v=Ew_-LE64e4w",
                "layout_id": "missing",
            },
        )
        assert missing_layout.status_code == 422
        assert client.get("/healthz").json() == {"status": "ok"}


def test_ui_serves_reviewed_drafts(tmp_path: Path):
    app = create_app(project_root=ROOT, data_root=tmp_path, run_jobs_inline=False)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "MLBB Draft Board" in page.text
        drafts = client.get("/ui/drafts.json")
        assert drafts.status_code == 200
        payload = json.loads(drafts.text)
        assert len(payload["drafts"]) == 8
        assert payload["drafts"][0]["last_pick"]["hero"] == "Bruno"
        rules, _ = ConfigRegistry(ROOT / "configs").load_rules(
            "msc-ewc-2026-draft-sequence-v1"
        )
        displayed_sequence = payload["draft_sequence"]
        assert displayed_sequence["rules_id"] == rules.rules_id
        assert [
            (phase["ordinal"], phase["side"], phase["action"], phase["count"])
            for phase in displayed_sequence["phases"]
        ] == [
            (phase.ordinal, phase.side, phase.action, phase.count)
            for phase in rules.phases
        ]


def test_evidence_frame_allows_ephemeral_success_without_retained_artifact():
    frame = EvidenceFrame(
        frame_id="ephemeral",
        source_video_id="video",
        source_url="https://www.youtube.com/watch?v=Ew_-LE64e4w",
        requested_timestamp_seconds=0,
        extraction_status=ExtractionStatus.SUCCEEDED,
        layout=LayoutReference(layout_id=LAYOUT_ID, version="1", config_sha256="b" * 64),
    )
    assert frame.artifact is None
