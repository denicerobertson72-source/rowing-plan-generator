from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import services.api.app.main as main
from rowing_plan.periodization import build_season_phases
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories
from services.api.tests.disposable_browser_fixture import synthetic_profile


def test_season_endpoint_exposes_phase_type_for_new_and_legacy_planversions(monkeypatch):
    profile=synthetic_profile()
    span=build_season_phases(profile)[0]
    phase_day=span["start_date"]
    plan={
        "sessions": [], "calendar_days": [],
        "phases":[{"date":phase_day,"phase":span["phase_id"]}],
    }
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(phase_day)
    monkeypatch.setattr(main,"date",FixedDate)
    with TemporaryDirectory() as directory:
        previous=REPOSITORIES._instance
        REPOSITORIES._instance=SQLiteRepositories(Path(directory)/"phase-display.sqlite3")
        try:
            athlete_id=REPOSITORIES.create(profile,"development-user")
            plan_id=REPOSITORIES.save_plan(athlete_id,plan)
            response=TestClient(main.app).get(f"/api/v1/plans/{plan_id}/season")
        finally:
            REPOSITORIES._instance=previous

    assert response.status_code == 200
    current=response.json()["current_phase"]
    assert current["phase"] == current["phase_id"] == span["phase_id"]
    assert current["phase_type"] == span["phase_type"]
    assert response.json()["transitions"][0]["phase_type"] == span["phase_type"]
