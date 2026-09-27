from datetime import date
from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.scheduler_v2 import build_v2_season_calendar, candidate_dates_for_demand, generate_frequency_targets, generate_rowing_dose_targets, generate_training_demands, generate_v2_demand_plan

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
    doses=generate_rowing_dose_targets(profile)
    assert any(item.category=="dedicated_ut2" and item.minimum_minutes>0 for item in doses)
    assert any(item.category=="long_aerobic" for item in doses)
    assert not any(item.source=="season_phase" for item in first)
    plan=generate_v2_demand_plan(profile)
    assert plan.weekly_commitment_demands==tuple(first) and plan.frequency_targets==tuple(targets) and plan.rowing_dose_targets==tuple(doses)

def test_v21_calendar_reserves_private_and_candidates_are_hard_eligible_only():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-13"}; profile["recurring_activities"][1]["preferred_days"]=["thursday"]
    demands=generate_training_demands(profile); calendar=build_v2_season_calendar(profile)
    private=next(item for item in demands if item.type=="private_coaching"); coached=next(item for item in demands if item.type=="coached_training")
    assert next(item for item in calendar if item.date==date(2026,9,9)).hard_committed_minutes==50
    assert candidate_dates_for_demand(private,calendar).candidates==(date(2026,9,9),)
    assert candidate_dates_for_demand(coached,calendar).candidates==(date(2026,9,8),date(2026,9,10))
    profile["weekly_availability"][3]["available"]=False
    result=candidate_dates_for_demand(coached,build_v2_season_calendar(profile))
    assert result.candidates==(date(2026,9,8),) and any(day=="2026-09-10" and "unavailable" in reasons for day,reasons in result.rejections)
