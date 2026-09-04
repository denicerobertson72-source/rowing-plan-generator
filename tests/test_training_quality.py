import json
from datetime import date

from rowing_plan.session_selection import select_and_instantiate
from rowing_plan.scheduler import _reconcile_low_intensity_volume


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
