from __future__ import annotations

from datetime import date, timedelta

from rowing_plan.continuous_placement import PlacementSearchConfig, place_continuous_training_needs
from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import (
    AuthoritySource,
    AuthorityType,
    AuthoritativeCommitment,
    ConflictBehavior,
    PhysiologicalCredit,
    Provenance,
    ProvenanceSource,
    TrainingNeed,
    TrainingRole,
)
from rowing_plan.planning_normalization import AUTHORITY_PRECEDENCE, NormalizationDiagnostics, NormalizedPlanningContext


AS_OF = date(2026, 10, 11)  # Sunday: no calendar-week meaning is inferred.


def _context(*, as_of=AS_OF, commitments=(), races=()):
    return NormalizedPlanningContext(
        planning_date=as_of,
        historical_facts=(),
        authoritative_commitments=tuple(commitments),
        provisional_prior_placement_ids=(),
        race_context=tuple((day.isoformat(), priority, "Race", None) for day, priority in races),
        authority_precedence=AUTHORITY_PRECEDENCE,
        diagnostics=NormalizationDiagnostics(future_authoritative_commitments=len(commitments)),
    )


def _profile(*, as_of=AS_OF, days=14, minutes=120, multiple=False, restrictions=None, preferred=()):
    restrictions = restrictions or {}
    preferred = set(preferred)
    return {
        "season": {"start_date": "2026-10-01", "end_date": "2026-12-31"},
        "date_availability": [
            {
                "date": (as_of + timedelta(days=index)).isoformat(),
                "available": True,
                "max_training_minutes": minutes,
                "multiple_sessions_allowed": multiple,
                "prohibited_role_families": restrictions.get(as_of + timedelta(days=index), ()),
                "preferred": as_of + timedelta(days=index) in preferred,
            }
            for index in range(days)
        ],
    }


def _state(context, profile=None):
    return reduce_physiological_state(context, profile=profile or {"season": {"start_date": "2026-10-01", "end_date": "2026-12-31"}})


def _need(identifier, role, *, start=AS_OF, end=None, minutes=35, priority="test", relation=None):
    return TrainingNeed(
        id=identifier,
        role=role,
        target_credit=PhysiologicalCredit(role, 1, minutes, True),
        earliest_date=start,
        latest_date=end or start + timedelta(days=10),
        recovery_cost="hard" if role in {TrainingRole.STRENGTH, TrainingRole.QUALITY, TrainingRole.THRESHOLD_AT, TrainingRole.RACE_SPECIFIC_TR} else "low",
        priority=priority,
        source_policy="test_policy",
        progression_relation=relation,
    )


def _commitment(identifier, kind, day, minutes=30):
    return AuthoritativeCommitment(
        identifier, kind, day, None, minutes,
        AuthoritySource.RACE_PROFILE if kind is AuthorityType.RACE else AuthoritySource.RECURRING_ACTIVITY,
        ConflictBehavior.PRESERVE_AND_REPORT,
        Provenance(identifier, ProvenanceSource.RACE_PROFILE if kind is AuthorityType.RACE else ProvenanceSource.RECURRING_ACTIVITY),
    )


def _result(*, as_of=AS_OF, needs=(), commitments=(), races=(), profile=None, prior=None, config=None):
    context = _context(as_of=as_of, commitments=commitments, races=races)
    profile = profile or _profile(as_of=as_of)
    return place_continuous_training_needs(
        context, _state(context, profile), needs, profile=profile,
        prior_provisional_dates=prior, config=config or PlacementSearchConfig(horizon_days=14, beam_width=32),
    )


def _planned(result, need_id):
    return next(item for item in result.placements if item.training_need_id == need_id)


def test_authority_is_seeded_preserved_and_consumes_capacity_without_moving():
    wednesday = AS_OF + timedelta(days=3)
    private = _commitment("private-wed", AuthorityType.PRIVATE_COACHING, wednesday, 50)
    result = _result(
        commitments=(private,),
        profile=_profile(minutes=60, multiple=False),
        needs=(_need("ut2", TrainingRole.DEDICATED_UT2, start=wednesday, end=wednesday, minutes=35),),
    )

    authority = next(item for item in result.placements if item.placement_id == "authority:private-wed")
    assert authority.date == wednesday
    assert authority.authority_status.value == "authoritative"
    assert not any(item.training_need_id == "ut2" for item in result.placements)
    assert result.available_dates[3].remaining_minutes == 10
    assert result.diagnostics.unplaced_needs[0].primary_blocking_reason == "insufficient_capacity"


def test_weekday_and_week_boundary_have_no_intrinsic_identity():
    sunday_need = _need("ut2", TrainingRole.DEDICATED_UT2, start=AS_OF, end=AS_OF + timedelta(days=2))
    sunday = _result(needs=(sunday_need,))
    monday_start = AS_OF + timedelta(days=1)
    monday_need = _need("ut2", TrainingRole.DEDICATED_UT2, start=monday_start, end=monday_start + timedelta(days=2))
    monday = _result(as_of=monday_start, needs=(monday_need,))

    assert _planned(sunday, "ut2").date == AS_OF
    assert _planned(monday, "ut2").date == monday_start
    assert sunday.diagnostics.objective_order[-1] == "deterministic_date_need_tie_break"


def test_projected_quality_changes_later_ut2_date_without_weekly_template():
    quality = _need("quality", TrainingRole.QUALITY, start=AS_OF, end=AS_OF + timedelta(days=3), priority="quality_development")
    ut2 = _need("ut2", TrainingRole.DEDICATED_UT2, start=AS_OF, end=AS_OF + timedelta(days=3), priority="protected_aerobic_base")
    profile = _profile(multiple=False)
    alone = _result(needs=(ut2,), profile=profile)
    together = _result(needs=(quality, ut2), profile=profile)

    assert _planned(alone, "ut2").date == AS_OF
    assert _planned(together, "quality").date == AS_OF
    assert _planned(together, "ut2").date == AS_OF + timedelta(days=1)


def test_race_recovery_changes_strength_date_but_leaves_empty_day_valid():
    race_day = AS_OF + timedelta(days=6)  # Saturday
    race = _commitment("race-sat", AuthorityType.RACE, race_day, 30)
    strength = _need("strength", TrainingRole.STRENGTH, start=race_day + timedelta(days=1), end=race_day + timedelta(days=3), priority="strength_maintenance")
    without_race = _result(needs=(strength,), profile=_profile(days=14))
    with_race = _result(needs=(strength,), commitments=(race,), races=((race_day, "B"),), profile=_profile(days=14))

    assert _planned(without_race, "strength").date == race_day + timedelta(days=1)
    assert _planned(with_race, "strength").date > race_day + timedelta(days=1)
    assert all(not (item.date == race_day + timedelta(days=1) and item.training_need_id == "strength") for item in with_race.placements)


def test_plan_stability_is_low_priority_and_never_overrides_recovery():
    target = AS_OF + timedelta(days=3)
    need = _need("strength", TrainingRole.STRENGTH, start=AS_OF, end=AS_OF + timedelta(days=5), priority="strength_maintenance")
    stable = _result(needs=(need,), prior={"strength": target})
    assert _planned(stable, "strength").date == target
    assert _planned(stable, "strength").stability_effect == "prior_provisional_date_preserved"

    race = _commitment("race", AuthorityType.RACE, target - timedelta(days=1), 30)
    constrained = _result(needs=(need,), commitments=(race,), races=((target - timedelta(days=1), "B"),), prior={"strength": target})
    assert _planned(constrained, "strength").date != target


def test_long_aerobic_retains_one_physical_placement_with_ut2_relationship_traceability():
    long = _need("long", TrainingRole.LONG_AEROBIC, minutes=75, relation="may_contribute:dedicated_ut2")
    ut2 = _need("ut2", TrainingRole.DEDICATED_UT2, minutes=45)
    result = _result(needs=(long, ut2), profile=_profile(multiple=False, minutes=90))
    placement = _planned(result, "long")

    assert placement.served_need_ids == ("long", "ut2")
    assert len([item for item in result.placements if item.training_need_id == "long"]) == 1
    assert placement.target_credit.minutes == 75


def test_repeated_race_fixture_preserves_fixed_commitments_and_adapts_flexible_work():
    saturday = AS_OF + timedelta(days=6)
    following_sunday = saturday + timedelta(days=8)
    commitments = (
        _commitment("private-wed", AuthorityType.PRIVATE_COACHING, AS_OF + timedelta(days=3), 45),
        _commitment("coach-thu", AuthorityType.FIXED_COACHING, AS_OF + timedelta(days=4), 45),
        _commitment("race-one", AuthorityType.RACE, saturday, 30),
        _commitment("race-two", AuthorityType.RACE, following_sunday, 30),
    )
    strength = _need("strength", TrainingRole.STRENGTH, start=saturday + timedelta(days=1), end=following_sunday + timedelta(days=2), priority="strength_maintenance")
    quality = _need("quality", TrainingRole.RACE_SPECIFIC_TR, start=saturday + timedelta(days=1), end=following_sunday + timedelta(days=2), priority="race_specific")
    result = _result(
        commitments=commitments, races=((saturday, "B"), (following_sunday, "B")),
        needs=(strength, quality), profile=_profile(days=21), config=PlacementSearchConfig(horizon_days=21, beam_width=48),
    )

    seeded = {(item.training_need_id, item.date) for item in result.placements if item.authority_status.value == "authoritative"}
    assert ("authority:private-wed", AS_OF + timedelta(days=3)) in seeded
    assert ("authority:coach-thu", AS_OF + timedelta(days=4)) in seeded
    assert ("authority:race-one", saturday) in seeded
    assert ("authority:race-two", following_sunday) in seeded
    sunday_after_first = saturday + timedelta(days=1)
    assert all(not (item.date == sunday_after_first and item.training_need_id in {"strength", "quality"}) for item in result.placements)


def test_unavailable_need_is_typed_unplaced_and_empty_dates_are_not_rest_sessions():
    day = AS_OF
    need = _need("ut2", TrainingRole.DEDICATED_UT2, start=day, end=day, minutes=45)
    result = _result(needs=(need,), profile={"date_availability": [{"date": day.isoformat(), "available": False}]})

    assert result.placements == ()
    diagnostic = result.diagnostics.unplaced_needs[0]
    assert diagnostic.training_need_id == "ut2"
    assert diagnostic.status == "blocked_by_capacity"
    assert all(item.training_need_id != "rest" for item in result.placements)


def test_beam_search_is_deterministic_and_explicitly_configured_not_policy():
    needs = (
        _need("ut2", TrainingRole.DEDICATED_UT2, end=AS_OF + timedelta(days=2)),
        _need("strength", TrainingRole.STRENGTH, end=AS_OF + timedelta(days=2), priority="strength_maintenance"),
    )
    profile = _profile(multiple=False)
    first = _result(needs=needs, profile=profile, config=PlacementSearchConfig(horizon_days=7, beam_width=16))
    second = _result(needs=needs, profile=profile, config=PlacementSearchConfig(horizon_days=7, beam_width=16))

    assert first == second
    assert first.diagnostics.search_width == 16
    assert "beam" not in "|".join(rule for item in first.placements for rule in item.policy_rule_ids)
