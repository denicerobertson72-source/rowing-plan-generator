"use client";
import Link from "next/link";
import { AppShell } from "../../components/app-shell";
import { getSavedSession } from "../../lib/session";
export default function Profile(){const saved=getSavedSession();return <AppShell title="Profile"><section className="empty"><h2>{saved ? "Your profile is saved on this device" : "Start your athlete profile"}</h2><p>Set training availability, races, heart rate, and current performance testing in the guided setup.</p><Link href="/onboarding">{saved ? "Edit or create another plan" : "Set up my plan"}</Link></section></AppShell>}
