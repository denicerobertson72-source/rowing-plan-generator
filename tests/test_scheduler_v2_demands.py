from datetime import date
from dataclasses import replace
from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.models import TargetCredit, WindowPlacement
from rowing_plan.scheduler_v2 import assign_window_placement, build_v2_season_calendar, candidate_dates_for_demand, freeze_leading_half, generate_frequency_targets, generate_rowing_dose_targets, generate_training_demands, generate_v2_demand_plan, initialize_active_window_state, placements_compatible, place_v2_non_rowing, place_v2_rowing, reconcile_demand_satisfaction, release_window_placement, solve_v2_rolling_non_rowing

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

def test_v22_strength_reconciles_every_overlapping_14_day_window_without_adjacency():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-10-04"}; profile["recurring_activities"][1]["preferred_days"]=["thursday"]
    first=place_v2_non_rowing(profile); second=place_v2_non_rowing(profile)
    strengths=[day for key,day in first.placements if key.startswith("strength:")]
    windows=[audit for audit in first.audits if "window_start" in audit and audit["window_end"]>="2026-09-20"]
    assert first==second and all((right-left).days>=2 for left,right in zip(strengths,strengths[1:]))
    assert all(audit["achieved"]>=audit["minimum"] for audit in windows)
    assert all(audit["target"]==4 for audit in windows[:-1])

def test_v23_places_generic_rowing_roles_without_using_rest_or_adjacent_quality_days():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; profile["recurring_activities"][1]["preferred_days"]=["thursday"]
    plan=generate_v2_demand_plan(profile); calendar=build_v2_season_calendar(profile); placed=place_v2_rowing(profile,plan,calendar)
    rows=[(key,day) for key,day in placed.placements if key.startswith("rowing:")]; rest={day for key,day in placed.placements if key.startswith("rest:")}
    quality=sorted(day for key,day in rows if ":quality:" in key)
    assert rows and not {day for _,day in rows}&rest and all((right-left).days>=2 for left,right in zip(quality,quality[1:]))

def test_active_window_assign_release_freeze_and_multi_credit_are_immutable():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}
    state=initialize_active_window_state(build_v2_season_calendar(profile),date(2026,9,7),date(2026,9,20)); day=date(2026,9,8)
    placement=WindowPlacement("long",day,"long_aerobic","dose",75,False,(TargetCredit("long","long_aerobic",1,75),TargetCredit("ut2","dedicated_ut2",1,75)))
    assigned=assign_window_placement(state,placement)
    assert state.remaining_minutes_by_date[day]==90 and assigned.remaining_minutes_by_date[day]==15 and assigned.rowing_dose_credits["ut2"]==(1,75)
    released=release_window_placement(assigned,"long")
    assert released.remaining_minutes_by_date[day]==90 and "ut2" not in released.rowing_dose_credits
    frozen=freeze_leading_half(assigned,date(2026,9,9))
    try: release_window_placement(frozen,"long")
    except ValueError as error: assert str(error)=="frozen_placement"
    else: assert False

def test_rest_compatibility_is_symmetric_and_blocks_all_flexible_training():
    day=date(2026,9,8); rest=WindowPlacement("rest",day,"rest","rest",0); roles=("strength","coached_training","dedicated_ut2","long_aerobic","quality")
    for role in roles:
        training=WindowPlacement(role,day,role,"test",30)
        assert placements_compatible(rest,training)==placements_compatible(training,rest)==(False,"rest_day")
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-13"}
    state=initialize_active_window_state(build_v2_season_calendar(profile),date(2026,9,7),date(2026,9,13))
    for first,second in ((WindowPlacement("s",day,"strength","s",30),rest),(rest,WindowPlacement("s",day,"strength","s",30))):
        try: assign_window_placement(assign_window_placement(state,first),second)
        except ValueError as error: assert str(error)=="rest_day"
        else: assert False

def test_weekly_demand_provenance_is_canonical_and_updates_from_placements():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; demands=tuple(generate_training_demands(profile)); coached=next(item for item in demands if item.type=="coached_training")
    state=initialize_active_window_state(build_v2_season_calendar(profile),date(2026,9,7),date(2026,9,20)); open_state=reconcile_demand_satisfaction(state,demands,date(2026,9,7))
    assert coached.canonical_week_start==date(2026,9,7) and open_state.demand_satisfaction[coached.demand_id].status=="open"
    placed=assign_window_placement(state,WindowPlacement("coach",date(2026,9,8),"coached_training",coached.demand_id,30))
    assert reconcile_demand_satisfaction(placed,demands).demand_satisfaction[coached.demand_id].status=="provisional_satisfied"
    assert reconcile_demand_satisfaction(release_window_placement(placed,"coach"),demands,date(2026,9,7)).demand_satisfaction[coached.demand_id].status=="open"

def test_window_reconstruction_imports_overlap_once_and_keeps_frozen_history():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}; calendar=build_v2_season_calendar(profile); demands=tuple(generate_training_demands(profile)); coached=next(item for item in demands if item.type=="coached_training" and item.canonical_week_start==date(2026,9,14))
    frozen=WindowPlacement("sun-strength",date(2026,9,13),"strength","strength",60,True)
    overlap=WindowPlacement("coach",date(2026,9,17),"coached_training",coached.demand_id,30)
    state=initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),frozen_history=(frozen,),provisional_overlap=(overlap,),canonical_demands=demands)
    assert state.remaining_minutes_by_date[date(2026,9,17)]==60 and state.demand_satisfaction[coached.demand_id].status=="provisional_satisfied"
    try: assign_window_placement(state,WindowPlacement("mon-strength",date(2026,9,14),"strength","strength",30))
    except ValueError as error: assert str(error)=="strength_spacing"
    else: assert False
    assert release_window_placement(state,"coach").remaining_minutes_by_date[date(2026,9,17)]==90

def test_shared_solver_overlap_reversal_preserves_canonical_provenance_and_capacity():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}
    calendar=build_v2_season_calendar(profile); demands=tuple(generate_training_demands(profile)); coached=next(item for item in demands if item.type=="coached_training" and item.canonical_week_start==date(2026,9,14))
    first=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),canonical_demands=demands)
    thu=assign_window_placement(first,WindowPlacement("coach-thu",date(2026,9,17),"coached_training",coached.demand_id,0))
    carried=freeze_leading_half(thu,date(2026,9,14))
    second=initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),frozen_history=carried.frozen_placements,provisional_overlap=carried.provisional_placements,canonical_demands=demands)
    assert second.demand_satisfaction[coached.demand_id].status=="provisional_satisfied"
    open_state=reconcile_demand_satisfaction(release_window_placement(second,"coach-thu"),demands,date(2026,9,14))
    assert open_state.demand_satisfaction[coached.demand_id].status=="open" and open_state.remaining_minutes_by_date[date(2026,9,17)]==90
    tue=reconcile_demand_satisfaction(assign_window_placement(open_state,WindowPlacement("coach-tue",date(2026,9,15),"coached_training",coached.demand_id,0)),demands,date(2026,9,14))
    assert tue.demand_satisfaction[coached.demand_id].status=="provisional_satisfied" and len([p for p in tue.provisional_placements if p.source_id==coached.demand_id])==1

def test_frozen_and_missed_canonical_demands_do_not_duplicate_in_later_windows():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}; calendar=build_v2_season_calendar(profile); demands=tuple(generate_training_demands(profile))
    coached=next(item for item in demands if item.type=="coached_training" and item.canonical_week_start==date(2026,9,7))
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),canonical_demands=demands)
    frozen=freeze_leading_half(assign_window_placement(state,WindowPlacement("coach",date(2026,9,8),"coached_training",coached.demand_id,0)),date(2026,9,14))
    later=reconcile_demand_satisfaction(initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),frozen_history=frozen.frozen_placements,canonical_demands=demands),demands,date(2026,9,14))
    assert later.demand_satisfaction[coached.demand_id].status=="frozen_satisfied"
    missed=reconcile_demand_satisfaction(initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),canonical_demands=demands),demands,date(2026,9,21))
    assert missed.demand_satisfaction[coached.demand_id].status=="missed"

def test_shared_solver_four_week_diagnostics_are_deterministic_and_leave_row_capacity_open():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-10-04"}; profile["recurring_activities"][1]["preferred_days"]=["thursday"]
    first,diagnostics=solve_v2_rolling_non_rowing(profile); second,repeat=solve_v2_rolling_non_rowing(profile)
    stable=lambda records: tuple({**item,"search":{key:value for key,value in item["search"].items() if key!="runtime_ms"}} for item in records)
    assert first==second and stable(diagnostics)==stable(repeat)
    placements=(*first.frozen_placements,*first.provisional_placements)
    source_ids=[item.source_id for item in placements if item.role in {"coached_training","rest"}]
    assert len(source_ids)==len(set(source_ids))
    strength=sorted(item.date for item in placements if item.role=="strength")
    assert all((right-left).days>=2 for left,right in zip(strength,strength[1:]))
    assert all({"coached_demands","rest_demands","strength","score_vector","search","frozen_placements","provisional_placements"}<=set(item) for item in diagnostics)
    assert all(item["search"]["beam_width"]==16 for item in diagnostics)
    assert any(value>0 for value in first.remaining_minutes_by_date.values())

def test_shared_solver_strength_reports_target_acceptable_and_below_minimum_without_unsafe_spacing():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    _,full=solve_v2_rolling_non_rowing(profile,calendar=calendar)
    assert full[0]["strength"]["status"]=="target_met" and full[0]["strength"]["achieved"]==4
    constrained=tuple(replace(item,available=False,unavailable=True,remaining_minutes=0,permitted_activity_categories=()) if index in {0,1,4} else item for index,item in enumerate(calendar))
    _,acceptable=solve_v2_rolling_non_rowing(profile,calendar=constrained)
    assert acceptable[0]["strength"]["status"]=="acceptable_miss" and acceptable[0]["strength"]["achieved"]==3
    too_small=tuple(replace(item,available=False,unavailable=True,remaining_minutes=0,permitted_activity_categories=()) if index in {0,4,7,11} else item for index,item in enumerate(calendar))
    _,below=solve_v2_rolling_non_rowing(profile,calendar=too_small)
    assert below[0]["strength"]["status"]=="below_minimum" and below[0]["strength"]["blockers"]

def test_frozen_sunday_strength_blocks_monday_but_not_tuesday():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}; calendar=build_v2_season_calendar(profile)
    sunday=WindowPlacement("sun",date(2026,9,13),"strength","strength",60,True)
    state=initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),frozen_history=(sunday,))
    try: assign_window_placement(state,WindowPlacement("mon",date(2026,9,14),"strength","strength",60))
    except ValueError as error: assert str(error)=="strength_spacing"
    else: assert False
    assert assign_window_placement(state,WindowPlacement("tue",date(2026,9,15),"strength","strength",60)).provisional_placements
