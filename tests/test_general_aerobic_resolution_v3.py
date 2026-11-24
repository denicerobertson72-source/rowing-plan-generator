"""R9E.2a contracts for profile-backed general-aerobic materialization."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from rowing_plan.diagnostic_v3 import generate_v3_diagnostic_report
from rowing_plan.materialization_v3 import MaterializationContext, materialize_training_days_v3
from rowing_plan.planning_domain import (
    GeneralAerobicModalityCapability,
    CurrentTrainingSource,
    CurrentTrainingState,
    PhysicalCategory,
    PhysiologicalState,
    RecommendedDoseSegment,
    RecommendedTrainingDose,
    RowingQualityPlan,
    TrainingAllocation,
    TrainingAllocationSegment,
    TrainingModality,
)
from rowing_plan.training_allocation import (
    allocate_training_dose,
    general_aerobic_capabilities,
    reconcile_training_allocation_for_cadence_feasibility,
)
from rowing_plan.training_days import (
    AuthoritativeComponentFact,
    CombinedDayPreferences,
    DailyTrainingCapacity,
    allocation_segment_id,
    evaluate_cadence_category_feasibility,
    generate_training_days_v3,
)


START = date(2026, 12, 28)


def _capability(modality: TrainingModality, *, preference: str = "neutral", familiarity: str | None = "regular"):
    return GeneralAerobicModalityCapability(
        modality, familiarity, preference,
        (f"profile_cross_training:{modality.value}:enabled",),
    )


def _allocation(*, rowing: int = 0, strength: int = 45, general: int = 15,
                days: int = 1, capabilities=(), supplemental_max: int | None = None,
                supplemental_only: bool = False):
    end = START + timedelta(days=days - 1)
    total = rowing + strength + general
    segment = TrainingAllocationSegment(
        START, end, total, rowing, strength, general, 0, 0,
        strength // 45, 0, strength // 45, 45,
        rationale_codes=("r9e_2a_test",),
        general_aerobic_capabilities=tuple(capabilities),
        deliberate_general_aerobic_minutes=general if supplemental_only else 0,
        general_aerobic_supplemental_max_minutes=supplemental_max,
        general_aerobic_supplemental_only=supplemental_only,
    )
    return TrainingAllocation(
        START, end, (segment,), total, rowing, strength, general, 0,
        strength // 45, rationale_codes=("r9e_2a_test",), confidence="high",
    )


def _empty_quality_plan(allocation: TrainingAllocation) -> RowingQualityPlan:
    segment = allocation.segments[0]
    return RowingQualityPlan(
        allocation_segment_id(segment), segment.start_date, segment.end_date,
        0, 0, (), "r9e_2a", None, "high", policy_version="test",
        performance_profile_version="test", basis_fingerprint="r9e_2a",
    )


@pytest.mark.parametrize("modality", [
    TrainingModality.BIKE,
    TrainingModality.ELLIPTICAL,
    TrainingModality.STAIR_CLIMBER,
    TrainingModality.RUNNING,
])
def test_enabled_profile_modality_resolves_and_materializes_a_short_strength_add_on(modality):
    allocation = _allocation(capabilities=(_capability(modality, preference="prefer"),))
    r4 = generate_training_days_v3(allocation, (DailyTrainingCapacity(START, 60),))
    r5 = materialize_training_days_v3(r4, allocation=allocation)

    general = next(item for item in r4.components if item.physical_category is PhysicalCategory.GENERAL_AEROBIC)
    assert r4.success and r5.success
    assert (general.duration_minutes, general.modality, general.purpose) == (15, modality, general.purpose.EASY_AEROBIC)
    assert "r4:general_aerobic_modality_resolution" in general.capability_provenance
    assert sum(item.duration_minutes for item in r4.components if item.physical_category is PhysicalCategory.GENERAL_AEROBIC) == 15
    assert sum(item.duration_minutes for item in r4.components if item.physical_category is PhysicalCategory.ROWING_SPECIFIC) == 0


def test_disabled_and_avoided_profile_modalities_are_not_allocation_capabilities():
    profile = {
        "cross_training": {"modalities": {
            "bike": {"enabled": False, "familiarity": "regular", "preference": "prefer"},
            "elliptical": {"enabled": True, "familiarity": "regular", "preference": "avoid"},
            "stair_climber": {"enabled": True, "familiarity": "regular", "preference": "neutral"},
        }},
    }
    assert [item.modality for item in general_aerobic_capabilities(profile)] == [TrainingModality.STAIR_CLIMBER]


def test_multiple_eligible_modalities_resolve_by_preference_then_familiarity_then_modality():
    allocation = _allocation(capabilities=(
        _capability(TrainingModality.ELLIPTICAL, preference="neutral", familiarity="regular"),
        _capability(TrainingModality.BIKE, preference="prefer", familiarity="new"),
        _capability(TrainingModality.STAIR_CLIMBER, preference="prefer", familiarity="regular"),
    ))
    result = generate_training_days_v3(allocation, (DailyTrainingCapacity(START, 60),))
    general = next(item for item in result.components if item.physical_category is PhysicalCategory.GENERAL_AEROBIC)
    assert result.success
    assert general.modality is TrainingModality.STAIR_CLIMBER


def test_r3_authorized_general_aerobic_without_an_eligible_modality_is_explicitly_infeasible():
    allocation = _allocation(capabilities=())
    result = generate_training_days_v3(allocation, (DailyTrainingCapacity(START, 60),))
    assert not result.success
    assert any(item.code == "general_aerobic_modality_unresolved" for item in result.infeasibilities)
    assert not any(item.physical_category is PhysicalCategory.GENERAL_AEROBIC for item in result.components)


def test_combined_day_limit_is_respected_for_resolved_general_aerobic_work():
    allocation = _allocation(capabilities=(_capability(TrainingModality.STAIR_CLIMBER),))
    result = generate_training_days_v3(
        allocation, (DailyTrainingCapacity(START, 60),),
        preferences=CombinedDayPreferences(maximum_combined_days_per_week=0),
    )
    assert not result.success
    assert any(item.code == "generated_component_capacity_infeasible" for item in result.infeasibilities)
    assert all(not day.combined_day for day in result.days)


@pytest.mark.parametrize(
    ("minutes", "expected"),
    ((30, (15, 15)), (26, (13, 13)), (10, (10,)), (15, (15,))),
)
def test_supplemental_general_aerobic_uses_balanced_meaningful_exact_partitions(minutes, expected):
    strength = 45 * len(expected)
    allocation = _allocation(
        strength=strength, general=minutes, days=len(expected),
        capabilities=(_capability(TrainingModality.STAIR_CLIMBER, preference="prefer"),),
        supplemental_max=30, supplemental_only=True,
    )
    capacity = tuple(DailyTrainingCapacity(START + timedelta(days=index), 70) for index in range(len(expected)))
    result = generate_training_days_v3(allocation, capacity)
    preflight = evaluate_cadence_category_feasibility(allocation.segments[0], capacity)

    durations = tuple(sorted(
        item.duration_minutes for item in result.components
        if item.physical_category is PhysicalCategory.GENERAL_AEROBIC
    ))
    assert result.success and preflight.feasible
    assert durations == expected
    assert all(10 <= duration <= 15 for duration in durations)
    assert all(
        any(item.physical_category is PhysicalCategory.STRENGTH for item in day.components)
        for day in result.days if any(item.physical_category is PhysicalCategory.GENERAL_AEROBIC for item in day.components)
    )


def test_supplemental_general_aerobic_uses_one_longer_component_only_when_one_strength_day_makes_it_necessary():
    allocation = _allocation(
        strength=45, general=26, days=1,
        capabilities=(_capability(TrainingModality.STAIR_CLIMBER),),
        supplemental_max=30, supplemental_only=True,
    )
    result = generate_training_days_v3(allocation, (DailyTrainingCapacity(START, 75),))
    general = [item for item in result.components if item.physical_category is PhysicalCategory.GENERAL_AEROBIC]
    assert result.success
    assert [item.duration_minutes for item in general] == [26]


def test_supplemental_general_aerobic_below_minimum_is_explicitly_infeasible_not_materialized_as_a_fragment():
    allocation = _allocation(
        strength=45, general=5, days=1,
        capabilities=(_capability(TrainingModality.STAIR_CLIMBER),),
        supplemental_max=15, supplemental_only=True,
    )
    result = generate_training_days_v3(allocation, (DailyTrainingCapacity(START, 60),))
    assert not result.success
    assert "supplemental_general_aerobic_partition_infeasible" in {item.code for item in result.infeasibilities}
    assert not any(item.physical_category is PhysicalCategory.GENERAL_AEROBIC for item in result.components)


def test_supplemental_duration_policy_conflict_is_not_authorized_by_r3():
    profile = {
        "athlete": {"current_approx_weekly_rowing_minutes": 120, "current_weekly_training_minutes": 260},
        "season": {"start_date": START.isoformat(), "end_date": "2027-01-03", "phase_type": "general_preparation"},
        "training_context": {"current_strength_sessions_per_week": 1, "typical_strength_duration_minutes": 45},
        "planning_goals": {"primary_goal": "build_endurance"},
        "cross_training": {"modalities": {"stair_climber": {"enabled": True, "preference": "prefer"}}},
        "recurring_activities": [{"activity_type": "strength", "alternate_cardio": {"mode": "optional", "max_minutes": 5}}],
    }
    dose = RecommendedTrainingDose(
        START, START + timedelta(days=6),
        (RecommendedDoseSegment(START, START + timedelta(days=6), 260, 260, 260, 1000, 0, "ordinary", ("test",)),),
        260, 260, 260, 1000, 0, "maintenance", "maintain", "moderate", policy_version="test",
    )
    state = CurrentTrainingState(
        CurrentTrainingSource.ATHLETE_ESTIMATE,
        approximate_total_physical_minutes=260, approximate_rowing_minutes=120, strength_exposures=1,
        typical_strength_duration_minutes=45, consistency="regular", confidence="moderate",
    )
    result = allocate_training_dose(dose, profile=profile, current_state=state, state=PhysiologicalState(as_of_date=START))
    segment = result.allocation.segments[0]
    assert segment.deliberate_general_aerobic_minutes == 0
    assert "general_aerobic:supplemental_duration_policy_conflict" in segment.rationale_codes


def test_production_shaped_supplemental_distribution_is_exact_balanced_and_materializable():
    spans = (6, 7, 7, 7, 7, 4)
    general_minutes = (26, 30, 30, 30, 30, 15)
    strength_exposures = (2, 3, 3, 3, 3, 1)
    expected_partitions = ((13, 13), (15, 15), (15, 15), (15, 15), (15, 15), (15,))
    capability = _capability(TrainingModality.STAIR_CLIMBER, preference="prefer")
    cursor = START
    segments = []
    for days, general, exposures in zip(spans, general_minutes, strength_exposures):
        end = cursor + timedelta(days=days - 1)
        strength = exposures * 45
        segments.append(TrainingAllocationSegment(
            cursor, end, strength + general, 0, strength, general, 0, 0,
            exposures, 0, exposures, 45, rationale_codes=("production_shaped_supplement",),
            general_aerobic_capabilities=(capability,),
            deliberate_general_aerobic_minutes=general,
            general_aerobic_supplemental_max_minutes=30,
            general_aerobic_supplemental_only=True,
        ))
        cursor = end + timedelta(days=1)
    total = sum(item.recommended_physical_minutes for item in segments)
    allocation = TrainingAllocation(
        START, cursor - timedelta(days=1), tuple(segments), total, 0,
        sum(item.strength_minutes for item in segments), sum(general_minutes), 0,
        sum(strength_exposures), rationale_codes=("production_shaped_supplement",), confidence="high",
    )
    capacities = tuple(DailyTrainingCapacity(day, 70) for day in (START + timedelta(days=index) for index in range(38)))
    r4 = generate_training_days_v3(allocation, capacities)
    r5 = materialize_training_days_v3(r4, allocation=allocation)

    assert r4.success and r5.success
    assert sum(component.duration_minutes for component in r4.components) == total
    assert sum(component.duration_minutes for component in r4.components if component.physical_category is PhysicalCategory.STRENGTH) == 675
    assert sum(component.duration_minutes for component in r4.components if component.physical_category is PhysicalCategory.GENERAL_AEROBIC) == 161
    for segment, expected in zip(segments, expected_partitions):
        segment_id = allocation_segment_id(segment)
        actual = tuple(sorted(
            component.duration_minutes for component in r4.components
            if component.allocation_segment_id == segment_id
            and component.physical_category is PhysicalCategory.GENERAL_AEROBIC
        ))
        assert actual == expected


def test_partial_cadence_segment_conserves_205_rowing_45_strength_and_15_general_aerobic():
    allocation = _allocation(
        rowing=205, strength=45, general=15, days=4,
        capabilities=(_capability(TrainingModality.STAIR_CLIMBER, preference="prefer"),),
    )
    capacity = tuple(DailyTrainingCapacity(START + timedelta(days=index), 70) for index in range(4))
    r4 = generate_training_days_v3(
        allocation, capacity, rowing_quality_plans=(_empty_quality_plan(allocation),),
        materialization_context=MaterializationContext(phase_id="general_preparation", experience="intermediate"),
    )
    r5 = materialize_training_days_v3(r4, allocation=allocation)

    assert r4.success and r5.success
    by_category = {
        category: sum(item.duration_minutes for item in r4.components if item.physical_category is category)
        for category in PhysicalCategory
    }
    assert by_category == {
        PhysicalCategory.ROWING_SPECIFIC: 205,
        PhysicalCategory.STRENGTH: 45,
        PhysicalCategory.GENERAL_AEROBIC: 15,
        PhysicalCategory.OTHER: 0,
    }
    general = next(item for item in r4.components if item.physical_category is PhysicalCategory.GENERAL_AEROBIC)
    assert general.modality is TrainingModality.STAIR_CLIMBER
    assert [day.total_physical_minutes for day in r4.days] == [60, 68, 68, 69]
    assert sum(item.materialized.physical_duration_minutes for item in r5.results if item.materialized) == 265


def test_persisted_diagnostic_exposes_r3_capability_and_r4_selected_modality():
    profile = {
        "athlete": {"current_weekly_training_minutes": 380, "current_approx_weekly_rowing_minutes": 240},
        "season": {"start_date": "2026-12-28", "end_date": "2027-01-03", "phase_type": "general_preparation"},
        "training_context": {
            "strength_experience": "advanced", "current_strength_state": "regular",
            "current_strength_sessions_per_week": 1, "typical_strength_duration_minutes": 45,
            "strength_override": {"mode": "none"},
        },
        "planning_goals": {"primary_goal": "develop_strength"},
        "cross_training": {"modalities": {
            "stair_climber": {"enabled": True, "familiarity": "regular", "preference": "prefer"},
        }},
        "weekly_availability": [
            {"weekday": name, "available": True, "fixed_rest": False, "max_training_minutes": 120, "rowing_modes": ["erg"]}
            for name in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        ],
        "races": [],
    }
    report = generate_v3_diagnostic_report(profile, planning_date=START)
    segment = report["r3"]["segments"][0]
    calendar_components = [item for day in report["calendar"] for item in day["components"]]
    general = next(item for item in calendar_components if item["category"] == "general_aerobic")
    assert segment["general_aerobic_capabilities"][0]["modality"] is TrainingModality.STAIR_CLIMBER
    assert general["modality"] == "stair_climber"
    assert "r4:general_aerobic_modality_resolution" in general["capability_provenance"]


def _december_28_allocation(*, capabilities=(), rowing_floor=137):
    return _allocation(
        rowing=220, strength=45, general=0, days=4,
        capabilities=capabilities,
    ), tuple(DailyTrainingCapacity(START + timedelta(days=index), 70) for index in range(4))


def test_cadence_feasibility_preflight_reconciles_only_when_the_original_category_split_is_impossible():
    allocation, capacity = _december_28_allocation(
        capabilities=(_capability(TrainingModality.STAIR_CLIMBER, preference="prefer"),),
    )
    original = allocation.segments[0]
    # The production-shaped floor is the preserved rowing-specific minimum,
    # not a new total-dose target.
    original = TrainingAllocationSegment(
        original.start_date, original.end_date, 265, 220, 45, 0, 0, 137,
        1, 0, 1, 45, rationale_codes=("r9e_2b",),
        general_aerobic_capabilities=original.general_aerobic_capabilities,
    )
    allocation = TrainingAllocation(
        original.start_date, original.end_date, (original,), 265, 220, 45, 0, 0, 1,
        rationale_codes=("r9e_2b",), confidence="high",
    )
    preflight = evaluate_cadence_category_feasibility(original, capacity)
    reconciled = reconcile_training_allocation_for_cadence_feasibility(allocation, capacity=capacity)

    assert not preflight.feasible
    assert "joint_strength_rowing_partition_infeasible" in preflight.infeasibility_codes
    assert reconciled.success
    segment = reconciled.allocation.segments[0]
    assert segment.recommended_physical_minutes == 265
    assert segment.strength_minutes == 45 and segment.recommended_strength_exposures == 1
    assert segment.rowing_specific_minutes >= segment.rowing_specific_floor_minutes == 137
    assert segment.general_aerobic_minutes == segment.category_reconciliation_minutes
    assert 0 < segment.category_reconciliation_minutes <= 17
    assert segment.category_reconciliation_reason == "cadence_envelope_feasibility_rowing_to_general_aerobic"
    assert segment.category_feasibility_original is False
    assert segment.category_feasibility_final is True

    r4 = generate_training_days_v3(
        reconciled.allocation, capacity,
        rowing_quality_plans=(_empty_quality_plan(reconciled.allocation),),
        materialization_context=MaterializationContext(phase_id="general_preparation", experience="intermediate"),
    )
    r5 = materialize_training_days_v3(r4, allocation=reconciled.allocation)
    assert r4.success and r5.success
    assert sum(item.duration_minutes for item in r4.components) == 265
    assert sum(item.duration_minutes for item in r4.components if item.physical_category is PhysicalCategory.ROWING_SPECIFIC) == segment.rowing_specific_minutes
    assert sum(item.duration_minutes for item in r4.components if item.physical_category is PhysicalCategory.GENERAL_AEROBIC) == segment.general_aerobic_minutes


def test_cadence_reconciliation_never_converts_rowing_without_an_eligible_modality_or_below_floor():
    no_capabilities, capacity = _december_28_allocation()
    original = TrainingAllocationSegment(
        START, START + timedelta(days=3), 265, 220, 45, 0, 0, 137,
        1, 0, 1, 45, rationale_codes=("r9e_2b",),
    )
    no_capabilities = TrainingAllocation(
        START, START + timedelta(days=3), (original,), 265, 220, 45, 0, 0, 1,
        rationale_codes=("r9e_2b",), confidence="high",
    )
    missing = reconcile_training_allocation_for_cadence_feasibility(no_capabilities, capacity=capacity)
    assert not missing.success
    assert "no_eligible_general_aerobic_modality" in missing.infeasibility.conflicting_constraint_provenance

    capability = _capability(TrainingModality.STAIR_CLIMBER)
    floor_bound = TrainingAllocationSegment(
        START, START + timedelta(days=3), 265, 220, 45, 0, 0, 215,
        1, 0, 1, 45, rationale_codes=("r9e_2b",), general_aerobic_capabilities=(capability,),
    )
    floor_allocation = TrainingAllocation(
        START, START + timedelta(days=3), (floor_bound,), 265, 220, 45, 0, 0, 1,
        rationale_codes=("r9e_2b",), confidence="high",
    )
    blocked = reconcile_training_allocation_for_cadence_feasibility(floor_allocation, capacity=capacity)
    assert not blocked.success


def test_already_feasible_cadence_allocation_is_not_reconciled():
    allocation = _allocation(
        rowing=205, strength=45, general=15, days=4,
        capabilities=(_capability(TrainingModality.STAIR_CLIMBER),),
    )
    capacity = tuple(DailyTrainingCapacity(START + timedelta(days=index), 70) for index in range(4))
    result = reconcile_training_allocation_for_cadence_feasibility(allocation, capacity=capacity)
    assert result.success
    segment = result.allocation.segments[0]
    assert segment.rowing_specific_minutes == 205
    assert segment.general_aerobic_minutes == 15
    assert segment.category_reconciliation_minutes == 0
    assert segment.category_feasibility_original is True


def test_cadence_preflight_respects_combined_day_limits_and_pinned_authority():
    capability = _capability(TrainingModality.STAIR_CLIMBER, preference="prefer")
    segment = TrainingAllocationSegment(
        START, START + timedelta(days=3), 265, 220, 45, 0, 0, 137,
        1, 0, 1, 45, rationale_codes=("r9e_2b",), general_aerobic_capabilities=(capability,),
    )
    allocation = TrainingAllocation(
        START, START + timedelta(days=3), (segment,), 265, 220, 45, 0, 0, 1,
        rationale_codes=("r9e_2b",), confidence="high",
    )
    capacity = tuple(DailyTrainingCapacity(START + timedelta(days=index), 70) for index in range(4))
    pinned_strength = AuthoritativeComponentFact(
        "locked-strength", START, 45, modality=TrainingModality.STRENGTH,
    )
    # The pinned strength remains on its date; the candidate is not made
    # feasible by moving it.  With no combined day allowed, no valid general
    # add-on can occupy the residual 25-minute envelope.
    blocked = reconcile_training_allocation_for_cadence_feasibility(
        allocation, capacity=capacity, authorities=(pinned_strength,),
        preferences=CombinedDayPreferences(maximum_combined_days_per_week=0),
    )
    assert not blocked.success

    reconciled = reconcile_training_allocation_for_cadence_feasibility(
        allocation, capacity=capacity, authorities=(pinned_strength,),
    )
    assert reconciled.success
    r4 = generate_training_days_v3(
        reconciled.allocation, capacity, authorities=(pinned_strength,),
        rowing_quality_plans=(_empty_quality_plan(reconciled.allocation),),
    )
    pinned = next(item for item in r4.components if item.source_lineage_id == "authority:locked-strength")
    assert r4.success and pinned.duration_minutes == 45


def test_persisted_shaped_six_segment_block_preserves_all_r2_minutes_after_bounded_reconciliation():
    start = date(2026, 11, 24)
    lengths = (6, 7, 7, 7, 7, 4)
    totals = (436, 503, 503, 503, 504, 265)
    rowing = (346, 368, 368, 368, 369, 220)
    strength = (90, 135, 135, 135, 135, 45)
    capability = _capability(TrainingModality.STAIR_CLIMBER, preference="prefer")
    segments = []
    cursor = start
    for length, total, rowing_minutes, strength_minutes in zip(lengths, totals, rowing, strength):
        end = cursor + timedelta(days=length - 1)
        segments.append(TrainingAllocationSegment(
            cursor, end, total, rowing_minutes, strength_minutes, 0, 0,
            137 if length == 4 else 0,
            strength_minutes // 45, 0, strength_minutes // 45, 45,
            rationale_codes=("persisted_shaped_r9e_2b",),
            general_aerobic_capabilities=(capability,),
        ))
        cursor = end + timedelta(days=1)
    allocation = TrainingAllocation(
        start, cursor - timedelta(days=1), tuple(segments), 2714, 2039, 675, 0, 0, 15,
        rationale_codes=("persisted_shaped_r9e_2b",), confidence="high",
    )
    capacity = tuple(
        DailyTrainingCapacity(start + timedelta(days=index), 90 if (start + timedelta(days=index)).weekday() >= 5 else 70)
        for index in range(38)
    )
    reconciled = reconcile_training_allocation_for_cadence_feasibility(allocation, capacity=capacity)
    assert reconciled.success
    assert (reconciled.allocation.recommended_physical_minutes, reconciled.allocation.strength_minutes,
            reconciled.allocation.recommended_strength_exposures) == (2714, 675, 15)
    assert reconciled.allocation.rowing_specific_minutes + reconciled.allocation.general_aerobic_minutes == 2039
    assert all(
        item.category_reconciliation_minutes == 0 or item.start_date == date(2026, 12, 28)
        for item in reconciled.allocation.segments
    )
    quality = tuple(_empty_quality_plan(TrainingAllocation(
        item.start_date, item.end_date, (item,), item.recommended_physical_minutes,
        item.rowing_specific_minutes, item.strength_minutes, item.general_aerobic_minutes,
        item.other_physical_minutes, item.recommended_strength_exposures,
        rationale_codes=("quality",), confidence="high",
    )) for item in reconciled.allocation.segments)
    r4 = generate_training_days_v3(reconciled.allocation, capacity, rowing_quality_plans=quality)
    r5 = materialize_training_days_v3(r4, allocation=reconciled.allocation)
    assert r4.success and r5.success
    assert sum(item.duration_minutes for item in r4.components) == 2714
    assert sum(item.duration_minutes for item in r4.components if item.physical_category is PhysicalCategory.STRENGTH) == 675
    assert sum(item.materialized.physical_duration_minutes for item in r5.results if item.materialized) == 2714


def test_persisted_diagnostic_path_reports_deliberate_supplement_and_modern_precedence():
    profile = {
        "athlete": {"current_weekly_training_minutes": 380, "current_approx_weekly_rowing_minutes": 180},
        "season": {"start_date": START.isoformat(), "end_date": "2027-01-03", "phase_type": "general_preparation"},
        "training_context": {
            "strength_experience": "advanced", "current_strength_state": "regular",
            "current_strength_sessions_per_week": 1, "typical_strength_duration_minutes": 45,
            "strength_override": {"mode": "none"},
        },
        "planning_goals": {"primary_goal": "build_endurance"},
        "cross_training": {"modalities": {
            "stair_climber": {"enabled": True, "familiarity": "regular", "preference": "prefer"},
        }},
        "recurring_activities": [{
            "activity_id": "lift", "activity_type": "strength", "sessions_per_week": 1,
            "alternate_cardio": {"mode": "optional", "max_minutes": 15},
        }],
        "weekly_availability": [
            {"weekday": name, "available": True, "fixed_rest": False, "max_training_minutes": 120,
             "rowing_modes": ["erg"], "alternate_ut2_allowed": False}
            for name in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        ],
        "races": [],
    }
    report = generate_v3_diagnostic_report(profile, planning_date=START)
    feasibility = report["r3_cadence_feasibility"][0]
    assert feasibility["deliberate_general_aerobic_minutes"] == 15
    assert feasibility["feasibility_reconciled_general_aerobic_minutes"] == 0
    assert feasibility["supplemental_max_minutes"] == 15
    assert "legacy_weekday_alternate_ut2_flags_ignored_by_explicit_recurring_card" in feasibility["general_aerobic_policy_provenance"]
    general = next(item for day in report["calendar"] for item in day["components"] if item["category"] == "general_aerobic")
    assert (general["physical_minutes"], general["modality"]) == (15, "stair_climber")
    assert "r4:supplemental_general_aerobic_exact_partition" in general["capability_provenance"]
