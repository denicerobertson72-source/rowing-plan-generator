"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell } from "../components/app-shell";
import { OptionalActivitySheet } from "../components/optional-activity-sheet";
import { SessionCard } from "../components/session-card";
import { getToday, PlanSession } from "../lib/api";
import { cachedPlan } from "../lib/plan-cache";
import { getSavedSession } from "../lib/session";

type TodayResult={sessions:PlanSession[];plan_needs_update:boolean;plan_version:number;date:string;state:string;actual_activities?:Record<string,unknown>[]};

export default function Today(){
 const [today,setToday]=useState<TodayResult|null>(null),[planId,setPlanId]=useState(""),[notice,setNotice]=useState("");
 const refresh=()=>{if(planId)getToday(planId).then(result=>setToday(result));};
 useEffect(()=>{const saved=getSavedSession();if(!saved){setToday({sessions:[],plan_needs_update:false,plan_version:0,date:"",state:""});return;}setPlanId(saved.planId);getToday(saved.planId).then(result=>{if(result.plan_needs_update){setToday({sessions:[],plan_needs_update:true,plan_version:result.plan_version,date:result.date,state:result.state});setNotice("Plan needs update — your Profile schedule has changed.");return;}if(result.sessions.length||result.state==="designated_rest")setToday(result);else{const fallback=cachedPlan();const next=fallback?.planId===saved.planId?fallback.plan.sessions.find(session=>session.date>=new Date().toISOString().slice(0,10)):undefined;setToday({sessions:next?[next]:[],plan_needs_update:false,plan_version:result.plan_version,date:result.date,state:result.state});setNotice("No session is scheduled for today; showing your next planned session.");}}).catch(()=>{const fallback=cachedPlan();const next=fallback?.planId===saved.planId?fallback.plan.sessions.find(session=>session.date>=new Date().toISOString().slice(0,10)):undefined;setToday({sessions:next?[next]:[],plan_needs_update:false,plan_version:0,date:"",state:""});setNotice("Couldn’t reach the rowing-plan service — showing your most recently saved plan.");});},[]);
 return <AppShell title="Today">{notice&&<section className="notice"><p>{notice}</p>{notice.startsWith("Plan needs update")&&<Link href="/profile">Update future plan</Link>}</section>}{today===null?<p>Loading your plan…</p>:today.sessions.length?today.sessions.map(session=><SessionCard key={`${session.date}-${session.session_id}-${session.mode}`} session={session} planId={planId}/>):today.state==="designated_rest"?<section className="session-card"><h2>Off</h2><p className="duration">Planned: Off</p><OptionalActivitySheet planId={planId} date={today.date} actuals={today.actual_activities} onSaved={refresh}/></section>:<section className="empty"><h2>{notice.startsWith("Plan needs update")?"Plan needs update":"No plan yet"}</h2><p>{notice.startsWith("Plan needs update")?"Regenerate future sessions from your updated Profile schedule.":"Create your athlete profile and plan to see daily guidance."}</p><Link href={notice.startsWith("Plan needs update")?"/profile":"/onboarding"}>{notice.startsWith("Plan needs update")?"Open Profile":"Create my plan"}</Link></section>}</AppShell>;
}
