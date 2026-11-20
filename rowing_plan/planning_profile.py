"""Pure profile-to-domain adapters for the reset planner input contract.

These adapters are intentionally not consumed by the existing V3 dose engine.
They make current-state evidence and available capacity explicit without
turning either into a future prescription.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .planning_domain import (
    AvailableTrainingCapacity,
    AvailableTrainingDay,
    CurrentTrainingSource,
    CurrentTrainingState,
    HistoricalFact,
    TrainingModality,
    normalize_training_modality,
)


_CONSISTENCY = {
    "consistent": "regular",
    "inconsistent": "variable",
    "returning": "returning",
    "building": "building",
    "regular": "regular",
    "variable": "variable",
}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def normalize_available_training_capacity(profile: Mapping[str, Any]) -> AvailableTrainingCapacity:
    """Return the canonical recurring capacity without expanding a calendar.

    A fixed-rest day deliberately contributes zero usable minutes even if its
    legacy availability record contains a positive maximum.
    """
    activities = profile.get("recurring_activities", ())
    by_day: dict[str, list[str]] = {}
    for activity in activities if isinstance(activities, Iterable) and not isinstance(activities, (str, bytes, Mapping)) else ():
        if not isinstance(activity, Mapping) or str(activity.get("scheduling_status", "")) != "fixed":
            continue
        activity_id = str(activity.get("activity_id") or activity.get("id") or "fixed_activity")
        for day in activity.get("fixed_days", ()) if isinstance(activity.get("fixed_days"), Iterable) and not isinstance(activity.get("fixed_days"), (str, bytes, Mapping)) else ():
            by_day.setdefault(str(day).lower(), []).append(activity_id)

    days = []
    weekly = profile.get("weekly_availability", ())
    for item in weekly if isinstance(weekly, Iterable) and not isinstance(weekly, (str, bytes, Mapping)) else ():
        if not isinstance(item, Mapping) or not item.get("weekday"):
            continue
        key = str(item["weekday"]).lower()
        modes = []
        for raw in item.get("rowing_modes", ()) if isinstance(item.get("rowing_modes"), Iterable) and not isinstance(item.get("rowing_modes"), (str, bytes, Mapping)) else ():
            mode = normalize_training_modality(raw)
            if mode is not None:
                modes.append(mode)
        days.append(AvailableTrainingDay(
            day_key=key,
            available_minutes=_positive_int(item.get("max_training_minutes")) or 0,
            available=bool(item.get("available", True)),
            fixed_rest=bool(item.get("fixed_rest", False)),
            allowed_modalities=tuple(modes),
            fixed_commitment_ids=tuple(by_day.get(key, ())),
            prohibited_role_families=tuple(str(value) for value in item.get("prohibited_role_families", ()) if isinstance(value, str)),
        ))
    return AvailableTrainingCapacity(tuple(days))


def _actual_duration(fact: HistoricalFact) -> int | None:
    actual = fact.completed_actual
    if not isinstance(actual, Mapping):
        return None
    return _positive_int(actual.get("actual_duration_min"))


def _actual_activity_type(fact: HistoricalFact) -> TrainingModality | None:
    actual = fact.completed_actual
    return normalize_training_modality(actual.get("activity_type")) if isinstance(actual, Mapping) else None


def normalize_current_training_state(
    profile: Mapping[str, Any], *, historical_facts: Iterable[HistoricalFact] = (),
    recent_actuals_sufficient: bool = False,
    observation_window_days: int | None = None,
) -> CurrentTrainingState:
    """Normalize evidence using an explicit, non-policy sufficiency decision.

    R1B does not invent a sufficiency threshold.  R2 must supply the policy
    decision for ``recent_actuals_sufficient``; until then, present actuals
    yield ``mixed`` evidence rather than silently replacing athlete estimates.
    """
    athlete = profile.get("athlete", {}) if isinstance(profile.get("athlete"), Mapping) else {}
    context = profile.get("training_context", {}) if isinstance(profile.get("training_context"), Mapping) else {}
    persisted = profile.get("current_training_state", {}) if isinstance(profile.get("current_training_state"), Mapping) else {}
    facts = tuple(item for item in historical_facts if isinstance(item, HistoricalFact) and item.completed_actual is not None)

    actual_minutes = [value for value in (_actual_duration(item) for item in facts) if value is not None]
    rowing_facts = tuple(item for item in facts if _actual_activity_type(item) in {TrainingModality.ERG, TrainingModality.ON_WATER})
    strength_facts = tuple(item for item in facts if _actual_activity_type(item) is TrainingModality.STRENGTH)
    actual_total = sum(actual_minutes) if actual_minutes else None
    actual_rowing = sum(value for value in (_actual_duration(item) for item in rowing_facts) if value is not None) or None

    legacy_values = {
        "approximate_total_physical_minutes": _positive_int(athlete.get("current_weekly_training_minutes")),
        "approximate_rowing_minutes": _positive_int(athlete.get("current_approx_weekly_rowing_minutes")),
        "rowing_exposures": _positive_int(athlete.get("current_rowing_sessions_per_week")),
        "strength_exposures": _positive_int(context.get("current_strength_sessions_per_week")),
        "typical_strength_duration_minutes": _positive_int(context.get("typical_strength_duration_minutes")),
        "longest_comfortable_row_minutes": _positive_int(athlete.get("longest_comfortable_continuous_row_minutes")),
    }
    explicit_values = {
        key: _positive_int(persisted.get(key)) if key != "consistency" else None
        for key in legacy_values
    }
    has_explicit = bool(persisted)
    estimates = {key: explicit_values[key] if explicit_values[key] is not None else value for key, value in legacy_values.items()}
    consistency = _CONSISTENCY.get(str(persisted.get("consistency") or athlete.get("recent_training_consistency") or "").lower()) or None

    if facts and recent_actuals_sufficient:
        source = CurrentTrainingSource.RECENT_ACTUALS
        values = {
            **estimates,
            "approximate_total_physical_minutes": actual_total,
            "approximate_rowing_minutes": actual_rowing,
            "rowing_exposures": len(rowing_facts),
            "strength_exposures": len(strength_facts),
        }
        confidence = "high"
    elif facts and any(value is not None for value in estimates.values()):
        source = CurrentTrainingSource.MIXED
        values = estimates
        confidence = "moderate"
    elif has_explicit:
        source = CurrentTrainingSource.ATHLETE_ESTIMATE
        values = estimates
        confidence = "moderate"
    else:
        source = CurrentTrainingSource.LEGACY_PROFILE_ESTIMATE
        values = estimates
        confidence = "low"

    missing = tuple(key for key, value in values.items() if value is None)
    return CurrentTrainingState(
        source=source,
        observation_window_days=observation_window_days,
        approximate_total_physical_minutes=values["approximate_total_physical_minutes"],
        approximate_rowing_minutes=values["approximate_rowing_minutes"],
        rowing_exposures=values["rowing_exposures"],
        strength_exposures=values["strength_exposures"],
        typical_strength_duration_minutes=values["typical_strength_duration_minutes"],
        consistency=consistency,
        longest_comfortable_row_minutes=values["longest_comfortable_row_minutes"],
        confidence=confidence,
        missing_evidence=missing,
        completed_history_ids=tuple(item.id for item in facts),
    )
