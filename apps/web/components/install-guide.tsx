"use client";
import { useEffect, useState } from "react";
export function InstallGuide() { const [ios, setIos] = useState(false); useEffect(() => setIos(/iPad|iPhone|iPod/.test(navigator.userAgent)), []); return <aside className="install"><b>Install this app</b><br />{ios ? "In Safari, tap Share, then Add to Home Screen." : "Use your browser’s Install app option for quick access."}</aside>; }
