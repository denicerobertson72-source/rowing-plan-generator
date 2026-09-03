import json
from datetime import date, timedelta

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.periodization import PLANNING_MODEL_VERSION, build_season_phases, build_weekly_training_intents
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan


def _profile():
    return {
        "athlete": {"display_name": "Intent test", "experience_level": "experienced"},
        "season": {"start_date": "2026-09-07", "end_date": "2026-10-25", "current_weekly_endurance_minutes": 180, "target_peak_weekly_endurance_minutes": 240, "default_block_pattern": "3_build_1_deload"},
        "tests": {"resting_hr": 58, "max_hr": 177, "erg_2k_seconds": None, "multi_duration_power_tests": {}},
        "weekly_availability": [{"weekday": day, "available": True, "max_training_minutes": 90, "rowing_modes": ["erg"]} for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")],
        "races": [{"event_name": "Championship Head", "start_date": "2026-10-24", "end_date": "2026-10-25", "priority": "A", "race_type": "head_5k"}],
        "recurring_activities": [
            {"activity_id": "lift", "activity_type": "strength", "sessions_per_week": 2, "scheduling_status": "fixed", "fixed_days": ["monday", "friday"], "preferred_days": [], "allowed_days": [], "prohibited_days": [], "same_day_rules": {"rowing_allowed": False}},
            {"activity_id": "rest", "activity_type": "rest", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["sunday"], "preferred_days": [], "allowed_days": [], "prohibited_days": []},
        ],
    }


def test_season_phases_are_contiguous_versioned_and_race_aware():
    phases = build_season_phases(_profile())
    assert phases[0]["start_date"] == "2026-09-07"
    assert phases[-1]["end_date"] == "2026-10-25"
    assert all(item["algorithm_version"] == PLANNING_MODEL_VERSION for item in phases)
    assert all(item["source_ids"] and item["reason"] for item in phases)
    assert any(item["phase_type"] == "taper" and item["target_race_id"] for item in phases)
    assert any(item["phase_type"] == "race" for item in phases)
    for left, right in zip(phases, phases[1:]):
        assert left["end_date"] < right["start_date"]


def test_weekly_intent_records_taper_and_requested_commitments_without_changing_sessions():
    profile = _profile()
    config = json.load(open("config/defaults.json"))
    plan = generate_plan(profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config))
    intents = plan["weekly_training_intents"]
    taper = next(item for item in intents if item["load_direction"] == "taper")
    assert plan["season_phases"]
    assert taper["taper_volume_factor"] == 0.50
    assert taper["target_total_rowing_minutes"] < next(item["target_total_rowing_minutes"] for item in intents if item["week_start"] == "2026-10-05")
    assert taper["primary_session_roles"] == ["RACE_PACE", "TECHNIQUE_EASY"]
    normal = next(item for item in intents if item["week_start"] == "2026-09-07")
    assert normal["target_strength_sessions"] == 2
    assert normal["target_rest_days"] == 1
    assert normal["target_rowing_sessions"] == 4
    # Step 2 adds only persisted metadata; the established daily session path remains intact.
    assert all("session_id" in item for item in plan["sessions"])


def test_novice_initial_frequency_and_volume_follow_demonstrated_history():
    profile = _profile()
    profile["athlete"] = {
        "display_name": "New rower",
        "experience_level": "novice",
        "current_rowing_sessions_per_week": 2,
        "recent_training_consistency": "building",
        "longest_comfortable_continuous_row_minutes": 10,
        "current_approx_weekly_rowing_minutes": 60,
    }
    profile["season"] = {"start_date": "2026-09-07", "end_date": "2026-11-15", "current_weekly_endurance_minutes": 90, "target_peak_weekly_endurance_minutes": 150, "default_block_pattern": "3_build_1_deload"}
    profile["races"] = []
    profile["recurring_activities"] = [{"activity_id": "rest", "activity_type": "rest", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["sunday"], "preferred_days": [], "allowed_days": [], "prohibited_days": []}]
    intents = build_weekly_training_intents(profile, build_season_phases(profile))
    first_block = intents[:3]
    assert [item["target_rowing_sessions"] for item in first_block] == [2, 2, 2]
    assert all(item["target_rowing_sessions"] <= 3 for item in first_block)
    assert [item["target_total_rowing_minutes"] for item in first_block] == [60, 60, 60]
    assert intents[3]["target_rowing_sessions"] == 3
    assert intents[3]["target_total_rowing_minutes"] == 70
    config = json.load(open("config/defaults.json"))
    plan = generate_plan(profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config))
    actual_first_three = []
    for intent in intents[:3]:
        week_start = date.fromisoformat(intent["week_start"])
        actual_first_three.append(sum(1 for session in plan["sessions"] if session["rowing_minutes"] and week_start <= date.fromisoformat(session["date"]) <= week_start + timedelta(days=6)))
    assert actual_first_three == [2, 2, 2]
    profile["athlete"].pop("current_approx_weekly_rowing_minutes")
    tolerance_only = build_weekly_training_intents(profile, build_season_phases(profile))[0]
    assert tolerance_only["target_total_rowing_minutes"] == 40


def test_flexible_commitments_and_mixed_taper_week_are_reflected_in_intent():
    profile = _profile()
    profile["season"] = {"start_date": "2026-09-01", "end_date": "2026-11-08", "current_weekly_endurance_minutes": 180, "target_peak_weekly_endurance_minutes": 270, "default_block_pattern": "3_build_1_deload"}
    profile["races"] = [
        {"event_name": "September C", "start_date": "2026-09-26", "end_date": "2026-09-26", "priority": "C", "race_type": "head_5k"},
        {"event_name": "October B", "start_date": "2026-10-17", "end_date": "2026-10-17", "priority": "B", "race_type": "head_5k"},
        {"event_name": "November A", "start_date": "2026-11-07", "end_date": "2026-11-08", "priority": "A", "race_type": "head_5k"},
    ]
    profile["recurring_activities"] = [
        {"activity_id": "private", "activity_type": "private_coaching", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["wednesday"], "preferred_days": [], "allowed_days": [], "prohibited_days": []},
        {"activity_id": "coach", "activity_type": "coached_row", "sessions_per_week": 1, "scheduling_status": "flexible", "fixed_days": [], "preferred_days": [], "allowed_days": ["tuesday", "thursday"], "prohibited_days": []},
        {"activity_id": "lift", "activity_type": "strength", "sessions_per_week": 2, "scheduling_status": "preferred", "fixed_days": [], "preferred_days": ["monday", "friday"], "allowed_days": ["tuesday", "thursday", "saturday"], "prohibited_days": [], "same_day_rules": {"rowing_allowed": False}},
        {"activity_id": "rest", "activity_type": "rest", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["sunday"], "preferred_days": [], "allowed_days": [], "prohibited_days": []},
    ]
    config = json.load(open("config/defaults.json"))
    plan = generate_plan(profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config))
    normal = next(item for item in plan["weekly_training_intents"] if item["week_start"] == "2026-10-05")
    assert (normal["target_strength_sessions"], normal["target_private_coaching_sessions"], normal["target_coached_row_sessions"], normal["target_rest_days"]) == (2, 1, 1, 1)
    normal_sessions = [item for item in plan["sessions"] if "2026-10-05" <= item["date"] <= "2026-10-11"]
    assert [item["day"] for item in normal_sessions if item["session_id"] == "LIFT"] == ["Monday", "Friday"]
    assert [item["day"] for item in normal_sessions if item["title"] == "Private coaching"] == ["Wednesday"]
    assert [item["day"] for item in normal_sessions if item["title"] == "Coached row"] == ["Thursday"]
    assert not any(item["day"] == "Sunday" for item in normal_sessions)
    lift_days = {item["date"] for item in normal_sessions if item["session_id"] == "LIFT"}
    assert not any(item["date"] in lift_days and item["rowing_minutes"] for item in normal_sessions if item["session_id"] != "LIFT")
    mixed = next(item for item in plan["weekly_training_intents"] if item["week_start"] == "2026-10-26")
    assert {item["phase_type"] for item in mixed["phase_mix"]} == {"race_specific_preparation", "taper"}
    assert mixed["transition_note"]
    assert mixed["volume_target_factor"] == 0.643
    post_b = next(item for item in plan["weekly_training_intents"] if item["week_start"] == "2026-10-19")
    assert post_b["load_direction"] == "recover_then_build"
    assert post_b["primary_session_roles"] == ["RACE_PACE", "THRESHOLD"]
    assert post_b["transition_note"] == "Begin the week with post-race recovery, then resume A-race-specific preparation."
    assert post_b["volume_target_factor"] > 0.82  # race recovery, not an extra generic deload


def test_experienced_general_development_starts_from_demonstrated_frequency_not_availability():
    profile = _profile()
    profile["races"] = []
    profile["athlete"].update({
        "current_rowing_sessions_per_week": 4,
        "current_approx_weekly_rowing_minutes": 240,
        "recent_training_consistency": "building",
    })
    # Six days can accommodate a row, including alongside these athlete-approved
    # lifts.  The demonstrated four rows, rather than capacity, sets week one.
    profile["recurring_activities"][0]["same_day_rules"] = {"rowing_allowed": True}
    config = json.load(open("config/defaults.json"))
    plan = generate_plan(profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config))
    first_intent = next(item for item in plan["weekly_training_intents"] if item["week_start"] == "2026-09-07")
    first_week_rows = [item for item in plan["sessions"] if "2026-09-07" <= item["date"] <= "2026-09-13" and item["rowing_minutes"]]
    assert first_intent["target_rowing_sessions"] == 4
    assert len(first_week_rows) == 4
