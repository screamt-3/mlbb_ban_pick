#!/usr/bin/env python3
"""Crop deterministic layout and team references from the approved MSC fixture."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from mlbb_draft.config import ConfigRegistry
from mlbb_draft.vision import crop_rect, normalize_frame


LAYOUT_ID = "msc-ewc-2026-group-stage-en-v1"


def write_image(path: Path, pixels) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), pixels):
        raise SystemExit(f"could not write {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("completed_frame", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    frame = cv2.imread(str(args.completed_frame.resolve()), cv2.IMREAD_COLOR)
    if frame is None:
        raise SystemExit(f"cannot read {args.completed_frame}")
    layout, _ = ConfigRegistry(project_root / "configs").load_layout(LAYOUT_ID)
    canonical = normalize_frame(frame, layout).pixels
    layout_root = project_root / "assets" / "layouts" / LAYOUT_ID
    for anchor in layout.anchors:
        write_image(layout_root / Path(anchor.template_asset).name, crop_rect(canonical, anchor.roi))
    write_image(layout_root / "adjust-cue.png", crop_rect(canonical, layout.stage_cues.adjust_roi))

    regions = {str(region.side): region for region in layout.team_regions}
    team_root = project_root / "assets" / "teams" / "msc-ewc-2026-v1"
    write_image(team_root / "team-vitality.png", crop_rect(canonical, regions["blue"].roi))
    write_image(team_root / "true-rippers.png", crop_rect(canonical, regions["red"].roi))
    print(f"wrote layout templates to {layout_root}")
    print(f"wrote fixture team references to {team_root}")


if __name__ == "__main__":
    main()
