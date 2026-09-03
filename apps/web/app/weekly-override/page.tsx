"use client";

import { useMemo, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { useSaveFeedback } from "../../components/save-feedback";
import { API_BASE } from "../../lib/api";
import { getSavedSession } from "../../lib/session";
import { supabase } from "../../lib/supabase";

const localDate = (value: Date) => value.toISOString().slice(0, 10);
const mondayFor = (value: string) => {
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() - ((date.getDay() + 6) % 7));
  return localDate(date);
};

export default function WeeklyOverride() {
  const today = useMemo(() => localDate(new Date()), []);
  const [changeDate, setChangeDate] = useState(today);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const { showSaveSuccess, showSaveError } = useSaveFeedback();

  const save = async () => {
    if (saving) return;
    const saved = getSavedSession();
    const session = await supabase?.auth.getSession();
    const token = session?.data.session?.access_token;
    if (!saved || !token) {
      showSaveError("Sign in and create a plan first.");
      return;
    }
    setSaving(true);
    try { const response = await fetch(`${API_BASE}/athletes/${saved.athleteId}/weekly-overrides`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        week_start: mondayFor(changeDate),
        scope: "this_week_only",
        changes: [{ date: changeDate, unavailable: true, reason: reason.trim() || "Unavailable" }],
      }),
    });
    if (!response.ok) throw new Error();
    showSaveSuccess("Temporary change saved. Non-fixed sessions on that date will be hidden in Week view.");
    } catch { showSaveError("Couldn’t save this change. Your choices are still here."); } finally { setSaving(false); }
  };

  return <AppShell title="Adjust this week">
    <section className="session-card">
      <h2>Need a day off?</h2>
      <p>Mark one planned date unavailable for this week only. Fixed races and coach-directed sessions remain visible.</p>
      <label>Date<input type="date" value={changeDate} onChange={(event) => setChangeDate(event.target.value)} /></label>
      <label>Why? (optional)<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Travel, recovery, lifting, family…" /></label>
      <button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save temporary change"}</button>
    </section>
  </AppShell>;
}
