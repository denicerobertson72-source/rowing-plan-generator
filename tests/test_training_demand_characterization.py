from datetime import date, timedelta

from rowing_plan.materialization_v3 import materialize_training_days_v3
from rowing_plan.planning_domain import (
    ComponentAuthority, PhysicalCategory, TrainingComponent,
    TrainingComponentPurpose, TrainingDay, TrainingDayPlanResult, TrainingDayState,
    TrainingModality,
)
from rowing_plan.training_demand_characterization import characterize_training_demand


START = date(2026, 12, 7)


def _component(identifier, category, minutes, purpose, *, modality=None, sequence=1):
    return TrainingComponent(
        identifier, f"lineage:{identifier}", "r3:2026-12-07:2026-12-13",
        category, minutes, purpose, sequence=sequence, modality=modality,
        authority=ComponentAuthority.GENERATED,
    )


def _plan():
    return TrainingDayPlanResult((
        TrainingDay(START, 70, (
            _component("strength", PhysicalCategory.STRENGTH, 45, TrainingComponentPurpose.STRENGTH,
                       modality=TrainingModality.STRENGTH),
            _component("stairs", PhysicalCategory.GENERAL_AEROBIC, 15, TrainingComponentPurpose.EASY_AEROBIC,
                       modality=TrainingModality.STAIR_CLIMBER, sequence=2),
        ), TrainingDayState.PLANNED_TRAINING),
        TrainingDay(START + timedelta(days=1), 70, (
            _component("erg", PhysicalCategory.ROWING_SPECIFIC, 45, TrainingComponentPurpose.EASY_AEROBIC,
                       modality=TrainingModality.ERG),
        ), TrainingDayState.PLANNED_TRAINING),
        TrainingDay(START + timedelta(days=2), 0, (), TrainingDayState.DESIGNATED_REST),
    ))


def _characterization(*, completed=()):
    plan = _plan()
    materialized = materialize_training_days_v3(plan)
    segment = type("Segment", (), {"start_date": START, "end_date": START + timedelta(days=2)})()
    return characterize_training_demand(
        planning_start=START, planning_end=START + timedelta(days=2),
        training_days=plan, materialization=materialized,
        exposure_ledger={
            "exposures": list(completed),
            "reported_training_baseline": {"total_physical_minutes": 500, "dated_exposures_created": 0},
        },
        allocation_segments=(segment,),
        sequence_diagnostics=({"code": "continuous_sequence_policy_applied"},),
    )


def test_r4_r5_enrichment_preserves_one_physical_component_and_combined_day_meaning():
    result = _characterization()
    monday = result["daily"][0]
    assert result["diagnostic_only"] is True
    assert result["prescription_behavior"] == "unchanged"
    assert result["r4_r5_enrichment"] == {
        "r4_component_count": 3, "r5_match_count": 3, "unique_component_id_match": True,
        "one_r4_component_to_one_r5_result": True,
    }
    assert result["physical_conservation"] == {"r4_minutes": 105, "r5_minutes": 105, "pass": True}
    assert monday["physical_minutes_by_category"] == {"general_aerobic": 15, "strength": 45}
    assert monday["combined_day_characteristics"]["strength_plus_general_aerobic"] is True
    assert monday["explicit_recovery_purpose"] is False
    assert result["daily"][1]["explicit_recovery_purpose"] is False  # UT2/easy is not recovery by default.


def test_daily_and_rolling_summaries_keep_completed_and_prospective_work_separate():
    completed = ({
        "date": START.isoformat(), "status": "completed_actual", "physical_duration_minutes": 30,
        "exposure_id": "historical:completed", "source_lineage_id": "completed",
    },)
    result = _characterization(completed=completed)
    monday, _tuesday, rest = result["daily"]
    assert monday["completed_physical_minutes"] == 30
    assert monday["prospective_generated_physical_minutes"] == 60
    assert monday["rolling"]["3d"]["completed_physical_minutes"] == 30
    assert monday["rolling"]["7d"]["calendar_window_fully_represented_by_planning_rows"] is False
    assert rest["explicit_complete_rest"] is True
    assert rest["no_recorded_completed_work_is_not_rest"] is True
    assert result["history_coverage"]["completed_exposure_count_within_planning_period"] == 1
    assert result["history_coverage"]["observed_completed_exposure_count"] == 1
    assert result["history_coverage"]["observed_completed_exposure_count_scope"] == "planning_period_only_legacy_alias"
    assert result["history_coverage"]["reported_aggregate_baseline"]["dated_exposures_created"] == 0
    assert "no_fatigue_score" in result["limitations"]


def test_continuous_windows_and_sequence_diagnostics_are_descriptive_only():
    result = _characterization()
    assert result["consecutive_loaded_day_sequences"] == [[START.isoformat(), (START + timedelta(days=1)).isoformat()]]
    assert result["daily"][2]["rolling"]["3d"]["prospective_generated_physical_minutes"] == 105
    assert result["sequence_decisions"] == [{"code": "continuous_sequence_policy_applied"}]
    assert result["cadence_summaries"][0]["prospective_physical_minutes"] == 105


def test_first_planning_day_rolling_windows_include_completed_history_without_turning_gaps_into_rest():
    historical = ({
        "date": (START - timedelta(days=2)).isoformat(), "status": "completed_actual",
        "physical_duration_minutes": 40, "strength_exposure": True,
        "exposure_id": "historical:strength", "source_lineage_id": "strength-history",
    }, {
        "date": (START - timedelta(days=1)).isoformat(), "status": "planned_uncompleted",
        "physical_duration_minutes": 99, "exposure_id": "historical:planned", "source_lineage_id": "planned",
    })
    result = _characterization(completed=historical)
    rolling = result["daily"][0]["rolling"]
    assert rolling["3d"]["window_start"] == (START - timedelta(days=2)).isoformat()
    assert rolling["3d"]["completed_physical_minutes"] == 40
    assert rolling["3d"]["completed_strength_exposures"] == 1
    assert rolling["7d"]["calendar_window_fully_represented_by_planning_rows"] is False
    assert (START - timedelta(days=6)).isoformat() in rolling["7d"]["missing_or_unobserved_historical_dates"]
    assert rolling["3d"]["activity_evidence_complete"] is False


def test_strength_duration_does_not_claim_known_muscular_demand_or_zero_total_demand():
    result = _characterization()
    evidence = result["daily"][0]["evidence_completeness"]
    assert evidence["strength_muscular_load_unknown_count"] == 1
    assert evidence["known_demanding_work_minutes_scope"] == "endurance_quality_work_only_not_total_physiological_demand"
    assert evidence["unknown_demand_component_count_scope"] == "component_metadata_gaps_only_not_total_physiological_demand_completeness"


def test_unknown_duration_race_and_assessment_remain_visible_without_creating_completed_minutes():
    occurrences = ({
        "date": (START - timedelta(days=1)).isoformat(), "status": "completed_actual",
        "physical_duration_minutes": None, "race_exposure": True,
        "exposure_id": "race:unknown", "source_lineage_id": "race:unknown",
    }, {
        "date": (START - timedelta(days=2)).isoformat(), "status": "assessment_evidence",
        "physical_duration_minutes": None, "assessment_exposure": True,
        "exposure_id": "assessment:unknown", "source_lineage_id": "assessment:unknown",
    })
    result = _characterization(completed=occurrences)
    rolling = result["daily"][0]["rolling"]["3d"]
    assert rolling["completed_physical_minutes"] == 0
    assert rolling["unknown_duration_race_occurrence_count"] == 1
    assert rolling["unknown_duration_assessment_occurrence_count"] == 1
