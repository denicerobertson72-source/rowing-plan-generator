"use client";

import Link from "next/link";
import { useState } from "react";
import { PlanSession, completeAsPlanned, undoCompleteAsPlanned } from "../lib/api";
import { actualLogSummary } from "../lib/actual-log";
import { dailySummary } from "../lib/daily-summary";
import { localCalendarDate } from "../lib/local-date";
import { WorkoutLogSheet } from "./workout-log-sheet";

const keyFor=(session:PlanSession)=>`${session.date}:${session.session_id}:${session.mode}`;
const isQuick=(actual:Record<string,unknown>|undefined)=>actual?.provenance==="completed_as_planned";
const isModified=(actual:Record<string,unknown>|undefined, session:PlanSession)=>Boolean(actual && !isQuick(actual) && (actual.actual_duration_min!==session.total_cardio_minutes || actual.actual_intensity!==session.band || actual.status==="modified"));

/** Shared by Today and Week so each prescribed session has one completion action. */
export function CompletionControl({planId,session,onChanged}:{planId:string;session:PlanSession;onChanged?:()=>void}) {
  const initial=session.actual as Record<string,unknown>|undefined;
  const [actual,setActual]=useState(initial); const [saving,setSaving]=useState(false); const [error,setError]=useState("");
  const eligible=session.date<=localCalendarDate() && session.session_id!=="RACE" && session.mode!=="race";
  if(!eligible) return null;
  const complete=async()=>{setSaving(true);setError("");try{await completeAsPlanned(planId,keyFor(session));setActual({provenance:"completed_as_planned"});onChanged?.();}catch{setError("Couldn’t save completion. Try again.");}finally{setSaving(false);}};
  const undo=async()=>{setSaving(true);setError("");try{await undoCompleteAsPlanned(planId,keyFor(session));setActual(undefined);onChanged?.();}catch{setError("Add or edit details to change this recorded actual safely.");}finally{setSaving(false);}};
  if(actual) return <>{error&&<p className="notice">{error}</p>}<button className="quiet" disabled={saving} onClick={isQuick(actual)?undo:undefined}>{saving?"Saving…":isQuick(actual)?"✓ Completed as planned":isModified(actual,session)?"✓ Completed — modified":"✓ Completed"}</button></>;
  return <>{error&&<p className="notice">{error}</p>}<button className="quiet" disabled={saving} onClick={complete}>{saving?"Saving…":"Completed as planned"}</button></>;
}

export function SessionCard({session,planId}:{session:PlanSession;planId:string}) {
 const href=`/workout?date=${encodeURIComponent(session.date)}&sessionId=${encodeURIComponent(session.session_id)}&mode=${encodeURIComponent(session.mode)}`,actual=session.actual as Record<string,unknown>|undefined,why=session.transformation_reason??session.selection_reason;
 return <section className="session-card"><span className="band">{session.coached ? "COACHED SESSION" : session.band}</span><h2>{dailySummary(session)}</h2><p className="duration">Planned: {session.total_cardio_minutes} min{session.session_role?` · ${session.session_role.replaceAll("_"," ")}`:""}</p>{actual&&<p className="status">{actualLogSummary(actual)}</p>}<div className="target"><b>Rate</b><span>{session.rating ?? "Follow coach guidance"}</span></div><div className="target"><b>Heart rate</b><span>{session.hr_range ?? "Use RPE / breathing"}</span></div>{session.target_watts&&<div className="target"><b>Erg</b><span>{session.target_watts} W · {session.split_guide}</span></div>}{session.coached&&<p className="notice">Coach instructions take priority.</p>}{session.technical_cues?.length&&<p className="note"><b>Coach focus:</b> {session.technical_cues.join(" ")}</p>}{why&&<p className="note"><b>Why this workout?</b> {why}</p>}<div className="actions"><Link href={href}>View details</Link><CompletionControl planId={planId} session={session}/><WorkoutLogSheet planId={planId} session={session}/></div></section>;
}
