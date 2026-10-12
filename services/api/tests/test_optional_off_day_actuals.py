from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from services.api.app.main import CONFIG, _generation_history, app
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories
from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.planner_v2 import generate_plan_v2
from rowing_plan.power_profile import build_power_profile


def _off_day_plan():
    return {
        "sessions": [],
        "calendar_days": [{"date":"2026-09-07","state":"designated_rest"}],
        "generation_input_context": {},
        "generation_input_fingerprint": {},
    }


def test_off_day_optional_actual_is_separate_idempotent_and_visible_in_daily_and_week():
    with TemporaryDirectory() as directory:
        previous=REPOSITORIES._instance; repository=SQLiteRepositories(Path(directory)/"off-day.sqlite3"); REPOSITORIES._instance=repository
        try:
            athlete_id=repository.create(synthetic_profile(),"development-user")
            plan_id=repository.save_plan(athlete_id,_off_day_plan())
            client=TestClient(app)
            before=client.get(f"/api/v1/plans/{plan_id}/week?week_start=2026-09-07")
            walk={"status":"completed","activity_type":"walk","actual_duration_min":30,"rpe":2,"notes":"Easy walk"}
            first=client.post(f"/api/v1/plans/{plan_id}/days/2026-09-07/actuals",json=walk)
            second=client.post(f"/api/v1/plans/{plan_id}/days/2026-09-07/actuals",json={**walk,"notes":"Edited walk"})
            daily=client.get(f"/api/v1/plans/{plan_id}/today?on=2026-09-07")
            after=client.get(f"/api/v1/plans/{plan_id}/week?week_start=2026-09-07")
            erg=client.post(f"/api/v1/plans/{plan_id}/days/2026-09-07/actuals",json={"status":"completed","activity_type":"erg","actual_duration_min":20,"actual_intensity":"UT2","rpe":2})
            reloaded=client.get(f"/api/v1/plans/{plan_id}/week?week_start=2026-09-07")
            log_count=len(repository.logs_for_plan(plan_id))
        finally:
            REPOSITORIES._instance=previous
    assert before.status_code == first.status_code == second.status_code == daily.status_code == after.status_code == erg.status_code == reloaded.status_code == 200
    assert first.json()["log_id"] == second.json()["log_id"]
    for payload in (daily.json(), after.json()["days"][0]):
        day=payload if "sessions" in payload else payload["days"][0]
        assert day["state"] == "designated_rest" and day["sessions"] == []
        assert len(day["actual_activities"]) == 1
        assert day["actual_activities"][0]["notes"] == "Edited walk"
    assert len(reloaded.json()["days"][0]["actual_activities"]) == 2
    assert log_count == 2


def test_optional_off_day_actual_is_carried_once_as_completed_history_and_preserves_rest_plan():
    with TemporaryDirectory() as directory:
        previous=REPOSITORIES._instance; repository=SQLiteRepositories(Path(directory)/"off-history.sqlite3"); REPOSITORIES._instance=repository
        try:
            athlete_id=repository.create(synthetic_profile(),"development-user")
            plan_id=repository.save_plan(athlete_id,_off_day_plan())
            repository.save_log(plan_id,"actual:2026-09-07:erg",{"status":"completed","activity_type":"erg","actual_duration_min":20,"actual_intensity":"UT2","completed_date":"2026-09-07"})
            previous_plan=repository.get_plan(plan_id)
            locked,actuals,source_ids=_generation_history(previous_plan)
            regenerated=dict(_off_day_plan()); regenerated["generation_input_context"]={"actual_plan_ids":source_ids,"locked_sessions":tuple(locked)}
            regenerated_id=repository.save_plan(athlete_id,regenerated)
            locked_again,actuals_again,_=_generation_history(repository.get_plan(regenerated_id))
        finally:
            REPOSITORIES._instance=previous
    assert actuals["actual:2026-09-07:erg"]["activity_type"] == "erg"
    assert len(locked) == len(locked_again) == 1
    assert locked[0]["session_id"] == "ACTUAL_ERG" and locked[0]["date"] == "2026-09-07"
    assert actuals_again["actual:2026-09-07:erg"]["actual_duration_min"] == 20
    assert regenerated["calendar_days"][0] == {"date":"2026-09-07","state":"designated_rest"}


def test_optional_erg_is_included_once_in_real_v2_completed_history():
    profile=synthetic_profile()
    locked=[{
        "date":"2026-09-07", "session_id":"ACTUAL_ERG", "mode":"erg",
        "source_id":"actual:2026-09-07:erg", "total_cardio_minutes":20,
        "total_training_minutes":20, "band":"UT2",
    }]
    plan=generate_plan_v2(profile,CONFIG,build_intensity_profile(profile,CONFIG),build_power_profile(profile,CONFIG),locked)
    actuals=[session for session in plan["sessions"] if session.get("source_id")=="actual:2026-09-07:erg"]
    assert len(actuals) == 1
    assert actuals[0]["session_id"] == "ACTUAL_ERG"


def test_optional_actual_rejects_a_day_that_is_not_planned_off():
    with TemporaryDirectory() as directory:
        previous=REPOSITORIES._instance; repository=SQLiteRepositories(Path(directory)/"non-off.sqlite3"); REPOSITORIES._instance=repository
        try:
            athlete_id=repository.create(synthetic_profile(),"development-user")
            plan_id=repository.save_plan(athlete_id,{"sessions":[],"calendar_days":[{"date":"2026-09-07","state":"no_additional_session"}]})
            response=TestClient(app).post(f"/api/v1/plans/{plan_id}/days/2026-09-07/actuals",json={"status":"completed","activity_type":"walk","actual_duration_min":30})
        finally:
            REPOSITORIES._instance=previous
    assert response.status_code == 422
