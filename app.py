"""Streamlit UI for the local-first Rowing Plan Generator MVP."""
from __future__ import annotations
import json
import csv
from io import StringIO
from pathlib import Path
from datetime import date, timedelta
import streamlit as st
from rowing_plan.intensity import build_intensity_profile
from rowing_plan.power_profile import build_power_profile
from rowing_plan.scheduler import generate_plan
from rowing_plan.validators import validate_profile, hard_constraint_errors
from rowing_plan.persistence import dump_profile, load_profile, safe_filename
from rowing_plan.workbook import build_workbook

ROOT=Path(__file__).parent
CONFIG=json.loads((ROOT/"config/defaults.json").read_text())
SAMPLE=json.loads((ROOT/"data/sample_athlete.json").read_text())
def blank_profile():
    weekdays=["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    return {"profile_version":"0.3","athlete":{"display_name":"","age_band":"30-39","experience_level":"intermediate","boat_classes":[],"medical_review_required":False,"coach_review_requested":False},"season":{"start_date":date.today().isoformat(),"end_date":(date.today()+timedelta(days=120)).isoformat(),"current_weekly_endurance_minutes":0,"target_peak_weekly_endurance_minutes":0},"goals":[],"tests":{"multi_duration_power_tests":{},"testing_blocks":[],"power_profile_settings":{"mode":"anchors_only","allow_provisional_low_intensity_watts_from_sustained_test":False}},"weekly_availability":[{"weekday":day,"available":False,"fixed_rest":False,"max_training_minutes":60,"max_sessions":1,"heavy_lifting":False,"lifting_minutes":0,"alternate_ut2_allowed":False,"alternate_ut2_modes":["elliptical"],"row_on_lifting_day":True,"fixed_coached_row":False,"expected_coached_intensity":"unknown","rowing_modes":[],"notes":""} for day in weekdays],"races":[],"preferences":{"terminology":"UT","erg_primary_display":"both","broken_aerobic_preferred":True,"fixed_rest_weekdays":[],"workbook_detail_level":"detailed","include_sources_sheet":True},"locked_weeks":[]}
def get_profile():
    if "profile" not in st.session_state: st.session_state.profile=blank_profile()
    return st.session_state.profile
def set_nested(obj,path,value):
    for part in path[:-1]: obj=obj.setdefault(part,{})
    obj[path[-1]]=value
def source_rows():
    import csv
    with (ROOT/"sources/source_register.csv").open() as f:
        return [[r.get("source_id",""),f"{r.get('author_or_org','')}: {r.get('title','')}",r.get("url",""),r.get("license_or_access",""),r.get("caution","")] for r in csv.DictReader(f)]
def two_k_parts(seconds):
    seconds=int(seconds or 0)
    return seconds // 60, seconds % 60
def mode_index(item):
    modes=item.get("rowing_modes") or ["on_water"]
    return 2 if len(modes)>1 else (0 if modes[0]=="on_water" else 1)
def schedule_csv(rows):
    output=StringIO()
    writer=csv.DictWriter(output,fieldnames=list(rows[0]) if rows else [])
    if rows:
        writer.writeheader()
        writer.writerows(rows)
    return output.getvalue().encode("utf-8")
def section_intro(title, text, tone="teal"):
    st.markdown(f'<div class="section-intro {tone}"><div class="section-title">{title}</div><div>{text}</div></div>', unsafe_allow_html=True)

st.set_page_config(page_title="Rowing Plan Generator",layout="wide")
st.markdown("""
<style>
    :root {
        --teal: #087E8B;
        --turquoise: #15B8A6;
        --leaf: #4C9A2A;
        --lavender: #E9E1F7;
        --ink: #173B42;
        --mist: #F4FBFA;
    }
    .stApp {
        background: radial-gradient(circle at 4% 0%, #c8f1eb 0, transparent 34%),
                    radial-gradient(circle at 98% 2%, #dfd2f5 0, transparent 32%),
                    linear-gradient(120deg, #f3fcfb 0%, #fbfaff 56%, #f7f2fc 100%);
        color: var(--ink);
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0a6873 0%, #087e8b 55%, #397f53 100%);
    }
    [data-testid="stSidebar"] * { color: #f8ffff; }
    [data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] { background: rgba(255,255,255,.12); border-color: rgba(255,255,255,.5); }
    [data-testid="stSidebar"] button,
    [data-testid="stSidebar"] button * {
        color: #000000 !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        background: #f3edfc;
        color: #000000 !important;
        border: 1px solid #d1c2ec;
    }
    [data-testid="stSidebar"] .stDownloadButton > button {
        background: #e3f6f1;
        color: #000000 !important;
        border: 1px solid #a8dbd1;
    }
    h1 { color: #076875; letter-spacing: -.035em; font-weight: 750; }
    h2, h3 { color: #226876; }
    .stTabs [data-baseweb="tab-list"] { gap: .35rem; border-bottom: 2px solid #cce8e4; }
    .stTabs [data-baseweb="tab"] { border-radius: 12px 12px 0 0; color: #35666b; font-weight: 600; padding: .55rem .9rem; }
    .stTabs [aria-selected="true"] { background: #e6f6f4; color: #087e8b; border-bottom-color: #087e8b; }
    .stButton > button, .stDownloadButton > button {
        border-radius: 9px; border: 0; font-weight: 650; transition: transform .15s ease, box-shadow .15s ease;
    }
    .stButton > button[kind="primary"] { background: linear-gradient(135deg, #087e8b, #15a99c); color: white; box-shadow: 0 4px 12px rgba(8,126,139,.25); }
    .stDownloadButton > button { background: #e9e1f7; color: #4e3b70; }
    .stButton > button:hover, .stDownloadButton > button:hover { transform: translateY(-1px); box-shadow: 0 5px 14px rgba(28,76,81,.18); }
    [data-testid="stExpander"] { border: 1px solid #cce8e4; border-radius: 12px; background: rgba(255,255,255,.82); }
    [data-testid="stAlert"] { border-radius: 10px; }
    [data-testid="stDataFrame"] { border: 1px solid #d8eae7; border-radius: 12px; overflow: hidden; }
    [data-testid="stMetric"] { background: #f1f9f7; border-left: 4px solid #4c9a2a; border-radius: 10px; padding: .5rem; }
    div[data-testid="stForm"] { background: rgba(255,255,255,.78); border: 1px solid #d4ebe7; border-radius: 14px; padding: 1rem; }
    [data-testid="stWidgetLabel"] p { color: #17606a; font-weight: 600; }
    [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input { border-color: #b7ddd7; }
    [data-testid="stCheckbox"] label { color: #275d61; }
    .section-intro { border-radius: 14px; padding: 1rem 1.15rem; margin: .2rem 0 1.1rem; border-left: 6px solid; box-shadow: 0 5px 16px rgba(22, 90, 96, .07); }
    .section-intro.teal { background: linear-gradient(100deg, #d8f4ef, #f7fcfb); border-color: #15b8a6; }
    .section-intro.green { background: linear-gradient(100deg, #ebf6e6, #fafdf8); border-color: #4c9a2a; }
    .section-intro.lavender { background: linear-gradient(100deg, #e5d8f5, #fbfaff); border-color: #9981ca; }
    .section-title { color: #0a6975; font-size: 1.08rem; font-weight: 750; margin-bottom: .2rem; }
</style>
""", unsafe_allow_html=True)
st.title("Rowing Plan Generator")
st.caption("Build a clear season around your life, training, and race goals.")
profile=get_profile()
with st.sidebar:
    st.caption("Your plan starts blank. You can optionally load an example or restore your saved entries.")
    if st.button("Load example plan"):
        st.session_state.profile=json.loads(json.dumps(SAMPLE))
        st.session_state.pop("plan",None)
        st.rerun()
    if st.button("Start a new blank plan"):
        st.session_state.profile=blank_profile()
        st.session_state.pop("plan",None)
        st.session_state.pop("locked_sessions",None)
        st.rerun()
    up=st.file_uploader("Restore saved profile",type="json")
    if up:
        try: st.session_state.profile=load_profile(up.getvalue()); profile=get_profile(); st.success("Profile loaded.")
        except Exception as e: st.error(f"Could not load profile: {e}")
    st.download_button("Save my entries (optional)",dump_profile(profile),"rowing_profile.json","application/json")
tabs=st.tabs(["1 Athlete & goals","2 Testing","3 Weekly structure","4 Races","5 Intensity Guidance","6 Preview & download"])
with tabs[0]:
    section_intro("Your season at a glance", "Choose the start and end dates for the plan, then set the amount of training you can sustain.", "teal")
    with st.form("athlete"):
        a=profile["athlete"]; season=profile["season"]
        name=st.text_input("Name or nickname",a["display_name"]); experience=st.selectbox("Experience",["novice","intermediate","experienced","competitive"],index=["novice","intermediate","experienced","competitive"].index(a["experience_level"]))
        st.markdown("#### Plan date range")
        start=st.date_input("Plan start date",date.fromisoformat(season["start_date"])); end=st.date_input("Plan end date",date.fromisoformat(season["end_date"])); current=st.number_input("Current weekly endurance minutes",0,2000,int(season["current_weekly_endurance_minutes"])); peak=st.number_input("Target peak weekly endurance minutes",0,2000,int(season["target_peak_weekly_endurance_minutes"]))
        if st.form_submit_button("Save athlete and goals"):
            a["display_name"],a["experience_level"]=name,experience; season.update(start_date=start.isoformat(),end_date=end.isoformat(),current_weekly_endurance_minutes=current,target_peak_weekly_endurance_minutes=peak); st.success("Saved.")
with tabs[1]:
    section_intro("Testing that stays in context", "Store current or winter testing blocks. Results refine training targets without claiming to diagnose thresholds.", "lavender")
    st.info("An actual 2k is the primary integrated erg performance anchor. Seven-stroke and 60-second tests add peak and short-duration power information; none of these tests directly measure lactate threshold.")
    with st.form("tests"):
        tests=profile.setdefault("tests",{}); m=tests.setdefault("multi_duration_power_tests",{})
        rest=st.number_input("Resting HR (optional)",0,250,int(tests.get("resting_hr") or 0)); maximum=st.number_input("Max HR (optional)",0,250,int(tests.get("max_hr") or 0))
        mins, secs = two_k_parts(tests.get("erg_2k_seconds"))
        c_time1,c_time2=st.columns(2)
        with c_time1: two_minutes=st.number_input("2k minutes",0,20,mins,help="Enter 0 if you do not have a current 2k time.")
        with c_time2: two_seconds=st.number_input("2k seconds",0,59,secs)
        block_label=st.text_input("Testing block label",f"Winter testing block — {date.today().isoformat()}",help="Tests in a block should reflect roughly the same training state; they do not need to occur on one day.")
        test_date=st.date_input("Test date",date.today())
        c1,c2,c3=st.columns(3)
        vals=[]
        for col,key,label in ((c1,"short_peak","Short peak watts"),(c2,"one_minute","One-minute average watts"),(c3,"rate_capped_sustained","Rate-capped sustained watts")):
            raw=m.get(key,{})
            with col:
                vals.append(st.number_input(label,0.0,2000.0,float(raw.get("value_watts") or 0),key=f"{key}_w")); st.caption("Test date and drag factor improve confidence.")
        c4,c5=st.columns(2)
        with c4: twenty=st.number_input("Optional 20-second average watts",0.0,2000.0,0.0)
        with c5: thirty=st.number_input("Optional 30-minute rate-capped watts",0.0,2000.0,float((m.get("rate_capped_sustained") or {}).get("value_watts") or 0))
        if st.form_submit_button("Save testing"):
            two=two_minutes*60+two_seconds
            tests.update(resting_hr=rest or None,max_hr=maximum or None,erg_2k_seconds=two or None)
            defaults=[("short_peak",{"protocol":"other_short_peak"}),("one_minute",{"duration_seconds":60}),("rate_capped_sustained",{"duration_seconds":1800,"rate_cap_spm":20})]
            for (key,extra),value in zip(defaults,vals):
                if value: m[key]=dict(m.get(key,{}),**extra,value_watts=value,validity="valid",test_date=test_date.isoformat())
            block_tests=[]
            if two: block_tests.append({"id":f"two-k-{test_date.isoformat()}","protocol":"two_k","test_date":test_date.isoformat(),"time_seconds":two,"valid_for_profile":True,"source":"manual","notes":""})
            for protocol,value,field in (("seven_stroke_peak",vals[0],"peak_watts"),("sixty_second",vals[1],"average_watts"),("twenty_second_optional",twenty,"average_watts"),("thirty_min_rate_capped_optional",thirty,"average_watts")):
                if value: block_tests.append({"id":f"{protocol}-{test_date.isoformat()}","protocol":protocol,"test_date":test_date.isoformat(),field:value,"duration_seconds":1800 if protocol=="thirty_min_rate_capped_optional" else None,"rate_cap_spm":20 if protocol=="thirty_min_rate_capped_optional" else None,"valid_for_profile":True,"source":"manual","notes":""})
            if block_tests:
                tests.setdefault("testing_blocks",[]).append({"id":f"block-{test_date.isoformat()}-{len(tests.get('testing_blocks',[]))+1}","label":block_label or f"Testing block {test_date.isoformat()}","start_date":test_date.isoformat(),"end_date":test_date.isoformat(),"notes":"Entered in app.","performance_tests":block_tests})
            st.success("Testing block saved.")
    power_history=build_power_profile(profile,CONFIG)
    history=power_history.get("longitudinal",{}).get("rows",[])
    if history:
        st.subheader("Winter testing history")
        st.dataframe([{k:r.get(k) for k in ("label","date","two_k_time_seconds","two_k","seven_stroke_peak","sixty_second","twenty_second","thirty_min_rate_capped","peak_to_2k_ratio","sixty_to_2k_ratio")} for r in history],hide_index=True,use_container_width=True)
        st.caption("Separate charts avoid implying that 2k, peak, and 60-second watts share the same scale.")
        chart_one,chart_two,chart_three=st.columns(3)
        with chart_one: st.caption("2k average watts"); st.line_chart(history,x="label",y="two_k")
        with chart_two: st.caption("7-stroke peak watts"); st.line_chart(history,x="label",y="seven_stroke_peak")
        with chart_three: st.caption("60-second average watts"); st.line_chart(history,x="label",y="sixty_second")
        st.caption("Changes should be interpreted alongside normal test variability, training context, and repeated measurements.")
with tabs[2]:
    section_intro("Build around real life", "Use the colored checkboxes to protect rest, lifting, coaching, and availability before sessions are scheduled.", "green")
    st.write("Set your normal week here. Checkboxes create protected rest, lifting, and coached-row commitments before the app places ordinary sessions.")
    with st.form("weekly_structure"):
        updated=[]
        for item in profile["weekly_availability"]:
            day=item["weekday"]; label=day.title()
            with st.expander(label, expanded=False):
                a1,a2,a3=st.columns(3)
                with a1:
                    available=st.checkbox("Available to train",item.get("available",True),key=f"available_{day}")
                    rest=st.checkbox("Fixed rest day",item.get("fixed_rest",False),key=f"rest_{day}")
                    coached=st.checkbox("Coached row",item.get("fixed_coached_row",False),key=f"coached_{day}")
                with a2:
                    heavy=st.checkbox("Heavy lifting",item.get("heavy_lifting",False),key=f"heavy_{day}")
                    can_row=st.checkbox("Row on lifting day",item.get("row_on_lifting_day",True),key=f"rowlift_{day}")
                    alternate=st.checkbox("Easy cross-training after lifting",item.get("alternate_ut2_allowed",False),key=f"alternate_{day}")
                with a3:
                    max_minutes=st.number_input("Maximum training minutes",0,300,int(item.get("max_training_minutes",60)),key=f"minutes_{day}")
                    max_sessions=st.number_input("Maximum sessions",0,3,int(item.get("max_sessions",1)),key=f"sessions_{day}")
                    mode=st.selectbox("Usual rowing mode",["on_water","erg","either"],index=mode_index(item),key=f"mode_{day}")
                copy=dict(item); copy.update(available=available,fixed_rest=rest,fixed_coached_row=coached,heavy_lifting=heavy,row_on_lifting_day=can_row,alternate_ut2_allowed=alternate,max_training_minutes=max_minutes,max_sessions=max_sessions,rowing_modes=["on_water","erg"] if mode=="either" else [mode]); updated.append(copy)
        if st.form_submit_button("Save weekly structure"):
            profile["weekly_availability"]=updated; st.success("Weekly structure saved.")
with tabs[3]:
    section_intro("Let races shape the plan", "A, B, and C races automatically protect their race days and adjust the build-up around them.", "lavender")
    st.write("Add your race calendar. Race days override ordinary training automatically.")
    with st.form("add_race",clear_on_submit=True):
        event=st.text_input("Event name")
        c1,c2,c3=st.columns(3)
        with c1: race_date=st.date_input("Race start date",date.today()); race_type=st.selectbox("Race type",["head_5k","sprint_1k","erg_2k","other"])
        with c2: end_date=st.date_input("Race end date",race_date); priority=st.selectbox("Priority",["A","B","C"])
        with c3: boat=st.text_input("Boat class","single"); expected=st.number_input("Expected races",1,10,1)
        if st.form_submit_button("Add race"):
            if not event.strip(): st.error("Enter an event name.")
            else:
                profile["races"].append({"event_name":event.strip(),"start_date":race_date.isoformat(),"end_date":end_date.isoformat(),"race_type":race_type,"distance_m":5000 if race_type=="head_5k" else 1000 if race_type=="sprint_1k" else 2000 if race_type=="erg_2k" else None,"priority":priority,"boat_class":boat,"expected_races":expected,"travel_days_before":0,"travel_days_after":0,"benchmark_for_future_plan":True,"notes":""}); st.success("Race added.")
    if profile["races"]:
        st.dataframe([{k:r.get(k) for k in ("event_name","start_date","end_date","race_type","priority","boat_class","expected_races")} for r in profile["races"]],use_container_width=True,hide_index=True)
        if st.button("Clear race calendar"):
            profile["races"]=[]; st.rerun()
with tabs[4]:
    errors=validate_profile(profile); bands=build_intensity_profile(profile,CONFIG); power=build_power_profile(profile,CONFIG)
    st.dataframe([{k:b.get(k) for k in ("name","hr_low","hr_high","watts_low","watts_high","method","confidence","assumptions")} for b in bands],use_container_width=True,hide_index=True)
with tabs[5]:
    section_intro("Review, preserve, and download", "Generate the schedule, keep already-completed training unchanged when needed, then download Excel or CSV.", "green")
    bands=build_intensity_profile(profile,CONFIG); power=build_power_profile(profile,CONFIG)
    if st.button("Generate deterministic plan",type="primary"):
        st.session_state.plan=generate_plan(profile,CONFIG,bands,power,st.session_state.get("locked_sessions",[]))
    plan=st.session_state.get("plan")
    if plan:
        hard=hard_constraint_errors(plan,profile)
        if hard:
            for e in hard: st.error(e)
        else: st.success("Plan meets hard scheduling constraints.")
        for w in plan["warnings"]: st.warning(w["message"])
        for impact in plan.get("plan_impacts",[]): st.info(impact)
        st.subheader("Your schedule")
        schedule=[{"Date":s["date"],"Day":s["day"],"Type of row":s["band"],"Coached": "Yes" if s.get("coached") else "No","HR range":s.get("hr_range"),"Description of workout":s.get("description",s.get("title")),"Rating (spm)":s.get("rating"),"Total time (min)":s.get("total_cardio_minutes",0)} for s in plan["sessions"]]
        st.dataframe(schedule,use_container_width=True,hide_index=True,column_config={"Description of workout":st.column_config.TextColumn(width="large")})
        st.subheader("Keep completed training unchanged (optional)")
        st.caption("Use this only before generating an updated plan after a new test or race result. Sessions through this date stay exactly as completed; only future sessions can change.")
        plan_dates=sorted(date.fromisoformat(s["date"]) for s in plan["sessions"])
        if plan_dates:
            completed_through=st.date_input("Completed through",min_value=plan_dates[0],max_value=plan_dates[-1],value=plan_dates[0])
            lock_col,clear_col=st.columns(2)
            with lock_col:
                if st.button("Keep completed dates unchanged"):
                    st.session_state.locked_sessions=[s for s in plan["sessions"] if date.fromisoformat(s["date"]) <= completed_through]
                    st.success(f"Training through {completed_through:%b %d, %Y} will remain unchanged when you regenerate.")
            with clear_col:
                if st.button("Allow all dates to regenerate"):
                    st.session_state.locked_sessions=[]
                    st.success("All plan dates can change on the next regeneration.")
        data=build_workbook(profile,plan,source_rows())
        st.download_button("Download Excel workbook",data,safe_filename(profile["athlete"]["display_name"]+"_rowing_plan"),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("Download daily schedule CSV",schedule_csv(schedule),safe_filename(profile["athlete"]["display_name"]+"_daily_schedule",suffix=".csv"),"text/csv")
    else: st.info("Generate a plan to preview the calendar and download the workbook.")
