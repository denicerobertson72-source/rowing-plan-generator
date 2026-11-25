"""Pure, non-persistent end-to-end inspection of the reset V3 spine."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from time import perf_counter
from typing import Any, Callable, Mapping

from .materialization_v3 import MaterializationContext, materialize_training_days_v3
from .development_recommendation import build_development_recommendation
from .performance_profile import build_athlete_performance_profile, derive_performance_limiter, derive_training_focus
from .physiological_evidence import build_physiological_evidence_profile
from .planning_profile import normalize_available_training_capacity, normalize_current_training_state
from .planning_normalization import normalize_planning_inputs
from .recommended_training_dose import r2_baseline_evidence, recent_actual_history_is_sufficient
from .generation_history import recover_generation_history
from .replacement_plan_v3 import generate_recommended_training_dose_v3
from .rowing_quality_v3 import CompletedQualityExposure, RowingQualityPlanningContext, generate_rowing_quality_plan
from .training_allocation import (
    generate_training_allocation_v3,
    weekly_evidence_for_allocation,
)
from .periodization import build_season_phases, race_dates
from .physiological_state import reduce_physiological_state
from .planning_domain import (
    HistoricalFactType, PhysicalCategory, RowingQualityInfeasibility,
    RowingQualityPlanResult, TrainingModality, TrainingRole,
)
from .training_days import CombinedDayPreferences, DailyTrainingCapacity, generate_training_days_v3


@dataclass(frozen=True)
class PersistedDiagnosticInput:
    """Read-only persistence snapshot; it deliberately has no save operation."""

    athlete_id: str
    profile: Mapping[str, Any]
    prior_plan_id: str | None
    prior_plan: Mapping[str, Any] | None
    workout_logs: tuple[Mapping[str, Any], ...]
    workout_logs_by_plan: Mapping[str, tuple[Mapping[str, Any], ...]]
    weekly_overrides: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class DatedSegmentContext:
    """Auditable, non-prescriptive context for one exact R9B cadence segment.

    R2 retains physical-dose authority and R3 retains category authority.  This
    value only supplies the already-validated dated phase/race/recovery facts
    needed by R3 envelope checks and R6--R5 composition/materialization.
    """

    allocation_segment_id: str
    start_date: date
    end_date: date
    phase_id: str | None
    phase_provenance: str
    phase_reason: str | None
    phase_span_count: int
    race_type: str = "general"
    race_dates: tuple[date, ...] = ()
    days_to_race: int | None = None
    race_count: int = 0
    recovery_active: bool = False
    recovery_constraint_codes: tuple[str, ...] = ()
    fixed_rest_dates: tuple[date, ...] = ()
    flexible_rest_sessions_per_week: int = 0
    completed_quality_exposure_count: int = 0
    completed_strength_exposure_count: int = 0
    completed_race_exposure_count: int = 0
    evidence_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.end_date < self.start_date or self.phase_span_count < 0 or self.race_count < 0:
            raise ValueError("dated_segment_context_values_are_invalid")
        object.__setattr__(self, "race_dates", tuple(sorted(set(self.race_dates))))
        object.__setattr__(self, "recovery_constraint_codes", tuple(sorted(set(self.recovery_constraint_codes))))
        object.__setattr__(self, "fixed_rest_dates", tuple(sorted(set(self.fixed_rest_dates))))
        object.__setattr__(self, "evidence_limitations", tuple(sorted(set(self.evidence_limitations))))

    @property
    def operational_phase_id(self) -> str:
        """A materializer-safe phase only when dated phase evidence is singular."""
        return self.phase_id or "general_preparation"


def load_persisted_diagnostic_input(
    repository: Any, athlete_id: str, *, profile: Mapping[str, Any] | None = None,
) -> PersistedDiagnosticInput:
    """Load the same persisted facts used by generation without invoking writes."""
    loaded_profile = profile if isinstance(profile, Mapping) else repository.get(athlete_id)
    if not isinstance(loaded_profile, Mapping):
        raise ValueError("diagnostic_athlete_not_found")
    latest = repository.latest_plan_for_athlete(athlete_id)
    plan = latest.get("plan") if isinstance(latest, Mapping) and isinstance(latest.get("plan"), Mapping) else None
    plan_id = latest.get("plan_id") if isinstance(latest, Mapping) else None
    context = plan.get("generation_input_context", {}) if isinstance(plan, Mapping) else {}
    inherited_actual_plan_ids = context.get("actual_plan_ids", ()) if isinstance(context, Mapping) else ()
    source_ids = tuple(dict.fromkeys(
        [str(item) for item in inherited_actual_plan_ids if item] + ([str(plan_id)] if plan_id else [])
    ))
    logs_by_plan = {source_id: tuple(repository.logs_for_plan(source_id)) for source_id in source_ids}
    logs = tuple(entry for source_id in source_ids for entry in logs_by_plan[source_id])
    overrides = repository.weekly_overrides(athlete_id)
    return PersistedDiagnosticInput(
        athlete_id, dict(loaded_profile), str(plan_id) if plan_id else None,
        dict(plan) if plan else None, logs, logs_by_plan, tuple(overrides),
    )


def _enum(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _diagnostic_preferences(profile: Mapping[str, Any]) -> CombinedDayPreferences:
    raw = profile.get("training_preferences", {})
    raw = raw if isinstance(raw, Mapping) else {}
    maximum = raw.get("maximum_combined_days_per_week")
    return CombinedDayPreferences(
        combined_days=str(raw.get("combined_days", "allowed")),
        strength_plus_easy_aerobic=str(raw.get("strength_plus_easy_aerobic", "allowed")),
        strength_plus_hard_rowing=str(raw.get("strength_plus_hard_rowing", "avoid")),
        maximum_combined_days_per_week=maximum if isinstance(maximum, int) else None,
    )


def _r4_status_payload(result) -> dict[str, Any]:
    """Expose R4 failure evidence without serializing its internal objects."""
    return {
        "success": result.success,
        "infeasibilities": [
            {
                "code": item.code,
                "allocation_segment_id": item.allocation_segment_id,
                "physical_category": item.physical_category.value if item.physical_category else None,
                "remaining_minutes": item.remaining_minutes,
                "detail": item.detail,
            }
            for item in result.infeasibilities
        ],
        "diagnostics": [
            {
                "code": item.code,
                "date": item.date.isoformat() if item.date else None,
                "source_lineage_id": item.source_lineage_id,
                "detail": item.detail,
            }
            for item in result.diagnostics
        ],
    }


def _rowing_modalities(capacity) -> tuple[TrainingModality, ...]:
    values = tuple(sorted({mode for day in capacity.days for mode in day.allowed_modalities
                           if mode in {TrainingModality.ERG, TrainingModality.ON_WATER}}, key=lambda item: item.value))
    return values or (TrainingModality.ERG, TrainingModality.ON_WATER)


def _segment_id(start: date, end: date) -> str:
    return f"r3:{start.isoformat()}:{end.isoformat()}"


def _quality_band(fact: Any) -> str | None:
    actual = fact.completed_actual or {}
    planned = fact.planned_prescription or {}
    value = actual.get("actual_intensity") or actual.get("intensity_band") or actual.get("band") or planned.get("band")
    band = str(value).upper() if value else None
    return band if band in {"AT", "TR", "AN", "PP"} else None


def _completed_quality_exposures(facts) -> tuple[CompletedQualityExposure, ...]:
    """Adapt completed facts into R6 spacing evidence without inventing credit."""
    values = []
    for fact in facts:
        if fact.type is HistoricalFactType.RACE_FACT:
            values.append(CompletedQualityExposure(fact.date, "RACE", fact.physiological_credit.minutes, is_race=True))
            continue
        if fact.completed_actual is None or fact.physiological_credit.role is not TrainingRole.QUALITY:
            continue
        values.append(CompletedQualityExposure(
            fact.date, _quality_band(fact) or "UNCERTAIN_QUALITY", fact.physiological_credit.minutes,
        ))
    return tuple(sorted(values, key=lambda item: (item.completed_date, item.physiological_band, item.work_minutes, item.is_race)))


def _flexible_rest_sessions(profile: Mapping[str, Any]) -> int:
    activities = profile.get("recurring_activities", ())
    if not isinstance(activities, (tuple, list)):
        return 0
    return sum(
        int(item.get("sessions_per_week", 0) or 0)
        for item in activities
        if isinstance(item, Mapping)
        and item.get("activity_type") == "rest"
        and item.get("scheduling_status") == "flexible"
    )


def _segment_contexts(
    profile: Mapping[str, Any], *, segments, state, days,
) -> tuple[DatedSegmentContext, ...]:
    """Derive dated context from existing periodization and history facts.

    R2 already splits at season/recovery boundaries.  The defensive
    multi-phase branch deliberately emits a limitation rather than choosing a
    phase silently; later policy can define how a mixed segment should behave.
    """
    phases = build_season_phases(dict(profile))
    races = tuple(item for item in profile.get("races", ()) if isinstance(item, Mapping))
    fixed_rest = {item.date for item in days if item.fixed_rest}
    flexible_rest = _flexible_rest_sessions(profile)
    contexts = []
    for segment in segments:
        overlaps = [
            item for item in phases
            if item["start_date"] <= segment.end_date.isoformat()
            and item["end_date"] >= segment.start_date.isoformat()
        ]
        phase_types = tuple(sorted({str(item["phase_type"]) for item in overlaps}))
        limitation = []
        if len(phase_types) == 1:
            phase_id = phase_types[0]
            phase_provenance = "season_phase"
            phase_reason = str(overlaps[0].get("reason") or "")
        elif not overlaps:
            phase_id = None
            phase_provenance = "fallback_missing_season_phase"
            phase_reason = None
            limitation.append("no_season_phase_for_segment")
        else:
            phase_id = None
            phase_provenance = "unresolved_multi_phase_segment"
            phase_reason = None
            limitation.append("multi_phase_segment_requires_policy")
        dated_races = tuple(sorted({
            race_day
            for race in races for race_day in race_dates(dict(race))
            if segment.start_date <= race_day <= segment.end_date
        }))
        future_races = tuple(sorted(
            (
                (race_day, race)
                for race in races for race_day in race_dates(dict(race))
                if race_day >= segment.start_date
            ),
            key=lambda item: (item[0], str(item[1].get("event_name", "race")), str(item[1].get("race_type", ""))),
        ))
        next_race_day, next_race = future_races[0] if future_races else (None, None)
        recovery = tuple(
            item for item in state.recovery_constraints
            if item.start_date <= segment.end_date and item.end_date >= segment.start_date
        )
        if phase_id is None:
            limitation.append("materialization_phase_fallback_general_preparation")
        if not races:
            limitation.append("no_race_dates_configured")
        contexts.append(DatedSegmentContext(
            _segment_id(segment.start_date, segment.end_date), segment.start_date, segment.end_date,
            phase_id, phase_provenance, phase_reason, len(phase_types),
            race_type=str((next_race or {}).get("race_type") or "general"),
            race_dates=dated_races,
            days_to_race=((next_race_day - segment.start_date).days if next_race_day else None),
            race_count=len(dated_races),
            recovery_active=bool(recovery) or phase_id == "post_race_recovery",
            recovery_constraint_codes=tuple(item.rationale or item.source for item in recovery),
            fixed_rest_dates=tuple(day for day in fixed_rest if segment.start_date <= day <= segment.end_date),
            flexible_rest_sessions_per_week=flexible_rest,
            completed_quality_exposure_count=state.completed_quality_exposure.exposures,
            completed_strength_exposure_count=state.completed_strength_exposure.exposures,
            completed_race_exposure_count=state.completed_race_exposure.exposures,
            evidence_limitations=tuple(limitation),
        ))
    return tuple(contexts)


def _context_payload(context: DatedSegmentContext) -> dict[str, Any]:
    return {
        "allocation_segment_id": context.allocation_segment_id,
        "start_date": context.start_date.isoformat(), "end_date": context.end_date.isoformat(),
        "phase_id": context.phase_id, "phase_provenance": context.phase_provenance,
        "phase_reason": context.phase_reason, "phase_span_count": context.phase_span_count,
        "race_context": {
            "race_type": context.race_type, "race_dates": [item.isoformat() for item in context.race_dates],
            "days_to_race": context.days_to_race, "race_count": context.race_count,
        },
        "recovery_context": {
            "active": context.recovery_active,
            "constraint_codes": list(context.recovery_constraint_codes),
            "fixed_rest_dates": [item.isoformat() for item in context.fixed_rest_dates],
            "flexible_rest_sessions_per_week": context.flexible_rest_sessions_per_week,
        },
        "completed_exposure_evidence": {
            "quality_exposures": context.completed_quality_exposure_count,
            "strength_exposures": context.completed_strength_exposure_count,
            "race_exposures": context.completed_race_exposure_count,
        },
        "evidence_limitations": list(context.evidence_limitations),
    }


def _r6_result_payload(result, context: DatedSegmentContext) -> dict[str, Any]:
    plan = result.plan
    infeasibilities = [
        {"code": item.code, "allocation_segment_id": item.allocation_segment_id, "detail": dict(item.detail)}
        for item in result.infeasibilities
    ]
    if result.infeasibilities:
        outcome = "infeasible"
    elif (
        context.recovery_active or context.recovery_constraint_codes
        or context.phase_id in {"race", "post_race_recovery"}
    ):
        outcome = "intentional_phase_policy_suppression"
    elif plan is not None and not plan.requirements:
        outcome = "no_quality_requirement_generated"
    else:
        outcome = "requirements_generated"
    return {
        "success": result.success,
        "outcome": outcome,
        "plan": asdict(plan) if plan is not None else None,
        "infeasibilities": infeasibilities,
        "diagnostics": {
            "phase_id": context.phase_id,
            "phase_provenance": context.phase_provenance,
            "recovery_active": context.recovery_active,
            "r3_rowing_allocation_minutes": plan.rowing_specific_minutes if plan else None,
        },
    }


def render_v3_diagnostic_report(report: Mapping[str, Any]) -> str:
    """Render the pure report without adding a second planning representation."""
    lines = ["ATHLETE CONTEXT", f"Planning block: {report['planning_date']} to {report['planning_end']}",
             f"Available capacity: {report['available_capacity_minutes']} min"]
    current = report["current_training"]
    r2_evidence = report.get("r2_baseline_evidence", {})
    lines += [f"Current evidence: source={current['source']} total={current['approximate_total_physical_minutes']} rowing={current['approximate_rowing_minutes']} rowing exposures={current['rowing_exposures']} strength exposures={current['strength_exposures']}",
              "", "R2 TOTAL DOSE", f"Recommended: {report['r2']['recommended_physical_minutes']} min; selected baseline={r2_evidence.get('selected_baseline_weekly_minutes')} source={r2_evidence.get('selected_baseline_source')}; capacity is a ceiling, not a quota."]
    evidence = report.get("r3_weekly_evidence", {})
    lines += [
        "", "R3 WEEKLY EVIDENCE",
        f"Observation window: {evidence.get('observation_window_days')}; raw rowing={evidence.get('raw_observation_rowing_minutes')} raw strength exposures={evidence.get('raw_observation_strength_exposures')}; normalized weekly rowing={evidence.get('weekly_rowing_minutes')} strength exposures={evidence.get('weekly_strength_exposures')}",
        "", "R3 ALLOCATION",
    ]
    physiological_evidence = report.get("physiological_evidence", {})
    if physiological_evidence:
        lines += ["", "PHYSIOLOGICAL MEASUREMENT EVIDENCE"]
        lines.append(
            "Capability: " + ", ".join(physiological_evidence.get("capability_description", ()))
        )
        lines.append(
            "Legacy limiter remains a compatibility heuristic; it is not a validated physiological diagnosis."
        )
        for item in physiological_evidence.get("measurements", ()):
            lines.append(
                f"{item['measurement_id']}: {item['protocol']} modality={item['modality']} "
                f"date={item['assessment_date']} validity={item['validity']} intent={item['effort_intent']}"
            )
        for item in physiological_evidence.get("domains", ()):
            lines.append(
                f"{item['domain']}: {item['evidence_status']} "
                f"measurements={item['measurement_ids']} age_days={item['ages_days']} "
                f"comparability={item['comparability']}"
            )
    recommendation = report.get("development_recommendation", {})
    if recommendation:
        lines += ["", "EVIDENCE-INFORMED DEVELOPMENTAL RECOMMENDATION"]
        lines.append(
            f"Primary={recommendation['primary_emphasis']}; secondary={recommendation['secondary_emphasis']}; "
            f"type={recommendation['recommendation_type']}; confidence={recommendation['confidence']}"
        )
        lines.append("Diagnostic-only: R6 prescription remains unchanged on its legacy path.")
        if recommendation.get("next_most_informative_assessments"):
            lines.append("Assessment considerations: " + ", ".join(
                item["protocol"] for item in recommendation["next_most_informative_assessments"]
            ))
    for segment in report["r3"]["segments"]:
        lines.append(f"{segment['start_date']}–{segment['end_date']}: total={segment['recommended_physical_minutes']} rowing={segment['rowing_specific_minutes']} strength={segment['strength_minutes']} ({segment['recommended_strength_exposures']}×{segment['expected_strength_duration_minutes']}) general={segment['general_aerobic_minutes']} other={segment['other_physical_minutes']}")
    lines += ["", "DATED PHASE / RECOVERY CONTEXT"]
    for item in report.get("dated_context", ()):
        race = item["race_context"]
        recovery = item["recovery_context"]
        lines.append(
            f"{item['start_date']}–{item['end_date']}: phase={item['phase_id'] or 'unresolved'} "
            f"({item['phase_provenance']}); race_days={race['race_dates']}; "
            f"recovery={recovery['active']}; fixed_rest={recovery['fixed_rest_dates']}"
        )
    lines += ["", "R6 QUALITY"]
    for result in report.get("r6_results", ()):
        plan = result.get("plan")
        lines.append(f"{result['outcome']}: success={result['success']} infeasibilities={[item['code'] for item in result['infeasibilities']]}")
        if plan is None:
            continue
        lines.append(f"R3 rowing physical={plan['rowing_specific_minutes']} low-intensity guard={plan['low_intensity_guard_minutes']}")
        for requirement in plan["requirements"]:
            lines.append(f"{requirement['priority']} {requirement['physiological_band']}: {requirement['desired_work_minutes']} work min, exposures={requirement['target_exposures']}, physical envelopes={requirement['compatible_envelopes']}")
    lines += ["", "R4 CALENDAR"]
    for day in report["calendar"]:
        lines.append(f"{day['date']} {day['state']}: planned={day['planned_minutes']}/{day['available_minutes']} remaining={day['remaining_minutes']}")
        for component in day["components"]:
            lines.append(f"  #{component['sequence']} {component['category']} {component['purpose']} {component['physical_minutes']}m modality={component['modality'] or 'unresolved'} quality_work={component['quality_work_minutes'] or '-'} authority={component['authority']}")
    lines += ["", "R5 WORKOUTS"]
    for item in report["workouts"]:
        lines.append(f"{item['component_id']} {item['date']} {item['purpose']} {item['physical_minutes']}m: {item['status']} {item['archetype_or_placeholder']}")
    ledger = report["conservation"]
    lines += ["", "CONSERVATION", f"R2={ledger['r2']} R3={ledger['r3']} R4={ledger['r4']} R5={ledger['r5']} PASS={ledger['pass']}",
              "Target credit used for physical conservation: NO", "Reservation minutes used for physical conservation: NO",
              "", "TRAINING PLAUSIBILITY REVIEW"]
    for name, value in report["plausibility_reviews"].items(): lines.append(f"{name}: {value['rating']} — {value['facts']}")
    return "\n".join(lines)


def generate_v3_diagnostic_report(
    profile: Mapping[str, Any], *, planning_date: date,
    prior_plan: Mapping[str, Any] | None = None,
    history_actuals: Mapping[str, Mapping[str, Any]] | None = None,
    locked_sessions=(), future_placements=(),
    diagnostic_timings_ms: dict[str, float] | None = None,
    diagnostic_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Run R1B→R7B without routing, persistence, or legacy planner execution."""
    def record_timing(name: str, started: float) -> None:
        if diagnostic_timings_ms is not None:
            diagnostic_timings_ms[name] = round((perf_counter() - started) * 1000, 3)

    r2_started = perf_counter()
    r2 = generate_recommended_training_dose_v3(
        profile, planning_date=planning_date, prior_plan=prior_plan,
        history_actuals=history_actuals, locked_sessions=locked_sessions,
        future_placements=future_placements,
    )
    record_timing("r2_recommended_dose", r2_started)
    capacity = normalize_available_training_capacity(profile)
    rowing_modalities = _rowing_modalities(capacity)
    preferences = _diagnostic_preferences(profile)
    by_weekday = {item.day_key: item for item in capacity.days}
    days = tuple(
        DailyTrainingCapacity(
            r2.planning_start + timedelta(days=index),
            by_weekday[(r2.planning_start + timedelta(days=index)).strftime("%A").lower()].usable_minutes,
            available=by_weekday[(r2.planning_start + timedelta(days=index)).strftime("%A").lower()].available,
            fixed_rest=by_weekday[(r2.planning_start + timedelta(days=index)).strftime("%A").lower()].fixed_rest,
        )
        for index in range((r2.planning_end - r2.planning_start).days + 1)
    )
    normalization_started = perf_counter()
    normalized = normalize_planning_inputs(
        profile, planning_date=r2.planning_start, prior_plan=prior_plan,
        history_actuals=history_actuals, locked_sessions=locked_sessions,
        future_placements=future_placements,
    )
    current = normalize_current_training_state(
        profile, historical_facts=normalized.historical_facts,
        recent_actuals_sufficient=recent_actual_history_is_sufficient(
            normalized.historical_facts, planning_start=r2.planning_start,
        ), observation_window_days=21,
    )
    state = reduce_physiological_state(normalized, profile=profile)
    baseline_evidence = r2_baseline_evidence(
        profile, current, normalized.historical_facts, planning_start=r2.planning_start,
    )
    segment_contexts = _segment_contexts(profile, segments=r2.segments, state=state, days=days)
    segment_context_by_id = {item.allocation_segment_id: item for item in segment_contexts}
    materialization_context = MaterializationContext(
        phase_id="general_preparation",
        experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")),
        available_rowing_modalities=rowing_modalities,
        segment_phase_contexts=tuple(
            (item.allocation_segment_id, item.operational_phase_id, item.race_type)
            for item in segment_contexts
        ),
    )
    record_timing("current_training_history_normalization", normalization_started)
    r3_started = perf_counter()
    r3_result = generate_training_allocation_v3(
        profile, planning_date=planning_date, recommended_dose=r2,
        prior_plan=prior_plan, history_actuals=history_actuals,
        locked_sessions=locked_sessions, future_placements=future_placements,
        cadence_capacity=days,
        cadence_preferences=preferences,
        cadence_materialization_context=materialization_context,
    )
    if r3_result.allocation is None:
        raise ValueError("r3_cadence_category_envelope_infeasible")
    r3 = r3_result.allocation
    record_timing("r3_allocation", r3_started)
    performance_started = perf_counter()
    performance = build_athlete_performance_profile(profile, as_of=r2.planning_start)
    limiter = derive_performance_limiter(performance, coach_focus=profile.get("preferences", {}).get("development_focus"))
    physiological_evidence = build_physiological_evidence_profile(profile, as_of=r2.planning_start)
    development_recommendation = build_development_recommendation(
        profile, evidence=physiological_evidence, planning_date=r2.planning_start,
        phase_context=state.phase_context, recovery_constraints=state.recovery_constraints,
        current_training=current, next_race_context=state.next_race_context,
    )
    record_timing("performance_profile_focus", performance_started)
    r6_started = perf_counter()
    completed_quality = _completed_quality_exposures(normalized.historical_facts)
    quality_results = []
    for segment in r3.segments:
        segment_id = _segment_id(segment.start_date, segment.end_date)
        dated_context = segment_context_by_id[segment_id]
        if dated_context.phase_id is None:
            quality_results.append(RowingQualityPlanResult(
                None,
                (RowingQualityInfeasibility(
                    "multi_phase_segment_phase_policy_unresolved", segment_id,
                    {"phase_provenance": dated_context.phase_provenance,
                     "evidence_limitations": dated_context.evidence_limitations},
                ),),
            ))
            continue
        quality_context = RowingQualityPlanningContext(
            segment.start_date,
            phase_id=dated_context.operational_phase_id,
            race_type=dated_context.race_type,
            days_to_race=dated_context.days_to_race,
            race_count=dated_context.race_count,
            recovery_active=dated_context.recovery_active,
            hard_restriction_codes=dated_context.recovery_constraint_codes,
            experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")),
            available_rowing_modalities=rowing_modalities,
        )
        focus = derive_training_focus(
            limiter, phase=quality_context.phase_id, race_type=quality_context.race_type,
            days_to_race=quality_context.days_to_race,
            recovery_active=quality_context.recovery_active,
        )
        quality_results.append(generate_rowing_quality_plan(
            segment, allocation_segment_id=segment_id,
            performance=performance, limiter=limiter, focus=focus,
            context=quality_context, completed_exposures=completed_quality,
        ))
    quality_results = tuple(quality_results)
    quality = tuple(item.plan for item in quality_results if item.plan is not None)
    record_timing("r6_rowing_quality", r6_started)
    # Winter ERG is an explicit athlete environment, not an auto-selected
    # cross-training modality.
    r4_started = perf_counter()
    r4 = generate_training_days_v3(
        r3, days, rowing_quality_plans=quality,
        materialization_context=materialization_context,
        current_training_state=current,
        preferences=preferences,
    )
    record_timing("r4_training_days", r4_started)
    r4_status = _r4_status_payload(r4)
    r5_started = perf_counter()
    r5 = materialize_training_days_v3(
        r4,
        context=materialization_context,
        allocation=r3,
    )
    record_timing("r5_materialization", r5_started)
    r2_total = r2.recommended_physical_minutes
    r3_total = r3.recommended_physical_minutes
    r4_total = sum(day.total_physical_minutes for day in r4.days)
    r5_total = sum(item.materialized.physical_duration_minutes for item in r5.results if item.success and item.materialized)
    primitive_started = perf_counter()
    calendar = []
    component_dates = {component.component_id: day.date.isoformat() for day in r4.days for component in day.components}
    for day in r4.days:
        calendar.append({"date": day.date.isoformat(), "state": day.state.value, "available_minutes": day.available_minutes,
                         "planned_minutes": day.total_physical_minutes, "remaining_minutes": day.remaining_capacity_minutes,
                         "components": [{"component_id": item.component_id, "sequence": item.sequence, "category": item.physical_category.value,
                                         "purpose": item.purpose.value, "modality": _enum(item.modality), "physical_minutes": item.duration_minutes,
                                         "quality_work_minutes": item.quality_work_minutes, "quality_requirement_id": item.quality_requirement_id,
                                         "authority": item.authority.value, "combined_day": day.combined_day,
                                         "permitted_modalities": [_enum(value) for value in item.permitted_modalities],
                                         "capability_provenance": list(item.capability_provenance),
                                         "rationale_codes": list(item.rationale_codes)} for item in day.components]})
    workouts = []
    for result in r5.results:
        materialized = result.materialized
        workouts.append({"component_id": result.component_id, "date": component_dates.get(result.component_id),
                         "status": "success" if result.success else "failure", "physical_minutes": materialized.physical_duration_minutes if materialized else None,
                         "purpose": materialized.purpose.value if materialized else None,
                         "archetype_or_placeholder": (materialized.catalog_archetype_id or "exact_duration_placeholder") if materialized else result.failure.reason_code,
                         "physiological_work": asdict(materialized.physiological_work) if materialized and materialized.physiological_work else None,
                         "modality": _enum(materialized.modality) if materialized else None,
                         "rationale": list(materialized.selection_rationale_codes) if materialized else []})
    rowing = [component for day in r4.days for component in day.components if component.physical_category is PhysicalCategory.ROWING_SPECIFIC]
    strength = [component for day in r4.days for component in day.components if component.physical_category is PhysicalCategory.STRENGTH]
    reviews = {
        "TOTAL_LOAD": {"rating": "GOOD" if r2_total <= capacity.total_available_minutes else "PROBLEM", "facts": f"{r2_total} recommended vs {current.approximate_total_physical_minutes} current evidence and {capacity.total_available_minutes} capacity"},
        "STRENGTH": {"rating": "QUESTIONABLE" if len(strength) < (current.strength_exposures or 0) else "GOOD", "facts": f"{len(strength)} planned exposures vs {current.strength_exposures} current"},
        "ROWING_FREQUENCY": {"rating": "QUESTIONABLE" if len(rowing) < (current.rowing_exposures or 0) else "GOOD", "facts": f"{len(rowing)} planned exposures vs {current.rowing_exposures} current"},
        "CROSS_TRAINING": {"rating": "GOOD", "facts": "No modality is guessed for unresolved general aerobic work."},
        "AVAILABILITY_USE": {"rating": "GOOD", "facts": f"{r2_total} planned of {capacity.total_available_minutes} available minutes."},
    }
    report = {
        "planning_date": r2.planning_start.isoformat(), "planning_end": r2.planning_end.isoformat(),
        "available_capacity_minutes": sum(day.usable_minutes for day in days),
        "planning_goals": dict(profile.get("planning_goals", {})), "training_preferences": preferences.to_dict(),
        "rowing_modalities": [item.value for item in rowing_modalities],
        "dated_context": [_context_payload(item) for item in segment_contexts],
        "recovery_context_summary": {
            "completed_quality_exposure_count": len(completed_quality),
            "completed_strength_exposure_count": state.completed_strength_exposure.exposures,
            "completed_race_exposure_count": state.completed_race_exposure.exposures,
            "active_recovery_constraint_count": len(state.recovery_constraints),
            "flexible_rest_sessions_per_week": _flexible_rest_sessions(profile),
            "readiness_auto_adjustment": "not_applied",
        },
        "current_training": asdict(current), "r2_baseline_evidence": baseline_evidence,
        "physiological_evidence": {
            **physiological_evidence.to_dict(),
            "legacy_limiter": {
                "mode": limiter.mode,
                "primary_focus": limiter.primary_focus,
                "confidence": limiter.confidence,
                "status": "legacy_compatibility_heuristic_not_physiological_diagnosis",
                "limitations": [
                    "heterogeneous_within_athlete_ratios_are_not_a_common_deficit_scale",
                    "not_used_by_r9f3c_developmental_recommendation",
                ],
            },
        },
        "development_recommendation": development_recommendation.to_dict(),
        "r3_weekly_evidence": weekly_evidence_for_allocation(profile, current), "r2": asdict(r2), "r3": asdict(r3),
        "r3_cadence_feasibility": [
            {
                "allocation_segment_id": f"r3:{segment.start_date.isoformat()}:{segment.end_date.isoformat()}",
                "original": {
                    "rowing_specific_minutes": segment.rowing_specific_minutes + segment.category_reconciliation_minutes,
                    "general_aerobic_minutes": segment.general_aerobic_minutes - segment.category_reconciliation_minutes,
                    "feasible": segment.category_feasibility_original,
                },
                "final": {
                    "rowing_specific_minutes": segment.rowing_specific_minutes,
                    "general_aerobic_minutes": segment.general_aerobic_minutes,
                    "feasible": segment.category_feasibility_final,
                },
                "minutes_transferred_rowing_to_general_aerobic": segment.category_reconciliation_minutes,
                "deliberate_general_aerobic_minutes": segment.deliberate_general_aerobic_minutes,
                "feasibility_reconciled_general_aerobic_minutes": segment.category_reconciliation_minutes,
                "reason": segment.category_reconciliation_reason,
                "eligible_general_aerobic_modalities": [item.modality.value for item in segment.general_aerobic_capabilities],
                "general_aerobic_policy_provenance": list(segment.general_aerobic_policy_provenance),
                "supplemental_max_minutes": segment.general_aerobic_supplemental_max_minutes,
                "supplemental_only": segment.general_aerobic_supplemental_only,
                "protected_rowing_floor_minutes": segment.rowing_specific_floor_minutes,
                "strength_minutes": segment.strength_minutes,
                "strength_exposures": segment.recommended_strength_exposures,
                "runtime_ms": segment.category_feasibility_runtime_ms,
            }
            for segment in r3.segments
        ],
        "r6": [asdict(item) for item in quality],
        "r6_results": [
            _r6_result_payload(result, segment_context_by_id[result.plan.allocation_segment_id if result.plan else result.infeasibilities[0].allocation_segment_id])
            for result in quality_results
        ],
        "r4": r4, "r4_status": r4_status,
        "r5": r5, "calendar": calendar, "workouts": workouts,
        "conservation": {"r2": r2_total, "r3": r3_total, "r4": r4_total, "r5": r5_total, "pass": r2_total == r3_total == r4_total == r5_total},
        "structural_validation": "PASS" if r4.success and r5.success and r2_total == r3_total == r4_total == r5_total else "FAIL",
        "plausibility_reviews": reviews,
        "training_plausibility": "QUESTIONABLE" if any(value["rating"] == "QUESTIONABLE" for value in reviews.values()) else "GOOD",
    }
    if diagnostic_counts is not None:
        diagnostic_counts.update({
            "r2_segment_count": len(r2.segments),
            "r3_segment_count": len(r3.segments),
            "r6_requirement_count": sum(len(item.requirements) for item in quality),
            "r6_infeasibility_count": sum(len(item.infeasibilities) for item in quality_results),
            "r4_day_count": len(r4.days),
            "r4_component_count": sum(len(day.components) for day in r4.days),
            "r5_result_count": len(r5.results),
        })
    record_timing("report_primitive_conversion", primitive_started)
    rendering_started = perf_counter()
    report["human_readable"] = render_v3_diagnostic_report(report)
    record_timing("human_readable_rendering", rendering_started)
    if diagnostic_timings_ms is not None:
        report["diagnostic_timing_ms"] = dict(diagnostic_timings_ms)
    if diagnostic_counts is not None:
        report["diagnostic_counts"] = dict(diagnostic_counts)
    return report


_PUBLIC_DIAGNOSTIC_KEYS = (
    "planning_date", "planning_end", "available_capacity_minutes",
    "planning_goals", "training_preferences", "rowing_modalities", "r2_baseline_evidence", "physiological_evidence", "development_recommendation", "r3_weekly_evidence",
    "current_training", "dated_context", "recovery_context_summary", "r2", "r3", "r3_cadence_feasibility", "r6", "r6_results", "r4_status", "calendar", "workouts",
    "conservation", "structural_validation", "plausibility_reviews",
    "training_plausibility", "persisted_input_audit", "diagnostic_timing_ms",
    "diagnostic_counts", "human_readable",
)


def public_v3_diagnostic_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the R9A API payload without leaking internal R4/R5 result objects.

    ``calendar`` is the full dated R4 component representation and ``workouts``
    is the full diagnostic R5 materialization representation.  They preserve
    the review data while keeping the pure internal report free to retain its
    domain objects for local diagnostic assertions.
    """
    return {key: report[key] for key in _PUBLIC_DIAGNOSTIC_KEYS if key in report}


def generate_persisted_v3_diagnostic_report(
    repository: Any, athlete_id: str, *, planning_date: date,
    profile: Mapping[str, Any] | None = None,
    stage_timings_ms: dict[str, float] | None = None,
    stage_checkpoint: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Run the pure diagnostic from the same persisted-history inputs as V3.

    Weekly overrides are deliberately retained in the report but not supplied
    to the planner: the production generation path uses them for fingerprint
    invalidation and Week-view display, not for V3 prescription authority.
    """
    snapshot_started = perf_counter()
    persisted = load_persisted_diagnostic_input(repository, athlete_id, profile=profile)
    if stage_timings_ms is not None:
        stage_timings_ms["persisted_snapshot_load"] = round((perf_counter() - snapshot_started) * 1000, 3)
    if stage_checkpoint is not None:
        stage_checkpoint("snapshot_complete", (perf_counter() - snapshot_started) * 1000)
    history_started = perf_counter()
    previous = (
        {"plan_id": persisted.prior_plan_id, "plan": persisted.prior_plan}
        if persisted.prior_plan_id and persisted.prior_plan else None
    )
    locked, actuals, actual_plan_ids = recover_generation_history(
        previous, lambda plan_id: persisted.workout_logs_by_plan.get(plan_id, ()),
    )
    if stage_timings_ms is not None:
        stage_timings_ms["canonical_generation_history"] = round((perf_counter() - history_started) * 1000, 3)
    report_started = perf_counter()
    if stage_checkpoint is not None:
        stage_checkpoint("report_start", 0.0)
    diagnostic_counts = {
        "inherited_planversion_source_count": len(actual_plan_ids),
        "completed_actual_count": len(actuals),
        "history_actual_count": len(actuals),
        "locked_session_count": len(locked),
    }
    report = generate_v3_diagnostic_report(
        persisted.profile, planning_date=planning_date,
        prior_plan=persisted.prior_plan, history_actuals=actuals,
        locked_sessions=locked,
        diagnostic_timings_ms=stage_timings_ms,
        diagnostic_counts=diagnostic_counts,
    )
    if stage_timings_ms is not None:
        stage_timings_ms["report_generation"] = round((perf_counter() - report_started) * 1000, 3)
    if stage_checkpoint is not None:
        stage_checkpoint("report_complete", (perf_counter() - report_started) * 1000)
    report["persisted_input_audit"] = {
        "prior_plan_id": persisted.prior_plan_id,
        "actual_plan_ids": actual_plan_ids,
        "workout_log_count": len(persisted.workout_logs),
        "inherited_locked_count": len(locked),
        "weekly_override_count": len(persisted.weekly_overrides),
        "weekly_overrides_planner_input": False,
        "performance_tests_source": "persisted_profile",
        "race_context_source": "persisted_profile",
    }
    report["human_readable"] = render_v3_diagnostic_report(report)
    return report
