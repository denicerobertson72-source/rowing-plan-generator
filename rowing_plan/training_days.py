"""R4 component-aware physical calendar foundation.

This is deliberately a small, deterministic placement layer.  It consumes the
authoritative R3 allocation exactly; it neither selects catalog workouts nor
chooses a cross-training modality or rowing intensity composition.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from hashlib import sha256
from typing import Iterable, Mapping

from .planning_domain import (
    ComponentAuthority,
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

    def __post_init__(self) -> None:
        if not self.authority_id or self.duration_minutes <= 0:
            raise ValueError("authoritative_component_fact_values_are_invalid")
        object.__setattr__(self, "rationale_codes", tuple(sorted(set(self.rationale_codes))))
        object.__setattr__(self, "constraint_codes", tuple(sorted(set(self.constraint_codes))))


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
                    constraint_codes: tuple[str, ...] = ()) -> TrainingComponent:
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
    )


def generate_training_days_v3(
    allocation: TrainingAllocation, capacity: Iterable[DailyTrainingCapacity],
    authorities: Iterable[AuthoritativeComponentFact] = (),
    preferences: CombinedDayPreferences = CombinedDayPreferences(),
    restrictions: Mapping[date, tuple[str, ...]] | None = None,
    race_context: Iterable[RaceEvent] = (),
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
            constraint_codes=fact.constraint_codes,
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
        return sorted(result, key=lambda item: _candidate_sort_key(daily_components, item, category, preferences))

    def append_generated(day: date, *, lineage: str, segment_id: str, category: PhysicalCategory,
                         minutes: int, purpose: TrainingComponentPurpose) -> None:
        permitted = ((TrainingModality.ERG, TrainingModality.ON_WATER) if category is PhysicalCategory.ROWING_SPECIFIC
                     else (TrainingModality.BIKE, TrainingModality.ELLIPTICAL, TrainingModality.STAIR_CLIMBER, TrainingModality.RUNNING)
                     if category is PhysicalCategory.GENERAL_AEROBIC else ())
        daily_components[day].append(_make_component(
            lineage=lineage, segment_id=segment_id, category=category, minutes=minutes,
            purpose=purpose, authority=ComponentAuthority.GENERATED,
            sequence=len(daily_components[day]) + 1, permitted_modalities=permitted,
            rationale_codes=("r3_allocation_component",),
        ))

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
            append_generated(days[0], lineage=f"{segment_id}:strength:{exposure}", segment_id=segment_id,
                             category=PhysicalCategory.STRENGTH,
                             minutes=segment.expected_strength_duration_minutes,
                             purpose=TrainingComponentPurpose.STRENGTH)

        for category in (PhysicalCategory.ROWING_SPECIFIC, PhysicalCategory.GENERAL_AEROBIC, PhysicalCategory.OTHER):
            required = max(0, expected[category] - authority_category_minutes.get((segment_id, category), 0))
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
                                 category=category, minutes=minutes, purpose=_default_purpose(category))
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
