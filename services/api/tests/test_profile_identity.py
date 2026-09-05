import copy
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from services.api.app.main import app
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories
from services.api.tests.disposable_browser_fixture import exported_runtime_profile


def client_for_database(path: Path):
    previous=REPOSITORIES._instance
    REPOSITORIES._instance=SQLiteRepositories(path)
    return TestClient(app), previous


def test_account_listing_is_owner_scoped_and_reports_safe_duplicate_metadata():
    with TemporaryDirectory() as directory:
        client, previous=client_for_database(Path(directory)/"identity.sqlite3")
        try:
            profile=exported_runtime_profile()
            first=client.post("/api/v1/athletes",json={"athlete_profile":profile,"user_id":"not-trusted"})
            second_profile=copy.deepcopy(profile); second_profile["athlete"]["display_name"]="Second profile"
            second=client.post("/api/v1/athletes",json={"athlete_profile":second_profile})
            listing=client.get("/api/v1/account/athletes")
        finally:
            REPOSITORIES._instance=previous
    assert first.status_code == second.status_code == listing.status_code == 200
    candidates=listing.json()["athletes"]
    assert {item["athlete_id"] for item in candidates} == {first.json()["athlete_id"],second.json()["athlete_id"]}
    assert all("user_id" not in item for item in candidates)
    assert candidates[0]["race_count"] == 2
    assert candidates[0]["recurring_activity_count"] == 2


def test_revision_conflict_preserves_newer_profile_and_reload_can_save():
    with TemporaryDirectory() as directory:
        client, previous=client_for_database(Path(directory)/"revision.sqlite3")
        try:
            original=exported_runtime_profile()
            created=client.post("/api/v1/athletes",json={"athlete_profile":original}).json()
            athlete_id=created["athlete_id"]
            client_a=client.get(f"/api/v1/athletes/{athlete_id}").json()
            client_b=client.get(f"/api/v1/athletes/{athlete_id}").json()
            a_profile=copy.deepcopy(client_a["athlete_profile"]); a_profile["athlete"]["display_name"]="Saved by A"
            saved_a=client.put(f"/api/v1/athletes/{athlete_id}",json={"athlete_profile":a_profile,"expected_revision":client_a["profile_revision"]})
            b_profile=copy.deepcopy(client_b["athlete_profile"]); b_profile["athlete"]["display_name"]="Stale B"
            stale_b=client.put(f"/api/v1/athletes/{athlete_id}",json={"athlete_profile":b_profile,"expected_revision":client_b["profile_revision"]})
            latest=client.get(f"/api/v1/athletes/{athlete_id}").json()
            latest_profile=copy.deepcopy(latest["athlete_profile"]); latest_profile["athlete"]["display_name"]="Reloaded B"
            saved_b=client.put(f"/api/v1/athletes/{athlete_id}",json={"athlete_profile":latest_profile,"expected_revision":latest["profile_revision"]})
        finally:
            REPOSITORIES._instance=previous
    assert saved_a.status_code == 200 and saved_a.json()["profile_revision"] == 1
    assert stale_b.status_code == 409
    assert latest["athlete_profile"]["athlete"]["display_name"] == "Saved by A"
    assert saved_b.status_code == 200 and saved_b.json()["profile_revision"] == 2
