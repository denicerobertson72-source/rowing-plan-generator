import type { CalendarDay, PlanSession } from "./api";

const roleLabels:Record<string,string>={AEROBIC_BASE:"Aerobic base",TECHNIQUE_EASY:"Technique / easy",LONG_AEROBIC:"Long aerobic",RACE_PACE:"Race pace",THRESHOLD:"Threshold",RECOVERY:"Recovery",AEROBIC_STRENGTH:"Aerobic strength",SPRINT_POWER:"Sprint power"};
export const roleLabel=(role?:string)=>role?roleLabels[role]??role.replaceAll("_"," ").replace(/\b\w/g,letter=>letter.toUpperCase()):"";
export const sessionCategory=(session:PlanSession)=>session.session_id==="LIFT"?"strength":session.session_id==="COACHED"?"coaching":session.session_id==="RACE"?"race":session.band.includes("AN")||session.band.includes("PP")?"high":session.band.includes("AT")||session.band.includes("TR")?"quality":"aerobic";
export const workoutStructure=(session:PlanSession)=>session.structure?.replace(/^\d+ × /,match=>match).replace(/ UT[123]| AT| TR| AN| PP/g,"").split(";")[0]||session.title;

export function dailySummary(session?:PlanSession, day?:CalendarDay):string {
 if(!session) return day?.state==="designated_rest"?"Off":day?.state==="unavailable"?"Unavailable":day?.state==="no_additional_session"?"Optional recovery":"Plan detail unavailable";
 if(session.session_id==="LIFT") return `Weights${(session as any).strength_state?` · ${(session as any).strength_state}`:""}`;
 if(session.session_id==="COACHED") return session.title==="Private coaching"?"Private coaching · Coach-directed":"Coached row · Coach-directed";
 if(session.session_id==="RACE") return `Race · ${(session as any).race_distance??"Race"}`;
 const structure=(session.structure??"").replace(/minutes?/g,"min").replace(/\s+/g," ");
 return structure?`${session.band} · ${structure}`:`${session.band} · ${session.title}`;
}
