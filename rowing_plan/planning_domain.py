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
    GENERAL_AEROBIC = "general_aerobic"
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


class TrainingModality(_TextEnum):
    """Canonical activity modality for actual-history and profile capability data.

    It is intentionally separate from ``TrainingRole``: modality identifies how
    work was performed; a role is a conservative physiological classification.
    M1 records the former and reduces descriptive history only.
    """
    ON_WATER = "on_water"
    ERG = "erg"
    BIKE = "bike"
    ELLIPTICAL = "elliptical"
    STAIR_CLIMBER = "stair_climber"
    RUNNING = "running"
    STRENGTH = "strength"
    OTHER = "other"


class PhysicalCategory(_TextEnum):
    """One and only one physical allocation category for a component."""

    ROWING_SPECIFIC = "rowing_specific"
    STRENGTH = "strength"
    GENERAL_AEROBIC = "general_aerobic"
    OTHER = "other"


class TrainingComponentPurpose(_TextEnum):
    """Small, pre-composition purpose vocabulary for R4 calendar facts."""

    EASY_AEROBIC = "easy_aerobic"
    TECHNICAL = "technical"
    AEROBIC_STRENGTH = "aerobic_strength"
    STRENGTH = "strength"
    THRESHOLD = "threshold"
    RACE_SPECIFIC = "race_specific"
    ANAEROBIC = "anaerobic"
    POWER = "power"
    RACE = "race"
    RECOVERY = "recovery"
    COACHED = "coached"
    PRIVATE_COACHING = "private_coaching"


class RowingQualityFamily(_TextEnum):
    """R6 physiological composition vocabulary, separate from physical dose."""

    LOW_INTENSITY_AEROBIC = "low_intensity_aerobic"
    AEROBIC_STRENGTH = "aerobic_strength"
    THRESHOLD = "threshold"
    RACE_SPECIFIC = "race_specific"
    ANAEROBIC = "anaerobic"
    POWER = "power"


class QualityRequirementPriority(_TextEnum):
    HARD = "hard"
    PREFERRED = "preferred"
    OPTIONAL = "optional"


class TrainingDayState(_TextEnum):
    DESIGNATED_REST = "designated_rest"
    UNUSED_AVAILABLE = "unused_available"
    UNAVAILABLE = "unavailable"
    PLANNED_TRAINING = "planned_training"


class ComponentAuthority(_TextEnum):
    GENERATED = "generated"
    AUTHORITATIVE = "authoritative"


class CurrentTrainingSource(_TextEnum):
    """Provenance for baseline evidence; never a future-dose instruction."""

    RECENT_ACTUALS = "recent_actuals"
    ATHLETE_ESTIMATE = "athlete_estimate"
    MIXED = "mixed"
    LEGACY_PROFILE_ESTIMATE = "legacy_profile_estimate"


@dataclass(frozen=True)
class AvailableTrainingDay:
    """One recurring or date-specific opportunity in the future capacity model."""

    day_key: str
    available_minutes: int
    available: bool = True
    fixed_rest: bool = False
    allowed_modalities: tuple[TrainingModality, ...] = ()
    fixed_commitment_ids: tuple[str, ...] = ()
    prohibited_role_families: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.available_minutes < 0:
            raise ValueError("available_training_day_minutes_must_be_nonnegative")
        object.__setattr__(self, "allowed_modalities", tuple(sorted(set(self.allowed_modalities), key=lambda item: item.value)))
        object.__setattr__(self, "fixed_commitment_ids", tuple(sorted(set(self.fixed_commitment_ids))))
        object.__setattr__(self, "prohibited_role_families", tuple(sorted(set(self.prohibited_role_families))))

    @property
    def usable_minutes(self) -> int:
        return self.available_minutes if self.available and not self.fixed_rest else 0


@dataclass(frozen=True)
class AvailableTrainingCapacity:
    """Normalized weekly/date capacity; a ceiling, never a prescription."""

    days: tuple[AvailableTrainingDay, ...]
    source: str = "weekly_recurrence"

    def __post_init__(self) -> None:
        keys = tuple(item.day_key for item in self.days)
        if len(set(keys)) != len(keys):
            raise ValueError("available_training_capacity_day_keys_must_be_unique")
        object.__setattr__(self, "days", tuple(sorted(self.days, key=lambda item: item.day_key)))

    @property
    def total_available_minutes(self) -> int:
        return sum(item.usable_minutes for item in self.days)


@dataclass(frozen=True)
class CurrentTrainingState:
    """Immutable evidence about current training, deliberately not a target."""

    source: CurrentTrainingSource
    observation_window_days: int | None = None
    approximate_total_physical_minutes: int | None = None
    approximate_rowing_minutes: int | None = None
    rowing_exposures: int | None = None
    strength_exposures: int | None = None
    typical_strength_duration_minutes: int | None = None
    consistency: str | None = None
    longest_comfortable_row_minutes: int | None = None
    confidence: str = "low"
    missing_evidence: tuple[str, ...] = ()
    completed_history_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.observation_window_days is not None and self.observation_window_days < 1:
            raise ValueError("current_training_state_window_must_be_positive")
        for value in (
            self.approximate_total_physical_minutes, self.approximate_rowing_minutes,
            self.rowing_exposures, self.strength_exposures,
            self.typical_strength_duration_minutes, self.longest_comfortable_row_minutes,
        ):
            if value is not None and value < 0:
                raise ValueError("current_training_state_values_must_be_nonnegative")
        if self.consistency not in {None, "returning", "building", "regular", "variable"}:
            raise ValueError("current_training_state_consistency_is_invalid")
        if self.confidence not in {"low", "moderate", "high"}:
            raise ValueError("current_training_state_confidence_is_invalid")
        object.__setattr__(self, "missing_evidence", tuple(sorted(set(self.missing_evidence))))
        object.__setattr__(self, "completed_history_ids", tuple(sorted(set(self.completed_history_ids))))


@dataclass(frozen=True)
class RecommendedDoseSegment:
    """One non-overlapping future physical-dose recommendation.

    This is deliberately about *materialized physical minutes*, not target
    credit, physiological credit, or a provisional reservation.  Segments are
    chronological pieces of one coherent planning block; they are never
    overlapping rolling prescriptions.
    """

    start_date: date
    end_date: date
    recommended_physical_minutes: int
    minimum_acceptable_physical_minutes: int
    maximum_reasonable_physical_minutes: int
    available_capacity_minutes: int
    committed_physical_minutes: int = 0
    recommendation_status: str = "ordinary"
    rationale_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        values = (
            self.recommended_physical_minutes,
            self.minimum_acceptable_physical_minutes,
            self.maximum_reasonable_physical_minutes,
            self.available_capacity_minutes,
            self.committed_physical_minutes,
        )
        if self.end_date < self.start_date or any(value < 0 for value in values):
            raise ValueError("recommended_dose_segment_values_are_invalid")
        if not (
            self.minimum_acceptable_physical_minutes
            <= self.recommended_physical_minutes
            <= self.maximum_reasonable_physical_minutes
            <= self.available_capacity_minutes
        ):
            raise ValueError("recommended_dose_segment_range_is_invalid")
        if self.committed_physical_minutes > self.recommended_physical_minutes:
            raise ValueError("recommended_dose_segment_commitment_exceeds_recommendation")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))


@dataclass(frozen=True)
class RecommendedTrainingDose:
    """The sole future physical-dose authority for replacement V3.

    Block totals are derived from ``segments`` and are validated rather than
    independently authored.  Later allocation must partition each segment's
    recommended physical minutes exactly once into materialized components.
    """

    planning_start: date
    planning_end: date
    segments: tuple[RecommendedDoseSegment, ...]
    recommended_physical_minutes: int
    minimum_acceptable_physical_minutes: int
    maximum_reasonable_physical_minutes: int
    available_capacity_minutes: int
    committed_physical_minutes: int
    recommendation_status: str
    progression_state: str
    confidence: str
    rationale_codes: tuple[str, ...] = ()
    evidence_sources: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    policy_version: str = "recommended-dose-v1"

    def __post_init__(self) -> None:
        if self.planning_end < self.planning_start or not self.segments:
            raise ValueError("recommended_training_dose_horizon_is_invalid")
        ordered = tuple(sorted(self.segments, key=lambda item: (item.start_date, item.end_date)))
        if ordered != self.segments:
            raise ValueError("recommended_training_dose_segments_must_be_ordered")
        if ordered[0].start_date != self.planning_start or ordered[-1].end_date != self.planning_end:
            raise ValueError("recommended_training_dose_segments_do_not_cover_block")
        if any(right.start_date != left.end_date + timedelta(days=1) for left, right in zip(ordered, ordered[1:])):
            raise ValueError("recommended_training_dose_segments_must_be_contiguous")
        derived = (
            sum(item.recommended_physical_minutes for item in ordered),
            sum(item.minimum_acceptable_physical_minutes for item in ordered),
            sum(item.maximum_reasonable_physical_minutes for item in ordered),
            sum(item.available_capacity_minutes for item in ordered),
            sum(item.committed_physical_minutes for item in ordered),
        )
        supplied = (
            self.recommended_physical_minutes,
            self.minimum_acceptable_physical_minutes,
            self.maximum_reasonable_physical_minutes,
            self.available_capacity_minutes,
            self.committed_physical_minutes,
        )
        if any(value < 0 for value in supplied) or supplied != derived:
            raise ValueError("recommended_training_dose_block_totals_must_be_derived")
        if self.confidence not in {"low", "moderate", "high"}:
            raise ValueError("recommended_training_dose_confidence_is_invalid")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "evidence_sources", tuple(sorted(set(self.evidence_sources))))
        object.__setattr__(self, "constraints", tuple(sorted(set(self.constraints))))


@dataclass(frozen=True)
class GeneralAerobicModalityCapability:
    """An athlete-approved, non-rowing aerobic modality for R3/R4.

    This is capability evidence, not a prescription.  R3 carries it alongside
    an already-authorized general-aerobic allocation and R4 resolves one
    concrete modality when it dates the component.
    """

    modality: TrainingModality
    familiarity: str | None = None
    preference: str = "neutral"
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.modality not in {
            TrainingModality.BIKE, TrainingModality.ELLIPTICAL,
            TrainingModality.STAIR_CLIMBER, TrainingModality.RUNNING,
        }:
            raise ValueError("general_aerobic_modality_capability_is_invalid")
        if self.familiarity not in {None, "new", "some_experience", "regular"}:
            raise ValueError("general_aerobic_modality_familiarity_is_invalid")
        if self.preference not in {"prefer", "neutral"}:
            raise ValueError("general_aerobic_modality_preference_is_invalid")
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


@dataclass(frozen=True)
class TrainingAllocationSegment:
    """One exact physical-category partition of an R2 dose segment."""

    start_date: date
    end_date: date
    recommended_physical_minutes: int
    rowing_specific_minutes: int
    strength_minutes: int
    general_aerobic_minutes: int
    other_physical_minutes: int
    rowing_specific_floor_minutes: int
    recommended_strength_exposures: int
    minimum_strength_exposures: int
    maximum_strength_exposures: int
    expected_strength_duration_minutes: int
    rationale_codes: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    other_provenance: tuple[str, ...] = ()
    general_aerobic_capabilities: tuple[GeneralAerobicModalityCapability, ...] = ()
    # R3 normally preserves its first category partition.  These fields make
    # the exceptional, feasibility-authorized rowing -> general-aerobic
    # reconciliation observable without creating another dose authority.
    category_reconciliation_minutes: int = 0
    category_reconciliation_reason: str | None = None
    category_feasibility_original: bool | None = None
    category_feasibility_final: bool | None = None
    category_feasibility_runtime_ms: float | None = None
    # Deliberate supplemental aerobic work is separate from a later
    # feasibility reconciliation.  Both remain inside the one R3 category
    # allocation and therefore charge physical dose exactly once.
    deliberate_general_aerobic_minutes: int = 0
    general_aerobic_policy_provenance: tuple[str, ...] = ()
    general_aerobic_supplemental_max_minutes: int | None = None
    general_aerobic_supplemental_only: bool = False

    def __post_init__(self) -> None:
        values = (
            self.recommended_physical_minutes, self.rowing_specific_minutes,
            self.strength_minutes, self.general_aerobic_minutes,
            self.other_physical_minutes, self.rowing_specific_floor_minutes,
            self.recommended_strength_exposures, self.minimum_strength_exposures,
            self.maximum_strength_exposures, self.expected_strength_duration_minutes,
        )
        if self.end_date < self.start_date or any(value < 0 for value in values):
            raise ValueError("training_allocation_segment_values_are_invalid")
        if self.rowing_specific_minutes < self.rowing_specific_floor_minutes:
            raise ValueError("training_allocation_segment_rowing_floor_is_unmet")
        if not self.minimum_strength_exposures <= self.recommended_strength_exposures <= self.maximum_strength_exposures:
            raise ValueError("training_allocation_segment_strength_range_is_invalid")
        if self.strength_minutes != self.recommended_strength_exposures * self.expected_strength_duration_minutes:
            raise ValueError("training_allocation_segment_strength_minutes_must_match_exposures")
        if self.other_physical_minutes and (not self.other_provenance or not self.rationale_codes):
            raise ValueError("training_allocation_segment_other_requires_provenance_and_rationale")
        if self.category_reconciliation_minutes < 0 or self.category_reconciliation_minutes > self.general_aerobic_minutes:
            raise ValueError("training_allocation_segment_category_reconciliation_is_invalid")
        if self.category_reconciliation_minutes and not self.category_reconciliation_reason:
            raise ValueError("training_allocation_segment_category_reconciliation_reason_is_required")
        if self.category_feasibility_runtime_ms is not None and self.category_feasibility_runtime_ms < 0:
            raise ValueError("training_allocation_segment_category_feasibility_runtime_is_invalid")
        if self.deliberate_general_aerobic_minutes < 0 or self.deliberate_general_aerobic_minutes > self.general_aerobic_minutes:
            raise ValueError("training_allocation_segment_deliberate_general_aerobic_is_invalid")
        if self.general_aerobic_supplemental_max_minutes is not None and self.general_aerobic_supplemental_max_minutes <= 0:
            raise ValueError("training_allocation_segment_supplemental_max_is_invalid")
        if self.general_aerobic_supplemental_only and self.general_aerobic_supplemental_max_minutes is None:
            raise ValueError("training_allocation_segment_supplemental_policy_is_incomplete")
        if (
            self.rowing_specific_minutes + self.strength_minutes
            + self.general_aerobic_minutes + self.other_physical_minutes
            != self.recommended_physical_minutes
        ):
            raise ValueError("training_allocation_segment_physical_conservation_mismatch")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "constraints", tuple(sorted(set(self.constraints))))
        object.__setattr__(self, "other_provenance", tuple(sorted(set(self.other_provenance))))
        object.__setattr__(self, "general_aerobic_policy_provenance", tuple(sorted(set(self.general_aerobic_policy_provenance))))
        capabilities = tuple(sorted(self.general_aerobic_capabilities, key=lambda item: item.modality.value))
        if len({item.modality for item in capabilities}) != len(capabilities):
            raise ValueError("training_allocation_segment_general_aerobic_modalities_must_be_unique")
        object.__setattr__(self, "general_aerobic_capabilities", capabilities)


@dataclass(frozen=True)
class TrainingAllocation:
    """Derived block allocation; never a second physical-dose authority."""

    planning_start: date
    planning_end: date
    segments: tuple[TrainingAllocationSegment, ...]
    recommended_physical_minutes: int
    rowing_specific_minutes: int
    strength_minutes: int
    general_aerobic_minutes: int
    other_physical_minutes: int
    recommended_strength_exposures: int
    rationale_codes: tuple[str, ...] = ()
    evidence_sources: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    confidence: str = "low"
    policy_version: str = "training-allocation-v1"

    def __post_init__(self) -> None:
        if self.planning_end < self.planning_start or not self.segments:
            raise ValueError("training_allocation_horizon_is_invalid")
        ordered = tuple(sorted(self.segments, key=lambda item: (item.start_date, item.end_date)))
        if ordered != self.segments:
            raise ValueError("training_allocation_segments_must_be_ordered")
        if ordered[0].start_date != self.planning_start or ordered[-1].end_date != self.planning_end:
            raise ValueError("training_allocation_segments_do_not_cover_block")
        if any(right.start_date != left.end_date + timedelta(days=1) for left, right in zip(ordered, ordered[1:])):
            raise ValueError("training_allocation_segments_must_be_contiguous")
        derived = (
            sum(item.recommended_physical_minutes for item in ordered),
            sum(item.rowing_specific_minutes for item in ordered),
            sum(item.strength_minutes for item in ordered),
            sum(item.general_aerobic_minutes for item in ordered),
            sum(item.other_physical_minutes for item in ordered),
            sum(item.recommended_strength_exposures for item in ordered),
        )
        supplied = (
            self.recommended_physical_minutes, self.rowing_specific_minutes,
            self.strength_minutes, self.general_aerobic_minutes,
            self.other_physical_minutes, self.recommended_strength_exposures,
        )
        if any(value < 0 for value in supplied) or supplied != derived:
            raise ValueError("training_allocation_block_totals_must_be_derived")
        if self.confidence not in {"low", "moderate", "high"}:
            raise ValueError("training_allocation_confidence_is_invalid")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "evidence_sources", tuple(sorted(set(self.evidence_sources))))
        object.__setattr__(self, "constraints", tuple(sorted(set(self.constraints))))


@dataclass(frozen=True)
class TrainingComponent:
    """One physical activity on a component-aware R4 training day.

    ``duration_minutes`` is physical truth.  It intentionally has no
    reservation or physiological-credit companion: R3 allocation is the
    upstream accounting authority and a component owns exactly one category
    of that allocation.
    """

    component_id: str
    source_lineage_id: str
    allocation_segment_id: str
    physical_category: PhysicalCategory
    duration_minutes: int
    purpose: TrainingComponentPurpose
    sequence: int = 1
    modality: TrainingModality | None = None
    permitted_modalities: tuple[TrainingModality, ...] = ()
    authority: ComponentAuthority = ComponentAuthority.GENERATED
    primary: bool | None = None
    materialization_requirements: tuple[str, ...] = ()
    rationale_codes: tuple[str, ...] = ()
    constraint_codes: tuple[str, ...] = ()
    quality_requirement_id: str | None = None
    quality_exposure_id: str | None = None
    physiological_band: str | None = None
    quality_work_minutes: int | None = None
    minimum_quality_work_minutes: int | None = None
    maximum_quality_work_minutes: int | None = None
    capability_provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.component_id or not self.source_lineage_id or not self.allocation_segment_id:
            raise ValueError("training_component_identity_is_required")
        if self.duration_minutes <= 0 or self.sequence < 1:
            raise ValueError("training_component_duration_or_sequence_is_invalid")
        if self.physical_category is PhysicalCategory.OTHER and not self.rationale_codes:
            raise ValueError("training_component_other_requires_rationale")
        quality_values = (self.quality_work_minutes, self.minimum_quality_work_minutes, self.maximum_quality_work_minutes)
        if any(value is not None and value < 0 for value in quality_values):
            raise ValueError("training_component_quality_work_values_are_invalid")
        if self.quality_requirement_id is not None:
            if not self.quality_exposure_id or not self.physiological_band or self.quality_work_minutes is None:
                raise ValueError("training_component_quality_link_is_incomplete")
            if self.physical_category is not PhysicalCategory.ROWING_SPECIFIC:
                raise ValueError("training_component_quality_link_requires_rowing")
        if self.minimum_quality_work_minutes is not None and self.maximum_quality_work_minutes is not None:
            if self.minimum_quality_work_minutes > self.maximum_quality_work_minutes:
                raise ValueError("training_component_quality_work_range_is_invalid")
            if self.quality_work_minutes is not None and not self.minimum_quality_work_minutes <= self.quality_work_minutes <= self.maximum_quality_work_minutes:
                raise ValueError("training_component_quality_work_is_outside_range")
        permitted = tuple(sorted(set(self.permitted_modalities), key=lambda item: item.value))
        if self.modality is not None and permitted and self.modality not in permitted:
            raise ValueError("training_component_modality_violates_constraint")
        object.__setattr__(self, "permitted_modalities", permitted)
        object.__setattr__(self, "materialization_requirements", tuple(sorted(set(self.materialization_requirements))))
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "constraint_codes", tuple(sorted(set(self.constraint_codes))))
        object.__setattr__(self, "capability_provenance", tuple(sorted(set(self.capability_provenance))))


@dataclass(frozen=True)
class TrainingDay:
    """A dated physical-capacity container whose totals are always derived."""

    date: date
    available_minutes: int
    components: tuple[TrainingComponent, ...] = ()
    state: TrainingDayState = TrainingDayState.UNUSED_AVAILABLE
    authority_ids: tuple[str, ...] = ()
    compatibility_diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.available_minutes < 0:
            raise ValueError("training_day_available_minutes_must_be_nonnegative")
        ordered = tuple(sorted(self.components, key=lambda item: (item.sequence, item.component_id)))
        if ordered != self.components:
            raise ValueError("training_day_components_must_be_in_sequence_order")
        sequences = tuple(item.sequence for item in ordered)
        if len(set(sequences)) != len(sequences):
            raise ValueError("training_day_component_sequences_must_be_unique")
        component_ids = tuple(item.component_id for item in ordered)
        if len(set(component_ids)) != len(component_ids):
            raise ValueError("training_day_component_ids_must_be_unique")
        if self.state in {TrainingDayState.DESIGNATED_REST, TrainingDayState.UNAVAILABLE} and ordered:
            raise ValueError("training_day_rest_or_unavailable_cannot_have_components")
        if self.state is TrainingDayState.PLANNED_TRAINING and not ordered:
            raise ValueError("training_day_planned_training_requires_component")
        if self.state is TrainingDayState.UNUSED_AVAILABLE and ordered:
            raise ValueError("training_day_unused_available_cannot_have_components")
        total = sum(item.duration_minutes for item in ordered)
        if total > self.available_minutes and any(item.authority is ComponentAuthority.GENERATED for item in ordered):
            raise ValueError("training_day_generated_component_exceeds_capacity")
        object.__setattr__(self, "authority_ids", tuple(sorted(set(self.authority_ids))))
        object.__setattr__(self, "compatibility_diagnostics", tuple(sorted(set(self.compatibility_diagnostics))))

    @property
    def total_physical_minutes(self) -> int:
        return sum(item.duration_minutes for item in self.components)

    @property
    def remaining_capacity_minutes(self) -> int:
        return max(0, self.available_minutes - self.total_physical_minutes)

    @property
    def combined_day(self) -> bool:
        return len(self.components) > 1

    @property
    def authoritative_capacity_conflict(self) -> bool:
        return self.total_physical_minutes > self.available_minutes


@dataclass(frozen=True)
class TrainingDayDiagnostic:
    code: str
    date: date | None = None
    source_lineage_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class TrainingDayInfeasibility:
    code: str
    allocation_segment_id: str | None = None
    physical_category: PhysicalCategory | None = None
    remaining_minutes: int = 0
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.remaining_minutes < 0:
            raise ValueError("training_day_infeasibility_minutes_must_be_nonnegative")


@dataclass(frozen=True)
class RaceEvent:
    """Authoritative race context, deliberately outside minute conservation when unknown."""

    race_id: str
    date: date
    planned_duration_minutes: int | None = None
    authority_source: str = "race_profile"
    rationale_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.race_id or self.planned_duration_minutes is not None and self.planned_duration_minutes < 0:
            raise ValueError("race_event_values_are_invalid")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))


@dataclass(frozen=True)
class TrainingDayPlanResult:
    """Pure R4 result: physical days plus explicit diagnostics/infeasibility."""

    days: tuple[TrainingDay, ...]
    diagnostics: tuple[TrainingDayDiagnostic, ...] = ()
    infeasibilities: tuple[TrainingDayInfeasibility, ...] = ()
    race_events: tuple[RaceEvent, ...] = ()
    preference_context: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "training-days-v1"

    def __post_init__(self) -> None:
        dates = tuple(item.date for item in self.days)
        if len(set(dates)) != len(dates) or tuple(sorted(self.days, key=lambda item: item.date)) != self.days:
            raise ValueError("training_day_plan_days_must_be_unique_and_ordered")
        component_ids = tuple(component.component_id for day in self.days for component in day.components)
        if len(set(component_ids)) != len(component_ids):
            raise ValueError("training_day_plan_component_ids_must_be_unique")
        object.__setattr__(self, "diagnostics", tuple(sorted(set(self.diagnostics), key=lambda item: (item.date or date.min, item.code, item.source_lineage_id or ""))))
        object.__setattr__(self, "infeasibilities", tuple(sorted(set(self.infeasibilities), key=lambda item: (item.allocation_segment_id or "", item.code, item.physical_category.value if item.physical_category else ""))))
        object.__setattr__(self, "race_events", tuple(sorted(set(self.race_events), key=lambda item: (item.date, item.race_id))))
        object.__setattr__(self, "preference_context", _freeze(self.preference_context))

    @property
    def success(self) -> bool:
        return not self.infeasibilities

    @property
    def components(self) -> tuple[TrainingComponent, ...]:
        return tuple(component for day in self.days for component in day.components)


@dataclass(frozen=True)
class PhysiologicalWorkSummary:
    """Descriptive work inside a materialized component, never physical dose.

    ``work_minutes`` is intentionally separate from a component's physical
    duration: it can describe AT/TR/AN/PP work (or an aerobic band) but cannot
    satisfy, reserve, or replace calendar time.
    """

    band: str | None = None
    work_minutes: int = 0
    rationale_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.work_minutes < 0:
            raise ValueError("physiological_work_minutes_must_be_nonnegative")
        if self.work_minutes and not self.band:
            raise ValueError("physiological_work_band_is_required")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))


@dataclass(frozen=True)
class MaterializedTrainingComponent:
    """Immutable exact-duration expression of one R4 ``TrainingComponent``."""

    component_id: str
    source_lineage_id: str
    catalog_archetype_id: str | None
    catalog_version: str | None
    materializer_policy_version: str
    modality: TrainingModality
    purpose: TrainingComponentPurpose
    physical_duration_minutes: int
    prescribed_work_minutes: int = 0
    warmup_minutes: int = 0
    recovery_minutes: int = 0
    cooldown_minutes: int = 0
    easy_extension_minutes: int = 0
    interval_structure: Mapping[str, Any] = field(default_factory=dict)
    physiological_work: PhysiologicalWorkSummary | None = None
    selection_rationale_codes: tuple[str, ...] = ()
    constraint_codes: tuple[str, ...] = ()
    adaptation_metadata: Mapping[str, Any] = field(default_factory=dict)
    final_fingerprint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.component_id or not self.source_lineage_id or not self.materializer_policy_version:
            raise ValueError("materialized_component_identity_is_required")
        values = (
            self.physical_duration_minutes, self.prescribed_work_minutes,
            self.warmup_minutes, self.recovery_minutes, self.cooldown_minutes,
            self.easy_extension_minutes,
        )
        if any(value < 0 for value in values) or self.physical_duration_minutes <= 0:
            raise ValueError("materialized_component_minutes_are_invalid")
        if sum(values[1:]) != self.physical_duration_minutes:
            raise ValueError("materialized_component_duration_breakdown_mismatch")
        if self.physiological_work and self.physiological_work.work_minutes > self.prescribed_work_minutes:
            raise ValueError("physiological_work_cannot_exceed_prescribed_work")
        object.__setattr__(self, "interval_structure", _freeze(self.interval_structure))
        object.__setattr__(self, "adaptation_metadata", _freeze(self.adaptation_metadata))
        object.__setattr__(self, "final_fingerprint", _freeze(self.final_fingerprint))
        object.__setattr__(self, "selection_rationale_codes", tuple(sorted(set(self.selection_rationale_codes))))
        object.__setattr__(self, "constraint_codes", tuple(sorted(set(self.constraint_codes))))


@dataclass(frozen=True)
class MaterializationFailure:
    """Deterministic diagnostics for an unsatisfied component requirement."""

    component_id: str
    reason_code: str
    requested_duration_minutes: int
    purpose: TrainingComponentPurpose
    modality_constraint: tuple[TrainingModality, ...] = ()
    catalog_version: str | None = None
    compatible_envelopes: tuple[tuple[str, int, int], ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.component_id or not self.reason_code or self.requested_duration_minutes <= 0:
            raise ValueError("materialization_failure_values_are_invalid")
        object.__setattr__(self, "modality_constraint", tuple(sorted(set(self.modality_constraint), key=lambda item: item.value)))
        object.__setattr__(self, "compatible_envelopes", tuple(sorted(set(self.compatible_envelopes))))
        object.__setattr__(self, "detail", _freeze(self.detail))


@dataclass(frozen=True)
class MaterializedTrainingComponentResult:
    component_id: str
    materialized: MaterializedTrainingComponent | None = None
    failure: MaterializationFailure | None = None

    def __post_init__(self) -> None:
        if bool(self.materialized) == bool(self.failure):
            raise ValueError("materialized_component_result_requires_exactly_one_outcome")
        if self.materialized and self.materialized.component_id != self.component_id:
            raise ValueError("materialized_component_result_identity_mismatch")
        if self.failure and self.failure.component_id != self.component_id:
            raise ValueError("materialization_failure_identity_mismatch")

    @property
    def success(self) -> bool:
        return self.materialized is not None


@dataclass(frozen=True)
class RowingQualityRequirement:
    """One physiological work requirement within fixed R3 rowing capacity.

    The work minutes below are not a calendar reservation.  R4 selects a
    physical component duration inside one of ``compatible_envelopes`` and R5
    later realizes that component exactly.
    """

    requirement_id: str
    family: RowingQualityFamily
    physiological_band: str
    desired_work_minutes: int
    minimum_work_minutes: int
    maximum_work_minutes: int
    minimum_exposures: int
    target_exposures: int
    maximum_exposures: int
    compatible_envelopes: tuple[tuple[str, int, int], ...]
    minimum_spacing_hours: int = 0
    recovery_constraint_codes: tuple[str, ...] = ()
    rationale_codes: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    priority: QualityRequirementPriority = QualityRequirementPriority.PREFERRED

    def __post_init__(self) -> None:
        if not self.requirement_id or not self.physiological_band:
            raise ValueError("rowing_quality_requirement_identity_is_required")
        values = (
            self.desired_work_minutes, self.minimum_work_minutes,
            self.maximum_work_minutes, self.minimum_exposures,
            self.target_exposures, self.maximum_exposures,
            self.minimum_spacing_hours,
        )
        if any(value < 0 for value in values):
            raise ValueError("rowing_quality_requirement_values_are_invalid")
        if not self.minimum_work_minutes <= self.desired_work_minutes <= self.maximum_work_minutes:
            raise ValueError("rowing_quality_requirement_work_range_is_invalid")
        if not self.minimum_exposures <= self.target_exposures <= self.maximum_exposures:
            raise ValueError("rowing_quality_requirement_exposure_range_is_invalid")
        if self.target_exposures and not self.compatible_envelopes:
            raise ValueError("rowing_quality_requirement_requires_compatible_envelope")
        normalized = tuple(sorted(set(self.compatible_envelopes)))
        if any(not archetype_id or minimum <= 0 or maximum < minimum for archetype_id, minimum, maximum in normalized):
            raise ValueError("rowing_quality_requirement_envelopes_are_invalid")
        object.__setattr__(self, "compatible_envelopes", normalized)
        object.__setattr__(self, "recovery_constraint_codes", tuple(sorted(set(self.recovery_constraint_codes))))
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


@dataclass(frozen=True)
class RowingQualityInfeasibility:
    """A composition conflict; it never changes the R3 physical allocation."""

    code: str
    allocation_segment_id: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code or not self.allocation_segment_id:
            raise ValueError("rowing_quality_infeasibility_identity_is_required")
        object.__setattr__(self, "detail", _freeze(self.detail))


@dataclass(frozen=True)
class RowingQualityPlan:
    """R6 physiological composition over one exact R3 allocation segment."""

    allocation_segment_id: str
    segment_start: date
    segment_end: date
    rowing_specific_minutes: int
    low_intensity_guard_minutes: int
    requirements: tuple[RowingQualityRequirement, ...]
    dominant_focus: str
    secondary_focus: str | None
    performance_confidence: str
    completed_quality_history_summary: tuple[str, ...] = ()
    spacing_constraint_codes: tuple[str, ...] = ()
    rationale_codes: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    policy_version: str = "rowing-quality-plan-v1"
    performance_profile_version: str = ""
    basis_fingerprint: str = ""
    technical_emphasis: bool = False
    recovery_emphasis: bool = False

    def __post_init__(self) -> None:
        if (
            not self.allocation_segment_id or self.segment_end < self.segment_start
            or self.rowing_specific_minutes < 0 or self.low_intensity_guard_minutes < 0
            or self.low_intensity_guard_minutes > self.rowing_specific_minutes
            or not self.dominant_focus or not self.policy_version
            or not self.performance_profile_version or not self.basis_fingerprint
        ):
            raise ValueError("rowing_quality_plan_values_are_invalid")
        ids = tuple(item.requirement_id for item in self.requirements)
        if len(ids) != len(set(ids)) or tuple(sorted(self.requirements, key=lambda item: item.requirement_id)) != self.requirements:
            raise ValueError("rowing_quality_plan_requirements_must_be_unique_and_ordered")
        work = sum(item.desired_work_minutes for item in self.requirements)
        if work > self.rowing_specific_minutes - self.low_intensity_guard_minutes:
            raise ValueError("rowing_quality_plan_violates_low_intensity_guard")
        object.__setattr__(self, "completed_quality_history_summary", tuple(sorted(set(self.completed_quality_history_summary))))
        object.__setattr__(self, "spacing_constraint_codes", tuple(sorted(set(self.spacing_constraint_codes))))
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


@dataclass(frozen=True)
class RowingQualityPlanResult:
    plan: RowingQualityPlan | None = None
    infeasibilities: tuple[RowingQualityInfeasibility, ...] = ()

    def __post_init__(self) -> None:
        if self.plan is None and not self.infeasibilities:
            raise ValueError("rowing_quality_result_requires_plan_or_infeasibility")
        object.__setattr__(self, "infeasibilities", tuple(sorted(
            self.infeasibilities,
            key=lambda item: (item.code, item.allocation_segment_id, repr(item.detail)),
        )))

    @property
    def success(self) -> bool:
        return self.plan is not None and not self.infeasibilities


@dataclass(frozen=True)
class TrainingAllocationInfeasibility:
    code: str
    segment_start: date
    segment_end: date
    required_strength_minutes: int
    rowing_specific_floor_minutes: int
    recommended_physical_minutes: int
    shortfall_minutes: int
    conflicting_constraint_provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        if min(
            self.required_strength_minutes, self.rowing_specific_floor_minutes,
            self.recommended_physical_minutes, self.shortfall_minutes,
        ) < 0 or self.shortfall_minutes == 0:
            raise ValueError("training_allocation_infeasibility_values_are_invalid")
        object.__setattr__(self, "conflicting_constraint_provenance", tuple(sorted(set(self.conflicting_constraint_provenance))))


@dataclass(frozen=True)
class TrainingAllocationResult:
    allocation: TrainingAllocation | None = None
    infeasibility: TrainingAllocationInfeasibility | None = None

    def __post_init__(self) -> None:
        if (self.allocation is None) == (self.infeasibility is None):
            raise ValueError("training_allocation_result_must_have_exactly_one_outcome")

    @property
    def success(self) -> bool:
        return self.allocation is not None


_MODALITY_ALIASES = {
    "rowing": TrainingModality.ERG, "row": TrainingModality.ERG,
    "bike_erg": TrainingModality.BIKE, "stationary_bike": TrainingModality.BIKE,
    "cycling": TrainingModality.BIKE, "bicycle": TrainingModality.BIKE,
    "run": TrainingModality.RUNNING, "stairclimber": TrainingModality.STAIR_CLIMBER,
}


def normalize_training_modality(value: Any) -> TrainingModality | None:
    """Normalize safe legacy aliases without reinterpreting unknown entries."""
    if not isinstance(value, str):
        return None
    normalized = value.lower().strip().replace("-", "_").replace(" ", "_")
    try:
        return TrainingModality(normalized)
    except ValueError:
        return _MODALITY_ALIASES.get(normalized)


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
    general_aerobic_minutes: int = 0
    general_aerobic_exposures: int = 0
    # A fact may be both rowing-specific and generally aerobic, but it is
    # still exactly one physical exposure in this identity ledger.
    physical_endurance_minutes: int = 0
    # Physical training is a non-additive identity ledger above endurance:
    # strength and endurance each contribute once, while descriptive rowing /
    # general-aerobic classifications never add a second charge.
    physical_training_minutes: int = 0
    rowing_specific_minutes: int = 0
    physical_endurance_fact_ids: tuple[str, ...] = ()
    physical_training_fact_ids: tuple[str, ...] = ()
    rowing_specific_fact_ids: tuple[str, ...] = ()
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
            self.general_aerobic_minutes, self.general_aerobic_exposures,
            self.physical_endurance_minutes, self.rowing_specific_minutes,
            self.physical_training_minutes,
        ):
            if value < 0:
                raise ValueError("recent_load_values_must_be_nonnegative")
        object.__setattr__(self, "counted_fact_ids", tuple(sorted(set(self.counted_fact_ids))))
        object.__setattr__(self, "excluded_outside_window_fact_ids", tuple(sorted(set(self.excluded_outside_window_fact_ids))))
        object.__setattr__(self, "physical_endurance_fact_ids", tuple(sorted(set(self.physical_endurance_fact_ids))))
        object.__setattr__(self, "physical_training_fact_ids", tuple(sorted(set(self.physical_training_fact_ids))))
        object.__setattr__(self, "rowing_specific_fact_ids", tuple(sorted(set(self.rowing_specific_fact_ids))))


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
    selected_categorical_rowing_minutes: int = 0
    selected_residual_rowing_minutes: int = 0
    selected_categorical_low_intensity_minutes: int = 0
    selected_residual_low_intensity_minutes: int = 0
    overage_minutes: int = 0
    overage_sources: tuple[str, ...] = ()
    suppressed_residual_due_to_shorter_horizon_maximum: int = 0
    low_intensity_conflict_with_maximum: bool = False

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
            self.selected_categorical_rowing_minutes, self.selected_residual_rowing_minutes,
            self.selected_categorical_low_intensity_minutes, self.selected_residual_low_intensity_minutes,
            self.overage_minutes, self.suppressed_residual_due_to_shorter_horizon_maximum,
        )
        if any(item < 0 for item in values) or not self.minimum_rowing_minutes <= self.total_rowing_target_minutes <= self.maximum_rowing_minutes:
            raise ValueError("training_load_target_values_are_invalid")
        if self.strength_minimum_exposures > self.strength_target_exposures:
            raise ValueError("training_load_target_strength_range_is_invalid")
        object.__setattr__(self, "source_policy_ids", tuple(sorted(set(self.source_policy_ids))))
        object.__setattr__(self, "rationale", tuple(sorted(set(self.rationale))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))
        object.__setattr__(self, "overage_sources", tuple(sorted(set(self.overage_sources))))


@dataclass(frozen=True)
class EnduranceDemandTarget:
    """One non-additive physical endurance budget for a rolling horizon.

    ``TrainingLoadTarget`` remains the current rowing-planning contract. This
    M2 object mirrors that approved envelope while separately exposing the
    rowing-specific work protected before later substitution is considered.
    """

    id: str
    source_load_target_id: str
    horizon_start: date
    horizon_end: date
    horizon_days: int
    phase: str
    total_minimum_minutes: int
    total_target_minutes: int
    total_maximum_minutes: int
    completed_physical_endurance_minutes: int
    authoritative_future_physical_endurance_minutes: int
    completed_rowing_specific_minutes: int
    authoritative_future_rowing_specific_minutes: int
    protected_rowing_specific_minutes: int
    eligible_general_aerobic_residual_minutes: int
    source_policy_ids: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon_days < 1 or self.horizon_end != self.horizon_start + timedelta(days=self.horizon_days - 1):
            raise ValueError("endurance_demand_target_horizon_is_invalid")
        values = (
            self.total_minimum_minutes, self.total_target_minutes, self.total_maximum_minutes,
            self.completed_physical_endurance_minutes, self.authoritative_future_physical_endurance_minutes,
            self.completed_rowing_specific_minutes, self.authoritative_future_rowing_specific_minutes,
            self.protected_rowing_specific_minutes, self.eligible_general_aerobic_residual_minutes,
        )
        if any(item < 0 for item in values) or not self.total_minimum_minutes <= self.total_target_minutes <= self.total_maximum_minutes:
            raise ValueError("endurance_demand_target_values_are_invalid")
        object.__setattr__(self, "source_policy_ids", tuple(sorted(set(self.source_policy_ids))))
        object.__setattr__(self, "rationale", tuple(sorted(set(self.rationale))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


@dataclass(frozen=True)
class TotalTrainingLoadTarget:
    """One rolling physical-duration envelope above strength and endurance.

    It deliberately tracks session reservations, not overlapping physiological
    descriptors.  Therefore a strength session and an erg session each charge
    once; an erg session that is also low-intensity/general-aerobic does not.
    """

    id: str
    horizon_start: date
    horizon_end: date
    horizon_days: int
    phase: str
    minimum_minutes: int
    target_minutes: int
    maximum_minutes: int
    completed_physical_training_minutes: int
    authoritative_future_physical_training_minutes: int
    selected_physical_training_minutes: int = 0
    selected_endurance_minutes: int = 0
    selected_rowing_specific_minutes: int = 0
    selected_strength_minutes: int = 0
    selected_cross_training_minutes: int = 0
    source_policy_ids: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon_days < 1 or self.horizon_end != self.horizon_start + timedelta(days=self.horizon_days - 1):
            raise ValueError("total_training_load_target_horizon_is_invalid")
        values = (
            self.minimum_minutes, self.target_minutes, self.maximum_minutes,
            self.completed_physical_training_minutes,
            self.authoritative_future_physical_training_minutes,
            self.selected_physical_training_minutes,
            self.selected_endurance_minutes, self.selected_rowing_specific_minutes,
            self.selected_strength_minutes, self.selected_cross_training_minutes,
        )
        if any(item < 0 for item in values) or not self.minimum_minutes <= self.target_minutes <= self.maximum_minutes:
            raise ValueError("total_training_load_target_values_are_invalid")
        object.__setattr__(self, "source_policy_ids", tuple(sorted(set(self.source_policy_ids))))
        object.__setattr__(self, "rationale", tuple(sorted(set(self.rationale))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


@dataclass(frozen=True)
class TrainingCompositionTarget:
    """Athlete-relative emphasis inside existing rolling load envelopes.

    This is intentionally not an intensity-percentage prescription.  It
    identifies at most one compatible development role and leaves actual
    minute, recovery, capacity, and maximum-load enforcement to existing
    needs and placement contracts.
    """

    horizon_start: date
    horizon_end: date
    horizon_days: int
    dominant_focus: str
    secondary_focus: str | None
    protected_low_intensity_minutes: int
    desired_development_roles: tuple[TrainingRole, ...] = ()
    maximum_quality_exposures: int = 1
    confidence: str = "unavailable"
    limiter_mode: str = "unknown"
    rationale: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon_days < 1 or self.horizon_end != self.horizon_start + timedelta(days=self.horizon_days - 1):
            raise ValueError("training_composition_target_horizon_is_invalid")
        if self.protected_low_intensity_minutes < 0 or self.maximum_quality_exposures < 0:
            raise ValueError("training_composition_target_values_are_invalid")
        object.__setattr__(self, "desired_development_roles", tuple(sorted(set(self.desired_development_roles), key=lambda item: item.value)))
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
    original_target_credit_minutes: int | None = None
    partition_reason: str | None = None

    def __post_init__(self) -> None:
        if self.earliest_date > self.latest_date:
            raise ValueError("training_need_window_is_reversed")
        if self.state not in {"active", "satisfied", "partially_satisfied", "deferred", "superseded", "reduced", "missed"}:
            raise ValueError("training_need_state_is_invalid")
        if self.exposure_count < 1 or not 1 <= self.exposure_index <= self.exposure_count:
            raise ValueError("training_need_exposure_cardinality_is_invalid")
        if self.original_target_credit_minutes is not None and self.original_target_credit_minutes < self.target_credit.minutes:
            raise ValueError("training_need_partition_credit_exceeds_original")


@dataclass(frozen=True)
class PhysicalExposureChild:
    """One catalog-feasible physical representation of approved credit.

    The child is deliberately upstream of date placement: it says what may be
    scheduled, never that it has been scheduled.
    """
    child_id: str
    source_need_ids: tuple[str, ...]
    role: TrainingRole
    target_credit: PhysiologicalCredit
    minimum_materializable_envelope: int
    preferred_envelope: int
    earliest_date: date
    latest_date: date
    purpose_retained: str
    distribution_reason: str
    catalog_capability: str | None = None
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.earliest_date > self.latest_date:
            raise ValueError("physical_exposure_child_window_is_reversed")
        if self.minimum_materializable_envelope < 0 or self.preferred_envelope < self.minimum_materializable_envelope:
            raise ValueError("physical_exposure_child_envelope_is_invalid")
        object.__setattr__(self, "source_need_ids", tuple(sorted(set(self.source_need_ids))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


@dataclass(frozen=True)
class PhysicalExposurePlan:
    """Typed bridge from approved physiological credit to physical sessions."""
    plan_id: str
    source_need_ids: tuple[str, ...]
    source_credit_total: int
    realized_credit_minutes: int
    unrealized_credit_minutes: int
    physiological_purposes: tuple[str, ...]
    physical_exposure_count: int
    children: tuple[PhysicalExposureChild, ...]
    splittable_credit_minutes: int
    non_splittable_credit_minutes: int
    distribution_rationale: tuple[str, ...]
    race_taper_context: str | None
    frequency_target_served: int | None
    total_credit_conserved: bool
    rolling_max_feasible: bool
    failure_reason: str | None = None
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_credit_total < 0 or self.physical_exposure_count < 0:
            raise ValueError("physical_exposure_plan_values_are_invalid")
        if self.physical_exposure_count != len(self.children):
            raise ValueError("physical_exposure_plan_child_count_mismatch")
        if self.realized_credit_minutes < 0 or self.unrealized_credit_minutes < 0 or self.realized_credit_minutes + self.unrealized_credit_minutes != self.source_credit_total:
            raise ValueError("physical_exposure_plan_realization_is_invalid")
        child_total = sum(item.target_credit.minutes for item in self.children)
        if any(item.target_credit.minutes <= 0 for item in self.children):
            raise ValueError("physical_exposure_plan_child_credit_must_be_positive")
        if child_total != self.realized_credit_minutes or child_total > self.source_credit_total:
            raise ValueError("physical_exposure_plan_credit_conservation_mismatch")
        if not self.total_credit_conserved:
            raise ValueError("physical_exposure_plan_credit_not_conserved")
        object.__setattr__(self, "source_need_ids", tuple(sorted(set(self.source_need_ids))))
        object.__setattr__(self, "physiological_purposes", tuple(sorted(set(self.physiological_purposes))))
        object.__setattr__(self, "distribution_rationale", tuple(sorted(set(self.distribution_rationale))))
        object.__setattr__(self, "provenance", tuple(sorted(set(self.provenance))))


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
