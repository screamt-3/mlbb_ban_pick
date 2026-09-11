# Decision-complete technical plan

## 1. Scope, assumptions, and risks

Version 0.1 supports one versioned layout: the English MSC at EWC 2026 Group
Stage broadcast at 16:9, with 1280×720 as the minimum accepted resolution and
1920×1080 as the canonical coordinate space. Red is screen-left and blue is
screen-right for the supplied rules terminology.

The representative source is YouTube video `Ew_-LE64e4w`. Calibration uses a
bounded local segment covering source seconds 14520–14980. The hero catalog is
built from official MLBB/MPL-hosted assets. Team aliases cover the 16 event
teams; the representative team logos are fixture-calibrated.

Unsupported facts stay explicit:

- The exact competitive patch is not publicly disclosed.
- The public rulebook does not enumerate phase order, so the supplied order is
  `provisional`.
- Fixed 180-second sampling can miss an entire draft between samples.
- Small tinted ban icons are below the current reference-matching acceptance
  threshold.
- Player-ordered post-swap slots do not prove phase membership.

No draft recommendation model and no layout-agnostic recognizer are in scope.

## 2. Modules and ownership

| Module | Responsibility | Principal output |
|---|---|---|
| `video.py` | Provider resolution/probe/materialization and timestamp decoding | `VideoSource`, `DecodedFrame` |
| `sampling.py` | Exact coarse grid and bounded cadence generation | timestamps |
| `vision.py` | Normalization, anchors, slots, timer cue, deterministic detection | `DraftFrameAnalysis` |
| `clustering.py` | Positive-frame clustering and overlapping-window merge | `CandidateDraftRegion` |
| `timeline.py` | Stable final selection and visual lock-state transitions | `FinalFrameSelection`, `LastPickEvidence` |
| `recognition.py` | Conservative hero and team identity matching | recognition models |
| `reconstruction.py` | Set validation and evidence-limited phase reconstruction | `SwapValidation`, `DraftReconstruction` |
| `artifacts.py` | Atomic local evidence storage, retention, lookup, pruning | `ArtifactRef` |
| `jobs.py` | Durable SQLite job state and bounded local executor | `AnalysisJob` |
| `pipeline.py` | Stage orchestration and result assembly | `VideoAnalysisResult` |
| `api.py` | HTTP validation and serialization | FastAPI routes |

Downstream modules depend on the `VideoProvider` protocol, not yt-dlp. The
production adapter accepts YouTube URLs; the local adapter exists for fixtures.

## 3. Stage contracts and data flow

1. `resolve(url) -> VideoLocator` normalizes the provider identifier.
2. `probe(locator) -> VideoSource` records duration, dimensions, FPS, title,
   provider, video ID, and canonical URL.
3. `materialize(locator, workspace) -> VideoAsset` returns a local media path.
4. `coarse_timestamps(duration)` returns only `0, 180, 360, ... < duration`.
5. Each decoded frame becomes an ephemeral `EvidenceFrame` plus
   `DraftFrameAnalysis`; failures carry a structured extraction error.
6. `LayoutDetector.analyze` normalizes geometry and returns raw feature scores,
   required-anchor gates, thresholded decision, stage, timer count, and slots.
7. Positive coarse frames become candidate regions. Overlapping decode
   intervals are merged while all originating candidate IDs are preserved.
8. The fine search returns a selected final frame, pre-swap frame, first
   two-timer frame, alternatives, stability evidence, or an explicit failure.
9. Layout crops produce observed slots and independently recognized teams.
10. Pre/post category sets and the two-timer cue produce `SwapValidation`.
11. Final sets remain observed data; only evidence-supported locks populate
    reconstructed phases.
12. A video-level result contains zero or more game results plus unresolved
    candidates, warnings, versions, evidence IDs, confidence, and review codes.

Pydantic models in `models.py` are the authoritative JSON contract. Strict
models reject unknown fields and include schema/layout/rules/catalog versions.
Their nested structure is directly decomposable into future relational tables
without making a database part of this MVP.

## 4. Concrete algorithms and defaults

### Coarse detection

The deterministic weighted score is:

```text
0.55 anchors + 0.20 geometry + 0.20 locked-slot occupancy + 0.05 stage cue
```

Decision threshold is 0.78, and every required anchor must independently pass.
The stable MSC/EWC lockup and Flying Cloud graphic are required. Geometry is
layout-specific. Overlays or transitions that remove an anchor fail the gate;
partially populated drafts may still be positive if the weighted score passes.
No custom neural model is justified by the representative fixture.

### Candidate grouping and deduplication

- Cluster positive coarse observations whose gap is at most 360 seconds.
- Search from 180 seconds before the first positive through 420 seconds after
  the last positive, clamped to the media bounds.
- Merge overlapping search intervals before decoding.
- Produce at most one game from a merged interval and retain every contributing
  candidate ID as provenance, preventing duplicate game assignment.

### Fine search

- Broad cadence: 2.0 seconds.
- Two-timer boundary refinement: ±3 seconds at 0.25-second cadence.
- Lock-state evidence: 0.5-second cadence from the earliest positive frame to
  the boundary.
- Stable final requirement: three equivalent completed samples.
- Transition requirement: two consecutive non-draft samples.
- Alternatives: latest three other complete frames.

A complete slot state excludes empty/player-placeholder/unknown. A valid swap
frame requires two visually detected tall countdown glyph regions; OCR is not
trusted for that gate. The final frame is the latest stable complete frame in
the final two-timer run before HUD disappearance.

### Hero recognition

Hero recognition is crop-local. The primary signal is an ensemble of grayscale
correlation, perceptual hash, and HSV histogram (weights 0.50/0.25/0.25). Hero
label OCR can narrow candidates and support a portrait match but cannot confirm
an identity by itself. Confirmation requires configured score/margin gates;
otherwise the result is ambiguous or unknown with alternatives.

This is preferred over exact templates because broadcast crops are scaled,
tinted, compressed, and animated. A learned embedding model is deferred until
fixture measurements show deterministic reference matching is inadequate and
a properly licensed model is selected.

### Teams and sides

Side comes only from configured screen position. Team identity cannot change
side attribution. This layout uses logo-reference matching because the center
regions contain logos rather than stable team-name text. OCR plus normalized
fuzzy allowlist matching remains supported for future layouts and preserves raw
text, normalized text, match score, runner-up margin, and status.

### Reconstruction

Rules are an external ordered list of `(ordinal, phase_id, side, action, count)`.
Totals must exactly equal five picks and five bans per side. Final screen data
and reconstructed phases are separate models.

For `player_order` layouts, final positions establish only final side sets. The
active `PICKING` slot changes the stable visible count from nine to ten and
establishes the side of the final-pick phase; it does not claim the exact click
timestamp. The last hero is assigned only when the closest clean four-hero set
is a subset of the final five-hero set with exactly one addition. The singleton
final phase is then exact. No other phase membership or within-phase order is
asserted without lock evidence.

## 5. Confidence, validation, and review

Raw scores retain metric name, threshold, producer, algorithm version, and
calibration details. Classes are `high`, `medium`, `low`, and `unknown`.
Similarity values are not probabilities. Overall confidence uses the minimum
component class and deterministic review caps.

Pre/post validation is category-specific. A match flag is true only when the
expected five identities are present on both sides of the boundary and sets are
equal. Timeline-completed pick sets are explicitly marked. An empty unknown set
cannot equal another empty unknown set and pass. Overall swap validation also
requires the two-timer boundary and no cross-side move.

Representative review codes include:

- `HERO_UNRECOGNIZED`, `TEAM_UNRECOGNIZED`
- `SWAP_VALIDATION_UNAVAILABLE`, `SWAP_VALIDATION_INCOMPLETE`,
  `SWAP_VALIDATION_FAILED`
- `LAST_PICK_NOT_OBSERVED`, `LAST_PICK_SIDE_CONFLICT`
- `PHASE_MEMBERSHIP_UNRESOLVED`, `RULES_PROVISIONAL`
- `TRANSITION_NOT_OBSERVED` and final-selection failure codes

Errors record code, pipeline stage, message, retryability, and bounded traceback
details. Unresolved candidate regions are returned rather than silently dropped.

## 6. API and job behavior

`POST /analyses` validates the YouTube URL and layout, persists a queued job,
and returns HTTP 202. A one-worker in-process executor runs it. `GET
/analyses/{job_id}` exposes queued/running/succeeded/failed state, current stage,
timestamps, source metadata, error, and result. Artifact GET resolves only IDs
listed in that job's manifest.

SQLite stores the complete typed job document with atomic updates and WAL.
Interrupted queued/running jobs are marked retryable failures on startup. This
is deliberately not Celery/Redis/PostgreSQL, but the runner boundary is small
enough to replace later.

## 7. Artifact lifecycle and deployment

Source media is capped at 720p, stored under a UUID work directory, and removed
in `finally` after success or failure. Search frames are ephemeral. Referenced
evidence and slot crops are PNG artifacts with SHA-256 and a 30-day expiry;
unreferenced intermediates are pruned. Failed-job files are deleted. Startup
sweeps expired artifacts.

Deployment must install FFmpeg/FFprobe, Tesseract English data, and Node.js.
yt-dlp website support can change and is an operational dependency. Official
hero/team/broadcast imagery may carry publisher or tournament rights; use in a
deployed product must comply with those licenses and platform terms. YouTube
access must comply with YouTube's terms and the operator's rights to process the
source.

## 8. Fixture strategy and acceptance criteria

Tests cover exact timestamps/end bounds, clustering/interval merge, strict
config totals, provider URL normalization, local artifacts, job restart, API
validation, stable final-pick-phase evidence, full-set swap gates, and non-invention of
player-order chronology. Optional ignored screenshots cover:

- positive completed/two-timer draft;
- post-draft transition rejection;
- native 1080p and normalized 720p;
- minimum-resolution rejection;
- calibrated team identity and side mapping;
- all ten final picks;
- the clean four-to-five set difference proving Badang last.

Before expanding to more layouts, create a labeled evaluation manifest with
draft/non-draft/partial/animation/occlusion/transition frames across all games.
Report metrics only on games whose draft intersects the fixed coarse grid, and
separately count unavoidable between-sample misses.

MVP acceptance targets:

| Measure | Target |
|---|---:|
| False-positive coarse regions on representative VODs | 0 |
| Eligible games found | ≥95% |
| Duplicate game outputs | 0 |
| Defensible completed-frame selection | ≥95% |
| Confirmed hero precision | ≥99% |
| Pick recall on calibrated layout | ≥95% |
| Team and side attribution | 100% |
| Unsupported values promoted to confirmed | 0 |
| Required low-confidence cases carrying review codes | 100% |

These thresholds require a larger labeled fixture set before claiming they are
met. The single calibrated game proves functional behavior, not population-wide
accuracy.

## 9. Dependency-ordered completion phases

1. **Contracts/configuration:** strict models and config validation load.
2. **Media/artifacts:** provider adapters, decoding, cleanup, and evidence IDs
   pass unit checks.
3. **Detection/search:** representative positives/transitions and stable
   boundary selection pass fixtures.
4. **Recognition:** approved catalogs confirm known picks/teams and preserve
   unknown bans.
5. **Reconstruction/validation:** two-timer, pre/post sets, last lock, and
   non-invention tests pass.
6. **Jobs/API:** persisted lifecycle, restart failure, 202/status/artifact
   endpoints, and deterministic serialization pass.
7. **Release fixture:** the bounded source produces one game, no duplicate or
   unresolved region, correct teams/picks/last lock, retained evidence, and
   explicit review for bans/provisional rules.

## 10. Sources fixed for this MVP

- [MSC at EWC 2026 Group Stage Day 1 VOD](https://www.youtube.com/watch?v=Ew_-LE64e4w)
- [Official Esports World Cup MLBB competition page](https://ewc-web.prod.esf-systems.com/en/competitions/2026/mlbb)
- [Official MSC at EWC 2026 rulebook](https://cdn.esportsworldcup.com/resources/uploads/MSC_at_EWC_26_Rulebook_c5937d8775.pdf)
- [Official MPL Malaysia / MLBB hero listing used for reference assets](https://my.mpl.mobilelegends.com/en/)
