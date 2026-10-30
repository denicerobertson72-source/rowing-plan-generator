"""Continuous, policy-owned training-load targets for the replacement planner."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .planning_domain import (
    AuthorityType, PhysiologicalState, Placement, PlacementAuthorityStatus, Provenance, ProvenanceSource, TrainingDistributionTarget, TrainingLoadTarget, TrainingNeed, TrainingRole,
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


@dataclass(frozen=True)
class SelectedDoseLedgerEntry:
    """One horizon's view of the same selected physical placements.

    The ledger deliberately has no residual-owner field.  A placement is
    counted by its selected date in every target horizon that contains it.
    """

    target_id: str
    authoritative_rowing_minutes: int
    selected_categorical_rowing_minutes: int
    selected_residual_rowing_minutes: int
    selected_categorical_low_intensity_minutes: int
    selected_residual_low_intensity_minutes: int
    total_rowing_minutes: int
    low_intensity_minutes: int
    overage_minutes: int
    overage_sources: tuple[str, ...]


def _is_residual_need(need: TrainingNeed | None) -> bool:
    return bool(need and need.progression_relation and "load_target_residual" in need.progression_relation)


def _placement_minutes(item: Placement) -> int:
    return item.target_credit.minutes if item.role in _ROWING_ROLES else 0


def selected_dose_ledger(
    targets: Iterable[TrainingLoadTarget],
    placements: Iterable[Placement],
    *,
    need_by_id: Mapping[str, TrainingNeed] | None = None,
) -> tuple[SelectedDoseLedgerEntry, ...]:
    """Reconcile selected physical dose by date across every target horizon.

    Authoritative work is already represented in each target's immutable
    ``authoritative_future_rowing_minutes``.  It is therefore never added a
    second time from the seeded placement sequence.
    """
    selected = tuple(item for item in placements if item.authority_status is not PlacementAuthorityStatus.AUTHORITATIVE)
    index = need_by_id or {}
    values = []
    for target in targets:
        categorical_total = categorical_low = residual_total = residual_low = 0
        for placement in selected:
            if not target.horizon_start <= placement.date <= target.horizon_end:
                continue
            minutes = _placement_minutes(placement)
            if not minutes:
                continue
            need = index.get(placement.training_need_id)
            residual = _is_residual_need(need)
            low = placement.role in _LOW_INTENSITY_ROLES
            if residual:
                residual_total += minutes
                residual_low += minutes if low else 0
            else:
                categorical_total += minutes
                categorical_low += minutes if low else 0
        total = target.completed_rowing_minutes + target.authoritative_future_rowing_minutes + categorical_total + residual_total
        low_total = target.completed_low_intensity_minutes + categorical_low + residual_low
        overage = max(0, total - target.maximum_rowing_minutes)
        sources: list[str] = []
        if overage:
            completed = target.completed_rowing_minutes
            authority = target.authoritative_future_rowing_minutes
            if completed >= target.maximum_rowing_minutes:
                sources.append("completed_history")
            elif completed + authority >= target.maximum_rowing_minutes:
                sources.append("authority")
            elif completed + authority + categorical_total > target.maximum_rowing_minutes:
                sources.append("categorical")
            else:
                sources.append("residual")
        values.append(SelectedDoseLedgerEntry(
            target.id, target.authoritative_future_rowing_minutes,
            categorical_total, residual_total, categorical_low, residual_low,
            total, low_total, overage, tuple(sources),
        ))
    return tuple(values)


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


def derive_training_distribution_targets(
    targets: Iterable[TrainingLoadTarget], policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
) -> tuple[TrainingDistributionTarget, ...]:
    """Turn continuous dose into soft exposure/gap expectations.

    The practical-session magnitude is an implementation choice; this is a
    sequence preference, not a sessions-per-week rule or feasibility gate.
    """
    rules = _rules(policy)
    practical = int(rules["residual_aerobic_session_minutes"].value["preferred"])
    values = []
    for item in targets:
        minimum = max(0, (item.minimum_rowing_minutes + practical - 1) // practical)
        target = max(minimum, (item.total_rowing_target_minutes + practical - 1) // practical)
        low = max(0, (item.low_intensity_minimum_minutes + practical - 1) // practical)
        values.append(TrainingDistributionTarget(
            item.horizon_start, item.horizon_end, item.horizon_days, minimum, target, low,
            max(1, (item.horizon_days + max(1, minimum) - 1) // max(1, minimum)),
            item.taper_modifier < 1.0,
            ("residual_aerobic_session_minutes", "load_target_range_fractions", "taper_volume_multiplier"),
            ("dose_derived_continuous_exposure_expectation", "soft_gap_and_clustering_preference"),
        ))
    return tuple(values)


def reconcile_training_load_targets(
    targets: Iterable[TrainingLoadTarget],
    needs: Iterable[TrainingNeed] = (),
    *,
    placements: Iterable[Placement] | None = None,
    need_by_id: Mapping[str, TrainingNeed] | None = None,
    suppressed_residual_by_target: Mapping[str, int] | None = None,
) -> tuple[TrainingLoadTarget, ...]:
    """Reconcile selected dose once across overlapping horizons.

    ``needs`` preserves the original prospective helper contract.  V3 passes
    actual ``placements`` so the ledger uses selected dates rather than a
    broad candidate window.  This is the only representation suitable for
    overlapping rolling horizons.
    """
    targets = tuple(targets)
    needs = tuple(needs)
    suppressed = suppressed_residual_by_target or {}
    if placements is None:
        pseudo: list[Placement] = []
        source = dict(need_by_id or {item.id: item for item in needs})
        for need in needs:
            pseudo.append(Placement(
                placement_id=f"reconcile:{need.id}", training_need_id=need.id,
                date=need.earliest_date, reservation_minutes=need.target_credit.minutes,
                target_credit=need.target_credit,
                provenance=Provenance(need.id, ProvenanceSource.LEGACY_V2),
                authority_status=PlacementAuthorityStatus.PROVISIONAL, role=need.role,
            ))
        selected = tuple(pseudo)
    else:
        selected = tuple(placements)
        source = dict(need_by_id or {})
    ledger = {item.target_id: item for item in selected_dose_ledger(targets, selected, need_by_id=source)}
    result = []
    for target in targets:
        entry = ledger[target.id]
        planned_total = entry.selected_categorical_rowing_minutes + entry.selected_residual_rowing_minutes
        planned_low = entry.selected_categorical_low_intensity_minutes + entry.selected_residual_low_intensity_minutes
        if placements is None:
            in_horizon = tuple(item for item in needs if target.horizon_start <= item.earliest_date <= target.horizon_end)
            planned_strength = sum(item.target_credit.exposures for item in in_horizon if item.role is TrainingRole.STRENGTH)
        else:
            planned_strength = sum(
                item.target_credit.exposures for item in selected
                if item.authority_status is not PlacementAuthorityStatus.AUTHORITATIVE
                and target.horizon_start <= item.date <= target.horizon_end
                and item.role is TrainingRole.STRENGTH
            )
        result.append(replace(
            target,
            provisional_planned_rowing_minutes=planned_total,
            planned_low_intensity_minutes=planned_low,
            remaining_rowing_minutes=max(0, target.total_rowing_target_minutes - entry.total_rowing_minutes),
            remaining_low_intensity_minutes=max(0, target.low_intensity_minimum_minutes - entry.low_intensity_minutes),
            remaining_strength_exposures=max(0, target.strength_minimum_exposures - target.completed_strength_exposures - planned_strength),
            selected_categorical_rowing_minutes=entry.selected_categorical_rowing_minutes,
            selected_residual_rowing_minutes=entry.selected_residual_rowing_minutes,
            selected_categorical_low_intensity_minutes=entry.selected_categorical_low_intensity_minutes,
            selected_residual_low_intensity_minutes=entry.selected_residual_low_intensity_minutes,
            overage_minutes=entry.overage_minutes,
            overage_sources=entry.overage_sources,
            suppressed_residual_due_to_shorter_horizon_maximum=int(suppressed.get(target.id, 0)),
            low_intensity_conflict_with_maximum=(
                max(0, target.low_intensity_minimum_minutes - entry.low_intensity_minutes) > 0
                and entry.total_rowing_minutes >= target.maximum_rowing_minutes
            ),
        ))
    return tuple(result)
