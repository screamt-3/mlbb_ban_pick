from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from .models import Action, LayoutReference, RulesReference, Side, StrictModel


class Rect(StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    def as_slices(self) -> tuple[slice, slice]:
        return slice(self.y, self.y + self.height), slice(self.x, self.x + self.width)


class CanonicalFrameConfig(StrictModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    aspect_ratio_tolerance: float = Field(default=0.01, ge=0, le=0.1)
    minimum_width: int = Field(default=1280, gt=0)
    minimum_height: int = Field(default=720, gt=0)
    maximum_crop_fraction: float = Field(default=0.03, ge=0, le=0.25)
    letterbox_policy: Literal["detect_and_remove", "reject"] = "detect_and_remove"


class AnchorConfig(StrictModel):
    anchor_id: str
    roi: Rect
    template_asset: str
    required: bool = True
    minimum_score: float = Field(default=0.80, ge=0, le=1)
    weight: float = Field(default=1.0, gt=0)


class SlotConfig(StrictModel):
    slot_id: str
    side: Side
    action: Action
    visible_order: int = Field(ge=1)
    roi: Rect
    empty_template_asset: str | None = None
    player_placeholder_template_asset: str | None = None
    recognition_mask_asset: str | None = None
    hero_name_roi: Rect | None = None


class TeamRegionConfig(StrictModel):
    side: Side
    recognition_method: Literal["ocr", "logo_reference"]
    roi: Rect


class DetectorConfig(StrictModel):
    threshold: float = Field(default=0.78, ge=0, le=1)
    anchor_weight: float = Field(default=0.55, ge=0)
    geometry_weight: float = Field(default=0.20, ge=0)
    occupancy_weight: float = Field(default=0.20, ge=0)
    stage_cue_weight: float = Field(default=0.05, ge=0)
    locked_slot_threshold: float = Field(default=0.72, ge=0, le=1)
    placeholder_threshold: float = Field(default=0.82, ge=0, le=1)
    adjust_cue_threshold: float = Field(default=0.78, ge=0, le=1)
    dual_timer_threshold: float = Field(default=0.72, ge=0, le=1)

    @model_validator(mode="after")
    def weights_are_nonzero(self) -> "DetectorConfig":
        total = self.anchor_weight + self.geometry_weight + self.occupancy_weight + self.stage_cue_weight
        if total <= 0:
            raise ValueError("detector feature weights must have a positive sum")
        return self


class FineSearchConfig(StrictModel):
    seconds_before_first_positive: float = Field(default=180, ge=0)
    seconds_after_last_positive: float = Field(default=420, gt=0)
    cadence_seconds: float = Field(default=2.0, gt=0)
    transition_refinement_cadence_seconds: float = Field(default=0.25, gt=0)
    event_refinement_cadence_seconds: float = Field(default=0.5, gt=0)
    refinement_radius_seconds: float = Field(default=3.0, gt=0)
    required_stability_samples: int = Field(default=3, ge=2)
    disappearance_samples: int = Field(default=2, ge=1)
    clustering_distance_seconds: float = Field(default=360, gt=0)
    maximum_alternatives: int = Field(default=3, ge=0, le=10)


class HeroRecognitionConfig(StrictModel):
    high_threshold: float = Field(default=0.90, ge=0, le=1)
    confirmed_threshold: float = Field(default=0.86, ge=0, le=1)
    unknown_threshold: float = Field(default=0.78, ge=0, le=1)
    high_margin: float = Field(default=0.08, ge=0, le=1)
    confirmed_margin: float = Field(default=0.06, ge=0, le=1)
    crop_size: int = Field(default=128, gt=0)
    correlation_weight: float = Field(default=0.50, ge=0)
    phash_weight: float = Field(default=0.25, ge=0)
    histogram_weight: float = Field(default=0.25, ge=0)


class OCRConfig(StrictModel):
    language: str = "eng"
    page_segmentation_mode: int = 7
    scale_factor: int = Field(default=4, ge=1, le=8)
    confirmed_match_threshold: float = Field(default=0.90, ge=0, le=1)
    uncertain_match_threshold: float = Field(default=0.80, ge=0, le=1)
    minimum_runner_up_margin: float = Field(default=0.08, ge=0, le=1)


class StageCuesConfig(StrictModel):
    adjust_roi: Rect
    adjust_template_asset: str
    left_timer_roi: Rect
    right_timer_roi: Rect


class LayoutConfig(StrictModel):
    schema_version: Literal["1.0"]
    layout_id: str
    version: str
    description: str
    canonical_frame: CanonicalFrameConfig
    side_orientation: dict[Side, Literal["left", "right"]]
    pick_slot_order_semantics: Literal["phase_order", "player_order"] = "player_order"
    anchors: list[AnchorConfig]
    slots: list[SlotConfig]
    team_regions: list[TeamRegionConfig]
    stage_cues: StageCuesConfig
    masks: list[Rect] = Field(default_factory=list)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    fine_search: FineSearchConfig = Field(default_factory=FineSearchConfig)
    hero_recognition: HeroRecognitionConfig = Field(default_factory=HeroRecognitionConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)

    @model_validator(mode="after")
    def validate_layout(self) -> "LayoutConfig":
        if set(self.side_orientation) != {Side.BLUE, Side.RED}:
            raise ValueError("side_orientation must define blue and red")
        if self.side_orientation[Side.BLUE] == self.side_orientation[Side.RED]:
            raise ValueError("blue and red cannot occupy the same screen position")
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("slot IDs must be unique")
        team_sides = [region.side for region in self.team_regions]
        if sorted(team_sides) != sorted([Side.BLUE, Side.RED]):
            raise ValueError("exactly one team region is required for each side")
        width, height = self.canonical_frame.width, self.canonical_frame.height
        rects = [a.roi for a in self.anchors] + [s.roi for s in self.slots]
        rects += [t.roi for t in self.team_regions] + self.masks
        rects += [
            self.stage_cues.adjust_roi,
            self.stage_cues.left_timer_roi,
            self.stage_cues.right_timer_roi,
        ]
        for rect in rects:
            if rect.x + rect.width > width or rect.y + rect.height > height:
                raise ValueError(f"rectangle {rect} exceeds canonical frame")
        return self


class DraftPhaseConfig(StrictModel):
    phase_id: str
    ordinal: int = Field(ge=1)
    side: Side
    action: Action
    count: int = Field(ge=1, le=5)
    exact_internal_order_encoded: bool = False


class DraftRulesConfig(StrictModel):
    schema_version: Literal["1.0"]
    rules_id: str
    version: str
    source: str
    provenance: str
    validation_status: Literal["validated", "provisional"]
    expected_blue_picks: int = Field(default=5, ge=0)
    expected_red_picks: int = Field(default=5, ge=0)
    expected_blue_bans: int = Field(default=5, ge=0)
    expected_red_bans: int = Field(default=5, ge=0)
    phases: list[DraftPhaseConfig]

    @model_validator(mode="after")
    def validate_phase_totals(self) -> "DraftRulesConfig":
        ordinals = [phase.ordinal for phase in self.phases]
        phase_ids = [phase.phase_id for phase in self.phases]
        if len(ordinals) != len(set(ordinals)) or sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise ValueError("phase ordinals must be unique and contiguous from one")
        if len(phase_ids) != len(set(phase_ids)):
            raise ValueError("phase IDs must be unique")
        totals: dict[tuple[Side, Action], int] = {}
        for phase in self.phases:
            key = (phase.side, phase.action)
            totals[key] = totals.get(key, 0) + phase.count
        expected = {
            (Side.BLUE, Action.PICK): self.expected_blue_picks,
            (Side.RED, Action.PICK): self.expected_red_picks,
            (Side.BLUE, Action.BAN): self.expected_blue_bans,
            (Side.RED, Action.BAN): self.expected_red_bans,
        }
        if totals != expected:
            raise ValueError(f"phase totals {totals} do not match expected totals {expected}")
        return self


class HeroReferenceConfig(StrictModel):
    asset_id: str
    path: str


class HeroConfig(StrictModel):
    hero_id: str
    display_name: str
    references: list[HeroReferenceConfig]


class HeroCatalogConfig(StrictModel):
    schema_version: Literal["1.0"]
    catalog_id: str
    version: str
    patch: str
    source: str
    heroes: list[HeroConfig]


class TeamConfig(StrictModel):
    team_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    logo_assets: list[str] = Field(default_factory=list)


class TeamCatalogConfig(StrictModel):
    schema_version: Literal["1.0"]
    catalog_id: str
    version: str
    source: str
    teams: list[TeamConfig]


def _load_yaml(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError(f"configuration must contain an object: {path}")
    return value, digest


class ConfigRegistry:
    def __init__(self, root: Path):
        self.root = root

    def load_layout(self, layout_id: str) -> tuple[LayoutConfig, LayoutReference]:
        path = self.root / "layouts" / f"{layout_id}.yaml"
        value, digest = _load_yaml(path)
        config = LayoutConfig.model_validate(value)
        if config.layout_id != layout_id:
            raise ValueError(f"layout_id mismatch in {path}")
        return config, LayoutReference(layout_id=config.layout_id, version=config.version, config_sha256=digest)

    def load_rules(self, rules_id: str) -> tuple[DraftRulesConfig, RulesReference]:
        path = self.root / "rules" / f"{rules_id}.yaml"
        value, digest = _load_yaml(path)
        config = DraftRulesConfig.model_validate(value)
        if config.rules_id != rules_id:
            raise ValueError(f"rules_id mismatch in {path}")
        ref = RulesReference(
            rules_id=config.rules_id,
            version=config.version,
            config_sha256=digest,
            provenance=config.provenance,
            validation_status=config.validation_status,
        )
        return config, ref

    def load_hero_catalog(self, catalog_id: str) -> HeroCatalogConfig:
        value, _ = _load_yaml(self.root / "catalogs" / f"{catalog_id}.yaml")
        config = HeroCatalogConfig.model_validate(value)
        if config.catalog_id != catalog_id:
            raise ValueError("hero catalog ID mismatch")
        return config

    def load_team_catalog(self, catalog_id: str) -> TeamCatalogConfig:
        value, _ = _load_yaml(self.root / "catalogs" / f"{catalog_id}.yaml")
        config = TeamCatalogConfig.model_validate(value)
        if config.catalog_id != catalog_id:
            raise ValueError("team catalog ID mismatch")
        return config
