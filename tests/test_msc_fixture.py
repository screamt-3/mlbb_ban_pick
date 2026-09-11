from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from mlbb_draft.config import ConfigRegistry
from mlbb_draft.models import Action, RecognitionStatus, Side
from mlbb_draft.recognition import HeroRecognizer, TeamRecognizer
from mlbb_draft.vision import (
    LayoutCompatibilityError,
    LayoutDetector,
    TemplateRepository,
    crop_rect,
    normalize_frame,
    recognize_text,
)


ROOT = Path(__file__).resolve().parents[1]
FRAME_DIR = ROOT / "fixtures" / "msc-ewc-2026-group-stage-day1" / "frames"
LAYOUT_ID = "msc-ewc-2026-group-stage-en-v1"


def _fixture_frame(name: str):
    path = FRAME_DIR / name
    if not path.exists():
        pytest.skip("ignored MSC calibration screenshots are not available")
    pixels = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert pixels is not None
    return pixels


def test_msc_detector_finds_dual_timer_and_rejects_transition():
    registry = ConfigRegistry(ROOT / "configs")
    layout, _ = registry.load_layout(LAYOUT_ID)
    detector = LayoutDetector(layout, TemplateRepository(ROOT))
    adjust = detector.analyze(_fixture_frame("source-14940.png"))
    transition = detector.analyze(_fixture_frame("source-14960.png"))
    assert adjust.detection.possible_draft
    assert adjust.detection.dual_timer_count == 2
    assert adjust.detection.draft_stage == "adjust"
    assert not transition.detection.possible_draft

    source = _fixture_frame("source-14940.png")
    at_720p = cv2.resize(source, (1280, 720), interpolation=cv2.INTER_AREA)
    normalized = detector.analyze(at_720p)
    assert normalized.detection.possible_draft
    assert normalized.detection.dual_timer_count == 2
    with pytest.raises(LayoutCompatibilityError):
        normalize_frame(np.zeros((360, 640, 3), dtype=np.uint8), layout)


def test_msc_final_frame_recognizes_teams_picks_and_side_mapping():
    registry = ConfigRegistry(ROOT / "configs")
    layout, _ = registry.load_layout(LAYOUT_ID)
    hero_catalog = registry.load_hero_catalog("msc-ewc-2026-heroes-v1")
    team_catalog = registry.load_team_catalog("msc-ewc-2026-teams-v1")
    analysis = LayoutDetector(layout, TemplateRepository(ROOT)).analyze(
        _fixture_frame("source-14954.png")
    )
    hero_recognizer = HeroRecognizer(ROOT, hero_catalog, layout.hero_recognition)
    team_recognizer = TeamRecognizer(ROOT, team_catalog, layout.hero_recognition, layout.ocr)

    teams = {
        Side(region.side): team_recognizer.recognize_logo(
            Side(region.side), crop_rect(analysis.normalized.pixels, region.roi)
        )
        for region in layout.team_regions
    }
    assert teams[Side.BLUE].canonical_name == "True Rippers"
    assert teams[Side.RED].canonical_name == "Team Vitality"

    def recognized_picks(frame_analysis, sides=(Side.BLUE, Side.RED), strict=True):
        result: dict[Side, set[str]] = {Side.BLUE: set(), Side.RED: set()}
        for observation in frame_analysis.slots:
            if Action(observation.config.action) != Action.PICK:
                continue
            if Side(observation.config.side) not in sides:
                continue
            if observation.visual_state == "player_placeholder":
                continue
            label = crop_rect(
                frame_analysis.normalized.pixels, observation.config.hero_name_roi
            )
            text_six, _ = recognize_text(label, psm=6)
            text_eight, _ = recognize_text(label, psm=8)
            label_text = f"{text_six} {text_eight}".strip()
            recognition = hero_recognizer.recognize(
                observation.crop[: int(observation.crop.shape[0] * 0.78)],
                supporting_text=label_text,
            )
            if strict:
                assert recognition.status == RecognitionStatus.CONFIRMED, (
                    observation.config.slot_id,
                    label_text,
                    recognition.model_dump(),
                )
            if recognition.status == RecognitionStatus.CONFIRMED:
                result[Side(observation.config.side)].add(recognition.display_name)
        return result

    picks = recognized_picks(analysis)

    assert picks[Side.BLUE] == {"Badang", "Alice", "Karrie", "Ling", "Eudora"}
    assert picks[Side.RED] == {"Atlas", "Akai", "Valentina", "Paquito", "Claude"}

    before_last = LayoutDetector(layout, TemplateRepository(ROOT)).analyze(
        _fixture_frame("source-inspect-404.png")
    )
    before_picks = recognized_picks(before_last, sides=(Side.BLUE,), strict=False)
    assert before_picks[Side.BLUE] == {"Alice", "Karrie", "Ling", "Eudora"}
    assert picks[Side.BLUE] - before_picks[Side.BLUE] == {"Badang"}
