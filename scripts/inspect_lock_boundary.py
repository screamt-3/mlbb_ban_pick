#!/usr/bin/env python3
"""Inspect stable visual lock-count transitions in the local MSC fixture."""

from __future__ import annotations

import argparse
from pathlib import Path

from mlbb_draft.config import ConfigRegistry
from mlbb_draft.models import EvidenceFrame, ExtractionStatus
from mlbb_draft.pipeline import AnalysisPipeline
from mlbb_draft.sampling import bounded_timestamps
from mlbb_draft.timeline import (
    AnalyzedEvidence,
    infer_last_pick_evidence,
    lock_state_key,
    select_lock_state_frames,
)
from mlbb_draft.video import FFmpegFrameExtractor, LocalFileProvider
from mlbb_draft.vision import LayoutDetector, TemplateRepository


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = ROOT / "fixtures" / "msc-ewc-2026-group-stage-day1" / "draft-1.mp4"
LAYOUT_ID = "msc-ewc-2026-group-stage-en-v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--start", type=float, default=360.0)
    parser.add_argument("--end", type=float, default=408.0)
    parser.add_argument("--cadence", type=float, default=0.5)
    parser.add_argument("--show-all", action="store_true")
    args = parser.parse_args()

    registry = ConfigRegistry(ROOT / "configs")
    layout, layout_ref = registry.load_layout(LAYOUT_ID)
    provider = LocalFileProvider()
    locator = provider.resolve(str(args.video.resolve()))
    asset = provider.materialize(locator, ROOT / ".data" / "boundary-inspection")
    decoded = FFmpegFrameExtractor().extract_many(
        asset, bounded_timestamps(args.start, args.end, args.cadence)
    )
    detector = LayoutDetector(layout, TemplateRepository(ROOT))
    analyzed = []
    for frame in decoded:
        analysis = detector.analyze(frame.pixels)
        evidence = EvidenceFrame(
            frame_id=AnalysisPipeline._frame_id(
                asset.source.video_id, frame.requested_timestamp_seconds
            ),
            source_video_id=asset.source.video_id,
            source_url=asset.source.url,
            requested_timestamp_seconds=frame.requested_timestamp_seconds,
            actual_timestamp_seconds=frame.actual_timestamp_seconds,
            extraction_status=ExtractionStatus.SUCCEEDED,
            detection=analysis.detection,
            layout=layout_ref,
        )
        analyzed.append(AnalyzedEvidence(evidence, analysis))

    if args.show_all:
        for item in analyzed:
            timestamp = item.frame.actual_timestamp_seconds
            print(
                f"raw {timestamp:.3f} possible={item.analysis.detection.possible_draft} "
                f"stage={item.analysis.detection.draft_stage} key={lock_state_key(item)}"
            )
    for item in select_lock_state_frames(analyzed):
        timestamp = item.frame.actual_timestamp_seconds
        print(f"{timestamp:.3f} {lock_state_key(item)}")
    last = infer_last_pick_evidence(analyzed)
    print(last.model_dump_json(indent=2) if last else "no last-pick transition")
    return 0 if last else 1


if __name__ == "__main__":
    raise SystemExit(main())
