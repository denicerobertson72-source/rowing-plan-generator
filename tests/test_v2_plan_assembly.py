import json
from datetime import date, timedelta

from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.models import DateContext, DatedTrainingRole, QualityTranslationContext, V2SessionMaterializationRequest
from rowing_plan.plan_assembly_v2 import build_plan_version_from_v2
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler_v2 import finalize_translated_quality_sequence, translate_quality_role

CONFIG=json.load(open("config/defaults.json"))

def inputs():
    profile=synthetic_profile(); return profile,build_intensity_profile(profile,CONFIG),build_power_profile(profile,CONFIG)

def calendar(start=date(2026,9,7),days=10):
    return tuple(DateContext(start+timedelta(days=index),(start+timedelta(days=index)).strftime("%A"),"taper" if index>=5 else "race_specific_preparation",True,90,0,90) for index in range(days))

def req(role,day,minutes=50,phase="race_specific_preparation",**kwargs):
    return V2SessionMaterializationRequest(role,day,minutes,phase,placement_id=f"{role}-{day}",source_id=role,**kwargs)

def quality(day):
    translated=translate_quality_role(DatedTrainingRole(day,"quality",60,"taper","quality","provisional",(),"quality-final"),QualityTranslationContext("taper"))
    return finalize_translated_quality_sequence((translated,),experience="experienced",race_types={"quality-final":"head_5k"},race_priorities={"quality-final":"A"}).roles[0]

def test_v27b_assembly_materializes_complete_calendar_without_rest_session():
    profile,bands,power=inputs(); days=calendar(); q=quality(date(2026,9,12))
    requests=(req("dedicated_ut2",date(2026,9,7),60),req("long_aerobic",date(2026,9,8),75),req("ut1_aerobic_strength",date(2026,9,9),60),req("strength",date(2026,9,10),45),req("coached_training",date(2026,9,11),50),req("quality",date(2026,9,12),60,"taper",concrete_quality=q),req("private_coaching",date(2026,9,13),50,fixed=True),req("course_practice",date(2026,9,14),30,fact={"title":"Course practice"}),req("race",date(2026,9,15),20,fact={"event_name":"Head race","race_type":"head_5k","priority":"A"}),req("rest",date(2026,9,16),0,"taper"))
    first=build_plan_version_from_v2(profile=profile,bands=bands,power=power,calendar=days,requests=requests,rest_dates=(date(2026,9,16),))
    second=build_plan_version_from_v2(profile=profile,bands=bands,power=power,calendar=days,requests=tuple(reversed(requests)),rest_dates=(date(2026,9,16),))
    assert first==second and first.success
    plan=first.plan; ids=[item["session_id"] for item in plan["sessions"]]
    assert {"LIFT","COACHED","COURSE_PRACTICE","RACE",q.archetype_id} <= set(ids) and "REST" not in ids
    assert next(item for item in plan["calendar_days"] if item["date"]=="2026-09-16")["state"]=="designated_rest"
    serialized=next(item for item in plan["sessions"] if item["session_id"]==q.archetype_id)
    assert serialized["total_cardio_minutes"]==q.final_prescription["total_cardio_minutes"] and serialized["session_fingerprint"]==q.final_fingerprint
    assert len({(item["date"],item["session_id"],item["mode"]) for item in plan["sessions"]})==len(plan["sessions"])

def test_v27b_authoritative_sessions_win_and_identity_is_preserved():
    profile,bands,power=inputs(); days=calendar(); original={"date":"2026-09-08","day":"Tuesday","phase":"authoritative","fixed":True,"mode":"erg","session_id":"old-row","title":"Completed original","band":"UT2","total_cardio_minutes":44,"rowing_minutes":44,"quality_minutes":0,"structure":"Original prescription."}
    result=build_plan_version_from_v2(profile=profile,bands=bands,power=power,calendar=days,requests=(req("dedicated_ut2",date(2026,9,8),60),),authoritative_sessions=(original,))
    assert result.success and result.plan["sessions"]==[original]
    assert f'{original["date"]}:{original["session_id"]}:{original["mode"]}'=="2026-09-08:old-row:erg"

def test_v27b_rejects_rest_conflicts_and_duplicate_external_keys():
    profile,bands,power=inputs(); days=calendar()
    rest=build_plan_version_from_v2(profile=profile,bands=bands,power=power,calendar=days,requests=(req("strength",date(2026,9,9),45),),rest_dates=(date(2026,9,9),))
    assert not rest.success and rest.failure_reason=="rest_session_conflict"
    duplicate=build_plan_version_from_v2(profile=profile,bands=bands,power=power,calendar=days,requests=(req("strength",date(2026,9,9),45),req("strength",date(2026,9,9),45)))
    assert not duplicate.success and duplicate.failure_reason=="duplicate_external_session_key"

def test_v27b_complete_shape_totals_and_diagnostics_use_serialized_sessions():
    profile,bands,power=inputs(); days=calendar(); completed={"date":"2026-09-08","day":"Tuesday","phase":"authoritative","fixed":True,"mode":"erg","session_id":"old-row","title":"Completed","band":"UT2","total_cardio_minutes":44,"rowing_minutes":44,"quality_minutes":0,"structure":"Original."}
    diagnostics={"demand_satisfaction":[{"status":"frozen_satisfied"}],"rolling_targets":[{"target_horizon_start":date(2026,9,7)}],"repair_changes":[{"kind":"moved","placement_id":"x"}],"target_consequences":[{"after_status":"below_minimum"}],"overrides":[{"override_id":"move-1"}]}
    result=build_plan_version_from_v2(profile=profile,bands=bands,power=power,calendar=days,requests=(req("strength",date(2026,9,7),45),req("dedicated_ut2",date(2026,9,8),60)),authoritative_sessions=(completed,),generated_at="2026-09-01T00:00:00",v2_diagnostics=diagnostics)
    assert result.success and {"plan_version","profile_id","generated_at","intensity_profile","power_profile","sessions","calendar_days","phases","weekly_totals","warnings","plan_impacts","v2_diagnostics"} <= set(result.plan)
    total=next(item for item in result.plan["weekly_totals"] if item["week"]==37)
    assert total["cardio_minutes"]==44 and total["rowing_minutes"]==44 and total["strength_sessions"]==1 and total["quality_sessions"]==0
    assert result.plan["v2_diagnostics"]["rolling_targets"][0]["target_horizon_start"]=="2026-09-07"
    assert json.dumps(result.plan)
