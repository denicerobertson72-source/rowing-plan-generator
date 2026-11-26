"""R9F.4b: R4 reuses the approved continuous sequence policy."""
from __future__ import annotations

from datetime import date, timedelta

from rowing_plan.materialization_v3 import MaterializationContext
from rowing_plan.diagnostic_v3 import _known_future_authoritative_components
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import (
    ComponentAuthority, HistoricalFact, HistoricalFactType, PhysiologicalCredit, PhysicalCategory,
    GeneralAerobicModalityCapability, Provenance, ProvenanceSource, QualityRequirementPriority,
    RowingQualityFamily,
    RowingQualityPlan, RowingQualityRequirement, TrainingAllocation,
    TrainingAllocationSegment, TrainingComponentPurpose, TrainingModality, TrainingRole,
)
from rowing_plan.planning_normalization import (
    AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext,
)
from rowing_plan.training_days import (
    DailyTrainingCapacity, OperationalSequenceContext, allocation_segment_id,
    evaluate_cadence_category_feasibility, generate_training_days_v3,
)
from rowing_plan.training_allocation import reconcile_training_allocation_for_cadence_feasibility


SUNDAY = date(2026, 1, 4)
MONDAY = SUNDAY + timedelta(days=1)


def _context(*, planning_date=SUNDAY, facts=(), races=()):
    return NormalizedPlanningContext(
        planning_date, tuple(facts), (), (),
        tuple((item.isoformat(), priority, "Race", None) for item, priority in races),
        AUTHORITY_PRECEDENCE, NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _fact(identifier, day, role, *, completed=True):
    actual = {"status": "completed", "actual_duration_min": 45}
    if role is TrainingRole.STRENGTH:
        actual["activity_type"] = "strength"
    elif role is TrainingRole.QUALITY:
        actual["actual_intensity"] = "AT"
    return HistoricalFact(
        identifier, day,
        HistoricalFactType.COMPLETED_PLANNED_SESSION if completed else HistoricalFactType.PRESCRIBED_SESSION,
        planned_prescription={"band": "AT"} if role is TrainingRole.QUALITY else None,
        completed_actual=actual if completed else None,
        physiological_credit=PhysiologicalCredit(role, 1, 45, True),
        provenance=Provenance(identifier, ProvenanceSource.COMPLETED_LOG, original_date=day),
    )


def _sequence(context):
    return OperationalSequenceContext(context, reduce_physiological_state(context))


def _allocation(start, segments):
    values = []
    for segment_start, segment_end, rowing, strength in segments:
        values.append(TrainingAllocationSegment(
            segment_start, segment_end, rowing + strength, rowing, strength, 0, 0, 0,
            strength // 45, 0, strength // 45, 45, rationale_codes=("test",),
        ))
    end = values[-1].end_date
    return TrainingAllocation(
        start, end, tuple(values), sum(item.recommended_physical_minutes for item in values),
        sum(item.rowing_specific_minutes for item in values), sum(item.strength_minutes for item in values),
        0, 0, sum(item.recommended_strength_exposures for item in values),
        rationale_codes=("test",), confidence="high",
    )


def _capacities(start, *minutes):
    return tuple(DailyTrainingCapacity(start + timedelta(days=index), value) for index, value in enumerate(minutes))


def _quality_plan(allocation):
    segment = allocation.segments[0]
    identifier = allocation_segment_id(segment)
    requirement = RowingQualityRequirement(
        "quality", RowingQualityFamily.THRESHOLD, "AT", 20, 15, 30, 1, 1, 1,
        (("at", 35, 75),), 24, priority=QualityRequirementPriority.HARD,
    )
    return RowingQualityPlan(
        identifier, segment.start_date, segment.end_date, segment.rowing_specific_minutes,
        0, (requirement,), "test", None, "high", policy_version="test",
        performance_profile_version="test", basis_fingerprint="test",
    )


def test_completed_historical_strength_blocks_monday_but_preserves_r3_minutes_on_tuesday():
    context = _context(
        planning_date=MONDAY,
        facts=(_fact("sunday-strength", SUNDAY, TrainingRole.STRENGTH),),
    )
    allocation = _allocation(MONDAY, ((MONDAY, MONDAY + timedelta(days=1), 0, 45),))

    result = generate_training_days_v3(
        allocation, _capacities(MONDAY, 45, 45), operational_sequence=_sequence(context),
    )

    assert result.success
    assert not result.days[0].components
    assert [item.date for item in result.days if item.components] == [MONDAY + timedelta(days=1)]
    assert any(item.code == "continuous_sequence_candidate_rejections" for item in result.diagnostics)


def test_generated_sunday_strength_continues_across_monday_cadence_boundary():
    context = _context(planning_date=SUNDAY)
    allocation = _allocation(SUNDAY, (
        (SUNDAY, SUNDAY, 0, 45),
        (MONDAY, MONDAY + timedelta(days=1), 0, 45),
    ))

    result = generate_training_days_v3(
        allocation, _capacities(SUNDAY, 45, 45, 45), operational_sequence=_sequence(context),
    )

    assert result.success
    strength_dates = [day.date for day in result.days if any(
        item.physical_category is PhysicalCategory.STRENGTH for item in day.components
    )]
    assert strength_dates == [SUNDAY, MONDAY + timedelta(days=1)]


def test_planned_uncompleted_history_does_not_create_operational_spacing():
    context = _context(
        planning_date=MONDAY,
        facts=(_fact("planned-strength", SUNDAY, TrainingRole.STRENGTH, completed=False),),
    )
    allocation = _allocation(MONDAY, ((MONDAY, MONDAY, 0, 45),))

    result = generate_training_days_v3(
        allocation, _capacities(MONDAY, 45), operational_sequence=_sequence(context),
    )

    assert result.success
    assert result.days[0].components[0].physical_category is PhysicalCategory.STRENGTH


def test_completed_historical_quality_blocks_next_day_generated_quality_but_not_r3_conservation():
    context = _context(
        planning_date=MONDAY,
        facts=(_fact("sunday-quality", SUNDAY, TrainingRole.QUALITY),),
    )
    allocation = _allocation(MONDAY, ((MONDAY, MONDAY + timedelta(days=1), 35, 0),))
    result = generate_training_days_v3(
        allocation, _capacities(MONDAY, 60, 60), rowing_quality_plans=(_quality_plan(allocation),),
        materialization_context=MaterializationContext(phase_id="threshold_development", experience="experienced"),
        operational_sequence=_sequence(context),
    )

    assert result.success
    quality = [item for item in result.components if item.quality_requirement_id]
    assert len(quality) == 1
    assert next(day.date for day in result.days if quality[0] in day.components) == MONDAY + timedelta(days=1)


def test_future_race_hard_recovery_produces_explicit_r4_sequence_infeasibility():
    context = _context(planning_date=SUNDAY, races=((SUNDAY, "B"),))
    # The allocation begins after the state as-of date but remains inside one
    # chronological context, so it must see Sunday's formal race.
    allocation = _allocation(MONDAY, ((MONDAY, MONDAY, 0, 45),))
    result = generate_training_days_v3(
        allocation, _capacities(SUNDAY + timedelta(days=1), 45), operational_sequence=_sequence(context),
    )

    assert not result.success
    assert any(item.code == "continuous_sequence_infeasible" for item in result.infeasibilities)
    assert any(item.code == "continuous_sequence_candidate_rejections" for item in result.diagnostics)


def test_r3_preflight_and_final_r4_share_the_formal_race_constraint_without_rewriting_dose():
    context = _context(planning_date=SUNDAY, races=((SUNDAY, "B"),))
    allocation = _allocation(MONDAY, ((MONDAY, MONDAY, 0, 45),))
    capacity = _capacities(MONDAY, 45)

    preflight = evaluate_cadence_category_feasibility(
        allocation.segments[0], capacity, operational_sequence=_sequence(context),
    )
    reconciled = reconcile_training_allocation_for_cadence_feasibility(
        allocation, capacity=capacity, operational_sequence=_sequence(context),
    )
    final = generate_training_days_v3(
        reconciled.allocation, capacity, operational_sequence=_sequence(context),
    )

    assert not preflight.feasible
    assert preflight.classification == "existing_physiological_sequence_constraint_conflict"
    assert any(code.startswith("projected_race_hard_recovery:") for code in preflight.sequence_constraint_codes)
    assert preflight.quality_specific_feasibility == "deferred_until_r6"
    assert reconciled.success
    segment = reconciled.allocation.segments[0]
    assert (segment.recommended_physical_minutes, segment.strength_minutes) == (45, 45)
    assert segment.category_feasibility_final is False
    assert segment.category_feasibility_requires_upstream_reconciliation is True
    assert not final.success
    assert any(item.code == "continuous_sequence_infeasible" for item in final.infeasibilities)


def test_r3_preflight_carries_completed_spacing_across_a_cadence_boundary_immutably():
    context = _context(
        planning_date=MONDAY,
        facts=(_fact("sunday-strength", SUNDAY, TrainingRole.STRENGTH),),
    )
    allocation = _allocation(MONDAY, ((MONDAY, MONDAY + timedelta(days=1), 0, 45),))
    capacity = _capacities(MONDAY, 45, 45)

    first = evaluate_cadence_category_feasibility(
        allocation.segments[0], capacity, operational_sequence=_sequence(context),
    )
    second = evaluate_cadence_category_feasibility(
        allocation.segments[0], capacity, operational_sequence=_sequence(context),
    )
    final = generate_training_days_v3(
        allocation, capacity, operational_sequence=_sequence(context),
    )

    assert (first.allocation_segment_id, first.feasible, first.infeasibility_codes,
            first.classification, first.sequence_constraint_codes,
            first.quality_specific_feasibility) == (
        second.allocation_segment_id, second.feasible, second.infeasibility_codes,
        second.classification, second.sequence_constraint_codes,
        second.quality_specific_feasibility,
    )
    assert first.feasible
    assert first.quality_specific_feasibility == "deferred_until_r6"
    assert final.success
    assert [item.date for item in final.days if item.components] == [MONDAY + timedelta(days=1)]


def test_r3_cross_cadence_preflight_projects_earlier_generated_strength_before_final_r4():
    context = _context(planning_date=SUNDAY)
    allocation = _allocation(SUNDAY, (
        (SUNDAY, SUNDAY, 0, 45),
        (MONDAY, MONDAY, 0, 45),
    ))
    capacity = _capacities(SUNDAY, 45, 45)

    reconciled = reconcile_training_allocation_for_cadence_feasibility(
        allocation, capacity=capacity, operational_sequence=_sequence(context),
    )
    final = generate_training_days_v3(
        reconciled.allocation, capacity, operational_sequence=_sequence(context),
    )

    assert reconciled.success
    first, second = reconciled.allocation.segments
    assert first.category_feasibility_final is True
    assert second.category_feasibility_final is False
    assert second.category_feasibility_requires_upstream_reconciliation is True
    assert "projected_spacing" in " ".join(second.category_feasibility_codes)
    assert not final.success
    assert any(item.code == "continuous_sequence_infeasible" for item in final.infeasibilities)


def test_soft_race_recovery_is_accepted_once_with_a_compact_sequence_diagnostic():
    tuesday = MONDAY + timedelta(days=1)
    context = _context(planning_date=SUNDAY, races=((SUNDAY, "C"),))
    allocation = _allocation(tuesday, ((tuesday, tuesday, 0, 45),))

    result = generate_training_days_v3(
        allocation, _capacities(tuesday, 45), operational_sequence=_sequence(context),
    )

    accepted = [item for item in result.diagnostics if item.code == "continuous_sequence_accepted_constraint"]
    assert result.success
    assert len(accepted) == 1
    assert accepted[0].date == tuesday
    assert "projected_race_soft_recovery:" in accepted[0].detail


def test_reliably_typed_future_user_fixed_authority_is_shared_by_r3_probe_and_final_r4_only():
    context = _context(planning_date=MONDAY)
    placement = {
        "placement_id": "fixed-strength", "date": MONDAY.isoformat(),
        "role": "strength", "minutes": 45, "user_fixed": True,
    }
    authorities = _known_future_authoritative_components((placement,), planning_start=MONDAY)
    assert [item.authority_id for item in authorities] == ["user_fixed:fixed-strength"]
    assert _known_future_authoritative_components(({
        **placement, "placement_id": "ordinary-provisional", "user_fixed": False,
    },), planning_start=MONDAY) == ()

    allocation = _allocation(MONDAY, ((MONDAY, MONDAY, 0, 45),))
    capacity = _capacities(MONDAY, 45)
    r3 = reconcile_training_allocation_for_cadence_feasibility(
        allocation, capacity=capacity, authorities=authorities,
        operational_sequence=_sequence(context),
    )
    final = generate_training_days_v3(
        r3.allocation, capacity, authorities=authorities,
        operational_sequence=_sequence(context),
    )

    assert r3.success and r3.allocation.segments[0].category_feasibility_final is True
    assert final.success
    assert len(final.components) == 1
    component = final.components[0]
    assert component.authority is ComponentAuthority.AUTHORITATIVE
    assert component.source_lineage_id == "authority:user_fixed:fixed-strength"


def test_same_day_strength_and_easy_rowing_remain_allowed_and_deterministic():
    context = _context(planning_date=MONDAY)
    allocation = _allocation(MONDAY, ((MONDAY, MONDAY, 20, 45),))
    capacity = _capacities(MONDAY, 65)

    first = generate_training_days_v3(allocation, capacity, operational_sequence=_sequence(context))
    second = generate_training_days_v3(allocation, capacity, operational_sequence=_sequence(context))

    assert first == second
    assert first.success
    assert [item.physical_category for item in first.days[0].components] == [
        PhysicalCategory.STRENGTH, PhysicalCategory.ROWING_SPECIFIC,
    ]


def test_r3_reconciliation_continues_after_candidate_rollbacks_and_preserves_r6_ownership():
    """A rejected initial R4 candidate is not proof that every R3 split fails.

    Sunday's strength becomes shared operational-sequence context for the
    following cadence segment.  The initial Monday--Thursday split strands a
    strength exposure, while the existing authorised 10-minute rowing to
    supplemental-aerobic reconciliation makes the same total dose feasible.
    R6 is intentionally supplied only for final R4 placement; R3 must not
    fabricate a quality requirement merely to preflight the physical split.
    """
    first = TrainingAllocationSegment(
        SUNDAY, SUNDAY, 45, 0, 45, 0, 0, 0,
        1, 0, 1, 45, rationale_codes=("test",),
    )
    capability = GeneralAerobicModalityCapability(
        TrainingModality.STAIR_CLIMBER,
        familiarity="regular",
        preference="prefer",
        provenance=("test:authorised_supplemental_aerobic",),
    )
    second = TrainingAllocationSegment(
        MONDAY, MONDAY + timedelta(days=3), 265, 220, 45, 0, 0, 137,
        1, 0, 1, 45, rationale_codes=("test",),
        general_aerobic_capabilities=(capability,),
    )
    allocation = TrainingAllocation(
        SUNDAY, second.end_date, (first, second), 310, 220, 90, 0, 0, 2,
        rationale_codes=("test",), confidence="high",
    )
    capacity = _capacities(SUNDAY, 70, 70, 70, 70, 70)
    sequence = _sequence(_context(planning_date=SUNDAY))

    reconciled = reconcile_training_allocation_for_cadence_feasibility(
        allocation, capacity=capacity, operational_sequence=sequence,
    )

    assert reconciled.success
    accepted = reconciled.allocation.segments[1]
    assert (
        accepted.recommended_physical_minutes,
        accepted.rowing_specific_minutes,
        accepted.strength_minutes,
        accepted.general_aerobic_minutes,
        accepted.category_reconciliation_minutes,
    ) == (265, 210, 45, 10, 10)
    assert accepted.category_reconciliation_reason == "cadence_envelope_feasibility_rowing_to_general_aerobic"
    assert accepted.category_feasibility_final is True
    assert accepted.category_feasibility_requires_upstream_reconciliation is False
    assert (
        reconciled.allocation.recommended_physical_minutes,
        reconciled.allocation.rowing_specific_minutes,
        reconciled.allocation.strength_minutes,
        reconciled.allocation.general_aerobic_minutes,
    ) == (310, 210, 90, 10)

    # This is an actual R6 plan used downstream.  It remains R6-owned, and
    # R3's physical preflight has not invented or rewritten its requirement.
    requirement = RowingQualityRequirement(
        "r6-aerobic-strength", RowingQualityFamily.AEROBIC_STRENGTH, "UT1",
        20, 15, 30, 1, 1, 1, (("ut1", 35, 60),), 24,
        priority=QualityRequirementPriority.PREFERRED,
    )
    quality_plan = RowingQualityPlan(
        allocation_segment_id(accepted), accepted.start_date, accepted.end_date,
        accepted.rowing_specific_minutes, 0, (requirement,), "test", None, "high",
        policy_version="test", performance_profile_version="test", basis_fingerprint="test",
    )
    final = generate_training_days_v3(
        reconciled.allocation, capacity, rowing_quality_plans=(quality_plan,),
        operational_sequence=sequence,
    )

    assert final.success
    assert any(item.code == "continuous_sequence_candidate_rejections" for item in final.diagnostics)
    assert quality_plan.requirements == (requirement,)
    assert sum(item.duration_minutes for item in final.components) == 310


def test_r3_preserves_upstream_allocation_when_sequence_candidate_rollbacks_exhaust_allowed_alternatives():
    """Unresolved candidate rollbacks never authorise an unapproved transfer."""
    first = TrainingAllocationSegment(
        SUNDAY, SUNDAY, 45, 0, 45, 0, 0, 0,
        1, 0, 1, 45, rationale_codes=("test",),
    )
    # The second segment deliberately has no athlete-approved general-aerobic
    # capability.  R3 may not invent one merely to evade the sequence-aware
    # R4 rejection.
    second = TrainingAllocationSegment(
        MONDAY, MONDAY + timedelta(days=3), 265, 220, 45, 0, 0, 137,
        1, 0, 1, 45, rationale_codes=("test",),
    )
    allocation = TrainingAllocation(
        SUNDAY, second.end_date, (first, second), 310, 220, 90, 0, 0, 2,
        rationale_codes=("test",), confidence="high",
    )
    capacity = _capacities(SUNDAY, 70, 70, 70, 70, 70)
    sequence = _sequence(_context(planning_date=SUNDAY))

    reconciled = reconcile_training_allocation_for_cadence_feasibility(
        allocation, capacity=capacity, operational_sequence=sequence,
    )

    assert reconciled.success
    unresolved = reconciled.allocation.segments[1]
    assert (
        reconciled.allocation.recommended_physical_minutes,
        reconciled.allocation.rowing_specific_minutes,
        reconciled.allocation.strength_minutes,
        reconciled.allocation.general_aerobic_minutes,
    ) == (310, 220, 90, 0)
    assert unresolved.category_reconciliation_minutes == 0
    assert unresolved.category_feasibility_final is False
    assert unresolved.category_feasibility_requires_upstream_reconciliation is True
    assert unresolved.category_feasibility_classification == (
        "candidate_rejections_unresolved_requires_upstream_reconciliation"
    )
    assert "continuous_sequence_candidate_rejections" in unresolved.category_feasibility_codes

    ordinary_plan = RowingQualityPlan(
        allocation_segment_id(unresolved), unresolved.start_date, unresolved.end_date,
        unresolved.rowing_specific_minutes, 0, (), "ordinary_aerobic", None, "high",
        policy_version="test", performance_profile_version="test", basis_fingerprint="test",
    )
    final = generate_training_days_v3(
        reconciled.allocation, capacity, rowing_quality_plans=(ordinary_plan,),
        operational_sequence=sequence,
    )
    assert not final.success
    assert sum(item.duration_minutes for item in final.components) < 310
