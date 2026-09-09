import json
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan
from services.api.tests.disposable_browser_fixture import exported_runtime_profile


def _plan(with_practice=True):
    profile=exported_runtime_profile()
    profile["season"].update(start_date="2026-10-12",end_date="2026-10-18")
    race={"event_name":"October B","start_date":"2026-10-16","end_date":"2026-10-17","race_dates":["2026-10-17"],"priority":"B","race_type":"head_5k"}
    if with_practice: race["practice_sessions"]=[{"date":"2026-10-16","title":"Course practice","duration_minutes":30}]
    profile["races"]=[race]
    profile["recurring_activities"]=[]
    profile["weekly_availability"]=[{**item,"available":True,"fixed_rest":False,"max_training_minutes":90} for item in profile["weekly_availability"]]
    config=json.load(open("config/defaults.json"))
    return generate_plan(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config))


def test_post_race_sunday_is_recovery_not_long_aerobic_and_has_consistent_duration():
    plan=_plan()
    sunday=next(item for item in plan["sessions"] if item["date"]=="2026-10-18")
    assert sunday["session_role"]=="RECOVERY" and sunday["band"]=="UT3"
    fingerprint=sunday["session_fingerprint"]
    modeled=fingerprint["total_work_duration"]+(fingerprint["repetitions"]-1)*fingerprint["recovery_duration"]+fingerprint["modeled_overhead_minutes"]+fingerprint["modeled_cooldown_minutes"]
    assert modeled==sunday["total_cardio_minutes"]


def test_practice_is_scheduled_only_when_persisted():
    assert next(item for item in _plan()["sessions"] if item["date"]=="2026-10-16")["session_id"]=="COURSE_PRACTICE"
    assert not any(item["session_id"]=="COURSE_PRACTICE" for item in _plan(False)["sessions"])


def test_strength_duration_is_distinct_from_rowing_minutes():
    profile=exported_runtime_profile(); profile["season"].update(start_date="2026-09-07",end_date="2026-09-13")
    activity=profile["recurring_activities"][0]; activity["duration_minutes"]=55
    config=json.load(open("config/defaults.json")); plan=generate_plan(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config))
    lift=next(item for item in plan["sessions"] if item["session_id"]=="LIFT")
    assert lift["strength_minutes"]==55 and lift["total_training_minutes"]==55 and lift["rowing_minutes"]==0 and lift["total_cardio_minutes"]==0
