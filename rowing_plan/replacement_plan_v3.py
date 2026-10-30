"""Parallel replacement-planner materialization and PlanVersion adapter.

This module deliberately owns no persistence, API routing, date placement, or
workout catalog.  It converts M7's already chosen abstract placements into the
existing V2 materialization and PlanVersion-shaped interfaces for shadow use.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from time import perf_counter
from typing import Any, Mapping

from .continuous_placement import (
    ContinuousPlacementDiagnostics,
    ContinuousPlacementResult,
    PlacementSearchConfig,
    place_continuous_training_needs,
)
from .models import (
    DateContext,
    DatedTrainingRole,
    QualityTranslationContext,
    TranslatedTrainingRole,
    V2SessionMaterializationRequest,
)
from .periodization import build_season_phases
from .physiological_state import reduce_physiological_state
from .plan_assembly_v2 import _sort_key, _totals, build_plan_version_from_v2
from .planning_domain import AuthorityType, Placement, PlacementAuthorityStatus, PhysiologicalState, TrainingNeed, TrainingRole
from .training_load_targets import derive_training_distribution_targets, derive_training_load_targets, reconcile_training_load_targets
from .training_needs import (
    generate_residual_aerobic_needs,
    generate_training_need_plan,
    partition_taper_frequency_needs,
    validate_frequency_partitions,
)
from .planning_normalization import NormalizedPlanningContext, normalize_planning_inputs
from .scheduler_v2 import finalize_translated_quality_sequence, translate_quality_role
from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy
from .performance_profile import build_athlete_performance_profile, derive_development_block, derive_performance_limiter, derive_training_focus


@dataclass(frozen=True)
class MaterializationDiagnostic:
    code: str
    placement_id: str | None = None
    date: date | None = None
    role: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class PlacementMaterializationResult:
    requests: tuple[V2SessionMaterializationRequest, ...]
    rest_dates: tuple[date, ...]
    diagnostics: tuple[MaterializationDiagnostic, ...] = ()


@dataclass(frozen=True)
class ReplacementPlanCandidate:
    success: bool
    plan: Mapping[str, Any] | None
    context: NormalizedPlanningContext
    state: PhysiologicalState
    needs: tuple[TrainingNeed, ...]
    placements: tuple[Placement, ...]
    placement_diagnostics: ContinuousPlacementDiagnostics
    materialization_diagnostics: tuple[MaterializationDiagnostic, ...] = ()
    failure_reason: str | None = None
    stage_timings_ms: tuple[tuple[str, float], ...] = field(default=(), compare=False)


_ROW_ROLE_MAP = {
    TrainingRole.DEDICATED_UT2: "dedicated_ut2",
    TrainingRole.LONG_AEROBIC: "long_aerobic",
    TrainingRole.UT1_AEROBIC_STRENGTH: "ut1_aerobic_strength",
    TrainingRole.TECHNICAL_REENTRY: "technical_reentry",
    TrainingRole.RECOVERY_AEROBIC: "recovery_aerobic",
}
_EXPLICIT_QUALITY = {
    TrainingRole.THRESHOLD_AT: "AT",
    TrainingRole.RACE_SPECIFIC_TR: "TR",
    TrainingRole.ANAEROBIC_AN: "AN",
    TrainingRole.POWER_PP: "PP",
    TrainingRole.RACE_PREPARATION: "TR",
    TrainingRole.TAPER_MAINTENANCE: "TR",
}


def _phase_lookup(profile: Mapping[str, Any], days: tuple[date, ...]) -> dict[date, tuple[str, str]]:
    spans = build_season_phases(dict(profile))
    result = {}
    for day in days:
        span = next((item for item in spans if item["start_date"] <= day.isoformat() <= item["end_date"]), None)
        if span:
            result[day] = (str(span["phase_id"]), str(span["phase_type"]))
        else:
            result[day] = ("replacement:outside_season", "general_preparation")
    return result


def _race_fact(profile: Mapping[str, Any], day: date) -> Mapping[str, Any]:
    for race in profile.get("races", ()):
        dates = race.get("race_dates") or [race.get("end_date")]
        if day.isoformat() in {str(value) for value in dates if value}:
            return race
    return {}


def _practice_fact(profile: Mapping[str, Any], day: date) -> Mapping[str, Any]:
    for race in profile.get("races", ()):
        for practice in race.get("practice_sessions", ()):
            if practice.get("date") == day.isoformat():
                return practice
    return {}


def _upcoming_race_fact(profile: Mapping[str, Any], day: date) -> Mapping[str, Any]:
    candidates=[]
    for race in profile.get("races", ()):
        dates=tuple(sorted(date.fromisoformat(str(value)) for value in (race.get("race_dates") or [race.get("end_date")]) if value))
        if dates and dates[-1] >= day:
            candidates.append((dates[0], race, dates))
    if not candidates:
        return {}
    start, race, dates=min(candidates,key=lambda item:(item[0],str(item[1].get("event_name",""))))
    return {**race,"race_dates":tuple(item.isoformat() for item in dates),"days_to_race":(start-day).days}


def _quality_translation(
    placement: Placement, role: TrainingRole, phase_id: str, phase_type: str,
) -> TranslatedTrainingRole:
    provenance = "fixed" if placement.authority_status is PlacementAuthorityStatus.AUTHORITATIVE else "provisional"
    if role in _EXPLICIT_QUALITY:
        quality_type = _EXPLICIT_QUALITY[role]
        return TranslatedTrainingRole(
            placement.placement_id, placement.training_need_id, placement.date,
            placement.reservation_minutes, phase_id, "quality", quality_type,
            provenance, reason_code=f"explicit_replacement_role:{role.value}",
        )
    generic = DatedTrainingRole(
        placement.date, "quality", placement.reservation_minutes, phase_type,
        placement.training_need_id, provenance, (), placement.placement_id,
    )
    translated = translate_quality_role(generic, QualityTranslationContext(phase_type))
    return TranslatedTrainingRole(
        translated.placement_id, translated.source_id, translated.date,
        translated.planned_duration_minutes, phase_id, translated.upstream_role,
        translated.quality_type, translated.provenance, translated.user_fixed,
        translated.original_date, translated.override_id, translated.reason_code,
    )


def adapt_placements_to_materialization_requests(
    placements: tuple[Placement, ...] | list[Placement], *, profile: Mapping[str, Any], selection_context: Mapping[str, Any] | None = None, selector_history=(),
) -> PlacementMaterializationResult:
    """Map placed needs to the existing V2 request/quality pipeline.

    This adapter cannot select a date or alter target credit.  Every resulting
    request carries the M7 reservation as its existing downstream capacity.
    """
    ordered = tuple(sorted(placements, key=lambda item: (item.date, item.placement_id))); selection_context=dict(selection_context or {})
    phase = _phase_lookup(profile, tuple(sorted({item.date for item in ordered})))
    diagnostics: list[MaterializationDiagnostic] = []
    requests: list[V2SessionMaterializationRequest] = []
    quality: list[tuple[Placement, TrainingRole, str, str]] = []
    rest_dates: list[date] = []

    for placement in ordered:
        role = placement.role
        if role is None:
            diagnostics.append(MaterializationDiagnostic("no_materialization_mapping", placement.placement_id, placement.date, None, "placement_role_missing"))
            continue
        _phase_id, phase_type = phase[placement.date]
        # Existing V2 materializers consume the established phase *type*, not
        # the opaque season-span identifier.
        phase_id = phase_type
        fixed = placement.authority_status is not PlacementAuthorityStatus.PROVISIONAL
        if role is TrainingRole.REST:
            rest_dates.append(placement.date)
            continue
        if placement.authority_type is AuthorityType.USER_FIXED_PLACEMENT:
            # A user-fixed serialized session is authoritative data, not a new
            # generic workout request.  The candidate merges it through the
            # existing authoritative-session assembly path below.
            continue
        upcoming=_upcoming_race_fact(profile, placement.date)
        race_type=str(upcoming.get("race_type", "general")); race_priority=upcoming.get("priority")
        if role in {TrainingRole.RACE, TrainingRole.COURSE_PRACTICE}:
            request_role = "race" if role is TrainingRole.RACE else "course_practice"
            fact = _race_fact(profile, placement.date) if role is TrainingRole.RACE else _practice_fact(profile, placement.date)
            requests.append(V2SessionMaterializationRequest(request_role, placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=True, fact=fact, race_type=race_type, race_priority=race_priority, selection_context=selection_context))
            continue
        if role is TrainingRole.PRIVATE_COACHING:
            requests.append(V2SessionMaterializationRequest("private_coaching", placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=True, race_type=race_type, race_priority=race_priority, selection_context=selection_context))
            continue
        if role is TrainingRole.COACHED_TRAINING:
            requests.append(V2SessionMaterializationRequest("coached_training", placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=fixed, race_type=race_type, race_priority=race_priority, selection_context=selection_context))
            continue
        if role is TrainingRole.STRENGTH:
            requests.append(V2SessionMaterializationRequest("strength", placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=fixed, race_type=race_type, race_priority=race_priority, selection_context=selection_context))
            continue
        if role in _ROW_ROLE_MAP:
            requests.append(V2SessionMaterializationRequest(_ROW_ROLE_MAP[role], placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=fixed, race_type=race_type, race_priority=race_priority, selection_context=selection_context))
            continue
        if role is TrainingRole.QUALITY or role in _EXPLICIT_QUALITY:
            quality.append((placement, role, phase_id, phase_type))
            continue
        diagnostics.append(MaterializationDiagnostic("no_materialization_mapping", placement.placement_id, placement.date, role.value))

    if quality:
        translated = tuple(_quality_translation(*item) for item in quality)
        finalized = finalize_translated_quality_sequence(
            translated,
            experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")),
            race_types={item.placement_id: str(_upcoming_race_fact(profile, item.date).get("race_type", "general")) for item, *_ in quality},
            race_priorities={item.placement_id: str(_upcoming_race_fact(profile, item.date).get("priority", "B")) for item, *_ in quality},
            preference=str(profile.get("preferences", {}).get("workout_structure_preference", "varied")),
            initial_history=selector_history,
            selection_contexts={item.placement_id: selection_context for item, *_ in quality},
        )
        if not finalized.success:
            diagnostics.append(MaterializationDiagnostic(
                "no_eligible_archetype" if finalized.failure_stage == "selection" else "quality_finalization_failed",
                finalized.failed_placement_id, finalized.failed_date, "quality", finalized.failure_reason,
            ))
        else:
            concrete = {item.placement_id: item for item in finalized.roles}
            for placement, _role, phase_id, _phase_type in quality:
                requests.append(V2SessionMaterializationRequest(
                    "quality", placement.date, placement.reservation_minutes, phase_id,
                    placement_id=placement.placement_id, source_id=placement.training_need_id,
                    fixed=placement.authority_status is not PlacementAuthorityStatus.PROVISIONAL,
                    race_type=str(_upcoming_race_fact(profile, placement.date).get("race_type", "general")),
                    race_priority=_upcoming_race_fact(profile, placement.date).get("priority"), selection_context=selection_context,
                    concrete_quality=concrete[placement.placement_id],
                ))
    return PlacementMaterializationResult(
        tuple(sorted(requests, key=lambda item: (item.date, item.placement_id))),
        tuple(sorted(set(rest_dates))), tuple(sorted(diagnostics, key=lambda item: (item.code, item.placement_id or ""))),
    )


def _calendar_from_placement_result(result: ContinuousPlacementResult, profile: Mapping[str, Any]) -> tuple[DateContext, ...]:
    phases = _phase_lookup(profile, tuple(item.date for item in result.available_dates))
    return tuple(
        DateContext(
            item.date, item.date.strftime("%A"), phases[item.date][1],
            item.available_minutes > 0, item.available_minutes, 0,
            item.remaining_minutes, unavailable=item.available_minutes == 0,
        )
        for item in result.available_dates
    )


def _empty_date_rationales(
    result: ContinuousPlacementResult,
    state: PhysiologicalState,
    load_targets,
) -> tuple[dict[str, str], ...]:
    """Explain empty *available* dates from current facts, not wishful intent."""
    placed_dates = {item.date for item in result.placements}
    residual_exists = any(item.remaining_rowing_minutes or item.remaining_low_intensity_minutes for item in load_targets)
    unplaced_by_date: dict[date, list[str]] = {}
    for item in result.diagnostics.unplaced_needs:
        for candidate in item.candidate_dates_considered:
            unplaced_by_date.setdefault(candidate, []).append(item.primary_blocking_reason)
    values = []
    for item in result.available_dates:
        if item.available_minutes <= 0 or item.date in placed_dates:
            continue
        if any(rule.start_date <= item.date <= rule.end_date for rule in state.recovery_constraints):
            reason = "required_recovery"
        elif not residual_exists:
            reason = "taper_or_load_target_satisfied"
        elif item.date in unplaced_by_date:
            blockers = unplaced_by_date[item.date]
            if any("capacity" in blocker for blocker in blockers):
                reason = "capacity_restriction"
            elif any("recovery" in blocker or "spacing" in blocker for blocker in blockers):
                reason = "active_need_but_not_physio_eligible"
            else:
                reason = "other_explicit_policy_reason"
        else:
            reason = "no_active_need"
        values.append({"date": item.date.isoformat(), "reason": reason})
    return tuple(values)


def _distribution_diagnostics(result: ContinuousPlacementResult, targets) -> tuple[dict[str, Any], ...]:
    """Describe selected physical exposure distribution without creating dose."""
    low_roles = {
        TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC,
        TrainingRole.RECOVERY_AEROBIC, TrainingRole.TECHNICAL_REENTRY,
    }
    rowing_roles = low_roles | {
        TrainingRole.UT1_AEROBIC_STRENGTH, TrainingRole.QUALITY,
        TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR,
        TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP,
        TrainingRole.TAPER_MAINTENANCE, TrainingRole.RACE_PREPARATION,
        TrainingRole.RACE, TrainingRole.COURSE_PRACTICE,
        TrainingRole.PRIVATE_COACHING, TrainingRole.COACHED_TRAINING,
    }
    values = []
    for target in targets:
        dates = tuple(sorted(item.date for item in result.placements if item.role in rowing_roles and target.horizon_start <= item.date <= target.horizon_end))
        low_dates = tuple(sorted(item.date for item in result.placements if item.role in low_roles and target.horizon_start <= item.date <= target.horizon_end))
        bounds = (target.horizon_start - timedelta(days=1), *low_dates, target.horizon_end + timedelta(days=1))
        actual_gap = max((right - left).days - 1 for left, right in zip(bounds, bounds[1:]))
        available = {item.date for item in result.available_dates if item.available_minutes > 0}
        occupied = {item.date for item in result.placements}
        max_empty = current = 0
        for day in (target.horizon_start + timedelta(days=offset) for offset in range(target.horizon_days)):
            if day in available and day not in occupied:
                current += 1; max_empty = max(max_empty, current)
            else:
                current = 0
        values.append({
            "horizon_days": target.horizon_days,
            "distribution_target_exposures": target.target_rowing_exposures,
            "selected_exposures": len(dates),
            "selected_low_intensity_exposures": len(low_dates),
            "frequency_deficit": max(0, target.minimum_rowing_exposures - len(dates)),
            "preferred_gap": target.preferred_max_aerobic_gap_days,
            "actual_gap": actual_gap,
            "consecutive_empty_available_days": max_empty,
        })
    return tuple(values)


def _selector_history_from_prior(prior_plan: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    values=[]
    for session in (prior_plan or {}).get("sessions", ()):
        if not isinstance(session, Mapping) or not isinstance(session.get("session_fingerprint"), Mapping):
            continue
        values.append({**dict(session["session_fingerprint"]), "date": session.get("date"), "completed": bool(session.get("completed") or session.get("actual")), "modified": bool(session.get("modified")), "rpe": session.get("rpe")})
    return tuple(sorted(values, key=lambda item: (str(item.get("date", "")), str(item.get("archetype_id", "")))))


def generate_plan_sequence_v3_candidate(
    profile: Mapping[str, Any], config: Mapping[str, Any], bands, power: Mapping[str, Any], *,
    planning_date: date, prior_plan: Mapping[str, Any] | None = None,
    history_actuals: Mapping[str, Mapping[str, Any]] | None = None,
    locked_sessions=(), future_placements=(), policy: TrainingPolicy = DEFAULT_TRAINING_POLICY,
    placement_config: PlacementSearchConfig = PlacementSearchConfig(),
    needs: tuple[TrainingNeed, ...] | None = None,
) -> ReplacementPlanCandidate:
    """Generate an in-memory replacement candidate only; never persist or route."""
    del config
    timings: list[tuple[str, float]] = []
    started = perf_counter()
    context = normalize_planning_inputs(
        profile, planning_date=planning_date, prior_plan=prior_plan,
        history_actuals=history_actuals, locked_sessions=locked_sessions,
        future_placements=future_placements,
    )
    timings.append(("normalization", round((perf_counter() - started) * 1000, 3)))
    started = perf_counter()
    state = reduce_physiological_state(context, profile=profile)
    timings.append(("physiological_reduction", round((perf_counter() - started) * 1000, 3)))
    started = perf_counter()
    need_plan = generate_training_need_plan(context, state, policy, profile=profile)
    performance = build_athlete_performance_profile(profile, as_of=planning_date)
    limiter = derive_performance_limiter(performance, coach_focus=profile.get("preferences", {}).get("development_focus"))
    next_race = _upcoming_race_fact(profile, planning_date)
    focus = derive_training_focus(limiter, phase=str(state.phase_context.get("phase_type") or "general_preparation"), race_type=str(next_race.get("race_type", "general")), days_to_race=next_race.get("days_to_race"), recovery_active=bool(state.recovery_constraints))
    prior_diagnostics = (prior_plan or {}).get("v2_diagnostics", {}) if isinstance(prior_plan, Mapping) else {}
    development_block = derive_development_block(focus, planning_date=planning_date, prior_diagnostics=prior_diagnostics)
    load_targets = derive_training_load_targets(context, state, policy, profile=profile, planning_date=planning_date)
    distribution_targets = derive_training_distribution_targets(load_targets, policy)
    categorical_needs = tuple(needs) if needs is not None else need_plan.needs
    timings.append(("need_generation", round((perf_counter() - started) * 1000, 3)))
    started = perf_counter()
    categorical = place_continuous_training_needs(
        context, state, categorical_needs, policy, profile=profile, config=placement_config,
        distribution_targets=distribution_targets,
    )
    residual_plan = generate_residual_aerobic_needs(
        categorical_needs, load_targets, policy, selected_placements=categorical.placements,
    )
    categorical_ids = {item.id for item in categorical_needs}
    residual_needs = tuple(item for item in residual_plan if item.id not in categorical_ids)
    redistribution = partition_taper_frequency_needs(
        (*categorical_needs, *residual_needs), distribution_targets, context=context, state=state,
        profile=profile, selected_placements=categorical.placements, load_targets=load_targets,
    )
    effective_needs = redistribution.needs
    validate_frequency_partitions(effective_needs)
    placed = place_continuous_training_needs(
        context, state, effective_needs, policy, profile=profile, config=placement_config,
        load_targets=load_targets,
        distribution_targets=distribution_targets,
    )
    need_by_id = {item.id: item for item in effective_needs}
    # Reconciliation is about the selected physical date, not merely the
    # broad candidate window carried by a need.
    final_load_targets = reconcile_training_load_targets(
        load_targets, placements=placed.placements, need_by_id=need_by_id,
        suppressed_residual_by_target=placed.diagnostics.suppressed_residual_by_target,
    )
    empty_date_rationales = _empty_date_rationales(placed, state, final_load_targets)
    distribution_diagnostics = _distribution_diagnostics(placed, distribution_targets)
    timings.append(("sequence_placement", round((perf_counter() - started) * 1000, 3)))
    started = perf_counter()
    selector_history = _selector_history_from_prior(prior_plan)
    selection_context = {
        "active_training_focus": focus.primary,
        "performance_limiter_mode": limiter.mode,
        "performance_limiter_focus": limiter.primary_focus,
        "test_evidence_used": performance.source_test_ids or ("none",),
        "race_context_used": {"race_type": focus.race_type, "days_to_race": focus.days_to_race},
        "development_block_id": development_block.block_id,
        "progression_reason": "prior_completed_history" if selector_history else "initial_exposure",
        "recent_history_considered": len(selector_history),
    }
    materialized = adapt_placements_to_materialization_requests(placed.placements, profile=profile, selection_context=selection_context, selector_history=selector_history)
    timings.append(("materialization", round((perf_counter() - started) * 1000, 3)))
    serialized_locks = tuple(
        dict(item) for item in locked_sessions
        if isinstance(item, Mapping) and {"date", "day", "session_id", "mode", "title", "band", "total_cardio_minutes", "phase", "structure"} <= set(item)
    )
    user_fixed_dates = {item.date.isoformat() for item in placed.placements if item.authority_type is AuthorityType.USER_FIXED_PLACEMENT}
    supplied_dates = {item["date"] for item in serialized_locks}
    authority_diagnostics = tuple(
        MaterializationDiagnostic("authority_mismatch", detail="user_fixed_serialized_session_required")
        for item in sorted(user_fixed_dates - supplied_dates)
    )
    all_materialization_diagnostics = (*materialized.diagnostics, *authority_diagnostics)
    if all_materialization_diagnostics:
        return ReplacementPlanCandidate(False, None, context, state, effective_needs, placed.placements, placed.diagnostics, all_materialization_diagnostics, all_materialization_diagnostics[0].code, tuple(timings))
    calendar = _calendar_from_placement_result(placed, profile)
    historical_calendar = {
        item["date"]: dict(item) for item in (prior_plan or {}).get("calendar_days", ())
        if isinstance(item, Mapping) and isinstance(item.get("date"), str) and item["date"] < planning_date.isoformat()
    }
    historical_sessions = tuple(
        dict(item) for item in (prior_plan or {}).get("sessions", ())
        if isinstance(item, Mapping) and isinstance(item.get("date"), str) and item["date"] < planning_date.isoformat()
    )
    repeated_generated: dict[str, int] = {}
    for need in effective_needs:
        if need.exposure_count > 1:
            repeated_generated[need.role.value] = repeated_generated.get(need.role.value, 0) + 1
    diagnostics = {
        "scheduler_version": "replacement-v3-shadow",
        "athlete_performance_profile": {
            "source_test_ids": performance.source_test_ids, "two_k_seconds": performance.two_k_seconds,
            "two_k_watts": performance.two_k_watts, "thirty_r20_watts": performance.thirty_r20_watts,
            "sixty_second_watts": performance.sixty_second_watts, "seven_stroke_watts": performance.seven_stroke_watts,
            "relationships": dict(performance.relationships), "longitudinal_deltas": dict(performance.longitudinal_deltas),
            "confidence": performance.confidence, "evidence_completeness": performance.evidence_completeness,
            "warnings": performance.warnings, "provenance": performance.provenance,
        },
        "performance_limiter": {"mode": limiter.mode, "primary_focus": limiter.primary_focus, "secondary_focus": limiter.secondary_focus, "evidence": limiter.evidence, "tests_used": limiter.tests_used, "tests_missing": limiter.tests_missing, "policy_version": limiter.policy_version, "rationale": limiter.rationale},
        "active_training_focus": {"primary": focus.primary, "secondary": focus.secondary, "source": focus.source, "phase": focus.phase, "race_type": focus.race_type, "days_to_race": focus.days_to_race, "rationale": focus.rationale},
        "performance_development_block": {"block_id": development_block.block_id, "start_date": development_block.start_date.isoformat(), "end_date": development_block.end_date.isoformat(), "primary_focus": development_block.primary_focus, "secondary_focus": development_block.secondary_focus, "progression_family": development_block.progression_family, "current_progression_step": development_block.current_progression_step, "completed_key_exposures": development_block.completed_key_exposures, "retest_state": development_block.retest_state, "completion_state": development_block.completion_state},
        "workout_selection_explanations": {"WHY_THIS_ROLE": "placement role is fixed by M11A-D", "WHY_THIS_WORKOUT": "deterministic compatible selector ranking", "ACTIVE_TRAINING_FOCUS": focus.primary, "TEST_EVIDENCE_USED": performance.source_test_ids or ("none",), "RACE_CONTEXT_USED": focus.race_type, "PROGRESSION_REASON": selection_context["progression_reason"], "VARIETY_REASON": "recent final fingerprints penalize exact/equivalent repetition", "REPETITION_REASON": "only explicit/no-alternative reasons", "RECENT_HISTORY_CONSIDERED": len(selector_history)},
        "replacement_comparison": {
            "needs_generated": len(effective_needs), "needs_placed": len({item.training_need_id for item in placed.placements if item.authority_status is PlacementAuthorityStatus.PROVISIONAL}),
            "needs_unplaced": len(placed.diagnostics.unplaced_needs),
            "role_distribution": {role.value: sum(item.role is role for item in placed.placements) for role in TrainingRole},
            "completed_history_preserved": len(context.historical_facts),
            "authoritative_commitments_preserved": len(context.authoritative_commitments),
            "race_commitments": sum(item.role is TrainingRole.RACE for item in placed.placements),
            "low_intensity_exposures": sum(item.role in {TrainingRole.DEDICATED_UT2, TrainingRole.LONG_AEROBIC, TrainingRole.RECOVERY_AEROBIC, TrainingRole.TECHNICAL_REENTRY} for item in placed.placements),
            "strength_exposures": sum(item.role is TrainingRole.STRENGTH for item in placed.placements),
            "quality_exposures": sum(item.role in {TrainingRole.QUALITY, *tuple(_EXPLICIT_QUALITY)} for item in placed.placements),
            "recovery_conflicts": sum(
                1 for item in placed.placements
                if item.constraint_decision and item.constraint_decision.recovery_status != "ordinary"
            ),
            "materialization_failures": 0,
        },
        "recent_completed_load": {
            "7d": {
                "rowing_minutes": state.recent_7d_load.rowing_minutes,
                "low_intensity_rowing_minutes": state.recent_7d_load.low_intensity_rowing_minutes,
                "quality_exposures": state.recent_7d_load.quality_exposures,
                "strength_exposures": state.recent_7d_load.strength_exposures,
                "race_exposures": state.recent_7d_load.race_exposures,
                "optional_activity_minutes": state.recent_7d_load.optional_activity_minutes,
                "historical_facts_excluded_outside_horizon": len(state.recent_7d_load.excluded_outside_window_fact_ids),
                "duplicate_facts_excluded": state.recent_7d_load.duplicate_facts_excluded,
            },
            "14d": {
                "rowing_minutes": state.recent_14d_load.rowing_minutes,
                "low_intensity_rowing_minutes": state.recent_14d_load.low_intensity_rowing_minutes,
                "quality_exposures": state.recent_14d_load.quality_exposures,
                "strength_exposures": state.recent_14d_load.strength_exposures,
                "race_exposures": state.recent_14d_load.race_exposures,
                "optional_activity_minutes": state.recent_14d_load.optional_activity_minutes,
                "historical_facts_excluded_outside_horizon": len(state.recent_14d_load.excluded_outside_window_fact_ids),
                "duplicate_facts_excluded": state.recent_14d_load.duplicate_facts_excluded,
            },
        },
        "repeated_exposure_needs": {
            "generated_by_role": repeated_generated,
            "placed_by_role": dict(placed.diagnostics.repeated_needs_placed_by_role),
            "unplaced_by_role": dict(placed.diagnostics.repeated_needs_unplaced_by_role),
        },
        "training_load_targets": tuple({
            "id": item.id, "horizon_days": item.horizon_days, "phase": item.phase,
            "minimum_rowing_minutes": item.minimum_rowing_minutes,
            "target_rowing_minutes": item.total_rowing_target_minutes,
            "maximum_rowing_minutes": item.maximum_rowing_minutes,
            "low_intensity_minimum_minutes": item.low_intensity_minimum_minutes,
            "completed_rowing_minutes": item.completed_rowing_minutes,
            "authoritative_future_rowing_minutes": item.authoritative_future_rowing_minutes,
            "selected_categorical_rowing_minutes": item.selected_categorical_rowing_minutes,
            "selected_residual_rowing_minutes": item.selected_residual_rowing_minutes,
            "total_selected_rowing_minutes": item.provisional_planned_rowing_minutes,
            "planned_rowing_minutes": item.provisional_planned_rowing_minutes,
            "selected_categorical_low_intensity_minutes": item.selected_categorical_low_intensity_minutes,
            "selected_residual_low_intensity_minutes": item.selected_residual_low_intensity_minutes,
            "total_selected_low_intensity_minutes": item.planned_low_intensity_minutes,
            "planned_low_intensity_minutes": item.planned_low_intensity_minutes,
            "quality_exposures": item.completed_quality_exposures,
            "strength_exposures": item.completed_strength_exposures,
            "remaining_rowing_minutes": item.remaining_rowing_minutes,
            "remaining_low_intensity_minutes": item.remaining_low_intensity_minutes,
            "availability_ceiling_minutes": item.availability_ceiling_minutes,
            "overage_minutes": item.overage_minutes,
            "overage_sources": item.overage_sources,
            "suppressed_residual_due_to_shorter_horizon_maximum": item.suppressed_residual_due_to_shorter_horizon_maximum,
            "low_intensity_conflict_with_maximum": item.low_intensity_conflict_with_maximum,
            "taper_modifier": item.taper_modifier, "recovery_modifier": item.recovery_modifier,
            "unmet_target_reason": (
                "availability_limited" if item.availability_ceiling_minutes < item.minimum_rowing_minutes
                else "low_intensity_conflict_with_maximum" if item.low_intensity_conflict_with_maximum
                else "residual_needs_unplaced" if item.remaining_rowing_minutes or item.remaining_low_intensity_minutes
                else None
            ),
        } for item in final_load_targets),
        "training_distribution_targets": tuple({
            "horizon_days": item.horizon_days,
            "minimum_rowing_exposures": item.minimum_rowing_exposures,
            "target_rowing_exposures": item.target_rowing_exposures,
            "minimum_low_intensity_exposures": item.minimum_low_intensity_exposures,
            "preferred_max_aerobic_gap_days": item.preferred_max_aerobic_gap_days,
            "taper_frequency_retention": item.taper_frequency_retention,
        } for item in distribution_targets),
        "training_distribution": distribution_diagnostics,
        "frequency_redistribution": tuple({
            "distribution_target_exposures": item.distribution_target_exposures,
            "selected_exposures": item.selected_exposures,
            "frequency_deficit": item.frequency_deficit,
            "redistribution_attempted": item.redistribution_attempted,
            "source_need_id": item.source_need_id,
            "original_credit": item.original_credit,
            "partition_credits": item.partition_credits,
            "redistribution_reason": item.redistribution_reason,
            "redistribution_blocked_reason": item.redistribution_blocked_reason,
            "total_credit_conserved": item.total_credit_conserved,
        } for item in redistribution.diagnostics),
        "empty_date_rationales": empty_date_rationales,
        "replacement_placements": tuple({"placement_id": item.placement_id, "date": item.date, "role": item.role.value if item.role else None, "reservation_minutes": item.reservation_minutes, "target_credit": item.target_credit, "served_need_ids": item.served_need_ids} for item in placed.placements),
    }
    started = perf_counter()
    assembled = build_plan_version_from_v2(
        profile=dict(profile), bands=bands, power=dict(power), calendar=calendar,
        requests=materialized.requests, rest_dates=materialized.rest_dates,
        # Existing V2 assembly receives only its active calendar.  Immutable
        # pre-planning history is merged below, rather than pretending it is a
        # new active authoritative session with a date outside that calendar.
        authoritative_sessions=serialized_locks, historical_calendar_days={},
        v2_diagnostics=diagnostics, selector_history_seed=selector_history, chronological_selector_history=True,
    )
    timings.append(("plan_assembly", round((perf_counter() - started) * 1000, 3)))
    if not assembled.success:
        failure = dict(assembled.failure_diagnostics)
        diagnostic = MaterializationDiagnostic(
            "duration_exceeds_reservation" if assembled.failure_reason == "concrete_duration_exceeds_reserved_capacity" else "no_eligible_archetype" if assembled.failure_reason == "no_eligible_materialization_archetype" else "planversion_contract_failure",
            failure.get("placement_id"), date.fromisoformat(failure["date"]) if failure.get("date") else None,
            failure.get("role"), assembled.failure_reason,
        )
        return ReplacementPlanCandidate(False, None, context, state, effective_needs, placed.placements, placed.diagnostics, (diagnostic,), assembled.failure_reason, tuple(timings))
    plan = dict(assembled.plan)
    active_dates = {item["date"] for item in plan["calendar_days"]}
    active_session_keys = {(item["date"], item["session_id"], item["mode"]) for item in plan["sessions"]}
    preserved_sessions = [
        item for item in historical_sessions
        if (item.get("date"), item.get("session_id"), item.get("mode")) not in active_session_keys
    ]
    plan["sessions"] = sorted([*plan["sessions"], *preserved_sessions], key=_sort_key)
    plan["weekly_totals"] = _totals(plan["sessions"])
    plan["calendar_days"] = sorted(
        [*plan["calendar_days"], *(item for item in historical_calendar.values() if item["date"] not in active_dates)],
        key=lambda item: item["date"],
    )
    from .planner_v2 import validate_v2_plan_contract_details
    started = perf_counter()
    contract_error = validate_v2_plan_contract_details(plan, authoritative_sessions=historical_sessions)
    timings.append(("validation", round((perf_counter() - started) * 1000, 3)))
    if contract_error:
        diagnostic = MaterializationDiagnostic("planversion_contract_failure", detail=str(contract_error.get("reason_code")))
        return ReplacementPlanCandidate(False, None, context, state, effective_needs, placed.placements, placed.diagnostics, (diagnostic,), diagnostic.detail, tuple(timings))
    return ReplacementPlanCandidate(True, plan, context, state, effective_needs, placed.placements, placed.diagnostics, (), None, tuple(timings))
