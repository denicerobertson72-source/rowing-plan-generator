"""Diagnostic-only category progression decisions for the existing R3 proposal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any, Mapping

from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy


CATEGORY_PROGRESSION_VERSION = "r9f5b3-category-progression-v1"
CATEGORY_PROGRESSION_POLICY_IDS = ("F01", "F02", "F04", "F05", "F06", "F09", "F10", "F11", "F12", "F13", "F14", "F23", "F33", "F39", "F44", "F45", "F46", "F47", "F48", "F52", "OP-01", "OP-07", "OP-08", "OP-09", "OP-10", "OP-11")
_GENERAL_AEROBIC_MODALITIES = ("bike", "elliptical", "stair_climber", "running")


@dataclass(frozen=True)
class CategoryProgressionDecision:
    physical_category: str
    training_dimension: str
    baseline_value: int | None
    proposed_value: int | None
    unit: str
    baseline_source: str | None
    baseline_coverage: str
    decision_status: str
    intended_action: str
    evidence_sufficiency: str
    authorization_status: str
    gate_result: str
    rationale_codes: tuple[str, ...]
    unresolved_evidence_needs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.decision_status not in {"supported", "provisional", "review_required", "approval_required", "blocked", "infeasible"}:
            raise ValueError("category_progression_status_invalid")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "unresolved_evidence_needs", tuple(sorted(set(self.unresolved_evidence_needs))))

    def to_dict(self) -> dict[str, Any]:
        relative = None if self.baseline_value is None or self.baseline_value == 0 or self.proposed_value is None else round((self.proposed_value - self.baseline_value) * 100 / self.baseline_value, 1)
        return {"physical_category": self.physical_category, "training_dimension": self.training_dimension,
                "baseline_value": self.baseline_value, "proposed_value": self.proposed_value, "unit": self.unit,
                "baseline_source": self.baseline_source, "baseline_coverage": self.baseline_coverage,
                "absolute_change": None if self.baseline_value is None or self.proposed_value is None else self.proposed_value - self.baseline_value,
                "relative_change_percent": relative, "decision_status": self.decision_status,
                "intended_action": self.intended_action, "evidence_sufficiency": self.evidence_sufficiency,
                "authorization_status": self.authorization_status, "gate_result": self.gate_result,
                "rationale_codes": list(self.rationale_codes), "unresolved_evidence_needs": list(self.unresolved_evidence_needs)}


def _reported(baseline: Mapping[str, Any], metric: str) -> tuple[int | None, str | None]:
    for item in baseline.get("reported_observations", ()):
        if isinstance(item, Mapping) and item.get("metric") == metric and item.get("known"):
            value = item.get("value")
            return (value if isinstance(value, int) and not isinstance(value, bool) else None, item.get("source"))
    return None, None


def _weekly_rows(daily: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
    buckets: dict[date, list[Mapping[str, Any]]] = {}
    for row in daily:
        current = date.fromisoformat(str(row["date"])); start = current - timedelta(days=current.weekday())
        buckets.setdefault(start, []).append(row)
    result = []
    for start, rows in sorted(buckets.items()):
        categories = {name: sum(int(row.get("physical_minutes_by_category", {}).get(name, 0) or 0) for row in rows) for name in ("rowing_specific", "strength", "general_aerobic", "other")}
        durations = {category: [minutes for row in rows for minutes in row.get("category_session_durations", {}).get(category, ())] for category in categories}
        result.append({"week_start": start.isoformat(), "week_end": (start + timedelta(days=6)).isoformat(), "calendar_days_covered": len(rows), "complete_week": len(rows) == 7, "physical_minutes_by_category": categories, "strength_exposures": sum(int(row.get("strength_exposure_count", 0) or 0) for row in rows), "category_exposure_frequency": {category: len(values) for category, values in durations.items()}, "category_session_duration_distribution": {category: {"durations_minutes": values, "maximum_minutes": max(values) if values else None} for category, values in durations.items()}})
    return result


def _restriction_context(profile: Mapping[str, Any], tolerance: Mapping[str, Any], demand: Mapping[str, Any]) -> dict[str, Any]:
    """Expose only existing, non-private restriction contracts.

    The profile currently has a global hard-restriction signal and per-modality
    cross-training availability.  It does not have a typed strength opt-out,
    typed category restriction, or date-addressable category restriction
    contract.  Free-text health/strength notes and preferences must therefore
    remain review context, rather than be guessed into prohibitions.
    """
    context = profile.get("training_context", {}) if isinstance(profile.get("training_context"), Mapping) else {}
    cross = profile.get("cross_training", {}) if isinstance(profile.get("cross_training"), Mapping) else {}
    modalities = cross.get("modalities", {}) if isinstance(cross.get("modalities"), Mapping) else {}
    disabled = tuple(sorted(
        name for name in _GENERAL_AEROBIC_MODALITIES
        if isinstance(modalities.get(name), Mapping) and modalities[name].get("enabled") is False
    ))
    explicitly_disabled_category = bool(modalities) and len(disabled) == len(_GENERAL_AEROBIC_MODALITIES)
    proposed_modalities = set()
    for row in demand.get("daily", ()) if isinstance(demand.get("daily"), (list, tuple)) else ():
        if not isinstance(row, Mapping):
            continue
        values = row.get("category_modalities", {})
        if isinstance(values, Mapping):
            proposed_modalities.update(str(value) for value in values.get("general_aerobic", ()) if value)
    hard = bool(context.get("hard_training_restriction") is True or tolerance.get("hard_restriction_active") is True)
    health = context.get("health_return_state")
    return {
        "global_hard_restriction": hard,
        "general_aerobic_opt_out": explicitly_disabled_category,
        "disabled_general_aerobic_modalities": disabled,
        "proposed_general_aerobic_modalities": tuple(sorted(proposed_modalities)),
        "typed_strength_opt_out_contract": "unavailable",
        "typed_category_hard_restriction_contract": "unavailable",
        "dated_category_restriction_contract": "deferred_r9h0",
        "reported_health_context": "present_requires_review" if health and health != "ordinary_training" else "not_reported",
        "soft_preference_is_not_hard_restriction": True,
    }


def _restriction_override(
    decision: dict[str, Any], restrictions: Mapping[str, Any], *, modality: str | None = None,
) -> dict[str, Any]:
    """Apply authoritative restriction evidence without changing allocation."""
    category = decision["physical_category"]
    codes = list(decision["rationale_codes"])
    needs = list(decision["unresolved_evidence_needs"])
    blocked = False
    if restrictions["global_hard_restriction"]:
        blocked = True
        codes.append("global_hard_training_restriction_authoritative")
    elif category == "general_aerobic" and restrictions["general_aerobic_opt_out"]:
        blocked = True
        codes.append("explicit_general_aerobic_opt_out_all_modalities_disabled")
    elif modality and modality in restrictions["disabled_general_aerobic_modalities"] and modality in restrictions["proposed_general_aerobic_modalities"]:
        blocked = True
        codes.append(f"explicit_modality_opt_out:{modality}")
    if blocked:
        decision.update({
            "decision_status": "blocked", "intended_action": "restricted_allocation_requires_revision",
            "evidence_sufficiency": "explicit_restriction_authoritative",
            "gate_result": "not_automatically_accepted_due_to_restriction",
        })
        needs.extend(("downstream_revision_or_upstream_reconciliation", "restriction_resolution"))
    elif restrictions["reported_health_context"] == "present_requires_review":
        codes.append("reported_health_context_not_typed_to_category")
        needs.append("category_applicability_of_health_context")
    decision["rationale_codes"] = sorted(set(codes))
    decision["unresolved_evidence_needs"] = sorted(set(needs))
    return decision


def _category_history_context(
    category: str, baseline_value: int | None, baseline_source: str | None,
    profile: Mapping[str, Any], baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Characterize history without treating missing records as inactivity.

    There is intentionally no production profile schema for prior category
    workload or category-scoped interruption.  ``category_history`` is a
    diagnostic fixture seam only: it lets the gate express the already-known
    distinctions while production calls surface the contract as unavailable.
    """
    raw_history = baseline.get("category_history", {})
    supplied = raw_history.get(category, {}) if isinstance(raw_history, Mapping) else {}
    supplied = supplied if isinstance(supplied, Mapping) else {}
    if baseline_value is None:
        current_status = "unknown_current_workload"
    elif baseline_value == 0:
        current_status = "explicit_zero_current_workload"
    else:
        current_status = "established_nonzero_current_workload"
    previous_experience = supplied.get("previous_experience")
    if previous_experience is True:
        experience_status = "established_previous_experience"
    elif previous_experience is False:
        experience_status = "established_lack_of_previous_experience"
    else:
        experience_status = "unknown_previous_experience"
    previous_workload = supplied.get("previous_workload_minutes")
    previous_workload = previous_workload if isinstance(previous_workload, int) and not isinstance(previous_workload, bool) and previous_workload >= 0 else None
    interruption = supplied.get("interruption")
    if interruption not in {"ordinary_category_specific", "health_category_specific"}:
        interruption = None
    context = profile.get("training_context", {}) if isinstance(profile.get("training_context"), Mapping) else {}
    overall_interruption = context.get("documented_training_interruption") is True
    health_return = context.get("health_return_state")
    if interruption == "health_category_specific" or (health_return and health_return != "ordinary_training" and experience_status == "established_previous_experience"):
        interruption_status = "health_related_restoration_requires_review"
    elif interruption == "ordinary_category_specific" and previous_workload is not None:
        interruption_status = "documented_category_specific_ordinary_interruption"
    elif overall_interruption:
        interruption_status = "overall_interruption_category_applicability_unresolved"
    else:
        interruption_status = "no_category_specific_interruption_evidence"
    return {
        "current_workload_status": current_status,
        "current_workload_minutes": baseline_value,
        "current_workload_source": baseline_source,
        "previous_experience_status": experience_status,
        "previous_workload_minutes": previous_workload,
        "previous_workload_evidence": "typed_diagnostic_context" if previous_workload is not None else "unavailable",
        "previous_modalities": tuple(str(value) for value in supplied.get("previous_modalities", ()) if value) if isinstance(supplied.get("previous_modalities", ()), (list, tuple, set)) else (),
        "interruption_status": interruption_status,
        "category_history_contract": "typed_diagnostic_context" if supplied else "unavailable_in_production_contract",
        "missing_completed_records_do_not_establish_inactivity": True,
    }


def _introduction_or_restoration(
    history: Mapping[str, Any], *, proposed: int | None,
) -> tuple[str | None, str | None, tuple[str, ...], tuple[str, ...]]:
    """Return a narrower action only when the category evidence supports it."""
    if proposed is None or proposed <= 0:
        return None, None, (), ()
    current = history["current_workload_status"]
    experience = history["previous_experience_status"]
    interruption = history["interruption_status"]
    if interruption == "documented_category_specific_ordinary_interruption":
        return "restoration", "documented_previous_workload_and_category_interruption", ("category_restoration_not_automatic_acceptance",), ("no_automatic_restoration_dose",)
    if interruption == "health_related_restoration_requires_review":
        return "health_related_restoration_review", "health_return_requires_review", ("health_return_does_not_advance_by_date",), ("health_return_authority_and_response_contract",)
    if current == "explicit_zero_current_workload" and experience == "established_lack_of_previous_experience":
        return "confirmed_introduction", "new_category_evidence", ("confirmed_category_introduction",), ("category_specific_introduction_review",)
    if current == "explicit_zero_current_workload" and experience == "unknown_previous_experience":
        return "possible_introduction", "current_zero_previous_experience_unknown", ("zero_current_workload_is_not_no_previous_experience",), ("previous_category_experience",)
    if current == "explicit_zero_current_workload" and experience == "established_previous_experience":
        return "previously_familiar_without_established_current_workload", "previous_experience_not_current_tolerance", ("previous_experience_is_not_tolerance",), ("category_specific_interruption_or_current_workload",)
    if current == "unknown_current_workload":
        return "unknown_baseline_requires_review", "current_category_workload_unknown", ("unknown_category_baseline_not_unfamiliarity",), ("current_category_workload",)
    if interruption == "overall_interruption_category_applicability_unresolved":
        return "possible_restoration", "overall_interruption_not_category_specific", ("overall_interruption_does_not_establish_category_interruption",), ("category_specific_interruption", "previous_category_workload")
    return None, None, (), ()


def _modality_transition_context(category: str, history: Mapping[str, Any], demand: Mapping[str, Any]) -> dict[str, Any]:
    raw_history = history.get("previous_modalities", ())
    previous = tuple(sorted({str(value) for value in raw_history if value})) if isinstance(raw_history, (list, tuple, set)) else ()
    proposed: set[str] = set()
    for row in demand.get("daily", ()) if isinstance(demand.get("daily"), (list, tuple)) else ():
        values = row.get("category_modalities", {}) if isinstance(row, Mapping) else {}
        if isinstance(values, Mapping):
            proposed.update(str(value) for value in values.get(category, ()) if value)
    transition = bool(previous and proposed and not proposed.intersection(previous))
    return {
        "previous_modalities": list(previous), "proposed_modalities": sorted(proposed),
        "status": "within_category_modality_transition_requires_review" if transition else "no_evidenced_modality_transition",
        "category_interruption_inferred": False,
    }


def _category_tolerance_context(
    tolerance: Mapping[str, Any], category: str, dimension: str,
) -> dict[str, Any]:
    """Read only scoped tolerance declarations; never promote overall evidence.

    Current production pathways describe availability but intentionally do not
    populate favorable or unfavorable category feedback.  The typed structure
    is a diagnostic/test capability for a future approved feedback contract.
    """
    pathways = {
        str(item.get("pathway")): str(item.get("availability"))
        for item in tolerance.get("pathways", ()) if isinstance(item, Mapping)
    }
    typed = tolerance.get("category_tolerance", {})
    category_map = typed.get(category, {}) if isinstance(typed, Mapping) else {}
    item = category_map.get(dimension, {}) if isinstance(category_map, Mapping) else {}
    item = item if isinstance(item, Mapping) else {}
    favorable = tuple(sorted({str(value) for value in item.get("favorable_pathways", ()) if value}))
    unfavorable = tuple(sorted({str(value) for value in item.get("unfavorable_pathways", ()) if value}))
    overall_favorable = tolerance.get("favorable_tolerance_by_pathway", {})
    overall_favorable = bool(isinstance(overall_favorable, Mapping) and any(overall_favorable.values()))
    if favorable and unfavorable:
        state = "conflicting_relevant_tolerance_evidence"
    elif unfavorable:
        state = "unfavorable_relevant_tolerance_evidence"
    elif favorable:
        state = "favorable_relevant_tolerance_evidence"
    elif item:
        state = "insufficient_relevant_tolerance_evidence"
    elif overall_favorable:
        state = "insufficient_overall_evidence_not_category_specific"
    else:
        state = "unavailable_category_specific_tolerance_contract"
    return {
        "category": category, "training_dimension": dimension,
        "evidence_state": state,
        "contract_availability": "typed_diagnostic_context" if item else "unavailable_in_production_contract",
        "pathway_availability": pathways,
        "favorable_pathways": list(favorable), "unfavorable_pathways": list(unfavorable),
        "completion_alone_establishes_favorable_tolerance": False,
        "private_readiness_used": False,
        "provenance": ["category_tolerance_typed_context" if item else "no_category_specific_tolerance_contract"],
    }


def _tolerance_override(
    decision: dict[str, Any], evidence: Mapping[str, Any], *, developmental_need: bool,
) -> dict[str, Any]:
    """Apply scoped evidence to one dimension, before Task A restrictions."""
    state = evidence["evidence_state"]
    codes = list(decision["rationale_codes"])
    needs = list(decision["unresolved_evidence_needs"])
    if state == "unfavorable_relevant_tolerance_evidence":
        decision.update({"decision_status": "review_required", "intended_action": "reactive_recovery_review",
                         "evidence_sufficiency": state, "gate_result": "not_automatically_accepted"})
        codes.append("unfavorable_category_tolerance_requires_review")
        needs.append("review_of_unfavorable_category_response")
    elif state == "conflicting_relevant_tolerance_evidence":
        decision.update({"decision_status": "review_required", "intended_action": "tolerance_conflict_review",
                         "evidence_sufficiency": state, "gate_result": "not_automatically_accepted"})
        codes.append("conflicting_category_tolerance_not_resolved_for_progression")
        needs.append("resolution_of_conflicting_category_tolerance")
    elif state == "favorable_relevant_tolerance_evidence":
        if not developmental_need:
            changed_proposal = (
                decision["baseline_value"] is not None and decision["proposed_value"] is not None
                and decision["baseline_value"] != decision["proposed_value"]
            )
            decision.update({
                "decision_status": "review_required" if changed_proposal else "provisional",
                "intended_action": "maintenance",
                "evidence_sufficiency": "favorable_tolerance_without_developmental_progression_need",
                "gate_result": "proposed_change_requires_review" if changed_proposal else "maintenance_not_progression_acceptance",
            })
            codes.append("maintenance_does_not_accept_changed_proposal" if changed_proposal else "favorable_tolerance_does_not_require_progression")
            if changed_proposal:
                needs.append("developmental_justification_for_proposed_change")
        elif decision["authorization_status"].startswith("explicit"):
            decision.update({"decision_status": "supported", "intended_action": "advisory_progression_candidate",
                             "evidence_sufficiency": state, "gate_result": "accepted_for_future_controlled_integration"})
            codes.append("scoped_favorable_tolerance_and_authorization")
        else:
            decision.update({"decision_status": "approval_required", "intended_action": "advisory_progression_candidate",
                             "evidence_sufficiency": state, "gate_result": "requires_approval"})
            codes.append("scoped_favorable_tolerance_requires_authorization")
            needs.append("category_progression_approval")
    else:
        codes.append("category_tolerance_not_established")
    decision["tolerance_evidence"] = dict(evidence)
    decision["rationale_codes"] = sorted(set(codes))
    decision["unresolved_evidence_needs"] = sorted(set(needs))
    return decision


def build_category_progression_diagnostic(*, profile: Mapping[str, Any], baseline: Mapping[str, Any], tolerance: Mapping[str, Any], sustainable_dose: Mapping[str, Any], allocation: Any, demand: Mapping[str, Any], policy: TrainingPolicy = DEFAULT_TRAINING_POLICY) -> dict[str, Any]:
    """Evaluate the existing proposal; never alter it or claim R4 feasibility."""
    days = (allocation.planning_end - allocation.planning_start).days + 1
    context = profile.get("training_context", {}) if isinstance(profile.get("training_context"), Mapping) else {}
    auth = "explicit_athlete_or_coach_authorization_present" if context.get("progression_authorized") is True else "no_explicit_category_progression_authorization_contract"
    restrictions = _restriction_context(profile, tolerance, demand)
    developmental_need = sustainable_dose.get("intended_action") == "progression"
    decisions = []
    category_histories: dict[str, dict[str, Any]] = {}
    for category, metric, proposed in (("rowing_specific", "rowing_minutes", allocation.rowing_specific_minutes), ("strength", "strength_minutes", allocation.strength_minutes), ("general_aerobic", "general_aerobic_minutes", allocation.general_aerobic_minutes)):
        base, source = _reported(baseline, metric)
        weekly = round(proposed * 7 / days) if days else None
        history = _category_history_context(category, base, source, profile, baseline)
        history["modality_transition"] = _modality_transition_context(category, history, demand)
        category_histories[category] = history
        if base is None:
            status, action, sufficiency, gate, codes, needs = ("review_required", "introduction_or_unresolved", "category_baseline_unknown", "not_automatically_accepted", ("unknown_category_baseline_not_zero",), ("category_specific_baseline_or_tolerance",))
        elif weekly == base:
            status, action, sufficiency, gate, codes, needs = ("provisional", "maintenance", "baseline_known_category_tolerance_incomplete", "maintenance_not_progression_acceptance", ("category_minutes_unchanged",), ())
        elif weekly > base:
            status, action, sufficiency, gate, codes, needs = ("review_required", "progression", "category_specific_favorable_tolerance_not_established", "not_automatically_accepted", ("overall_tolerance_does_not_establish_category_tolerance", "no_universal_category_progression_cap_applied"), ("category_specific_tolerance_or_authorized_review",))
        else:
            status, action, sufficiency, gate, codes, needs = ("provisional", "reduction_or_substitution", "category_change_requires_context_review", "preserve_original_proposal", ("category_change_not_reallocated",), ())
        action_override, sufficiency_override, history_codes, history_needs = _introduction_or_restoration(history, proposed=weekly)
        if action_override is not None:
            action = action_override
            sufficiency = sufficiency_override or sufficiency
            codes = (*codes, *history_codes)
            needs = (*needs, *history_needs)
            # Recognition never supplies tolerance, clearance, or an automatic
            # dose.  It is therefore review-required before restrictions are
            # applied with their higher precedence.
            status, gate = "review_required", "not_automatically_accepted"
        decision = CategoryProgressionDecision(category, "weekly_physical_minutes", base, weekly, "minutes_per_week", source, "reported_aggregate" if base is not None else "unknown", status, action, sufficiency, auth, gate, codes, needs).to_dict()
        decision["category_workload_context"] = history
        evidence = _category_tolerance_context(tolerance, category, "weekly_physical_minutes")
        if action_override is None:
            decision = _tolerance_override(decision, evidence, developmental_need=developmental_need)
        else:
            decision["tolerance_evidence"] = evidence
        decisions.append(_restriction_override(decision, restrictions))
    weeks = _weekly_rows(tuple(demand.get("daily", ())))
    # Planning-period equivalents can hide a demanding complete week.  These
    # are diagnostic comparisons only; partial weeks remain descriptive and
    # are never directly compared with a weekly baseline.
    for week in (item for item in weeks if item["complete_week"]):
        for category, metric in (("rowing_specific", "rowing_minutes"), ("strength", "strength_minutes"), ("general_aerobic", "general_aerobic_minutes")):
            base, source = _reported(baseline, metric)
            proposed = week["physical_minutes_by_category"][category]
            status = "provisional" if base is not None and proposed == base else "review_required"
            codes = ("complete_week_compared_independently_of_planning_period_average",) if base is not None else ("complete_week_baseline_unknown",)
            needs = () if base is not None and proposed == base else ("complete_week_category_baseline_or_review",)
            decision = CategoryProgressionDecision(category, f"complete_week_physical_minutes:{week['week_start']}", base, proposed, "minutes_per_week", source, "reported_aggregate" if base is not None else "unknown", status, "maintenance" if status == "provisional" else "complete_week_review", "complete_week_comparison", auth, "maintenance_not_progression_acceptance" if status == "provisional" else "not_automatically_accepted", codes, needs).to_dict()
            decision["tolerance_evidence"] = _category_tolerance_context(tolerance, category, "weekly_physical_minutes")
            decisions.append(_restriction_override(decision, restrictions))
    # Frequency and duration are deliberately distinct from category minutes.
    # A reported aggregate may derive a typical average, but never a longest
    # previously tolerated session.
    for category, metric in (("rowing_specific", "rowing_exposures"), ("strength", "strength_exposures"), ("general_aerobic", "general_aerobic_exposures")):
        base, source = _reported(baseline, metric)
        complete = [item for item in weeks if item["complete_week"]]
        proposed_values = [item["category_exposure_frequency"].get(category, 0) for item in complete]
        proposed = max(proposed_values) if proposed_values else None
        status = "provisional" if base is not None and proposed == base else "review_required" if proposed is not None else "review_required"
        decision = CategoryProgressionDecision(category, "weekly_exposure_frequency", base, proposed, "exposures_per_week", source, "reported_aggregate" if base is not None else "unknown", status, "maintenance" if base == proposed else "frequency_change_or_unresolved", "frequency_is_independent_of_minutes", auth, "not_automatically_accepted" if status == "review_required" else "maintenance_not_progression_acceptance", ("frequency_compared_independently_of_category_minutes",), (() if base is not None else ("category_frequency_baseline",))).to_dict()
        decision["tolerance_evidence"] = _category_tolerance_context(tolerance, category, "weekly_exposure_frequency")
        decisions.append(_restriction_override(decision, restrictions))
    rowing_minutes, rowing_source = _reported(baseline, "rowing_minutes")
    rowing_frequency, _ = _reported(baseline, "rowing_exposures")
    derived_average = round(rowing_minutes / rowing_frequency) if rowing_minutes is not None and rowing_frequency not in {None, 0} else None
    complete = [item for item in weeks if item["complete_week"]]
    proposed_maximum = max((item["category_session_duration_distribution"]["rowing_specific"]["maximum_minutes"] or 0 for item in complete), default=None)
    decision = CategoryProgressionDecision("rowing_specific", "maximum_session_duration", None, proposed_maximum, "minutes_per_session", None, "unknown_no_observed_individual_session_history", "review_required" if proposed_maximum is not None else "provisional", "session_duration_change_or_unresolved", "derived_average_is_not_observed_session_maximum", auth, "not_automatically_accepted" if proposed_maximum is not None else "unavailable", ("reported_average_not_used_as_longest_tolerated_session", f"derived_reported_average_minutes:{derived_average}" if derived_average is not None else "no_derived_average"), ("observed_comparable_session_duration_history",)).to_dict()
    decision["tolerance_evidence"] = _category_tolerance_context(tolerance, "rowing_specific", "maximum_session_duration")
    decisions.append(_restriction_override(decision, restrictions))
    for category in ("strength", "general_aerobic"):
        proposed_maximum = max((item["category_session_duration_distribution"][category]["maximum_minutes"] or 0 for item in complete), default=None)
        decision = CategoryProgressionDecision(category, "maximum_session_duration", None, proposed_maximum, "minutes_per_session", None, "unknown_no_observed_individual_session_history", "review_required" if proposed_maximum is not None else "provisional", "session_duration_change_or_unresolved", "session_duration_baseline_unavailable", auth, "unavailable_or_review_required", ("session_duration_dimension_evaluated_baseline_unavailable",), ("observed_comparable_session_duration_history",)).to_dict()
        decision["tolerance_evidence"] = _category_tolerance_context(tolerance, category, "maximum_session_duration")
        decisions.append(_restriction_override(decision, restrictions))
    # Existing profile capability only describes general-aerobic modality
    # availability/familiarity; it is not a blanket substitution permission.
    cross = profile.get("cross_training", {}) if isinstance(profile.get("cross_training"), Mapping) else {}
    modalities = cross.get("modalities", {}) if isinstance(cross.get("modalities"), Mapping) else {}
    for modality, detail in sorted(modalities.items()):
        detail = detail if isinstance(detail, Mapping) else {}
        enabled, familiarity = detail.get("enabled") is True, detail.get("familiarity")
        # A disabled capability only blocks an allocation that actually uses
        # it; it is not a blanket verdict on unrelated aerobic work.
        status = "review_required"
        decision = CategoryProgressionDecision("general_aerobic", f"modality:{modality}", None, None, "not_comparable", None, "unknown_no_modality_specific_completed_baseline", status, "authorized_familiar_modality_candidate" if status == "review_required" else "category_opt_out_or_unavailable", "familiarity_and_authorization_are_independent", auth, "requires_specific_substitution_or_standalone_authorization", (f"profile_enabled:{enabled}", f"familiarity:{familiarity or 'unknown'}", "weather_permission_not_equivalent_to_routine_standalone_authorization"), ("modality_specific_completed_baseline", "substitution_or_standalone_authorization")).to_dict()
        decision["tolerance_evidence"] = _category_tolerance_context(tolerance, "general_aerobic", f"modality:{modality}")
        decisions.append(_restriction_override(decision, restrictions, modality=str(modality)))
    statuses = {item["decision_status"] for item in decisions}
    aggregate = "blocked" if "blocked" in statuses else "review_required" if "review_required" in statuses else "approval_required" if "approval_required" in statuses else "provisional"
    category_dispositions = {category: ("review_required" if any(item["physical_category"] == category and item["decision_status"] in {"review_required", "blocked"} for item in decisions) else "approval_required" if any(item["physical_category"] == category and item["decision_status"] == "approval_required" for item in decisions) else "provisional") for category in ("rowing_specific", "strength", "general_aerobic")}
    return {"version": CATEGORY_PROGRESSION_VERSION, "policy_version": policy.version, "applicable_policy_ids": list(CATEGORY_PROGRESSION_POLICY_IDS), "diagnostic_only": True, "prescription_unchanged": True,
            "overall_sustainable_dose_context": {"intended_action": sustainable_dose.get("intended_action"), "decision_status": sustainable_dose.get("decision_status")},
            "original_proposal": {"physical_minutes": allocation.recommended_physical_minutes, "rowing_specific_minutes": allocation.rowing_specific_minutes, "strength_minutes": allocation.strength_minutes, "general_aerobic_minutes": allocation.general_aerobic_minutes},
            "planning_days": days, "weekly_equivalents": {"rowing_specific_minutes": round(allocation.rowing_specific_minutes * 7 / days), "strength_minutes": round(allocation.strength_minutes * 7 / days), "general_aerobic_minutes": round(allocation.general_aerobic_minutes * 7 / days)},
            "complete_and_partial_weeks": weeks, "restriction_evidence": restrictions, "category_workload_context": category_histories, "decisions": decisions, "category_gate_dispositions": category_dispositions, "aggregate_gate_disposition": aggregate,
            "downstream_validation": "R4_dated_category_validation_deferred_until_r9h0_or_controlled_integration", "private_readiness_used": False,
            "limitations": ["no_hidden_replacement_allocation", "no_category_tolerance_inferred_from_overall_tolerance", "no_assessment_performance_as_tolerance_evidence"]}
