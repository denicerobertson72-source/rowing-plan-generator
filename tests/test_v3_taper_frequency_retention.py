"""M11F contracts: taper frequency is physical redistribution, never extra dose."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

from rowing_plan.continuous_placement import place_continuous_training_needs
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.materialization_v2 import materialize_v2_session
from rowing_plan.planning_domain import (
    AuthorityType, PhysiologicalCredit, RecoveryConstraint, TrainingDistributionTarget,
    TrainingNeed, TrainingRole,
)
from rowing_plan.planning_normalization import normalize_planning_inputs
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import adapt_placements_to_materialization_requests
from rowing_plan.training_load_targets import derive_training_load_targets
from rowing_plan.training_needs import partition_taper_frequency_needs


CONFIG = json.load(open("config/defaults.json"))
AS_OF = date(2026, 11, 2)


def _profile(*, fixed_rest: bool = False, fixed_coaching: bool = False) -> dict:
    recurring = []
    if fixed_rest:
        recurring.append({"activity_id": "rest", "activity_type": "rest", "scheduling_status": "fixed", "fixed_days": ["monday"]})
    if fixed_coaching:
        recurring.append({"activity_id": "coach", "activity_type": "coached_row", "scheduling_status": "fixed", "fixed_days": ["thursday"]})
    return {
        "athlete": {"current_approx_weekly_rowing_minutes": 220, "experience_level": "experienced"},
        "season": {"start_date": "2026-10-01", "end_date": "2026-11-30"},
        "races": [{"event_name": "double", "start_date": "2026-11-07", "end_date": "2026-11-08", "race_dates": ["2026-11-07", "2026-11-08"], "priority": "A", "race_type": "head_5k"}],
        "recurring_activities": recurring,
        "weekly_availability": [
            {"weekday": day, "available": True, "max_training_minutes": 90, "rowing_modes": ["erg"]}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        ],
    }


def _target() -> TrainingDistributionTarget:
    return TrainingDistributionTarget(
        AS_OF, AS_OF + timedelta(days=6), 7, 2, 3, 2, 4, True,
        ("test",), ("test taper frequency",),
    )


def _residual(minutes: int = 60, *, start: date = AS_OF, end: date = AS_OF + timedelta(days=3)) -> TrainingNeed:
    return TrainingNeed(
        "residual-ut2", TrainingRole.DEDICATED_UT2,
        PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, minutes, True),
        start, end, "low", "aerobic_target_topup", "test",
        progression_relation="load_target_residual:test",
    )


def _partitioned(*, profile=None, state=None, source=None):
    profile = profile or _profile()
    context = normalize_planning_inputs(profile, planning_date=AS_OF)
    state = state or reduce_physiological_state(context, profile=profile)
    result = partition_taper_frequency_needs(
        (source or _residual(),), (_target(),), context=context, state=state,
        profile=profile, selected_placements=(),
    )
    return profile, context, state, result


def test_taper_frequency_partition_is_actionable_and_conserves_generic_residual_credit():
    _profile_value, _context, _state, result = _partitioned()

    assert len(result.needs) == 2
    assert {item.role for item in result.needs} == {TrainingRole.DEDICATED_UT2, TrainingRole.TECHNICAL_REENTRY}
    assert sum(item.target_credit.minutes for item in result.needs) == 60
    assert all(item.parent_need_id == "residual-ut2" for item in result.needs)
    assert all(item.original_target_credit_minutes == 60 for item in result.needs)
    assert all(item.partition_reason == "taper_frequency_redistribution" for item in result.needs)
    diagnostic = result.diagnostics[0]
    assert diagnostic.redistribution_attempted and diagnostic.total_credit_conserved
    assert diagnostic.partition_credits == tuple(item.target_credit.minutes for item in result.needs)


def test_partitioned_easy_exposure_places_and_materializes_without_enlarging_credit():
    profile, context, state, partitioned = _partitioned()
    placed = place_continuous_training_needs(
        context, state, partitioned.needs, profile=profile, distribution_targets=(_target(),),
    )
    physical = tuple(item for item in placed.placements if item.training_need_id.startswith("residual-ut2"))
    assert len(physical) == 2
    assert sum(item.target_credit.minutes for item in physical) == 60
    assert any(item.role is TrainingRole.TECHNICAL_REENTRY for item in physical)
    adapted = adapt_placements_to_materialization_requests(physical, profile=profile)
    assert not adapted.diagnostics
    bands = build_intensity_profile(profile, CONFIG); power = build_power_profile(profile, CONFIG)
    materialized = [materialize_v2_session(item, profile=profile, bands=bands, power=power) for item in adapted.requests]
    assert all(item.success for item in materialized)
    assert all(
        session.session["total_cardio_minutes"] <= next(
            placement.reservation_minutes for placement in physical if placement.placement_id == request.placement_id
        )
        for request, session in zip(adapted.requests, materialized)
    )


def test_double_race_taper_gap_with_prior_tr_can_place_a_budget_neutral_easy_exposure():
    profile = _profile()
    prior = {
        "sessions": [{
            "date": "2026-11-01", "session_id": "TR", "mode": "erg", "band": "TR",
            "session_role": "RACE_PACE", "total_cardio_minutes": 24,
        }],
        "calendar_days": [],
    }
    actuals = {"2026-11-01:TR:erg": {"status": "completed", "actual_duration_min": 24, "actual_intensity": "TR"}}
    context = normalize_planning_inputs(profile, planning_date=AS_OF, prior_plan=prior, history_actuals=actuals)
    state = reduce_physiological_state(context, profile=profile)
    redistributed = partition_taper_frequency_needs(
        (_residual(),), (_target(),), context=context, state=state, profile=profile,
    )
    placed = place_continuous_training_needs(
        context, state, redistributed.needs, profile=profile, distribution_targets=(_target(),),
    )
    physical = tuple(item for item in placed.placements if item.training_need_id.startswith("residual-ut2"))

    assert state.recent_7d_load.quality_exposures == 1
    assert redistributed.diagnostics[0].redistribution_attempted
    assert len(physical) == 2
    assert sum(item.target_credit.minutes for item in physical) == 60
    assert any(item.date in {date(2026, 11, 2), date(2026, 11, 3)} for item in physical)
    assert {date(2026, 11, 7), date(2026, 11, 8)} <= {
        item.date for item in placed.placements if item.authority_type is AuthorityType.RACE
    }
    assert not any(item.role in {TrainingRole.QUALITY, TrainingRole.RACE_SPECIFIC_TR} for item in physical)


def test_true_dedicated_ut2_and_quality_are_not_reclassified_as_frequency_credit():
    profile, _context, _state, result = _partitioned(source=replace(
        _residual(), progression_relation="aerobic_development",
    ))
    assert result.needs[0].role is TrainingRole.DEDICATED_UT2
    assert not result.diagnostics[0].redistribution_attempted
    assert result.diagnostics[0].redistribution_blocked_reason == "hard_recovery_or_no_redistributable_credit"
    assert TrainingRole.QUALITY not in {item.role for item in result.needs}


def test_hard_recovery_and_final_48_hours_allow_consecutive_empty_days():
    profile = _profile()
    context = normalize_planning_inputs(profile, planning_date=AS_OF)
    state = reduce_physiological_state(context, profile=profile)
    recovery = replace(state, recovery_constraints=(RecoveryConstraint(
        "test", AS_OF, AS_OF + timedelta(days=3),
        (TrainingRole.DEDICATED_UT2, TrainingRole.TECHNICAL_REENTRY), rationale="test",
    ),))
    hard = partition_taper_frequency_needs((_residual(),), (_target(),), context=context, state=recovery, profile=profile)
    assert not hard.diagnostics[0].redistribution_attempted
    assert hard.diagnostics[0].redistribution_blocked_reason == "hard_recovery_or_no_redistributable_credit"
    final = partition_taper_frequency_needs(
        (_residual(start=date(2026, 11, 5), end=date(2026, 11, 6)),), (_target(),),
        context=context, state=state, profile=profile,
    )
    assert not final.diagnostics[0].redistribution_attempted
    assert final.diagnostics[0].redistribution_blocked_reason == "race_proximity_or_availability"


def test_high_recent_load_leaves_frequency_target_unmet_with_an_explicit_reason():
    profile = _profile()
    context = normalize_planning_inputs(profile, planning_date=AS_OF)
    state = reduce_physiological_state(context, profile=profile)
    target = derive_training_load_targets(context, state, profile=profile)[0]
    high_load = replace(target, completed_rowing_minutes=target.total_rowing_target_minutes)
    result = partition_taper_frequency_needs(
        (_residual(),), (_target(),), context=context, state=state, profile=profile,
        load_targets=(high_load,),
    )
    assert not result.diagnostics[0].redistribution_attempted
    assert result.diagnostics[0].redistribution_blocked_reason == "recent_load_target_satisfied"


def test_fixed_rest_and_fixed_coaching_remain_authoritative_and_deterministic():
    profile = _profile(fixed_rest=True, fixed_coaching=True)
    first_profile, context, state, first = _partitioned(profile=profile)
    _profile2, _context2, _state2, second = _partitioned(profile=profile)
    assert first == second and first_profile == profile
    placed = place_continuous_training_needs(context, state, first.needs, profile=profile, distribution_targets=(_target(),))
    assert any(item.authority_type is AuthorityType.FIXED_REST for item in placed.placements)
    assert any(item.authority_type is AuthorityType.FIXED_COACHING for item in placed.placements)
    assert not any(item.date == AS_OF and item.role is TrainingRole.TECHNICAL_REENTRY for item in placed.placements)
