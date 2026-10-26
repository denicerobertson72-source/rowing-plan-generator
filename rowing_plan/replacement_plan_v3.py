"""Parallel replacement-planner materialization and PlanVersion adapter.

This module deliberately owns no persistence, API routing, date placement, or
workout catalog.  It converts M7's already chosen abstract placements into the
existing V2 materialization and PlanVersion-shaped interfaces for shadow use.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
from .planning_normalization import NormalizedPlanningContext, normalize_planning_inputs
from .scheduler_v2 import finalize_translated_quality_sequence, translate_quality_role
from .training_needs import generate_training_need_plan
from .training_policy import DEFAULT_TRAINING_POLICY, TrainingPolicy


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


_ROW_ROLE_MAP = {
    TrainingRole.DEDICATED_UT2: "dedicated_ut2",
    TrainingRole.LONG_AEROBIC: "long_aerobic",
    TrainingRole.UT1_AEROBIC_STRENGTH: "ut1_aerobic_strength",
    # The existing selector has no separate recovery/technical catalog.  Both
    # safely reuse its low-intensity UT2 path; role/provenance remain explicit.
    TrainingRole.TECHNICAL_REENTRY: "dedicated_ut2",
    TrainingRole.RECOVERY_AEROBIC: "dedicated_ut2",
    TrainingRole.TAPER_MAINTENANCE: "dedicated_ut2",
}
_EXPLICIT_QUALITY = {
    TrainingRole.THRESHOLD_AT: "AT",
    TrainingRole.RACE_SPECIFIC_TR: "TR",
    TrainingRole.ANAEROBIC_AN: "AN",
    TrainingRole.POWER_PP: "PP",
    TrainingRole.RACE_PREPARATION: "TR",
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
    placements: tuple[Placement, ...] | list[Placement], *, profile: Mapping[str, Any],
) -> PlacementMaterializationResult:
    """Map placed needs to the existing V2 request/quality pipeline.

    This adapter cannot select a date or alter target credit.  Every resulting
    request carries the M7 reservation as its existing downstream capacity.
    """
    ordered = tuple(sorted(placements, key=lambda item: (item.date, item.placement_id)))
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
        if role in {TrainingRole.RACE, TrainingRole.COURSE_PRACTICE}:
            request_role = "race" if role is TrainingRole.RACE else "course_practice"
            fact = _race_fact(profile, placement.date) if role is TrainingRole.RACE else _practice_fact(profile, placement.date)
            requests.append(V2SessionMaterializationRequest(request_role, placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=True, fact=fact))
            continue
        if role is TrainingRole.PRIVATE_COACHING:
            requests.append(V2SessionMaterializationRequest("private_coaching", placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=True))
            continue
        if role is TrainingRole.COACHED_TRAINING:
            requests.append(V2SessionMaterializationRequest("coached_training", placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=fixed))
            continue
        if role is TrainingRole.STRENGTH:
            requests.append(V2SessionMaterializationRequest("strength", placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=fixed))
            continue
        if role in _ROW_ROLE_MAP:
            requests.append(V2SessionMaterializationRequest(_ROW_ROLE_MAP[role], placement.date, placement.reservation_minutes, phase_id, placement_id=placement.placement_id, source_id=placement.training_need_id, fixed=fixed))
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
            race_types={item.placement_id: str(_race_fact(profile, item.date).get("race_type", "general")) for item, *_ in quality},
            race_priorities={item.placement_id: str(_race_fact(profile, item.date).get("priority", "B")) for item, *_ in quality},
            preference=str(profile.get("preferences", {}).get("workout_structure_preference", "varied")),
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
    context = normalize_planning_inputs(
        profile, planning_date=planning_date, prior_plan=prior_plan,
        history_actuals=history_actuals, locked_sessions=locked_sessions,
        future_placements=future_placements,
    )
    state = reduce_physiological_state(context, profile=profile)
    need_plan = generate_training_need_plan(context, state, policy, profile=profile)
    effective_needs = tuple(needs) if needs is not None else need_plan.needs
    placed = place_continuous_training_needs(
        context, state, effective_needs, policy, profile=profile, config=placement_config,
    )
    materialized = adapt_placements_to_materialization_requests(placed.placements, profile=profile)
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
        return ReplacementPlanCandidate(False, None, context, state, effective_needs, placed.placements, placed.diagnostics, all_materialization_diagnostics, all_materialization_diagnostics[0].code)
    calendar = _calendar_from_placement_result(placed, profile)
    historical_calendar = {
        item["date"]: dict(item) for item in (prior_plan or {}).get("calendar_days", ())
        if isinstance(item, Mapping) and isinstance(item.get("date"), str) and item["date"] < planning_date.isoformat()
    }
    historical_sessions = tuple(
        dict(item) for item in (prior_plan or {}).get("sessions", ())
        if isinstance(item, Mapping) and isinstance(item.get("date"), str) and item["date"] < planning_date.isoformat()
    )
    diagnostics = {
        "scheduler_version": "replacement-v3-shadow",
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
        "replacement_placements": tuple({"placement_id": item.placement_id, "date": item.date, "role": item.role.value if item.role else None, "reservation_minutes": item.reservation_minutes, "target_credit": item.target_credit, "served_need_ids": item.served_need_ids} for item in placed.placements),
    }
    assembled = build_plan_version_from_v2(
        profile=dict(profile), bands=bands, power=dict(power), calendar=calendar,
        requests=materialized.requests, rest_dates=materialized.rest_dates,
        # Existing V2 assembly receives only its active calendar.  Immutable
        # pre-planning history is merged below, rather than pretending it is a
        # new active authoritative session with a date outside that calendar.
        authoritative_sessions=serialized_locks, historical_calendar_days={},
        v2_diagnostics=diagnostics,
    )
    if not assembled.success:
        failure = dict(assembled.failure_diagnostics)
        diagnostic = MaterializationDiagnostic(
            "duration_exceeds_reservation" if assembled.failure_reason == "concrete_duration_exceeds_reserved_capacity" else "no_eligible_archetype" if assembled.failure_reason == "no_eligible_materialization_archetype" else "planversion_contract_failure",
            failure.get("placement_id"), date.fromisoformat(failure["date"]) if failure.get("date") else None,
            failure.get("role"), assembled.failure_reason,
        )
        return ReplacementPlanCandidate(False, None, context, state, effective_needs, placed.placements, placed.diagnostics, (diagnostic,), assembled.failure_reason)
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
    contract_error = validate_v2_plan_contract_details(plan, authoritative_sessions=historical_sessions)
    if contract_error:
        diagnostic = MaterializationDiagnostic("planversion_contract_failure", detail=str(contract_error.get("reason_code")))
        return ReplacementPlanCandidate(False, None, context, state, effective_needs, placed.placements, placed.diagnostics, (diagnostic,), diagnostic.detail)
    return ReplacementPlanCandidate(True, plan, context, state, effective_needs, placed.placements, placed.diagnostics)
