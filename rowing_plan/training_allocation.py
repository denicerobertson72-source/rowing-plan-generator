"""R3 authoritative partition of an R2 physical-dose recommendation.

No calendar placement, rowing intensity composition, or cross-training
modality selection belongs here.  This module only divides the exact physical
minutes already authorized by :mod:`recommended_training_dose`.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any, Iterable, Mapping

from .planning_domain import (
    CurrentTrainingSource,
    CurrentTrainingState,
    GeneralAerobicModalityCapability,
    RecommendedTrainingDose,
    TrainingAllocation,
    TrainingAllocationInfeasibility,
    TrainingAllocationResult,
    TrainingAllocationSegment,
    TrainingModality,
)
from .planning_normalization import NormalizedPlanningContext, normalize_planning_inputs
from .planning_profile import normalize_current_training_state
from .physiological_state import reduce_physiological_state
from .recommended_training_dose import recent_actual_history_is_sufficient


POLICY_VERSION = "training-allocation-v1"
DEFAULT_STRENGTH_DURATION_MINUTES = 45
GENERAL_PREP_CROSS_TRAINING_LATITUDE_MINUTES_PER_7D = 30


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _discrete_exposures(per_week: int, days: int) -> int:
    """Scale a weekly evidence/override discretely, never fractionally."""
    if per_week <= 0 or days <= 0:
        return 0
    whole_weeks, remainder = divmod(days, 7)
    interval = max(1, (7 + per_week - 1) // per_week)
    return whole_weeks * per_week + min(per_week, remainder // interval)


def _normalized_weekly_minutes(observed_minutes: int, observation_window_days: int) -> int:
    """Return a deterministic nearest whole-minute seven-day equivalent."""
    return (observed_minutes * 7 + observation_window_days // 2) // observation_window_days


def _normalized_weekly_exposures(observed_exposures: int, observation_window_days: int) -> int:
    """Return a conservative discrete weekly exposure rate.

    Floor is deliberate: a sparse observation must not create an extra weekly
    strength obligation merely because a partial observation window is scaled.
    The later ``_discrete_exposures`` function still determines what fits a
    partial cadence segment.
    """
    return observed_exposures * 7 // observation_window_days


def _is_observation_window_evidence(current: CurrentTrainingState) -> bool:
    """True only when current-state quantities came from completed actuals."""
    return (
        current.source is CurrentTrainingSource.RECENT_ACTUALS
        and current.observation_window_days is not None
    )


def _strength_inputs(profile: Mapping[str, Any], current: CurrentTrainingState) -> tuple[int, int, str, str | None, str]:
    context = profile.get("training_context", {}) if isinstance(profile.get("training_context"), Mapping) else {}
    override = context.get("strength_override", {}) if isinstance(context.get("strength_override"), Mapping) else {}
    mode = str(override.get("mode", "none"))
    requested = _positive_int(override.get("sessions_per_week"))
    current_sessions = _positive_int(context.get("current_strength_sessions_per_week"))
    if current_sessions is not None:
        strength_evidence_source = "explicit_weekly_profile_context"
    elif current.strength_exposures is not None and _is_observation_window_evidence(current):
        current_sessions = _normalized_weekly_exposures(
            current.strength_exposures, current.observation_window_days or 7,
        )
        strength_evidence_source = "normalized_observation_window_evidence"
    elif current.strength_exposures is not None:
        current_sessions = current.strength_exposures
        strength_evidence_source = "fallback_current_training_state_evidence"
    else:
        current_sessions = 0
        strength_evidence_source = "fallback_default_evidence"
    duration = _positive_int(override.get("expected_duration_minutes"))
    if duration is not None:
        duration_source = "strength_override_expected_duration"
    elif current.typical_strength_duration_minutes is not None:
        duration = current.typical_strength_duration_minutes
        duration_source = "current_training_state_strength_duration"
    elif _positive_int(context.get("typical_strength_duration_minutes")) is not None:
        duration = _positive_int(context.get("typical_strength_duration_minutes"))
        duration_source = "training_context_strength_duration"
    else:
        duration = DEFAULT_STRENGTH_DURATION_MINUTES
        duration_source = "policy_default_strength_duration"
    if mode in {"minimum", "fixed_commitment"} and requested is None:
        raise ValueError("strength_override_requires_exposure_count")
    return current_sessions, duration, duration_source, mode if mode != "none" else None, strength_evidence_source


def _goal(profile: Mapping[str, Any]) -> str | None:
    goals = profile.get("planning_goals", {}) if isinstance(profile.get("planning_goals"), Mapping) else {}
    return goals.get("primary_goal") if isinstance(goals.get("primary_goal"), str) else None


def _phase_and_race_context(profile: Mapping[str, Any], start: date, end: date) -> tuple[str, bool]:
    season = profile.get("season", {}) if isinstance(profile.get("season"), Mapping) else {}
    phase = str(season.get("phase") or season.get("phase_type") or "general_preparation")
    race_near = False
    for race in profile.get("races", ()):
        if not isinstance(race, Mapping):
            continue
        for raw in race.get("race_dates") or (race.get("end_date"),):
            try:
                race_day = date.fromisoformat(str(raw))
            except (TypeError, ValueError):
                continue
            if -1 <= (race_day - start).days <= 14:
                race_near = True
    race_specific = phase in {"race_specific_preparation", "taper", "race"} or race_near
    return phase, race_specific


def _rowing_weekly_evidence(profile: Mapping[str, Any], current: CurrentTrainingState) -> tuple[int, str]:
    athlete = profile.get("athlete", {}) if isinstance(profile.get("athlete"), Mapping) else {}
    explicit_weekly = _positive_int(athlete.get("current_approx_weekly_rowing_minutes"))
    if explicit_weekly is not None:
        return explicit_weekly, "explicit_weekly_profile_context"
    if current.approximate_rowing_minutes is not None and _is_observation_window_evidence(current):
        return (
            _normalized_weekly_minutes(
                current.approximate_rowing_minutes, current.observation_window_days or 7,
            ),
            "normalized_observation_window_evidence",
        )
    if current.approximate_rowing_minutes is not None:
        return current.approximate_rowing_minutes, "fallback_current_training_state_evidence"
    sessions_per_week = _positive_int(athlete.get("current_rowing_sessions_per_week"))
    if sessions_per_week is not None:
        return sessions_per_week * 45, "fallback_explicit_weekly_session_count"
    return 0, "fallback_default_evidence"


def weekly_evidence_for_allocation(profile: Mapping[str, Any], current: CurrentTrainingState) -> dict[str, int | str | None]:
    """Expose raw/current-state evidence and R3's normalized weekly inputs."""
    strength, _duration, _duration_source, _mode, strength_source = _strength_inputs(profile, current)
    rowing, rowing_source = _rowing_weekly_evidence(profile, current)
    return {
        "observation_window_days": current.observation_window_days,
        "raw_observation_rowing_minutes": current.approximate_rowing_minutes,
        "raw_observation_strength_exposures": current.strength_exposures,
        "weekly_rowing_minutes": rowing,
        "weekly_strength_exposures": strength,
        "rowing_evidence_source": rowing_source,
        "strength_evidence_source": strength_source,
    }


_GENERAL_AEROBIC_MODALITIES = {
    "bike": TrainingModality.BIKE,
    "elliptical": TrainingModality.ELLIPTICAL,
    "stair_climber": TrainingModality.STAIR_CLIMBER,
    "running": TrainingModality.RUNNING,
}


def general_aerobic_capabilities(profile: Mapping[str, Any]) -> tuple[GeneralAerobicModalityCapability, ...]:
    """Adapt persisted cross-training settings into R3 capability evidence.

    Disabled and explicitly avoided modalities are intentionally absent.  A
    profile currently has no date-specific cross-training-equipment field, so
    this function represents only the athlete-approved capability that exists
    in persisted input; R4 applies daily capacity/combined-day restrictions.
    """
    cross = profile.get("cross_training", {}) if isinstance(profile.get("cross_training"), Mapping) else {}
    modes = cross.get("modalities", {}) if isinstance(cross.get("modalities"), Mapping) else {}
    values = []
    for key, modality in _GENERAL_AEROBIC_MODALITIES.items():
        item = modes.get(key)
        if not isinstance(item, Mapping) or item.get("enabled") is not True:
            continue
        preference = str(item.get("preference", "neutral"))
        if preference == "avoid":
            continue
        familiarity = item.get("familiarity")
        familiarity = familiarity if familiarity in {None, "new", "some_experience", "regular"} else None
        values.append(GeneralAerobicModalityCapability(
            modality=modality,
            familiarity=familiarity,
            preference=preference if preference in {"prefer", "neutral"} else "neutral",
            provenance=(
                f"profile_cross_training:{key}:enabled",
                f"profile_cross_training:{key}:preference:{preference}",
                f"profile_cross_training:{key}:familiarity:{familiarity or 'unspecified'}",
            ),
        ))
    return tuple(sorted(values, key=lambda item: item.modality.value))


def _cross_training_eligible(profile: Mapping[str, Any]) -> bool:
    return bool(general_aerobic_capabilities(profile))


def _strength_desired_per_week(
    profile: Mapping[str, Any], current_sessions: int, override_mode: str | None,
) -> int:
    context = profile.get("training_context", {}) if isinstance(profile.get("training_context"), Mapping) else {}
    override = context.get("strength_override", {}) if isinstance(context.get("strength_override"), Mapping) else {}
    requested = _positive_int(override.get("sessions_per_week"))
    if override_mode == "fixed_commitment":
        return requested or 0
    if override_mode == "minimum":
        return max(current_sessions, requested or 0)
    if override_mode == "preferred_target" and requested is not None:
        return requested
    if current_sessions:
        return current_sessions
    if _goal(profile) == "develop_strength" or context.get("strength_priority") == "develop_strength":
        return 1
    return 0


def _segment_context_codes(segment, profile: Mapping[str, Any], state) -> tuple[str, ...]:
    codes = []
    if segment.recommendation_status in {"recovery", "taper", "capacity_constrained", "override_fixed"}:
        codes.append(f"r2_status:{segment.recommendation_status}")
    if any(item.start_date <= segment.end_date and item.end_date >= segment.start_date for item in state.recovery_constraints):
        codes.append("recovery_constraint")
    phase, race_specific = _phase_and_race_context(profile, segment.start_date, segment.end_date)
    if phase != "general_preparation":
        codes.append(f"season_phase:{phase}")
    if race_specific:
        codes.append("race_specific_context")
    return tuple(codes)


def allocate_training_dose(
    recommended_dose: RecommendedTrainingDose, *, profile: Mapping[str, Any],
    current_state: CurrentTrainingState, state,
) -> TrainingAllocationResult:
    """Partition an R2 dose without importing any legacy future target."""
    current_strength, duration, duration_source, override_mode, strength_evidence_source = _strength_inputs(profile, current_state)
    desired_per_week = _strength_desired_per_week(profile, current_strength, override_mode)
    strength_experience = str((profile.get("training_context") or {}).get("strength_experience") or "none")
    current_strength_state = str((profile.get("training_context") or {}).get("current_strength_state") or "none")
    goal = _goal(profile)
    cross_capabilities = general_aerobic_capabilities(profile)
    cross_eligible = bool(cross_capabilities)
    rowing_weekly, rowing_evidence_source = _rowing_weekly_evidence(profile, current_state)
    raw_segments: list[TrainingAllocationSegment] = []

    for segment in recommended_dose.segments:
        days = (segment.end_date - segment.start_date).days + 1
        phase, race_specific = _phase_and_race_context(profile, segment.start_date, segment.end_date)
        recovery = segment.recommendation_status == "recovery" or any(
            item.start_date <= segment.end_date and item.end_date >= segment.start_date
            for item in state.recovery_constraints
        )
        taper = segment.recommendation_status == "taper" or phase == "taper"
        exposures = _discrete_exposures(desired_per_week, days)
        rationale = [
            "allocation_from_recommended_training_dose",
            f"strength_duration:{duration_source}",
            f"strength_weekly_evidence:{strength_evidence_source}",
            f"rowing_weekly_evidence:{rowing_evidence_source}",
        ]
        constraints = list(_segment_context_codes(segment, profile, state))
        if recovery:
            exposures = 0
            rationale.append("strength_zero:recovery")
        elif taper:
            exposures = min(exposures, _discrete_exposures(1, days))
            rationale.append("strength_maintenance:taper")
        elif goal == "develop_strength":
            rationale.append("strength_goal:development")
        strength_minutes = exposures * duration

        rowing_floor = round(rowing_weekly * days / 7)
        if not race_specific and phase in {"general_preparation", "foundation_orientation", "aerobic_development"} and goal == "develop_strength":
            # Explicit policy latitude, not a percentage or second target.
            rowing_floor = max(0, rowing_floor - round(GENERAL_PREP_CROSS_TRAINING_LATITUDE_MINUTES_PER_7D * days / 7))
            rationale.append("rowing_floor:general_preparation_strength_latitude")
        if recovery:
            rowing_floor = min(rowing_floor, segment.recommended_physical_minutes)
            rationale.append("rowing_floor:recovery_context")
        else:
            rationale.append("rowing_floor:history_and_specificity")

        hard_strength = override_mode in {"minimum", "fixed_commitment"}
        if strength_minutes + rowing_floor > segment.recommended_physical_minutes:
            if hard_strength:
                return TrainingAllocationResult(infeasibility=TrainingAllocationInfeasibility(
                    code="allocation_infeasible",
                    segment_start=segment.start_date, segment_end=segment.end_date,
                    required_strength_minutes=strength_minutes,
                    rowing_specific_floor_minutes=rowing_floor,
                    recommended_physical_minutes=segment.recommended_physical_minutes,
                    shortfall_minutes=strength_minutes + rowing_floor - segment.recommended_physical_minutes,
                    conflicting_constraint_provenance=(f"strength_override:{override_mode}", "rowing_specific_floor"),
                ))
            while exposures and strength_minutes + rowing_floor > segment.recommended_physical_minutes:
                exposures -= 1
                strength_minutes = exposures * duration
            rationale.append("strength_reduced_for_rowing_specific_protection")

        if exposures == 0 and current_strength_state == "regular" and strength_experience in {"intermediate", "advanced"} and not any(code.startswith("strength_zero:") for code in rationale):
            rationale.append("strength_zero:hard_capacity_constraint")
        general_desired = 0
        if cross_eligible and not race_specific and not recovery and phase in {"general_preparation", "foundation_orientation", "aerobic_development"} and goal == "develop_strength":
            general_desired = round(GENERAL_PREP_CROSS_TRAINING_LATITUDE_MINUTES_PER_7D * days / 7)
            rationale.append("general_aerobic:eligible_general_preparation")
        elif not cross_eligible:
            rationale.append("general_aerobic:zero_no_eligible_preference")
        elif race_specific:
            rationale.append("general_aerobic:zero_race_specificity")
        general = min(general_desired, max(0, segment.recommended_physical_minutes - strength_minutes - rowing_floor))
        rowing = segment.recommended_physical_minutes - strength_minutes - general
        min_strength = exposures if hard_strength else 0
        max_strength = exposures
        raw_segments.append(TrainingAllocationSegment(
            start_date=segment.start_date, end_date=segment.end_date,
            recommended_physical_minutes=segment.recommended_physical_minutes,
            rowing_specific_minutes=rowing, strength_minutes=strength_minutes,
            general_aerobic_minutes=general, other_physical_minutes=0,
            rowing_specific_floor_minutes=rowing_floor,
            recommended_strength_exposures=exposures,
            minimum_strength_exposures=min_strength, maximum_strength_exposures=max_strength,
            expected_strength_duration_minutes=duration,
            rationale_codes=tuple(rationale), constraints=tuple(constraints),
            general_aerobic_capabilities=cross_capabilities,
        ))

    segments = []
    for previous, current in zip((None, *raw_segments[:-1]), raw_segments):
        rationale = set(current.rationale_codes)
        if previous is not None:
            changed = (
                previous.rowing_specific_minutes != current.rowing_specific_minutes
                or previous.general_aerobic_minutes != current.general_aerobic_minutes
                or previous.recommended_strength_exposures != current.recommended_strength_exposures
            )
            if changed:
                rationale.add("composition_transition:segment_context_or_duration_change")
            if abs(previous.recommended_strength_exposures - current.recommended_strength_exposures) > 1:
                rationale.add("composition_transition:strength_exposure_change")
        segments.append(replace(current, rationale_codes=tuple(rationale)))
    ordered = tuple(segments)
    allocation = TrainingAllocation(
        planning_start=recommended_dose.planning_start, planning_end=recommended_dose.planning_end,
        segments=ordered,
        recommended_physical_minutes=sum(item.recommended_physical_minutes for item in ordered),
        rowing_specific_minutes=sum(item.rowing_specific_minutes for item in ordered),
        strength_minutes=sum(item.strength_minutes for item in ordered),
        general_aerobic_minutes=sum(item.general_aerobic_minutes for item in ordered),
        other_physical_minutes=sum(item.other_physical_minutes for item in ordered),
        recommended_strength_exposures=sum(item.recommended_strength_exposures for item in ordered),
        rationale_codes=tuple(code for item in ordered for code in item.rationale_codes),
        evidence_sources=(current_state.source.value, "recommended_training_dose"),
        constraints=tuple(code for item in ordered for code in item.constraints),
        confidence=recommended_dose.confidence,
        policy_version=POLICY_VERSION,
    )
    return TrainingAllocationResult(allocation=allocation)


def reconcile_training_allocation_for_cadence_feasibility(
    allocation: TrainingAllocation,
    *,
    capacity: Iterable[Any],
    preferences: Any = None,
    authorities: Iterable[Any] = (),
    restrictions: Mapping[date, tuple[str, ...]] | None = None,
    materialization_context: Any = None,
    current_training_state: CurrentTrainingState | None = None,
) -> TrainingAllocationResult:
    """Keep R3's category partition unless actual dated envelopes reject it.

    R2 remains the total-dose authority.  This is the sole narrowly-scoped
    exception to R3's original category split: in non-race/non-recovery
    general-preparation context, R3 may exchange a bounded amount of rowing
    residual for athlete-approved general aerobic work when, and only when,
    the original partition cannot be placed under R4/R5's real envelopes.
    """
    # Local import avoids making the allocation module depend on R4 at import
    # time while ensuring the query uses the one actual envelope implementation.
    from .materialization_v3 import MaterializationContext
    from .training_days import (
        CombinedDayPreferences,
        evaluate_cadence_category_feasibility,
    )

    capacity = tuple(capacity)
    preferences = preferences if preferences is not None else CombinedDayPreferences()
    materialization_context = materialization_context or MaterializationContext()
    authorities = tuple(authorities)
    reconciled: list[TrainingAllocationSegment] = []

    for segment in allocation.segments:
        original = evaluate_cadence_category_feasibility(
            segment, capacity, authorities=authorities, preferences=preferences,
            restrictions=restrictions, materialization_context=materialization_context,
            current_training_state=current_training_state,
        )
        if original.feasible:
            reconciled.append(replace(
                segment,
                category_feasibility_original=True,
                category_feasibility_final=True,
                category_feasibility_runtime_ms=original.runtime_ms,
            ))
            continue

        # This policy uses the existing 30 min / 7 d general-preparation
        # latitude as a *maximum*, but now permits it to solve a demonstrated
        # envelope infeasibility even when R3's ordinary composition initially
        # assigned none of that latitude to cross-training.
        days = (segment.end_date - segment.start_date).days + 1
        phase_code = next(
            (code.removeprefix("season_phase:") for code in segment.constraints if code.startswith("season_phase:")),
            "general_preparation",
        )
        phase_allows_reconciliation = (
            phase_code in {"general_preparation", "foundation_orientation", "aerobic_development"}
            and "race_specific_context" not in segment.constraints
            and "recovery_constraint" not in segment.constraints
            and not any(code in {"r2_status:recovery", "r2_status:taper"} for code in segment.constraints)
        )
        latitude = round(GENERAL_PREP_CROSS_TRAINING_LATITUDE_MINUTES_PER_7D * days / 7)
        maximum_transfer = min(
            max(0, latitude - segment.general_aerobic_minutes),
            max(0, segment.rowing_specific_minutes - segment.rowing_specific_floor_minutes),
        )
        selected: tuple[TrainingAllocationSegment, Any] | None = None
        if phase_allows_reconciliation and segment.general_aerobic_capabilities:
            for transfer in range(1, maximum_transfer + 1):
                candidate = replace(
                    segment,
                    rowing_specific_minutes=segment.rowing_specific_minutes - transfer,
                    general_aerobic_minutes=segment.general_aerobic_minutes + transfer,
                    category_reconciliation_minutes=transfer,
                    category_reconciliation_reason="cadence_envelope_feasibility_rowing_to_general_aerobic",
                    category_feasibility_original=False,
                )
                feasibility = evaluate_cadence_category_feasibility(
                    candidate, capacity, authorities=authorities, preferences=preferences,
                    restrictions=restrictions, materialization_context=materialization_context,
                    current_training_state=current_training_state,
                )
                if feasibility.feasible:
                    selected = (candidate, feasibility)
                    break
        if selected is None:
            return TrainingAllocationResult(infeasibility=TrainingAllocationInfeasibility(
                code="cadence_category_envelope_infeasible",
                segment_start=segment.start_date,
                segment_end=segment.end_date,
                required_strength_minutes=segment.strength_minutes,
                rowing_specific_floor_minutes=segment.rowing_specific_floor_minutes,
                recommended_physical_minutes=segment.recommended_physical_minutes,
                shortfall_minutes=1,
                conflicting_constraint_provenance=tuple(sorted({
                    "r4_r5_category_envelope",
                    *original.infeasibility_codes,
                    *(("no_eligible_general_aerobic_modality",) if not segment.general_aerobic_capabilities else ()),
                })),
            ))
        candidate, final = selected
        reconciled.append(replace(
            candidate,
            rationale_codes=(*candidate.rationale_codes, "category_reconciliation:cadence_envelope_feasibility"),
            category_feasibility_final=True,
            category_feasibility_runtime_ms=round(original.runtime_ms + final.runtime_ms, 3),
        ))

    ordered = tuple(reconciled)
    return TrainingAllocationResult(allocation=TrainingAllocation(
        planning_start=allocation.planning_start,
        planning_end=allocation.planning_end,
        segments=ordered,
        recommended_physical_minutes=sum(item.recommended_physical_minutes for item in ordered),
        rowing_specific_minutes=sum(item.rowing_specific_minutes for item in ordered),
        strength_minutes=sum(item.strength_minutes for item in ordered),
        general_aerobic_minutes=sum(item.general_aerobic_minutes for item in ordered),
        other_physical_minutes=sum(item.other_physical_minutes for item in ordered),
        recommended_strength_exposures=sum(item.recommended_strength_exposures for item in ordered),
        rationale_codes=tuple(code for item in ordered for code in item.rationale_codes),
        evidence_sources=allocation.evidence_sources,
        constraints=tuple(code for item in ordered for code in item.constraints),
        confidence=allocation.confidence,
        policy_version=allocation.policy_version,
    ))


def generate_training_allocation_v3(
    profile: Mapping[str, Any], *, planning_date: date,
    recommended_dose: RecommendedTrainingDose | None = None,
    prior_plan: Mapping[str, Any] | None = None,
    history_actuals: Mapping[str, Mapping[str, Any]] | None = None,
    locked_sessions=(), future_placements=(),
    cadence_capacity: Iterable[Any] | None = None,
    cadence_preferences: Any = None,
    cadence_authorities: Iterable[Any] = (),
    cadence_restrictions: Mapping[date, tuple[str, ...]] | None = None,
    cadence_materialization_context: Any = None,
) -> TrainingAllocationResult:
    """Clear R3 entry point; it consumes R2 directly and no legacy targets."""
    if recommended_dose is None:
        from .replacement_plan_v3 import generate_recommended_training_dose_v3
        recommended_dose = generate_recommended_training_dose_v3(
            profile, planning_date=planning_date, prior_plan=prior_plan,
            history_actuals=history_actuals, locked_sessions=locked_sessions,
            future_placements=future_placements,
        )
    context = normalize_planning_inputs(
        profile, planning_date=recommended_dose.planning_start, prior_plan=prior_plan,
        history_actuals=history_actuals, locked_sessions=locked_sessions,
        future_placements=future_placements,
    )
    sufficient = recent_actual_history_is_sufficient(context.historical_facts, planning_start=recommended_dose.planning_start)
    current = normalize_current_training_state(
        profile, historical_facts=context.historical_facts,
        recent_actuals_sufficient=sufficient, observation_window_days=21,
    )
    state = reduce_physiological_state(context, profile=profile)
    result = allocate_training_dose(recommended_dose, profile=profile, current_state=current, state=state)
    if not result.success or cadence_capacity is None:
        return result
    return reconcile_training_allocation_for_cadence_feasibility(
        result.allocation,
        capacity=cadence_capacity,
        preferences=cadence_preferences,
        authorities=cadence_authorities,
        restrictions=cadence_restrictions,
        materialization_context=cadence_materialization_context,
        current_training_state=current,
    )
