"""Deterministic Step 5 post-instantiation load transformations."""
from __future__ import annotations

VERSION="load-transformation-0.1.0"

def transform(session: dict, *, phase: str, race_priority: str | None=None) -> dict:
    """Reduce quantity, never inflate intensity; retain provenance and reasons."""
    if session.get("session_id") == "RACE": return session
    if session.get("session_id") == "LIFT":
        if phase != "taper_sharpen": return session
        state="reduced-load" if race_priority=="A" else "maintenance" if race_priority=="B" else "heavy"
        return {**session,"title":f"{state.title()} strength","strength_state":state,"load_transformation":{"transformation_type":"strength_taper","original_archetype_id":None,"original_work_minutes":0,"final_work_minutes":0,"volume_factor":1,"frequency_preserved":True,"primary_band_preserved":True,"race_rate_preserved":False,"changed_parameters":["strength_fatigue_category"],"reason_codes":[f"{race_priority or 'A'}_RACE_STRENGTH"],"athlete_explanation":f"Strength is {state} to reduce race-week fatigue.","source_ids":["S017","S009"],"algorithm_version":VERSION}}
    if session.get("session_id") == "COACHED": return session
    role=session.get("session_role",""); band=session.get("band","")
    if phase not in {"taper_sharpen","race_recovery"}: return session
    high=band in {"AT","TR","AN","PP"}; factor=.72 if high else .62
    original=session.get("rowing_minutes",0); final=max(20,round(original*factor))
    record={"transformation_type":"taper" if phase=="taper_sharpen" else "post_race_recovery","original_archetype_id":session.get("archetype_id"),"original_work_minutes":original,"final_work_minutes":final,"volume_factor":round(final/original,2) if original else 1,"frequency_preserved":True,"primary_band_preserved":True,"race_rate_preserved":high,"changed_parameters":["total_work_duration","repetition_count"] if final<original else [],"reason_codes":[f"{race_priority or 'A'}_RACE_TAPER","REDUCE_ACCUMULATED_FATIGUE"]+(["RETAIN_RACE_SPECIFICITY"] if high else []),"athlete_explanation":"This session keeps its intended technical or race-specific focus while reducing accumulated fatigue.","source_ids":["S009","S010"],"algorithm_version":VERSION}
    return {**session,"original_structure":session.get("structure"),"total_cardio_minutes":final,"rowing_minutes":final,"quality_minutes":final if high else 0,"load_transformation":record,"transformation_reason":record["athlete_explanation"]}
