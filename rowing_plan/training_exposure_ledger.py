"""Continuous, diagnostic-only training exposure chronology for V3.

The ledger deliberately combines existing facts without becoming a second
planner, load target, or fatigue model.  It records what is known, preserves
what is uncertain, and keeps completed physiology distinct from prospective
calendar work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, Iterable, Mapping

from .physiological_evidence import PhysiologicalEvidenceProfile
from .planning_domain import (
    AuthorityType,
    ComponentAuthority,
    HistoricalFact,
    HistoricalFactType,
    PhysicalCategory,
    PhysiologicalState,
    TrainingComponentPurpose,
    TrainingDayPlanResult,
    TrainingDayState,
    TrainingRole,
)
from .training_policy import DEFAULT_TRAINING_POLICY


EXPOSURE_LEDGER_VERSION = "r9f4a-continuous-exposure-ledger-1"
_QUALITY_BANDS = frozenset({"AT", "TR", "AN", "PP", "RACE"})
_ROWING_ROLES = frozenset({
    TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC,
    TrainingRole.UT1_AEROBIC_STRENGTH, TrainingRole.QUALITY,
    TrainingRole.RACE, TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR,
    TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP, TrainingRole.TECHNICAL_REENTRY,
    TrainingRole.RECOVERY_AEROBIC, TrainingRole.TAPER_MAINTENANCE,
    TrainingRole.RACE_PREPARATION, TrainingRole.COURSE_PRACTICE,
})


class ExposureStatus(str, Enum):
    COMPLETED_ACTUAL = "completed_actual"
    AUTHORITATIVE_COMMITMENT = "authoritative_commitment"
    GENERATED_FUTURE = "generated_future"
    PLANNED_UNCOMPLETED = "planned_uncompleted"
    CANCELLED_OR_MISSED = "cancelled_or_missed"
    ASSESSMENT_EVIDENCE = "assessment_evidence"


class ExposureSource(str, Enum):
    COMPLETED_HISTORY = "completed_history"
    AUTHORITATIVE_COMMITMENT = "authoritative_commitment"
    GENERATED_CALENDAR = "generated_calendar"
    ASSESSMENT_EVIDENCE = "assessment_evidence"


@dataclass(frozen=True)
class TrainingExposure:
    """One dated evidence item; duration remains absent when unknown."""

    exposure_id: str
    source_lineage_id: str
    exposure_date: date
    status: ExposureStatus
    source: ExposureSource
    provenance: tuple[str, ...]
    physical_category: str | None = None
    modality: str | None = None
    purpose: str | None = None
    physiological_band: str | None = None
    physical_duration_minutes: int | None = None
    known_work_duration_minutes: int | None = None
    known_high_intensity_work_minutes: int | None = None
    strength_exposure: bool = False
    race_exposure: bool = False
    assessment_exposure: bool = False
    recovery_or_technical_purpose: bool = False
    reported_rpe: int | None = None
    data_completeness: str = "partial"
    uncertainty: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.exposure_id or not self.source_lineage_id:
            raise ValueError("training_exposure_identity_is_required")
        for value in (
            self.physical_duration_minutes, self.known_work_duration_minutes,
            self.known_high_intensity_work_minutes, self.reported_rpe,
        ):
            if value is not None and value < 0:
                raise ValueError("training_exposure_values_must_be_nonnegative")
        if self.known_high_intensity_work_minutes and self.physiological_band not in _QUALITY_BANDS:
            raise ValueError("training_exposure_high_intensity_band_is_required")
        if self.data_completeness not in {"known_duration", "partial", "unknown"}:
            raise ValueError("training_exposure_data_completeness_is_invalid")
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))
        object.__setattr__(self, "uncertainty", tuple(sorted(set(self.uncertainty))))
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "exposure_id": self.exposure_id,
            "source_lineage_id": self.source_lineage_id,
            "date": self.exposure_date.isoformat(),
            "status": self.status.value,
            "source": self.source.value,
            "provenance": list(self.provenance),
            "physical_category": self.physical_category,
            "modality": self.modality,
            "purpose": self.purpose,
            "physiological_band": self.physiological_band,
            "physical_duration_minutes": self.physical_duration_minutes,
            "known_work_duration_minutes": self.known_work_duration_minutes,
            "known_high_intensity_work_minutes": self.known_high_intensity_work_minutes,
            "strength_exposure": self.strength_exposure,
            "race_exposure": self.race_exposure,
            "assessment_exposure": self.assessment_exposure,
            "recovery_or_technical_purpose": self.recovery_or_technical_purpose,
            "reported_rpe": self.reported_rpe,
            "data_completeness": self.data_completeness,
            "uncertainty": list(self.uncertainty),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class ReportedTrainingBaseline:
    """Undated athlete report, intentionally not manufactured as sessions."""

    source: str
    observation_window_days: int | None
    total_physical_minutes: int | None
    rowing_minutes: int | None
    strength_exposures: int | None
    consistency: str | None
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "observation_window_days": self.observation_window_days,
            "total_physical_minutes": self.total_physical_minutes,
            "rowing_minutes": self.rowing_minutes,
            "strength_exposures": self.strength_exposures,
            "consistency": self.consistency,
            "limitations": list(self.limitations),
            "dated_exposures_created": 0,
        }


def _role_category(role: TrainingRole | None) -> str | None:
    if role is TrainingRole.STRENGTH:
        return PhysicalCategory.STRENGTH.value
    if role is TrainingRole.GENERAL_AEROBIC:
        return PhysicalCategory.GENERAL_AEROBIC.value
    if role in _ROWING_ROLES:
        return PhysicalCategory.ROWING_SPECIFIC.value
    return None


def _raw_modality(value: Any) -> str | None:
    value = str(value or "").strip().lower()
    return value or None


def _band(*values: Any) -> str | None:
    for value in values:
        normalized = str(value or "").upper()
        if normalized in _QUALITY_BANDS | {"UT3", "UT2", "UT1"}:
            return normalized
    return None


def _integer(value: Any) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _actual_duration(fact: HistoricalFact) -> int | None:
    actual = fact.completed_actual or {}
    for key in ("actual_duration_min", "duration_minutes", "total_training_minutes", "total_cardio_minutes"):
        value = _integer(actual.get(key))
        if value is not None:
            return value
    if fact.type is not HistoricalFactType.RACE_FACT and fact.physiological_credit.eligible:
        return fact.physiological_credit.minutes
    return None


def _fact_status(fact: HistoricalFact) -> ExposureStatus:
    if fact.type is HistoricalFactType.RACE_FACT:
        return ExposureStatus.COMPLETED_ACTUAL
    actual = fact.completed_actual or {}
    status = str(actual.get("status") or "").lower()
    if status in {"skipped", "missed", "cancelled", "canceled"}:
        return ExposureStatus.CANCELLED_OR_MISSED
    if actual or fact.physiological_credit.eligible:
        return ExposureStatus.COMPLETED_ACTUAL
    return ExposureStatus.PLANNED_UNCOMPLETED


def _exposure_from_fact(fact: HistoricalFact) -> TrainingExposure:
    actual = fact.completed_actual or {}
    planned = fact.planned_prescription or {}
    status = _fact_status(fact)
    role = fact.physiological_credit.role
    race = fact.type is HistoricalFactType.RACE_FACT
    band = _band(actual.get("actual_intensity"), actual.get("intensity_band"), actual.get("band"), planned.get("band"))
    duration = _actual_duration(fact) if status is ExposureStatus.COMPLETED_ACTUAL else None
    work = fact.physiological_credit.minutes if role is TrainingRole.QUALITY and fact.physiological_credit.eligible else None
    high_work = work if band in _QUALITY_BANDS else None
    purpose = (role.value if role else _raw_modality(planned.get("mode")))
    modality = _raw_modality(actual.get("activity_type") or actual.get("mode") or planned.get("mode"))
    limitations: list[str] = []
    uncertainty: list[str] = []
    if status is ExposureStatus.PLANNED_UNCOMPLETED:
        limitations.append("planned_work_has_no_completed_physiological_credit")
    if status is ExposureStatus.CANCELLED_OR_MISSED:
        limitations.append("cancelled_or_missed_work_has_no_completed_physiological_credit")
    if race and duration is None:
        uncertainty.extend(("race_occurred", "race_physical_duration_unknown", "race_quality_work_duration_unknown"))
    if duration is None and not race:
        uncertainty.append("physical_duration_unknown")
    return TrainingExposure(
        f"historical:{fact.id}", fact.provenance.source_object_id or fact.id, fact.date,
        status, ExposureSource.COMPLETED_HISTORY,
        ("normalized_historical_fact", fact.provenance.source_type.value, *fact.provenance.adaptation_notes),
        _role_category(role), modality, purpose, band, duration, work, high_work,
        role is TrainingRole.STRENGTH, race, False,
        role in {TrainingRole.RECOVERY_AEROBIC, TrainingRole.TECHNICAL_REENTRY, TrainingRole.COURSE_PRACTICE},
        _integer(actual.get("rpe")),
        "known_duration" if duration is not None else "partial", tuple(uncertainty), tuple(limitations),
    )


def _exposure_from_commitment(item: Any) -> TrainingExposure | None:
    if item.date is None:
        return None
    race = item.type is AuthorityType.RACE
    rest = item.type is AuthorityType.FIXED_REST
    return TrainingExposure(
        f"commitment:{item.id}", item.provenance.source_object_id or item.id, item.date,
        ExposureStatus.AUTHORITATIVE_COMMITMENT, ExposureSource.AUTHORITATIVE_COMMITMENT,
        ("normalized_authoritative_commitment", item.authority_source.value, item.type.value, *item.provenance.adaptation_notes),
        PhysicalCategory.ROWING_SPECIFIC.value if race else None,
        None, "complete_rest" if rest else ("race" if race else item.type.value),
        "RACE" if race else None,
        item.reservation_minutes if item.reservation_minutes else None,
        None, None, False, race, False, rest, None,
        "known_duration" if item.reservation_minutes else "partial",
        ("prospective_authoritative_commitment",) if not rest else (),
        ("authoritative_commitment_is_not_completed_physiological_credit",),
    )


def _exposures_from_calendar(calendar: TrainingDayPlanResult) -> tuple[TrainingExposure, ...]:
    values: list[TrainingExposure] = []
    for day in calendar.days:
        if day.state is TrainingDayState.DESIGNATED_REST:
            values.append(TrainingExposure(
                f"calendar-rest:{day.date.isoformat()}", f"calendar-rest:{day.date.isoformat()}", day.date,
                ExposureStatus.PLANNED_UNCOMPLETED, ExposureSource.GENERATED_CALENDAR,
                ("r4_training_day", "designated_rest"), None, None, "complete_rest", None,
                None, None, None, False, False, False, True, None, "known_duration", (),
                ("planned_rest_is_not_completed_physiological_credit",),
            ))
        for component in day.components:
            status = ExposureStatus.AUTHORITATIVE_COMMITMENT if component.authority is ComponentAuthority.AUTHORITATIVE else ExposureStatus.GENERATED_FUTURE
            values.append(TrainingExposure(
                f"component:{component.component_id}", component.source_lineage_id, day.date, status,
                ExposureSource.GENERATED_CALENDAR,
                ("r4_training_component", component.authority.value, *component.rationale_codes, *component.capability_provenance),
                component.physical_category.value,
                component.modality.value if component.modality else None,
                component.purpose.value, component.physiological_band,
                component.duration_minutes, component.quality_work_minutes,
                component.quality_work_minutes if component.physiological_band in _QUALITY_BANDS else None,
                component.physical_category is PhysicalCategory.STRENGTH,
                component.purpose is TrainingComponentPurpose.RACE,
                False,
                component.purpose in {TrainingComponentPurpose.RECOVERY, TrainingComponentPurpose.TECHNICAL},
                None, "known_duration", (),
                ("prospective_work_is_not_completed_physiological_credit",),
            ))
    return tuple(values)


def _assessment_exposures(evidence: PhysiologicalEvidenceProfile, facts: Iterable[HistoricalFact]) -> tuple[TrainingExposure, ...]:
    known_completed_assessment_ids = {
        str(value)
        for fact in facts for value in ((fact.completed_actual or {}).get("assessment_id"), (fact.completed_actual or {}).get("performance_test_id"))
        if value not in (None, "")
    }
    values = []
    for measurement in evidence.measurements:
        if measurement.assessment_date is None or measurement.measurement_id in known_completed_assessment_ids:
            continue
        values.append(TrainingExposure(
            f"assessment:{measurement.measurement_id}", measurement.measurement_id, measurement.assessment_date,
            ExposureStatus.ASSESSMENT_EVIDENCE, ExposureSource.ASSESSMENT_EVIDENCE,
            ("r9f3b_physiological_evidence", *measurement.provenance), None, measurement.modality,
            "assessment", None, None, None, None, False, False, True, False, None, "unknown",
            ("assessment_occurrence_known", "complete_assessment_session_duration_unknown"),
            ("does_not_create_completed_physical_minutes", "does_not_create_recovery_score"),
        ))
    return tuple(values)


def _deduplicate(values: Iterable[TrainingExposure]) -> tuple[tuple[TrainingExposure, ...], tuple[str, ...]]:
    """Collapse only exact lineage/date duplicates; uncertain matches remain visible."""
    chosen: dict[tuple[date, str], TrainingExposure] = {}
    collapsed: list[str] = []
    rank = {
        ExposureStatus.COMPLETED_ACTUAL: 0,
        ExposureStatus.AUTHORITATIVE_COMMITMENT: 1,
        ExposureStatus.GENERATED_FUTURE: 2,
        ExposureStatus.PLANNED_UNCOMPLETED: 3,
        ExposureStatus.CANCELLED_OR_MISSED: 4,
        ExposureStatus.ASSESSMENT_EVIDENCE: 5,
    }
    for item in values:
        key = (item.exposure_date, item.source_lineage_id)
        prior = chosen.get(key)
        if prior is None:
            chosen[key] = item
        elif rank[item.status] < rank[prior.status]:
            chosen[key] = item
            collapsed.append(prior.exposure_id)
        else:
            collapsed.append(item.exposure_id)
    return tuple(sorted(chosen.values(), key=lambda item: (item.exposure_date, item.exposure_id))), tuple(sorted(collapsed))


def _daily_summary(exposures: Iterable[TrainingExposure]) -> tuple[dict[str, Any], ...]:
    grouped: dict[date, list[TrainingExposure]] = {}
    for item in exposures:
        grouped.setdefault(item.exposure_date, []).append(item)
    values = []
    for day, items in sorted(grouped.items()):
        completed = [item for item in items if item.status is ExposureStatus.COMPLETED_ACTUAL]
        prospective = [item for item in items if item.status in {ExposureStatus.GENERATED_FUTURE, ExposureStatus.AUTHORITATIVE_COMMITMENT, ExposureStatus.PLANNED_UNCOMPLETED}]
        def minutes(rows: Iterable[TrainingExposure]) -> int:
            return sum(item.physical_duration_minutes or 0 for item in rows)
        values.append({
            "date": day.isoformat(),
            "completed_physical_minutes": minutes(completed),
            "prospective_physical_minutes": minutes(prospective),
            "categories": {key: sum(item.physical_duration_minutes or 0 for item in items if item.physical_category == key)
                           for key in sorted({item.physical_category for item in items if item.physical_category})},
            "modalities": sorted({item.modality for item in items if item.modality}),
            "purposes": sorted({item.purpose for item in items if item.purpose}),
            "strength_exposure_count": sum(item.strength_exposure for item in items),
            "quality_exposure_count": sum(item.physiological_band in _QUALITY_BANDS for item in items),
            "race_exposure_count": sum(item.race_exposure for item in items),
            "assessment_exposure_count": sum(item.assessment_exposure for item in items),
            "known_demanding_work_minutes": sum(item.known_high_intensity_work_minutes or 0 for item in items),
            "unknown_work_duration_count": sum(item.known_work_duration_minutes is None and (item.race_exposure or item.assessment_exposure) for item in items),
            "combined_training_day": sum(item.physical_duration_minutes is not None for item in items) > 1,
            "explicit_recovery_or_technical": any(item.recovery_or_technical_purpose for item in items),
            "explicit_complete_rest": any(item.purpose == "complete_rest" for item in items),
            "exposure_ids": [item.exposure_id for item in sorted(items, key=lambda item: item.exposure_id)],
        })
    return tuple(values)


def _rolling_summary(exposures: Iterable[TrainingExposure], *, as_of: date, window_days: int) -> dict[str, Any]:
    start = as_of - timedelta(days=window_days)
    rows = [
        item for item in exposures
        if item.status is ExposureStatus.COMPLETED_ACTUAL and start <= item.exposure_date < as_of
    ]
    return {
        "window_days": window_days,
        "window_start": start.isoformat(),
        "as_of_date": as_of.isoformat(),
        "completed_physical_minutes": sum(item.physical_duration_minutes or 0 for item in rows),
        "strength_exposures": sum(item.strength_exposure for item in rows),
        "quality_exposures": sum(item.physiological_band in _QUALITY_BANDS for item in rows),
        "race_exposures": sum(item.race_exposure for item in rows),
        "known_high_intensity_work_minutes": sum(item.known_high_intensity_work_minutes or 0 for item in rows),
        "unknown_work_duration_count": sum(item.known_work_duration_minutes is None and (item.race_exposure or item.assessment_exposure) for item in rows),
        "exposure_ids": [item.exposure_id for item in rows],
    }


def _sequence_policy_inventory() -> tuple[dict[str, Any], ...]:
    rules = {item.key: item for item in DEFAULT_TRAINING_POLICY.rules}
    definitions = (
        ("quality_minimum_spacing_days", "rowing_plan.sequence_constraints.evaluate_sequence_constraint", "quality", "date_only", "continuous", "completed_and_projected", "coach_override"),
        ("strength_minimum_spacing_days", "rowing_plan.sequence_constraints.evaluate_sequence_constraint", "strength", "date_only", "continuous", "completed_and_projected", "coach_override"),
        ("race_recovery_days_by_priority", "rowing_plan.physiological_state._recovery_constraints", "race_to_strength_or_quality", "date_only", "continuous", "completed_race_and_projected_race", "priority_scaled"),
        ("race_soft_recovery_days_by_priority", "rowing_plan.sequence_constraints.evaluate_sequence_constraint", "race_to_training_load", "date_only", "continuous", "completed_race_and_projected_race", "low_cost_roles_allowed"),
        ("r6_recent_quality_suppression_hours", "rowing_plan.rowing_quality_v3.generate_rowing_quality_plan", "quality_band", "date_only_for_completed_history", "continuous_completed_history", "completed_quality", "legacy_r6_only"),
    )
    values = []
    for key, location, applies, resolution, scope, statuses, exceptions in definitions:
        rule = rules[key]
        values.append({
            "policy_key": key, "code_location": location, "policy_provenance": list(rule.source_ids),
            "rule_class": rule.rule_class.value, "applicable_exposure_types": applies,
            "required_resolution": resolution, "scope": scope, "applies_to": statuses,
            "existing_exceptions_or_limitations": exceptions,
        })
    return tuple(values)


def _reported_baseline(current: Any) -> ReportedTrainingBaseline:
    return ReportedTrainingBaseline(
        getattr(getattr(current, "source", None), "value", "unknown"),
        getattr(current, "observation_window_days", None),
        getattr(current, "approximate_total_physical_minutes", None),
        getattr(current, "approximate_rowing_minutes", None),
        getattr(current, "strength_exposures", None),
        getattr(current, "consistency", None),
        (
            "aggregate_context_is_not_converted_to_dated_exposures",
            "sparse_completed_history_does_not_establish_inactivity",
            "reported_strength_frequency_is_not_exact_strength_spacing_evidence",
        ),
    )


def build_continuous_training_exposure_ledger(
    *, normalized_context: Any, physiological_state: PhysiologicalState,
    current_training: Any, training_days: TrainingDayPlanResult,
    physiological_evidence: PhysiologicalEvidenceProfile,
    allocation_segments: Iterable[Any], planning_start: date, planning_end: date,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the R9F.4a diagnostic chronology without influencing planning."""
    historical = tuple(normalized_context.historical_facts)
    raw = [*(_exposure_from_fact(item) for item in historical)]
    raw.extend(item for item in (_exposure_from_commitment(item) for item in normalized_context.authoritative_commitments) if item is not None)
    raw.extend(_exposures_from_calendar(training_days))
    raw.extend(_assessment_exposures(physiological_evidence, historical))
    exposures, collapsed = _deduplicate(raw)
    completed_races = [item for item in exposures if item.status is ExposureStatus.COMPLETED_ACTUAL and item.race_exposure]
    historical_race_facts = [item for item in historical if item.type is HistoricalFactType.RACE_FACT]
    race_scope_difference = len(completed_races) != physiological_state.completed_race_exposure.exposures
    segments = tuple(allocation_segments)
    boundaries = [
        {"previous_end": left.end_date.isoformat(), "next_start": right.start_date.isoformat(), "contiguous": right.start_date == left.end_date + timedelta(days=1)}
        for left, right in zip(segments, segments[1:])
    ]
    generated = [item for item in exposures if item.status in {ExposureStatus.GENERATED_FUTURE, ExposureStatus.AUTHORITATIVE_COMMITMENT}]
    previous = [item for item in exposures if item.exposure_date < planning_start]
    return {
        "version": EXPOSURE_LEDGER_VERSION,
        "diagnostic_only": True,
        "prescription_behavior": "unchanged",
        "planning_start": planning_start.isoformat(), "planning_end": planning_end.isoformat(),
        "exposures": [item.to_dict() for item in exposures],
        "reported_training_baseline": _reported_baseline(current_training).to_dict(),
        "daily_summaries": list(_daily_summary(exposures)),
        "rolling_completed_summaries": {
            "7d": _rolling_summary(exposures, as_of=planning_start, window_days=7),
            "14d": _rolling_summary(exposures, as_of=planning_start, window_days=14),
        },
        "continuity": {
            "cadence_boundaries": boundaries,
            "historical_exposure_count_before_planning_start": len(previous),
            "prospective_exposure_count": len(generated),
            "continuity_rule": "cadence_boundaries_do_not_reset_exposure_chronology",
        },
        "race_reconciliation": {
            "completed_race_occurrence_count_all_history": len(completed_races),
            "completed_race_fact_count": len(historical_race_facts),
            "trailing_14d_recovery_race_exposure_count": physiological_state.completed_race_exposure.exposures,
            "quality_history_race_count": sum(item.race_exposure for item in exposures if item.status is ExposureStatus.COMPLETED_ACTUAL),
            "duplicate_lineage_collapsed": list(collapsed),
            "scope_difference_explained": race_scope_difference,
            "scope_explanation": (
                "quality_history_and_ledger_retain_all_completed_race_occurrences; "
                "PhysiologicalState completed_race_exposure is intentionally trailing_14d_recovery_context"
                if race_scope_difference else "completed_race_scopes_agree_for_the_current_observation_window"
            ),
            "unknown_race_work_is_not_zero": any(item.race_exposure and item.known_work_duration_minutes is None for item in exposures),
        },
        "assessment_reconciliation": {
            "assessment_occurrence_count": sum(item.assessment_exposure for item in exposures),
            "known_completed_assessment_matches_are_not_duplicated": True,
            "unknown_complete_session_duration_is_preserved": True,
        },
        "sequence_policy_inventory": list(_sequence_policy_inventory()),
        "goal_integration_audit": {
            "planning_goals": dict(profile.get("planning_goals", {})) if isinstance(profile.get("planning_goals"), Mapping) else {},
            "legacy_goal_record_count": len(profile.get("goals", ())) if isinstance(profile.get("goals"), (tuple, list)) else 0,
            "legacy_development_focus": (
                profile.get("preferences", {}).get("development_focus")
                if isinstance(profile.get("preferences"), Mapping) else None
            ),
            "finding": "development_recommendation_reads_planning_goals_and_legacy_goals_only; category_allocation_preference_is_not_promoted_to_primary_goal",
        },
        "training_history_integration_audit": {
            "current_training_source": getattr(getattr(current_training, "source", None), "value", None),
            "observation_window_days": getattr(current_training, "observation_window_days", None),
            "reported_total_physical_minutes": getattr(current_training, "approximate_total_physical_minutes", None),
            "reported_rowing_minutes": getattr(current_training, "approximate_rowing_minutes", None),
            "reported_strength_exposures": getattr(current_training, "strength_exposures", None),
            "finding": "reported_aggregate_context_is_available_to_recommendation_as_source_and_consistency_context; sparse_logs_remain_incomplete_individual_evidence",
        },
        "privacy": {
            "private_readiness_used": False,
            "free_text_notes_exposed": False,
            "time_resolution": "date_only_when_exact_time_is_not_recorded",
        },
    }
