# Session handoff: MSC at EWC 2026 draft-extraction MVP

Updated: 2026-09-12 (Asia/Singapore)

## Goal and target

Build the local extraction MVP for the MSC at EWC 2026 English Group Stage
broadcast, using the supplied Day 1 VOD:

- Source: `https://www.youtube.com/watch?v=Ew_-LE64e4w`
- Layout: `msc-ewc-2026-group-stage-en-v1`
- Calibration segment: source seconds 14520–14980
- User-confirmed cue: two center 30-second timers mark the start of team
  swapping.
- User-confirmed final action: blue picks one hero last.

## Completed this session

- Built the Python/FastAPI/Pydantic/OpenCV MVP in `src/mlbb_draft`.
- Implemented provider-independent video access with YouTube and local-fixture
  adapters.
- Enforced coarse timestamps exactly at `0, 180, 360, ...` and bounded all
  fine search to positive candidate windows.
- Implemented deterministic MSC/EWC layout detection, temporal clustering,
  overlapping-window deduplication, and stable final-frame selection.
- Calibrated the two-timer boundary at approximately source 14926.5 seconds.
  The retained evidence policy selects:
  - last complete frame before two timers;
  - first two-timer frame;
  - last stable complete frame before the HUD disappears.
- Added final-pick-phase tracking to identify the last side even when the
  immediate lock frame is covered by the broadcast reveal animation. The
  active slot appears occupied before lock, so the timestamps describe a phase
  interval rather than a frame-perfect click.
- Corrected this broadcast's side mapping from an assumed blue-left convention
  to the observed/user-defined red-left and blue-right mapping.
- Added strict pre/post swap validation. It requires the two-timer cue and five
  confirmed identities in every pick/ban category; two empty unknown sets can
  no longer pass validation.
- Added versioned layout, rule, hero, and team catalogs under `configs/`.
- Synced 132 available official hero assets and calibrated Team Vitality and
  True Rippers logo references.
- Implemented conservative recognition: unknown/ambiguous identities are not
  promoted to confirmed values. Hero-name OCR only supports portrait matching;
  it is not accepted as the primary recognizer.
- Implemented durable SQLite jobs, bounded in-process execution, structured
  failures, artifact retention, and cleanup of downloaded work media.
- Changed artifact handling so transient search frames remain in memory. Only
  selected evidence, nearby alternatives, and final slot crops survive for 30
  days; failed-job intermediates are removed.
- Added reusable fixture runner: `scripts/run_fixture.py`.
- Added a decision-complete plan in `docs/TECHNICAL_PLAN.md`, operational setup
  in `README.md`, and focused fixture/boundary inspection scripts.
- The final suite has 21 passing tests, including real-layout screenshot
  fixtures, 720p normalization, side attribution, all ten picks, timer/transition
  detection, swap completeness gates, and non-invention of phase membership.

## Calibrated facts for the representative game

- Blue team (right side): True Rippers
- Red team (left side): Team Vitality
- Expected blue pick set: Badang, Alice, Karrie, Ling, Eudora
- Expected red pick set: Atlas, Akai, Valentina, Paquito, Claude
- Last pick side: blue
- Last picked hero: Badang
- Expected source evidence timestamps:
  - pre-swap: about 14926.25
  - swap start: about 14926.5
  - final post-swap: about 14954
  - HUD transition: about 14960

These values are fixture assertions, not hard-coded production results.

## Final fixture outcome

The complete local pipeline was run repeatedly against `draft-1.mp4`; the last
serialized result is `.data/fixture-validation/latest.json` (gitignored).

- Job status: `succeeded`
- Games: 1
- Unresolved candidate regions: 0
- Selected relative timestamp: 434.000233 (source ≈14954.000233)
- Teams: blue True Rippers; red Team Vitality
- Confirmed picks: all 10, with the expected five-hero set on each side
- Two-timer boundary: confirmed
- Blue pick set across the boundary: complete and matching
- Red pick set across the boundary: complete and matching
- Last phase: blue; last hero: Badang; exact singleton membership known
- Bans: 0/10 confirmed, so overall swap validation is false and the game is
  correctly `partial`
- Earlier phases: left unresolved because player-order slots cannot establish
  chronology
- Retained storage after pruning: about 16 MB and 27 files for the run
- Measured full local 1080p fixture runtime: about three minutes

Final fast verification:

- `pytest`: 21 passed (69% measured statement coverage)
- `compileall`: passed
- `pip check`: no broken requirements

Only dependency deprecation warnings from Starlette's current TestClient stack
remain; they do not affect runtime behavior.

## Known limitations and deliberate safeguards

- Fixed 180-second coarse sampling can miss a draft wholly between samples.
- This version supports exactly one broadcast layout; incompatible geometry is
  rejected.
- The public EWC rulebook confirms Tournament Mode Draft Pick but does not list
  the action order. The phase sequence remains clearly marked `provisional` and
  `user_supplied`.
- The tournament's exact competitive patch is not publicly disclosed.
- Small tinted ban icons do not yet match official hero artwork reliably.
  Uncertain bans remain unknown and force review instead of being guessed.
- Exact order inside a multi-hero phase is reported only when distinct lock
  events support it.
- Post-swap slots are player order, not phase order. They establish final sets
  but are never sliced into invented chronological phase membership.
- The full seven-hour VOD is downloaded at up to 720p for a production API run,
  then removed after success or failure. Disk and processing time remain an MVP
  operational cost.

## After-action review

What worked:

- The user's two-timer observation produced a deterministic and precise swap
  boundary without relying on fragile timer OCR.
- Real screenshots caught the initial side-orientation assumption. Flipping to
  red-left/blue-right made the observed last side agree with the supplied rules.
- Separating observed final sets from reconstructed phases prevented post-swap
  player order from being misreported as chronology.
- Conservative category completeness exposed unknown bans rather than allowing
  two empty sets to pass validation.
- A bounded real-video run caught animation occlusion and motivated the clean
  pre-phase set-difference fallback that proves Badang last.

What needed correction during the session:

- The first layout assumed conventional blue-left orientation.
- Early reconstruction incorrectly treated final player positions as phase
  positions.
- Early event recognition mistook long-lived hero previews for locks; it was
  removed rather than patched with guesses.
- Initial artifact handling retained every search frame; it now keeps only
  result-referenced evidence.
- A full four-category state key let ban-icon flicker affect last-pick evidence;
  last-pick inference now depends only on pick-side state.

## What remains for a later session

1. Build broadcast-matched references or another validated method for the ten
   small tinted ban portraits. Until then, keep them unknown.
2. Add several labeled games from this same VOD before claiming the acceptance
   percentages in the technical plan; one calibrated game proves function, not
   population-wide accuracy.
3. Add explicit partial/obstructed/replay/letterboxed fixtures and multi-game/no-
   game end-to-end cases.
4. Profile full-VOD processing and consider provider-side ranged acquisition or
   a more efficient decode index. Do not change the required 180-second coarse
   sampling behavior.
5. Resolve or document the single unavailable official Chang'e reference asset
   if it remains absent on refresh.
6. Initialize/repair Git metadata outside the restricted sandbox if this folder
   is meant to become a working clone, then review and commit the repository.
