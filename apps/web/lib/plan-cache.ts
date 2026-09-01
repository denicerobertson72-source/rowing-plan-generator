import { Plan } from "./api";
const KEY="rowing-plan-latest-read-v1";
export function cachePlan(plan:Plan):void { localStorage.setItem(KEY,JSON.stringify({savedAt:new Date().toISOString(),plan})); }
export function cachedPlan():{savedAt:string;plan:Plan}|null { const raw=localStorage.getItem(KEY); return raw?JSON.parse(raw) as {savedAt:string;plan:Plan}:null; }
export function clearCachedPlan():void { if (typeof window !== "undefined") localStorage.removeItem(KEY); }
