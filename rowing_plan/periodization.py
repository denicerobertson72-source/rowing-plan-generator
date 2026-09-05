"""Race-priority phases and weekly training intent, computed deterministically.

Legacy date-indexed phase labels remain available for existing scheduler and UI
callers. The v0.7 records are persisted with a PlanVersion and constrain
ordinary-row frequency, but do not select or alter workout templates.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import NAMESPACE_URL, uuid5

from .models import SeasonPhase, WeeklyTrainingIntent

PLANNING_MODEL_VERSION = "phase-weekly-intent-0.7.0"
TAPER = {"A": 10, "B": 5, "C": 2}
RECOVERY = {"A": 3, "B": 2, "C": 1}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def parse(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _stable_id(prefix: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, f"rowing-plan/{prefix}/" + "/".join(map(str, parts))))


def _race_id(race: dict) -> str:
    return str(race.get("race_id") or _stable_id("race", race.get("event_name", "race"), race.get("start_date"), race.get("end_date")))


def _ordered_races(profile: dict) -> list[dict]:
    return sorted(profile.get("races", []), key=lambda item: (item.get("start_date", ""), item.get("end_date", ""), item.get("event_name", "")))


def phase_for_day(day: date, races: list[dict]) -> tuple[str, dict | None]:
    """Legacy labels used by the current scheduler; do not repurpose them."""
    for race in races:
        start, end = parse(race["start_date"]), parse(race["end_date"])
        if start <= day <= end:
            return "race", race
        if start - timedelta(days=TAPER[race["priority"]]) <= day < start:
            return "taper_sharpen", race
        if end < day <= end + timedelta(days=RECOVERY[race["priority"]]):
            return "race_recovery", race
    future = [race for race in races if parse(race["start_date"]) > day]
    return ("race_build" if future and (parse(future[0]["start_date"]) - day).days < 28 else "specific_preparation"), (future[0] if future else None)


def build_phases(profile: dict) -> list[dict]:
    """Legacy daily phases retained for calendar and scheduler callers."""
    start, end = parse(profile["season"]["start_date"]), parse(profile["season"]["end_date"])
    result, day, races = [], start, _ordered_races(profile)
    while day <= end:
        phase, race = phase_for_day(day, races)
        result.append({"date": day.isoformat(), "phase": phase, "race_event": race.get("event_name") if race else None})
        day += timedelta(days=1)
    return result


PHASE_DETAILS = {
    "foundation_orientation": (["technical_consistency", "training_consistency"], ["comfortable_aerobic_duration"], ["UT3", "UT2"], [], "increase", 1, "none", "foundation", ["S010"]),
    "aerobic_development": (["aerobic_capacity", "technical_consistency"], ["durable_training_routine"], ["UT2", "UT1"], ["AT", "PP"], "increase", 2, "low", "maintain_or_build", ["S010", "S017"]),
    "general_preparation": (["aerobic_capacity", "aerobic_strength"], ["technical_consistency"], ["UT2", "UT1"], ["AT", "PP"], "increase", 3, "low", "maintain_or_build", ["S010", "S017"]),
    "threshold_development": (["threshold_capacity", "aerobic_strength"], ["aerobic_capacity"], ["UT1", "AT"], ["UT2", "PP"], "hold", 4, "moderate", "maintain", ["S010", "S014", "S015", "S020"]),
    "race_specific_preparation": (["race_specific_capacity", "race_rate_familiarity"], ["threshold_maintenance"], ["TR", "UT1"], ["UT2", "AT", "PP"], "hold", 5, "high", "maintain_reduce_fatigue", ["S010", "S014", "S015", "S017"]),
    "taper": (["freshness", "race_rate_familiarity"], ["technical_consistency"], ["TR", "UT3"], ["UT2"], "decrease", 5, "targeted", "reduce_fatigue", ["S009", "S010", "S017"]),
    "race": (["race_execution"], ["recovery_preparation"], ["RACE"], [], "decrease", 5, "race", "defer", ["S010"]),
    "post_race_recovery": (["recovery", "movement_quality"], ["training_continuity"], ["UT3"], [], "decrease", 1, "none", "recover", ["S009", "S019"]),
    "transition": (["recovery", "movement_quality"], ["technical_consistency"], ["UT3", "UT2"], [], "decrease", 1, "none", "maintain_or_recover", ["S010", "S019"]),
}


def _season_phase_for_day(day: date, profile: dict, races: list[dict]) -> tuple[str, dict | None, str]:
    for race in races:
        start, end, priority = parse(race["start_date"]), parse(race["end_date"]), race.get("priority", "B")
        if start <= day <= end:
            return "race", race, "The date falls within this race event."
        if end < day <= end + timedelta(days=RECOVERY[priority]):
            return "post_race_recovery", race, f"Recovery follows the {priority}-priority race."
        if start - timedelta(days=TAPER[priority]) <= day < start:
            return "taper", race, f"The {priority}-priority race is within its configurable taper window."
    future = next((race for race in races if parse(race["start_date"]) > day), None)
    if future:
        days_to_race = (parse(future["start_date"]) - day).days
        if days_to_race <= 21:
            return "race_specific_preparation", future, "The next race is within 21 days, before its taper window."
        if days_to_race <= 42:
            return "threshold_development", future, "The next race is 22–42 days away; sustained quality can be emphasized."
        return "aerobic_development", future, "The next race is more than six weeks away; aerobic development is the current priority."
    experience = profile.get("athlete", {}).get("experience_level", "intermediate")
    if experience in {"new", "novice", "developing"} and day < parse(profile["season"]["start_date"]) + timedelta(days=14):
        return "foundation_orientation", None, "Early-season orientation records a technique and consistency emphasis."
    return "general_preparation", None, "No upcoming race is set; general preparation maintains a durable training base."


def build_season_phases(profile: dict) -> list[dict]:
    """Build contiguous, versioned SeasonPhase records from season and races."""
    start, end, races = parse(profile["season"]["start_date"]), parse(profile["season"]["end_date"]), _ordered_races(profile)
    spans, current_start = [], start
    current_type, current_race, current_reason = _season_phase_for_day(start, profile, races)
    day = start + timedelta(days=1)
    while day <= end:
        phase_type, race, reason = _season_phase_for_day(day, profile, races)
        if (phase_type, _race_id(race) if race else None) != (current_type, _race_id(current_race) if current_race else None):
            spans.append((current_start, day - timedelta(days=1), current_type, current_race, current_reason))
            current_start, current_type, current_race, current_reason = day, phase_type, race, reason
        day += timedelta(days=1)
    spans.append((current_start, end, current_type, current_race, current_reason))
    result = []
    for phase_start, phase_end, phase_type, race, reason in spans:
        primary, secondary, priority, maintain, volume, specificity, rate, strength, sources = PHASE_DETAILS[phase_type]
        target_race_id = _race_id(race) if race else None
        result.append(SeasonPhase(
            phase_id=_stable_id("phase", phase_type, phase_start.isoformat(), phase_end.isoformat(), target_race_id or "none"),
            phase_type=phase_type, start_date=phase_start.isoformat(), end_date=phase_end.isoformat(),
            primary_objectives=primary, secondary_objectives=secondary, priority_bands=priority, maintain_bands=maintain,
            volume_direction=volume, specificity_level=specificity, race_rate_exposure=rate, strength_emphasis=strength,
            target_race_id=target_race_id, source_ids=sources, reason=reason, algorithm_version=PLANNING_MODEL_VERSION,
        ).to_dict())
    return result


ROLE_MAP = {
    "foundation_orientation": (["TECHNIQUE_EASY", "AEROBIC_BASE"], ["RECOVERY"]), "aerobic_development": (["AEROBIC_BASE", "LONG_AEROBIC"], ["TECHNIQUE_EASY", "AEROBIC_STRENGTH"]),
    "general_preparation": (["AEROBIC_BASE", "AEROBIC_STRENGTH"], ["LONG_AEROBIC", "TECHNIQUE_EASY"]), "threshold_development": (["THRESHOLD", "AEROBIC_BASE"], ["LONG_AEROBIC", "TECHNIQUE_EASY"]),
    "race_specific_preparation": (["RACE_PACE", "THRESHOLD"], ["AEROBIC_BASE", "TECHNIQUE_EASY"]), "taper": (["RACE_PACE", "TECHNIQUE_EASY"], ["AEROBIC_BASE"]),
    "race": (["RACE"], ["RECOVERY"]), "post_race_recovery": (["RECOVERY"], ["TECHNIQUE_EASY"]), "transition": (["RECOVERY", "TECHNIQUE_EASY"], ["AEROBIC_BASE"]),
}
INTENSITY_SPLITS = {
    "foundation_orientation": (0.90, 0.10, 0.00), "aerobic_development": (0.82, 0.14, 0.04), "general_preparation": (0.78, 0.17, 0.05),
    "threshold_development": (0.70, 0.23, 0.07), "race_specific_preparation": (0.66, 0.21, 0.13), "taper": (0.72, 0.14, 0.14),
    "race": (0.30, 0.10, 0.60), "post_race_recovery": (1.00, 0.00, 0.00), "transition": (0.90, 0.10, 0.00),
}


def _phase_mix_for_week(week_start: date, start: date, end: date, season_phases: list[dict]) -> list[tuple[int, dict]]:
    """Return each phase's in-season day count for this week."""
    week_start, week_end = max(week_start, start), min(week_start + timedelta(days=6), end)
    result = []
    for phase in season_phases:
        overlap = max(0, (min(week_end, parse(phase["end_date"])) - max(week_start, parse(phase["start_date"]))).days + 1)
        if overlap:
            result.append((overlap, phase))
    return result


def _primary_phase(phase_mix: list[tuple[int, dict]]) -> dict:
    """Race/taper periods win mixed weeks; recovery must not eclipse a longer build."""
    return max(phase_mix, key=lambda item: (item[1]["phase_type"] in {"race", "taper"}, item[0], item[1]["specificity_level"]))[1]


def _weekly_volume_baseline(profile: dict, week_start: date) -> int:
    season = profile["season"]
    start, end = parse(season["start_date"]), parse(season["end_date"])
    current = int(season.get("current_weekly_endurance_minutes") or 0)
    peak = int(season.get("target_peak_weekly_endurance_minutes") or current)
    total_weeks = max(1, ((end - start).days // 7) + 1)
    first_monday = start - timedelta(days=start.weekday())
    week_number = max(0, min(total_weeks - 1, (week_start - first_monday).days // 7))
    return round(current + (peak - current) * (week_number / max(1, total_weeks - 1)))


def _commitment_state(profile: dict, commitments: dict, day: date, modern_schedule: bool) -> tuple[bool, bool, bool, bool, int, int, int]:
    items = commitments.get(day.isoformat(), []) if modern_schedule else []
    rest = any(item.get("activity_type") == "rest" for item in items)
    strength = next((item for item in items if item.get("activity_type") == "strength"), None)
    strength_blocks = bool(strength and not strength.get("same_day_rules", {}).get("rowing_allowed", True))
    private_count = sum(item.get("activity_type") == "private_coaching" for item in items)
    coached_count = sum(item.get("activity_type") == "coached_row" for item in items)
    coached = bool(private_count or coached_count)
    strength_count = int(bool(strength))
    if not modern_schedule:
        availability = {item["weekday"]: item for item in profile.get("weekly_availability", [])}
        legacy = availability.get(WEEKDAYS[day.weekday()], {})
        rest = rest or bool(legacy.get("fixed_rest", False))
        unavailable = not legacy.get("available", False) and not rest
        strength_blocks = strength_blocks or bool(legacy.get("heavy_lifting") and not legacy.get("row_on_lifting_day", True))
        coached = coached or bool(legacy.get("fixed_coached_row", False))
        coached_count = int(bool(legacy.get("fixed_coached_row")))
        strength_count = int(bool(legacy.get("heavy_lifting")))
    else:
        unavailable = False
    return rest, unavailable, strength_blocks, coached, strength_count, private_count, coached_count


def _prescribed_frequency(profile: dict, week_index: int, available_slots: int, strength_sessions: int) -> int:
    """Set weekly rowing frequency from demonstrated load, not open calendar days."""
    athlete = profile.get("athlete", {})
    experience = athlete.get("experience_level", "intermediate")
    desired_frequency = athlete.get("desired_rowing_sessions_per_week")
    starting_frequency = desired_frequency if desired_frequency is not None else athlete.get("current_rowing_sessions_per_week")
    if starting_frequency is None:
        current_minutes = athlete.get("current_approx_weekly_rowing_minutes")
        defaults = {"new": 2, "novice": 2, "developing": 2, "intermediate": 3, "experienced": 4, "competitive": 5}
        starting_frequency = defaults.get(experience, 3)
        if current_minutes is not None:
            # About one established hour per weekly row is a conservative
            # inference when an athlete supplied minutes but not frequency.
            starting_frequency = min(starting_frequency, max(2, (int(current_minutes) + 59) // 60))
    # Two or more heavy-lifting commitments are real workload, not empty
    # rowing capacity.  They keep an otherwise unknown athlete at a durable
    # starting frequency; an explicit desired/current frequency still wins.
    if desired_frequency is None and athlete.get("current_rowing_sessions_per_week") is None and strength_sessions >= 2:
        starting_frequency = min(int(starting_frequency), 4)
    starting_frequency = max(0, min(int(starting_frequency), available_slots))
    consistency = athlete.get("recent_training_consistency", "building")
    if desired_frequency is not None:
        return starting_frequency
    if experience in {"new", "novice", "developing"}:
        interval, cap = (2 if consistency == "consistent" else 3), 4
    else:
        # Experienced athletes may progress faster, but only after a complete
        # demonstrated week; returning/inconsistent athletes hold longer.
        interval = 4 if consistency in {"returning", "inconsistent"} else 2 if consistency == "consistent" else 3
        cap = available_slots
    increase = min(2, week_index // interval)
    return min(available_slots, starting_frequency + increase, cap)


def _novice_volume(profile: dict, week_index: int, baseline: int) -> int:
    athlete, season = profile.get("athlete", {}), profile.get("season", {})
    starting_minutes = athlete.get("current_approx_weekly_rowing_minutes")
    if starting_minutes is None:
        # When only a continuous-row tolerance is known, do not let a generic
        # seasonal target silently prescribe a much larger first week.  Two
        # tolerance-sized exposures per demonstrated weekly row leave room for
        # warm-up/cool-down while remaining a conservative starting estimate.
        tolerance = athlete.get("longest_comfortable_continuous_row_minutes")
        if tolerance is not None:
            frequency = int(athlete.get("current_rowing_sessions_per_week") or 2)
            starting_minutes = min(int(season.get("current_weekly_endurance_minutes") or baseline), frequency * max(20, int(tolerance)))
        else:
            starting_minutes = season.get("current_weekly_endurance_minutes") or baseline
    starting_minutes = max(0, int(starting_minutes))
    consistency = athlete.get("recent_training_consistency", "building")
    interval = 2 if consistency == "consistent" else 3
    gradual_target = starting_minutes + (week_index // interval) * 10
    return min(baseline, gradual_target)


def build_weekly_training_intents(profile: dict, season_phases: list[dict], commitments: dict | None = None, modern_schedule: bool | None = None) -> list[dict]:
    """Build weekly objectives. They are deliberately not consumed by template selection yet."""
    commitments = commitments or {}
    modern_schedule = profile.get("recurring_activities") is not None if modern_schedule is None else modern_schedule
    start, end, races = parse(profile["season"]["start_date"]), parse(profile["season"]["end_date"]), _ordered_races(profile)
    intents, week_start, first_monday = [], start - timedelta(days=start.weekday()), start - timedelta(days=start.weekday())
    while week_start <= end:
        phase_mix = _phase_mix_for_week(week_start, start, end, season_phases)
        phase = _primary_phase(phase_mix)
        next_race = next((race for race in races if parse(race["end_date"]) >= week_start), None)
        rest_days = strength_days = rowing_slots = private_sessions = coached_sessions = 0
        events = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            if not start <= day <= end:
                continue
            rest, unavailable, strength_blocks, coached, strength_count, private_count, coached_count = _commitment_state(profile, commitments, day, modern_schedule)
            race = next((item for item in races if parse(item["start_date"]) <= day <= parse(item["end_date"])), None)
            rest_days += int(rest)
            strength_days += strength_count
            private_sessions += private_count
            coached_sessions += coached_count
            if race:
                events.append({"type": "race", "date": day.isoformat(), "event_name": race.get("event_name"), "priority": race.get("priority")})
            if coached:
                events.append({"type": "coached_commitment", "date": day.isoformat()})
            if not (rest or unavailable or strength_blocks or race):
                rowing_slots += 1
        week_index = (week_start - first_monday).days // 7
        experience = profile.get("athlete", {}).get("experience_level", "intermediate")
        prescribed_rows = _prescribed_frequency(profile, week_index, rowing_slots, strength_days)
        prescribed_rows = max(prescribed_rows, private_sessions + coached_sessions)
        coached_total = private_sessions + coached_sessions
        normal_capacity = max(0, rowing_slots - coached_total)
        race_or_recovery = any(component["phase_type"] in {"race", "taper", "post_race_recovery", "transition"} for _, component in phase_mix)
        independent_target = max(0, prescribed_rows - coached_total)
        if experience in {"intermediate", "experienced", "competitive"} and int(profile.get("athlete", {}).get("current_approx_weekly_rowing_minutes") or 0) >= 180 and not race_or_recovery:
            independent_target = min(normal_capacity, max(2, independent_target))
        prescribed_rows = coached_total + independent_target
        baseline = _weekly_volume_baseline(profile, week_start)
        if experience in {"new", "novice", "developing"}:
            baseline = _novice_volume(profile, week_index, baseline)
        phase_factors = {"foundation_orientation": 1.0, "post_race_recovery": 0.55, "transition": 0.65, "race": 0.35}
        weighted_factor = 0.0
        weighted_splits = [0.0, 0.0, 0.0]
        taper_factors = []
        for days, component in phase_mix:
            component_type = component["phase_type"]
            component_race = next((race for race in races if _race_id(race) == component.get("target_race_id")), None)
            taper_factor = {"A": 0.50, "B": 0.72, "C": 0.88}.get((component_race or {}).get("priority"), 0.72) if component_type == "taper" else 1.0
            component_factor = phase_factors.get(component_type, 1.0) * taper_factor
            weighted_factor += days * component_factor
            for index, share in enumerate(INTENSITY_SPLITS[component_type]): weighted_splits[index] += days * share
            if component_type == "taper": taper_factors.append(taper_factor)
        days_in_week = sum(days for days, _ in phase_mix)
        volume_factor = weighted_factor / days_in_week
        low_share, moderate_share, high_share = (value / days_in_week for value in weighted_splits)
        pattern = profile.get("season", {}).get("default_block_pattern", "custom")
        deload_every = 4 if pattern == "3_build_1_deload" else 3 if pattern == "2_build_1_deload" else None
        race_affected = any(component["phase_type"] in {"race", "taper", "post_race_recovery"} for _, component in phase_mix)
        # A calendar pattern is optional guidance, never an implicit override
        # of race/recovery planning. It must be explicitly enabled by a future
        # profile control before it can create a generic recovery week.
        block_guidance_enabled = profile.get("season", {}).get("apply_block_pattern_guidance") is True
        recovery_week = bool(block_guidance_enabled and deload_every and week_index > 0 and week_index % deload_every == deload_every - 1 and not race_affected)
        if recovery_week: volume_factor *= 0.82
        # Fixed coach-led rows are part of rowing volume. Keep the weekly
        # target feasible rather than asking ordinary sessions to disappear or
        # truncate below their archetype envelopes.
        feasible_floor = coached_total * 50 + max(0, prescribed_rows - coached_total) * 40 if coached_total else 0
        total = max(feasible_floor, round(baseline * volume_factor))
        low, moderate = round(total * low_share), round(total * moderate_share)
        high = total - low - moderate
        phase_types = {component["phase_type"] for _, component in phase_mix}
        recovery_then_build = (
            phase_mix[0][1]["phase_type"] == "post_race_recovery"
            and phase["phase_type"] != "post_race_recovery"
            and not phase_types & {"race", "taper"}
        )
        if recovery_then_build: load = "recover_then_build"
        elif "race" in phase_types: load = "race"
        elif "taper" in phase_types: load = "taper"
        elif "post_race_recovery" in phase_types or "transition" in phase_types or recovery_week: load = "recover"
        elif phase["volume_direction"] == "increase": load = "build"
        else: load = "hold"
        primary, secondary = ROLE_MAP[phase["phase_type"]]
        mix_output = [{"phase_id": component["phase_id"], "phase_type": component["phase_type"], "days": days, "volume_factor": round((phase_factors.get(component["phase_type"], 1.0) * ({"A": 0.50, "B": 0.72, "C": 0.88}.get((next((race for race in races if _race_id(race) == component.get("target_race_id")), {}) or {}).get("priority"), 0.72) if component["phase_type"] == "taper" else 1.0)), 2)} for days, component in phase_mix]
        transition_note = None
        if recovery_then_build:
            if phase["phase_type"] == "race_specific_preparation" and next_race:
                resumed_intent = f"{next_race.get('priority', 'next')}-race-specific preparation"
            else:
                resumed_intent = phase["phase_type"].replace("_", " ")
            transition_note = f"Begin the week with post-race recovery, then resume {resumed_intent}."
        elif len(phase_mix) > 1:
            transition_note = "This week transitions from " + " + ".join(f"{days} day(s) {component['phase_type'].replace('_', ' ')}" for days, component in phase_mix) + "; targets are weighted by those days, with race/taper priorities taking precedence."
        intents.append(WeeklyTrainingIntent(
            week_start=week_start.isoformat(), phase_id=phase["phase_id"], target_rowing_sessions=prescribed_rows, target_total_rowing_exposures=prescribed_rows, target_coached_rowing_exposures=coached_total, target_independent_rowing_exposures=independent_target, target_strength_sessions=strength_days, target_rest_days=rest_days,
            target_private_coaching_sessions=private_sessions, target_coached_row_sessions=coached_sessions,
            primary_session_roles=primary, secondary_session_roles=secondary, target_low_intensity_minutes=low, target_moderate_minutes=moderate, target_high_intensity_minutes=high,
            target_total_rowing_minutes=total, race_specific_minutes=high if phase_types & {"race_specific_preparation", "taper", "race"} else 0,
            load_direction=load, testing_or_race_events=events, taper_volume_factor=min(taper_factors) if taper_factors else 1.0, volume_target_factor=round(volume_factor, 3),
            phase_mix=mix_output, transition_note=transition_note, next_race_name=next_race.get("event_name") if next_race else None, next_race_priority=next_race.get("priority") if next_race else None,
            notes=(f"{phase['phase_type'].replace('_', ' ').title()} intent: {phase['reason']} "
                   "This intent constrains ordinary-row frequency but does not change template selection or workout duration."), algorithm_version=PLANNING_MODEL_VERSION,
        ).to_dict())
        week_start += timedelta(days=7)
    return intents
