from __future__ import annotations

import math


COARSE_INTERVAL_SECONDS = 180.0


def coarse_timestamps(duration_seconds: float) -> list[float]:
    """Return exactly 0, 180, 360, ... strictly before end-of-video."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be finite and greater than zero")
    last_index = math.ceil(duration_seconds / COARSE_INTERVAL_SECONDS) - 1
    return [index * COARSE_INTERVAL_SECONDS for index in range(last_index + 1)]


def bounded_timestamps(start_seconds: float, end_seconds: float, cadence_seconds: float) -> list[float]:
    if not all(math.isfinite(value) for value in (start_seconds, end_seconds, cadence_seconds)):
        raise ValueError("timestamp arguments must be finite")
    if start_seconds < 0 or end_seconds < start_seconds or cadence_seconds <= 0:
        raise ValueError("invalid bounded timestamp range")
    count = math.floor((end_seconds - start_seconds) / cadence_seconds)
    values = [start_seconds + index * cadence_seconds for index in range(count + 1)]
    if not values or values[-1] < end_seconds - 1e-9:
        values.append(end_seconds)
    return values


def refinement_timestamps(
    center_seconds: float,
    radius_seconds: float,
    cadence_seconds: float,
    *,
    lower_bound: float,
    upper_bound: float,
) -> list[float]:
    start = max(lower_bound, center_seconds - radius_seconds)
    end = min(upper_bound, center_seconds + radius_seconds)
    return bounded_timestamps(start, end, cadence_seconds)
