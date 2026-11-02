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
        if "race_dates" in r and not r["race_dates"]: errors.append(f"Race {r.get('event_name','')} needs at least one actual competition date.")
        for race_date in r.get("race_dates", []):
            if not isinstance(race_date, str) or not r["start_date"] <= race_date <= r["end_date"]: errors.append(f"Race date for {r.get('event_name','')} must be inside its event dates.")
        for practice in r.get("practice_sessions", []):
            practice_date=practice.get("date") if isinstance(practice,dict) else None
            if not isinstance(practice_date, str) or not r["start_date"] <= practice_date <= r["end_date"]: errors.append(f"Practice date for {r.get('event_name','')} must be inside its event dates.")
    for t in (profile.get("tests",{}).get("multi_duration_power_tests") or {}).values():
        if isinstance(t,dict) and t.get("value_watts") is not None and t["value_watts"] <= 0: errors.append("Test watts must be positive.")
    athlete=profile.get("athlete",{})
    for key in ("current_rowing_sessions_per_week","desired_rowing_sessions_per_week","longest_comfortable_continuous_row_minutes","current_approx_weekly_rowing_minutes"):
        if athlete.get(key) is not None and (not isinstance(athlete[key],int) or athlete[key] < 0): errors.append(f"{key.replace('_',' ').capitalize()} must be a non-negative whole number.")
    if athlete.get("recent_training_consistency") not in (None,"consistent","building","inconsistent","returning"): errors.append("Recent training consistency must be consistent, building, inconsistent, or returning.")
    context=profile.get("training_context", {})
    if context is not None and not isinstance(context, dict):
        errors.append("Training context must be an object.")
    elif isinstance(context, dict):
        strength_sessions=context.get("current_strength_sessions_per_week")
        if strength_sessions is not None and (not isinstance(strength_sessions, int) or not 0 <= strength_sessions <= 7):
            errors.append("Current strength sessions per week must be a whole number from 0 to 7.")
        if context.get("strength_experience") not in (None,"none","beginner","intermediate","advanced"):
            errors.append("Strength experience must be none, beginner, intermediate, or advanced.")
        if context.get("strength_priority") not in (None,"maintenance","support_rowing","develop_strength","not_a_current_priority"):
            errors.append("Strength priority is invalid.")
        if context.get("training_restriction_source", "none") not in ("none","athlete","coach","clinician"):
            errors.append("Training restriction source must be none, athlete, coach, or clinician.")
        equipment=context.get("strength_equipment_access", [])
        allowed_equipment={"full_gym","barbell_rack","dumbbells","machines_cables","kettlebells","bands_bodyweight","home_gym","other"}
        if not isinstance(equipment, list) or any(item not in allowed_equipment for item in equipment):
            errors.append("Strength equipment access contains an invalid option.")
        for key in ("strength_limitations", "training_restriction_summary"):
            if key in context and (not isinstance(context[key], str) or len(context[key]) > 1000):
                errors.append(f"{key.replace('_',' ').capitalize()} must be text of 1000 characters or fewer.")
    errors.extend(validate_recurring_activities(profile))
    return errors

def hard_constraint_errors(plan: dict, profile: dict) -> list[str]:
    availability={x["weekday"]:x for x in profile.get("weekly_availability",[])}; errors=[]
    for s in plan.get("sessions",[]):
        a=availability.get(s.get("day","").lower(),{})
        if s.get("mode") in ("erg","on_water") and a.get("heavy_lifting") and not a.get("row_on_lifting_day",True): errors.append(f"Rowing is prohibited on {s['date']}.")
        if s.get("mode") in ("erg","on_water") and (a.get("fixed_rest") or not a.get("available",True)): errors.append(f"Training is prohibited on {s['date']}.")
    return errors
