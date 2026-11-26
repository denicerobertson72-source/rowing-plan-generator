"""R9F.4c diagnostic-only demand characterization.

This module projects already accepted R4/R5 facts into descriptive daily and
rolling observations.  It is deliberately not a load target, fatigue score,
or input to R2--R5 planning.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .planning_domain import (
    PhysicalCategory, TrainingComponentPurpose, TrainingDayPlanResult, TrainingDayState,
)
from .materialization_v3 import MaterializedTrainingDayPlanResult


DEMAND_CHARACTERIZATION_VERSION = "r9f4c-1-historical-continuity-demand-1"
_QUALITY_BANDS = frozenset({"AT", "TR", "AN", "PP", "RACE"})


def _days(start: date, end: date):
    return tuple(start + timedelta(days=index) for index in range((end - start).days + 1))


def _materialized_by_component(result: MaterializedTrainingDayPlanResult) -> dict[str, Any]:
    return {item.component_id: item for item in result.results}


def _component_record(component: Any, materialized_result: Any) -> dict[str, Any]:
    materialized = materialized_result.materialized if materialized_result and materialized_result.success else None
    work = materialized.physiological_work if materialized else None
    band = (work.band if work else None) or component.physiological_band
    known_work = work.work_minutes if work else component.quality_work_minutes
    demanding_work_applicable = band in _QUALITY_BANDS
    return {
        "component_id": component.component_id,
        "source_lineage_id": component.source_lineage_id,
        "category": component.physical_category.value,
        "modality": (materialized.modality.value if materialized else component.modality.value if component.modality else None),
        "purpose": component.purpose.value,
        "authority": component.authority.value,
        "physical_minutes": component.duration_minutes,
        "warmup_minutes": materialized.warmup_minutes if materialized else None,
        "prescribed_work_minutes": materialized.prescribed_work_minutes if materialized else known_work,
        "recovery_interval_minutes": materialized.recovery_minutes if materialized else None,
        "cooldown_minutes": materialized.cooldown_minutes if materialized else None,
        "easy_extension_minutes": materialized.easy_extension_minutes if materialized else None,
        "physiological_band": band,
        # This is deliberately limited to known endurance-quality work.  It is
        # not a whole-session stress or physiological-demand estimate.
        "known_demanding_work_minutes": known_work if demanding_work_applicable and known_work is not None else None,
        "endurance_demanding_work_applicable": demanding_work_applicable,
        "quality_requirement_id": component.quality_requirement_id,
        "materialization_status": "matched" if materialized else "missing_or_failed",
        "archetype_id": materialized.catalog_archetype_id if materialized else None,
        "unknown_characteristics": [*(() if (materialized or component.modality) else ("modality_unknown",)),
                                    *(("workout_structure_unknown",) if materialized is None else ())],
        "evidence_completeness": {
            "physical_duration": "known", "modality": "known" if (materialized or component.modality) else "unknown",
            "endurance_intensity": "known" if band else "unknown",
            "endurance_demanding_work": (
                "known" if known_work is not None else "unknown"
            ) if demanding_work_applicable else "not_applicable",
            "strength_occurrence_duration": "known" if component.physical_category is PhysicalCategory.STRENGTH else "not_applicable",
            "strength_purpose": "known" if component.physical_category is PhysicalCategory.STRENGTH else "not_applicable",
            "muscular_load_structure": "unknown" if component.physical_category is PhysicalCategory.STRENGTH else "not_applicable",
            "neuromuscular_demand": "unknown",
            "race_assessment_demand": "unknown" if component.purpose is TrainingComponentPurpose.RACE else "not_applicable",
        },
    }


def _daily_record(day: date, training_day: Any | None, components: list[dict[str, Any]], completed: list[Mapping[str, Any]]) -> dict[str, Any]:
    categories = {category.value: sum(item["physical_minutes"] for item in components if item["category"] == category.value)
                  for category in PhysicalCategory}
    categories = {key: value for key, value in categories.items() if value}
    purposes = sorted({item["purpose"] for item in components})
    demanding = sum(item["known_demanding_work_minutes"] or 0 for item in components)
    strength = [item for item in components if item["category"] == PhysicalCategory.STRENGTH.value]
    recovery = any(item["purpose"] == TrainingComponentPurpose.RECOVERY.value for item in components)
    technical = any(item["purpose"] == TrainingComponentPurpose.TECHNICAL.value for item in components)
    return {
        "date": day.isoformat(),
        "completed_physical_minutes": sum(int(item.get("physical_duration_minutes") or 0) for item in completed),
        "completed_exposure_count": len(completed),
        "prospective_authoritative_physical_minutes": sum(item["physical_minutes"] for item in components if item["authority"] == "authoritative"),
        "prospective_generated_physical_minutes": sum(item["physical_minutes"] for item in components if item["authority"] != "authoritative"),
        "prospective_component_count": len(components),
        "physical_minutes_by_category": categories,
        "modalities": sorted({item["modality"] for item in components if item["modality"]}),
        "purposes": purposes,
        "known_endurance_minutes": sum(item["physical_minutes"] for item in components if item["category"] in {"rowing_specific", "general_aerobic"}),
        "known_demanding_work_minutes": demanding,
        "known_endurance_demanding_work_component_count": sum(
            item["known_demanding_work_minutes"] is not None for item in components
        ),
        "unknown_endurance_demanding_work_component_count": sum(
            item["evidence_completeness"]["endurance_demanding_work"] == "unknown" for item in components
        ),
        "strength_exposure_count": len(strength),
        "strength_minutes": sum(item["physical_minutes"] for item in strength),
        "quality_rowing_exposure_count": sum(item["physiological_band"] in _QUALITY_BANDS for item in components),
        "race_exposure_count": sum(item["purpose"] == "race" for item in components),
        "assessment_exposure_count": 0,
        "combined_day_characteristics": {
            "combined": len(components) > 1,
            "strength_plus_easy_rowing": bool(strength and any(item["category"] == "rowing_specific" and item["purpose"] == "easy_aerobic" for item in components)),
            "strength_plus_general_aerobic": bool(strength and any(item["category"] == "general_aerobic" for item in components)),
            "strength_plus_quality_rowing": bool(strength and any(item["physiological_band"] in _QUALITY_BANDS for item in components)),
        },
        "explicit_recovery_purpose": recovery,
        "technical_purpose": technical,
        "explicit_complete_rest": bool(training_day and training_day.state is TrainingDayState.DESIGNATED_REST),
        "no_recorded_completed_work_is_not_rest": not completed,
        "unknown_demand_component_count": sum(bool(item["unknown_characteristics"]) for item in components),
        "evidence_completeness": {
            "known_physical_duration_component_count": len(components),
            "unknown_modality_component_count": sum(item["evidence_completeness"]["modality"] == "unknown" for item in components),
            "strength_muscular_load_unknown_count": sum(item["evidence_completeness"]["muscular_load_structure"] == "unknown" for item in components),
            "known_demanding_work_minutes_scope": "endurance_quality_work_only_not_total_physiological_demand",
            "unknown_demand_component_count_scope": "component_metadata_gaps_only_not_total_physiological_demand_completeness",
        },
        "component_ids": [item["component_id"] for item in components],
    }


def _rolling(daily: list[dict[str, Any]], index: int, window_days: int,
             completed_exposures: list[Mapping[str, Any]],
             occurrence_evidence: list[Mapping[str, Any]]) -> dict[str, Any]:
    start = max(0, index - window_days + 1)
    rows = daily[start:index + 1]
    end_date = date.fromisoformat(rows[-1]["date"])
    calendar_start = end_date - timedelta(days=window_days - 1)
    completed = [item for item in completed_exposures if calendar_start <= date.fromisoformat(str(item["date"])) <= end_date]
    occurrences = [item for item in occurrence_evidence if calendar_start <= date.fromisoformat(str(item["date"])) <= end_date]
    observed_dates = {str(item["date"]) for item in completed}
    represented_calendar_dates = {row["date"] for row in rows}
    missing_historical_days = [
        (calendar_start + timedelta(days=offset)).isoformat()
        for offset in range(window_days)
        if (calendar_start + timedelta(days=offset)).isoformat() not in represented_calendar_dates
        and (calendar_start + timedelta(days=offset)).isoformat() not in observed_dates
    ]
    return {
        "window_days": window_days,
        "window_start": calendar_start.isoformat(), "window_end": rows[-1]["date"],
        "planning_calendar_days_represented": len(rows), "calendar_window_fully_represented_by_planning_rows": len(rows) == window_days,
        "completed_physical_minutes": sum(int(item.get("physical_duration_minutes") or 0) for item in completed),
        "completed_exposure_count": len(completed),
        "completed_strength_exposures": sum(bool(item.get("strength_exposure")) for item in completed),
        # Race/assessment occurrences with unknown duration remain visible
        # evidence, but never create physical minutes or completed credit.
        "unknown_duration_race_occurrence_count": sum(bool(item.get("race_exposure")) for item in occurrences),
        "unknown_duration_assessment_occurrence_count": sum(bool(item.get("assessment_exposure")) for item in occurrences),
        "missing_or_unobserved_historical_dates": missing_historical_days,
        "activity_evidence_complete": False,
        "prospective_authoritative_physical_minutes": sum(row["prospective_authoritative_physical_minutes"] for row in rows),
        "prospective_generated_physical_minutes": sum(row["prospective_generated_physical_minutes"] for row in rows),
        "known_demanding_work_minutes": sum(row["known_demanding_work_minutes"] for row in rows),
        "strength_exposures": sum(row["strength_exposure_count"] for row in rows),
        "quality_rowing_exposures": sum(row["quality_rowing_exposure_count"] for row in rows),
        "loaded_days": sum(bool(row["prospective_component_count"] or row["completed_exposure_count"]) for row in rows),
    }


def characterize_training_demand(*, planning_start: date, planning_end: date,
                                 training_days: TrainingDayPlanResult,
                                 materialization: MaterializedTrainingDayPlanResult,
                                 exposure_ledger: Mapping[str, Any],
                                 allocation_segments: Iterable[Any],
                                 sequence_diagnostics: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Describe accepted R4/R5 work; never feeds a planning decision."""
    result_by_id = _materialized_by_component(materialization)
    day_by_date = {item.date: item for item in training_days.days}
    completed_by_day: dict[str, list[Mapping[str, Any]]] = {}
    unknown_duration_occurrences: list[Mapping[str, Any]] = []
    for exposure in exposure_ledger.get("exposures", ()):
        if exposure.get("status") == "completed_actual":
            completed_by_day.setdefault(str(exposure["date"]), []).append(exposure)
        if (
            exposure.get("physical_duration_minutes") is None
            and (exposure.get("race_exposure") or exposure.get("assessment_exposure"))
            and exposure.get("status") in {"completed_actual", "assessment_evidence"}
        ):
            unknown_duration_occurrences.append(exposure)
    daily: list[dict[str, Any]] = []
    for current in _days(planning_start, planning_end):
        day = day_by_date.get(current)
        components = [_component_record(item, result_by_id.get(item.component_id)) for item in (day.components if day else ())]
        daily.append(_daily_record(current, day, components, completed_by_day.get(current.isoformat(), [])))
    completed_exposures = [item for rows in completed_by_day.values() for item in rows]
    for index, row in enumerate(daily):
        row["rolling"] = {
            f"{window}d": _rolling(daily, index, window, completed_exposures, unknown_duration_occurrences)
            for window in (3, 7, 14)
        }
    cadence = []
    for segment in allocation_segments:
        rows = [row for row in daily if segment.start_date.isoformat() <= row["date"] <= segment.end_date.isoformat()]
        cadence.append({"start_date": segment.start_date.isoformat(), "end_date": segment.end_date.isoformat(),
                        "prospective_physical_minutes": sum(row["prospective_authoritative_physical_minutes"] + row["prospective_generated_physical_minutes"] for row in rows),
                        "completed_physical_minutes": sum(row["completed_physical_minutes"] for row in rows),
                        "combined_days": sum(row["combined_day_characteristics"]["combined"] for row in rows),
                        "explicit_rest_days": sum(row["explicit_complete_rest"] for row in rows)})
    physical_r4 = sum(day.total_physical_minutes for day in training_days.days)
    physical_r5 = sum(item.materialized.physical_duration_minutes for item in materialization.results if item.success and item.materialized)
    loaded = [row["date"] for row in daily if row["prospective_component_count"]]
    sequences = []
    for value in loaded:
        if not sequences or date.fromisoformat(value) != date.fromisoformat(sequences[-1][-1]) + timedelta(days=1): sequences.append([value])
        else: sequences[-1].append(value)
    baseline = exposure_ledger.get("reported_training_baseline", {})
    r4_component_ids = [component.component_id for day in training_days.days for component in day.components]
    r5_result_ids = [item.component_id for item in materialization.results]
    unique_component_id_match = (
        len(r4_component_ids) == len(set(r4_component_ids))
        and len(r5_result_ids) == len(set(r5_result_ids))
        and set(r4_component_ids) == set(r5_result_ids)
    )
    return {
        "version": DEMAND_CHARACTERIZATION_VERSION, "diagnostic_only": True,
        "prescription_behavior": "unchanged", "window_convention": "inclusive calendar windows ending on each reported date",
        "r4_r5_enrichment": {"r4_component_count": sum(len(day.components) for day in training_days.days),
                               "r5_match_count": sum(item.success for item in materialization.results),
                               "unique_component_id_match": unique_component_id_match,
                               "one_r4_component_to_one_r5_result": unique_component_id_match},
        "daily": daily, "cadence_summaries": cadence,
        "consecutive_loaded_day_sequences": sequences,
        "sequence_decisions": list(sequence_diagnostics),
        "physical_conservation": {"r4_minutes": physical_r4, "r5_minutes": physical_r5, "pass": physical_r4 == physical_r5},
        "history_coverage": {
                             # Keep the original consumer field, but make its
                             # historical scope unambiguous through the alias.
                             "observed_completed_exposure_count": sum(row["completed_exposure_count"] for row in daily),
                             "observed_completed_exposure_count_scope": "planning_period_only_legacy_alias",
                             "completed_exposure_count_within_planning_period": sum(row["completed_exposure_count"] for row in daily),
                             "observed_completed_exposure_count_all_ledger": len(completed_exposures),
                             "reported_aggregate_baseline": baseline,
                             "limitations": ["missing_completed_days_are_not_known_rest", "aggregate_baseline_is_not_dated_work", "unknown_characteristics_are_not_zero_demand"]},
        "limitations": ["no_fatigue_score", "no_acwr", "no_recovery_or_sustainability_judgment", "strength_excluded_from_endurance_intensity_accounting", "known_endurance_demanding_work_is_not_total_physiological_demand"],
    }
