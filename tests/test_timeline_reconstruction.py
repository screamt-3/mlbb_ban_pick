from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np

from mlbb_draft.config import FineSearchConfig, Rect, SlotConfig
from mlbb_draft.models import (
    Action,
    ArtifactRef,
    ConfidenceClass,
    DraftDetection,
    DraftStage,
    DraftStateSnapshot,
    EvidenceFrame,
    ExtractionStatus,
    HeroRecognition,
    LastPickEvidence,
    LayoutReference,
    ObservedDraftSlot,
    RecognitionStatus,
    ScoreEvidence,
    Side,
    SlotVisualState,
    FinalSelectionStatus,
)
from mlbb_draft.config import ConfigRegistry
from mlbb_draft.reconstruction import (
    derive_last_pick_event,
    reconstruct_from_final_screen,
    validate_swap,
)
from mlbb_draft.timeline import (
    AnalyzedEvidence,
    infer_last_pick_evidence,
    select_final_draft_frames,
    select_lock_state_frames,
)
from mlbb_draft.vision import DraftFrameAnalysis, FrameTransform, NormalizedFrame, SlotObservation


LAYOUT = LayoutReference(layout_id="test", version="1", config_sha256="a" * 64)


def _score(value: float = 0.99) -> ScoreEvidence:
    return ScoreEvidence(
        metric="test",
        value=value,
        threshold=0.8,
        classification=ConfidenceClass.HIGH,
        producer="test",
        algorithm_version="1",
    )


def _frame(timestamp: float, *, dual_timers: int = 0) -> EvidenceFrame:
    return EvidenceFrame(
        frame_id=f"frame-{timestamp}",
        source_video_id="fixture",
        source_url="file:///fixture.mp4",
        requested_timestamp_seconds=timestamp,
        actual_timestamp_seconds=timestamp,
        extraction_status=ExtractionStatus.SUCCEEDED,
        artifact=ArtifactRef(
            artifact_id=uuid4().hex,
            uri="/artifact",
            mime_type="image/png",
            sha256="b" * 64,
            created_at=datetime.now(timezone.utc),
        ),
        detection=DraftDetection(
            possible_draft=True,
            score=_score(),
            feature_scores={},
            required_anchors_passed=True,
            geometry_passed=True,
            draft_stage=DraftStage.ADJUST if dual_timers == 2 else DraftStage.UNKNOWN,
            dual_timer_count=dual_timers,
        ),
        layout=LAYOUT,
    )


def _hero(hero_id: str) -> HeroRecognition:
    return HeroRecognition(
        hero_id=hero_id,
        display_name=hero_id,
        score=_score(),
        status=RecognitionStatus.CONFIRMED,
        catalog_version="test",
    )


def _snapshot(timestamp: float, *, dual_timers: int, include_bans: bool = True) -> DraftStateSnapshot:
    slots: list[ObservedDraftSlot] = []
    for side in (Side.BLUE, Side.RED):
        for action in (Action.PICK, Action.BAN):
            for index in range(1, 6):
                hero_id = (
                    f"{side.value}-{action.value}-{index}"
                    if include_bans or action == Action.PICK
                    else None
                )
                slots.append(
                    ObservedDraftSlot(
                        slot_id=f"{side.value}_{action.value}_{index}",
                        side=side,
                        action=action,
                        visible_order=index,
                        visual_state=SlotVisualState.HERO_LOCKED,
                        occupancy_score=_score(),
                        hero=_hero(hero_id) if hero_id else None,
                    )
                )
    return DraftStateSnapshot(
        frame=_frame(timestamp, dual_timers=dual_timers),
        draft_stage=DraftStage.ADJUST if dual_timers == 2 else DraftStage.UNKNOWN,
        dual_timer_count=dual_timers,
        slots=slots,
    )


def _analysis(timestamp: float, counts: tuple[int, int, int, int]) -> AnalyzedEvidence:
    # blue bans, red bans, blue picks, red picks
    frame = _frame(timestamp)
    pairs = (
        (Side.BLUE, Action.BAN, counts[0]),
        (Side.RED, Action.BAN, counts[1]),
        (Side.BLUE, Action.PICK, counts[2]),
        (Side.RED, Action.PICK, counts[3]),
    )
    observations: list[SlotObservation] = []
    for side, action, locked_count in pairs:
        for index in range(1, 6):
            config = SlotConfig(
                slot_id=f"{side.value}_{action.value}_{index}",
                side=side,
                action=action,
                visible_order=index,
                roi=Rect(x=0, y=0, width=2, height=2),
            )
            observations.append(
                SlotObservation(
                    config=config,
                    visual_state=(
                        SlotVisualState.HERO_LOCKED
                        if index <= locked_count
                        else SlotVisualState.PLAYER_PLACEHOLDER
                    ),
                    occupancy_score=1.0 if index <= locked_count else 0.0,
                    placeholder_score=None,
                    crop=np.full((2, 2, 3), index, dtype=np.uint8),
                )
            )
    pixels = np.zeros((4, 4, 3), dtype=np.uint8)
    analysis = DraftFrameAnalysis(
        normalized=NormalizedFrame(
            pixels=pixels,
            transform=FrameTransform(4, 4, 0, 0, 4, 4, 4, 4),
        ),
        detection=frame.detection,
        slots=tuple(observations),
        timer_values=(None, None),
    )
    return AnalyzedEvidence(frame=frame, analysis=analysis)


def _detection_state(
    item: AnalyzedEvidence, *, stage: DraftStage, dual_timers: int, possible: bool
) -> AnalyzedEvidence:
    detection = item.analysis.detection.model_copy(
        update={
            "possible_draft": possible,
            "draft_stage": stage,
            "dual_timer_count": dual_timers,
        }
    )
    frame = item.frame.model_copy(update={"detection": detection})
    analysis = DraftFrameAnalysis(
        normalized=item.analysis.normalized,
        detection=detection,
        slots=item.analysis.slots,
        timer_values=item.analysis.timer_values,
    )
    return AnalyzedEvidence(frame, analysis)


def test_swap_validation_requires_complete_identity_sets_and_dual_timer():
    pre = _snapshot(1, dual_timers=0)
    swap = _snapshot(2, dual_timers=2)
    post = _snapshot(3, dual_timers=2)
    valid = validate_swap(pre, swap, post)
    assert valid.valid
    assert valid.swap_start_dual_timer_confirmed
    assert valid.blue_pick_multiset_matches

    missing_bans = validate_swap(
        _snapshot(1, dual_timers=0, include_bans=False),
        swap,
        _snapshot(3, dual_timers=2, include_bans=False),
    )
    assert not missing_bans.valid
    assert missing_bans.blue_pick_multiset_matches
    assert not missing_bans.blue_ban_recognition_complete
    assert not missing_bans.blue_bans_match

    no_timer = validate_swap(pre, _snapshot(2, dual_timers=0), post)
    assert not no_timer.valid
    assert not no_timer.swap_start_dual_timer_confirmed


def test_stable_visual_9_to_10_transition_identifies_last_pick_side():
    frames = [
        _analysis(1.0, (5, 5, 4, 5)),
        _analysis(1.5, (5, 5, 4, 5)),
        _analysis(1.75, (5, 5, 3, 5)),
        _analysis(2.0, (5, 5, 4, 5)),
        _analysis(2.5, (5, 5, 4, 5)),
        _analysis(3.0, (5, 5, 5, 5)),
        _analysis(3.5, (5, 5, 5, 5)),
    ]
    selected = select_lock_state_frames(frames)
    assert len(selected) == 2
    evidence = infer_last_pick_evidence(frames)
    assert evidence is not None
    assert evidence.side == Side.BLUE
    assert evidence.before_total_picks == 9
    assert evidence.after_total_picks == 10
    assert evidence.before_timestamp_seconds == 2.5
    assert evidence.after_timestamp_seconds == 3.5
    assert evidence.detection_method == "visual_final_pick_phase_v1"


def test_player_order_does_not_invent_phase_membership_and_last_lock_is_proven():
    final = _snapshot(3, dual_timers=2)
    before = final.model_copy(
        update={
            "frame": _frame(2),
            "dual_timer_count": 0,
            "slots": [
                slot.model_copy(update={"hero": None})
                if slot.side == Side.BLUE
                and slot.action == Action.PICK
                and slot.visible_order == 5
                else slot
                for slot in final.slots
            ],
        }
    )
    evidence = LastPickEvidence(
        side=Side.BLUE,
        before_frame_id=before.frame.frame_id,
        after_frame_id=final.frame.frame_id,
        before_total_picks=9,
        after_total_picks=10,
        before_timestamp_seconds=2,
        after_timestamp_seconds=3,
    )
    event = derive_last_pick_event(before, final, evidence)
    assert event is not None
    assert event.heroes == ["blue-pick-5"]

    registry = ConfigRegistry(Path(__file__).resolve().parents[1] / "configs")
    rules, rules_ref = registry.load_rules("msc-ewc-2026-draft-sequence-v1")
    reconstruction = reconstruct_from_final_screen(
        final,
        [event],
        rules,
        rules_ref,
        last_pick_evidence=evidence,
        slot_order_semantics="player_order",
    )
    last_phase = reconstruction.phases[-1]
    assert last_phase.phase_id == "blue_pick_4"
    assert last_phase.heroes == ["blue-pick-5"]
    assert last_phase.exact_internal_order_known
    assert all(not phase.heroes for phase in reconstruction.phases[:-1])
    assert len(reconstruction.unassigned_observed_slot_ids) == 19


def test_final_selector_requires_stable_dual_timer_run_and_observes_transition():
    complete = (5, 5, 5, 5)
    frames = [
        _detection_state(
            _analysis(1, complete), stage=DraftStage.UNKNOWN, dual_timers=0, possible=True
        ),
        *[
            _detection_state(
                _analysis(timestamp, complete),
                stage=DraftStage.ADJUST,
                dual_timers=2,
                possible=True,
            )
            for timestamp in (2, 3, 4)
        ],
        *[
            _detection_state(
                _analysis(timestamp, complete),
                stage=DraftStage.NON_DRAFT,
                dual_timers=0,
                possible=False,
            )
            for timestamp in (5, 6)
        ],
    ]
    selection = select_final_draft_frames(frames, FineSearchConfig())
    assert selection.status == FinalSelectionStatus.SELECTED
    assert selection.pre_swap_locked_frame.frame_id == "frame-1"
    assert selection.swap_start_frame.frame_id == "frame-2"
    assert selection.selected_frame.frame_id == "frame-4"
    assert selection.stability_sample_count == 3
    assert selection.transition_observed

    no_timer = select_final_draft_frames(frames[:1], FineSearchConfig())
    assert no_timer.status == FinalSelectionStatus.NOT_FOUND
    assert no_timer.failure_code == "SWAP_BOUNDARY_NOT_FOUND"
