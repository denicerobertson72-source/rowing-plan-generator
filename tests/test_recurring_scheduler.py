import json
import tempfile
from pathlib import Path

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan
from services.api.app.repositories import SQLiteRepositories


def test_recurring_commitments_change_the_generated_week():
    config=json.load(open("config/defaults.json"))
    profile={
        "athlete":{"display_name":"Test"},
        "season":{"start_date":"2026-09-07","end_date":"2026-09-13","current_weekly_endurance_minutes":180,"target_peak_weekly_endurance_minutes":270},
        "tests":{"resting_hr":58,"max_hr":177,"erg_2k_seconds":None,"multi_duration_power_tests":{}},
        "weekly_availability":[{"weekday":day,"available":True,"max_training_minutes":90,"rowing_modes":["erg"]} for day in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")],
        "races":[],
        "recurring_activities":[
            {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
            {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","thursday"],"prohibited_days":[]},
            {"activity_id":"lift","activity_type":"strength","sessions_per_week":2,"scheduling_status":"preferred","fixed_days":[],"preferred_days":["monday","friday"],"allowed_days":["tuesday","thursday"],"prohibited_days":[],"same_day_rules":{"rowing_allowed":False,"alternate_ut2_allowed":True}},
            {"activity_id":"rest","activity_type":"rest","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["saturday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        ],
    }
    plan=generate_plan(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config))
    rows=[(session["day"],session["session_id"],session["title"]) for session in plan["sessions"]]
    assert ("Wednesday","COACHED","Private coaching") in rows
    assert sum(session_id=="COACHED" and title=="Coached row" for _,session_id,title in rows)==1
    assert sum(session_id=="LIFT" for _,session_id,_ in rows)==2
    assert not any(day=="Saturday" for day,_,_ in rows)
    assert sum(session_id=="XL-UT2-01" for _,session_id,_ in rows)==2


def test_movable_commitments_keep_their_requested_frequency_without_overlap():
    config=json.load(open("config/defaults.json"))
    profile={
        "athlete":{"display_name":"Test"},
        "season":{"start_date":"2026-09-07","end_date":"2026-09-13","current_weekly_endurance_minutes":180,"target_peak_weekly_endurance_minutes":270},
        "tests":{"resting_hr":58,"max_hr":177,"erg_2k_seconds":None,"multi_duration_power_tests":{}},
        "weekly_availability":[{"weekday":day,"available":True,"max_training_minutes":90,"rowing_modes":["erg"]} for day in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")],
        "races":[],
        "recurring_activities":[
            {"activity_id":"rest","activity_type":"rest","sessions_per_week":2,"scheduling_status":"fixed","fixed_days":["monday","tuesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
            {"activity_id":"lift","activity_type":"strength","sessions_per_week":2,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["wednesday","thursday","friday"],"prohibited_days":[],"same_day_rules":{"rowing_allowed":False}},
            {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["friday","saturday"],"prohibited_days":[]},
        ],
    }
    plan=generate_plan(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config))
    assert sum(item["session_id"]=="LIFT" for item in plan["sessions"])==2
    assert sum(item["session_id"]=="COACHED" for item in plan["sessions"])==1
    assert not any(item["date"].endswith("-07") or item["date"].endswith("-08") for item in plan["sessions"])


def test_six_normal_weeks_keep_two_flexible_lifts_and_one_designated_rest_day():
    config=json.load(open("config/defaults.json"))
    profile={
        "athlete":{"display_name":"Regression athlete"},
        "season":{"start_date":"2026-09-07","end_date":"2026-10-18","current_weekly_endurance_minutes":180,"target_peak_weekly_endurance_minutes":270},
        "tests":{"resting_hr":58,"max_hr":177,"erg_2k_seconds":None,"multi_duration_power_tests":{}},
        "weekly_availability":[{"weekday":day,"available":True,"max_training_minutes":90,"rowing_modes":["erg"]} for day in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")],
        "races":[],
        "recurring_activities":[
            {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
            {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","thursday","sunday"],"prohibited_days":[]},
            {"activity_id":"lift","activity_type":"strength","sessions_per_week":2,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["monday","friday","saturday"],"prohibited_days":[],"same_day_rules":{"rowing_allowed":False}},
            {"activity_id":"rest","activity_type":"rest","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["saturday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        ],
    }
    plan=generate_plan(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config))
    assert len(plan["calendar_days"])==42
    for offset in range(0,42,7):
        week=plan["calendar_days"][offset:offset+7]
        commitments=[entry for day in week for entry in day["commitments"]]
        assert sum(item["activity_type"]=="strength" for item in commitments)==2
        assert sum(day["state"]=="designated_rest" for day in week)==1
        assert sum(item["activity_type"]=="private_coaching" for item in commitments)==1


def test_persisted_profile_and_latest_plan_keep_the_schedule_source_of_truth():
    config=json.load(open("config/defaults.json"))
    profile={
        "athlete":{"display_name":"Persisted athlete"},
        "season":{"start_date":"2026-09-07","end_date":"2026-10-18","current_weekly_endurance_minutes":180,"target_peak_weekly_endurance_minutes":270},
        "tests":{"resting_hr":58,"max_hr":177,"erg_2k_seconds":None,"multi_duration_power_tests":{}},
        "weekly_availability":[{"weekday":day,"available":True,"max_training_minutes":90,"rowing_modes":["erg"]} for day in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")],
        "races":[],
        "recurring_activities":[
            {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
            {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","thursday"],"prohibited_days":[]},
            {"activity_id":"lift","activity_type":"strength","sessions_per_week":2,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["monday","friday","sunday"],"prohibited_days":[],"same_day_rules":{"rowing_allowed":False}},
            {"activity_id":"rest","activity_type":"rest","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["saturday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        ],
    }
    with tempfile.TemporaryDirectory() as directory:
        repo=SQLiteRepositories(Path(directory)/"test.sqlite3"); athlete_id=repo.create(profile,"test-user")
        reloaded=repo.get(athlete_id); assert reloaded["recurring_activities"][2]["sessions_per_week"]==2
        first=generate_plan(reloaded,config,build_intensity_profile(reloaded,config),build_power_profile(reloaded,config)); first_id=repo.save_plan(athlete_id,first)
        assert sum(day["state"]=="designated_rest" for day in repo.get_plan(first_id)["plan"]["calendar_days"][:7])==1
        reloaded["recurring_activities"][2]["sessions_per_week"]=3; repo.save(athlete_id,reloaded)
        future=generate_plan(repo.get(athlete_id),config,build_intensity_profile(reloaded,config),build_power_profile(reloaded,config)); future_id=repo.save_plan(athlete_id,future)
        assert repo.get_plan(future_id)["version_number"]==2
        assert sum(item["activity_type"]=="strength" for day in repo.get_plan(future_id)["plan"]["calendar_days"][:7] for item in day["commitments"])==3
