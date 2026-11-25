"""Exact-duration materialization for the replacement V3 component path.

This module deliberately has no live planner, UI, or V2 materializer caller.
R4 owns physical component duration; R5 expresses each component faithfully or
returns a deterministic infeasibility.  In particular, legacy target-credit
and reservation-minute adaptations are not inputs to this path.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from .planning_domain import (
    ComponentAuthority,
    MaterializationFailure,
    MaterializedTrainingComponent,
    MaterializedTrainingComponentResult,
    PhysicalCategory,
    PhysiologicalWorkSummary,
    TrainingAllocation,
    TrainingComponent,
    TrainingComponentPurpose,
    TrainingDayPlanResult,
    TrainingModality,
)
from .session_archetypes import ARCHETYPE_LIBRARY_VERSION, build_archetype_library
from .session_selection import FAMILY, ROLE_BAND, _score, candidates


MATERIALIZER_POLICY_VERSION = "component-materialization-v1"
_ROWING_PURPOSE_TO_ROLE = {
    TrainingComponentPurpose.EASY_AEROBIC: "AEROBIC_BASE",
    TrainingComponentPurpose.TECHNICAL: "TECHNIQUE_EASY",
    TrainingComponentPurpose.RECOVERY: "RECOVERY",
    TrainingComponentPurpose.AEROBIC_STRENGTH: "AEROBIC_STRENGTH",
    TrainingComponentPurpose.THRESHOLD: "THRESHOLD",
    TrainingComponentPurpose.RACE_SPECIFIC: "RACE_PACE",
    TrainingComponentPurpose.ANAEROBIC: "ANAEROBIC_CAPACITY",
    TrainingComponentPurpose.POWER: "SPRINT_POWER",
}
_QUALITY_PURPOSES = {
    TrainingComponentPurpose.AEROBIC_STRENGTH,
    TrainingComponentPurpose.THRESHOLD,
    TrainingComponentPurpose.RACE_SPECIFIC,
    TrainingComponentPurpose.ANAEROBIC,
    TrainingComponentPurpose.POWER,
}
_ROWING_MODALITIES = (TrainingModality.ERG, TrainingModality.ON_WATER)


@dataclass(frozen=True)
class MaterializationContext:
    """Inputs that can legitimately affect a concrete selection."""

    phase_id: str = "general_preparation"
    race_type: str = "general"
    experience: str = "intermediate"
    available_rowing_modalities: tuple[TrainingModality, ...] = _ROWING_MODALITIES
    selector_history: tuple[Mapping[str, object], ...] = ()
    catalog_version: str = ARCHETYPE_LIBRARY_VERSION
    # The reset diagnostic may carry several R9B cadence segments from
    # different validated season phases.  R4/R5 still receive one context
    # object, so this immutable lookup keeps every component bound to its own
    # R3 lineage rather than silently applying the first block's phase.
    segment_phase_contexts: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        allowed = tuple(item for item in self.available_rowing_modalities if item in _ROWING_MODALITIES)
        object.__setattr__(self, "available_rowing_modalities", allowed or _ROWING_MODALITIES)
        object.__setattr__(self, "selector_history", tuple(self.selector_history))
        contexts = tuple(sorted(self.segment_phase_contexts, key=lambda item: item[0]))
        if any(len(item) != 3 or not all(isinstance(value, str) and value for value in item) for item in contexts):
            raise ValueError("materialization_segment_phase_context_is_invalid")
        if len({item[0] for item in contexts}) != len(contexts):
            raise ValueError("materialization_segment_phase_contexts_must_be_unique")
        object.__setattr__(self, "segment_phase_contexts", contexts)

    def for_allocation_segment(self, allocation_segment_id: str) -> "MaterializationContext":
        """Return the phase/race context owned by one exact R3 segment.

        Callers without a dated mapping keep their existing single-context
        behavior.  The lookup intentionally clears itself on the returned
        context so it cannot affect fingerprinting or selection recursively.
        """
        matched = next((item for item in self.segment_phase_contexts if item[0] == allocation_segment_id), None)
        if matched is None:
            return self
        _segment_id, phase_id, race_type = matched
        return replace(self, phase_id=phase_id, race_type=race_type, segment_phase_contexts=())


@dataclass(frozen=True)
class CatalogCapability:
    archetype_id: str
    minimum_duration_minutes: int
    maximum_duration_minutes: int
    parameterizable: bool
    modality: TrainingModality
    purpose: TrainingComponentPurpose
    catalog_version: str


@dataclass(frozen=True)
class RowingCatalogCapability:
    """Catalog envelope available to R6 without constructing an R4 component."""

    selector_role: str
    archetype_id: str
    minimum_duration_minutes: int
    maximum_duration_minutes: int
    modality: TrainingModality
    catalog_version: str

    def __post_init__(self) -> None:
        if (
            not self.selector_role or not self.archetype_id
            or self.minimum_duration_minutes <= 0
            or self.maximum_duration_minutes < self.minimum_duration_minutes
        ):
            raise ValueError("rowing_catalog_capability_values_are_invalid")


@dataclass(frozen=True)
class QualityDurationPreflight:
    """Pure R5 feasibility evidence; it deliberately does not choose a workout."""

    purpose: TrainingComponentPurpose
    physiological_band: str
    work_minutes: int
    duration_minutes: int
    feasible_modalities: tuple[TrainingModality, ...] = ()
    capability_ids: tuple[str, ...] = ()

    @property
    def feasible(self) -> bool:
        return bool(self.feasible_modalities)


@dataclass(frozen=True)
class MaterializationReconciliationResult:
    success: bool
    violation_codes: tuple[str, ...] = ()
    day_minutes: tuple[tuple[str, int], ...] = ()
    segment_category_minutes: tuple[tuple[str, str, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "violation_codes", tuple(sorted(set(self.violation_codes))))
        object.__setattr__(self, "day_minutes", tuple(sorted(self.day_minutes)))
        object.__setattr__(self, "segment_category_minutes", tuple(sorted(self.segment_category_minutes)))


@dataclass(frozen=True)
class MaterializedTrainingDayPlanResult:
    results: tuple[MaterializedTrainingComponentResult, ...]
    reconciliation: MaterializationReconciliationResult

    @property
    def success(self) -> bool:
        return self.reconciliation.success


def _mode_name(modality: TrainingModality) -> str:
    return "on_water" if modality is TrainingModality.ON_WATER else "erg"


def _requested_rowing_modalities(component: TrainingComponent, context: MaterializationContext) -> tuple[TrainingModality, ...]:
    if component.modality is not None:
        return (component.modality,) if component.modality in _ROWING_MODALITIES else ()
    permitted = component.permitted_modalities or _ROWING_MODALITIES
    return tuple(item for item in permitted if item in context.available_rowing_modalities and item in _ROWING_MODALITIES)


def _selector_role(component: TrainingComponent) -> str | None:
    return _ROWING_PURPOSE_TO_ROLE.get(component.purpose)


def _envelopes(pool: Iterable[Mapping[str, object]]) -> tuple[tuple[str, int, int], ...]:
    return tuple(sorted(
        (str(item["archetype_id"]), int(item["duration_range_min"]["min"]), int(item["duration_range_min"]["max"]))
        for item in pool
    ))


def catalog_capabilities_v3(component: TrainingComponent, *, context: MaterializationContext) -> tuple[CatalogCapability, ...]:
    """Return versioned, hard min/max capabilities without selecting a workout."""
    role = _selector_role(component)
    if component.physical_category is not PhysicalCategory.ROWING_SPECIFIC or role is None:
        return ()
    result: list[CatalogCapability] = []
    for modality in _requested_rowing_modalities(component, context):
        # A large request obtains the contextual catalog pool; hard maxima are
        # applied below rather than relying on the legacy lower-bound helper.
        pool = candidates(
            role=role, band=ROLE_BAND[role], experience=context.experience,
            phase=context.phase_id, race_type=context.race_type,
            mode=_mode_name(modality), minutes=999,
        )
        result.extend(
            CatalogCapability(
                str(item["archetype_id"]), int(item["duration_range_min"]["min"]),
                int(item["duration_range_min"]["max"]), True, modality,
                component.purpose, context.catalog_version,
            )
            for item in pool
        )
    return tuple(sorted(result, key=lambda item: (item.modality.value, item.archetype_id)))


def quality_catalog_capabilities_v3(
    selector_role: str, *, context: MaterializationContext,
) -> tuple[RowingCatalogCapability, ...]:
    """Expose compatible catalog duration envelopes for R6 requirements.

    This is intentionally a capability query, not selection or workout
    construction.  It preserves the R4/R5 boundary by reporting only the
    catalog's concrete physical-duration range.
    """
    band = ROLE_BAND.get(selector_role)
    if band is None:
        return ()
    result: list[RowingCatalogCapability] = []
    for modality in context.available_rowing_modalities:
        pool = candidates(
            role=selector_role, band=band, experience=context.experience,
            phase=context.phase_id, race_type=context.race_type,
            mode=_mode_name(modality), minutes=999,
        )
        result.extend(
            RowingCatalogCapability(
                selector_role, str(item["archetype_id"]),
                int(item["duration_range_min"]["min"]),
                int(item["duration_range_min"]["max"]), modality,
                context.catalog_version,
            )
            for item in pool
        )
    return tuple(sorted(set(result), key=lambda item: (item.modality.value, item.archetype_id)))


def preflight_quality_duration_v3(
    purpose: TrainingComponentPurpose,
    *, physiological_band: str, work_minutes: int, duration_minutes: int,
    context: MaterializationContext = MaterializationContext(),
    permitted_modalities: tuple[TrainingModality, ...] = (),
) -> QualityDurationPreflight:
    """Test exact R5 compatibility without ranking or selecting an archetype."""
    role = _ROWING_PURPOSE_TO_ROLE.get(purpose)
    if role is None or work_minutes < 0 or duration_minutes <= 0 or ROLE_BAND.get(role) != physiological_band:
        return QualityDurationPreflight(purpose, physiological_band, work_minutes, duration_minutes)
    requested = permitted_modalities or context.available_rowing_modalities
    modalities: list[TrainingModality] = []
    identifiers: list[str] = []
    for modality in requested:
        if modality not in context.available_rowing_modalities or modality not in _ROWING_MODALITIES:
            continue
        pool = candidates(
            role=role, band=physiological_band, experience=context.experience,
            phase=context.phase_id, race_type=context.race_type,
            mode=_mode_name(modality), minutes=duration_minutes,
        )
        viable = []
        for item in pool:
            envelope = item["duration_range_min"]
            if not int(envelope["min"]) <= duration_minutes <= int(envelope["max"]):
                continue
            # A throwaway component only invokes the existing exact-variant
            # feasibility math; no identity or catalog choice escapes R5.
            probe = TrainingComponent(
                "preflight", "preflight", "preflight", PhysicalCategory.ROWING_SPECIFIC,
                duration_minutes, purpose, modality=modality,
            )
            if _exact_variants(item, component=probe, role=role, desired_work_minutes=work_minutes):
                viable.append(str(item["archetype_id"]))
        if viable:
            modalities.append(modality)
            identifiers.extend(viable)
    return QualityDurationPreflight(
        purpose, physiological_band, work_minutes, duration_minutes,
        tuple(sorted(set(modalities), key=lambda item: item.value)), tuple(sorted(set(identifiers))),
    )


def preflight_rowing_duration_v3(
    purpose: TrainingComponentPurpose, *, duration_minutes: int,
    context: MaterializationContext = MaterializationContext(),
    permitted_modalities: tuple[TrainingModality, ...] = (),
) -> tuple[TrainingModality, ...]:
    """Pure exact-duration feasibility for non-quality residual rowing."""
    role = _ROWING_PURPOSE_TO_ROLE.get(purpose)
    if role is None or duration_minutes <= 0:
        return ()
    requested = permitted_modalities or context.available_rowing_modalities
    result: list[TrainingModality] = []
    for modality in requested:
        if modality not in context.available_rowing_modalities or modality not in _ROWING_MODALITIES:
            continue
        pool = candidates(role=role, band=ROLE_BAND[role], experience=context.experience,
                          phase=context.phase_id, race_type=context.race_type,
                          mode=_mode_name(modality), minutes=duration_minutes)
        if any(
            int(item["duration_range_min"]["min"]) <= duration_minutes <= int(item["duration_range_min"]["max"])
            and _exact_variants(
                item,
                component=TrainingComponent("preflight", "preflight", "preflight", PhysicalCategory.ROWING_SPECIFIC, duration_minutes, purpose, modality=modality),
                role=role, desired_work_minutes=None,
            )
            for item in pool
        ):
            result.append(modality)
    return tuple(sorted(set(result), key=lambda item: item.value))


def _failure(component: TrainingComponent, reason: str, *, context: MaterializationContext,
             capabilities: Iterable[CatalogCapability] = (), detail: Mapping[str, object] | None = None) -> MaterializedTrainingComponentResult:
    modalities = ((component.modality,) if component.modality else component.permitted_modalities)
    return MaterializedTrainingComponentResult(
        component.component_id,
        failure=MaterializationFailure(
            component.component_id, reason, component.duration_minutes, component.purpose,
            tuple(modalities), context.catalog_version,
            tuple((item.archetype_id, item.minimum_duration_minutes, item.maximum_duration_minutes) for item in capabilities),
            dict(detail or {}),
        ),
    )


def _exact_variants(item: Mapping[str, object], *, component: TrainingComponent,
                    role: str, desired_work_minutes: int | None) -> tuple[dict[str, int], ...]:
    """Enumerate purpose-preserving, exact integer-minute parameterizations."""
    duration = component.duration_minutes
    work = item["work_interval_range_min"]
    repetitions = item["repetition_range"]
    recovery = item["recovery_range_min"]
    quality = component.purpose in _QUALITY_PURPOSES
    result: list[dict[str, int]] = []
    for count in range(int(repetitions["min"]), int(repetitions["max"]) + 1):
        for piece in range(int(work["min"]), int(work["max"]) + 1):
            prescribed = count * piece
            if desired_work_minutes is not None and prescribed != desired_work_minutes:
                continue
            for recovery_each in range(int(recovery["min"]), int(recovery["max"]) + 1):
                recovery_minutes = max(0, count - 1) * recovery_each
                remainder = duration - prescribed - recovery_minutes
                if remainder < 0:
                    continue
                # Quality may use only a bounded warm-up/cooldown envelope.
                # The bound is an explicit materializer heuristic, not a dose
                # credit; catalog minimums remain usable where they fit it.
                if quality and remainder > 24:
                    continue
                warmup = min(12, remainder)
                cooldown = (remainder - warmup) if quality else 0
                easy_extension = 0 if quality else max(0, remainder - warmup)
                result.append({
                    "count": count, "piece": piece, "recovery_each": recovery_each,
                    "prescribed": prescribed, "recovery": recovery_minutes,
                    "warmup": warmup, "cooldown": cooldown,
                    "easy_extension": easy_extension,
                })
    return tuple(result)


def _variant_key(variant: Mapping[str, int], *, quality: bool) -> tuple[int, int, int, int, int]:
    # Exact parameterizations without an extension are preferred.  Within that
    # set, quality favours more intended work; easy work favours less padding.
    return (
        -int(bool(variant["easy_extension"])),
        variant["prescribed"] if quality else -variant["easy_extension"],
        -variant["cooldown"], variant["count"], variant["piece"],
    )


def _materialized(component: TrainingComponent, *, context: MaterializationContext,
                  modality: TrainingModality, item: Mapping[str, object] | None,
                  variant: Mapping[str, int], rationale: tuple[str, ...],
                  physiological_band: str | None = None,
                  physiological_work_minutes: int | None = None,
                  adaptation: Mapping[str, object] | None = None) -> MaterializedTrainingComponentResult:
    prescribed = int(variant["prescribed"])
    physiological = (PhysiologicalWorkSummary(physiological_band, physiological_work_minutes if physiological_work_minutes is not None else prescribed, ("materialized_work",))
                     if physiological_band else None)
    structure = ({
        "repetitions": int(variant["count"]),
        "work_interval_minutes": int(variant["piece"]),
        "recovery_each_minutes": int(variant["recovery_each"]),
    } if item is not None else {})
    fingerprint = {
        "component_id": component.component_id,
        "archetype_id": item.get("archetype_id") if item else None,
        "modality": modality.value,
        "purpose": component.purpose.value,
        "physical_duration_minutes": component.duration_minutes,
        "interval_structure": structure,
    }
    materialized = MaterializedTrainingComponent(
        component.component_id, component.source_lineage_id,
        str(item["archetype_id"]) if item else None,
        context.catalog_version if item else None, MATERIALIZER_POLICY_VERSION,
        modality, component.purpose, component.duration_minutes, prescribed,
        int(variant["warmup"]), int(variant["recovery"]), int(variant["cooldown"]),
        int(variant["easy_extension"]), structure, physiological, rationale,
        component.constraint_codes,
        dict(adaptation or {}), fingerprint,
    )
    return MaterializedTrainingComponentResult(component.component_id, materialized=materialized)


def _generic_authority(component: TrainingComponent, *, context: MaterializationContext) -> MaterializedTrainingComponentResult:
    modality = component.modality or TrainingModality.ON_WATER
    return _materialized(
        component, context=context, modality=modality, item=None,
        variant={"count": 1, "piece": component.duration_minutes, "recovery_each": 0,
                 "prescribed": component.duration_minutes, "recovery": 0,
                 "warmup": 0, "cooldown": 0, "easy_extension": 0},
        rationale=("authoritative_content_preserved", "catalog_not_applied"),
        physiological_band=component.physiological_band,
        physiological_work_minutes=component.quality_work_minutes,
        adaptation={"authority": "generic_known_duration"},
    )


def materialize_training_component_v3(
    component: TrainingComponent,
    *,
    context: MaterializationContext = MaterializationContext(),
    quality_work_minutes: int | None = None,
) -> MaterializedTrainingComponentResult:
    """Materialize one R4 component exactly, or return a typed failure.

    ``quality_work_minutes`` is a narrow R8 handoff seam.  It has no physical
    accounting role and is rejected if the selected quality purpose cannot
    represent it inside the component's immutable total duration.
    """
    if quality_work_minutes is not None and quality_work_minutes < 0:
        return _failure(component, "purpose_duration_incompatible", context=context,
                        detail={"quality_work_minutes": quality_work_minutes})
    if component.physical_category is PhysicalCategory.STRENGTH:
        return _materialized(
            component, context=context, modality=TrainingModality.STRENGTH, item=None,
            variant={"count": 1, "piece": component.duration_minutes, "recovery_each": 0,
                     "prescribed": component.duration_minutes, "recovery": 0,
                     "warmup": 0, "cooldown": 0, "easy_extension": 0},
            rationale=("strength_program_pending",),
            adaptation={"strength_program": "pending"},
        )
    if component.physical_category is PhysicalCategory.GENERAL_AEROBIC:
        if component.modality is None:
            return _failure(component, "modality_unresolved", context=context)
        return _materialized(
            component, context=context, modality=component.modality, item=None,
            variant={"count": 1, "piece": component.duration_minutes, "recovery_each": 0,
                     "prescribed": component.duration_minutes, "recovery": 0,
                     "warmup": 0, "cooldown": 0, "easy_extension": 0},
            rationale=("resolved_general_aerobic_modality",),
            adaptation={"modality_selection": "not_performed"},
        )
    if component.physical_category is not PhysicalCategory.ROWING_SPECIFIC:
        return _failure(component, "no_compatible_archetype", context=context)
    if component.authority is ComponentAuthority.AUTHORITATIVE and (
        component.quality_requirement_id is not None or component.purpose in {
        TrainingComponentPurpose.COACHED, TrainingComponentPurpose.PRIVATE_COACHING,
    }):
        return _generic_authority(component, context=context)

    role = _selector_role(component)
    if role is None:
        return _failure(component, "no_compatible_archetype", context=context)
    capabilities = catalog_capabilities_v3(component, context=context)
    if not capabilities:
        return _failure(component, "no_compatible_archetype", context=context)
    minima = min(item.minimum_duration_minutes for item in capabilities)
    maxima = max(item.maximum_duration_minutes for item in capabilities)
    if component.duration_minutes < minima:
        return _failure(component, "duration_below_minimum", context=context,
                        capabilities=capabilities, detail={"minimum_duration_minutes": minima})
    if component.duration_minutes > maxima:
        return _failure(component, "duration_above_maximum", context=context,
                        capabilities=capabilities, detail={"maximum_duration_minutes": maxima})

    compatible: list[tuple[float, Mapping[str, object], TrainingModality, Mapping[str, int], Mapping[str, object]]] = []
    quality = component.purpose in _QUALITY_PURPOSES
    for modality in _requested_rowing_modalities(component, context):
        pool = candidates(
            role=role, band=ROLE_BAND[role], experience=context.experience,
            phase=context.phase_id, race_type=context.race_type,
            mode=_mode_name(modality), minutes=component.duration_minutes,
        )
        for item in pool:
            envelope = item["duration_range_min"]
            if not int(envelope["min"]) <= component.duration_minutes <= int(envelope["max"]):
                continue
            variants = _exact_variants(item, component=component, role=role,
                                       desired_work_minutes=quality_work_minutes if quality else None)
            if not variants:
                continue
            variant = max(variants, key=lambda value: _variant_key(value, quality=quality))
            score, score_components, _ = _score(
                item, role, context.phase_id, context.race_type, "varied",
                component.duration_minutes, list(context.selector_history), False, None,
            )
            compatible.append((score, item, modality, variant, score_components))
    if not compatible:
        reason = "purpose_duration_incompatible" if quality_work_minutes is not None else "catalog_parameterization_failed"
        return _failure(component, reason, context=context, capabilities=capabilities,
                        detail={"quality_work_minutes": quality_work_minutes})

    # Duration/purpose compatibility has already filtered the pool.  Existing
    # deterministic variety scoring now supplies the soft final rank only.
    score, item, modality, variant, score_components = max(
        compatible, key=lambda row: (row[0], str(row[1]["archetype_id"]), row[2].value)
    )
    adaptation = {
        "adaptation_kind": "easy_extension" if variant["easy_extension"] else "exact_parameterization",
        "easy_extension_minutes": variant["easy_extension"],
        "selector_score_components": score_components,
    }
    return _materialized(
        component, context=context, modality=modality, item=item, variant=variant,
        rationale=("exact_duration_materialization", "deterministic_catalog_selection"),
        physiological_band=ROLE_BAND[role], adaptation=adaptation,
        physiological_work_minutes=quality_work_minutes,
    )


def reconcile_materialized_training_days_v3(
    plan: TrainingDayPlanResult,
    materializations: Iterable[MaterializedTrainingComponentResult],
    *, allocation: TrainingAllocation | None = None,
) -> MaterializationReconciliationResult:
    """Strictly compare final physical materialization with R4/R3 facts."""
    expected = {component.component_id: (day, component) for day in plan.days for component in day.components}
    received = tuple(materializations)
    result_ids = [item.component_id for item in received]
    violations: list[str] = []
    if len(set(result_ids)) != len(result_ids):
        violations.append("duplicate_materialization_component")
    if set(result_ids) != set(expected):
        violations.append("materialization_component_set_mismatch")
    by_id = {item.component_id: item for item in received}
    day_minutes: dict[str, int] = {day.date.isoformat(): 0 for day in plan.days}
    segment_category: dict[tuple[str, str], int] = {}
    for component_id, (day, component) in expected.items():
        result = by_id.get(component_id)
        if result is None or not result.success:
            violations.append("materialization_failure_or_missing_component")
            continue
        materialized = result.materialized
        assert materialized is not None
        if materialized.physical_duration_minutes != component.duration_minutes:
            violations.append("materialized_duration_component_mismatch")
        if component.quality_requirement_id is not None:
            work = materialized.physiological_work
            if work is None or work.band != component.physiological_band or work.work_minutes != component.quality_work_minutes:
                violations.append("materialized_quality_requirement_mismatch")
            elif (
                component.minimum_quality_work_minutes is not None
                and component.maximum_quality_work_minutes is not None
                and not component.minimum_quality_work_minutes <= work.work_minutes <= component.maximum_quality_work_minutes
            ):
                violations.append("materialized_quality_work_outside_requirement_range")
        day_minutes[day.date.isoformat()] += materialized.physical_duration_minutes
        key = (component.allocation_segment_id, component.physical_category.value)
        segment_category[key] = segment_category.get(key, 0) + materialized.physical_duration_minutes
    for day in plan.days:
        if day_minutes[day.date.isoformat()] != day.total_physical_minutes:
            violations.append("materialized_day_duration_mismatch")
    if allocation is not None:
        for segment in allocation.segments:
            segment_id = f"r3:{segment.start_date.isoformat()}:{segment.end_date.isoformat()}"
            expected_categories = {
                PhysicalCategory.ROWING_SPECIFIC.value: segment.rowing_specific_minutes,
                PhysicalCategory.STRENGTH.value: segment.strength_minutes,
                PhysicalCategory.GENERAL_AEROBIC.value: segment.general_aerobic_minutes,
                PhysicalCategory.OTHER.value: segment.other_physical_minutes,
            }
            for category, minutes in expected_categories.items():
                if segment_category.get((segment_id, category), 0) != minutes:
                    violations.append("materialized_segment_category_conservation_mismatch")
            total = sum(segment_category.get((segment_id, category), 0) for category in expected_categories)
            if total != segment.recommended_physical_minutes:
                violations.append("materialized_segment_total_conservation_mismatch")
    return MaterializationReconciliationResult(
        not violations, tuple(violations), tuple(day_minutes.items()),
        tuple((segment_id, category, minutes) for (segment_id, category), minutes in segment_category.items()),
    )


def materialize_training_days_v3(
    plan: TrainingDayPlanResult,
    *,
    context: MaterializationContext = MaterializationContext(),
    allocation: TrainingAllocation | None = None,
) -> MaterializedTrainingDayPlanResult:
    results = tuple(
        materialize_training_component_v3(
            component,
            context=context.for_allocation_segment(component.allocation_segment_id),
            quality_work_minutes=component.quality_work_minutes,
        )
        for day in plan.days for component in day.components
    )
    return MaterializedTrainingDayPlanResult(results, reconcile_materialized_training_days_v3(plan, results, allocation=allocation))
