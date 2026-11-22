"""Canonical persisted-completion inputs shared by generation and diagnostics."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .planning_domain import TrainingModality, normalize_training_modality


def session_key_for_generation(session: Mapping[str, Any]) -> str:
    return f'{session.get("date")}:{session.get("session_id")}:{session.get("mode")}'


def optional_activity_history_session(session_key: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt an optional actual into the planner's completed-session contract."""
    _, activity_date, activity_type = session_key.split(":", 2)
    modality = normalize_training_modality(activity_type)
    mode = {
        TrainingModality.ERG: "erg",
        TrainingModality.ON_WATER: "on_water",
        TrainingModality.STRENGTH: "strength",
    }.get(
        modality,
        modality.value if modality in {
            TrainingModality.BIKE, TrainingModality.ELLIPTICAL,
            TrainingModality.STAIR_CLIMBER, TrainingModality.RUNNING,
        } else "other",
    )
    minutes = payload.get("actual_duration_min") or 0
    session: dict[str, Any] = {
        "date": activity_date,
        "session_id": f"ACTUAL_{activity_type.upper()}",
        "mode": mode,
        "source_id": session_key,
        "title": f"Completed {activity_type.replace('_', ' ')}",
        "total_cardio_minutes": minutes if mode in {"erg", "on_water"} else 0,
        "strength_minutes": minutes if mode == "strength" else 0,
        "total_training_minutes": minutes,
    }
    if payload.get("actual_intensity"):
        session["band"] = payload["actual_intensity"]
    return session


def recover_generation_history(
    previous: Mapping[str, Any] | None,
    logs_for_plan: Callable[[str], list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], tuple[str, ...]]:
    """Recover the precise completed-history inputs used by regeneration.

    The function is deliberately persistence-agnostic: callers supply the
    read operation, which makes an offline diagnostic use the exact same
    interpretation without acquiring a mutable repository.
    """
    if not previous:
        return [], {}, ()
    plan = previous.get("plan", {})
    context = plan.get("generation_input_context", {}) if isinstance(plan, Mapping) else {}
    inherited_locked = [dict(item) for item in context.get("locked_sessions", ()) if isinstance(item, Mapping)] if isinstance(context, Mapping) else []
    source_ids = [str(item) for item in context.get("actual_plan_ids", ()) if item] if isinstance(context, Mapping) else []
    plan_id = previous.get("plan_id")
    if plan_id:
        source_ids.append(str(plan_id))
    actuals: dict[str, dict[str, Any]] = {}
    for source_id in dict.fromkeys(source_ids):
        for entry in logs_for_plan(source_id):
            if isinstance(entry, Mapping) and isinstance(entry.get("payload"), Mapping) and entry.get("session_key"):
                actuals[str(entry["session_key"])] = dict(entry["payload"])
    completed = {key for key, payload in actuals.items() if payload.get("status") == "completed"}
    newly_completed = [
        dict(session) for session in plan.get("sessions", ())
        if isinstance(session, Mapping) and session_key_for_generation(session) in completed
    ] if isinstance(plan, Mapping) else []
    optional_completed = [
        optional_activity_history_session(key, payload)
        for key, payload in actuals.items()
        if key in completed and key.startswith("actual:")
    ]
    locked_by_key = {
        session_key_for_generation(item): item
        for item in inherited_locked + newly_completed + optional_completed
    }
    return list(locked_by_key.values()), actuals, tuple(dict.fromkeys(source_ids))
