from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from rowing_plan.development_recommendation import build_development_recommendation
from rowing_plan.diagnostic_v3 import generate_v3_diagnostic_report, public_v3_diagnostic_payload
from rowing_plan.physiological_evidence import build_physiological_evidence_profile
from services.api.tests.disposable_browser_fixture import synthetic_profile


DAY = date(2026, 11, 25)


def _test(identifier: str, protocol: str, day: str = "2026-01-04", **values):
    return {
        "id": identifier, "protocol": protocol, "test_date": day,
        "valid_for_profile": True, "source": "manual", **values,
    }


def _profile(*tests, goal: str | None = None, environment: str = "mixed") -> dict:
    profile = {
        "athlete": {"training_environment": environment, "recent_training_consistency": "consistent"},
        "season": {"start_date": "2026-11-16", "end_date": "2026-12-31"},
        "tests": {"testing_blocks": [{"id": "winter", "performance_tests": list(tests)}]},
    }
    if goal:
        profile["planning_goals"] = {"primary_goal": goal}
    return profile


def _recommend(profile: dict, *, recovery=False, consistency="regular", next_race=None):
    evidence = build_physiological_evidence_profile(profile, as_of=DAY)
    return build_development_recommendation(
        profile, evidence=evidence, planning_date=DAY,
        phase_context={"phase_type": "general_preparation"},
        recovery_constraints=(object(),) if recovery else (),
        current_training=SimpleNamespace(consistency=consistency, source=SimpleNamespace(value="athlete_estimate")),
        next_race_context=next_race or {},
    )


def test_no_assessment_is_foundational_not_a_physiological_weakness_or_ratio_limiter():
    recommendation = _recommend(_profile())
    assert recommendation.primary_emphasis == "foundational_general_preparation"
    assert recommendation.recommendation_type == "foundational"
    assert recommendation.decision_basis == "general_foundational_context"
    assert "no_formal_performance_assessment_available" in recommendation.limitations
    assert "no_heterogeneous_ratio_ranking" in recommendation.limitations
    assert recommendation.physiological_diagnosis == "not_made"


def test_goal_and_season_context_choose_developmental_emphasis_without_legacy_ratio_comparison():
    sprint = _recommend(_profile(_test("2k", "two_k", time_seconds=496), goal="improve_sprint_power"))
    head = _recommend(_profile(_test("2k", "two_k", time_seconds=496), goal="head_5k"))
    strength = _recommend(_profile(goal="develop_strength"))
    assert sprint.primary_emphasis == "peak_power_development"
    assert head.primary_emphasis == "head_race_specific_preparation"
    assert strength.primary_emphasis == "maximum_strength_development"
    assert all("athlete_relative" not in item for item in (*sprint.rationale, *head.rationale, *strength.rationale))


def test_recovery_and_reconditioning_precede_goal_and_defer_assessment_recommendations():
    profile = _profile(goal="improve_sprint_power")
    recovery = _recommend(profile, recovery=True)
    returning = _recommend(profile, consistency="returning")
    assert recovery.primary_emphasis == "recovery_reconditioning"
    assert recovery.next_most_informative_assessments == ()
    assert returning.recommendation_type == "reconditioning"
    assert "training_tolerance_is_not_formally_assessed" in returning.limitations


def test_missing_r20_is_an_evidence_gap_and_optional_goal_relevant_question_not_a_deficit():
    recommendation = _recommend(_profile(_test("2k", "two_k", time_seconds=496), goal="head_5k"))
    candidates = {item.protocol for item in recommendation.next_most_informative_assessments}
    assert "aerobic_strength_endurance" in recommendation.evidence_gaps
    assert "thirty_min_rate_capped_maximal" in candidates
    assert recommendation.primary_emphasis == "head_race_specific_preparation"
    assert "not_a_physiological_diagnosis" in recommendation.limitations


def test_historical_measurements_do_not_expire_or_trigger_automatic_retesting_without_a_question():
    profile = _profile(_test("2k", "two_k", "2025-01-01", time_seconds=496))
    recommendation = _recommend(profile)
    assert recommendation.primary_emphasis == "foundational_general_preparation"
    assert recommendation.next_most_informative_assessments == ()
    assert "no_upcoming_race_does_not_prevent_progressive_general_preparation" in recommendation.rationale
    assert recommendation.confidence != "high"


def test_maximal_and_submaximal_r20_candidates_remain_distinct_and_advisory():
    no_r20 = _recommend(_profile(_test("2k", "two_k", time_seconds=496), goal="build_endurance"))
    with_max = _recommend(_profile(
        _test("2k", "two_k", time_seconds=496),
        _test("r20", "thirty_min_rate_capped_maximal", average_watts=145, effort_intent="maximal"),
        goal="build_endurance",
    ))
    assert "thirty_min_rate_capped_maximal" in {item.protocol for item in no_r20.next_most_informative_assessments}
    assert "thirty_min_rate_capped_submaximal" in {item.protocol for item in with_max.next_most_informative_assessments}
    assert all("threshold" not in item.developmental_question.lower() for item in with_max.next_most_informative_assessments)


def test_on_water_only_athlete_receives_modality_specific_assessment_consideration_not_erg_requirement():
    profile = _profile(goal="technical_development", environment="on_water")
    recommendation = _recommend(profile)
    assert recommendation.primary_emphasis == "technical_efficiency"
    assert {item.modality for item in recommendation.next_most_informative_assessments} == {"on_water"}
    assert all(item.protocol != "two_k_time_trial" for item in recommendation.next_most_informative_assessments)


def test_seven_stroke_legacy_peak_storage_semantics_and_partial_coverage_are_explicit():
    profile = _profile(
        _test("2k", "two_k", "2025-11-21", time_seconds=496),
        _test("60", "sixty_second", "2026-01-04", average_watts=220),
        _test("seven", "seven_stroke_peak", "2026-01-14", average_watts=287),
    )
    evidence = build_physiological_evidence_profile(profile, as_of=DAY)
    seven = next(item for item in evidence.measurements if item.measurement_id == "seven")
    recommendation = _recommend(profile)
    assert [(item.key, item.value) for item in seven.outcomes if item.key == "peak_watts"] == [("peak_watts", 287)]
    assert "seven_stroke_protocol_declares_peak_power_from_legacy_average_watts_storage" in seven.provenance
    assert dict(seven.protocol_metadata)["source_power_field"] == "average_watts"
    assert evidence.coverage_description == "partial_domain_coverage_not_comprehensive"
    assert "complementary_evidence_is_not_comprehensive_physiological_coverage" in recommendation.limitations


def test_persisted_shaped_historical_battery_supports_foundational_context_without_anaerobic_diagnosis():
    profile = _profile(
        _test("2k", "two_k", "2025-11-21", time_seconds=496),
        _test("60", "sixty_second", "2026-01-04", average_watts=220),
        _test("seven", "seven_stroke_peak", "2026-01-14", average_watts=287),
    )
    profile["athlete"].update({"current_weekly_training_minutes": 500, "current_approx_weekly_rowing_minutes": 240})
    profile["training_context"] = {"current_strength_sessions_per_week": 3}
    recommendation = _recommend(profile)
    assert recommendation.primary_emphasis == "foundational_general_preparation"
    assert recommendation.secondary_emphasis == "aerobic_capacity_development"
    assert recommendation.decision_basis == "general_foundational_context"
    assert "anaerobic_performance_development" not in {recommendation.primary_emphasis, recommendation.secondary_emphasis}
    assert "thirty_min_rate_capped_maximal" in {item.protocol for item in recommendation.next_most_informative_assessments}


def test_private_readiness_payload_cannot_change_diagnostic_recommendation():
    profile = _profile(goal="build_endurance")
    baseline = _recommend(profile)
    profile["private_readiness"] = {"hrv": 20, "symptom_notes": "private"}
    with_private_data = _recommend(profile)
    assert with_private_data == baseline


def test_diagnostic_recommendation_is_public_and_cannot_change_existing_r2_r3_r6_or_conservation():
    profile = synthetic_profile()
    profile["season"] = {"start_date": "2026-11-16", "end_date": "2026-11-22"}
    profile["planning_goals"] = {"primary_goal": "improve_sprint_power"}
    report = generate_v3_diagnostic_report(profile, planning_date=date(2026, 11, 16))
    recommendation = report["development_recommendation"]
    assert recommendation["diagnostic_only"] is True
    assert recommendation["r6_prescription_status"] == "unchanged_legacy_path"
    assert report["conservation"]["pass"]
    assert report["r6_results"]
    assert public_v3_diagnostic_payload(report)["development_recommendation"] == recommendation
    assert "EVIDENCE-INFORMED DEVELOPMENTAL RECOMMENDATION" in report["human_readable"]
