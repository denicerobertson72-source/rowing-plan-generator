"""V2.8A routing boundary: V1 remains the sole public planner."""
from __future__ import annotations

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
def test_v2_internal_flag_parsing_and_default_resolution(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("V2_PLANNER_INTERNAL_ENABLED", raising=False)
    else:
        monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", value)
    assert main.v2_planner_internal_enabled() is expected
    assert main.resolve_planner_choice() is main.PlannerChoice.V1


def test_internal_v2_choice_is_denied_while_flag_is_off(monkeypatch):
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "false")
    with pytest.raises(main.PlannerRoutingError, match="v2_planner_not_enabled"):
        main.resolve_planner_choice(main.PlannerChoice.V2)


def test_internal_v2_choice_reaches_the_real_v2_branch_when_enabled(monkeypatch):
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=_v2_ready_profile()), internal_planner_choice=main.PlannerChoice.V2)
    assert plan["v2_diagnostics"]["scheduler_version"] == "v2"


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


def test_public_generate_stays_v1_with_internal_flag_enabled_and_saves_once(monkeypatch):
    calls: list[str] = []
    original_generate = main.generate_plan

    def tracked_generate(*args, **kwargs):
        calls.append("v1")
        return original_generate(*args, **kwargs)

    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    monkeypatch.setattr(main, "generate_plan", tracked_generate)
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
    assert calls == ["v1"]
    assert len(save_calls) == 1
    assert "planner" not in main.PlanGenerationRequest.model_fields


def test_athlete_regeneration_uses_v1_and_saves_one_new_plan(monkeypatch):
    calls: list[str] = []
    original_generate = main.generate_plan

    def tracked_generate(*args, **kwargs):
        calls.append("v1")
        return original_generate(*args, **kwargs)

    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    monkeypatch.setattr(main, "generate_plan", tracked_generate)
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "regeneration.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == ["v1"]
    assert latest and latest["version_number"] == 1


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
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
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

    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
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

    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_materialization_failed", "fixture")))
    prior = {"v2_diagnostics": {"scheduler_version": "v2", "placement_authority": [{"date": "2099-01-01", "user_fixed": True, "override_id": "athlete-choice"}], "overrides": []}}
    with pytest.raises(main.PlannerRoutingError, match="v2_materialization_failed"):
        main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V2, prior_plan=prior)


def test_v2_contract_and_unexpected_errors_do_not_fallback(monkeypatch):
    from rowing_plan import planner_v2

    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    bad = {"plan_version": "0.7.0", "sessions": []}
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: bad)
    with pytest.raises(main.PlannerRoutingError, match="v2_plan_contract_failed"):
        main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V2)
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("invariant")))
    with pytest.raises(RuntimeError, match="invariant"):
        main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V2)


def test_completed_locked_history_is_carried_by_v2_and_does_not_block_fallback(monkeypatch):
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    profile = _v2_ready_profile()
    locked = {"date": "2026-09-08", "day": "Tuesday", "phase": "locked", "fixed": True, "mode": "erg", "session_id": "completed-row", "title": "Completed original", "band": "UT2", "total_cardio_minutes": 44, "rowing_minutes": 44, "quality_minutes": 0, "structure": "Original prescription."}
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=profile, locked_sessions=[locked]), internal_planner_choice=main.PlannerChoice.V2)
    assert next(item for item in plan["sessions"] if item["session_id"] == "completed-row") == locked
    assert main.v1_fallback_allowed({"v2_diagnostics": {"scheduler_version": "v2", "placement_authority": [], "overrides": []}})


def test_shadow_flag_off_does_not_execute_v2(monkeypatch):
    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "false")
    monkeypatch.setattr(main, "run_v2_shadow", lambda **_: (_ for _ in ()).throw(AssertionError("shadow must be off")))
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()))
    assert "v2_diagnostics" not in plan and "planner_routing" not in plan


@pytest.mark.parametrize("value, expected", [(None, False), ("false", False), ("0", False), ("true", True), ("1", True), ("yes", True)])
def test_shadow_flag_parsing_is_independent_from_internal_v2_permission(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("V2_PLANNER_SHADOW_ENABLED", raising=False)
    else:
        monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", value)
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "false")
    assert main.v2_planner_shadow_enabled() is expected
    assert main.v2_planner_internal_enabled() is False


def test_public_v1_shadow_success_saves_only_v1_once(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner

    def tracked(**kwargs):
        calls.append(kwargs["choice"])
        return original(**kwargs)

    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "true")
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
    # The public route remains V1 and does not gain fallback metadata.
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()))
    assert "planner_routing" not in plan


def test_shadow_comparison_is_deterministic_and_does_not_compare_schedule_equality(monkeypatch):
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    v2 = main.build_plan(PlanGenerationRequest(athlete_profile=_v2_ready_profile()), internal_planner_choice=main.PlannerChoice.V2)
    v1 = main.build_plan(PlanGenerationRequest(athlete_profile=_v2_ready_profile()))
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

    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "true")
    monkeypatch.setattr(main, "execute_selected_planner", tracked)
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=_v2_ready_profile()), internal_planner_choice=main.PlannerChoice.V2)
    assert calls == [main.PlannerChoice.V2] and plan["v2_diagnostics"]["scheduler_version"] == "v2"


@pytest.mark.parametrize("raw, expected", [(None, frozenset()), ("", frozenset()), (" , , ", frozenset()), ("not-a-uuid", frozenset()), ("550e8400-e29b-41d4-a716-446655440000", frozenset({"550e8400-e29b-41d4-a716-446655440000"})), (" 550E8400-E29B-41D4-A716-446655440000 ,550e8400-e29b-41d4-a716-446655440000, bad,", frozenset({"550e8400-e29b-41d4-a716-446655440000"}))])
def test_live_cohort_parser_is_uuid_normalized_deterministic_and_fail_safe(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", raising=False)
        actual = main.parse_v2_live_optin_athlete_ids()
    else:
        actual = main.parse_v2_live_optin_athlete_ids(raw)
    assert actual == expected


@pytest.mark.parametrize("value, expected", [(None, False), ("false", False), ("0", False), ("true", True), ("1", True), ("yes", True), ("on", True)])
def test_live_global_flag_uses_the_existing_boolean_convention(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("V2_PLANNER_LIVE_OPTIN_ENABLED", raising=False)
    else:
        monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", value)
    assert main.v2_planner_live_optin_enabled() is expected


def test_live_cohort_eligibility_and_pure_precedence(monkeypatch):
    athlete = "550e8400-e29b-41d4-a716-446655440000"
    cohort = main.parse_v2_live_optin_athlete_ids(athlete)
    assert not main.is_live_v2_athlete(athlete, live_enabled=False, cohort=cohort)
    assert not main.is_live_v2_athlete("550e8400-e29b-41d4-a716-446655440001", live_enabled=True, cohort=cohort)
    assert main.is_live_v2_athlete(athlete.upper(), live_enabled=True, cohort=cohort)
    assert main.resolve_authenticated_athlete_planner(athlete_id=athlete, live_enabled=True, cohort=cohort) is main.PlannerChoice.V2
    assert main.resolve_authenticated_athlete_planner(athlete_id=athlete, internal_planner_choice=main.PlannerChoice.V1, live_enabled=True, cohort=cohort) is main.PlannerChoice.V1
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    assert main.resolve_authenticated_athlete_planner(athlete_id=athlete, internal_planner_choice=main.PlannerChoice.V2, live_enabled=True, cohort=cohort) is main.PlannerChoice.V2


def test_live_configuration_keeps_generic_generation_v1_but_routes_an_owned_cohort_athlete_to_v2(monkeypatch):
    athlete_profile = synthetic_profile()
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "true")
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", "550e8400-e29b-41d4-a716-446655440000")
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-d1.sqlite3")
        try:
            generic = client.post("/api/v1/plans/generate", json={"athlete_profile": athlete_profile})
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_id)
            regeneration = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert generic.status_code == regeneration.status_code == 200
    assert calls == [main.PlannerChoice.V1, main.PlannerChoice.V2]
    assert "v2_diagnostics" not in generic.json()["plan"]
    assert regeneration.json()["plan"]["v2_diagnostics"]["scheduler_version"] == "v2"


@pytest.mark.parametrize(("live_enabled", "cohort_member"), [(False, True), (True, False)])
def test_authenticated_live_routing_requires_both_global_flag_and_verified_cohort_membership(monkeypatch, live_enabled, cohort_member):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "true" if live_enabled else "false")
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-cohort.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_id if cohort_member else "550e8400-e29b-41d4-a716-446655440000")
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V1]


def test_live_v2_expected_failure_uses_existing_safe_fallback_once_and_logs_its_mode(monkeypatch, caplog):
    from rowing_plan import planner_v2

    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "true")
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_schedule_infeasible", "fixture")))
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-fallback.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_id)
            with caplog.at_level("INFO"):
                response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2, main.PlannerChoice.V1]
    assert latest and latest["version_number"] == 1
    assert response.json()["plan"]["planner_routing"] == {"attempted_planner": "v2", "final_planner": "v1", "fallback_reason_code": "v2_schedule_infeasible"}
    assert "mode=v2_live_optin_fallback" in caplog.text


def test_concrete_v2_fallback_preserves_structured_failure_diagnostics(monkeypatch):
    from rowing_plan import planner_v2

    diagnostic = {"failure_stage": "quality_finalization", "failure_origin": "finalization", "placement_id": "quality-1", "date": "2026-09-12", "role": "quality", "quality_type": "TR", "reason_code": "malformed_concrete_prescription"}
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_concrete_workout_failed", "fixture", diagnostic)))
    plan = main.build_plan(PlanGenerationRequest(athlete_profile=synthetic_profile()), internal_planner_choice=main.PlannerChoice.V2)
    assert plan["planner_routing"] == {"attempted_planner": "v2", "final_planner": "v1", "fallback_reason_code": "v2_concrete_workout_failed", "failure_diagnostics": diagnostic}


@pytest.mark.parametrize("optout", ["global_off", "cohort_removal"])
def test_future_v2_authority_blocks_live_fallback_and_v2_to_v1_optout_before_any_save(monkeypatch, caplog, optout):
    from rowing_plan import planner_v2

    authority_plan = {"sessions": [], "v2_diagnostics": {"scheduler_version": "v2", "placement_authority": [{"date": "2099-01-01", "user_fixed": True, "override_id": "athlete-choice"}], "overrides": []}}
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "future-authority.sqlite3")
        try:
            athlete_id = repository.create(synthetic_profile(), "development-user")
            repository.save_plan(athlete_id, authority_plan)
            original = main.execute_selected_planner
            calls: list[main.PlannerChoice] = []
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "true")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_id)
            monkeypatch.setattr(planner_v2, "generate_plan_v2", lambda *args, **kwargs: (_ for _ in ()).throw(planner_v2.V2PlanningError("v2_schedule_infeasible", "fixture")))
            monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
            with caplog.at_level("WARNING"):
                live_failure = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            assert live_failure.status_code == 409
            assert live_failure.json()["detail"]["error_code"] == "v2_fallback_blocked_by_future_authority"
            assert calls == [main.PlannerChoice.V2]
            assert repository.latest_plan_for_athlete(athlete_id)["version_number"] == 1
            calls.clear()
            if optout == "global_off":
                monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "false")
            else:
                monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", "")
            transition_block = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            assert transition_block.status_code == 409
            assert transition_block.json()["detail"]["error_code"] == "v2_future_authority_requires_resolution"
            assert calls == []
            assert repository.latest_plan_for_athlete(athlete_id)["version_number"] == 1
        finally:
            REPOSITORIES._instance = previous
    assert "future_authority_blocks_v1_transition=true" in caplog.text


@pytest.mark.parametrize("optout", ["global_off", "cohort_removal"])
def test_safe_v2_to_v1_optout_preserves_completed_history_and_versions_once(monkeypatch, optout):
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "true")
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "safe-optout.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_id)
            first = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            assert first.status_code == 200
            session = first.json()["plan"]["sessions"][0]
            session_key = f'{session["date"]}:{session.get("session_id")}:{session.get("mode")}'
            repository.save_log(first.json()["plan_id"], session_key, {"status": "completed"})
            if optout == "global_off":
                monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "false")
            else:
                monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", "")
            second = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert second.status_code == 200
    assert "v2_diagnostics" not in second.json()["plan"]
    assert latest and latest["version_number"] == 2
    assert any(item.get("date") == session["date"] and item.get("session_id") == session["session_id"] and item.get("mode") == session["mode"] for item in second.json()["plan"]["sessions"])


def test_unowned_allowlisted_athlete_is_rejected_before_any_planner_selection(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "true")
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-ownership.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "owner-user")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_id)
            main.app.dependency_overrides[main.current_user_id] = lambda: "other-user"
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            main.app.dependency_overrides.pop(main.current_user_id, None)
            REPOSITORIES._instance = previous
    assert response.status_code == 403
    assert calls == []


def test_live_v2_does_not_run_a_second_shadow_execution(monkeypatch):
    calls: list[main.PlannerChoice] = []
    original = main.execute_selected_planner
    monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "true")
    monkeypatch.setenv("V2_PLANNER_SHADOW_ENABLED", "true")
    monkeypatch.setattr(main, "execute_selected_planner", lambda **kwargs: (calls.append(kwargs["choice"]), original(**kwargs))[1])
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-no-shadow.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_id)
            response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous
    assert response.status_code == 200
    assert calls == [main.PlannerChoice.V2]


def test_realistic_v1_to_v2_to_v1_transition_preserves_completed_history_and_versions(monkeypatch):
    """Live cohort routing changes only the next authoritative PlanVersion."""
    with TemporaryDirectory() as directory:
        client, repository, previous = _client_for_database(Path(directory) / "live-transition.sqlite3")
        try:
            athlete_id = repository.create(_v2_ready_profile(), "development-user")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "false")
            first_v1 = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            assert first_v1.status_code == 200
            completed = next(item for item in first_v1.json()["plan"]["sessions"] if item.get("quality_minutes", 0) == 0)
            session_key = f'{completed["date"]}:{completed.get("session_id")}:{completed.get("mode")}'
            repository.save_log(first_v1.json()["plan_id"], session_key, {"status": "completed"})
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ENABLED", "true")
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", athlete_id)
            first_v2 = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            second_v2 = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            monkeypatch.setenv("V2_PLANNER_LIVE_OPTIN_ATHLETE_IDS", "")
            final_v1 = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            latest = repository.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance = previous
    assert first_v2.status_code == second_v2.status_code == final_v1.status_code == 200
    assert first_v2.json()["plan"]["v2_diagnostics"]["scheduler_version"] == "v2"
    assert second_v2.json()["plan"]["v2_diagnostics"]["scheduler_version"] == "v2"
    assert "v2_diagnostics" not in final_v1.json()["plan"]
    assert latest and latest["version_number"] == 4
    assert any(item.get("date") == completed["date"] and item.get("session_id") == completed["session_id"] and item.get("mode") == completed["mode"] for item in final_v1.json()["plan"]["sessions"])
