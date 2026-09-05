"""Small deterministic candidate scorer for movable recurring activities."""
from __future__ import annotations
from itertools import combinations

def placements(activity: dict, available_days: list[str] | None = None) -> list[tuple[str,...]]:
    if activity.get("scheduling_status")=="fixed": return [tuple(activity.get("fixed_days",[]))]
    # Preferred days are valid days too; "also possible" is additive, not a
    # replacement for the athlete's preferred choices.
    explicit=[*activity.get("preferred_days",[]),*activity.get("allowed_days",[])]
    # An empty allowed-days list means "planner may choose from my available
    # week", not "there are no possible days".  This keeps preference and
    # availability distinct for modern flexible cards.
    # Preferred days never imply that all other available days are forbidden.
    # Only an explicit allowed-days list narrows a movable card.
    days=explicit if activity.get("allowed_days") else [*explicit,*list(available_days or [])]
    days=[d for d in dict.fromkeys(days) if d not in activity.get("prohibited_days",[])]
    return list(combinations(days,activity.get("sessions_per_week",1)))

def score(activity: dict, selected: tuple[str,...], quality_days: set[str], fixed_days: set[str]) -> tuple[int,list[str]]:
    points,reasons=0,[]; preferred=set(activity.get("preferred_days",[]))
    for day in selected:
        if day in preferred: points+=3
        else: points-=1; reasons.append("moved_from_preferred_day")
        if day in fixed_days: points-=8; reasons.append("avoids_fixed_commitment")
        if day in quality_days: points-=6; reasons.append("improved_spacing_before_quality_row")
        if day in activity.get("discouraged_days",[]): points-=12; reasons.append("protects_preferred_long_training_day")
    return points,reasons

def choose(activity: dict, quality_days: set[str], fixed_days: set[str], unavailable_days: set[str] | None = None, available_days: list[str] | None = None) -> dict:
    """Select every requested occurrence or report that the schedule is impossible.

    A movable activity may never silently share a day already committed to another
    recurring activity.  That keeps its requested weekly frequency meaningful.
    """
    unavailable_days=unavailable_days or set()
    candidates=placements(activity, available_days)
    if activity.get("scheduling_status") != "fixed":
        candidates=[candidate for candidate in candidates if not set(candidate) & unavailable_days]
    if not candidates: return {"scheduled_days":[],"score":-999,"explanation":"No valid scheduling day is available."}
    ranked=sorted(((score(activity,c,quality_days,fixed_days),c) for c in candidates),key=lambda x:x[0][0],reverse=True)
    (points,reasons),days=ranked[0]; moved=bool(set(days)-set(activity.get("preferred_days",[])))
    return {"scheduled_days":list(days),"score":points,"activity_moved":moved,"reason_codes":reasons,"explanation":"Planner selected this placement to improve recovery spacing before rowing quality work." if moved else "Placement preserves your preferred schedule."}
