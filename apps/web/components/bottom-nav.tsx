"use client";
const tabs = ["Today", "Week", "Season", "Profile"];
export function BottomNav({active, onChange}: {active: string; onChange: (tab: string) => void}) { return <nav className="bottom-nav" aria-label="Primary navigation">{tabs.map(tab => <button key={tab} className={active === tab ? "active" : ""} onClick={() => onChange(tab)}>{tab}</button>)}</nav>; }
