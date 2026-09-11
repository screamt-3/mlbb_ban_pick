from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .confidence import classify_score
from .config import FineSearchConfig
from .models import (
    Action,
    DraftStage,
    EvidenceFrame,
    FinalFrameSelection,
    FinalSelectionStatus,
    LastPickEvidence,
    Side,
    SlotVisualState,
)
from .vision import DraftFrameAnalysis


@dataclass(frozen=True)
class AnalyzedEvidence:
    frame: EvidenceFrame
    analysis: DraftFrameAnalysis


def _phash_bits(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(gray)[:8, :8]
    return dct > np.median(dct[1:])


def _slot_signature(item: AnalyzedEvidence) -> tuple[np.ndarray, ...]:
    return tuple(_phash_bits(slot.crop) for slot in item.analysis.slots)


def _signatures_equivalent(left: tuple[np.ndarray, ...], right: tuple[np.ndarray, ...]) -> bool:
    if len(left) != len(right):
        return False
    # Broadcast compression and subtle portrait animation move a few DCT bits;
    # an actual slot replacement is substantially larger on this fixture.
    return all(np.count_nonzero(a != b) <= 15 for a, b in zip(left, right, strict=True))


def _is_complete(item: AnalyzedEvidence) -> bool:
    return bool(item.analysis.slots) and all(
        slot.visual_state in {SlotVisualState.HERO_LOCKED, SlotVisualState.SWAPPING}
        for slot in item.analysis.slots
    )


_LOCKED_STATES = {SlotVisualState.HERO_LOCKED, SlotVisualState.SWAPPING}
_STATE_KEY_ORDER = (
    (Side.BLUE, Action.BAN),
    (Side.RED, Action.BAN),
    (Side.BLUE, Action.PICK),
    (Side.RED, Action.PICK),
)


def lock_state_key(item: AnalyzedEvidence) -> tuple[int, int, int, int]:
    return tuple(
        sum(
            slot.visual_state in _LOCKED_STATES
            and Side(slot.config.side) == side
            and Action(slot.config.action) == action
            for slot in item.analysis.slots
        )
        for side, action in _STATE_KEY_ORDER
    )


def _select_state_frames(items, key_function, minimum_run_samples):
    eligible = sorted(
        (
            item
            for item in items
            if item.analysis.detection.possible_draft
            and item.analysis.detection.draft_stage != DraftStage.ADJUST
        ),
        key=lambda item: item.frame.actual_timestamp_seconds
        if item.frame.actual_timestamp_seconds is not None
        else item.frame.requested_timestamp_seconds,
    )
    if not eligible:
        return []
    runs: list[list[AnalyzedEvidence]] = [[eligible[0]]]
    for item in eligible[1:]:
        if key_function(item) == key_function(runs[-1][-1]):
            runs[-1].append(item)
        else:
            runs.append([item])

    selected: list[AnalyzedEvidence] = []
    accepted_key = None
    for run in runs:
        if len(run) < minimum_run_samples:
            continue
        key = key_function(run[-1])
        if accepted_key is not None and key == accepted_key:
            selected[-1] = run[-1]
        elif accepted_key is None or (
            all(current >= previous for current, previous in zip(key, accepted_key, strict=True))
            and sum(key) > sum(accepted_key)
        ):
            selected.append(run[-1])
            accepted_key = key
    return selected


def select_lock_state_frames(
    items: list[AnalyzedEvidence], *, minimum_run_samples: int = 2
) -> list[AnalyzedEvidence]:
    """Return stable, monotonic endpoints where any lock count changes."""
    return _select_state_frames(items, lock_state_key, minimum_run_samples)


def _pick_state_key(item: AnalyzedEvidence) -> tuple[int, int]:
    state = lock_state_key(item)
    return state[2], state[3]


def infer_last_pick_evidence(items: list[AnalyzedEvidence]) -> LastPickEvidence | None:
    # Ban icon occupancy can flicker independently, so final-pick inference must
    # stabilize only the two pick counts. The active PICKING slot becomes
    # colored before lock; these frames delimit the final-pick phase rather than
    # claiming a frame-perfect lock instant.
    frames = _select_state_frames(items, _pick_state_key, 2)
    for before, after in reversed(list(zip(frames, frames[1:]))):
        before_key = lock_state_key(before)
        after_key = lock_state_key(after)
        before_blue, before_red = before_key[2], before_key[3]
        after_blue, after_red = after_key[2], after_key[3]
        if before_blue + before_red != 9 or after_blue + after_red != 10:
            continue
        if after_blue == before_blue + 1 and after_red == before_red:
            side = Side.BLUE
        elif after_red == before_red + 1 and after_blue == before_blue:
            side = Side.RED
        else:
            continue
        return LastPickEvidence(
            side=side,
            before_frame_id=before.frame.frame_id,
            after_frame_id=after.frame.frame_id,
            before_total_picks=9,
            after_total_picks=10,
            before_timestamp_seconds=(
                before.frame.actual_timestamp_seconds
                if before.frame.actual_timestamp_seconds is not None
                else before.frame.requested_timestamp_seconds
            ),
            after_timestamp_seconds=(
                after.frame.actual_timestamp_seconds
                if after.frame.actual_timestamp_seconds is not None
                else after.frame.requested_timestamp_seconds
            ),
        )
    return None


def _stable_runs(items: list[AnalyzedEvidence]) -> list[list[AnalyzedEvidence]]:
    if not items:
        return []
    runs: list[list[AnalyzedEvidence]] = [[items[0]]]
    previous_signature = _slot_signature(items[0])
    for item in items[1:]:
        signature = _slot_signature(item)
        previous = runs[-1][-1]
        chronological = (
            item.frame.actual_timestamp_seconds or item.frame.requested_timestamp_seconds
        ) > (previous.frame.actual_timestamp_seconds or previous.frame.requested_timestamp_seconds)
        if chronological and _signatures_equivalent(previous_signature, signature):
            runs[-1].append(item)
        else:
            runs.append([item])
        previous_signature = signature
    return runs


def select_final_draft_frames(
    frames: list[AnalyzedEvidence], config: FineSearchConfig
) -> FinalFrameSelection:
    ordered = sorted(
        frames,
        key=lambda item: item.frame.actual_timestamp_seconds
        if item.frame.actual_timestamp_seconds is not None
        else item.frame.requested_timestamp_seconds,
    )
    completed = [item for item in ordered if _is_complete(item) and item.analysis.detection.possible_draft]
    swap = [
        item
        for item in completed
        if item.analysis.detection.draft_stage == DraftStage.ADJUST
        and item.analysis.detection.dual_timer_count == 2
    ]
    if not swap:
        return FinalFrameSelection(
            status=FinalSelectionStatus.NOT_FOUND,
            failure_code="SWAP_BOUNDARY_NOT_FOUND" if completed else "FINAL_FRAME_NOT_FOUND",
            alternatives=[item.frame for item in completed[-config.maximum_alternatives :]],
        )

    first_swap = swap[0]
    swap_time = first_swap.frame.actual_timestamp_seconds or first_swap.frame.requested_timestamp_seconds
    pre_swap_candidates = [
        item
        for item in completed
        if (item.frame.actual_timestamp_seconds or item.frame.requested_timestamp_seconds) < swap_time
        and item.analysis.detection.draft_stage != DraftStage.ADJUST
    ]
    pre_swap = pre_swap_candidates[-1] if pre_swap_candidates else None

    swap_runs = [
        run
        for run in _stable_runs(swap)
        if len(run) >= config.required_stability_samples
    ]
    if not swap_runs:
        return FinalFrameSelection(
            status=FinalSelectionStatus.NOT_FOUND,
            pre_swap_locked_frame=pre_swap.frame if pre_swap else None,
            swap_start_frame=first_swap.frame,
            failure_code="NO_STABLE_COMPLETED_DRAFT",
            alternatives=[item.frame for item in swap[-config.maximum_alternatives :]],
        )

    final_run = swap_runs[-1]
    post_swap = final_run[-1]
    first_time = final_run[0].frame.actual_timestamp_seconds or final_run[0].frame.requested_timestamp_seconds
    last_time = post_swap.frame.actual_timestamp_seconds or post_swap.frame.requested_timestamp_seconds
    last_index = ordered.index(post_swap)
    later = ordered[last_index + 1 :]
    consecutive_invalid = 0
    transition_observed = False
    for item in later:
        if not item.analysis.detection.possible_draft:
            consecutive_invalid += 1
            if consecutive_invalid >= config.disappearance_samples:
                transition_observed = True
                break
        else:
            consecutive_invalid = 0

    quality_value = (
        0.50 * post_swap.analysis.detection.score.value
        + 0.35
        + 0.15 * min(1.0, len(final_run) / config.required_stability_samples)
    )
    quality = classify_score(
        metric="final_frame_quality_v1",
        value=quality_value,
        medium_threshold=0.85,
        high_threshold=0.92,
        producer="FinalFrameSelector",
        algorithm_version="dual-timer-stability-v1",
        details={
            "complete": True,
            "stability_samples": len(final_run),
            "transition_observed": transition_observed,
        },
    )
    alternative_items = [item for item in completed if item is not post_swap]
    alternatives = [item.frame for item in alternative_items[-config.maximum_alternatives :]]
    return FinalFrameSelection(
        status=FinalSelectionStatus.SELECTED,
        selected_frame=post_swap.frame,
        pre_swap_locked_frame=pre_swap.frame if pre_swap else None,
        swap_start_frame=first_swap.frame,
        post_swap_final_frame=post_swap.frame,
        quality_score=quality,
        stability_sample_count=len(final_run),
        stability_span_seconds=max(0.0, last_time - first_time),
        transition_observed=transition_observed,
        alternatives=alternatives,
    )
