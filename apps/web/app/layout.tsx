import type { Metadata, Viewport } from "next";
import "./styles.css";
import { ServiceWorkerRegistration } from "../components/service-worker-registration";

export const metadata: Metadata = { title: "Rowing Plan Generator", applicationName: "Rowing Plan Generator", manifest: "/manifest.webmanifest", appleWebApp: { capable: true, title: "Rowing Plan" }, icons: { apple: "/icons/icon-192.svg" } };
export const viewport: Viewport = { themeColor: "#087e8b", colorScheme: "light" };
export default function RootLayout({ children }: Readonly<{children: React.ReactNode}>) { return <html lang="en"><body><ServiceWorkerRegistration />{children}</body></html>; }
