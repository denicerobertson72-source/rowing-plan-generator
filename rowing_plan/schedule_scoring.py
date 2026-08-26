"""Small deterministic candidate scorer for movable recurring activities."""
from __future__ import annotations
from itertools import combinations

def placements(activity: dict) -> list[tuple[str,...]]:
    if activity.get("scheduling_status")=="fixed": return [tuple(activity.get("fixed_days",[]))]
    # Preferred days are valid days too; "also possible" is additive, not a
    # replacement for the athlete's preferred choices.
    days=[d for d in dict.fromkeys([*activity.get("preferred_days",[]),*activity.get("allowed_days",[])]) if d not in activity.get("prohibited_days",[])]
    return list(combinations(days,activity.get("sessions_per_week",1)))

def score(activity: dict, selected: tuple[str,...], quality_days: set[str], fixed_days: set[str]) -> tuple[int,list[str]]:
    points,reasons=0,[]; preferred=set(activity.get("preferred_days",[]))
    for day in selected:
        if day in preferred: points+=3
        else: points-=1; reasons.append("moved_from_preferred_day")
        if day in fixed_days: points-=8; reasons.append("avoids_fixed_commitment")
        if day in quality_days: points-=6; reasons.append("improved_spacing_before_quality_row")
    return points,reasons

def choose(activity: dict, quality_days: set[str], fixed_days: set[str]) -> dict:
    candidates=placements(activity)
    if not candidates: return {"scheduled_days":[],"score":-999,"explanation":"No valid scheduling day is available."}
    ranked=sorted(((score(activity,c,quality_days,fixed_days),c) for c in candidates),key=lambda x:x[0][0],reverse=True)
    (points,reasons),days=ranked[0]; moved=bool(set(days)-set(activity.get("preferred_days",[])))
    return {"scheduled_days":list(days),"score":points,"activity_moved":moved,"reason_codes":reasons,"explanation":"Planner selected this placement to improve recovery spacing before rowing quality work." if moved else "Placement preserves your preferred schedule."}
