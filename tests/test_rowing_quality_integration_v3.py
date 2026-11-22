"""R7 R6→R4→R5 integration without any live planner route."""
from __future__ import annotations

from datetime import date, timedelta

from rowing_plan.materialization_v3 import MaterializationContext, materialize_training_days_v3
from rowing_plan.planning_domain import (
    ComponentAuthority, CurrentTrainingSource, CurrentTrainingState, PhysicalCategory,
    QualityRequirementPriority, RowingQualityFamily, RowingQualityPlan,
    RowingQualityRequirement, TrainingAllocation, TrainingAllocationSegment,
    TrainingComponentPurpose, TrainingModality,
)
from rowing_plan.training_days import (
    AuthoritativeComponentFact, DailyTrainingCapacity, generate_training_days_v3,
    allocation_segment_id,
)


START = date(2026, 1, 5)


def _allocation(rowing: int, *, days: int = 7, strength: int = 0) -> TrainingAllocation:
    end = START + timedelta(days=days - 1)
    segment = TrainingAllocationSegment(
        START, end, rowing + strength, rowing, strength, 0, 0, 0,
        strength // 45, 0, strength // 45, 45, rationale_codes=("r7_test",),
    )
    return TrainingAllocation(START, end, (segment,), rowing + strength, rowing, strength, 0, 0,
                              strength // 45, rationale_codes=("r7_test",), confidence="high")


def _capacity(*minutes):
    return tuple(DailyTrainingCapacity(START + timedelta(days=index), available_minutes=value) for index, value in enumerate(minutes))


def _quality_plan(allocation: TrainingAllocation, *, priority=QualityRequirementPriority.PREFERRED,
                  purpose=RowingQualityFamily.THRESHOLD, work=20, technical=False):
    segment = allocation.segments[0]
    segment_id = allocation_segment_id(segment)
    band = {RowingQualityFamily.THRESHOLD: "AT", RowingQualityFamily.AEROBIC_STRENGTH: "UT1"}[purpose]
    envelopes = (("at_01", 35, 75),) if band == "AT" else (("ut1_01", 35, 90),)
    requirement = RowingQualityRequirement(
        f"{segment_id}:{purpose.value}", purpose, band, work, work - 5, work + 10,
        1, 1, 1, envelopes, 24, priority=priority,
    )
    return RowingQualityPlan(
        segment_id, segment.start_date, segment.end_date, segment.rowing_specific_minutes,
        0, (requirement,), "test", None, "high", policy_version="r7-test",
        performance_profile_version="r7-test", basis_fingerprint="r7-test",
        technical_emphasis=technical,
    )


def _context():
    return MaterializationContext(phase_id="threshold_development", experience="experienced")


def _rowing(result):
    return [item for item in result.components if item.physical_category is PhysicalCategory.ROWING_SPECIFIC]


def test_exact_215_partition_quality_residual_and_history_frequency_are_conserved():
    allocation = _allocation(215)
    result = generate_training_days_v3(
        allocation, _capacity(70, 70, 70, 70, 70, 90, 90),
        rowing_quality_plans=(_quality_plan(allocation),), materialization_context=_context(),
        current_training_state=CurrentTrainingState(CurrentTrainingSource.ATHLETE_ESTIMATE, rowing_exposures=4),
    )
    rows = _rowing(result)
    assert result.success
    assert sum(item.duration_minutes for item in rows) == 215
    assert len(rows) == 4
    assert any(item.quality_requirement_id for item in rows)
    assert all(item.duration_minutes >= 25 for item in rows)
    materialized = materialize_training_days_v3(result, context=_context(), allocation=allocation)
    assert materialized.success


def test_small_residual_is_rebalanced_into_the_quality_envelope_never_a_tiny_component():
    allocation = _allocation(40, days=1)
    result = generate_training_days_v3(allocation, _capacity(40), rowing_quality_plans=(_quality_plan(allocation),), materialization_context=_context())
    assert result.success
    assert sum(item.duration_minutes for item in _rowing(result)) == 40
    assert all(item.duration_minutes >= 25 for item in _rowing(result))


def test_fixed_generic_row_counts_once_but_known_fixed_threshold_satisfies_without_catalog():
    allocation = _allocation(95, days=2)
    generic = AuthoritativeComponentFact("coached", START, 50, modality=TrainingModality.ERG,
                                         purpose=TrainingComponentPurpose.COACHED)
    generated = generate_training_days_v3(allocation, _capacity(70, 70), (generic,),
                                          rowing_quality_plans=(_quality_plan(allocation),), materialization_context=_context())
    assert generated.success
    assert sum(item.duration_minutes for item in _rowing(generated)) == 95
    assert len([item for item in _rowing(generated) if item.quality_requirement_id]) == 1

    known = AuthoritativeComponentFact("known-threshold", START, 50, modality=TrainingModality.ERG,
                                       purpose=TrainingComponentPurpose.THRESHOLD,
                                       physiological_band="AT", quality_work_minutes=20)
    authoritative = generate_training_days_v3(allocation, _capacity(70, 70), (known,),
                                               rowing_quality_plans=(_quality_plan(allocation),), materialization_context=MaterializationContext())
    linked = next(item for item in _rowing(authoritative) if item.source_lineage_id == "authority:known-threshold")
    assert authoritative.success and linked.quality_requirement_id
    assert materialize_training_days_v3(authoritative, context=MaterializationContext(), allocation=allocation).success


def test_hard_requirement_is_explicit_while_preferred_requirement_defers():
    allocation = _allocation(50, days=1)
    hard = generate_training_days_v3(allocation, _capacity(0), rowing_quality_plans=(
        _quality_plan(allocation, priority=QualityRequirementPriority.HARD),), materialization_context=_context())
    preferred = generate_training_days_v3(allocation, _capacity(0), rowing_quality_plans=(
        _quality_plan(allocation),), materialization_context=_context())
    assert any(item.code == "hard_quality_requirement_unplaceable" for item in hard.infeasibilities)
    assert any(item.code == "quality_requirement_deferred" for item in preferred.diagnostics)


def test_technical_residual_and_partial_segment_are_exact_without_extra_minutes():
    allocation = _allocation(90, days=3)
    plan = _quality_plan(allocation, purpose=RowingQualityFamily.AEROBIC_STRENGTH, work=20, technical=True)
    result = generate_training_days_v3(allocation, _capacity(45, 0, 45), rowing_quality_plans=(plan,),
                                       materialization_context=MaterializationContext(experience="experienced"))
    rows = _rowing(result)
    assert result.success and sum(item.duration_minutes for item in rows) == 90
    assert any(item.purpose is TrainingComponentPurpose.TECHNICAL for item in rows)


def test_sparse_history_uses_feasibility_fallback_and_records_its_confidence_limit():
    allocation = _allocation(120)
    result = generate_training_days_v3(
        allocation, _capacity(60, 60, 60, 0, 0, 0, 0),
        rowing_quality_plans=(_quality_plan(allocation),), materialization_context=_context(),
    )
    assert result.success
    assert sum(item.duration_minutes for item in _rowing(result)) == 120
    assert any(item.code == "frequency_target_low_confidence" for item in result.diagnostics)


def test_no_exact_partition_is_an_explicit_failure_not_hidden_or_tiny_work():
    allocation = _allocation(20, days=1)
    result = generate_training_days_v3(
        allocation, _capacity(20), rowing_quality_plans=(_quality_plan(allocation),),
        materialization_context=_context(),
    )
    assert not result.success
    assert any(item.code in {"residual_below_component_minimum", "rowing_partition_infeasible"}
               for item in result.infeasibilities)
    assert not _rowing(result)


def test_user_fixed_known_quality_is_preserved_and_reports_a_spacing_conflict():
    allocation = _allocation(70, days=2)
    segment_id = allocation_segment_id(allocation.segments[0])
    requirement_one = RowingQualityRequirement(
        f"{segment_id}:threshold-one", RowingQualityFamily.THRESHOLD, "AT", 20, 15, 30,
        1, 1, 1, (("at_01", 35, 75),), 48,
    )
    requirement_two = RowingQualityRequirement(
        f"{segment_id}:threshold-two", RowingQualityFamily.THRESHOLD, "AT", 20, 15, 30,
        1, 1, 1, (("at_01", 35, 75),), 48,
    )
    plan = RowingQualityPlan(
        segment_id, allocation.planning_start, allocation.planning_end, 70, 0,
        (requirement_one, requirement_two), "test", None, "high",
        policy_version="r7-test", performance_profile_version="r7-test", basis_fingerprint="r7-test",
    )
    fixed = AuthoritativeComponentFact(
        "fixed-at", START, 35, modality=TrainingModality.ERG,
        purpose=TrainingComponentPurpose.THRESHOLD, physiological_band="AT",
        quality_work_minutes=20, user_fixed=True,
    )
    result = generate_training_days_v3(
        allocation, _capacity(35, 35, 35), (fixed,),
        rowing_quality_plans=(plan,), materialization_context=_context(),
    )
    fixed_component = next(item for item in _rowing(result) if item.source_lineage_id == "authority:fixed-at")
    assert fixed_component.quality_requirement_id is not None
    assert "user_fixed" in fixed_component.constraint_codes
    assert any(item.code == "user_fixed_quality_spacing_conflict" for item in result.diagnostics)


def test_known_race_familiarization_counts_once_as_technical_rowing():
    allocation = _allocation(80, days=2)
    familiarization = AuthoritativeComponentFact(
        "race-familiarization", START, 40, modality=TrainingModality.ON_WATER,
        purpose=TrainingComponentPurpose.TECHNICAL,
    )
    result = generate_training_days_v3(
        allocation, _capacity(40, 40), (familiarization,),
        rowing_quality_plans=(), materialization_context=_context(),
    )
    assert result.success
    rows = _rowing(result)
    assert sum(item.duration_minutes for item in rows) == 80
    assert sum(item.source_lineage_id == "authority:race-familiarization" for item in rows) == 1
