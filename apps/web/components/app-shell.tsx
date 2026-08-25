"use client";
import Link from "next/link";
import { ReactNode,useEffect,useState } from "react";
import { usePathname,useRouter } from "next/navigation";
import { ConnectionStatus } from "./connection-status";
import { supabase } from "../lib/supabase";
const tabs=[['Today','/'],['Week','/week'],['Season','/season'],['Profile','/profile'],['Account','/account']];
export function AppShell({title,children}:{title:string;children:ReactNode}) {const pathname=usePathname(),router=useRouter();const [ready,setReady]=useState(!supabase);useEffect(()=>{if(!supabase){setReady(true);return;}supabase.auth.getSession().then(({data})=>{if(!data.session&&pathname!=="/account")router.replace("/account");setReady(true);});},[pathname,router]);return <main className="app-shell"><header><p className="eyebrow">ROWING PLAN GENERATOR</p><h1>{title}</h1><ConnectionStatus /></header>{ready?children:<p>Checking your account…</p>}<nav className="bottom-nav" aria-label="Primary navigation">{tabs.map(([label,href])=><Link key={href} href={href}>{label}</Link>)}</nav></main>; }
