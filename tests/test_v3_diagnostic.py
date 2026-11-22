from datetime import date

from rowing_plan.diagnostic_v3 import (
    generate_persisted_v3_diagnostic_report,
    generate_v3_diagnostic_report,
    load_persisted_diagnostic_input,
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
    report = generate_v3_diagnostic_report(profile, planning_date=date(2026, 11, 16))
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
    assert report["conservation"]["pass"]
