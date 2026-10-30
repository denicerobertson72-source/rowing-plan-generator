"""Typed contracts for the future continuous planning core.

These internal models deliberately have no current V2 scheduler consumer.  They
make fact, authority, physiological-credit, and future-placement semantics
explicit before the replacement solver is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class _TextEnum(str, Enum):
    pass


class HistoricalFactType(_TextEnum):
    PRESCRIBED_SESSION = "prescribed_session"
    COMPLETED_PLANNED_SESSION = "completed_planned_session"
    OPTIONAL_ACTIVITY = "optional_activity"
    RACE_FACT = "race_fact"


class AuthorityType(_TextEnum):
    RACE = "race"
    COURSE_PRACTICE = "course_practice"
    PRIVATE_COACHING = "private_coaching"
    FIXED_COACHING = "fixed_coaching"
    FIXED_RECURRING_ACTIVITY = "fixed_recurring_activity"
    FIXED_REST = "fixed_rest"
    USER_FIXED_PLACEMENT = "user_fixed_placement"
    EXPLICIT_API_LOCK = "explicit_api_lock"


class AuthoritySource(_TextEnum):
    RACE_PROFILE = "race_profile"
    RECURRING_ACTIVITY = "recurring_activity"
    USER_OVERRIDE = "user_override"
    PRIOR_PLAN = "prior_plan"
    EXPLICIT_API_LOCK = "explicit_api_lock"


class ConflictBehavior(_TextEnum):
    CALENDAR_BLOCK = "calendar_block"
    PRESERVE_AND_REPORT = "preserve_and_report"
    REJECT = "reject"


class TrainingRole(_TextEnum):
    DEDICATED_UT2 = "dedicated_ut2"
    LONG_AEROBIC = "long_aerobic"
    UT1_AEROBIC_STRENGTH = "ut1_aerobic_strength"
    QUALITY = "quality"
    STRENGTH = "strength"
    REST = "rest"
    COACHED_TRAINING = "coached_training"
    PRIVATE_COACHING = "private_coaching"
    RACE = "race"
    COURSE_PRACTICE = "course_practice"
    THRESHOLD_AT = "threshold_at"
    RACE_SPECIFIC_TR = "race_specific_tr"
    ANAEROBIC_AN = "anaerobic_an"
    POWER_PP = "power_pp"
    TECHNICAL_REENTRY = "technical_reentry"
    RECOVERY_AEROBIC = "recovery_aerobic"
    TAPER_MAINTENANCE = "taper_maintenance"
    RACE_PREPARATION = "race_preparation"


class RuleClass(_TextEnum):
    RESEARCH_CONSTRAINED = "research_constrained"
    EVIDENCE_INFORMED_HEURISTIC = "evidence_informed_heuristic"
    COACH_OR_ATHLETE_PREFERENCE = "coach_or_athlete_preference"
    SOFTWARE_IMPLEMENTATION_CHOICE = "software_implementation_choice"


class SequenceEligibility(_TextEnum):
    ELIGIBLE = "eligible"
    ELIGIBLE_REDUCED = "eligible_reduced"
    ELIGIBLE_LOW_COST_ONLY = "eligible_low_cost_only"
    DISCOURAGED = "discouraged"
    PROHIBITED = "prohibited"


class PlacementAuthorityStatus(_TextEnum):
    PROVISIONAL = "provisional"
    AUTHORITATIVE = "authoritative"
    USER_FIXED = "user_fixed"


class ProvenanceSource(_TextEnum):
    PRIOR_PLAN = "prior_plan"
    COMPLETED_LOG = "completed_log"
    OPTIONAL_ACTIVITY_LOG = "optional_activity_log"
    RACE_PROFILE = "race_profile"
    RECURRING_ACTIVITY = "recurring_activity"
    USER_OVERRIDE = "user_override"
    LEGACY_V2 = "legacy_v2"
    LEGACY_LOCKED_SESSION = "legacy_locked_session"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _parse_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


@dataclass(frozen=True)
class Provenance:
    source_object_id: str
    source_type: ProvenanceSource
    source_policy_version: str | None = None
    rule_class: RuleClass | None = None
    original_date: date | None = None
    original_serialized_identity: tuple[str, ...] = ()
    adaptation_notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_object_id": self.source_object_id,
            "source_type": self.source_type.value,
            "source_policy_version": self.source_policy_version,
            "rule_class": self.rule_class.value if self.rule_class else None,
            "original_date": self.original_date.isoformat() if self.original_date else None,
            "original_serialized_identity": list(self.original_serialized_identity),
            "adaptation_notes": list(self.adaptation_notes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Provenance":
        return cls(
            source_object_id=str(value["source_object_id"]),
            source_type=ProvenanceSource(value["source_type"]),
            source_policy_version=value.get("source_policy_version"),
            rule_class=RuleClass(value["rule_class"]) if value.get("rule_class") else None,
            original_date=_parse_date(value.get("original_date")),
            original_serialized_identity=tuple(map(str, value.get("original_serialized_identity", ()))),
            adaptation_notes=tuple(map(str, value.get("adaptation_notes", ()))),
        )


@dataclass(frozen=True)
class PhysiologicalCredit:
    """Completed physiological credit; planned work never creates this credit."""

    role: TrainingRole | None = None
    exposures: int = 0
    minutes: int = 0
    eligible: bool = False

    def __post_init__(self) -> None:
        if self.exposures < 0 or self.minutes < 0:
            raise ValueError("physiological_credit_must_be_nonnegative")
        if not self.eligible and (self.exposures or self.minutes):
            raise ValueError("ineligible_credit_must_be_zero")

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role.value if self.role else None, "exposures": self.exposures, "minutes": self.minutes, "eligible": self.eligible}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PhysiologicalCredit":
        return cls(TrainingRole(value["role"]) if value.get("role") else None, int(value.get("exposures", 0)), int(value.get("minutes", 0)), bool(value.get("eligible", False)))


@dataclass(frozen=True)
class HistoricalFact:
    id: str
    date: date
    type: HistoricalFactType
    planned_prescription: Mapping[str, Any] | None = None
    completed_actual: Mapping[str, Any] | None = None
    physiological_credit: PhysiologicalCredit = field(default_factory=PhysiologicalCredit)
    provenance: Provenance = field(default_factory=lambda: Provenance("unknown", ProvenanceSource.LEGACY_V2))
    immutable: bool = True
    future_placement_authority: bool = False

    def __post_init__(self) -> None:
        if not self.immutable or self.future_placement_authority:
            raise ValueError("historical_fact_must_be_immutable_and_non_authoritative")
        object.__setattr__(self, "planned_prescription", _freeze(self.planned_prescription) if self.planned_prescription is not None else None)
        object.__setattr__(self, "completed_actual", _freeze(self.completed_actual) if self.completed_actual is not None else None)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "date": self.date.isoformat(), "type": self.type.value, "planned_prescription": _thaw(self.planned_prescription), "completed_actual": _thaw(self.completed_actual), "physiological_credit": self.physiological_credit.to_dict(), "provenance": self.provenance.to_dict(), "immutable": True, "future_placement_authority": False}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HistoricalFact":
        return cls(str(value["id"]), _parse_date(value["date"]), HistoricalFactType(value["type"]), value.get("planned_prescription"), value.get("completed_actual"), PhysiologicalCredit.from_dict(value.get("physiological_credit", {})), Provenance.from_dict(value["provenance"]), bool(value.get("immutable", True)), bool(value.get("future_placement_authority", False)))


@dataclass(frozen=True)
class AuthoritativeCommitment:
    id: str
    type: AuthorityType
    date: date | None
    date_window: tuple[date, date] | None
    reservation_minutes: int
    authority_source: AuthoritySource
    conflict_behavior: ConflictBehavior
    provenance: Provenance

    def __post_init__(self) -> None:
        if (self.date is None) == (self.date_window is None):
            raise ValueError("commitment_requires_exactly_one_date_or_window")
        if self.date_window and self.date_window[0] > self.date_window[1]:
            raise ValueError("commitment_window_is_reversed")
        if self.reservation_minutes < 0:
            raise ValueError("commitment_minutes_must_be_nonnegative")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type.value, "date": self.date.isoformat() if self.date else None, "date_window": [item.isoformat() for item in self.date_window] if self.date_window else None, "reservation_minutes": self.reservation_minutes, "authority_source": self.authority_source.value, "conflict_behavior": self.conflict_behavior.value, "provenance": self.provenance.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuthoritativeCommitment":
        window=value.get("date_window")
        parsed_window=tuple(_parse_date(item) for item in window) if window else None
        if parsed_window is not None and (len(parsed_window) != 2 or any(item is None for item in parsed_window)):
            raise ValueError("commitment_window_requires_two_dates")
        return cls(str(value["id"]), AuthorityType(value["type"]), _parse_date(value.get("date")), parsed_window, int(value["reservation_minutes"]), AuthoritySource(value["authority_source"]), ConflictBehavior(value["conflict_behavior"]), Provenance.from_dict(value["provenance"]))


@dataclass(frozen=True)
class RecentLoad:
    """Completed physiological load in one trailing chronological window.

    Windows are half-open: ``[window_start, as_of_date)``.  The planning day
    itself remains mutable, and no Monday--Sunday identity is implied.
    """

    window_days: int
    window_start: date
    as_of_date: date
    rowing_minutes: int = 0
    low_intensity_rowing_minutes: int = 0
    quality_exposures: int = 0
    strength_exposures: int = 0
    race_exposures: int = 0
    optional_activity_minutes: int = 0
    counted_fact_ids: tuple[str, ...] = ()
    excluded_outside_window_fact_ids: tuple[str, ...] = ()
    duplicate_facts_excluded: int = 0

    def __post_init__(self) -> None:
        if self.window_days < 1 or self.window_start > self.as_of_date or self.duplicate_facts_excluded < 0:
            raise ValueError("recent_load_window_is_invalid")
        for value in (
            self.rowing_minutes, self.low_intensity_rowing_minutes,
            self.quality_exposures, self.strength_exposures,
            self.race_exposures, self.optional_activity_minutes,
        ):
            if value < 0:
                raise ValueError("recent_load_values_must_be_nonnegative")
        object.__setattr__(self, "counted_fact_ids", tuple(sorted(set(self.counted_fact_ids))))
        object.__setattr__(self, "excluded_outside_window_fact_ids", tuple(sorted(set(self.excluded_outside_window_fact_ids))))


@dataclass(frozen=True)
class TrainingLoadTarget:
    """A continuous load range, never a Monday--Sunday session stencil.

    ``horizon_*`` names the prospective planning interval.  Its residual is
    assessed against a combined decision budget: completed trailing load from
    the same-length RecentLoad window plus known/proposed work in this future
    interval.  This makes the accounting explicit rather than treating past
    work as credit against an unrelated purely-future prescription.
    """

    id: str
    horizon_start: date
    horizon_end: date
    horizon_days: int
    phase: str
    total_rowing_target_minutes: int
    minimum_rowing_minutes: int
    maximum_rowing_minutes: int
    low_intensity_minimum_minutes: int
    quality_allowance_exposures: int
    strength_target_exposures: int
    strength_minimum_exposures: int
    completed_rowing_minutes: int
    completed_low_intensity_minutes: int
    completed_quality_exposures: int
    completed_strength_exposures: int
    authoritative_future_rowing_minutes: int
    provisional_planned_rowing_minutes: int
    planned_low_intensity_minutes: int
    remaining_rowing_minutes: int
    remaining_low_intensity_minutes: int
    remaining_strength_exposures: int
    taper_modifier: float
    recovery_modifier: float
    availability_ceiling_minutes: int
    source_policy_ids: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon_days < 1 or self.horizon_end != self.horizon_start + timedelta(days=self.horizon_days - 1):
            raise ValueError("training_load_target_horizon_is_invalid")
        values = (
            self.total_rowing_target_minutes, self.minimum_rowing_minutes,
            self.maximum_rowing_minutes, self.low_intensity_minimum_minutes,
            self.quality_allowance_exposures, self.strength_target_exposures,
            self.strength_minimum_exposures, self.completed_rowing_minutes,
            self.completed_low_intensity_minutes, self.completed_quality_exposures,
            self.completed_strength_exposures, self.authoritative_future_rowing_minutes,
            self.provisional_planned_rowing_minutes, self.planned_low_intensity_minutes,
            self.remaining_rowing_minutes, self.remaining_low_intensity_minutes,
            self.remaining_strength_exposures, self.availability_ceiling_minutes,
        )
        if any(item < 0 for item in values) or not self.minimum_rowing_minutes <= self.total_rowing_target_minutes <= self.maximum_rowing_minutes:
            raise ValueError("training_load_target_values_are_invalid")
        if self.strength_minimum_exposures > self.strength_target_exposures:
            raise ValueError("training_load_target_strength_range_is_invalid")
        object.__setattr__(self, "source_policy_ids", tuple(sorted(set(self.source_policy_ids))))
        object.__setattr__(self, "rationale", tuple(sorted(set(self.rationale))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


@dataclass(frozen=True)
class TrainingDistributionTarget:
    """Soft chronological sequence-quality expectation, never a weekly stencil."""
    horizon_start: date
    horizon_end: date
    horizon_days: int
    minimum_rowing_exposures: int
    target_rowing_exposures: int
    minimum_low_intensity_exposures: int
    preferred_max_aerobic_gap_days: int
    taper_frequency_retention: bool
    source_policy_ids: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon_days < 1 or self.horizon_end != self.horizon_start + timedelta(days=self.horizon_days - 1):
            raise ValueError("training_distribution_target_horizon_is_invalid")
        if min(self.minimum_rowing_exposures, self.target_rowing_exposures, self.minimum_low_intensity_exposures, self.preferred_max_aerobic_gap_days) < 0:
            raise ValueError("training_distribution_target_values_are_invalid")
        if self.minimum_rowing_exposures > self.target_rowing_exposures:
            raise ValueError("training_distribution_target_range_is_invalid")


@dataclass(frozen=True)
class StrengthContinuityTarget:
    horizon_start: date
    horizon_end: date
    target_exposures: int
    minimum_exposures: int
    taper_target_exposures: int
    completed_exposures: int
    deferred_reason: str | None = None
    earliest_return_date: date | None = None
    source_policy_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhysiologicalState:
    as_of_date: date
    completed_aerobic_exposure: PhysiologicalCredit = field(default_factory=PhysiologicalCredit)
    completed_quality_exposure: PhysiologicalCredit = field(default_factory=PhysiologicalCredit)
    completed_strength_exposure: PhysiologicalCredit = field(default_factory=PhysiologicalCredit)
    completed_aerobic_by_classification: Mapping[str, PhysiologicalCredit] = field(default_factory=dict)
    completed_quality_by_classification: Mapping[str, PhysiologicalCredit] = field(default_factory=dict)
    completed_race_exposure: PhysiologicalCredit = field(default_factory=PhysiologicalCredit)
    completed_duration_minutes_by_fact: Mapping[str, int] = field(default_factory=dict)
    completed_classification_by_fact: Mapping[str, str] = field(default_factory=dict)
    recent_7d_load: RecentLoad | None = None
    recent_14d_load: RecentLoad | None = None
    recent_race_load: tuple[HistoricalFact, ...] = ()
    recovery_constraints: tuple["RecoveryConstraint", ...] = ()
    next_race_context: Mapping[str, Any] = field(default_factory=dict)
    phase_context: Mapping[str, Any] = field(default_factory=dict)
    progression_context: Mapping[str, Any] = field(default_factory=dict)
    athlete_response_inputs: Mapping[str, Any] = field(default_factory=dict)
    completed_fact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "completed_aerobic_by_classification", _freeze(self.completed_aerobic_by_classification))
        object.__setattr__(self, "completed_quality_by_classification", _freeze(self.completed_quality_by_classification))
        object.__setattr__(self, "completed_duration_minutes_by_fact", _freeze(self.completed_duration_minutes_by_fact))
        object.__setattr__(self, "completed_classification_by_fact", _freeze(self.completed_classification_by_fact))
        object.__setattr__(self, "next_race_context", _freeze(self.next_race_context))
        object.__setattr__(self, "phase_context", _freeze(self.phase_context))
        object.__setattr__(self, "progression_context", _freeze(self.progression_context))
        object.__setattr__(self, "athlete_response_inputs", _freeze(self.athlete_response_inputs))


@dataclass(frozen=True)
class TrainingNeed:
    id: str
    role: TrainingRole
    target_credit: PhysiologicalCredit
    earliest_date: date
    latest_date: date
    recovery_cost: str
    priority: str
    source_policy: str
    race_relevance: str | None = None
    progression_relation: str | None = None
    provenance: tuple[str, ...] = ()
    state: str = "active"
    state_reason: str | None = None
    parent_need_id: str | None = None
    exposure_index: int = 1
    exposure_count: int = 1

    def __post_init__(self) -> None:
        if self.earliest_date > self.latest_date:
            raise ValueError("training_need_window_is_reversed")
        if self.state not in {"active", "satisfied", "partially_satisfied", "deferred", "superseded", "reduced", "missed"}:
            raise ValueError("training_need_state_is_invalid")
        if self.exposure_count < 1 or not 1 <= self.exposure_index <= self.exposure_count:
            raise ValueError("training_need_exposure_cardinality_is_invalid")


@dataclass(frozen=True)
class ProjectedSequenceItem:
    """An immutable future event used only for local physiological context."""

    id: str
    date: date
    role: TrainingRole
    target_credit: PhysiologicalCredit = field(default_factory=PhysiologicalCredit)
    authoritative: bool = False
    source: str = "projected"


@dataclass(frozen=True)
class ProjectedSequenceState:
    """Future sequence context; never mutates completed PhysiologicalState."""

    items: tuple[ProjectedSequenceItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(sorted(self.items, key=lambda item: (item.date, item.id, item.role.value))))

    def with_item(self, item: ProjectedSequenceItem) -> "ProjectedSequenceState":
        return ProjectedSequenceState((*self.items, item))


@dataclass(frozen=True)
class SequenceConstraintDecision:
    training_need_id: str
    candidate_date: date
    eligibility: SequenceEligibility
    hard_rejection_reasons: tuple[str, ...] = ()
    soft_constraints: tuple[str, ...] = ()
    load_ceiling: str | None = None
    recommended_credit_adjustment: PhysiologicalCredit | None = None
    recovery_status: str = "ordinary"
    spacing_status: str = "clear"
    race_taper_status: str = "ordinary"
    policy_rule_ids: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    diagnostic_codes: tuple[str, ...] = ()
    authority_conflicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hard_rejection_reasons", tuple(sorted(set(self.hard_rejection_reasons))))
        object.__setattr__(self, "soft_constraints", tuple(sorted(set(self.soft_constraints))))
        object.__setattr__(self, "policy_rule_ids", tuple(sorted(set(self.policy_rule_ids))))
        object.__setattr__(self, "rationale", tuple(sorted(set(self.rationale))))
        object.__setattr__(self, "diagnostic_codes", tuple(sorted(set(self.diagnostic_codes))))
        object.__setattr__(self, "authority_conflicts", tuple(sorted(set(self.authority_conflicts))))


@dataclass(frozen=True)
class RecoveryConstraint:
    source: str
    start_date: date
    end_date: date
    hard_excluded_roles: tuple[TrainingRole, ...] = ()
    soft_load_ceiling: str | None = None
    minimum_spacing: int | None = None
    optional_low_load_roles: tuple[TrainingRole, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("recovery_constraint_window_is_reversed")
        if self.minimum_spacing is not None and self.minimum_spacing < 0:
            raise ValueError("minimum_spacing_must_be_nonnegative")


@dataclass(frozen=True)
class Placement:
    placement_id: str
    training_need_id: str
    date: date
    reservation_minutes: int
    target_credit: PhysiologicalCredit
    provenance: Provenance
    authority_status: PlacementAuthorityStatus
    applied_constraint_decisions: tuple[str, ...] = ()
    constraint_decision: SequenceConstraintDecision | None = None
    policy_rule_ids: tuple[str, ...] = ()
    preference_effect: str | None = None
    stability_effect: str | None = None
    selection_reason: str = ""
    served_need_ids: tuple[str, ...] = ()
    role: TrainingRole | None = None
    authority_type: AuthorityType | None = None

    def __post_init__(self) -> None:
        if self.reservation_minutes < 0:
            raise ValueError("placement_minutes_must_be_nonnegative")
        object.__setattr__(self, "applied_constraint_decisions", tuple(sorted(set(self.applied_constraint_decisions))))
        object.__setattr__(self, "policy_rule_ids", tuple(sorted(set(self.policy_rule_ids))))
        object.__setattr__(self, "served_need_ids", tuple(sorted(set(self.served_need_ids))))
