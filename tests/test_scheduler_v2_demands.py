from datetime import date
from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.scheduler_v2 import generate_frequency_targets, generate_training_demands

def test_v2_demands_are_pure_deterministic_and_leave_flexible_dates_unplaced():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}
    profile["recurring_activities"][1]["preferred_days"]=["thursday"]
    first=generate_training_demands(profile); second=generate_training_demands(profile)
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]
    private=next(item for item in first if item.type=="private_coaching")
    coached=next(item for item in first if item.type=="coached_training" and item.earliest_date==date(2026,9,7))
    assert private.priority=="hard" and private.earliest_date==private.latest_date and private.earliest_date.weekday()==2
    assert coached.priority=="strong" and coached.desired_dates== (date(2026,9,8),date(2026,9,10)) and coached.preferred_dates==(date(2026,9,10),)
    targets=generate_frequency_targets(profile)
    strength=targets[0]
    assert not any(item.type=="strength" for item in first)
    assert strength.group_id=="strength" and strength.window_days==14 and strength.target_count==4 and strength.minimum_count==3 and strength.minimum_spacing_days==1
    assert all(not item.desired_dates for item in first if item.type=="rest")
    assert any(item.type in {"aerobic_base","long_aerobic"} and item.quality_class=="aerobic" for item in first)
