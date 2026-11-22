"""Pure, non-persistent end-to-end inspection of the reset V3 spine."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Mapping

from .intensity import build_intensity_profile
from .materialization_v3 import MaterializationContext, materialize_training_days_v3
from .performance_profile import build_athlete_performance_profile, derive_performance_limiter, derive_training_focus
from .planning_profile import normalize_available_training_capacity, normalize_current_training_state
from .power_profile import build_power_profile
from .replacement_plan_v3 import generate_recommended_training_dose_v3
from .rowing_quality_v3 import RowingQualityPlanningContext, generate_rowing_quality_plan
from .training_allocation import generate_training_allocation_v3
from .planning_domain import PhysicalCategory, TrainingModality
from .training_days import CombinedDayPreferences, DailyTrainingCapacity, generate_training_days_v3


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
    lines += [f"Current evidence: source={current['source']} total={current['approximate_total_physical_minutes']} rowing={current['approximate_rowing_minutes']} rowing exposures={current['rowing_exposures']} strength exposures={current['strength_exposures']}",
              "", "R2 TOTAL DOSE", f"Recommended: {report['r2']['recommended_physical_minutes']} min; baseline/current estimate: {current['approximate_total_physical_minutes']} min; capacity is a ceiling, not a quota."]
    lines += ["", "R3 ALLOCATION"]
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


def generate_v3_diagnostic_report(profile: Mapping[str, Any], config: Mapping[str, Any], *, planning_date: date) -> dict[str, Any]:
    """Run R1B→R7B without routing, persistence, or legacy planner execution."""
    r2 = generate_recommended_training_dose_v3(profile, planning_date=planning_date)
    r3 = generate_training_allocation_v3(profile, planning_date=planning_date, recommended_dose=r2).allocation
    current = normalize_current_training_state(profile)
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
    performance = build_athlete_performance_profile(profile, as_of=r2.planning_start)
    limiter = derive_performance_limiter(performance, coach_focus=profile.get("preferences", {}).get("development_focus"))
    focus = derive_training_focus(limiter, phase="general_preparation")
    quality = tuple(generate_rowing_quality_plan(
        segment, allocation_segment_id=f"r3:{segment.start_date.isoformat()}:{segment.end_date.isoformat()}",
        performance=performance, limiter=limiter, focus=focus,
        context=RowingQualityPlanningContext(r2.planning_start, experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")), available_rowing_modalities=rowing_modalities),
    ).plan for segment in r3.segments)
    # Winter ERG is an explicit athlete environment, not an auto-selected
    # cross-training modality.
    r4 = generate_training_days_v3(
        r3, days, rowing_quality_plans=quality,
        materialization_context=MaterializationContext(phase_id="general_preparation", experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")), available_rowing_modalities=rowing_modalities),
        current_training_state=current,
        preferences=preferences,
    )
    r5 = materialize_training_days_v3(
        r4,
        context=MaterializationContext(
            phase_id="general_preparation",
            experience=str(profile.get("athlete", {}).get("experience_level", "intermediate")),
            available_rowing_modalities=rowing_modalities,
        ),
        allocation=r3,
    )
    r2_total = r2.recommended_physical_minutes
    r3_total = r3.recommended_physical_minutes
    r4_total = sum(day.total_physical_minutes for day in r4.days)
    r5_total = sum(item.materialized.physical_duration_minutes for item in r5.results if item.success and item.materialized)
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
        "current_training": asdict(current), "r2": asdict(r2), "r3": asdict(r3),
        "r6": [asdict(item) for item in quality], "r4": r4, "r5": r5, "calendar": calendar, "workouts": workouts,
        "conservation": {"r2": r2_total, "r3": r3_total, "r4": r4_total, "r5": r5_total, "pass": r2_total == r3_total == r4_total == r5_total},
        "structural_validation": "PASS" if r4.success and r5.success and r2_total == r3_total == r4_total == r5_total else "FAIL",
        "plausibility_reviews": reviews,
        "training_plausibility": "QUESTIONABLE" if any(value["rating"] == "QUESTIONABLE" for value in reviews.values()) else "GOOD",
    }
    report["human_readable"] = render_v3_diagnostic_report(report)
    return report
