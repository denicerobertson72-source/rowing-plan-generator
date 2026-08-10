# PWA Milestone 2

Milestone 2 adds a development SQLite persistence implementation behind the repository boundaries and a live API-driven PWA flow.

## Added API surface

- `POST /api/v1/athletes` and `GET`/`PUT /api/v1/athletes/{athlete_id}`
- `POST /api/v1/athletes/{athlete_id}/plans/generate`
- `GET /api/v1/plans/{plan_id}`, Today, Week, Excel, and workout-log endpoints
- versioned plan snapshots in SQLite, scoped to an athlete record

## Added PWA routes

- `/onboarding`: athlete, season, availability, primary race, HR, and current test inputs
- `/`: API-backed Today screen
- `/week`, `/season`, `/profile`: mobile read models for the current plan

## Development limits

SQLite is a local development store and has no authentication. It must be replaced by production infrastructure plus authentication before real multi-user athlete data is hosted. Onboarding intentionally captures a concise first plan; coach constraints, multiple races, full testing-block editing, and plan regeneration are introduced in later milestones.

## Run

```bash
/Users/robertsonde/.venv/bin/uvicorn services.api.app.main:app --reload
cd apps/web && npm run dev
```
