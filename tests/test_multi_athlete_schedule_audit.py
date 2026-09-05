"""Disposable, athlete-specific audit of whole-week scheduling constraints."""
from __future__ import annotations

import copy
import json
from datetime import date, timedelta

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan


DAYS=("monday","tuesday","wednesday","thursday","friday","saturday","sunday")


def _profile(*, experience="experienced", minutes=220, available=DAYS, preferences=None, activities=None):
    return {
        "athlete":{"display_name":"Disposable schedule athlete","experience_level":experience,"current_rowing_sessions_per_week":4 if experience!="novice" else 2,"current_approx_weekly_rowing_minutes":minutes,"recent_training_consistency":"consistent"},
        "season":{"start_date":"2026-09-07","end_date":"2026-09-27","current_weekly_endurance_minutes":minutes,"target_peak_weekly_endurance_minutes":270},
        "tests":{"resting_hr":58,"max_hr":177,"erg_2k_seconds":496,"multi_duration_power_tests":{}},
        "weekly_availability":[{"weekday":day,"available":day in available,"max_training_minutes":150 if day in {"saturday","sunday"} else 50,"rowing_modes":["erg"]} for day in DAYS],
        "preferences":preferences or {},"races":[],"recurring_activities":activities or [],
    }


def _activity(activity_id, activity_type, sessions, status, *, fixed=(), preferred=(), allowed=(), prohibited=(), rules=None):
    return {"activity_id":activity_id,"activity_type":activity_type,"sessions_per_week":sessions,"scheduling_status":status,"fixed_days":list(fixed),"preferred_days":list(preferred),"allowed_days":list(allowed),"prohibited_days":list(prohibited),"planner_may_choose_day":status=="flexible","same_day_rules":rules or {}}


def _plan(profile):
    config=json.load(open("config/defaults.json"))
    return generate_plan(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config))


def _week(plan):
    return [item for item in plan["sessions"] if "2026-09-07"<=item["date"]<="2026-09-13"]


def _calendar_day(plan, weekday):
    return next(item for item in plan["calendar_days"][:7] if item["date"] == (date(2026,9,7)+timedelta(days=DAYS.index(weekday))).isoformat())


def test_athlete_a_current_acceptance_case_is_deterministic_and_athlete_specific():
    activities=[
        _activity("private","private_coaching",1,"fixed",fixed=("wednesday",)),
        _activity("coach","coached_row",1,"flexible",allowed=("tuesday","thursday")),
        _activity("lift","strength",2,"preferred",preferred=("monday","friday"),prohibited=("wednesday",),rules={"rowing_allowed":False}),
        _activity("rest","rest",1,"flexible"),
    ]
    profile=_profile(preferences={"preferred_long_session_days":["sunday"]},activities=activities)
    first,second=_plan(profile),_plan(copy.deepcopy(profile))
    sessions=_week(first)
    assert [(item["date"],item["session_id"]) for item in sessions] == [(item["date"],item["session_id"]) for item in _week(second)]
    assert any(item["day"]=="Wednesday" and item["title"]=="Private coaching" for item in sessions)
    assert sum(item["title"]=="Coached row" for item in sessions)==1
    assert next(item for item in sessions if item["title"]=="Coached row")["day"] in {"Tuesday","Thursday"}
    assert any(item["day"]=="Sunday" and item.get("session_role")=="LONG_AEROBIC" for item in sessions)


def test_athlete_b_uses_saturday_long_day_without_sunday_or_mon_fri_assumptions():
    activities=[
        _activity("lift","strength",2,"preferred",preferred=("tuesday","saturday"),rules={"rowing_allowed":False}),
        _activity("rest","rest",1,"flexible"),
    ]
    plan=_plan(_profile(available=tuple(day for day in DAYS if day!="sunday"),preferences={"preferred_long_session_days":["saturday"]},activities=activities))
    sessions=_week(plan)
    assert not any(item["day"]=="Sunday" for item in sessions)
    assert any(item["day"]=="Saturday" and item.get("rowing_minutes",0) for item in sessions)
    assert all(item["day"]!="Saturday" for item in sessions if item["session_id"]=="LIFT")
    assert any(item["day"]=="Tuesday" for item in sessions if item["session_id"]=="LIFT")


def test_athlete_c_honors_friday_private_monday_wednesday_coaching_and_tuesday_rest():
    activities=[
        _activity("private","private_coaching",1,"fixed",fixed=("friday",)),
        _activity("coach","coached_row",1,"flexible",allowed=("monday","wednesday")),
        _activity("lift","strength",2,"flexible",prohibited=("friday",),rules={"rowing_allowed":False}),
        _activity("rest","rest",1,"fixed",fixed=("tuesday",)),
    ]
    plan=_plan(_profile(activities=activities)); sessions=_week(plan)
    assert any(item["day"]=="Friday" and item["title"]=="Private coaching" for item in sessions)
    coached=next(item for item in sessions if item["title"]=="Coached row")
    assert coached["day"] in {"Monday","Wednesday"}
    assert _calendar_day(plan,"tuesday")["state"]=="designated_rest"
    assert all(item["day"]!="Friday" for item in sessions if item["session_id"]=="LIFT")


def test_athlete_d_novice_three_day_week_keeps_lower_independent_frequency():
    activities=[_activity("rest","rest",1,"flexible")]
    plan=_plan(_profile(experience="novice",minutes=75,available=("monday","wednesday","saturday"),activities=activities))
    rows=[item for item in _week(plan) if item.get("rowing_minutes",0)]
    assert len(rows)<=2
    assert all(item["day"] in {"Monday","Wednesday","Saturday"} for item in rows)
    assert sum(item["state"]=="designated_rest" for item in plan["calendar_days"][:7])==1


def test_athlete_e_unavailable_sunday_uses_another_viable_long_session_day():
    activities=[_activity("rest","rest",1,"flexible"),_activity("lift","strength",2,"preferred",preferred=("monday","wednesday"),rules={"rowing_allowed":False})]
    profile=_profile(preferences={"preferred_long_session_days":["sunday"]},activities=activities)
    profile["weekly_availability"]=[{**item,"available":item["weekday"]!="sunday"} for item in profile["weekly_availability"]]
    plan=_plan(profile); sessions=_week(plan)
    long=next(item for item in sessions if item.get("session_role")=="LONG_AEROBIC")
    assert long["day"]=="Saturday"
    assert not any(item["day"]=="Sunday" for item in sessions)
