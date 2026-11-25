from datetime import date, timedelta

from rowing_plan.physiological_evidence import build_physiological_evidence_profile
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import (
    AuthoritySource, AuthorityType, AuthoritativeCommitment, ComponentAuthority,
    ConflictBehavior, HistoricalFact, HistoricalFactType, PhysicalCategory,
    PhysiologicalCredit, Provenance, ProvenanceSource, TrainingComponent,
    TrainingComponentPurpose, TrainingDay, TrainingDayPlanResult, TrainingDayState,
    TrainingModality, TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext
from rowing_plan.training_exposure_ledger import build_continuous_training_exposure_ledger


START = date(2026, 11, 16)  # Monday


def _fact(identifier, day, kind, *, role=None, minutes=0, actual=None, planned=None):
    return HistoricalFact(
        identifier, day, kind, planned_prescription=planned,
        completed_actual=actual,
        physiological_credit=PhysiologicalCredit(role, 1, minutes, True) if role else PhysiologicalCredit(),
        provenance=Provenance(identifier, ProvenanceSource.COMPLETED_LOG, original_date=day),
    )


def _context(facts=(), commitments=()):
    return NormalizedPlanningContext(
        START, tuple(facts), tuple(commitments), (), (), AUTHORITY_PRECEDENCE,
        NormalizationDiagnostics(historical_facts=len(facts)),
    )


def _component(identifier, category, minutes, purpose, *, modality=None, band=None, authority=ComponentAuthority.GENERATED, sequence=1):
    return TrainingComponent(
        identifier, f"lineage:{identifier}", "r3:2026-11-16:2026-11-22", category, minutes,
        purpose, sequence=sequence, modality=modality, authority=authority,
        physiological_band=band,
        quality_work_minutes=20 if band else None,
        quality_requirement_id="q" if band else None,
        quality_exposure_id="q-e" if band else None,
    )


def _calendar():
    return TrainingDayPlanResult((
        TrainingDay(START, 70, (
            _component("lift", PhysicalCategory.STRENGTH, 45, TrainingComponentPurpose.STRENGTH,
                       modality=TrainingModality.STRENGTH),
            _component("stairs", PhysicalCategory.GENERAL_AEROBIC, 15, TrainingComponentPurpose.EASY_AEROBIC,
                       modality=TrainingModality.STAIR_CLIMBER, sequence=2),
        ), TrainingDayState.PLANNED_TRAINING),
        TrainingDay(START + timedelta(days=1), 70, (
            _component("easy", PhysicalCategory.ROWING_SPECIFIC, 45, TrainingComponentPurpose.EASY_AEROBIC,
                       modality=TrainingModality.ERG),
        ), TrainingDayState.PLANNED_TRAINING),
        TrainingDay(START + timedelta(days=2), 0, (), TrainingDayState.DESIGNATED_REST),
    ))


def _evidence(*tests):
    return build_physiological_evidence_profile(
        {"tests": {"testing_blocks": [{"id": "battery", "performance_tests": list(tests)}]}},
        as_of=START,
    )


def _ledger(facts=(), *, commitments=(), evidence_tests=()):
    context = _context(facts, commitments)
    state = reduce_physiological_state(context)
    return build_continuous_training_exposure_ledger(
        normalized_context=context, physiological_state=state,
        current_training=type("Current", (), {
            "source": type("Source", (), {"value": "mixed"})(), "observation_window_days": 21,
            "approximate_total_physical_minutes": 500, "approximate_rowing_minutes": 240,
            "strength_exposures": 3, "consistency": None,
        })(),
        training_days=_calendar(), physiological_evidence=_evidence(*evidence_tests),
        allocation_segments=(type("Segment", (), {"start_date": START, "end_date": START + timedelta(days=2)})(),
                             type("Segment", (), {"start_date": START + timedelta(days=3), "end_date": START + timedelta(days=6)})()),
        planning_start=START, planning_end=START + timedelta(days=6),
        profile={"planning_goals": {"primary_goal": "develop_strength"}},
    )


def test_completed_history_and_generated_calendar_remain_continuous_across_sunday_monday_boundary():
    sunday_quality = _fact(
        "sunday-quality", START - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION,
        role=TrainingRole.QUALITY, minutes=35,
        actual={"status": "completed", "actual_duration_min": 45, "actual_intensity": "AT"},
    )
    ledger = _ledger((sunday_quality,))
    by_id = {item["exposure_id"]: item for item in ledger["exposures"]}
    assert by_id["historical:sunday-quality"]["status"] == "completed_actual"
    assert by_id["component:lift"]["status"] == "generated_future"
    assert ledger["rolling_completed_summaries"]["7d"]["quality_exposures"] == 1
    assert all(item["contiguous"] for item in ledger["continuity"]["cadence_boundaries"])


def test_combined_strength_and_general_aerobic_are_distinct_from_easy_rowing_and_recovery():
    ledger = _ledger()
    daily = {item["date"]: item for item in ledger["daily_summaries"]}
    monday = daily[START.isoformat()]
    tuesday = daily[(START + timedelta(days=1)).isoformat()]
    assert monday["combined_training_day"] is True
    assert monday["categories"] == {"general_aerobic": 15, "strength": 45}
    assert monday["modalities"] == ["stair_climber", "strength"]
    assert tuesday["explicit_recovery_or_technical"] is False
    assert daily[(START + timedelta(days=2)).isoformat()]["explicit_complete_rest"] is True


def test_race_occurrence_with_unknown_work_is_not_zero_and_scope_difference_is_explicit():
    races = (
        _fact("race-oct17", date(2026, 10, 17), HistoricalFactType.RACE_FACT, planned={"priority": "B"}),
        _fact("race-oct25", date(2026, 10, 25), HistoricalFactType.RACE_FACT, planned={"priority": "B"}),
    )
    ledger = _ledger(races)
    race = ledger["race_reconciliation"]
    race_exposures = [item for item in ledger["exposures"] if item["race_exposure"]]
    assert race["completed_race_occurrence_count_all_history"] == 2
    assert race["trailing_14d_recovery_race_exposure_count"] == 0
    assert race["scope_difference_explained"] is True
    assert race["unknown_race_work_is_not_zero"] is True
    assert all(item["physical_duration_minutes"] is None for item in race_exposures)


def test_assessment_occurrence_has_no_invented_session_duration_and_explicit_match_is_not_duplicated():
    matched = _fact(
        "assessment-session", START - timedelta(days=2), HistoricalFactType.COMPLETED_PLANNED_SESSION,
        role=TrainingRole.DEDICATED_UT2, minutes=40,
        actual={"status": "completed", "actual_duration_min": 40, "performance_test_id": "two-k"},
    )
    ledger = _ledger((matched,), evidence_tests=(
        {"id": "two-k", "protocol": "two_k", "test_date": "2026-11-14", "time_seconds": 480, "valid_for_profile": True},
        {"id": "seven", "protocol": "seven_stroke_peak", "test_date": "2026-11-12", "peak_watts": 500, "valid_for_profile": True},
    ))
    assessments = [item for item in ledger["exposures"] if item["assessment_exposure"]]
    assert [item["source_lineage_id"] for item in assessments] == ["seven"]
    assert assessments[0]["physical_duration_minutes"] is None
    assert "complete_assessment_session_duration_unknown" in assessments[0]["uncertainty"]


def test_planned_or_cancelled_work_never_receives_completed_credit_and_reported_baseline_stays_undated():
    planned = _fact("planned", START - timedelta(days=2), HistoricalFactType.PRESCRIBED_SESSION, planned={"mode": "erg"})
    cancelled = _fact(
        "cancelled", START - timedelta(days=1), HistoricalFactType.COMPLETED_PLANNED_SESSION,
        role=TrainingRole.STRENGTH, minutes=45, actual={"status": "skipped", "actual_duration_min": 45},
    )
    ledger = _ledger((planned, cancelled))
    rows = {item["exposure_id"]: item for item in ledger["exposures"]}
    assert rows["historical:planned"]["status"] == "planned_uncompleted"
    assert rows["historical:cancelled"]["status"] == "cancelled_or_missed"
    assert ledger["rolling_completed_summaries"]["7d"]["completed_physical_minutes"] == 0
    assert ledger["reported_training_baseline"]["dated_exposures_created"] == 0
    assert ledger["sequence_policy_inventory"]
    assert ledger["privacy"] == {
        "private_readiness_used": False, "free_text_notes_exposed": False,
        "time_resolution": "date_only_when_exact_time_is_not_recorded",
    }


def test_authoritative_future_commitment_is_prospective_and_never_completed_history():
    commitment = AuthoritativeCommitment(
        "fixed-race", AuthorityType.RACE, START + timedelta(days=5), None, 0,
        AuthoritySource.RACE_PROFILE, ConflictBehavior.PRESERVE_AND_REPORT,
        Provenance("race-profile:fixed-race", ProvenanceSource.RACE_PROFILE),
    )
    ledger = _ledger(commitments=(commitment,))
    race = next(item for item in ledger["exposures"] if item["exposure_id"] == "commitment:fixed-race")
    assert race["status"] == "authoritative_commitment"
    assert race["physical_duration_minutes"] is None
    assert ledger["rolling_completed_summaries"]["7d"]["race_exposures"] == 0
