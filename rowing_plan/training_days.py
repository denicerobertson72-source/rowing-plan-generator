"""R4 component-aware physical calendar foundation.

This is deliberately a small, deterministic placement layer.  It consumes the
authoritative R3 allocation exactly; it neither selects catalog workouts nor
rowing intensity composition.  It resolves an athlete-approved general-aerobic
modality because that decision needs the dated placement context, not another
physical-dose allocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from hashlib import sha256
from typing import Iterable, Mapping

from .planning_domain import (
    ComponentAuthority,
    GeneralAerobicModalityCapability,
    PhysicalCategory,
    RaceEvent,
    TrainingAllocation,
    TrainingAllocationSegment,
    TrainingComponent,
    TrainingComponentPurpose,
    TrainingDay,
    TrainingDayDiagnostic,
    TrainingDayInfeasibility,
    TrainingDayPlanResult,
    TrainingDayState,
    TrainingModality,
    CurrentTrainingState,
    QualityRequirementPriority,
    RowingQualityPlan,
    RowingQualityRequirement,
)
from .materialization_v3 import (
    MaterializationContext, preflight_quality_duration_v3,
    preflight_rowing_duration_v3,
)


def allocation_segment_id(segment: TrainingAllocationSegment) -> str:
    """A stable R3 lineage key; it is independent of component date/order."""
    return f"r3:{segment.start_date.isoformat()}:{segment.end_date.isoformat()}"


def stable_component_identity(*, source_lineage_id: str, allocation_segment_id: str,
                              physical_category: PhysicalCategory,
                              duration_minutes: int,
                              purpose: TrainingComponentPurpose,
                              authority: ComponentAuthority,
                              modality: TrainingModality | None = None) -> str:
    """Return a deterministic semantic identity, deliberately excluding date/sequence."""
    payload = "|".join((
        "training-component-v1", source_lineage_id, allocation_segment_id,
        physical_category.value, str(duration_minutes), purpose.value,
        authority.value, modality.value if modality else "",
    ))
    return f"cmp_{sha256(payload.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True)
class DailyTrainingCapacity:
    date: date
    available_minutes: int
    available: bool = True
    fixed_rest: bool = False
    restriction_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.available_minutes < 0:
            raise ValueError("daily_training_capacity_minutes_must_be_nonnegative")
        if self.fixed_rest and not self.available:
            raise ValueError("daily_training_capacity_rest_must_be_available")
        object.__setattr__(self, "restriction_codes", tuple(sorted(set(self.restriction_codes))))

    @property
    def usable_minutes(self) -> int:
        return self.available_minutes if self.available and not self.fixed_rest else 0


@dataclass(frozen=True)
class AuthoritativeComponentFact:
    """A fixed dated activity that must survive R4 placement.

    A missing category/modality is intentionally representable so the result
    can return ``unresolved_authority_classification`` without inventing
    ``OTHER`` work.
    """

    authority_id: str
    date: date
    duration_minutes: int
    physical_category: PhysicalCategory | None = None
    modality: TrainingModality | None = None
    purpose: TrainingComponentPurpose | None = None
    rationale_codes: tuple[str, ...] = ()
    constraint_codes: tuple[str, ...] = ()
    physiological_band: str | None = None
    quality_work_minutes: int | None = None
    user_fixed: bool = False

    def __post_init__(self) -> None:
        if not self.authority_id or self.duration_minutes <= 0:
            raise ValueError("authoritative_component_fact_values_are_invalid")
        if self.quality_work_minutes is not None and self.quality_work_minutes < 0:
            raise ValueError("authoritative_component_fact_quality_work_is_invalid")
        if self.quality_work_minutes is not None and not self.physiological_band:
            raise ValueError("authoritative_component_fact_quality_band_is_required")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "constraint_codes", tuple(sorted(set(self.constraint_codes))))


@dataclass(frozen=True)
class RowingComponentRequirement:
    """Derived R6/R5 placement input; never an independent dose authority."""

    requirement_id: str
    exposure_id: str
    family: str
    purpose: TrainingComponentPurpose
    physiological_band: str
    desired_work_minutes: int
    minimum_work_minutes: int
    maximum_work_minutes: int
    minimum_spacing_hours: int
    priority: QualityRequirementPriority
    rationale_codes: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class CombinedDayPreferences:
    combined_days: str = "allowed"  # avoid | allowed | preferred
    strength_plus_easy_aerobic: str = "allowed"  # avoid | allowed | preferred
    strength_plus_hard_rowing: str = "avoid"  # avoid | allowed
    maximum_combined_days_per_week: int | None = None

    def __post_init__(self) -> None:
        if self.combined_days not in {"avoid", "allowed", "preferred"}:
            raise ValueError("combined_days_preference_is_invalid")
        if self.strength_plus_easy_aerobic not in {"avoid", "allowed", "preferred"}:
            raise ValueError("strength_plus_easy_aerobic_preference_is_invalid")
        if self.strength_plus_hard_rowing not in {"avoid", "allowed"}:
            raise ValueError("strength_plus_hard_rowing_preference_is_invalid")
        if self.maximum_combined_days_per_week is not None and self.maximum_combined_days_per_week < 0:
            raise ValueError("maximum_combined_days_per_week_is_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "combined_days": self.combined_days,
            "strength_plus_easy_aerobic": self.strength_plus_easy_aerobic,
            "strength_plus_hard_rowing": self.strength_plus_hard_rowing,
            "maximum_combined_days_per_week": self.maximum_combined_days_per_week,
        }


def _category_from_modality(modality: TrainingModality | None) -> PhysicalCategory | None:
    if modality in {TrainingModality.ERG, TrainingModality.ON_WATER}:
        return PhysicalCategory.ROWING_SPECIFIC
    if modality is TrainingModality.STRENGTH:
        return PhysicalCategory.STRENGTH
    if modality in {TrainingModality.BIKE, TrainingModality.ELLIPTICAL, TrainingModality.STAIR_CLIMBER, TrainingModality.RUNNING}:
        return PhysicalCategory.GENERAL_AEROBIC
    return None


def _default_purpose(category: PhysicalCategory) -> TrainingComponentPurpose:
    return {
        PhysicalCategory.ROWING_SPECIFIC: TrainingComponentPurpose.EASY_AEROBIC,
        PhysicalCategory.STRENGTH: TrainingComponentPurpose.STRENGTH,
        PhysicalCategory.GENERAL_AEROBIC: TrainingComponentPurpose.EASY_AEROBIC,
        PhysicalCategory.OTHER: TrainingComponentPurpose.RECOVERY,
    }[category]


_GENERAL_AEROBIC_PREFERENCE = {"prefer": 0, "neutral": 1}
_GENERAL_AEROBIC_FAMILIARITY = {"regular": 0, "some_experience": 1, "new": 2, None: 3}


def _resolve_general_aerobic_modality(
    capabilities: tuple[GeneralAerobicModalityCapability, ...],
) -> GeneralAerobicModalityCapability | None:
    """Choose only an R3-carried athlete-approved modality, deterministically.

    Preference is the primary athlete direction, familiarity is the stable
    tie-break, and modality name makes otherwise equal profile input fully
    deterministic.  R4 owns this selection because it creates the dated
    component; R3 remains the sole authority for *whether* general-aerobic
    minutes exist.
    """
    if not capabilities:
        return None
    return min(
        capabilities,
        key=lambda item: (
            _GENERAL_AEROBIC_PREFERENCE[item.preference],
            _GENERAL_AEROBIC_FAMILIARITY[item.familiarity],
            item.modality.value,
        ),
    )


_QUALITY_PURPOSE_BY_FAMILY = {
    "aerobic_strength": TrainingComponentPurpose.AEROBIC_STRENGTH,
    "threshold": TrainingComponentPurpose.THRESHOLD,
    "race_specific": TrainingComponentPurpose.RACE_SPECIFIC,
    "anaerobic": TrainingComponentPurpose.ANAEROBIC,
    "power": TrainingComponentPurpose.POWER,
}


def _rowing_component_requirements(plan: RowingQualityPlan) -> tuple[RowingComponentRequirement, ...]:
    result: list[RowingComponentRequirement] = []
    for item in plan.requirements:
        purpose = _QUALITY_PURPOSE_BY_FAMILY.get(item.family.value)
        if purpose is None:
            continue
        for ordinal in range(1, item.target_exposures + 1):
            result.append(RowingComponentRequirement(
                item.requirement_id, f"{item.requirement_id}:exposure:{ordinal}", item.family.value,
                purpose, item.physiological_band, item.desired_work_minutes,
                item.minimum_work_minutes, item.maximum_work_minutes,
                item.minimum_spacing_hours, item.priority,
                item.rationale_codes, item.provenance,
            ))
    return tuple(result)


def _segment_for(day: date, segments: Iterable[TrainingAllocationSegment]) -> TrainingAllocationSegment | None:
    return next((segment for segment in segments if segment.start_date <= day <= segment.end_date), None)


def _dates(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _calendar_week(day: date) -> tuple[int, int]:
    iso = day.isocalendar()
    return iso.year, iso.week


def _combined_days_in_week(days: Mapping[date, list[TrainingComponent]], candidate: date) -> int:
    key = _calendar_week(candidate)
    return sum(len(items) > 1 for current, items in days.items() if _calendar_week(current) == key)


def _can_combine(days: Mapping[date, list[TrainingComponent]], candidate: date,
                 preferences: CombinedDayPreferences) -> bool:
    current = days[candidate]
    if not current:
        return True
    if preferences.maximum_combined_days_per_week is None:
        return True
    # A day which is already combined does not create another combined day.
    if len(current) > 1:
        return True
    return _combined_days_in_week(days, candidate) < preferences.maximum_combined_days_per_week


def _candidate_sort_key(days: Mapping[date, list[TrainingComponent]], candidate: date,
                        category: PhysicalCategory, preferences: CombinedDayPreferences) -> tuple[int, date]:
    existing = days[candidate]
    combined = bool(existing)
    preference = preferences.combined_days
    if category is PhysicalCategory.GENERAL_AEROBIC and any(item.physical_category is PhysicalCategory.STRENGTH for item in existing):
        # Avoidance is the conservative result when broad and pair-specific
        # lifestyle preferences disagree.  This remains a soft ordering, not
        # a dose or hard-compatibility rule.
        pair = preferences.strength_plus_easy_aerobic
        if "avoid" in {preference, pair}:
            preference = "avoid"
        elif "preferred" in {preference, pair}:
            preference = "preferred"
    penalty = 0
    if preference == "avoid":
        penalty = 1 if combined else 0
    elif preference == "preferred":
        penalty = 0 if combined else 1
    return penalty, candidate


def _make_component(*, lineage: str, segment_id: str, category: PhysicalCategory,
                    minutes: int, purpose: TrainingComponentPurpose,
                    authority: ComponentAuthority, sequence: int,
                    modality: TrainingModality | None = None,
                    permitted_modalities: tuple[TrainingModality, ...] = (),
                    rationale_codes: tuple[str, ...] = (),
                    constraint_codes: tuple[str, ...] = (),
                    quality_requirement_id: str | None = None,
                    quality_exposure_id: str | None = None,
                    physiological_band: str | None = None,
                    quality_work_minutes: int | None = None,
                    minimum_quality_work_minutes: int | None = None,
                    maximum_quality_work_minutes: int | None = None,
                    capability_provenance: tuple[str, ...] = ()) -> TrainingComponent:
    return TrainingComponent(
        component_id=stable_component_identity(
            source_lineage_id=lineage, allocation_segment_id=segment_id,
            physical_category=category, duration_minutes=minutes,
            purpose=purpose, authority=authority, modality=modality,
        ),
        source_lineage_id=lineage, allocation_segment_id=segment_id,
        physical_category=category, duration_minutes=minutes, purpose=purpose,
        sequence=sequence, modality=modality, permitted_modalities=permitted_modalities,
        authority=authority, rationale_codes=rationale_codes,
        constraint_codes=constraint_codes,
        quality_requirement_id=quality_requirement_id,
        quality_exposure_id=quality_exposure_id,
        physiological_band=physiological_band,
        quality_work_minutes=quality_work_minutes,
        minimum_quality_work_minutes=minimum_quality_work_minutes,
        maximum_quality_work_minutes=maximum_quality_work_minutes,
        capability_provenance=capability_provenance,
    )


def generate_training_days_v3(
    allocation: TrainingAllocation, capacity: Iterable[DailyTrainingCapacity],
    authorities: Iterable[AuthoritativeComponentFact] = (),
    preferences: CombinedDayPreferences = CombinedDayPreferences(),
    restrictions: Mapping[date, tuple[str, ...]] | None = None,
    race_context: Iterable[RaceEvent] = (),
    rowing_quality_plans: Iterable[RowingQualityPlan] = (),
    materialization_context: MaterializationContext = MaterializationContext(),
    current_training_state: CurrentTrainingState | None = None,
) -> TrainingDayPlanResult:
    """Create a deterministic component calendar without catalog materialization.

    This foundation intentionally uses a simple capability-first date chooser.
    R4B represents combined-day preferences and calendar-week limits, but does
    not claim to be the future physiological placement scorer.
    """
    capacity_by_date = {item.date: item for item in capacity}
    expected_dates = _dates(allocation.planning_start, allocation.planning_end)
    diagnostics: list[TrainingDayDiagnostic] = []
    infeasibilities: list[TrainingDayInfeasibility] = []
    restrictions = restrictions or {}
    quality_plan_by_segment = {item.allocation_segment_id: item for item in rowing_quality_plans}
    for day in expected_dates:
        if day not in capacity_by_date:
            capacity_by_date[day] = DailyTrainingCapacity(day, 0, available=False, restriction_codes=("missing_capacity",))
            diagnostics.append(TrainingDayDiagnostic("missing_daily_capacity", day))
    # Ignore unrelated capacity facts rather than manufacturing extra days.
    daily_components: dict[date, list[TrainingComponent]] = {day: [] for day in expected_dates}
    authority_ids: dict[date, set[str]] = {day: set() for day in expected_dates}

    authority_category_minutes: dict[tuple[str, PhysicalCategory], int] = {}
    authority_strength_counts: dict[str, int] = {}
    for fact in sorted(authorities, key=lambda item: (item.date, item.authority_id)):
        if fact.date not in daily_components:
            diagnostics.append(TrainingDayDiagnostic("authority_date_outside_planning_range", fact.date, fact.authority_id))
            continue
        if capacity_by_date[fact.date].fixed_rest or not capacity_by_date[fact.date].available:
            diagnostics.append(TrainingDayDiagnostic("authority_day_state_conflict", fact.date, fact.authority_id))
            continue
        category = fact.physical_category or _category_from_modality(fact.modality)
        if category is None:
            diagnostics.append(TrainingDayDiagnostic("unresolved_authority_classification", fact.date, fact.authority_id))
            continue
        segment = _segment_for(fact.date, allocation.segments)
        if segment is None:  # defensive; the date-range check above should make this unreachable
            diagnostics.append(TrainingDayDiagnostic("authority_date_outside_allocation_segment", fact.date, fact.authority_id))
            continue
        segment_id = allocation_segment_id(segment)
        purpose = fact.purpose or _default_purpose(category)
        component = _make_component(
            lineage=f"authority:{fact.authority_id}", segment_id=segment_id,
            category=category, minutes=fact.duration_minutes, purpose=purpose,
            authority=ComponentAuthority.AUTHORITATIVE,
            sequence=len(daily_components[fact.date]) + 1, modality=fact.modality,
            rationale_codes=("fixed_authority", *fact.rationale_codes),
            constraint_codes=(*fact.constraint_codes, *(("user_fixed",) if fact.user_fixed else ())),
            physiological_band=fact.physiological_band,
            quality_work_minutes=fact.quality_work_minutes,
        )
        daily_components[fact.date].append(component)
        authority_ids[fact.date].add(fact.authority_id)
        key = (segment_id, category)
        authority_category_minutes[key] = authority_category_minutes.get(key, 0) + fact.duration_minutes
        if category is PhysicalCategory.STRENGTH:
            authority_strength_counts[segment_id] = authority_strength_counts.get(segment_id, 0) + 1

    blocked_generated_dates: set[date] = set()
    for day in expected_dates:
        fact = capacity_by_date[day]
        authoritative_minutes = sum(item.duration_minutes for item in daily_components[day])
        if authoritative_minutes > fact.usable_minutes:
            blocked_generated_dates.add(day)
            diagnostics.append(TrainingDayDiagnostic("authoritative_capacity_conflict", day))

    def remaining(day: date) -> int:
        return max(0, capacity_by_date[day].usable_minutes - sum(item.duration_minutes for item in daily_components[day]))

    def eligible_days(segment: TrainingAllocationSegment, category: PhysicalCategory, minutes: int) -> list[date]:
        result=[]
        for day in _dates(segment.start_date, segment.end_date):
            fact=capacity_by_date[day]
            if not fact.available or fact.fixed_rest or day in blocked_generated_dates:
                continue
            if restrictions.get(day):
                continue
            if remaining(day) < minutes or not _can_combine(daily_components, day, preferences):
                continue
            result.append(day)
        return sorted(
            result,
            key=lambda item: (
                int(
                    category is PhysicalCategory.STRENGTH
                    and any(component.physical_category is PhysicalCategory.STRENGTH
                            for component in daily_components[item])
                ),
                _candidate_sort_key(daily_components, item, category, preferences),
            ),
        )

    def append_generated(day: date, *, lineage: str, segment_id: str, category: PhysicalCategory,
                         minutes: int, purpose: TrainingComponentPurpose,
                         quality: RowingComponentRequirement | None = None,
                         modality: TrainingModality | None = None,
                         capability_provenance: tuple[str, ...] = ()) -> None:
        permitted = ((TrainingModality.ERG, TrainingModality.ON_WATER) if category is PhysicalCategory.ROWING_SPECIFIC
                     else (TrainingModality.BIKE, TrainingModality.ELLIPTICAL, TrainingModality.STAIR_CLIMBER, TrainingModality.RUNNING)
                     if category is PhysicalCategory.GENERAL_AEROBIC else ())
        daily_components[day].append(_make_component(
            lineage=lineage, segment_id=segment_id, category=category, minutes=minutes,
            purpose=purpose, authority=ComponentAuthority.GENERATED,
            sequence=len(daily_components[day]) + 1, modality=modality, permitted_modalities=permitted,
            rationale_codes=("r3_allocation_component",),
            quality_requirement_id=quality.requirement_id if quality else None,
            quality_exposure_id=quality.exposure_id if quality else None,
            physiological_band=quality.physiological_band if quality else None,
            quality_work_minutes=quality.desired_work_minutes if quality else None,
            minimum_quality_work_minutes=quality.minimum_work_minutes if quality else None,
            maximum_quality_work_minutes=quality.maximum_work_minutes if quality else None,
            capability_provenance=capability_provenance,
        ))

    rowing_duration_feasibility: dict[tuple[TrainingComponentPurpose, int], bool] = {}

    def ordinary_rowing_duration_is_feasible(purpose: TrainingComponentPurpose, duration: int) -> bool:
        """Cache R5's exact-envelope answer for bounded R4 feasibility work."""
        key = (purpose, duration)
        if key not in rowing_duration_feasibility:
            rowing_duration_feasibility[key] = bool(preflight_rowing_duration_v3(
                purpose, duration_minutes=duration, context=materialization_context,
            ))
        return rowing_duration_feasibility[key]

    def ordinary_rowing_purpose(segment_id: str) -> TrainingComponentPurpose:
        plan = quality_plan_by_segment.get(segment_id)
        if plan and plan.recovery_emphasis:
            return TrainingComponentPurpose.RECOVERY
        if plan and plan.technical_emphasis:
            return TrainingComponentPurpose.TECHNICAL
        return TrainingComponentPurpose.EASY_AEROBIC

    def residual_rowing_partition_exists(segment: TrainingAllocationSegment, segment_id: str) -> bool:
        """Return whether remaining dated capacity can exactly honour R3 rowing.

        The search is deliberately bounded to one R9B cadence segment.  It
        uses the same exact-duration R5 preflight as ordinary rowing placement
        and therefore prevents strength from fragmenting capacity into values
        that cannot become physical rowing components.
        """
        placed = sum(
            component.duration_minutes
            for current_day in _dates(segment.start_date, segment.end_date)
            for component in daily_components[current_day]
            if (
                component.allocation_segment_id == segment_id
                and component.physical_category is PhysicalCategory.ROWING_SPECIFIC
            )
        )
        residual = segment.rowing_specific_minutes - placed
        if residual < 0:
            return False
        if residual == 0:
            return True
        purpose = ordinary_rowing_purpose(segment_id)
        candidates = [
            current_day for current_day in eligible_days(segment, PhysicalCategory.ROWING_SPECIFIC, 1)
            if not any(item.physical_category is PhysicalCategory.ROWING_SPECIFIC for item in daily_components[current_day])
        ]
        totals = {0}
        for current_day in candidates:
            options = [
                duration for duration in range(1, remaining(current_day) + 1)
                if ordinary_rowing_duration_is_feasible(purpose, duration)
            ]
            totals |= {
                total + duration
                for total in tuple(totals)
                for duration in options
                if total + duration <= residual
            }
        return residual in totals

    def joint_strength_rowing_detail(segment: TrainingAllocationSegment, segment_id: str,
                                     remaining_strength_exposures: int) -> str:
        purpose = ordinary_rowing_purpose(segment_id)
        segment_days = _dates(segment.start_date, segment.end_date)
        usable_capacity = sum(remaining(current_day) for current_day in segment_days)
        unusable = sum(
            remaining(current_day)
            for current_day in segment_days
            if remaining(current_day) and not any(
                ordinary_rowing_duration_is_feasible(purpose, duration)
                for duration in range(1, remaining(current_day) + 1)
            )
        )
        return (
            f"segment={segment.start_date.isoformat()}:{segment.end_date.isoformat()};"
            f"rowing_required={segment.rowing_specific_minutes};"
            f"strength_required={remaining_strength_exposures * segment.expected_strength_duration_minutes};"
            f"strength_exposures_required={remaining_strength_exposures};"
            f"usable_capacity={usable_capacity};unusable_rowing_capacity={unusable}"
        )

    def future_strength_and_rowing_partition_exists(segment: TrainingAllocationSegment, segment_id: str,
                                                     remaining_strength_exposures: int) -> bool:
        """Bounded joint feasibility for remaining strength plus R3 rowing."""
        if remaining_strength_exposures == 0:
            return residual_rowing_partition_exists(segment, segment_id)
        for current_day in eligible_days(segment, PhysicalCategory.STRENGTH, segment.expected_strength_duration_minutes):
            append_generated(
                current_day,
                lineage=f"{segment_id}:strength:probe:{remaining_strength_exposures}",
                segment_id=segment_id, category=PhysicalCategory.STRENGTH,
                minutes=segment.expected_strength_duration_minutes,
                purpose=TrainingComponentPurpose.STRENGTH,
            )
            try:
                if future_strength_and_rowing_partition_exists(
                    segment, segment_id, remaining_strength_exposures - 1,
                ):
                    return True
            finally:
                daily_components[current_day].pop()
        return False

    def strength_day_preserves_rowing_partition(segment: TrainingAllocationSegment, segment_id: str,
                                                current_day: date, remaining_strength_exposures: int) -> bool:
        """Trial a strength date while reserving feasibility for later exposures."""
        append_generated(
            current_day, lineage=f"{segment_id}:strength:probe:{remaining_strength_exposures}", segment_id=segment_id,
            category=PhysicalCategory.STRENGTH,
            minutes=segment.expected_strength_duration_minutes,
            purpose=TrainingComponentPurpose.STRENGTH,
        )
        try:
            return future_strength_and_rowing_partition_exists(
                segment, segment_id, remaining_strength_exposures - 1,
            )
        finally:
            daily_components[current_day].pop()

    def place_quality_aware_rowing(segment: TrainingAllocationSegment, segment_id: str,
                                   plan: RowingQualityPlan) -> None:
        """Replace the legacy generic-rowing fill with an exact R6/R5 partition."""
        requirements = list(_rowing_component_requirements(plan))
        satisfied: set[str] = set()
        quality_dates: list[tuple[date, RowingComponentRequirement]] = []
        user_fixed_quality_dates: list[tuple[date, RowingComponentRequirement]] = []
        low_duration_feasibility: dict[tuple[TrainingComponentPurpose, int], bool] = {}

        def low_duration_is_feasible(purpose: TrainingComponentPurpose, duration: int) -> bool:
            key = (purpose, duration)
            if key not in low_duration_feasibility:
                low_duration_feasibility[key] = preflight_rowing_duration_v3(
                    purpose, duration_minutes=duration,
                    context=materialization_context,
                )
            return low_duration_feasibility[key]

        # Fixed content counts once physically and only satisfies quality when
        # its purpose, band, and known work amount explicitly match.
        for current_day in _dates(segment.start_date, segment.end_date):
            for index, component in enumerate(tuple(daily_components[current_day])):
                if component.physical_category is not PhysicalCategory.ROWING_SPECIFIC:
                    continue
                for requirement in requirements:
                    if requirement.exposure_id in satisfied:
                        continue
                    if (
                        component.purpose is requirement.purpose
                        and component.physiological_band == requirement.physiological_band
                        and component.quality_work_minutes is not None
                        and requirement.minimum_work_minutes <= component.quality_work_minutes <= requirement.maximum_work_minutes
                    ):
                        daily_components[current_day][index] = replace(
                            component,
                            quality_requirement_id=requirement.requirement_id,
                            quality_exposure_id=requirement.exposure_id,
                            minimum_quality_work_minutes=requirement.minimum_work_minutes,
                            maximum_quality_work_minutes=requirement.maximum_work_minutes,
                            capability_provenance=("authoritative_known_content",),
                        )
                        satisfied.add(requirement.exposure_id)
                        quality_dates.append((current_day, requirement))
                        if "user_fixed" in component.constraint_codes:
                            user_fixed_quality_dates.append((current_day, requirement))
                        break

        def spacing_ok(candidate: date, requirement: RowingComponentRequirement) -> bool:
            for prior_day, prior_requirement in quality_dates:
                required_hours = max(requirement.minimum_spacing_hours, prior_requirement.minimum_spacing_hours)
                if abs((candidate - prior_day).days) * 24 < required_hours:
                    return False
            return True

        def candidate_key(candidate: date, duration: int) -> tuple[int, int, date, int]:
            items = daily_components[candidate]
            has_strength = any(item.physical_category is PhysicalCategory.STRENGTH for item in items)
            # Hard rowing with strength is a strong soft avoidance, never a
            # hidden dose mutation.
            hard_penalty = int(has_strength and preferences.strength_plus_hard_rowing == "avoid")
            combined_penalty = int(bool(items) and preferences.combined_days == "avoid")
            typical = current_training_state.longest_comfortable_row_minutes if current_training_state else None
            duration_penalty = abs(duration - typical) if typical else duration
            return hard_penalty, combined_penalty, candidate, duration_penalty

        def residual_partition_feasible_after(quality_day: date, quality_duration: int) -> bool:
            """Prove a quality envelope leaves an exact R3 rowing partition.

            An R6 requirement is physiological only; it must never cause R4
            to strand a non-materializable physical remainder.  This is a
            small, local feasibility query, not another dose allocator.
            """
            placed_after = sum(
                component.duration_minutes
                for current_day in _dates(segment.start_date, segment.end_date)
                for component in daily_components[current_day]
                if (
                    component.allocation_segment_id == segment_id
                    and component.physical_category is PhysicalCategory.ROWING_SPECIFIC
                )
            ) + quality_duration
            residual_after = segment.rowing_specific_minutes - placed_after
            if residual_after < 0:
                return False
            if residual_after == 0:
                return True
            low_purpose = (
                TrainingComponentPurpose.RECOVERY if plan.recovery_emphasis else
                TrainingComponentPurpose.TECHNICAL if plan.technical_emphasis else
                TrainingComponentPurpose.EASY_AEROBIC
            )
            totals = {0}
            for current_day in _dates(segment.start_date, segment.end_date):
                if current_day == quality_day or any(
                    item.physical_category is PhysicalCategory.ROWING_SPECIFIC
                    for item in daily_components[current_day]
                ):
                    continue
                options = [
                    duration
                    for duration in range(1, remaining(current_day) + 1)
                    if low_duration_is_feasible(low_purpose, duration)
                ]
                totals |= {
                    total + duration
                    for total in tuple(totals)
                    for duration in options
                    if total + duration <= residual_after
                }
            return residual_after in totals

        for requirement in sorted(requirements, key=lambda item: (item.priority.value != "hard", item.priority.value != "preferred", item.exposure_id)):
            if requirement.exposure_id in satisfied:
                continue
            possibilities: list[tuple[tuple[int, int, date, int], date, int, tuple[str, ...]]] = []
            for current_day in eligible_days(segment, PhysicalCategory.ROWING_SPECIFIC, 1):
                if not spacing_ok(current_day, requirement):
                    continue
                for duration in range(1, remaining(current_day) + 1):
                    preflight = preflight_quality_duration_v3(
                        requirement.purpose, physiological_band=requirement.physiological_band,
                        work_minutes=requirement.desired_work_minutes, duration_minutes=duration,
                        context=materialization_context,
                    )
                    if preflight.feasible:
                        possibilities.append((candidate_key(current_day, duration), current_day, duration, preflight.capability_ids))
            if not possibilities:
                blocked_by_user_fixed = any(
                    abs((current_day - fixed_day).days) * 24 < max(
                        requirement.minimum_spacing_hours,
                        fixed_requirement.minimum_spacing_hours,
                    )
                    for fixed_day, fixed_requirement in user_fixed_quality_dates
                    for current_day in eligible_days(segment, PhysicalCategory.ROWING_SPECIFIC, 1)
                )
                code = "hard_quality_requirement_unplaceable" if requirement.priority is QualityRequirementPriority.HARD else "quality_requirement_deferred"
                if requirement.priority is QualityRequirementPriority.HARD:
                    infeasibilities.append(TrainingDayInfeasibility(code, segment_id, PhysicalCategory.ROWING_SPECIFIC, 0, requirement.exposure_id))
                else:
                    diagnostics.append(TrainingDayDiagnostic(
                        "user_fixed_quality_spacing_conflict" if blocked_by_user_fixed else code,
                        None, requirement.requirement_id,
                        requirement.exposure_id,
                    ))
                continue
            partition_safe = [
                item for item in possibilities
                if residual_partition_feasible_after(item[1], item[2])
            ]
            if requirement.priority is QualityRequirementPriority.PREFERRED and not partition_safe:
                diagnostics.append(TrainingDayDiagnostic(
                    "quality_requirement_deferred", None, requirement.requirement_id,
                    "no_exact_r3_partition_after_quality_envelope",
                ))
                continue
            selected = partition_safe or possibilities
            # A duration that consumes the otherwise tiny remainder is better
            # than a shorter envelope that would make R3 impossible to honour.
            _key, selected_day, duration, capability_ids = min(
                selected,
                key=lambda item: (item[0], -item[2]),
            )
            append_generated(
                selected_day, lineage=requirement.exposure_id, segment_id=segment_id,
                category=PhysicalCategory.ROWING_SPECIFIC, minutes=duration,
                purpose=requirement.purpose, quality=requirement,
                capability_provenance=tuple(f"r5:{item}" for item in capability_ids),
            )
            satisfied.add(requirement.exposure_id)
            quality_dates.append((selected_day, requirement))

        placed = sum(
            component.duration_minutes for current_day in _dates(segment.start_date, segment.end_date)
            for component in daily_components[current_day]
            if component.allocation_segment_id == segment_id and component.physical_category is PhysicalCategory.ROWING_SPECIFIC
        )
        residual = segment.rowing_specific_minutes - placed
        if residual < 0:
            infeasibilities.append(TrainingDayInfeasibility("rowing_partition_infeasible", segment_id, PhysicalCategory.ROWING_SPECIFIC, -residual))
            return

        low_purpose = (TrainingComponentPurpose.RECOVERY if plan.recovery_emphasis else
                       TrainingComponentPurpose.TECHNICAL if plan.technical_emphasis else
                       TrainingComponentPurpose.EASY_AEROBIC)
        low_candidates = [
            day for day in eligible_days(segment, PhysicalCategory.ROWING_SPECIFIC, 1)
            if not any(item.physical_category is PhysicalCategory.ROWING_SPECIFIC for item in daily_components[day])
        ]

        # Bounded deterministic DP. Each date contributes zero or one valid
        # residual component; all durations are verified through R5 first.
        states: dict[tuple[int, int], tuple[tuple[date, int], ...]] = {(0, 0): ()}
        for current_day in low_candidates:
            options = [duration for duration in range(1, remaining(current_day) + 1)
                       if low_duration_is_feasible(low_purpose, duration)]
            next_states = dict(states)
            for (total, count), placements in states.items():
                for duration in options:
                    updated = total + duration
                    if updated > residual:
                        continue
                    proposed = placements + ((current_day, duration),)
                    key = (updated, count + 1)
                    existing = next_states.get(key)
                    if existing is None or (max(value for _day, value in proposed), proposed) < (max(value for _day, value in existing), existing):
                        next_states[key] = proposed
            states = next_states
        partitions = [(count, placements) for (total, count), placements in states.items() if total == residual]
        target_exposures = current_training_state.rowing_exposures if current_training_state and current_training_state.rowing_exposures is not None else None
        current_rowing = sum(
            1 for current_day in _dates(segment.start_date, segment.end_date)
            for component in daily_components[current_day]
            if component.allocation_segment_id == segment_id and component.physical_category is PhysicalCategory.ROWING_SPECIFIC
        )
        partition = (min(
            partitions,
            key=lambda item: (
                abs(current_rowing + item[0] - target_exposures) if target_exposures is not None else max((value for _day, value in item[1]), default=0),
                max((value for _day, value in item[1]), default=0), item[0], item[1],
            ),
        )[1] if partitions else None)
        if partition is None:
            code = "residual_below_component_minimum" if 0 < residual < 25 else "rowing_partition_infeasible"
            infeasibilities.append(TrainingDayInfeasibility(code, segment_id, PhysicalCategory.ROWING_SPECIFIC, residual))
            return
        if current_training_state is None or current_training_state.rowing_exposures is None:
            diagnostics.append(TrainingDayDiagnostic("frequency_target_low_confidence", None, segment_id))
        for ordinal, (current_day, duration) in enumerate(partition, 1):
            append_generated(current_day, lineage=f"{segment_id}:low_intensity:{ordinal}", segment_id=segment_id,
                             category=PhysicalCategory.ROWING_SPECIFIC, minutes=duration, purpose=low_purpose)

    for segment in allocation.segments:
        segment_id = allocation_segment_id(segment)
        expected = {
            PhysicalCategory.ROWING_SPECIFIC: segment.rowing_specific_minutes,
            PhysicalCategory.STRENGTH: segment.strength_minutes,
            PhysicalCategory.GENERAL_AEROBIC: segment.general_aerobic_minutes,
            PhysicalCategory.OTHER: segment.other_physical_minutes,
        }
        for category, expected_minutes in expected.items():
            committed = authority_category_minutes.get((segment_id, category), 0)
            if committed > expected_minutes:
                infeasibilities.append(TrainingDayInfeasibility(
                    "authoritative_component_exceeds_r3_allocation", segment_id, category,
                    committed - expected_minutes,
                ))
        expected_strength = segment.recommended_strength_exposures
        committed_strength = authority_strength_counts.get(segment_id, 0)
        if committed_strength > expected_strength:
            infeasibilities.append(TrainingDayInfeasibility(
                "authoritative_strength_exposures_exceed_r3_allocation", segment_id,
                PhysicalCategory.STRENGTH, committed_strength - expected_strength,
            ))
        if any(
            component.physical_category is PhysicalCategory.STRENGTH
            and component.duration_minutes != segment.expected_strength_duration_minutes
            for day in expected_dates for component in daily_components[day]
            if component.allocation_segment_id == segment_id
        ):
            infeasibilities.append(TrainingDayInfeasibility(
                "strength_component_duration_mismatch", segment_id,
                PhysicalCategory.STRENGTH, 0,
            ))
        for exposure in range(committed_strength + 1, expected_strength + 1):
            days = eligible_days(segment, PhysicalCategory.STRENGTH, segment.expected_strength_duration_minutes)
            if not days:
                infeasibilities.append(TrainingDayInfeasibility(
                    "generated_component_capacity_infeasible", segment_id,
                    PhysicalCategory.STRENGTH, segment.expected_strength_duration_minutes,
                ))
                break
            # Generic R4 rowing retains its legacy direct-placement contract.
            # Once an R6 composition plan is present, however, ordinary
            # residual rowing must meet R5's exact catalog envelope, so a
            # strength candidate has to preserve that physical partition.
            rowing_safe_days = (
                [
                    current_day for current_day in days
                    if strength_day_preserves_rowing_partition(
                        segment, segment_id, current_day, expected_strength - exposure + 1,
                    )
                ]
                if segment_id in quality_plan_by_segment else days
            )
            if not rowing_safe_days:
                remaining_exposures = expected_strength - exposure + 1
                detail = joint_strength_rowing_detail(segment, segment_id, remaining_exposures)
                infeasibilities.append(TrainingDayInfeasibility(
                    "joint_strength_rowing_partition_infeasible", segment_id,
                    PhysicalCategory.STRENGTH,
                    remaining_exposures * segment.expected_strength_duration_minutes,
                    detail,
                ))
                diagnostics.append(TrainingDayDiagnostic(
                    "strength_candidate_would_strand_rowing_partition", None, segment_id, detail,
                ))
                break
            append_generated(rowing_safe_days[0], lineage=f"{segment_id}:strength:{exposure}", segment_id=segment_id,
                             category=PhysicalCategory.STRENGTH,
                             minutes=segment.expected_strength_duration_minutes,
                             purpose=TrainingComponentPurpose.STRENGTH)

        for category in (PhysicalCategory.ROWING_SPECIFIC, PhysicalCategory.GENERAL_AEROBIC, PhysicalCategory.OTHER):
            if category is PhysicalCategory.ROWING_SPECIFIC and segment_id in quality_plan_by_segment:
                quality_plan = quality_plan_by_segment[segment_id]
                if quality_plan.rowing_specific_minutes != segment.rowing_specific_minutes:
                    # R6 refines the composition of R3 rowing minutes; it
                    # cannot replace R3 as the physical-dose authority.  A
                    # stale/empty composition budget is therefore observable,
                    # but must not silently delete the owning R3 allocation.
                    diagnostics.append(TrainingDayDiagnostic(
                        "quality_plan_r3_rowing_mismatch", None, segment_id,
                        f"r6={quality_plan.rowing_specific_minutes};r3={segment.rowing_specific_minutes}",
                    ))
                place_quality_aware_rowing(segment, segment_id, quality_plan)
                continue
            required = max(0, expected[category] - authority_category_minutes.get((segment_id, category), 0))
            general_capability = None
            if category is PhysicalCategory.GENERAL_AEROBIC and required:
                general_capability = _resolve_general_aerobic_modality(segment.general_aerobic_capabilities)
                if general_capability is None:
                    infeasibilities.append(TrainingDayInfeasibility(
                        "general_aerobic_modality_unresolved", segment_id, category, required,
                        "r3_authorized_general_aerobic_requires_enabled_non_avoided_profile_modality",
                    ))
                    diagnostics.append(TrainingDayDiagnostic(
                        "general_aerobic_modality_unresolved", None, segment_id,
                        "no_enabled_non_avoided_profile_cross_training_modality",
                    ))
                    continue
            if category is PhysicalCategory.OTHER and required:
                split_allowed = any(code in {"allow_split", "splittable"} for code in segment.other_provenance)
                one_day = eligible_days(segment, category, required)
                if not one_day and not split_allowed:
                    infeasibilities.append(TrainingDayInfeasibility(
                        "other_component_split_not_authorized", segment_id, category, required,
                    ))
                    continue
            index = 1
            while required:
                candidates = eligible_days(segment, category, 1)
                if not candidates:
                    infeasibilities.append(TrainingDayInfeasibility(
                        "generated_component_capacity_infeasible", segment_id, category, required,
                    ))
                    break
                chosen = candidates[0]
                minutes = min(required, remaining(chosen))
                append_generated(chosen, lineage=f"{segment_id}:{category.value}:{index}", segment_id=segment_id,
                                 category=category, minutes=minutes, purpose=_default_purpose(category),
                                 modality=general_capability.modality if general_capability else None,
                                 capability_provenance=(
                                     ("r4:general_aerobic_modality_resolution", *general_capability.provenance)
                                     if general_capability else ()
                                 ))
                if general_capability:
                    diagnostics.append(TrainingDayDiagnostic(
                        "general_aerobic_modality_selected", chosen, segment_id,
                        f"modality={general_capability.modality.value};preference={general_capability.preference};"
                        f"familiarity={general_capability.familiarity or 'unspecified'}",
                    ))
                required -= minutes
                index += 1

    # Exact R3 reconciliation is required even if the result is otherwise
    # infeasible; this prevents a partial calendar from looking successful.
    for segment in allocation.segments:
        segment_id = allocation_segment_id(segment)
        all_components = [
            component for day in expected_dates for component in daily_components[day]
            if component.allocation_segment_id == segment_id
        ]
        expected = {
            PhysicalCategory.ROWING_SPECIFIC: segment.rowing_specific_minutes,
            PhysicalCategory.STRENGTH: segment.strength_minutes,
            PhysicalCategory.GENERAL_AEROBIC: segment.general_aerobic_minutes,
            PhysicalCategory.OTHER: segment.other_physical_minutes,
        }
        for category, minutes in expected.items():
            actual = sum(item.duration_minutes for item in all_components if item.physical_category is category)
            if actual != minutes:
                infeasibilities.append(TrainingDayInfeasibility(
                    "r3_category_conservation_mismatch", segment_id, category, abs(minutes - actual),
                ))
        strength_components = [item for item in all_components if item.physical_category is PhysicalCategory.STRENGTH]
        if len(strength_components) != segment.recommended_strength_exposures:
            infeasibilities.append(TrainingDayInfeasibility(
                "strength_exposure_conservation_mismatch", segment_id,
                PhysicalCategory.STRENGTH,
                abs(segment.recommended_strength_exposures - len(strength_components)),
            ))
        total = sum(item.duration_minutes for item in all_components)
        if total != segment.recommended_physical_minutes:
            infeasibilities.append(TrainingDayInfeasibility(
                "r3_total_physical_conservation_mismatch", segment_id, None,
                abs(segment.recommended_physical_minutes - total),
            ))

    days=[]
    for day in expected_dates:
        fact=capacity_by_date[day]
        components=tuple(daily_components[day])
        if fact.fixed_rest:
            state=TrainingDayState.DESIGNATED_REST
        elif not fact.available:
            state=TrainingDayState.UNAVAILABLE
        elif components:
            state=TrainingDayState.PLANNED_TRAINING
        else:
            state=TrainingDayState.UNUSED_AVAILABLE
        codes=tuple((*fact.restriction_codes, *(restrictions.get(day, ()))))
        if day in blocked_generated_dates:
            codes=tuple((*codes, "authoritative_capacity_conflict"))
        days.append(TrainingDay(day, fact.usable_minutes, components, state,
                                tuple(authority_ids[day]), codes))
    return TrainingDayPlanResult(
        tuple(days), tuple(diagnostics), tuple(infeasibilities), tuple(race_context),
        preferences.to_dict(),
    )


def component_calendar_planversion_fragment(result: TrainingDayPlanResult) -> dict[str, object]:
    """Serialize the additive R4 PlanVersion foundation without a fake session.

    Callers may attach this fragment to a future PlanVersion as ``training_days``
    while the existing ``sessions`` list remains the legacy display/logging
    projection.  R4B intentionally does not attach it to production plans yet.
    """
    def component_payload(component: TrainingComponent) -> dict[str, object]:
        return {
            "component_id": component.component_id,
            "source_lineage_id": component.source_lineage_id,
            "allocation_segment_id": component.allocation_segment_id,
            "physical_category": component.physical_category.value,
            "duration_minutes": component.duration_minutes,
            "purpose": component.purpose.value,
            "sequence": component.sequence,
            "modality": component.modality.value if component.modality else None,
            "permitted_modalities": [item.value for item in component.permitted_modalities],
            "authority": component.authority.value,
            "primary": component.primary,
            "materialization_requirements": list(component.materialization_requirements),
            "rationale_codes": list(component.rationale_codes),
            "constraint_codes": list(component.constraint_codes),
            "quality_requirement_id": component.quality_requirement_id,
            "quality_exposure_id": component.quality_exposure_id,
            "physiological_band": component.physiological_band,
            "quality_work_minutes": component.quality_work_minutes,
            "minimum_quality_work_minutes": component.minimum_quality_work_minutes,
            "maximum_quality_work_minutes": component.maximum_quality_work_minutes,
            "capability_provenance": list(component.capability_provenance),
        }

    return {
        "component_calendar_version": result.schema_version,
        "training_days": [
            {
                "date": day.date.isoformat(),
                "available_minutes": day.available_minutes,
                "total_physical_minutes": day.total_physical_minutes,
                "remaining_capacity_minutes": day.remaining_capacity_minutes,
                "combined_day": day.combined_day,
                "state": day.state.value,
                "authority_ids": list(day.authority_ids),
                "compatibility_diagnostics": list(day.compatibility_diagnostics),
                "components": [component_payload(component) for component in day.components],
            }
            for day in result.days
        ],
    }
