from datetime import date

from rowing_plan.physiological_state import reduce_physiological_state
from rowing_plan.planning_domain import AuthorityType, TrainingRole
from rowing_plan.planning_normalization import normalize_planning_inputs
from rowing_plan.training_load_targets import derive_training_distribution_targets, derive_training_load_targets
from rowing_plan.training_needs import generate_training_need_plan


DAY = date(2026, 10, 29)


def _profile(coach_status="fixed"):
    return {
        "athlete": {"current_approx_weekly_rowing_minutes": 180},
        "season": {"start_date": "2026-10-01", "end_date": "2026-11-20"},
        "races": [{"event_name": "double", "race_dates": ["2026-11-07", "2026-11-08"], "start_date": "2026-11-07", "end_date": "2026-11-08", "priority": "A"}],
        "recurring_activities": [
            {"activity_id": "private", "activity_type": "private_coaching", "scheduling_status": "fixed", "fixed_days": ["wednesday"]},
            {"activity_id": "coach", "activity_type": "coached_row", "scheduling_status": coach_status, "fixed_days": ["thursday"] if coach_status == "fixed" else [], "allowed_days": ["tuesday", "thursday"] if coach_status != "fixed" else []},
            {"activity_id": "rest", "activity_type": "rest", "scheduling_status": "fixed", "fixed_days": ["sunday"]},
        ],
    }


def test_fixed_coach_private_and_rest_have_distinct_authority_semantics():
    context = normalize_planning_inputs(_profile(), planning_date=DAY)
    values = {(item.date, item.type) for item in context.authoritative_commitments}
    assert (date(2026, 11, 4), AuthorityType.PRIVATE_COACHING) in values
    assert (date(2026, 10, 29), AuthorityType.FIXED_COACHING) in values
    assert (date(2026, 11, 1), AuthorityType.FIXED_REST) in values


def test_flexible_coaching_is_not_authoritative():
    context = normalize_planning_inputs(_profile("flexible"), planning_date=DAY)
    assert not any(item.type is AuthorityType.FIXED_COACHING for item in context.authoritative_commitments)


def test_distribution_targets_are_dose_derived_and_weekday_neutral():
    first_context = normalize_planning_inputs(_profile("flexible"), planning_date=DAY)
    first_state = reduce_physiological_state(first_context, profile=_profile("flexible"))
    first = derive_training_distribution_targets(derive_training_load_targets(first_context, first_state, profile=_profile("flexible")))
    shifted_day = date(2026, 10, 30)
    second_context = normalize_planning_inputs(_profile("flexible"), planning_date=shifted_day)
    second_state = reduce_physiological_state(second_context, profile=_profile("flexible"))
    second = derive_training_distribution_targets(derive_training_load_targets(second_context, second_state, profile=_profile("flexible")))
    assert [(x.horizon_days, x.target_rowing_exposures) for x in first] == [(x.horizon_days, x.target_rowing_exposures) for x in second]


def test_taper_strength_is_explicitly_decided_not_unconditionally_erased():
    profile = _profile("flexible")
    context = normalize_planning_inputs(profile, planning_date=DAY)
    needs = generate_training_need_plan(context, profile=profile).needs
    strength = [item for item in needs if item.role is TrainingRole.STRENGTH]
    assert len(strength) == 1
    assert strength[0].state == "reduced"
    assert strength[0].progression_relation == "taper_strength_continuity"
