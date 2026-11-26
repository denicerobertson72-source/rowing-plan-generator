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
        "r4_component_count": 3, "r5_match_count": 3, "one_r4_component_to_one_r5_result": True,
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
    assert monday["rolling"]["7d"]["partial_window"] is True
    assert rest["explicit_complete_rest"] is True
    assert rest["no_recorded_completed_work_is_not_rest"] is True
    assert result["history_coverage"]["reported_aggregate_baseline"]["dated_exposures_created"] == 0
    assert "no_fatigue_score" in result["limitations"]


def test_continuous_windows_and_sequence_diagnostics_are_descriptive_only():
    result = _characterization()
    assert result["consecutive_loaded_day_sequences"] == [[START.isoformat(), (START + timedelta(days=1)).isoformat()]]
    assert result["daily"][2]["rolling"]["3d"]["prospective_generated_physical_minutes"] == 105
    assert result["sequence_decisions"] == [{"code": "continuous_sequence_policy_applied"}]
    assert result["cadence_summaries"][0]["prospective_physical_minutes"] == 105
