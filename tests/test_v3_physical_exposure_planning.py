"""M11G: purpose-preserving physical exposure planning contracts."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.physical_exposure_planning import plan_physical_exposures
import pytest

from rowing_plan.planning_domain import PhysicalExposureChild, PhysicalExposurePlan, PhysiologicalCredit, TrainingDistributionTarget, TrainingNeed, TrainingRole
from rowing_plan.planning_normalization import normalize_planning_inputs
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.training_load_targets import derive_training_distribution_targets, derive_training_load_targets


CONFIG = json.load(open("config/defaults.json"))
AS_OF = date(2026, 11, 2)


def _profile() -> dict:
    return {
        "athlete": {"current_approx_weekly_rowing_minutes": 220, "experience_level": "intermediate"},
        "season": {"start_date": "2026-10-01", "end_date": "2026-12-01"},
        "races": [{"event_name": "double", "start_date": "2026-11-07", "end_date": "2026-11-08", "race_dates": ["2026-11-07", "2026-11-08"], "priority": "A", "race_type": "head_5k", "practice_sessions": [{"date": "2026-11-06", "title": "Course practice", "duration_minutes": 30}]}],
        "weekly_availability": [{"weekday": day, "available": True, "max_training_minutes": 90, "rowing_modes": ["erg"]} for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")],
    }


def _residual(minutes: int = 48) -> TrainingNeed:
    return TrainingNeed(
        "generic-residual", TrainingRole.DEDICATED_UT2,
        PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, minutes, True),
        AS_OF, AS_OF + timedelta(days=3), "low", "aerobic_target_topup", "test",
        progression_relation="load_target_residual:test",
    )


def _target() -> TrainingDistributionTarget:
    return TrainingDistributionTarget(AS_OF, AS_OF + timedelta(days=6), 7, 2, 3, 2, 4, True)


def test_generic_48_minute_residual_uses_a_catalog_safe_easy_child_without_dedicated_ut2_minimum():
    profile = _profile(); context = normalize_planning_inputs(profile, planning_date=AS_OF)
    state = reduce_physiological_state(context, profile=profile)
    result = plan_physical_exposures((_residual(),), (_target(),), context=context, state=state, profile=profile)

    assert len(result.plans) == 1
    plan = result.plans[0]
    assert plan.total_credit_conserved and plan.rolling_max_feasible
    assert [child.target_credit.minutes for child in plan.children] == [48]
    assert {child.role for child in plan.children} == {TrainingRole.TECHNICAL_REENTRY}
    assert plan.realized_credit_minutes == 48 and plan.unrealized_credit_minutes == 0
    assert all(child.minimum_materializable_envelope == 20 for child in plan.children)
    assert sum(item.target_credit.minutes for item in result.needs) == 48
    assert all(item.partition_reason == "physical_exposure_plan" for item in result.needs)


def test_categorical_dedicated_ut2_and_long_aerobic_are_not_reclassified_for_frequency():
    profile = _profile(); context = normalize_planning_inputs(profile, planning_date=AS_OF)
    state = reduce_physiological_state(context, profile=profile)
    ut2 = TrainingNeed("protected-ut2", TrainingRole.DEDICATED_UT2, PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, 48, True), AS_OF, AS_OF + timedelta(days=3), "low", "protected_aerobic_base", "test", progression_relation="aerobic_development")
    long = TrainingNeed("long", TrainingRole.LONG_AEROBIC, PhysiologicalCredit(TrainingRole.LONG_AEROBIC, 1, 60, True), AS_OF, AS_OF + timedelta(days=3), "moderate", "aerobic_base", "test")
    result = plan_physical_exposures((ut2, long), (_target(),), context=context, state=state, profile=profile)
    assert {item.id: item for item in result.needs} == {ut2.id: ut2, long.id: long}
    assert result.plans == ()


def test_live_shaped_48_minute_case_reports_rolling_maximum_infeasible_without_false_recovery():
    profile = _profile()
    profile["recurring_activities"] = [{"activity_id": "private", "activity_type": "private_coaching", "scheduling_status": "fixed", "sessions_per_week": 1, "fixed_days": ["wednesday"]}]
    bands = build_intensity_profile(profile, CONFIG); power = build_power_profile(profile, CONFIG)
    candidate = generate_plan_sequence_v3_candidate(profile, CONFIG, bands, power, planning_date=date(2026, 10, 30))
    assert candidate.success
    diagnostics = candidate.plan["v2_diagnostics"]
    plans = diagnostics["physical_exposure_plans"]
    matching = next(item for item in plans if item["approved_low_intensity_credit"] == 48)
    assert matching["exposure_plan_failure_reason"] == "partial_residual_realized"
    assert matching["realized_residual_credit"] == 20
    assert matching["unrealized_residual_credit"] == 28
    assert matching["rolling_max_feasible"] is True
    assert diagnostics["empty_date_rationales"] and not any(
        item["date"] in {"2026-11-02", "2026-11-03"} and item["reason"] == "required_recovery"
        for item in diagnostics["empty_date_rationales"]
    )
    seven = next(item for item in diagnostics["training_load_targets"] if item["horizon_days"] == 7)
    assert seven["maximum_rowing_minutes"] == 145
    assert seven["planned_rowing_minutes"] <= 145
    assert any(item.role is TrainingRole.TECHNICAL_REENTRY for item in candidate.needs)


def test_physical_exposure_planning_is_deterministic_and_does_not_change_strength_or_quality_needs():
    profile = _profile(); context = normalize_planning_inputs(profile, planning_date=AS_OF)
    state = reduce_physiological_state(context, profile=profile)
    strength = TrainingNeed("strength", TrainingRole.STRENGTH, PhysiologicalCredit(TrainingRole.STRENGTH, 1, 0, True), AS_OF, AS_OF + timedelta(days=3), "hard", "required_strength_minimum", "test")
    quality = TrainingNeed("quality", TrainingRole.QUALITY, PhysiologicalCredit(TrainingRole.QUALITY, 1, 35, True), AS_OF, AS_OF + timedelta(days=3), "hard", "quality_development", "test")
    args = ((_residual(), strength, quality), (_target(),))
    first = plan_physical_exposures(*args, context=context, state=state, profile=profile)
    second = plan_physical_exposures(*args, context=context, state=state, profile=profile)
    assert first == second
    assert {item.id for item in first.needs if item.role in {TrainingRole.STRENGTH, TrainingRole.QUALITY}} == {"strength", "quality"}


def test_physical_exposure_plan_accepts_full_partial_and_zero_realization_and_rejects_bad_accounting():
    child = PhysicalExposureChild("child", ("source",), TrainingRole.TECHNICAL_REENTRY, PhysiologicalCredit(TrainingRole.TECHNICAL_REENTRY, 1, 20, True), 20, 20, AS_OF, AS_OF, "generic", "test")
    common = dict(plan_id="plan", source_need_ids=("source",), source_credit_total=48, physiological_purposes=("generic",), splittable_credit_minutes=48, non_splittable_credit_minutes=0, distribution_rationale=("test",), race_taper_context="taper", frequency_target_served=3, total_credit_conserved=True, rolling_max_feasible=True)
    assert PhysicalExposurePlan(realized_credit_minutes=48, unrealized_credit_minutes=0, physical_exposure_count=1, children=(replace(child, target_credit=PhysiologicalCredit(TrainingRole.TECHNICAL_REENTRY, 1, 48, True), preferred_envelope=48),), **common)
    assert PhysicalExposurePlan(realized_credit_minutes=20, unrealized_credit_minutes=28, physical_exposure_count=1, children=(child,), **common)
    assert PhysicalExposurePlan(realized_credit_minutes=0, unrealized_credit_minutes=48, physical_exposure_count=0, children=(), **common)
    with pytest.raises(ValueError):
        PhysicalExposurePlan(realized_credit_minutes=20, unrealized_credit_minutes=20, physical_exposure_count=1, children=(child,), **common)
    with pytest.raises(ValueError):
        PhysicalExposurePlan(realized_credit_minutes=20, unrealized_credit_minutes=28, physical_exposure_count=1, children=(replace(child, target_credit=PhysiologicalCredit(TrainingRole.TECHNICAL_REENTRY, 1, 30, True), preferred_envelope=30),), **common)
    with pytest.raises(ValueError):
        PhysicalExposurePlan(realized_credit_minutes=-1, unrealized_credit_minutes=49, physical_exposure_count=0, children=(), **common)
