"""Athlete-relative V3 quality composition, upstream of TrainingNeeds."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from .performance_profile import AthletePerformanceProfile, PerformanceLimiterProfile, TrainingDevelopmentBlock, TrainingFocus
from .planning_domain import PhysiologicalState, TrainingCompositionTarget, TrainingLoadTarget, TrainingRole


def derive_training_composition_target(
    *,
    planning_date: date,
    state: PhysiologicalState,
    load_targets: Iterable[TrainingLoadTarget],
    performance: AthletePerformanceProfile,
    limiter: PerformanceLimiterProfile,
    focus: TrainingFocus,
    development_block: TrainingDevelopmentBlock,
) -> TrainingCompositionTarget:
    """Translate supported relative evidence into one compatible role emphasis.

    Race/taper/recovery own the decision whenever active.  Incomplete batteries
    deliberately retain the conservative baseline rather than manufacturing a
    limiter.  The mapping is an application heuristic, not a published dose
    formula, and cannot add volume outside the existing load targets.
    """
    targets = tuple(sorted(load_targets, key=lambda item: item.horizon_days))
    base = targets[0]
    recent_quality = state.recent_7d_load.quality_exposures if state.recent_7d_load else 0
    roles: tuple[TrainingRole, ...] = ()
    rationale = [
        "athlete_relative_composition_heuristic_not_population_norm",
        f"development_block:{development_block.block_id}",
    ]
    maximum_quality = 1
    if focus.recovery_active:
        rationale.append("recovery_overrides_limiter_development")
        maximum_quality = 0
    elif focus.primary == "taper_race_readiness":
        rationale.append("race_taper_overrides_long_term_limiter_development")
    elif limiter.mode != "athlete_relative_heuristic":
        rationale.append("incomplete_or_unknown_battery_preserves_conservative_baseline")
    elif limiter.primary_focus == "aerobic_strength":
        roles = (TrainingRole.UT1_AEROBIC_STRENGTH,)
        rationale.append("relative_30r20_relationship_supports_aerobic_strength_emphasis")
    elif limiter.primary_focus == "anaerobic_lactic":
        # Outside race-specific preparation the existing catalog has no safe
        # AN envelope, so threshold development is the compatible expression.
        roles = (TrainingRole.ANAEROBIC_AN,) if base.phase == "race_specific_preparation" else (TrainingRole.THRESHOLD_AT,) if base.phase == "threshold_development" else ()
        rationale.append("relative_60sec_relationship_requests_only_phase_compatible_quality")
    elif limiter.primary_focus == "anaerobic_alactic_peak_power":
        roles = (TrainingRole.POWER_PP,) if base.phase == "race_specific_preparation" else ()
        rationale.append("relative_7stroke_relationship_requests_only_phase_compatible_power")
    if recent_quality >= maximum_quality and roles and roles[0] in {
        TrainingRole.THRESHOLD_AT, TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP,
    }:
        roles = ()
        rationale.append("recent_quality_history_satisfies_quality_guard")
    return TrainingCompositionTarget(
        planning_date, planning_date + timedelta(days=base.horizon_days - 1),
        base.horizon_days, focus.primary, focus.secondary,
        base.low_intensity_minimum_minutes, roles, maximum_quality,
        performance.confidence, limiter.mode, tuple(rationale),
        ("athlete_performance_profile", "performance_limiter", "training_focus", "training_development_block", "training_load_target"),
    )
