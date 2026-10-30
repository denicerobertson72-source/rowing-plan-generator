"""Contracts for V3's continuous, policy-owned training-load targets."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import json

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import (
    AuthoritativeCommitment, AuthoritySource, AuthorityType, ConflictBehavior,
    HistoricalFact, HistoricalFactType, PhysiologicalCredit, Provenance,
    ProvenanceSource, RecoveryConstraint, Placement, PlacementAuthorityStatus,
    TrainingNeed, TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from rowing_plan.continuous_placement import place_continuous_training_needs
from rowing_plan.training_load_targets import (
    derive_training_load_targets, reconcile_training_load_targets, selected_dose_ledger,
)
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
    # Overlapping horizons share one candidate sequence; a 14-day need may
    # influence the first seven days without owning a separate date partition.
    assert all(item.earliest_date == targets[0].horizon_start for item in needs)
    assert reconciled[0].provisional_planned_rowing_minutes >= 180


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


def _placement(identifier: str, day: date, role: TrainingRole, minutes: int, need_id: str) -> Placement:
    return Placement(
        placement_id=identifier, training_need_id=need_id, date=day,
        reservation_minutes=minutes,
        target_credit=PhysiologicalCredit(role, 1, minutes, True),
        provenance=Provenance(identifier, ProvenanceSource.LEGACY_V2),
        authority_status=PlacementAuthorityStatus.PROVISIONAL, role=role,
    )


def test_one_selected_physical_dose_credits_each_overlapping_horizon_once_at_its_actual_date():
    targets, _ = _targets(_profile())
    need = TrainingNeed(
        "residual", TrainingRole.DEDICATED_UT2,
        PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, 44, True),
        AS_OF + timedelta(days=5), AS_OF + timedelta(days=13), "low", "aerobic_target_topup", "test",
        progression_relation=f"load_target_residual:{targets[1].id}",
    )
    placement = _placement("one-row", AS_OF + timedelta(days=5), TrainingRole.DEDICATED_UT2, 44, need.id)

    ledger = selected_dose_ledger(targets, (placement,), need_by_id={need.id: need})
    reconciled = reconcile_training_load_targets(targets, placements=(placement,), need_by_id={need.id: need})

    assert [(item.target_id, item.selected_residual_rowing_minutes) for item in ledger] == [
        (targets[0].id, 44), (targets[1].id, 44),
    ]
    assert [item.provisional_planned_rowing_minutes for item in reconciled] == [44, 44]


def test_residual_generation_uses_shared_credit_not_independent_7d_and_14d_quotas():
    targets, _ = _targets(_profile())
    seven, fourteen = targets
    categorical = (
        TrainingNeed("categorical", TrainingRole.RACE_SPECIFIC_TR,
                     PhysiologicalCredit(TrainingRole.RACE_SPECIFIC_TR, 1, 116, True),
                     AS_OF, AS_OF + timedelta(days=6), "high", "race_specific", "test"),
    )
    effective = generate_residual_aerobic_needs(categorical, targets, DEFAULT_TRAINING_POLICY)
    residual = tuple(item for item in effective if item.progression_relation and "load_target_residual" in item.progression_relation)

    # The 7d low-intensity minimum needs 126 minutes.  The 14d target receives
    # that same credit and emits only its additional 126 minutes, rather than
    # recreating all 252 minutes independently.
    totals = {}
    for item in residual:
        totals[item.progression_relation] = totals.get(item.progression_relation, 0) + item.target_credit.minutes
    assert totals[f"load_target_residual:{seven.id}"] == 126
    assert totals[f"load_target_residual:{fourteen.id}"] == 126
    assert sum(totals.values()) == 252


def test_long_horizon_residual_can_use_short_horizon_date_only_while_short_maximum_allows_it():
    profile = _profile()
    targets, state = _targets(profile)
    seven, fourteen = targets
    categorical = _placement("categorical", AS_OF, TrainingRole.DEDICATED_UT2, 80, "categorical")
    residual = TrainingNeed(
        "residual-14", TrainingRole.DEDICATED_UT2,
        PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, 30, True),
        AS_OF + timedelta(days=1), AS_OF + timedelta(days=13), "low", "aerobic_target_topup", "test",
        progression_relation=f"load_target_residual:{fourteen.id}",
    )
    context = _context()
    permitted = place_continuous_training_needs(
        context, state, (residual,), profile=profile,
        initial_placements=(categorical,),
        load_targets=(replace(seven, total_rowing_target_minutes=100, minimum_rowing_minutes=80, maximum_rowing_minutes=120), fourteen),
    )
    blocked = place_continuous_training_needs(
        context, state, (residual,), profile=profile,
        initial_placements=(categorical,),
        load_targets=(replace(seven, total_rowing_target_minutes=100, minimum_rowing_minutes=80, maximum_rowing_minutes=100), fourteen),
    )

    # The ordinary recovery evaluator may choose day two rather than the
    # immediately adjacent day, but it remains inside the 7d horizon when
    # that horizon's maximum allows the dose.
    assert next(item for item in permitted.placements if item.placement_id != categorical.placement_id).date <= AS_OF + timedelta(days=6)
    moved = next(item for item in blocked.placements if item.placement_id != categorical.placement_id)
    assert moved.date >= AS_OF + timedelta(days=7)
    assert blocked.diagnostics.suppressed_residual_by_target[seven.id] == 30


def test_authority_overage_is_preserved_and_low_intensity_conflict_does_not_create_extra_rowing_or_strength_credit():
    profile = _profile(availability=[
        {"date": (AS_OF + timedelta(days=index)).isoformat(), "available": True, "max_training_minutes": 300}
        for index in range(14)
    ])
    targets, _ = _targets(profile)
    seven = replace(
        targets[0], total_rowing_target_minutes=180, minimum_rowing_minutes=144,
        maximum_rowing_minutes=200, low_intensity_minimum_minutes=100,
        authoritative_future_rowing_minutes=220,
    )
    strength = _placement("strength", AS_OF + timedelta(days=1), TrainingRole.STRENGTH, 45, "strength")
    reconciled = reconcile_training_load_targets((seven,), placements=(strength,), need_by_id={})[0]

    assert reconciled.overage_minutes == 20
    assert reconciled.overage_sources == ("authority",)
    assert reconciled.low_intensity_conflict_with_maximum is True
    assert reconciled.provisional_planned_rowing_minutes == 0
    assert reconciled.remaining_strength_exposures == seven.strength_minimum_exposures - 1

    authority = AuthoritativeCommitment(
        "fixed-private", AuthorityType.PRIVATE_COACHING, AS_OF, None, 220,
        AuthoritySource.RECURRING_ACTIVITY, ConflictBehavior.PRESERVE_AND_REPORT,
        Provenance("fixed-private", ProvenanceSource.RECURRING_ACTIVITY),
    )
    context = replace(_context(), authoritative_commitments=(authority,))
    state = reduce_physiological_state(context, profile=profile)
    topup = TrainingNeed(
        "residual", TrainingRole.DEDICATED_UT2,
        PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, 30, True),
        AS_OF + timedelta(days=1), AS_OF + timedelta(days=6), "low", "aerobic_target_topup", "test",
        progression_relation=f"load_target_residual:{seven.id}",
    )
    placed = place_continuous_training_needs(context, state, (topup,), profile=profile, load_targets=(seven,))
    assert [item.placement_id for item in placed.placements] == ["authority:fixed-private"]
    assert placed.diagnostics.suppressed_residual_by_target[seven.id] == 30


def test_production_shaped_double_race_sequence_uses_one_shared_dose_ledger_deterministically():
    profile = {
        "athlete": {"current_approx_weekly_rowing_minutes": 220},
        "season": {"start_date": "2026-10-01", "end_date": "2026-12-01"},
        "races": [{
            "event_name": "weekend double", "start_date": "2026-11-07", "end_date": "2026-11-08",
            "race_dates": ["2026-11-07", "2026-11-08"], "priority": "A", "race_type": "head_5k",
            "practice_sessions": [{"date": "2026-11-06", "title": "Course practice", "duration_minutes": 30}],
        }],
        "recurring_activities": [{
            "activity_id": "private", "activity_type": "private_coaching", "scheduling_status": "fixed",
            "sessions_per_week": 1, "fixed_days": ["wednesday"],
        }],
    }
    config = json.load(open("config/defaults.json"))
    args = (profile, config, build_intensity_profile(profile, config), build_power_profile(profile, config))
    first = generate_plan_sequence_v3_candidate(*args, planning_date=date(2026, 10, 30))
    second = generate_plan_sequence_v3_candidate(*args, planning_date=date(2026, 10, 30))

    assert first.success and first == second
    assert {date(2026, 11, 7), date(2026, 11, 8)} <= {
        item.date for item in first.placements if item.role is TrainingRole.RACE
    }
    assert date(2026, 11, 6) in {item.date for item in first.placements if item.role is TrainingRole.COURSE_PRACTICE}
    # Taper continuity may retain one pre-race lift; the double-race recovery
    # constraint correctly does not require a post-race strength top-up.
    assert any(item.role is TrainingRole.STRENGTH and item.date == date(2026, 11, 6) for item in first.placements)
    targets = first.plan["v2_diagnostics"]["training_load_targets"]
    assert {item["horizon_days"] for item in targets} == {7, 14}
    assert all(item["selected_categorical_rowing_minutes"] + item["selected_residual_rowing_minutes"] == item["planned_rowing_minutes"] for item in targets)
