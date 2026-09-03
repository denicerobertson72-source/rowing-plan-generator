"""Input and generated-plan validation with user-facing messages."""
from __future__ import annotations
from datetime import date
from .recurring_activities import validate_recurring_activities

def validate_profile(profile: dict) -> list[str]:
    errors=[]
    try:
        if date.fromisoformat(profile["season"]["end_date"]) < date.fromisoformat(profile["season"]["start_date"]): errors.append("Season end date must be after the start date.")
    except (KeyError, ValueError): errors.append("Season dates are required and must use YYYY-MM-DD.")
    for r in profile.get("races",[]):
        if not r.get("event_name") or not r.get("start_date") or not r.get("end_date"):
            errors.append("Each race needs a name, start date, and end date.")
            continue
        if r.get("start_date","") > r.get("end_date",""): errors.append(f"Race {r.get('event_name','')} ends before it starts.")
        if r.get("priority") not in ("A","B","C"): errors.append(f"Race {r.get('event_name','')} needs an A, B, or C priority.")
    for t in (profile.get("tests",{}).get("multi_duration_power_tests") or {}).values():
        if isinstance(t,dict) and t.get("value_watts") is not None and t["value_watts"] <= 0: errors.append("Test watts must be positive.")
    athlete=profile.get("athlete",{})
    for key in ("current_rowing_sessions_per_week","desired_rowing_sessions_per_week","longest_comfortable_continuous_row_minutes","current_approx_weekly_rowing_minutes"):
        if athlete.get(key) is not None and (not isinstance(athlete[key],int) or athlete[key] < 0): errors.append(f"{key.replace('_',' ').capitalize()} must be a non-negative whole number.")
    if athlete.get("recent_training_consistency") not in (None,"consistent","building","inconsistent","returning"): errors.append("Recent training consistency must be consistent, building, inconsistent, or returning.")
    errors.extend(validate_recurring_activities(profile))
    return errors

def hard_constraint_errors(plan: dict, profile: dict) -> list[str]:
    availability={x["weekday"]:x for x in profile.get("weekly_availability",[])}; errors=[]
    for s in plan.get("sessions",[]):
        a=availability.get(s.get("day","").lower(),{})
        if s.get("mode") in ("erg","on_water") and a.get("heavy_lifting") and not a.get("row_on_lifting_day",True): errors.append(f"Rowing is prohibited on {s['date']}.")
        if s.get("mode") in ("erg","on_water") and (a.get("fixed_rest") or not a.get("available",True)): errors.append(f"Training is prohibited on {s['date']}.")
    return errors
