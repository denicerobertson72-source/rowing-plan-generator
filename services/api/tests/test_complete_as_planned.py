from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from services.api.app.main import _generation_history, app
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories


def _plan():
    return {"sessions": [
        {"date": "2026-06-01", "session_id": "UT2", "mode": "erg", "band": "UT2", "title": "Aerobic base", "total_cardio_minutes": 44, "session_role": "AEROBIC_BASE"},
        {"date": "2026-06-01", "session_id": "LIFT", "mode": "strength", "band": "STRENGTH", "title": "Strength", "strength_minutes": 35, "total_training_minutes": 35},
        {"date": "2026-06-02", "session_id": "AT", "mode": "erg", "band": "AT", "title": "Threshold", "total_cardio_minutes": 35, "session_role": "THRESHOLD"},
        {"date": "2999-06-03", "session_id": "UT2", "mode": "erg", "band": "UT2", "title": "Future", "total_cardio_minutes": 40},
    ], "calendar_days": [{"date": "2026-06-04", "state": "designated_rest"}]}


def test_completed_as_planned_uses_one_existing_log_and_preserves_prescription_snapshot():
    with TemporaryDirectory() as directory:
        previous = REPOSITORIES._instance; repository = SQLiteRepositories(Path(directory) / "complete.sqlite3"); REPOSITORIES._instance = repository
        try:
            athlete = repository.create({"athlete": {"display_name": "Rower"}}, "development-user")
            plan_id = repository.save_plan(athlete, _plan()); client = TestClient(app)
            key = "2026-06-01:UT2:erg"
            first = client.post(f"/api/v1/plans/{plan_id}/sessions/{key}/complete-as-planned")
            second = client.post(f"/api/v1/plans/{plan_id}/sessions/{key}/complete-as-planned")
            logs = repository.logs_for_plan(plan_id)
            locked, actuals, _ = _generation_history(repository.get_plan(plan_id))
        finally:
            REPOSITORIES._instance = previous
    assert first.status_code == second.status_code == 200
    assert first.json()["log_id"] == second.json()["log_id"] and len(logs) == 1
    actual = logs[0]["payload"]
    assert actual["provenance"] == "completed_as_planned"
    assert actual["actual_duration_min"] == 44 and actual["actual_intensity"] == "UT2"
    assert actual["planned_session"]["session_role"] == "AEROBIC_BASE"
    assert actuals[key]["planned_session_key"] == key
    assert len(locked) == 1 and locked[0]["session_id"] == "UT2"


def test_completion_is_session_key_scoped_and_detailed_log_replaces_quick_provenance():
    with TemporaryDirectory() as directory:
        previous = REPOSITORIES._instance; repository = SQLiteRepositories(Path(directory) / "detail.sqlite3"); REPOSITORIES._instance = repository
        try:
            athlete = repository.create({"athlete": {"display_name": "Rower"}}, "development-user")
            plan_id = repository.save_plan(athlete, _plan()); client = TestClient(app)
            row_key, lift_key = "2026-06-01:UT2:erg", "2026-06-01:LIFT:strength"
            row = client.post(f"/api/v1/plans/{plan_id}/sessions/{row_key}/complete-as-planned")
            lift = client.post(f"/api/v1/plans/{plan_id}/sessions/{lift_key}/complete-as-planned")
            detailed = client.post(f"/api/v1/plans/{plan_id}/sessions/{row_key}/log", json={"status":"modified", "completion":"yes", "actual_duration_min":30, "actual_intensity":"UT3", "rpe":5, "notes":"Shortened"})
            logs = {item["session_key"]: item["payload"] for item in repository.logs_for_plan(plan_id)}
            undo_modified = client.delete(f"/api/v1/plans/{plan_id}/sessions/{row_key}/complete-as-planned")
            undo_quick = client.delete(f"/api/v1/plans/{plan_id}/sessions/{lift_key}/complete-as-planned")
        finally:
            REPOSITORIES._instance = previous
    assert row.status_code == lift.status_code == detailed.status_code == 200
    assert len(logs) == 2 and logs[row_key]["provenance"] is None
    assert logs[row_key]["actual_duration_min"] == 30 and logs[lift_key]["provenance"] == "completed_as_planned"
    assert undo_modified.status_code == 409 and undo_quick.status_code == 200


def test_complete_as_planned_rejects_future_unknown_and_off_sessions():
    with TemporaryDirectory() as directory:
        previous = REPOSITORIES._instance; repository = SQLiteRepositories(Path(directory) / "guards.sqlite3"); REPOSITORIES._instance = repository
        try:
            athlete = repository.create({"athlete": {"display_name": "Rower"}}, "development-user")
            plan_id = repository.save_plan(athlete, _plan()); client = TestClient(app)
            future = client.post(f"/api/v1/plans/{plan_id}/sessions/2999-06-03:UT2:erg/complete-as-planned")
            unknown = client.post(f"/api/v1/plans/{plan_id}/sessions/2026-06-04:OFF:none/complete-as-planned")
        finally:
            REPOSITORIES._instance = previous
    assert future.status_code == 422 and unknown.status_code == 404
