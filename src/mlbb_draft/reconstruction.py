from __future__ import annotations

from collections import Counter

from .confidence import classify_score
from .config import DraftRulesConfig
from .models import (
    Action,
    DraftLockEvent,
    DraftReconstruction,
    DraftStateSnapshot,
    LastPickEvidence,
    RecognitionStatus,
    ReconstructedPhase,
    RulesReference,
    Side,
    SlotVisualState,
    SwapValidation,
)


def _hero_sets(snapshot: DraftStateSnapshot) -> dict[tuple[Side, Action], set[str]]:
    result: dict[tuple[Side, Action], set[str]] = {
        (Side.BLUE, Action.PICK): set(),
        (Side.RED, Action.PICK): set(),
        (Side.BLUE, Action.BAN): set(),
        (Side.RED, Action.BAN): set(),
    }
    for slot in snapshot.slots:
        if (
            slot.visual_state in {SlotVisualState.HERO_LOCKED, SlotVisualState.SWAPPING}
            and slot.hero is not None
            and slot.hero.status == RecognitionStatus.CONFIRMED
            and slot.hero.hero_id is not None
        ):
            result[(Side(slot.side), Action(slot.action))].add(slot.hero.hero_id)
    return result


def derive_lock_events(snapshots: list[DraftStateSnapshot]) -> list[DraftLockEvent]:
    ordered = sorted(snapshots, key=lambda item: item.frame.actual_timestamp_seconds or item.frame.requested_timestamp_seconds)
    events: list[DraftLockEvent] = []
    for before, after in zip(ordered, ordered[1:]):
        if after.dual_timer_count == 2:
            break
        before_sets = _hero_sets(before)
        after_sets = _hero_sets(after)
        for (side, action), after_heroes in after_sets.items():
            added = sorted(after_heroes - before_sets[(side, action)])
            if not added:
                continue
            score = classify_score(
                metric="draft_lock_event_v1",
                value=min(
                    slot.hero.score.value
                    for slot in after.slots
                    if slot.hero is not None and slot.hero.hero_id in added
                ),
                medium_threshold=0.80,
                high_threshold=0.92,
                producer="DraftTimeline",
                algorithm_version="set-difference-v1",
                details={"hero_count": len(added)},
            )
            events.append(
                DraftLockEvent(
                    event_index=len(events) + 1,
                    side=side,
                    action=action,
                    heroes=added,
                    exact_internal_order_known=len(added) == 1,
                    before_frame_id=before.frame.frame_id,
                    after_frame_id=after.frame.frame_id,
                    score=score,
                )
            )
    return events


def align_events_to_rules(
    events: list[DraftLockEvent],
    rules: DraftRulesConfig,
    rules_ref: RulesReference,
) -> DraftReconstruction:
    phases: list[ReconstructedPhase] = []
    event_index = 0
    for phase in sorted(rules.phases, key=lambda item: item.ordinal):
        heroes: list[str] = []
        evidence_indexes: list[int] = []
        while event_index < len(events) and len(heroes) < phase.count:
            event = events[event_index]
            if Side(event.side) != Side(phase.side) or Action(event.action) != Action(phase.action):
                break
            remaining = phase.count - len(heroes)
            if len(event.heroes) > remaining:
                break
            heroes.extend(event.heroes)
            evidence_indexes.append(event.event_index)
            event_index += 1
        membership_known = len(heroes) == phase.count
        exact_order = membership_known and (
            phase.exact_internal_order_encoded
            or phase.count == 1
            or all(events[index - 1].exact_internal_order_known for index in evidence_indexes)
        )
        phases.append(
            ReconstructedPhase(
                phase_id=phase.phase_id,
                ordinal=phase.ordinal,
                side=phase.side,
                action=phase.action,
                heroes=heroes,
                evidence_slot_ids=[],
                evidence_event_indexes=evidence_indexes,
                phase_membership_known=membership_known,
                exact_internal_order_known=exact_order,
            )
        )

    last_pick_event = next((event for event in reversed(events) if event.action == Action.PICK), None)
    last_pick_side = Side(last_pick_event.side) if last_pick_event else None
    last_hero = last_pick_event.heroes[-1] if last_pick_event and len(last_pick_event.heroes) == 1 else None
    return DraftReconstruction(
        rules=rules_ref,
        phases=phases,
        lock_events=events,
        last_pick_side=last_pick_side,
        last_picked_hero=last_hero,
    )


def reconstruct_from_final_screen(
    final_snapshot: DraftStateSnapshot,
    events: list[DraftLockEvent],
    rules: DraftRulesConfig,
    rules_ref: RulesReference,
    last_pick_evidence: LastPickEvidence | None = None,
    slot_order_semantics: str = "player_order",
) -> DraftReconstruction:
    """Reconstruct only phase membership justified by slot semantics or lock evidence.

    Player-ordered slots prove final per-side sets, not chronological phases.
    Phase-ordered layouts may map visible positions directly. Individual lock
    events can establish otherwise-unobservable phase membership.
    """
    slot_groups: dict[tuple[Side, Action], list] = {}
    for side in (Side.BLUE, Side.RED):
        for action in (Action.PICK, Action.BAN):
            slot_groups[(side, action)] = sorted(
                [
                    slot
                    for slot in final_snapshot.slots
                    if Side(slot.side) == side and Action(slot.action) == action
                ],
                key=lambda slot: slot.visible_order,
            )

    if slot_order_semantics not in {"phase_order", "player_order"}:
        raise ValueError(f"unsupported slot order semantics: {slot_order_semantics}")
    offsets = {key: 0 for key in slot_groups}
    phases: list[ReconstructedPhase] = []
    for phase in sorted(rules.phases, key=lambda item: item.ordinal):
        key = (Side(phase.side), Action(phase.action))
        selected_slots = []
        if slot_order_semantics == "phase_order":
            start = offsets[key]
            selected_slots = slot_groups[key][start : start + phase.count]
            offsets[key] += phase.count
        confirmed = [
            slot
            for slot in selected_slots
            if slot.hero is not None
            and slot.hero.status == RecognitionStatus.CONFIRMED
            and slot.hero.hero_id is not None
        ]
        phases.append(
            ReconstructedPhase(
                phase_id=phase.phase_id,
                ordinal=phase.ordinal,
                side=phase.side,
                action=phase.action,
                heroes=[slot.hero.hero_id for slot in confirmed],
                evidence_slot_ids=[slot.slot_id for slot in selected_slots],
                phase_membership_known=len(confirmed) == phase.count,
                exact_internal_order_known=bool(
                    len(confirmed) == phase.count
                    and (phase.count == 1 or phase.exact_internal_order_encoded)
                ),
            )
        )

    # Hero identity lets us attach observed lock events without inventing an
    # alignment when earlier fine samples are missing.
    for event in events:
        event_heroes = set(event.heroes)
        candidates = [
            phase
            for phase in phases
            if Side(phase.side) == Side(event.side)
            and Action(phase.action) == Action(event.action)
            and event_heroes
            and event_heroes.issubset(set(phase.heroes))
        ]
        if len(candidates) == 1:
            phase = candidates[0]
            phase.evidence_event_indexes.append(event.event_index)
            event.phase_id = phase.phase_id

    for phase in phases:
        linked = [event for event in events if event.event_index in phase.evidence_event_indexes]
        observed_order = [hero for event in linked for hero in event.heroes]
        if (
            phase.phase_membership_known
            and len(observed_order) == len(phase.heroes)
            and set(observed_order) == set(phase.heroes)
            and all(event.exact_internal_order_known for event in linked)
        ):
            phase.heroes = observed_order
            phase.exact_internal_order_known = True

    last_pick_event = next((event for event in reversed(events) if event.action == Action.PICK), None)
    last_pick_side = (
        Side(last_pick_evidence.side)
        if last_pick_evidence is not None
        else Side(last_pick_event.side) if last_pick_event else None
    )
    last_pick_hero = None
    if (
        last_pick_event is not None
        and len(last_pick_event.heroes) == 1
        and Side(last_pick_event.side) == last_pick_side
    ):
        last_pick_hero = last_pick_event.heroes[0]
        expected_last_phase = next(
            phase for phase in reversed(phases) if Action(phase.action) == Action.PICK
        )
        if (
            Side(expected_last_phase.side) == last_pick_side
            and not expected_last_phase.heroes
        ):
            matching_slots = [
                slot
                for slot in final_snapshot.slots
                if slot.hero is not None and slot.hero.hero_id == last_pick_hero
            ]
            expected_last_phase.heroes = [last_pick_hero]
            expected_last_phase.evidence_slot_ids = [slot.slot_id for slot in matching_slots]
            expected_last_phase.evidence_event_indexes = [last_pick_event.event_index]
            expected_last_phase.phase_membership_known = True
            expected_last_phase.exact_internal_order_known = True
            last_pick_event.phase_id = expected_last_phase.phase_id
    observed_slots = {slot.slot_id for slot in final_snapshot.slots}
    assigned_slots = {slot_id for phase in phases for slot_id in phase.evidence_slot_ids}
    return DraftReconstruction(
        rules=rules_ref,
        phases=phases,
        lock_events=events,
        last_pick_side=last_pick_side,
        last_picked_hero=last_pick_hero,
        last_pick_evidence=last_pick_evidence,
        unassigned_observed_slot_ids=sorted(observed_slots - assigned_slots),
    )


def derive_last_pick_event(
    before: DraftStateSnapshot,
    final: DraftStateSnapshot,
    evidence: LastPickEvidence,
    *,
    expected_side_picks: int = 5,
) -> DraftLockEvent | None:
    side = Side(evidence.side)
    before_heroes = _hero_sets(before)[(side, Action.PICK)]
    final_heroes = _hero_sets(final)[(side, Action.PICK)]
    added = final_heroes - before_heroes
    if (
        len(final_heroes) != expected_side_picks
        or len(before_heroes) != expected_side_picks - 1
        or not before_heroes.issubset(final_heroes)
        or len(added) != 1
    ):
        return None
    hero_id = next(iter(added))
    matched_slot = next(
        (
            slot
            for slot in final.slots
            if Side(slot.side) == side
            and Action(slot.action) == Action.PICK
            and slot.hero is not None
            and slot.hero.hero_id == hero_id
        ),
        None,
    )
    if matched_slot is None or matched_slot.hero is None:
        return None
    return DraftLockEvent(
        event_index=1,
        side=side,
        action=Action.PICK,
        heroes=[hero_id],
        exact_internal_order_known=True,
        before_frame_id=evidence.before_frame_id,
        after_frame_id=evidence.after_frame_id,
        score=matched_slot.hero.score,
    )


def validate_swap(
    pre_swap: DraftStateSnapshot,
    swap_start: DraftStateSnapshot,
    post_swap: DraftStateSnapshot,
    *,
    expected_per_category: int = 5,
    pre_pick_overrides: dict[Side, set[str]] | None = None,
) -> SwapValidation:
    pre = _hero_sets(pre_swap)
    post = _hero_sets(post_swap)
    pre_pick_overrides = pre_pick_overrides or {}
    for side, heroes in pre_pick_overrides.items():
        pre[(Side(side), Action.PICK)] = set(heroes)

    def complete(side: Side, action: Action) -> bool:
        return (
            len(pre[(side, action)]) == expected_per_category
            and len(post[(side, action)]) == expected_per_category
        )

    blue_picks_complete = complete(Side.BLUE, Action.PICK)
    red_picks_complete = complete(Side.RED, Action.PICK)
    blue_bans_complete = complete(Side.BLUE, Action.BAN)
    red_bans_complete = complete(Side.RED, Action.BAN)
    blue_picks = blue_picks_complete and Counter(pre[(Side.BLUE, Action.PICK)]) == Counter(
        post[(Side.BLUE, Action.PICK)]
    )
    red_picks = red_picks_complete and Counter(pre[(Side.RED, Action.PICK)]) == Counter(
        post[(Side.RED, Action.PICK)]
    )
    blue_bans = blue_bans_complete and Counter(pre[(Side.BLUE, Action.BAN)]) == Counter(
        post[(Side.BLUE, Action.BAN)]
    )
    red_bans = red_bans_complete and Counter(pre[(Side.RED, Action.BAN)]) == Counter(
        post[(Side.RED, Action.BAN)]
    )
    cross_side = bool(
        pre[(Side.BLUE, Action.PICK)] & post[(Side.RED, Action.PICK)]
        or pre[(Side.RED, Action.PICK)] & post[(Side.BLUE, Action.PICK)]
    )
    dual_timer = swap_start.dual_timer_count == 2
    valid = dual_timer and blue_picks and red_picks and blue_bans and red_bans and not cross_side
    return SwapValidation(
        pre_swap_frame_id=pre_swap.frame.frame_id,
        swap_start_frame_id=swap_start.frame.frame_id,
        post_swap_frame_id=post_swap.frame.frame_id,
        swap_start_dual_timer_confirmed=dual_timer,
        blue_pick_recognition_complete=blue_picks_complete,
        red_pick_recognition_complete=red_picks_complete,
        blue_ban_recognition_complete=blue_bans_complete,
        red_ban_recognition_complete=red_bans_complete,
        blue_pick_pre_timeline_completed=Side.BLUE in pre_pick_overrides,
        red_pick_pre_timeline_completed=Side.RED in pre_pick_overrides,
        blue_pick_multiset_matches=blue_picks,
        red_pick_multiset_matches=red_picks,
        blue_bans_match=blue_bans,
        red_bans_match=red_bans,
        cross_side_move_detected=cross_side,
        valid=valid,
    )
