"""Contracts for V3's continuous, policy-owned training-load targets."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import json

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import (
    HistoricalFact, HistoricalFactType, PhysiologicalCredit, Provenance,
    ProvenanceSource, RecoveryConstraint, TrainingNeed, TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.training_load_targets import derive_training_load_targets, reconcile_training_load_targets
from rowing_plan.training_needs import generate_residual_aerobic_needs
from rowing_plan.training_policy import DEFAULT_TRAINING_POLICY


AS_OF = date(2026, 4, 8)  # Wednesday: assertions use chronology, never its weekday.


def _profile(*, weekly: int = 180, availability: list[dict] | None = None) -> dict:
    value = {
        "athlete": {"current_approx_weekly_rowing_minutes": weekly},
        "season": {"start_date": "2026-04-01", "end_date": "2026-06-01"},
    }
    if availability is not None:
        value["date_availability"] = availability
    return value


def _context(*facts: HistoricalFact, as_of: date = AS_OF) -> NormalizedPlanningContext:
    return NormalizedPlanningContext(
        planning_date=as_of, historical_facts=tuple(facts), authoritative_commitments=(),
        provisional_prior_placement_ids=(), race_context=(), authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _completed(identifier: str, day: date, minutes: int) -> HistoricalFact:
    return HistoricalFact(
        identifier, day, HistoricalFactType.COMPLETED_PLANNED_SESSION,
        planned_prescription={}, completed_actual={"status": "completed", "actual_duration_min": minutes, "actual_intensity": "UT2"},
        physiological_credit=PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, minutes, True),
        provenance=Provenance(identifier, ProvenanceSource.COMPLETED_LOG, original_date=day),
    )


def _targets(profile: dict, *facts: HistoricalFact, as_of: date = AS_OF):
    context = _context(*facts, as_of=as_of)
    state = reduce_physiological_state(context, profile=profile)
    return derive_training_load_targets(context, state, profile=profile), state


def test_targets_are_chronological_and_shift_without_weekday_identity():
    first, _ = _targets(_profile(), as_of=AS_OF)
    second, _ = _targets(_profile(), as_of=AS_OF + timedelta(days=1))

    assert [(item.horizon_days, item.total_rowing_target_minutes, item.minimum_rowing_minutes) for item in first] == [
        (7, 180, 144), (14, 360, 288),
    ]
    assert [(item.horizon_days, item.total_rowing_target_minutes, item.minimum_rowing_minutes) for item in second] == [
        (7, 180, 144), (14, 360, 288),
    ]
    assert first[0].horizon_end == AS_OF + timedelta(days=6)
    assert second[0].horizon_start == AS_OF + timedelta(days=1)


def test_old_history_ages_out_but_recent_completed_load_reduces_the_decision_budget():
    old_targets, _ = _targets(_profile(), _completed("old", AS_OF - timedelta(days=15), 180))
    recent_targets, _ = _targets(_profile(), _completed("recent", AS_OF - timedelta(days=3), 180))

    assert old_targets[0].completed_rowing_minutes == 0
    assert recent_targets[0].completed_rowing_minutes == 180
    assert old_targets[0].remaining_rowing_minutes == 180
    assert recent_targets[0].remaining_rowing_minutes == 0


def test_residual_aerobic_needs_fill_load_without_quality_and_respect_shorter_horizon_range():
    profile = _profile()
    targets, _ = _targets(profile)
    needs = generate_residual_aerobic_needs((), targets, DEFAULT_TRAINING_POLICY)
    reconciled = reconcile_training_load_targets(targets, needs)

    assert all(item.role is TrainingRole.DEDICATED_UT2 for item in needs)
    assert all(item.target_credit.exposures == 1 for item in needs)
    seven_day = [item for item in needs if item.earliest_date <= targets[0].horizon_end]
    assert sum(item.target_credit.minutes for item in seven_day) == targets[0].total_rowing_target_minutes
    assert all(item.earliest_date > targets[0].horizon_end for item in needs[len(seven_day):])
    assert reconciled[0].provisional_planned_rowing_minutes == 180
    assert reconciled[0].provisional_planned_rowing_minutes <= reconciled[0].maximum_rowing_minutes


def test_live_like_116_minute_categorical_block_is_recognized_as_under_prescribed_without_extra_quality():
    targets, _ = _targets(_profile())
    categorical = (
        TrainingNeed("ut2", TrainingRole.DEDICATED_UT2, PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, 44, True), AS_OF, AS_OF + timedelta(days=6), "low", "aerobic_base", "test"),
        TrainingNeed("long", TrainingRole.LONG_AEROBIC, PhysiologicalCredit(TrainingRole.LONG_AEROBIC, 1, 50, True), AS_OF, AS_OF + timedelta(days=6), "low", "aerobic_base", "test"),
        TrainingNeed("tr", TrainingRole.RACE_SPECIFIC_TR, PhysiologicalCredit(TrainingRole.RACE_SPECIFIC_TR, 1, 22, True), AS_OF, AS_OF + timedelta(days=6), "high", "race_specific", "test"),
    )
    before = reconcile_training_load_targets(targets, categorical)[0]
    effective = generate_residual_aerobic_needs(categorical, targets, DEFAULT_TRAINING_POLICY)
    residual = tuple(item for item in effective if item.progression_relation and item.progression_relation.startswith("load_target_residual:"))

    assert before.provisional_planned_rowing_minutes == 116
    assert before.remaining_rowing_minutes == 64
    assert residual and all(item.role is TrainingRole.DEDICATED_UT2 for item in residual)
    assert not any(item.role in {TrainingRole.QUALITY, TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR} for item in residual)


def test_availability_is_a_feasibility_ceiling_not_a_physiology_redefinition():
    availability = [
        {"date": (AS_OF + timedelta(days=index)).isoformat(), "available": index == 0, "max_training_minutes": 45}
        for index in range(14)
    ]
    targets, _ = _targets(_profile(availability=availability))
    assert targets[0].availability_ceiling_minutes == 45
    assert targets[0].total_rowing_target_minutes == 180
    assert targets[0].minimum_rowing_minutes == 144


def test_taper_and_recovery_reduce_the_same_budget_without_compounding():
    profile = _profile()
    context = _context()
    ordinary_state = reduce_physiological_state(context, profile=profile)
    taper_state = replace(ordinary_state, next_race_context={"days_until": 2, "priority": "B"})
    recovery_state = replace(ordinary_state, recovery_constraints=(
        RecoveryConstraint("race:test", AS_OF, AS_OF, (TrainingRole.STRENGTH,), rationale="test"),
    ))
    both_state = replace(taper_state, recovery_constraints=recovery_state.recovery_constraints)

    taper = derive_training_load_targets(context, taper_state, profile=profile)[0]
    recovery = derive_training_load_targets(context, recovery_state, profile=profile)[0]
    both = derive_training_load_targets(context, both_state, profile=profile)[0]

    assert (taper.total_rowing_target_minutes, recovery.total_rowing_target_minutes) == (108, 135)
    assert both.total_rowing_target_minutes == 108
    assert (both.taper_modifier, both.recovery_modifier) == (0.6, 0.75)


def test_v3_candidate_reports_load_targets_and_deterministically_places_low_intensity_not_extra_quality():
    profile = _profile()
    config = json.load(open("config/defaults.json"))
    args = (profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config))
    first = generate_plan_sequence_v3_candidate(*args, planning_date=AS_OF)
    second = generate_plan_sequence_v3_candidate(*args, planning_date=AS_OF)

    assert first.success and second.success
    assert first.placements == second.placements
    diagnostics = first.plan["v2_diagnostics"]["training_load_targets"]
    assert {item["horizon_days"] for item in diagnostics} == {7, 14}
    seven = next(item for item in diagnostics if item["horizon_days"] == 7)
    assert seven["planned_rowing_minutes"] == seven["target_rowing_minutes"]
    assert seven["planned_low_intensity_minutes"] >= seven["low_intensity_minimum_minutes"]
    assert not any(item.role in {TrainingRole.QUALITY, TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR} for item in first.placements)
