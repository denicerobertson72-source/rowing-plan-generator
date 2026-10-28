"""Read-only catalog capability lookup used before replacement-plan placement.

This is intentionally a thin adapter over the existing selector: it exposes
minimum *whole-session* envelopes without copying catalog durations or picking
a concrete workout.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Mapping

from .periodization import build_season_phases
from .session_selection import ROLE_BAND, candidates


_ROLE_TO_SELECTOR = {
    "dedicated_ut2": "AEROBIC_BASE",
    "long_aerobic": "LONG_AEROBIC",
    "ut1_aerobic_strength": "AEROBIC_STRENGTH",
    "recovery_aerobic": "RECOVERY",
    "technical_reentry": "TECHNIQUE_EASY",
    "threshold_at": "THRESHOLD",
    "race_specific_tr": "RACE_PACE",
    "anaerobic_an": "ANAEROBIC_CAPACITY",
    "power_pp": "SPRINT_POWER",
    "race_preparation": "RACE_PACE",
    "taper_maintenance": "RACE_PACE",
}


def phase_type_for_date(profile: Mapping[str, object], day: date) -> str:
    season = profile.get("season") if isinstance(profile, Mapping) else None
    if not isinstance(season, Mapping) or not season.get("start_date") or not season.get("end_date"):
        return "general_preparation"
    for span in build_season_phases(dict(profile)):
        if span["start_date"] <= day.isoformat() <= span["end_date"]:
            return str(span["phase_type"])
    return "general_preparation"


def mode_for_date(profile: Mapping[str, object], day: date) -> str:
    for item in profile.get("weekly_availability", ()):
        if isinstance(item, Mapping) and str(item.get("weekday", "")).lower() == day.strftime("%A").lower():
            return next((value for value in item.get("rowing_modes", ()) if value in {"erg", "on_water"}), "erg")
    return "erg"


def selector_role_for_replacement_role(role: str, phase_type: str) -> str | None:
    if role == "quality":
        return "THRESHOLD" if phase_type == "threshold_development" else "RACE_PACE" if phase_type in {"race_specific_preparation", "taper"} else None
    return _ROLE_TO_SELECTOR.get(role)


@lru_cache(maxsize=None)
def _minimum_selector_envelope(selector_role: str, experience: str, phase: str, mode: str) -> int | None:
    """Catalog lookup cache; it is independent of athlete-private data."""
    for minutes in range(1, 241):
        if candidates(
            role=selector_role, band=ROLE_BAND[selector_role], experience=experience,
            phase=phase, race_type="general", mode=mode, minutes=minutes,
        ):
            return minutes
    return None


def minimum_materializable_reservation(*, role: str, profile: Mapping[str, object], day: date) -> tuple[int | None, str | None]:
    """Return the least selector-eligible whole-session envelope for ``role``.

    ``None`` means the existing catalog has no valid materialization path for
    this role/context.  Callers must leave the need unplaced rather than make
    materialization move it or invent a workout.
    """
    phase = phase_type_for_date(profile, day)
    selector_role = selector_role_for_replacement_role(role, phase)
    if selector_role is None:
        return 0, None
    experience = str(profile.get("athlete", {}).get("experience_level", "intermediate"))
    mode = mode_for_date(profile, day)
    return _minimum_selector_envelope(selector_role, experience, phase, mode), selector_role


def materialization_candidate_count(*, role: str, profile: Mapping[str, object], day: date,
                                    reservation_minutes: int) -> int:
    """Aggregate selector eligibility for safe failure diagnostics only."""
    phase = phase_type_for_date(profile, day)
    selector_role = selector_role_for_replacement_role(role, phase)
    if selector_role is None:
        return 0
    experience = str(profile.get("athlete", {}).get("experience_level", "intermediate"))
    return len(candidates(
        role=selector_role, band=ROLE_BAND[selector_role], experience=experience,
        phase=phase, race_type="general", mode=mode_for_date(profile, day),
        minutes=reservation_minutes,
    ))
