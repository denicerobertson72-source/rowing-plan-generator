from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from services.api.app.main import app
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories
from services.api.tests.disposable_browser_fixture import synthetic_profile


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
