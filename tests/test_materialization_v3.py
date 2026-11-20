"""R5 exact-duration component materialization contracts."""
from datetime import date

from rowing_plan.materialization_v3 import (
    MATERIALIZER_POLICY_VERSION,
    MaterializationContext,
    materialize_training_component_v3,
    materialize_training_days_v3,
    reconcile_materialized_training_days_v3,
)
from rowing_plan.planning_domain import (
    ComponentAuthority,
    PhysicalCategory,
    TrainingAllocation,
    TrainingAllocationSegment,
    TrainingComponent,
    TrainingComponentPurpose,
    TrainingDay,
    TrainingDayPlanResult,
    TrainingDayState,
    TrainingModality,
)
from rowing_plan.training_days import stable_component_identity


DAY = date(2026, 1, 5)
SEGMENT_ID = "r3:2026-01-05:2026-01-05"


def component(*, purpose=TrainingComponentPurpose.EASY_AEROBIC, minutes=50,
              category=PhysicalCategory.ROWING_SPECIFIC, modality=TrainingModality.ERG,
              authority=ComponentAuthority.GENERATED, lineage="lineage", sequence=1):
    identity = stable_component_identity(
        source_lineage_id=f"{lineage}:{purpose.value}:{minutes}", allocation_segment_id=SEGMENT_ID,
        physical_category=category, duration_minutes=minutes, purpose=purpose,
        authority=authority, modality=modality,
    )
    return TrainingComponent(
        identity, f"{lineage}:{purpose.value}:{minutes}", SEGMENT_ID,
        category, minutes, purpose, sequence=sequence, modality=modality, authority=authority,
    )


def context(**kwargs):
    return MaterializationContext(**kwargs)


def test_easy_aerobic_exact_duration_and_component_identity_are_preserved():
    source = component(minutes=50)
    result = materialize_training_component_v3(source, context=context())
    assert result.success
    assert result.materialized.component_id == source.component_id
    assert result.materialized.physical_duration_minutes == 50
    assert result.materialized.physical_duration_minutes == (
        result.materialized.prescribed_work_minutes + result.materialized.warmup_minutes
        + result.materialized.recovery_minutes + result.materialized.cooldown_minutes
        + result.materialized.easy_extension_minutes
    )
    assert result.materialized.materializer_policy_version == MATERIALIZER_POLICY_VERSION


def test_easy_aerobic_odd_duration_is_exact_not_credit_or_reservation_fallback():
    result = materialize_training_component_v3(component(minutes=47), context=context())
    assert result.success
    assert result.materialized.physical_duration_minutes == 47
    assert result.materialized.adaptation_metadata["adaptation_kind"] in {
        "exact_parameterization", "easy_extension",
    }


def test_threshold_quality_work_is_distinct_from_exact_physical_duration():
    source = component(purpose=TrainingComponentPurpose.THRESHOLD, minutes=50)
    result = materialize_training_component_v3(
        source, context=context(phase_id="threshold_development"), quality_work_minutes=20,
    )
    assert result.success
    assert result.materialized.physical_duration_minutes == 50
    assert result.materialized.prescribed_work_minutes == 20
    assert result.materialized.physiological_work.band == "AT"
    assert result.materialized.physiological_work.work_minutes == 20


def test_quality_below_minimum_and_power_above_maximum_fail_explicitly():
    threshold = materialize_training_component_v3(
        component(purpose=TrainingComponentPurpose.THRESHOLD, minutes=20),
        context=context(phase_id="threshold_development"),
    )
    power = materialize_training_component_v3(
        component(purpose=TrainingComponentPurpose.POWER, minutes=70),
        context=context(phase_id="race_specific_preparation", experience="competitive"),
    )
    assert threshold.failure.reason_code == "duration_below_minimum"
    assert power.failure.reason_code == "duration_above_maximum"


def test_authoritative_coached_and_strength_placeholders_preserve_known_duration():
    coached = component(purpose=TrainingComponentPurpose.COACHED, minutes=60,
                        modality=TrainingModality.ON_WATER, authority=ComponentAuthority.AUTHORITATIVE)
    strength = component(purpose=TrainingComponentPurpose.STRENGTH, minutes=45,
                         category=PhysicalCategory.STRENGTH, modality=None)
    coached_result = materialize_training_component_v3(coached, context=context())
    strength_result = materialize_training_component_v3(strength, context=context())
    assert coached_result.success and coached_result.materialized.physical_duration_minutes == 60
    assert coached_result.materialized.catalog_archetype_id is None
    assert strength_result.success and strength_result.materialized.physical_duration_minutes == 45
    assert "strength_program_pending" in strength_result.materialized.selection_rationale_codes


def test_general_aerobic_requires_resolved_modality_and_never_auto_selects_one():
    unresolved = component(purpose=TrainingComponentPurpose.EASY_AEROBIC, minutes=15,
                           category=PhysicalCategory.GENERAL_AEROBIC, modality=None)
    resolved = component(purpose=TrainingComponentPurpose.EASY_AEROBIC, minutes=15,
                         category=PhysicalCategory.GENERAL_AEROBIC, modality=TrainingModality.STAIR_CLIMBER)
    assert materialize_training_component_v3(unresolved, context=context()).failure.reason_code == "modality_unresolved"
    actual = materialize_training_component_v3(resolved, context=context())
    assert actual.success and actual.materialized.modality is TrainingModality.STAIR_CLIMBER
    assert actual.materialized.physical_duration_minutes == 15


def test_variety_is_deterministic_and_never_relaxes_purpose_or_maximum():
    source = component(minutes=50)
    first = materialize_training_component_v3(source, context=context())
    second = materialize_training_component_v3(source, context=context())
    assert first == second
    assert first.materialized.purpose is TrainingComponentPurpose.EASY_AEROBIC
    assert first.materialized.physical_duration_minutes == 50


def test_history_can_change_concrete_archetype_without_changing_component_identity():
    source = component(minutes=50)
    baseline = materialize_training_component_v3(source, context=context()).materialized
    varied = materialize_training_component_v3(
        source,
        context=context(selector_history=({
            "archetype_id": baseline.catalog_archetype_id,
            "structure_family": "medium_repeats",
            "primary_band": "UT2",
        },)),
    ).materialized
    assert baseline.component_id == varied.component_id == source.component_id
    assert baseline.catalog_archetype_id != varied.catalog_archetype_id
    assert varied.physical_duration_minutes == 50


def test_day_and_segment_conservation_keeps_strength_and_stairs_as_two_components():
    strength = component(purpose=TrainingComponentPurpose.STRENGTH, minutes=45,
                         category=PhysicalCategory.STRENGTH, modality=None, lineage="strength")
    stairs = component(minutes=15, category=PhysicalCategory.GENERAL_AEROBIC,
                       modality=TrainingModality.STAIR_CLIMBER, lineage="stairs", sequence=2)
    day = TrainingDay(DAY, 60, (strength, stairs), TrainingDayState.PLANNED_TRAINING)
    plan = TrainingDayPlanResult((day,))
    segment = TrainingAllocationSegment(
        DAY, DAY, 60, 0, 45, 15, 0, 0, 1, 0, 1, 45,
        rationale_codes=("test",),
    )
    allocation = TrainingAllocation(DAY, DAY, (segment,), 60, 0, 45, 15, 0, 1,
                                    rationale_codes=("test",), confidence="high")
    result = materialize_training_days_v3(plan, context=context(), allocation=allocation)
    assert result.success
    assert len(result.results) == 2
    assert {item.materialized.component_id for item in result.results} == {strength.component_id, stairs.component_id}
    assert result.reconciliation.day_minutes == ((DAY.isoformat(), 60),)
    assert reconcile_materialized_training_days_v3(plan, result.results, allocation=allocation).success


def test_reconciliation_rejects_missing_component_without_any_credit_fallback():
    source = component(minutes=50)
    plan = TrainingDayPlanResult((TrainingDay(DAY, 50, (source,), TrainingDayState.PLANNED_TRAINING),))
    reconciliation = reconcile_materialized_training_days_v3(plan, ())
    assert not reconciliation.success
    assert "materialization_component_set_mismatch" in reconciliation.violation_codes
    assert "materialization_failure_or_missing_component" in reconciliation.violation_codes
