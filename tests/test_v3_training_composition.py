"""M2.8 athlete-relative composition contracts."""
from __future__ import annotations

from datetime import date
import json

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.performance_profile import build_athlete_performance_profile, derive_performance_limiter
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate

AS_OF = date(2026, 11, 16)
CONFIG = json.load(open("config/defaults.json"))


def _test(identifier, protocol, value):
    return {
        "id": identifier, "protocol": protocol, "test_date": "2026-11-10",
        "valid_for_profile": True,
        **({"time_seconds": value} if protocol == "two_k" else {"average_watts": value}),
    }


def _profile(tests, *, race_date="2026-12-01"):
    return {
        "athlete": {
            "experience_level": "experienced", "training_environment": "erg",
            "current_approx_weekly_rowing_minutes": 180,
            "current_weekly_training_minutes": 360,
        },
        "season": {"start_date": "2026-10-01", "end_date": "2026-12-31"},
        "training_context": {"current_strength_sessions_per_week": 2},
        "races": [{"event_name": "Fixture race", "start_date": race_date, "end_date": race_date, "race_dates": [race_date], "priority": "B"}],
        "weekly_availability": [
            {"weekday": item, "available": True, "max_training_minutes": 90, "rowing_modes": ["erg"]}
            for item in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        ],
        "tests": {"testing_blocks": [{"id": "battery", "end_date": "2026-11-10", "performance_tests": tests}]},
    }


def _candidate(profile):
    return generate_plan_sequence_v3_candidate(
        profile, CONFIG, build_intensity_profile(profile, CONFIG), build_power_profile(profile, CONFIG),
        planning_date=AS_OF,
    )


def test_complete_batteries_change_role_composition_inside_same_total_budget():
    aerobic = _profile([
        _test("2k", "two_k", 420), _test("r20", "thirty_min_rate_capped_optional", 150),
        _test("60", "sixty_second", 500), _test("7", "seven_stroke_peak", 800),
    ])
    power = _profile([
        _test("2k", "two_k", 420), _test("r20", "thirty_min_rate_capped_optional", 400),
        _test("60", "sixty_second", 300), _test("7", "seven_stroke_peak", 250),
    ])
    a, b = _candidate(aerobic), _candidate(power)
    assert a.success and b.success
    target_a = a.plan["v2_diagnostics"]["training_composition_target"]
    target_b = b.plan["v2_diagnostics"]["training_composition_target"]
    assert target_a["desired_development_roles"] == ["ut1_aerobic_strength"]
    assert target_b["desired_development_roles"] == ["power_pp"]
    roles_a = {item.get("session_role") for item in a.plan["sessions"]}
    roles_b = {item.get("session_role") for item in b.plan["sessions"]}
    assert "AEROBIC_STRENGTH" in roles_a and "SPRINT_POWER" in roles_b
    total_a = a.plan["v2_diagnostics"]["total_training_load_targets"][0]
    total_b = b.plan["v2_diagnostics"]["total_training_load_targets"][0]
    assert total_a["selected_strength_minutes"] == total_b["selected_strength_minutes"]
    assert total_a["selected_total_physical_minutes"] <= total_a["total_training_maximum"]
    assert total_b["selected_total_physical_minutes"] <= total_b["total_training_maximum"]


def test_imminent_race_overrides_peak_power_development():
    profile = _profile([
        _test("2k", "two_k", 420), _test("r20", "thirty_min_rate_capped_optional", 400),
        _test("60", "sixty_second", 300), _test("7", "seven_stroke_peak", 250),
    ], race_date="2026-11-20")
    candidate = _candidate(profile)
    assert candidate.success
    target = candidate.plan["v2_diagnostics"]["training_composition_target"]
    assert target["dominant_focus"] == "taper_race_readiness"
    assert target["desired_development_roles"] == []


def test_two_k_plus_30r20_uses_available_aerobic_relationship_without_other_component_claims():
    profile = _profile([_test("2k", "two_k", 420), _test("r20", "thirty_min_rate_capped_optional", 150)])
    candidate = _candidate(profile)
    assert candidate.success
    target = candidate.plan["v2_diagnostics"]["training_composition_target"]
    assert target["limiter_mode"] == "athlete_relative_heuristic"
    assert target["desired_development_roles"] == ["ut1_aerobic_strength"]


def test_optional_tests_degrade_by_supported_relationship_instead_of_all_or_nothing():
    two_k = _test("2k", "two_k", 420)
    r20 = _test("r20", "thirty_min_rate_capped_optional", 150)
    sixty = _test("60", "sixty_second", 300)
    peak = _test("7", "seven_stroke_peak", 250)
    cases = (
        ((), "unknown", "insufficient_evidence", "unavailable", "none"),
        ((two_k,), "integrated_anchor", "integrated_rowing_performance", "low", "integrated_anchor"),
        ((r20,), "component_context", "aerobic_strength_context", "low", "component_only"),
        ((two_k, r20), "athlete_relative_heuristic", "aerobic_strength", "medium", "integrated_plus_one_component"),
        ((two_k, sixty), "athlete_relative_heuristic", "anaerobic_lactic", "medium", "integrated_plus_one_component"),
        ((two_k, peak), "athlete_relative_heuristic", "anaerobic_alactic_peak_power", "medium", "integrated_plus_one_component"),
        ((two_k, r20, sixty, peak), "athlete_relative_heuristic", "aerobic_strength", "high", "complete"),
    )
    for tests, mode, focus, confidence, completeness in cases:
        performance = build_athlete_performance_profile(_profile(list(tests)), as_of=AS_OF)
        limiter = derive_performance_limiter(performance)
        assert (limiter.mode, limiter.primary_focus) == (mode, focus)
        assert (performance.confidence, performance.evidence_completeness) == (confidence, completeness)


def test_missing_optional_tests_preserve_normal_dose_and_strength_without_test_derived_shift():
    no_tests = _candidate(_profile([]))
    two_k_only = _candidate(_profile([_test("2k", "two_k", 420)]))
    assert no_tests.success and two_k_only.success
    for candidate in (no_tests, two_k_only):
        composition = candidate.plan["v2_diagnostics"]["training_composition_target"]
        total = candidate.plan["v2_diagnostics"]["total_training_load_targets"][0]
        sessions = candidate.plan["sessions"]
        assert composition["desired_development_roles"] == []
        assert total["total_training_target"] == 360
        assert any(item["mode"] == "strength" for item in sessions)
        assert sum(item["mode"] == "erg" for item in sessions) >= 2
    assert no_tests.plan["v2_diagnostics"]["total_training_load_targets"] == two_k_only.plan["v2_diagnostics"]["total_training_load_targets"]


def test_each_supported_two_k_component_pair_reaches_only_its_compatible_composition_role():
    two_k = _test("2k", "two_k", 420)
    cases = (
        (_test("r20", "thirty_min_rate_capped_optional", 150), "ut1_aerobic_strength"),
        (_test("60", "sixty_second", 300), "anaerobic_an"),
        (_test("7", "seven_stroke_peak", 250), "power_pp"),
    )
    for component, expected_role in cases:
        candidate = _candidate(_profile([two_k, component]))
        assert candidate.success
        target = candidate.plan["v2_diagnostics"]["training_composition_target"]
        assert target["desired_development_roles"] == [expected_role]
