from __future__ import annotations

from datetime import date, timedelta

from rowing_plan.diagnostic_v3 import generate_v3_diagnostic_report, public_v3_diagnostic_payload
from rowing_plan.sustainable_training_dose import build_sustainable_dose_decision
from services.api.tests.disposable_browser_fixture import synthetic_profile


def _decision(*, actual_sufficient=False, detailed="partial", basic="partial", minimal="unavailable", favorable_pathways=(), unfavorable_pathways=(), authorization=False, recovery=False, hard=False, capacity=2650, prescribed=2500, consolidation=False, interruption=False, health_return_state=None):
    profile = {"training_context": {"progression_authorized": authorization, "hard_training_restriction": hard, "planned_consolidation": consolidation, "documented_training_interruption": interruption, "health_return_state": health_return_state}}
    baseline = {
        "reported_observations": [
            {"metric": "total_physical_minutes", "known": True, "value": 500, "source": "athlete.current_weekly_training_minutes"},
            {"metric": "rowing_minutes", "known": True, "value": 240, "source": "athlete.current_approx_weekly_rowing_minutes"},
        ],
        "evidence_conflicts": [{"metric": "total_physical_minutes", "status": "different_evidence_preserved_not_merged"}],
    }
    tolerance = {
        "pathways": [{"pathway": "detailed", "availability": detailed}, {"pathway": "basic", "availability": basic}, {"pathway": "minimal", "availability": minimal}],
        "favorable_tolerance_by_pathway": {name: True for name in favorable_pathways},
        "unfavorable_tolerance_by_pathway": {name: True for name in unfavorable_pathways},
        "completion_evidence_count": 6, "response_evidence_count": 3,
        "uncertainty": ["performance_assessments_are_not_training_tolerance_evidence"],
        "explicit_restriction_codes": [],
    }
    return build_sustainable_dose_decision(
        profile,
        r2_baseline={"selected_baseline_weekly_minutes": 500, "selected_baseline_source": "sufficient_recent_actual_training" if actual_sufficient else "explicit_weekly_total_training_estimate", "selected_baseline_confidence": "moderate", "actual_history_sufficient": actual_sufficient},
        baseline=baseline, tolerance=tolerance,
        development_recommendation={"primary_emphasis": "foundational_general_preparation", "secondary_emphasis": "aerobic_capacity_development", "recommendation_type": "foundational"},
        capacity_minutes=capacity, prescribed_physical_minutes=prescribed, prescribed_rowing_minutes=1722,
        planning_days=35, recovery_active=recovery,
    ).to_dict()


def test_credible_reported_workload_and_sparse_history_support_provisional_maintenance_not_growth():
    decision = _decision()
    assert (decision["intended_action"], decision["decision_status"]) == ("maintenance", "provisional")
    assert decision["selected_working_baseline"]["weekly_physical_minutes"] == 500
    assert decision["selected_working_baseline"]["provisional"] is True
    assert decision["candidate_developmental_dose"] is None
    assert decision["category_specific_review"]["status"] == "downstream_review_required"


def test_completed_baseline_precedence_and_conflict_are_explicit_without_merging_sources():
    decision = _decision(actual_sufficient=True)
    assert decision["selected_working_baseline"]["source"] == "sufficient_recent_actual_training"
    assert (decision["intended_action"], decision["decision_status"]) == ("maintenance", "supported")
    assert decision["established_workload"]["conflicts"]
    assert decision["established_workload"]["reported_total_physical_minutes_per_week"] == 500


def test_capacity_is_not_growth_or_tolerance_and_reduced_capacity_is_not_reduced_tolerance():
    roomy = _decision(capacity=2650)
    constrained = _decision(capacity=2400)
    assert roomy["capacity_constraint"]["capacity_relation"] == "available_capacity_does_not_authorize_increase"
    assert constrained["capacity_constraint"]["capacity_relation"] == "constrains_existing_or_candidate_dose"
    assert roomy["tolerance_evidence"]["favorable_tolerance_established"] is False
    assert constrained["tolerance_evidence"]["favorable_tolerance_established"] is False


def test_completion_missing_feedback_and_authorization_alone_do_not_authorize_progression():
    decision = _decision(authorization=True)
    assert decision["intended_action"] == "maintenance"
    assert decision["authorization_status"] == "explicit_athlete_or_coach_authorization_present"
    assert "completion_or_missing_feedback_do_not_authorize_progression" in decision["rationale_codes"]
    assert "authorized_minimal_confirmation_contract_is_unavailable" in decision["unresolved_evidence_needs"]


def test_favorable_authorized_evidence_from_any_supported_pathway_supports_only_an_advisory_single_dimension_candidate():
    decision = _decision(basic="available", favorable_pathways=("basic",), authorization=True)
    candidate = decision["candidate_developmental_dose"]
    assert (decision["intended_action"], decision["decision_status"]) == ("progression", "supported")
    assert candidate["weekly_overall_relative_range_percent"] == [2, 5]
    assert candidate["easy_session_additional_minutes_range"] == [5, 10]
    assert candidate["exact_minutes"] is None
    assert candidate["not_automatically_compounded"] is True
    assert candidate["not_multiple_dimensions"] is True


def test_favorable_evidence_can_recommend_progression_before_authorization_and_availability_alone_is_not_favorable():
    approval = _decision(minimal="available", favorable_pathways=("minimal",))
    available_only = _decision(detailed="available", authorization=True)
    assert (approval["intended_action"], approval["decision_status"]) == ("progression", "approval_required")
    assert approval["candidate_developmental_dose"] is not None
    assert available_only["intended_action"] == "maintenance"


def test_unfavorable_evidence_outranks_authorization_without_creating_a_new_recovery_algorithm():
    decision = _decision(basic="available", unfavorable_pathways=("basic",), authorization=True)
    assert (decision["intended_action"], decision["decision_status"]) == ("reactive_recovery", "review_required")
    assert decision["candidate_developmental_dose"] is None


def test_recovery_and_hard_restriction_have_independent_precedence():
    recovery = _decision(recovery=True)
    suspended = _decision(recovery=True, hard=True)
    assert (recovery["intended_action"], recovery["decision_status"]) == ("reactive_recovery", "review_required")
    assert recovery["recovery_adjustment"]["dose"] is None
    assert (suspended["intended_action"], suspended["decision_status"]) == ("suspension", "blocked")


def test_consolidation_restoration_and_health_return_are_explicit_contexts_not_calendar_inferences():
    consolidation = _decision(consolidation=True)
    restoration = _decision(interruption=True)
    health = _decision(health_return_state="initial_return")
    sparse = _decision()
    assert (consolidation["intended_action"], consolidation["decision_status"]) == ("consolidation", "review_required")
    assert (restoration["intended_action"], restoration["decision_status"]) == ("restoration", "review_required")
    assert (health["intended_action"], health["decision_status"]) == ("restoration", "review_required")
    assert sparse["intended_action"] == "maintenance"


def test_production_shaped_decision_is_public_diagnostic_only_and_prescription_invariant():
    profile = synthetic_profile()
    start = date(2026, 11, 27)
    profile["season"] = {"start_date": start.isoformat(), "end_date": "2026-12-31"}
    profile["date_availability"] = [
        {"date": (start + timedelta(days=index)).isoformat(), "available": True, "max_training_minutes": 100 if index == 0 else 75, "rowing_modes": ["erg"]}
        for index in range(35)
    ]
    profile["athlete"].update({"current_weekly_training_minutes": 500, "current_approx_weekly_rowing_minutes": 240})
    profile["training_context"] = {"current_strength_sessions_per_week": 3}
    profile["tests"] = {"testing_blocks": [{"performance_tests": [{"id": "r20-corrected", "protocol": "thirty_min_rate_capped_maximal", "test_date": "2026-11-20", "distance_meters": 6589, "duration_seconds": 1800, "rate_cap_spm": 20, "valid_for_profile": True}]}]}
    sparse = {f"actual:{day}:erg": {"status": "completed", "activity_type": "erg", "actual_duration_min": 60} for day in ("2026-11-03", "2026-11-07", "2026-11-11", "2026-11-15", "2026-11-19", "2026-11-23")}
    report = generate_v3_diagnostic_report(profile, planning_date=start, history_actuals=sparse)
    decision = report["sustainable_dose_decision"]
    assert decision["available_capacity"]["physical_minutes"] == 2650
    assert decision["selected_working_baseline"]["weekly_physical_minutes"] == 500
    assert report["r2"]["recommended_physical_minutes"] == 2500
    assert decision["intended_action"] == "maintenance"
    assert decision["candidate_developmental_dose"] is None
    assert decision["existing_prescribed_dose"]["physical_minutes"] == report["r2"]["recommended_physical_minutes"]
    assert decision["existing_prescribed_dose"]["rowing_minutes"] == report["r3"]["rowing_specific_minutes"]
    assert decision["diagnostic_only"] is True and decision["prescription_unchanged"] is True
    assert decision["private_readiness_used"] is False
    r20 = next(item for item in report["physiological_evidence"]["measurements"] if item["measurement_id"] == "r20-corrected")
    assert r20["validity"] != "invalid"
    assert all("r20-corrected" not in reference for reference in decision["evidence_references"])
    assert public_v3_diagnostic_payload(report)["sustainable_dose_decision"] == decision
