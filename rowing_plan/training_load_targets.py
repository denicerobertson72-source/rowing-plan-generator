"""Continuous, policy-owned training-load targets for the replacement planner."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .planning_domain import (
    AuthorityType, PhysiologicalState, TrainingLoadTarget, TrainingNeed, TrainingRole,
)
from .planning_normalization import NormalizedPlanningContext
from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy, TrainingPolicyRule


_ROWING_AUTHORITIES = frozenset({
    AuthorityType.RACE, AuthorityType.COURSE_PRACTICE, AuthorityType.PRIVATE_COACHING,
    AuthorityType.FIXED_COACHING,
})
_LOW_INTENSITY_ROLES = frozenset({
    TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC,
    TrainingRole.RECOVERY_AEROBIC, TrainingRole.TECHNICAL_REENTRY,
})
_ROWING_ROLES = _LOW_INTENSITY_ROLES | {
    TrainingRole.UT1_AEROBIC_STRENGTH, TrainingRole.QUALITY, TrainingRole.THRESHOLD_AT,
    TrainingRole.RACE_SPECIFIC_TR, TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP,
    TrainingRole.TAPER_MAINTENANCE, TrainingRole.RACE_PREPARATION,
}


def _rules(policy: TrainingPolicy) -> dict[str, TrainingPolicyRule]:
    return {item.key: item for item in policy.rules}


def _baseline(profile: Mapping[str, Any], rules: Mapping[str, TrainingPolicyRule]) -> tuple[int, str]:
    athlete = profile.get("athlete", {}) if isinstance(profile.get("athlete"), Mapping) else {}
    season = profile.get("season", {}) if isinstance(profile.get("season"), Mapping) else {}
    for source, value in (
        ("athlete.current_approx_weekly_rowing_minutes", athlete.get("current_approx_weekly_rowing_minutes")),
        ("season.current_weekly_endurance_minutes", season.get("current_weekly_endurance_minutes")),
    ):
        if value is not None:
            try:
                return max(0, int(value)), source
            except (TypeError, ValueError):
                pass
    for source, value in (
        ("athlete.desired_rowing_sessions_per_week", athlete.get("desired_rowing_sessions_per_week")),
        ("athlete.current_rowing_sessions_per_week", athlete.get("current_rowing_sessions_per_week")),
    ):
        if value is not None:
            try:
                return max(0, int(value)) * int(rules["residual_aerobic_session_minutes"].value["preferred"]), source
            except (TypeError, ValueError):
                pass
    experience = str(athlete.get("experience_level", "intermediate"))
    defaults = rules["default_weekly_rowing_minutes_by_experience"].value
    return int(defaults.get(experience, defaults["intermediate"])), "policy.default_weekly_rowing_minutes_by_experience"


def _availability_ceiling(profile: Mapping[str, Any], start: date, days: int) -> int:
    specific = profile.get("date_availability", ())
    weekly = profile.get("weekly_availability", ())
    total = 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        item = next((value for value in specific if isinstance(value, Mapping) and str(value.get("date")) == day.isoformat()), None)
        if item is None:
            item = next((value for value in weekly if isinstance(value, Mapping) and str(value.get("weekday", "")).lower() == day.strftime("%A").lower()), {})
        if item.get("available", True):
            try:
                total += max(0, int(item.get("max_training_minutes", 120)))
            except (TypeError, ValueError):
                total += 120
    return total


def _modifiers(context: NormalizedPlanningContext, state: PhysiologicalState, rules: Mapping[str, TrainingPolicyRule]) -> tuple[float, float, str]:
    taper = 1.0
    phase = str(state.phase_context.get("phase_type") or "general_preparation")
    race = state.next_race_context
    days_until = race.get("days_until") if race else None
    if isinstance(days_until, int) and days_until >= 0:
        priority = str(race.get("priority") or "B")
        if days_until <= int(rules["taper_days_by_priority"].value.get(priority, rules["taper_days_by_priority"].value["B"])):
            taper = float(rules["taper_volume_multiplier"].value)
            phase = "taper"
    recovery = 0.75 if any(item.source.startswith("race:") and item.start_date <= context.planning_date <= item.end_date for item in state.recovery_constraints) else 1.0
    return taper, recovery, phase


def _authoritative_rowing(context: NormalizedPlanningContext, start: date, days: int) -> int:
    end = start + timedelta(days=days)
    return sum(
        item.reservation_minutes for item in context.authoritative_commitments
        if item.date is not None and start <= item.date < end and item.type in _ROWING_AUTHORITIES
    )


def derive_training_load_targets(
    context: NormalizedPlanningContext,
    state: PhysiologicalState,
    policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
    *, profile: Mapping[str, Any], planning_date: date | None = None,
) -> tuple[TrainingLoadTarget, ...]:
    """Derive future 7/14-day target ranges without reading provisional plans."""
    planning_date = planning_date or context.planning_date
    if planning_date != context.planning_date or state.as_of_date != planning_date:
        raise ValueError("training_load_target_planning_date_mismatch")
    rules = _rules(policy)
    required = {
        "load_target_range_fractions", "low_intensity_minimum_fraction", "load_progression_guard",
        "rolling_strength_target_14d", "taper_days_by_priority", "taper_volume_multiplier",
    }
    missing = required - rules.keys()
    if missing:
        raise ValueError(f"missing_training_load_policy:{','.join(sorted(missing))}")
    baseline, baseline_source = _baseline(profile, rules)
    taper, recovery, phase = _modifiers(context, state, rules)
    fractions = rules["load_target_range_fractions"].value
    progression = rules["load_progression_guard"].value
    strength = rules["rolling_strength_target_14d"].value
    targets = []
    for days, recent in ((7, state.recent_7d_load), (14, state.recent_14d_load)):
        raw = baseline * (days / 7)
        recent_average = recent.rowing_minutes if days == 7 else round(recent.rowing_minutes / 2)
        # A modest cap applies only when there is meaningful recent evidence;
        # one unusually light week cannot reset established baseline below floor.
        guard = max(raw * float(progression["baseline_floor"]), recent_average * (1 + float(progression["maximum_increase"])))
        guarded = min(raw, guard) if recent_average else raw
        # Taper and active recovery both reduce the same volume budget.  Use
        # the stronger applicable modifier rather than multiplying two broad
        # reductions and accidentally turning a short recovery/taper overlap
        # into de-training.
        target = max(0, round(guarded * min(taper, recovery)))
        minimum = min(target, round(target * float(fractions["minimum"])))
        maximum = max(target, round(target * float(fractions["maximum"])))
        authoritative = _authoritative_rowing(context, planning_date, days)
        availability = _availability_ceiling(profile, planning_date, days)
        completed_strength = recent.strength_exposures
        future_strength = 0  # No current authoritative commitment type safely identifies a lift.
        targets.append(TrainingLoadTarget(
            id=f"load:{policy.version}:{days}d", horizon_start=planning_date,
            horizon_end=planning_date + timedelta(days=days - 1), horizon_days=days, phase=phase,
            total_rowing_target_minutes=target, minimum_rowing_minutes=minimum,
            maximum_rowing_minutes=maximum,
            low_intensity_minimum_minutes=round(target * float(rules["low_intensity_minimum_fraction"].value)),
            quality_allowance_exposures=1, strength_target_exposures=int(strength["target"]),
            strength_minimum_exposures=int(strength["minimum"]),
            completed_rowing_minutes=recent.rowing_minutes,
            completed_low_intensity_minutes=recent.low_intensity_rowing_minutes,
            completed_quality_exposures=recent.quality_exposures,
            completed_strength_exposures=completed_strength,
            authoritative_future_rowing_minutes=authoritative,
            provisional_planned_rowing_minutes=0, planned_low_intensity_minutes=0,
            remaining_rowing_minutes=max(0, target - recent.rowing_minutes - authoritative),
            remaining_low_intensity_minutes=max(0, round(target * float(rules["low_intensity_minimum_fraction"].value)) - recent.low_intensity_rowing_minutes),
            remaining_strength_exposures=max(0, int(strength["minimum"]) - completed_strength - future_strength),
            taper_modifier=taper, recovery_modifier=recovery, availability_ceiling_minutes=availability,
            source_policy_ids=("load_target_range_fractions", "low_intensity_minimum_fraction", "load_progression_guard", "taper_volume_multiplier", "rolling_strength_target_14d"),
            rationale=(f"baseline_source:{baseline_source}", "combined_recent_completed_and_future_decision_budget", "taper_and_recovery_modifiers_do_not_compound"),
            provenance=("training_policy", "physiological_state", "authoritative_commitments"),
        ))
    return tuple(targets)


def reconcile_training_load_targets(targets: Iterable[TrainingLoadTarget], needs: Iterable[TrainingNeed]) -> tuple[TrainingLoadTarget, ...]:
    """Apply proposed needs once to residual targets; planned work is not history."""
    needs = tuple(needs)
    result = []
    for target in targets:
        in_horizon = tuple(item for item in needs if target.horizon_start <= item.earliest_date <= target.horizon_end)
        planned_total = sum(item.target_credit.minutes for item in in_horizon if item.role in _ROWING_ROLES)
        planned_low = sum(item.target_credit.minutes for item in in_horizon if item.role in _LOW_INTENSITY_ROLES)
        planned_strength = sum(item.target_credit.exposures for item in in_horizon if item.role is TrainingRole.STRENGTH)
        combined_total = target.completed_rowing_minutes + target.authoritative_future_rowing_minutes + planned_total
        combined_low = target.completed_low_intensity_minutes + planned_low
        result.append(replace(
            target, provisional_planned_rowing_minutes=planned_total,
            planned_low_intensity_minutes=planned_low,
            remaining_rowing_minutes=max(0, target.total_rowing_target_minutes - combined_total),
            remaining_low_intensity_minutes=max(0, target.low_intensity_minimum_minutes - combined_low),
            remaining_strength_exposures=max(0, target.strength_minimum_exposures - target.completed_strength_exposures - planned_strength),
        ))
    return tuple(result)
