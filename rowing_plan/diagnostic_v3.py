"""Pure, non-persistent end-to-end inspection of the reset V3 spine."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from time import perf_counter
from typing import Any, Callable, Mapping

from .materialization_v3 import MaterializationContext, materialize_training_days_v3
from .performance_profile import build_athlete_performance_profile, derive_performance_limiter, derive_training_focus
from .planning_profile import normalize_available_training_capacity, normalize_current_training_state
from .planning_normalization import normalize_planning_inputs
from .recommended_training_dose import r2_baseline_evidence, recent_actual_history_is_sufficient
from .generation_history import recover_generation_history
from .replacement_plan_v3 import generate_recommended_training_dose_v3
from .rowing_quality_v3 import RowingQualityPlanningContext, generate_rowing_quality_plan
from .training_allocation import generate_training_allocation_v3, weekly_evidence_for_allocation
from .planning_domain import PhysicalCategory, TrainingModality
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


def _rowing_modalities(capacity) -> tuple[TrainingModality, ...]:
    values = tuple(sorted({mode for day in capacity.days for mode in day.allowed_modalities
                           if mode in {TrainingModality.ERG, TrainingModality.ON_WATER}}, key=lambda item: item.value))
    return values or (TrainingModality.ERG, TrainingModality.ON_WATER)


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
    for segment in report["r3"]["segments"]:
        lines.append(f"{segment['start_date']}–{segment['end_date']}: total={segment['recommended_physical_minutes']} rowing={segment['rowing_specific_minutes']} strength={segment['strength_minutes']} ({segment['recommended_strength_exposures']}×{segment['expected_strength_duration_minutes']}) general={segment['general_aerobic_minutes']} other={segment['other_physical_minutes']}")
    lines += ["", "R6 QUALITY"]
    for plan in report["r6"]:
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
    r3_started = perf_counter()
    r3 = generate_training_allocation_v3(
        profile, planning_date=planning_date, recommended_dose=r2,
        prior_plan=prior_plan, history_actuals=history_actuals,
        locked_sessions=locked_sessions, future_placements=future_placements,
    ).allocation
    record_timing("r3_allocation", r3_started)
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
    baseline_evidence = r2_baseline_evidence(
        profile, current, normalized.historical_facts, planning_start=r2.planning_start,
    )
    record_timing("current_training_history_normalization", normalization_started)
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
    performance_started = perf_counter()
    performance = build_athlete_performance_profile(profile, as_of=r2.planning_start)
    limiter = derive_performance_limiter(performance, coach_focus=profile.get("preferences", {}).get("development_focus"))
    focus = derive_training_focus(limiter, phase="general_preparation")
    record_timing("performance_profile_focus", performance_started)
    r6_started = perf_counter()
    quality = tuple(generate_rowing_quality_plan(
        segment, allocation_segment_id=f"r3:{segment.start_date.isoformat()}:{segment.end_date.isoformat()}",
        performance=performance, limiter=limiter, focus=focus,
        context=RowingQualityPlanningContext(r2.planning_start, experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")), available_rowing_modalities=rowing_modalities),
    ).plan for segment in r3.segments)
    record_timing("r6_rowing_quality", r6_started)
    # Winter ERG is an explicit athlete environment, not an auto-selected
    # cross-training modality.
    r4_started = perf_counter()
    r4 = generate_training_days_v3(
        r3, days, rowing_quality_plans=quality,
        materialization_context=MaterializationContext(phase_id="general_preparation", experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")), available_rowing_modalities=rowing_modalities),
        current_training_state=current,
        preferences=preferences,
    )
    record_timing("r4_training_days", r4_started)
    r5_started = perf_counter()
    r5 = materialize_training_days_v3(
        r4,
        context=MaterializationContext(
            phase_id="general_preparation",
            experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")),
            available_rowing_modalities=rowing_modalities,
        ),
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
                                         "authority": item.authority.value, "combined_day": day.combined_day} for item in day.components]})
    workouts = []
    for result in r5.results:
        materialized = result.materialized
        workouts.append({"component_id": result.component_id, "date": component_dates.get(result.component_id),
                         "status": "success" if result.success else "failure", "physical_minutes": materialized.physical_duration_minutes if materialized else None,
                         "purpose": materialized.purpose.value if materialized else None,
                         "archetype_or_placeholder": (materialized.catalog_archetype_id or "exact_duration_placeholder") if materialized else result.failure.reason_code,
                         "physiological_work": asdict(materialized.physiological_work) if materialized and materialized.physiological_work else None,
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
        "current_training": asdict(current), "r2_baseline_evidence": baseline_evidence,
        "r3_weekly_evidence": weekly_evidence_for_allocation(profile, current), "r2": asdict(r2), "r3": asdict(r3),
        "r6": [asdict(item) for item in quality], "r4": r4, "r5": r5, "calendar": calendar, "workouts": workouts,
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
    "planning_goals", "training_preferences", "rowing_modalities", "r2_baseline_evidence", "r3_weekly_evidence",
    "current_training", "r2", "r3", "r6", "calendar", "workouts",
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
