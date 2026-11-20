"""Authoritative future physical-dose recommendation for replacement V3.

This module deliberately does not create a rowing, strength, or
cross-training allocation.  It establishes one physical-minute budget for a
coherent future block; R3 will partition that budget without introducing
another dose authority.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from .periodization import build_season_phases
from .planning_domain import (
    AuthorityType,
    CurrentTrainingState,
    HistoricalFact,
    RecommendedDoseSegment,
    RecommendedTrainingDose,
)
from .planning_normalization import NormalizedPlanningContext
from .planning_profile import normalize_current_training_state
from .physiological_state import reduce_physiological_state
from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy


POLICY_VERSION = "recommended-dose-v1"
_FALLBACK_WEEKLY_MINUTES = {
    "new": 120,
    "novice": 120,
    "developing": 180,
    "intermediate": 210,
    "experienced": 270,
    "competitive": 330,
}


class RecommendedDoseInfeasibleError(ValueError):
    """A fixed request conflicts with usable capacity or hard authority."""


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _completed_actuals(
    facts: Iterable[HistoricalFact], start: date, days: int = 21,
) -> tuple[HistoricalFact, ...]:
    lower = start - timedelta(days=days)
    return tuple(
        item for item in facts
        if lower <= item.date < start and isinstance(item.completed_actual, Mapping)
    )


def _actual_minutes(item: HistoricalFact) -> int | None:
    actual = item.completed_actual
    return _positive_int(actual.get("actual_duration_min")) if isinstance(actual, Mapping) else None


def recent_actual_history_is_sufficient(
    facts: Iterable[HistoricalFact], *, planning_start: date,
) -> bool:
    """Transparent 21-day history rule approved for R2.

    It tolerates imperfect logging while requiring multiple dates and weeks.
    The profile estimate is used as a coverage check only when available.
    """
    actuals = _completed_actuals(facts, planning_start)
    dated = {item.date for item in actuals if _actual_minutes(item) is not None}
    if len(dated) < 6 or len({item.date.isocalendar()[:2] for item in actuals}) < 2:
        return False
    earliest = min(dated, default=planning_start)
    return (planning_start - earliest).days >= 14


def _weekly_actual_baseline(facts: Iterable[HistoricalFact], planning_start: date) -> int | None:
    actuals = _completed_actuals(facts, planning_start)
    values = [value for item in actuals if (value := _actual_minutes(item)) is not None]
    dates = [item.date for item in actuals if _actual_minutes(item) is not None]
    if not values or not dates:
        return None
    observed_days = max(1, min(21, (planning_start - min(dates)).days + 1))
    return round(sum(values) * 7 / observed_days)


def _baseline_weekly_minutes(
    profile: Mapping[str, Any], current: CurrentTrainingState, facts: Iterable[HistoricalFact], *, planning_start: date,
) -> tuple[int, str, str]:
    actual_weekly = _weekly_actual_baseline(facts, planning_start)
    estimate = current.approximate_total_physical_minutes
    if current.source.value == "recent_actuals" and actual_weekly is not None:
        return actual_weekly, "recent_actual_training", "high"
    if actual_weekly is not None and estimate is not None:
        # The equal blend is explicit R2 policy, not a physiological claim.
        return round((actual_weekly + estimate) / 2), "mixed_actual_and_athlete_estimate", "moderate"
    if estimate is not None:
        return estimate, "athlete_current_training_estimate", current.confidence
    athlete = profile.get("athlete", {}) if isinstance(profile.get("athlete"), Mapping) else {}
    experience = str(athlete.get("experience_level", "intermediate"))
    return _FALLBACK_WEEKLY_MINUTES.get(experience, _FALLBACK_WEEKLY_MINUTES["intermediate"]), "conservative_experience_start", "low"


def _date_capacity(profile: Mapping[str, Any], day: date) -> int:
    specific = next(
        (item for item in profile.get("date_availability", ())
         if isinstance(item, Mapping) and str(item.get("date")) == day.isoformat()),
        None,
    )
    item = specific or next(
        (value for value in profile.get("weekly_availability", ())
         if isinstance(value, Mapping) and str(value.get("weekday", "")).lower() == day.strftime("%A").lower()),
        {},
    )
    if not item or not bool(item.get("available", True)) or bool(item.get("fixed_rest", False)):
        return 0
    return _positive_int(item.get("max_training_minutes")) or 0


def _capacity(profile: Mapping[str, Any], start: date, end: date) -> int:
    return sum(_date_capacity(profile, start + timedelta(days=offset)) for offset in range((end - start).days + 1))


def _committed_minutes(context: NormalizedPlanningContext, start: date, end: date) -> int:
    return sum(
        item.reservation_minutes
        for item in context.authoritative_commitments
        if item.date is not None
        and start <= item.date <= end
        and item.type is not AuthorityType.FIXED_REST
    )


def _boundaries(profile: Mapping[str, Any], state, start: date, end: date) -> tuple[date, ...]:
    values = {start, end + timedelta(days=1)}
    for phase in build_season_phases(dict(profile)):
        try:
            phase_start = date.fromisoformat(str(phase["start_date"]))
            phase_end = date.fromisoformat(str(phase["end_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if start < phase_start <= end:
            values.add(phase_start)
        if start <= phase_end < end:
            values.add(phase_end + timedelta(days=1))
    for constraint in state.recovery_constraints:
        if start < constraint.start_date <= end:
            values.add(constraint.start_date)
        if start <= constraint.end_date < end:
            values.add(constraint.end_date + timedelta(days=1))
    for race in profile.get("races", ()):
        if not isinstance(race, Mapping):
            continue
        for raw in race.get("race_dates") or (race.get("end_date"),):
            try:
                race_day = date.fromisoformat(str(raw))
            except (TypeError, ValueError):
                continue
            if start < race_day <= end:
                values.add(race_day)
            if start <= race_day < end:
                values.add(race_day + timedelta(days=1))
    return tuple(sorted(values))


def _context_for_segment(profile: Mapping[str, Any], state, start: date, end: date, policy: TrainingPolicy) -> tuple[str, int, tuple[str, ...]]:
    rules = {item.key: item for item in policy.rules}
    recovery = any(
        constraint.start_date <= end and constraint.end_date >= start
        for constraint in state.recovery_constraints
    )
    if recovery:
        return "recovery", int(round(float(rules["taper_volume_multiplier"].value) * 100)), ("active_recovery_constraint",)
    race_days = []
    for race in profile.get("races", ()):
        if not isinstance(race, Mapping):
            continue
        for raw in race.get("race_dates") or (race.get("end_date"),):
            try:
                race_days.append(date.fromisoformat(str(raw)))
            except (TypeError, ValueError):
                continue
    taper_days = rules["taper_days_by_priority"].value
    for race_day in race_days:
        distance = (race_day - start).days
        if 0 <= distance <= int(taper_days["B"]):
            return "taper", int(round(float(rules["taper_volume_multiplier"].value) * 100)), ("upcoming_race_taper",)
    return "ordinary", 100, ()


def _override(profile: Mapping[str, Any]) -> tuple[str | None, int | None]:
    raw = profile.get("planning_dose_override")
    if not isinstance(raw, Mapping):
        return None, None
    values = {
        "preferred": raw.get("preferred_physical_minutes"),
        "minimum": raw.get("minimum_physical_minutes"),
        "maximum": raw.get("maximum_physical_minutes"),
        "fixed": raw.get("fixed_physical_minutes"),
    }
    supplied = [(kind, _positive_int(value)) for kind, value in values.items() if value is not None]
    if len(supplied) != 1 or supplied[0][1] is None:
        raise ValueError("planning_dose_override_is_invalid")
    return supplied[0]


def _allocate_total(total: int, capacities: tuple[int, ...], commitments: tuple[int, ...]) -> tuple[int, ...]:
    if total < sum(commitments):
        raise RecommendedDoseInfeasibleError("fixed_dose_below_authoritative_commitments")
    if total > sum(capacities):
        raise RecommendedDoseInfeasibleError("fixed_dose_exceeds_usable_capacity")
    remaining = total - sum(commitments)
    headroom = [capacity - committed for capacity, committed in zip(capacities, commitments)]
    if remaining > sum(headroom):
        raise RecommendedDoseInfeasibleError("fixed_dose_exceeds_usable_capacity")
    allocation = list(commitments)
    weighted = [remaining * room / sum(headroom) if sum(headroom) else 0 for room in headroom]
    floors = [int(value) for value in weighted]
    allocation = [value + floor for value, floor in zip(allocation, floors)]
    residual = remaining - sum(floors)
    order = sorted(range(len(headroom)), key=lambda index: (-(weighted[index] - floors[index]), index))
    for index in order:
        if residual <= 0:
            break
        if allocation[index] < capacities[index]:
            allocation[index] += 1
            residual -= 1
    return tuple(allocation)


def derive_recommended_training_dose(
    profile: Mapping[str, Any], context: NormalizedPlanningContext, *, planning_start: date,
    planning_end: date, policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
) -> RecommendedTrainingDose:
    """Derive the sole future physical-dose authority for the R2 path."""
    if planning_end < planning_start:
        raise ValueError("recommended_dose_end_before_start")
    sufficient = recent_actual_history_is_sufficient(context.historical_facts, planning_start=planning_start)
    current = normalize_current_training_state(
        profile, historical_facts=context.historical_facts,
        recent_actuals_sufficient=sufficient, observation_window_days=21,
    )
    state = reduce_physiological_state(context, profile=profile)
    baseline_weekly, baseline_source, confidence = _baseline_weekly_minutes(
        profile, current, context.historical_facts, planning_start=planning_start,
    )
    starts = _boundaries(profile, state, planning_start, planning_end)
    ranges = tuple(zip(starts, starts[1:]))
    capacities = tuple(_capacity(profile, start, end - timedelta(days=1)) for start, end in ranges)
    commitments = tuple(_committed_minutes(context, start, end - timedelta(days=1)) for start, end in ranges)
    requested = []
    statuses = []
    rationales = []
    for start, exclusive_end in ranges:
        end = exclusive_end - timedelta(days=1)
        status, modifier_percent, rationale = _context_for_segment(profile, state, start, end, policy)
        days = (end - start).days + 1
        target = round(baseline_weekly * days / 7 * modifier_percent / 100)
        requested.append(target)
        statuses.append(status)
        rationales.append((f"baseline_source:{baseline_source}", *rationale))
    mode, value = _override(profile)
    constraints: list[str] = []
    if mode == "fixed":
        targets = _allocate_total(value or 0, capacities, commitments)
        statuses = ["override_fixed"] * len(ranges)
        rationales = [(*entry, "fixed_dose_override") for entry in rationales]
    else:
        total_requested = sum(requested)
        if mode == "preferred":
            total_requested = value or 0
            constraints.append("preferred_dose_override")
        elif mode == "minimum":
            total_requested = max(total_requested, value or 0)
            constraints.append("minimum_dose_override")
        elif mode == "maximum":
            total_requested = min(total_requested, value or 0)
            constraints.append("maximum_dose_override")
        feasible = min(total_requested, sum(capacities))
        if feasible != total_requested:
            constraints.append("capacity_constrained")
        feasible = max(feasible, sum(commitments))
        targets = _allocate_total(feasible, capacities, commitments)
    segments = tuple(
        RecommendedDoseSegment(
            start_date=start,
            end_date=exclusive_end - timedelta(days=1),
            recommended_physical_minutes=target,
            minimum_acceptable_physical_minutes=target,
            maximum_reasonable_physical_minutes=target,
            available_capacity_minutes=capacity,
            committed_physical_minutes=committed,
            recommendation_status=("capacity_constrained" if "capacity_constrained" in constraints else status),
            rationale_codes=rationale,
        )
        for (start, exclusive_end), capacity, committed, target, status, rationale
        in zip(ranges, capacities, commitments, targets, statuses, rationales)
    )
    overall = "capacity_constrained" if "capacity_constrained" in constraints else (
        "return_to_training" if current.consistency == "returning" else next((item for item in statuses if item != "ordinary"), "maintenance")
    )
    return RecommendedTrainingDose(
        planning_start=planning_start,
        planning_end=planning_end,
        segments=segments,
        recommended_physical_minutes=sum(item.recommended_physical_minutes for item in segments),
        minimum_acceptable_physical_minutes=sum(item.minimum_acceptable_physical_minutes for item in segments),
        maximum_reasonable_physical_minutes=sum(item.maximum_reasonable_physical_minutes for item in segments),
        available_capacity_minutes=sum(item.available_capacity_minutes for item in segments),
        committed_physical_minutes=sum(item.committed_physical_minutes for item in segments),
        recommendation_status=overall,
        progression_state="maintain" if overall == "maintenance" else overall,
        confidence=confidence,
        rationale_codes=tuple(code for item in segments for code in item.rationale_codes),
        evidence_sources=(current.source.value, baseline_source),
        constraints=tuple(constraints),
        policy_version=POLICY_VERSION,
    )
