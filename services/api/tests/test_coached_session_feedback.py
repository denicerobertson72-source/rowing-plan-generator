from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from services.api.app.main import app
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories


def test_coached_actual_is_idempotent_and_only_acceptance_versions_future_week():
    with TemporaryDirectory() as directory:
        previous=REPOSITORIES._instance; REPOSITORIES._instance=SQLiteRepositories(Path(directory)/"feedback.sqlite3")
        try:
            athlete=REPOSITORIES.create({"athlete":{"display_name":"Rower"}},"development-user")
            sessions=[
                {"date":"2026-06-01","session_id":"LIFT","mode":"strength","band":"","title":"Strength","total_cardio_minutes":0},
                {"date":"2026-06-02","session_id":"AT","mode":"erg","band":"AT","title":"Threshold","structure":"4 × 8 min AT","total_cardio_minutes":50},
                {"date":"2026-06-03","session_id":"COACHED","mode":"on_water","band":"UT3","title":"Private coaching","total_cardio_minutes":55,"coached":True},
                {"date":"2026-06-07","session_id":"AT","mode":"erg","band":"AT","title":"Threshold","structure":"4 × 8 min AT","total_cardio_minutes":50},
            ]
            plan_id=REPOSITORIES.save_plan(athlete,{"sessions":sessions,"calendar_days":[]})
            client=TestClient(app); key="2026-06-03:COACHED:on_water"
            tuesday=client.post(f"/api/v1/plans/{plan_id}/sessions/2026-06-02:AT:erg/log",json={"status":"completed","completion":"yes","actual_duration_min":50,"actual_intensity":"AT","rpe":7})
            payload={"status":"completed","completion":"yes","actual_duration_min":60,"actual_intensity":"mixed_unsure","rpe":8,"coach_cues":"Place then push.","carry_cue_forward":True,"actual_segments":[{"segment_type":"technical_drill","duration_seconds":900},{"segment_type":"aerobic_rowing","duration_seconds":1620,"intensity_band":"UT2"},{"segment_type":"work_piece","repetitions":2,"duration_seconds":510,"intensity_band":"TR","rate_min":26,"rate_max":28,"effort_label":"Full pressure"}]}
            logged=client.post(f"/api/v1/plans/{plan_id}/sessions/{key}/log",json=payload)
            edited=client.post(f"/api/v1/plans/{plan_id}/sessions/{key}/log",json={**payload,"rpe":9})
            original=client.get(f"/api/v1/plans/{plan_id}")
            applied=client.post(f"/api/v1/plans/{plan_id}/coached-session-adjustment/apply?session_key={key}")
            updated=client.get(f"/api/v1/plans/{applied.json()['plan_id']}")
            updated_week=client.get(f"/api/v1/plans/{applied.json()['plan_id']}/week?week_start=2026-06-01")
            logs=client.get(f"/api/v1/plans/{plan_id}/logs")
        finally:
            REPOSITORIES._instance=previous
    assert tuesday.status_code == logged.status_code == edited.status_code == applied.status_code == 200
    assert logged.json()["adjustment"]["recommendation"] == "review"
    assert logged.json()["adjustment"]["composition"]["quality_seconds"] == 1020
    assert "already completed 2 quality" in logged.json()["adjustment"]["explanation"]
    coached_logs=[entry for entry in logs.json()["logs"] if entry["session_key"] == key]
    assert len(coached_logs) == 1 and coached_logs[0]["payload"]["rpe"] == 9
    assert original.json()["plan"]["sessions"] == sessions
    assert updated.json()["version_number"] == 2
    assert updated.json()["plan"]["sessions"][0:3] == sessions[0:3]
    assert updated.json()["plan"]["sessions"][3]["band"] == "UT2"
    assert updated_week.json()["days"][2]["sessions"][0]["actual"]["rpe"] == 9
    assert updated_week.json()["days"][6]["sessions"][0]["technical_cues"] == ["Place then push."]


def test_easy_and_mixed_actuals_are_conservative_without_automatic_change():
    with TemporaryDirectory() as directory:
        previous=REPOSITORIES._instance; REPOSITORIES._instance=SQLiteRepositories(Path(directory)/"easy.sqlite3")
        try:
            athlete=REPOSITORIES.create({"athlete":{"display_name":"Rower"}},"development-user")
            plan_id=REPOSITORIES.save_plan(athlete,{"sessions":[{"date":"2026-06-03","session_id":"COACHED","mode":"on_water","band":"UT3","title":"Coached row","total_cardio_minutes":50,"coached":True},{"date":"2026-06-07","session_id":"AT","mode":"erg","band":"AT","title":"Threshold","total_cardio_minutes":50}],"calendar_days":[]})
            client=TestClient(app); key="2026-06-03:COACHED:on_water"
            easy=client.post(f"/api/v1/plans/{plan_id}/sessions/{key}/log",json={"status":"completed","completion":"yes","actual_duration_min":50,"actual_intensity":"UT3","rpe":3})
            mixed=client.post(f"/api/v1/plans/{plan_id}/sessions/{key}/log",json={"status":"completed","completion":"yes","actual_duration_min":60,"actual_intensity":"mixed_unsure","rpe":8})
        finally:
            REPOSITORIES._instance=previous
    assert easy.json()["adjustment"]["recommendation"] == "none"
    assert mixed.json()["adjustment"]["recommendation"] == "review"
