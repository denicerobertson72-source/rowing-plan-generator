"""Diagnostic-only sustainable training-dose decision framework.

This module is intentionally downstream of R2--R6.  It characterizes a
candidate decision and its evidence without selecting, allocating, or
authorizing a prescription.  In particular, capacity is a ceiling rather
than evidence of established workload or favourable tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy


SUSTAINABLE_DOSE_DECISION_VERSION = "r9f5b2-sustainable-dose-decision-v1"
SUSTAINABLE_DOSE_POLICY_IDS = (
    "F01", "F02", "F03", "F04", "F05", "F06", "F09", "F10", "F11", "F12",
    "F13", "F14", "F23", "F33", "F39", "F40", "F41", "F42", "F44", "F45",
    "F46", "F47", "F48", "F52", "F53", "F54", "F55", "OP-01", "OP-02",
    "OP-03", "OP-04", "OP-06", "OP-21", "OP-22", "OP-23",
)


@dataclass(frozen=True)
class SustainableDoseDecision:
    """Immutable, additive decision record; never an implemented dose."""

    version: str
    policy_version: str
    applicable_policy_ids: tuple[str, ...]
    available_capacity: Mapping[str, Any]
    established_workload: Mapping[str, Any]
    selected_working_baseline: Mapping[str, Any]
    tolerance_evidence: Mapping[str, Any]
    developmental_context: Mapping[str, Any]
    intended_action: str
    decision_status: str
    evidence_sufficiency: str
    authorization_status: str
    candidate_developmental_dose: Mapping[str, Any] | None
    recovery_adjustment: Mapping[str, Any] | None
    capacity_constraint: Mapping[str, Any]
    existing_prescribed_dose: Mapping[str, Any]
    category_specific_review: Mapping[str, Any]
    rationale_codes: tuple[str, ...]
    evidence_references: tuple[str, ...]
    unresolved_evidence_needs: tuple[str, ...]
    uncertainty: tuple[str, ...]
    provenance: tuple[str, ...]
    private_readiness_used: bool = False
    diagnostic_only: bool = True
    prescription_unchanged: bool = True
    scheduling_feasibility: str = "not_evaluated_by_diagnostic_decision"

    def __post_init__(self) -> None:
        if self.intended_action not in {"progression", "maintenance", "consolidation", "reactive_recovery", "restoration", "suspension"}:
            raise ValueError("sustainable_dose_action_is_invalid")
        if self.decision_status not in {"supported", "provisional", "review_required", "approval_required", "blocked", "infeasible"}:
            raise ValueError("sustainable_dose_status_is_invalid")
        object.__setattr__(self, "applicable_policy_ids", tuple(sorted(set(self.applicable_policy_ids))))
        for field in ("rationale_codes", "evidence_references", "unresolved_evidence_needs", "uncertainty", "provenance"):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))
        for field in ("available_capacity", "established_workload", "selected_working_baseline", "tolerance_evidence", "developmental_context", "capacity_constraint", "existing_prescribed_dose", "category_specific_review"):
            object.__setattr__(self, field, MappingProxyType(dict(getattr(self, field))))
        if self.candidate_developmental_dose is not None:
            object.__setattr__(self, "candidate_developmental_dose", MappingProxyType(dict(self.candidate_developmental_dose)))
        if self.recovery_adjustment is not None:
            object.__setattr__(self, "recovery_adjustment", MappingProxyType(dict(self.recovery_adjustment)))

    def to_dict(self) -> dict[str, Any]:
        def plain(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
            return dict(value) if value is not None else None
        return {
            "version": self.version, "policy_version": self.policy_version,
            "applicable_policy_ids": list(self.applicable_policy_ids),
            "available_capacity": plain(self.available_capacity),
            "established_workload": plain(self.established_workload),
            "selected_working_baseline": plain(self.selected_working_baseline),
            "tolerance_evidence": plain(self.tolerance_evidence),
            "developmental_context": plain(self.developmental_context),
            "intended_action": self.intended_action, "decision_status": self.decision_status,
            "evidence_sufficiency": self.evidence_sufficiency,
            "authorization_status": self.authorization_status,
            "candidate_developmental_dose": plain(self.candidate_developmental_dose),
            "recovery_adjustment": plain(self.recovery_adjustment),
            "capacity_constraint": plain(self.capacity_constraint),
            "existing_prescribed_dose": plain(self.existing_prescribed_dose),
            "category_specific_review": plain(self.category_specific_review),
            "rationale_codes": list(self.rationale_codes),
            "evidence_references": list(self.evidence_references),
            "unresolved_evidence_needs": list(self.unresolved_evidence_needs),
            "uncertainty": list(self.uncertainty), "provenance": list(self.provenance),
            "private_readiness_used": self.private_readiness_used,
            "diagnostic_only": self.diagnostic_only,
            "prescription_unchanged": self.prescription_unchanged,
            "scheduling_feasibility": self.scheduling_feasibility,
        }


def _reported(baseline: Mapping[str, Any], metric: str) -> int | None:
    for item in baseline.get("reported_observations", ()):
        if isinstance(item, Mapping) and item.get("metric") == metric and item.get("known"):
            value = item.get("value")
            return value if isinstance(value, int) and not isinstance(value, bool) else None
    return None


def _pathways(tolerance: Mapping[str, Any]) -> dict[str, str]:
    return {str(item.get("pathway")): str(item.get("availability")) for item in tolerance.get("pathways", ()) if isinstance(item, Mapping)}


def _favorable_pathways(tolerance: Mapping[str, Any], pathways: Mapping[str, str]) -> tuple[str, ...]:
    """Return only explicitly established favourable pathway conclusions.

    A pathway being ``available`` describes a source contract, not a positive
    tolerance conclusion.  The current evidence adapter intentionally emits
    no favourable conclusion until a detailed/basic/minimal contract can do
    so honestly; future adapters may populate this typed mapping.
    """
    declared = tolerance.get("favorable_tolerance_by_pathway", {})
    declared = declared if isinstance(declared, Mapping) else {}
    return tuple(sorted(name for name, value in declared.items() if value is True and pathways.get(str(name)) == "available"))


def _unfavorable_pathways(tolerance: Mapping[str, Any], pathways: Mapping[str, str]) -> tuple[str, ...]:
    declared = tolerance.get("unfavorable_tolerance_by_pathway", {})
    declared = declared if isinstance(declared, Mapping) else {}
    return tuple(sorted(name for name, value in declared.items() if value is True and pathways.get(str(name)) in {"available", "partial"}))


def _authorization(profile: Mapping[str, Any]) -> str:
    context = profile.get("training_context", {})
    if isinstance(context, Mapping) and context.get("progression_authorized") is True:
        return "explicit_athlete_or_coach_authorization_present"
    return "no_explicit_progression_authorization_contract"


def _hard_restriction(profile: Mapping[str, Any], tolerance: Mapping[str, Any]) -> bool:
    context = profile.get("training_context", {})
    return bool(isinstance(context, Mapping) and context.get("hard_training_restriction") is True) or bool(tolerance.get("hard_restriction_active"))


def _explicit_context(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    """Only consume established non-private context; absent values stay absent."""
    context = profile.get("training_context", {})
    return context if isinstance(context, Mapping) else {}


def build_sustainable_dose_decision(
    profile: Mapping[str, Any], *, r2_baseline: Mapping[str, Any], baseline: Mapping[str, Any],
    tolerance: Mapping[str, Any], development_recommendation: Mapping[str, Any],
    capacity_minutes: int | None, prescribed_physical_minutes: int | None,
    prescribed_rowing_minutes: int | None, planning_days: int, recovery_active: bool,
    policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
) -> SustainableDoseDecision:
    """Characterize a candidate action using already-normalized diagnostic facts.

    The only progression range comes from OP-01's versioned guidance.  It is
    deliberately advisory: no exact increase is selected and no downstream
    planner input is changed here.
    """
    selected = r2_baseline.get("selected_baseline_weekly_minutes")
    selected = selected if isinstance(selected, int) and not isinstance(selected, bool) else None
    source = r2_baseline.get("selected_baseline_source")
    confidence = r2_baseline.get("selected_baseline_confidence")
    reported_total = _reported(baseline, "total_physical_minutes")
    reported_rowing = _reported(baseline, "rowing_minutes")
    pathways = _pathways(tolerance)
    favorable_pathways = _favorable_pathways(tolerance, pathways)
    unfavorable_pathways = _unfavorable_pathways(tolerance, pathways)
    authorization = _authorization(profile)
    restrictions = tuple(str(item) for item in tolerance.get("explicit_restriction_codes", ()) if item)
    hard_restriction = _hard_restriction(profile, tolerance)
    context = _explicit_context(profile)
    planned_consolidation = context.get("planned_consolidation") is True
    documented_interruption = context.get("documented_training_interruption") is True
    health_return_state = context.get("health_return_state")
    guidance = policy.rule("pilot_progression_guidance").value
    rationale = ["diagnostic_only_prescription_unchanged", "capacity_is_ceiling_not_workload_or_tolerance"]
    needs = ["category_specific_progression_review_is_deferred_to_r9f5b3"]
    uncertainty = list(tolerance.get("uncertainty", ()))
    candidate: dict[str, Any] | None = None
    recovery_adjustment: dict[str, Any] | None = None
    if hard_restriction:
        action, status, sufficiency = "suspension", "blocked", "hard_restriction_authoritative"
        rationale.append("explicit_hard_restriction_preserved")
    elif health_return_state and health_return_state != "ordinary_training":
        action, status, sufficiency = "suspension" if health_return_state == "suspended" else "restoration", "review_required", "health_return_context_requires_review"
        rationale.append("health_return_state_does_not_advance_by_date")
        needs.append("health_return_authority_and_response_contract")
    elif recovery_active:
        action, status, sufficiency = "reactive_recovery", "review_required", "recovery_context_present"
        recovery_adjustment = {"status": "existing_recovery_safeguards_remain_authoritative", "dose": None}
        rationale.append("existing_recovery_context_precedes_growth")
        needs.append("recovery_adjusted_dose_requires_existing_recovery_planning_context")
    elif unfavorable_pathways:
        action, status, sufficiency = "reactive_recovery", "review_required", "unfavorable_tolerance_evidence"
        rationale.append(f"unfavorable_tolerance_pathways:{','.join(unfavorable_pathways)}")
        needs.append("review_of_unfavorable_response_before_progression")
    elif planned_consolidation:
        action, status, sufficiency = "consolidation", "review_required", "explicit_planned_consolidation_context"
        rationale.append("planned_consolidation_is_not_reactive_recovery")
        needs.append("consolidation_dimension_and_comparable_baseline")
    elif documented_interruption:
        action, status, sufficiency = "restoration", "review_required", "documented_interruption_requires_restoration_review"
        rationale.append("documented_interruption_is_distinct_from_sparse_logging")
        needs.append("previously_tolerated_dose_and_interruption_context")
    elif selected is None:
        action, status, sufficiency = "maintenance", "review_required", "no_selected_working_baseline"
        rationale.append("missing_baseline_does_not_establish_inactivity")
        needs.append("credible_reported_or_sufficient_completed_workload")
    else:
        # The currently-populated detailed/basic pathways are partial and the
        # minimal confirmation contract is unavailable.  Therefore ordinary
        # production evidence supports a provisional maintenance decision.
        action, status, sufficiency = "maintenance", ("supported" if source == "sufficient_recent_actual_training" else "provisional"), "favorable_tolerance_not_established"
        rationale.extend(("credible_baseline_preserved", "incomplete_completed_history_not_treated_as_inactivity", "completion_or_missing_feedback_do_not_authorize_progression"))
        needs.extend(("comparable_workout_response", "athlete_confirmed_recovery"))
        if pathways.get("minimal") == "unavailable":
            needs.append("authorized_minimal_confirmation_contract_is_unavailable")
        if favorable_pathways:
            action, status, sufficiency = "progression", ("supported" if authorization == "explicit_athlete_or_coach_authorization_present" else "approval_required"), "favorable_tolerance_evidence"
            candidate = {
                "intended_dimension": "overall_physical_minutes_or_one_appropriate_easy_session",
                "weekly_overall_relative_range_percent": list(guidance["weekly_overall_relative_range_percent"]),
                "easy_session_additional_minutes_range": list(guidance["easy_session_additional_minutes_range"]),
                "exact_minutes": None,
                "not_automatically_compounded": True,
                "not_multiple_dimensions": True,
                "status": "advisory_candidate_not_authorized_prescription",
            }
            rationale.extend((f"favorable_tolerance_pathways:{','.join(favorable_pathways)}", "op01_pilot_guidance"))
            if authorization == "explicit_athlete_or_coach_authorization_present":
                rationale.append("explicit_authorization_present")
            else:
                rationale.append("progression_candidate_requires_approval")
            needs = [item for item in needs if item not in {"comparable_workout_response", "athlete_confirmed_recovery"}]

    capacity_constraint = {
        "available_physical_minutes": capacity_minutes,
        "existing_prescribed_physical_minutes": prescribed_physical_minutes,
        "capacity_relation": (
            "constrains_existing_or_candidate_dose" if capacity_minutes is not None and prescribed_physical_minutes is not None and capacity_minutes < prescribed_physical_minutes
            else "available_capacity_does_not_authorize_increase" if capacity_minutes is not None
            else "capacity_unknown"
        ),
    }
    category_review = {
        "status": "downstream_review_required",
        "reason": "category_specific_progression_enforcement_deferred_to_r9f5b3",
        "reported_rowing_weekly_minutes": reported_rowing,
        "prospective_rowing_weekly_equivalent": (round(prescribed_rowing_minutes * 7 / planning_days) if prescribed_rowing_minutes is not None and planning_days > 0 else None),
    }
    return SustainableDoseDecision(
        SUSTAINABLE_DOSE_DECISION_VERSION, policy.version, SUSTAINABLE_DOSE_POLICY_IDS,
        {"physical_minutes": capacity_minutes, "planning_days": planning_days, "status": "capacity_not_established_workload"},
        {"reported_total_physical_minutes_per_week": reported_total, "reported_rowing_minutes_per_week": reported_rowing, "completed_history_status": "sufficient" if r2_baseline.get("actual_history_sufficient") else "sparse_or_incomplete", "conflicts": list(baseline.get("evidence_conflicts", ()))},
        {"weekly_physical_minutes": selected, "source": source, "confidence": confidence, "provisional": source != "sufficient_recent_actual_training"},
        {"pathway_availability": pathways, "favorable_pathways": list(favorable_pathways), "unfavorable_pathways": list(unfavorable_pathways), "completion_evidence_count": tolerance.get("completion_evidence_count"), "response_evidence_count": tolerance.get("response_evidence_count"), "explicit_restriction_codes": list(restrictions), "favorable_tolerance_established": bool(favorable_pathways)},
        {"primary_emphasis": development_recommendation.get("primary_emphasis"), "secondary_emphasis": development_recommendation.get("secondary_emphasis"), "recommendation_type": development_recommendation.get("recommendation_type"), "planned_consolidation": planned_consolidation, "documented_interruption": documented_interruption, "health_return_state": health_return_state},
        action, status, sufficiency, authorization, candidate, recovery_adjustment, capacity_constraint,
        {"physical_minutes": prescribed_physical_minutes, "rowing_minutes": prescribed_rowing_minutes, "status": "existing_r2_r3_prescription_for_comparison_only"},
        category_review, tuple(rationale),
        tuple(f"baseline:{item.get('source')}" for item in baseline.get("reported_observations", ()) if isinstance(item, Mapping) and item.get("known")),
        tuple(needs), tuple(uncertainty),
        (SUSTAINABLE_DOSE_DECISION_VERSION, "r2_baseline_evidence", "training_baseline_and_tolerance_evidence", "development_recommendation", "existing_r2_r3_outputs"),
    )
