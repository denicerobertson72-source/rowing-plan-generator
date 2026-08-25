import Link from "next/link";
import { ReactNode } from "react";
import { ConnectionStatus } from "./connection-status";
const tabs=[['Today','/'],['Week','/week'],['Season','/season'],['Profile','/profile'],['Account','/account']];
export function AppShell({title,children}:{title:string;children:ReactNode}) { return <main className="app-shell"><header><p className="eyebrow">ROWING PLAN GENERATOR</p><h1>{title}</h1><ConnectionStatus /></header>{children}<nav className="bottom-nav" aria-label="Primary navigation">{tabs.map(([label,href])=><Link key={href} href={href}>{label}</Link>)}</nav></main>; }
