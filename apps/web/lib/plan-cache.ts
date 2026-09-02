import { Plan } from "./api";
const KEY="rowing-plan-latest-read-v2";
export function cachePlan(planId:string,plan:Plan,version?:number):void { localStorage.setItem(KEY,JSON.stringify({savedAt:new Date().toISOString(),planId,version,plan})); }
export function cachedPlan():{savedAt:string;planId:string;version?:number;plan:Plan}|null { const raw=localStorage.getItem(KEY); return raw?JSON.parse(raw) as {savedAt:string;planId:string;version?:number;plan:Plan}:null; }
export function clearCachedPlan():void { if (typeof window !== "undefined") localStorage.removeItem(KEY); }
