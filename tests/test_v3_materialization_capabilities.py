"""V3 placement must reserve catalog envelopes before materialization."""
from __future__ import annotations

import json
from datetime import date

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.materialization_capabilities import minimum_materializable_reservation
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate
from services.api.tests.disposable_browser_fixture import synthetic_profile


CONFIG = json.load(open("config/defaults.json"))


def _post_race_profile():
    profile = synthetic_profile()
    profile["season"] = {**profile["season"], "start_date": "2026-10-01", "end_date": "2026-11-20"}
    profile["races"] = [{
        "event_name": "Completed race", "start_date": "2026-10-27", "end_date": "2026-10-27",
        "race_dates": ["2026-10-27"], "priority": "B", "race_type": "head_5k",
    }]
    return profile


def test_post_race_recovery_uses_recovery_catalog_without_changing_credit():
    profile = _post_race_profile()
    candidate = generate_plan_sequence_v3_candidate(
        profile, CONFIG, build_intensity_profile(profile, CONFIG), build_power_profile(profile, CONFIG),
        planning_date=date(2026, 10, 28),
    )
    recovery = next(item for item in candidate.placements if item.role.value == "recovery_aerobic")
    envelope, selector_role = minimum_materializable_reservation(
        role="recovery_aerobic", profile=profile, day=recovery.date,
    )
    assert candidate.success
    assert selector_role == "RECOVERY" and envelope == 20
    assert recovery.target_credit.minutes == 20
    assert recovery.reservation_minutes == 20


def test_catalog_capability_reports_no_fictional_path_for_unknown_role():
    profile = _post_race_profile()
    envelope, selector_role = minimum_materializable_reservation(
        role="not_a_catalog_role", profile=profile, day=date(2026, 10, 28),
    )
    assert envelope == 0 and selector_role is None
