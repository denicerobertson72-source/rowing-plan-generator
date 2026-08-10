"use client";
import { useEffect, useState } from "react";
export function ConnectionStatus(){const [online,setOnline]=useState(true);useEffect(()=>{const update=()=>setOnline(navigator.onLine);update();addEventListener("online",update);addEventListener("offline",update);return()=>{removeEventListener("online",update);removeEventListener("offline",update);};},[]);return <p className={`connection ${online?"":"offline"}`}>{online?"Plan available online":"Offline · showing latest saved plan"}</p>}
