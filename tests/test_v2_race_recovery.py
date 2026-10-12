from datetime import date
import json
from pathlib import Path

from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.models import FrequencyTarget, V2DemandPlan, WindowPlacement
from rowing_plan.planner_v2 import generate_plan_v2
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler_v2 import (
    assign_window_placement,
    build_v2_season_calendar,
    initialize_active_window_state,
    move_user_placement,
    solve_v2_rolling_non_rowing,
)


def _profile(*, race_dates, priority="C", practice=False):
    profile=synthetic_profile()
    profile["season"]={**profile["season"],"start_date":"2026-10-16","end_date":"2026-10-23"}
    profile["recurring_activities"]=[]
    race={"event_name":"Test regatta","start_date":"2026-10-16","end_date":race_dates[-1],"race_dates":race_dates,"priority":priority,"race_type":"head_5k"}
    if practice:
        race["practice_sessions"]=[{"date":"2026-10-16","title":"Course familiarization","duration_minutes":30}]
    profile["races"]=[race]
    return profile


def _state(calendar):
    return initialize_active_window_state(calendar,calendar[0].date,calendar[-1].date)


def _rejection(state, placement):
    try:
        assign_window_placement(state,placement)
    except ValueError as error:
        return str(error)
    raise AssertionError("expected placement to be rejected")


def test_saturday_race_protects_sunday_but_keeps_practice_easy_and_later_training_available():
    calendar=build_v2_season_calendar(_profile(race_dates=["2026-10-17"],practice=True))
    friday=next(item for item in calendar if item.date==date(2026,10,16))
    saturday=next(item for item in calendar if item.date==date(2026,10,17))
    sunday=next(item for item in calendar if item.date==date(2026,10,18))
    monday=next(item for item in calendar if item.date==date(2026,10,19))
    assert friday.race_practice and not friday.post_race_recovery
    assert saturday.race and not saturday.post_race_recovery
    assert sunday.post_race_recovery and "post_race_recovery" in sunday.diagnostic_reason_codes
    assert not monday.post_race_recovery
    state=_state(calendar)
    assert _rejection(state,WindowPlacement("sun-lift",sunday.date,"strength","strength",30))=="post_race_recovery"
    assert _rejection(state,WindowPlacement("sun-quality",sunday.date,"quality","quality",35))=="post_race_recovery"
    # Recovery is not compulsory training: designated rest remains feasible.
    recovered=assign_window_placement(state,WindowPlacement("sun-rest",sunday.date,"rest","rest",0))
    assert recovered.provisional_placements[0].role=="rest"
    assert assign_window_placement(state,WindowPlacement("mon-lift",monday.date,"strength","strength",30)).provisional_placements


def test_multi_race_event_extends_recovery_beyond_single_c_priority_race():
    single=build_v2_season_calendar(_profile(race_dates=["2026-10-17"]))
    multi=build_v2_season_calendar(_profile(race_dates=["2026-10-17","2026-10-18"]))
    assert next(item for item in single if item.date==date(2026,10,18)).post_race_recovery
    assert not next(item for item in single if item.date==date(2026,10,19)).post_race_recovery
    assert next(item for item in multi if item.date==date(2026,10,19)).post_race_recovery
    assert next(item for item in multi if item.date==date(2026,10,20)).post_race_recovery
    assert not next(item for item in multi if item.date==date(2026,10,21)).post_race_recovery


def test_post_race_guard_preserves_user_authority_by_reporting_conflict_without_mutation():
    calendar=build_v2_season_calendar(_profile(race_dates=["2026-10-17"]))
    state=_state(calendar)
    friday,sunday=date(2026,10,16),date(2026,10,18)
    state=assign_window_placement(state,WindowPlacement("athlete-lift",friday,"strength","strength",30,False,(),True,date(2026,10,15),"athlete-choice"))
    result=move_user_placement(state,"athlete-lift",sunday,"athlete-after-race")
    assert not result.success and result.hard_failures==("post_race_recovery",)
    assert result.state==state and result.overrides==()


def test_no_race_leaves_ordinary_strength_and_quality_eligibility_unchanged():
    profile=_profile(race_dates=["2026-10-17"])
    profile["races"]=[]
    calendar=build_v2_season_calendar(profile)
    state=_state(calendar)
    sunday=date(2026,10,18)
    strength=assign_window_placement(state,WindowPlacement("lift",sunday,"strength","strength",30))
    assert strength.provisional_placements[0].role=="strength"


def test_complete_v2_plan_keeps_saturday_race_practice_and_recovery_day_distinct():
    profile=_profile(race_dates=["2026-10-17"],practice=True)
    profile["recurring_activities"]=[item for item in synthetic_profile()["recurring_activities"] if item["activity_type"]=="strength"]
    config=json.loads((Path(__file__).resolve().parents[1] / "config" / "defaults.json").read_text())
    plan=generate_plan_v2(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config))
    sessions=lambda day:[item for item in plan["sessions"] if item["date"]==day]
    assert [item["session_id"] for item in sessions("2026-10-16")]==["COURSE_PRACTICE"]
    assert [item["session_id"] for item in sessions("2026-10-17")]==["RACE"]
    assert not {item["session_id"] for item in sessions("2026-10-18")}&{"LIFT","QUALITY"}
    assert any(item["session_id"]=="LIFT" for item in sessions("2026-10-19"))


def test_rolling_strength_yields_to_race_recovery_and_moves_to_next_open_day():
    profile=_profile(race_dates=["2026-10-17"],practice=True)
    profile["recurring_activities"]=[item for item in synthetic_profile()["recurring_activities"] if item["activity_type"]=="strength"]
    calendar=build_v2_season_calendar(profile)
    strength=FrequencyTarget("strength",14,1,1,1,"strong",1,"test","must yield to post-race recovery")
    state,diagnostics=solve_v2_rolling_non_rowing(profile,V2DemandPlan((),(strength,),()),calendar)
    placements=(*state.frozen_placements,*state.provisional_placements)
    strength_dates={item.date for item in placements if item.role=="strength"}
    assert date(2026,10,18) not in strength_dates
    assert date(2026,10,19) in strength_dates
    assert diagnostics[0]["strength"]["achieved"]>=diagnostics[0]["strength"]["minimum"]
