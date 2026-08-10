"use client";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { getSeason } from "../../lib/api";
import { getSavedSession } from "../../lib/session";
type Season={current_phase?:{date:string;phase:string};next_race?:{event_name:string;start_date:string;priority:string;distance_m?:number};days_to_next_race?:number;transitions:{date:string;phase:string;race_event?:string}[]};
export default function Season(){const [season,setSeason]=useState<Season|null>(null);useEffect(()=>{const saved=getSavedSession();if(saved)getSeason(saved.planId).then(setSeason).catch(()=>undefined);},[]);return <AppShell title="Season">{season?<><section className="race-card"><span className="band">CURRENT PHASE</span><h2>{season.current_phase?.phase.replaceAll("_"," ")??"Plan ready"}</h2>{season.next_race?<><p className="duration">{season.days_to_next_race} days to next race</p><p><b>{season.next_race.event_name}</b> · {season.next_race.priority} priority · {season.next_race.distance_m} m</p></>:<p>No upcoming race has been added.</p>}</section><h2>Upcoming phases</h2>{season.transitions.map(item=><article className="week-row" key={item.date}><b>{item.date}</b><span>{item.phase.replaceAll("_"," ")}</span><span>{item.race_event??""}</span></article>)}</>:<p className="empty">Create a plan to see phase and race information.</p>}</AppShell>}
