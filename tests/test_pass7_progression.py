"""Pass 7: final-prescription progression and weekly-demand scheduling."""
from rowing_plan.session_selection import select_and_instantiate
from rowing_plan.schedule_scoring import weekly_candidates


def _select(role="RACE_PACE", history=None, reason=None):
    return select_and_instantiate(role=role, experience="experienced", phase="race_build" if role == "RACE_PACE" else "specific_preparation", race_type="head_5k", mode="erg", minutes=60, preference="varied", history=history or [], exact_repeat_reason=reason)


def test_exact_final_fingerprint_is_penalized_even_when_archetype_id_changes():
    first = _select()
    equivalent = next(row["fingerprint"] for row in first["candidate_scores"] if row["archetype_id"] == "head_06")
    history = [{**equivalent, "archetype_id": "different_catalog_id"}]
    again = _select(history=history)
    repeated = next(row for row in again["candidate_scores"] if row["archetype_id"] == "head_06")
    assert repeated["components"]["concrete_history"] == -24


def test_intentional_exact_repeat_is_explicitly_allowed():
    first = _select()
    repeated = _select(history=[first["fingerprint"]], reason="deliberate benchmark repeat")
    selected = next(row for row in repeated["candidate_scores"] if row["archetype_id"] == first["archetype"]["archetype_id"])
    assert selected["components"]["concrete_history"] == 0
    assert repeated["fingerprint"]["intentional_repeat_reason"] == "deliberate benchmark repeat"


def test_same_archetype_can_progress_parameters_and_multiple_quality_roles_are_reachable():
    first = _select("THRESHOLD")
    same_id_history = [{**first["fingerprint"], "work_interval_duration": first["fingerprint"]["work_interval_duration"] - 1, "total_work_duration": first["fingerprint"]["total_work_duration"] - first["fingerprint"]["repetitions"]}]
    progressed = _select("THRESHOLD", same_id_history)
    matching = next(row for row in progressed["candidate_scores"] if row["archetype_id"] == first["archetype"]["archetype_id"])
    assert matching["fingerprint"]["work_interval_duration"] > same_id_history[0]["work_interval_duration"]
    assert len(_select("THRESHOLD")["candidate_scores"]) >= 6
    assert len(_select("RACE_PACE")["candidate_scores"]) >= 6


def test_weekly_scoring_changes_for_current_hard_demand_but_can_stay_when_equal():
    activities = [
        {"activity_id":"lift", "activity_type":"strength", "sessions_per_week":1, "scheduling_status":"preferred", "preferred_days":["tuesday"], "allowed_days":["friday"], "prohibited_days":[]},
    ]
    days = ["tuesday", "friday", "saturday", "sunday"]
    ordinary = weekly_candidates(activities, days, set(), {"sunday"})[0]
    hard = weekly_candidates(activities, days, {"tuesday"}, {"sunday"}, hard_session_days={"tuesday"})[0]
    assert ordinary["placements"]["lift"] == ["tuesday"]
    assert hard["placements"]["lift"] == ["friday"]
    assert weekly_candidates(activities, days, set(), {"sunday"})[0]["placements"] == ordinary["placements"]
