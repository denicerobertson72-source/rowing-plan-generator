/** Typed boundary for the FastAPI OpenAPI contract; code generation replaces this shim in Milestone 2. */
export type PlanSession = { date:string; day:string; band:string; title:string; description?:string; total_cardio_minutes:number; hr_range?:string; rating?:string; coached?:boolean; mode:string; session_id:string };
export type Plan = { sessions: PlanSession[]; phases: {date:string;phase:string;race_event?:string}[]; plan_impacts?:string[] };
export type PlanResponse = { plan_id: string; plan: Plan };
export type AthleteResponse = { athlete_id: string; athlete_profile: Record<string, unknown> };
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
async function request<T>(path:string, init?:RequestInit): Promise<T> { const response=await fetch(`${API_BASE}${path}`,{headers:{"Content-Type":"application/json",...(init?.headers ?? {})},...init}); if (!response.ok) throw new Error((await response.text()) || "Request failed"); return response.json() as Promise<T>; }
export function createAthlete(athlete_profile: Record<string, unknown>): Promise<AthleteResponse> { return request("/athletes",{method:"POST",body:JSON.stringify({athlete_profile})}); }
export function generateAthletePlan(athleteId:string): Promise<PlanResponse> { return request(`/athletes/${athleteId}/plans/generate`,{method:"POST",body:"{}"}); }
export function getPlan(planId:string): Promise<{plan_id:string; athlete_id:string; version_number:number; plan:Plan}> { return request(`/plans/${planId}`); }
export async function getToday(planId:string): Promise<PlanSession[]> { const data=await request<{sessions:PlanSession[]}>(`/plans/${planId}/today`); return data.sessions; }
export function getWeek(planId:string, weekNumber:number): Promise<{days:{date:string;day:string;state:string;sessions:PlanSession[]}[]}> { return request(`/plans/${planId}/week?week_number=${weekNumber}`); }
export function getSessionDetail(planId:string, date:string, sessionId:string, mode:string): Promise<Record<string, unknown>> { return request(`/plans/${planId}/sessions/detail?session_date=${encodeURIComponent(date)}&session_id=${encodeURIComponent(sessionId)}&mode=${encodeURIComponent(mode)}`); }
export function logWorkout(planId:string, sessionKey:string, payload:Record<string, unknown>): Promise<{status:string;log_id:string}> { return request(`/plans/${planId}/sessions/${encodeURIComponent(sessionKey)}/log`,{method:"POST",body:JSON.stringify(payload)}); }
export function getSeason(planId:string): Promise<{current_phase?:{date:string;phase:string};next_race?:{event_name:string;start_date:string;priority:string;distance_m?:number};days_to_next_race?:number;transitions:{date:string;phase:string;race_event?:string}[]}> { return request(`/plans/${planId}/season`); }
