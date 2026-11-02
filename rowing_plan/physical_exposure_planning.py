"""Physical exposure planning for the V3 replacement planner.

This is the only layer allowed to turn one approved, *generic* aerobic credit
into more than one physical TrainingNeed.  Placement receives the resulting
children but never decides how credit may be reclassified or partitioned.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Iterable, Mapping

from .materialization_capabilities import minimum_materializable_reservation
from .planning_domain import (
    AuthorityType, PhysicalExposureChild, PhysicalExposurePlan, PhysiologicalState,
    TrainingDistributionTarget, TrainingLoadTarget, TrainingNeed, TrainingRole,
)
from .planning_normalization import NormalizedPlanningContext


_LOW_INTENSITY = frozenset({
    TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC,
    TrainingRole.RECOVERY_AEROBIC, TrainingRole.TECHNICAL_REENTRY,
})
_ROWING = _LOW_INTENSITY | {
    TrainingRole.UT1_AEROBIC_STRENGTH, TrainingRole.QUALITY, TrainingRole.THRESHOLD_AT,
    TrainingRole.RACE_SPECIFIC_TR, TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP,
    TrainingRole.TAPER_MAINTENANCE, TrainingRole.RACE_PREPARATION, TrainingRole.RACE,
    TrainingRole.COURSE_PRACTICE, TrainingRole.PRIVATE_COACHING, TrainingRole.COACHED_TRAINING,
}


@dataclass(frozen=True)
class PhysicalExposurePlanningResult:
    needs: tuple[TrainingNeed, ...]
    plans: tuple[PhysicalExposurePlan, ...]


def _is_generic_residual(need: TrainingNeed) -> bool:
    return bool(need.progression_relation and "load_target_residual" in need.progression_relation)


def _available(profile: Mapping[str, object], day: date) -> bool:
    for item in profile.get("date_availability", ()):
        if isinstance(item, Mapping) and str(item.get("date")) == day.isoformat():
            return bool(item.get("available", True))
    for item in profile.get("weekly_availability", ()):
        if isinstance(item, Mapping) and str(item.get("weekday", "")).lower() == day.strftime("%A").lower():
            return bool(item.get("available", True))
    return True


def _fixed_rest(context: NormalizedPlanningContext, day: date) -> bool:
    return any(item.type is AuthorityType.FIXED_REST and item.date == day for item in context.authoritative_commitments)


def _final_48h(context: NormalizedPlanningContext, day: date) -> bool:
    return any(0 <= (date.fromisoformat(race_date) - day).days <= 2 for race_date, *_ in context.race_context)


def _hard_low_intensity_recovery(state: PhysiologicalState, need: TrainingNeed) -> bool:
    return any(
        constraint.start_date <= need.latest_date and constraint.end_date >= need.earliest_date
        and TrainingRole.TECHNICAL_REENTRY in constraint.hard_excluded_roles
        for constraint in state.recovery_constraints
    )


def _selected_exposures(selected, target: TrainingDistributionTarget) -> tuple[int, int]:
    rowing = [item for item in selected if target.horizon_start <= item.date <= target.horizon_end and item.role in _ROWING]
    low = [item for item in selected if target.horizon_start <= item.date <= target.horizon_end and item.role in _LOW_INTENSITY]
    return len(rowing), len(low)


def _headroom(
    source: TrainingNeed, targets: tuple[TrainingLoadTarget, ...], selected,
) -> tuple[int, int]:
    """Reject a representation only when all of its credit cannot fit a target.

    This is planning-time arithmetic, not placement: dates remain flexible, but
    a source whose full conserved credit would exceed an enclosing rolling cap
    is not advertised as a fully feasible exposure plan.
    """
    target_headroom: list[int] = []
    maximum_headroom: list[int] = []
    for target in targets:
        if not (target.horizon_start <= source.earliest_date and source.latest_date <= target.horizon_end):
            continue
        already = sum(
            item.target_credit.minutes for item in selected
            if item.role in _ROWING and target.horizon_start <= item.date <= target.horizon_end
            and item.authority_status.value != "authoritative"
        )
        total = target.completed_rowing_minutes + target.authoritative_future_rowing_minutes + already
        target_headroom.append(max(0, target.total_rowing_target_minutes - total))
        maximum_headroom.append(max(0, target.maximum_rowing_minutes - total))
    return (min(target_headroom, default=source.target_credit.minutes), min(maximum_headroom, default=source.target_credit.minutes))


def plan_physical_exposures(
    needs: Iterable[TrainingNeed], distribution_targets: Iterable[TrainingDistributionTarget], *,
    context: NormalizedPlanningContext, state: PhysiologicalState, profile: Mapping[str, object],
    selected_placements=(), load_targets: Iterable[TrainingLoadTarget] = (),
) -> PhysicalExposurePlanningResult:
    """Derive catalog-safe physical children from eligible generic residual credit.

    Dedicated UT2, long aerobic, recovery, technical, coaching, strength, and
    quality needs intentionally pass through unchanged.  Only a residual need
    explicitly marked as generic load top-up can become easy technical children.
    """
    original = tuple(needs)
    targets = tuple(item for item in distribution_targets if item.taper_frequency_retention)
    loads = tuple(load_targets)
    selected = tuple(selected_placements)
    result = list(original)
    plans: list[PhysicalExposurePlan] = []
    partitioned = False
    for source in original:
        if partitioned or not _is_generic_residual(source) or source.role is not TrainingRole.DEDICATED_UT2:
            continue
        applicable = next((item for item in targets if item.horizon_start <= source.earliest_date <= item.horizon_end), None)
        if applicable is None:
            continue
        selected_rowing, selected_low = _selected_exposures(selected, applicable)
        deficit = max(0, applicable.target_rowing_exposures - selected_rowing, applicable.minimum_low_intensity_exposures - selected_low)
        if not deficit:
            continue
        viable_days = tuple(
            source.earliest_date + timedelta(days=offset)
            for offset in range((source.latest_date - source.earliest_date).days + 1)
            if _available(profile, source.earliest_date + timedelta(days=offset))
            and not _fixed_rest(context, source.earliest_date + timedelta(days=offset))
            and not _final_48h(context, source.earliest_date + timedelta(days=offset))
        )
        if len(viable_days) < 2 or _hard_low_intensity_recovery(state, source):
            plans.append(PhysicalExposurePlan(
                f"exposure:{source.id}", (source.id,), source.target_credit.minutes, 0, source.target_credit.minutes,
                ("generic_low_intensity_residual",), 0, (), source.target_credit.minutes, 0,
                ("taper_frequency_retention",), "taper", applicable.target_rowing_exposures,
                True, False, "no_viable_low_intensity_exposure_window", ("physical_exposure_planning",),
            ))
            continue
        minimum, capability = minimum_materializable_reservation(role="technical_reentry", profile=profile, day=viable_days[0])
        if minimum is None or source.target_credit.minutes < minimum * 2:
            plans.append(PhysicalExposurePlan(
                f"exposure:{source.id}", (source.id,), source.target_credit.minutes, 0, source.target_credit.minutes,
                ("generic_low_intensity_residual",), 0, (), source.target_credit.minutes, 0,
                ("catalog_minimum_session_envelope",), "taper", applicable.target_rowing_exposures,
                True, False, "insufficient_generic_credit_for_two_catalog_exposures", ("physical_exposure_planning",),
            ))
            continue
        target_headroom, maximum_headroom = _headroom(source, loads, selected)
        # A residual is discretionary target-range credit.  Prefer the
        # smallest catalog-safe exposure that reaches toward target and serves
        # frequency; never treat maximum as a fill target.
        realized = min(source.target_credit.minutes, maximum_headroom)
        if realized:
            realized = max(minimum, min(realized, max(minimum, target_headroom)))
        if realized > maximum_headroom or realized < minimum:
            plans.append(PhysicalExposurePlan(
                f"exposure:{source.id}", (source.id,), source.target_credit.minutes, 0, source.target_credit.minutes,
                ("generic_low_intensity_residual",), 0, (), source.target_credit.minutes, 0,
                ("taper_frequency_retention", "rolling_maximum"), "taper", applicable.target_rowing_exposures,
                True, False, "insufficient_headroom_for_catalog_minimum", ("physical_exposure_planning",),
            ))
            continue
        child = PhysicalExposureChild(
            f"{source.id}:physical:01", (source.id,), TrainingRole.TECHNICAL_REENTRY,
            source.target_credit.__class__(TrainingRole.TECHNICAL_REENTRY, 1, realized, True),
            minimum, max(minimum, realized), source.earliest_date, source.latest_date,
            "generic_low_intensity_residual_retained", "taper_frequency_retention", capability,
            ("physical_exposure_planning", "generic_residual_not_dedicated_ut2"),
        )
        children = (child,)
        result.remove(source)
        for index, child in enumerate(children, 1):
            result.append(replace(
                source, id=child.child_id, role=child.role, target_credit=child.target_credit,
                parent_need_id=source.id, exposure_index=index, exposure_count=len(children),
                original_target_credit_minutes=source.target_credit.minutes,
                partition_reason="physical_exposure_plan", provenance=child.provenance,
            ))
        plans.append(PhysicalExposurePlan(
            f"exposure:{source.id}", (source.id,), source.target_credit.minutes, realized, source.target_credit.minutes - realized,
            ("generic_low_intensity_residual",), len(children), children,
            source.target_credit.minutes, 0, ("taper_frequency_retention", "catalog_safe_generic_split"),
            "taper", applicable.target_rowing_exposures, True, True,
            "partial_residual_realized" if realized < source.target_credit.minutes else None,
            ("physical_exposure_planning", "total_credit_conserved"),
        ))
        partitioned = True
    return PhysicalExposurePlanningResult(
        tuple(sorted(result, key=lambda item: (item.earliest_date, item.latest_date, item.id))),
        tuple(sorted(plans, key=lambda item: item.plan_id)),
    )
