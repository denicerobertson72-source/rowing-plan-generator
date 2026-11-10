"""M2.8 athlete-relative composition contracts."""
from __future__ import annotations

from datetime import date
import json

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
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


def test_incomplete_battery_keeps_conservative_baseline_without_composition_shift():
    profile = _profile([_test("2k", "two_k", 420), _test("r20", "thirty_min_rate_capped_optional", 150)])
    candidate = _candidate(profile)
    assert candidate.success
    target = candidate.plan["v2_diagnostics"]["training_composition_target"]
    assert target["limiter_mode"] == "unknown"
    assert target["desired_development_roles"] == []
    assert "incomplete_or_unknown_battery_preserves_conservative_baseline" in target["rationale"]
