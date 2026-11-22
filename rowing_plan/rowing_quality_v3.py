"""R6 physiological rowing-quality composition over an exact R3 allocation.

This module deliberately does not allocate physical minutes, create training
components, place dates, or select a concrete workout.  R3 owns the physical
rowing budget, R4 owns component durations and placement, and R5 owns exact
materialization.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from typing import Iterable

from .materialization_v3 import MaterializationContext, quality_catalog_capabilities_v3
from .performance_profile import AthletePerformanceProfile, PerformanceLimiterProfile, TrainingFocus
from .planning_domain import (
    RowingQualityFamily,
    RowingQualityInfeasibility,
    RowingQualityPlan,
    RowingQualityPlanResult,
    RowingQualityRequirement,
    TrainingAllocationSegment,
    TrainingModality,
)
from .training_policy import POLICY_VERSION


ROWING_QUALITY_PLAN_VERSION = "rowing-quality-plan-v1"
_ORDINARY_LOW_INTENSITY_GUARD_FRACTION = 0.70
_TAPER_LOW_INTENSITY_GUARD_FRACTION = 0.50
_FAMILY_SPEC = {
    RowingQualityFamily.AEROBIC_STRENGTH: ("AEROBIC_STRENGTH", "UT1", 15, 20, 30, 24),
    RowingQualityFamily.THRESHOLD: ("THRESHOLD", "AT", 12, 20, 30, 72),
    RowingQualityFamily.RACE_SPECIFIC: ("RACE_PACE", "TR", 8, 15, 25, 72),
    RowingQualityFamily.ANAEROBIC: ("ANAEROBIC_CAPACITY", "AN", 6, 12, 20, 96),
    RowingQualityFamily.POWER: ("SPRINT_POWER", "PP", 5, 10, 15, 72),
}
_BAND_SUPPRESSION_HOURS = {"AT": 72, "TR": 72, "AN": 96, "PP": 72}


@dataclass(frozen=True)
class CompletedQualityExposure:
    completed_date: date
    physiological_band: str
    work_minutes: int
    completed: bool = True
    is_race: bool = False
    modality: TrainingModality | None = None

    def __post_init__(self) -> None:
        if not self.physiological_band or self.work_minutes < 0:
            raise ValueError("completed_quality_exposure_values_are_invalid")


@dataclass(frozen=True)
class CoachQualityOverride:
    family: RowingQualityFamily
    desired_work_minutes: int | None = None
    target_exposures: int = 1
    rationale: str = "coach_quality_override"
    force_repeat: bool = False

    def __post_init__(self) -> None:
        if self.desired_work_minutes is not None and self.desired_work_minutes < 0:
            raise ValueError("coach_quality_override_work_is_invalid")
        if self.target_exposures < 0:
            raise ValueError("coach_quality_override_exposures_are_invalid")


@dataclass(frozen=True)
class RowingQualityPlanningContext:
    planning_date: date
    phase_id: str = "general_preparation"
    race_type: str = "general"
    days_to_race: int | None = None
    race_count: int = 0
    recovery_active: bool = False
    hard_restriction_codes: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    experience: str = "intermediate"
    available_rowing_modalities: tuple[TrainingModality, ...] = (
        TrainingModality.ERG, TrainingModality.ON_WATER,
    )

    def __post_init__(self) -> None:
        if self.days_to_race is not None and self.days_to_race < 0 or self.race_count < 0:
            raise ValueError("rowing_quality_context_values_are_invalid")
        object.__setattr__(self, "hard_restriction_codes", tuple(sorted(set(self.hard_restriction_codes))))
        object.__setattr__(self, "goals", tuple(sorted(set(self.goals))))
        object.__setattr__(self, "available_rowing_modalities", tuple(sorted(set(self.available_rowing_modalities), key=lambda item: item.value)))


def _fingerprint(*, segment: TrainingAllocationSegment, allocation_segment_id: str,
                 performance: AthletePerformanceProfile, limiter: PerformanceLimiterProfile,
                 focus: TrainingFocus, context: RowingQualityPlanningContext,
                 completed: Iterable[CompletedQualityExposure], override: CoachQualityOverride | None) -> str:
    payload = {
        "segment": [allocation_segment_id, segment.start_date.isoformat(), segment.end_date.isoformat(), segment.rowing_specific_minutes],
        "performance": [performance.source_test_ids, performance.confidence, performance.evidence_completeness],
        "limiter": [limiter.mode, limiter.primary_focus, limiter.secondary_focus, limiter.confidence],
        "focus": [focus.primary, focus.secondary, focus.phase, focus.race_type, focus.days_to_race, focus.recovery_active],
        "context": [context.phase_id, context.race_type, context.days_to_race, context.race_count, context.recovery_active, context.hard_restriction_codes, context.goals, context.experience, tuple(item.value for item in context.available_rowing_modalities)],
        "completed": [(item.completed_date.isoformat(), item.physiological_band, item.work_minutes, item.completed, item.is_race) for item in sorted(completed, key=lambda item: (item.completed_date, item.physiological_band, item.work_minutes))],
        "override": None if override is None else [override.family.value, override.desired_work_minutes, override.target_exposures, override.rationale, override.force_repeat],
        "versions": [ROWING_QUALITY_PLAN_VERSION, POLICY_VERSION],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _candidate_family(limiter: PerformanceLimiterProfile, focus: TrainingFocus,
                      context: RowingQualityPlanningContext) -> RowingQualityFamily | None:
    if context.recovery_active or focus.recovery_active:
        return None
    if context.days_to_race is not None and context.days_to_race <= 7:
        return RowingQualityFamily.RACE_SPECIFIC
    if "taper" in context.phase_id:
        return RowingQualityFamily.RACE_SPECIFIC
    if context.phase_id in {"race_specific_preparation", "specific_preparation", "race_build"}:
        if context.race_type in {"one_k", "sprint_1k"} and limiter.primary_focus == "anaerobic_alactic_peak_power":
            return RowingQualityFamily.POWER
        if context.race_type == "one_k" and limiter.primary_focus == "anaerobic_lactic":
            return RowingQualityFamily.ANAEROBIC
        return RowingQualityFamily.RACE_SPECIFIC
    if limiter.primary_focus == "aerobic_strength":
        return RowingQualityFamily.AEROBIC_STRENGTH
    if limiter.primary_focus == "anaerobic_lactic":
        return RowingQualityFamily.THRESHOLD
    if limiter.primary_focus == "anaerobic_alactic_peak_power":
        return RowingQualityFamily.POWER if "power" in context.phase_id else RowingQualityFamily.AEROBIC_STRENGTH
    return RowingQualityFamily.AEROBIC_STRENGTH


def _suppressed(family: RowingQualityFamily, *, context: RowingQualityPlanningContext,
                completed: Iterable[CompletedQualityExposure]) -> bool:
    # A near race may deliberately retain a small readiness exposure.  Recent
    # history informs it, but does not silently prohibit the explicit context.
    if (context.days_to_race is not None and context.days_to_race <= 7) or "taper" in context.phase_id:
        return False
    selector_role, band, *_unused, spacing = _FAMILY_SPEC[family]
    del selector_role
    required_hours = max(spacing, _BAND_SUPPRESSION_HOURS.get(band, 0))
    for item in completed:
        if not item.completed or item.completed_date > context.planning_date:
            continue
        elapsed = (context.planning_date - item.completed_date).days * 24
        if elapsed < required_hours and (item.physiological_band == band or (item.is_race and band in _BAND_SUPPRESSION_HOURS)):
            return True
    return False


def _requirement(family: RowingQualityFamily, *, context: RowingQualityPlanningContext,
                 allocation_segment_id: str, override: CoachQualityOverride | None) -> RowingQualityRequirement | None:
    selector_role, band, minimum, desired, maximum, spacing = _FAMILY_SPEC[family]
    if override:
        desired = override.desired_work_minutes if override.desired_work_minutes is not None else desired
        minimum = min(minimum, desired)
        maximum = max(maximum, desired)
    materialization_context = MaterializationContext(
        phase_id=context.phase_id,
        race_type={"one_k": "sprint_1k"}.get(context.race_type, context.race_type),
        experience=context.experience,
        available_rowing_modalities=context.available_rowing_modalities,
    )
    capabilities = quality_catalog_capabilities_v3(selector_role, context=materialization_context)
    envelopes = tuple((item.archetype_id, item.minimum_duration_minutes, item.maximum_duration_minutes) for item in capabilities)
    if not envelopes:
        return None
    exposures = override.target_exposures if override else 1
    return RowingQualityRequirement(
        f"{allocation_segment_id}:{family.value}", family, band, desired, minimum, maximum,
        exposures, exposures, exposures, envelopes, spacing,
        (f"minimum_{spacing}_hour_spacing",) if spacing else (),
        ("r6_physiological_requirement", "catalog_envelope_verified"),
        ("r6", "r5_catalog_capability"),
    )


def generate_rowing_quality_plan(
    segment: TrainingAllocationSegment,
    *,
    allocation_segment_id: str,
    performance: AthletePerformanceProfile,
    limiter: PerformanceLimiterProfile,
    focus: TrainingFocus,
    context: RowingQualityPlanningContext,
    completed_exposures: Iterable[CompletedQualityExposure] = (),
    coach_override: CoachQualityOverride | None = None,
) -> RowingQualityPlanResult:
    """Create a pure R6 plan while preserving the exact R3 rowing allocation."""
    completed = tuple(completed_exposures)
    guard_fraction = _TAPER_LOW_INTENSITY_GUARD_FRACTION if (context.days_to_race is not None and context.days_to_race <= 7) or "taper" in context.phase_id else _ORDINARY_LOW_INTENSITY_GUARD_FRACTION
    if context.recovery_active or focus.recovery_active:
        guard_fraction = 1.0
    guard = int(segment.rowing_specific_minutes * guard_fraction)
    fingerprint = _fingerprint(
        segment=segment, allocation_segment_id=allocation_segment_id, performance=performance,
        limiter=limiter, focus=focus, context=context, completed=completed, override=coach_override,
    )
    history_summary = tuple(
        f"{item.completed_date.isoformat()}:{item.physiological_band}:{item.work_minutes}"
        for item in completed if item.completed and item.completed_date <= context.planning_date
    )
    base = dict(
        allocation_segment_id=allocation_segment_id, segment_start=segment.start_date,
        segment_end=segment.end_date, rowing_specific_minutes=segment.rowing_specific_minutes,
        low_intensity_guard_minutes=guard, dominant_focus=focus.primary,
        secondary_focus=focus.secondary, performance_confidence=performance.confidence,
        completed_quality_history_summary=history_summary,
        spacing_constraint_codes=("r6_continuous_quality_spacing",),
        rationale_codes=("r3_rowing_allocation_consumed_without_mutation", "r6_low_intensity_guard_is_app_heuristic"),
        provenance=("training_allocation_v1", "performance_profile", "training_policy"),
        policy_version=ROWING_QUALITY_PLAN_VERSION,
        performance_profile_version=limiter.policy_version,
        basis_fingerprint=fingerprint,
        technical_emphasis=focus.primary == "technical_consolidation" or "technical_development" in context.goals,
        recovery_emphasis=context.recovery_active or focus.recovery_active,
    )
    if context.hard_restriction_codes or context.recovery_active or focus.recovery_active:
        plan = RowingQualityPlan(requirements=(), **base)
        if coach_override:
            return RowingQualityPlanResult(plan, (RowingQualityInfeasibility(
                "coach_override_conflicts_with_hard_restriction", allocation_segment_id,
                {"restrictions": context.hard_restriction_codes or ("recovery_active",)},
            ),))
        return RowingQualityPlanResult(plan)

    if (
        coach_override
        and ((context.days_to_race is not None and context.days_to_race <= 7) or "taper" in context.phase_id)
        and coach_override.family is not RowingQualityFamily.RACE_SPECIFIC
    ):
        return RowingQualityPlanResult(
            RowingQualityPlan(requirements=(), **base),
            (RowingQualityInfeasibility("coach_override_conflicts_with_race_context", allocation_segment_id, {
                "family": coach_override.family.value,
                "phase_id": context.phase_id,
                "days_to_race": context.days_to_race,
            }),),
        )

    family = coach_override.family if coach_override else _candidate_family(limiter, focus, context)
    if family is None:
        return RowingQualityPlanResult(RowingQualityPlan(requirements=(), **base))
    if not coach_override and _suppressed(family, context=context, completed=completed):
        return RowingQualityPlanResult(RowingQualityPlan(requirements=(), **base))
    requirement = _requirement(family, context=context, allocation_segment_id=allocation_segment_id, override=coach_override)
    if requirement is None:
        return RowingQualityPlanResult(
            RowingQualityPlan(requirements=(), **base),
            (RowingQualityInfeasibility("no_r5_compatible_quality_envelope", allocation_segment_id, {"family": family.value}),),
        )
    minimum_physical_envelope = min(item[1] for item in requirement.compatible_envelopes)
    if minimum_physical_envelope > segment.rowing_specific_minutes:
        return RowingQualityPlanResult(
            RowingQualityPlan(requirements=(), **base),
            (RowingQualityInfeasibility("quality_envelope_exceeds_r3_rowing_budget", allocation_segment_id, {
                "minimum_physical_envelope_minutes": minimum_physical_envelope,
                "r3_rowing_specific_minutes": segment.rowing_specific_minutes,
            }),),
        )
    physical_budget_for_work = segment.rowing_specific_minutes - guard
    if requirement.desired_work_minutes > physical_budget_for_work:
        return RowingQualityPlanResult(
            RowingQualityPlan(requirements=(), **base),
            (RowingQualityInfeasibility("quality_work_conflicts_with_low_intensity_guard", allocation_segment_id, {
                "desired_work_minutes": requirement.desired_work_minutes,
                "available_physiological_work_minutes": physical_budget_for_work,
            }),),
        )
    plan = RowingQualityPlan(requirements=(requirement,), **base)
    return RowingQualityPlanResult(plan)


def rowing_quality_plan_planversion_fragment(plans: Iterable[RowingQualityPlan]) -> dict[str, object]:
    """Additive persistence payload only; no live API route consumes it yet."""
    def requirement(item: RowingQualityRequirement) -> dict[str, object]:
        return {
            "requirement_id": item.requirement_id, "family": item.family.value,
            "physiological_band": item.physiological_band,
            "desired_work_minutes": item.desired_work_minutes,
            "minimum_work_minutes": item.minimum_work_minutes,
            "maximum_work_minutes": item.maximum_work_minutes,
            "minimum_exposures": item.minimum_exposures, "target_exposures": item.target_exposures,
            "maximum_exposures": item.maximum_exposures,
            "compatible_envelopes": [list(value) for value in item.compatible_envelopes],
            "minimum_spacing_hours": item.minimum_spacing_hours,
            "rationale_codes": list(item.rationale_codes), "provenance": list(item.provenance),
        }
    return {
        "rowing_quality_plan_version": ROWING_QUALITY_PLAN_VERSION,
        "rowing_quality_plans": [
            {
                "allocation_segment_id": item.allocation_segment_id,
                "segment_start": item.segment_start.isoformat(), "segment_end": item.segment_end.isoformat(),
                "rowing_specific_minutes": item.rowing_specific_minutes,
                "low_intensity_guard_minutes": item.low_intensity_guard_minutes,
                "requirements": [requirement(value) for value in item.requirements],
                "dominant_focus": item.dominant_focus, "secondary_focus": item.secondary_focus,
                "performance_confidence": item.performance_confidence,
                "completed_quality_history_summary": list(item.completed_quality_history_summary),
                "spacing_constraint_codes": list(item.spacing_constraint_codes),
                "rationale_codes": list(item.rationale_codes), "provenance": list(item.provenance),
                "policy_version": item.policy_version,
                "performance_profile_version": item.performance_profile_version,
                "basis_fingerprint": item.basis_fingerprint,
            }
            for item in sorted(plans, key=lambda value: (value.segment_start, value.allocation_segment_id))
        ],
    }


# Explicit milestone-facing name.  The shorter spelling remains the internal
# pure-function seam used by the R6 tests.
generate_rowing_quality_plan_v3 = generate_rowing_quality_plan
