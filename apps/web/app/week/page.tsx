"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { getPlan, getWeek, PlanSession } from "../../lib/api";
import { cachedPlan } from "../../lib/plan-cache";
import { getSavedSession } from "../../lib/session";
type Day={date:string;day:string;state:string;sessions:PlanSession[]};
function isoWeek(value:string){const d=new Date(`${value}T12:00:00`);const target=new Date(d.valueOf());target.setDate(d.getDate()+4-(d.getDay()||7));return Math.ceil((((target.valueOf()-new Date(target.getFullYear(),0,1).valueOf())/86400000)+1)/7);}
export default function Week(){const [days,setDays]=useState<Day[]>([]);const [notice,setNotice]=useState("");useEffect(()=>{const saved=getSavedSession();if(!saved)return;getPlan(saved.planId).then(plan=>{const first=plan.plan.sessions[0];if(!first)return;return getWeek(saved.planId,isoWeek(first.date));}).then(data=>{if(data)setDays(data.days);}).catch(()=>{const sessions=cachedPlan()?.plan.sessions.slice(0,7)??[];setDays(sessions.map(s=>({date:s.date,day:s.day,state:"planned",sessions:[s]})));setNotice("Offline — showing the latest saved week.");});},[]);return <AppShell title="Week">{notice&&<p className="notice">{notice}</p>}{days.length?days.map(entry=>entry.sessions.length?entry.sessions.map(s=>{const href=`/workout?date=${s.date}&sessionId=${s.session_id}&mode=${s.mode}`;return <Link className="week-row" href={href} key={`${s.date}-${s.session_id}-${s.mode}`}><b>{entry.day}</b><span>{s.band}</span><span>{s.title}</span><span>{s.total_cardio_minutes} min</span></Link>}):<article className="week-row rest-row" key={entry.date}><b>{entry.day}</b><span>REST</span><span>Protected rest day</span><span>—</span></article>):<p className="empty">Create a plan to see your week.</p>}</AppShell>}
