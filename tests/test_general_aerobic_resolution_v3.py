"""R9E.2a contracts for profile-backed general-aerobic materialization."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from rowing_plan.diagnostic_v3 import generate_v3_diagnostic_report
from rowing_plan.materialization_v3 import MaterializationContext, materialize_training_days_v3
from rowing_plan.planning_domain import (
    GeneralAerobicModalityCapability,
    PhysicalCategory,
    RowingQualityPlan,
    TrainingAllocation,
    TrainingAllocationSegment,
    TrainingModality,
)
from rowing_plan.training_allocation import allocate_training_dose, general_aerobic_capabilities
from rowing_plan.training_days import (
    CombinedDayPreferences,
    DailyTrainingCapacity,
    allocation_segment_id,
    generate_training_days_v3,
)


START = date(2026, 12, 28)


def _capability(modality: TrainingModality, *, preference: str = "neutral", familiarity: str | None = "regular"):
    return GeneralAerobicModalityCapability(
        modality, familiarity, preference,
        (f"profile_cross_training:{modality.value}:enabled",),
    )


def _allocation(*, rowing: int = 0, strength: int = 45, general: int = 15,
                days: int = 1, capabilities=()):
    end = START + timedelta(days=days - 1)
    total = rowing + strength + general
    segment = TrainingAllocationSegment(
        START, end, total, rowing, strength, general, 0, 0,
        strength // 45, 0, strength // 45, 45,
        rationale_codes=("r9e_2a_test",),
        general_aerobic_capabilities=tuple(capabilities),
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
