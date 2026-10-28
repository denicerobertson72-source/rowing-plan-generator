"""V2-default routing boundary with guarded V1 emergency rollback."""
from __future__ import annotations

from datetime import date, timedelta
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient

from services.api.app import main
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories
from services.api.app.schemas import PlanGenerationRequest
from services.api.tests.disposable_browser_fixture import synthetic_profile


def _client_for_database(path: Path):
    previous = REPOSITORIES._instance
    repository = SQLiteRepositories(path)
    REPOSITORIES._instance = repository
    return TestClient(main.app), repository, previous


def _v2_ready_profile():
    """Real compact season with a finalized TR placement and all V2 role paths."""
    profile = synthetic_profile()
    profile["season"].update({"start_date": "2026-09-05", "end_date": "2026-09-18"})
    profile["races"] = [{"event_name": "V2 target race", "start_date": "2026-09-26", "end_date": "2026-09-26", "priority": "A", "race_type": "head_5k"}]
    return profile


@pytest.mark.parametrize(("value", "expected"), [(None, False), ("", False), ("false", False), ("0", False), ("no", False), ("true", True), ("1", True), ("yes", True), ("on", True)])
def test_emergency_rollback_flag_is_the_only_default_routing_switch(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("V2_PLANNER_EMERGENCY_V1_ROLLBACK", raising=False)
    else:
        monkeypatch.setenv("V2_PLANNER_EMERGENCY_V1_ROLLBACK", value)
    assert main.v2_emergency_v1_rollback_enabled() is expected
    assert main.resolve_planner_choice() is (main.PlannerChoice.V1 if expected else main.PlannerChoice.V2)


def test_explicit_internal_choices_remain_available_for_tests_and_recovery_paths():
    assert main.resolve_planner_choice(main.PlannerChoice.V1) is main.PlannerChoice.V1
    assert main.resolve_planner_choice(main.PlannerChoice.V2) is main.PlannerChoice.V2


def test_internal_v2_choice_reaches_the_real_v2_branch():
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=_v2_ready_profile()), internal_planner_choice=main.PlannerChoice.V2)
    assert plan["v2_diagnostics"]["scheduler_version"] == "v2"


def test_v2_contract_failure_logging_keeps_structured_safe_diagnostics():
    error = main.PlannerRoutingError("v2_plan_contract_failed", {
        "reason_code":"rest_session_conflict", "failure_stage":"plan_assembly",
        "failure_origin":"contract", "date":"2026-10-18", "role":"strength",
        "session_identity":{"date":"2026-10-18", "session_id":"LIFT", "mode":"strength", "source_id":"do-not-log"},
        "profile_payload":"do-not-log",
    })
    assert main.safe_planner_failure_diagnostics(error) == {
        "date":"2026-10-18", "failure_origin":"contract", "failure_stage":"plan_assembly",
        "reason_code":"rest_session_conflict", "role":"strength",
        "session_identity":{"date":"2026-10-18", "session_id":"LIFT", "mode":"strength"},
    }


def test_invalid_profile_fails_before_any_planner_is_selected(monkeypatch):
    called = False

    def forbidden_executor(**_):
        nonlocal called
        called = True
        raise AssertionError("planner must not run for invalid input")

    monkeypatch.setattr(main, "execute_selected_planner", forbidden_executor)
    with pytest.raises(Exception) as error:
        main.build_plan(PlanGenerationRequest(athlete_profile={}))
    assert getattr(error.value, "status_code", None) == 422
    assert called is False


def test_public_generate_defaults_to_v2_and_saves_once(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "routing.sqlite3")
        save_calls: list[str] = []
        original_save = repository.save_plan
        monkeypatch.setattr(repository, "save_plan", lambda athlete_id, plan: (save_calls.append(athlete_id), original_save(athlete_id, plan))[1])
        try:
            response = client.post("/api/v1/plans/generate", json={"athlete_profile": synthetic_profile()})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]
    assert len(save_calls) == 1
    assert "planner" not in main.PlanGenerationRequest.model_fields


def test_athlete_regeneration_defaults_to_v2_and_saves_one_new_plan(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "regeneration.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]
    assert latest and latest["version_number"] == 1


def _track_selected_planner(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    return calls


def test_authenticated_repeat_generation_returns_existing_plan_without_planning_or_save(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=main.__name__)
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "noop.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            first = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            calls = _track_selected_planner(monkeypatch)
            save_calls: list[str] = []
            original_save = repository.save_plan
            monkeypatch.setattr(repository, "save_plan", lambda athlete_id, plan: (save_calls.append(athlete_id), original_save(athlete_id, plan))[1])
            second = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert first.status_code == second.status_code == 200
    assert second.json()["plan_id"] == first.json()["plan_id"]
    assert calls == [] and save_calls == []
    assert latest and latest["version_number"] == 1
    assert "planner_generation mode=noop_existing_plan authoritative_planner=v2 regeneration_skipped=True" in caplog.text


def test_authenticated_default_v2_repeat_generation_returns_existing_plan_without_planning(monkeypatch):
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "v2-noop.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            first = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            calls = _track_selected_planner(monkeypatch)
            second = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert first.status_code == second.status_code == 200
    assert first.json()["plan"]["v2_diagnostics"]["scheduler_version"] == "v2"
    assert second.json()["plan_id"] == first.json()["plan_id"]
    assert calls == []
    assert latest and latest["version_number"] == 1


def test_profile_change_regenerates_authenticated_plan(monkeypatch):
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "profile-change.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            assert client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={}).status_code == 200
            profile = repository.get(athlete_id)
            profile["season"]["target_peak_weekly_endurance_minutes"] += 10
            repository.save(athlete_id, profile)
            calls = _track_selected_planner(monkeypatch)
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]
    assert latest and latest["version_number"] == 2


def test_completed_actual_change_regenerates_authenticated_plan(monkeypatch):
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "actual-change.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            first = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            session = next(item for item in first.json()["plan"]["sessions"] if item.get("session_id") != "RACE")
            repository.save_log(first.json()["plan_id"], f'{session["date"]}:{session.get("session_id")}:{session.get("mode")}', {"status": "completed", "actual_duration_min": 42})
            calls = _track_selected_planner(monkeypatch)
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]
    assert latest and latest["version_number"] == 2


def test_race_change_regenerates_authenticated_plan(monkeypatch):
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "race-change.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            assert client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={}).status_code == 200
            profile = repository.get(athlete_id)
            profile["races"][0]["event_name"] = "Changed target race"
            repository.save(athlete_id, profile)
            calls = _track_selected_planner(monkeypatch)
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]


def test_weekly_override_change_regenerates_authenticated_plan(monkeypatch):
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "override-change.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            assert client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={}).status_code == 200
            repository.save_weekly_override(athlete_id, {"week_start": "2026-09-07", "scope": "this_week_only", "changes": [{"date": "2026-09-08", "kind": "rest"}]})
            calls = _track_selected_planner(monkeypatch)
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]


def test_user_fixed_placement_authority_changes_generation_fingerprint():
    common = {
        "profile": synthetic_profile(),
        "choice": main.PlannerChoice.V2,
        "locked_sessions": [],
        "actuals": {},
        "weekly_overrides": [],
        "as_of": date(2026, 10, 12),
    }
    ordinary = main.generation_input_fingerprint(**common, prior_plan={"v2_diagnostics": {"placement_authority": [], "overrides": []}})
    user_fixed = main.generation_input_fingerprint(
        **common,
        prior_plan={"v2_diagnostics": {"placement_authority": [{"placement_id": "strength:1", "date": "2026-10-13", "user_fixed": True, "original_date": "2026-10-11", "override_id": "move-1"}], "overrides": []}},
    )
    assert user_fixed["sha256"] != ordinary["sha256"]


def test_planning_date_boundary_regenerates_authenticated_plan(monkeypatch):
    today = date(2026, 10, 12)
    monkeypatch.setattr(main, "current_planning_date", lambda: today)
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "date-change.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            assert client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={}).status_code == 200
            monkeypatch.setattr(main, "current_planning_date", lambda: today + timedelta(days=1))
            calls = _track_selected_planner(monkeypatch)
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]


def test_planner_algorithm_version_change_regenerates_authenticated_plan(monkeypatch):
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "planner-version-change.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            assert client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={}).status_code == 200
            monkeypatch.setattr(main, "PLANNER_VERSION", "planner-version-test-change")
            calls = _track_selected_planner(monkeypatch)
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]


def test_planner_failure_happens_before_save(monkeypatch):
    def failing_executor(**_):
        raise main.PlannerRoutingError("test_planner_failure")

    monkeypatch.setattr(main, "execute_selected_planner", failing_executor)
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "failure.sqlite3")
        save_calls: list[str] = []
        original_save = repository.save_plan
        monkeypatch.setattr(repository, "save_plan", lambda athlete_id, plan: (save_calls.append(athlete_id), original_save(athlete_id, plan))[1])
        try:
            with pytest.raises(main.PlannerRoutingError, match="test_planner_failure"):
                main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()))
        finally:
            REPOSITORIES._instance = previous
    assert save_calls == []


def test_internal_v2_executes_real_pipeline_validates_contract_and_saves_once(monkeypatch):
    request = PlanGenerationRequest(athlete_profile=_v2_ready_profile())
    with TemporaryDirectory() as directory:
        _, repository, previous = _client_for_database(Path(directory) / "v2-success.sqlite3")
        try:
            plan = main.build_plan(request, internal_planner_choice=main.PlannerChoice.V2)
            athlete_id = repository.create(request.athlete_profile)
            plan_id = repository.save_plan(athlete_id, plan)
            saved = repository.get_plan(plan_id)
        finally:
            REPOSITORIES._instance = previous
    assert plan["v2_diagnostics"]["scheduler_version"] == "v2"
    assert plan["sessions"] and plan["calendar_days"] and plan["weekly_totals"]
    assert any(item["band"] == "TR" and item.get("session_fingerprint") for item in plan["sessions"])
    assert any(item["session_id"] == "LIFT" for item in plan["sessions"])
    assert any(item["designated_rest"] for item in plan["calendar_days"])
    assert saved and saved["plan"] == plan


def test_v2_expected_failure_falls_back_once_for_a_fresh_plan(monkeypatch):
    from rowing_plan import planner_v2

    v1_calls: list[str] = []
    original_v1 = main.generate_plan
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_materialization_failed", "fixture")))
    monkeypatch.setattr(main, "generate_plan", lambda *args, **kwargs: (v1_calls.append("v1"), original_v1(*args, **kwargs))[1])
    request = PlanGenerationRequest(athlete_profile=synthetic_profile())
    with TemporaryDirectory() as directory:
        _, repository, previous = _client_for_database(Path(directory) / "fallback.sqlite3")
        try:
            plan = main.build_plan(request, internal_planner_choice=main.PlannerChoice.V2)
            plan_id = repository.save_plan(repository.create(request.athlete_profile), plan)
            saved = repository.get_plan(plan_id)
        finally:
            REPOSITORIES._instance = previous
    assert v1_calls == ["v1"]
    assert plan["planner_routing"] == {"attempted_planner": "v2", "final_planner": "v1", "fallback_reason_code": "v2_materialization_failed"}
    assert saved and saved["version_number"] == 1 and saved["plan"] == plan


def test_future_v2_user_authority_blocks_an_otherwise_eligible_fallback(monkeypatch):
    from rowing_plan import planner_v2

    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_materialization_failed", "fixture")))
    prior = {"v2_diagnostics": {"scheduler_version": "v2", "placement_authority": [{"date": "2099-01-01", "user_fixed": True, "override_id": "athlete-choice"}], "overrides": []}}
    with pytest.raises(main.PlannerRoutingError, match="v2_materialization_failed"):
        main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V2, prior_plan=prior)


def test_v2_contract_and_unexpected_errors_do_not_fallback(monkeypatch):
    from rowing_plan import planner_v2

    bad = {"plan_version": "0.7.0", "sessions": []}
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: bad)
    with pytest.raises(main.PlannerRoutingError, match="v2_plan_contract_failed"):
        main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V2)
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("invariant")))
    with pytest.raises(RuntimeError, match="invariant"):
        main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V2)


def test_completed_locked_history_is_carried_by_v2_and_does_not_block_fallback(monkeypatch):
    profile = _v2_ready_profile()
    locked = {"date": "2026-09-08", "day": "Tuesday", "phase": "locked", "fixed": True, "mode": "erg", "session_id": "completed-row", "title": "Completed original", "band": "UT2", "total_cardio_minutes": 44, "rowing_minutes": 44, "quality_minutes": 0, "structure": "Original prescription."}
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=profile, locked_sessions=[locked]), internal_planner_choice=main.PlannerChoice.V2)
    assert next(item for item in plan["sessions"] if item["session_id"] == "completed-row") == locked
    assert main.v1_fallback_allowed({"v2_diagnostics": {"scheduler_version": "v2", "placement_authority": [], "overrides": []}})


def test_shadow_flag_off_does_not_execute_v2(monkeypatch):
    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "false")
    monkeypatch.setattr(main, "run_v2_shadow", lambda **_: (_ for _ in ()).throw(AssertionError("shadow must be off")))
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V1)
    assert "v2_diagnostics" not in plan and "planner_routing" not in plan


@pytest.mark.parametrize("value, expected", [(None, False), ("false", False), ("0", False), ("true", True), ("1", True), ("yes", True)])
def test_shadow_flag_parsing_is_independent_from_default_v2_routing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("V2_PLANNER_SHADOW_ENABLED", raising=False)
    else:
        monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", value)
    assert main.v2_planner_shadow_enabled() is expected
    assert main.default_planner_choice() is main.PlannerChoice.V2


def test_public_v1_shadow_success_saves_only_v1_once(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner

    def tracked(**kwargs):
        calls.append(kwargs["choice"])
        return original(**kwargs)

    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "true")
    monkeypatch.setenv("V2_PLANNER_EMERGENCY_V1_ROLLBACK", "true")
    monkeypatch.setattr(main, "execute_selected_planner", tracked)
    request = PlanGenerationRequest(athlete_profile=_v2_ready_profile())
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "shadow-success.sqlite3")
        try:
            response = client.post("/api/v1/plans/generate", json=request.model_dump())
            saved = repository.get_plan(response.json()["plan_id"])
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V1, main.PlannerChoice.V2]
    assert saved and saved["version_number"] == 1 and saved["plan"] == response.json()["plan"]
    assert "v2_diagnostics" not in response.json()["plan"] and "planner_routing" not in response.json()["plan"]


@pytest.mark.parametrize("failure, category, code", [(main.PlannerRoutingError("v2_materialization_failed"), "expected_v2_failure", "v2_materialization_failed"), (RuntimeError("invariant"), "unexpected", "unexpected_v2_exception")])
def test_shadow_failures_are_non_authoritative(monkeypatch, failure, category, code):
    original = main.execute_selected_planner

    def shadow_failure(**kwargs):
        if kwargs["choice"] is main.PlannerChoice.V2:
            raise failure
        return original(**kwargs)

    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "true")
    monkeypatch.setattr(main, "execute_selected_planner", shadow_failure)
    result = main.run_v2_shadow(profile=synthetic_profile(), bands=[], power={}, locked_sessions=[], v1_plan={"sessions": [], "calendar_days": []})
    assert result == main.ShadowPlannerResult(True, False, category, code)
    # An explicit V1 recovery path remains V1 and does not gain fallback metadata.
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V1)
    assert "planner_routing" not in plan


def test_shadow_comparison_is_deterministic_and_does_not_compare_schedule_equality(monkeypatch):
    v2 = main.build_plan(PlanGenerationRequest(athlete_profile=_v2_ready_profile()), internal_planner_choice=main.PlannerChoice.V2)
    v1 = main.build_plan(PlanGenerationRequest(athlete_profile=_v2_ready_profile()), internal_planner_choice=main.PlannerChoice.V1)
    first = main.compare_planner_invariants(v1, v2)
    second = main.compare_planner_invariants(v1, v2)
    names = {name for name, _ in first}
    assert first == second and {"v2_contract_valid", "external_session_keys_unique", "no_rest_session_emitted", "materialization_complete"} <= names
    assert not {"same_training_date", "same_session_count", "winner"} & names


def test_explicit_v2_does_not_create_a_second_shadow_copy(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner

    def tracked(**kwargs):
        calls.append(kwargs["choice"])
        return original(**kwargs)

    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "true")
    monkeypatch.setattr(main, "execute_selected_planner", tracked)
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=_v2_ready_profile()), internal_planner_choice=main.PlannerChoice.V2)
    assert calls == [main.PlannerChoice.V2] and plan["v2_diagnostics"]["scheduler_version"] == "v2"


def test_replacement_shadow_is_default_off_and_never_changes_authoritative_v2(monkeypatch):
    monkeypatch.setenv("REPLACEMENT_PLANNER_SHADOW_ENABLED", "false")
    monkeypatch.setattr(main, "run_replacement_shadow", lambda **_: (_ for _ in ()).throw(AssertionError("replacement shadow must be off")))
    plan = main.build_plan(
        PlanGenerationRequest(athlete_profile=synthetic_profile()),
        internal_planner_choice=main.PlannerChoice.V2, planning_date=date.today(),
    )
    assert plan["v2_diagnostics"]["scheduler_version"] == "v2"


def test_replacement_shadow_failure_is_isolated_from_live_v2(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=main.__name__)
    monkeypatch.setenv("REPLACEMENT_PLANNER_SHADOW_ENABLED", "true")
    result = main.ReplacementShadowResult(True, False, None, "unexpected", "fixture_failure")
    monkeypatch.setattr(main, "run_replacement_shadow", lambda **_: result)
    plan = main.build_plan(
        PlanGenerationRequest(athlete_profile=synthetic_profile()),
        internal_planner_choice=main.PlannerChoice.V2, planning_date=date.today(),
    )
    assert plan["v2_diagnostics"]["scheduler_version"] == "v2"
    assert "fixture_failure" in caplog.text
    assert "Synthetic Step 6B Rower" not in caplog.text


@pytest.mark.parametrize("v3_success", [True, False])
def test_replacement_shadow_runs_independently_when_authoritative_v2_fails(monkeypatch, caplog, v3_success):
    """A V3 diagnostic candidate never changes the current V2 failure path."""
    caplog.set_level(logging.INFO, logger=main.__name__)
    monkeypatch.setenv("REPLACEMENT_PLANNER_SHADOW_ENABLED", "true")
    calls = []
    result = main.ReplacementShadowResult(True, v3_success, None, None if v3_success else "candidate_failure", None if v3_success else "fixture_v3_failure")

    def v2_failure(**kwargs):
        assert kwargs["choice"] is main.PlannerChoice.V2
        raise main.PlannerRoutingError("v2_plan_contract_failed", {"reason_code": "rest_session_conflict"})

    monkeypatch.setattr(main, "execute_selected_planner", v2_failure)
    monkeypatch.setattr(main, "run_replacement_shadow", lambda **kwargs: (calls.append(kwargs), result)[1])
    with pytest.raises(main.PlannerRoutingError, match="v2_plan_contract_failed"):
        main.build_plan(
            PlanGenerationRequest(athlete_profile=synthetic_profile()),
            internal_planner_choice=main.PlannerChoice.V2, planning_date=date.today(),
        )
    assert len(calls) == 1
    assert calls[0]["v2_plan"] is None and calls[0]["v2_failure_reason"] == "rest_session_conflict"
    assert "v2_status=failed" in caplog.text


def test_enabled_replacement_shadow_never_persists_a_second_plan(monkeypatch):
    monkeypatch.setenv("REPLACEMENT_PLANNER_SHADOW_ENABLED", "true")
    calls = []
    monkeypatch.setattr(
        main, "run_replacement_shadow",
        lambda **kwargs: (calls.append(kwargs), main.ReplacementShadowResult(True, True))[1],
    )
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "replacement-shadow.sqlite3")
        save_calls = []
        original_save = repository.save_plan
        monkeypatch.setattr(repository, "save_plan", lambda athlete_id, plan: (save_calls.append(athlete_id), original_save(athlete_id, plan))[1])
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert len(calls) == 1 and calls[0]["v2_plan"]["v2_diagnostics"]["scheduler_version"] == "v2"
    assert len(save_calls) == 1


@pytest.mark.parametrize("enabled, athlete_ids", [("false", ""), ("true", "not-an-athlete-id")])
def test_legacy_live_optin_environment_is_ignored(monkeypatch, enabled, athlete_ids):
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", enabled)
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_ids)
    assert main.default_planner_choice() is main.PlannerChoice.V2
    assert main.resolve_authenticated_athlete_planner() is main.PlannerChoice.V2


def test_public_and_authenticated_generation_default_to_v2_without_allowlist(monkeypatch):
    athlete_profile = synthetic_profile()
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-d1.sqlite3")
        try:
            generic = client.post("/api/v1/plans/generate", json={"athlete_profile": athlete_profile})
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            regeneration = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert generic.status_code == regeneration.status_code == 200
    assert calls == [main.PlannerChoice.V2, main.PlannerChoice.V2]
    assert generic.json()["plan"]["v2_diagnostics"]["scheduler_version"] == "v2"
    assert regeneration.json()["plan"]["v2_diagnostics"]["scheduler_version"] == "v2"


def test_authenticated_default_uses_v2_when_legacy_envs_are_absent(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.delenv("V2_PLANNER_LIVE_OPTIN_ENABLED", raising=False)
    monkeypatch.delenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", raising=False)
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-cohort.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]


def test_default_v2_expected_failure_uses_existing_safe_fallback_once_and_logs_its_mode(monkeypatch, caplog):
    from rowing_plan import planner_v2

    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_schedule_infeasible", "fixture")))
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-fallback.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            with caplog.at_level("INFO"):
                response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2, main.PlannerChoice.V1]
    assert latest and latest["version_number"] == 1
    assert response.json()["plan"]["planner_routing"] == {"attempted_planner": "v2", "final_planner": "v1", "fallback_reason_code": "v2_schedule_infeasible"}
    assert "mode=v2_default_fallback" in caplog.text


def test_concrete_v2_fallback_preserves_structured_failure_diagnostics(monkeypatch):
    from rowing_plan import planner_v2

    diagnostic = {"failure_stage": "quality_finalization", "failure_origin": "finalization", "placement_id": "quality-1", "date": "2026-09-12", "role": "quality", "quality_type": "TR", "reason_code": "malformed_concrete_prescription"}
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_concrete_workout_failed", "fixture", diagnostic)))
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V2)
    assert plan["planner_routing"] == {"attempted_planner": "v2", "final_planner": "v1", "fallback_reason_code": "v2_concrete_workout_failed", "failure_diagnostics": diagnostic}


def test_future_v2_authority_blocks_default_fallback_and_emergency_rollback_before_any_save(monkeypatch, caplog):
    from rowing_plan import planner_v2

    authority_plan = {"sessions": [], "v2_diagnostics": {"scheduler_version": "v2", "placement_authority": [{"date": "2099-01-01", "user_fixed": True, "override_id": "athlete-choice"}], "overrides": []}}
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "future-authority.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            repository.save_plan(athlete_id, authority_plan)
            original = main.execute_selected_planner
            calls: list[main.PlannerChoice] = []
            monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_schedule_infeasible", "fixture")))
            monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
            with caplog.at_level("WARNING"):
                live_failure = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            assert live_failure.status_code == 409
            assert live_failure.json()["detail"]["error_code"] == "v2_fallback_blocked_by_future_authority"
            assert calls == [main.PlannerChoice.V2]
            assert repository.latest_plan_for_athlete(athlete_id)["version_number"] == 1
            calls.clear()
            monkeypatch.setenv("V2_PLANNER_EMERGENCY_V1_ROLLBACK", "true")
            transition_block = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            assert transition_block.status_code == 409
            assert transition_block.json()["detail"]["error_code"] == "v2_future_authority_requires_resolution"
            assert calls == []
            assert repository.latest_plan_for_athlete(athlete_id)["version_number"] == 1
        finally:
            REPOSITORIES._instance = previous
    assert "future_authority_blocks_v1_transition=true" in caplog.text


def test_emergency_rollback_runs_v1_for_a_plan_without_future_v2_authority(monkeypatch):
    monkeypatch.setenv("V2_PLANNER_EMERGENCY_V1_ROLLBACK", "true")
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "safe-optout.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            second = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert second.status_code == 200
    assert "v2_diagnostics" not in second.json()["plan"]
    assert latest and latest["version_number"] == 1


def test_unowned_athlete_is_rejected_before_any_planner_selection(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-ownership.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "owner-user")
            main.app.dependency_overrides[main.current_user_id] = lambda: "other-user"
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            main.app.dependency_overrides.pop(main.current_user_id, None)
            REPOSITORIES._instance = previous
    assert response.status_code == 403
    assert calls == []


def test_default_v2_does_not_run_a_second_shadow_execution(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "true")
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-no-shadow.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]


def test_emergency_rollback_changes_only_the_next_authoritative_plan(monkeypatch):
    """The rollback switch is server-wide and keeps completed history intact."""
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-transition.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            first_v2 = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            assert first_v2.status_code == 200
            completed = next(item for item in first_v2.json()["plan"]["sessions"] if item.get("quality_minutes", 0) == 0)
            session_key = f'{completed["date"]}:{completed.get("session_id")}:{completed.get("mode")}'
            repository.save_log(first_v2.json()["plan_id"], session_key, {"status": "completed"})
            monkeypatch.setenv("V2_PLANNER_EMERGENCY_V1_ROLLBACK", "true")
            final_v1 = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert final_v1.status_code == 200
    assert first_v2.json()["plan"]["v2_diagnostics"]["scheduler_version"] == "v2"
    assert "v2_diagnostics" not in final_v1.json()["plan"]
    assert latest and latest["version_number"] == 2
    assert any(item.get("date") == completed["date"] and item.get("session_id") == completed["session_id"] and item.get("mode") == completed["mode"] for item in final_v1.json()["plan"]["sessions"])
