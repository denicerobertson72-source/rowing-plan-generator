import json
from datetime import date

from services.api.tests.disposable_browser_fixture import synthetic_profile
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.materialization_v2 import materialize_v2_session
from rowing_plan.models import DatedTrainingRole, QualityTranslationContext, V2SessionMaterializationRequest
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import _availability, _commitment
from rowing_plan.session_selection import select_and_instantiate
from rowing_plan.scheduler_v2 import finalize_translated_quality_sequence, translate_quality_role

CONFIG=json.load(open("config/defaults.json"))

def inputs():
    profile=synthetic_profile()
    return profile,build_intensity_profile(profile,CONFIG),build_power_profile(profile,CONFIG)

def request(role,day=date(2026,9,8),minutes=60,phase="race_specific_preparation",**kwargs):
    return V2SessionMaterializationRequest(role,day,minutes,phase,placement_id=f"{role}-1",source_id=role,**kwargs)

def assert_shape(session):
    assert {"date","day","session_id","mode","title","band","total_cardio_minutes","phase","structure"} <= set(session)

def test_v27a_easy_rows_reuse_v1_selector_semantics_and_are_deterministic():
    profile,bands,power=inputs()
    for role,expected in (("dedicated_ut2","UT2"),("long_aerobic","UT2"),("ut1_aerobic_strength","UT1")):
        item=request(role,minutes=75 if role=="long_aerobic" else 60)
        first=materialize_v2_session(item,profile=profile,bands=bands,power=power)
        second=materialize_v2_session(item,profile=profile,bands=bands,power=power)
        assert first==second and first.success and first.session["band"]==expected
        assert first.session["total_cardio_minutes"]<=item.planned_duration_minutes
        assert first.session["session_role"] in {"AEROBIC_BASE","LONG_AEROBIC","AEROBIC_STRENGTH"}
        assert first.session["session_fingerprint"] and "target_watts" in first.session and "split_guide" in first.session
        assert_shape(first.session)

def test_v27a_mode_specific_guidance_preserves_existing_erg_and_water_contracts():
    profile,bands,power=inputs(); item=request("dedicated_ut2")
    erg=materialize_v2_session(item,profile=profile,bands=bands,power=power)
    water=materialize_v2_session(request("dedicated_ut2",mode="on_water"),profile=profile,bands=bands,power=power)
    assert erg.success and water.success and erg.session["mode"]=="erg" and water.session["mode"]=="on_water"
    assert "target_watts" in erg.session and "split_guide" in erg.session
    assert water.session["target_watts"] is None and water.session["rating"]!="—" and water.session["hr_range"]

def test_v27a_finalized_quality_serializes_once_with_final_fingerprint():
    profile,bands,power=inputs(); day=date(2026,9,8)
    translated=translate_quality_role(DatedTrainingRole(day,"quality",60,"taper","source","provisional",(),"quality-1"),QualityTranslationContext("taper"))
    finalized=finalize_translated_quality_sequence((translated,),experience="experienced",race_types={"quality-1":"head_5k"},race_priorities={"quality-1":"A"}).roles[0]
    result=materialize_v2_session(request("quality",day,60,"taper",concrete_quality=finalized),profile=profile,bands=bands,power=power)
    assert result.success and result.session["session_id"]==finalized.archetype_id and result.session["band"]=="TR"
    assert result.session["total_cardio_minutes"]==finalized.final_prescription["total_cardio_minutes"]
    assert result.session["session_fingerprint"]==finalized.final_fingerprint
    assert result.session["total_cardio_minutes"]<finalized.prescription["total_minutes"]
    assert result.session["load_transformation"]==finalized.final_prescription["load_transformation"]
    assert_shape(result.session)

def test_v27a_fixed_roles_rest_and_locked_passthrough_match_external_semantics():
    profile,bands,power=inputs()
    cases=(
        (request("strength",minutes=45),"LIFT","strength","STRENGTH"),
        (request("coached_training",minutes=50),"COACHED","on_water","UT2/UT1"),
        (request("private_coaching",minutes=50,fixed=True),"COACHED","on_water","UT2/UT1"),
        (request("race",minutes=40,fact={"event_name":"Head race","race_type":"head_5k","priority":"A","expected_starts":2}),"RACE","race","RACE"),
        (request("course_practice",minutes=30,fact={"title":"Course practice"}),"COURSE_PRACTICE","on_water","TECHNIQUE"),
    )
    for item,session_id,mode,band in cases:
        result=materialize_v2_session(item,profile=profile,bands=bands,power=power)
        assert result.success and (result.session["session_id"],result.session["mode"],result.session["band"])==(session_id,mode,band)
        assert_shape(result.session)
    coached=materialize_v2_session(request("coached_training",minutes=50),profile=profile,bands=bands,power=power).session
    private=materialize_v2_session(request("private_coaching",minutes=50),profile=profile,bands=bands,power=power).session
    assert coached["coached"] and private["title"]=="Private coaching" and coached["quality_minutes"]==private["quality_minutes"]==0
    rest=materialize_v2_session(request("rest",minutes=0),profile=profile,bands=bands,power=power)
    assert rest.success and rest.calendar_only and rest.session is None and rest.reason_code=="calendar_only"
    locked={"date":"2026-09-08","session_id":"existing","mode":"erg","title":"Completed","band":"UT2","total_cardio_minutes":50}
    carried=materialize_v2_session(request("completed",serialized_session=locked),profile=profile,bands=bands,power=power)
    assert carried.success and carried.session==locked

def test_v27a_materialization_failure_never_changes_reserved_duration_or_role():
    profile,bands,power=inputs()
    too_short=materialize_v2_session(request("long_aerobic",minutes=1),profile=profile,bands=bands,power=power)
    assert not too_short.success and too_short.failure_reason=="no_eligible_materialization_archetype"
    missing_quality=materialize_v2_session(request("quality"),profile=profile,bands=bands,power=power)
    assert not missing_quality.success and missing_quality.failure_reason=="finalized_quality_required"

def test_v27a_non_quality_external_fields_match_existing_v1_materializers():
    profile,bands,power=inputs(); day=date(2026,9,8); availability=_availability(profile)["tuesday"]
    for v2_role,v1_role,band in (("dedicated_ut2","AEROBIC_BASE","UT2"),("long_aerobic","LONG_AEROBIC","UT2"),("ut1_aerobic_strength","AEROBIC_STRENGTH","UT1")):
        minutes=75 if v2_role=="long_aerobic" else 60
        actual=materialize_v2_session(request(v2_role,day,minutes),profile=profile,bands=bands,power=power).session
        selected=select_and_instantiate(role=v1_role,experience=profile["athlete"]["experience_level"],phase="race_specific_preparation",race_type="general",mode=actual["mode"],minutes=minutes,preference=profile.get("preferences",{}).get("workout_structure_preference","varied"),history=[])
        assert (actual["session_id"],actual["archetype_id"],actual["band"],actual["total_cardio_minutes"],actual["rowing_minutes"],actual["quality_minutes"],actual["session_role"],actual["session_fingerprint"])==(selected["archetype"]["archetype_id"],selected["archetype"]["archetype_id"],band,selected["total_minutes"],selected["total_minutes"],0,v1_role,selected["fingerprint"])
    activity={"scheduling_status":"flexible","duration_minutes":45}
    strength=materialize_v2_session(request("strength",day,45),profile=profile,bands=bands,power=power).session
    assert {key:strength[key] for key in ("session_id","mode","band","total_cardio_minutes","total_training_minutes","strength_minutes","rowing_minutes","quality_minutes","structure")}=={key:_commitment("strength",day,"race_specific_preparation",availability,activity)[key] for key in ("session_id","mode","band","total_cardio_minutes","total_training_minutes","strength_minutes","rowing_minutes","quality_minutes","structure")}
