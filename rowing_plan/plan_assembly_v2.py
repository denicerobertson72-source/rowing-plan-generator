"""Pure V2 session/calendar assembly; persistence and routing stay outside."""
from __future__ import annotations
from dataclasses import asdict, is_dataclass
from datetime import date

from .materialization_v2 import materialize_v2_session
from .models import DateContext, V2PlanAssemblyResult, V2SessionMaterializationRequest
from .recurring_activities import schedule_signature

_ORDER={"RACE":0,"COURSE_PRACTICE":1,"COACHED":2,"LIFT":3}
_REQUIRED={"date","day","session_id","mode","title","band","total_cardio_minutes","phase","structure"}

def _calendar_days(calendar, rest_dates):
    result=[]
    for context in calendar:
        day=context.date
        rest=day in rest_dates
        commitments=tuple(context.fixed_commitments)
        state="designated_rest" if rest else "unavailable" if context.unavailable else "no_additional_session"
        result.append({"date":day.isoformat(),"designated_rest":rest,"unavailable":context.unavailable,"state":state,"commitments":list(commitments)})
    return result

def _sort_key(session):
    return (session["date"],_ORDER.get(session.get("session_id"),4),str(session.get("session_id","")),str(session.get("mode","")))

def _totals(sessions):
    byweek={}
    for session in sessions: byweek.setdefault(date.fromisoformat(session["date"]).isocalendar().week,[]).append(session)
    result=[]
    for week,items in sorted(byweek.items()):
        optional=sum(item.get("total_cardio_minutes",0) for item in items if item.get("optional_add_on"))
        required=sum(item.get("total_cardio_minutes",0) for item in items if item.get("required_cross_training"))
        result.append({"week":week,"cardio_minutes":sum(item.get("total_cardio_minutes",0) for item in items if not item.get("optional_add_on")),"rowing_minutes":sum(item.get("rowing_minutes",0) for item in items),"required_cross_training_minutes":required,"optional_add_on_minutes":optional,"strength_sessions":sum(item.get("session_id")=="LIFT" for item in items),"quality_sessions":sum(item.get("band") in {"TR","AN","PP"} for item in items)})
    return result

def _json(value):
    if is_dataclass(value): return _json(asdict(value))
    if isinstance(value,date): return value.isoformat()
    if isinstance(value,dict): return {str(key):_json(item) for key,item in sorted(value.items(),key=lambda pair:str(pair[0]))}
    if isinstance(value,(tuple,list)): return [_json(item) for item in value]
    return value

def build_plan_version_from_v2(*, profile: dict, bands, power: dict, calendar: tuple[DateContext,...], requests: tuple[V2SessionMaterializationRequest,...], rest_dates=(), authoritative_sessions=(), selector_histories=None, generated_at=None, v2_diagnostics=None, warnings=(), plan_impacts=()) -> V2PlanAssemblyResult:
    """Materialize resolved V2 facts into a PlanVersion-shaped dictionary.

    It never invokes placement, translation, finalization, persistence, or API
    code.  Totals are reporting values computed from final serialized sessions.
    """
    rest_dates=frozenset(rest_dates); valid_dates={item.date for item in calendar}
    if any(day not in valid_dates for day in rest_dates): return V2PlanAssemblyResult(False,failure_reason="rest_date_outside_calendar")
    histories=selector_histories or {}; materialized=[]
    for request in requests:
        if request.date not in valid_dates: return V2PlanAssemblyResult(False,failure_reason="session_date_outside_calendar",failure_diagnostics={"failure_stage":"plan_assembly","failure_origin":"materialization","placement_id":request.placement_id or None,"date":request.date.isoformat(),"role":request.role,"reason_code":"session_date_outside_calendar"})
        result=materialize_v2_session(request,profile=profile,bands=bands,power=power,selector_history=histories.get(request.placement_id,()))
        if not result.success:
            return V2PlanAssemblyResult(False,failure_reason=result.failure_reason,failure_diagnostics={"failure_stage":"plan_assembly","failure_origin":"materialization","placement_id":request.placement_id or None,"date":request.date.isoformat(),"role":request.role,"quality_type":getattr(request.concrete_quality,"quality_type",None),"reason_code":result.failure_reason})
        if result.calendar_only: continue
        if not _REQUIRED <= set(result.session): return V2PlanAssemblyResult(False,failure_reason="incomplete_session_shape")
        materialized.append(dict(result.session))
    if any(session["date"] in {day.isoformat() for day in rest_dates} for session in materialized): return V2PlanAssemblyResult(False,failure_reason="rest_session_conflict")
    # Match V1 lock restoration: an authoritative date replaces newly planned
    # work on that date and retains its serialized dictionary byte-for-byte.
    carried=[dict(item) for item in authoritative_sessions]
    if any(item.get("date") not in {day.isoformat() for day in valid_dates} for item in carried): return V2PlanAssemblyResult(False,failure_reason="authoritative_date_outside_calendar")
    carried_dates={item["date"] for item in carried}
    sessions=[item for item in materialized if item["date"] not in carried_dates]+carried
    keys=[(item["date"],item["session_id"],item["mode"]) for item in sessions]
    if len(keys)!=len(set(keys)): return V2PlanAssemblyResult(False,failure_reason="duplicate_external_session_key")
    sessions=sorted(sessions,key=_sort_key)
    plan={"plan_version":"0.7.0","profile_id":profile.get("athlete",{}).get("display_name","athlete"),"generated_at":generated_at or date.today().isoformat(),"schedule_signature":schedule_signature(profile),"intensity_profile":_json(bands),"power_profile":_json(power),"sessions":sessions,"calendar_days":_calendar_days(calendar,rest_dates),"phases":[{"date":item.date.isoformat(),"phase":item.phase_id} for item in calendar],"weekly_totals":_totals(sessions),"warnings":_json(warnings),"plan_impacts":_json(plan_impacts),"v2_diagnostics":_json(v2_diagnostics or {"scheduler_version":"v2","demand_satisfaction":[],"rolling_targets":[],"repair_changes":[],"target_consequences":[],"overrides":[]})}
    return V2PlanAssemblyResult(True,plan)
