import { PlanSession } from "./api";

const qualityBands = new Set(["AT", "TR", "AN", "PP"]);

export function isRowingSession(session: Pick<PlanSession, "mode" | "session_id">): boolean {
  return (session.mode === "erg" || session.mode === "on_water") && session.session_id !== "RACE";
}

export function actualLogSummary(actual: Record<string, unknown>): string {
  const duration = actual.actual_duration_min ?? "—";
  const intensity = actual.actual_intensity === "mixed_unsure" ? "Mixed" : actual.actual_intensity ?? "—";
  const rpe = actual.rpe ?? "—";
  const qualityMinutes = Array.isArray(actual.actual_segments)
    ? actual.actual_segments.reduce((total, segment: any) => total + (qualityBands.has(segment?.intensity_band) ? (Number(segment?.duration_seconds) || 0) * Math.max(1, Number(segment?.repetitions) || 1) : 0), 0) / 60
    : 0;
  return `Actual: ${duration} min · ${intensity} · RPE ${rpe}${qualityMinutes ? ` / ${Math.round(qualityMinutes)} min quality` : ""}`;
}
