"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";

type Notice = { kind: "success" | "error"; message: string } | null;
type SaveFeedback = { showSaveSuccess: (message: string) => void; showSaveError: (message: string) => void };
const SaveFeedbackContext = createContext<SaveFeedback | null>(null);

export function SaveFeedbackProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice>(null);
  const show = useCallback((kind: "success" | "error", message: string) => setNotice({ kind, message }), []);
  useEffect(() => {
    if (!notice || notice.kind === "error") return;
    const timeout = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timeout);
  }, [notice]);
  return <SaveFeedbackContext.Provider value={{ showSaveSuccess: message => show("success", message), showSaveError: message => show("error", message) }}>
    {children}
    {notice && <div className={`save-toast ${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"} aria-live={notice.kind === "error" ? "assertive" : "polite"}>
      <span>{notice.message}</span>{notice.kind === "error" && <button type="button" className="toast-close" onClick={() => setNotice(null)} aria-label="Dismiss notification">×</button>}
    </div>}
  </SaveFeedbackContext.Provider>;
}

export function useSaveFeedback(): SaveFeedback {
  const value = useContext(SaveFeedbackContext);
  if (!value) throw new Error("useSaveFeedback must be used inside AppShell");
  return value;
}
