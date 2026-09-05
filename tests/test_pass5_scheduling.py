"""Corrective Pass 5: flexible scheduling and Sunday long-row regressions."""
from __future__ import annotations

import copy
import json
from datetime import date, timedelta

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.schedule_scoring import choose
from rowing_plan.scheduler import generate_plan
from services.api.tests.disposable_browser_fixture import exported_runtime_profile


def _config():
    return json.load(open("config/defaults.json"))


def _profile(alternate="off"):
    profile=exported_runtime_profile()
    profile["season"].update(start_date="2026-09-07", end_date="2026-10-18")
    profile["preferences"].update(preferred_long_session_days=["sunday"])
    profile["weekly_availability"]=[{**item,"max_training_minutes":150 if item["weekday"]=="sunday" else 45} for item in profile["weekly_availability"]]
    profile["recurring_activities"]=[
        {"activity_id":"lift","activity_type":"strength","sessions_per_week":2,"scheduling_status":"preferred","fixed_days":[],"preferred_days":["monday","friday"],"allowed_days":[],"prohibited_days":["wednesday"],"planner_may_choose_day":True,"same_day_rules":{"rowing_allowed":False},"alternate_cardio":{"mode":alternate,"max_minutes":20}},
        {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":["tuesday","thursday"],"prohibited_days":[]},
        {"activity_id":"rest","activity_type":"rest","sessions_per_week":1,"scheduling_status":"flexible","fixed_days":[],"preferred_days":[],"allowed_days":[],"prohibited_days":[],"planner_may_choose_day":True},
    ]
    return profile


def _plan(profile):
    config=_config()
    return generate_plan(profile,config,build_intensity_profile(profile,config),build_power_profile(profile,config))


def _week(plan, start):
    end=(date.fromisoformat(start)+timedelta(days=6)).isoformat()
    return [item for item in plan["sessions"] if start<=item["date"]<=end]


def test_flexible_cards_without_explicit_allowed_days_use_available_weekdays_not_preferred_only():
    placement=choose({"sessions_per_week":1,"scheduling_status":"preferred","preferred_days":["monday"],"allowed_days":[],"prohibited_days":["wednesday"]},{"monday"},set(),available_days=["monday","tuesday","thursday"])
    assert placement["scheduled_days"]==["tuesday"]


def test_six_week_disposable_schedule_protects_sunday_for_substantive_required_rowing():
    plan=_plan(_profile())
    intents={item["week_start"]:item for item in plan["weekly_training_intents"]}
    for week_start in ("2026-09-07","2026-09-14","2026-09-21","2026-09-28","2026-10-05"):
        items=_week(plan,week_start)
        sunday=[item for item in items if item["day"]=="Sunday" and item.get("rowing_minutes",0)]
        rows=[item for item in items if item.get("rowing_minutes",0)]
        independent=[item for item in rows if item["session_id"]!="COACHED"]
        assert len(sunday)==1 and sunday[0].get("session_role") != "RECOVERY"
        assert len(independent)==intents[week_start]["target_independent_rowing_exposures"]
        assert sum(item["session_id"]=="LIFT" for item in items)==2
        assert sum(item["title"]=="Private coaching" for item in items)==1
        assert sum(item["title"]=="Coached row" for item in items)==1
        days=[item for item in plan["calendar_days"] if week_start<=item["date"]<=(date.fromisoformat(week_start)+timedelta(days=6)).isoformat()]
        assert sum(item["state"]=="designated_rest" for item in days)==1


def test_sunday_is_a_preference_not_a_hard_rule_and_race_week_can_override_it():
    profile=_profile(); profile["races"]=[{"event_name":"Sunday race","start_date":"2026-09-13","end_date":"2026-09-13","priority":"B","race_type":"head_5k"}]
    plan=_plan(profile)
    sunday=[item for item in _week(plan,"2026-09-07") if item["day"]=="Sunday"]
    assert sunday and sunday[0]["session_id"]=="RACE"


def test_optional_and_off_alternate_cardio_are_separate_from_required_core_volume():
    off=_plan(_profile("off")); optional=_plan(_profile("optional"))
    assert not any(item["session_id"]=="XL-UT2-01" for item in off["sessions"])
    optional_sessions=[item for item in optional["sessions"] if item["session_id"]=="XL-UT2-01"]
    assert optional_sessions and all(item.get("optional_add_on") and not item.get("required_cross_training") for item in optional_sessions)
    first_week=next(item for item in optional["weekly_totals"] if item["week"]==37)
    assert first_week["optional_add_on_minutes"]>0
    assert first_week["cardio_minutes"] == first_week["rowing_minutes"]


def test_day_specific_time_gives_sunday_long_row_more_room_than_workdays():
    plan=_plan(_profile())
    sunday=[item for item in _week(plan,"2026-09-07") if item["day"]=="Sunday" and item.get("session_role")=="LONG_AEROBIC"][0]
    assert sunday["total_cardio_minutes"]>45


def test_preferred_strength_days_can_move_when_they_conflict_with_fixed_commitments():
    placement=choose({"activity_type":"strength","sessions_per_week":2,"scheduling_status":"preferred","preferred_days":["monday","friday"],"allowed_days":[],"prohibited_days":["wednesday"]},{"tuesday"},{"monday","friday"},unavailable_days={"monday","friday"},available_days=["monday","tuesday","thursday","friday","saturday"])
    assert placement["scheduled_days"]==["thursday","saturday"]
