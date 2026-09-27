import json
from datetime import date

from rowing_plan.session_selection import select_and_instantiate
from rowing_plan.scheduler import _hard_session_spacing, _quality_spacing_findings, _reconcile_low_intensity_volume, _space_quality_roles


def _select(role, history=None):
    return select_and_instantiate(role=role, experience="experienced", phase="general_preparation", race_type="head_5k", mode="erg", minutes=60, preference="varied", history=history or [])


def test_role_semantics_rank_sustained_long_aerobic_and_technical_easy():
    long_row = _select("LONG_AEROBIC")
    easy_row = _select("TECHNIQUE_EASY")
    base_row = _select("AEROBIC_BASE")
    assert long_row["fingerprint"]["structure_family"] in {"continuous", "long_repeats", "progressive_duration"}
    assert long_row["fingerprint"]["total_work_duration"] > base_row["fingerprint"]["total_work_duration"]
    assert easy_row["fingerprint"]["structure_family"] in {"technical_intervals", "drill_aerobic", "low_rate_rhythm", "easy_continuous", "broken_recovery"}
    assert len(base_row["candidate_scores"]) >= 5
    assert len({row["score"] for row in long_row["candidate_scores"]}) > 1
    assert {"role_fit", "phase_fit", "preference", "history", "duplicate_structure", "progression"} <= long_row["candidate_scores"][0]["components"].keys()


def test_concrete_progression_and_history_avoid_minimum_restart():
    first = _select("LONG_AEROBIC")
    repeated = _select("LONG_AEROBIC", [first["fingerprint"]])
    assert repeated["work_interval_duration"] >= 15
    assert repeated["fingerprint"] != first["fingerprint"] or repeated["archetype"]["archetype_id"] != first["archetype"]["archetype_id"]


def test_bounded_reconciliation_extends_only_low_intensity_rowing():
    intents = [{"week_start":"2026-09-07", "target_total_rowing_minutes":150}]
    sessions = [
        {"date":"2026-09-08", "session_role":"AEROBIC_BASE", "band":"UT2", "rowing_minutes":80, "total_cardio_minutes":80, "structure":"Base", "session_fingerprint":{}},
        {"date":"2026-09-09", "session_role":"THRESHOLD", "band":"AT", "rowing_minutes":40, "total_cardio_minutes":40, "structure":"Threshold"},
    ]
    result = _reconcile_low_intensity_volume(intents, sessions, tolerance=.10)[0]
    assert result["final_status"] == "reconciled"
    assert sessions[0]["rowing_minutes"] > 80 and sessions[1]["rowing_minutes"] == 40


def test_impossible_reconciliation_is_explicit_not_unprocessed():
    intents = [{"week_start":"2026-09-07", "target_total_rowing_minutes":200}]
    sessions = [{"date":"2026-09-09", "session_role":"THRESHOLD", "band":"AT", "rowing_minutes":40, "total_cardio_minutes":40, "structure":"Threshold"}]
    result = _reconcile_low_intensity_volume(intents, sessions, tolerance=.10)[0]
    assert result["final_status"] == "infeasible_with_reason"
    assert result["status"] != "needs_reconciliation"


def test_adjacent_independent_hard_rows_are_recorded_but_race_days_are_not():
    adjacent = _hard_session_spacing([
        {"date":"2026-09-26", "session_role":"RACE_PACE", "band":"TR"},
        {"date":"2026-09-27", "session_role":"THRESHOLD", "band":"AT"},
    ])
    assert adjacent[0]["status"] == "unavoidable_constraints"
    assert not _hard_session_spacing([
        {"date":"2026-09-26", "session_id":"RACE", "band":"RACE"},
        {"date":"2026-09-27", "session_role":"THRESHOLD", "band":"AT"},
    ])


def test_cross_week_quality_role_is_relocated_using_continuous_dates():
    roles={"2026-09-13":"RACE_PACE", "2026-09-14":"THRESHOLD", "2026-09-15":"AEROBIC_BASE"}
    spaced,moves=_space_quality_roles(roles,[])
    assert spaced["2026-09-14"] == "AEROBIC_BASE"
    assert spaced["2026-09-15"] == "THRESHOLD"
    assert moves == [{"quality_role":"THRESHOLD","from_date":"2026-09-14","to_date":"2026-09-15","reason":"continuous_quality_recovery_spacing"}]


def test_same_week_and_cross_week_quality_pairs_share_one_validator_with_race_exception():
    assert _quality_spacing_findings([
        {"date":"2026-09-09", "session_role":"THRESHOLD", "band":"AT"},
        {"date":"2026-09-10", "session_role":"RACE_PACE", "band":"TR"},
    ])
    assert _quality_spacing_findings([
        {"date":"2026-09-13", "session_role":"RACE_PACE", "band":"TR", "session_fingerprint":{"primary_band":"TR"}},
        {"date":"2026-09-14", "session_role":"THRESHOLD", "band":"AT", "session_fingerprint":{"primary_band":"AT"}},
    ])[0]["recovery_gap_days"] == 1
    assert not _quality_spacing_findings([
        {"date":"2026-09-13", "session_id":"RACE", "band":"RACE"},
        {"date":"2026-09-14", "session_id":"RACE", "band":"RACE"},
    ])
