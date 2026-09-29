"""Pure V2 session/calendar assembly; persistence and routing stay outside."""
from __future__ import annotations

from .materialization_v2 import materialize_v2_session
from .models import DateContext, V2PlanAssemblyResult, V2SessionMaterializationRequest

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

def build_plan_version_from_v2(*, profile: dict, bands, power: dict, calendar: tuple[DateContext,...], requests: tuple[V2SessionMaterializationRequest,...], rest_dates=(), authoritative_sessions=(), selector_histories=None) -> V2PlanAssemblyResult:
    """Materialize resolved V2 facts into a PlanVersion-shaped dictionary.

    This intentionally omits totals and V2 diagnostic serialization; it never
    invokes placement, translation, finalization, persistence, or API code.
    """
    rest_dates=frozenset(rest_dates); valid_dates={item.date for item in calendar}
    if any(day not in valid_dates for day in rest_dates): return V2PlanAssemblyResult(False,failure_reason="rest_date_outside_calendar")
    histories=selector_histories or {}; materialized=[]
    for request in requests:
        if request.date not in valid_dates: return V2PlanAssemblyResult(False,failure_reason="session_date_outside_calendar")
        result=materialize_v2_session(request,profile=profile,bands=bands,power=power,selector_history=histories.get(request.placement_id,()))
        if not result.success: return V2PlanAssemblyResult(False,failure_reason=result.failure_reason)
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
    plan={"plan_version":"0.7.0","sessions":sessions,"calendar_days":_calendar_days(calendar,rest_dates),"phases":[{"date":item.date.isoformat(),"phase":item.phase_id} for item in calendar]}
    return V2PlanAssemblyResult(True,plan)
