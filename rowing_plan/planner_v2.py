"""Complete in-memory Scheduler V2 orchestration.

This module deliberately composes the already validated V2 primitives.  It
contains neither API routing nor persistence.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date
from time import perf_counter
from typing import Any

from .models import QualityTranslationContext, TargetCredit, V2SessionMaterializationRequest, WindowPlacement
from .periodization import build_season_phases, race_dates
from .plan_assembly_v2 import build_plan_version_from_v2
from .scheduler_v2 import (
    build_dated_training_roles,
    build_v2_season_calendar,
    finalize_translated_quality_sequence,
    generate_v2_demand_plan,
    solve_v2_rolling_non_rowing,
    translate_quality_roles,
)

logger = logging.getLogger(__name__)

_SERIALIZED_SESSION_REQUIRED = frozenset({"date", "day", "session_id", "mode", "title", "band", "total_cardio_minutes", "phase", "structure"})


def _log_generation_timing(stage: str, started: float) -> None:
    """Emit stage timing without adding nondeterministic data to PlanVersion."""

    logger.info("planner_generation_timing planner=v2 stage=%s duration_ms=%.3f", stage, (perf_counter() - started) * 1000)


class V2PlanningError(RuntimeError):
    """Expected, safe-to-report V2 execution failure with a stable category."""

    def __init__(self, reason_code: str, detail: str | None = None, diagnostics: dict[str, Any] | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail or reason_code
        self.diagnostics = dict(diagnostics or {})


def _concrete_failure_diagnostics(*, failure_stage: str, failure_origin: str, reason_code: str, placement_id: str | None = None, day: date | None = None, role: str | None = None, quality_type: str | None = None) -> dict[str, Any]:
    """Return JSON-safe, non-sensitive V2 concrete-workout failure context."""

    return {
        "failure_stage": failure_stage,
        "failure_origin": failure_origin,
        "placement_id": placement_id,
        "date": day.isoformat() if day else None,
        "role": role,
        "quality_type": quality_type,
        "reason_code": reason_code,
    }


def _context_minutes(context, activity_type: str, fallback: int) -> int:
    return next((int(item.get("minutes", fallback)) for item in context.fixed_commitments if item.get("type") == activity_type), fallback)


def _fixed_private_requests(profile: dict, calendar) -> tuple[V2SessionMaterializationRequest, ...]:
    """Materialize the calendar's existing fixed private-coaching facts once."""

    activities = tuple(item for item in profile.get("recurring_activities", ()) if item.get("activity_type") == "private_coaching" and item.get("scheduling_status") == "fixed")
    requests = []
    for context in calendar:
        for index, activity in enumerate(activities):
            if context.weekday not in activity.get("fixed_days", ()):
                continue
            minutes = _context_minutes(context, "private_coaching", int(activity.get("duration_minutes", 50)))
            requests.append(V2SessionMaterializationRequest("private_coaching", context.date, minutes, context.phase_id, placement_id=f"private:{activity.get('activity_id', index)}:{context.date.isoformat()}", source_id=str(activity.get("activity_id", "private_coaching")), fixed=True))
    return tuple(requests)


def _race_requests(profile: dict, calendar) -> tuple[V2SessionMaterializationRequest, ...]:
    valid_dates = {context.date: context for context in calendar}
    requests = []
    for race_index, race in enumerate(profile.get("races", ())):
        for day in race_dates(race):
            context = valid_dates.get(day)
            if context:
                requests.append(V2SessionMaterializationRequest("race", day, 20, context.phase_id, placement_id=f"race:{race_index}:{day.isoformat()}", source_id=f"race:{race_index}", fixed=True, fact=race))
        for practice_index, practice in enumerate(race.get("practice_sessions", ())):
            try:
                day = date.fromisoformat(practice["date"])
            except (KeyError, TypeError, ValueError):
                continue
            context = valid_dates.get(day)
            if context:
                requests.append(V2SessionMaterializationRequest("course_practice", day, int(practice.get("duration_minutes", 30)), context.phase_id, placement_id=f"practice:{race_index}:{practice_index}:{day.isoformat()}", source_id=f"practice:{race_index}:{practice_index}", fixed=True, fact=practice))
    return tuple(requests)


def _quality_requests(profile: dict, roles, phase_types: dict[str, str]) -> tuple[V2SessionMaterializationRequest, ...]:
    quality_roles = tuple(item for item in roles if item.role == "quality")
    contexts = {item.placement_id: QualityTranslationContext(phase_types[item.phase_id]) for item in quality_roles}
    try:
        translated = translate_quality_roles(quality_roles, contexts)
    except ValueError as error:
        raise V2PlanningError("v2_concrete_workout_failed", str(error), _concrete_failure_diagnostics(failure_stage="quality_translation", failure_origin="translation", reason_code=str(error), role="quality")) from error
    experience = profile.get("athlete", {}).get("experience_level", "experienced")
    preference = profile.get("preferences", {}).get("workout_structure_preference", "varied")
    finalized = finalize_translated_quality_sequence(translated, experience=experience, preference=preference)
    if not finalized.success:
        raise V2PlanningError(
            "v2_concrete_workout_failed",
            finalized.failure_reason,
            _concrete_failure_diagnostics(
                failure_stage="quality_finalization",
                failure_origin=finalized.failure_stage or "finalization",
                reason_code=finalized.failure_reason or "unknown_quality_failure",
                placement_id=finalized.failed_placement_id,
                day=finalized.failed_date,
                role="quality",
                quality_type=finalized.failed_quality_type,
            ),
        )
    by_id = {item.placement_id: item for item in finalized.roles}
    return tuple(V2SessionMaterializationRequest("quality", item.date, item.duration_minutes, item.phase_id, placement_id=item.placement_id, source_id=item.source_id, concrete_quality=by_id[item.placement_id]) for item in quality_roles)


def _routing_diagnostics(state, diagnostics) -> dict[str, Any]:
    placements = (*state.frozen_placements, *state.provisional_placements)
    return {
        "scheduler_version": "v2",
        "demand_satisfaction": tuple(state.demand_satisfaction.values()) if state.demand_satisfaction else (),
        "rolling_targets": diagnostics,
        "repair_changes": (),
        "target_consequences": (),
        "overrides": (),
        # This is the persisted authority signal consumed by V2.8B fallback
        # eligibility; ordinary generation contains no user-authored records.
        "placement_authority": tuple({"placement_id": item.placement_id, "date": item.date, "user_fixed": item.user_fixed, "original_date": item.original_date, "override_id": item.override_id} for item in placements),
    }


def _historical_snapshot(prior_plan: dict | None, planning_date: date | None) -> tuple[dict[str, dict], tuple[dict, ...]]:
    """Return immutable serialized history strictly before the planning date."""
    if not prior_plan or planning_date is None:
        return {}, ()
    calendar={item.get("date"):dict(item) for item in prior_plan.get("calendar_days",()) if isinstance(item,dict) and isinstance(item.get("date"),str) and item["date"] < planning_date.isoformat()}
    sessions=tuple(dict(item) for item in prior_plan.get("sessions",()) if isinstance(item,dict) and isinstance(item.get("date"),str) and item["date"] < planning_date.isoformat())
    return calendar, sessions


def _history_role(session: dict) -> str | None:
    role=str(session.get("session_role") or "").upper()
    if session.get("session_id")=="LIFT" or session.get("mode")=="strength": return "strength"
    if role=="LONG_AEROBIC": return "long_aerobic"
    if role=="AEROBIC_STRENGTH": return "ut1_aerobic_strength"
    if role in {"THRESHOLD","RACE_PACE","SPRINT_POWER"} or session.get("band") in {"AT","TR","AN","PP"}: return "quality"
    if session.get("session_id")=="COACHED" or session.get("coached"): return "coached_training"
    if session.get("mode") in {"erg","on_water"}: return "dedicated_ut2"
    return None


def _completed_history_placements(*, historical_sessions: tuple[dict, ...], locked_sessions: tuple[dict, ...], actuals: dict[str, dict] | None, demands, calendar, planning_date: date | None) -> tuple[WindowPlacement, ...]:
    """Create frozen physiological context only for completed actuals.

    A prior prescription alone is deliberately not credited.  This keeps audit
    history separate from what the athlete actually completed.
    """
    if planning_date is None or not actuals:
        return ()
    sessions={f'{item.get("date")}:{item.get("session_id")}:{item.get("mode")}':item for item in historical_sessions}
    sessions.update({str(item.get("source_id")):item for item in locked_sessions if item.get("source_id")})
    contexts={item.date:item for item in calendar}
    placements=[]
    for key,payload in sorted(actuals.items()):
        if payload.get("status")!="completed": continue
        session=sessions.get(key)
        if not session: continue
        try: day=date.fromisoformat(str(session.get("date")))
        except (TypeError,ValueError): continue
        if day>=planning_date: continue
        role=_history_role(session)
        if role is None: continue
        minutes=int(payload.get("actual_duration_min") or session.get("total_training_minutes") or session.get("total_cardio_minutes") or 0)
        credits=[]; context=contexts.get(day)
        if role in {"dedicated_ut2","long_aerobic","ut1_aerobic_strength","quality"} and context:
            for target in demands.rowing_dose_targets:
                if target.phase_id==context.phase_id and target.category==role and target.window_start<=day<=target.window_end:
                    credits.append(TargetCredit(f"{target.phase_id}:{target.category}",target.category,1,minutes))
                    if role=="long_aerobic":
                        ut2=next((item for item in demands.rowing_dose_targets if item.phase_id==target.phase_id and item.category=="dedicated_ut2"),None)
                        if ut2: credits.append(TargetCredit(f"{ut2.phase_id}:dedicated_ut2","dedicated_ut2",1,minutes))
        placements.append(WindowPlacement(f"completed:{key}",day,role,key,minutes,True,tuple(credits)))
    return tuple(placements)


def generate_plan_v2(profile: dict, config: dict, bands, power: dict, locked_sessions: list[dict] | None = None, *, prior_plan: dict | None = None, history_actuals: dict[str, dict] | None = None, planning_date: date | None = None) -> dict:
    """Build a complete V2 PlanVersion in memory, without saving it.

    The argument shape intentionally matches V1 ``generate_plan`` at the API
    routing seam.  ``config`` is retained for that shared contract; existing
    V2 primitives own their validated planning parameters.
    """

    del config  # V2's validated primitives currently have no additional config input.
    locked = tuple(dict(item) for item in (locked_sessions or ()))
    try:
        orchestration_started = perf_counter()
        phases = build_season_phases(profile)
        demands = generate_v2_demand_plan(profile, phases)
        historical_calendar, historical_sessions = _historical_snapshot(prior_plan, planning_date)
        calendar = build_v2_season_calendar(profile, phases, locked_sessions=list(locked), historical_calendar_days=historical_calendar)
        completed_history=_completed_history_placements(historical_sessions=historical_sessions,locked_sessions=locked,actuals=history_actuals,demands=demands,calendar=calendar,planning_date=planning_date)
        _log_generation_timing("input_orchestration", orchestration_started)
        solver_started = perf_counter()
        preserving_history=bool(historical_calendar)
        solver_start=planning_date if preserving_history and calendar[0].date<=planning_date<=calendar[-1].date else None
        state, diagnostics = solve_v2_rolling_non_rowing(profile, demands, calendar, historical_placements=completed_history, start_date=solver_start)
        _log_generation_timing("solver", solver_started)
        # The rolling solver retains full-season frozen placement history but
        # its final active context is only the closing window. Expand that
        # immutable context for the existing final-state role adapter.
        full_state = replace(state, fixed_context={item.date: item for item in calendar})
        roles = build_dated_training_roles(full_state)
        rest_dates = tuple(item.date for item in roles if item.role == "rest")
        ordinary = tuple(V2SessionMaterializationRequest(item.role, item.date, item.duration_minutes, item.phase_id, placement_id=item.placement_id, source_id=item.source_id) for item in roles if item.role not in {"rest", "quality"})
        phase_types = {item["phase_id"]: item["phase_type"] for item in phases}
        quality_started = perf_counter()
        active_requests=ordinary + _quality_requests(profile, roles, phase_types) + _fixed_private_requests(profile, calendar) + _race_requests(profile, calendar)
        requests=tuple(item for item in active_requests if not preserving_history or item.date>=planning_date)
        _log_generation_timing("quality_translation_finalization", quality_started)
        materialization_started = perf_counter()
        carryable_locked=tuple(item for item in locked if {"date","day","session_id","mode","title","band","total_cardio_minutes","phase","structure"} <= set(item))
        carried_by_key={(item["date"],item["session_id"],item["mode"]):item for item in (*historical_sessions,*carryable_locked)}
        diagnostics_payload=_routing_diagnostics(full_state, diagnostics)
        diagnostics_payload["historical_preserved"]={"calendar_dates":tuple(sorted(historical_calendar)),"session_count":len(historical_sessions),"completed_history_count":len(completed_history),"planning_date":planning_date.isoformat() if planning_date else None}
        diagnostics_payload["post_race_recovery_dates"]=tuple(item.date.isoformat() for item in calendar if item.post_race_recovery)
        result = build_plan_version_from_v2(profile=profile, bands=bands, power=power, calendar=calendar, requests=requests, rest_dates=rest_dates, authoritative_sessions=tuple(carried_by_key.values()), historical_calendar_days=historical_calendar, v2_diagnostics=diagnostics_payload)
        _log_generation_timing("materialization", materialization_started)
    except V2PlanningError:
        raise
    except ValueError as error:
        raise V2PlanningError("v2_schedule_infeasible", str(error)) from error
    if not result.success:
        reason = result.failure_reason or "unknown_materialization_failure"
        category = "v2_materialization_failed" if reason not in {"incomplete_session_shape", "duplicate_external_session_key", "rest_session_conflict"} else "v2_plan_contract_failed"
        diagnostics = dict(result.failure_diagnostics)
        if not diagnostics:
            diagnostics = _concrete_failure_diagnostics(failure_stage="plan_assembly", failure_origin="materialization", reason_code=reason)
        raise V2PlanningError(category, reason, diagnostics)
    return dict(result.plan)


def _serialized_authoritative_sessions(authoritative_sessions) -> tuple[dict, ...]:
    """Return planned-session authorities, excluding history-only inputs.

    ``locked_sessions`` is also the internal transport for completed actuals
    and optional off-day activity history.  Those records constrain physiology
    but are not prescribed sessions and must never be required in the emitted
    plan calendar.  The assembly layer already makes this exact distinction.
    """

    return tuple(dict(item) for item in authoritative_sessions if isinstance(item, dict) and _SERIALIZED_SESSION_REQUIRED <= set(item))


def _authoritative_session_detail(expected: dict, actual: dict | None) -> dict:
    key = (expected.get("date"), expected.get("session_id"), expected.get("mode"))
    changed = () if actual is None else tuple(sorted(key for key in set(expected) | set(actual) if actual.get(key) != expected.get(key)))
    return {
        "reason_code": "authoritative_session_not_preserved",
        "authority_type": "serialized_locked_or_historical_session",
        "source_session_identity": {"date": key[0], "session_id": key[1], "mode": key[2], "source_id": expected.get("source_id")},
        "source_date": key[0],
        "expected_date": key[0],
        "preservation_status": "omitted" if actual is None else "changed",
        "changed_fields": changed,
    }


def validate_v2_plan_contract_details(plan: dict, *, authoritative_sessions=()) -> dict | None:
    """Return a stable diagnostic for an external PlanVersion contract failure."""

    try:
        json.dumps(plan)
    except (TypeError, ValueError):
        return {"reason_code": "not_json_serializable"}
    required = {"plan_version", "profile_id", "generated_at", "intensity_profile", "power_profile", "sessions", "calendar_days", "phases", "weekly_totals", "v2_diagnostics"}
    if not required <= set(plan) or not isinstance(plan.get("sessions"), list) or not isinstance(plan.get("calendar_days"), list):
        return {"reason_code": "missing_plan_structure"}
    session_required = {"date", "day", "session_id", "mode", "title", "band", "total_cardio_minutes", "phase", "structure"}
    sessions = plan["sessions"]
    if any(not session_required <= set(item) for item in sessions):
        return {"reason_code": "incomplete_session_shape"}
    keys = [(item["date"], item["session_id"], item["mode"]) for item in sessions]
    if len(keys) != len(set(keys)):
        return {"reason_code": "duplicate_external_session_key"}
    for item in sessions:
        if item.get("band") in {"AT", "TR", "AN", "PP"} and (not item.get("session_fingerprint") or not item.get("structure") or "v2_finalized_quality" not in item.get("selection_reason_codes", ())):
            return {"reason_code": "unfinalized_quality_session"}
    rest_dates = {item["date"] for item in plan["calendar_days"] if item.get("designated_rest")}
    if any(item["date"] in rest_dates for item in sessions):
        return {"reason_code": "rest_session_conflict"}
    for calendar_day in plan["calendar_days"]:
        if not {"date", "designated_rest", "unavailable", "state", "commitments"} <= set(calendar_day):
            return {"reason_code": "incomplete_calendar_shape"}
        commitments = calendar_day.get("commitments", ())
        if any(item.get("type") == "race" for item in commitments) and not any(session["date"] == calendar_day["date"] and session["session_id"] == "RACE" for session in sessions):
            return {"reason_code": "race_commitment_not_represented", "date": calendar_day["date"], "authority_type": "fixed_race_commitment"}
    authoritative = {(item.get("date"), item.get("session_id"), item.get("mode")): item for item in _serialized_authoritative_sessions(authoritative_sessions)}
    serialized = {(item.get("date"), item.get("session_id"), item.get("mode")): item for item in sessions}
    for key, item in authoritative.items():
        if serialized.get(key) != item:
            return _authoritative_session_detail(item, serialized.get(key))
    return None


def validate_v2_plan_contract(plan: dict, *, authoritative_sessions=()) -> str | None:
    """Validate the external PlanVersion shape, not scheduling physiology."""

    detail = validate_v2_plan_contract_details(plan, authoritative_sessions=authoritative_sessions)
    return detail["reason_code"] if detail else None
