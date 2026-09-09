import copy
import json
from pathlib import Path

from rowing_plan.intensity import build_intensity_profile
from rowing_plan.periodization import build_season_phases, phase_for_day, race_dates
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan
from services.api.tests.disposable_browser_fixture import synthetic_profile


CONFIG=json.loads((Path(__file__).resolve().parents[1] / "config" / "defaults.json").read_text())


def _profile(race: dict) -> dict:
    profile=synthetic_profile()
    profile["season"].update({"start_date":"2026-10-12","end_date":"2026-10-18"})
    profile["races"]=[race]
    profile["recurring_activities"]=[]
    return profile


def _plan(profile: dict) -> dict:
    return generate_plan(profile, CONFIG, build_intensity_profile(profile, CONFIG), build_power_profile(profile, CONFIG))


def _on(plan: dict, value: str) -> list[dict]:
    return [session for session in plan["sessions"] if session["date"] == value]


def test_single_day_event_has_one_actual_race_day():
    profile=_profile({"event_name":"Saturday race","start_date":"2026-10-17","end_date":"2026-10-17","race_dates":["2026-10-17"],"priority":"B","race_type":"head_5k"})
    plan=_plan(profile)
    assert [session["session_id"] for session in _on(plan,"2026-10-17")] == ["RACE"]
    assert all(session["session_id"] != "RACE" for session in _on(plan,"2026-10-16"))


def test_event_window_practice_is_not_race_and_taper_recovery_use_actual_race_day():
    race={"event_name":"Two-day head race","start_date":"2026-10-16","end_date":"2026-10-17","race_dates":["2026-10-17"],"practice_sessions":[{"date":"2026-10-16","title":"Course practice","duration_minutes":30}],"priority":"B","race_type":"head_5k"}
    profile=_profile(race)
    plan=_plan(profile)
    friday, saturday, sunday=_on(plan,"2026-10-16"),_on(plan,"2026-10-17"),_on(plan,"2026-10-18")
    assert [session["session_id"] for session in friday] == ["COURSE_PRACTICE"]
    assert friday[0]["title"] == "Course practice" and friday[0]["quality_minutes"] == 0
    assert [session["session_id"] for session in saturday] == ["RACE"]
    assert phase_for_day(__import__("datetime").date(2026,10,16), [race])[0] == "taper_sharpen"
    assert phase_for_day(__import__("datetime").date(2026,10,18), [race])[0] == "race_recovery"
    assert all(session.get("phase") != "race" for session in friday)
    assert all(session.get("session_id") != "RACE" for session in sunday)


def test_multi_day_regatta_can_have_multiple_actual_race_days():
    race={"event_name":"Weekend regatta","start_date":"2026-10-17","end_date":"2026-10-18","race_dates":["2026-10-17","2026-10-18"],"priority":"A","race_type":"head_5k"}
    plan=_plan(_profile(race))
    assert [session["session_id"] for session in _on(plan,"2026-10-17")] == ["RACE"]
    assert [session["session_id"] for session in _on(plan,"2026-10-18")] == ["RACE"]


def test_travel_window_without_competition_is_not_a_race_day_and_legacy_data_is_not_mutated():
    race={"event_name":"Legacy travel event","start_date":"2026-10-16","end_date":"2026-10-17","priority":"B","race_type":"head_5k"}
    original=copy.deepcopy(race)
    plan=_plan(_profile(race))
    assert race == original
    assert race_dates(race) == [__import__("datetime").date(2026,10,17)]
    assert not any(session["session_id"] == "RACE" for session in _on(plan,"2026-10-16"))
    assert [session["session_id"] for session in _on(plan,"2026-10-17")] == ["RACE"]


def test_acceptance_week_retains_unknown_coached_row_before_practice_and_race():
    race={"event_name":"Two-day head race","start_date":"2026-10-16","end_date":"2026-10-17","race_dates":["2026-10-17"],"practice_sessions":[{"date":"2026-10-16","title":"Race familiarization","duration_minutes":30}],"priority":"B","race_type":"head_5k"}
    profile=_profile(race)
    profile["recurring_activities"]=[
        {"activity_id":"private","activity_type":"private_coaching","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["wednesday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        {"activity_id":"coach","activity_type":"coached_row","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["thursday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
        {"activity_id":"rest","activity_type":"rest","sessions_per_week":1,"scheduling_status":"fixed","fixed_days":["monday"],"preferred_days":[],"allowed_days":[],"prohibited_days":[]},
    ]
    plan=_plan(profile)
    assert [item["session_id"] for item in _on(plan,"2026-10-15")] == ["COACHED"]
    assert _on(plan,"2026-10-15")[0]["quality_minutes"] == 0
    assert [item["session_id"] for item in _on(plan,"2026-10-16")] == ["COURSE_PRACTICE"]
    assert [item["session_id"] for item in _on(plan,"2026-10-17")] == ["RACE"]
    assert phase_for_day(__import__("datetime").date(2026,10,18), [race])[0] == "race_recovery"
