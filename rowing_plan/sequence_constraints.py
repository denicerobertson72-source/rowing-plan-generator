"""Physiological sequence constraints for undated replacement-planner needs.

The engine evaluates one candidate date in chronological context.  It never
enumerates dates, ranks a calendar, mutates history, or calls live Scheduler
V2 placement code.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

from .planning_domain import (
    AuthorityType,
    PhysiologicalCredit,
    PhysiologicalState,
    ProjectedSequenceState,
    SequenceConstraintDecision,
    SequenceEligibility,
    TrainingNeed,
    TrainingRole,
)
from .planning_normalization import NormalizedPlanningContext
from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy, TrainingPolicyRule


_QUALITY_ROLES = frozenset({
    TrainingRole.QUALITY,
    TrainingRole.THRESHOLD_AT,
    TrainingRole.RACE_SPECIFIC_TR,
    TrainingRole.ANAEROBIC_AN,
    TrainingRole.POWER_PP,
    TrainingRole.TAPER_MAINTENANCE,
    TrainingRole.RACE_PREPARATION,
    TrainingRole.RACE,
})
_LOW_COST_ROLES = frozenset({
    TrainingRole.DEDICATED_UT2,
    TrainingRole.RECOVERY_AEROBIC,
    TrainingRole.TECHNICAL_REENTRY,
    TrainingRole.TAPER_MAINTENANCE,
})


def _rules(policy: TrainingPolicy) -> dict[str, TrainingPolicyRule]:
    return {item.key: item for item in policy.rules}


def _is_quality(role: TrainingRole) -> bool:
    return role in _QUALITY_ROLES


def _matches_constraint(role: TrainingRole, excluded: TrainingRole) -> bool:
    if excluded is TrainingRole.QUALITY:
        return _is_quality(role)
    return role is excluded


def _priority_value(fact: Any) -> str:
    return str((fact.planned_prescription or {}).get("priority", "B"))


def _race_days(context: NormalizedPlanningContext, state: PhysiologicalState) -> tuple[tuple[date, str], ...]:
    result: set[tuple[date, str]] = set()
    for value in context.race_context:
        result.add((date.fromisoformat(value[0]), value[1]))
    for item in context.authoritative_commitments:
        if item.type is AuthorityType.RACE and item.date:
            result.add((item.date, "B"))
    if state.next_race_context.get("date"):
        result.add((date.fromisoformat(str(state.next_race_context["date"])), str(state.next_race_context.get("priority", "B"))))
    return tuple(sorted(result))


def _reduced_credit(credit: PhysiologicalCredit, multiplier: float) -> PhysiologicalCredit:
    return PhysiologicalCredit(
        role=credit.role,
        exposures=credit.exposures,
        minutes=max(0, round(credit.minutes * multiplier)),
        eligible=credit.eligible,
    )


def _projected_spacing_conflicts(
    projected: ProjectedSequenceState,
    role: TrainingRole,
    candidate_date: date,
    days: int,
) -> tuple[str, ...]:
    if not days:
        return ()
    result = []
    for item in projected.items:
        same_class = (_is_quality(role) and _is_quality(item.role)) or (role is TrainingRole.STRENGTH and item.role is TrainingRole.STRENGTH)
        if same_class and abs((candidate_date - item.date).days) <= days:
            result.append(f"projected_spacing:{item.id}")
    return tuple(sorted(result))


def _soft_race_recovery(
    state: PhysiologicalState,
    candidate_date: date,
    rules: Mapping[str, TrainingPolicyRule],
) -> tuple[str, ...]:
    spans = rules["race_soft_recovery_days_by_priority"].value
    reasons = []
    for race in state.recent_race_load:
        priority = _priority_value(race)
        hard_days = int(rules["race_recovery_days_by_priority"].value.get(priority, rules["race_recovery_days_by_priority"].value["B"]))
        soft_days = int(spans.get(priority, spans["B"]))
        elapsed = (candidate_date - race.date).days
        if hard_days < elapsed <= soft_days:
            reasons.append(f"soft_race_recovery:{race.id}")
    return tuple(sorted(reasons))


def _multiple_race_extension(
    state: PhysiologicalState,
    candidate_date: date,
    rules: Mapping[str, TrainingPolicyRule],
) -> bool:
    recent = [item for item in state.recent_race_load if 0 <= (candidate_date - item.date).days <= 7]
    if len(recent) < 2:
        return False
    last_race = max(recent, key=lambda item: item.date)
    extra = int(rules["multiple_race_load_additional_recovery_day"].value)
    priority = _priority_value(last_race)
    hard_days = int(rules["race_recovery_days_by_priority"].value.get(priority, rules["race_recovery_days_by_priority"].value["B"]))
    return candidate_date <= last_race.date + timedelta(days=hard_days + extra)


def evaluate_sequence_constraint(
    context: NormalizedPlanningContext,
    state: PhysiologicalState,
    need: TrainingNeed,
    candidate_date: date,
    policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
    *,
    projected: ProjectedSequenceState = ProjectedSequenceState(),
) -> SequenceConstraintDecision:
    """Evaluate physiological appropriateness at one candidate date.

    This is intentionally local: callers choose whether to evaluate a date and
    later layers choose whether to place anything there.
    """
    if state.as_of_date != context.planning_date:
        raise ValueError("physiological_state_planning_date_mismatch")
    rules = _rules(policy)
    required = {
        "quality_minimum_spacing_days", "strength_minimum_spacing_days",
        "race_recovery_days_by_priority", "race_soft_recovery_days_by_priority",
        "multiple_race_load_additional_recovery_day", "soft_recovery_credit_multiplier",
        "taper_days_by_priority", "taper_volume_multiplier",
    }
    missing = required - rules.keys()
    if missing:
        raise ValueError(f"missing_sequence_constraint_policy:{','.join(sorted(missing))}")

    hard: list[str] = []
    soft: list[str] = []
    policy_ids: set[str] = {item for item in need.source_policy.split("|") if item}
    diagnostics: list[str] = []
    rationale: list[str] = []
    authority: list[str] = []
    load_ceiling: str | None = None
    adjustment: PhysiologicalCredit | None = None
    recovery_status = "ordinary"
    spacing_status = "clear"
    race_taper_status = "ordinary"

    if not need.earliest_date <= candidate_date <= need.latest_date:
        hard.append("outside_need_window")
        diagnostics.append("need_window_prohibited")
        rationale.append("The candidate is outside this undated need's acceptable chronological window.")

    for constraint in state.recovery_constraints:
        if not constraint.start_date <= candidate_date <= constraint.end_date:
            continue
        matches_hard = any(_matches_constraint(need.role, item) for item in constraint.hard_excluded_roles)
        allowed_low = need.role in constraint.optional_low_load_roles or need.role in _LOW_COST_ROLES
        if matches_hard:
            hard.append(f"recovery_hard_exclusion:{constraint.source}")
            diagnostics.append("recovery_hard_prohibited")
            recovery_status = "hard_recovery"
            policy_ids.add(constraint.rationale or "race_hard_recovery_exclusions")
            rationale.append(f"{constraint.source} excludes this role during its active recovery span.")
        elif constraint.hard_excluded_roles and allowed_low:
            recovery_status = "hard_recovery"
            load_ceiling = constraint.soft_load_ceiling or "recovery_only"
            policy_ids.add(constraint.rationale or "race_hard_recovery_exclusions")
            diagnostics.append("recovery_low_cost_allowed")
        elif constraint.soft_load_ceiling:
            recovery_status = "soft_recovery" if recovery_status == "ordinary" else recovery_status
            load_ceiling = constraint.soft_load_ceiling
            policy_ids.add(constraint.rationale or "race_hard_recovery_exclusions")
            if allowed_low:
                diagnostics.append("recovery_low_cost_allowed")
            else:
                soft.append(f"recovery_load_ceiling:{constraint.source}")
                diagnostics.append("recovery_load_reduced")
                adjustment = _reduced_credit(need.target_credit, float(rules["soft_recovery_credit_multiplier"].value))

    if _multiple_race_extension(state, candidate_date, rules) and (need.role is TrainingRole.STRENGTH or _is_quality(need.role)):
        hard.append("multiple_race_recovery_extension")
        diagnostics.append("multiple_race_hard_prohibited")
        recovery_status = "hard_recovery"
        policy_ids.add("multiple_race_load_additional_recovery_day")
        rationale.append("Multiple recent races retain a short additional hard-recovery consequence.")

    quality_days = int(rules["quality_minimum_spacing_days"].value)
    strength_days = int(rules["strength_minimum_spacing_days"].value)
    spacing_days = quality_days if _is_quality(need.role) else strength_days if need.role is TrainingRole.STRENGTH else 0
    projected_conflicts = _projected_spacing_conflicts(projected, need.role, candidate_date, spacing_days)
    if projected_conflicts:
        hard.extend(projected_conflicts)
        spacing_status = "minimum_spacing_conflict"
        diagnostics.append("projected_spacing_prohibited")
        policy_ids.add("quality_minimum_spacing_days" if _is_quality(need.role) else "strength_minimum_spacing_days")
        rationale.append("A projected chronological exposure violates the current minimum-spacing guard.")

    for constraint in state.recovery_constraints:
        if not constraint.minimum_spacing or not constraint.start_date <= candidate_date <= constraint.end_date:
            continue
        relevant = (_is_quality(need.role) and constraint.source.startswith("quality:")) or (need.role is TrainingRole.STRENGTH and constraint.source.startswith("strength:"))
        if relevant:
            hard.append(f"completed_spacing:{constraint.source}")
            spacing_status = "minimum_spacing_conflict"
            diagnostics.append("completed_spacing_prohibited")
            policy_ids.add("quality_minimum_spacing_days" if _is_quality(need.role) else "strength_minimum_spacing_days")
            rationale.append("Completed history is still inside the policy spacing span.")

    if need.role is TrainingRole.STRENGTH:
        nearby_quality = [item for item in projected.items if _is_quality(item.role) and abs((candidate_date - item.date).days) <= 1]
        if nearby_quality:
            soft.append("strength_quality_interference")
            diagnostics.append("strength_quality_discouraged")
            policy_ids.add("concurrent_strength_endurance_is_individualized")

    soft_race = _soft_race_recovery(state, candidate_date, rules)
    if soft_race:
        recovery_status = "soft_recovery" if recovery_status != "hard_recovery" else recovery_status
        policy_ids.add("race_soft_recovery_days_by_priority")
        if need.role in _LOW_COST_ROLES:
            diagnostics.append("soft_recovery_low_cost_allowed")
        elif need.role in {TrainingRole.LONG_AEROBIC, TrainingRole.UT1_AEROBIC_STRENGTH}:
            soft.extend(soft_race)
            adjustment = _reduced_credit(need.target_credit, float(rules["soft_recovery_credit_multiplier"].value))
            diagnostics.append("soft_recovery_reduced")
            load_ceiling = "recovery_only"
        else:
            soft.extend(soft_race)
            diagnostics.append("soft_recovery_discouraged")

    races = _race_days(context, state)
    for race_day, priority in races:
        if candidate_date == race_day and need.role is not TrainingRole.RACE:
            hard.append("race_day_automatic_session")
            diagnostics.append("race_day_prohibited")
            race_taper_status = "race_day"
            policy_ids.add("race_is_completed_stress_once")
        days = (race_day - candidate_date).days
        taper_days = int(rules["taper_days_by_priority"].value.get(priority, rules["taper_days_by_priority"].value["B"]))
        if 0 < days <= taper_days:
            race_taper_status = "taper"
            policy_ids.update({"taper_reduce_volume_retain_useful_intensity_frequency", "taper_volume_multiplier"})
            if need.role in {TrainingRole.LONG_AEROBIC, TrainingRole.STRENGTH, TrainingRole.UT1_AEROBIC_STRENGTH}:
                soft.append("taper_fatigue_reduction")
                adjustment = _reduced_credit(need.target_credit, float(rules["taper_volume_multiplier"].value))
                diagnostics.append("taper_reduced")
                load_ceiling = "taper_reduced"
            elif _is_quality(need.role) or need.role is TrainingRole.TAPER_MAINTENANCE:
                diagnostics.append("taper_maintenance_allowed")

    for commitment in context.authoritative_commitments:
        if commitment.date != candidate_date or commitment.type is AuthorityType.RACE:
            continue
        authority.append(f"authority_context:{commitment.type.value}:{commitment.id}")
        soft.append("authoritative_commitment_load")
        diagnostics.append("authority_conflict_reported")
        rationale.append("An authoritative future commitment remains intact; later placement must report or respect this load context.")

    if hard:
        eligibility = SequenceEligibility.PROHIBITED
    elif adjustment is not None:
        eligibility = SequenceEligibility.ELIGIBLE_REDUCED
    elif any(code.endswith("low_cost_allowed") for code in diagnostics):
        eligibility = SequenceEligibility.ELIGIBLE_LOW_COST_ONLY
    elif soft:
        eligibility = SequenceEligibility.DISCOURAGED
    else:
        eligibility = SequenceEligibility.ELIGIBLE

    return SequenceConstraintDecision(
        training_need_id=need.id, candidate_date=candidate_date, eligibility=eligibility,
        hard_rejection_reasons=tuple(hard), soft_constraints=tuple(soft), load_ceiling=load_ceiling,
        recommended_credit_adjustment=adjustment, recovery_status=recovery_status,
        spacing_status=spacing_status, race_taper_status=race_taper_status,
        policy_rule_ids=tuple(policy_ids), rationale=tuple(rationale), diagnostic_codes=tuple(diagnostics),
        authority_conflicts=tuple(authority),
    )
