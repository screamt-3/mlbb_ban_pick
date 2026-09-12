# MLBB Draft Extractor

A local, evidence-first MVP for extracting Mobile Legends: Bang Bang ban/pick
screens from tournament YouTube VODs. Version 0.1 supports exactly the English
main-broadcast layout used for the MSC at EWC 2026 Group Stage.

The implementation deliberately favors `unknown` plus review evidence over a
plausible but unsupported hero identity or chronology.

## Supported input

- Layout ID: `msc-ewc-2026-group-stage-en-v1`
- Calibrated VOD: `https://www.youtube.com/watch?v=Ew_-LE64e4w`
- Reference game: the draft ending near source second 14954
- Screen orientation: red on the left, blue on the right
- Swap cue: two center 30-second timers and `ADJUST`

The public EWC rulebook confirms Tournament Mode Draft Pick but does not list
the phase order. The configured 15-phase sequence is therefore versioned as
`user_supplied` and `provisional`. The exact competitive patch is also not
publicly disclosed.

## Important detection limitation

Coarse frames are generated at exactly:

```text
0, 180, 360, 540, ... seconds
```

No offset sampling, scene-change scan, or full-video high-frequency pass is
performed. Fine analysis runs only inside a bounded window created by a
positive coarse frame. A draft wholly between two coarse timestamps can be
missed; this is an accepted MVP limitation exposed in both API metadata and
results.

## Pipeline

```text
provider URL
  → probe and materialize video
  → exact 180-second coarse sampling
  → deterministic layout detection
  → temporal clustering and overlapping-window merge
  → bounded 2-second fine search
  → 0.25-second two-timer boundary refinement
  → 0.5-second lock-state evidence
  → final/pre-swap/swap-start frame selection
  → slot, team, and hero recognition
  → pre/post set validation and conservative reconstruction
  → durable result JSON and retained evidence
```

The two-timer boundary selects three distinct facts:

- the last complete state before swapping;
- the first frame proving the two-timer `ADJUST` state;
- the latest stable complete frame before the draft HUD disappears.

Pick validation can combine the nearest clean 9-pick state with the observed
last lock when the immediate pre-timer frame is occluded by the hero reveal
animation. Pick and ban categories report independent completeness and match
flags. The entire swap validation remains false if any required category is
unrecognized.

Post-swap slots are player order, not chronological phase order. They prove
final per-side sets but are not sliced into invented phases. The final-pick
phase is reconstructed when the active slot's side, the completed pre-`ADJUST`
endpoint, and a one-hero final-set difference agree. This is a phase interval,
not a frame-perfect click timestamp. Earlier phase membership remains
unresolved unless separate lock evidence proves it.

See [docs/TECHNICAL_PLAN.md](docs/TECHNICAL_PLAN.md) for component ownership,
contracts, defaults, error behavior, and evaluation criteria. See
[SESSION_HANDOFF.md](SESSION_HANDOFF.md) for the current calibration and
after-action record.

## Requirements

- Python 3.11+
- FFmpeg and FFprobe on `PATH`
- Tesseract 5 with English trained data on `PATH`
- Node.js for current YouTube extraction challenges
- Enough temporary disk for the source VOD (the production provider caps video
  acquisition at 720p)

Python dependencies are declared in `pyproject.toml`: FastAPI, Pydantic,
Uvicorn, yt-dlp, OpenCV, NumPy, pytesseract, RapidFuzz, and PyYAML.

## Setup and checks

```sh
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/pip check
```

To start the local API:

```sh
.venv/bin/mlbb-draft-api
```

It binds to `127.0.0.1:8000` by default. Set `MLBB_DRAFT_DATA_DIR` to place the
SQLite database and retained artifacts somewhere other than `.data/`.

Open `http://127.0.0.1:8000/` to use the draft board. It includes the eight
manually reviewed MSC at EWC 2026 Day 1 drafts, side-by-side bans and picks,
direct VOD links, confidence markers, and the last chronological pick.

## API

Create an asynchronous analysis:

```http
POST /analyses
Content-Type: application/json

{
  "youtube_url": "https://www.youtube.com/watch?v=Ew_-LE64e4w",
  "layout_id": "msc-ewc-2026-group-stage-en-v1"
}
```

Response:

```json
{
  "job_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "queued"
}
```

Read status/result and evidence:

```http
GET /analyses/{job_id}
GET /analyses/{job_id}/artifacts/{artifact_id}
GET /healthz
```

Jobs are persisted in SQLite. A process restart marks unfinished jobs failed
with `PROCESS_RESTARTED`; resubmission is explicit rather than silently
resuming partial work.

## Fixture validation

The large video fixture and extracted source screenshots are intentionally
gitignored. With `fixtures/msc-ewc-2026-group-stage-day1/draft-1.mp4` present:

```sh
.venv/bin/python scripts/run_fixture.py \
  --output .data/fixture-validation/latest.json
```

The segment starts at source second 14520, so add 14520 to its relative result
timestamps. The calibrated final timestamp of roughly 434 seconds corresponds
to source second 14954.

Official hero assets can be refreshed from a saved official MPL page:

```sh
.venv/bin/python scripts/sync_official_hero_assets.py --html /path/to/mpl-page.html
```

## Confidence and evidence

Similarity scores are measurements, not probabilities. Each score records its
producer, algorithm version, threshold, and classification. Overall confidence
is the minimum component classification, then capped when review reasons are
present.

The current fixture confirms all ten picks, both teams, both per-side pick-set
matches, and blue/Badang as the last lock. The small tinted ban portraits do not
reliably match the official artwork, so bans remain unknown and the game result
is correctly `partial` with review reasons.

## Artifact lifecycle

- Downloaded media lives in a per-job work directory and is deleted after both
  success and failure.
- Transient search frames remain in memory and are not retained.
- Selected final, pre-swap, boundary, alternatives, and final slot crops are
  retained for 30 days with SHA-256 metadata and stable API identifiers.
- Unreferenced intermediates are pruned before success is committed.
- Failed-job artifacts are removed; the structured SQLite error remains.
- Expired evidence is swept on application startup.
