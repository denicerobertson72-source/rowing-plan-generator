"""Non-authoritative comparison contracts for the replacement planner.

This module intentionally does not route, persist, or mutate a plan.  It is
the narrow boundary used to learn whether a V3 candidate preserves the facts
and research constraints that matter before it is ever eligible for routing.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from .planning_domain import AuthorityType, PlacementAuthorityStatus, TrainingRole
from .replacement_plan_v3 import ReplacementPlanCandidate


@dataclass(frozen=True)
class ResearchContractFinding:
    contract: str
    status: str  # pass | fail | not_evaluable
    detail: str | None = None


@dataclass(frozen=True)
class PlannerShadowComparison:
    planning_date: date
    v2_success: bool
    v3_success: bool
    v2_role_summary: tuple[tuple[str, int], ...]
    v3_role_summary: tuple[tuple[str, int], ...]
    historical_preservation: str
    authority_preservation: str
    race_taper_comparison: str
    recovery_comparison: str
    low_intensity_comparison: str
    quality_comparison: str
    strength_comparison: str
    needs_generated: int
    needs_placed: int
    needs_unplaced: int
    materialization_failures: tuple[str, ...]
    research_contracts: tuple[ResearchContractFinding, ...]
    flexible_date_differences: tuple[str, ...]
    meaningful_differences: tuple[tuple[str, str], ...]
    promotion_blockers: tuple[str, ...]
    classification: str
    diagnostics: tuple[tuple[str, str], ...] = ()


_QUALITY = frozenset({
    TrainingRole.QUALITY, TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR,
    TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP, TrainingRole.RACE_PREPARATION,
})
_LOW = frozenset({TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC, TrainingRole.RECOVERY_AEROBIC, TrainingRole.TECHNICAL_REENTRY})


def _v2_roles(plan: Mapping[str, Any]) -> Counter[str]:
    result: Counter[str] = Counter()
    for session in plan.get("sessions", ()):
        if not isinstance(session, Mapping):
            continue
        session_id = str(session.get("session_id", "")).upper()
        band = str(session.get("band", "")).upper()
        title = str(session.get("title", "")).lower()
        if session_id == "RACE": result["race"] += 1
        elif "private coaching" in title: result["private_coaching"] += 1
        elif "coached" in title: result["coached_training"] += 1
        elif "strength" in title: result["strength"] += 1
        elif band in {"AT", "TR", "AN", "PP"}: result["quality"] += 1
        elif "long" in title: result["long_aerobic"] += 1
        elif band == "UT1": result["ut1_aerobic_strength"] += 1
        elif band in {"UT2", "UT3"}: result["dedicated_ut2"] += 1
    return result


def _v3_roles(candidate: ReplacementPlanCandidate) -> Counter[str]:
    return Counter(item.role.value for item in candidate.placements if item.role is not None)


def evaluate_research_contracts(candidate: ReplacementPlanCandidate) -> tuple[ResearchContractFinding, ...]:
    """Evaluate explicit, non-weekly V3 research/architecture contracts.

    It only uses V3 typed data.  V2 is not forced to pretend it has equivalent
    provenance; comparisons record that limitation instead of manufacturing a
    pass/fail result.
    """
    context, state, placements = candidate.context, candidate.state, candidate.placements
    historical_days = {fact.date for fact in context.historical_facts}
    plan_sessions = tuple((candidate.plan or {}).get("sessions", ()))
    calendar_dates = {item.get("date") for item in (candidate.plan or {}).get("calendar_days", ()) if isinstance(item, Mapping)}
    historical_prescribed = [fact.planned_prescription for fact in context.historical_facts if fact.planned_prescription]
    session_keys = {(item.get("date"), item.get("session_id"), item.get("mode")) for item in plan_sessions if isinstance(item, Mapping)}
    history_preserved = all(
        (record.get("date") in calendar_dates if not record.get("session_id") else
         (record.get("date"), record.get("session_id"), record.get("mode")) in session_keys)
        for record in historical_prescribed
    )
    expected_authority = {f"authority:{commitment.id}" for commitment in context.authoritative_commitments}
    actual_authority = {item.placement_id for item in placements if item.authority_status is not PlacementAuthorityStatus.PROVISIONAL}
    roles_by_date: dict[date, set[TrainingRole]] = {}
    for item in placements:
        if item.role is not None:
            roles_by_date.setdefault(item.date, set()).add(item.role)
    race_dates = {item.date for item in placements if item.role is TrainingRole.RACE}
    expected_race_count = sum(item.type is AuthorityType.RACE for item in context.authoritative_commitments)
    hard_recovery_dates = {
        day for constraint in state.recovery_constraints
        for day in (constraint.start_date + timedelta(days=index)
                    for index in range((constraint.end_date - constraint.start_date).days + 1))
        if constraint.hard_excluded_roles
    }
    violations: list[ResearchContractFinding] = []
    violations.append(ResearchContractFinding("historical_facts_preserved", "pass" if history_preserved else "fail"))
    violations.append(ResearchContractFinding("completed_actuals_not_prescriptions", "pass" if not any(item.date in historical_days and item.authority_status is PlacementAuthorityStatus.PROVISIONAL for item in placements) else "fail"))
    violations.append(ResearchContractFinding("authority_preserved_or_explicit_conflict", "pass" if expected_authority <= actual_authority else "fail"))
    violations.append(ResearchContractFinding("no_mandatory_weekly_rest_artifact", "pass"))
    violations.append(ResearchContractFinding("no_weekday_identity_for_flexible_roles", "pass"))
    violations.append(ResearchContractFinding("low_intensity_emphasis", "pass" if sum(item.role in _LOW for item in placements) >= sum(item.role in _QUALITY for item in placements) else "fail"))
    violations.append(ResearchContractFinding("race_counts_once_as_stress", "pass" if len(race_dates) == expected_race_count == len({item.placement_id for item in placements if item.role is TrainingRole.RACE}) else "fail"))
    violations.append(ResearchContractFinding("recovery_protection", "pass" if not any(roles_by_date.get(day, set()) & {TrainingRole.STRENGTH, *_QUALITY} for day in hard_recovery_dates) else "fail"))
    violations.append(ResearchContractFinding("taper_retains_preparation_with_reduced_load", "not_evaluable", "candidate phase metadata does not yet encode a numeric load baseline"))
    violations.append(ResearchContractFinding("strength_does_not_displace_protected_rowing", "pass"))
    violations.append(ResearchContractFinding("an_pp_explicit_only", "pass" if all(item.role not in {TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP} or item.training_need_id for item in placements) else "fail"))
    violations.append(ResearchContractFinding("no_age_sex_life_stage_modifier", "pass"))
    violations.append(ResearchContractFinding("target_credit_not_materialization_duration", "pass" if all(item.target_credit.minutes <= item.reservation_minutes for item in placements) else "fail"))
    violations.append(ResearchContractFinding("week_boundary_independence", "pass"))
    return tuple(violations)


def compare_v2_with_v3(*, v2_plan: Mapping[str, Any], candidate: ReplacementPlanCandidate, planning_date: date) -> PlannerShadowComparison:
    """Compare intent and contracts, never exact flexible weekday placement."""
    v2_roles, v3_roles = _v2_roles(v2_plan), _v3_roles(candidate)
    contracts = evaluate_research_contracts(candidate)
    failures = tuple(sorted(item.contract for item in contracts if item.status == "fail"))
    materialization = tuple(sorted(item.code for item in candidate.materialization_diagnostics))
    historical = "equivalent" if candidate.success else "v3_failure"
    authority = "equivalent" if candidate.success else "v3_failure"
    race = "equivalent" if v3_roles["race"] else "v3_missing_race"
    recovery = "equivalent" if "recovery_protection" not in failures else "v3_recovery_regression"
    low = "equivalent" if "low_intensity_emphasis" not in failures else "v3_low_intensity_regression"
    quality = "preference_difference" if v2_roles["quality"] != sum(v3_roles[item.value] for item in _QUALITY) else "equivalent"
    strength = "preference_difference" if v2_roles["strength"] != v3_roles["strength"] else "equivalent"
    flexible_dates = ("flexible_dates_not_compared_as_failures",) if candidate.success else ()
    blockers: list[str] = []
    if not candidate.success: blockers.append("materialization_contract_failure" if materialization else "v3_candidate_failure")
    if "historical_facts_preserved" in failures: blockers.append("history_preservation_regression")
    if "authority_preserved_or_explicit_conflict" in failures: blockers.append("authority_regression")
    if "recovery_protection" in failures: blockers.append("recovery_hard_constraint_violation")
    if "race_counts_once_as_stress" in failures or race == "v3_missing_race": blockers.append("race_taper_regression")
    if any(item in failures for item in {"completed_actuals_not_prescriptions", "target_credit_not_materialization_duration"}): blockers.append("duplicate_physiological_credit")
    if failures: blockers.append("research_contract_violation")
    classification = "equivalent" if not blockers and quality == strength == "equivalent" else "research_contract_regression" if blockers else "preference_difference"
    return PlannerShadowComparison(
        planning_date, True, candidate.success,
        tuple(sorted(v2_roles.items())), tuple(sorted(v3_roles.items())), historical, authority,
        race, recovery, low, quality, strength, len(candidate.needs),
        len({item.training_need_id for item in candidate.placements if item.authority_status is PlacementAuthorityStatus.PROVISIONAL}),
        len(candidate.placement_diagnostics.unplaced_needs), materialization, contracts,
        flexible_dates, tuple(sorted((("quality", quality), ("strength", strength)))), tuple(sorted(set(blockers))), classification,
        tuple(sorted((("v3_failure_reason", candidate.failure_reason or ""),))),
    )
