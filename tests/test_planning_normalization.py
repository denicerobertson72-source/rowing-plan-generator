from datetime import date

from rowing_plan.models import WindowPlacement
from rowing_plan.planning_domain import AuthorityType, HistoricalFactType
from rowing_plan.planning_normalization import (
    AUTHORITY_PRECEDENCE,
    LEGACY_LOCKED_SESSION_AUDIT,
    normalize_planning_inputs,
)


AS_OF = date(2026, 10, 12)


def _profile():
    return {
        "season": {"start_date": "2026-10-01", "end_date": "2026-10-31"},
        "races": [
            {"event_name": "Past race", "race_dates": ["2026-10-10"]},
            {"event_name": "Future race", "race_dates": ["2026-10-20"], "practice_sessions": [{"date": "2026-10-19", "duration_minutes": 25}]},
        ],
        "recurring_activities": [
            {"activity_id": "fixed-coach", "activity_type": "coached_training", "scheduling_status": "fixed", "fixed_days": ["wednesday"], "duration_minutes": 50},
            {"activity_id": "flex-coach", "activity_type": "coached_training", "scheduling_status": "preferred", "fixed_days": [], "preferred_days": ["wednesday"], "duration_minutes": 50},
        ],
    }


def _session(day, session_id="ROW", mode="erg", **extra):
    return {"date": day, "session_id": session_id, "mode": mode, "total_cardio_minutes": 45, **extra}


def _prior_plan():
    return {
        "calendar_days": [
            {"date": "2026-10-09", "designated_rest": True, "state": "off", "commitments": []},
            {"date": "2026-10-10", "designated_rest": False, "state": "planned", "commitments": []},
        ],
        "sessions": [
            _session("2026-10-10", "DONE"),
            _session("2026-10-11", "MISSED"),
            _session("2026-10-10", "RACE", "race", band="RACE"),
            _session("2026-10-12", "TODAY"),
            _session("2026-10-13", "TOMORROW"),
        ],
    }


def _context(*, actuals=None, locks=(), placements=(), prior=None):
    return normalize_planning_inputs(
        _profile(), planning_date=AS_OF, prior_plan=_prior_plan() if prior is None else prior,
        history_actuals=actuals or {}, locked_sessions=locks, future_placements=placements,
    )


def test_past_off_is_one_zero_credit_audit_fact():
    context = _context(prior={"calendar_days": _prior_plan()["calendar_days"], "sessions": []})
    off = next(item for item in context.historical_facts if item.date == date(2026, 10, 9))
    assert off.type is HistoricalFactType.PRESCRIBED_SESSION
    assert off.physiological_credit.eligible is False
    assert context.diagnostics.historical_prescriptions_without_credit == 1


def test_past_off_optional_erg_is_credited_once_and_never_future_authority():
    actuals = {"actual:2026-10-09:erg": {"status": "completed", "actual_duration_min": 30}}
    context = _context(actuals=actuals, prior={"calendar_days": _prior_plan()["calendar_days"], "sessions": []})
    optional = next(item for item in context.historical_facts if item.type is HistoricalFactType.OPTIONAL_ACTIVITY)
    assert optional.physiological_credit.eligible and optional.physiological_credit.minutes == 30
    assert all(item.provenance.source_object_id != "actual:2026-10-09:erg" for item in context.authoritative_commitments)
    assert context.diagnostics.completed_credit_facts == 1


def test_completed_planned_workout_preserves_plan_and_actual_with_one_credit():
    actuals = {"2026-10-10:DONE:erg": {"status": "completed", "actual_duration_min": 42}}
    context = _context(actuals=actuals)
    fact = next(item for item in context.historical_facts if item.provenance.source_object_id == "2026-10-10:DONE:erg")
    assert fact.planned_prescription["session_id"] == "DONE"
    assert fact.completed_actual["actual_duration_min"] == 42
    assert fact.physiological_credit.minutes == 42 and fact.physiological_credit.eligible


def test_uncompleted_past_prescription_is_audit_only():
    context = _context()
    fact = next(item for item in context.historical_facts if item.provenance.source_object_id == "2026-10-11:MISSED:erg")
    assert fact.type is HistoricalFactType.PRESCRIBED_SESSION
    assert not fact.physiological_credit.eligible


def test_past_race_is_one_historical_fact_even_when_prior_plan_also_has_race():
    context = _context()
    races = [item for item in context.historical_facts if item.type is HistoricalFactType.RACE_FACT and item.date == date(2026, 10, 10)]
    assert len(races) == 1
    assert context.diagnostics.historical_race_facts == 1
    assert context.diagnostics.duplicates_collapsed >= 1


def test_future_race_course_practice_and_fixed_wednesday_coaching_are_authority():
    context = _context()
    assert any(item.type is AuthorityType.RACE and item.date == date(2026, 10, 20) for item in context.authoritative_commitments)
    assert any(item.type is AuthorityType.COURSE_PRACTICE and item.date == date(2026, 10, 19) for item in context.authoritative_commitments)
    assert any(item.type is AuthorityType.FIXED_COACHING and item.date.weekday() == 2 for item in context.authoritative_commitments)
    assert context.authority_precedence == AUTHORITY_PRECEDENCE


def test_future_user_fixed_is_authority_and_provisional_is_id_only():
    fixed = WindowPlacement("fixed", date(2026, 10, 14), "strength", "strength", 40, user_fixed=True)
    provisional = WindowPlacement("provisional", date(2026, 10, 15), "quality", "quality", 35)
    context = _context(placements=(fixed, provisional))
    assert any(item.type is AuthorityType.USER_FIXED_PLACEMENT for item in context.authoritative_commitments)
    assert context.provisional_prior_placement_ids == ("provisional",)


def test_preferred_flexible_weekday_is_not_authoritative():
    context = _context()
    assert all(item.provenance.source_object_id != "recurring:flex-coach:2026-10-14" for item in context.authoritative_commitments)


def test_duplicate_completed_actual_from_legacy_lock_collapses_to_one_credit():
    actual = {"status": "completed", "actual_duration_min": 42}
    locked = _session("2026-10-10", "DONE", source_id="2026-10-10:DONE:erg")
    context = _context(actuals={"2026-10-10:DONE:erg": actual}, locks=(locked,))
    facts = [item for item in context.historical_facts if item.date == date(2026, 10, 10) and item.planned_prescription and item.planned_prescription.get("session_id") == "DONE"]
    assert len(facts) == 1 and facts[0].physiological_credit.minutes == 42
    assert context.diagnostics.completed_credit_facts == 1


def test_planning_date_boundary_keeps_today_mutable_and_tomorrow_not_authority():
    context = _context()
    dates = {item.provenance.source_object_id for item in context.historical_facts}
    assert "2026-10-11:MISSED:erg" in dates
    assert "2026-10-12:TODAY:erg" not in dates
    assert "2026-10-13:TOMORROW:erg" not in dates
    assert all(item.date != AS_OF for item in context.authoritative_commitments if item.type is AuthorityType.EXPLICIT_API_LOCK)


def test_explicit_future_legacy_lock_is_authority_but_past_lock_is_history():
    future = _session("2026-10-13", "LOCK", source_id="api-lock")
    past = _session("2026-10-11", "PAST_LOCK", source_id="past-lock")
    context = _context(locks=(future, past))
    assert any(item.type is AuthorityType.EXPLICIT_API_LOCK and item.date == date(2026, 10, 13) for item in context.authoritative_commitments)
    assert any(item.provenance.source_object_id == "past-lock" for item in context.historical_facts)
    assert dict(LEGACY_LOCKED_SESSION_AUDIT)["optional_activity_history_session"] == "historical_fact"


def test_normalization_serialization_and_order_are_deterministic():
    actuals = {"actual:2026-10-09:erg": {"status": "completed", "actual_duration_min": 30}}
    first = _context(actuals=actuals)
    second = _context(actuals=actuals)
    assert first == second
    assert first.to_dict() == second.to_dict()
