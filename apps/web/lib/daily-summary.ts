import type { CalendarDay, PlanSession } from "./api";

export function dailySummary(session?:PlanSession, day?:CalendarDay):string {
 if(!session) return day?.state==="designated_rest"?"Off":day?.state==="unavailable"?"Unavailable":day?.state==="no_additional_session"?"Optional recovery":"Plan detail unavailable";
 if(session.session_id==="LIFT") return `Weights${(session as any).strength_state?` · ${(session as any).strength_state}`:""}`;
 if(session.session_id==="COACHED") return session.title==="Private coaching"?"Private coaching · Technique":"Coached row · Coach-led";
 if(session.session_id==="RACE") return `Race · ${(session as any).race_distance??"Race"}`;
 const structure=(session.structure??"").replace(/minutes?/g,"min").replace(/\s+/g," ");
 return structure?`${session.band} · ${structure}`:`${session.band} · ${session.title}`;
}
