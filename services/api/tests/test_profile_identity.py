import copy
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from services.api.app.main import app
from services.api.app.repositories import REPOSITORIES, SQLiteRepositories
from services.api.tests.disposable_browser_fixture import exported_runtime_profile, synthetic_profile


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


def test_real_profile_shaped_regeneration_normalizes_legacy_flexible_rest_without_mutating_profile():
    """Exercise the PWA regeneration route, not the direct planner helper."""
    with TemporaryDirectory() as directory:
        client, previous=client_for_database(Path(directory)/"regeneration.sqlite3")
        try:
            profile=exported_runtime_profile()
            # This is the deployed-profile shape: the flexible rest card still
            # carries old Saturday markers, while coaching uses modern cards.
            profile["recurring_activities"].extend([
                {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":[],"preferred_days":[],"allowed_days":["wednesday"],"prohibited_days":[],"planner_may_choose_day":False,"same_day_rules":{"rowing_allowed":False},"race_week_mobility":"locked","notes":"Private Wednesday coaching","additive_fixture_field":{"preserve":True}},
                {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","thursday"],"prohibited_days":[],"planner_may_choose_day":True},
            ])
            next(item for item in profile["weekly_availability"] if item["weekday"] == "wednesday")["fixed_coached_row"] = True
            created=client.post("/api/v1/athletes",json={"athlete_profile":profile})
            athlete_id=created.json()["athlete_id"]
            initial=client.post(f"/api/v1/athletes/{athlete_id}/plans/generate",json={})
            before_save=REPOSITORIES.get(athlete_id)
            current=client.get(f"/api/v1/athletes/{athlete_id}").json()
            saved=client.put(f"/api/v1/athletes/{athlete_id}",json={"athlete_profile":current["athlete_profile"],"expected_revision":current["profile_revision"]})
            generated=client.post(f"/api/v1/athletes/{athlete_id}/plans/generate",json={})
            plan_id=generated.json()["plan_id"]
            persisted=REPOSITORIES.get_plan(plan_id)
            original=REPOSITORIES.get_plan(initial.json()["plan_id"])
            week=client.get(f"/api/v1/plans/{plan_id}/week?week_start=2026-09-07")
            first_week=client.get(f"/api/v1/plans/{plan_id}/week?week_start=2026-08-31")
            season=client.get(f"/api/v1/plans/{plan_id}/season")
            stored=REPOSITORIES.get(athlete_id)
        finally:
            REPOSITORIES._instance=previous
    assert created.status_code == initial.status_code == saved.status_code == generated.status_code == week.status_code == first_week.status_code == season.status_code == 200
    assert initial.json()["plan_id"] != plan_id
    assert original and original["version_number"] == 1
    assert persisted and persisted["athlete_id"] == athlete_id and persisted["version_number"] == 2
    intent=next(item for item in persisted["plan"]["weekly_training_intents"] if item["week_start"] == "2026-09-07")
    assert {"target_total_rowing_exposures","target_coached_rowing_exposures","target_independent_rowing_exposures"} <= intent.keys()
    assert [item["title"] for day in first_week.json()["days"] if day["date"] == "2026-09-02" for item in day["sessions"]] == ["Private coaching"]
    assert [item["title"] for day in week.json()["days"] if day["date"] == "2026-09-09" for item in day["sessions"]] == ["Private coaching"]
    commitments=[item for day in persisted["plan"]["calendar_days"] if "2026-09-07" <= day["date"] <= "2026-09-13" for item in day["commitments"]]
    assert {kind:sum(item["activity_type"] == kind for item in commitments) for kind in ("strength","private_coaching","coached_row","rest")} == {"strength":2,"private_coaching":1,"coached_row":1,"rest":1}
    private_before=next(item for item in before_save["recurring_activities"] if item["activity_id"] == "private")
    private_after=next(item for item in stored["recurring_activities"] if item["activity_id"] == "private")
    assert private_before["fixed_days"] == [] and private_before["allowed_days"] == ["wednesday"]
    assert private_after["fixed_days"] == ["wednesday"]
    assert private_after["same_day_rules"] == {"rowing_allowed":False}
    assert private_after["race_week_mobility"] == "locked" and private_after["notes"] == "Private Wednesday coaching"
    assert private_after["additive_fixture_field"] == {"preserve":True}
    assert stored["recurring_activities"][1]["fixed_days"] == []
    assert next(item for item in stored["weekly_availability"] if item["weekday"] == "saturday")["fixed_rest"] is False


def test_legacy_profile_explicit_save_canonicalizes_capability_without_changing_read_time_data():
    """A sanitized production-shaped legacy Profile upgrades only on explicit save."""
    with TemporaryDirectory() as directory:
        client, previous=client_for_database(Path(directory)/"legacy-upgrade.sqlite3")
        try:
            profile=exported_runtime_profile()
            profile["athlete"]["current_rowing_sessions_per_week"]=3
            profile["athlete"].pop("current_approx_weekly_rowing_minutes")
            profile["races"]=[]
            profile["season"].update({"start_date":"2026-09-07","end_date":"2026-10-18"})
            profile["preferences"]["future_compatible_fixture"]={"preserve":True}
            profile["recurring_activities"].extend([
                {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":[],"preferred_days":[],"allowed_days":["wednesday"],"prohibited_days":[],"planner_may_choose_day":False,"same_day_rules":{"rowing_allowed":False}},
                {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","thursday"],"prohibited_days":[],"planner_may_choose_day":True},
            ])
            created=client.post("/api/v1/athletes",json={"athlete_profile":profile})
            athlete_id=created.json()["athlete_id"]
            # Generating uses a non-mutating planning copy: the persisted raw
            # legacy flag and unknown fields are untouched before the user saves.
            legacy_plan=client.post(f"/api/v1/athletes/{athlete_id}/plans/generate",json={})
            before=REPOSITORIES.get(athlete_id)
            current=client.get(f"/api/v1/athletes/{athlete_id}").json()
            upgraded=copy.deepcopy(current["athlete_profile"])
            upgraded["athlete"].update({"current_rowing_sessions_per_week":4,"current_approx_weekly_rowing_minutes":220})
            upgraded.setdefault("preferences",{})["preferred_long_session_days"]=["sunday"]
            sunday=next(item for item in upgraded["weekly_availability"] if item["weekday"] == "sunday")
            sunday["max_training_minutes"]=150
            saved=client.put(f"/api/v1/athletes/{athlete_id}",json={"athlete_profile":upgraded,"expected_revision":current["profile_revision"]})
            generated=client.post(f"/api/v1/athletes/{athlete_id}/plans/generate",json={})
            stored=REPOSITORIES.get(athlete_id)
            plan=REPOSITORIES.get_plan(generated.json()["plan_id"])["plan"]
        finally:
            REPOSITORIES._instance=previous

    assert created.status_code == legacy_plan.status_code == saved.status_code == generated.status_code == 200
    legacy_strength=next(item for item in before["recurring_activities"] if item["activity_id"] == "lift")
    assert legacy_strength.get("alternate_cardio") is None
    assert legacy_strength["same_day_rules"]["alternate_ut2_allowed"] is True
    stored_strength=next(item for item in stored["recurring_activities"] if item["activity_id"] == "lift")
    assert stored_strength["alternate_cardio"]["mode"] == "optional"
    assert stored_strength["alternate_cardio"]["compatibility_source"] == "legacy_alternate_ut2_allowed"
    assert stored_strength["allowed_days"] == ["monday","friday","tuesday","thursday"]  # ambiguous legacy restriction is retained.
    assert stored["athlete"]["current_rowing_sessions_per_week"] == 4
    assert stored["athlete"]["current_approx_weekly_rowing_minutes"] == 220
    assert stored["preferences"]["preferred_long_session_days"] == ["sunday"]
    assert stored["preferences"]["future_compatible_fixture"] == {"preserve":True}
    assert all(item["structure"].find("–") == -1 for item in plan["sessions"])
    complete=[intent for intent in plan["weekly_training_intents"] if intent["week_start"] <= "2026-10-12"]
    assert len(complete) == 6
    assert all(intent["target_independent_rowing_exposures"] >= 2 for intent in complete)
    sunday_sessions=[item for item in plan["sessions"] if item["date"].endswith(("09-13","09-20","09-27","10-04","10-11","10-18"))]
    assert any(item.get("session_role") == "LONG_AEROBIC" and item["total_cardio_minutes"] > 60 for item in sunday_sessions)


def test_generation_conflict_preserves_safe_scheduler_diagnostic_and_creates_no_plan(caplog):
    profile=synthetic_profile()
    profile["recurring_activities"] = [
        {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        {"activity_id":"rest","activity_type":"rest","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["sunday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        {"activity_id":"strength","activity_type":"strength","sessions_per_week":2,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["monday","friday"],"prohibited_days":[]},
        {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["monday","friday"],"prohibited_days":[]},
    ]
    with TemporaryDirectory() as directory:
        client, previous=client_for_database(Path(directory)/"conflict.sqlite3")
        try:
            athlete_id=client.post("/api/v1/athletes",json={"athlete_profile":profile}).json()["athlete_id"]
            caplog.set_level(logging.WARNING, logger="services.api.app.main")
            response=client.post(f"/api/v1/athletes/{athlete_id}/plans/generate",json={})
            latest=REPOSITORIES.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance=previous
    detail=response.json()["detail"]
    diagnostic=detail["diagnostic"]
    assert response.status_code == 422 and detail["error_code"] == "planning_conflict"
    assert diagnostic["conflict_type"] == "recurring_activity_placement"
    assert diagnostic["activity_type"] == "coached_row"
    assert diagnostic["requested_frequency"] == 1
    assert diagnostic["week_start"] == "2026-08-31"
    assert latest is None
    assert "code=planning_conflict" in caplog.text
    assert "conflict_type=recurring_activity_placement" in caplog.text
    assert "activity_type=coached_row" in caplog.text
    assert "fixed_days=[]" in caplog.text


def test_ambiguous_fixed_private_coaching_day_remains_a_planning_conflict():
    profile=synthetic_profile()
    profile["recurring_activities"] = [
        {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","wednesday"],"prohibited_days":[]},
        {"activity_id":"rest","activity_type":"rest","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["sunday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
    ]
    with TemporaryDirectory() as directory:
        client, previous=client_for_database(Path(directory)/"ambiguous-private.sqlite3")
        try:
            athlete_id=client.post("/api/v1/athletes",json={"athlete_profile":profile}).json()["athlete_id"]
            response=client.post(f"/api/v1/athletes/{athlete_id}/plans/generate",json={})
            latest=REPOSITORIES.latest_plan_for_athlete(athlete_id)
        finally:
            REPOSITORIES._instance=previous
    detail=response.json()["detail"]
    assert response.status_code == 422 and detail["error_code"] == "planning_conflict"
    assert detail["diagnostic"]["activity_type"] == "private_coaching"
    assert detail["diagnostic"]["candidate_days"] == ["tuesday","wednesday"]
    assert detail["diagnostic"]["fixed_days"] == [] and latest is None
