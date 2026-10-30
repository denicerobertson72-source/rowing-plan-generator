from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from rowing_plan.models import WindowPlacement
from rowing_plan.planning_adapters import adapt_v2_inputs
from rowing_plan.planning_domain import (
    AuthorityType,
    HistoricalFact,
    HistoricalFactType,
    PhysiologicalCredit,
    PlacementAuthorityStatus,
    Provenance,
    ProvenanceSource,
    TrainingNeed,
    TrainingRole,
)
from rowing_plan.training_policy import TrainingPolicyRule
from rowing_plan.planning_domain import RuleClass


AS_OF = date(2026, 10, 12)


def _profile():
    return {
        "season": {"start_date": "2026-10-01", "end_date": "2026-10-31"},
        "races": [
            {"event_name": "Past", "race_dates": ["2026-10-10"]},
            {"event_name": "Future", "race_dates": ["2026-10-20"], "practice_sessions": [{"date": "2026-10-19", "duration_minutes": 25}]},
        ],
        "recurring_activities": [
            {"activity_id": "private", "activity_type": "private_coaching", "scheduling_status": "fixed", "fixed_days": ["wednesday"], "duration_minutes": 55},
            {"activity_id": "coach", "activity_type": "coached_training", "scheduling_status": "fixed", "fixed_days": ["thursday"], "duration_minutes": 50},
            {"activity_id": "rest", "activity_type": "rest", "scheduling_status": "fixed", "fixed_days": ["sunday"]},
        ],
    }


def _prior_plan():
    return {
        "sessions": [
            {"date": "2026-10-09", "session_id": "UT2", "mode": "erg", "total_cardio_minutes": 45},
            {"date": "2026-10-10", "session_id": "LIFT", "mode": "strength", "total_training_minutes": 35},
            {"date": "2026-10-12", "session_id": "FUTURE", "mode": "erg", "total_cardio_minutes": 45},
        ],
    }


def _actuals():
    return {
        "2026-10-10:LIFT:strength": {"status": "completed", "actual_duration_min": 38},
        "actual:2026-10-11:walk": {"status": "completed", "actual_duration_min": 30},
        "actual:2026-10-11:erg": {"status": "completed", "actual_duration_min": 25},
    }


def _snapshot(placements=()):
    return adapt_v2_inputs(_profile(), prior_plan=_prior_plan(), actuals=_actuals(), planning_date=AS_OF, future_placements=placements)


def test_optional_off_day_actual_is_immutable_history_never_authority():
    snapshot = _snapshot()
    optional = [item for item in snapshot.historical_facts if item.type is HistoricalFactType.OPTIONAL_ACTIVITY]
    assert {item.completed_actual["activity_type"] for item in optional} == {"walk", "erg"}
    assert all(item.immutable and not item.future_placement_authority for item in optional)
    assert all("actual:2026-10-11" not in item.provenance.source_object_id for item in snapshot.authoritative_commitments)


def test_completed_planned_session_has_eligible_credit_and_prescription_only_does_not():
    snapshot = _snapshot()
    facts = {item.provenance.source_object_id: item for item in snapshot.historical_facts if item.provenance.source_type is ProvenanceSource.PRIOR_PLAN}
    assert facts["2026-10-10:LIFT:strength"].physiological_credit == PhysiologicalCredit(TrainingRole.STRENGTH, 1, 38, True)
    assert facts["2026-10-09:UT2:erg"].type is HistoricalFactType.PRESCRIBED_SESSION
    assert facts["2026-10-09:UT2:erg"].physiological_credit == PhysiologicalCredit()


def test_historical_race_is_fact_and_future_race_is_authoritative_commitment():
    snapshot = _snapshot()
    assert any(item.type is HistoricalFactType.RACE_FACT and item.date == date(2026, 10, 10) for item in snapshot.historical_facts)
    assert any(item.type is AuthorityType.RACE and item.date == date(2026, 10, 20) for item in snapshot.authoritative_commitments)


def test_course_practice_and_fixed_recurring_activities_become_commitments():
    commitments = _snapshot().authoritative_commitments
    assert any(item.type is AuthorityType.COURSE_PRACTICE and item.date == date(2026, 10, 19) for item in commitments)
    assert any(item.type is AuthorityType.PRIVATE_COACHING for item in commitments)
    assert any(item.type is AuthorityType.FIXED_COACHING for item in commitments)
    assert any(item.type is AuthorityType.FIXED_REST and item.reservation_minutes == 0 for item in commitments)


def test_user_fixed_future_placement_is_authoritative_and_provisional_is_not():
    fixed = WindowPlacement("fixed", date(2026, 10, 15), "strength", "strength", 40, user_fixed=True, original_date=date(2026, 10, 14), override_id="move-1")
    provisional = WindowPlacement("provisional", date(2026, 10, 16), "quality", "quality", 35)
    snapshot = _snapshot((provisional, fixed))
    commitment = next(item for item in snapshot.authoritative_commitments if item.id == "user_fixed:fixed")
    assert commitment.type is AuthorityType.USER_FIXED_PLACEMENT
    assert commitment.provenance.original_date == date(2026, 10, 14)
    assert snapshot.provisional_placement_ids == ("provisional",)
    assert all(item.id != "user_fixed:provisional" for item in snapshot.authoritative_commitments)


def test_domain_fact_is_frozen_and_serializable_with_provenance_identity():
    fact = HistoricalFact("x", date(2026, 10, 1), HistoricalFactType.PRESCRIBED_SESSION, planned_prescription={"nested": ["value"]}, provenance=Provenance("source", ProvenanceSource.PRIOR_PLAN, original_serialized_identity=("a", "b")))
    assert HistoricalFact.from_dict(fact.to_dict()) == fact
    with pytest.raises((FrozenInstanceError, TypeError)):
        fact.date = date(2026, 10, 2)
    with pytest.raises(TypeError):
        fact.planned_prescription["other"] = "no"


def test_training_need_carries_no_weekday_or_calendar_week_identity():
    need = TrainingNeed("need", TrainingRole.QUALITY, PhysiologicalCredit(TrainingRole.QUALITY, 1, 30, True), date(2026, 10, 12), date(2026, 10, 20), "high", "strong", "policy")
    assert not hasattr(need, "weekday") and not hasattr(need, "calendar_week")
    with pytest.raises(TypeError):
        TrainingNeed("need", TrainingRole.QUALITY, PhysiologicalCredit(), date(2026, 10, 12), date(2026, 10, 20), "high", "strong", "policy", weekday="monday")


def test_adapter_is_deterministic_and_does_not_consume_v2_inputs():
    fixed = WindowPlacement("fixed", date(2026, 10, 15), "strength", "strength", 40, user_fixed=True)
    first = _snapshot((fixed,))
    second = _snapshot((fixed,))
    assert first == second


def test_training_policy_rule_is_immutable_versioned_and_classified():
    rule = TrainingPolicyRule("post_race_hard_day", {"days": 1}, RuleClass.RESEARCH_CONSTRAINED, ("world-rowing",), "Protect immediate recovery", "coach_only", "v1")
    assert rule.policy_version == "v1" and rule.rule_class is RuleClass.RESEARCH_CONSTRAINED
    with pytest.raises(FrozenInstanceError):
        rule.key = "other"


def test_placement_authority_status_contract_is_explicit():
    assert {item.value for item in PlacementAuthorityStatus} == {"provisional", "authoritative", "user_fixed"}
