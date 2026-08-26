"use client";

import { useMemo, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { getSavedSession } from "../../lib/session";
import { supabase } from "../../lib/supabase";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
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
  const [message, setMessage] = useState("");

  const save = async () => {
    const saved = getSavedSession();
    const session = await supabase?.auth.getSession();
    const token = session?.data.session?.access_token;
    if (!saved || !token) {
      setMessage("Sign in and create a plan first.");
      return;
    }
    const response = await fetch(`${apiBase}/athletes/${saved.athleteId}/weekly-overrides`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        week_start: mondayFor(changeDate),
        scope: "this_week_only",
        changes: [{ date: changeDate, unavailable: true, reason: reason.trim() || "Unavailable" }],
      }),
    });
    setMessage(response.ok
      ? "Saved. Non-fixed sessions on that date will be hidden in the Week view; your normal schedule is unchanged."
      : "Could not save this change. Please try again.");
  };

  return <AppShell title="Adjust this week">
    <section className="session-card">
      <h2>Need a day off?</h2>
      <p>Mark one planned date unavailable for this week only. Fixed races and coach-directed sessions remain visible.</p>
      <label>Date<input type="date" value={changeDate} onChange={(event) => setChangeDate(event.target.value)} /></label>
      <label>Why? (optional)<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Travel, recovery, lifting, family…" /></label>
      <button onClick={save}>Save temporary change</button>
      {message && <p className="status">{message}</p>}
    </section>
  </AppShell>;
}
