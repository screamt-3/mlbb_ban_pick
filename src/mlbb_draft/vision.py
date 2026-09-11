from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .confidence import classify_score
from .config import AnchorConfig, LayoutConfig, Rect, SlotConfig
from .models import (
    ConfidenceClass,
    DraftDetection,
    DraftStage,
    ScoreEvidence,
    SlotVisualState,
)


class LayoutCompatibilityError(ValueError):
    pass


@dataclass(frozen=True)
class FrameTransform:
    source_width: int
    source_height: int
    content_x: int
    content_y: int
    content_width: int
    content_height: int
    canonical_width: int
    canonical_height: int


@dataclass(frozen=True)
class NormalizedFrame:
    pixels: np.ndarray
    transform: FrameTransform


@dataclass(frozen=True)
class SlotObservation:
    config: SlotConfig
    visual_state: SlotVisualState
    occupancy_score: float
    placeholder_score: float | None
    crop: np.ndarray


@dataclass(frozen=True)
class DraftFrameAnalysis:
    normalized: NormalizedFrame
    detection: DraftDetection
    slots: tuple[SlotObservation, ...]
    timer_values: tuple[int | None, int | None]


def _content_bounds(pixels: np.ndarray) -> tuple[int, int, int, int]:
    gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
    row_active = (gray.mean(axis=1) > 4.0) | (gray.std(axis=1) > 3.0)
    col_active = (gray.mean(axis=0) > 4.0) | (gray.std(axis=0) > 3.0)
    row_indexes = np.flatnonzero(row_active)
    col_indexes = np.flatnonzero(col_active)
    if not row_indexes.size or not col_indexes.size:
        return 0, 0, pixels.shape[1], pixels.shape[0]
    y0, y1 = int(row_indexes[0]), int(row_indexes[-1] + 1)
    x0, x1 = int(col_indexes[0]), int(col_indexes[-1] + 1)
    return x0, y0, x1 - x0, y1 - y0


def normalize_frame(pixels: np.ndarray, layout: LayoutConfig) -> NormalizedFrame:
    if pixels.ndim != 3 or pixels.shape[2] != 3:
        raise LayoutCompatibilityError("expected a BGR color frame")
    source_height, source_width = pixels.shape[:2]
    if (
        source_width < layout.canonical_frame.minimum_width
        or source_height < layout.canonical_frame.minimum_height
    ):
        raise LayoutCompatibilityError(
            f"frame resolution {source_width}x{source_height} is below the configured minimum "
            f"{layout.canonical_frame.minimum_width}x{layout.canonical_frame.minimum_height}"
        )
    expected_aspect = layout.canonical_frame.width / layout.canonical_frame.height
    source_aspect = source_width / source_height
    source_aspect_error = abs(source_aspect - expected_aspect) / expected_aspect
    # Dark scene content can resemble letterboxing. A native-aspect frame does
    # not need bar removal and must be evaluated as the complete image.
    if (
        layout.canonical_frame.letterbox_policy == "detect_and_remove"
        and source_aspect_error > layout.canonical_frame.aspect_ratio_tolerance
    ):
        x, y, width, height = _content_bounds(pixels)
    else:
        x, y, width, height = 0, 0, source_width, source_height

    actual_aspect = width / height
    relative_error = abs(actual_aspect - expected_aspect) / expected_aspect
    if relative_error > layout.canonical_frame.aspect_ratio_tolerance:
        raise LayoutCompatibilityError(
            f"active content aspect ratio {actual_aspect:.5f} is incompatible with {expected_aspect:.5f}"
        )
    cropped_width_fraction = 1.0 - width / source_width
    cropped_height_fraction = 1.0 - height / source_height
    if max(cropped_width_fraction, cropped_height_fraction) > layout.canonical_frame.maximum_crop_fraction:
        # Uniform letterbox removal is allowed; only reject meaningful cropping along the content axis.
        letterbox_expected = abs(source_aspect - expected_aspect) / expected_aspect > 0.01
        if not letterbox_expected:
            raise LayoutCompatibilityError("frame appears cropped beyond the configured tolerance")

    active = pixels[y : y + height, x : x + width]
    canonical = cv2.resize(
        active,
        (layout.canonical_frame.width, layout.canonical_frame.height),
        interpolation=cv2.INTER_AREA if width > layout.canonical_frame.width else cv2.INTER_CUBIC,
    )
    return NormalizedFrame(
        pixels=canonical,
        transform=FrameTransform(
            source_width=source_width,
            source_height=source_height,
            content_x=x,
            content_y=y,
            content_width=width,
            content_height=height,
            canonical_width=layout.canonical_frame.width,
            canonical_height=layout.canonical_frame.height,
        ),
    )


def crop_rect(pixels: np.ndarray, rect: Rect) -> np.ndarray:
    rows, columns = rect.as_slices()
    return pixels[rows, columns].copy()


class TemplateRepository:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self._cache: dict[str, np.ndarray | None] = {}

    def load(self, relative_path: str | None) -> np.ndarray | None:
        if not relative_path:
            return None
        if relative_path in self._cache:
            return self._cache[relative_path]
        path = (self.project_root / relative_path).resolve()
        if self.project_root not in path.parents:
            raise ValueError("template path escapes project root")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR) if path.exists() else None
        self._cache[relative_path] = image
        return image


def template_similarity(crop: np.ndarray, template: np.ndarray | None) -> float | None:
    if template is None or crop.size == 0:
        return None
    if template.shape[0] > crop.shape[0] or template.shape[1] > crop.shape[1]:
        template = cv2.resize(template, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_AREA)
    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(crop_gray, template_gray, cv2.TM_CCOEFF_NORMED)
    if result.size == 0:
        return 0.0
    score = float(np.nanmax(result))
    if not np.isfinite(score):
        return 0.0
    return max(0.0, min(1.0, (score + 1.0) / 2.0))


def _generic_occupancy_score(crop: np.ndarray) -> float:
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    edge_fraction = float((cv2.Canny(gray, 60, 140) > 0).mean())
    saturation = float(hsv[:, :, 1].mean() / 255.0)
    contrast = float(min(1.0, gray.std() / 64.0))
    return max(0.0, min(1.0, 0.40 * min(1.0, edge_fraction / 0.18) + 0.30 * saturation + 0.30 * contrast))


def preprocess_text(crop: np.ndarray, scale_factor: int = 4) -> np.ndarray:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _, threshold = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return threshold


def recognize_text(crop: np.ndarray, *, psm: int = 7, digits_only: bool = False) -> tuple[str, float]:
    try:
        import pytesseract
    except (ImportError, RuntimeError):
        return "", 0.0
    options = f"--psm {psm}"
    if digits_only:
        options += " -c tessedit_char_whitelist=0123456789"
    try:
        data = pytesseract.image_to_data(
            preprocess_text(crop),
            config=options,
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        return "", 0.0
    texts: list[str] = []
    confidences: list[float] = []
    for text, confidence in zip(data.get("text", []), data.get("conf", []), strict=False):
        cleaned = text.strip()
        try:
            numeric_confidence = float(confidence)
        except (TypeError, ValueError):
            numeric_confidence = -1
        if cleaned:
            texts.append(cleaned)
        if numeric_confidence >= 0:
            confidences.append(numeric_confidence / 100.0)
    return " ".join(texts), (sum(confidences) / len(confidences) if confidences else 0.0)


def _timer_value(crop: np.ndarray) -> tuple[int | None, float]:
    text, confidence = recognize_text(crop, digits_only=True)
    match = re.search(r"\d{1,2}", text)
    if match is None:
        return None, confidence
    value = int(match.group())
    return (value if 0 <= value <= 30 else None), confidence


def _timer_present(crop: np.ndarray) -> bool:
    """Detect the broadcast's tall white countdown glyphs without trusting OCR."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    binary = (gray >= 200).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    tall_components = [
        item
        for item in stats[1:]
        if item[cv2.CC_STAT_AREA] >= 250
        and item[cv2.CC_STAT_HEIGHT] >= int(crop.shape[0] * 0.55)
        and item[cv2.CC_STAT_WIDTH] >= 8
    ]
    return bool(tall_components)


class LayoutDetector:
    ALGORITHM_VERSION = "msc-layout-deterministic-v1"

    def __init__(self, layout: LayoutConfig, templates: TemplateRepository):
        self.layout = layout
        self.templates = templates

    def _anchor_score(self, pixels: np.ndarray, anchor: AnchorConfig) -> float:
        score = template_similarity(crop_rect(pixels, anchor.roi), self.templates.load(anchor.template_asset))
        return score if score is not None else 0.0

    def _slot_observation(self, pixels: np.ndarray, slot: SlotConfig) -> SlotObservation:
        crop = crop_rect(pixels, slot.roi)
        placeholder = template_similarity(crop, self.templates.load(slot.player_placeholder_template_asset))
        empty = template_similarity(crop, self.templates.load(slot.empty_template_asset))
        generic = _generic_occupancy_score(crop)
        # Pick placeholders are live player photos, so portrait texture is not an
        # occupancy cue. The broadcast changes the bottom name plate from white
        # (player-only placeholder) to the side color once a hero is locked.
        colored_name_plate: float | None = None
        if slot.hero_name_roi is not None:
            name_plate = crop_rect(pixels, slot.hero_name_roi)
            hsv = cv2.cvtColor(name_plate, cv2.COLOR_BGR2HSV)
            colored_name_plate = float(((hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 45)).mean())

        if colored_name_plate is not None and colored_name_plate < 0.28:
            state = SlotVisualState.PLAYER_PLACEHOLDER
            occupancy = colored_name_plate
        elif placeholder is not None and placeholder >= self.layout.detector.placeholder_threshold:
            state = SlotVisualState.PLAYER_PLACEHOLDER
            occupancy = max(0.0, 1.0 - placeholder)
        elif empty is not None and empty >= self.layout.detector.placeholder_threshold:
            state = SlotVisualState.EMPTY
            occupancy = max(0.0, 1.0 - empty)
        elif (
            colored_name_plate is not None and colored_name_plate >= 0.28
        ) or generic >= self.layout.detector.locked_slot_threshold:
            state = SlotVisualState.HERO_LOCKED
            occupancy = max(generic, colored_name_plate or 0.0)
        else:
            state = SlotVisualState.UNKNOWN
            occupancy = generic
        return SlotObservation(slot, state, occupancy, placeholder, crop)

    def analyze(self, pixels: np.ndarray) -> DraftFrameAnalysis:
        normalized = normalize_frame(pixels, self.layout)
        canonical = normalized.pixels
        anchor_scores = {anchor.anchor_id: self._anchor_score(canonical, anchor) for anchor in self.layout.anchors}
        required_passed = all(
            anchor_scores[anchor.anchor_id] >= anchor.minimum_score
            for anchor in self.layout.anchors
            if anchor.required
        )
        weighted_anchor_total = sum(anchor.weight for anchor in self.layout.anchors)
        anchor_score = (
            sum(anchor_scores[anchor.anchor_id] * anchor.weight for anchor in self.layout.anchors)
            / weighted_anchor_total
            if weighted_anchor_total
            else 0.0
        )

        observations = tuple(self._slot_observation(canonical, slot) for slot in self.layout.slots)
        locked_count = sum(item.visual_state == SlotVisualState.HERO_LOCKED for item in observations)
        occupancy_score = locked_count / len(observations) if observations else 0.0

        adjust_crop = crop_rect(canonical, self.layout.stage_cues.adjust_roi)
        adjust_similarity = template_similarity(
            adjust_crop, self.templates.load(self.layout.stage_cues.adjust_template_asset)
        ) or 0.0
        left_timer_crop = crop_rect(canonical, self.layout.stage_cues.left_timer_roi)
        right_timer_crop = crop_rect(canonical, self.layout.stage_cues.right_timer_roi)
        left_timer_present = _timer_present(left_timer_crop)
        right_timer_present = _timer_present(right_timer_crop)
        dual_timer_count = int(left_timer_present) + int(right_timer_present)

        if dual_timer_count == 2:
            stage = DraftStage.ADJUST
        else:
            stage = DraftStage.UNKNOWN
        stage_cue_score = max(adjust_similarity, 1.0 if dual_timer_count == 2 else 0.0)
        geometry_score = 1.0
        weights = self.layout.detector
        weight_total = weights.anchor_weight + weights.geometry_weight + weights.occupancy_weight + weights.stage_cue_weight
        raw_score = (
            weights.anchor_weight * anchor_score
            + weights.geometry_weight * geometry_score
            + weights.occupancy_weight * occupancy_score
            + weights.stage_cue_weight * stage_cue_score
        ) / weight_total
        score = classify_score(
            metric="weighted_layout_similarity_v1",
            value=raw_score,
            medium_threshold=weights.threshold,
            high_threshold=0.90,
            producer="LayoutDetector",
            algorithm_version=self.ALGORITHM_VERSION,
            details={"locked_slot_count": locked_count, "slot_count": len(observations)},
        )
        possible = required_passed and geometry_score == 1.0 and raw_score >= weights.threshold
        reasons: list[str] = []
        if not required_passed:
            reasons.append("required_anchor_gate_failed")
        if raw_score < weights.threshold:
            reasons.append("detector_score_below_threshold")
        detection = DraftDetection(
            possible_draft=possible,
            score=score,
            feature_scores={
                "anchor": anchor_score,
                "geometry": geometry_score,
                "occupancy": occupancy_score,
                "stage_cue": stage_cue_score,
                **{f"anchor:{key}": value for key, value in anchor_scores.items()},
            },
            required_anchors_passed=required_passed,
            geometry_passed=True,
            draft_stage=stage,
            dual_timer_count=dual_timer_count,
            reasons=reasons,
        )
        return DraftFrameAnalysis(normalized, detection, observations, (None, None))


def occupancy_score_evidence(value: float) -> ScoreEvidence:
    return classify_score(
        metric="slot_occupancy_similarity_v1",
        value=value,
        medium_threshold=0.72,
        high_threshold=0.88,
        producer="LayoutDetector",
        algorithm_version=LayoutDetector.ALGORITHM_VERSION,
    )
