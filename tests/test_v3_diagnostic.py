from datetime import date
import json

from rowing_plan.diagnostic_v3 import generate_v3_diagnostic_report
from services.api.tests.disposable_browser_fixture import synthetic_profile


def test_reset_spine_diagnostic_separates_capacity_conservation_and_plausibility():
    profile = synthetic_profile()
    profile["season"] = {"start_date": "2026-11-16", "end_date": "2026-11-22"}
    profile["weekly_availability"] = [
        {"weekday": day, "available": True, "max_training_minutes": minutes, "rowing_modes": ["erg"]}
        for day, minutes in (("monday", 70), ("tuesday", 70), ("wednesday", 70), ("thursday", 70), ("friday", 70), ("saturday", 90), ("sunday", 90))
    ]
    profile["athlete"].update({"current_weekly_training_minutes": 350, "current_approx_weekly_rowing_minutes": 240, "current_rowing_sessions_per_week": 4})
    profile["training_context"] = {"current_strength_sessions_per_week": 3, "typical_strength_duration_minutes": 45}
    profile["planning_goals"] = {"primary_goal": "develop_strength", "secondary_goal": "build_endurance"}
    profile["training_preferences"] = {"combined_days": "preferred", "strength_plus_easy_aerobic": "preferred", "strength_plus_hard_rowing": "avoid"}
    report = generate_v3_diagnostic_report(profile, json.load(open("config/defaults.json")), planning_date=date(2026, 11, 16))
    assert report["available_capacity_minutes"] == 530
    assert report["r2"]["recommended_physical_minutes"] < 530
    assert report["conservation"]["pass"]
    assert report["structural_validation"] == "PASS"
    assert report["training_plausibility"] == "GOOD"
    assert "R2 TOTAL DOSE" in report["human_readable"]
    assert "R4 CALENDAR" in report["human_readable"]
    assert "TRAINING PLAUSIBILITY REVIEW" in report["human_readable"]
    assert report["planning_goals"]["primary_goal"] == "develop_strength"
    assert report["training_preferences"]["combined_days"] == "preferred"
    assert report["rowing_modalities"] == ["erg"]
    assert {result.materialized.modality.value for result in report["r5"].results if result.materialized and result.materialized.purpose.value != "strength"} == {"erg"}
