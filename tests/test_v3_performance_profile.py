from datetime import date

from rowing_plan.performance_profile import (
    build_athlete_performance_profile, derive_development_block,
    derive_performance_limiter, derive_training_focus,
)
from rowing_plan.session_selection import select_and_instantiate


AS_OF = date(2026, 10, 30)


def _profile(tests=()):
    return {"tests": {"testing_blocks": [{"id": "battery", "end_date": "2026-10-20", "performance_tests": list(tests)}]}}


def _test(identifier, protocol, watts, day="2026-10-20"):
    return {"id": identifier, "protocol": protocol, "average_watts": watts, "test_date": day, "valid_for_profile": True}


def test_performance_profile_keeps_raw_test_domains_and_relative_relationships_separate():
    profile = build_athlete_performance_profile(_profile((
        {"id": "2k", "protocol": "two_k", "time_seconds": 420, "test_date": "2026-10-20", "valid_for_profile": True},
        _test("r20", "thirty_min_rate_capped_optional", 210), _test("60", "sixty_second", 430), _test("7", "seven_stroke_peak", 700),
    )), as_of=AS_OF)
    assert profile.two_k_watts and profile.thirty_r20_watts == 210
    assert profile.sixty_second_watts == 430 and profile.seven_stroke_watts == 700
    assert {"thirty_r20_to_two_k", "sixty_second_to_two_k", "seven_stroke_to_sixty_second"} <= set(profile.relationships)
    assert profile.evidence_completeness == "complete"


def test_missing_test_evidence_stays_unknown_and_season_focus_still_works():
    performance = build_athlete_performance_profile(_profile(), as_of=AS_OF)
    limiter = derive_performance_limiter(performance)
    focus = derive_training_focus(limiter, phase="general_preparation")
    assert (limiter.mode, limiter.primary_focus, focus.primary) == ("unknown", "insufficient_evidence", "aerobic_development")


def test_complete_battery_uses_explicitly_labeled_athlete_relative_heuristic_and_block_is_stable():
    performance = build_athlete_performance_profile(_profile((
        _test("2k", "two_k", 300), _test("r20", "thirty_min_rate_capped_optional", 180), _test("60", "sixty_second", 450), _test("7", "seven_stroke_peak", 600),
    )), as_of=AS_OF)
    limiter = derive_performance_limiter(performance)
    focus = derive_training_focus(limiter, phase="general_preparation")
    block = derive_development_block(focus, planning_date=AS_OF)
    preserved = derive_development_block(focus, planning_date=AS_OF, prior_diagnostics={"performance_development_block": {"block_id": block.block_id, "start_date": block.start_date.isoformat(), "end_date": block.end_date.isoformat(), "primary_focus": block.primary_focus}})
    assert limiter.mode == "athlete_relative_heuristic"
    assert "evidence_informed_heuristic" in limiter.provenance
    assert preserved.block_id == block.block_id


def test_selector_history_penalizes_accidental_3x10_repetition_but_is_deterministic():
    args = dict(role="AEROBIC_BASE", experience="experienced", phase="general_preparation", race_type="general", mode="erg", minutes=44, preference="varied")
    first = select_and_instantiate(**args, history=[])
    second = select_and_instantiate(**args, history=[first["fingerprint"]])
    again = select_and_instantiate(**args, history=[first["fingerprint"]])
    assert second == again
    assert second["fingerprint"] != first["fingerprint"] or second["repetition_reason"]
    assert first["archetype"]["primary_band"] == second["archetype"]["primary_band"] == "UT2"


def test_upcoming_race_context_changes_only_compatible_race_pace_ranking():
    args = dict(role="RACE_PACE", experience="experienced", phase="taper", mode="erg", minutes=45, preference="varied", history=[])
    one_k = select_and_instantiate(**args, race_type="one_k", selection_context={"active_training_focus": "taper_race_readiness"})
    head = select_and_instantiate(**args, race_type="head_5k", selection_context={"active_training_focus": "taper_race_readiness"})
    assert one_k["archetype"]["primary_band"] == head["archetype"]["primary_band"] == "TR"
