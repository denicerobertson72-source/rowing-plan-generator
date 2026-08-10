# Native-app readiness

The Next.js client is configured for static export and communicates with the FastAPI service over a versioned HTTP API. The Python engine remains independent of both, so Capacitor can package the exported `/apps/web/out` assets without rewriting planning or workbook logic.

Works unchanged in a Capacitor shell: the UI, API client, structured plans, workout display, cached read models, and Excel API request. Native adapters/plugins are later needed for push notifications, secure token storage, native share sheets, HealthKit/Health Connect, Bluetooth, background sync, and device imports. Keep all such calls behind `apps/web/lib/platform.ts` interfaces.

Avoid Next.js Server Actions, server-side plan calculation, and filesystem access in athlete flows; they would block a static mobile bundle. Recommended next steps are: add Capacitor after the PWA routes are complete, replace the no-op adapters one by one, then test iOS standalone and Android WebView behavior.
