"""Race-priority phases computed deterministically from calendar inputs."""
from __future__ import annotations
from datetime import date, timedelta

TAPER = {"A":10,"B":5,"C":2}
RECOVERY = {"A":3,"B":2,"C":1}
def parse(d): return date.fromisoformat(d) if isinstance(d,str) else d
def phase_for_day(day: date, races: list[dict]) -> tuple[str, dict | None]:
    for r in races:
        start,end=parse(r["start_date"]),parse(r["end_date"])
        if start <= day <= end: return "race",r
        if start-timedelta(days=TAPER[r["priority"]]) <= day < start: return "taper_sharpen",r
        if end < day <= end+timedelta(days=RECOVERY[r["priority"]]): return "race_recovery",r
    future=[r for r in races if parse(r["start_date"])>day]
    return ("race_build" if future and (parse(future[0]["start_date"])-day).days < 28 else "specific_preparation"), (future[0] if future else None)

def build_phases(profile: dict) -> list[dict]:
    start,end=parse(profile["season"]["start_date"]),parse(profile["season"]["end_date"])
    result=[]; day=start
    while day<=end:
        phase,race=phase_for_day(day,profile.get("races",[])); result.append({"date":day.isoformat(),"phase":phase,"race_event":race.get("event_name") if race else None}); day+=timedelta(days=1)
    return result
