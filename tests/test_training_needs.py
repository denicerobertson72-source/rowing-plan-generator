from __future__ import annotations

from datetime import date, timedelta

from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import (
    AuthoritySource,
    AuthorityType,
    AuthoritativeCommitment,
    ConflictBehavior,
    HistoricalFact,
    HistoricalFactType,
    PhysiologicalCredit,
    Provenance,
    ProvenanceSource,
    TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.training_needs import generate_training_need_plan, generate_training_needs, transition_training_need


AS_OF = date(2026, 10, 12)


def _profile(*, races=(), capacity=270, **athlete):
    return {
        "athlete": {"current_approx_weekly_rowing_minutes": capacity, **athlete},
        "season": {"start_date": "2026-10-01", "end_date": "2026-11-30"},
        "races": list(races),
    }


def _fact(identifier, day, kind, *, role=None, minutes=0, actual=None, planned=None):
    return HistoricalFact(
        identifier, day, kind, planned_prescription=planned,
        completed_actual=actual,
        physiological_credit=PhysiologicalCredit(role, 1, minutes, True) if role else PhysiologicalCredit(),
        provenance=Provenance(identifier, ProvenanceSource.COMPLETED_LOG, original_date=day),
    )


def _context(*, as_of=AS_OF, facts=(), races=(), commitments=()):
    return NormalizedPlanningContext(
        planning_date=as_of, historical_facts=tuple(facts), authoritative_commitments=tuple(commitments),
        provisional_prior_placement_ids=(),
        race_context=tuple((day.isoformat(), priority, "Race", None) for day, priority in races),
        authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _plan(context, profile):
    state = reduce_physiological_state(context, profile=profile)
    return generate_training_need_plan(context, state, profile=profile)


def _signature(needs, anchor):
    return tuple((
        item.id, item.role, item.target_credit, (item.earliest_date - anchor).days,
        (item.latest_date - anchor).days, item.priority, item.progression_relation, item.state,
    ) for item in needs)


def test_identical_physiology_is_weekday_and_monday_sunday_boundary_neutral():
    monday = AS_OF
    shifted = monday + timedelta(days=7)
    first_context = _context(as_of=monday, facts=(_fact("ut2", monday - timedelta(days=2), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=45, actual={"actual_intensity": "UT2"}),))
    second_context = _context(as_of=shifted, facts=(_fact("ut2", shifted - timedelta(days=2), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=45, actual={"actual_intensity": "UT2"}),))
    profile = _profile()

    assert _signature(generate_training_needs(first_context, reduce_physiological_state(first_context, profile=profile), profile=profile), monday) == _signature(generate_training_needs(second_context, reduce_physiological_state(second_context, profile=profile), profile=profile), shifted)


def test_completed_ut2_reduces_dedicated_need_but_uncompleted_prescription_does_not():
    profile = _profile()
    baseline = _plan(_context(), profile).needs
    completed_context = _context(facts=(_fact("done", AS_OF - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=60, actual={"actual_intensity": "UT2"}),))
    uncompleted_context = _context(facts=(_fact("missed", AS_OF - timedelta(days=1), HistoricalFactType.PRESCRIBED_SESSION, planned={"band": "UT2"}),))

    completed = _plan(completed_context, profile).needs
    uncompleted = _plan(uncompleted_context, profile).needs
    assert any(item.role is TrainingRole.DEDICATED_UT2 for item in baseline)
    assert not any(item.role is TrainingRole.DEDICATED_UT2 for item in completed)
    assert _signature(uncompleted, AS_OF) == _signature(baseline, AS_OF)


def test_unknown_coached_commitment_does_not_erase_dedicated_ut2_need():
    profile = _profile()
    coached = AuthoritativeCommitment(
        "coach", AuthorityType.FIXED_COACHING, AS_OF + timedelta(days=1), None, 50,
        AuthoritySource.RECURRING_ACTIVITY, ConflictBehavior.PRESERVE_AND_REPORT,
        Provenance("coach", ProvenanceSource.RECURRING_ACTIVITY),
    )
    baseline = _plan(_context(), profile).needs
    with_coach = _plan(_context(commitments=(coached,)), profile).needs

    assert any(item.role is TrainingRole.DEDICATED_UT2 for item in baseline)
    assert _signature(with_coach, AS_OF) == _signature(baseline, AS_OF)


def test_long_aerobic_explicitly_offsets_but_does_not_double_credit_ut2():
    plan = _plan(_context(), _profile())
    long_need = next(item for item in plan.needs if item.role is TrainingRole.LONG_AEROBIC)
    dedicated = next(item for item in plan.needs if item.role is TrainingRole.DEDICATED_UT2)

    assert long_need.progression_relation == "may_contribute:dedicated_ut2"
    assert long_need.target_credit.minutes + dedicated.target_credit.minutes == 90
    assert len([item for item in plan.needs if item.role is TrainingRole.LONG_AEROBIC]) == 1


def test_completed_race_creates_recovery_reentry_without_duplicate_race_or_hard_need():
    race = _fact("race", AS_OF - timedelta(days=1), HistoricalFactType.RACE_FACT, planned={"priority": "B"})
    plan = _plan(_context(facts=(race,)), _profile())

    assert [item.role for item in plan.needs] == [TrainingRole.RECOVERY_AEROBIC]
    assert plan.diagnostics.needs_blocked_by_recovery == ("quality", "strength")
    assert all(item.role is not TrainingRole.RACE for item in plan.needs)


def test_race_in_one_week_preserves_purposeful_preparation_not_blanket_rest():
    race_day = AS_OF + timedelta(days=7)
    profile = _profile(races=({"event_name": "Race", "race_dates": [race_day.isoformat()], "priority": "B"},))
    plan = _plan(_context(races=((race_day, "B"),)), profile)

    assert any(item.role in {TrainingRole.RACE_SPECIFIC_TR, TrainingRole.RACE_PREPARATION} for item in plan.needs)
    assert all(item.role is not TrainingRole.REST for item in plan.needs)


def test_repeated_races_transition_from_recovery_to_next_race_preparation():
    next_race = AS_OF + timedelta(days=3)
    profile = _profile(races=({"event_name": "Next", "race_dates": [next_race.isoformat()], "priority": "B"},))
    past_race = _fact("past", AS_OF - timedelta(days=4), HistoricalFactType.RACE_FACT, planned={"priority": "B"})
    plan = _plan(_context(facts=(past_race,), races=((next_race, "B"),)), profile)

    assert not plan.diagnostics.needs_blocked_by_recovery
    assert any(item.role is TrainingRole.TAPER_MAINTENANCE for item in plan.needs)


def test_strength_is_rolling_not_weekday_based_and_no_rest_need_is_created():
    first = _plan(_context(), _profile()).needs
    strength_fact = _fact("lift", AS_OF - timedelta(days=2), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.STRENGTH, minutes=40)
    second = _plan(_context(facts=(strength_fact,)), _profile()).needs

    first_strength = [item for item in first if item.role is TrainingRole.STRENGTH]
    second_strength = [item for item in second if item.role is TrainingRole.STRENGTH]
    assert len(first_strength) == 3 and all(item.target_credit.exposures == 1 for item in first_strength)
    assert len(second_strength) == 2 and all(item.target_credit.exposures == 1 for item in second_strength)
    assert {item.parent_need_id for item in first_strength} == {"need:training-policy-3.0:strength:rolling_14d_strength"}
    assert all(item.role is not TrainingRole.REST for item in (*first, *second))


def test_taper_reduces_volume_but_retains_quality_technical_maintenance():
    race_day = AS_OF + timedelta(days=2)
    profile = _profile(races=({"event_name": "Race", "race_dates": [race_day.isoformat()], "priority": "B"},))
    ordinary = _plan(_context(), _profile()).needs
    taper = _plan(_context(races=((race_day, "B"),)), profile)

    assert any(item.role is TrainingRole.TAPER_MAINTENANCE for item in taper.needs)
    assert sum(item.target_credit.minutes for item in taper.needs) < sum(item.target_credit.minutes for item in ordinary)
    assert taper.diagnostics.race_taper_adjustments == ("taper_volume_reduced",)


def test_an_pp_not_generated_without_explicit_policy_and_demographics_do_not_change_needs():
    context = _context()
    first = _plan(context, _profile(age=25, sex="female")).needs
    second = _plan(context, _profile(age=65, sex="male", menopause_status="x")).needs

    assert not any(item.role in {TrainingRole.ANAEROBIC_AN, TrainingRole.POWER_PP} for item in first)
    assert _signature(first, AS_OF) == _signature(second, AS_OF)


def test_diagnostics_transitions_and_input_order_are_deterministic():
    first = _fact("ut2", AS_OF - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=30, actual={"actual_intensity": "UT2"})
    second = _fact("strength", AS_OF - timedelta(days=2), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.STRENGTH, minutes=40)
    profile = _profile()
    plan_a = _plan(_context(facts=(first, second)), profile)
    plan_b = _plan(_context(facts=(second, first)), profile)
    reduced = transition_training_need(plan_a.needs[0], "deferred", "recovery_constraint")

    assert plan_a == plan_b
    assert reduced.state == "deferred" and reduced.state_reason == "recovery_constraint"
    assert "monday" not in repr(plan_a).lower()
