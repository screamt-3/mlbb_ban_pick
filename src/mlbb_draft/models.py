from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProcessingStage(str, Enum):
    QUEUED = "queued"
    VALIDATING_SOURCE = "validating_source"
    ACQUIRING = "acquiring"
    PROBING = "probing"
    COARSE_SAMPLING = "coarse_sampling"
    COARSE_DETECTION = "coarse_detection"
    CLUSTERING = "clustering"
    FINE_SEARCH = "fine_search"
    LAYOUT_PARSING = "layout_parsing"
    RECOGNITION = "recognition"
    RECONSTRUCTION = "reconstruction"
    VALIDATION = "validation"
    FINALIZING = "finalizing"


class ConfidenceClass(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class RecognitionStatus(str, Enum):
    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"
    UNVERIFIED = "unverified"


class Side(str, Enum):
    BLUE = "blue"
    RED = "red"


class Action(str, Enum):
    PICK = "pick"
    BAN = "ban"


class SlotVisualState(str, Enum):
    EMPTY = "empty"
    PLAYER_PLACEHOLDER = "player_placeholder"
    HERO_PREVIEW = "hero_preview"
    HERO_LOCK_ANIMATION = "hero_lock_animation"
    HERO_LOCKED = "hero_locked"
    SWAPPING = "swapping"
    UNKNOWN = "unknown"


class DraftStage(str, Enum):
    NON_DRAFT = "non_draft"
    BANNING = "banning"
    PICKING = "picking"
    ADJUST = "adjust"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


class ExtractionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FinalSelectionStatus(str, Enum):
    SELECTED = "selected"
    NOT_FOUND = "not_found"


class ScoreEvidence(StrictModel):
    metric: str
    value: float = Field(ge=0.0, le=1.0)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    classification: ConfidenceClass
    producer: str
    algorithm_version: str
    calibration_fixture_version: str | None = None
    details: dict[str, float | int | str | bool | None] = Field(default_factory=dict)


class ArtifactRef(StrictModel):
    artifact_id: str
    uri: str
    mime_type: str
    sha256: str
    created_at: datetime
    retained_until: datetime | None = None


class LayoutReference(StrictModel):
    layout_id: str
    version: str
    config_sha256: str


class RulesReference(StrictModel):
    rules_id: str
    version: str
    config_sha256: str
    provenance: str
    validation_status: Literal["validated", "provisional"]


class AnalysisCreateRequest(StrictModel):
    youtube_url: str = Field(min_length=1)
    layout_id: str = Field(min_length=1)


class AnalysisAccepted(StrictModel):
    job_id: UUID
    status: Literal["queued"] = "queued"


class VideoSource(StrictModel):
    provider: str
    url: str
    video_id: str
    title: str | None = None
    duration_seconds: float = Field(gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)


class FrameExtractionError(StrictModel):
    code: str
    message: str
    retryable: bool = False


class DraftDetection(StrictModel):
    possible_draft: bool
    score: ScoreEvidence
    feature_scores: dict[str, float]
    required_anchors_passed: bool
    geometry_passed: bool
    draft_stage: DraftStage = DraftStage.UNKNOWN
    dual_timer_count: int = Field(default=0, ge=0, le=2)
    replay_suspected: bool = False
    occluded: bool = False
    reasons: list[str] = Field(default_factory=list)


class EvidenceFrame(StrictModel):
    frame_id: str
    source_video_id: str
    source_url: str
    requested_timestamp_seconds: float = Field(ge=0)
    actual_timestamp_seconds: float | None = Field(default=None, ge=0)
    extraction_status: ExtractionStatus
    artifact: ArtifactRef | None = None
    extraction_error: FrameExtractionError | None = None
    detection: DraftDetection | None = None
    layout: LayoutReference

    @model_validator(mode="after")
    def validate_extraction_payload(self) -> "EvidenceFrame":
        if self.extraction_status == ExtractionStatus.FAILED and self.extraction_error is None:
            raise ValueError("failed extraction requires an extraction_error")
        if self.extraction_status == ExtractionStatus.FAILED and self.artifact is not None:
            raise ValueError("failed extraction cannot retain an artifact")
        return self


class CandidateDraftRegion(StrictModel):
    candidate_id: str
    coarse_frame_ids: list[str]
    coarse_detection_timestamps: list[float]
    search_start_seconds: float = Field(ge=0)
    search_end_seconds: float = Field(gt=0)
    clustering_distance_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_region(self) -> "CandidateDraftRegion":
        if self.search_end_seconds <= self.search_start_seconds:
            raise ValueError("candidate search end must be after start")
        return self


class HeroAlternative(StrictModel):
    hero_id: str
    display_name: str
    score: float = Field(ge=0, le=1)


class HeroRecognition(StrictModel):
    hero_id: str | None = None
    display_name: str | None = None
    score: ScoreEvidence
    status: RecognitionStatus
    catalog_version: str
    reference_asset_id: str | None = None
    alternatives: list[HeroAlternative] = Field(default_factory=list)

    @model_validator(mode="after")
    def unknown_has_no_identity(self) -> "HeroRecognition":
        if self.status == RecognitionStatus.UNKNOWN and self.hero_id is not None:
            raise ValueError("unknown hero recognition cannot expose a confirmed hero_id")
        return self


class TeamAlternative(StrictModel):
    team_id: str
    canonical_name: str
    score: float = Field(ge=0, le=1)


class TeamRecognition(StrictModel):
    side: Side
    recognition_method: Literal["ocr", "logo_reference"]
    raw_text: str | None = None
    normalized_text: str | None = None
    canonical_team_id: str | None = None
    canonical_name: str | None = None
    primary_score: ScoreEvidence
    runner_up_margin: float | None = Field(default=None, ge=0, le=1)
    status: RecognitionStatus
    logo_reference_id: str | None = None
    alternatives: list[TeamAlternative] = Field(default_factory=list)


class ObservedDraftSlot(StrictModel):
    slot_id: str
    side: Side
    action: Action
    visible_order: int = Field(ge=1)
    visual_state: SlotVisualState
    occupancy_score: ScoreEvidence
    crop_artifact: ArtifactRef | None = None
    hero: HeroRecognition | None = None


class DraftStateSnapshot(StrictModel):
    frame: EvidenceFrame
    draft_stage: DraftStage
    dual_timer_count: int = Field(ge=0, le=2)
    slots: list[ObservedDraftSlot]


class SwapValidation(StrictModel):
    pre_swap_frame_id: str
    swap_start_frame_id: str
    post_swap_frame_id: str
    swap_start_dual_timer_confirmed: bool
    blue_pick_recognition_complete: bool
    red_pick_recognition_complete: bool
    blue_ban_recognition_complete: bool
    red_ban_recognition_complete: bool
    blue_pick_pre_timeline_completed: bool = False
    red_pick_pre_timeline_completed: bool = False
    blue_pick_multiset_matches: bool
    red_pick_multiset_matches: bool
    blue_bans_match: bool
    red_bans_match: bool
    cross_side_move_detected: bool
    valid: bool


class FinalFrameSelection(StrictModel):
    status: FinalSelectionStatus
    selected_frame: EvidenceFrame | None = None
    pre_swap_locked_frame: EvidenceFrame | None = None
    swap_start_frame: EvidenceFrame | None = None
    post_swap_final_frame: EvidenceFrame | None = None
    quality_score: ScoreEvidence | None = None
    stability_sample_count: int = Field(default=0, ge=0)
    stability_span_seconds: float = Field(default=0, ge=0)
    transition_observed: bool = False
    alternatives: list[EvidenceFrame] = Field(default_factory=list)
    failure_code: str | None = None


class ObservedFinalDraft(StrictModel):
    blue_team: TeamRecognition
    red_team: TeamRecognition
    blue_bans: list[ObservedDraftSlot]
    red_bans: list[ObservedDraftSlot]
    blue_picks: list[ObservedDraftSlot]
    red_picks: list[ObservedDraftSlot]
    swap_validation: SwapValidation | None = None


class DraftLockEvent(StrictModel):
    event_index: int = Field(ge=1)
    phase_id: str | None = None
    side: Side
    action: Action
    heroes: list[str]
    exact_internal_order_known: bool
    before_frame_id: str
    after_frame_id: str
    score: ScoreEvidence


class LastPickEvidence(StrictModel):
    side: Side
    before_frame_id: str
    after_frame_id: str
    before_total_picks: int = Field(ge=0)
    after_total_picks: int = Field(ge=0)
    before_timestamp_seconds: float | None = Field(default=None, ge=0)
    after_timestamp_seconds: float | None = Field(default=None, ge=0)
    detection_method: Literal[
        "visual_lock_count_v1", "visual_final_pick_phase_v1"
    ] = "visual_final_pick_phase_v1"


class ReconstructedPhase(StrictModel):
    phase_id: str
    ordinal: int = Field(ge=1)
    side: Side
    action: Action
    heroes: list[str]
    evidence_slot_ids: list[str]
    evidence_event_indexes: list[int] = Field(default_factory=list)
    phase_membership_known: bool
    exact_internal_order_known: bool


class DraftReconstruction(StrictModel):
    rules: RulesReference
    phases: list[ReconstructedPhase]
    lock_events: list[DraftLockEvent] = Field(default_factory=list)
    last_pick_side: Side | None = None
    last_picked_hero: str | None = None
    last_pick_evidence: LastPickEvidence | None = None
    unassigned_observed_slot_ids: list[str] = Field(default_factory=list)


class ReviewReason(StrictModel):
    code: str
    message: str
    severity: Literal["warning", "error"]
    stage: ProcessingStage
    frame_id: str | None = None
    slot_id: str | None = None


class ConfidenceSummary(StrictModel):
    policy_version: Literal["minimum-component-v1"] = "minimum-component-v1"
    components: list[ScoreEvidence]
    overall_classification: ConfidenceClass


class GameEvidence(StrictModel):
    coarse_detection_timestamps: list[float]
    final_frame: ArtifactRef
    pre_swap_frame: ArtifactRef | None = None
    swap_start_frame: ArtifactRef | None = None
    alternative_frames: list[ArtifactRef] = Field(default_factory=list)
    duplicate_candidate_ids: list[str] = Field(default_factory=list)


class GameDraftResult(StrictModel):
    game_index: int = Field(ge=1)
    result_status: Literal["complete", "partial"]
    candidate_ids: list[str]
    draft_timestamp_seconds: float = Field(ge=0)
    evidence: GameEvidence
    observed_draft: ObservedFinalDraft
    reconstruction: DraftReconstruction
    confidence: ConfidenceSummary
    requires_review: bool
    review_reasons: list[ReviewReason] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class UnresolvedCandidate(StrictModel):
    candidate: CandidateDraftRegion
    final_selection: FinalFrameSelection
    requires_review: Literal[True] = True
    review_reasons: list[ReviewReason]


class VideoAnalysisResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    source: VideoSource
    layout: LayoutReference
    draft_rules: RulesReference
    hero_catalog_version: str
    games: list[GameDraftResult]
    unresolved_candidates: list[UnresolvedCandidate] = Field(default_factory=list)
    requires_review: bool
    warnings: list[str] = Field(default_factory=list)


class ProcessingError(StrictModel):
    code: str
    stage: ProcessingStage
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class AnalysisJob(StrictModel):
    job_id: UUID
    request: AnalysisCreateRequest
    status: JobStatus
    stage: ProcessingStage
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    source: VideoSource | None = None
    result: VideoAnalysisResult | None = None
    error: ProcessingError | None = None
