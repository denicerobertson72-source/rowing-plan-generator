"""M2 total-endurance accounting and rowing-specific protection contracts."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import json

import pytest

from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.planning_domain import (
    HistoricalFact, HistoricalFactType, PhysiologicalCredit, Provenance,
    ProvenanceSource, TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.training_load_targets import (
    derive_endurance_demand_targets, derive_training_load_targets,
    reconcile_endurance_demand_targets,
)


AS_OF = date(2026, 4, 8)


def _profile() -> dict:
    return {
        "athlete": {"current_approx_weekly_rowing_minutes": 132},
        "season": {"start_date": "2026-04-01", "end_date": "2026-06-01"},
    }


def _context(*facts: HistoricalFact) -> NormalizedPlanningContext:
    return NormalizedPlanningContext(
        planning_date=AS_OF, historical_facts=tuple(facts), authoritative_commitments=(),
        provisional_prior_placement_ids=(), race_context=(), authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _actual(identifier: str, activity_type: str, minutes: int, *, intensity: str | None = "UT2") -> HistoricalFact:
    actual = {"status": "completed", "activity_type": activity_type, "actual_duration_min": minutes}
    if intensity:
        actual["actual_intensity"] = intensity
    role = TrainingRole.DEDICATED_UT2 if activity_type in {"erg", "on_water"} else (
        TrainingRole.GENERAL_AEROBIC if activity_type in {"bike", "elliptical", "stair_climber", "running"} else TrainingRole.STRENGTH if activity_type == "strength" else None
    )
    return HistoricalFact(
        identifier, AS_OF - timedelta(days=1), HistoricalFactType.OPTIONAL_ACTIVITY,
        completed_actual=actual,
        physiological_credit=PhysiologicalCredit(role, 1, minutes, True) if role else PhysiologicalCredit(),
        provenance=Provenance(identifier, ProvenanceSource.OPTIONAL_ACTIVITY_LOG),
    )


def _state(*facts: HistoricalFact):
    context = _context(*facts)
    return context, reduce_physiological_state(context, profile=_profile())


@pytest.mark.parametrize(("facts", "physical", "rowing"), [
    ((_actual("erg", "erg", 50),), 50, 50),
    ((_actual("on-water", "on_water", 50),), 50, 50),
    ((_actual("bike", "bike", 50),), 50, 0),
    ((_actual("elliptical", "elliptical", 50),), 50, 0),
    ((_actual("stairs", "stair_climber", 50),), 50, 0),
    ((_actual("running", "running", 50),), 50, 0),
    ((_actual("erg", "erg", 50), _actual("bike", "bike", 40)), 90, 50),
    ((_actual("strength", "strength", 60),), 0, 0),
    ((_actual("other", "other", 45),), 0, 0),
])
def test_recent_physical_endurance_is_identity_reconciled_not_dimension_sum(facts, physical, rowing):
    _, state = _state(*facts)
    recent = state.recent_7d_load
    assert recent.physical_endurance_minutes == physical
    assert recent.rowing_specific_minutes == rowing
    assert len(recent.physical_endurance_fact_ids) == (2 if physical == 90 else int(bool(physical)))


def test_easy_erg_can_be_general_and_rowing_specific_but_is_one_physical_duration():
    _, state = _state(_actual("erg", "erg", 50))
    recent = state.recent_7d_load
    assert (recent.rowing_minutes, recent.general_aerobic_minutes) == (50, 50)
    assert recent.physical_endurance_minutes == 50
    assert recent.physical_endurance_fact_ids == ("erg",)


def test_one_completed_physical_exposure_credits_each_applicable_horizon_once():
    _, state = _state(_actual("erg", "erg", 50))
    assert state.recent_7d_load.physical_endurance_minutes == 50
    assert state.recent_14d_load.physical_endurance_minutes == 50
    assert state.recent_7d_load.physical_endurance_fact_ids == ("erg",)
    assert state.recent_14d_load.physical_endurance_fact_ids == ("erg",)


def test_endurance_target_uses_existing_physical_envelope_and_counts_bike_without_rowing_credit():
    context, state = _state(_actual("erg", "erg", 50), _actual("bike", "bike", 25))
    loads = derive_training_load_targets(context, state, profile=_profile())
    load = next(item for item in loads if item.horizon_days == 7)
    target = next(item for item in derive_endurance_demand_targets((load,), state) if item.horizon_days == 7)
    assert (target.total_minimum_minutes, target.total_target_minutes, target.total_maximum_minutes) == (
        load.minimum_rowing_minutes, load.total_rowing_target_minutes, load.maximum_rowing_minutes,
    )
    assert (target.completed_physical_endurance_minutes, target.completed_rowing_specific_minutes) == (75, 50)
    assert target.eligible_general_aerobic_residual_minutes == max(0, load.total_rowing_target_minutes - 75)


def test_documented_7d_target_example_reconciles_75_physical_minutes_without_double_counting():
    context, state = _state(_actual("erg", "erg", 50), _actual("bike", "bike", 25))
    source = derive_training_load_targets(context, state, profile=_profile())[0]
    load = replace(source, total_rowing_target_minutes=132, minimum_rowing_minutes=106, maximum_rowing_minutes=145)
    target = derive_endurance_demand_targets((load,), state)[0]
    assert target.completed_physical_endurance_minutes == 75
    assert target.completed_rowing_specific_minutes == 50
    assert target.eligible_general_aerobic_residual_minutes == 57


def test_protected_categorical_rowing_leaves_only_total_endurance_residual_and_never_exceeds_headroom():
    context, state = _state()
    original = derive_training_load_targets(context, state, profile=_profile())[0]
    load = replace(
        original, total_rowing_target_minutes=240, minimum_rowing_minutes=192,
        maximum_rowing_minutes=260, selected_categorical_rowing_minutes=170,
    )
    initial = derive_endurance_demand_targets((load,), state)
    final = reconcile_endurance_demand_targets(initial, (load,))[0]
    assert final.protected_rowing_specific_minutes == 170
    assert final.eligible_general_aerobic_residual_minutes == 70
    assert final.eligible_general_aerobic_residual_minutes <= final.total_maximum_minutes - 170


def test_general_aerobic_history_does_not_satisfy_rowing_specific_target_or_create_new_cross_need():
    context, state = _state(_actual("bike", "bike", 100))
    load = derive_training_load_targets(context, state, profile=_profile())[0]
    endurance = derive_endurance_demand_targets((load,), state)[0]
    assert load.completed_rowing_minutes == 0
    assert load.remaining_rowing_minutes == 132
    assert endurance.completed_physical_endurance_minutes == 100
    assert endurance.completed_rowing_specific_minutes == 0
    assert endurance.eligible_general_aerobic_residual_minutes == 32


def test_endurance_target_has_zero_eligible_residual_when_physical_target_is_already_satisfied():
    context, state = _state(_actual("erg", "erg", 132))
    load = derive_training_load_targets(context, state, profile=_profile())[0]
    target = derive_endurance_demand_targets((load,), state)[0]
    assert target.eligible_general_aerobic_residual_minutes == 0


def test_physical_maximum_headroom_applies_independently_of_modality():
    context, state = _state(_actual("erg", "erg", 50), _actual("bike", "bike", 75))
    source = derive_training_load_targets(context, state, profile=_profile())[0]
    load = replace(source, total_rowing_target_minutes=132, minimum_rowing_minutes=106, maximum_rowing_minutes=145)
    target = derive_endurance_demand_targets((load,), state)[0]
    assert target.completed_physical_endurance_minutes == 125
    assert target.eligible_general_aerobic_residual_minutes == 7
    assert target.eligible_general_aerobic_residual_minutes <= 145 - 125


def test_m2_general_aerobic_accounting_is_diagnostic_only_and_does_not_change_v3_output():
    profile = _profile()
    config = json.load(open("config/defaults.json"))
    inputs = (config, build_intensity_profile(profile, config), build_power_profile(profile, config))
    baseline = generate_plan_sequence_v3_candidate(profile, *inputs, planning_date=AS_OF)
    with_bike = generate_plan_sequence_v3_candidate(
        profile, *inputs, planning_date=AS_OF,
        history_actuals={"actual:2026-04-07:bike": {
            "status": "completed", "activity_type": "bike", "actual_duration_min": 45,
            "actual_intensity": "UT2",
        }},
    )
    assert baseline.success and with_bike.success
    assert baseline.plan["sessions"] == with_bike.plan["sessions"]
    assert baseline.plan["calendar_days"] == with_bike.plan["calendar_days"]
    endurance = with_bike.plan["v2_diagnostics"]["endurance_demand_targets"]
    assert endurance and all(item["completed_physical_endurance"] >= 45 for item in endurance)
    assert not any(session.get("mode") in {"bike", "elliptical", "stair_climber", "running"} for session in with_bike.plan["sessions"])
