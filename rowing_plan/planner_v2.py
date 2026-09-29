"""Complete in-memory Scheduler V2 orchestration.

This module deliberately composes the already validated V2 primitives.  It
contains neither API routing nor persistence.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from typing import Any

from .models import QualityTranslationContext, V2SessionMaterializationRequest
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


class V2PlanningError(RuntimeError):
    """Expected, safe-to-report V2 execution failure with a stable category."""

    def __init__(self, reason_code: str, detail: str | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail or reason_code


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
        raise V2PlanningError("v2_concrete_workout_failed", str(error)) from error
    experience = profile.get("athlete", {}).get("experience_level", "experienced")
    preference = profile.get("preferences", {}).get("workout_structure_preference", "varied")
    finalized = finalize_translated_quality_sequence(translated, experience=experience, preference=preference)
    if not finalized.success:
        raise V2PlanningError("v2_concrete_workout_failed", finalized.failure_reason)
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


def generate_plan_v2(profile: dict, config: dict, bands, power: dict, locked_sessions: list[dict] | None = None) -> dict:
    """Build a complete V2 PlanVersion in memory, without saving it.

    The argument shape intentionally matches V1 ``generate_plan`` at the API
    routing seam.  ``config`` is retained for that shared contract; existing
    V2 primitives own their validated planning parameters.
    """

    del config  # V2's validated primitives currently have no additional config input.
    locked = tuple(dict(item) for item in (locked_sessions or ()))
    try:
        phases = build_season_phases(profile)
        demands = generate_v2_demand_plan(profile, phases)
        calendar = build_v2_season_calendar(profile, phases, locked_sessions=list(locked))
        state, diagnostics = solve_v2_rolling_non_rowing(profile, demands, calendar)
        # The rolling solver retains full-season frozen placement history but
        # its final active context is only the closing window. Expand that
        # immutable context for the existing final-state role adapter.
        full_state = replace(state, fixed_context={item.date: item for item in calendar})
        roles = build_dated_training_roles(full_state)
        rest_dates = tuple(item.date for item in roles if item.role == "rest")
        ordinary = tuple(V2SessionMaterializationRequest(item.role, item.date, item.duration_minutes, item.phase_id, placement_id=item.placement_id, source_id=item.source_id) for item in roles if item.role not in {"rest", "quality"})
        phase_types = {item["phase_id"]: item["phase_type"] for item in phases}
        requests = ordinary + _quality_requests(profile, roles, phase_types) + _fixed_private_requests(profile, calendar) + _race_requests(profile, calendar)
        result = build_plan_version_from_v2(profile=profile, bands=bands, power=power, calendar=calendar, requests=requests, rest_dates=rest_dates, authoritative_sessions=locked, v2_diagnostics=_routing_diagnostics(full_state, diagnostics))
    except V2PlanningError:
        raise
    except ValueError as error:
        raise V2PlanningError("v2_schedule_infeasible", str(error)) from error
    if not result.success:
        reason = result.failure_reason or "unknown_materialization_failure"
        category = "v2_materialization_failed" if reason not in {"incomplete_session_shape", "duplicate_external_session_key", "rest_session_conflict"} else "v2_plan_contract_failed"
        raise V2PlanningError(category, reason)
    return dict(result.plan)


def validate_v2_plan_contract(plan: dict, *, authoritative_sessions=()) -> str | None:
    """Validate the external PlanVersion shape, not scheduling physiology."""

    try:
        json.dumps(plan)
    except (TypeError, ValueError):
        return "not_json_serializable"
    required = {"plan_version", "profile_id", "generated_at", "intensity_profile", "power_profile", "sessions", "calendar_days", "phases", "weekly_totals", "v2_diagnostics"}
    if not required <= set(plan) or not isinstance(plan.get("sessions"), list) or not isinstance(plan.get("calendar_days"), list):
        return "missing_plan_structure"
    session_required = {"date", "day", "session_id", "mode", "title", "band", "total_cardio_minutes", "phase", "structure"}
    sessions = plan["sessions"]
    if any(not session_required <= set(item) for item in sessions):
        return "incomplete_session_shape"
    keys = [(item["date"], item["session_id"], item["mode"]) for item in sessions]
    if len(keys) != len(set(keys)):
        return "duplicate_external_session_key"
    for item in sessions:
        if item.get("band") in {"AT", "TR", "AN", "PP"} and (not item.get("session_fingerprint") or not item.get("structure") or "v2_finalized_quality" not in item.get("selection_reason_codes", ())):
            return "unfinalized_quality_session"
    rest_dates = {item["date"] for item in plan["calendar_days"] if item.get("designated_rest")}
    if any(item["date"] in rest_dates for item in sessions):
        return "rest_session_conflict"
    for calendar_day in plan["calendar_days"]:
        if not {"date", "designated_rest", "unavailable", "state", "commitments"} <= set(calendar_day):
            return "incomplete_calendar_shape"
        commitments = calendar_day.get("commitments", ())
        if any(item.get("type") == "race" for item in commitments) and not any(session["date"] == calendar_day["date"] and session["session_id"] == "RACE" for session in sessions):
            return "race_commitment_not_represented"
    authoritative = {(item.get("date"), item.get("session_id"), item.get("mode")): item for item in authoritative_sessions}
    serialized = {(item.get("date"), item.get("session_id"), item.get("mode")): item for item in sessions}
    if any(serialized.get(key) != item for key, item in authoritative.items()):
        return "authoritative_session_not_preserved"
    return None
