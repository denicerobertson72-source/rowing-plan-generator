"""Canonical, parallel normalization for replacement-planner inputs.

This module is deliberately pure: current Scheduler V2 still receives its
legacy dictionaries.  The normalized context records what those dictionaries
mean without treating their shape as a future solver contract.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Iterable, Mapping

from .models import WindowPlacement
from .periodization import race_dates
from .planning_adapters import (
    TypedPlanningInputs,
    _activity_role,
    _as_date,
    _completed_credit,
    _identity,
    _session_minutes,
    _session_role,
    adapt_fixed_recurring_commitments,
    adapt_future_placements,
    adapt_future_race_commitments,
    adapt_historical_races,
    adapt_historical_sessions,
    adapt_optional_off_day_actuals,
)
from .planning_domain import (
    AuthoritySource,
    AuthorityType,
    AuthoritativeCommitment,
    ConflictBehavior,
    HistoricalFact,
    HistoricalFactType,
    PhysiologicalCredit,
    Provenance,
    ProvenanceSource,
    TrainingRole,
)


# This is semantic documentation, not an operational conflict resolver yet.
AUTHORITY_PRECEDENCE: tuple[AuthorityType, ...] = (
    AuthorityType.RACE,
    AuthorityType.USER_FIXED_PLACEMENT,
    AuthorityType.COURSE_PRACTICE,
    AuthorityType.PRIVATE_COACHING,
    AuthorityType.FIXED_COACHING,
    AuthorityType.FIXED_RECURRING_ACTIVITY,
    AuthorityType.EXPLICIT_API_LOCK,
)
_PRECEDENCE_INDEX = {item: index for index, item in enumerate(AUTHORITY_PRECEDENCE)}

# `locked_sessions` is deliberately not consumed here as a behavioral input.
# These labels make the existing overloaded transport auditable for its future
# replacement, and contain no athlete-specific values.
LEGACY_LOCKED_SESSION_AUDIT: tuple[tuple[str, str], ...] = (
    ("completed_planned_session_from_generation_history", "historical_fact"),
    ("optional_activity_history_session", "historical_fact"),
    ("historical_serialized_session", "historical_fact"),
    ("explicit_api_lock_on_current_or_future_date", "authoritative_commitment"),
    ("prior_calendar_position_without_explicit_lock", "provisional_only"),
    ("untyped_legacy_lock_without_date_or_identity", "ambiguous"),
)


@dataclass(frozen=True)
class NormalizationDiagnostics:
    historical_facts: int = 0
    completed_credit_facts: int = 0
    historical_prescriptions_without_credit: int = 0
    optional_actuals: int = 0
    historical_race_facts: int = 0
    future_authoritative_commitments: int = 0
    user_fixed_commitments: int = 0
    fixed_recurring_commitments: int = 0
    provisional_prior_placements: int = 0
    duplicates_collapsed: int = 0
    legacy_locked_session_classifications: tuple[tuple[str, str], ...] = LEGACY_LOCKED_SESSION_AUDIT

    def to_dict(self) -> dict[str, Any]:
        return {
            "historical_facts": self.historical_facts,
            "completed_credit_facts": self.completed_credit_facts,
            "historical_prescriptions_without_credit": self.historical_prescriptions_without_credit,
            "optional_actuals": self.optional_actuals,
            "historical_race_facts": self.historical_race_facts,
            "future_authoritative_commitments": self.future_authoritative_commitments,
            "user_fixed_commitments": self.user_fixed_commitments,
            "fixed_recurring_commitments": self.fixed_recurring_commitments,
            "provisional_prior_placements": self.provisional_prior_placements,
            "duplicates_collapsed": self.duplicates_collapsed,
            "legacy_locked_session_classifications": [list(item) for item in self.legacy_locked_session_classifications],
        }


@dataclass(frozen=True)
class NormalizedPlanningContext:
    """Replacement-planner source facts, authority, and non-authoritative IDs."""

    planning_date: date
    historical_facts: tuple[HistoricalFact, ...]
    authoritative_commitments: tuple[AuthoritativeCommitment, ...]
    provisional_prior_placement_ids: tuple[str, ...]
    race_context: tuple[tuple[str, str, str, str | None], ...]
    authority_precedence: tuple[AuthorityType, ...]
    diagnostics: NormalizationDiagnostics

    def to_dict(self) -> dict[str, Any]:
        return {
            "planning_date": self.planning_date.isoformat(),
            "historical_facts": [item.to_dict() for item in self.historical_facts],
            "authoritative_commitments": [item.to_dict() for item in self.authoritative_commitments],
            "provisional_prior_placement_ids": list(self.provisional_prior_placement_ids),
            "race_context": [list(item) for item in self.race_context],
            "authority_precedence": [item.value for item in self.authority_precedence],
            "diagnostics": self.diagnostics.to_dict(),
        }


def _fact_key(fact: HistoricalFact) -> tuple[Any, ...]:
    """Semantic identity, independent of source dictionary ordering."""
    if fact.type is HistoricalFactType.OPTIONAL_ACTIVITY:
        return ("optional_actual", fact.date, fact.provenance.source_object_id)
    if fact.type is HistoricalFactType.RACE_FACT:
        return ("race", fact.date)
    prescribed = fact.planned_prescription or {}
    identity = fact.provenance.original_serialized_identity
    if len(identity) >= 3:
        return ("planned_session", fact.date, identity[1], identity[2])
    return ("planned_session", fact.date, prescribed.get("session_id"), prescribed.get("mode"))


def _merge_notes(*facts: HistoricalFact) -> tuple[str, ...]:
    notes = []
    for fact in facts:
        source = f"merged_source:{fact.provenance.source_type.value}:{fact.provenance.source_object_id}"
        notes.extend((*fact.provenance.adaptation_notes, source))
    return tuple(sorted(set(notes)))


def _fact_precedence(fact: HistoricalFact) -> tuple[int, int, str]:
    # Preserve completed planned evidence, then optional/race facts, then a
    # prescription; this affects audit payload only, never live V2 behavior.
    rank = {
        HistoricalFactType.COMPLETED_PLANNED_SESSION: 0,
        HistoricalFactType.OPTIONAL_ACTIVITY: 1,
        HistoricalFactType.RACE_FACT: 2,
        HistoricalFactType.PRESCRIBED_SESSION: 3,
    }[fact.type]
    return rank, -int(fact.physiological_credit.eligible), fact.id


def _merge_fact_group(facts: Iterable[HistoricalFact]) -> HistoricalFact:
    group = tuple(sorted(facts, key=_fact_precedence))
    chosen = group[0]
    completed = next((item for item in group if item.completed_actual is not None), None)
    planned = next((item for item in group if item.planned_prescription is not None), None)
    credit = next((item.physiological_credit for item in group if item.physiological_credit.eligible), PhysiologicalCredit())
    fact_type = chosen.type
    if chosen.type is HistoricalFactType.PRESCRIBED_SESSION and completed:
        fact_type = HistoricalFactType.COMPLETED_PLANNED_SESSION
    provenance = replace(chosen.provenance, adaptation_notes=_merge_notes(*group))
    return replace(
        chosen,
        type=fact_type,
        planned_prescription=planned.planned_prescription if planned else None,
        completed_actual=completed.completed_actual if completed else None,
        physiological_credit=credit,
        provenance=provenance,
    )


def _deduplicate_facts(facts: Iterable[HistoricalFact]) -> tuple[tuple[HistoricalFact, ...], int]:
    groups: dict[tuple[Any, ...], list[HistoricalFact]] = {}
    for fact in facts:
        groups.setdefault(_fact_key(fact), []).append(fact)
    normalized = tuple(sorted((_merge_fact_group(group) for group in groups.values()), key=lambda item: (item.date, item.id)))
    return normalized, sum(len(group) - 1 for group in groups.values())


def _commitment_key(item: AuthoritativeCommitment) -> tuple[Any, ...]:
    if item.type in {AuthorityType.RACE, AuthorityType.COURSE_PRACTICE}:
        return item.type, item.date, item.date_window
    if item.type is AuthorityType.USER_FIXED_PLACEMENT:
        identity = item.provenance.original_serialized_identity
        return item.type, identity[0] if identity else item.provenance.source_object_id
    return item.type, item.date, item.date_window, item.provenance.source_object_id


def _merge_commitment_group(items: Iterable[AuthoritativeCommitment]) -> AuthoritativeCommitment:
    group = tuple(sorted(items, key=lambda item: (_PRECEDENCE_INDEX[item.type], item.id)))
    chosen = group[0]
    notes = []
    for item in group:
        notes.extend((*item.provenance.adaptation_notes, f"merged_source:{item.provenance.source_type.value}:{item.provenance.source_object_id}"))
    return replace(chosen, provenance=replace(chosen.provenance, adaptation_notes=tuple(sorted(set(notes)))))


def _deduplicate_commitments(items: Iterable[AuthoritativeCommitment]) -> tuple[tuple[AuthoritativeCommitment, ...], int]:
    groups: dict[tuple[Any, ...], list[AuthoritativeCommitment]] = {}
    for item in items:
        groups.setdefault(_commitment_key(item), []).append(item)
    normalized = tuple(sorted(
        (_merge_commitment_group(group) for group in groups.values()),
        key=lambda item: (item.date or date.min, _PRECEDENCE_INDEX[item.type], item.id),
    ))
    return normalized, sum(len(group) - 1 for group in groups.values())


def _historical_calendar_facts(prior_plan: Mapping[str, Any] | None, planning_date: date) -> tuple[HistoricalFact, ...]:
    """Only past OFF/rest calendar rows need a separate audit fact.

    Ordinary calendar days with sessions are represented by their session fact;
    emitting both would duplicate audit state without adding a new semantic.
    """
    facts = []
    for row in (prior_plan or {}).get("calendar_days", ()):
        if not isinstance(row, Mapping):
            continue
        day = _as_date(row.get("date"))
        is_off = bool(row.get("designated_rest")) or str(row.get("state", "")).lower() in {"off", "rest"}
        if day is None or day >= planning_date or not is_off:
            continue
        identifier = f"calendar:{day.isoformat()}"
        facts.append(HistoricalFact(
            id=f"historical:{identifier}", date=day, type=HistoricalFactType.PRESCRIBED_SESSION,
            planned_prescription=dict(row),
            provenance=Provenance(identifier, ProvenanceSource.PRIOR_PLAN, original_date=day,
                original_serialized_identity=(identifier,), adaptation_notes=("historical_planned_off_or_rest",)),
        ))
    return tuple(facts)


def _locked_fact(item: Mapping[str, Any], actuals: Mapping[str, Mapping[str, Any]], planning_date: date) -> HistoricalFact | None:
    day = _as_date(item.get("date"))
    if day is None or day >= planning_date:
        return None
    source_id = str(item.get("source_id") or "")
    identity = _identity(item)
    standard_key = ":".join(identity)
    actual = actuals.get(source_id) or actuals.get(standard_key)
    is_optional = source_id.startswith("actual:") or str(item.get("session_id", "")).startswith("ACTUAL_")
    if is_optional:
        activity_type = source_id.rsplit(":", 1)[-1] if source_id.startswith("actual:") else item.get("mode")
        actual_payload = dict(actual or {})
        actual_payload.setdefault("activity_type", activity_type)
        return HistoricalFact(
            id=f"historical:locked:{source_id or standard_key}", date=day, type=HistoricalFactType.OPTIONAL_ACTIVITY,
            completed_actual=actual_payload or None,
            physiological_credit=_completed_credit(_activity_role(activity_type), _session_minutes(item, actual), bool(actual and actual.get("status") == "completed")),
            provenance=Provenance(source_id or standard_key, ProvenanceSource.LEGACY_LOCKED_SESSION, original_date=day,
                original_serialized_identity=(source_id or standard_key,), adaptation_notes=("legacy_optional_activity_history",)),
        )
    role = _session_role(item)
    completed = bool(actual and actual.get("status") == "completed")
    return HistoricalFact(
        id=f"historical:locked:{standard_key}", date=day,
        type=(HistoricalFactType.RACE_FACT if role is TrainingRole.RACE else HistoricalFactType.COMPLETED_PLANNED_SESSION if completed else HistoricalFactType.PRESCRIBED_SESSION),
        planned_prescription=dict(item), completed_actual=dict(actual) if completed else None,
        physiological_credit=_completed_credit(role, _session_minutes(item, actual), completed),
        provenance=Provenance(source_id or standard_key, ProvenanceSource.LEGACY_LOCKED_SESSION, original_date=day,
            original_serialized_identity=identity, adaptation_notes=("legacy_locked_history",)),
    )


def _future_locked_commitment(item: Mapping[str, Any], planning_date: date) -> AuthoritativeCommitment | None:
    day = _as_date(item.get("date"))
    if day is None or day < planning_date:
        return None
    placement_id = str(item.get("placement_id") or item.get("source_id") or ":".join(_identity(item)))
    user_fixed = bool(item.get("user_fixed") or item.get("override_id"))
    authority_type = AuthorityType.USER_FIXED_PLACEMENT if user_fixed else AuthorityType.EXPLICIT_API_LOCK
    source = AuthoritySource.USER_OVERRIDE if user_fixed else AuthoritySource.EXPLICIT_API_LOCK
    source_type = ProvenanceSource.USER_OVERRIDE if user_fixed else ProvenanceSource.LEGACY_LOCKED_SESSION
    original = _as_date(item.get("original_date")) or day
    return AuthoritativeCommitment(
        id=(f"user_fixed:{placement_id}" if user_fixed else f"api_lock:{placement_id}"),
        type=authority_type, date=day, date_window=None,
        reservation_minutes=_session_minutes(item), authority_source=source,
        conflict_behavior=ConflictBehavior.PRESERVE_AND_REPORT,
        provenance=Provenance(placement_id, source_type, original_date=original,
            original_serialized_identity=(placement_id, *_identity(item)),
            adaptation_notes=("legacy_locked_session_explicit_authority",)),
    )


def _race_context(profile: Mapping[str, Any], planning_date: date) -> tuple[tuple[str, str, str, str | None], ...]:
    records = []
    for race in profile.get("races", ()):
        if not isinstance(race, Mapping):
            continue
        for day in race_dates(dict(race)):
            if day >= planning_date:
                records.append((
                    day.isoformat(), str(race.get("priority", "B")),
                    str(race.get("event_name", "race")),
                    str(race.get("race_type")) if race.get("race_type") else None,
                ))
    return tuple(sorted(records))


def normalize_planning_inputs(
    profile: Mapping[str, Any], *, planning_date: date,
    prior_plan: Mapping[str, Any] | None = None,
    history_actuals: Mapping[str, Mapping[str, Any]] | None = None,
    locked_sessions: Iterable[Mapping[str, Any]] = (),
    future_placements: Iterable[WindowPlacement | Mapping[str, Any]] = (),
) -> NormalizedPlanningContext:
    """Normalize legacy planner inputs without changing live planner behavior."""
    actuals = history_actuals or {}
    locks = tuple(dict(item) for item in locked_sessions if isinstance(item, Mapping))
    adapter_inputs: TypedPlanningInputs = TypedPlanningInputs(
        historical_facts=(*adapt_historical_sessions(prior_plan, actuals, planning_date),
                          *adapt_optional_off_day_actuals(actuals, planning_date),
                          *adapt_historical_races(profile, planning_date)),
        authoritative_commitments=(*adapt_future_race_commitments(profile, planning_date),
                                     *adapt_fixed_recurring_commitments(profile, planning_date)),
    )
    historical_candidates = (*adapter_inputs.historical_facts,
                             *_historical_calendar_facts(prior_plan, planning_date),
                             *(item for lock in locks if (item := _locked_fact(lock, actuals, planning_date)) is not None))
    future_lock_commitments = tuple(item for lock in locks if (item := _future_locked_commitment(lock, planning_date)) is not None)
    placement_commitments, provisional_ids = adapt_future_placements(future_placements, planning_date)
    facts, collapsed_facts = _deduplicate_facts(historical_candidates)
    commitments, collapsed_commitments = _deduplicate_commitments((*adapter_inputs.authoritative_commitments, *future_lock_commitments, *placement_commitments))
    diagnostics = NormalizationDiagnostics(
        historical_facts=len(facts),
        completed_credit_facts=sum(item.physiological_credit.eligible for item in facts),
        historical_prescriptions_without_credit=sum(item.type is HistoricalFactType.PRESCRIBED_SESSION and not item.physiological_credit.eligible for item in facts),
        optional_actuals=sum(item.type is HistoricalFactType.OPTIONAL_ACTIVITY for item in facts),
        historical_race_facts=sum(item.type is HistoricalFactType.RACE_FACT for item in facts),
        future_authoritative_commitments=len(commitments),
        user_fixed_commitments=sum(item.type is AuthorityType.USER_FIXED_PLACEMENT for item in commitments),
        fixed_recurring_commitments=sum(item.type in {AuthorityType.PRIVATE_COACHING, AuthorityType.FIXED_COACHING, AuthorityType.FIXED_RECURRING_ACTIVITY} for item in commitments),
        provisional_prior_placements=len(provisional_ids),
        duplicates_collapsed=collapsed_facts + collapsed_commitments,
    )
    return NormalizedPlanningContext(
        planning_date=planning_date, historical_facts=facts,
        authoritative_commitments=commitments,
        provisional_prior_placement_ids=provisional_ids,
        race_context=_race_context(profile, planning_date),
        authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=diagnostics,
    )
