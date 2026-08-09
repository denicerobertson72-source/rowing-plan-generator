"""Input and generated-plan validation with user-facing messages."""
from __future__ import annotations
from datetime import date

def validate_profile(profile: dict) -> list[str]:
    errors=[]
    try:
        if date.fromisoformat(profile["season"]["end_date"]) < date.fromisoformat(profile["season"]["start_date"]): errors.append("Season end date must be after the start date.")
    except (KeyError, ValueError): errors.append("Season dates are required and must use YYYY-MM-DD.")
    for r in profile.get("races",[]):
        if r.get("start_date","") > r.get("end_date",""): errors.append(f"Race {r.get('event_name','')} ends before it starts.")
    for t in (profile.get("tests",{}).get("multi_duration_power_tests") or {}).values():
        if isinstance(t,dict) and t.get("value_watts") is not None and t["value_watts"] <= 0: errors.append("Test watts must be positive.")
    return errors

def hard_constraint_errors(plan: dict, profile: dict) -> list[str]:
    availability={x["weekday"]:x for x in profile.get("weekly_availability",[])}; errors=[]
    for s in plan.get("sessions",[]):
        a=availability.get(s.get("day","").lower(),{})
        if s.get("mode") in ("erg","on_water") and a.get("heavy_lifting") and not a.get("row_on_lifting_day",True): errors.append(f"Rowing is prohibited on {s['date']}.")
        if s.get("mode") in ("erg","on_water") and (a.get("fixed_rest") or not a.get("available",True)): errors.append(f"Training is prohibited on {s['date']}.")
    return errors
