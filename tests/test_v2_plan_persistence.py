import json
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from services.api.app.repositories import SQLiteRepositories
from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.models import DateContext, V2SessionMaterializationRequest
from rowing_plan.plan_assembly_v2 import build_plan_version_from_v2
from rowing_plan.power_profile import build_power_profile
from rowing_plan.workbook import build_workbook

CONFIG=json.load(open("config/defaults.json"))

def v2_plan():
    profile=synthetic_profile(); bands=build_intensity_profile(profile,CONFIG); power=build_power_profile(profile,CONFIG)
    start=date(2026,9,7); calendar=tuple(DateContext(start+timedelta(days=i),(start+timedelta(days=i)).strftime("%A"),"race_specific_preparation",True,90,0,90) for i in range(7))
    requests=(V2SessionMaterializationRequest("dedicated_ut2",start,60,"race_specific_preparation",placement_id="ut2"),V2SessionMaterializationRequest("strength",start+timedelta(days=1),45,"race_specific_preparation",placement_id="lift"),V2SessionMaterializationRequest("coached_training",start+timedelta(days=2),50,"race_specific_preparation",placement_id="coach"),V2SessionMaterializationRequest("race",start+timedelta(days=4),20,"race",placement_id="race",fact={"event_name":"Race","race_type":"head_5k","priority":"A"}))
    plan=build_plan_version_from_v2(profile=profile,bands=bands,power=power,calendar=calendar,requests=requests,rest_dates=(start+timedelta(days=6),),v2_diagnostics={"scheduler_version":"v2","demand_satisfaction":[{"status":"provisional_satisfied"}],"rolling_targets":[],"repair_changes":[],"target_consequences":[],"overrides":[]}).plan
    return profile,plan

def test_v2_plan_round_trips_through_existing_sqlite_versions_and_workbook():
    profile,plan=v2_plan()
    with TemporaryDirectory() as directory:
        repo=SQLiteRepositories(Path(directory)/"v2.sqlite3"); athlete=repo.create(profile,"development-user")
        first=repo.save_plan(athlete,plan); second=repo.save_plan(athlete,plan)
        loaded=repo.get_plan(first); latest=repo.latest_plan_for_athlete(athlete)
        assert loaded["plan"]==plan and latest["plan_id"]==second and latest["version_number"]==2
        assert json.loads(json.dumps(loaded["plan"]))==plan
        export_profile={**profile,"races":[]}
        assert build_workbook(export_profile,loaded["plan"])
        assert {(item["date"],item["session_id"],item["mode"]) for item in plan["sessions"]}=={(item["date"],item["session_id"],item["mode"]) for item in loaded["plan"]["sessions"]}
