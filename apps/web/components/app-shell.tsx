"use client";
import Link from "next/link";
import { ReactNode,useEffect,useState } from "react";
import { usePathname,useRouter } from "next/navigation";
import { ConnectionStatus } from "./connection-status";
import { supabase } from "../lib/supabase";
import { getDeveloperDiagnosticsAccess } from "../lib/api";
const tabs=[['Today','/'],['Week','/week'],['Season','/season'],['Profile','/profile'],['Account','/account']];
export function AppShell({title,children}:{title:string;children:ReactNode}) {const pathname=usePathname(),router=useRouter();const [ready,setReady]=useState(!supabase),[developer,setDeveloper]=useState(false);useEffect(()=>{let active=true;const loadDeveloperAccess=async()=>{try{const access=await getDeveloperDiagnosticsAccess();if(active)setDeveloper(access.developer_diagnostics_authorized===true);}catch{if(active)setDeveloper(false);}};if(!supabase){void loadDeveloperAccess();setReady(true);return()=>{active=false;};}supabase.auth.getSession().then(async({data})=>{if(!active)return;if(!data.session){if(pathname!=="/account")router.replace("/account");setDeveloper(false);setReady(true);return;}await loadDeveloperAccess();if(active)setReady(true);});return()=>{active=false;};},[pathname,router]);const visibleTabs=developer?[...tabs,['Developer diagnostics','/developer-diagnostics']]:tabs;return <main className="app-shell"><header><p className="eyebrow">ROWING PLAN GENERATOR</p><h1>{title}</h1><ConnectionStatus /></header>{ready?children:<p>Checking your account…</p>}<nav className="bottom-nav" aria-label="Primary navigation">{visibleTabs.map(([label,href])=>{const active=pathname===href;return <Link key={href} href={href} className={active?"active":undefined} aria-current={active?"page":undefined}>{label}</Link>})}</nav></main>; }
