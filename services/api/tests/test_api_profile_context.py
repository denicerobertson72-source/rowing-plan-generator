import logging
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from services.api.app import main
from services.api.app.main import app
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories
from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.planning_normalization import normalize_planning_inputs


def _client(path: Path):
    previous = REPOSITORIES._instance
    REPOSITORIES._instance = SQLiteRepositories(path)
    return TestClient(app), previous


def _context() -> dict:
    return {
        "strength_experience": "intermediate",
        "current_strength_sessions_per_week": 2,
        "strength_equipment_access": ["full_gym", "dumbbells"],
        "strength_priority": "support_rowing",
        "strength_limitations": "No overhead barbell work.",
        "training_restriction_source": "clinician",
        "training_restriction_summary": "Gradual return; cap sessions at 45 minutes.",
    }


def test_profile_context_persists_without_affecting_legacy_profile_loading():
    with TemporaryDirectory() as directory:
        client, previous = _client(Path(directory) / "profile-context.sqlite3")
        try:
            old_profile = synthetic_profile()
            created = client.post("/api/v1/athletes", json={"athlete_profile": old_profile})
            athlete_id = created.json()["athlete_id"]
            loaded_old = client.get(f"/api/v1/athletes/{athlete_id}")
            updated_profile = loaded_old.json()["athlete_profile"]
            updated_profile["training_context"] = _context()
            updated_profile.setdefault("preferences", {})["readiness_checkins_enabled"] = True
            saved = client.put(f"/api/v1/athletes/{athlete_id}", json={
                "athlete_profile": updated_profile,
                "expected_revision": loaded_old.json()["profile_revision"],
            })
            reloaded = client.get(f"/api/v1/athletes/{athlete_id}")
        finally:
            REPOSITORIES._instance = previous

    assert created.status_code == loaded_old.status_code == saved.status_code == reloaded.status_code == 200
    assert "training_context" not in loaded_old.json()["athlete_profile"]
    stored = reloaded.json()["athlete_profile"]
    assert stored["training_context"] == _context()
    assert stored["preferences"]["readiness_checkins_enabled"] is True


def test_cross_training_capability_round_trips_without_affecting_legacy_profiles():
    with TemporaryDirectory() as directory:
        client, previous = _client(Path(directory) / "cross-training-profile.sqlite3")
        try:
            created = client.post("/api/v1/athletes", json={"athlete_profile": synthetic_profile()})
            athlete_id = created.json()["athlete_id"]
            loaded = client.get(f"/api/v1/athletes/{athlete_id}")
            updated = loaded.json()["athlete_profile"]
            updated["cross_training"] = {"modalities": {
                "bike": {"enabled": True, "familiarity": "regular", "preference": "prefer"},
                "elliptical": {"enabled": False, "familiarity": None, "preference": "neutral"},
            }}
            saved = client.put(f"/api/v1/athletes/{athlete_id}", json={"athlete_profile": updated, "expected_revision": loaded.json()["profile_revision"]})
            reloaded = client.get(f"/api/v1/athletes/{athlete_id}")
        finally:
            REPOSITORIES._instance = previous
    assert created.status_code == loaded.status_code == saved.status_code == reloaded.status_code == 200
    assert "cross_training" not in loaded.json()["athlete_profile"]
    assert reloaded.json()["athlete_profile"]["cross_training"] == updated["cross_training"]


def test_invalid_review_only_strength_frequency_is_rejected_without_creating_context_data():
    with TemporaryDirectory() as directory:
        client, previous = _client(Path(directory) / "invalid-profile-context.sqlite3")
        try:
            profile = synthetic_profile()
            profile["training_context"] = {"current_strength_sessions_per_week": 8}
            response = client.post("/api/v1/athletes", json={"athlete_profile": profile})
        finally:
            REPOSITORIES._instance = previous

    assert response.status_code == 422
    assert "Current strength sessions per week must be a whole number from 0 to 7." in response.json()["detail"]["validation_errors"]


def test_profile_context_save_then_authenticated_v3_generation_preserves_context_and_normalized_inputs(monkeypatch, caplog):
    """The production route must tolerate persisted review-only profile data."""

    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ENABLED", "true")
    monkeypatch.setattr(main, "current_planning_date", lambda: date(2026, 11, 1))
    with TemporaryDirectory() as directory:
        client, previous = _client(Path(directory) / "profile-context-v3.sqlite3")
        try:
            base = synthetic_profile()
            base["season"].update({"start_date": "2026-11-01", "end_date": "2026-11-20"})
            base["races"] = [{
                "event_name": "Production-shaped Tuesday race", "start_date": "2026-11-10",
                "end_date": "2026-11-10", "race_dates": ["2026-11-10"],
                "priority": "B", "race_type": "head_5k",
            }]
            created = client.post("/api/v1/athletes", json={"athlete_profile": base})
            athlete_id = created.json()["athlete_id"]
            updated = dict(base)
            updated["training_context"] = {
                **_context(),
                "current_strength_sessions_per_week": 0,
                "strength_limitations": "",
                "training_restriction_summary": "",
            }
            updated["preferences"] = {**base.get("preferences", {}), "readiness_checkins_enabled": False}
            saved = client.put(f"/api/v1/athletes/{athlete_id}", json={
                "athlete_profile": updated, "expected_revision": created.json()["profile_revision"],
            })
            monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", athlete_id)
            with caplog.at_level(logging.INFO, logger="services.api.app.main"):
                generated = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
            reloaded = client.get(f"/api/v1/athletes/{athlete_id}")
        finally:
            REPOSITORIES._instance = previous

    assert created.status_code == saved.status_code == generated.status_code == reloaded.status_code == 200
    assert generated.json()["plan"]["v2_diagnostics"]["scheduler_version"] == "replacement-v3"
    assert reloaded.json()["athlete_profile"]["training_context"] == updated["training_context"]
    assert reloaded.json()["athlete_profile"]["preferences"]["readiness_checkins_enabled"] is False
    assert normalize_planning_inputs(base, planning_date=date(2026, 11, 1)) == normalize_planning_inputs(updated, planning_date=date(2026, 11, 1))
    assert "planner_generation_stage_start planner=replacement_v3 stage=normalization" in caplog.text
    assert "planner_generation_timing planner=replacement_v3 stage=validation duration_ms=" in caplog.text


def test_authenticated_v3_failure_logs_safe_current_stage_and_reference(monkeypatch, caplog):
    """A production-only V3 exception identifies its stage without profile contents."""

    import rowing_plan.replacement_plan_v3 as replacement_v3

    def injected_failure(*args, **kwargs):
        raise RuntimeError("injected_v3_stage_failure")

    monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ENABLED", "true")
    monkeypatch.setattr(main, "current_planning_date", lambda: date(2026, 11, 1))
    monkeypatch.setattr(replacement_v3, "reduce_physiological_state", injected_failure)
    with TemporaryDirectory() as directory:
        client, previous = _client(Path(directory) / "v3-diagnostics.sqlite3")
        try:
            profile = synthetic_profile()
            profile["training_context"] = {
                **_context(),
                "training_restriction_summary": "private-health-text-must-not-be-logged",
            }
            created = client.post("/api/v1/athletes", json={"athlete_profile": profile})
            athlete_id = created.json()["athlete_id"]
            monkeypatch.setenv("REPLACEMENT_PLANNER_LIVE_ATHLETE_IDS", athlete_id)
            with caplog.at_level(logging.INFO, logger="services.api.app.main"):
                response = client.post(f"/api/v1/athletes/{athlete_id}/plans/generate", json={})
        finally:
            REPOSITORIES._instance = previous

    assert response.status_code == 500
    body = response.json()["detail"]
    assert body["error_code"] == "plan_generation_failed"
    messages = caplog.text
    assert "planner_generation_stage_start planner=replacement_v3 stage=physiological_reduction" in messages
    assert f"reference={body['error_id']}" in messages
    assert "planner=replacement_v3" in messages
    assert "exception_class=RuntimeError" in messages
    assert "exception_message=injected_v3_stage_failure" in messages
    assert "stage=physiological_reduction" in messages
    assert "private-health-text-must-not-be-logged" not in messages
