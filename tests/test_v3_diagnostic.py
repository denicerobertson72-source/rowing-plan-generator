import json
from datetime import date

from fastapi.encoders import jsonable_encoder

from rowing_plan.diagnostic_v3 import (
    generate_persisted_v3_diagnostic_report,
    generate_v3_diagnostic_report,
    load_persisted_diagnostic_input,
    public_v3_diagnostic_payload,
)
from rowing_plan.generation_history import recover_generation_history
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
    timings = {}
    counts = {}
    report = generate_v3_diagnostic_report(
        profile, planning_date=date(2026, 11, 16),
        diagnostic_timings_ms=timings, diagnostic_counts=counts,
    )
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
    assert report["r3_weekly_evidence"] == {
        "observation_window_days": 21,
        "raw_observation_rowing_minutes": 240,
        "raw_observation_strength_exposures": 3,
        "weekly_rowing_minutes": 240,
        "weekly_strength_exposures": 3,
        "rowing_evidence_source": "explicit_weekly_profile_context",
        "strength_evidence_source": "explicit_weekly_profile_context",
    }
    assert {result.materialized.modality.value for result in report["r5"].results if result.materialized and result.materialized.purpose.value != "strength"} == {"erg"}
    assert report["diagnostic_timing_ms"]
    assert report["diagnostic_counts"]["r4_day_count"] == len(report["calendar"])
    assert report["diagnostic_counts"]["r5_result_count"] == len(report["workouts"])
    public = public_v3_diagnostic_payload(report)
    assert "r4" not in public and "r5" not in public
    assert public["r4_status"] == {"success": True, "infeasibilities": [], "diagnostics": []}
    assert public["calendar"] == report["calendar"]
    assert public["workouts"] == report["workouts"]
    assert public["r3_weekly_evidence"] == report["r3_weekly_evidence"]
    assert public["r6_results"] == report["r6_results"]
    assert public["dated_context"] == report["dated_context"]
    assert json.loads(json.dumps(jsonable_encoder(public)))["conservation"]["pass"] is True


def test_persisted_diagnostic_adapter_uses_only_read_methods():
    class ReadOnlyRepository:
        def __init__(self): self.calls = []
        def get(self, athlete_id): self.calls.append(("get", athlete_id)); return {"athlete": {"display_name": "Persisted"}}
        def latest_plan_for_athlete(self, athlete_id): self.calls.append(("latest_plan_for_athlete", athlete_id)); return {"plan_id": "plan-1", "plan": {"sessions": []}}
        def logs_for_plan(self, plan_id): self.calls.append(("logs_for_plan", plan_id)); return [{"payload": {"actual_duration_min": 30}}]
        def weekly_overrides(self, athlete_id): self.calls.append(("weekly_overrides", athlete_id)); return [{"payload": {"action_type": "move"}}]
        def __getattr__(self, name): raise AssertionError(f"write_or_unexpected_method:{name}")
    repository = ReadOnlyRepository()
    loaded = load_persisted_diagnostic_input(repository, "athlete-1")
    assert loaded.athlete_id == "athlete-1"
    assert loaded.profile["athlete"]["display_name"] == "Persisted"
    assert len(loaded.workout_logs) == len(loaded.weekly_overrides) == 1
    assert [name for name, _value in repository.calls] == ["get", "latest_plan_for_athlete", "logs_for_plan", "weekly_overrides"]


def test_persisted_diagnostic_consumes_the_canonical_completed_history_path():
    profile = synthetic_profile()
    profile["season"] = {"start_date": "2026-11-16", "end_date": "2026-11-22"}
    profile["weekly_availability"] = [
        {"weekday": day, "available": True, "max_training_minutes": 70, "rowing_modes": ["erg"]}
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    ]
    profile["races"] = []
    profile["planning_goals"] = {"primary_goal": "develop_strength"}
    profile["tests"] = {"testing_blocks": [{"performance_tests": [{"test_type": "2k", "date": "2026-11-01", "result_seconds": 480}]}]}
    completed_days = ("2026-10-30", "2026-11-09")
    sessions = [
        {"date": day, "session_id": "ROW", "mode": "erg", "total_training_minutes": 40}
        for day in completed_days
    ] + [{"date": "2026-11-10", "session_id": "UNFINISHED", "mode": "erg", "total_training_minutes": 90}]
    logs = {
        f"{day}:ROW:erg": {"status": "completed", "activity_type": "erg", "actual_duration_min": 40}
        for day in completed_days
    }
    logs["2026-11-10:UNFINISHED:erg"] = {"status": "in_progress", "activity_type": "erg", "actual_duration_min": 90}

    class ReadOnlyRepository:
        def get(self, athlete_id): return profile
        def latest_plan_for_athlete(self, athlete_id):
            return {"plan_id": "plan-current", "plan": {"sessions": sessions, "generation_input_context": {"actual_plan_ids": ("plan-history",)}}}
        def logs_for_plan(self, plan_id):
            if plan_id == "plan-history":
                return [{"session_key": key, "payload": payload} for key, payload in logs.items()]
            return []
        def weekly_overrides(self, athlete_id): return [{"payload": {"action_type": "move"}}]
        def __getattr__(self, name): raise AssertionError(f"unexpected_repository_method:{name}")

    repository = ReadOnlyRepository()
    persisted = load_persisted_diagnostic_input(repository, "athlete-1")
    report = generate_persisted_v3_diagnostic_report(repository, "athlete-1", planning_date=date(2026, 11, 16))
    locked, actuals, source_ids = recover_generation_history(
        {"plan_id": persisted.prior_plan_id, "plan": persisted.prior_plan},
        lambda plan_id: persisted.workout_logs_by_plan.get(plan_id, ()),
    )
    canonical = generate_v3_diagnostic_report(
        persisted.profile, planning_date=date(2026, 11, 16), prior_plan=persisted.prior_plan,
        history_actuals=actuals, locked_sessions=locked,
    )

    assert report["current_training"] == canonical["current_training"]
    assert report["current_training"]["source"] == "mixed"
    assert len(report["current_training"]["completed_history_ids"]) == len(completed_days)
    assert all("UNFINISHED" not in item for item in report["current_training"]["completed_history_ids"])
    assert report["persisted_input_audit"]["actual_plan_ids"] == source_ids
    assert report["persisted_input_audit"]["weekly_overrides_planner_input"] is False
    assert report["persisted_input_audit"]["performance_tests_source"] == "persisted_profile"
    assert report["persisted_input_audit"]["race_context_source"] == "persisted_profile"
    assert report["dated_context"]
    assert all(item["phase_provenance"] == "season_phase" for item in report["dated_context"])
    assert report["r6_results"]
    assert report["conservation"]["pass"]


def test_persisted_style_21_day_observation_is_reported_and_normalized_before_r3_allocation():
    profile = synthetic_profile()
    profile["season"] = {"start_date": "2026-11-18", "end_date": "2026-12-25"}
    profile["weekly_availability"] = [
        {"weekday": day, "available": True, "max_training_minutes": 90, "rowing_modes": ["erg"]}
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    ]
    profile["athlete"].pop("current_approx_weekly_rowing_minutes", None)
    profile["athlete"].pop("current_rowing_sessions_per_week", None)
    profile["training_context"] = {"typical_strength_duration_minutes": 45}
    history_actuals = {
        f"actual:{day}:erg": {"status": "completed", "activity_type": "erg", "actual_duration_min": 80}
        for day in ("2026-10-28", "2026-11-04", "2026-11-10")
    }
    history_actuals.update({
        f"actual:{day}:strength": {"status": "completed", "activity_type": "strength", "actual_duration_min": 45}
        for day in ("2026-10-31", "2026-11-07", "2026-11-14")
    })

    report = generate_v3_diagnostic_report(
        profile, planning_date=date(2026, 11, 18), history_actuals=history_actuals,
    )

    evidence = report["r3_weekly_evidence"]
    assert evidence["observation_window_days"] == 21
    assert evidence["raw_observation_rowing_minutes"] == 240
    assert evidence["raw_observation_strength_exposures"] == 3
    assert evidence["weekly_rowing_minutes"] == 80
    assert evidence["weekly_strength_exposures"] == 1
    assert evidence["rowing_evidence_source"] == "normalized_observation_window_evidence"
    assert evidence["strength_evidence_source"] == "normalized_observation_window_evidence"
    assert any(item["recommended_strength_exposures"] > 0 for item in report["r3"]["segments"])
    assert report["conservation"]["pass"]


def test_persisted_style_sparse_actuals_do_not_dilute_explicit_r2_weekly_total_baseline():
    profile = synthetic_profile()
    profile["season"] = {"start_date": "2026-11-18", "end_date": "2026-12-25"}
    profile["weekly_availability"] = [
        {"weekday": day, "available": True, "max_training_minutes": 90, "rowing_modes": ["erg"]}
        for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    ]
    profile["athlete"].update({
        "current_weekly_training_minutes": 500,
        "current_approx_weekly_rowing_minutes": 240,
    })
    profile["training_context"] = {
        "current_strength_sessions_per_week": 3,
        "typical_strength_duration_minutes": 45,
    }
    history_actuals = {
        f"actual:{day}:erg": {"status": "completed", "activity_type": "erg", "actual_duration_min": 60}
        for day in ("2026-10-29", "2026-11-02", "2026-11-06", "2026-11-10")
    }

    report = generate_v3_diagnostic_report(
        profile, planning_date=date(2026, 11, 18), history_actuals=history_actuals,
    )

    evidence = report["r2_baseline_evidence"]
    assert evidence["actual_history_sufficient"] is False
    assert evidence["explicit_weekly_total_training_minutes"] == 500
    assert evidence["selected_baseline_weekly_minutes"] == 500
    assert evidence["selected_baseline_source"] == "explicit_weekly_total_training_estimate"
    assert evidence["insufficient_actual_history_not_used_for_dose"] is True
    assert report["r2"]["recommended_physical_minutes"] == 2_714
    assert report["conservation"]["pass"]


def _quality_context_profile(*, planning_end="2026-12-27", races=True):
    profile = synthetic_profile()
    profile["season"] = {
        "start_date": "2026-11-16", "end_date": planning_end,
        "current_weekly_endurance_minutes": 210, "target_peak_weekly_endurance_minutes": 270,
    }
    profile["recurring_activities"] = []
    profile["tests"] = {"testing_blocks": [{"performance_tests": [
        {"protocol": "two_k", "test_date": "2026-11-01", "time_seconds": 496, "valid_for_profile": True},
        {"protocol": "sixty_second", "test_date": "2026-11-01", "average_watts": 220, "valid_for_profile": True},
        {"protocol": "seven_stroke_peak", "test_date": "2026-11-01", "average_watts": 287, "valid_for_profile": True},
    ]}]}
    profile["races"] = ([{
        "event_name": "Context race", "start_date": "2026-12-20", "end_date": "2026-12-20",
        "priority": "B", "race_type": "head_5k",
    }] if races else [])
    return profile


def test_diagnostic_uses_each_segment_actual_phase_for_r6_r4_and_r5():
    report = generate_v3_diagnostic_report(
        _quality_context_profile(), planning_date=date(2026, 11, 16),
    )

    contexts = {item["allocation_segment_id"]: item for item in report["dated_context"]}
    threshold = next(item for item in report["r6_results"] if item["plan"] and item["plan"]["segment_start"] == date(2026, 11, 16))
    threshold_context = contexts[threshold["plan"]["allocation_segment_id"]]
    assert threshold_context["phase_id"] == "threshold_development"
    assert threshold_context["phase_provenance"] == "season_phase"
    assert threshold["success"] and threshold["outcome"] == "requirements_generated"
    assert threshold["plan"]["requirements"][0]["physiological_band"] == "AT"
    assert any(item["purpose"] == "threshold" and item["status"] == "success" for item in report["workouts"])
    assert len({item["phase_id"] for item in report["dated_context"]}) > 2
    assert report["conservation"]["pass"]


def test_general_preparation_catalog_conflict_is_reported_not_disguised_as_successful_easy_quality():
    report = generate_v3_diagnostic_report(
        _quality_context_profile(planning_end="2026-11-22", races=False), planning_date=date(2026, 11, 16),
    )

    result = report["r6_results"][0]
    assert report["dated_context"][0]["phase_id"] == "general_preparation"
    assert report["dated_context"][0]["phase_provenance"] == "season_phase"
    assert "no_race_dates_configured" in report["dated_context"][0]["evidence_limitations"]
    assert result["success"] is False
    assert result["outcome"] == "infeasible"
    assert [item["code"] for item in result["infeasibilities"]] == ["no_r5_compatible_quality_envelope"]
    assert result["plan"]["requirements"] == ()
    # R4 still honours physical R3 minutes as ordinary rowing; it does not
    # claim that R6 successfully prescribed a quality exposure.
    assert report["conservation"]["pass"]


def test_diagnostic_represents_race_taper_recovery_fixed_and_flexible_rest_context_without_placing_flexible_rest():
    profile = _quality_context_profile()
    for item in profile["weekly_availability"]:
        item["fixed_rest"] = item["weekday"] == "wednesday"
    profile["recurring_activities"] = [{
        "activity_id": "flex-rest", "activity_type": "rest", "sessions_per_week": 1,
        "scheduling_status": "flexible", "fixed_days": [], "preferred_days": ["friday"],
        "allowed_days": ["friday", "saturday"], "prohibited_days": [], "planner_may_choose_day": True,
    }]
    report = generate_v3_diagnostic_report(profile, planning_date=date(2026, 11, 16))

    contexts = report["dated_context"]
    assert any(item["phase_id"] == "taper" for item in contexts)
    recovery = next(item for item in contexts if item["phase_id"] == "post_race_recovery")
    assert recovery["recovery_context"]["active"] is True
    assert any(item["recovery_context"]["fixed_rest_dates"] for item in contexts)
    assert {item["recovery_context"]["flexible_rest_sessions_per_week"] for item in contexts} == {1}
    assert report["recovery_context_summary"]["readiness_auto_adjustment"] == "not_applied"


def test_completed_quality_strength_and_race_evidence_are_auditable_context_not_new_prescription():
    profile = _quality_context_profile(planning_end="2026-11-22", races=False)
    profile["races"] = [{
        "event_name": "Past race", "start_date": "2026-11-14", "end_date": "2026-11-14",
        "priority": "B", "race_type": "head_5k",
    }]
    prior_plan = {"sessions": [
        {"date": "2026-11-10", "session_id": "THRESHOLD", "mode": "erg", "band": "AT", "total_training_minutes": 45},
        {"date": "2026-11-12", "session_id": "LIFT", "mode": "strength", "total_training_minutes": 45},
    ]}
    actuals = {
        "2026-11-10:THRESHOLD:erg": {"status": "completed", "activity_type": "erg", "actual_duration_min": 45, "actual_intensity": "AT"},
        "2026-11-12:LIFT:strength": {"status": "completed", "activity_type": "strength", "actual_duration_min": 45},
    }
    report = generate_v3_diagnostic_report(
        profile, planning_date=date(2026, 11, 16), prior_plan=prior_plan, history_actuals=actuals,
    )

    context = report["dated_context"][0]
    evidence = context["completed_exposure_evidence"]
    assert evidence == {"quality_exposures": 2, "strength_exposures": 1, "race_exposures": 1}
    assert context["recovery_context"]["active"] is True
    assert report["r6_results"][0]["outcome"] == "intentional_phase_policy_suppression"
    assert report["conservation"]["pass"]
