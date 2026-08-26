"use client";
import Link from "next/link";
import {useEffect,useState} from "react";
import {AppShell} from "../../components/app-shell";
import {getPlan,getWeek,PlanSession} from "../../lib/api";
import {getSavedSession} from "../../lib/session";
type Day={date:string;day:string;state:string;sessions:PlanSession[]};
const weekNo=(v:string)=>{const d=new Date(`${v}T12:00:00`),t=new Date(d);t.setDate(d.getDate()+4-(d.getDay()||7));return Math.ceil((((t.valueOf()-new Date(t.getFullYear(),0,1).valueOf())/86400000)+1)/7)};
export default function Week(){const [days,setDays]=useState<Day[]>([]);useEffect(()=>{const s=getSavedSession();if(!s)return;getPlan(s.planId).then(p=>p.plan.sessions[0]&&getWeek(s.planId,weekNo(p.plan.sessions[0].date))).then(x=>x&&setDays(x.days)).catch(()=>{});},[]);return <AppShell title="Week"><div className="actions"><Link href="/weekly-override">Adjust this week</Link></div>{days.length?days.map(d=>d.sessions.length?d.sessions.map(s=><Link className="week-row" href={`/workout?date=${s.date}&sessionId=${s.session_id}&mode=${s.mode}`} key={`${s.date}-${s.session_id}`}><b>{d.day}</b><span>{s.band}</span><span>{s.title}</span><span>{s.total_cardio_minutes} min</span></Link>):<article className="week-row rest-row" key={d.date}><b>{d.day}</b><span>REST</span><span>Protected rest day</span><span>—</span></article>):<p className="empty">Create a plan to see your week.</p>}</AppShell>}
