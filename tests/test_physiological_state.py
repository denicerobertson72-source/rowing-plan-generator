from datetime import date, timedelta

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
from rowing_plan.planning_normalization import (
    AUTHORITY_PRECEDENCE,
    NormalizationDiagnostics,
    NormalizedPlanningContext,
    normalize_planning_inputs,
)
from rowing_plan.physiological_state import reduce_physiological_state


AS_OF = date(2026, 10, 12)


def _fact(identifier, day, fact_type, *, role=None, minutes=0, actual=None, planned=None):
    return HistoricalFact(
        identifier, day, fact_type,
        planned_prescription=planned,
        completed_actual=actual,
        physiological_credit=PhysiologicalCredit(role, 1, minutes, True) if role else PhysiologicalCredit(),
        provenance=Provenance(identifier, ProvenanceSource.COMPLETED_LOG, original_date=day),
    )


def _context(facts=(), *, as_of=AS_OF, commitments=(), race_context=()):
    return NormalizedPlanningContext(
        planning_date=as_of, historical_facts=tuple(facts),
        authoritative_commitments=tuple(commitments), provisional_prior_placement_ids=(),
        race_context=tuple(race_context), authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _shape(state, as_of):
    return {
        "aerobic": state.completed_aerobic_exposure,
        "quality": state.completed_quality_exposure,
        "strength": state.completed_strength_exposure,
        "aerobic_classes": state.completed_aerobic_by_classification,
        "quality_classes": state.completed_quality_by_classification,
        "races": state.completed_race_exposure,
        "constraints": tuple((item.source.split(":")[0], (item.start_date - as_of).days, (item.end_date - as_of).days, item.hard_excluded_roles) for item in state.recovery_constraints),
    }


def test_weekday_neutral_completed_history_and_monday_sunday_boundary():
    monday = date(2026, 10, 12)
    shifted_as_of = monday + timedelta(days=7)
    first = reduce_physiological_state(_context((_fact("row", monday - timedelta(days=2), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=45, actual={"actual_intensity": "UT2"}),), as_of=monday))
    second = reduce_physiological_state(_context((_fact("row", shifted_as_of - timedelta(days=2), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=45, actual={"actual_intensity": "UT2"}),), as_of=shifted_as_of))
    assert _shape(first, monday) == _shape(second, shifted_as_of)


def test_completed_ut2_changes_aerobic_exposure_but_uncompleted_prescription_does_not():
    completed = _fact("ut2", AS_OF - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=50, actual={"actual_intensity": "UT2"})
    missed = _fact("missed", AS_OF - timedelta(days=2), HistoricalFactType.PRESCRIBED_SESSION, planned={"band": "UT2"})
    state = reduce_physiological_state(_context((missed, completed)))
    assert state.completed_aerobic_exposure.exposures == 1
    assert state.completed_aerobic_by_classification["UT2"].minutes == 50
    assert "missed" not in state.completed_fact_ids


def test_optional_off_day_erg_actual_is_credited_once():
    optional = _fact("optional", AS_OF - timedelta(days=1), HistoricalFactType.OPTIONAL_ACTIVITY, role=TrainingRole.DEDICATED_UT2, minutes=30, actual={"actual_intensity": "UT2", "actual_duration_min": 30})
    state = reduce_physiological_state(_context((optional,)))
    assert state.completed_aerobic_exposure == PhysiologicalCredit(TrainingRole.DEDICATED_UT2, 1, 30, True)
    assert state.completed_duration_minutes_by_fact["optional"] == 30


def test_completed_strength_changes_strength_only_and_creates_factual_spacing_constraint():
    strength = _fact("lift", AS_OF - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.STRENGTH, minutes=40, actual={"actual_duration_min": 40})
    state = reduce_physiological_state(_context((strength,)))
    assert state.completed_strength_exposure.exposures == 1
    assert state.completed_aerobic_exposure.exposures == state.completed_quality_exposure.exposures == 0
    assert any(item.source == "strength:lift" for item in state.recovery_constraints)


def test_completed_quality_tracks_explicit_at_and_not_a_speculative_band():
    quality = _fact("at", AS_OF - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.QUALITY, minutes=35, actual={"actual_intensity": "AT"})
    uncertain = _fact("quality", AS_OF - timedelta(days=3), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.QUALITY, minutes=30, actual={})
    state = reduce_physiological_state(_context((quality, uncertain)))
    assert state.completed_quality_exposure.exposures == 2
    assert state.completed_quality_by_classification["AT"].minutes == 35
    assert state.completed_quality_by_classification["uncertain_quality"].minutes == 30


def test_race_contributes_stress_once_quality_once_and_recovery_without_a_session():
    race = _fact("race", AS_OF - timedelta(days=1), HistoricalFactType.RACE_FACT, planned={"priority": "B", "event_name": "Head race"})
    state = reduce_physiological_state(_context((race,)))
    assert state.completed_race_exposure.exposures == 1
    assert state.completed_quality_exposure.exposures == 1
    constraint = next(item for item in state.recovery_constraints if item.source == "race:race")
    assert constraint.hard_excluded_roles == (TrainingRole.QUALITY, TrainingRole.STRENGTH)
    assert constraint.start_date == AS_OF


def test_course_practice_does_not_become_race_stress():
    practice = _fact("practice", AS_OF - timedelta(days=1), HistoricalFactType.PRESCRIBED_SESSION, planned={"session_id": "COURSE_PRACTICE", "mode": "on_water"})
    state = reduce_physiological_state(_context((practice,)))
    assert state.completed_race_exposure.exposures == 0
    assert not state.completed_fact_ids


def test_normalized_duplicate_race_and_actual_representation_is_credited_once():
    profile = {"season": {"start_date": "2026-10-01", "end_date": "2026-10-30"}, "races": [{"event_name": "Past", "race_dates": ["2026-10-10"], "priority": "B"}], "recurring_activities": []}
    prior = {"sessions": [{"date": "2026-10-10", "session_id": "RACE", "mode": "race", "band": "RACE"}]}
    context = normalize_planning_inputs(profile, planning_date=AS_OF, prior_plan=prior)
    state = reduce_physiological_state(context)
    assert len(state.recent_race_load) == state.completed_race_exposure.exposures == 1


def test_historical_fact_ordering_is_idempotent():
    facts = (
        _fact("strength", AS_OF - timedelta(days=2), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.STRENGTH, minutes=40),
        _fact("ut2", AS_OF - timedelta(days=3), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=50, actual={"actual_intensity": "UT2"}),
    )
    assert reduce_physiological_state(_context(facts)) == reduce_physiological_state(_context(tuple(reversed(facts))))


def test_future_authority_does_not_change_completed_exposure():
    commitment = AuthoritativeCommitment("future", AuthorityType.USER_FIXED_PLACEMENT, AS_OF, None, 45, AuthoritySource.USER_OVERRIDE, ConflictBehavior.PRESERVE_AND_REPORT, Provenance("future", ProvenanceSource.USER_OVERRIDE))
    state = reduce_physiological_state(_context((), commitments=(commitment,)))
    assert not state.completed_fact_ids
    assert state.completed_aerobic_exposure == PhysiologicalCredit()


def test_no_age_sex_or_life_stage_modifier_is_introduced():
    fact = _fact("ut2", AS_OF - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION, role=TrainingRole.DEDICATED_UT2, minutes=45, actual={"actual_intensity": "UT2"})
    first = reduce_physiological_state(_context((fact,)), profile={"athlete": {"age": 25, "sex": "female"}})
    second = reduce_physiological_state(_context((fact,)), profile={"athlete": {"age": 65, "sex": "male", "menopause_status": "x"}})
    assert _shape(first, AS_OF) == _shape(second, AS_OF)
    assert first.athlete_response_inputs == second.athlete_response_inputs


def test_next_race_and_phase_context_are_factual_not_a_training_prescription():
    profile = {"season": {"start_date": "2026-10-01", "end_date": "2026-11-01"}, "races": []}
    context = _context(race_context=(("2026-10-20", "A", "Championship", "head_5k"),))
    state = reduce_physiological_state(context, profile=profile)
    assert state.next_race_context["days_until"] == 8
    assert state.next_race_context["event_name"] == "Championship"
    assert state.phase_context["source"] == "season_phase"
    assert state.progression_context == {}
