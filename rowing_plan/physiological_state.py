"""Pure completed-history reducer for the replacement scheduler.

The reducer consumes normalized historical facts only.  It deliberately does
not create needs, dates, sessions, or placements, and no live V2 path reads
its output in this milestone.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .periodization import build_season_phases
from .planning_domain import (
    HistoricalFact,
    HistoricalFactType,
    PhysiologicalCredit,
    PhysiologicalState,
    RecentLoad,
    RecoveryConstraint,
    RuleClass,
    TrainingRole,
)
from .planning_normalization import NormalizedPlanningContext
from .training_policy import DEFAULT_PHYSIOLOGICAL_POLICY, TrainingPolicyRule


_AEROBIC_ROLES = frozenset({
    TrainingRole.DEDICATED_UT2,
    TrainingRole.LONG_AEROBIC,
    TrainingRole.UT1_AEROBIC_STRENGTH,
})
_QUALITY_BANDS = frozenset({"AT", "TR", "AN", "PP"})
_AEROBIC_BANDS = frozenset({"UT3", "UT2", "UT1"})
_LOW_INTENSITY_BANDS = frozenset({"UT3", "UT2"})
_ROWING_ROLES = _AEROBIC_ROLES | {TrainingRole.QUALITY, TrainingRole.RACE}


def _rules_by_key(policy: Iterable[TrainingPolicyRule]) -> dict[str, TrainingPolicyRule]:
    return {item.key: item for item in policy}


def _actual_duration(fact: HistoricalFact) -> int:
    actual = fact.completed_actual or {}
    planned = fact.planned_prescription or {}
    value = (actual.get("actual_duration_min") or actual.get("duration_minutes")
             or planned.get("total_training_minutes") or planned.get("total_cardio_minutes")
             or fact.physiological_credit.minutes)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _explicit_band(fact: HistoricalFact) -> str | None:
    """Return a band only when actual history carries an explicit intensity."""
    actual = fact.completed_actual or {}
    value = actual.get("actual_intensity") or actual.get("intensity_band") or actual.get("band")
    band = str(value).upper() if value else None
    return band if band in _AEROBIC_BANDS | _QUALITY_BANDS else None


def _classification(fact: HistoricalFact) -> str | None:
    if fact.type is HistoricalFactType.RACE_FACT:
        return "RACE"
    explicit = _explicit_band(fact)
    if explicit:
        return explicit
    role = fact.physiological_credit.role
    if role in _AEROBIC_ROLES:
        return "uncertain_aerobic"
    if role is TrainingRole.QUALITY:
        return "uncertain_quality"
    if role is TrainingRole.STRENGTH:
        return "strength"
    return None


def _credit_sum(role: TrainingRole, credits: Iterable[PhysiologicalCredit]) -> PhysiologicalCredit:
    eligible = tuple(item for item in credits if item.eligible)
    if not eligible:
        return PhysiologicalCredit()
    return PhysiologicalCredit(role=role, exposures=sum(item.exposures for item in eligible), minutes=sum(item.minutes for item in eligible), eligible=bool(eligible))


def _classification_credits(facts: Iterable[HistoricalFact], wanted: set[str], role: TrainingRole) -> dict[str, PhysiologicalCredit]:
    grouped: dict[str, list[PhysiologicalCredit]] = {}
    for fact in facts:
        classification = _classification(fact)
        if classification not in wanted:
            continue
        if fact.type is HistoricalFactType.RACE_FACT:
            credit = PhysiologicalCredit(role=role, exposures=1, minutes=_actual_duration(fact), eligible=True)
        elif fact.physiological_credit.eligible:
            credit = fact.physiological_credit
        else:
            continue
        grouped.setdefault(classification, []).append(credit)
    return {key: _credit_sum(role, values) for key, values in sorted(grouped.items())}


def _completed_facts(context: NormalizedPlanningContext) -> tuple[HistoricalFact, ...]:
    """Facts that describe completed physiology, deduped by normalization."""
    result = []
    for fact in context.historical_facts:
        if fact.type is HistoricalFactType.RACE_FACT or fact.physiological_credit.eligible:
            result.append(fact)
    return tuple(sorted(result, key=lambda item: (item.date, item.id)))


def _recent_load(completed: tuple[HistoricalFact, ...], *, as_of: date, window_days: int,
                 duplicate_facts_excluded: int = 0) -> RecentLoad:
    """Reduce completed facts over ``[as_of - days, as_of)`` exactly once."""
    start = as_of - timedelta(days=window_days)
    in_window = tuple(item for item in completed if start <= item.date < as_of)
    excluded = tuple(item.id for item in completed if item.date < start)
    rowing_minutes = 0
    low_intensity_minutes = 0
    quality_exposures = 0
    strength_exposures = 0
    race_exposures = 0
    optional_minutes = 0
    for fact in in_window:
        role = fact.physiological_credit.role
        duration = _actual_duration(fact)
        classification = _classification(fact)
        is_race = fact.type is HistoricalFactType.RACE_FACT
        if is_race or role in _ROWING_ROLES:
            rowing_minutes += duration
        if classification in _LOW_INTENSITY_BANDS:
            low_intensity_minutes += duration
        if classification in _QUALITY_BANDS | {"uncertain_quality"}:
            quality_exposures += fact.physiological_credit.exposures
        if classification == "strength":
            strength_exposures += fact.physiological_credit.exposures
        if is_race:
            race_exposures += 1
        if fact.type is HistoricalFactType.OPTIONAL_ACTIVITY and fact.physiological_credit.eligible:
            optional_minutes += duration
    return RecentLoad(
        window_days=window_days, window_start=start, as_of_date=as_of,
        rowing_minutes=rowing_minutes, low_intensity_rowing_minutes=low_intensity_minutes,
        quality_exposures=quality_exposures, strength_exposures=strength_exposures,
        race_exposures=race_exposures, optional_activity_minutes=optional_minutes,
        counted_fact_ids=tuple(item.id for item in in_window),
        excluded_outside_window_fact_ids=excluded,
        duplicate_facts_excluded=duplicate_facts_excluded,
    )


def _race_priority(fact: HistoricalFact) -> str:
    source = fact.planned_prescription or {}
    return str(source.get("priority", "B"))


def _recovery_constraints(
    completed: tuple[HistoricalFact, ...], as_of: date, policy: Mapping[str, TrainingPolicyRule]
) -> tuple[RecoveryConstraint, ...]:
    result: list[RecoveryConstraint] = []
    quality_spacing = int(policy["completed_quality_minimum_spacing_days"].value)
    strength_spacing = int(policy["completed_strength_minimum_spacing_days"].value)
    race_recovery = policy["completed_race_recovery_days_by_priority"].value
    for fact in completed:
        classification = _classification(fact)
        if fact.type is HistoricalFactType.RACE_FACT:
            days = int(race_recovery.get(_race_priority(fact), race_recovery["B"]))
            end = fact.date + timedelta(days=days)
            if end >= as_of:
                result.append(RecoveryConstraint(
                    source=f"race:{fact.id}", start_date=fact.date + timedelta(days=1), end_date=end,
                    hard_excluded_roles=(TrainingRole.QUALITY, TrainingRole.STRENGTH),
                    soft_load_ceiling="recovery_only", optional_low_load_roles=(TrainingRole.DEDICATED_UT2,),
                    rationale=f"{policy['completed_race_recovery_days_by_priority'].key}:{_race_priority(fact)}",
                ))
        elif classification in _QUALITY_BANDS | {"uncertain_quality"}:
            end = fact.date + timedelta(days=quality_spacing)
            if end >= as_of:
                result.append(RecoveryConstraint(
                    source=f"quality:{fact.id}", start_date=fact.date, end_date=end,
                    hard_excluded_roles=(TrainingRole.QUALITY,), minimum_spacing=quality_spacing,
                    rationale=policy["completed_quality_minimum_spacing_days"].key,
                ))
        elif classification == "strength":
            end = fact.date + timedelta(days=strength_spacing)
            if end >= as_of:
                result.append(RecoveryConstraint(
                    source=f"strength:{fact.id}", start_date=fact.date, end_date=end,
                    hard_excluded_roles=(TrainingRole.STRENGTH,), minimum_spacing=strength_spacing,
                    rationale=policy["completed_strength_minimum_spacing_days"].key,
                ))
    return tuple(sorted(result, key=lambda item: (item.start_date, item.end_date, item.source)))


def _next_race_context(context: NormalizedPlanningContext) -> dict[str, Any]:
    if not context.race_context:
        return {}
    day, priority, event_name, race_type = context.race_context[0]
    race_day = date.fromisoformat(day)
    return {
        "date": day,
        "days_until": (race_day - context.planning_date).days,
        "priority": priority,
        "event_name": event_name,
        "race_type": race_type,
    }


def _phase_context(profile: Mapping[str, Any] | None, as_of: date) -> dict[str, Any]:
    if not profile or not isinstance(profile.get("season"), Mapping):
        return {}
    phases = build_season_phases(dict(profile))
    phase = next((item for item in phases if item["start_date"] <= as_of.isoformat() <= item["end_date"]), None)
    if not phase:
        return {}
    return {"phase_id": phase["phase_id"], "phase_type": phase["phase_type"], "source": "season_phase"}


def _athlete_response_inputs(completed: Iterable[HistoricalFact]) -> dict[str, Any]:
    rpe = []
    notes = []
    for fact in completed:
        actual = fact.completed_actual or {}
        value = actual.get("rpe") or actual.get("actual_rpe")
        if value is not None:
            rpe.append((fact.id, value))
        if actual.get("notes"):
            notes.append(fact.id)
    return {"rpe_by_fact": tuple(sorted(rpe)), "notes_fact_ids": tuple(sorted(notes))}


def reduce_physiological_state(
    context: NormalizedPlanningContext, *, profile: Mapping[str, Any] | None = None,
    policy: Iterable[TrainingPolicyRule] = DEFAULT_PHYSIOLOGICAL_POLICY,
) -> PhysiologicalState:
    """Return a deterministic physiological description of completed history.

    The only operational constants come from the supplied versioned policy.
    Future commitments and provisional identifiers are intentionally ignored.
    """
    rules = _rules_by_key(policy)
    required = {
        "completed_quality_minimum_spacing_days",
        "completed_strength_minimum_spacing_days",
        "completed_race_recovery_days_by_priority",
    }
    missing = required - set(rules)
    if missing:
        raise ValueError(f"missing_physiological_policy:{','.join(sorted(missing))}")
    completed = _completed_facts(context)
    recent_7d = _recent_load(
        completed, as_of=context.planning_date, window_days=7,
        duplicate_facts_excluded=context.diagnostics.duplicates_collapsed,
    )
    recent_14d = _recent_load(
        completed, as_of=context.planning_date, window_days=14,
        duplicate_facts_excluded=context.diagnostics.duplicates_collapsed,
    )
    # Need generation is a 14-day continuous policy.  Keep full history in
    # HistoricalFact for audit, but use only this trailing physiological ledger
    # to avoid old completed work suppressing present needs indefinitely.
    recent_completed = tuple(item for item in completed if item.id in set(recent_14d.counted_fact_ids))
    aerobic = tuple(item.physiological_credit for item in recent_completed if item.physiological_credit.eligible and item.physiological_credit.role in _AEROBIC_ROLES)
    quality = tuple(item.physiological_credit for item in recent_completed if item.physiological_credit.eligible and item.physiological_credit.role is TrainingRole.QUALITY)
    races = tuple(item for item in recent_completed if item.type is HistoricalFactType.RACE_FACT)
    strength = tuple(item.physiological_credit for item in recent_completed if item.physiological_credit.eligible and item.physiological_credit.role is TrainingRole.STRENGTH)
    aerobic_by_band = _classification_credits(recent_completed, set(_AEROBIC_BANDS) | {"uncertain_aerobic"}, TrainingRole.DEDICATED_UT2)
    quality_by_band = _classification_credits(recent_completed, set(_QUALITY_BANDS) | {"uncertain_quality", "RACE"}, TrainingRole.QUALITY)
    durations = {item.id: _actual_duration(item) for item in recent_completed}
    classifications = {item.id: value for item in recent_completed if (value := _classification(item)) is not None}
    race_credits = tuple(PhysiologicalCredit(TrainingRole.RACE, 1, _actual_duration(item), True) for item in races)
    return PhysiologicalState(
        as_of_date=context.planning_date,
        completed_aerobic_exposure=_credit_sum(TrainingRole.DEDICATED_UT2, aerobic),
        completed_quality_exposure=_credit_sum(TrainingRole.QUALITY, (*quality, *race_credits)),
        completed_strength_exposure=_credit_sum(TrainingRole.STRENGTH, strength),
        completed_aerobic_by_classification=aerobic_by_band,
        completed_quality_by_classification=quality_by_band,
        completed_race_exposure=_credit_sum(TrainingRole.RACE, race_credits),
        completed_duration_minutes_by_fact=durations,
        completed_classification_by_fact=classifications,
        recent_7d_load=recent_7d,
        recent_14d_load=recent_14d,
        recent_race_load=races,
        recovery_constraints=_recovery_constraints(completed, context.planning_date, rules),
        next_race_context=_next_race_context(context),
        phase_context=_phase_context(profile, context.planning_date),
        # No progression is derived: season phase is factual context, while a
        # prescription/progression decision belongs to a later milestone.
        progression_context={},
        athlete_response_inputs=_athlete_response_inputs(completed),
        completed_fact_ids=tuple(item.id for item in completed),
    )
