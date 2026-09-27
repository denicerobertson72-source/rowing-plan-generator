"""Scheduler V2.0 demand generation.  This module performs no placement."""
from __future__ import annotations
from datetime import date, timedelta
from itertools import combinations
from .models import CandidateDateResult, DateContext, FrequencyTarget, RollingPlacementResult, TrainingDemand, TrainingDoseTarget, V2DemandPlan
from .periodization import build_season_phases, parse, race_dates

_ROLE={"LONG_AEROBIC":("long_aerobic","aerobic",0),"AEROBIC_BASE":("aerobic_base","aerobic",0),"AEROBIC_STRENGTH":("aerobic_strength","aerobic",1),"THRESHOLD":("threshold","quality",1),"RACE_PACE":("race_pace","quality",1),"SPRINT_POWER":("sprint_power","quality",1),"RECOVERY":("recovery","none",0),"TECHNIQUE_EASY":("aerobic_base","aerobic",0)}
ROLLING_WINDOW_DAYS=14
ROLLING_OVERLAP_DAYS=7
BEAM_WIDTH=16

def _weeks(start,end):
    monday=start-timedelta(days=start.weekday())
    while monday<=end:
        if monday+timedelta(days=6)>=start: yield monday
        monday+=timedelta(days=7)

def generate_training_demands(profile: dict, season_phases: list[dict]|None=None, races: list[dict]|None=None, recurring_activities: list[dict]|None=None) -> list[TrainingDemand]:
    """Purely describe V2 needs; flexible demands deliberately have windows."""
    start,end=parse(profile["season"]["start_date"]),parse(profile["season"]["end_date"])
    phases=season_phases or build_season_phases(profile); activities=recurring_activities if recurring_activities is not None else profile.get("recurring_activities",[])
    output=[]
    for week in _weeks(start,end):
        eligible_start=max(start,week); eligible_end=min(end,week+timedelta(days=6))
        phase=next(item for item in phases if item["start_date"]<=eligible_start.isoformat()<=item["end_date"])
        for activity in activities:
            kind=activity.get("activity_type"); count=int(activity.get("sessions_per_week",0))
            if not count: continue
            if kind=="private_coaching" and activity.get("scheduling_status")=="fixed":
                for weekday in activity.get("fixed_days",[]):
                    day=week+timedelta(days=("monday tuesday wednesday thursday friday saturday sunday".split().index(weekday)))
                    if eligible_start<=day<=eligible_end: output.append(TrainingDemand(f"private:{week}:{day}","private_coaching",phase["phase_id"],day,day,(day,),(),"hard",None,"none",0,"private_weekly","recurring_activity","Fixed private coaching."))
            elif kind=="coached_row":
                choices=tuple(week+timedelta(days=("monday tuesday wednesday thursday friday saturday sunday".split().index(x))) for x in activity.get("allowed_days",[]))
                preferred=tuple(week+timedelta(days=("monday tuesday wednesday thursday friday saturday sunday".split().index(x))) for x in activity.get("preferred_days",[]))
                output.append(TrainingDemand(f"coached:{week}","coached_training",phase["phase_id"],eligible_start,eligible_end,choices,preferred,"strong",None,"aerobic",0,"coached_weekly","recurring_activity","One coached exposure in an eligible week."))
            elif kind == "rest":
                occurrences=1
                for occurrence in range(occurrences):
                    group="rest_weekly"
                    output.append(TrainingDemand(f"{kind}:{week}:{occurrence}",kind,phase["phase_id"],eligible_start,eligible_end,(),tuple(week+timedelta(days=("monday tuesday wednesday thursday friday saturday sunday".split().index(x))) for x in activity.get("preferred_days",[])),"strong",None,"none",0,group,"recurring_activity","One designated rest target; placement remains flexible."))
    return sorted(output,key=lambda item:item.demand_id)

def generate_frequency_targets(profile: dict, recurring_activities: list[dict]|None=None) -> list[FrequencyTarget]:
    """Return non-dated rolling targets.  Strength intentionally has no week key."""
    activities=recurring_activities if recurring_activities is not None else profile.get("recurring_activities",[])
    result=[]
    for activity in activities:
        if activity.get("activity_type")=="strength" and int(activity.get("sessions_per_week",0)):
            target=int(activity["sessions_per_week"])*2
            result.append(FrequencyTarget("strength",14,target,max(0,target-1),target,"strong",1,"recurring_activity","Approximately 4 strength exposures per rolling 14 days; recovery may reduce this to 3."))
    return result

def generate_rowing_dose_targets(profile: dict, season_phases: list[dict]|None=None) -> list[TrainingDoseTarget]:
    """Phase-clipped rolling doses; no target has a display-week identity."""
    start,end=parse(profile["season"]["start_date"]),parse(profile["season"]["end_date"]); phases=season_phases or build_season_phases(profile); result=[]
    patterns={"race_specific_preparation":[("dedicated_ut2",2,90,"aerobic",0),("long_aerobic",1,60,"aerobic",0),("ut1_aerobic_strength",1,45,"aerobic",1),("quality",3,75,"quality",1)],"threshold_development":[("dedicated_ut2",3,120,"aerobic",0),("long_aerobic",1,60,"aerobic",0),("quality",2,60,"quality",1)],"taper":[("dedicated_ut2",1,45,"aerobic",0),("quality",1,30,"quality",1)],"post_race_recovery":[("dedicated_ut2",1,40,"aerobic",0)]}
    for phase in phases:
        left=max(start,date.fromisoformat(phase["start_date"])); right=min(end,date.fromisoformat(phase["end_date"])); days=(right-left).days+1
        for category,count,minutes,quality,recovery in patterns.get(phase["phase_type"],[("dedicated_ut2",3,120,"aerobic",0),("long_aerobic",1,60,"aerobic",0),("ut1_aerobic_strength",1,45,"aerobic",1)]):
            scaled=max(0,round(count*min(days,14)/14)); result.append(TrainingDoseTarget(phase["phase_id"],category,left,right,min(14,days),scaled,max(0,scaled-1),round(minutes*min(days,14)/14),max(0,round(minutes*min(days,14)/14)-30),quality,"strong",recovery,"season_phase",f"{phase['phase_type']} rolling {category} dose."))
    return result

def generate_v2_demand_plan(profile: dict, season_phases: list[dict]|None=None, races: list[dict]|None=None, recurring_activities: list[dict]|None=None) -> V2DemandPlan:
    return V2DemandPlan(tuple(generate_training_demands(profile,season_phases,races,recurring_activities)),tuple(generate_frequency_targets(profile,recurring_activities)),tuple(generate_rowing_dose_targets(profile,season_phases)))

def build_v2_season_calendar(profile: dict, season_phases: list[dict]|None=None, races: list[dict]|None=None, locked_sessions: list[dict]|None=None) -> tuple[DateContext,...]:
    """Build dated hard context only; no flexible activity is placed here."""
    start,end=parse(profile["season"]["start_date"]),parse(profile["season"]["end_date"]); phases=season_phases or build_season_phases(profile); races=races if races is not None else profile.get("races",[]); locks=locked_sessions or []
    availability={item["weekday"]:item for item in profile.get("weekly_availability",[])}; activities=profile.get("recurring_activities",[]); result=[]; current=start
    names=("monday","tuesday","wednesday","thursday","friday","saturday","sunday")
    while current<=end:
        weekday=names[current.weekday()]; avail=availability.get(weekday,{}); max_minutes=int(avail.get("max_training_minutes",0)); unavailable=not avail.get("available",True) or max_minutes<=0
        phase=next(item for item in phases if item["start_date"]<=current.isoformat()<=item["end_date"]); commitments=[]
        race=any(current in race_dates(item) for item in races); practice=next((entry for item in races for entry in item.get("practice_sessions",[]) if entry.get("date")==current.isoformat()),None)
        for activity in activities:
            if activity.get("scheduling_status")=="fixed" and weekday in activity.get("fixed_days",[]): commitments.append({"type":activity.get("activity_type"),"minutes":min(50,max_minutes),"fixed":True})
        commitments += [{"type":"locked","minutes":int(item.get("total_training_minutes",item.get("total_cardio_minutes",0))),"fixed":True} for item in locks if item.get("date")==current.isoformat()]
        if race: commitments.append({"type":"race","minutes":20,"fixed":True})
        if practice: commitments.append({"type":"course_practice","minutes":int(practice.get("duration_minutes",30)),"fixed":True})
        used=sum(item["minutes"] for item in commitments); reasons=tuple(code for code,truth in (("unavailable",unavailable),("race_day",race),("race_practice",bool(practice))) if truth)
        permitted=() if unavailable or race else ("coached_training","strength","rest","rowing")
        result.append(DateContext(current,weekday,phase["phase_id"],not unavailable,max_minutes,used,max(0,max_minutes-used),unavailable,race,bool(practice),phase["phase_type"] in {"taper","post_race_recovery"},tuple(commitments),tuple(item for item in locks if item.get("date")==current.isoformat()),permitted,reasons)); current+=timedelta(days=1)
    return tuple(result)

def candidate_dates_for_demand(demand, calendar: tuple[DateContext,...], required_minutes: int|None=None) -> CandidateDateResult:
    """Return hard-feasible dates only; preference and recovery are intentionally absent."""
    needed=required_minutes if required_minutes is not None else getattr(demand,"target_minutes",None) or 0; accepted=[]; rejected=[]
    for context in calendar:
        reasons=[]
        if not demand.earliest_date<=context.date<=demand.latest_date: reasons.append("outside_demand_window")
        if context.unavailable: reasons.append("unavailable")
        if context.race: reasons.append("race_day")
        if context.race_practice: reasons.append("fixed_commitment_conflict")
        if context.remaining_minutes<needed: reasons.append("insufficient_minutes")
        if demand.type=="coached_training" and context.date not in demand.desired_dates: reasons.append("outside_allowed_dates")
        if demand.type=="private_coaching" and context.date!=demand.earliest_date: reasons.append("outside_allowed_dates")
        if reasons: rejected.append((context.date.isoformat(),tuple(reasons)))
        else: accepted.append(context.date)
    return CandidateDateResult(tuple(accepted),tuple(rejected))

def candidate_dates_for_dose(target: TrainingDoseTarget, calendar: tuple[DateContext,...]) -> CandidateDateResult:
    """Hard-feasible dates for a rolling strength/rowing target without selecting one."""
    class Window: pass
    window=Window(); window.earliest_date=target.window_start; window.latest_date=target.window_end; window.type=target.category; window.target_minutes=target.target_minutes
    window.desired_dates=() if target.category!="coached_training" else ()
    return candidate_dates_for_demand(window,calendar,target.target_minutes)

def _strength_minutes(profile):
    activity=next((item for item in profile.get("recurring_activities",[]) if item.get("activity_type")=="strength"),{})
    legacy={item.get("weekday"):item for item in profile.get("weekly_availability",[])}
    return int(activity.get("duration_minutes") or activity.get("typical_strength_minutes") or next((item.get("lifting_minutes",0) for item in legacy.values() if item.get("lifting_minutes")),0) or 60)

def place_v2_non_rowing(profile: dict, demand_plan: V2DemandPlan|None=None, calendar: tuple[DateContext,...]|None=None) -> RollingPlacementResult:
    """V2.2 bounded placement of coached/rest/strength only; rowing stays open."""
    plan=demand_plan or generate_v2_demand_plan(profile); cal=calendar or build_v2_season_calendar(profile); contexts={item.date:item for item in cal}; demands=[item for item in plan.weekly_commitment_demands if item.type in {"coached_training","rest"}]
    states=[(0,(),())]; explored=0
    for demand in sorted(demands,key=lambda item:(item.type!="coached_training",item.demand_id)):
        next_states=[]
        candidates=candidate_dates_for_demand(demand,cal).candidates
        for score,placements,audits in states:
            occupied={day for _,day in placements}
            for day in candidates:
                if day in occupied: continue
                component=3 if day in demand.preferred_dates else 0
                # Rest gains a small spacing benefit beside hard commitments,
                # never a weekday preference.
                if demand.type=="rest": component+=sum(1 for offset in (-1,1) if contexts.get(day+timedelta(days=offset),None) and contexts[day+timedelta(days=offset)].hard_committed_minutes)
                next_states.append((score+component,placements+((demand.demand_id,day),),audits+({"activity":demand.type,"selected_date":day.isoformat(),"candidate_dates":[value.isoformat() for value in candidates],"score_components":{"preferred_day":component if demand.type=="coached_training" else 0},"decisive_reason_codes":["preferred_day"] if day in demand.preferred_dates else []},))) ; explored+=1
        states=sorted(next_states,key=lambda row:(-row[0],tuple((key,value.isoformat()) for key,value in row[1])))[:BEAM_WIDTH]
    strength_target=next((item for item in plan.frequency_targets if item.group_id=="strength"),None); strength_days=[]; misses=[]; duration=_strength_minutes(profile); window_audits=[]
    if strength_target:
        occupied={day for _,day in states[0][1]}
        preferred=set(next((a for a in profile.get("recurring_activities",[]) if a.get("activity_type")=="strength"),{}).get("preferred_days",[]))
        names=("monday","tuesday","wednesday","thursday","friday","saturday","sunday")
        first,last=cal[0].date,cal[-1].date; window_start=first
        while window_start<=last:
            window_end=min(last,window_start+timedelta(days=ROLLING_WINDOW_DAYS-1)); days=(window_end-window_start).days+1
            # A short season tail is diagnostic-only: do not invent a weekly fallback.
            target=strength_target.target_count if days==ROLLING_WINDOW_DAYS else round(strength_target.target_count*days/ROLLING_WINDOW_DAYS)
            minimum=strength_target.minimum_count if days==ROLLING_WINDOW_DAYS else max(0,round(strength_target.minimum_count*days/ROLLING_WINDOW_DAYS))
            frozen=[day for day in strength_days if day<window_start]
            # Days from this window forward are provisional and deliberately replaced.
            strength_days=[day for day in strength_days if day<window_start]
            feasible=[item.date for item in cal if window_start<=item.date<=window_end and item.date not in occupied and not item.unavailable and not item.race and not item.race_practice and item.remaining_minutes>=duration and all(abs((item.date-old).days)>strength_target.minimum_spacing_days for old in frozen)]
            choices=[]
            for count in range(min(target,len(feasible)),-1,-1):
                for picked in combinations(feasible,count):
                    if all((right-left).days>strength_target.minimum_spacing_days for left,right in zip(picked,picked[1:])): choices.append(picked)
                if choices: break
            best=max(choices,key=lambda picked:(sum(names[day.weekday()] in preferred for day in picked),tuple(-day.toordinal() for day in picked))) if choices else ()
            strength_days.extend(best); achieved=len(best); status="target_met" if achieved==target else "acceptable_miss" if achieved>=minimum else "below_minimum"
            audit={"window_start":window_start.isoformat(),"window_end":window_end.isoformat(),"target":target,"minimum":minimum,"achieved":achieved,"status":status,"frozen_strength_dates":[day.isoformat() for day in frozen],"provisional_strength_dates":[day.isoformat() for day in best],"states_explored":len(choices),"states_retained":1,"pruning_count":max(0,len(feasible)-len(choices))}
            window_audits.append(audit)
            if status!="target_met": misses.append({"group":"strength",**audit,"blockers":["hard_calendar_or_spacing"]})
            window_start+=timedelta(days=ROLLING_WINDOW_DAYS-ROLLING_OVERLAP_DAYS)
    placements=states[0][1]+tuple((f"strength:{index}",day) for index,day in enumerate(strength_days))
    audits=states[0][2]+tuple({"activity":"strength","selected_date":day.isoformat(),"decisive_reason_codes":["rolling_target","no_consecutive_strength"]} for day in strength_days)+tuple(window_audits)
    return RollingPlacementResult(placements,audits,tuple(misses),BEAM_WIDTH,explored,len(states))
