from datetime import date, timedelta
from dataclasses import replace
from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.models import DatedTrainingRole, FrequencyTarget, QualityTranslationContext, RepairScope, TargetCredit, TrainingDoseTarget, V2DemandPlan, WindowPlacement
from rowing_plan.scheduler_v2 import _dose_target_summary, assign_window_placement, build_dated_training_roles, build_v2_season_calendar, candidate_dates_for_demand, classify_repair_placement, derive_repair_scope, freeze_leading_half, generate_frequency_targets, generate_rowing_dose_targets, generate_training_demands, generate_v2_demand_plan, initialize_active_window_state, move_user_placement, placements_compatible, place_v2_non_rowing, place_v2_rowing, reconcile_demand_satisfaction, reconstruct_repair_state, release_window_placement, repair_user_schedule_change, replace_window_placement, role_family, solve_v2_rolling_non_rowing, swap_user_placements, translate_quality_role, translate_quality_roles

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
    no_rowing=replace(generate_v2_demand_plan(profile),rowing_dose_targets=())
    _,full=solve_v2_rolling_non_rowing(profile,no_rowing,calendar)
    assert full[0]["strength"]["status"]=="target_met" and full[0]["strength"]["achieved"]==4
    constrained=tuple(replace(item,available=False,unavailable=True,remaining_minutes=0,permitted_activity_categories=()) if index in {0,1,4} else item for index,item in enumerate(calendar))
    _,acceptable=solve_v2_rolling_non_rowing(profile,no_rowing,constrained)
    assert acceptable[0]["strength"]["status"]=="acceptable_miss" and acceptable[0]["strength"]["achieved"]==3
    too_small=tuple(replace(item,available=False,unavailable=True,remaining_minutes=0,permitted_activity_categories=()) if index in {0,4,7,11} else item for index,item in enumerate(calendar))
    _,below=solve_v2_rolling_non_rowing(profile,no_rowing,too_small)
    assert below[0]["strength"]["status"]=="below_minimum" and below[0]["strength"]["blockers"]

def test_frozen_sunday_strength_blocks_monday_but_not_tuesday():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}; calendar=build_v2_season_calendar(profile)
    sunday=WindowPlacement("sun",date(2026,9,13),"strength","strength",60,True)
    state=initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),frozen_history=(sunday,))
    try: assign_window_placement(state,WindowPlacement("mon",date(2026,9,14),"strength","strength",60))
    except ValueError as error: assert str(error)=="strength_spacing"
    else: assert False
    assert assign_window_placement(state,WindowPlacement("tue",date(2026,9,15),"strength","strength",60)).provisional_placements

def test_rowing_overlap_role_change_replaces_sunday_quality_with_long_ut2_then_adds_tuesday_quality():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}
    profile["recurring_activities"]=[item for item in profile["recurring_activities"] if item["activity_type"]!="rest"]
    calendar=build_v2_season_calendar(profile)
    quality=WindowPlacement("sun-quality",date(2026,9,13),"quality","quality",25,False,(TargetCredit("quality","quality",1,25),))
    # Reconstruct the overlap where Sunday remains movable, then replace the
    # provisional quality role rather than duplicating its capacity or credits.
    overlap=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),provisional_overlap=(quality,))
    long=WindowPlacement("sun-long",date(2026,9,13),"long_aerobic","long",75,False,(TargetCredit("long","long_aerobic",1,75),TargetCredit("ut2","dedicated_ut2",1,75)))
    changed=replace_window_placement(overlap,"sun-quality",long)
    changed=assign_window_placement(changed,WindowPlacement("tue-quality",date(2026,9,15),"quality","quality",25,False,(TargetCredit("quality","quality",1,25),)))
    assert changed.remaining_minutes_by_date[date(2026,9,13)]==15
    assert changed.rowing_dose_credits["long"]==(1,75) and changed.rowing_dose_credits["ut2"]==(1,75)
    assert {item.placement_id for item in changed.provisional_placements}=={"sun-long","tue-quality"}

def test_conservative_coached_and_private_rows_have_no_assumed_rowing_credit():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20))
    coached=assign_window_placement(state,WindowPlacement("coach",date(2026,9,8),"coached_training","coach",0))
    assert not coached.rowing_dose_credits and coached.provisional_placements[0].credits==()

def test_two_slot_shared_solver_protects_ut2_and_strength_minimum_before_quality():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}
    profile["recurring_activities"]=[]
    raw=build_v2_season_calendar(profile); available={date(2026,9,7),date(2026,9,9),date(2026,9,14),date(2026,9,16)}
    calendar=tuple(replace(item,available=item.date in available,unavailable=item.date not in available,remaining_minutes=90 if item.date in available else 0,hard_committed_minutes=0,fixed_commitments=(),permitted_activity_categories=("strength","rowing") if item.date in available else ()) for item in raw)
    phase=calendar[0].phase_id
    strength=FrequencyTarget("strength",14,3,3,3,"strong",1,"test","three required strength exposures")
    ut2=TrainingDoseTarget(phase,"dedicated_ut2",date(2026,9,7),date(2026,9,20),14,1,1,75,75,"aerobic","strong",0,"test","protected UT2")
    quality=TrainingDoseTarget(phase,"quality",date(2026,9,7),date(2026,9,20),14,1,1,25,25,"quality","strong",1,"test","discretionary quality")
    plan=V2DemandPlan((),(strength,),(ut2,quality))
    state,diagnostics=solve_v2_rolling_non_rowing(profile,plan,calendar)
    placed={(item.date,item.role) for item in (*state.frozen_placements,*state.provisional_placements)}
    summary={item["category"]:item for item in diagnostics[0]["rowing_targets"]}
    assert any(role=="dedicated_ut2" for _,role in placed) and (date(2026,9,16),"strength") in placed
    assert summary["dedicated_ut2"]["status"]=="target_met" and diagnostics[0]["strength"]["achieved"]>=3
    assert summary["quality"]["status"]!="target_met"

def test_dated_role_adapter_preserves_identity_credits_order_and_provenance():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    frozen=WindowPlacement("long",date(2026,9,8),"long_aerobic","dose",75,True,(TargetCredit("long","long_aerobic",1,75),TargetCredit("ut2","dedicated_ut2",1,75)))
    provisional=WindowPlacement("quality",date(2026,9,10),"quality","quality",25)
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),frozen_history=(frozen,))
    state=assign_window_placement(state,provisional); roles=build_dated_training_roles(state)
    assert [(item.placement_id,item.role,item.provenance) for item in roles]==[("long","long_aerobic","frozen"),("quality","quality","provisional")]
    assert roles[0].duration_minutes==75 and roles[0].target_credits==frozen.credits and len(roles)==2

def test_dated_role_adapter_rejects_duplicate_placement_identity():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    duplicate=WindowPlacement("same",date(2026,9,8),"strength","strength",30,True)
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),frozen_history=(duplicate,duplicate))
    try: build_dated_training_roles(state)
    except ValueError as error: assert str(error)=="duplicate_placement_id"
    else: assert False

def test_user_fixed_mutability_and_rowing_only_restriction():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    day=date(2026,9,8); calendar=tuple(replace(item,prohibited_role_families=("rowing",)) if item.date==day else item for item in calendar)
    plain=WindowPlacement("plain",day,"strength","strength",30); fixed=WindowPlacement("fixed",day,"strength","strength",30,False,(),True,day,"override")
    assert not plain.user_fixed and plain.original_date is None and plain.override_id is None
    state=assign_window_placement(initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)),fixed)
    try: release_window_placement(state,"fixed")
    except ValueError as error: assert str(error)=="user_fixed_placement"
    else: assert False
    for role in ("dedicated_ut2","long_aerobic","ut1_aerobic_strength","quality"):
        try: assign_window_placement(initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)),WindowPlacement(role,day,role,role,20))
        except ValueError as error: assert str(error)=="activity_prohibited"
        else: assert False
    assert {role_family(role) for role in ("dedicated_ut2","long_aerobic","ut1_aerobic_strength","quality")}=={"rowing"}
    assert role_family("strength")!="rowing" and build_dated_training_roles(state)[0].provenance=="provisional"

def test_user_fixed_replace_rejects_while_ordinary_provisional_remains_mutable():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    base=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)); fixed=assign_window_placement(base,WindowPlacement("fixed",date(2026,9,8),"strength","strength",30,False,(),True))
    try: replace_window_placement(fixed,"fixed",WindowPlacement("new",date(2026,9,10),"strength","strength",30))
    except ValueError as error: assert str(error)=="user_fixed_placement" and fixed.provisional_placements[0].placement_id=="fixed"
    else: assert False
    ordinary=assign_window_placement(base,WindowPlacement("old",date(2026,9,8),"strength","strength",30))
    changed=replace_window_placement(ordinary,"old",WindowPlacement("new",date(2026,9,10),"strength","strength",30))
    assert {item.placement_id for item in changed.provisional_placements}=={"new"}

def test_user_fixed_reconstruction_preserves_metadata_and_historical_spacing():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}; calendar=build_v2_season_calendar(profile)
    active=WindowPlacement("active",date(2026,9,15),"strength","strength",30,False,(),True,date(2026,9,8),"override-test-1")
    state=initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),provisional_overlap=(active,))
    item=state.provisional_placements[0]
    assert (item.user_fixed,item.original_date,item.override_id,item.placement_id)==(True,date(2026,9,8),"override-test-1","active") and state.remaining_minutes_by_date[date(2026,9,15)]==60
    try: release_window_placement(state,"active")
    except ValueError as error: assert str(error)=="user_fixed_placement"
    else: assert False

def test_historical_user_fixed_ut2_respects_horizon_expiration_and_phase_clipping():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}; calendar=build_v2_season_calendar(profile)
    phase=calendar[0].phase_id; target=TrainingDoseTarget(phase,"dedicated_ut2",date(2026,9,7),date(2026,9,27),14,1,1,75,75,"aerobic","strong",0,"test","")
    credit=(TargetCredit(f"{phase}:dedicated_ut2","dedicated_ut2",1,75),)
    fixed=WindowPlacement("ut2",date(2026,9,13),"dedicated_ut2",f"{phase}:dedicated_ut2",75,True,credit,True,date(2026,9,8),"override")
    ordinary=WindowPlacement("ut2o",date(2026,9,13),"dedicated_ut2",f"{phase}:dedicated_ut2",75,True,credit)
    state=initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),frozen_history=(fixed,))
    summary=_dose_target_summary(state,target,date(2026,9,7),date(2026,9,20))
    assert fixed.user_fixed and summary["achieved_exposures"]==1 and summary["achieved_minutes"]==75 and state.remaining_minutes_by_date[date(2026,9,14)]==90
    ordinary_state=initialize_active_window_state(calendar,date(2026,9,14),date(2026,9,27),frozen_history=(ordinary,))
    assert _dose_target_summary(ordinary_state,target,date(2026,9,7),date(2026,9,20))==summary
    assert _dose_target_summary(state,target,date(2026,9,21),date(2026,9,27))["achieved_minutes"]==0
    clipped=replace(target,window_start=date(2026,9,14)); assert _dose_target_summary(state,clipped,date(2026,9,14),date(2026,9,20))["achieved_minutes"]==0

def test_explicit_user_move_is_atomic_preserves_identity_and_allows_second_move():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    base=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)); state=assign_window_placement(base,WindowPlacement("lift",date(2026,9,8),"strength","strength",30))
    moved=move_user_placement(state,"lift",date(2026,9,7),"one")
    item=moved.state.provisional_placements[0]
    assert moved.success and (item.placement_id,item.source_id,item.date,item.original_date,item.user_fixed,item.override_id)==("lift","strength",date(2026,9,7),date(2026,9,8),True,"one")
    assert moved.state.remaining_minutes_by_date[date(2026,9,8)]==90 and moved.state.remaining_minutes_by_date[date(2026,9,7)]==60
    second=move_user_placement(moved.state,"lift",date(2026,9,10),"two")
    assert second.success and second.state.provisional_placements[0].original_date==date(2026,9,8) and second.state.provisional_placements[0].override_id=="two"
    same=move_user_placement(second.state,"lift",date(2026,9,10),"same")
    assert same.success and same.state==second.state and not same.overrides

def test_user_move_rejects_rowing_restriction_atomically_and_adapter_has_no_stale_date():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    restricted=tuple(replace(item,prohibited_role_families=("rowing",)) if item.date==date(2026,9,8) else item for item in calendar)
    credit=(TargetCredit("ut2","dedicated_ut2",1,45),); state=assign_window_placement(initialize_active_window_state(restricted,date(2026,9,7),date(2026,9,20)),WindowPlacement("ut2",date(2026,9,7),"dedicated_ut2","ut2",45,False,credit))
    failed=move_user_placement(state,"ut2",date(2026,9,8),"blocked")
    assert not failed.success and failed.hard_failures==("activity_prohibited",) and failed.state==state and not failed.overrides
    moved=move_user_placement(state,"ut2",date(2026,9,10),"ok")
    assert moved.success and [(item.placement_id,item.date) for item in build_dated_training_roles(moved.state)]==[("ut2",date(2026,9,10))]
    try: release_window_placement(moved.state,"ut2")
    except ValueError as error: assert str(error)=="user_fixed_placement"
    else: assert False

def test_user_move_capacity_race_and_frozen_failures_are_atomic():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    constrained=tuple(replace(item,remaining_minutes=45) if item.date==date(2026,9,8) else item for item in calendar)
    state=assign_window_placement(initialize_active_window_state(constrained,date(2026,9,7),date(2026,9,20)),WindowPlacement("lift",date(2026,9,7),"strength","strength",60))
    failed=move_user_placement(state,"lift",date(2026,9,8),"cap")
    assert not failed.success and failed.hard_failures==("insufficient_minutes",) and failed.state==state and not failed.overrides
    frozen=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),frozen_history=(WindowPlacement("old",date(2026,9,7),"strength","strength",30,True),))
    no_move=move_user_placement(frozen,"old",date(2026,9,8),"frozen")
    assert not no_move.success and no_move.state==frozen and not no_move.overrides

def test_user_move_long_multi_credit_preserves_single_session_and_capacity():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    credits=(TargetCredit("long","long_aerobic",1,75),TargetCredit("ut2","dedicated_ut2",1,75)); state=assign_window_placement(initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)),WindowPlacement("long",date(2026,9,7),"long_aerobic","long",75,False,credits))
    moved=move_user_placement(state,"long",date(2026,9,8),"long-move")
    item=moved.state.provisional_placements[0]
    assert moved.success and item.credits==credits and moved.state.remaining_minutes_by_date[date(2026,9,7)]==90 and moved.state.remaining_minutes_by_date[date(2026,9,8)]==15
    assert [(role.placement_id,role.date) for role in build_dated_training_roles(moved.state)]==[("long",date(2026,9,8))]

def test_user_move_race_and_spacing_follow_current_not_original_date():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    raced=tuple(replace(item,race=True) if item.date==date(2026,9,8) else item for item in calendar)
    state=assign_window_placement(initialize_active_window_state(raced,date(2026,9,7),date(2026,9,20)),WindowPlacement("s",date(2026,9,7),"strength","strength",30))
    failed=move_user_placement(state,"s",date(2026,9,8),"race")
    assert not failed.success and failed.hard_failures==("hard_calendar_conflict",) and failed.state==state
    clean=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20))
    strength=move_user_placement(assign_window_placement(clean,WindowPlacement("a",date(2026,9,7),"strength","strength",30)),"a",date(2026,9,9),"move").state
    assert strength.provisional_placements[0].original_date==date(2026,9,7)
    try: assign_window_placement(strength,WindowPlacement("b",date(2026,9,10),"strength","strength",30))
    except ValueError as error: assert str(error)=="strength_spacing"
    else: assert False

def test_atomic_user_swap_exchanges_capacity_identity_and_rolls_back_restriction_failure():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)); state=assign_window_placement(state,WindowPlacement("ut2",date(2026,9,7),"dedicated_ut2","ut2",60)); state=assign_window_placement(state,WindowPlacement("lift",date(2026,9,8),"strength","strength",30))
    swapped=swap_user_placements(state,"ut2","lift","swap")
    assert swapped.success and {(item.placement_id,item.date,item.user_fixed) for item in swapped.state.provisional_placements}=={("ut2",date(2026,9,8),True),("lift",date(2026,9,7),True)}
    assert swapped.state.remaining_minutes_by_date[date(2026,9,7)]==60 and swapped.state.remaining_minutes_by_date[date(2026,9,8)]==30

def test_atomic_swap_preserves_origins_audit_order_and_multi_credit_identity():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    credits=(TargetCredit("long","long_aerobic",1,75),TargetCredit("ut2","dedicated_ut2",1,75))
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)); state=assign_window_placement(state,WindowPlacement("a",date(2026,9,7),"long_aerobic","long",75,False,credits)); state=assign_window_placement(state,WindowPlacement("b",date(2026,9,8),"strength","strength",30)); state=assign_window_placement(state,WindowPlacement("c",date(2026,9,10),"quality","quality",20))
    first=swap_user_placements(state,"a","b","swap-1","weather"); reverse=swap_user_placements(state,"b","a","swap-1","weather")
    a=next(item for item in first.state.provisional_placements if item.placement_id=="a"); b=next(item for item in first.state.provisional_placements if item.placement_id=="b")
    assert first.success and a.date==date(2026,9,8) and a.original_date==date(2026,9,7) and a.credits==credits and b.original_date==date(2026,9,8)
    assert len(first.overrides)==1 and first.overrides[0].action_type=="swap" and first.overrides[0].reason=="weather"
    assert first.state==reverse.state and next(item for item in first.state.provisional_placements if item.placement_id=="c")==next(item for item in state.provisional_placements if item.placement_id=="c")
    assert [(item.placement_id,item.date) for item in build_dated_training_roles(first.state)]==[("b",date(2026,9,7)),("a",date(2026,9,8)),("c",date(2026,9,10))]

def test_swap_weather_reswap_audit_and_atomic_failure_contract():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)); state=assign_window_placement(state,WindowPlacement("u",date(2026,9,7),"dedicated_ut2","ut2",45)); state=assign_window_placement(state,WindowPlacement("s",date(2026,9,8),"strength","strength",30)); state=assign_window_placement(state,WindowPlacement("c",date(2026,9,10),"quality","quality",20))
    weather=replace(state,fixed_context={**state.fixed_context,date(2026,9,7):replace(state.fixed_context[date(2026,9,7)],prohibited_role_families=("rowing",))})
    swapped=swap_user_placements(weather,"u","s","weather","rain")
    assert swapped.success and {(x.placement_id,x.date) for x in swapped.state.provisional_placements if x.placement_id in {"u","s"}}=={("u",date(2026,9,8)),("s",date(2026,9,7))}
    assert len(swapped.overrides)==1 and swapped.overrides[0].placement_ids==("s","u") and swapped.overrides[0].reason=="rain"
    clear=replace(swapped.state,fixed_context=state.fixed_context); reswap=swap_user_placements(clear,"u","s","again"); u=next(x for x in reswap.state.provisional_placements if x.placement_id=="u")
    assert reswap.success and u.original_date==date(2026,9,7) and u.override_id=="again"
    blocked=replace(state,fixed_context={**state.fixed_context,date(2026,9,8):replace(state.fixed_context[date(2026,9,8)],prohibited_role_families=("rowing",))})
    failed=swap_user_placements(blocked,"u","s","bad")
    assert not failed.success and failed.state==blocked and not failed.overrides

def test_swap_failure_matrix_capacity_race_frozen_and_missing_are_atomic():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    cap=tuple(replace(item,max_training_minutes=60,remaining_minutes=60) if item.date==date(2026,9,7) else item for item in calendar)
    state=initialize_active_window_state(cap,date(2026,9,7),date(2026,9,20)); state=assign_window_placement(state,WindowPlacement("a",date(2026,9,7),"strength","strength",30)); state=assign_window_placement(state,WindowPlacement("b",date(2026,9,8),"long_aerobic","long",75))
    for left,right,reason in (("a","b","insufficient_minutes"),("a","missing","unknown_placement")):
        result=swap_user_placements(state,left,right,"x"); assert not result.success and result.hard_failures==(reason,) and result.state==state and not result.overrides
    race=replace(state,fixed_context={**state.fixed_context,date(2026,9,7):replace(state.fixed_context[date(2026,9,7)],race=True)})
    result=swap_user_placements(race,"a","b","race"); assert not result.success and result.hard_failures==("hard_calendar_conflict",) and result.state==race
    frozen=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),frozen_history=(WindowPlacement("old",date(2026,9,7),"strength","strength",30,True),))
    assert not swap_user_placements(frozen,"old","missing","f").success

def test_swap_third_placement_strength_and_quality_spacing_fail_atomically():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    for role,reason in (("strength","strength_spacing"),("quality","quality_spacing")):
        state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20)); state=assign_window_placement(state,WindowPlacement("a",date(2026,9,7),role,role,20)); state=assign_window_placement(state,WindowPlacement("b",date(2026,9,11),"long_aerobic","long",20)); state=assign_window_placement(state,WindowPlacement("c",date(2026,9,10),role,role,20))
        result=swap_user_placements(state,"a","b","spacing")
        assert not result.success and result.hard_failures==(reason,) and result.state==state and not result.overrides

def _repair_scope_fixture(start=date(2026,9,7), end=date(2026,10,11)):
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":start.isoformat(),"end_date":end.isoformat()}
    calendar=build_v2_season_calendar(profile)
    strength=FrequencyTarget("strength",14,4,3,4,"strong",1,"test","")
    early=TrainingDoseTarget("phase-a","dedicated_ut2",start,date(2026,9,20),14,2,1,120,90,"aerobic","strong",0,"test","")
    late=TrainingDoseTarget("phase-b","dedicated_ut2",date(2026,9,21),end,14,2,1,120,90,"aerobic","strong",0,"test","")
    quality=TrainingDoseTarget("phase-b","quality",date(2026,9,21),end,14,1,0,45,0,"quality","strong",1,"test","")
    coached=generate_training_demands(profile)
    return calendar,V2DemandPlan(tuple(coached),(strength,),(early,late,quality))

def test_repair_scope_uses_actual_windows_and_is_order_invariant_for_nearby_move():
    calendar,plan=_repair_scope_fixture()
    monday,tuesday=date(2026,9,14),date(2026,9,15)
    scope=derive_repair_scope((monday,tuesday),calendar,plan)
    reverse=derive_repair_scope((tuesday,monday),calendar,plan)
    # Sep 14/15 occur in the Sep 7--20 and Sep 14--27 solver windows.
    assert isinstance(scope,RepairScope) and scope==reverse
    assert scope.changed_dates==(monday,tuesday)
    assert (scope.mutable_start,scope.mutable_end)==(date(2026,9,7),date(2026,9,27))
    # Strength's real 14-day horizon supplies the prior thirteen days; season
    # clipping prevents an invalid pre-season history range.
    assert scope.history_start==date(2026,9,7)
    # Strength influence ends Sep 27; the canonical Sep 21--27 coached/rest
    # week is still reconciled by its overlapping Sep 21--Oct 4 solver window.
    assert scope.reconciliation_end==date(2026,10,4)

def test_repair_scope_unions_wider_swap_and_clips_season_edges():
    calendar,plan=_repair_scope_fixture()
    scope=derive_repair_scope((date(2026,9,8),date(2026,9,29)),calendar,plan)
    assert (scope.mutable_start,scope.mutable_end)==(date(2026,9,7),date(2026,10,11))
    assert scope.history_start==date(2026,9,7) and scope.reconciliation_end==date(2026,10,11)
    start_calendar,start_plan=_repair_scope_fixture(date(2026,9,7),date(2026,9,20))
    at_start=derive_repair_scope((date(2026,9,7),),start_calendar,start_plan)
    assert at_start.history_start==date(2026,9,7)
    end_calendar,end_plan=_repair_scope_fixture(date(2026,9,7),date(2026,9,20))
    at_end=derive_repair_scope((date(2026,9,20),),end_calendar,end_plan)
    assert at_end.mutable_end==date(2026,9,20) and at_end.reconciliation_end==date(2026,9,20)

def test_repair_scope_keeps_rowing_history_phase_clipped_and_includes_canonical_week():
    calendar,plan=_repair_scope_fixture()
    # This change is on the first day of phase-b.  The mutable calendar range
    # can cross the transition, while dose history for phase-b starts at Sep 21.
    scope=derive_repair_scope((date(2026,9,21),),calendar,plan)
    assert scope.mutable_start==date(2026,9,14) and scope.mutable_end==date(2026,10,4)
    phase_b=next(item for item in plan.rowing_dose_targets if item.phase_id=="phase-b" and item.category=="dedicated_ut2")
    assert max(phase_b.window_start,scope.mutable_start-timedelta(days=phase_b.window_days-1))==date(2026,9,21)
    # Coached/rest demands retain Monday--Sunday canonical identity; the
    # affected week is included by reconciliation rather than invented for
    # rolling strength or rowing targets.
    affected=[item for item in plan.weekly_commitment_demands if item.type in {"coached_training","rest"} and item.canonical_week_start<=scope.mutable_end and item.canonical_week_end>=scope.mutable_start]
    assert affected and scope.reconciliation_end>=max(item.canonical_week_end for item in affected)

def test_repair_reconstruction_reopens_planner_work_but_preserves_user_fixed_capacity_and_demands():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}
    calendar=build_v2_season_calendar(profile); demands=tuple(generate_training_demands(profile)); rest=next(item for item in demands if item.type=="rest"); coached=next(item for item in demands if item.type=="coached_training")
    scope=RepairScope((date(2026,9,7),),date(2026,9,7),date(2026,9,20),date(2026,9,7),date(2026,9,20))
    placements=(
        WindowPlacement("ut2",date(2026,9,7),"dedicated_ut2","ut2",60,False,(TargetCredit("ut2","dedicated_ut2",1,60),)),
        WindowPlacement("lift",date(2026,9,7),"strength","strength",30,False,(),True,date(2026,9,6),"athlete"),
        WindowPlacement("rest",date(2026,9,8),"rest",rest.demand_id,0),
        WindowPlacement("coach",date(2026,9,10),"coached_training",coached.demand_id,0),
        WindowPlacement("long",date(2026,9,11),"long_aerobic","long",75,False,(TargetCredit("long","long_aerobic",1,75),TargetCredit("ut2","dedicated_ut2",1,75))),
        WindowPlacement("quality",date(2026,9,13),"quality","quality",20),
    )
    rebuilt=reconstruct_repair_state(calendar,scope,placements,canonical_demands=demands)
    assert rebuilt.state.remaining_minutes_by_date[date(2026,9,7)]==60
    assert [(item.placement_id,item.date,item.user_fixed) for item in rebuilt.state.provisional_placements]==[("lift",date(2026,9,7),True)]
    assert {item.placement_id for item in rebuilt.reopened_placements}=={"ut2","rest","coach","long","quality"}
    assert set(rebuilt.reopened_source_ids)>={rest.demand_id,coached.demand_id}
    assert rebuilt.state.demand_satisfaction[rest.demand_id].status=="open"
    assert rebuilt.state.demand_satisfaction[coached.demand_id].status=="open"

def test_repair_reconstruction_keeps_historical_frozen_context_and_reopens_future_frozen():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}; calendar=build_v2_season_calendar(profile)
    scope=RepairScope((date(2026,9,15),),date(2026,9,14),date(2026,9,27),date(2026,9,7),date(2026,9,27))
    historical=WindowPlacement("history",date(2026,9,13),"strength","strength",30,True)
    historical_ut2=WindowPlacement("history-ut2",date(2026,9,12),"dedicated_ut2","ut2",45,True,(TargetCredit("ut2","dedicated_ut2",1,45),))
    future_frozen=WindowPlacement("future",date(2026,9,15),"dedicated_ut2","ut2",45,True,(TargetCredit("ut2","dedicated_ut2",1,45),))
    rebuilt=reconstruct_repair_state(calendar,scope,(future_frozen,historical,historical_ut2))
    assert classify_repair_placement(historical,scope,calendar)=="immutable_context"
    assert classify_repair_placement(future_frozen,scope,calendar)=="reopened_planner_owned"
    assert {item.placement_id for item in rebuilt.immutable_history}=={"history","history-ut2"} and [item.placement_id for item in rebuilt.reopened_placements]==["future"]
    assert rebuilt.state.rowing_dose_credits["ut2"]==(1,45)
    assert rebuilt.state.remaining_minutes_by_date[date(2026,9,14)]==90
    try: release_window_placement(rebuilt.state,"history")
    except ValueError as error: assert str(error)=="frozen_placement"
    else: assert False
    try: assign_window_placement(rebuilt.state,WindowPlacement("adjacent",date(2026,9,14),"strength","strength",30))
    except ValueError as error: assert str(error)=="strength_spacing"
    else: assert False

def test_repair_classification_respects_calendar_authority_user_fixed_and_is_deterministic():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; base=build_v2_season_calendar(profile)
    calendar=tuple(
        replace(item,race=True) if item.date==date(2026,9,7) else
        replace(item,fixed_commitments=({"type":"locked","source_id":"lock","fixed":True},)) if item.date==date(2026,9,8) else
        replace(item,completed_sessions=({"source_id":"done"},)) if item.date==date(2026,9,9) else
        replace(item,fixed_commitments=({"type":"coached_row","fixed":True},)) if item.date==date(2026,9,12) else item
        for item in base
    )
    scope=RepairScope((date(2026,9,10),),date(2026,9,7),date(2026,9,20),date(2026,9,7),date(2026,9,20))
    placements=(
        WindowPlacement("race",date(2026,9,7),"strength","strength",30),
        WindowPlacement("lock",date(2026,9,8),"strength","lock",30),
        WindowPlacement("done",date(2026,9,9),"strength","done",30),
        WindowPlacement("private",date(2026,9,9),"private_coaching","private",0),
        WindowPlacement("fixed-coach",date(2026,9,12),"coached_training","coach",0),
        WindowPlacement("user-rest",date(2026,9,10),"rest","rest",0,False,(),True),
        WindowPlacement("user-coach",date(2026,9,11),"coached_training","coach",0,False,(),True),
    )
    assert [classify_repair_placement(item,scope,calendar) for item in placements[:5]]==["immutable_context"]*5
    assert [classify_repair_placement(item,scope,calendar) for item in placements[5:]]==["preserved_user_fixed"]*2
    first=reconstruct_repair_state(calendar,scope,placements)
    second=reconstruct_repair_state(calendar,scope,tuple(reversed(placements)))
    assert first==second and {item.placement_id for item in first.preserved_user_fixed}=={"user-rest","user-coach"}

def test_shared_solver_preserves_user_fixed_overlap_and_releases_only_planner_owned_overlap():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-27"}; calendar=build_v2_season_calendar(profile)
    strength=FrequencyTarget("strength",14,1,0,1,"strong",1,"test","")
    ut2=TrainingDoseTarget("phase","dedicated_ut2",date(2026,9,7),date(2026,9,27),14,1,0,75,0,"aerobic","strong",0,"test","")
    long=TrainingDoseTarget("phase","long_aerobic",date(2026,9,7),date(2026,9,27),14,1,0,75,0,"aerobic","strong",0,"test","")
    quality=TrainingDoseTarget("phase","quality",date(2026,9,7),date(2026,9,27),14,1,0,20,0,"quality","strong",1,"test","")
    plan=V2DemandPlan((),(strength,),(ut2,long,quality))
    credits=(TargetCredit("phase:long_aerobic","long_aerobic",1,75),TargetCredit("phase:dedicated_ut2","dedicated_ut2",1,75))
    fixed=WindowPlacement("fixed-long",date(2026,9,15),"long_aerobic","phase:long_aerobic",75,False,credits,True,date(2026,9,14),"athlete")
    planner=WindowPlacement("planner-quality",date(2026,9,10),"quality","phase:quality",20)
    initial=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),provisional_overlap=(fixed,planner))
    assert initial.remaining_minutes_by_date[date(2026,9,15)]==15
    final,diagnostics=solve_v2_rolling_non_rowing(profile,plan,calendar,initial_state=initial)
    retained=[item for item in (*final.frozen_placements,*final.provisional_placements) if item.placement_id=="fixed-long"]
    assert len(retained)==1 and retained[0].date==date(2026,9,15) and retained[0].frozen and retained[0].user_fixed and retained[0].original_date==date(2026,9,14) and retained[0].override_id=="athlete" and retained[0].credits==credits
    assert not any(item.placement_id=="planner-quality" for item in (*final.frozen_placements,*final.provisional_placements))
    first=diagnostics[0]
    assert any(item["placement_id"]=="fixed-long" for item in first["provisional_placements"])
    assert any(item["placement_id"]=="fixed-long" for item in diagnostics[1]["provisional_placements"])
    assert any(item["category"]=="long_aerobic" and item["achieved_minutes"]>=75 for item in first["rowing_targets"])

def test_shared_solver_user_fixed_rest_and_coached_satisfy_demands_without_duplicate_and_no_user_parity():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile); demands=tuple(generate_training_demands(profile))
    rest=next(item for item in demands if item.type=="rest"); coached=next(item for item in demands if item.type=="coached_training")
    initial=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),provisional_overlap=(WindowPlacement("rest",date(2026,9,8),"rest",rest.demand_id,0,False,(),True,date(2026,9,8),"r"),WindowPlacement("coach",date(2026,9,10),"coached_training",coached.demand_id,0,False,(),True,date(2026,9,10),"c")),canonical_demands=demands)
    plan=V2DemandPlan(demands,(FrequencyTarget("strength",14,1,0,1,"strong",1,"test",""),),())
    final,_=solve_v2_rolling_non_rowing(profile,plan,calendar,initial_state=initial)
    retained={item.placement_id:item for item in (*final.frozen_placements,*final.provisional_placements)}
    assert set(retained)>={"rest","coach"} and all(retained[key].user_fixed for key in ("rest","coach"))
    assert len([item for item in retained.values() if item.source_id==coached.demand_id])==1
    assert reconcile_demand_satisfaction(final,demands).demand_satisfaction[coached.demand_id].status in {"frozen_satisfied","provisional_satisfied"}
    ordinary=solve_v2_rolling_non_rowing(profile,plan,calendar)
    explicit_none=solve_v2_rolling_non_rowing(profile,plan,calendar,initial_state=None)
    stable=lambda result:(result[0],tuple({**item,"search":{key:value for key,value in item["search"].items() if key!="runtime_ms"}} for item in result[1]))
    assert stable(ordinary)==stable(explicit_none)

def test_shared_solver_keeps_user_fixed_strength_and_generic_quality_while_reopening_conflicts():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    strength=FrequencyTarget("strength",14,1,0,1,"strong",1,"test","")
    quality_target=TrainingDoseTarget("phase","quality",date(2026,9,7),date(2026,9,20),14,1,0,20,0,"quality","strong",1,"test","")
    user_strength=WindowPlacement("user-strength",date(2026,9,8),"strength","strength",30,False,(),True,date(2026,9,7),"s")
    user_quality=WindowPlacement("user-quality",date(2026,9,11),"quality","phase:quality",20,False,(TargetCredit("phase:quality","quality",1,20),),True,date(2026,9,10),"q")
    planner_strength=WindowPlacement("planner-strength",date(2026,9,14),"strength","strength",30)
    planner_quality=WindowPlacement("planner-quality",date(2026,9,15),"quality","phase:quality",20)
    initial=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20),provisional_overlap=(user_strength,user_quality,planner_strength,planner_quality))
    final,diagnostics=solve_v2_rolling_non_rowing(profile,V2DemandPlan((),(strength,),(quality_target,)),calendar,initial_state=initial)
    retained={item.placement_id:item for item in (*final.frozen_placements,*final.provisional_placements)}
    assert {"user-strength","user-quality"}<=set(retained) and all(retained[item].user_fixed for item in ("user-strength","user-quality"))
    assert not {"planner-strength","planner-quality"}&set(retained)
    assert diagnostics[0]["strength"]["achieved"]>=1
    assert next(item for item in diagnostics[0]["rowing_targets"] if item["category"]=="quality")["achieved_minutes"]>=20

def test_local_repair_weather_swap_reopens_third_strength_and_preserves_history_locally():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; ordinary_calendar=build_v2_season_calendar(profile)
    state=initialize_active_window_state(ordinary_calendar,date(2026,9,7),date(2026,9,20),frozen_history=(WindowPlacement("history",date(2026,9,4),"strength","strength",30,True),))
    state=assign_window_placement(state,WindowPlacement("ut2",date(2026,9,7),"dedicated_ut2","ut2",45))
    state=assign_window_placement(state,WindowPlacement("c",date(2026,9,8),"strength","strength",30))
    state=assign_window_placement(state,WindowPlacement("lift",date(2026,9,10),"strength","strength",30))
    weather_calendar=tuple(replace(item,prohibited_role_families=("rowing",)) if item.date==date(2026,9,7) else item for item in ordinary_calendar)
    weather_state=replace(state,fixed_context={item.date:item for item in weather_calendar})
    direct=swap_user_placements(weather_state,"ut2","lift","weather")
    assert not direct.success and direct.hard_failures==("strength_spacing",)
    plan=V2DemandPlan((),(FrequencyTarget("strength",14,1,0,1,"strong",1,"test",""),),())
    repaired=repair_user_schedule_change(profile,weather_state,action_type="swap",placement_ids=("ut2","lift"),override_id="weather",reason="rain",demand_plan=plan,calendar=weather_calendar)
    assert repaired.success and repaired.overrides[0].action_type=="swap"
    final={item.placement_id:item for item in repaired.merged_placements}
    assert final["lift"].date==date(2026,9,7) and final["lift"].user_fixed
    assert final["ut2"].date==date(2026,9,10) and final["ut2"].user_fixed
    assert "c" not in final and final["history"].date==date(2026,9,4)
    assert not any(item.role=="dedicated_ut2" and item.date==date(2026,9,7) for item in repaired.merged_placements)
    assert [(item.kind,item.placement_id) for item in repaired.repair_changes]==[("removed","c")]
    assert not repaired.target_consequences

def test_local_repair_relocates_open_rest_and_returns_original_on_hard_invalid_move():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile); demands=tuple(generate_training_demands(profile)); rest=next(item for item in demands if item.type=="rest")
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20))
    state=assign_window_placement(state,WindowPlacement("lift",date(2026,9,7),"strength","strength",30))
    state=assign_window_placement(state,WindowPlacement("rest",date(2026,9,8),"rest",rest.demand_id,0))
    plan=V2DemandPlan(demands,(FrequencyTarget("strength",14,1,0,1,"strong",1,"test",""),),())
    repaired=repair_user_schedule_change(profile,state,action_type="move",placement_ids=("lift",),destination=date(2026,9,8),override_id="move",demand_plan=plan,calendar=calendar)
    assert repaired.success
    final={item.placement_id:item for item in repaired.merged_placements}
    assert final["lift"].date==date(2026,9,8) and final["lift"].user_fixed
    rests=[item for item in final.values() if item.source_id==rest.demand_id]
    assert len(rests)==1 and rests[0].date!=date(2026,9,8)
    assert any(item.placement_id=="rest" and item.kind in {"moved","removed"} for item in repaired.repair_changes)
    raced=tuple(replace(item,race=True) if item.date==date(2026,9,10) else item for item in calendar)
    invalid=repair_user_schedule_change(profile,state,action_type="move",placement_ids=("lift",),destination=date(2026,9,10),override_id="race",demand_plan=plan,calendar=raced)
    assert not invalid.success and invalid.hard_failures==("hard_calendar_conflict",) and invalid.state==state and invalid.merged_placements==tuple(sorted((*state.frozen_placements,*state.provisional_placements),key=lambda item:(item.date,item.placement_id,item.role)))
    assert not invalid.repair_changes and not invalid.target_consequences

def test_local_repair_reopens_conflicting_generic_quality_without_undoing_user_move():
    profile=synthetic_profile(); profile["season"]={**profile["season"],"start_date":"2026-09-07","end_date":"2026-09-20"}; calendar=build_v2_season_calendar(profile)
    state=initialize_active_window_state(calendar,date(2026,9,7),date(2026,9,20))
    state=assign_window_placement(state,WindowPlacement("user-q",date(2026,9,10),"quality","phase:quality",20))
    state=assign_window_placement(state,WindowPlacement("planner-q",date(2026,9,8),"quality","phase:quality",20))
    direct=move_user_placement(state,"user-q",date(2026,9,7),"q")
    assert not direct.success and direct.hard_failures==("quality_spacing",)
    target=TrainingDoseTarget("phase","quality",date(2026,9,7),date(2026,9,20),14,1,0,20,0,"quality","strong",1,"test","")
    repaired=repair_user_schedule_change(profile,state,action_type="move",placement_ids=("user-q",),destination=date(2026,9,7),override_id="q",demand_plan=V2DemandPlan((),(FrequencyTarget("strength",14,0,0,0,"strong",1,"test",""),),(target,)),calendar=calendar)
    final={item.placement_id:item for item in repaired.merged_placements}
    assert repaired.success and final["user-q"].date==date(2026,9,7) and final["user-q"].user_fixed and "planner-q" not in final

def test_quality_translation_is_pure_phase_based_and_preserves_user_metadata():
    role=DatedTrainingRole(date(2026,9,14),"quality",50,"ignored","source","provisional",(),"quality-1")
    moved=translate_quality_role(role,QualityTranslationContext("race_specific_preparation",True,date(2026,9,10),"move"))
    threshold=translate_quality_role(role,QualityTranslationContext("threshold_development"))
    taper=translate_quality_role(role,QualityTranslationContext("taper"))
    sprint=translate_quality_role(role,QualityTranslationContext("race_specific_preparation",explicit_intent="SPRINT_POWER"))
    assert (moved.quality_type,threshold.quality_type,taper.quality_type,sprint.quality_type)==("TR","AT","TR","PP")
    assert (moved.date,moved.placement_id,moved.source_id,moved.planned_duration_minutes,moved.user_fixed,moved.original_date,moved.override_id)==(role.date,"quality-1","source",50,True,date(2026,9,10),"move")
    assert [item.quality_type for item in translate_quality_roles((role,role),{"quality-1":QualityTranslationContext("threshold_development")})]==["AT","AT"]
    try: translate_quality_role(DatedTrainingRole(role.date,"strength",30,"p","s","provisional",(),"s"),QualityTranslationContext("threshold_development"))
    except ValueError as error: assert str(error)=="quality_translation_requires_quality_role"
    else: assert False
    try: translate_quality_role(role,QualityTranslationContext("post_race_recovery"))
    except ValueError as error: assert str(error)=="quality_not_valid_for_phase"
    else: assert False
