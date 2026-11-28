from datetime import date, timedelta
from types import SimpleNamespace

from rowing_plan.category_progression import _weekly_rows, build_category_progression_diagnostic


def _allocation(rowing=1709, strength=585, general=135):
    return SimpleNamespace(planning_start=date(2026, 11, 28), planning_end=date(2026, 12, 31), recommended_physical_minutes=2429, rowing_specific_minutes=rowing, strength_minutes=strength, general_aerobic_minutes=general)


def _diagnostic(*, rowing=1709, strength=585, general=135, category_favorable=False, authorized=False, profile=None, demand=None, baseline=None, tolerance=None, developmental_need=True, allocation=None):
    profile = profile or {"training_context": {}}
    profile.setdefault("training_context", {})["progression_authorized"] = authorized
    baseline = baseline or {"reported_observations": [
        {"metric": "rowing_minutes", "known": True, "value": 240, "source": "athlete.current_approx_weekly_rowing_minutes"},
        {"metric": "strength_exposures", "known": True, "value": 3, "source": "training_context.current_strength_sessions_per_week"},
        {"metric": "strength_minutes", "known": False, "value": None, "source": "absent"},
        {"metric": "general_aerobic_minutes", "known": False, "value": None, "source": "absent"},
    ]}
    demand = demand or {"daily": [{"date": "2026-11-28", "physical_minutes_by_category": {"rowing_specific": 169}, "strength_exposure_count": 0}, {"date": "2026-11-30", "physical_minutes_by_category": {"rowing_specific": 50, "strength": 45, "general_aerobic": 15}, "strength_exposure_count": 1}]}
    if tolerance is None:
        tolerance = {"category_tolerance": {"rowing_specific": {"weekly_physical_minutes": {"favorable_pathways": ["basic"]}}} if category_favorable else {}}
    return build_category_progression_diagnostic(profile=profile, baseline=baseline, tolerance=tolerance, sustainable_dose={"intended_action": "progression" if developmental_need else "maintenance", "decision_status": "provisional"}, allocation=allocation or _allocation(rowing, strength, general), demand=demand)


def test_overall_maintenance_does_not_automatically_accept_unsupported_rowing_progression():
    result = _diagnostic()
    rowing = next(item for item in result["decisions"] if item["physical_category"] == "rowing_specific")
    assert rowing["baseline_value"] == 240 and rowing["proposed_value"] == 352
    assert rowing["decision_status"] == "review_required"
    assert result["aggregate_gate_disposition"] == "review_required"
    assert result["original_proposal"]["rowing_specific_minutes"] == 1709


def test_category_evidence_and_authorization_are_independent():
    approval = _diagnostic(category_favorable=True)
    supported = _diagnostic(category_favorable=True, authorized=True)
    assert next(item for item in approval["decisions"] if item["physical_category"] == "rowing_specific")["decision_status"] == "approval_required"
    assert next(item for item in supported["decisions"] if item["physical_category"] == "rowing_specific")["decision_status"] == "supported"


def test_unknown_strength_and_general_aerobic_baselines_are_not_inferred_from_frequency_or_remaining_minutes():
    result = _diagnostic()
    strength = next(item for item in result["decisions"] if item["physical_category"] == "strength")
    aerobic = next(item for item in result["decisions"] if item["physical_category"] == "general_aerobic")
    assert strength["baseline_value"] is None and strength["decision_status"] == "review_required"
    assert aerobic["baseline_value"] is None and aerobic["decision_status"] == "review_required"


def test_partial_weeks_are_explicit_and_zero_baseline_never_divides_for_a_percentage():
    result = _diagnostic()
    assert all(not item["complete_week"] for item in result["complete_and_partial_weeks"])
    # Explicit zero remains distinct from unknown; the decision exposes no fabricated percentage.
    zero = _diagnostic(rowing=0)
    rowing = next(item for item in zero["decisions"] if item["physical_category"] == "rowing_specific")
    assert rowing["relative_change_percent"] == -100.0


def test_equal_rowing_minutes_can_have_independent_frequency_and_session_duration_changes():
    rows = tuple({"date": f"2026-12-{day:02d}", "physical_minutes_by_category": {"rowing_specific": duration}, "strength_exposure_count": 0, "category_session_durations": {"rowing_specific": ([duration] if duration else [])}} for day, duration in zip(range(7, 14), (40, 40, 40, 40, 40, 40, 0)))
    week = _weekly_rows(rows)[0]
    assert week["complete_week"] is True
    assert week["physical_minutes_by_category"]["rowing_specific"] == 240
    assert week["category_exposure_frequency"]["rowing_specific"] == 6
    assert week["category_session_duration_distribution"]["rowing_specific"]["maximum_minutes"] == 40


def _weekly(result, category):
    return next(item for item in result["decisions"] if item["physical_category"] == category and item["training_dimension"] == "weekly_physical_minutes")


def test_global_hard_restriction_blocks_each_category_without_changing_original_allocation():
    result = _diagnostic(profile={"training_context": {"hard_training_restriction": True}})
    assert result["aggregate_gate_disposition"] == "blocked"
    assert all(_weekly(result, category)["decision_status"] == "blocked" for category in ("rowing_specific", "strength", "general_aerobic"))
    assert result["original_proposal"] == {"physical_minutes": 2429, "rowing_specific_minutes": 1709, "strength_minutes": 585, "general_aerobic_minutes": 135}
    assert result["restriction_evidence"]["typed_category_hard_restriction_contract"] == "unavailable"


def test_favorable_tolerance_and_progression_authorization_cannot_override_hard_restriction():
    result = _diagnostic(
        category_favorable=True, authorized=True,
        profile={"training_context": {"hard_training_restriction": True}},
    )
    rowing = _weekly(result, "rowing_specific")
    assert rowing["authorization_status"] == "explicit_athlete_or_coach_authorization_present"
    assert rowing["decision_status"] == "blocked"
    assert "global_hard_training_restriction_authoritative" in rowing["rationale_codes"]


def test_general_aerobic_opt_out_blocks_nonzero_aerobic_but_not_independently_supported_rowing():
    profile = {"training_context": {}, "cross_training": {"modalities": {
        name: {"enabled": False, "preference": "avoid"} for name in ("bike", "elliptical", "stair_climber", "running")
    }}}
    result = _diagnostic(profile=profile, category_favorable=True, authorized=True)
    assert _weekly(result, "general_aerobic")["decision_status"] == "blocked"
    assert _weekly(result, "rowing_specific")["decision_status"] == "supported"
    assert result["aggregate_gate_disposition"] == "blocked"
    assert result["original_proposal"]["general_aerobic_minutes"] == 135


def test_disabled_modality_only_blocks_a_proposed_session_in_that_modality():
    profile = {"training_context": {}, "cross_training": {"modalities": {
        "bike": {"enabled": False, "preference": "avoid"},
        "elliptical": {"enabled": True, "familiarity": "regular", "preference": "neutral"},
    }}}
    demand = {"daily": [{"date": "2026-11-30", "physical_minutes_by_category": {"general_aerobic": 30}, "category_modalities": {"general_aerobic": ["bike"]}}]}
    result = _diagnostic(profile=profile, demand=demand)
    bike = next(item for item in result["decisions"] if item["training_dimension"] == "modality:bike")
    assert bike["decision_status"] == "blocked"
    assert "explicit_modality_opt_out:bike" in bike["rationale_codes"]


def test_unavailable_strength_opt_out_contract_and_soft_preferences_do_not_become_hard_restrictions():
    profile = {"training_context": {"strength_priority": "not_a_current_priority", "current_strength_state": "none"}, "cross_training": {"modalities": {
        "bike": {"enabled": True, "preference": "avoid"},
    }}}
    result = _diagnostic(profile=profile)
    assert result["restriction_evidence"]["typed_strength_opt_out_contract"] == "unavailable"
    assert result["restriction_evidence"]["soft_preference_is_not_hard_restriction"] is True
    assert _weekly(result, "strength")["decision_status"] != "blocked"


def test_dated_restriction_is_deferred_not_a_blanket_strength_prohibition():
    profile = {"training_context": {}, "weekly_availability": [
        {"weekday": "saturday", "prohibited_role_families": ["strength"]},
    ]}
    result = _diagnostic(profile=profile)
    assert result["restriction_evidence"]["dated_category_restriction_contract"] == "deferred_r9h0"
    assert _weekly(result, "strength")["decision_status"] != "blocked"


def _baseline(rowing, *, history=None):
    return {"reported_observations": [
        {"metric": "rowing_minutes", "known": rowing is not None, "value": rowing, "source": "athlete.current_approx_weekly_rowing_minutes" if rowing is not None else "absent"},
        {"metric": "strength_minutes", "known": False, "value": None, "source": "absent"},
        {"metric": "general_aerobic_minutes", "known": False, "value": None, "source": "absent"},
    ], "category_history": history or {}}


def test_established_nonzero_zero_and_unknown_workloads_remain_distinct_without_percentage_from_zero_or_unknown():
    established = _diagnostic(baseline=_baseline(240))
    zero = _diagnostic(baseline=_baseline(0))
    unknown = _diagnostic(baseline=_baseline(None))
    assert _weekly(established, "rowing_specific")["category_workload_context"]["current_workload_status"] == "established_nonzero_current_workload"
    assert _weekly(zero, "rowing_specific")["category_workload_context"]["current_workload_status"] == "explicit_zero_current_workload"
    assert _weekly(unknown, "rowing_specific")["category_workload_context"]["current_workload_status"] == "unknown_current_workload"
    assert _weekly(zero, "rowing_specific")["relative_change_percent"] is None
    assert _weekly(unknown, "rowing_specific")["relative_change_percent"] is None


def test_confirmed_and_possible_category_introduction_preserve_experience_uncertainty():
    confirmed = _diagnostic(baseline=_baseline(0, history={"rowing_specific": {"previous_experience": False}}))
    possible = _diagnostic(baseline=_baseline(0))
    assert _weekly(confirmed, "rowing_specific")["intended_action"] == "confirmed_introduction"
    assert _weekly(possible, "rowing_specific")["intended_action"] == "possible_introduction"
    assert _weekly(possible, "rowing_specific")["category_workload_context"]["previous_experience_status"] == "unknown_previous_experience"


def test_previous_experience_is_not_current_tolerance_or_restoration_without_category_interruption():
    result = _diagnostic(baseline=_baseline(0, history={"rowing_specific": {"previous_experience": True, "previous_workload_minutes": 300}}))
    rowing = _weekly(result, "rowing_specific")
    assert rowing["intended_action"] == "previously_familiar_without_established_current_workload"
    assert rowing["decision_status"] == "review_required"
    assert "no_automatic_restoration_dose" not in rowing["unresolved_evidence_needs"]


def test_category_specific_ordinary_restoration_is_distinct_from_overall_interruption_and_other_category_training():
    restoration = _diagnostic(
        profile={"training_context": {"documented_training_interruption": True}},
        baseline=_baseline(0, history={"rowing_specific": {"previous_experience": True, "previous_workload_minutes": 300, "interruption": "ordinary_category_specific"}}),
    )
    overall_only = _diagnostic(
        profile={"training_context": {"documented_training_interruption": True}}, baseline=_baseline(240),
    )
    assert _weekly(restoration, "rowing_specific")["intended_action"] == "restoration"
    assert "no_automatic_restoration_dose" in _weekly(restoration, "rowing_specific")["unresolved_evidence_needs"]
    assert _weekly(overall_only, "rowing_specific")["intended_action"] == "possible_restoration"
    assert _weekly(overall_only, "strength")["intended_action"] != "restoration"


def test_health_return_and_hard_restriction_override_restoration_acceptance_without_advancement():
    history = {"rowing_specific": {"previous_experience": True, "previous_workload_minutes": 300, "interruption": "health_category_specific"}}
    health = _diagnostic(profile={"training_context": {"health_return_state": "initial_return"}}, baseline=_baseline(0, history=history))
    blocked = _diagnostic(profile={"training_context": {"hard_training_restriction": True}}, baseline=_baseline(0, history=history))
    assert _weekly(health, "rowing_specific")["intended_action"] == "health_related_restoration_review"
    assert "health_return_does_not_advance_by_date" in _weekly(health, "rowing_specific")["rationale_codes"]
    assert _weekly(blocked, "rowing_specific")["decision_status"] == "blocked"


def test_seasonal_rowing_modality_transition_is_not_category_interruption():
    demand = {"daily": [{"date": "2026-11-30", "physical_minutes_by_category": {"rowing_specific": 60}, "category_modalities": {"rowing_specific": ["on_water"]}}]}
    result = _diagnostic(baseline=_baseline(240, history={"rowing_specific": {"previous_experience": True, "previous_modalities": ["erg"]}}), demand=demand)
    transition = _weekly(result, "rowing_specific")["category_workload_context"]["modality_transition"]
    assert transition["status"] == "within_category_modality_transition_requires_review"
    assert transition["category_interruption_inferred"] is False


def _tolerance(*, category=None, pathways=()):
    return {"category_tolerance": category or {}, "pathways": [{"pathway": item, "availability": "available"} for item in pathways]}


def test_overall_favorable_tolerance_does_not_transfer_to_a_category_and_missing_is_not_unfavorable():
    result = _diagnostic(tolerance={"favorable_tolerance_by_pathway": {"basic": True}})
    rowing = _weekly(result, "rowing_specific")
    assert rowing["tolerance_evidence"]["evidence_state"] == "insufficient_overall_evidence_not_category_specific"
    assert rowing["decision_status"] == "review_required"


def test_scoped_favorable_tolerance_requires_developmental_need_and_authorization_for_advisory_candidate():
    scoped = _tolerance(category={"rowing_specific": {"weekly_physical_minutes": {"favorable_pathways": ["basic"]}}}, pathways=("basic",))
    supported = _diagnostic(tolerance=scoped, authorized=True)
    approval = _diagnostic(tolerance=scoped)
    maintenance = _diagnostic(tolerance=scoped, authorized=True, developmental_need=False)
    assert (_weekly(supported, "rowing_specific")["decision_status"], _weekly(supported, "rowing_specific")["intended_action"]) == ("supported", "advisory_progression_candidate")
    assert _weekly(approval, "rowing_specific")["decision_status"] == "approval_required"
    assert _weekly(maintenance, "rowing_specific")["intended_action"] == "maintenance"


def test_unfavorable_and_conflicting_scoped_tolerance_override_authorization_without_changing_allocation():
    unfavorable = _tolerance(category={"rowing_specific": {"weekly_physical_minutes": {"unfavorable_pathways": ["detailed"]}}}, pathways=("detailed",))
    conflicting = _tolerance(category={"rowing_specific": {"weekly_physical_minutes": {"favorable_pathways": ["basic"], "unfavorable_pathways": ["detailed"]}}}, pathways=("basic", "detailed"))
    bad = _diagnostic(tolerance=unfavorable, authorized=True)
    conflict = _diagnostic(tolerance=conflicting, authorized=True)
    assert _weekly(bad, "rowing_specific")["intended_action"] == "reactive_recovery_review"
    assert _weekly(conflict, "rowing_specific")["intended_action"] == "tolerance_conflict_review"
    assert bad["original_proposal"]["rowing_specific_minutes"] == 1709


def test_category_and_dimension_tolerance_are_independent_and_pathways_are_scoped():
    baseline = {"reported_observations": [
        {"metric": "rowing_minutes", "known": True, "value": 240, "source": "reported"},
        {"metric": "strength_minutes", "known": True, "value": 100, "source": "reported"},
        {"metric": "general_aerobic_minutes", "known": True, "value": 10, "source": "reported"},
    ]}
    tolerance = _tolerance(category={
        "rowing_specific": {"weekly_physical_minutes": {"favorable_pathways": ["minimal"]}},
        "strength": {"weekly_physical_minutes": {"unfavorable_pathways": ["detailed"]}},
    }, pathways=("detailed", "basic", "minimal"))
    result = _diagnostic(baseline=baseline, tolerance=tolerance, authorized=True)
    rowing = _weekly(result, "rowing_specific")
    strength = _weekly(result, "strength")
    frequency = next(item for item in result["decisions"] if item["physical_category"] == "rowing_specific" and item["training_dimension"] == "weekly_exposure_frequency")
    duration = next(item for item in result["decisions"] if item["physical_category"] == "rowing_specific" and item["training_dimension"] == "maximum_session_duration")
    assert rowing["decision_status"] == "supported"
    assert strength["intended_action"] == "reactive_recovery_review"
    assert frequency["tolerance_evidence"]["evidence_state"] == "unavailable_category_specific_tolerance_contract"
    assert duration["tolerance_evidence"]["evidence_state"] == "unavailable_category_specific_tolerance_contract"
    assert result["aggregate_gate_disposition"] == "review_required"


def test_modality_familiarity_performance_and_private_readiness_are_not_tolerance_evidence():
    profile = {"training_context": {}, "cross_training": {"modalities": {"bike": {"enabled": True, "familiarity": "regular", "preference": "neutral"}}}}
    result = _diagnostic(profile=profile, tolerance={})
    bike = next(item for item in result["decisions"] if item["training_dimension"] == "modality:bike")
    assert bike["tolerance_evidence"]["evidence_state"] == "unavailable_category_specific_tolerance_contract"
    assert bike["tolerance_evidence"]["private_readiness_used"] is False


def test_restrictions_and_introduction_remain_authoritative_over_typed_favorable_evidence():
    scoped = _tolerance(category={"rowing_specific": {"weekly_physical_minutes": {"favorable_pathways": ["basic"]}}}, pathways=("basic",))
    blocked = _diagnostic(profile={"training_context": {"hard_training_restriction": True}}, tolerance=scoped, authorized=True)
    introduced = _diagnostic(baseline=_baseline(0, history={"rowing_specific": {"previous_experience": False}}), tolerance=scoped, authorized=True)
    assert _weekly(blocked, "rowing_specific")["decision_status"] == "blocked"
    assert _weekly(introduced, "rowing_specific")["decision_status"] == "review_required"
    assert introduced["prescription_unchanged"] is True


def test_authorization_partial_pathway_and_assessment_reference_do_not_establish_scoped_favorable_tolerance():
    partial = _tolerance(category={"rowing_specific": {"weekly_physical_minutes": {"assessment_id": "old-2k"}}}, pathways=("basic",))
    result = _diagnostic(tolerance=partial, authorized=True)
    rowing = _weekly(result, "rowing_specific")
    assert rowing["decision_status"] == "review_required"
    assert rowing["tolerance_evidence"]["evidence_state"] == "insufficient_relevant_tolerance_evidence"
    assert rowing["tolerance_evidence"]["completion_alone_establishes_favorable_tolerance"] is False
    assert rowing["tolerance_evidence"]["provenance"] == ["category_tolerance_typed_context"]


def test_maintenance_does_not_silently_accept_an_increased_proposed_allocation():
    increased = SimpleNamespace(planning_start=date(2026, 11, 30), planning_end=date(2026, 12, 6), recommended_physical_minutes=338, rowing_specific_minutes=338, strength_minutes=0, general_aerobic_minutes=0)
    unchanged = SimpleNamespace(planning_start=date(2026, 11, 30), planning_end=date(2026, 12, 6), recommended_physical_minutes=240, rowing_specific_minutes=240, strength_minutes=0, general_aerobic_minutes=0)
    scoped = _tolerance(category={"rowing_specific": {"weekly_physical_minutes": {"favorable_pathways": ["basic"]}}}, pathways=("basic",))
    baseline = _baseline(240)
    increased_result = _diagnostic(baseline=baseline, tolerance=scoped, authorized=True, developmental_need=False, allocation=increased)
    unchanged_result = _diagnostic(baseline=baseline, tolerance=scoped, authorized=True, developmental_need=False, allocation=unchanged)
    increased_rowing = _weekly(increased_result, "rowing_specific")
    unchanged_rowing = _weekly(unchanged_result, "rowing_specific")
    assert (increased_rowing["intended_action"], increased_rowing["decision_status"], increased_rowing["gate_result"]) == ("maintenance", "review_required", "proposed_change_requires_review")
    assert (unchanged_rowing["intended_action"], unchanged_rowing["decision_status"]) == ("maintenance", "provisional")
    assert increased_result["original_proposal"]["rowing_specific_minutes"] == 338


def test_complete_weeks_are_compared_independently_when_planning_period_average_hides_a_demanding_week():
    allocation = SimpleNamespace(planning_start=date(2026, 11, 30), planning_end=date(2026, 12, 13), recommended_physical_minutes=480, rowing_specific_minutes=480, strength_minutes=0, general_aerobic_minutes=0)
    rows = []
    for index in range(14):
        minutes = 300 // 7 if index < 7 else 180 // 7
        rows.append({"date": (date(2026, 11, 30) + timedelta(days=index)).isoformat(), "physical_minutes_by_category": {"rowing_specific": minutes}, "category_session_durations": {"rowing_specific": [minutes]}})
    # Keep exact weekly totals while retaining seven calendar rows per week.
    rows[0]["physical_minutes_by_category"]["rowing_specific"] += 6
    rows[7]["physical_minutes_by_category"]["rowing_specific"] += 5
    result = _diagnostic(baseline=_baseline(240), demand={"daily": rows}, allocation=allocation)
    first = next(item for item in result["decisions"] if item["training_dimension"] == "complete_week_physical_minutes:2026-11-30" and item["physical_category"] == "rowing_specific")
    second = next(item for item in result["decisions"] if item["training_dimension"] == "complete_week_physical_minutes:2026-12-07" and item["physical_category"] == "rowing_specific")
    assert result["weekly_equivalents"]["rowing_specific_minutes"] == 240
    assert (first["proposed_value"], first["decision_status"]) == (300, "review_required")
    assert (second["proposed_value"], second["decision_status"]) == (180, "review_required")
