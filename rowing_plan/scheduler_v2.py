"""Scheduler V2.0 demand generation.  This module performs no placement."""
from __future__ import annotations
from datetime import date, timedelta
from time import perf_counter
from dataclasses import replace
from .models import ActiveWindowState, CandidateDateResult, DateContext, DatedTrainingRole, DemandSatisfaction, FrequencyTarget, RepairReconstructionResult, RepairScope, ReopenedPlacement, RollingPlacementResult, ScheduleChangeResult, TargetCredit, TrainingDemand, TrainingDoseTarget, UserScheduleOverride, V2DemandPlan, WindowPlacement
from .periodization import build_season_phases, parse, race_dates

_ROLE={"LONG_AEROBIC":("long_aerobic","aerobic",0),"AEROBIC_BASE":("aerobic_base","aerobic",0),"AEROBIC_STRENGTH":("aerobic_strength","aerobic",1),"THRESHOLD":("threshold","quality",1),"RACE_PACE":("race_pace","quality",1),"SPRINT_POWER":("sprint_power","quality",1),"RECOVERY":("recovery","none",0),"TECHNIQUE_EASY":("aerobic_base","aerobic",0)}
ROLLING_WINDOW_DAYS=14
ROLLING_OVERLAP_DAYS=7
BEAM_WIDTH=16

def _rolling_solver_windows(start: date, end: date) -> tuple[tuple[date,date],...]:
    """The concrete windows used by ``solve_v2_rolling_non_rowing``."""
    result=[]; current=start
    while current<=end:
        result.append((current,min(end,current+timedelta(days=ROLLING_WINDOW_DAYS-1))))
        current+=timedelta(days=ROLLING_OVERLAP_DAYS)
    return tuple(result)

def derive_repair_scope(changed_dates, calendar: tuple[DateContext,...], demand_plan: V2DemandPlan) -> RepairScope:
    """Derive bounded repair ranges without reconstructing or changing state.

    Mutable dates are the union of the real fourteen-day solver windows that
    contain a source or requested destination date.  History is read-only and
    starts far enough back for rolling strength, phase-clipped dose, and
    recovery checks.  Reconciliation extends through the last real solver
    window whose strength/dose horizon, recovery interval, or affected
    canonical coached/rest week can still include a changed date.
    """
    if not calendar:
        raise ValueError("empty_calendar")
    changes=tuple(sorted(set(changed_dates)))
    if not changes:
        raise ValueError("no_changed_dates")
    season_start,season_end=calendar[0].date,calendar[-1].date
    if any(day<season_start or day>season_end for day in changes):
        raise ValueError("changed_date_outside_season")
    windows=_rolling_solver_windows(season_start,season_end)
    mutable_windows=tuple(window for window in windows if any(window[0]<=day<=window[1] for day in changes))
    # Every in-season date belongs to at least its window beginning on or
    # before it, but retain an explicit guard if the solver cadence changes.
    if not mutable_windows:
        raise ValueError("changed_date_outside_solver_windows")
    mutable_start=min(window[0] for window in mutable_windows)
    mutable_end=max(window[1] for window in mutable_windows)

    strengths=tuple(item for item in demand_plan.frequency_targets if item.group_id=="strength")
    strength_history=max((item.window_days-1 for item in strengths),default=0)
    quality_recovery=max((item.minimum_recovery_days for item in demand_plan.rowing_dose_targets if item.category=="quality"),default=0)
    strength_recovery=max((item.minimum_spacing_days for item in strengths),default=0)
    recovery_history=max(quality_recovery,strength_recovery)
    dose_history=[]
    for target in demand_plan.rowing_dose_targets:
        if target.window_end < mutable_start or target.window_start > mutable_end:
            continue
        # The target's phase boundary remains a hard lower bound: repair
        # history never makes prior-phase dose eligible for this target.
        dose_history.append(max(target.window_start,mutable_start-timedelta(days=target.window_days-1)))
    history_start=min((mutable_start-timedelta(days=max(strength_history,recovery_history)),*dose_history),default=mutable_start)
    history_start=max(season_start,history_start)

    weekly=tuple(item for item in demand_plan.weekly_commitment_demands if item.type in {"coached_training","rest"} and item.canonical_week_start and item.canonical_week_end and item.canonical_week_start<=mutable_end and item.canonical_week_end>=mutable_start)
    relevant_ends=[]
    for window_start,window_end in windows:
        relevant=False
        for changed in changes:
            if any(window_end-timedelta(days=item.window_days-1)<=changed<=window_end for item in strengths):
                relevant=True
            if window_start<=changed+timedelta(days=max(quality_recovery,strength_recovery)) and window_end>=changed:
                relevant=True
            for target in demand_plan.rowing_dose_targets:
                if target.window_start>window_end or target.window_end<window_start:
                    continue
                horizon_start=max(target.window_start,window_end-timedelta(days=target.window_days-1))
                horizon_end=min(target.window_end,window_end)
                if horizon_start<=changed<=horizon_end:
                    relevant=True
        if any(window_start<=item.canonical_week_end and window_end>=item.canonical_week_start for item in weekly):
            relevant=True
        if relevant:
            relevant_ends.append(window_end)
    reconciliation_end=min(season_end,max(relevant_ends,default=mutable_end))
    return RepairScope(changes,mutable_start,mutable_end,history_start,reconciliation_end)

def _placement_matches_calendar_authority(placement: WindowPlacement, context: DateContext|None) -> bool:
    """Whether a calendar fact, rather than the planner, owns this placement."""
    if context is None:
        return False
    if context.race:
        return True
    records=(*context.fixed_commitments,*context.completed_sessions)
    for record in records:
        if record.get("placement_id")==placement.placement_id or record.get("source_id")==placement.source_id:
            return True
        kind=record.get("type")
        if kind in {"locked","completed"} and (placement.role==kind or placement.source_id==kind):
            return True
        if record.get("fixed") and (placement.role==kind or placement.source_id==kind):
            return True
    # These activity types are only emitted as fixed calendar commitments by
    # the current calendar builder, never as flexible generic roles.
    fixed_kinds={record.get("type") for record in context.fixed_commitments if record.get("fixed")}
    if placement.role=="private_coaching" and "private_coaching" in fixed_kinds:
        return True
    return placement.role in {"coached_training","coached_row"} and bool({"coached_training","coached_row"}&fixed_kinds)

def classify_repair_placement(placement: WindowPlacement, scope: RepairScope, calendar: tuple[DateContext,...]) -> str:
    """Classify final work by authority before considering planner ownership."""
    contexts={item.date:item for item in calendar}
    if _placement_matches_calendar_authority(placement,contexts.get(placement.date)):
        return "immutable_context"
    if placement.user_fixed:
        return "preserved_user_fixed"
    if not scope.mutable_start<=placement.date<=scope.mutable_end:
        return "immutable_context"
    return "reopened_planner_owned"

def reconstruct_repair_state(calendar: tuple[DateContext,...], scope: RepairScope, placements, *, canonical_demands: tuple[TrainingDemand,...]=()) -> RepairReconstructionResult:
    """Rebuild a local repair input from calendar facts without performing repair.

    Planner-owned placements in the mutable range are deliberately omitted from
    active capacity.  Their prior facts are returned separately so a later
    repair transaction can explain moves/removals without losing identity.
    """
    supplied=(*placements.frozen_placements,*placements.provisional_placements) if isinstance(placements,ActiveWindowState) else tuple(placements)
    contexts={item.date:item for item in calendar if scope.mutable_start<=item.date<=scope.mutable_end}
    if len(contexts)!=(scope.mutable_end-scope.mutable_start).days+1:
        raise ValueError("repair_scope_outside_calendar")
    immutable_history=[]; preserved_user=[]; reopened=[]; untouched=[]; active_frozen=[]; active_user=[]
    for placement in sorted(supplied,key=lambda item:(item.date,item.placement_id,item.role)):
        classification=classify_repair_placement(placement,scope,calendar)
        active=scope.mutable_start<=placement.date<=scope.mutable_end
        if classification=="reopened_planner_owned":
            reopened.append(ReopenedPlacement(placement.placement_id,placement.source_id,placement.date,placement.role,placement.minutes,placement.credits))
            continue
        if classification=="preserved_user_fixed":
            preserved_user.append(placement)
            if active: active_user.append(placement)
            else: immutable_history.append(placement)
            continue
        if active:
            # Context-owned work remains non-releasable in the repair input.
            active_frozen.append(replace(placement,frozen=True))
        elif placement.date<scope.mutable_start:
            immutable_history.append(placement)
        else:
            untouched.append(placement)
    frozen=tuple(sorted(tuple(replace(item,frozen=True) for item in immutable_history)+tuple(active_frozen),key=lambda item:(item.date,item.placement_id,item.role)))
    provisional=tuple(sorted((replace(item,frozen=False) for item in active_user),key=lambda item:(item.date,item.placement_id,item.role)))
    remaining={day:context.remaining_minutes for day,context in contexts.items()}
    for placement in (*active_frozen,*active_user):
        remaining[placement.date]-=placement.minutes
        if remaining[placement.date]<0:
            raise ValueError("repair_authoritative_capacity_exceeded")
    state=ActiveWindowState(scope.mutable_start,scope.mutable_end,frozen,provisional,remaining,contexts,{}, {}, {})
    state=reconcile_active_window_state(state)
    if canonical_demands:
        state=reconcile_demand_satisfaction(state,canonical_demands,scope.mutable_start)
    reopened=tuple(sorted(reopened,key=lambda item:(item.prior_date,item.placement_id,item.prior_role)))
    preserved_user=tuple(sorted(preserved_user,key=lambda item:(item.date,item.placement_id,item.role)))
    immutable_history=tuple(sorted(immutable_history,key=lambda item:(item.date,item.placement_id,item.role)))
    untouched=tuple(sorted(untouched,key=lambda item:(item.date,item.placement_id,item.role)))
    return RepairReconstructionResult(scope,state,immutable_history,preserved_user,reopened,tuple(sorted({item.source_id for item in reopened})),untouched)

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
    normalized=[]
    for item in output:
        week=item.earliest_date-timedelta(days=item.earliest_date.weekday()); week_end=week+timedelta(days=6); in_season=sum(start<=week+timedelta(days=offset)<=end for offset in range(7))
        required=item.type=="private_coaching" or in_season>=4
        if item.type=="coached_training": required=required and bool(set(item.desired_dates)&{week+timedelta(days=offset) for offset in range(7) if start<=week+timedelta(days=offset)<=end})
        normalized.append(replace(item,canonical_week_start=week,canonical_week_end=week_end,eligibility="required" if required else "edge_exception"))
    return sorted(normalized,key=lambda item:item.demand_id)

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
        if demand.type=="rest" and context.hard_committed_minutes: reasons.append("fixed_commitment_conflict")
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

def initialize_active_window_state(calendar: tuple[DateContext,...], window_start: date, window_end: date, *, frozen_history: tuple[WindowPlacement,...]=(), provisional_overlap: tuple[WindowPlacement,...]=(), canonical_demands: tuple[TrainingDemand,...]=()) -> ActiveWindowState:
    """Reconstruct a window solely from immutable calendar facts and placements."""
    contexts={item.date:item for item in calendar if window_start<=item.date<=window_end}
    frozen=tuple(sorted((WindowPlacement(item.placement_id,item.date,item.role,item.source_id,item.minutes,True,item.credits,item.user_fixed,item.original_date,item.override_id) for item in frozen_history),key=lambda item:(item.date,item.placement_id)))
    state=ActiveWindowState(window_start,window_end,frozen,(),{day:item.remaining_minutes for day,item in contexts.items()},contexts,{}, {}, {})
    for placement in sorted(provisional_overlap,key=lambda item:(item.date,item.placement_id)):
        if not window_start<=placement.date<=window_end: raise ValueError("provisional_outside_active_window")
        state=assign_window_placement(state,WindowPlacement(placement.placement_id,placement.date,placement.role,placement.source_id,placement.minutes,False,placement.credits,placement.user_fixed,placement.original_date,placement.override_id))
    return reconcile_demand_satisfaction(state,canonical_demands,window_start) if canonical_demands else state

def _is_quality(role): return role in {"quality","threshold","race_pace","sprint_power"}
def _is_strength(role): return role=="strength"
def _all_placements(state): return (*state.frozen_placements,*state.provisional_placements)

def build_dated_training_roles(state: ActiveWindowState) -> tuple[DatedTrainingRole,...]:
    """Pure final-state adapter; deliberately does not select workouts."""
    result=[]; seen=set()
    for item in sorted(_all_placements(state),key=lambda item:(item.date,item.placement_id,item.role)):
        if item.placement_id in seen: raise ValueError("duplicate_placement_id")
        seen.add(item.placement_id)
        context=state.fixed_context.get(item.date)
        if context is None: continue
        result.append(DatedTrainingRole(item.date,item.role,item.minutes,context.phase_id,item.source_id,"frozen" if item.frozen else "provisional",item.credits,item.placement_id))
    return tuple(result)
def _role_category(role): return "rest" if role=="rest" else "strength" if role=="strength" else "coached" if role=="coached_training" else "rowing"
def role_family(role): return "rowing" if role in {"dedicated_ut2","long_aerobic","ut1_aerobic_strength","quality"} else _role_category(role)
def placements_compatible(existing: WindowPlacement, incoming: WindowPlacement) -> tuple[bool,str|None]:
    """Order-independent flexible same-day compatibility source of truth."""
    if existing.date != incoming.date: return True,None
    if "rest" in {_role_category(existing.role),_role_category(incoming.role)}: return False,"rest_day"
    # V2 has not yet introduced profile-authorized stacking; capacity and a
    # future explicit matrix may widen this conservatively safe default.
    return False,"same_day_conflict"

def reconcile_active_window_state(state: ActiveWindowState) -> ActiveWindowState:
    frequency={}; rowing={}; weekly={}
    for placement in _all_placements(state):
        weekly[placement.source_id]=weekly.get(placement.source_id,0)+1
        for credit in placement.credits:
            dest=frequency if credit.category=="strength" else rowing
            count,minutes=dest.get(credit.target_id,(0,0)); dest[credit.target_id]=(count+credit.exposures,minutes+credit.minutes)
    return ActiveWindowState(state.window_start,state.window_end,state.frozen_placements,state.provisional_placements,dict(state.remaining_minutes_by_date),state.fixed_context,weekly,frequency,rowing,state.last_frozen_quality_date,state.last_frozen_strength_date,state.score_vector,state.audits,state.exceptions)

def reconcile_demand_satisfaction(state: ActiveWindowState, demands: tuple[TrainingDemand,...], as_of: date|None=None) -> ActiveWindowState:
    current=as_of or state.window_end; records={}
    for demand in demands:
        if demand.eligibility=="edge_exception": records[demand.demand_id]=DemandSatisfaction(demand.demand_id,demand.canonical_week_start,demand.canonical_week_end,"edge_exception","edge_exception"); continue
        frozen=next((item for item in state.frozen_placements if item.source_id==demand.demand_id),None); provisional=next((item for item in state.provisional_placements if item.source_id==demand.demand_id),None)
        item,status=(frozen,"frozen_satisfied") if frozen else (provisional,"provisional_satisfied") if provisional else (None,"missed" if demand.canonical_week_end<current else "open")
        records[demand.demand_id]=DemandSatisfaction(demand.demand_id,demand.canonical_week_start,demand.canonical_week_end,"required",status,item.placement_id if item else None,item.date if item else None,"placement" if item else "derived")
    return replace(state,demand_satisfaction=records)

def assign_window_placement(state: ActiveWindowState, placement: WindowPlacement) -> ActiveWindowState:
    if not state.window_start<=placement.date<=state.window_end: raise ValueError("outside_active_window")
    context=state.fixed_context.get(placement.date)
    if not context or context.unavailable or context.race or context.race_practice: raise ValueError("hard_calendar_conflict")
    if role_family(placement.role) in context.prohibited_role_families: raise ValueError("activity_prohibited")
    if state.remaining_minutes_by_date.get(placement.date,0)<placement.minutes: raise ValueError("insufficient_minutes")
    existing=_all_placements(state)
    if any(item.placement_id==placement.placement_id for item in existing): raise ValueError("duplicate_placement_id")
    if placement.role=="rest" and context.hard_committed_minutes: raise ValueError("fixed_commitment_conflict")
    for item in existing:
        compatible,reason=placements_compatible(item,placement)
        if not compatible: raise ValueError(reason or "same_day_conflict")
    history=[item for item in existing if _is_quality(item.role)]
    if _is_quality(placement.role) and any(abs((placement.date-item.date).days)<=1 for item in history): raise ValueError("quality_spacing")
    strengths=[item for item in existing if _is_strength(item.role)]
    if _is_strength(placement.role) and any(abs((placement.date-item.date).days)<=1 for item in strengths): raise ValueError("strength_spacing")
    remaining=dict(state.remaining_minutes_by_date); remaining[placement.date]-=placement.minutes
    result=ActiveWindowState(state.window_start,state.window_end,state.frozen_placements,state.provisional_placements+(placement,),remaining,state.fixed_context,state.weekly_commitment_status,state.frequency_credits,state.rowing_dose_credits,state.last_frozen_quality_date,state.last_frozen_strength_date,state.score_vector,state.audits+({"event":"assigned","placement_id":placement.placement_id},),state.exceptions)
    return reconcile_active_window_state(result)

def release_window_placement(state: ActiveWindowState, placement_id: str) -> ActiveWindowState:
    if any(item.placement_id==placement_id for item in state.frozen_placements): raise ValueError("frozen_placement")
    placement=next((item for item in state.provisional_placements if item.placement_id==placement_id),None)
    if placement is None: raise ValueError("unknown_placement")
    if placement.user_fixed: raise ValueError("user_fixed_placement")
    remaining=dict(state.remaining_minutes_by_date); remaining[placement.date]+=placement.minutes
    result=ActiveWindowState(state.window_start,state.window_end,state.frozen_placements,tuple(item for item in state.provisional_placements if item.placement_id!=placement_id),remaining,state.fixed_context,state.weekly_commitment_status,state.frequency_credits,state.rowing_dose_credits,state.last_frozen_quality_date,state.last_frozen_strength_date,state.score_vector,state.audits+({"event":"released","placement_id":placement_id},),state.exceptions)
    return reconcile_active_window_state(result)

def replace_window_placement(state: ActiveWindowState, old_id: str, replacement: WindowPlacement) -> ActiveWindowState:
    return assign_window_placement(release_window_placement(state,old_id),replacement)

def move_user_placement(state: ActiveWindowState, placement_id: str, destination: date, override_id: str, reason: str|None=None) -> ScheduleChangeResult:
    """Atomic explicit-athlete move; ordinary automatic release remains guarded."""
    placement=next((item for item in state.provisional_placements if item.placement_id==placement_id),None)
    if placement is None: return ScheduleChangeResult(False,("unknown_placement",),state=state)
    if destination==placement.date: return ScheduleChangeResult(True,state=state,overrides=())
    remaining=dict(state.remaining_minutes_by_date); remaining[placement.date]+=placement.minutes
    trial=replace(state,provisional_placements=tuple(item for item in state.provisional_placements if item.placement_id!=placement_id),remaining_minutes_by_date=remaining)
    moved=WindowPlacement(placement.placement_id,destination,placement.role,placement.source_id,placement.minutes,False,placement.credits,True,placement.original_date or placement.date,override_id)
    try: result=assign_window_placement(trial,moved)
    except ValueError as error: return ScheduleChangeResult(False,(str(error),),state=state)
    override=UserScheduleOverride(override_id,"move",(placement_id,),(placement.date,),(destination,),reason)
    return ScheduleChangeResult(True,state=result,overrides=(override,))

def swap_user_placements(state: ActiveWindowState, first_id: str, second_id: str, override_id: str, reason: str|None=None) -> ScheduleChangeResult:
    """Atomic explicit-athlete date exchange; never exposes a half-swap."""
    if first_id==second_id: return ScheduleChangeResult(True,state=state)
    items={item.placement_id:item for item in state.provisional_placements}; first=items.get(first_id); second=items.get(second_id)
    if first is None or second is None: return ScheduleChangeResult(False,("unknown_placement",),state=state)
    if first.date==second.date: return ScheduleChangeResult(True,state=state)
    remaining=dict(state.remaining_minutes_by_date); remaining[first.date]+=first.minutes; remaining[second.date]+=second.minutes
    trial=replace(state,provisional_placements=tuple(item for item in state.provisional_placements if item.placement_id not in {first_id,second_id}),remaining_minutes_by_date=remaining)
    moved=lambda item,day: WindowPlacement(item.placement_id,day,item.role,item.source_id,item.minutes,False,item.credits,True,item.original_date or item.date,override_id)
    try:
        result=assign_window_placement(trial,moved(first,second.date)); result=assign_window_placement(result,moved(second,first.date))
    except ValueError as error: return ScheduleChangeResult(False,(str(error),),state=state)
    result=replace(result,provisional_placements=tuple(sorted(result.provisional_placements,key=lambda item:(item.date,item.placement_id,item.role))))
    links=sorted(((first.placement_id,first.date,second.date),(second.placement_id,second.date,first.date)),key=lambda item:item[0])
    override=UserScheduleOverride(override_id,"swap",tuple(item[0] for item in links),tuple(item[1] for item in links),tuple(item[2] for item in links),reason)
    result=replace(result,audits=tuple(item for item in result.audits if item.get("event") not in {"assigned","released"})+({"event":"swap","override_id":override_id},))
    return ScheduleChangeResult(True,state=result,overrides=(override,))

def freeze_leading_half(state: ActiveWindowState, freeze_before: date) -> ActiveWindowState:
    frozen=state.frozen_placements+tuple(WindowPlacement(item.placement_id,item.date,item.role,item.source_id,item.minutes,True,item.credits,item.user_fixed,item.original_date,item.override_id) for item in state.provisional_placements if item.date<freeze_before)
    provisional=tuple(item for item in state.provisional_placements if item.date>=freeze_before)
    result=ActiveWindowState(state.window_start,state.window_end,frozen,provisional,dict(state.remaining_minutes_by_date),state.fixed_context,state.weekly_commitment_status,state.frequency_credits,state.rowing_dose_credits,state.last_frozen_quality_date,state.last_frozen_strength_date,state.score_vector,state.audits+({"event":"frozen","before":freeze_before.isoformat()},),state.exceptions)
    return reconcile_active_window_state(result)

def _strength_target_summary(state, target, horizon_start, horizon_end):
    """Reconcile a rolling target independently of the mutable active window."""
    days=(horizon_end-horizon_start).days+1
    wanted=round(target.target_count*min(days,target.window_days)/target.window_days)
    minimum=round(target.minimum_count*min(days,target.window_days)/target.window_days)
    dates=tuple(sorted({item.date for item in _all_placements(state) if item.role=="strength" and horizon_start<=item.date<=horizon_end}))
    achieved=len(dates)
    status="target_met" if achieved>=wanted else "acceptable_miss" if achieved>=minimum else "below_minimum"
    return {"target":wanted,"minimum":minimum,"maximum":target.maximum_count,"achieved":achieved,"status":status,"dates":dates}

def _dose_target_id(target): return f"{target.phase_id}:{target.category}"

def _dose_target_summary(state, target, horizon_start, horizon_end):
    exposures=minutes=0
    for placement in _all_placements(state):
        if not horizon_start<=placement.date<=horizon_end: continue
        for credit in placement.credits:
            if credit.target_id==_dose_target_id(target): exposures+=credit.exposures; minutes+=credit.minutes
    status="target_met" if exposures>=target.target_exposures and minutes>=target.target_minutes else "acceptable_miss" if exposures>=target.minimum_exposures and minutes>=target.minimum_minutes else "below_minimum"
    return {"target_exposures":target.target_exposures,"achieved_exposures":exposures,"minimum_exposures":target.minimum_exposures,"target_minutes":target.target_minutes,"achieved_minutes":minutes,"minimum_minutes":target.minimum_minutes,"status":status}

def _rowing_credits(target, targets, minutes):
    credits=[TargetCredit(_dose_target_id(target),target.category,1,minutes)]
    # A long aerobic session is dedicated UT2 only when it is explicitly
    # credited to the matching phase target; coached/private activity never is.
    if target.category=="long_aerobic":
        ut2=next((item for item in targets if item.phase_id==target.phase_id and item.category=="dedicated_ut2"),None)
        if ut2: credits.append(TargetCredit(_dose_target_id(ut2),"dedicated_ut2",1,minutes))
    return tuple(credits)

def _solver_score(state, demands, active, target, start, end, strength_preferences, rowing_targets=()):
    """The explicit lexicographic objective used for every retained beam node."""
    reconciled=reconcile_demand_satisfaction(state,demands,end)
    satisfaction=reconciled.demand_satisfaction
    coached_misses=sum(satisfaction[item.demand_id].status not in {"frozen_satisfied","provisional_satisfied"} for item in active if item.type=="coached_training")
    rest_misses=sum(satisfaction[item.demand_id].status not in {"frozen_satisfied","provisional_satisfied"} for item in active if item.type=="rest")
    strength_horizon_start=end-timedelta(days=(target.window_days-1)) if target else start
    strength=_strength_target_summary(state,target,strength_horizon_start,end) if target else {"target":0,"minimum":0,"achieved":0,"status":"target_met","dates":()}
    strength_below=int(strength["achieved"]<strength["minimum"])
    deficit=max(0,strength["target"]-strength["achieved"])
    dates=strength["dates"]
    clustering=sum(1 for left,right in zip(dates,dates[1:]) if (right-left).days==target.minimum_spacing_days+1) if target else 0
    coached_preference=sum(item.placement_date not in demand.preferred_dates for demand_id,item in satisfaction.items() if item.placement_date for demand in active if demand.demand_id==demand_id and demand.type=="coached_training" and demand.preferred_dates)
    strength_preference=sum(_weekday_name(day) not in strength_preferences for day in dates) if strength_preferences else 0
    doses=[(item,_dose_target_summary(reconciled,item,max(item.window_start,end-timedelta(days=item.window_days-1)),min(item.window_end,end))) for item in rowing_targets]
    ut2_deficit=sum(max(0,item.minimum_minutes-summary["achieved_minutes"]) for item,summary in doses if item.category=="dedicated_ut2")
    other_below=sum(summary["status"]=="below_minimum" for item,summary in doses if item.category!="dedicated_ut2")
    rowing_deficit=sum(max(0,item.target_minutes-summary["achieved_minutes"]) for item,summary in doses)
    long_deficit=sum(max(0,item.minimum_minutes-summary["achieved_minutes"]) for item,summary in doses if item.category=="long_aerobic")
    ut1_deficit=sum(max(0,item.minimum_minutes-summary["achieved_minutes"]) for item,summary in doses if item.category=="ut1_aerobic_strength")
    long_preferred=set()
    tie=tuple((item.date.isoformat(),item.role,item.source_id,item.placement_id) for item in sorted(_all_placements(reconciled),key=lambda item:(item.date,item.role,item.source_id,item.placement_id)))
    # Valid quality recovery is a hard primitive constraint, so it never needs
    # a compensating soft score component.
    # Long aerobic is part of the protected aerobic base: when otherwise tied
    # at the required-dose level, retain its coverage before discretionary
    # quality target excess.
    vector=(0,coached_misses,rest_misses,ut2_deficit,strength_below,0,long_deficit,other_below,rowing_deficit,ut1_deficit,deficit,coached_preference,0,strength_preference,tie)
    return vector,strength,reconciled

def _weekday_name(day):
    return ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")[day.weekday()]

def _retain_solver_states(states, demands, active, target, start, end, strength_preferences, rowing_targets=()):
    """Stable dominance pruning.  Equivalent placement sets retain one state."""
    unique={}
    for state in states:
        signature=tuple((item.date,item.role,item.source_id,item.placement_id) for item in sorted(_all_placements(state),key=lambda item:(item.date,item.role,item.source_id,item.placement_id)))
        score,_,_= _solver_score(state,demands,active,target,start,end,strength_preferences,rowing_targets)
        if signature not in unique or score<unique[signature][0]: unique[signature]=(score,state)
    ordered=sorted(unique.values(),key=lambda item:item[0])
    return [item[1] for item in ordered[:BEAM_WIDTH]],max(0,len(ordered)-BEAM_WIDTH)

def _window_diagnostic(state, demands, active, target, start, end, strength_preferences, search, rowing_targets=()):
    score,strength,reconciled=_solver_score(state,demands,active,target,start,end,strength_preferences,rowing_targets)
    strength={**strength,"blockers":() if strength["status"]!="below_minimum" else ("hard_calendar_or_spacing",)}
    def records(kind):
        result=[]
        for demand in active:
            if demand.type!=kind: continue
            item=reconciled.demand_satisfaction[demand.demand_id]
            result.append({"demand_id":demand.demand_id,"canonical_week":demand.canonical_week_start.isoformat(),"eligibility":demand.eligibility,"final_satisfaction_status":item.status,"selected_placement":item.placement_date.isoformat() if item.placement_date else None})
        return result
    doses=[{"target_id":_dose_target_id(item),"category":item.category,"target_horizon_start":max(item.window_start,end-timedelta(days=item.window_days-1)).isoformat(),"target_horizon_end":min(item.window_end,end).isoformat(),**_dose_target_summary(reconciled,item,max(item.window_start,end-timedelta(days=item.window_days-1)),min(item.window_end,end))} for item in rowing_targets]
    return {"window_start":start.isoformat(),"window_end":end.isoformat(),"coached_demands":records("coached_training"),"rest_demands":records("rest"),"strength":{**strength,"dates":[day.isoformat() for day in strength["dates"]]},"rowing_targets":doses,"target":strength["target"],"minimum":strength["minimum"],"maximum":strength["maximum"],"achieved":strength["achieved"],"status":strength["status"],"frozen_placements":[{"placement_id":item.placement_id,"date":item.date.isoformat(),"role":item.role,"source_id":item.source_id} for item in reconciled.frozen_placements],"provisional_placements":[{"placement_id":item.placement_id,"date":item.date.isoformat(),"role":item.role,"source_id":item.source_id} for item in reconciled.provisional_placements],"score_vector":score,"search":search}

def solve_v2_rolling_non_rowing(profile: dict, demand_plan: V2DemandPlan|None=None, calendar: tuple[DateContext,...]|None=None) -> tuple[ActiveWindowState, tuple[dict,...]]:
    """Bounded shared V2.2R-2 solver for coached/rest/strength; no rowing roles.

    Each seven-day advance reconstructs immutable capacity, imports the trailing
    provisional overlap through the normal primitive, then releases it so the
    next window may improve it before the leading half is frozen.
    """
    plan=demand_plan or generate_v2_demand_plan(profile); cal=calendar or build_v2_season_calendar(profile)
    demands=tuple(item for item in plan.weekly_commitment_demands if item.type in {"coached_training","rest"})
    target=next((item for item in plan.frequency_targets if item.group_id=="strength"),None)
    dose_targets=tuple(plan.rowing_dose_targets)
    preferences=set(next((item for item in profile.get("recurring_activities",[]) if item.get("activity_type")=="strength"),{}).get("preferred_days",()))
    first,last=cal[0].date,cal[-1].date; frozen=(); provisional=(); diagnostics=[]; final=None; start=first
    while start<=last:
        window_clock=perf_counter()
        end=min(last,start+timedelta(days=ROLLING_WINDOW_DAYS-1))
        state=initialize_active_window_state(cal,start,end,frozen_history=frozen,provisional_overlap=provisional,canonical_demands=demands)
        # Imported overlap proves capacity/provenance are reconstructed exactly;
        # it remains movable until it enters the frozen leading half.
        for placement in tuple(state.provisional_placements): state=release_window_placement(state,placement.placement_id)
        active=tuple(item for item in demands if item.eligibility=="required" and item.canonical_week_start<=end and item.canonical_week_end>=start)
        active_doses=tuple(item for item in dose_targets if item.window_start<=end and item.window_end>=start)
        states=[state]; explored=hard_rejected=pruned=0
        for demand in sorted(active,key=lambda item:(item.type!="coached_training",item.demand_id)):
            expanded=[]
            for node in states:
                current=reconcile_demand_satisfaction(node,demands,start).demand_satisfaction[demand.demand_id]
                if current.status=="frozen_satisfied": expanded.append(node); continue
                # Keep an unsatisfied branch: diagnostics must distinguish a
                # required miss from an impossible edge exception.
                expanded.append(node)
                for day in candidate_dates_for_demand(demand,cal).candidates:
                    if not start<=day<=end: continue
                    try:
                        expanded.append(assign_window_placement(node,WindowPlacement(f"{demand.demand_id}:{day.isoformat()}",day,demand.type,demand.demand_id,0))); explored+=1
                    except ValueError: hard_rejected+=1
            states,cut=_retain_solver_states(expanded,demands,active,target,start,end,preferences,active_doses); pruned+=cut
        duration=_strength_minutes(profile); candidates=[]
        for node in states:
            frontier=[node]; candidates.append(node)
            for level in range(target.target_count if target else 0):
                expanded=[]
                for item in frontier:
                    for context in cal:
                        if not start<=context.date<=end: continue
                        placement=WindowPlacement(f"strength:{start.isoformat()}:{context.date.isoformat()}",context.date,"strength","strength",duration,False,(TargetCredit("strength","strength",1,duration),))
                        try: expanded.append(assign_window_placement(item,placement)); explored+=1
                        except ValueError: hard_rejected+=1
                frontier,cut=_retain_solver_states(expanded,demands,active,target,start,end,preferences,active_doses) if expanded else ([],0); pruned+=cut
                candidates.extend(frontier)
                if not frontier: break
        # Generic rowing roles use the same immutable state, compatibility, and
        # reversible placement primitives as every non-rowing role.
        rowing_frontier=candidates
        for dose in sorted(active_doses,key=lambda item:({"dedicated_ut2":0,"long_aerobic":1,"ut1_aerobic_strength":2,"quality":3}.get(item.category,9),item.phase_id,item.category)):
            expanded=list(rowing_frontier)
            minutes=max(1,round(dose.target_minutes/max(1,dose.target_exposures)))
            for node in rowing_frontier:
                frontier=[node]
                for _ in range(dose.target_exposures):
                    next_frontier=[]
                    for item in frontier:
                        for context in cal:
                            if not start<=context.date<=end or not dose.window_start<=context.date<=dose.window_end: continue
                            placement=WindowPlacement(f"rowing:{_dose_target_id(dose)}:{context.date.isoformat()}",context.date,dose.category,_dose_target_id(dose),minutes,False,_rowing_credits(dose,active_doses,minutes))
                            try: next_frontier.append(assign_window_placement(item,placement)); explored+=1
                            except ValueError: hard_rejected+=1
                    frontier,cut=_retain_solver_states(next_frontier,demands,active,target,start,end,preferences,active_doses) if next_frontier else ([],0); pruned+=cut
                    expanded.extend(frontier)
                    if not frontier: break
            rowing_frontier,cut=_retain_solver_states(expanded,demands,active,target,start,end,preferences,active_doses); pruned+=cut
        retained,cut=_retain_solver_states(rowing_frontier,demands,active,target,start,end,preferences,active_doses); pruned+=cut
        final=retained[0]
        search={"beam_width":BEAM_WIDTH,"states_explored":explored,"states_retained":len(retained),"hard_rejected":hard_rejected,"dominance_pruned":pruned,"runtime_ms":round((perf_counter()-window_clock)*1000,3)}
        diagnostics.append(_window_diagnostic(final,demands,active,target,start,end,preferences,search,active_doses))
        frozen_state=freeze_leading_half(final,start+timedelta(days=ROLLING_OVERLAP_DAYS))
        frozen=frozen_state.frozen_placements; provisional=frozen_state.provisional_placements
        start+=timedelta(days=ROLLING_OVERLAP_DAYS)
    return reconcile_demand_satisfaction(final,demands,last),tuple(diagnostics)

def _strength_minutes(profile):
    activity=next((item for item in profile.get("recurring_activities",[]) if item.get("activity_type")=="strength"),{})
    legacy={item.get("weekday"):item for item in profile.get("weekly_availability",[])}
    return int(activity.get("duration_minutes") or activity.get("typical_strength_minutes") or next((item.get("lifting_minutes",0) for item in legacy.values() if item.get("lifting_minutes")),0) or 60)

def place_v2_non_rowing(profile: dict, demand_plan: V2DemandPlan|None=None, calendar: tuple[DateContext,...]|None=None) -> RollingPlacementResult:
    """Compatibility view of the shared solver; it contains no second engine."""
    final,diagnostics=solve_v2_rolling_non_rowing(profile,demand_plan,calendar)
    placements=[]
    for item in sorted(_all_placements(final),key=lambda item:(item.date,item.placement_id)):
        placements.append((item.placement_id if item.role in {"strength","dedicated_ut2","long_aerobic","ut1_aerobic_strength","quality"} else item.source_id,item.date))
    misses=[]
    for diagnostic in diagnostics:
        strength=diagnostic["strength"]
        if strength["status"]!="target_met": misses.append({"group":"strength","window_start":diagnostic["window_start"],"window_end":diagnostic["window_end"],**strength,"blockers":["hard_calendar_or_spacing"]})
    explored=sum(item["search"]["states_explored"] for item in diagnostics)
    retained=max((item["search"]["states_retained"] for item in diagnostics),default=0)
    stable_audits=tuple({**item,"search":{key:value for key,value in item["search"].items() if key!="runtime_ms"}} for item in diagnostics)
    return RollingPlacementResult(tuple(placements),stable_audits,tuple(misses),BEAM_WIDTH,explored,retained)

def place_v2_rowing(profile: dict, demand_plan: V2DemandPlan|None=None, calendar: tuple[DateContext,...]|None=None, non_rowing: RollingPlacementResult|None=None) -> RollingPlacementResult:
    """Historical compatibility alias for the shared generic-role solver."""
    return place_v2_non_rowing(profile,demand_plan,calendar)
