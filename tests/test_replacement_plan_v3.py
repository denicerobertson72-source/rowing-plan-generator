from __future__ import annotations

import json
from datetime import date, timedelta

from rowing_plan.continuous_placement import PlacementSearchConfig
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.materialization_v2 import materialize_v2_session
from rowing_plan.models import WindowPlacement
from rowing_plan.planning_domain import (
    PhysiologicalCredit,
    Placement,
    PlacementAuthorityStatus,
    Provenance,
    ProvenanceSource,
    TrainingNeed,
    TrainingRole,
)
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import (
    adapt_placements_to_materialization_requests,
    generate_plan_sequence_v3_candidate,
)
from services.api.tests.disposable_browser_fixture import synthetic_profile


CONFIG = json.load(open("config/defaults.json"))
START = date(2026, 9, 6)


def _inputs(profile=None):
    profile = profile or synthetic_profile()
    return profile, build_intensity_profile(profile, CONFIG), build_power_profile(profile, CONFIG)


def _placement(identifier, day, role, *, reservation=60, credit_minutes=45, served=(), authority=PlacementAuthorityStatus.PROVISIONAL):
    return Placement(
        placement_id=identifier, training_need_id=f"need:{identifier}", date=day,
        reservation_minutes=reservation,
        target_credit=PhysiologicalCredit(role, 1, credit_minutes, True),
        provenance=Provenance(identifier, ProvenanceSource.LEGACY_V2),
        authority_status=authority, served_need_ids=served, role=role,
    )


def _need(identifier, role, start, end, *, minutes=60, priority="protected_aerobic_base"):
    return TrainingNeed(
        identifier, role, PhysiologicalCredit(role, 1, minutes, True), start, end,
        "hard" if role in {TrainingRole.STRENGTH, TrainingRole.RACE_SPECIFIC_TR} else "low",
        priority, "replacement-test-policy",
    )


def test_adapter_reuses_existing_materializers_for_supported_roles_without_weekday_identity():
    profile, bands, power = _inputs()
    placements = (
        _placement("ut2", START, TrainingRole.DEDICATED_UT2),
        _placement("long", START + timedelta(days=1), TrainingRole.LONG_AEROBIC, reservation=75, credit_minutes=70, served=("need:long", "need:ut2")),
        _placement("ut1", START + timedelta(days=2), TrainingRole.UT1_AEROBIC_STRENGTH),
        _placement("at", START + timedelta(days=3), TrainingRole.THRESHOLD_AT),
        _placement("tr", START + timedelta(days=4), TrainingRole.RACE_SPECIFIC_TR),
        _placement("an", START + timedelta(days=5), TrainingRole.ANAEROBIC_AN),
        _placement("pp", START + timedelta(days=6), TrainingRole.POWER_PP),
        _placement("strength", START + timedelta(days=7), TrainingRole.STRENGTH, reservation=45, credit_minutes=0),
        _placement("technical", START + timedelta(days=8), TrainingRole.TECHNICAL_REENTRY),
        _placement("recovery", START + timedelta(days=9), TrainingRole.RECOVERY_AEROBIC),
        _placement("taper", START + timedelta(days=10), TrainingRole.TAPER_MAINTENANCE),
        _placement("prep", START + timedelta(days=11), TrainingRole.RACE_PREPARATION),
    )
    adapted = adapt_placements_to_materialization_requests(placements, profile=profile)

    assert not adapted.diagnostics
    assert len(adapted.requests) == len(placements)
    assert {item.role for item in adapted.requests} >= {"dedicated_ut2", "long_aerobic", "ut1_aerobic_strength", "quality", "strength"}
    quality = [item for item in adapted.requests if item.role == "quality"]
    assert {item.concrete_quality.quality_type for item in quality} == {"AT", "TR", "AN", "PP"}
    assert all(item.date == next(placement.date for placement in placements if placement.placement_id == item.placement_id) for item in adapted.requests)
    assert all(materialize_v2_session(item, profile=profile, bands=bands, power=power).success for item in adapted.requests)


def test_duration_credit_and_long_ut2_relationship_remain_explicit_and_nonduplicated():
    profile, bands, power = _inputs()
    threshold = _placement("at", START, TrainingRole.THRESHOLD_AT, reservation=60, credit_minutes=35)
    long = _placement("long", START + timedelta(days=1), TrainingRole.LONG_AEROBIC, reservation=75, credit_minutes=60, served=("need:long", "need:ut2"))
    adapted = adapt_placements_to_materialization_requests((threshold, long), profile=profile)
    requests = {item.placement_id: item for item in adapted.requests}
    concrete = materialize_v2_session(requests["at"], profile=profile, bands=bands, power=power)

    assert concrete.success and concrete.session["total_cardio_minutes"] <= 60
    assert threshold.target_credit.minutes == 35
    assert requests["at"].planned_duration_minutes == 60
    assert long.served_need_ids == ("need:long", "need:ut2")
    assert len([item for item in adapted.requests if item.placement_id == "long"]) == 1


def test_adapter_reports_unmapped_role_without_inventing_a_workout():
    profile, _, _ = _inputs()
    unmapped = Placement("missing", "need:missing", START, 30, PhysiologicalCredit(), Provenance("missing", ProvenanceSource.LEGACY_V2), PlacementAuthorityStatus.PROVISIONAL)
    result = adapt_placements_to_materialization_requests((unmapped,), profile=profile)

    assert result.requests == ()
    assert result.diagnostics[0].code == "no_materialization_mapping"


def test_candidate_preserves_historical_off_and_optional_actual_without_turning_it_into_planned_work():
    profile, bands, power = _inputs()
    planning_day = date(2026, 10, 1)
    prior = {
        "calendar_days": [{"date": "2026-09-30", "designated_rest": True, "unavailable": False, "state": "designated_rest", "commitments": []}],
        "sessions": [],
    }
    need = _need("ut2", TrainingRole.DEDICATED_UT2, planning_day, planning_day + timedelta(days=3), minutes=60)
    candidate = generate_plan_sequence_v3_candidate(
        profile, CONFIG, bands, power, planning_date=planning_day, prior_plan=prior,
        history_actuals={"actual:2026-09-30:walk": {"status": "completed", "activity_type": "walk", "actual_duration_min": 30}},
        needs=(need,),
    )

    assert candidate.success
    historical = next(item for item in candidate.plan["calendar_days"] if item["date"] == "2026-09-30")
    assert historical["designated_rest"] is True
    assert not any(item.get("session_id") == "walk" for item in candidate.plan["sessions"])
    optional = [item for item in candidate.context.historical_facts if item.provenance.source_object_id == "actual:2026-09-30:walk"]
    assert len(optional) == 1 and optional[0].completed_actual["activity_type"] == "walk"


def test_user_fixed_serialized_authority_is_carried_not_rematerialized():
    profile, bands, power = _inputs()
    planned = date(2026, 10, 2)
    serialized = {
        "date": planned.isoformat(), "day": "Friday", "phase": "authoritative", "fixed": True,
        "mode": "erg", "session_id": "athlete-fixed", "title": "Athlete choice", "band": "UT2",
        "total_cardio_minutes": 44, "rowing_minutes": 44, "quality_minutes": 0,
        "structure": "Existing authoritative prescription.",
    }
    candidate = generate_plan_sequence_v3_candidate(
        profile, CONFIG, bands, power, planning_date=date(2026, 10, 1), needs=(),
        future_placements=(WindowPlacement("fixed", planned, "dedicated_ut2", "fixed", 44, False, (), True),),
        locked_sessions=(serialized,),
    )

    assert candidate.success
    assert candidate.plan["sessions"].count(serialized) == 1
    assert not candidate.materialization_diagnostics


def test_end_to_end_repeated_race_candidate_is_deterministic_and_respects_recovery():
    profile, bands, power = _inputs()
    profile["season"] = {"start_date": "2026-09-01", "end_date": "2026-10-05", "current_weekly_endurance_minutes": 180, "target_peak_weekly_endurance_minutes": 220}
    profile["races"] = [
        {"event_name": "Saturday race", "start_date": "2026-09-12", "end_date": "2026-09-12", "priority": "B", "race_type": "head_5k"},
        {"event_name": "Next Sunday race", "start_date": "2026-09-20", "end_date": "2026-09-20", "priority": "B", "race_type": "head_5k"},
    ]
    profile["recurring_activities"] = [
        {"activity_id": "private", "activity_type": "private_coaching", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["wednesday"]},
        {"activity_id": "coach", "activity_type": "coached_row", "sessions_per_week": 1, "scheduling_status": "fixed", "fixed_days": ["thursday"]},
    ]
    needs = (
        _need("strength", TrainingRole.STRENGTH, date(2026, 9, 13), date(2026, 9, 18), minutes=0, priority="strength_maintenance"),
        _need("ut2", TrainingRole.DEDICATED_UT2, date(2026, 9, 13), date(2026, 9, 18), minutes=60),
        _need("race-prep", TrainingRole.RACE_PREPARATION, date(2026, 9, 15), date(2026, 9, 18), minutes=60, priority="race_readiness"),
    )
    kwargs = dict(planning_date=START, needs=needs, placement_config=PlacementSearchConfig(horizon_days=21, beam_width=48))
    first = generate_plan_sequence_v3_candidate(profile, CONFIG, bands, power, **kwargs)
    second = generate_plan_sequence_v3_candidate(profile, CONFIG, bands, power, **kwargs)

    assert first.success and first == second
    sunday = date(2026, 9, 13)
    assert not any(item.date == sunday and item.role in {TrainingRole.STRENGTH, TrainingRole.RACE_PREPARATION} for item in first.placements)
    assert {date(2026, 9, 12), date(2026, 9, 20)} <= {item.date for item in first.placements if item.role is TrainingRole.RACE}
    assert {date(2026, 9, 9), date(2026, 9, 10)} <= {item.date for item in first.placements if item.authority_status is PlacementAuthorityStatus.AUTHORITATIVE}
    assert len({item.placement_id for item in first.placements}) == len(first.placements)
    assert all(session["total_cardio_minutes"] <= next(item.reservation_minutes for item in first.placements if item.placement_id == request["placement_id"]) for request in first.plan["v2_diagnostics"]["replacement_placements"] if request["role"] not in {"race", "course_practice", "strength", "coached_training", "private_coaching"} for session in first.plan["sessions"] if session["date"] == request["date"])
