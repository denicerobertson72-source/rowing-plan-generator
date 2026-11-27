"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "../../components/app-shell";
import {
  ApiRequestError,
  apiErrorMessage,
  getCurrentAthlete,
  getDeveloperDiagnosticsAccess,
  getPersistedV3Diagnostic,
} from "../../lib/api";
import { supabase } from "../../lib/supabase";

type Diagnostic = Record<string, unknown>;
type Section = { id:string; title:string; keys:string[] };

const SECTIONS:Section[] = [
  {id:"overview",title:"Overview and structural validation",keys:["planning_date","planning_end","r2","r3","r4_status","conservation","structural_validation","training_plausibility"]},
  {id:"baseline",title:"Training baseline and tolerance evidence",keys:["baseline_and_tolerance_evidence","r2_baseline_evidence","r3_weekly_evidence"]},
  {id:"physiology",title:"Physiological evidence and assessment history",keys:["physiological_evidence","current_training"]},
  {id:"development",title:"Developmental recommendations",keys:["development_recommendation"]},
  {id:"dose",title:"Recommended training dose and category allocation",keys:["r2","r3","r3_cadence_feasibility"]},
  {id:"calendar",title:"Calendar and materialized workouts",keys:["calendar","workouts"]},
  {id:"ledger",title:"Continuous exposure ledger and historical demand",keys:["training_exposure_ledger","training_demand_characterization","recovery_context_summary"]},
  {id:"sequence",title:"Sequence enforcement and cadence feasibility",keys:["sequence_enforcement","r3_cadence_feasibility","authority_integration"]},
  {id:"quality",title:"R6 quality composition and outcomes",keys:["r6","r6_results"]},
  {id:"performance",title:"Diagnostic performance timings",keys:["diagnostic_timing_ms","diagnostic_counts"]},
];

const knownKeys=new Set(SECTIONS.flatMap(section=>section.keys));
const initialSelection=new Set(["overview","baseline","dose","quality","performance"]);

function selectedPayload(report:Diagnostic, sectionIds:Set<string>) {
  const sections=SECTIONS.filter(section=>sectionIds.has(section.id)).map(section=>[
    section.title,
    Object.fromEntries(section.keys.filter(key=>key in report).map(key=>[key,report[key]])),
  ]);
  const unrecognized=Object.fromEntries(Object.entries(report).filter(([key])=>!knownKeys.has(key)));
  if(sectionIds.has("unrecognized")&&Object.keys(unrecognized).length) sections.push(["Additional response fields",unrecognized]);
  return Object.fromEntries(sections);
}

function safeFilename(label:string, capturedAt:string) {
  const clean=label.trim().toLowerCase().replace(/[^a-z0-9_-]+/g,"-").replace(/^-+|-+$/g,"").slice(0,48);
  return `${clean||"v3-diagnostic"}-${capturedAt.replace(/[:.]/g,"").replace("Z", "Z")}.json`;
}

function pretty(value:unknown) { return JSON.stringify(value,null,2); }

function overview(report:Diagnostic) {
  const r2=(report.r2??{}) as Record<string,unknown>, r3=(report.r3??{}) as Record<string,unknown>;
  const conservation=(report.conservation??{}) as Record<string,unknown>, r4=(report.r4_status??{}) as Record<string,unknown>;
  return [
    ["R2 recommended physical minutes",r2.recommended_physical_minutes],
    ["R3 rowing / strength / general aerobic",[r3.rowing_specific_minutes,r3.strength_minutes,r3.general_aerobic_minutes].every(value=>value!==undefined)?`${r3.rowing_specific_minutes} / ${r3.strength_minutes} / ${r3.general_aerobic_minutes}`:undefined],
    ["R4 feasibility",r4.success===undefined?undefined:r4.success?"feasible":"infeasible"],
    ["Conservation",conservation.pass===undefined?undefined:conservation.pass?"PASS":"FAIL"],
    ["Structural validation",report.structural_validation],
    ["R6 outcomes",Array.isArray(report.r6_results)?`${report.r6_results.length} result(s)`:undefined],
    ["Diagnostic-only",(report.training_exposure_ledger as Record<string,unknown>|undefined)?.diagnostic_only===true?"yes":undefined],
  ].filter((item):item is [string,unknown]=>item[1]!==undefined);
}

export default function DeveloperDiagnosticsPage() {
  const [authorized,setAuthorized]=useState<boolean|null>(null),[athleteId,setAthleteId]=useState<string|null>(null);
  const [report,setReport]=useState<Diagnostic|null>(null),[snapshotLabel,setSnapshotLabel]=useState("");
  const [capturedAt,setCapturedAt]=useState<string|null>(null),[elapsed,setElapsed]=useState<number|null>(null),[status,setStatus]=useState("");
  const [error,setError]=useState(""),[running,setRunning]=useState(false),[selection,setSelection]=useState<Set<string>>(initialSelection);
  const activeAthlete=useRef<string|null>(null);
  const diagnosticEpoch=useRef(0);

  const clearDiagnostic=useCallback(()=>{diagnosticEpoch.current+=1;setReport(null);setCapturedAt(null);setElapsed(null);setStatus("");setError("");},[]);
  const loadAccess=useCallback(async()=>{
    try {
      const access=await getDeveloperDiagnosticsAccess();
      setAuthorized(access.developer_diagnostics_authorized===true);
      if(!access.developer_diagnostics_authorized){setAthleteId(null);clearDiagnostic();return;}
      const athlete=await getCurrentAthlete();
      if(activeAthlete.current&&activeAthlete.current!==athlete.athlete_id){setRunning(false);clearDiagnostic();}
      activeAthlete.current=athlete.athlete_id;
      setAthleteId(athlete.athlete_id);
    } catch(error) {
      setAuthorized(false);setAthleteId(null);clearDiagnostic();
      if(error instanceof ApiRequestError&&error.status===401) setError("Your session expired. Sign in again to use Developer Diagnostics.");
    }
  },[clearDiagnostic]);

  useEffect(()=>{
    void loadAccess();
    const listener=supabase?.auth.onAuthStateChange((_event,session)=>{
      if(!session){activeAthlete.current=null;setAuthorized(false);setAthleteId(null);setRunning(false);clearDiagnostic();return;}
      void loadAccess();
    });
    const onFocus=()=>void loadAccess();
    window.addEventListener("focus",onFocus);
    return()=>{listener?.data.subscription.unsubscribe();window.removeEventListener("focus",onFocus);};
  },[clearDiagnostic,loadAccess]);

  const run=async()=>{
    if(running||!authorized||!athleteId)return;
    setRunning(true);clearDiagnostic();
    const requestEpoch=diagnosticEpoch.current;
    const started=performance.now();
    try {
      const response=await getPersistedV3Diagnostic(athleteId);
      if(diagnosticEpoch.current!==requestEpoch)return;
      setReport(response);setCapturedAt(new Date().toISOString());setElapsed(Math.round(performance.now()-started));setStatus("200 OK · read-only diagnostic captured");
    } catch(error) {
      if(diagnosticEpoch.current!==requestEpoch)return;
      setElapsed(Math.round(performance.now()-started));
      setStatus(error instanceof ApiRequestError?`HTTP ${error.status} · diagnostic request failed`:"Diagnostic request failed");
      setError(apiErrorMessage(error,"The diagnostic could not be run. No plan or profile changes were made."));
    } finally { if(diagnosticEpoch.current===requestEpoch)setRunning(false); }
  };

  const unrecognized=useMemo(()=>report?Object.keys(report).filter(key=>!knownKeys.has(key)):[],[report]);
  const toggle=(id:string)=>setSelection(current=>{const next=new Set(current);next.has(id)?next.delete(id):next.add(id);return next;});
  const selectAll=()=>setSelection(new Set([...SECTIONS.map(section=>section.id),...(unrecognized.length?["unrecognized"]:[])]));
  const copy=async(text:string,label:string)=>{
    try { await navigator.clipboard.writeText(text);setStatus(label); }
    catch { setError("Clipboard access was unavailable. Select the displayed JSON and copy it manually."); }
  };
  const exportPayload=report&&capturedAt?{capture_metadata:{snapshot_label:snapshotLabel||null,captured_at:capturedAt,source:"authenticated_read_only_v3_persisted_diagnostic"},diagnostic:report}:null;
  const download=()=>{
    if(!exportPayload||!capturedAt)return;
    const url=URL.createObjectURL(new Blob([pretty(exportPayload)],{type:"application/json"}));
    const anchor=document.createElement("a");anchor.href=url;anchor.download=safeFilename(snapshotLabel,capturedAt);anchor.click();URL.revokeObjectURL(url);
    setStatus("Full diagnostic JSON downloaded.");
  };

  return <AppShell title="Developer Diagnostics">
    <section className="session-card developer-diagnostics">
      <p>Authenticated, read-only V3 persisted diagnostics. Running this never generates or saves a plan.</p>
      {authorized===null?<p>Checking developer access…</p>:authorized?<p className="status">Developer access authorized for the selected athlete.</p>:<p className="error" role="alert">Developer Diagnostics is not enabled for this account.</p>}
      <label>Snapshot label (optional)<input value={snapshotLabel} onChange={event=>setSnapshotLabel(event.target.value)} placeholder="e.g. winter-baseline-review" disabled={!authorized||running}/></label>
      <div className="actions"><button type="button" onClick={run} disabled={!authorized||!athleteId||running}>{running?"Running diagnostic…":"Run persisted V3 diagnostic"}</button></div>
      {status&&<p className="status" role="status">{status}</p>}{error&&<p className="error" role="alert">{error}</p>}
      {capturedAt&&<p className="field-help">Captured {capturedAt}{elapsed!==null?` · ${elapsed} ms request time`:""}.</p>}
    </section>
    {report&&<>
      <section className="session-card"><h2>Overview</h2><dl className="diagnostic-overview">{overview(report).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>
        {Boolean(report.planning_date)&&<p>Planning: {String(report.planning_date)} to {String(report.planning_end??"unavailable")}</p>}
        {Boolean((report.baseline_and_tolerance_evidence as Record<string,unknown>|undefined)?.baseline)&&<p>Evidence contract: {String(((report.baseline_and_tolerance_evidence as Record<string,unknown>).baseline as Record<string,unknown>).version??"unavailable")}</p>}
      </section>
      <section className="session-card"><h2>Export</h2><div className="actions"><button type="button" className="quiet" onClick={selectAll}>Select all</button><button type="button" className="quiet" onClick={()=>setSelection(new Set())}>Clear all</button><button type="button" onClick={()=>copy(pretty({snapshot_label:snapshotLabel||null,captured_at:capturedAt,sections:selectedPayload(report,selection)}),"Selected diagnostic summary copied.")}>Copy selected summary</button><button type="button" onClick={()=>copy(pretty(report),"Full diagnostic JSON copied.")}>Copy full JSON</button><button type="button" onClick={download}>Download full JSON</button></div><p className="field-help">Downloads wrap the unchanged approved response in capture metadata; no snapshot is persisted.</p></section>
      <section className="session-card"><h2>Diagnostic sections</h2>{SECTIONS.map(section=>{const available=section.keys.some(key=>key in report);const payload=Object.fromEntries(section.keys.filter(key=>key in report).map(key=>[key,report[key]]));return <details key={section.id} className="diagnostic-section"><summary><label><input type="checkbox" checked={selection.has(section.id)} onChange={()=>toggle(section.id)}/>{section.title}</label>{available?null:<span>Unavailable</span>}</summary>{available&&<pre>{pretty(payload)}</pre>}</details>;})}{unrecognized.length>0&&<details className="diagnostic-section"><summary><label><input type="checkbox" checked={selection.has("unrecognized")} onChange={()=>toggle("unrecognized")}/>Additional response fields</label></summary><pre>{pretty(Object.fromEntries(unrecognized.map(key=>[key,report[key]])))}</pre></details>}</section>
    </>}
  </AppShell>;
}
