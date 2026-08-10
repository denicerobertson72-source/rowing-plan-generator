"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell } from "../components/app-shell";
import { SessionCard } from "../components/session-card";
import { getToday, PlanSession } from "../lib/api";
import { cachedPlan } from "../lib/plan-cache";
import { getSavedSession } from "../lib/session";
export default function Today(){const [sessions,setSessions]=useState<PlanSession[]|null>(null);const [planId,setPlanId]=useState("");const [notice,setNotice]=useState("");useEffect(()=>{const saved=getSavedSession();if(!saved){setSessions([]);return;}setPlanId(saved.planId);getToday(saved.planId).then(rows=>{if(rows.length)setSessions(rows);else{const fallback=cachedPlan()?.plan.sessions.find(s=>s.date>=new Date().toISOString().slice(0,10));setSessions(fallback?[fallback]:[]);setNotice("No session is scheduled for today; showing your next planned session.");}}).catch(()=>{const fallback=cachedPlan()?.plan.sessions.find(s=>s.date>=new Date().toISOString().slice(0,10));setSessions(fallback?[fallback]:[]);setNotice("Offline or API unavailable — showing your most recently saved plan.");});},[]);return <AppShell title="Today">{notice&&<p className="notice">{notice}</p>}{sessions===null?<p>Loading your plan…</p>:sessions.length?sessions.map(s=><SessionCard key={`${s.date}-${s.session_id}-${s.mode}`} session={s} planId={planId}/>):<section className="empty"><h2>No plan yet</h2><p>Create your athlete profile and plan to see daily guidance.</p><Link href="/onboarding">Create my plan</Link></section>}</AppShell>}
