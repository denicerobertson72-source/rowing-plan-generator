from datetime import date

from services.api.app.main import CONFIG, optional_activity_history_session
from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.models import WindowPlacement
from rowing_plan.planner_v2 import generate_plan_v2
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler_v2 import initialize_active_window_state, release_window_placement


AS_OF=date(2026,10,12)


def _inputs():
    profile=synthetic_profile()
    return profile,build_intensity_profile(profile,CONFIG),build_power_profile(profile,CONFIG)


def _calendar_day(day, state="no_additional_session"):
    return {"date":day,"designated_rest":state=="designated_rest","unavailable":False,"state":state,"commitments":[]}


def _session(day, session_id="old-row"):
    return {"date":day,"day":"Saturday","phase":"race_specific_preparation","fixed":False,"mode":"erg","session_id":session_id,"title":"Historical prescription","band":"UT2","total_cardio_minutes":45,"rowing_minutes":45,"quality_minutes":0,"structure":"Historical prescription."}


def _regenerate(prior, *, locked=(), actuals=None):
    profile,bands,power=_inputs()
    return generate_plan_v2(profile,CONFIG,bands,power,list(locked),prior_plan=prior,history_actuals=actuals or {},planning_date=AS_OF)


def test_past_off_and_optional_actual_remain_auditable_without_becoming_planned_work():
    key="actual:2026-10-11:erg"
    prior={"calendar_days":[_calendar_day("2026-10-11","designated_rest")],"sessions":[]}
    lock=optional_activity_history_session(key,{"actual_duration_min":20,"actual_intensity":"UT2"})
    plan=_regenerate(prior,locked=(lock,),actuals={key:{"status":"completed","actual_duration_min":20,"actual_intensity":"UT2"}})
    day=next(item for item in plan["calendar_days"] if item["date"]=="2026-10-11")
    assert day==prior["calendar_days"][0]
    assert not [item for item in plan["sessions"] if item["date"]=="2026-10-11"]
    assert plan["v2_diagnostics"]["historical_preserved"]["completed_history_count"]==1


def test_past_prescriptions_are_preserved_verbatim_but_only_completed_work_becomes_history_credit():
    completed=_session("2026-10-10","completed-row")
    uncompleted=_session("2026-10-11","uncompleted-row")
    prior={"calendar_days":[_calendar_day("2026-10-10"),_calendar_day("2026-10-11")],"sessions":[completed,uncompleted]}
    key="2026-10-10:completed-row:erg"
    plan=_regenerate(prior,locked=(completed,),actuals={key:{"status":"completed","actual_duration_min":40,"actual_intensity":"UT2"}})
    sessions={(item["date"],item["session_id"]):item for item in plan["sessions"]}
    assert sessions[("2026-10-10","completed-row")]==completed
    assert sessions[("2026-10-11","uncompleted-row")]==uncompleted
    assert plan["v2_diagnostics"]["historical_preserved"]["completed_history_count"]==1


def test_future_provisional_is_not_carried_as_history_and_user_fixed_primitive_remains_immutable():
    future=_session("2026-10-13","provisional-old-row")
    plan=_regenerate({"calendar_days":[],"sessions":[future]})
    assert not any(item.get("session_id")=="provisional-old-row" for item in plan["sessions"])
    profile,_,_=_inputs()
    from rowing_plan.scheduler_v2 import build_v2_season_calendar
    calendar=build_v2_season_calendar(profile)
    state=initialize_active_window_state(calendar,date(2026,10,12),date(2026,10,25),provisional_overlap=(WindowPlacement("fixed-future",date(2026,10,13),"strength","strength",45,False,(),True,date(2026,10,11),"move-1"),))
    try:
        release_window_placement(state,"fixed-future")
    except ValueError as error:
        assert str(error)=="user_fixed_placement"
    else:
        raise AssertionError("user-fixed future placement became releasable")
