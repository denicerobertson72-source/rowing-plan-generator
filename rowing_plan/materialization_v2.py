"""Pure V2 role-to-session materialization; it does not build a PlanVersion."""
from __future__ import annotations

from .conversions import format_split, watts_to_split_seconds
from .models import V2SessionMaterializationRequest, V2SessionMaterializationResult
from .power_profile import target_for_band
from .session_selection import select_and_instantiate

_SELECTOR_ROLES={"dedicated_ut2":"AEROBIC_BASE","long_aerobic":"LONG_AEROBIC","ut1_aerobic_strength":"AEROBIC_STRENGTH"}

def _mode(profile: dict, day, requested: str|None) -> str:
    if requested: return requested
    availability=next((item for item in profile.get("weekly_availability",[]) if item.get("weekday")==day.strftime("%A").lower()),{})
    return next((item for item in availability.get("rowing_modes",[]) if item in {"erg","on_water"}),"erg")

def _guidance(session: dict, bands, power: dict, *, mode: str, rate=None) -> dict:
    band_map={item["name"]:item for item in bands}
    matching=[band_map[item] for item in session.get("band","").split("/") if item in band_map]
    lows=[item["hr_low"] for item in matching if item.get("hr_low") is not None]
    highs=[item["hr_high"] for item in matching if item.get("hr_high") is not None]
    result={**session,"hr_range":f"{min(lows)}–{max(highs)} bpm" if lows and highs else "Use breathing / RPE","rating":f"{rate['min']}–{rate['max']} spm" if rate else "—","coached":session.get("session_id")=="COACHED"}
    anchor=target_for_band(power,session.get("band","")) if mode=="erg" else None
    if not anchor:
        return {**result,"power_target_method":"Intensity provider / HRR-RPE guidance","source_anchor":None,"target_watts":None,"split_guide":None,"confidence":"low","assumptions":["Follow rate, breathing, and RPE where exact power is unavailable."]}
    watts=round((anchor["target_watts_low"]+anchor["target_watts_high"])/2,1)
    return {**result,"power_target_method":anchor["formula"],"source_anchor":anchor["source_test"],"target_watts":watts,"split_guide":format_split(watts_to_split_seconds(watts)),"confidence":anchor["confidence"],"assumptions":anchor["assumptions"]}

def _row_session(request: V2SessionMaterializationRequest, profile: dict, bands, power: dict, history) -> V2SessionMaterializationResult:
    selector_role=_SELECTOR_ROLES[request.role]; mode=_mode(profile,request.date,request.mode)
    selected=select_and_instantiate(role=selector_role,experience=profile.get("athlete",{}).get("experience_level","intermediate"),phase=request.phase_id,race_type=request.race_type,mode=mode,minutes=request.planned_duration_minutes,preference=profile.get("preferences",{}).get("workout_structure_preference","varied"),history=list(history))
    if not selected: return V2SessionMaterializationResult(False,failure_reason="no_eligible_materialization_archetype")
    if selected["total_minutes"]>request.planned_duration_minutes: return V2SessionMaterializationResult(False,failure_reason="concrete_duration_exceeds_reserved_capacity")
    archetype=selected["archetype"]; band=archetype["primary_band"]
    session={"date":request.date.isoformat(),"day":request.date.strftime("%A"),"phase":request.phase_id,"fixed":request.fixed,"mode":mode,"session_id":archetype["archetype_id"],"archetype_id":archetype["archetype_id"],"title":archetype["name"],"total_cardio_minutes":selected["total_minutes"],"rowing_minutes":selected["total_minutes"],"quality_minutes":0,"band":band,"structure":f"{selected['repetitions']} × {selected['work_interval_duration']} min {band}; {selected['recovery_duration']} min easy recovery.","modeled_overhead_minutes":12,"recovery":archetype["minimum_recovery_guidance"],"technical_cue":"Maintain posture and connection as the session develops.","rate_guide":archetype["rate_range_spm"],"source_basis_ids":archetype["source_ids"],"session_role":selector_role,"phase_role":request.phase_id,"progression_dimension":selected["progression_dimension"],"selection_reason":selected["selection_reason"],"preference_effect":selected["preference_effect"],"selection_reason_codes":["v2_role_materialization","deterministic_archetype_selection"],"candidate_scores":selected["candidate_scores"],"session_fingerprint":selected["fingerprint"]}
    session=_guidance(session,bands,power,mode=mode,rate=archetype["rate_range_spm"])
    session["description"]=f"{session['title']}. {session['structure']}"
    return V2SessionMaterializationResult(True,session)

def _quality_session(request: V2SessionMaterializationRequest, profile: dict, bands, power: dict) -> V2SessionMaterializationResult:
    concrete=request.concrete_quality
    if concrete is None or concrete.final_prescription is None or concrete.final_fingerprint is None:
        return V2SessionMaterializationResult(False,failure_reason="finalized_quality_required")
    if concrete.date!=request.date or concrete.quality_type!=concrete.physiological_band:
        return V2SessionMaterializationResult(False,failure_reason="quality_identity_mismatch")
    final=concrete.final_prescription; mode=_mode(profile,request.date,request.mode); archetype=concrete.prescription["archetype"]
    minutes=int(final["total_cardio_minutes"])
    if minutes>request.planned_duration_minutes: return V2SessionMaterializationResult(False,failure_reason="concrete_duration_exceeds_reserved_capacity")
    session={"date":request.date.isoformat(),"day":request.date.strftime("%A"),"phase":concrete.phase_id,"fixed":request.fixed,"mode":mode,"session_id":concrete.archetype_id,"archetype_id":concrete.archetype_id,"title":archetype["name"],"total_cardio_minutes":minutes,"rowing_minutes":int(final.get("rowing_minutes",minutes)),"quality_minutes":int(final.get("quality_minutes",minutes)),"band":concrete.physiological_band,"structure":final.get("structure",""),"modeled_overhead_minutes":final.get("modeled_overhead_minutes",12),"modeled_cooldown_minutes":final.get("modeled_cooldown_minutes",0),"recovery":archetype["minimum_recovery_guidance"],"technical_cue":"Maintain posture and connection as the session develops.","rate_guide":archetype["rate_range_spm"],"source_basis_ids":archetype["source_ids"],"session_role":concrete.selector_role,"phase_role":concrete.phase_id,"progression_dimension":concrete.prescription["progression_dimension"],"selection_reason":concrete.prescription["selection_reason"],"preference_effect":concrete.prescription["preference_effect"],"selection_reason_codes":["v2_finalized_quality"],"session_fingerprint":dict(concrete.final_fingerprint)}
    session=_guidance(session,bands,power,mode=mode,rate=archetype["rate_range_spm"])
    session.update({key:final[key] for key in ("original_structure","load_transformation","transformation_reason") if key in final})
    session["description"]=f"{session['title']}. {session['structure']}"
    return V2SessionMaterializationResult(True,session)

def materialize_v2_session(request: V2SessionMaterializationRequest, *, profile: dict, bands, power: dict, selector_history=()) -> V2SessionMaterializationResult:
    """Return one compatible external session dictionary, never a full plan."""
    if request.role=="rest": return V2SessionMaterializationResult(True,calendar_only=True,reason_code="calendar_only")
    if request.role in {"locked","completed"}:
        return V2SessionMaterializationResult(bool(request.serialized_session),session=dict(request.serialized_session or {}) or None,failure_reason=None if request.serialized_session else "serialized_session_required")
    if request.role in _SELECTOR_ROLES: return _row_session(request,profile,bands,power,selector_history)
    if request.role=="quality": return _quality_session(request,profile,bands,power)
    base={"date":request.date.isoformat(),"day":request.date.strftime("%A"),"phase":request.phase_id,"fixed":request.fixed}
    if request.role=="strength":
        minutes=request.planned_duration_minutes; session={**base,"mode":"strength","session_id":"LIFT","title":"Heavy lifting","total_cardio_minutes":0,"total_training_minutes":minutes,"strength_minutes":minutes,"rowing_minutes":0,"quality_minutes":0,"band":"STRENGTH","structure":"Scheduled strength commitment."}
    elif request.role in {"coached_training","private_coaching"}:
        title="Private coaching" if request.role=="private_coaching" else "Coached row"; minutes=request.planned_duration_minutes
        session={**base,"mode":"on_water","session_id":"COACHED","title":title,"total_cardio_minutes":minutes,"rowing_minutes":minutes,"quality_minutes":0,"band":"UT2/UT1","structure":"Coach-led technique and aerobic work.","warning":"Coach instructions take priority.","coached":True}
    elif request.role=="race":
        fact=dict(request.fact or {}); minutes=request.planned_duration_minutes or int(fact.get("expected_starts",1))*20
        session={**base,"phase":"race","fixed":True,"mode":"race","session_id":"RACE","title":fact.get("event_name","Race"),"total_cardio_minutes":minutes,"rowing_minutes":minutes,"quality_minutes":minutes,"band":"RACE","structure":"Race day; no ordinary training.","race_distance":fact.get("race_type"),"race_priority":fact.get("priority"),"expected_starts":int(fact.get("expected_starts",1)),"warmup_guidance":"Use the athlete's practiced race warm-up.","cooldown_guidance":"Easy movement and recovery between starts.","warning":None}
    elif request.role=="course_practice":
        fact=dict(request.fact or {}); minutes=request.planned_duration_minutes or int(fact.get("duration_minutes",30))
        session={**base,"fixed":True,"mode":"on_water","session_id":"COURSE_PRACTICE","title":fact.get("title") or "Course practice","total_cardio_minutes":minutes,"rowing_minutes":minutes,"quality_minutes":0,"band":"TECHNIQUE","structure":fact.get("notes") or "Course familiarization; keep the load controlled and preserve race readiness.","warning":"Athlete/coach-directed familiarization; not counted as race load.","optional_add_on":False,"race_event_practice":True}
    else: return V2SessionMaterializationResult(False,failure_reason="unsupported_v2_role")
    session=_guidance(session,bands,power,mode=session["mode"])
    session["description"]=f"{session['title']}. {session['structure']}"
    return V2SessionMaterializationResult(True,session)
