from __future__ import annotations

from .models import ConfidenceClass, ConfidenceSummary, ReviewReason, ScoreEvidence


_RANK = {
    ConfidenceClass.UNKNOWN: 0,
    ConfidenceClass.LOW: 1,
    ConfidenceClass.MEDIUM: 2,
    ConfidenceClass.HIGH: 3,
}


_UNKNOWN_REVIEW_CODES = {
    "FINAL_FRAME_NOT_FOUND",
    "REQUIRED_SLOT_MISSING",
    "LAYOUT_VALIDATION_FAILED",
    "SIDE_ATTRIBUTION_UNCERTAIN",
}


def summarize_confidence(
    components: list[ScoreEvidence], review_reasons: list[ReviewReason]
) -> ConfidenceSummary:
    if not components:
        overall = ConfidenceClass.UNKNOWN
    else:
        overall = min(
            (ConfidenceClass(component.classification) for component in components),
            key=lambda value: _RANK[value],
        )
    codes = {reason.code for reason in review_reasons}
    if codes & _UNKNOWN_REVIEW_CODES:
        overall = ConfidenceClass.UNKNOWN
    elif any(reason.severity == "error" for reason in review_reasons) and _RANK[overall] > _RANK[ConfidenceClass.LOW]:
        overall = ConfidenceClass.LOW
    return ConfidenceSummary(components=components, overall_classification=overall)


def classify_score(
    *,
    metric: str,
    value: float,
    medium_threshold: float,
    high_threshold: float,
    producer: str,
    algorithm_version: str,
    calibration_fixture_version: str | None = None,
    details: dict | None = None,
) -> ScoreEvidence:
    if value >= high_threshold:
        classification = ConfidenceClass.HIGH
    elif value >= medium_threshold:
        classification = ConfidenceClass.MEDIUM
    elif value > 0:
        classification = ConfidenceClass.LOW
    else:
        classification = ConfidenceClass.UNKNOWN
    return ScoreEvidence(
        metric=metric,
        value=max(0.0, min(1.0, value)),
        threshold=medium_threshold,
        classification=classification,
        producer=producer,
        algorithm_version=algorithm_version,
        calibration_fixture_version=calibration_fixture_version,
        details=details or {},
    )
