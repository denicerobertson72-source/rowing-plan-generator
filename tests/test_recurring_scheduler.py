import json

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan


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
