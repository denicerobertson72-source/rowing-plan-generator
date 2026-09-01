export type SavedSession = { athleteId: string; planId: string };
const KEY = "rowing-plan-session-v1";
export function getSavedSession(): SavedSession | null { if (typeof window === "undefined") return null; const value=window.localStorage.getItem(KEY); return value ? JSON.parse(value) as SavedSession : null; }
export function saveSession(session: SavedSession): void { window.localStorage.setItem(KEY, JSON.stringify(session)); }
export function clearSavedSession(): void { if (typeof window !== "undefined") window.localStorage.removeItem(KEY); }
