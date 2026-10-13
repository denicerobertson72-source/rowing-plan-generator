from datetime import date

import pytest

from rowing_plan.intensity import build_intensity_profile
import rowing_plan.planner_v2 as planner_v2
from rowing_plan.planner_v2 import generate_plan_v2, validate_v2_plan_contract_details
from rowing_plan.power_profile import build_power_profile
from services.api.app import main
from services.api.tests.disposable_browser_fixture import synthetic_profile


def _profile():
    profile=synthetic_profile()
    profile["season"]={**profile["season"],"start_date":"2026-10-16","end_date":"2026-10-23"}
    profile["recurring_activities"]=[{
        "activity_id":"lift", "activity_type":"strength", "sessions_per_week":1,
        "scheduling_status":"preferred", "fixed_days":[], "preferred_days":["monday"],
        "allowed_days":["monday", "wednesday"], "prohibited_days":[],
    }]
    profile["races"]=[{
        "event_name":"Saturday head race", "start_date":"2026-10-16", "end_date":"2026-10-17",
        "race_dates":["2026-10-17"], "priority":"C", "race_type":"head_5k",
        "practice_sessions":[{"date":"2026-10-16","title":"Course familiarization","duration_minutes":30}],
    }]
    return profile


def _strength(day="2026-10-18"):
    return {
        "date":day, "day":"Sunday", "phase":"post_race_recovery", "fixed":True,
        "mode":"strength", "session_id":"LIFT", "title":"Heavy lifting", "band":"strength",
        "total_cardio_minutes":0, "rowing_minutes":0, "quality_minutes":0,
        "total_training_minutes":45, "structure":"Authoritative athlete strength session.",
        "source_id":"athlete-user-fixed-strength",
    }


def _plan(profile, *, locked=(), prior=None, planning_date=None, actuals=None):
    bands=build_intensity_profile(profile,main.CONFIG)
    power=build_power_profile(profile,main.CONFIG)
    return main.execute_selected_planner(
        choice=main.PlannerChoice.V2, profile=profile, bands=bands, power=power,
        locked_sessions=list(locked), prior_plan=prior, planning_date=planning_date,
        history_actuals=actuals or {},
    )


def test_optional_actual_history_is_not_misclassified_as_authoritative_planned_session():
    profile=_profile()
    bands=build_intensity_profile(profile,main.CONFIG)
    power=build_power_profile(profile,main.CONFIG)
    prior=generate_plan_v2(profile,main.CONFIG,bands,power)
    key="actual:2026-10-16:erg"
    actual={"status":"completed","actual_duration_min":20,"actual_intensity":"UT2"}
    history_only=main.optional_activity_history_session(key,actual)
    plan=_plan(profile,locked=(history_only,),prior=prior,planning_date=date(2026,10,18),actuals={key:actual})
    assert not any(item.get("session_id")=="ACTUAL_ERG" for item in plan["sessions"])
    assert plan["v2_diagnostics"]["historical_preserved"]["completed_history_count"]==1


def test_post_race_authoritative_strength_is_preserved_while_automatic_strength_remains_blocked():
    profile=_profile()
    authoritative=_strength()
    plan=_plan(profile,locked=(authoritative,),planning_date=date(2026,10,18))
    sunday=[item for item in plan["sessions"] if item["date"]=="2026-10-18"]
    assert sunday==[authoritative]
    assert validate_v2_plan_contract_details(plan,authoritative_sessions=(authoritative,)) is None
    assert [item["session_id"] for item in plan["sessions"] if item["date"]=="2026-10-16"]==["COURSE_PRACTICE"]
    assert [item["session_id"] for item in plan["sessions"] if item["date"]=="2026-10-17"]==["RACE"]


def test_historical_serialized_session_is_preserved_and_contract_diagnostic_names_a_real_missing_authority(monkeypatch):
    profile=_profile()
    historical=_strength("2026-10-16")
    historical={**historical,"day":"Friday","session_id":"old-strength","title":"Historical fixed strength"}
    bands=build_intensity_profile(profile,main.CONFIG)
    power=build_power_profile(profile,main.CONFIG)
    prior=generate_plan_v2(profile,main.CONFIG,bands,power)
    prior={**prior,"sessions":[*prior["sessions"],historical]}
    plan=_plan(profile,prior=prior,planning_date=date(2026,10,18))
    assert next(item for item in plan["sessions"] if item["session_id"]=="old-strength")==historical
    missing={**historical,"session_id":"missing-authority"}
    detail=validate_v2_plan_contract_details(plan,authoritative_sessions=(missing,))
    assert detail=={
        "reason_code":"authoritative_session_not_preserved",
        "authority_type":"serialized_locked_or_historical_session",
        "source_session_identity":{"date":"2026-10-16","session_id":"missing-authority","mode":"strength","source_id":"athlete-user-fixed-strength"},
        "source_date":"2026-10-16",
        "expected_date":"2026-10-16",
        "preservation_status":"omitted",
        "changed_fields":(),
    }
    # The API boundary enriches the same deterministic validator detail with
    # planning-date and recovery context for production diagnosis.
    monkeypatch.setattr(planner_v2,"generate_plan_v2",lambda *_args,**_kwargs:plan)
    with pytest.raises(main.PlannerRoutingError) as error:
        _plan(profile,locked=(missing,),planning_date=date(2026,10,18))
    assert error.value.reason_code=="v2_plan_contract_failed"
    assert error.value.diagnostics["source_session_identity"]["session_id"]=="missing-authority"
    assert error.value.diagnostics["authority_type"]=="serialized_locked_session"
    assert error.value.diagnostics["date_relation"]=="historical"
    assert error.value.diagnostics["failure_stage"]=="validation"


def test_fixed_recurring_sunday_commitment_remains_calendar_authority_and_cannot_create_automatic_strength():
    profile=_profile()
    profile["recurring_activities"]=[{
        "activity_id":"fixed-lift", "activity_type":"strength", "sessions_per_week":1,
        "scheduling_status":"fixed", "fixed_days":["sunday"], "preferred_days":[],
        "allowed_days":[], "prohibited_days":[],
    }]
    bands=build_intensity_profile(profile,main.CONFIG)
    power=build_power_profile(profile,main.CONFIG)
    plan=generate_plan_v2(profile,main.CONFIG,bands,power,planning_date=date(2026,10,18))
    sunday=next(item for item in plan["calendar_days"] if item["date"]=="2026-10-18")
    assert {item["type"] for item in sunday["commitments"]}>={"strength"}
    assert not [item for item in plan["sessions"] if item["date"]=="2026-10-18" and item["session_id"]=="LIFT"]
