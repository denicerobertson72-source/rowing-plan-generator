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


_WEEKDAY=("monday","tuesday","wednesday","thursday","friday","saturday","sunday")

def weekly_candidates(activities: list[dict], available_days: list[str], quality_days: set[str], preferred_long_days: set[str], max_results: int = 5, hard_session_days: set[str] | None = None) -> list[dict]:
    """Return the best valid whole-week layouts with explicit score parts.

    The lexical final key is only a neutral deterministic tie-break: no score
    component gives Saturday special treatment.
    """
    options=[(activity,placements(activity,available_days)) for activity in activities]
    if any(not choices or any(len(choice) != activity.get("sessions_per_week",1) for choice in choices) for activity,choices in options):
        return []
    hard_session_days=quality_days if hard_session_days is None else hard_session_days
    candidates=[]
    def visit(index: int, occupied: set[str], placed: dict[str,tuple[str,...]]):
        if index == len(options):
            strength_days={day for activity,_ in options if activity.get("activity_type")=="strength" for day in placed[str(activity.get("activity_id"))]}
            rest_days={day for activity,_ in options if activity.get("activity_type")=="rest" for day in placed[str(activity.get("activity_id"))]}
            strength_preference=sum(3 if day in activity.get("preferred_days",[]) else -1 for activity,_ in options if activity.get("activity_type")=="strength" for day in placed[str(activity.get("activity_id"))])
            hard_spacing=sum(-10 if day in hard_session_days else -4 if any(abs(_WEEKDAY.index(day)-_WEEKDAY.index(hard))==1 for hard in hard_session_days) else 0 for day in strength_days)
            movable_quality=sum(score(activity,placed[str(activity.get("activity_id"))],quality_days,set())[0] for activity,_ in options if activity.get("scheduling_status")!="fixed" and activity.get("activity_type") not in {"strength"})
            long_open=bool(preferred_long_days-set(occupied))
            long_score=(10 if long_open else -10) if preferred_long_days else 0
            rest_score=sum(4 for day in rest_days if any(_WEEKDAY.index(long)-_WEEKDAY.index(day)==1 for long in preferred_long_days))
            rest_score+=sum(2 for day in rest_days if any(abs(_WEEKDAY.index(day)-_WEEKDAY.index(hard))==1 for hard in hard_session_days))
            available_time=5 if long_open and preferred_long_days else 0
            components={"fixed_commitment_score":0,"training_quality_recovery_score":movable_quality,"long_session_placement_score":long_score,"strength_preference_score":strength_preference,"rest_recovery_score":rest_score,"hard_session_spacing_score":hard_spacing,"available_time_score":available_time}
            candidates.append({"score":sum(components.values()),"score_components":components,"placements":{key:list(value) for key,value in placed.items()}})
            return
        activity,choices=options[index]; key=str(activity.get("activity_id"))
        for choice in choices:
            if not set(choice)&occupied: visit(index+1,occupied|set(choice),{**placed,key:choice})
    visit(0,set(),{})
    return sorted(candidates,key=lambda item:(-item["score"],tuple((key,tuple(value)) for key,value in sorted(item["placements"].items()))))[:max_results]
