"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ApiRequestError, getAthlete, getLatestAthletePlan, getPlan, getSeason, Plan } from "../../lib/api";
import { AppShell } from "../../components/app-shell";
import { getSavedSession, saveSession } from "../../lib/session";

const text=(value?:string)=>value?.replaceAll("_"," ").replace(/\b\w/g,letter=>letter.toUpperCase())||"Plan ready";
const monday=(value:string)=>{const date=new Date(`${value}T12:00:00`);date.setDate(date.getDate()-((date.getDay()+6)%7));return date.toISOString().slice(0,10)};
const short=(value?:string)=>value?new Date(`${value}T12:00:00`).toLocaleDateString(undefined,{month:"short",day:"numeric"}):"Dates unavailable";
const failureDetails=(stage:string,error:unknown,context:Record<string,boolean>)=>({stage,status:error instanceof ApiRequestError?error.status:undefined,endpoint:error instanceof ApiRequestError?error.endpoint:undefined,...context});

export default function Season(){
  const [plan,setPlan]=useState<Plan|null>(null),[profile,setProfile]=useState<any>(null),[summary,setSummary]=useState<any>(null),[state,setState]=useState<"loading"|"empty"|"error"|"ready">("loading");
  useEffect(()=>{const load=async()=>{
    const saved=getSavedSession();
    if(!saved?.athleteId){console.error("Season startup failed",{stage:"selected_athlete_missing",athleteIdPresent:false,cachedPlanIdPresent:Boolean(saved?.planId)});setState("error");return;}
    let record:any;
    try {
      if(saved.planId){
        try { record=await getPlan(saved.planId); }
        catch(error){if(!(error instanceof ApiRequestError&&error.status===404))throw error;}
      }
      if(!record||record.athlete_id!==saved.athleteId) record=await getLatestAthletePlan(saved.athleteId);
    } catch(error) {
      console.error("Season startup failed",failureDetails("planversion_load",error,{athleteIdPresent:true,cachedPlanIdPresent:Boolean(saved.planId),recoveredPlanIdPresent:Boolean(record?.plan_id)}));
      if(error instanceof ApiRequestError&&error.status===404){setState("empty");return;}
      setState("error");return;
    }
    setPlan(record.plan);
    if(record.plan_id!==saved.planId) saveSession({athleteId:saved.athleteId,planId:record.plan_id});
    const [profileResult,summaryResult]=await Promise.allSettled([getAthlete(saved.athleteId),getSeason(record.plan_id)]);
    if(profileResult.status==="fulfilled") setProfile(profileResult.value.athlete_profile);
    else console.warn("Season optional metadata unavailable",failureDetails("selected_athlete_profile",profileResult.reason,{athleteIdPresent:true,cachedPlanIdPresent:Boolean(saved.planId),recoveredPlanIdPresent:Boolean(record.plan_id)}));
    if(summaryResult.status==="fulfilled") setSummary(summaryResult.value);
    else console.warn("Season optional metadata unavailable",failureDetails("season_summary",summaryResult.reason,{athleteIdPresent:true,cachedPlanIdPresent:Boolean(saved.planId),recoveredPlanIdPresent:Boolean(record.plan_id)}));
    setState("ready");
  };void load();},[]);
  const weeks:any[]=useMemo(()=>{if(!plan)return[];const intents=new Map((plan as any).weekly_training_intents?.map((item:any)=>[item.week_start,item])),groups=new Map<string,any[]>();(plan as any).sessions.forEach((session:any)=>{const start=monday(session.date);groups.set(start,[...(groups.get(start)||[]),session])});return [...groups].sort().map(([start,sessions])=>({start,sessions,intent:intents.get(start),rowing:sessions.reduce((total,session)=>total+(session.rowing_minutes||0),0),race:sessions.find(session=>session.session_id==="RACE"),practice:sessions.find(session=>session.session_id==="COURSE_PRACTICE"),roles:[...new Set(sessions.filter(session=>["THRESHOLD","RACE_PACE","AEROBIC_STRENGTH"].includes(session.session_role)).map(session=>session.session_role))],lifts:sessions.filter(session=>session.session_id==="LIFT").length,coached:sessions.filter(session=>session.session_id==="COACHED").length}))},[plan]);
  if(state==="loading")return <AppShell title="Season"><p>Loading season…</p></AppShell>;
  if(state==="error")return <AppShell title="Season"><section className="empty"><p>Couldn’t load this season. Please try again.</p></section></AppShell>;
  if(state==="empty")return <AppShell title="Season"><p className="empty">No Plan Version Exists Yet</p></AppShell>;
  const max=Math.max(...weeks.map(week=>week.rowing),1),current=summary?.current_phase?.date?monday(summary.current_phase.date):"";
  return <AppShell title="Season"><section className="season-header"><span className="band">SEASON ROADMAP</span><h2>{profile?.season?.season_name||"Training season"}</h2><p>{profile?.season?.start_date&&profile?.season?.end_date?`${short(profile.season.start_date)} – ${short(profile.season.end_date)}`:"Plan dates unavailable"}</p><p><b>Current phase:</b> {text(summary?.current_phase?.phase)}</p>{summary?.next_race&&<p><b>Next race:</b> {summary.next_race.event_name} · {summary.next_race.priority} priority · {short(summary.next_race.start_date)}</p>}</section><section className="season-roadmap"><h2>Season arc</h2><div className="phase-bar">{weeks.map(week=>{const phase=week.intent?.phase_mix?.[0]?.phase_type||week.sessions[0]?.phase;return <span key={week.start} className={`phase-${phase}`}>{text(phase).replace(" Preparation","")}</span>})}</div><p className="priority-legend"><b>A</b> peak / key race · <b>B</b> important race · <b>C</b> training race</p></section><section className="season-load" aria-label="Weekly planned rowing minutes"><h2>Weekly rowing minutes</h2><div className="load-bars">{weeks.map(week=><div key={week.start} className={week.start===current?"current-week":""}><i style={{height:`${Math.max(8,week.rowing/max*100)}%`}} aria-label={`${short(week.start)}: ${week.rowing} planned rowing minutes`} /><span>{short(week.start)}</span></div>)}</div><p className="note">Final planned rowing minutes only; strength and optional cardio are excluded.</p></section><section className="season-weeks"><h2>Weekly roadmap</h2>{weeks.map(week=>{const phase=week.intent?.phase_mix?.map((item:any)=>text(item.phase_type)).join(" + ")||text(week.sessions[0]?.phase);return <Link key={week.start} href={`/week?week=${week.start}`} className={`season-week ${week.start===current?"current-week":""}`}><div><b>{short(week.start)}</b><h3>{phase}</h3><p><strong>{week.rowing} min</strong> rowing · Strength ×{week.lifts} · Coaching ×{week.coached}</p>{week.roles.length>0&&<p>Key work · {week.roles.map(text).join(" · ")}</p>}{week.race&&<p><b>Race week · {week.race.race_priority} priority</b> · {week.race.title}</p>}{week.practice&&<p>Course familiarization · {short(week.practice.date)}</p>}</div><span>View week →</span></Link>})}</section></AppShell>;
}
