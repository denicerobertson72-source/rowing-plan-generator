# Vercel and Supabase deployment

The app uses two Vercel projects from the same GitHub repository and one Supabase Postgres project.

## 1. Deploy the API

Create a second Vercel project from `denicerobertson72-source/rowing-plan-generator` with these settings:

- Project name: `rowing-plan-api`
- Root Directory: `services/api`
- Framework Preset: Other
- Include source files outside of the Root Directory: enabled

In Supabase, open **Project Settings → Database → Connect** and copy the **Session pooler** connection string. In the API project's Vercel **Settings → Environment Variables**, add these production, preview, and development variables:

| Name | Value |
| --- | --- |
| `SUPABASE_DB_URL` | The private Supabase Session pooler connection string |
| `ALLOWED_ORIGINS` | Leave as `http://localhost:3000` until the web app has a Vercel URL; then use `https://YOUR-WEB-APP.vercel.app` |

Do not put `SUPABASE_DB_URL` in GitHub, browser code, or a public variable. It contains database credentials. Deploy the API, open `https://YOUR-API.vercel.app/api/v1/health`, and confirm it returns `status: ok`.

The API creates the three required tables (`athletes`, `plan_versions`, and `workout_logs`) automatically on its first successful request. These tables are not exposed to browser clients; the FastAPI service is the only database client for this pilot.

## 2. Deploy the PWA

Return to the Vercel project you created for the web app and use:

- Project name: `rowing-plan-pwa`
- Root Directory: `apps/web`
- Framework Preset: Next.js

Add this production, preview, and development environment variable before deploying:

| Name | Value |
| --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `https://YOUR-API.vercel.app/api/v1` |

Deploy the PWA. Copy its production URL and update the API project's `ALLOWED_ORIGINS` to that exact URL. Redeploy the API after changing the variable.

## 3. Smoke test

1. Open the PWA production URL in a private browser window.
2. Complete onboarding and generate a plan.
3. Confirm Today and Week load.
4. Log a workout, then reload the page to confirm it remains saved.

## Current pilot security model

This setup keeps the database password on the server and does not expose Supabase directly to browsers. It does not yet have user accounts, so the pilot URL should be shared only with trusted testers. The next account milestone should add Supabase Auth and Row Level Security before broader distribution.
