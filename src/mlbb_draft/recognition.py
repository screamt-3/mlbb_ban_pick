from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz.fuzz import partial_ratio, ratio

from .confidence import classify_score
from .config import HeroCatalogConfig, HeroRecognitionConfig, OCRConfig, TeamCatalogConfig, TeamConfig
from .models import (
    ConfidenceClass,
    HeroAlternative,
    HeroRecognition,
    RecognitionStatus,
    Side,
    TeamAlternative,
    TeamRecognition,
)
from .vision import recognize_text


def _phash(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    coefficients = cv2.dct(resized)[:8, :8]
    median = float(np.median(coefficients[1:]))
    return coefficients > median


def _phash_similarity(left: np.ndarray, right: np.ndarray) -> float:
    return 1.0 - float(np.count_nonzero(_phash(left) != _phash(right))) / 64.0


def _histogram_similarity(left: np.ndarray, right: np.ndarray) -> float:
    left_hsv = cv2.cvtColor(left, cv2.COLOR_BGR2HSV)
    right_hsv = cv2.cvtColor(right, cv2.COLOR_BGR2HSV)
    hist_left = cv2.calcHist([left_hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    hist_right = cv2.calcHist([right_hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
    cv2.normalize(hist_left, hist_left)
    cv2.normalize(hist_right, hist_right)
    correlation = float(cv2.compareHist(hist_left, hist_right, cv2.HISTCMP_CORREL))
    return max(0.0, min(1.0, (correlation + 1.0) / 2.0))


def _correlation_similarity(left: np.ndarray, right: np.ndarray) -> float:
    size = (128, 128)
    left_gray = cv2.cvtColor(cv2.resize(left, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    right_gray = cv2.cvtColor(cv2.resize(right, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    left_float = left_gray.astype(np.float32)
    right_float = right_gray.astype(np.float32)
    left_float -= left_float.mean()
    right_float -= right_float.mean()
    denominator = float(np.linalg.norm(left_float) * np.linalg.norm(right_float))
    if denominator == 0:
        return 0.0
    correlation = float(np.sum(left_float * right_float) / denominator)
    return max(0.0, min(1.0, (correlation + 1.0) / 2.0))


def reference_similarity(
    crop: np.ndarray,
    reference: np.ndarray,
    config: HeroRecognitionConfig,
) -> tuple[float, dict[str, float]]:
    target = cv2.resize(crop, (config.crop_size, config.crop_size), interpolation=cv2.INTER_AREA)
    candidate = cv2.resize(reference, (config.crop_size, config.crop_size), interpolation=cv2.INTER_AREA)
    correlation = _correlation_similarity(target, candidate)
    phash = _phash_similarity(target, candidate)
    histogram = _histogram_similarity(target, candidate)
    total_weight = config.correlation_weight + config.phash_weight + config.histogram_weight
    score = (
        config.correlation_weight * correlation
        + config.phash_weight * phash
        + config.histogram_weight * histogram
    ) / total_weight
    return score, {"correlation": correlation, "phash": phash, "histogram": histogram}


@dataclass(frozen=True)
class _ReferenceMatch:
    item_id: str
    display_name: str
    asset_id: str
    score: float
    components: dict[str, float]


class HeroRecognizer:
    ALGORITHM_VERSION = "reference-ensemble-v1"

    def __init__(
        self,
        project_root: Path,
        catalog: HeroCatalogConfig,
        config: HeroRecognitionConfig,
    ):
        self.project_root = project_root.resolve()
        self.catalog = catalog
        self.config = config
        self._image_cache: dict[str, np.ndarray | None] = {}

    def _load(self, relative_path: str) -> np.ndarray | None:
        if relative_path in self._image_cache:
            return self._image_cache[relative_path]
        path = (self.project_root / relative_path).resolve()
        if self.project_root not in path.parents or not path.exists():
            self._image_cache[relative_path] = None
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        self._image_cache[relative_path] = image
        return image

    def recognize(self, crop: np.ndarray, supporting_text: str | None = None) -> HeroRecognition:
        candidate_ids: set[str] | None = None
        if supporting_text:
            normalized_support = normalize_team_text(supporting_text).replace("0", "O")
            text_candidates = sorted(
                (
                    (
                        partial_ratio(
                            normalized_support,
                            normalize_team_text(hero.display_name),
                        )
                        / 100.0,
                        hero.hero_id,
                    )
                    for hero in self.catalog.heroes
                ),
                reverse=True,
            )
            if text_candidates and text_candidates[0][0] >= 0.65:
                candidate_ids = {hero_id for _, hero_id in text_candidates[:12]}

        matches: list[_ReferenceMatch] = []
        for hero in self.catalog.heroes:
            if candidate_ids is not None and hero.hero_id not in candidate_ids:
                continue
            best: _ReferenceMatch | None = None
            for reference in hero.references:
                image = self._load(reference.path)
                if image is None:
                    continue
                score, components = reference_similarity(crop, image, self.config)
                candidate = _ReferenceMatch(hero.hero_id, hero.display_name, reference.asset_id, score, components)
                if best is None or candidate.score > best.score:
                    best = candidate
            if best is not None:
                matches.append(best)
        if supporting_text:
            rescored: list[_ReferenceMatch] = []
            for item in matches:
                text_score = partial_ratio(
                    normalized_support,
                    normalize_team_text(item.display_name),
                ) / 100.0
                combined = 0.55 * item.score + 0.45 * text_score
                rescored.append(
                    _ReferenceMatch(
                        item.item_id,
                        item.display_name,
                        item.asset_id,
                        combined,
                        {
                            **item.components,
                            "image_similarity": item.score,
                            "supporting_text_similarity": text_score,
                        },
                    )
                )
            matches = rescored
        matches.sort(key=lambda item: item.score, reverse=True)
        if not matches:
            score = classify_score(
                metric="hero_reference_similarity_v1",
                value=0,
                medium_threshold=self.config.confirmed_threshold,
                high_threshold=self.config.high_threshold,
                producer="HeroRecognizer",
                algorithm_version=self.ALGORITHM_VERSION,
            )
            return HeroRecognition(
                score=score,
                status=RecognitionStatus.UNKNOWN,
                catalog_version=self.catalog.version,
            )

        best = matches[0]
        runner_up = matches[1].score if len(matches) > 1 else 0.0
        margin = best.score - runner_up
        text_score = best.components.get("supporting_text_similarity", 0.0)
        image_score = best.components.get("image_similarity", best.score)
        text_supported = (
            bool(supporting_text)
            and text_score >= 0.78
            and image_score >= 0.44
            and best.score >= 0.68
            and margin >= 0.04
        )
        if best.score >= self.config.high_threshold and margin >= self.config.high_margin:
            status = RecognitionStatus.CONFIRMED
            classification = ConfidenceClass.HIGH
        elif (
            best.score >= self.config.confirmed_threshold
            and margin >= self.config.confirmed_margin
        ) or text_supported:
            status = RecognitionStatus.CONFIRMED
            classification = ConfidenceClass.MEDIUM
        elif best.score >= self.config.unknown_threshold:
            status = RecognitionStatus.AMBIGUOUS
            classification = ConfidenceClass.LOW
        else:
            status = RecognitionStatus.UNKNOWN
            classification = ConfidenceClass.UNKNOWN
        effective_threshold = 0.68 if supporting_text else self.config.confirmed_threshold
        score = classify_score(
            metric="hero_image_with_text_support_v1" if supporting_text else "hero_reference_similarity_v1",
            value=best.score,
            medium_threshold=effective_threshold,
            high_threshold=self.config.high_threshold,
            producer="HeroRecognizer",
            algorithm_version=self.ALGORITHM_VERSION,
            details={**best.components, "runner_up_margin": margin},
        ).model_copy(update={"classification": classification})
        alternatives = [
            HeroAlternative(hero_id=item.item_id, display_name=item.display_name, score=item.score)
            for item in matches[1:4]
        ]
        return HeroRecognition(
            hero_id=best.item_id if status == RecognitionStatus.CONFIRMED else None,
            display_name=best.display_name if status == RecognitionStatus.CONFIRMED else None,
            score=score,
            status=status,
            catalog_version=self.catalog.version,
            reference_asset_id=best.asset_id if status == RecognitionStatus.CONFIRMED else None,
            alternatives=alternatives,
        )


def normalize_team_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).upper()
    return re.sub(r"[^A-Z0-9]", "", normalized)


class TeamRecognizer:
    LOGO_ALGORITHM_VERSION = "team-logo-reference-v1"
    OCR_ALGORITHM_VERSION = "tesseract-fuzzy-v1"

    def __init__(
        self,
        project_root: Path,
        catalog: TeamCatalogConfig,
        image_config: HeroRecognitionConfig,
        ocr_config: OCRConfig,
    ):
        self.project_root = project_root.resolve()
        self.catalog = catalog
        self.image_config = image_config
        self.ocr_config = ocr_config

    def _team_candidates(self, text: str) -> list[tuple[TeamConfig, float]]:
        normalized = normalize_team_text(text)
        candidates: list[tuple[TeamConfig, float]] = []
        for team in self.catalog.teams:
            values = [team.canonical_name, *team.aliases]
            score = max(ratio(normalized, normalize_team_text(value)) / 100.0 for value in values)
            candidates.append((team, score))
        return sorted(candidates, key=lambda item: item[1], reverse=True)

    def recognize_ocr(self, side: Side, crop: np.ndarray) -> TeamRecognition:
        raw_text, ocr_confidence = recognize_text(crop, psm=self.ocr_config.page_segmentation_mode)
        candidates = self._team_candidates(raw_text) if raw_text else []
        best_score = candidates[0][1] if candidates else 0.0
        runner_up = candidates[1][1] if len(candidates) > 1 else 0.0
        margin = best_score - runner_up
        if best_score >= self.ocr_config.confirmed_match_threshold and margin >= self.ocr_config.minimum_runner_up_margin:
            status = RecognitionStatus.CONFIRMED
        elif best_score >= self.ocr_config.uncertain_match_threshold:
            status = RecognitionStatus.AMBIGUOUS
        else:
            status = RecognitionStatus.UNKNOWN
        score = classify_score(
            metric="team_fuzzy_match_v1",
            value=best_score,
            medium_threshold=self.ocr_config.confirmed_match_threshold,
            high_threshold=0.98,
            producer="TeamRecognizer",
            algorithm_version=self.OCR_ALGORITHM_VERSION,
            details={"ocr_confidence": ocr_confidence, "runner_up_margin": margin},
        )
        best = candidates[0][0] if candidates else None
        return TeamRecognition(
            side=side,
            recognition_method="ocr",
            raw_text=raw_text,
            normalized_text=normalize_team_text(raw_text),
            canonical_team_id=best.team_id if best and status == RecognitionStatus.CONFIRMED else None,
            canonical_name=best.canonical_name if best and status == RecognitionStatus.CONFIRMED else None,
            primary_score=score,
            runner_up_margin=margin,
            status=status,
            alternatives=[
                TeamAlternative(team_id=team.team_id, canonical_name=team.canonical_name, score=value)
                for team, value in candidates[1:4]
            ],
        )

    def recognize_logo(self, side: Side, crop: np.ndarray) -> TeamRecognition:
        matches: list[tuple[TeamConfig, str, float, dict[str, float]]] = []
        for team in self.catalog.teams:
            best: tuple[TeamConfig, str, float, dict[str, float]] | None = None
            for asset in team.logo_assets:
                path = (self.project_root / asset).resolve()
                if self.project_root not in path.parents or not path.exists():
                    continue
                reference = cv2.imread(str(path), cv2.IMREAD_COLOR)
                if reference is None:
                    continue
                score, components = reference_similarity(crop, reference, self.image_config)
                value = (team, asset, score, components)
                if best is None or value[2] > best[2]:
                    best = value
            if best is not None:
                matches.append(best)
        matches.sort(key=lambda item: item[2], reverse=True)
        best_score = matches[0][2] if matches else 0.0
        runner_up = matches[1][2] if len(matches) > 1 else 0.0
        margin = best_score - runner_up
        if best_score >= self.image_config.confirmed_threshold and margin >= self.image_config.confirmed_margin:
            status = RecognitionStatus.CONFIRMED
        elif best_score >= self.image_config.unknown_threshold:
            status = RecognitionStatus.AMBIGUOUS
        else:
            status = RecognitionStatus.UNKNOWN
        score = classify_score(
            metric="team_logo_similarity_v1",
            value=best_score,
            medium_threshold=self.image_config.confirmed_threshold,
            high_threshold=self.image_config.high_threshold,
            producer="TeamRecognizer",
            algorithm_version=self.LOGO_ALGORITHM_VERSION,
            details={"runner_up_margin": margin},
        )
        best = matches[0] if matches else None
        return TeamRecognition(
            side=side,
            recognition_method="logo_reference",
            canonical_team_id=best[0].team_id if best and status == RecognitionStatus.CONFIRMED else None,
            canonical_name=best[0].canonical_name if best and status == RecognitionStatus.CONFIRMED else None,
            primary_score=score,
            runner_up_margin=margin,
            status=status,
            logo_reference_id=best[1] if best and status == RecognitionStatus.CONFIRMED else None,
            alternatives=[
                TeamAlternative(team_id=item[0].team_id, canonical_name=item[0].canonical_name, score=item[2])
                for item in matches[1:4]
            ],
        )
