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


def test_internal_v2_choice_reaches_dormant_branch_when_enabled(monkeypatch):
    monkeypatch.setenv("V2_PLANNER_INTERNAL_ENABLED", "true")
    request = PlanGenerationRequest(athlete_profile=synthetic_profile())
    with pytest.raises(main.PlannerRoutingError, match="v2_planner_route_not_wired"):
        main.build_plan(request, internal_planner_choice=main.PlannerChoice.V2)


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
