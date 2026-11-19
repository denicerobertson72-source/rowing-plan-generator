"""Regression coverage for V3's explicit-season continuous target horizon."""
from __future__ import annotations

import json
from datetime import date

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.replacement_plan_v3 import generate_plan_sequence_v3_candidate


CONFIG = json.load(open("config/defaults.json"))


def _production_shaped_profile() -> dict:
    return {
        "athlete": {
            "experience_level": "intermediate",
            "training_environment": "erg",
            "current_approx_weekly_rowing_minutes": 240,
            "current_weekly_training_minutes": 350,
            "current_rowing_sessions_per_week": 4,
        },
        "season": {
            "start_date": "2026-11-16",
            "end_date": "2026-12-31",
            "season_name": "Continuous winter development",
        },
        "training_context": {"current_strength_sessions_per_week": 3},
        "weekly_availability": [
            {
                "weekday": weekday,
                "available": True,
                "max_training_minutes": 90 if weekday in {"saturday", "sunday"} else 70,
                "rowing_modes": ["erg"],
            }
            for weekday in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        ],
        "races": [],
    }


def test_explicit_season_generates_continuous_overlapping_horizons_through_terminal_tail():
    profile = _production_shaped_profile()
    candidate = generate_plan_sequence_v3_candidate(
        profile,
        CONFIG,
        build_intensity_profile(profile, CONFIG),
        build_power_profile(profile, CONFIG),
        planning_date=date(2026, 11, 10),
    )

    assert candidate.success
    plan = candidate.plan
    assert plan is not None
    assert plan["calendar_days"][0]["date"] == "2026-11-16"
    assert plan["calendar_days"][-1]["date"] == "2026-12-31"
    assert len(plan["calendar_days"]) == 46

    diagnostics = plan["v2_diagnostics"]
    assert diagnostics["active_sequence_start"] == "2026-11-16"
    assert diagnostics["active_sequence_end"] == "2026-12-31"
    assert diagnostics["calendar_horizon_days"] == 46

    targets = diagnostics["training_load_targets"]
    starts = {(item["horizon_start"], item["horizon_days"]) for item in targets}
    for anchor in ("2026-11-16", "2026-11-23", "2026-11-30", "2026-12-07", "2026-12-14"):
        assert (anchor, 7) in starts
        assert (anchor, 14) in starts
    # The last nominal 14-day anchor is clipped to the eleven real dates
    # remaining in the season; its stable id retains the nominal window type.
    assert any(item["horizon_start"] == "2026-12-21" and item["horizon_days"] == 11 and item["id"].endswith(":14d") for item in targets)
    # The final target is proportionate to the real four-day terminal tail,
    # rather than demanding a full additional seven-day dose by Dec 31.
    terminal = next(item for item in targets if item["horizon_start"] == "2026-12-28" and item["horizon_days"] == 4)
    first_week = next(item for item in targets if item["horizon_start"] == "2026-11-16" and item["horizon_days"] == 7)
    assert terminal["target_rowing_minutes"] < first_week["target_rowing_minutes"]
    assert terminal["minimum_rowing_minutes"] < first_week["minimum_rowing_minutes"]

    placements = diagnostics["replacement_placements"]
    assert len({item["placement_id"] for item in placements}) == len(placements)
    # A single selected session in the overlap is reconciled by the two target
    # windows that contain its date; it is not cloned into a second session.
    assert any("2026-11-23" <= item["date"] <= "2026-11-29" for item in placements)
    assert any(item["horizon_start"] == "2026-11-16" and item["horizon_end"] == "2026-11-29" for item in targets)
    assert any(item["horizon_start"] == "2026-11-23" and item["horizon_end"] == "2026-11-29" for item in targets)

    sessions = plan["sessions"]
    assert not any(item["mode"] in {"walk", "bike", "cross_training", "other_aerobic"} for item in sessions)
    # Demand continues after the former 21-day cutoff (Dec 6), including
    # strength and rowing through the late-season terminal range.
    assert any(item["date"] > "2026-12-06" and item["mode"] == "strength" for item in sessions)
    assert any(item["date"] > "2026-12-06" and item["mode"] == "erg" for item in sessions)
    for start, end in (
        ("2026-11-16", "2026-11-22"), ("2026-11-23", "2026-11-29"),
        ("2026-11-30", "2026-12-06"), ("2026-12-07", "2026-12-13"),
        ("2026-12-14", "2026-12-20"), ("2026-12-21", "2026-12-27"),
        ("2026-12-28", "2026-12-31"),
    ):
        assert any(start <= item["date"] <= end for item in sessions)
