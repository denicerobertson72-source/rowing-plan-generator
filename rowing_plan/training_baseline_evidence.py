"""Diagnostic-only evidence-qualified training baseline and tolerance summary.

This module deliberately characterizes evidence; it does not select a dose,
allocate categories, or alter R2--R5.  It consumes the canonical continuous
exposure ledger so aggregate profile statements are never manufactured as
dated completed training.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Any, Mapping

from .planning_domain import PhysicalCategory, TrainingModality
from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy


BASELINE_EVIDENCE_VERSION = "r9f5b1-baseline-tolerance-evidence-v1"
BASELINE_EVIDENCE_POLICY_IDS = (
    "F01", "F02", "F03", "F04", "F05", "F06", "F09", "F10",
    "F11", "F12", "F13", "F14", "F33", "F44", "F47", "F48",
    "F52", "OP-01", "OP-06",
)


@dataclass(frozen=True)
class BaselineEvidencePolicy:
    """Typed, versioned review-window policy used only for characterization."""

    version: str
    observation_windows_days: tuple[int, ...]
    policy_ids: tuple[str, ...] = BASELINE_EVIDENCE_POLICY_IDS
    configurable_pilot_guidance: bool = True

    def __post_init__(self) -> None:
        if not self.version or not self.observation_windows_days or any(value <= 0 for value in self.observation_windows_days):
            raise ValueError("baseline_evidence_policy_is_invalid")
        object.__setattr__(self, "observation_windows_days", tuple(sorted(set(self.observation_windows_days))))
        object.__setattr__(self, "policy_ids", tuple(sorted(set(self.policy_ids))))


def baseline_evidence_policy(policy: TrainingPolicy = DEFAULT_TRAINING_POLICY) -> BaselineEvidencePolicy:
    """Read the incremental evidence-window configuration from the policy registry."""
    values = policy.rule("baseline_evidence_observation_windows_days").value
    return BaselineEvidencePolicy(
        version=f"{BASELINE_EVIDENCE_VERSION}:{policy.version}",
        observation_windows_days=tuple(int(value) for value in values),
    )


@dataclass(frozen=True)
class BaselineObservation:
    """One known or explicitly unknown baseline observation.

    ``value=0`` is known evidence; ``value=None`` is unknown and must never be
    reinterpreted as no exposure.
    """

    metric: str
    unit: str
    value: int | None
    source: str
    evidence_kind: str
    observation_window_days: int | None = None
    window_start: date | None = None
    window_end: date | None = None
    observed_dates: tuple[date, ...] = ()
    coverage_status: str = "unknown"
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.metric or not self.unit or self.evidence_kind not in {"reported", "completed"}:
            raise ValueError("baseline_observation_identity_is_invalid")
        if self.value is not None and self.value < 0:
            raise ValueError("baseline_observation_value_must_be_nonnegative")
        if self.coverage_status not in {"reported_aggregate", "observed_incomplete", "unknown"}:
            raise ValueError("baseline_observation_coverage_is_invalid")
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("baseline_observation_window_is_incomplete")
        object.__setattr__(self, "observed_dates", tuple(sorted(set(self.observed_dates))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))

    @property
    def known(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "unit": self.unit,
            "value": self.value,
            "known": self.known,
            "explicit_zero": self.value == 0,
            "source": self.source,
            "evidence_kind": self.evidence_kind,
            "observation_window_days": self.observation_window_days,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "observed_dates": [item.isoformat() for item in self.observed_dates],
            "coverage_status": self.coverage_status,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True)
class ObservationWindowEvidence:
    window_days: int
    window_start: date
    as_of_date: date
    observed_completed_dates: tuple[date, ...]
    completed_exposure_count: int
    known_duration_exposure_count: int
    observations: tuple[BaselineObservation, ...]
    session_duration_evidence: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.window_days <= 0 or self.window_start != self.as_of_date - timedelta(days=self.window_days):
            raise ValueError("observation_window_evidence_is_invalid")
        object.__setattr__(self, "observed_completed_dates", tuple(sorted(set(self.observed_completed_dates))))
        object.__setattr__(self, "observations", tuple(sorted(self.observations, key=lambda item: (item.metric, item.source))))
        object.__setattr__(
            self, "session_duration_evidence",
            tuple(MappingProxyType(dict(item)) for item in sorted(self.session_duration_evidence, key=lambda item: repr(dict(item)))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "window_start": self.window_start.isoformat(),
            "as_of_date": self.as_of_date.isoformat(),
            "observed_completed_dates": [item.isoformat() for item in self.observed_completed_dates],
            "completed_exposure_count": self.completed_exposure_count,
            "known_duration_exposure_count": self.known_duration_exposure_count,
            "coverage": "observed_completed_records_only",
            "missing_dates_are_not_rest": True,
            "observations": [item.to_dict() for item in self.observations],
            "session_duration_evidence": [dict(item) for item in self.session_duration_evidence],
        }


@dataclass(frozen=True)
class TrainingBaselineEvidence:
    version: str
    policy_version: str
    policy_ids: tuple[str, ...]
    reported_observations: tuple[BaselineObservation, ...]
    completed_windows: tuple[ObservationWindowEvidence, ...]
    evidence_conflicts: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_ids", tuple(sorted(set(self.policy_ids))))
        object.__setattr__(self, "reported_observations", tuple(sorted(self.reported_observations, key=lambda item: (item.metric, item.source))))
        object.__setattr__(self, "completed_windows", tuple(sorted(self.completed_windows, key=lambda item: item.window_days)))
        object.__setattr__(
            self, "evidence_conflicts",
            tuple(MappingProxyType(dict(item)) for item in sorted(self.evidence_conflicts, key=lambda item: repr(dict(item)))),
        )
        object.__setattr__(self, "limitations", tuple(sorted(set(self.limitations))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "policy_version": self.policy_version,
            "applicable_policy_ids": list(self.policy_ids),
            "reported_observations": [item.to_dict() for item in self.reported_observations],
            "completed_observation_windows": [item.to_dict() for item in self.completed_windows],
            "evidence_conflicts": [dict(item) for item in self.evidence_conflicts],
            "limitations": list(self.limitations),
            "prescription_behavior": "unchanged",
        }


@dataclass(frozen=True)
class ToleranceEvidencePathway:
    pathway: str
    availability: str
    evidence_present: tuple[str, ...]
    missing_contracts: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.pathway not in {"detailed", "basic", "minimal"} or self.availability not in {"available", "partial", "unavailable"}:
            raise ValueError("tolerance_evidence_pathway_is_invalid")
        for field_name in ("evidence_present", "missing_contracts", "limitations"):
            object.__setattr__(self, field_name, tuple(sorted(set(getattr(self, field_name)))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pathway": self.pathway,
            "availability": self.availability,
            "evidence_present": list(self.evidence_present),
            "missing_contracts": list(self.missing_contracts),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class TrainingToleranceEvidence:
    version: str
    pathways: tuple[ToleranceEvidencePathway, ...]
    completion_evidence_count: int
    response_evidence_count: int
    reported_consistency: str | None
    explicit_restriction_codes: tuple[str, ...]
    uncertainty: tuple[str, ...]
    private_readiness_used: bool = False
    progression_tolerance_decision: str = "not_decided_in_diagnostic_slice"

    def __post_init__(self) -> None:
        object.__setattr__(self, "pathways", tuple(sorted(self.pathways, key=lambda item: item.pathway)))
        object.__setattr__(self, "explicit_restriction_codes", tuple(sorted(set(self.explicit_restriction_codes))))
        object.__setattr__(self, "uncertainty", tuple(sorted(set(self.uncertainty))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pathways": [item.to_dict() for item in self.pathways],
            "completion_evidence_count": self.completion_evidence_count,
            "response_evidence_count": self.response_evidence_count,
            "reported_consistency": self.reported_consistency,
            "explicit_restriction_codes": list(self.explicit_restriction_codes),
            "uncertainty": list(self.uncertainty),
            "private_readiness_used": self.private_readiness_used,
            "favorable_tolerance_by_pathway": {},
            "favorable_tolerance_contract_status": "not_yet_populated_by_current_partial_pathways",
            "completion_alone_establishes_favorable_tolerance": False,
            "missing_feedback_establishes_adequate_recovery": False,
            "progression_tolerance_decision": self.progression_tolerance_decision,
            "prescription_behavior": "unchanged",
        }


_CATEGORY_METRICS = {
    PhysicalCategory.ROWING_SPECIFIC.value: "rowing_minutes",
    PhysicalCategory.STRENGTH.value: "strength_minutes",
    PhysicalCategory.GENERAL_AEROBIC.value: "general_aerobic_minutes",
    PhysicalCategory.OTHER.value: "other_physical_minutes",
}
_CORE_REPORTED = (
    ("total_physical_minutes", "minutes_per_week"),
    ("rowing_minutes", "minutes_per_week"),
    ("strength_minutes", "minutes_per_week"),
    ("general_aerobic_minutes", "minutes_per_week"),
    ("other_physical_minutes", "minutes_per_week"),
    ("overall_training_days", "days_per_week"),
    ("rowing_exposures", "exposures_per_week"),
    ("strength_exposures", "exposures_per_week"),
    ("quality_exposures", "exposures_per_week"),
    ("typical_strength_session_duration_minutes", "minutes_per_session"),
)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reported(profile: Mapping[str, Any]) -> tuple[BaselineObservation, ...]:
    athlete = _mapping(profile.get("athlete"))
    context = _mapping(profile.get("training_context"))
    persisted = _mapping(profile.get("current_training_state"))
    candidates = (
        ("total_physical_minutes", "minutes_per_week", athlete, "current_weekly_training_minutes", "athlete.current_weekly_training_minutes"),
        ("rowing_minutes", "minutes_per_week", athlete, "current_approx_weekly_rowing_minutes", "athlete.current_approx_weekly_rowing_minutes"),
        ("rowing_exposures", "exposures_per_week", athlete, "current_rowing_sessions_per_week", "athlete.current_rowing_sessions_per_week"),
        ("strength_exposures", "exposures_per_week", context, "current_strength_sessions_per_week", "training_context.current_strength_sessions_per_week"),
        ("typical_strength_session_duration_minutes", "minutes_per_session", context, "typical_strength_duration_minutes", "training_context.typical_strength_duration_minutes"),
        ("total_physical_minutes", "minutes_per_week", persisted, "approximate_total_physical_minutes", "current_training_state.approximate_total_physical_minutes"),
        ("rowing_minutes", "minutes_per_week", persisted, "approximate_rowing_minutes", "current_training_state.approximate_rowing_minutes"),
        ("rowing_exposures", "exposures_per_week", persisted, "rowing_exposures", "current_training_state.rowing_exposures"),
        ("strength_exposures", "exposures_per_week", persisted, "strength_exposures", "current_training_state.strength_exposures"),
        ("typical_strength_session_duration_minutes", "minutes_per_session", persisted, "typical_strength_duration_minutes", "current_training_state.typical_strength_duration_minutes"),
    )
    observations: list[BaselineObservation] = []
    seen_metrics: set[str] = set()
    for metric, unit, mapping, field, source in candidates:
        if field not in mapping:
            continue
        value = _nonnegative_int(mapping.get(field))
        if value is None:
            continue
        seen_metrics.add(metric)
        observations.append(BaselineObservation(
            metric, unit, value, source, "reported", coverage_status="reported_aggregate",
            provenance=("profile_reported_aggregate", source),
        ))
    for metric, unit in _CORE_REPORTED:
        if metric not in seen_metrics:
            observations.append(BaselineObservation(
                metric, unit, None, "absent_reported_field", "reported",
                coverage_status="unknown", provenance=("reported_value_not_supplied",),
            ))
    return tuple(observations)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _completed_rows(ledger: Mapping[str, Any], *, planning_date: date) -> tuple[Mapping[str, Any], ...]:
    values = []
    for item in ledger.get("exposures", ()):
        if not isinstance(item, Mapping) or item.get("status") != "completed_actual":
            continue
        item_date = _parse_date(item.get("date"))
        if item_date is None or item_date >= planning_date:
            continue
        values.append({**item, "_date": item_date})
    return tuple(sorted(values, key=lambda item: (item["_date"], str(item.get("exposure_id", "")))))


def _window_evidence(rows: tuple[Mapping[str, Any], ...], *, planning_date: date, days: int) -> ObservationWindowEvidence:
    start = planning_date - timedelta(days=days)
    in_window = tuple(item for item in rows if start <= item["_date"] < planning_date)
    dates = tuple(item["_date"] for item in in_window)
    known = tuple(item for item in in_window if _nonnegative_int(item.get("physical_duration_minutes")) is not None)
    observations: list[BaselineObservation] = []

    def observed(metric: str, unit: str, values: tuple[int, ...], provenance: tuple[str, ...]) -> None:
        if not values:
            return
        observations.append(BaselineObservation(
            metric, unit, sum(values), "canonical_completed_exposure_ledger", "completed",
            days, start, planning_date, dates, "observed_incomplete", provenance,
        ))

    observed("total_physical_minutes", "minutes", tuple(_nonnegative_int(item.get("physical_duration_minutes")) or 0 for item in known), ("completed_actual_only", "known_duration_only"))
    for category, metric in _CATEGORY_METRICS.items():
        category_rows = tuple(item for item in known if item.get("physical_category") == category)
        observed(metric, "minutes", tuple(_nonnegative_int(item.get("physical_duration_minutes")) or 0 for item in category_rows), ("completed_actual_only", f"category:{category}"))
        if category_rows:
            observations.append(BaselineObservation(
                f"{category}_exposures", "exposures", len(category_rows),
                "canonical_completed_exposure_ledger", "completed", days, start, planning_date,
                tuple(item["_date"] for item in category_rows), "observed_incomplete", ("completed_actual_only", f"category:{category}"),
            ))
    observations.append(BaselineObservation(
        # This is an occurrence-day metric, not a minute-based metric.  A
        # completed race with unknown duration is therefore counted here but
        # remains excluded from all known-duration minute/category metrics.
        "overall_training_days", "days", len({item["_date"] for item in in_window}),
        "canonical_completed_exposure_ledger", "completed", days, start, planning_date,
        dates, "observed_incomplete", ("completed_actual_only", "duration_not_required_for_occurrence_day_count"),
    ))
    for modality in TrainingModality:
        modality_rows = tuple(item for item in known if item.get("modality") == modality.value)
        if modality_rows:
            observed(f"{modality.value}_minutes", "minutes", tuple(_nonnegative_int(item.get("physical_duration_minutes")) or 0 for item in modality_rows), ("completed_actual_only", f"modality:{modality.value}"))
            observations.append(BaselineObservation(
                f"{modality.value}_exposures", "exposures", len(modality_rows),
                "canonical_completed_exposure_ledger", "completed", days, start, planning_date,
                tuple(item["_date"] for item in modality_rows), "observed_incomplete", ("completed_actual_only", f"modality:{modality.value}"),
            ))
    quality_rows = tuple(item for item in in_window if item.get("physiological_band") in {"AT", "TR", "AN", "PP", "RACE"})
    if quality_rows:
        observations.append(BaselineObservation(
            "quality_exposures", "exposures", len(quality_rows), "canonical_completed_exposure_ledger", "completed",
            days, start, planning_date, tuple(item["_date"] for item in quality_rows), "observed_incomplete",
            ("completed_actual_only", "explicitly_characterized_quality_band"),
        ))
    duration_evidence: list[dict[str, Any]] = []
    for scope, label in (("physical_category", "category"), ("modality", "modality")):
        keys = sorted({str(item.get(scope)) for item in known if item.get(scope)})
        for key in keys:
            values = sorted(
                int(item["physical_duration_minutes"])
                for item in known if item.get(scope) == key and item.get("physical_duration_minutes") is not None
            )
            duration_evidence.append({
                "scope": label, "value": key, "observed_session_count": len(values),
                "minimum_minutes": values[0], "maximum_minutes": values[-1],
                "coverage_status": "observed_incomplete",
                "provenance": ["completed_actual_only", "known_duration_only"],
            })
    return ObservationWindowEvidence(
        days, start, planning_date, dates, len(in_window), len(known), tuple(observations),
        tuple(duration_evidence),
    )


def _evidence_conflicts(reported: tuple[BaselineObservation, ...], windows: tuple[ObservationWindowEvidence, ...]) -> tuple[dict[str, Any], ...]:
    if not windows:
        return ()
    widest = max(windows, key=lambda item: item.window_days)
    completed = {item.metric: item for item in widest.observations}
    values: list[dict[str, Any]] = []
    for item in reported:
        if not item.known or item.metric not in {"total_physical_minutes", "rowing_minutes"}:
            continue
        observed = completed.get(item.metric)
        if observed is None or observed.value is None:
            continue
        normalized_weekly = round(observed.value * 7 / widest.window_days)
        if normalized_weekly != item.value:
            values.append({
                "metric": item.metric,
                "reported_source": item.source,
                "reported_value": item.value,
                "completed_observed_value": observed.value,
                "completed_window_days": widest.window_days,
                "completed_weekly_equivalent": normalized_weekly,
                "status": "different_evidence_preserved_not_merged",
                "limitation": "completed_history_coverage_is_observed_not_proven_complete",
            })
    return tuple(values)


def _tolerance(rows: tuple[Mapping[str, Any], ...], *, profile: Mapping[str, Any], current: Any, ledger: Mapping[str, Any]) -> TrainingToleranceEvidence:
    rpe_count = sum(_nonnegative_int(item.get("reported_rpe")) is not None for item in rows)
    completion = len(rows)
    detailed = ToleranceEvidencePathway(
        "detailed", "partial" if completion else "unavailable",
        ("completed_workout_occurrences",) if completion else (),
        ("comparable_workout_response", "athlete_confirmed_recovery"),
        ("completion_alone_does_not_establish_favorable_tolerance",),
    )
    basic = ToleranceEvidencePathway(
        "basic", "partial" if completion or rpe_count else "unavailable",
        tuple(item for item, present in (("completed_workout_occurrences", completion), ("recorded_rpe", rpe_count)) if present),
        ("athlete_confirmed_recovery",) if not rpe_count else (),
        ("recorded_rpe_is_not_a_recovery_clearance", "missing_feedback_is_not_adequate_recovery"),
    )
    minimal = ToleranceEvidencePathway(
        "minimal", "unavailable", (),
        ("athlete_confirmation_consistency", "athlete_confirmation_appropriate_challenge", "athlete_confirmation_recovery", "athlete_confirmation_no_significant_interruption"),
        ("no_authorized_minimal_confirmation_contract",),
    )
    restriction_codes = [
        str(item.get("purpose")) for item in ledger.get("exposures", ())
        if isinstance(item, Mapping) and item.get("status") == "authoritative_commitment" and item.get("purpose") == "complete_rest"
    ]
    training_context = _mapping(profile.get("training_context"))
    restriction_source = str(training_context.get("training_restriction_source") or "none")
    if restriction_source != "none":
        restriction_codes.append(f"reported_training_restriction_source:{restriction_source}")
    if training_context.get("training_restriction_summary"):
        restriction_codes.append("reported_training_restriction_summary_present")
    return TrainingToleranceEvidence(
        version=BASELINE_EVIDENCE_VERSION,
        pathways=(detailed, basic, minimal),
        completion_evidence_count=completion,
        response_evidence_count=rpe_count,
        reported_consistency=getattr(current, "consistency", None),
        explicit_restriction_codes=tuple(restriction_codes),
        uncertainty=(
            "private_readiness_excluded_from_planning_and_diagnostic_evidence",
            "performance_assessments_are_not_training_tolerance_evidence",
            "no_composite_fatigue_or_recovery_score",
        ),
    )


def build_training_baseline_and_tolerance_evidence(
    profile: Mapping[str, Any], *, exposure_ledger: Mapping[str, Any], current_training_state: Any,
    planning_date: date, policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
) -> tuple[TrainingBaselineEvidence, TrainingToleranceEvidence]:
    """Characterize existing facts without altering any planning-stage input."""
    config = baseline_evidence_policy(policy)
    reported = _reported(profile)
    rows = _completed_rows(exposure_ledger, planning_date=planning_date)
    windows = tuple(_window_evidence(rows, planning_date=planning_date, days=days) for days in config.observation_windows_days)
    baseline = TrainingBaselineEvidence(
        BASELINE_EVIDENCE_VERSION, config.version, config.policy_ids, reported, windows,
        _evidence_conflicts(reported, windows),
        (
            "reported_aggregate_baselines_are_not_converted_to_dated_completed_exposures",
            "sparse_completed_history_does_not_establish_inactivity_or_rest",
            "unknown_category_or_modality_values_remain_unknown",
            "prospective_training_is_excluded_from_completed_baseline_evidence",
        ),
    )
    return baseline, _tolerance(rows, profile=profile, current=current_training_state, ledger=exposure_ledger)
