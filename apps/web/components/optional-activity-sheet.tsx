"use client";

import { FormEvent, useState } from "react";
import { logOptionalActivity } from "../lib/api";
import { useSaveFeedback } from "./save-feedback";

const activities=[
  ["erg","Rowing / erg"],["on_water","On-water rowing"],["walk","Walk"],
  ["cross_training","Cross-training / other aerobic"],["strength","Strength"],
  ["mobility_recovery","Mobility / recovery"],["other","Other"],
] as const;

export function OptionalActivitySheet({planId,date,actuals=[],onSaved}:{planId:string;date:string;actuals?:Record<string,unknown>[];onSaved?:()=>void}) {
  const current=actuals[0] as Record<string,any>|undefined;
  const [open,setOpen]=useState(false),[type,setType]=useState(current?.activity_type??"walk"),[minutes,setMinutes]=useState(current?.actual_duration_min??30),[distance,setDistance]=useState(current?.distance_meters?.toString()??""),[intensity,setIntensity]=useState(current?.actual_intensity??"UT3"),[rpe,setRpe]=useState(current?.rpe??2),[notes,setNotes]=useState(current?.notes??""),[saving,setSaving]=useState(false);
  const {showSaveSuccess,showSaveError}=useSaveFeedback();
  const save=async(event:FormEvent)=>{event.preventDefault();setSaving(true);try{await logOptionalActivity(planId,date,{status:"completed",completion:"yes",activity_type:type,actual_duration_min:minutes,distance_meters:distance?Number(distance):undefined,actual_intensity:["erg","on_water"].includes(type)?intensity:undefined,rpe,notes});showSaveSuccess("Actual activity recorded. Your planned Off day is unchanged.");setOpen(false);onSaved?.()}catch{showSaveError("Couldn’t record the activity. Your details are still here.");}finally{setSaving(false)}};
  return <><button className="quiet" type="button" onClick={()=>setOpen(true)}>{actuals.length?"Edit recorded activity":"Record session"}</button>{actuals.length>0&&<p className="status">Actual: {actuals.map(item=>`${item.actual_duration_min??"—"} min ${String(item.activity_type??"activity").replaceAll("_"," ")}`).join(" · ")}</p>}{open&&<div className="sheet-backdrop"><form className="log-sheet" onSubmit={save}><button className="close" type="button" onClick={()=>setOpen(false)}>Close</button><h2>Record session</h2><p className="note">This records what you did; it does not change the planned Off day.</p><label>Activity type<select value={type} onChange={e=>setType(e.target.value)}>{activities.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><label>Duration<input type="number" inputMode="numeric" min="0" value={minutes} onChange={e=>setMinutes(Number(e.target.value))}/> min</label>{["erg","on_water","walk","cross_training"].includes(type)&&<label>Distance (optional)<input type="number" inputMode="numeric" min="0" value={distance} onChange={e=>setDistance(e.target.value)}/> m</label>}{["erg","on_water"].includes(type)&&<label>Intensity<select value={intensity} onChange={e=>setIntensity(e.target.value)}>{["UT3","UT2","UT1","AT","TR","AN","PP","mixed_unsure"].map(value=><option key={value}>{value==="mixed_unsure"?"Mixed / unsure":value}</option>)}</select></label>}<label>RPE 1–10<input type="number" inputMode="numeric" min="1" max="10" value={rpe} onChange={e=>setRpe(Number(e.target.value))}/></label><label>Notes<textarea value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Anything useful to remember?"/></label><button disabled={saving}>{saving?"Saving…":"Save activity"}</button></form></div>}</>;
}
