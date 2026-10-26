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
    ProjectedSequenceItem,
    ProjectedSequenceState,
    Provenance,
    ProvenanceSource,
    SequenceEligibility,
    TrainingNeed,
    TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.sequence_constraints import evaluate_sequence_constraint


AS_OF = date(2026, 10, 12)


def _context(*, as_of=AS_OF, facts=(), races=(), commitments=()):
    return NormalizedPlanningContext(
        planning_date=as_of, historical_facts=tuple(facts), authoritative_commitments=tuple(commitments),
        provisional_prior_placement_ids=(),
        race_context=tuple((day.isoformat(), priority, "Race", None) for day, priority in races),
        authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _profile():
    return {"season": {"start_date": "2026-10-01", "end_date": "2026-11-30"}, "races": []}


def _fact(identifier, day, kind, *, role=None, priority="B"):
    return HistoricalFact(
        identifier, day, kind, planned_prescription={"priority": priority},
        physiological_credit=PhysiologicalCredit(role, 1, 35, True) if role else PhysiologicalCredit(),
        provenance=Provenance(identifier, ProvenanceSource.COMPLETED_LOG, original_date=day),
    )


def _need(role, *, start=AS_OF, end=None, minutes=35):
    return TrainingNeed(
        id=f"need:{role.value}", role=role,
        target_credit=PhysiologicalCredit(role, 1, minutes, True),
        earliest_date=start, latest_date=end or start + timedelta(days=14),
        recovery_cost="hard" if role in {TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR, TrainingRole.STRENGTH} else "low",
        priority="test", source_policy="test_policy_rule",
    )


def _state(context):
    return reduce_physiological_state(context, profile=_profile())


def test_quality_and_strength_spacing_are_chronological_not_weekly():
    context = _context()
    state = _state(context)
    projected = ProjectedSequenceState((ProjectedSequenceItem("quality", AS_OF, TrainingRole.THRESHOLD_AT),))
    quality = _need(TrainingRole.RACE_SPECIFIC_TR)
    same_spacing = evaluate_sequence_constraint(context, state, quality, AS_OF + timedelta(days=1), projected=projected)
    clear_spacing = evaluate_sequence_constraint(context, state, quality, AS_OF + timedelta(days=2), projected=projected)
    strength_projected = ProjectedSequenceState((ProjectedSequenceItem("strength", AS_OF, TrainingRole.STRENGTH),))
    strength = evaluate_sequence_constraint(context, state, _need(TrainingRole.STRENGTH), AS_OF + timedelta(days=1), projected=strength_projected)

    assert same_spacing.eligibility is SequenceEligibility.PROHIBITED
    assert "quality_minimum_spacing_days" in same_spacing.policy_rule_ids
    assert clear_spacing.eligibility is SequenceEligibility.ELIGIBLE
    assert strength.eligibility is SequenceEligibility.PROHIBITED
    assert "strength_minimum_spacing_days" in strength.policy_rule_ids


def test_race_hard_recovery_blocks_hard_work_but_allows_low_cost_reentry():
    race = _fact("race", AS_OF - timedelta(days=1), HistoricalFactType.RACE_FACT, priority="B")
    context = _context(facts=(race,))
    state = _state(context)

    hard = evaluate_sequence_constraint(context, state, _need(TrainingRole.STRENGTH), AS_OF)
    low = evaluate_sequence_constraint(context, state, _need(TrainingRole.DEDICATED_UT2), AS_OF)

    assert hard.eligibility is SequenceEligibility.PROHIBITED
    assert "recovery_hard_prohibited" in hard.diagnostic_codes
    assert low.eligibility is SequenceEligibility.ELIGIBLE_LOW_COST_ONLY
    assert low.recovery_status == "hard_recovery"


def test_seeded_future_race_has_the_same_recovery_consequence_in_projected_sequence():
    race_day = AS_OF + timedelta(days=2)
    context = _context(as_of=AS_OF)
    state = _state(context)
    projected = ProjectedSequenceState((
        ProjectedSequenceItem("future-race", race_day, TrainingRole.RACE, authoritative=True, source="race:B"),
    ))

    strength = evaluate_sequence_constraint(context, state, _need(TrainingRole.STRENGTH), race_day + timedelta(days=1), projected=projected)
    ut2 = evaluate_sequence_constraint(context, state, _need(TrainingRole.DEDICATED_UT2), race_day + timedelta(days=1), projected=projected)

    assert strength.eligibility is SequenceEligibility.PROHIBITED
    assert "projected_race_hard_recovery:future-race" in strength.hard_rejection_reasons
    assert ut2.eligibility is SequenceEligibility.ELIGIBLE_LOW_COST_ONLY
    assert "projected_race_low_cost_allowed" in ut2.diagnostic_codes


def test_two_seeded_future_races_extend_projected_hard_recovery():
    first = AS_OF + timedelta(days=1)
    second = AS_OF + timedelta(days=3)
    context = _context(as_of=AS_OF)
    state = _state(context)
    projected = ProjectedSequenceState((
        ProjectedSequenceItem("race-one", first, TrainingRole.RACE, authoritative=True, source="race:B"),
        ProjectedSequenceItem("race-two", second, TrainingRole.RACE, authoritative=True, source="race:B"),
    ))

    decision = evaluate_sequence_constraint(context, state, _need(TrainingRole.STRENGTH), second + timedelta(days=3), projected=projected)

    assert decision.eligibility is SequenceEligibility.PROHIBITED
    assert "projected_multiple_race_recovery_extension:race-two" in decision.hard_rejection_reasons


def test_soft_recovery_allows_low_cost_but_reduces_long_aerobic():
    # C race: one hard day; the following date is a soft-recovery transition.
    as_of = AS_OF
    race = _fact("race", as_of - timedelta(days=2), HistoricalFactType.RACE_FACT, priority="C")
    context = _context(as_of=as_of, facts=(race,))
    state = _state(context)

    low = evaluate_sequence_constraint(context, state, _need(TrainingRole.DEDICATED_UT2), as_of)
    long = evaluate_sequence_constraint(context, state, _need(TrainingRole.LONG_AEROBIC, minutes=75), as_of)

    assert low.eligibility is SequenceEligibility.ELIGIBLE_LOW_COST_ONLY
    assert "soft_recovery_low_cost_allowed" in low.diagnostic_codes
    assert long.eligibility is SequenceEligibility.ELIGIBLE_REDUCED
    assert long.recommended_credit_adjustment.minutes == 38
    assert "race_soft_recovery_days_by_priority" in long.policy_rule_ids


def test_repeated_races_extend_hard_recovery_and_course_practice_does_not():
    races = (
        _fact("race-one", AS_OF - timedelta(days=4), HistoricalFactType.RACE_FACT, priority="B"),
        _fact("race-two", AS_OF - timedelta(days=3), HistoricalFactType.RACE_FACT, priority="B"),
    )
    context = _context(facts=races)
    extended = evaluate_sequence_constraint(context, _state(context), _need(TrainingRole.THRESHOLD_AT), AS_OF)
    practice = _fact("practice", AS_OF - timedelta(days=1), HistoricalFactType.PRESCRIBED_SESSION)
    practice_context = _context(facts=(practice,))
    ordinary = evaluate_sequence_constraint(practice_context, _state(practice_context), _need(TrainingRole.THRESHOLD_AT), AS_OF)

    assert extended.eligibility is SequenceEligibility.PROHIBITED
    assert "multiple_race_recovery_extension" in extended.hard_rejection_reasons
    assert ordinary.eligibility is SequenceEligibility.ELIGIBLE


def test_taper_reduces_fatiguing_load_but_keeps_maintenance_eligible_without_rest():
    race_day = AS_OF + timedelta(days=2)
    context = _context(races=((race_day, "B"),))
    state = _state(context)
    long = evaluate_sequence_constraint(context, state, _need(TrainingRole.LONG_AEROBIC, minutes=75), AS_OF)
    maintenance = evaluate_sequence_constraint(context, state, _need(TrainingRole.TAPER_MAINTENANCE), AS_OF)

    assert long.eligibility is SequenceEligibility.ELIGIBLE_REDUCED
    assert long.load_ceiling == "taper_reduced"
    assert maintenance.eligibility is SequenceEligibility.ELIGIBLE
    assert "taper_maintenance_allowed" in maintenance.diagnostic_codes


def test_future_authority_is_reported_without_mutation_or_calendar_optimization():
    fixed = AuthoritativeCommitment(
        "private", AuthorityType.PRIVATE_COACHING, AS_OF, None, 50,
        AuthoritySource.RECURRING_ACTIVITY, ConflictBehavior.PRESERVE_AND_REPORT,
        Provenance("private", ProvenanceSource.RECURRING_ACTIVITY),
    )
    context = _context(commitments=(fixed,))
    state = _state(context)
    decision = evaluate_sequence_constraint(context, state, _need(TrainingRole.DEDICATED_UT2), AS_OF)

    assert decision.eligibility is SequenceEligibility.DISCOURAGED
    assert decision.authority_conflicts == ("authority_context:private_coaching:private",)
    assert context.authoritative_commitments == (fixed,)
    assert all(item.role is not TrainingRole.REST for item in ( _need(TrainingRole.DEDICATED_UT2), ))


def test_weekday_and_week_boundary_shifts_have_identical_relative_decisions():
    first_day = date(2026, 10, 11)  # Sunday
    second_day = first_day + timedelta(days=7)  # Sunday one display week later
    first_context = _context(as_of=first_day)
    second_context = _context(as_of=second_day)
    first = evaluate_sequence_constraint(first_context, _state(first_context), _need(TrainingRole.DEDICATED_UT2, start=first_day), first_day)
    second = evaluate_sequence_constraint(second_context, _state(second_context), _need(TrainingRole.DEDICATED_UT2, start=second_day), second_day)
    crossed_context = _context(as_of=first_day + timedelta(days=1))  # Monday
    crossed = evaluate_sequence_constraint(crossed_context, _state(crossed_context), _need(TrainingRole.DEDICATED_UT2, start=first_day + timedelta(days=1)), first_day + timedelta(days=1))

    assert (first.eligibility, first.recovery_status, first.spacing_status, first.race_taper_status) == (second.eligibility, second.recovery_status, second.spacing_status, second.race_taper_status)
    assert (first.eligibility, first.recovery_status) == (crossed.eligibility, crossed.recovery_status)


def test_decisions_are_deterministic_and_trace_policy_without_software_search_reasons():
    context = _context()
    state = _state(context)
    need = _need(TrainingRole.DEDICATED_UT2)
    first = evaluate_sequence_constraint(context, state, need, AS_OF)
    second = evaluate_sequence_constraint(context, state, need, AS_OF)

    assert first == second
    assert first.policy_rule_ids == ("test_policy_rule",)
    assert not any("beam" in item or "tie_break" in item for item in first.policy_rule_ids)
