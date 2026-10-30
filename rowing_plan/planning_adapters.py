"""Additive adapters from current V2 inputs to replacement-planner contracts.

Nothing in the live scheduler consumes these records yet. They are a
deterministic semantic view of legacy V2 inputs, preserving the distinction
between immutable history, future authority, and provisional work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .models import WindowPlacement
from .periodization import race_dates
from .planning_domain import (
    AuthoritySource, AuthorityType, AuthoritativeCommitment, ConflictBehavior,
    HistoricalFact, HistoricalFactType, PhysiologicalCredit, Provenance,
    ProvenanceSource, TrainingRole,
)


_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass(frozen=True)
class TypedPlanningInputs:
    """Parallel replacement-planner view; not a new live planning input."""

    historical_facts: tuple[HistoricalFact, ...]
    authoritative_commitments: tuple[AuthoritativeCommitment, ...]
    provisional_placement_ids: tuple[str, ...] = ()


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _value(item: Mapping[str, Any] | Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, Mapping) else getattr(item, name, default)


def _identity(session: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(session.get(key, "")) for key in ("date", "session_id", "mode"))


def _session_role(session: Mapping[str, Any]) -> TrainingRole | None:
    session_role = str(session.get("session_role") or "").upper()
    mode = str(session.get("mode") or "").lower()
    if session.get("session_id") == "LIFT" or mode == "strength":
        return TrainingRole.STRENGTH
    if session.get("session_id") == "RACE" or session_role == "RACE":
        return TrainingRole.RACE
    if session.get("session_id") == "COURSE_PRACTICE" or session_role == "COURSE_PRACTICE":
        return TrainingRole.COURSE_PRACTICE
    if session_role == "LONG_AEROBIC":
        return TrainingRole.LONG_AEROBIC
    if session_role == "AEROBIC_STRENGTH":
        return TrainingRole.UT1_AEROBIC_STRENGTH
    if session_role in {"THRESHOLD", "RACE_PACE", "SPRINT_POWER"} or session.get("band") in {"AT", "TR", "AN", "PP"}:
        return TrainingRole.QUALITY
    if session.get("session_id") == "COACHED" or session.get("coached"):
        return TrainingRole.COACHED_TRAINING
    if session.get("session_id") == "PRIVATE" or session.get("private_coaching"):
        return TrainingRole.PRIVATE_COACHING
    if mode in {"erg", "on_water"}:
        return TrainingRole.DEDICATED_UT2
    return None


def _activity_role(activity_type: str | None) -> TrainingRole | None:
    normalized = str(activity_type or "").lower().replace("-", "_").replace(" ", "_")
    if normalized in {"erg", "on_water", "rowing", "row"}:
        return TrainingRole.DEDICATED_UT2
    if normalized == "strength":
        return TrainingRole.STRENGTH
    if normalized in {"mobility", "recovery", "rest"}:
        return TrainingRole.REST
    return None


def _completed_credit(role: TrainingRole | None, minutes: int, completed: bool) -> PhysiologicalCredit:
    if not completed or role is None:
        return PhysiologicalCredit()
    return PhysiologicalCredit(role=role, exposures=1, minutes=max(0, minutes), eligible=True)


def _session_minutes(session: Mapping[str, Any], actual: Mapping[str, Any] | None = None) -> int:
    candidate = ((actual or {}).get("actual_duration_min") or session.get("total_training_minutes")
                 or session.get("total_cardio_minutes") or session.get("duration_minutes") or 0)
    try:
        return max(0, int(candidate))
    except (TypeError, ValueError):
        return 0


def adapt_historical_sessions(prior_plan: Mapping[str, Any] | None, actuals: Mapping[str, Mapping[str, Any]] | None, planning_date: date) -> tuple[HistoricalFact, ...]:
    """Adapt prescriptions before planning date; only completed actuals credit."""
    facts: list[HistoricalFact] = []
    actuals = actuals or {}
    for session in (prior_plan or {}).get("sessions", ()):
        if not isinstance(session, Mapping):
            continue
        day = _as_date(session.get("date"))
        if day is None or day >= planning_date:
            continue
        identity = _identity(session)
        key = ":".join(identity)
        actual = actuals.get(key) or actuals.get(str(session.get("source_id") or ""))
        completed = bool(actual and actual.get("status") == "completed")
        role = _session_role(session)
        facts.append(HistoricalFact(
            id=f"historical:{key}", date=day,
            type=(HistoricalFactType.RACE_FACT if role is TrainingRole.RACE else
                  HistoricalFactType.COMPLETED_PLANNED_SESSION if completed else
                  HistoricalFactType.PRESCRIBED_SESSION),
            planned_prescription=dict(session), completed_actual=dict(actual) if completed else None,
            physiological_credit=_completed_credit(role, _session_minutes(session, actual), completed),
            provenance=Provenance(
                source_object_id=key, source_type=ProvenanceSource.PRIOR_PLAN,
                original_date=day, original_serialized_identity=identity,
                adaptation_notes=("completed_actual_credited" if completed else "prescription_only_no_credit",),
            ),
        ))
    return tuple(sorted(facts, key=lambda item: (item.date, item.id)))


def adapt_optional_off_day_actuals(actuals: Mapping[str, Mapping[str, Any]] | None, planning_date: date) -> tuple[HistoricalFact, ...]:
    """Adapt optional OFF-day activity as immutable history, never authority."""
    facts: list[HistoricalFact] = []
    for key, raw_actual in sorted((actuals or {}).items()):
        if not key.startswith("actual:") or raw_actual.get("status") != "completed":
            continue
        parts = key.split(":", 2)
        day = _as_date(parts[1] if len(parts) > 1 else None)
        if day is None or day >= planning_date:
            continue
        activity_type = raw_actual.get("activity_type") or (parts[2] if len(parts) > 2 else None)
        role = _activity_role(activity_type)
        actual = dict(raw_actual)
        actual.setdefault("activity_type", activity_type)
        facts.append(HistoricalFact(
            id=f"historical:{key}", date=day, type=HistoricalFactType.OPTIONAL_ACTIVITY,
            completed_actual=actual,
            physiological_credit=_completed_credit(role, _session_minutes(actual, actual), True),
            provenance=Provenance(
                source_object_id=key, source_type=ProvenanceSource.OPTIONAL_ACTIVITY_LOG,
                original_date=day, original_serialized_identity=(key,),
                adaptation_notes=("optional_activity_is_history_not_authority",),
            ),
        ))
    return tuple(sorted(facts, key=lambda item: (item.date, item.id)))


def adapt_historical_races(profile: Mapping[str, Any], planning_date: date) -> tuple[HistoricalFact, ...]:
    facts: list[HistoricalFact] = []
    for race_index, race in enumerate(profile.get("races", ())):
        if not isinstance(race, Mapping):
            continue
        for day in race_dates(dict(race)):
            if day >= planning_date:
                continue
            source_id = f"race:{race_index}:{day.isoformat()}"
            facts.append(HistoricalFact(
                id=f"historical:{source_id}", date=day, type=HistoricalFactType.RACE_FACT,
                planned_prescription=dict(race),
                provenance=Provenance(source_id, ProvenanceSource.RACE_PROFILE, original_date=day,
                    original_serialized_identity=(source_id,), adaptation_notes=("race_fact_no_duplicate_actual_credit",)),
            ))
    return tuple(sorted(facts, key=lambda item: (item.date, item.id)))


def _commitment(*, identifier: str, authority_type: AuthorityType, day: date, minutes: int, source: AuthoritySource, behavior: ConflictBehavior, provenance_source: ProvenanceSource, notes: tuple[str, ...] = ()) -> AuthoritativeCommitment:
    return AuthoritativeCommitment(
        id=identifier, type=authority_type, date=day, date_window=None,
        reservation_minutes=max(0, minutes), authority_source=source, conflict_behavior=behavior,
        provenance=Provenance(identifier, provenance_source, original_date=day,
            original_serialized_identity=(identifier, day.isoformat()), adaptation_notes=notes),
    )


def adapt_future_race_commitments(profile: Mapping[str, Any], planning_date: date) -> tuple[AuthoritativeCommitment, ...]:
    commitments = []
    for race_index, race in enumerate(profile.get("races", ())):
        if not isinstance(race, Mapping):
            continue
        for day in race_dates(dict(race)):
            if day >= planning_date:
                identifier = f"race:{race_index}:{day.isoformat()}"
                commitments.append(_commitment(identifier=identifier, authority_type=AuthorityType.RACE, day=day, minutes=20, source=AuthoritySource.RACE_PROFILE, behavior=ConflictBehavior.CALENDAR_BLOCK, provenance_source=ProvenanceSource.RACE_PROFILE, notes=("legacy_v2_race_reservation",)))
        for practice_index, practice in enumerate(race.get("practice_sessions", ())):
            if not isinstance(practice, Mapping):
                continue
            day = _as_date(practice.get("date"))
            if day is None or day < planning_date:
                continue
            identifier = f"practice:{race_index}:{practice_index}:{day.isoformat()}"
            commitments.append(_commitment(identifier=identifier, authority_type=AuthorityType.COURSE_PRACTICE, day=day, minutes=int(practice.get("duration_minutes", 30)), source=AuthoritySource.RACE_PROFILE, behavior=ConflictBehavior.CALENDAR_BLOCK, provenance_source=ProvenanceSource.RACE_PROFILE, notes=("fixed_course_familiarization",)))
    return tuple(sorted(commitments, key=lambda item: (item.date or date.min, item.id)))


def _season_dates(profile: Mapping[str, Any], planning_date: date) -> tuple[date, date]:
    season = profile.get("season", {}) if isinstance(profile.get("season"), Mapping) else {}
    start = _as_date(season.get("start_date")) or planning_date
    end = _as_date(season.get("end_date")) or (planning_date + timedelta(days=180))
    return max(start, planning_date), max(end, planning_date)


def _iter_weekdays(start: date, end: date, fixed_days: Iterable[str]) -> Iterable[date]:
    wanted = {_WEEKDAY_INDEX[value.lower()] for value in fixed_days if value.lower() in _WEEKDAY_INDEX}
    current = start
    while current <= end:
        if current.weekday() in wanted:
            yield current
        current += timedelta(days=1)


def _activity_authority(activity_type: str) -> AuthorityType:
    if activity_type == "private_coaching":
        return AuthorityType.PRIVATE_COACHING
    if activity_type in {"coached", "coached_training", "coached_row"}:
        return AuthorityType.FIXED_COACHING
    if activity_type in {"rest", "off", "designated_rest"}:
        return AuthorityType.FIXED_REST
    return AuthorityType.FIXED_RECURRING_ACTIVITY


def adapt_fixed_recurring_commitments(profile: Mapping[str, Any], planning_date: date) -> tuple[AuthoritativeCommitment, ...]:
    start, end = _season_dates(profile, planning_date)
    commitments: list[AuthoritativeCommitment] = []
    for index, activity in enumerate(profile.get("recurring_activities", ())):
        if not isinstance(activity, Mapping) or activity.get("scheduling_status") != "fixed":
            continue
        activity_type = str(activity.get("activity_type", "recurring_activity"))
        activity_id = str(activity.get("activity_id", index))
        default = 0 if activity_type == "rest" else 50
        try:
            minutes = int(activity.get("duration_minutes", default))
        except (TypeError, ValueError):
            minutes = default
        for day in _iter_weekdays(start, end, activity.get("fixed_days", ())):
            identifier = f"recurring:{activity_id}:{day.isoformat()}"
            commitments.append(_commitment(identifier=identifier, authority_type=_activity_authority(activity_type), day=day, minutes=minutes, source=AuthoritySource.RECURRING_ACTIVITY, behavior=ConflictBehavior.CALENDAR_BLOCK, provenance_source=ProvenanceSource.RECURRING_ACTIVITY, notes=(f"fixed_{activity_type}",)))
    return tuple(sorted(commitments, key=lambda item: (item.date or date.min, item.id)))


def adapt_future_placements(placements: Iterable[WindowPlacement | Mapping[str, Any]], planning_date: date) -> tuple[tuple[AuthoritativeCommitment, ...], tuple[str, ...]]:
    """Only future user-fixed placements are authority; provisional IDs remain diagnostic."""
    commitments: list[AuthoritativeCommitment] = []
    provisional_ids: list[str] = []
    for item in placements:
        day = _as_date(_value(item, "date"))
        if day is None or day < planning_date:
            continue
        placement_id = str(_value(item, "placement_id", ""))
        if not bool(_value(item, "user_fixed", False)):
            provisional_ids.append(placement_id)
            continue
        role = str(_value(item, "role", "placement"))
        minutes = int(_value(item, "minutes", 0) or 0)
        source_id = str(_value(item, "source_id", placement_id))
        original = _as_date(_value(item, "original_date"))
        commitments.append(AuthoritativeCommitment(
            id=f"user_fixed:{placement_id}", type=AuthorityType.USER_FIXED_PLACEMENT, date=day, date_window=None,
            reservation_minutes=max(0, minutes), authority_source=AuthoritySource.USER_OVERRIDE, conflict_behavior=ConflictBehavior.PRESERVE_AND_REPORT,
            provenance=Provenance(source_id, ProvenanceSource.USER_OVERRIDE, original_date=original or day,
                original_serialized_identity=(placement_id, source_id, role), adaptation_notes=("future_user_fixed_placement",)),
        ))
    return tuple(sorted(commitments, key=lambda item: (item.date or date.min, item.id))), tuple(sorted(provisional_ids))


def adapt_v2_inputs(profile: Mapping[str, Any], *, prior_plan: Mapping[str, Any] | None = None, actuals: Mapping[str, Mapping[str, Any]] | None = None, planning_date: date, future_placements: Iterable[WindowPlacement | Mapping[str, Any]] = ()) -> TypedPlanningInputs:
    """Build deterministic contracts in parallel with, not instead of, V2."""
    future_commitments, provisional_ids = adapt_future_placements(future_placements, planning_date)
    facts = (*adapt_historical_sessions(prior_plan, actuals, planning_date), *adapt_optional_off_day_actuals(actuals, planning_date), *adapt_historical_races(profile, planning_date))
    commitments = (*adapt_future_race_commitments(profile, planning_date), *adapt_fixed_recurring_commitments(profile, planning_date), *future_commitments)
    return TypedPlanningInputs(tuple(sorted(facts, key=lambda item: (item.date, item.id))), tuple(sorted(commitments, key=lambda item: (item.date or date.min, item.id))), provisional_ids)
