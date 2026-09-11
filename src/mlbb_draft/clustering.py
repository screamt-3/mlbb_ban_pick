from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from .models import CandidateDraftRegion, EvidenceFrame, ExtractionStatus


def _detection_time(frame: EvidenceFrame) -> float:
    if frame.actual_timestamp_seconds is not None:
        return frame.actual_timestamp_seconds
    return frame.requested_timestamp_seconds


def positive_coarse_frames(frames: Iterable[EvidenceFrame]) -> list[EvidenceFrame]:
    return sorted(
        (
            frame
            for frame in frames
            if frame.extraction_status == ExtractionStatus.SUCCEEDED
            and frame.detection is not None
            and frame.detection.possible_draft
        ),
        key=_detection_time,
    )


def cluster_positive_detections(
    *,
    video_id: str,
    frames: Iterable[EvidenceFrame],
    duration_seconds: float,
    clustering_distance_seconds: float = 360.0,
    seconds_before: float = 180.0,
    seconds_after: float = 420.0,
) -> list[CandidateDraftRegion]:
    positives = positive_coarse_frames(frames)
    if not positives:
        return []

    groups: list[list[EvidenceFrame]] = [[positives[0]]]
    for frame in positives[1:]:
        if _detection_time(frame) - _detection_time(groups[-1][-1]) <= clustering_distance_seconds:
            groups[-1].append(frame)
        else:
            groups.append([frame])

    regions: list[CandidateDraftRegion] = []
    for group in groups:
        times = [_detection_time(frame) for frame in group]
        identity = f"{video_id}|{'|'.join(f'{value:.6f}' for value in times)}"
        candidate_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        regions.append(
            CandidateDraftRegion(
                candidate_id=candidate_id,
                coarse_frame_ids=[frame.frame_id for frame in group],
                coarse_detection_timestamps=times,
                search_start_seconds=max(0.0, times[0] - seconds_before),
                search_end_seconds=min(duration_seconds, times[-1] + seconds_after),
                clustering_distance_seconds=clustering_distance_seconds,
            )
        )
    return regions


@dataclass(frozen=True)
class PhysicalDecodeInterval:
    start_seconds: float
    end_seconds: float
    candidate_ids: tuple[str, ...]


def merge_overlapping_decode_intervals(
    regions: Iterable[CandidateDraftRegion],
) -> list[PhysicalDecodeInterval]:
    ordered = sorted(regions, key=lambda item: (item.search_start_seconds, item.search_end_seconds))
    if not ordered:
        return []
    merged: list[PhysicalDecodeInterval] = []
    start = ordered[0].search_start_seconds
    end = ordered[0].search_end_seconds
    candidate_ids = [ordered[0].candidate_id]
    for region in ordered[1:]:
        if region.search_start_seconds <= end:
            end = max(end, region.search_end_seconds)
            candidate_ids.append(region.candidate_id)
            continue
        merged.append(PhysicalDecodeInterval(start, end, tuple(candidate_ids)))
        start, end = region.search_start_seconds, region.search_end_seconds
        candidate_ids = [region.candidate_id]
    merged.append(PhysicalDecodeInterval(start, end, tuple(candidate_ids)))
    return merged
