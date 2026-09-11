from __future__ import annotations

import hashlib
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .artifacts import LocalArtifactStore
from .clustering import cluster_positive_detections, merge_overlapping_decode_intervals
from .confidence import summarize_confidence
from .config import ConfigRegistry, LayoutConfig, Rect
from .jobs import SQLiteJobStore
from .models import (
    Action,
    DraftStateSnapshot,
    EvidenceFrame,
    ExtractionStatus,
    FinalSelectionStatus,
    GameDraftResult,
    GameEvidence,
    ObservedDraftSlot,
    ObservedFinalDraft,
    ProcessingError,
    ProcessingStage,
    RecognitionStatus,
    ReviewReason,
    Side,
    SlotVisualState,
    UnresolvedCandidate,
    VideoAnalysisResult,
)
from .recognition import HeroRecognizer, TeamRecognizer
from .reconstruction import derive_last_pick_event, reconstruct_from_final_screen, validate_swap
from .sampling import bounded_timestamps, coarse_timestamps, refinement_timestamps
from .timeline import (
    AnalyzedEvidence,
    infer_last_pick_evidence,
    lock_state_key,
    select_final_draft_frames,
)
from .video import FFmpegFrameExtractor, VideoAsset, VideoProvider, YouTubeProvider
from .vision import (
    DraftFrameAnalysis,
    LayoutCompatibilityError,
    LayoutDetector,
    TemplateRepository,
    crop_rect,
    occupancy_score_evidence,
    recognize_text,
)


DEFAULT_RULES_ID = "msc-ewc-2026-draft-sequence-v1"
DEFAULT_HERO_CATALOG_ID = "msc-ewc-2026-heroes-v1"
DEFAULT_TEAM_CATALOG_ID = "msc-ewc-2026-teams-v1"


@dataclass(frozen=True)
class _FrameRecord:
    evidence: EvidenceFrame
    analysis: DraftFrameAnalysis | None


class AnalysisPipeline:
    """End-to-end, layout-specific MVP pipeline.

    Dependencies are injectable so tests can use an in-memory/synthetic video
    provider while production uses yt-dlp and FFmpeg.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        runtime_root: Path,
        store: SQLiteJobStore,
        artifacts: LocalArtifactStore,
        provider: VideoProvider | None = None,
        extractor: FFmpegFrameExtractor | None = None,
    ):
        self.project_root = project_root.resolve()
        self.runtime_root = runtime_root.resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.artifacts = artifacts
        self.provider = provider or YouTubeProvider()
        self.extractor = extractor
        self.registry = ConfigRegistry(self.project_root / "configs")

    def _stage(self, job_id: UUID, stage: ProcessingStage, *, source=None) -> None:
        self.store.update_stage(job_id, stage, source=source)

    @staticmethod
    def _frame_id(video_id: str, timestamp: float) -> str:
        key = f"{video_id}|{timestamp:.6f}".encode()
        return hashlib.sha256(key).hexdigest()[:24]

    @staticmethod
    def _artifact_ids(value) -> set[str]:
        payload = value.model_dump(mode="json")
        found: set[str] = set()

        def visit(item) -> None:
            if isinstance(item, dict):
                artifact_id = item.get("artifact_id")
                if isinstance(artifact_id, str):
                    found.add(artifact_id)
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(payload)
        return found

    def _extract(
        self,
        *,
        job_id: UUID,
        asset: VideoAsset,
        timestamp: float,
        detector: LayoutDetector,
        layout_ref,
    ) -> _FrameRecord:
        frame_id = self._frame_id(asset.source.video_id, timestamp)
        try:
            decoded = self._get_extractor().extract_at(asset, timestamp)
            analysis = detector.analyze(decoded.pixels)
            evidence = EvidenceFrame(
                frame_id=frame_id,
                source_video_id=asset.source.video_id,
                source_url=asset.source.url,
                requested_timestamp_seconds=timestamp,
                actual_timestamp_seconds=decoded.actual_timestamp_seconds,
                extraction_status=ExtractionStatus.SUCCEEDED,
                detection=analysis.detection,
                layout=layout_ref,
            )
            return _FrameRecord(evidence, analysis)
        except (LayoutCompatibilityError, ValueError, RuntimeError) as exc:
            from .models import FrameExtractionError

            evidence = EvidenceFrame(
                frame_id=frame_id,
                source_video_id=asset.source.video_id,
                source_url=asset.source.url,
                requested_timestamp_seconds=timestamp,
                extraction_status=ExtractionStatus.FAILED,
                extraction_error=FrameExtractionError(
                    code="FRAME_ANALYSIS_FAILED",
                    message=str(exc),
                    retryable=False,
                ),
                layout=layout_ref,
            )
            return _FrameRecord(evidence, None)

    def _get_extractor(self):
        if self.extractor is None:
            self.extractor = FFmpegFrameExtractor()
        return self.extractor

    @staticmethod
    def _deduplicate_timestamps(values: list[float], duration: float) -> list[float]:
        return sorted({round(value, 3) for value in values if 0 <= value < duration})

    def _extract_series(
        self,
        *,
        job_id: UUID,
        asset: VideoAsset,
        timestamps: list[float],
        detector: LayoutDetector,
        layout_ref,
        cache: dict[float, _FrameRecord],
    ) -> list[_FrameRecord]:
        normalized_times = self._deduplicate_timestamps(timestamps, asset.source.duration_seconds)
        missing = [timestamp for timestamp in normalized_times if timestamp not in cache]
        extractor = self._get_extractor()
        if missing and hasattr(extractor, "extract_many"):
            try:
                decoded_batch = extractor.extract_many(asset, missing)
            except (ValueError, RuntimeError):
                decoded_batch = []
            for decoded in decoded_batch:
                frame_id = self._frame_id(asset.source.video_id, decoded.requested_timestamp_seconds)
                try:
                    analysis = detector.analyze(decoded.pixels)
                    evidence = EvidenceFrame(
                        frame_id=frame_id,
                        source_video_id=asset.source.video_id,
                        source_url=asset.source.url,
                        requested_timestamp_seconds=decoded.requested_timestamp_seconds,
                        actual_timestamp_seconds=decoded.actual_timestamp_seconds,
                        extraction_status=ExtractionStatus.SUCCEEDED,
                        detection=analysis.detection,
                        layout=layout_ref,
                    )
                    cache[decoded.requested_timestamp_seconds] = _FrameRecord(evidence, analysis)
                except (LayoutCompatibilityError, ValueError, RuntimeError):
                    # Retry through the per-frame path below so the caller gets
                    # a structured extraction failure rather than losing the timestamp.
                    continue
        records: list[_FrameRecord] = []
        for timestamp in normalized_times:
            record = cache.get(timestamp)
            if record is None:
                record = self._extract(
                    job_id=job_id,
                    asset=asset,
                    timestamp=timestamp,
                    detector=detector,
                    layout_ref=layout_ref,
                )
                cache[timestamp] = record
            records.append(record)
        return records

    def _retain_selection_frames(self, job_id: UUID, analyzed: list[AnalyzedEvidence], selection):
        lookup = self._by_frame_id(analyzed)
        requested_frames = [
            selection.selected_frame,
            selection.pre_swap_locked_frame,
            selection.swap_start_frame,
            selection.post_swap_final_frame,
            *selection.alternatives,
        ]
        retained: dict[str, EvidenceFrame] = {}
        for frame in requested_frames:
            if frame is None or frame.frame_id in retained:
                continue
            item = lookup.get(frame.frame_id)
            if item is None:
                continue
            artifact = frame.artifact or self.artifacts.put_image(
                job_id, item.analysis.normalized.pixels
            )
            retained[frame.frame_id] = frame.model_copy(update={"artifact": artifact})

        def retained_frame(frame):
            return retained.get(frame.frame_id, frame) if frame is not None else None

        updated_selection = selection.model_copy(
            update={
                "selected_frame": retained_frame(selection.selected_frame),
                "pre_swap_locked_frame": retained_frame(selection.pre_swap_locked_frame),
                "swap_start_frame": retained_frame(selection.swap_start_frame),
                "post_swap_final_frame": retained_frame(selection.post_swap_final_frame),
                "alternatives": [retained_frame(frame) for frame in selection.alternatives],
            }
        )
        updated_analyzed = [
            AnalyzedEvidence(retained.get(item.frame.frame_id, item.frame), item.analysis)
            for item in analyzed
        ]
        return updated_selection, updated_analyzed

    @staticmethod
    def _analyzed(records: list[_FrameRecord]) -> list[AnalyzedEvidence]:
        return [
            AnalyzedEvidence(record.evidence, record.analysis)
            for record in records
            if record.analysis is not None
        ]

    def _recognize_snapshot(
        self,
        *,
        job_id: UUID,
        item: AnalyzedEvidence,
        hero_recognizer: HeroRecognizer,
        persist_crops: bool,
        recognize_categories: set[tuple[Side, Action]] | None = None,
    ) -> DraftStateSnapshot:
        slots: list[ObservedDraftSlot] = []
        for observation in item.analysis.slots:
            crop_artifact = self.artifacts.put_image(job_id, observation.crop) if persist_crops else None
            hero = None
            if observation.visual_state in {
                SlotVisualState.HERO_LOCKED,
                SlotVisualState.HERO_PREVIEW,
                SlotVisualState.HERO_LOCK_ANIMATION,
                SlotVisualState.SWAPPING,
            } and (
                recognize_categories is None
                or (Side(observation.config.side), Action(observation.config.action))
                in recognize_categories
            ):
                recognition_crop = observation.crop
                supporting_text = None
                if observation.config.hero_name_roi is not None:
                    recognition_crop = observation.crop[: int(observation.crop.shape[0] * 0.78)]
                    label_crop = crop_rect(
                        item.analysis.normalized.pixels,
                        observation.config.hero_name_roi,
                    )
                    text_six, _ = recognize_text(label_crop, psm=6)
                    text_eight, _ = recognize_text(label_crop, psm=8)
                    supporting_text = f"{text_six} {text_eight}".strip()
                hero = hero_recognizer.recognize(recognition_crop, supporting_text=supporting_text)
            slots.append(
                ObservedDraftSlot(
                    slot_id=observation.config.slot_id,
                    side=observation.config.side,
                    action=observation.config.action,
                    visible_order=observation.config.visible_order,
                    visual_state=observation.visual_state,
                    occupancy_score=occupancy_score_evidence(observation.occupancy_score),
                    crop_artifact=crop_artifact,
                    hero=hero,
                )
            )
        return DraftStateSnapshot(
            frame=item.frame,
            draft_stage=item.analysis.detection.draft_stage,
            dual_timer_count=item.analysis.detection.dual_timer_count,
            slots=slots,
        )

    @staticmethod
    def _by_frame_id(items: list[AnalyzedEvidence]) -> dict[str, AnalyzedEvidence]:
        return {item.frame.frame_id: item for item in items}

    @staticmethod
    def _side_slots(snapshot: DraftStateSnapshot, side: Side, action: Action) -> list[ObservedDraftSlot]:
        return sorted(
            [slot for slot in snapshot.slots if slot.side == side and slot.action == action],
            key=lambda slot: slot.visible_order,
        )

    @staticmethod
    def _fallback_team(side: Side, method: str, team_recognizer: TeamRecognizer, crop):
        return (
            team_recognizer.recognize_logo(side, crop)
            if method == "logo_reference"
            else team_recognizer.recognize_ocr(side, crop)
        )

    def _review_reasons(
        self,
        final: ObservedFinalDraft,
        *,
        transition_observed: bool,
        last_pick_side: Side | None,
        expected_last_side: Side,
    ) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for team in (final.blue_team, final.red_team):
            if team.status != RecognitionStatus.CONFIRMED:
                reasons.append(
                    ReviewReason(
                        code="TEAM_UNRECOGNIZED",
                        message=f"{team.side} team identity is not confirmed.",
                        severity="warning",
                        stage=ProcessingStage.RECOGNITION,
                    )
                )
        for slot in [*final.blue_bans, *final.red_bans, *final.blue_picks, *final.red_picks]:
            if slot.hero is None or slot.hero.status != RecognitionStatus.CONFIRMED:
                reasons.append(
                    ReviewReason(
                        code="HERO_UNRECOGNIZED",
                        message=f"Hero in {slot.slot_id} is not confirmed.",
                        severity="warning",
                        stage=ProcessingStage.RECOGNITION,
                        slot_id=slot.slot_id,
                    )
                )
        if final.swap_validation is None:
            reasons.append(
                ReviewReason(
                    code="SWAP_VALIDATION_UNAVAILABLE",
                    message="Pre-swap, swap-start, and post-swap snapshots were not all available.",
                    severity="warning",
                    stage=ProcessingStage.VALIDATION,
                )
            )
        elif not final.swap_validation.valid:
            identities_complete = all(
                (
                    final.swap_validation.blue_pick_recognition_complete,
                    final.swap_validation.red_pick_recognition_complete,
                    final.swap_validation.blue_ban_recognition_complete,
                    final.swap_validation.red_ban_recognition_complete,
                )
            )
            reasons.append(
                ReviewReason(
                    code=(
                        "SWAP_VALIDATION_FAILED"
                        if identities_complete
                        else "SWAP_VALIDATION_INCOMPLETE"
                    ),
                    message=(
                        "Per-side hero sets changed across the dual-timer boundary."
                        if identities_complete
                        else "The dual-timer boundary was found, but not every pre/post hero identity was confirmed."
                    ),
                    severity="error" if identities_complete else "warning",
                    stage=ProcessingStage.VALIDATION,
                )
            )
        if not transition_observed:
            reasons.append(
                ReviewReason(
                    code="TRANSITION_NOT_OBSERVED",
                    message="The draft HUD disappearance was not observed after the selected frame.",
                    severity="warning",
                    stage=ProcessingStage.FINE_SEARCH,
                )
            )
        if last_pick_side is None:
            reasons.append(
                ReviewReason(
                    code="LAST_PICK_NOT_OBSERVED",
                    message="The final 9-to-10 pick phase was not resolved from sampled visual states.",
                    severity="warning",
                    stage=ProcessingStage.RECONSTRUCTION,
                )
            )
        elif last_pick_side != expected_last_side:
            reasons.append(
                ReviewReason(
                    code="LAST_PICK_SIDE_CONFLICT",
                    message=f"Observed last pick side {last_pick_side} conflicts with configured {expected_last_side}.",
                    severity="error",
                    stage=ProcessingStage.RECONSTRUCTION,
                )
            )
        return reasons

    def _candidate_game(
        self,
        *,
        job_id: UUID,
        game_index: int,
        candidate_ids: list[str],
        coarse_times: list[float],
        analyzed: list[AnalyzedEvidence],
        selection,
        layout: LayoutConfig,
        rules,
        rules_ref,
        hero_recognizer: HeroRecognizer,
        team_recognizer: TeamRecognizer,
    ) -> GameDraftResult:
        selected_lookup = self._by_frame_id(analyzed)
        selected = selected_lookup[selection.selected_frame.frame_id]
        final_snapshot = self._recognize_snapshot(
            job_id=job_id,
            item=selected,
            hero_recognizer=hero_recognizer,
            persist_crops=True,
        )

        snapshot_cache: dict[str, DraftStateSnapshot] = {selected.frame.frame_id: final_snapshot}

        def recognized(frame):
            if frame is None:
                return None
            if frame.frame_id not in snapshot_cache:
                snapshot_cache[frame.frame_id] = self._recognize_snapshot(
                    job_id=job_id,
                    item=selected_lookup[frame.frame_id],
                    hero_recognizer=hero_recognizer,
                    persist_crops=False,
                )
            return snapshot_cache[frame.frame_id]

        pre_snapshot = recognized(selection.pre_swap_locked_frame)
        swap_snapshot = recognized(selection.swap_start_frame)
        post_snapshot = recognized(selection.post_swap_final_frame)
        swap_validation = None

        canonical = selected.analysis.normalized.pixels
        teams = {}
        for region in layout.team_regions:
            teams[Side(region.side)] = self._fallback_team(
                Side(region.side),
                region.recognition_method,
                team_recognizer,
                crop_rect(canonical, region.roi),
            )

        final = ObservedFinalDraft(
            blue_team=teams[Side.BLUE],
            red_team=teams[Side.RED],
            blue_bans=self._side_slots(final_snapshot, Side.BLUE, Action.BAN),
            red_bans=self._side_slots(final_snapshot, Side.RED, Action.BAN),
            blue_picks=self._side_slots(final_snapshot, Side.BLUE, Action.PICK),
            red_picks=self._side_slots(final_snapshot, Side.RED, Action.PICK),
            swap_validation=swap_validation,
        )

        last_pick_evidence = infer_last_pick_evidence(analyzed)
        events = []
        pre_pick_overrides: dict[Side, set[str]] = {}

        def confirmed_pick_ids(snapshot: DraftStateSnapshot, side: Side) -> set[str]:
            return {
                slot.hero.hero_id
                for slot in snapshot.slots
                if Side(slot.side) == side
                and Action(slot.action) == Action.PICK
                and slot.hero is not None
                and slot.hero.status == RecognitionStatus.CONFIRMED
                and slot.hero.hero_id is not None
            }

        def clean_pre_pick_snapshot(
            side: Side, expected_count: int, latest_timestamp: float
        ) -> DraftStateSnapshot | None:
            side_index = 2 if side == Side.BLUE else 3
            candidates = sorted(
                (
                    item
                    for item in analyzed
                    if item.analysis.detection.possible_draft
                    and item.analysis.detection.draft_stage != "adjust"
                    and (
                        item.frame.actual_timestamp_seconds
                        if item.frame.actual_timestamp_seconds is not None
                        else item.frame.requested_timestamp_seconds
                    )
                    <= latest_timestamp
                    and lock_state_key(item)[side_index] == expected_count
                ),
                key=lambda item: item.frame.actual_timestamp_seconds
                if item.frame.actual_timestamp_seconds is not None
                else item.frame.requested_timestamp_seconds,
                reverse=True,
            )
            best_snapshot = None
            best_count = -1
            for item in candidates[:12]:
                snapshot = self._recognize_snapshot(
                    job_id=job_id,
                    item=item,
                    hero_recognizer=hero_recognizer,
                    persist_crops=False,
                    recognize_categories={(side, Action.PICK)},
                )
                count = len(confirmed_pick_ids(snapshot, side))
                if count > best_count:
                    best_snapshot, best_count = snapshot, count
                if count == expected_count:
                    return snapshot
            return best_snapshot if best_count == expected_count else None

        if last_pick_evidence is not None:
            last_side = Side(last_pick_evidence.side)
            expected_side_picks = (
                rules.expected_blue_picks
                if last_side == Side.BLUE
                else rules.expected_red_picks
            )
            before_last_snapshot = clean_pre_pick_snapshot(
                last_side,
                expected_side_picks - 1,
                last_pick_evidence.before_timestamp_seconds,
            )
            if before_last_snapshot is not None:
                event = derive_last_pick_event(
                    before_last_snapshot,
                    final_snapshot,
                    last_pick_evidence,
                    expected_side_picks=expected_side_picks,
                )
                if event is not None:
                    events.append(event)
                    pre_pick_overrides[last_side] = confirmed_pick_ids(
                        before_last_snapshot, last_side
                    ) | set(event.heroes)

        swap_timestamp = (
            selection.swap_start_frame.actual_timestamp_seconds
            if selection.swap_start_frame
            and selection.swap_start_frame.actual_timestamp_seconds is not None
            else selection.swap_start_frame.requested_timestamp_seconds
            if selection.swap_start_frame
            else 0.0
        )
        for side, expected in (
            (Side.BLUE, rules.expected_blue_picks),
            (Side.RED, rules.expected_red_picks),
        ):
            if side in pre_pick_overrides:
                continue
            snapshot = clean_pre_pick_snapshot(side, expected, swap_timestamp)
            if snapshot is not None:
                pre_pick_overrides[side] = confirmed_pick_ids(snapshot, side)

        swap_validation = (
            validate_swap(
                pre_snapshot,
                swap_snapshot,
                post_snapshot,
                pre_pick_overrides=pre_pick_overrides,
            )
            if pre_snapshot is not None and swap_snapshot is not None and post_snapshot is not None
            else None
        )
        final = final.model_copy(update={"swap_validation": swap_validation})
        reconstruction = reconstruct_from_final_screen(
            final_snapshot,
            events,
            rules,
            rules_ref,
            last_pick_evidence=last_pick_evidence,
            slot_order_semantics=layout.pick_slot_order_semantics,
        )
        expected_last = Side(
            next(phase for phase in reversed(rules.phases) if phase.action == Action.PICK).side
        )
        reasons = self._review_reasons(
            final,
            transition_observed=selection.transition_observed,
            last_pick_side=reconstruction.last_pick_side,
            expected_last_side=expected_last,
        )
        if any(not phase.phase_membership_known for phase in reconstruction.phases):
            reasons.append(
                ReviewReason(
                    code="PHASE_MEMBERSHIP_UNRESOLVED",
                    message=(
                        "Post-swap player positions do not encode chronological phase membership; "
                        "only separately observed lock events are assigned."
                    ),
                    severity="warning",
                    stage=ProcessingStage.RECONSTRUCTION,
                )
            )
        reasons.append(
            ReviewReason(
                code="RULES_PROVISIONAL",
                message="The draft sequence is user-supplied because the public rulebook does not enumerate it.",
                severity="warning",
                stage=ProcessingStage.RECONSTRUCTION,
            )
        )
        components = [selection.quality_score]
        components.extend(
            slot.hero.score
            for slot in [*final.blue_bans, *final.red_bans, *final.blue_picks, *final.red_picks]
            if slot.hero is not None
        )
        components.extend([final.blue_team.primary_score, final.red_team.primary_score])
        confidence = summarize_confidence([value for value in components if value is not None], reasons)
        complete = not any(
            reason.code
            in {
                "HERO_UNRECOGNIZED",
                "TEAM_UNRECOGNIZED",
                "SWAP_VALIDATION_FAILED",
                "SWAP_VALIDATION_INCOMPLETE",
                "SWAP_VALIDATION_UNAVAILABLE",
                "LAST_PICK_NOT_OBSERVED",
                "LAST_PICK_SIDE_CONFLICT",
                "PHASE_MEMBERSHIP_UNRESOLVED",
            }
            for reason in reasons
        )
        return GameDraftResult(
            game_index=game_index,
            result_status="complete" if complete else "partial",
            candidate_ids=candidate_ids,
            draft_timestamp_seconds=selection.selected_frame.actual_timestamp_seconds
            or selection.selected_frame.requested_timestamp_seconds,
            evidence=GameEvidence(
                coarse_detection_timestamps=coarse_times,
                final_frame=selection.selected_frame.artifact,
                pre_swap_frame=selection.pre_swap_locked_frame.artifact
                if selection.pre_swap_locked_frame
                else None,
                swap_start_frame=selection.swap_start_frame.artifact if selection.swap_start_frame else None,
                alternative_frames=[frame.artifact for frame in selection.alternatives if frame.artifact],
                duplicate_candidate_ids=candidate_ids[1:],
            ),
            observed_draft=final,
            reconstruction=reconstruction,
            confidence=confidence,
            requires_review=bool(reasons),
            review_reasons=reasons,
            warnings=[
                "Coarse sampling is fixed at 0, 180, 360, ...; drafts wholly between samples are not detectable."
            ],
        )

    def run(self, job_id: UUID) -> None:
        work_dir = self.runtime_root / str(job_id)
        try:
            job = self.store.get(job_id)
            self._stage(job_id, ProcessingStage.VALIDATING_SOURCE)
            layout, layout_ref = self.registry.load_layout(job.request.layout_id)
            rules, rules_ref = self.registry.load_rules(DEFAULT_RULES_ID)
            hero_catalog = self.registry.load_hero_catalog(DEFAULT_HERO_CATALOG_ID)
            team_catalog = self.registry.load_team_catalog(DEFAULT_TEAM_CATALOG_ID)
            locator = self.provider.resolve(job.request.youtube_url)

            self._stage(job_id, ProcessingStage.PROBING)
            source = self.provider.probe(locator)
            self._stage(job_id, ProcessingStage.ACQUIRING, source=source)
            asset = self.provider.materialize(locator, work_dir)
            source = asset.source

            templates = TemplateRepository(self.project_root)
            detector = LayoutDetector(layout, templates)
            hero_recognizer = HeroRecognizer(self.project_root, hero_catalog, layout.hero_recognition)
            team_recognizer = TeamRecognizer(
                self.project_root, team_catalog, layout.hero_recognition, layout.ocr
            )
            cache: dict[float, _FrameRecord] = {}

            self._stage(job_id, ProcessingStage.COARSE_SAMPLING, source=source)
            coarse = self._extract_series(
                job_id=job_id,
                asset=asset,
                timestamps=coarse_timestamps(source.duration_seconds),
                detector=detector,
                layout_ref=layout_ref,
                cache=cache,
            )
            self._stage(job_id, ProcessingStage.COARSE_DETECTION)
            coarse_evidence = [record.evidence for record in coarse]
            self._stage(job_id, ProcessingStage.CLUSTERING)
            candidates = cluster_positive_detections(
                video_id=source.video_id,
                frames=coarse_evidence,
                duration_seconds=source.duration_seconds,
                clustering_distance_seconds=layout.fine_search.clustering_distance_seconds,
                seconds_before=layout.fine_search.seconds_before_first_positive,
                seconds_after=layout.fine_search.seconds_after_last_positive,
            )

            games: list[GameDraftResult] = []
            unresolved: list[UnresolvedCandidate] = []
            self._stage(job_id, ProcessingStage.FINE_SEARCH)
            by_candidate = {candidate.candidate_id: candidate for candidate in candidates}
            for interval in merge_overlapping_decode_intervals(candidates):
                broad_times = bounded_timestamps(
                    interval.start_seconds,
                    min(interval.end_seconds, source.duration_seconds - 0.001),
                    layout.fine_search.cadence_seconds,
                )
                broad = self._extract_series(
                    job_id=job_id,
                    asset=asset,
                    timestamps=broad_times,
                    detector=detector,
                    layout_ref=layout_ref,
                    cache=cache,
                )
                analyzed = self._analyzed(broad)
                adjust = next(
                    (
                        item
                        for item in analyzed
                        if item.analysis.detection.possible_draft
                        and item.analysis.detection.draft_stage == "adjust"
                        and item.analysis.detection.dual_timer_count == 2
                    ),
                    None,
                )
                if adjust is not None:
                    center = adjust.frame.actual_timestamp_seconds or adjust.frame.requested_timestamp_seconds
                    refined_times = refinement_timestamps(
                        center,
                        layout.fine_search.refinement_radius_seconds,
                        layout.fine_search.transition_refinement_cadence_seconds,
                        lower_bound=interval.start_seconds,
                        upper_bound=min(interval.end_seconds, source.duration_seconds - 0.001),
                    )
                    earliest = next(
                        (
                            item.frame.actual_timestamp_seconds or item.frame.requested_timestamp_seconds
                            for item in analyzed
                            if item.analysis.detection.possible_draft
                        ),
                        interval.start_seconds,
                    )
                    event_times = bounded_timestamps(
                        earliest,
                        center,
                        layout.fine_search.event_refinement_cadence_seconds,
                    )
                    refined = self._extract_series(
                        job_id=job_id,
                        asset=asset,
                        timestamps=[*refined_times, *event_times],
                        detector=detector,
                        layout_ref=layout_ref,
                        cache=cache,
                    )
                    combined = {record.evidence.frame_id: record for record in [*broad, *refined]}
                    analyzed = self._analyzed(list(combined.values()))

                selection = select_final_draft_frames(analyzed, layout.fine_search)
                selection, analyzed = self._retain_selection_frames(job_id, analyzed, selection)
                region_list = [by_candidate[value] for value in interval.candidate_ids]
                if selection.status != FinalSelectionStatus.SELECTED:
                    for region in region_list:
                        unresolved.append(
                            UnresolvedCandidate(
                                candidate=region,
                                final_selection=selection,
                                review_reasons=[
                                    ReviewReason(
                                        code=selection.failure_code or "FINAL_FRAME_NOT_FOUND",
                                        message="No stable completed dual-timer draft frame was found.",
                                        severity="error",
                                        stage=ProcessingStage.FINE_SEARCH,
                                    )
                                ],
                            )
                        )
                    continue

                self._stage(job_id, ProcessingStage.LAYOUT_PARSING)
                self._stage(job_id, ProcessingStage.RECOGNITION)
                self._stage(job_id, ProcessingStage.RECONSTRUCTION)
                self._stage(job_id, ProcessingStage.VALIDATION)
                games.append(
                    self._candidate_game(
                        job_id=job_id,
                        game_index=len(games) + 1,
                        candidate_ids=list(interval.candidate_ids),
                        coarse_times=sorted(
                            {
                                value
                                for region in region_list
                                for value in region.coarse_detection_timestamps
                            }
                        ),
                        analyzed=analyzed,
                        selection=selection,
                        layout=layout,
                        rules=rules,
                        rules_ref=rules_ref,
                        hero_recognizer=hero_recognizer,
                        team_recognizer=team_recognizer,
                    )
                )

            warnings = [
                "Coarse sampling uses only 0, 180, 360, ... seconds. A draft entirely between samples is an accepted blind spot.",
            ]
            if not hero_catalog.heroes:
                warnings.append(
                    "The selected hero catalog contains no approved reference assets; hero identities remain unknown."
                )
            result = VideoAnalysisResult(
                source=source,
                layout=layout_ref,
                draft_rules=rules_ref,
                hero_catalog_version=hero_catalog.version,
                games=games,
                unresolved_candidates=unresolved,
                requires_review=bool(unresolved) or any(game.requires_review for game in games),
                warnings=warnings,
            )
            self._stage(job_id, ProcessingStage.FINALIZING)
            self.artifacts.retain_only(job_id, self._artifact_ids(result))
            self.store.succeed(job_id, result)
        except Exception as exc:
            try:
                current = self.store.get(job_id)
                stage = ProcessingStage(current.stage)
                self.store.fail(
                    job_id,
                    ProcessingError(
                        code=type(exc).__name__.upper(),
                        stage=stage,
                        message=str(exc),
                        retryable=False,
                        details={"traceback": traceback.format_exc(limit=8)},
                    ),
                )
                self.artifacts.delete_job(job_id)
            except Exception:
                pass
        finally:
            if work_dir.exists():
                shutil.rmtree(work_dir)
